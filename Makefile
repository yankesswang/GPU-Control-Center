.PHONY: up down logs test test-mcp
up:
	./scripts/start.sh
down:
	./scripts/stop.sh
logs:
	docker compose logs -f --tail=100
test:
	docker compose exec -T api pytest -q
test-mcp:
	docker compose exec -T mcp python tests/smoke_mcp.py
