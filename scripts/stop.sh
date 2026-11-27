#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if systemctl --user is-active --quiet gpu-control-center-agent.service 2>/dev/null; then
  systemctl --user stop gpu-control-center-agent.service
fi
# Shut down from inside each container. This also works on hosts where
# AppArmor prevents the Docker daemon from sending stop signals.
for service in web mcp demo-agent api db; do
  container_id=$(docker ps -q --filter label=com.docker.compose.project=gpu-control-center --filter "label=com.docker.compose.service=$service")
  [[ -n "$container_id" ]] || continue
  docker update --restart=no "$container_id" >/dev/null
  case "$service" in
    web) docker exec "$container_id" nginx -s quit ;;
    mcp) docker exec "$container_id" python -c 'import os,signal; os.kill(1,signal.SIGINT)' ;;
    demo-agent) docker exec "$container_id" python -c 'import os,signal; os.kill(1,signal.SIGINT)' ;;
    api) docker exec "$container_id" python -c 'import psutil,signal; [p.send_signal(signal.SIGINT) for p in psutil.process_iter(["name"]) if p.info["name"] == "uvicorn"]' ;;
    db)
      # PostgreSQL exiting terminates the concurrent docker-exec process too.
      # Verify the container's exit below, rather than treating that as a failure.
      docker exec --user postgres "$container_id" sh -c 'pg_ctl stop -D "$PGDATA" -m fast -w -t 20' || true
      ;;
  esac
  timeout 25 docker wait "$container_id" >/dev/null
done
docker compose down
