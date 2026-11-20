"""Contract tests for /health, /chat and /sessions/{id}/messages (assignment sections 3 and 5)."""

from __future__ import annotations

import pytest

from tests.conftest import new_session

METADATA_FIELDS = {
    "resolution_source",
    "model",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "key_id",
    "fallback_attempts",
}


def chat(client, session_id, message, headers=None, **extra):
    return client.post("/chat", json={"session_id": session_id, "message": message, **extra}, headers=headers or {})


# -- /health -----------------------------------------------------------------------------
def test_health_reports_pool_and_cache(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["keys_configured"] == 2
    assert body["cache_enabled"] is True


def test_health_is_503_without_keys(make_client):
    res = make_client(keys="").get("/health")
    assert res.status_code == 503
    assert "detail" in res.json()


# -- /chat response shape -------------------------------------------------------------------
def test_chat_response_matches_contract(client):
    sid = new_session()
    res = chat(client, sid, "What is the capital of France?")
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == sid
    assert isinstance(body["reply"], str) and body["reply"].strip()
    meta = body["metadata"]
    assert METADATA_FIELDS <= meta.keys()
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "openai/fake-model"
    assert isinstance(meta["latency_ms"], int) and meta["latency_ms"] >= 0
    assert isinstance(meta["input_tokens"], int) and meta["input_tokens"] > 0
    assert isinstance(meta["output_tokens"], int) and meta["output_tokens"] > 0
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0


def test_response_never_contains_api_keys(make_client):
    client = make_client(keys="good-secret-AAA,good-secret-BBB")
    sid = new_session()
    res = chat(client, sid, "hello", headers={"X-Simulate-Failure": "key_1=429"})
    assert res.status_code == 200
    history = client.get(f"/sessions/{sid}/messages")
    for text in (res.text, history.text, client.get("/health").text):
        assert "good-secret" not in text


# -- validation (422) ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "s-1"},  # missing message
        {"message": "hi"},  # missing session_id
        {"session_id": "s-1", "message": ""},
        {"session_id": "s-1", "message": "   \n\t  "},  # empty after trimming
        {"session_id": "s-1", "message": "x" * 4001},
        {"session_id": "", "message": "hi"},
        {"session_id": "   ", "message": "hi"},
        {"session_id": "s" * 129, "message": "hi"},
        {"session_id": 123, "message": "hi"},
        {"session_id": "s-1", "message": 42},
        {"session_id": "s-1", "message": None},
    ],
)
def test_invalid_payloads_are_422_and_not_stored(client, fake_provider, payload):
    res = client.post("/chat", json=payload)
    assert res.status_code == 422
    assert "detail" in res.json()
    assert fake_provider.calls == []
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        assert client.get(f"/sessions/{sid}/messages").status_code == 404


def test_malformed_json_is_422(client):
    res = client.post("/chat", content=b'{"session_id": "s-1", "message": ', headers={"Content-Type": "application/json"})
    assert res.status_code == 422
    assert "detail" in res.json()


def test_message_length_is_checked_after_trimming(client):
    sid = new_session()
    res = chat(client, sid, "   " + "a" * 4000 + "   ")
    assert res.status_code == 200
    stored = client.get(f"/sessions/{sid}/messages").json()["messages"][0]["content"]
    assert stored == "a" * 4000


def test_session_id_boundaries(client):
    assert chat(client, "s" * 128, "hi").status_code == 200
    assert chat(client, "x", "hi").status_code == 200


# -- caching -----------------------------------------------------------------------------------
def test_same_first_message_in_new_session_is_cache_hit(client, fake_provider):
    first = chat(client, new_session("a"), "What is the capital of France?").json()
    second = chat(client, new_session("b"), "What is the capital of France?").json()

    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]  # identical text
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0
    # tokens on a hit are the usage stored with the cached response
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]
    assert second["metadata"]["output_tokens"] == first["metadata"]["output_tokens"]
    assert second["metadata"]["model"] == first["metadata"]["model"]
    assert second["metadata"]["latency_ms"] < first["metadata"]["latency_ms"]
    assert len(fake_provider.calls) == 1  # the hit never reached the provider


