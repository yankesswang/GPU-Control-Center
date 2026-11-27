#!/usr/bin/env bash
# Run on the host being connected; never copy the central database credentials.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ $# -eq 2 ]] || { echo "Usage: $0 <central-api-url> <unique-node-id>"; exit 1; }
api_url=${1%/}
node_id=$2
origin_pattern='^https?://(\[[0-9a-fA-F:]+\]|[a-zA-Z0-9._-]+)(:[0-9]+)?$'
[[ "$api_url" =~ $origin_pattern ]] || { echo 'Use an http(s) API origin, without a path or credentials.'; exit 1; }
[[ "$node_id" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$ ]] || { echo 'Node ID must be 1–100 letters, numbers, dots, underscores or hyphens.'; exit 1; }
command -v uv >/dev/null || { echo 'Install uv first, then run this command again.'; exit 1; }
systemctl --user show-environment >/dev/null
[[ ! -e .env.agent ]] || { echo '.env.agent already exists. Edit it and run AGENT_ENV_FILE=.env.agent ./scripts/install-host-agent.sh to reconnect.'; exit 1; }
read -r -s -p 'Central AGENT_TOKEN: ' agent_token
printf '\n'
[[ "$agent_token" =~ ^[a-zA-Z0-9._-]+$ ]] || { echo 'Token must contain only letters, numbers, dots, underscores or hyphens.'; exit 1; }
umask 077
# noclobber protects an existing configuration if two installers run together.
(set -o noclobber; printf 'API_URL=%s\nNODE_ID=%s\nAGENT_TOKEN=%s\nOLLAMA_URL=http://127.0.0.1:11434\nLMSTUDIO_URL=http://127.0.0.1:1234\nCOMFYUI_URL=http://127.0.0.1:8188\n' "$api_url" "$node_id" "$agent_token" > .env.agent)
unset agent_token
AGENT_ENV_FILE=.env.agent ./scripts/install-host-agent.sh
echo 'Agent installed. Check the host list for its first heartbeat; service startup alone does not confirm connection.'
