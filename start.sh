#!/bin/sh
# Bootstrap and run the complete product using Docker alone.
set -eu
LOGCHAT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$LOGCHAT_ROOT"
export LOGCHAT_HOST_ROOT=$LOGCHAT_ROOT
if ! command -v docker >/dev/null 2>&1; then
  echo 'Install Docker with Compose, then run ./start.sh again.' >&2
  exit 1
fi
if [ -n "${DOCKER_CONTEXT:-}" ]; then
  LOGCHAT_DOCKER_ENDPOINT=$(docker context inspect "$DOCKER_CONTEXT" --format '{{.Endpoints.docker.Host}}')
elif [ -n "${DOCKER_HOST:-}" ]; then
  LOGCHAT_DOCKER_ENDPOINT=$DOCKER_HOST
else
  LOGCHAT_DOCKER_ENDPOINT=$(docker context inspect "$(docker context show)" --format '{{.Endpoints.docker.Host}}')
fi
case "$LOGCHAT_DOCKER_ENDPOINT" in
  unix://*|npipe://*) ;;
  *) echo 'Logchat requires a local Docker daemon/socket. Select your local Docker context and retry.' >&2; exit 1 ;;
esac
unset DOCKER_CONTEXT
export DOCKER_HOST=$LOGCHAT_DOCKER_ENDPOINT
docker info >/dev/null 2>&1 || { echo 'Start Docker, then run ./start.sh again.' >&2; exit 1; }
docker compose version >/dev/null
LOGCHAT_DEMO=false
case "${1:-}" in
  --demo) LOGCHAT_DEMO=true ;;
  '') ;;
  *) echo 'Usage: ./start.sh [--demo]' >&2; exit 1 ;;
esac
LOGCHAT_CREDS="$LOGCHAT_ROOT/.logchat/credentials"
if [ -f .logchat/.secrets ]; then
  LOGCHAT_SAVED_CREDS=$(sed -n 's/^LOGCHAT_CREDENTIALS_DIR=//p' .logchat/.secrets | tail -1)
  LOGCHAT_CREDS=${LOGCHAT_SAVED_CREDS:-"$HOME/.local/share/logchat/credentials"}
fi
mkdir -p "$LOGCHAT_ROOT/.logchat" "$LOGCHAT_CREDS"
chmod 700 "$LOGCHAT_ROOT/.logchat" "$LOGCHAT_CREDS"
docker run --rm --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e LOGCHAT_SETUP_CREDENTIALS_DIR=/credentials \
  -e "LOGCHAT_HOST_CREDENTIALS_DIR=$LOGCHAT_CREDS" \
  -v "$LOGCHAT_ROOT:/workspace" -v "$LOGCHAT_CREDS:/credentials" \
  -w /workspace python:3.12.13-slim python scripts/setup.py
if [ "$LOGCHAT_DEMO" = true ]; then
  docker compose --env-file .logchat/.secrets --profile docker --profile demo up -d --build --wait --wait-timeout 900
else
  docker compose --env-file .logchat/.secrets --profile docker up -d --build --wait --wait-timeout 900
fi
LOGCHAT_WEB_PORT=$(sed -n 's/^WEB_PORT=//p' .logchat/.secrets | tail -1)
echo "Open http://127.0.0.1:${LOGCHAT_WEB_PORT:-3000} to create a local account and connect a project."
if [ "$LOGCHAT_DEMO" = true ]; then
  echo 'Demo Docker sources: logchat-demo-api (dev), logchat-demo-prod (prod). Retention: 24 hours.'
  echo 'Optional keyless demo MCP: http://demo-mcp:8090/mcp (inside this Docker stack).'
fi
