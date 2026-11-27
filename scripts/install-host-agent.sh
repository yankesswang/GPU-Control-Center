#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
repo_dir=$PWD
agent_env_file=${AGENT_ENV_FILE:-$repo_dir/.env}
[[ "$agent_env_file" = /* ]] || agent_env_file="$repo_dir/$agent_env_file"
[[ -f "$agent_env_file" ]] || { echo 'Agent environment file missing. Run ./scripts/start.sh or ./scripts/connect-host.sh first.'; exit 1; }
command -v uv >/dev/null || { echo 'Install uv from https://docs.astral.sh/uv/ first'; exit 1; }
[[ -x backend/.venv/bin/python ]] || uv venv --python 3.12 backend/.venv
uv pip install --python backend/.venv/bin/python -r backend/requirements.txt
mkdir -p "$HOME/.config/systemd/user"
api_port=$(sed -n 's/^API_PORT=//p' "$agent_env_file")
cat > "$HOME/.config/systemd/user/gpu-control-center-agent.service" <<EOF
[Unit]
Description=GPU Control Center native host agent
After=network.target

[Service]
WorkingDirectory=$repo_dir/backend
EnvironmentFile="$agent_env_file"
Environment=API_URL=http://127.0.0.1:${api_port:-8088}
ExecStart="$repo_dir/backend/.venv/bin/python" -m app.agent --mode host
Restart=on-failure
RestartSec=5
TimeoutStopSec=15

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable gpu-control-center-agent.service
systemctl --user restart gpu-control-center-agent.service
systemctl --user status gpu-control-center-agent.service --no-pager
