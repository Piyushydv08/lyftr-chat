# Demo video script (2–3 minutes, screen recording with voice)

Before recording:
- Open `https://<backend>/health` to wake the free instance.
- Have three tabs ready: the frontend, `/health`, and a terminal.
- Use the **deployed** URLs.

| Time | Show | Say |
|---|---|---|
| 0:00–0:15 | Frontend home | "This is a multi-turn chatbot: React frontend, FastAPI backend, a LiteLLM gateway with two Gemini keys, automatic failover, response caching and SQLite history. Both tiers are deployed." |
| 0:15–0:30 | `/health` tab | "Health returns ok with two keys configured and the cache enabled." |
| 0:30–1:00 | Send *What is the capital of France?*, then *And roughly how many people live there?* | "Each reply shows its source, LIVE_INFERENCE, the model, latency in ms, and the input and output tokens of this request only. The route shows key_1 answered. The follow-up proves memory: it knows we mean Paris. Input tokens went up because history is part of the prompt, but they are still per request." |
| 1:00–1:20 | **New chat**, send *What is the capital of France?* again | "A new session with the same first message gives LLM_CACHE: identical text, a few milliseconds instead of hundreds, the stored token counts, and no key used." |
| 1:20–1:50 | Failover test → *key_1 returns 429*, send a message; then *key_1 returns 500*; then *Every key returns 429* | "The UI sends the X-Simulate-Failure header. key_1 fails with 429, LiteLLM's Router fails over and key_2 answers with fallback_attempts 1. The same happens with a 500. When every key fails we get a clean 503 shown as a readable message, not a broken screen." Click *Open stored history*: "and the failed message was not saved." |
| 1:50–2:20 | Terminal: `python scripts/smoke_test.py https://<backend> --origin https://<frontend>` (or the curl commands from the README) | "The same contract from the command line: cache hit, key_1=429 and key_1=500 land on key_2, *=429 is a 503, the history endpoint returns messages oldest first, unknown sessions are 404, and invalid input is 422." |
| 2:20–2:50 | Code: `backend/app/llm/gateway.py`, `simulation.py`, `db.py` | "One Router model group per key with ordered fallbacks and no retries. LiteLLM's cache with caching groups, so the key is model plus the full message list. The simulation hook is a LiteLLM pre-call hook scoped to one request. History is written to SQLite in one transaction, only after a successful reply. Locally everything starts with `make up`, and the tests run in Docker with `make test`." |
| 2:50–3:00 | README | "Design decisions and verification steps are in the README. Thanks!" |
