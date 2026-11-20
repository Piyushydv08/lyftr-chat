# Lyftr Chat: LLM chatbot with key failover and response caching

A full-stack, multi-turn chatbot. **React** (Vite + TypeScript) frontend, **FastAPI** backend, **LiteLLM** gateway with a key pool, automatic failover and response caching, and **SQLite** session storage. Each reply in the UI shows how it was served: `LIVE_INFERENCE` or `LLM_CACHE`, the model, the latency, the input/output tokens for that request only, and the route it took through the key pool (for example `key_1 ✕ → key_2 ✓`).

```
Browser (React)                     FastAPI backend                                   Providers
┌──────────────────┐  POST /chat   ┌──────────────────────────────────────────┐
│ chat + metadata  │ ────────────▶ │ validate → load history (SQLite)          │
│ system prompt    │ X-Simulate-   │   → LiteLLM Router                        │      ┌────────┐
│ failover test    │   Failure     │       cache lookup (LiteLLM Cache) ───hit─┼─────▶│ cache  │
└──────────────────┘ ◀──────────── │       key_1 ──fail──▶ key_2 ──▶ ...       │─────▶│ Gemini │
                       JSON         │   → save user+assistant pair (SQLite)    │      └────────┘
                                    └──────────────────────────────────────────┘
                                         /data/app.db + /data/llm_cache  (Docker volume)
```

---

## 1. Live URLs

| What | URL |
|---|---|
| Frontend | `https://lyftr-chat.vercel.app` |
| Backend | `https://lyftr-chat-c8fd.onrender.com` (try `/health` and `/docs`) |
| Repository | `https://github.com/Piyushydv08/lyftr-chat.git` |
| Demo video | `<link>` |

Step-by-step deployment (Render + Vercel, about 15 minutes): **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.
Note: the free Render instance sleeps after 15 idle minutes, so the first request after a pause can take about 50 s.

---

## 2. Run locally

### With Docker (the whole stack)

```bash
cp .env.example .env        # then put two real keys in LLM_API_KEYS
make up                     # backend http://localhost:8000, frontend http://localhost:3000
make logs                   # follow both services
make down                   # stop (the SQLite volume is kept; `make clean` deletes it)
make test                   # backend pytest + frontend vitest, each in a Docker test stage
```

No `make` (e.g. Windows)? Use `docker compose up -d --build` and `docker compose down`.

`make up` builds two multi-stage images (`python:3.12-slim` runtime with only the virtualenv and app code; `nginx:alpine` serving the static build) and starts them with a named volume `app_data` mounted at `/data`. The SQLite file (`/data/app.db`) and the disk response cache (`/data/llm_cache`) live there, so history and cached answers survive `make down && make up`.

### Without Docker

Backend (Python 3.11+):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export LLM_API_KEYS="key-one,key-two" ENABLE_FAILURE_SIMULATION=true \
       DATABASE_URL=sqlite:///./data/app.db CACHE_DIR=./data/llm_cache
uvicorn app.main:app --reload --port 8000
python -m pytest                                        # 70 tests, no API keys or network needed
```

Frontend (Node 20+):

```bash
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev     # http://localhost:3000
npm test                                                # 6 UI contract tests
```

### Configuration

All configuration is via environment variables. Every variable is listed with placeholder values in [`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `LLM_API_KEYS` | required | Comma-separated key pool (≥ 2). Ids are `key_1`, `key_2`, … in this order. |
| `LLM_MODEL` | `gemini/gemini-2.5-flash-lite` | LiteLLM model name for every key. |
| `LLM_MODELS` | empty | Optional per-key models aligned with the keys (mix providers, e.g. Gemini + Groq). |
| `LLM_API_BASE` | empty | Optional base URL for OpenAI-compatible providers. |
| `DATABASE_URL` | `sqlite:////data/app.db` (Docker) | SQLite location. |
| `ALLOWED_ORIGINS` | `http://localhost:3000,…` | CORS allowlist (comma-separated). `ALLOWED_ORIGIN_REGEX` is optional. |
| `ENABLE_FAILURE_SIMULATION` | `false` | `true` enables the `X-Simulate-Failure` hook. **Set it on the deployed backend.** |
| `CACHE_TYPE` / `CACHE_DIR` / `CACHE_TTL_SECONDS` | `local` / `./data/llm_cache` / `86400` | LiteLLM cache: `local` (in-memory) or `disk`. |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_OUTPUT_TOKENS` | `30`, `1024` | Per-key timeout before failing over; reply length cap. |
| `DEFAULT_SYSTEM_PROMPT`, `HISTORY_MAX_MESSAGES` | see file, `50` | Default persona; how many stored messages are sent as context (0 = all). |
| `LOG_LEVEL`, `ENABLE_DOCS`, `PORT` | `INFO`, `true`, `8000` | Logging, Swagger UI at `/docs`, listen port. |
| `PUBLIC_API_BASE_URL` (Docker) / `VITE_API_BASE_URL` (static hosts) | `http://localhost:8000` | The backend URL the browser calls. |

