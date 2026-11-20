#!/usr/bin/env python3
"""End-to-end contract check against a running backend (local or deployed).

Usage:
    python scripts/smoke_test.py http://localhost:8000
    python scripts/smoke_test.py https://lyftr-chat-c8fd.onrender.com --origin https://lyftr-chat.vercel.app

Uses only the standard library. Makes about 6 real LLM calls.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
failures = 0


def call(base: str, method: str, path: str, body: object | None = None, headers: dict | None = None, raw: bytes | None = None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=90) as res:
            text = res.read().decode()
            try:
                parsed = json.loads(text) if text else None
            except json.JSONDecodeError:
                parsed = text  # e.g. the plain "OK" body of a CORS preflight
            return res.status, parsed, dict(res.headers)
    except urllib.error.HTTPError as err:
        text = err.read().decode()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
        return err.code, parsed, dict(err.headers)


def check(name: str, ok: bool, info: object = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"[{PASS if ok else FAIL}] {name}" + (f"  -> {info}" if info not in ("", None) else ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--origin", help="frontend origin to test CORS for")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    run = uuid.uuid4().hex[:6]

    status, body, _ = call(base, "GET", "/health")
    check("GET /health is 200 ok", status == 200 and body.get("status") == "ok", body)
    check("health: >= 2 keys, cache enabled", status == 200 and body.get("keys_configured", 0) >= 2 and body.get("cache_enabled") is True)

    # --- live call, then the same first message in a new session -> cache ---------------------
    question = f"In one short sentence, what is the capital of France? (run {run})"
    sa, sb = f"s-{run}-a", f"s-{run}-b"
    s1, live, _ = call(base, "POST", "/chat", {"session_id": sa, "message": question})
    m1 = (live or {}).get("metadata", {}) if s1 == 200 else {}
    check("POST /chat live call is 200", s1 == 200, live if s1 != 200 else m1)
    check("live: resolution_source LIVE_INFERENCE", m1.get("resolution_source") == "LIVE_INFERENCE")
    check("live: key_id set, fallback_attempts int", isinstance(m1.get("key_id"), str) and isinstance(m1.get("fallback_attempts"), int))
    check(
        "live: integer latency/tokens",
        all(isinstance(m1.get(k), int) for k in ("latency_ms", "input_tokens", "output_tokens")),
    )
    check("live: session_id echoed, reply non-empty", s1 == 200 and live["session_id"] == sa and live["reply"].strip() != "")

    s2, cached, _ = call(base, "POST", "/chat", {"session_id": sb, "message": question})
    m2 = (cached or {}).get("metadata", {}) if s2 == 200 else {}
    check("same first message, new session -> LLM_CACHE", m2.get("resolution_source") == "LLM_CACHE", m2)
    check("cache: identical reply", s2 == 200 and s1 == 200 and cached["reply"] == live["reply"])
    check("cache: key_id null, fallback_attempts 0", m2.get("key_id") is None and m2.get("fallback_attempts") == 0)
    check("cache: faster than live", m2.get("latency_ms", 1e9) < m1.get("latency_ms", 0), f"{m2.get('latency_ms')} ms vs {m1.get('latency_ms')} ms")
    check("cache: same token usage as stored", m2.get("input_tokens") == m1.get("input_tokens") and m2.get("output_tokens") == m1.get("output_tokens"))

    # --- multi-turn ---------------------------------------------------------------------------------
    s3, follow, _ = call(base, "POST", "/chat", {"session_id": sa, "message": "Which city did you just name? Reply with the city only."})
    check("follow-up in same session is 200", s3 == 200, follow if s3 != 200 else follow["reply"][:80])
    if s3 == 200:
        check("bot remembers earlier turn (mentions Paris; needs a real model)", "paris" in follow["reply"].lower(), follow["reply"][:80])
        check("follow-up input_tokens > first (history included)", follow["metadata"]["input_tokens"] > m1.get("input_tokens", 0))
    s4, hist, _ = call(base, "GET", f"/sessions/{sa}/messages")
    roles = [m["role"] for m in (hist or {}).get("messages", [])] if s4 == 200 else []
    check("GET history: 4 messages oldest first", roles == ["user", "assistant", "user", "assistant"], roles)
    check("GET history: assistant metadata present", s4 == 200 and "metadata" in hist["messages"][1])
    s5, nf, _ = call(base, "GET", f"/sessions/missing-{run}/messages")
    check("unknown session -> 404 'session not found'", s5 == 404 and nf == {"detail": "session not found"}, nf)

    # --- validation ------------------------------------------------------------------------------------
    for name, payload in [
        ("empty message", {"session_id": sa, "message": "   "}),
        ("message over 4000 chars", {"session_id": sa, "message": "x" * 4001}),
        ("missing session_id", {"message": "hi"}),
    ]:
        st, b, _ = call(base, "POST", "/chat", payload)
        check(f"422 for {name}", st == 422 and isinstance(b, dict) and "detail" in b, st)
    st, b, _ = call(base, "POST", "/chat", raw=b'{"session_id": "x", "message": ')
    check("422 for malformed JSON", st == 422 and isinstance(b, dict) and "detail" in b, st)

    # --- failover hook --------------------------------------------------------------------------------
    for header in ("key_1=429", "key_1=500"):
        st, b, _ = call(base, "POST", "/chat", {"session_id": f"s-{run}-{header[-3:]}", "message": f"Say OK ({header}, {run})"}, {"X-Simulate-Failure": header})
        meta = (b or {}).get("metadata", {}) if st == 200 else {}
        check(f"{header} -> 200 via key_2", st == 200 and meta.get("key_id") == "key_2" and meta.get("fallback_attempts", 0) >= 1, b if st != 200 else meta)
    before = len(hist["messages"]) if s4 == 200 else None
    st, b, _ = call(base, "POST", "/chat", {"session_id": sa, "message": f"this must fail {run}"}, {"X-Simulate-Failure": "*=429"})
    check("*=429 -> 503 all providers unavailable", st == 503 and b == {"detail": "all providers unavailable"}, (st, b))
    _, hist2, _ = call(base, "GET", f"/sessions/{sa}/messages")
    check("failed request not added to history", before is not None and len(hist2["messages"]) == before)

    # --- CORS ---------------------------------------------------------------------------------------------
    if args.origin:
        st, _, hdrs = call(
            base,
            "OPTIONS",
            "/chat",
            headers={"Origin": args.origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-simulate-failure"},
        )
        allowed = {k.lower(): v for k, v in hdrs.items()}.get("access-control-allow-origin")
        check(f"CORS allows {args.origin}", allowed in (args.origin, "*"), allowed)

    print(f"\n{'All checks passed' if failures == 0 else f'{failures} check(s) failed'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    t0 = time.time()
    code = main()
    print(f"({time.time() - t0:.1f}s)")
    sys.exit(code)
