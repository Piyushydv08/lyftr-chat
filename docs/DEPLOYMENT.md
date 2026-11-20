# Deployment guide (about 15 minutes)

The backend goes on **Render** (Docker, free plan) and the frontend on **Vercel** (free). Both are public and need no login to use.
Alternatives are listed at the end.

## 0. Prerequisites

1. **Two Gemini API keys** from https://aistudio.google.com/apikey.
   - Create the second key with *Create API key in new project*. Gemini rate limits are counted **per project, not per key**, so two keys in one project share one quota and real failover would not help.
   - Any LiteLLM provider works. Set `LLM_MODEL` / `LLM_MODELS` accordingly.
2. **A public GitHub repository** with this code:
   ```bash
   cd lyftr-chat
   git init && git add . && git commit -m "LLM chatbot with key failover and caching"
   git branch -M main
   git remote add origin https://github.com/Piyushydv08/lyftr-chat.git
   git push -u origin main
   ```
   `.env` is git-ignored, so your real keys never get committed. Check with `git status` before pushing.

## 1. Backend on Render

1. https://dashboard.render.com → **New → Web Service** → connect the GitHub repo.
2. Settings:
   - **Language:** Docker
   - **Root Directory:** leave empty
   - **Advanced → Dockerfile Path:** `./backend/Dockerfile`
   - **Advanced → Docker Build Context Directory:** `./backend`
   - **Instance type:** Free
   - **Health Check Path:** `/health`
3. **Environment variables:**

   | Key | Value |
   |---|---|
   | `LLM_API_KEYS` | `AIza...key1,AIza...key2` |
   | `LLM_MODEL` | `gemini/gemini-2.5-flash-lite` |
   | `ENABLE_FAILURE_SIMULATION` | `true` |
   | `DATABASE_URL` | `sqlite:////data/app.db` |
   | `CACHE_TYPE` | `disk` |
   | `CACHE_DIR` | `/data/llm_cache` |
   | `ALLOWED_ORIGINS` | `http://localhost:3000` (replaced in step 3) |

4. **Create Web Service.** The first build takes about 3–5 minutes. Then open `https://lyftr-chat-c8fd.onrender.com/health`. You should see
   `{"status":"ok","keys_configured":2,"cache_enabled":true,...}`.

> One-click alternative: **New → Blueprint** with this repo uses `render.yaml` and creates the backend and a Render static-site frontend together. You fill in `LLM_API_KEYS`, `ALLOWED_ORIGINS` and `VITE_API_BASE_URL` when prompted.

## 2. Frontend on Vercel

1. https://vercel.com/new → import the repo.
2. **Root Directory:** `frontend`. The framework preset is detected as **Vite**: build `npm run build`, output `dist`.
3. **Environment variable:** `VITE_API_BASE_URL` = `https://lyftr-chat-c8fd.onrender.com` (no trailing slash).
4. **Deploy**, then copy the production URL, e.g. `https://lyftr-chat.vercel.app`.

## 3. Connect them (CORS)

Render → your backend → **Environment** → set `ALLOWED_ORIGINS` to the exact Vercel production URL, e.g.
`https://lyftr-chat.vercel.app` (comma-separate several; no trailing slash) → **Save**. Render redeploys.

If you also want Vercel preview URLs to work, set `ALLOWED_ORIGIN_REGEX` to `^https://lyftr-chat.*\.vercel\.app$`.

## 4. Verify before submitting

```bash
python scripts/smoke_test.py https://lyftr-chat-c8fd.onrender.com --origin https://lyftr-chat.vercel.app
```

Every line should be PASS. Then open the frontend, send a message, and check that the browser console shows no CORS errors.

**Free-tier sleep:** Render's free instance sleeps after 15 idle minutes, and the next request takes about 50 s to wake it. Open `/health` just before you submit and before you record the demo.

**Persistence on the free tier:** the container disk is reset on every redeploy. History persists while the instance runs; locally, the Docker volume persists properly. For durable cloud storage use one of these:
- Render: a paid instance with a Disk mounted at `/data`;
- Railway (below).

## Alternatives

- **Railway (backend with a persistent volume):**
  1. New Project → Deploy from GitHub repo → service *Settings → Root Directory* `/backend` (it builds the Dockerfile).
  2. *Volumes* → add a volume mounted at `/data`.
  3. Add the same variables as above.
  4. *Networking → Generate Domain*.
- **Netlify / Cloudflare Pages (frontend):** base directory `frontend`, build `npm run build`, publish `dist`, env `VITE_API_BASE_URL`.
- **Frontend as a container:** `docker build -t lyftr-frontend ./frontend && docker run -p 3000:3000 -e API_BASE_URL=https://lyftr-chat-c8fd.onrender.com lyftr-frontend`. The backend URL is read at container start.
