#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  umask 077
  cp .env.example .env
  python3 - <<'PY'
from pathlib import Path
import secrets
p = Path('.env')
p.write_text(p.read_text().replace('replace-with-random-password', secrets.token_hex(24)).replace('replace-with-random-agent-token', secrets.token_hex(32)))
PY
fi
docker compose up -d --build --wait --wait-timeout 180
docker compose ps
web_port=$(sed -n 's/^WEB_PORT=//p' .env)
api_port=$(sed -n 's/^API_PORT=//p' .env)
printf '\nGUI: http://localhost:%s\nAPI docs: http://localhost:%s/docs\n' "${web_port:-5180}" "${api_port:-8088}"

mcp_port=$(sed -n "s/^MCP_PORT=//p" .env)
printf "MCP: http://localhost:%s/mcp\n" "${mcp_port:-8090}"
