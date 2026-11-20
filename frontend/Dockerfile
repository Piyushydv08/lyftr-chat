# ---- deps: install once, reused by the test and build stages ----
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# ---- test: `make test` builds this target ----
FROM deps AS test
COPY . .
RUN npm test

# ---- build: type-check and bundle the static site ----
FROM deps AS build
COPY . .
# Optional build-time default; the runtime API_BASE_URL (config.js) takes precedence.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# ---- runtime: nginx serving static files only (~50 MB) ----
FROM nginx:1.29-alpine AS runtime
ENV API_BASE_URL=http://localhost:8000
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --chmod=755 docker/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:3000/ || exit 1
