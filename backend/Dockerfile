# ---- builder: resolve and install dependencies into an isolated virtualenv ----
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- test: builder + dev deps + tests (`make test` builds this target) ----
FROM builder AS test
ENV LITELLM_LOCAL_MODEL_COST_MAP=True
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
WORKDIR /src
COPY . .
RUN python -m pytest

# ---- runtime: slim image with only the venv and the app code ----
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    DATABASE_URL=sqlite:////data/app.db \
    CACHE_DIR=/data/llm_cache \
    PORT=8000
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown app:app /data
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY app ./app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"
# PORT is honoured so the same image runs on Render/Railway (they inject PORT).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
