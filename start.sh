#!/bin/sh
set -e
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8765}"
export SERVE_STATIC="${SERVE_STATIC:-0}"
python3 /usr/share/nginx/html/server.py &
exec nginx -g 'daemon off;'