def test_different_message_never_comes_from_cache(client):
    chat(client, new_session(), "What is the capital of France?")
    other = chat(client, new_session(), "What is the capital of Spain?").json()
    assert other["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_cache_key_includes_history(client):
    """Same text, different history -> different message list -> live call."""
    sid = new_session()
    chat(client, sid, "Remember the number 7.")
    in_context = chat(client, sid, "What is the capital of France?").json()
    fresh = chat(client, new_session(), "What is the capital of France?").json()
    assert in_context["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert fresh["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_cache_key_includes_system_prompt(client):
    a = chat(client, new_session(), "Tell me a fact.", system_prompt="You are a pirate.").json()
    b = chat(client, new_session(), "Tell me a fact.", system_prompt="You are a poet.").json()
    c = chat(client, new_session(), "Tell me a fact.", system_prompt="You are a pirate.").json()
    assert a["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert b["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert c["metadata"]["resolution_source"] == "LLM_CACHE"


def test_cache_hit_is_shared_across_keys(client, fake_provider):
    """An answer produced by key_2 (after key_1 failed) is served from cache to later requests."""
    msg = "Shared across keys?"
    produced = chat(client, new_session(), msg, headers={"X-Simulate-Failure": "key_1=503"}).json()
    assert produced["metadata"]["key_id"] == "key_2"
    hit = chat(client, new_session(), msg).json()
    assert hit["metadata"]["resolution_source"] == "LLM_CACHE"
    assert hit["reply"] == produced["reply"]


def test_disk_cache_survives_restart(make_client):
    msg = "Persist me in the disk cache"
    first = make_client(CACHE_TYPE="disk").post("/chat", json={"session_id": new_session(), "message": msg}).json()
    restarted = make_client(CACHE_TYPE="disk")  # new app, same cache dir and DB
    second = restarted.post("/chat", json={"session_id": new_session(), "message": msg}).json()
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]


# -- multi-turn state & history endpoint ----------------------------------------------------------
def test_bot_remembers_earlier_messages_in_session(client, fake_provider):
    sid = new_session()
    chat(client, sid, "My name is Rahul.")
    second = chat(client, sid, "What is my name?").json()
    # The fake provider echoes the first user message it received: history was sent.
    assert "first message: My name is Rahul." in second["reply"]
    assert "user turns: 2" in second["reply"]
    sent = fake_provider.calls[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]


def test_sessions_are_isolated(client):
    a = new_session("a")
    chat(client, a, "My name is Rahul.")
    other = chat(client, new_session("b"), "What is my name?").json()
    assert "Rahul" not in other["reply"]
    assert "user turns: 1" in other["reply"]


def test_history_endpoint_returns_stored_messages_oldest_first(client):
    sid = new_session()
    r1 = chat(client, sid, "First question").json()
    r2 = chat(client, sid, "Second question").json()
    res = client.get(f"/sessions/{sid}/messages")
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == sid
    msgs = body["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in msgs] == ["First question", r1["reply"], "Second question", r2["reply"]]
    assert "metadata" not in msgs[0]  # user messages carry no metadata
    assert msgs[1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert msgs[1]["metadata"]["input_tokens"] == r1["metadata"]["input_tokens"]
    assert msgs[3]["metadata"]["output_tokens"] == r2["metadata"]["output_tokens"]
    assert all(m["role"] != "system" for m in msgs)  # system prompt is never returned


def test_unknown_session_is_404(client):
    res = client.get("/sessions/does-not-exist/messages")
    assert res.status_code == 404
    assert res.json() == {"detail": "session not found"}


def test_history_survives_app_restart(make_client):
    sid = new_session()
    make_client(db_name="persist.db").post("/chat", json={"session_id": sid, "message": "remember me"})
    restarted = make_client(db_name="persist.db")
    msgs = restarted.get(f"/sessions/{sid}/messages").json()["messages"]
    assert [m["content"] for m in msgs][0] == "remember me"


def test_system_prompt_override_is_sticky_for_the_session(client, fake_provider):
    sid = new_session()
    chat(client, sid, "hi", system_prompt="You are a pirate assistant.")
    chat(client, sid, "and again")  # no system_prompt -> keeps the session override
    assert fake_provider.calls[-1]["messages"][0] == {"role": "system", "content": "You are a pirate assistant."}
    chat(client, sid, "reset please", system_prompt="")  # blank -> back to the server default
    assert fake_provider.calls[-1]["messages"][0]["content"] != "You are a pirate assistant."


# -- token accounting ------------------------------------------------------------------------------
def test_tokens_are_per_request_not_running_totals(client, fake_provider):
    a = chat(client, new_session("a"), "Alpha beta gamma delta").json()["metadata"]
    b = chat(client, new_session("b"), "Omega sigma theta kappa").json()["metadata"]
    assert a["input_tokens"] == b["input_tokens"]  # equal-length first messages

    sid = new_session("c")
    t1 = chat(client, sid, "one two three").json()["metadata"]
    t2 = chat(client, sid, "four five six").json()["metadata"]
    # second prompt = system + turn 1 + reply 1 + new message (it includes history) ...
    sent = fake_provider.calls[-1]["messages"]
    assert t2["input_tokens"] == sum(len(m["content"].split()) for m in sent)
    # ... but output tokens are only this reply's tokens, never a conversation total
    assert t2["output_tokens"] == len(chat_reply_text(client, sid, index=3).split())
    assert t2["output_tokens"] < t1["output_tokens"] + t2["output_tokens"]


def chat_reply_text(client, sid, index):
    return client.get(f"/sessions/{sid}/messages").json()["messages"][index]["content"]


# -- generic error hygiene ----------------------------------------------------------------------------
def test_unknown_route_and_method_are_json(client):
    assert client.get("/nope").json() == {"detail": "Not Found"}
    res = client.get("/chat")
    assert res.status_code == 405
    assert "detail" in res.json()


def test_cors_preflight_allows_frontend_origin_and_simulation_header(client):
    res = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-simulate-failure" in res.headers["access-control-allow-headers"].lower()


def test_cors_rejects_unknown_origin(client):
    res = client.options(
        "/chat",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in res.headers