### Project layout

```
backend/
  app/
    main.py            FastAPI app: 3 endpoints, error handling, CORS
    chat_service.py    history → gateway → persist (only on success)
    db.py              SQLite schema + queries (stdlib sqlite3, WAL)
    schemas.py         request/response models (the API contract)
    config.py          env-var settings
    llm/gateway.py     LiteLLM Router: key pool, fallbacks, cache, result mapping
    llm/simulation.py  X-Simulate-Failure parser + LiteLLM pre-call hook
  tests/               pytest: contract, caching, multi-turn, failover (fake provider over real HTTP)
  Dockerfile           builder → test → runtime (multi-stage)
frontend/
  src/App.tsx, src/api.ts, src/components/   chat UI, API client, reply metadata + route line
  docker/              nginx.conf + entrypoint that writes config.js from API_BASE_URL
  Dockerfile           deps → test → build → nginx runtime (multi-stage)
scripts/smoke_test.py  end-to-end contract check against any running backend
docs/                  DEPLOYMENT.md (Render + Vercel), DEMO_SCRIPT.md (2–3 min video plan)
.github/workflows/     CI: pytest, vitest, docker compose build
docker-compose.yml  Makefile  .env.example  render.yaml
```

---

## 3. Design decisions

### Key fallback

- **One LiteLLM Router model group per key.** Each key is a deployment in its own group (`key_1`, `key_2`, …), and the Router is configured with `fallbacks=[{"key_1": ["key_2", …]}]`. The failover order is therefore deterministic: `key_1=429` always lands on `key_2`, and `fallback_attempts` is meaningful. The failover itself is LiteLLM's (`Router.acompletion` with fallbacks), not a hand-rolled loop.
- **Fail fast.** `num_retries=0` on the Router and `max_retries=0` per deployment, so a rate-limited or broken key is abandoned immediately instead of being retried while the user waits. `LLM_TIMEOUT_SECONDS` bounds a hanging provider, which then also fails over.
- **What triggers failover.** LiteLLM maps provider responses to typed exceptions: 429 or quota exhausted (`RESOURCE_EXHAUSTED`) → `RateLimitError`; 500/502/503 → `InternalServerError` / `BadGatewayError` / `ServiceUnavailableError`; plus timeouts and connection errors. 401/403 also fail over on purpose, because a revoked key should not take the app down. The test suite checks all of these against a fake provider that returns real HTTP errors.
- **When every key fails**, the endpoint returns `503 {"detail": "all providers unavailable"}`. The provider's error text is logged (with keys redacted), never returned.
- **Cooldowns disabled** (`disable_cooldowns=True`). The simulation must affect one request only, and with one deployment per group a cooldown would only empty that key's own group. Trade-off: a permanently dead `key_1` costs one fast failed call per request before `key_2` answers. That is acceptable at this scale, and the response reports it as `fallback_attempts: 1`.
- **`key_id` and `fallback_attempts`.** `key_id` comes from the deployment id LiteLLM reports for the answering call. `fallback_attempts` is the number of keys tried before it, recorded by the same pre-call hook described below. On `LLM_CACHE` they are `null` and `0`. The key itself never leaves the process: `KeySlot.__repr__` masks it and log lines are redacted.

### The `X-Simulate-Failure` hook

The header (`key_1=429`, `key_1=429,key_2=500`, `*=429`; statuses 429/500/502/503) is parsed per request into a `ContextVar`. A LiteLLM `CustomLogger.async_pre_call_check` hook runs every time the Router is about to use a deployment. If that key is listed, it raises the exact exception LiteLLM would raise for that HTTP status, so the Router treats it as a real provider failure and runs its normal fallback chain. Because the state is request-scoped, nothing leaks into other requests. The header is honoured only when `ENABLE_FAILURE_SIMULATION=true`, and a malformed header gets a 422 with an explanation.

