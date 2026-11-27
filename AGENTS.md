# GPU Control Center

@/home/trx50/.codex/RTK.md

- This is the development repository for GPU Control Center.
- Keep queue ownership and task transitions in the central API. MCP and GUI call the same API; do not create parallel task state in either client.
- Demo metrics and demo execution must remain explicitly labeled simulated. Host telemetry does not imply a real inference adapter exists.
- Use Alembic migrations for schema changes, preserving existing volumes.
- Use `make test` for isolated PostgreSQL integration tests and `make test-mcp` for the live protocol/worker smoke test.
- Do not commit `.env`, tokens, generated artifacts, or database data.
- On this host use `scripts/stop.sh` for shutdown; Docker may fail to send signals directly. Do not restart the shared Docker daemon.
