#!/bin/sh
# Writes /config.js from the API_BASE_URL environment variable so one image works for any
# backend URL (no rebuild needed). Runs automatically via the nginx image's entrypoint.
set -eu

target="${CONFIG_JS_PATH:-/usr/share/nginx/html/config.js}"
api_base_url="${API_BASE_URL:-http://localhost:8000}"

# Escape backslashes and double quotes for a JS string literal.
escaped=$(printf '%s' "$api_base_url" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
printf 'window.__APP_CONFIG__ = { API_BASE_URL: "%s" };\n' "$escaped" > "$target"
echo "runtime-config: API_BASE_URL=${api_base_url}"