**Simulated requests skip the cache lookup** (LiteLLM's per-request `cache={"no-cache": True}`), though a successful answer is still stored. Without this, `key_1=429` on a previously asked question would come back as `LLM_CACHE` with no key, and `*=429` could return 200 from cache. The hook exists to exercise the providers, so it always does.

### Caching

- **LiteLLM's built-in cache.** `CACHE_TYPE=disk` (the Docker default) uses LiteLLM's disk cache. It is `diskcache`, which is itself SQLite-backed, so there is no external service, and it sits on the `/data` volume so it survives restarts. `CACHE_TYPE=local` is the in-memory option.
- **Cache key = model + the full message list** (system prompt + history + new message), via LiteLLM's own key function. All other parameters are constant. This means:
  - the same first message in a new session is a hit;
  - a different message, a different history or a different system prompt is a miss;
  - follow-up turns are rarely hits, which is correct, because their context differs.
- **`caching_groups`** makes keys that serve the same model share one cache namespace. An answer produced by `key_2` during a failover is served from cache to later requests that start at `key_1`. The model name is part of the group, so changing `LLM_MODEL` never returns another model's answers.
- **Labelling.** LiteLLM marks hits with `_hidden_params["cache_hit"]`, which maps to `LLM_CACHE`; anything else is `LIVE_INFERENCE`. A hit returns the identical text and the token usage stored with the cached response. `latency_ms` is measured server-side from request start, so a hit (a few ms) is always faster than a live call (hundreds of ms).
- **Tokens** are the provider's `usage.prompt_tokens` / `usage.completion_tokens` for this request only (with a LiteLLM tokenizer fallback if a provider omits usage). They are never a running total. `input_tokens` grows with history because the prompt does.

### SQLite session storage

- The schema is created on startup: the `messages` table exactly as specified, an index on `(session_id, id)`, and a small `sessions` table holding the optional per-session `system_prompt` override.
- **A request writes nothing until it succeeds.** The user message and the assistant reply are inserted together in one transaction after the LLM answers. A 422 or 503 therefore never touches history, and a session exists only once it has messages, so an unknown id returns 404.
- The client sends only the new message. The server rebuilds the prompt from SQLite, oldest first, capped by `HISTORY_MAX_MESSAGES` and never starting on an assistant turn.
- It uses stdlib `sqlite3` with WAL and one short-lived connection per operation, run via `asyncio.to_thread` so the event loop never blocks. `DATABASE_URL` points at `/data/app.db` on the named volume.
- **`system_prompt` semantics:**
  - omitted: keep the session's stored prompt, or the default if none is stored;
  - text: override it for this session from now on;
  - `""`: reset to the default.

### Errors, security, robustness

- Every error is JSON with `detail`:
  - FastAPI 422s for validation and malformed JSON;
  - 503 when every provider is down;
  - 404 for an unknown session;
  - a catch-all middleware that turns any unexpected exception into `500 {"detail": "internal server error"}` (still with CORS headers) instead of a stack trace.
- CORS is an explicit allowlist. Containers run as non-root, and `.env` is git-ignored.
- **Pinned LiteLLM 1.83.14.** The newest 1.110.x release crashed on cache hits unless unrelated proxy dependencies were installed (`soundfile`). 1.83.14 runs Router fallbacks, `caching_groups` and cache hits with base dependencies only. It also avoids the compromised 1.82.7/1.82.8 PyPI releases.

### Known limitations

- On Render's free plan the container filesystem is not persistent across redeploys. Use a paid disk mounted at `/data`, or Railway with a volume, for durable history in the cloud. Locally the Docker volume persists.
- The cache is per instance. A horizontally scaled deployment would need a shared cache, which the brief rules out.

---

## 4. Verification

### Quick: run the contract check

```bash
python scripts/smoke_test.py https://lyftr-chat-c8fd.onrender.com --origin https://lyftr-chat.vercel.app
```

It checks health, live → cache hit, identical reply, cache faster than live, multi-turn memory, the history endpoint, 404, four 422 cases, `key_1=429`/`key_1=500` → `key_2`, `*=429` → 503 with nothing stored, and CORS.

### In the UI

1. Send *What is the capital of France?* The reply shows `LIVE_INFERENCE`, the model, latency in ms, its input/output tokens and the route `key_1 ✓`.
2. Click **New chat** and send the same message. You get `LLM_CACHE`, the identical text, a few ms of latency, the same stored token counts, and the route shows *Response cache*.
3. **Failover:** under *Failover test* pick *key_1 returns 429* and send a new message. The route shows `key_1 ✕ → key_2 ✓` and "failed over after 1 key". Pick *Every key returns 429*: a readable error appears in the chat, and *Open stored history* shows that nothing was saved.
4. **Tokens:** every reply shows only its own input/output tokens. Send a follow-up and `input_tokens` rises, because the prompt now contains history, while `output_tokens` stays per reply.
5. **Memory:** say "My name is Rahul", then ask "What is my name?" In a **New chat** it is forgotten, because sessions are separate.

### With curl

```bash
B=http://localhost:8000
curl -s $B/health
# live, then cache hit (same first message, new session)
curl -s -X POST $B/chat -H 'Content-Type: application/json' -d '{"session_id":"s-1","message":"What is the capital of France?"}'
curl -s -X POST $B/chat -H 'Content-Type: application/json' -d '{"session_id":"s-2","message":"What is the capital of France?"}'
# failover
curl -s -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429' -d '{"session_id":"s-3","message":"Say hi"}'
curl -s -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=500' -d '{"session_id":"s-3","message":"Say hi again"}'
curl -s -i -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: *=429' -d '{"session_id":"s-4","message":"fail"}'   # 503
# history, 404, 422
curl -s $B/sessions/s-1/messages
curl -s -i $B/sessions/nope/messages
curl -s -i -X POST $B/chat -H 'Content-Type: application/json' -d '{"session_id":"s-1","message":"   "}'
```

The backend logs one line per request, for example `chat session=s-3 source=LIVE_INFERENCE key=key_2 fallbacks=1 latency_ms=640 tokens=31/12 simulated={'key_1': 429}`.

### Automated tests

- **Backend** (`backend/tests`, 70 tests). An OpenAI-compatible fake provider runs on localhost, so LiteLLM makes real HTTP calls and receives real 429/500/502/503/401 responses. The suite covers:
  - the response shape and every 422 case;
  - cache hit/miss rules, including across keys and across restarts;
  - multi-turn memory and session isolation;
  - per-request tokens;
  - the history endpoint and persistence across restarts;
  - simulated and real failover (2 and 3 keys);
  - 503 with nothing stored;
  - CORS, and that keys never appear in responses.
- **Frontend** (`frontend/src/App.test.tsx`, 6 tests). It covers every `data-testid`, the input clearing after send, Enter to send, the session id staying stable across turns, readable 422, 503 and network errors, and the system prompt plus the failover header being sent.

---

## 5. Setup Used

- **Editor:** `<your editor, e.g. VS Code>` ← fill in
- **AI tools:** Claude (Anthropic), used to design and write the code, tests and docs, and to verify LiteLLM's Router/cache behaviour against the pinned version.
- **Stack:** Python 3.12, FastAPI, LiteLLM 1.83.14, SQLite; React 19, Vite 7, TypeScript; Docker Compose, nginx; pytest and Vitest.

---

## 6. Bonus features

- **Route line on every reply.** The UI draws the path each request took (`key_1 ✕ → key_2 ✓`, or *Response cache*), so failover is visible without reading JSON.
- **Failover test control in the UI.** Presets plus a custom value that send `X-Simulate-Failure`, which makes the demo reproducible from the browser.
- **Failover verified against real HTTP errors**, not only the simulation hook. A fake provider returns genuine 429/5xx/401 responses in the tests.
- **Persistent disk cache.** LiteLLM's disk cache, which is SQLite-backed, sits on the same volume as the database, so cache hits survive restarts as well as history.
- **Per-session system prompt** that persists in SQLite and is part of the cache key.
- **Runtime-configurable frontend image.** `API_BASE_URL` is written into `config.js` at container start, so one image works for any backend without a rebuild.
- **Tests run inside Docker** (`make test` builds the `test` stages), and `scripts/smoke_test.py` checks a live deployment end to end.
- **GitHub Actions CI** (`.github/workflows/ci.yml`) runs both test suites and builds both Docker images on every push.
- **Mixed-provider pools** via `LLM_MODELS`, e.g. a Gemini key plus a Groq key, without code changes.
- **Hardening:**
  - JSON-only errors, even for unexpected exceptions;
  - key redaction in logs;
  - non-root containers and health checks;
  - a readable "backend unreachable" state in the UI;
  - Retry on failed messages.
