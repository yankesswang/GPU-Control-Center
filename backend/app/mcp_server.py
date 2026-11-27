"""MCP tools delegate to the same HTTP API used by the GUI. No separate queue."""
import argparse
import os
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

API_URL = os.getenv('API_URL', 'http://127.0.0.1:8088').rstrip('/')
mcp = FastMCP(
    'GPU Control Center', host='0.0.0.0', port=8001,
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=['localhost:*', '127.0.0.1:*', 'mcp:*'],
        allowed_origins=['http://localhost:*', 'http://127.0.0.1:*'],
    ),
    instructions='Manage centrally scheduled GPU jobs. Use list_resources to discover real Ollama models and select their exact id. submit_job returns immediately; use get_job and explain_wait to track output. Demo execution exists only when explicitly enabled. Never infer that an offline worker has stopped running. Running Ollama cancellation waits for a confirmed generation boundary.',
)


async def request(path: str, method: str = 'GET', body=None):
    async with httpx.AsyncClient(base_url=API_URL, timeout=15) as client:
        try:
            response = await client.request(method, '/api' + path, json=body)
        except httpx.RequestError as exc:
            raise ValueError('Central API is unavailable; no new execution was confirmed.') from exc
        if response.is_error:
            raise ValueError(f'Central API returned HTTP {response.status_code}: {response.text[:300]}')
        return response.json()


@mcp.tool(annotations={'readOnlyHint': True})
async def list_resources() -> dict[str, Any]:
    """List registered workers, supported capabilities and overview. Demo metrics are marked simulated."""
    # Sequential calls keep the output straightforward; each is a separate API snapshot.
    return {'nodes': await request('/nodes'), 'models': await request('/models'),
            'overview': await request('/overview')}


@mcp.tool()
async def submit_job(
    name: Annotated[str, Field(min_length=1, max_length=120)],
    model: Annotated[str, Field(min_length=1, max_length=100)],
    prompt: Annotated[str, Field(max_length=32000)] = '',
    max_tokens: Annotated[int, Field(ge=1, le=2048)] = 256,
    source: Annotated[str, Field(min_length=1, max_length=80)] = 'mcp',
    steps: Annotated[int, Field(ge=5, le=120)] = 20,
    priority: Annotated[int, Field(ge=0, le=10)] = 0,
) -> dict[str, Any]:
    """Queue inference on an exact model id from list_resources. Real Ollama models require a prompt. Returns immediately."""
    return await request('/jobs', 'POST', dict(name=name, source=source, model=model, prompt=prompt, max_tokens=max_tokens, steps=steps, priority=priority))


@mcp.tool(annotations={'readOnlyHint': True})
async def get_job(job_id: str) -> dict[str, Any]:
    """Read a job's current status, progress, event log and saved result."""
    return await request('/jobs/' + safe_id(job_id))


@mcp.tool(annotations={'readOnlyHint': True})
async def explain_wait(job_id: str) -> dict[str, Any]:
    """Explain the current queue rank, compatible workers, occupied slots and unknown executions. No ETA guarantee."""
    return await request('/jobs/' + safe_id(job_id) + '/explain')


@mcp.tool()
async def cancel_job(job_id: str) -> dict[str, Any]:
    """Request cancellation. A running job releases its slot only after the worker confirms termination."""
    return await request('/jobs/' + safe_id(job_id) + '/cancel', 'POST')


def safe_id(value: str) -> str:
    from uuid import UUID
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError('job_id must be a UUID returned by submit_job') from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--transport', choices=['stdio', 'streamable-http'], default='streamable-http')
    args = parser.parse_args()
    mcp.run(transport=args.transport)


if __name__ == '__main__':
    main()
