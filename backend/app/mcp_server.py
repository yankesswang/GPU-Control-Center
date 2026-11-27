"""MCP server delegating to the central GPU Control Center HTTP API. No separate queue.

Supports:
- Streamable HTTP (/mcp), legacy SSE (/sse), and stdio transports
- Bearer token / X-API-Key authentication backed by central API tokens (with RBAC: read / infer / admin)
- Configurable host/origin allowlists for LAN, VPN, or reverse-proxy exposure
- 15 agent-ready tools (sync + async inference, job lifecycle, node/model discovery, energy analytics, model/host control)
- MCP resources (gpu://overview, gpu://nodes, gpu://models) and prompt templates
"""
import argparse
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import hmac
import json
import os
import re
import time
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse
import uvicorn

from .mcp_auth import ROLE_LEVEL, role_allows

API_URL = os.getenv('API_URL', 'http://127.0.0.1:8088').rstrip('/')
TERMINAL_STATUSES = {'completed', 'failed', 'cancelled', 'unknown'}
VALID_JOB_STATUSES = {'queued', 'running', 'cancelling', 'completed', 'failed', 'cancelled', 'unknown'}
NODE_ID_RE = re.compile(r'^[A-Za-z0-9._:@ -]{1,100}$')


@dataclass(frozen=True)
class McpCaller:
    authenticated: bool
    role: str
    source_tag: str | None
    client_name: str
    token_id: str | None = None


def _default_role() -> str:
    role = os.getenv('MCP_DEFAULT_ROLE', 'admin').strip().lower()
    return role if role in ROLE_LEVEL else 'infer'


def _api_url() -> str:
    return os.getenv('API_URL', API_URL).rstrip('/')


def _agent_token() -> str:
    return os.getenv('AGENT_TOKEN', '').strip()


def _static_api_key() -> str:
    return os.getenv('MCP_API_KEY', '').strip()


def _require_auth_env() -> bool:
    return os.getenv('MCP_REQUIRE_AUTH', 'false').strip().lower() == 'true'


def _parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(',') if item.strip()]


def _build_transport_security() -> TransportSecuritySettings:
    hosts_raw = os.getenv('MCP_ALLOWED_HOSTS', '').strip()
    origins_raw = os.getenv('MCP_ALLOWED_ORIGINS', '').strip()
    if hosts_raw == '*' or origins_raw == '*':
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    allowed_hosts = ['localhost:*', '127.0.0.1:*', 'mcp:*']
    allowed_origins = ['http://localhost:*', 'http://127.0.0.1:*']
    for h in _parse_csv(hosts_raw):
        if h not in allowed_hosts:
            allowed_hosts.append(h)
    for o in _parse_csv(origins_raw):
        if o not in allowed_origins:
            allowed_origins.append(o)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


_caller_var: ContextVar[McpCaller | None] = ContextVar('gcc_mcp_caller', default=None)


def current_caller() -> McpCaller:
    caller = _caller_var.get()
    if caller is not None:
        return caller
    return McpCaller(
        authenticated=False,
        role=_default_role(),
        source_tag=os.getenv('MCP_SOURCE_TAG', '').strip() or None,
        client_name='local',
    )


def require_role(required: Literal['read', 'infer', 'admin']) -> McpCaller:
    caller = current_caller()
    if not role_allows(caller.role, required):
        raise ValueError(
            f"Permission denied: this action requires '{required}' role, "
            f"but the current MCP client ('{caller.client_name}') has '{caller.role}' role."
        )
    return caller


def resolve_source(requested: str) -> str:
    caller = current_caller()
    cleaned = (requested or '').strip()
    if caller.source_tag and cleaned in ('', 'mcp'):
        return caller.source_tag
    return cleaned or 'mcp'


mcp = FastMCP(
    'GPU Control Center',
    host=os.getenv('MCP_HOST', '0.0.0.0'),
    port=int(os.getenv('MCP_INTERNAL_PORT', '8001')),
    stateless_http=True,
    json_response=True,
    transport_security=_build_transport_security(),
    instructions=(
        'Manage centrally scheduled local/LAN GPU workloads across Ollama, LM Studio, and ComfyUI hosts. '
        'Use list_models or list_nodes to discover online hosts, GPU VRAM, and exact model IDs. '
        'For single-turn text generation, prefer run_inference (submits, waits, and returns generated text directly). '
        'For async/batch execution, use submit_job followed by wait_for_job, get_job, or explain_wait. '
        'Demo metrics/execution exist only when explicitly enabled and are marked simulated. '
        'Never infer that an offline worker has stopped running; unknown jobs retain their host slot until confirmed. '
        'Running job cancellation waits for a confirmed generation boundary.'
    ),
)


async def request(
    path: str,
    method: str = 'GET',
    body: Any = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> Any:
    async with httpx.AsyncClient(base_url=_api_url(), timeout=timeout) as client:
        try:
            response = await client.request(method, '/api' + path, json=body, params=params, headers=headers)
        except httpx.RequestError as exc:
            raise ValueError('Central API is unavailable; no new execution was confirmed.') from exc
        if response.is_error:
            detail = response.text[:300]
            try:
                payload = response.json()
                if isinstance(payload, dict) and isinstance(payload.get('detail'), str):
                    detail = payload['detail']
            except Exception:
                pass
            raise ValueError(f'Central API returned HTTP {response.status_code}: {detail}')
        return response.json()


def safe_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError('job_id must be a UUID returned by submit_job') from exc


def safe_node_id(value: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned or '..' in cleaned or '/' in cleaned or '\\' in cleaned or not NODE_ID_RE.match(cleaned):
        raise ValueError('Invalid node_id; use an exact host id from list_nodes or list_resources.')
    return quote(cleaned, safe='')


def _extract_job_text(job: dict[str, Any]) -> str:
    result = job.get('result') or {}
    if isinstance(result, dict) and isinstance(result.get('text'), str) and result['text']:
        return result['text']
    runtime_state = job.get('runtime_state') or {}
    if isinstance(runtime_state, dict) and isinstance(runtime_state.get('text'), str):
        return runtime_state['text']
    return ''


def _compact_node(node: dict[str, Any], active_jobs_by_node: dict[str, dict[str, Any]]) -> dict[str, Any]:
    metrics = node.get('metrics') or {}
    gpus_out = []
    for g in metrics.get('gpus') or []:
        total = g.get('memory_total_mb')
        used = g.get('memory_used_mb')
        free = max(0, total - used) if isinstance(total, (int, float)) and isinstance(used, (int, float)) else None
        gpus_out.append({
            'index': g.get('index'),
            'name': g.get('name'),
            'utilization_pct': g.get('utilization'),
            'memory_used_mb': used,
            'memory_total_mb': total,
            'memory_free_mb': free,
            'temperature_c': g.get('temperature_c'),
            'power_w': g.get('power_w'),
        })
    runtimes: dict[str, Any] = {}
    for rt in ('lmstudio', 'ollama'):
        info = metrics.get(rt)
        if isinstance(info, dict):
            runtimes[rt] = {
                'status': info.get('status'),
                'models': info.get('models'),
                'gpu_placement_supported': info.get('gpu_placement_supported'),
                'loaded_models': [
                    {
                        'name': m.get('name'),
                        'vram_bytes': m.get('vram_bytes'),
                        'observed_gpus': m.get('observed_gpus'),
                    }
                    for m in (info.get('loaded_models') or [])
                ],
            }
    comfy = metrics.get('comfyui')
    if isinstance(comfy, dict):
        runtimes['comfyui'] = {
            'status': comfy.get('status'),
            'version': comfy.get('version'),
            'running_prompts': len(comfy.get('running') or []),
            'pending_prompts': len(comfy.get('pending') or []),
            'gpu_placement_supported': comfy.get('gpu_placement_supported'),
            'loaded_models': [m.get('name') for m in (comfy.get('loaded_models') or comfy.get('active_models') or [])],
        }
    holder = active_jobs_by_node.get(node['id'])
    return {
        'id': node['id'],
        'name': node['name'],
        'mode': node['mode'],
        'status': node['status'],
        'maintenance': node['maintenance'],
        'default_runtime': node.get('default_runtime'),
        'auth_required': bool(node.get('auth_required')),
        'remote': bool(node.get('connection_host') or metrics.get('remote')),
        'simulated': bool(metrics.get('simulated') or node['mode'] == 'demo'),
        'last_seen': node.get('last_seen'),
        'cpu_percent': metrics.get('cpu_percent'),
        'ram_percent': metrics.get('ram_percent'),
        'ram_total_gb': metrics.get('ram_total_gb'),
        'gpus': gpus_out,
        'runtimes': runtimes,
        'active_job': holder,
    }


async def _pick_default_model() -> str:
    models = await request('/models')
    nodes = await request('/nodes')
    preferred_runtimes = {
        n['id']: n.get('default_runtime')
        for n in nodes
        if n.get('status') == 'online' and not n.get('maintenance')
    }
    candidates = [
        m for m in models
        if m.get('available')
        and not m.get('hidden')
        and m.get('id') != 'demo.progress'
        and m.get('model_type', 'llm') == 'llm'
    ]
    if not candidates:
        fallback = [m['id'] for m in models if m.get('available')]
        hint = f" Available non-default models: {', '.join(fallback)}" if fallback else ' No available models found on online hosts.'
        raise ValueError(f'No available LLM model to auto-select.{hint} Pass an explicit model id from list_models.')

    def sort_key(m: dict[str, Any]) -> tuple[int, str]:
        matches_pref = any(preferred_runtimes.get(nid) == m.get('runtime') for nid in (m.get('nodes') or []))
        return (0 if matches_pref else 1, m['id'])

    candidates.sort(key=sort_key)
    return candidates[0]['id']


async def _poll_job_until_done(
    job_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    ctx: Context | None = None,
) -> dict[str, Any]:
    safe = safe_id(job_id)
    deadline = time.monotonic() + timeout_seconds
    interval = max(0.2, min(poll_interval_seconds, 5.0))
    last_job: dict[str, Any] = {}
    poll_count = 0

    while True:
        last_job = await request('/jobs/' + safe)
        status = last_job.get('status', '')
        if ctx is not None:
            try:
                progress = float(last_job.get('progress') or 0)
                phase = (last_job.get('runtime_state') or {}).get('phase') or status
                await ctx.report_progress(progress=progress, total=100.0, message=f'{status}: {phase}')
            except Exception:
                pass
        if status in TERMINAL_STATUSES:
            text = _extract_job_text(last_job)
            result = last_job.get('result')
            return {
                'job_id': last_job['id'],
                'status': status,
                'completed': status == 'completed',
                'timed_out': False,
                'model': last_job.get('model'),
                'node_id': last_job.get('node_id'),
                'source': last_job.get('source'),
                'text': text,
                'tokens_per_second': result.get('tokens_per_second') if isinstance(result, dict) else None,
                'result': result,
                'error': last_job.get('error'),
                'explanation': None,
                'job': last_job,
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            explanation = None
            try:
                explanation = await request('/jobs/' + safe + '/explain')
            except Exception:
                pass
            return {
                'job_id': last_job['id'],
                'status': status,
                'completed': False,
                'timed_out': True,
                'model': last_job.get('model'),
                'node_id': last_job.get('node_id'),
                'source': last_job.get('source'),
                'text': _extract_job_text(last_job),
                'tokens_per_second': None,
                'result': last_job.get('result'),
                'error': last_job.get('error'),
                'explanation': explanation,
                'job': last_job,
            }
        poll_count += 1
        step = interval if poll_count < 10 else min(max(interval, 0.75), 2.0)
        await asyncio.sleep(min(step, remaining))


# ---------------------------------------------------------------------------
# Discovery & Telemetry Tools (role: read)
# ---------------------------------------------------------------------------

@mcp.tool(annotations={'readOnlyHint': True})
async def list_resources() -> dict[str, Any]:
    """List registered workers, supported capabilities and overview. Demo metrics are marked simulated."""
    require_role('read')
    return {
        'nodes': await request('/nodes'),
        'models': await request('/models'),
        'overview': await request('/overview'),
    }


@mcp.tool(annotations={'readOnlyHint': True})
async def list_nodes(
    compact: bool = True,
    include_offline: bool = True,
) -> dict[str, Any]:
    """List compute hosts with GPU VRAM, utilization, temperature, power, loaded models, and active slot holders."""
    require_role('read')
    nodes = await request('/nodes')
    if not include_offline:
        nodes = [n for n in nodes if n.get('status') != 'offline']
    if not compact:
        return {'count': len(nodes), 'nodes': nodes}
    jobs = await request('/jobs', params={'limit': 50})
    active_by_node: dict[str, dict[str, Any]] = {}
    for j in jobs:
        if j.get('status') in ('running', 'cancelling', 'unknown') and j.get('node_id'):
            active_by_node[j['node_id']] = {
                'id': j['id'],
                'name': j['name'],
                'source': j['source'],
                'model': j['model'],
                'status': j['status'],
            }
    items = [_compact_node(n, active_by_node) for n in nodes]
    return {'count': len(items), 'nodes': items}


@mcp.tool(annotations={'readOnlyHint': True})
async def list_models(
    runtime: Literal['all', 'ollama', 'lmstudio', 'comfyui', 'demo'] = 'all',
    only_available: bool = True,
    only_llm: bool = False,
    include_hidden: bool = False,
) -> dict[str, Any]:
    """Discover installed and available models filtered by runtime, availability, and model type."""
    require_role('read')
    models = await request('/models')
    filtered = []
    for m in models:
        if runtime != 'all' and m.get('runtime') != runtime:
            continue
        if only_available and not m.get('available'):
            continue
        if only_llm and m.get('model_type', 'llm') != 'llm':
            continue
        if not include_hidden and m.get('hidden'):
            continue
        filtered.append(m)
    return {'count': len(filtered), 'models': filtered}


@mcp.tool(annotations={'readOnlyHint': True})
async def get_energy_analytics(
    range: Literal['1h', '6h', '24h', '7d', '30d', '90d', 'all'] = '24h',
    node_id: Annotated[str, Field(max_length=100)] = '',
    include_simulated: bool = False,
) -> dict[str, Any]:
    """Query live GPU power draw, kWh usage, Time-of-Use cost estimates, CO2, and per-GPU/model breakdown."""
    require_role('read')
    params: dict[str, Any] = {'range': range, 'include_simulated': str(include_simulated).lower()}
    if node_id.strip():
        params['node_id'] = node_id.strip()
    data = await request('/energy/analytics', params=params)
    return {
        'range': data.get('range'),
        'generated_at': data.get('generated_at'),
        'currency': (data.get('tariff') or {}).get('currency'),
        'tariff_mode': (data.get('tariff') or {}).get('tariff_mode'),
        'live': data.get('live'),
        'summary': data.get('summary'),
        'node_breakdown': data.get('node_breakdown'),
        'gpu_breakdown': data.get('gpu_breakdown'),
        'model_breakdown': data.get('model_breakdown'),
        'insights': data.get('insights'),
    }


# ---------------------------------------------------------------------------
# Inference & Job Lifecycle Tools (role: read / infer)
# ---------------------------------------------------------------------------

@mcp.tool()
async def run_inference(
    prompt: Annotated[str, Field(min_length=1, max_length=32000)],
    model: Annotated[str, Field(max_length=100)] = '',
    max_tokens: Annotated[int, Field(ge=1, le=2048)] = 512,
    name: Annotated[str, Field(min_length=1, max_length=120)] = 'Agent inference',
    priority: Annotated[int, Field(ge=0, le=10)] = 0,
    timeout_seconds: Annotated[int, Field(ge=5, le=600)] = 120,
    source: Annotated[str, Field(min_length=1, max_length=80)] = 'mcp',
    sla_class: Literal['realtime', 'interactive', 'standard', 'batch', 'background'] | None = None,
    deadline_seconds: Annotated[int, Field(ge=5, le=86400)] | None = None,
    preferred_node_id: Annotated[str, Field(max_length=100)] | None = None,
    fallback_models: list[str] | None = None,
    allow_defer: bool | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Submit a text inference prompt through the central dynamic GPU scheduler, wait for completion, and return the generated text.

    If `model` is omitted, automatically picks an available LLM matching an online host's preferred runtime.
    Optional scheduling hints (`sla_class`, `deadline_seconds`, `preferred_node_id`, `fallback_models`, `allow_defer`) guide dynamic routing.
    If the job does not finish within `timeout_seconds`, it remains queued/running and its job_id + wait explanation are returned.
    """
    require_role('infer')
    chosen_model = model.strip() or await _pick_default_model()
    payload: dict[str, Any] = {
        'name': name,
        'source': resolve_source(source),
        'model': chosen_model,
        'prompt': prompt,
        'max_tokens': max_tokens,
        'steps': 20,
        'priority': priority,
    }
    if sla_class is not None:
        payload['sla_class'] = sla_class
    if deadline_seconds is not None:
        payload['deadline_seconds'] = deadline_seconds
    if preferred_node_id:
        payload['preferred_node_id'] = preferred_node_id.strip()
    if fallback_models:
        payload['fallback_models'] = fallback_models
    if allow_defer is not None:
        payload['allow_defer'] = allow_defer
    job = await request('/jobs', 'POST', payload)
    return await _poll_job_until_done(job['id'], timeout_seconds=timeout_seconds, poll_interval_seconds=0.4, ctx=ctx)


@mcp.tool()
async def submit_job(
    name: Annotated[str, Field(min_length=1, max_length=120)],
    model: Annotated[str, Field(min_length=1, max_length=100)],
    prompt: Annotated[str, Field(max_length=32000)] = '',
    max_tokens: Annotated[int, Field(ge=1, le=2048)] = 256,
    source: Annotated[str, Field(min_length=1, max_length=80)] = 'mcp',
    steps: Annotated[int, Field(ge=5, le=120)] = 20,
    priority: Annotated[int, Field(ge=0, le=10)] = 0,
    sla_class: Literal['realtime', 'interactive', 'standard', 'batch', 'background'] | None = None,
    deadline_seconds: Annotated[int, Field(ge=5, le=86400)] | None = None,
    preferred_node_id: Annotated[str, Field(max_length=100)] | None = None,
    fallback_models: list[str] | None = None,
    allow_defer: bool | None = None,
) -> dict[str, Any]:
    """Queue inference on an exact model id from list_models or list_resources. Real LLM models require a prompt. Returns immediately."""
    require_role('infer')
    payload: dict[str, Any] = dict(
        name=name,
        source=resolve_source(source),
        model=model,
        prompt=prompt,
        max_tokens=max_tokens,
        steps=steps,
        priority=priority,
    )
    if sla_class is not None:
        payload['sla_class'] = sla_class
    if deadline_seconds is not None:
        payload['deadline_seconds'] = deadline_seconds
    if preferred_node_id:
        payload['preferred_node_id'] = preferred_node_id.strip()
    if fallback_models:
        payload['fallback_models'] = fallback_models
    if allow_defer is not None:
        payload['allow_defer'] = allow_defer
    return await request('/jobs', 'POST', payload)


@mcp.tool(annotations={'readOnlyHint': True})
async def get_scheduler_state(
    decision_limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Inspect the dynamic scheduler policy, per-node health & warm models, multi-service fair-share pressure, live queue plan, and recent decisions."""
    require_role('read')
    return await request('/scheduler/state', params={'decision_limit': decision_limit})


@mcp.tool(annotations={'readOnlyHint': True})
async def simulate_dispatch(
    model: Annotated[str, Field(min_length=1, max_length=100)],
    source: Annotated[str, Field(min_length=1, max_length=80)] = 'mcp',
    priority: Annotated[int, Field(ge=0, le=10)] = 0,
    max_tokens: Annotated[int, Field(ge=1, le=2048)] = 256,
    sla_class: Literal['realtime', 'interactive', 'standard', 'batch', 'background'] = 'standard',
    deadline_seconds: Annotated[int, Field(ge=5, le=86400)] | None = None,
    preferred_node_id: Annotated[str, Field(max_length=100)] | None = None,
    fallback_models: list[str] | None = None,
    allow_defer: bool | None = None,
) -> dict[str, Any]:
    """Dry-run the dynamic scheduler against current live cluster state to preview which host and action would be chosen without queueing a job."""
    require_role('read')
    body: dict[str, Any] = {
        'model': model,
        'source': resolve_source(source),
        'priority': priority,
        'max_tokens': max_tokens,
        'sla_class': sla_class,
    }
    if deadline_seconds is not None:
        body['deadline_seconds'] = deadline_seconds
    if preferred_node_id:
        body['preferred_node_id'] = preferred_node_id.strip()
    if fallback_models:
        body['fallback_models'] = fallback_models
    if allow_defer is not None:
        body['allow_defer'] = allow_defer
    return await request('/scheduler/simulate', 'POST', body)


@mcp.tool(annotations={'readOnlyHint': True})
async def wait_for_job(
    job_id: str,
    timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 60,
    poll_interval_seconds: Annotated[float, Field(ge=0.2, le=5.0)] = 0.5,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Wait server-side until a job finishes (completed/failed/cancelled/unknown) or times out."""
    require_role('read')
    return await _poll_job_until_done(
        job_id=job_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        ctx=ctx,
    )


@mcp.tool(annotations={'readOnlyHint': True})
async def list_jobs(
    status: Annotated[str, Field(max_length=20)] = '',
    source: Annotated[str, Field(max_length=80)] = '',
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """List recent jobs with optional filtering by status (queued, running, completed, failed, cancelled, cancelling, unknown) and source."""
    require_role('read')
    params: dict[str, Any] = {'limit': limit}
    cleaned_status = status.strip().lower()
    if cleaned_status:
        if cleaned_status not in VALID_JOB_STATUSES:
            raise ValueError(f"Invalid status '{cleaned_status}'. Must be one of: {', '.join(sorted(VALID_JOB_STATUSES))}")
        params['status'] = cleaned_status
    if source.strip():
        params['source'] = source.strip()
    rows = await request('/jobs', params=params)
    summary = [
        {
            'id': j['id'],
            'name': j['name'],
            'source': j['source'],
            'model': j['model'],
            'status': j['status'],
            'progress': j['progress'],
            'node_id': j['node_id'],
            'priority': j['priority'],
            'is_model_action': bool((j.get('parameters') or {}).get('model_action')),
            'created_at': j['created_at'],
            'started_at': j['started_at'],
            'finished_at': j['finished_at'],
            'error': j['error'],
            'preview_text': _extract_job_text(j)[:240] or None,
        }
        for j in rows
    ]
    return {'count': len(summary), 'jobs': summary}


@mcp.tool(annotations={'readOnlyHint': True})
async def get_job(job_id: str) -> dict[str, Any]:
    """Read a job's current status, progress, event log and saved result."""
    require_role('read')
    return await request('/jobs/' + safe_id(job_id))


@mcp.tool(annotations={'readOnlyHint': True})
async def explain_wait(job_id: str) -> dict[str, Any]:
    """Explain the current queue rank, compatible workers, occupied slots and unknown executions. No ETA guarantee."""
    require_role('read')
    return await request('/jobs/' + safe_id(job_id) + '/explain')


@mcp.tool()
async def cancel_job(job_id: str) -> dict[str, Any]:
    """Request cancellation. A running job releases its slot only after the worker confirms termination."""
    require_role('infer')
    return await request('/jobs/' + safe_id(job_id) + '/cancel', 'POST')


@mcp.tool()
async def retry_job(job_id: str) -> dict[str, Any]:
    """Retry a confirmed failed or cancelled inference job, creating a new queued job with the original prompt and parameters."""
    require_role('infer')
    return await request('/jobs/' + safe_id(job_id) + '/retry', 'POST')


# ---------------------------------------------------------------------------
# Model & Host Control Tools (role: admin)
# ---------------------------------------------------------------------------

@mcp.tool()
async def manage_model(
    node_id: Annotated[str, Field(min_length=1, max_length=100)],
    model: Annotated[str, Field(min_length=1, max_length=100)],
    action: Literal['load', 'unload'],
    gpu_indices: list[int] | None = None,
    replace: bool = False,
    wait: bool = False,
    timeout_seconds: Annotated[int, Field(ge=5, le=600)] = 120,
    host_password: str = '',
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Load or unload a model on a specific host through the central queue. Requires 'admin' role.

    Supports optional `gpu_indices` and `replace=True` for LM Studio / ComfyUI hosts that expose GPU placement.
    If the host has operation-password protection enabled, pass `host_password` to unlock via the sanctioned auth API.
    """
    require_role('admin')
    encoded_node = safe_node_id(node_id)
    headers: dict[str, str] | None = None
    if host_password:
        unlock = await request(f'/nodes/{encoded_node}/auth/unlock', 'POST', {'password': host_password})
        token = unlock.get('token')
        if token:
            headers = {'X-Host-Auth-Token': token}
    body: dict[str, Any] = {'model': model, 'action': action, 'replace': replace}
    if gpu_indices is not None:
        body['gpu_indices'] = gpu_indices
    job = await request(f'/nodes/{encoded_node}/model-actions', 'POST', body, headers=headers)
    if not wait:
        return job
    return await _poll_job_until_done(job['id'], timeout_seconds=timeout_seconds, poll_interval_seconds=0.5, ctx=ctx)


@mcp.tool()
async def set_default_runtime(
    node_id: Annotated[str, Field(min_length=1, max_length=100)],
    runtime: Literal['lmstudio', 'ollama'],
) -> dict[str, Any]:
    """Switch a compute host's preferred default inference runtime ('lmstudio' or 'ollama'). Requires 'admin' role."""
    require_role('admin')
    return await request(f'/nodes/{safe_node_id(node_id)}/default-runtime', 'PATCH', {'runtime': runtime})


@mcp.tool()
async def set_node_maintenance(
    node_id: Annotated[str, Field(min_length=1, max_length=100)],
    enabled: bool,
) -> dict[str, Any]:
    """Enable or disable maintenance mode on a compute host (maintenance hosts stop claiming queued jobs). Requires 'admin' role."""
    require_role('admin')
    return await request(f'/nodes/{safe_node_id(node_id)}/maintenance', 'PATCH', {'enabled': enabled})


# ---------------------------------------------------------------------------
# MCP Resources (role: read)
# ---------------------------------------------------------------------------

@mcp.resource('gpu://overview', mime_type='application/json')
async def resource_overview() -> str:
    """Live cluster overview including online hosts, job counts, and available model count."""
    require_role('read')
    overview = await request('/overview')
    models = await request('/models')
    payload = {
        'overview': overview,
        'available_models': sum(1 for m in models if m.get('available') and not m.get('hidden')),
        'total_models': len(models),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.resource('gpu://nodes', mime_type='application/json')
async def resource_nodes() -> str:
    """Compact live snapshot of all compute hosts, GPU VRAM, power, and resident models."""
    data = await list_nodes(compact=True, include_offline=True)
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.resource('gpu://models', mime_type='application/json')
async def resource_models() -> str:
    """Catalog of currently available models ready for inference or control."""
    data = await list_models(only_available=True)
    return json.dumps(data, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# MCP Prompts
# ---------------------------------------------------------------------------

@mcp.prompt()
def cluster_health_check() -> str:
    """Guide an agent to inspect GPU cluster health, queue blockers, and energy draw."""
    return (
        'Inspect the GPU Control Center cluster using `list_nodes`, `list_jobs`, and `get_energy_analytics`. '
        'Summarize: (1) online vs offline/maintenance hosts and per-GPU VRAM/temperature/power, '
        '(2) any queued, blocked, or unconfirmed (`unknown`) jobs using `explain_wait` if needed, '
        '(3) currently resident models across LM Studio / Ollama / ComfyUI, and '
        '(4) live power draw and 24h electricity cost estimate. Never assume an offline worker has stopped running.'
    )


@mcp.prompt()
def run_local_llm(task: str, preferred_runtime: str = 'auto') -> str:
    """Guide an agent to pick an available local model and run a prompt via the central GPU scheduler."""
    rt_hint = '' if preferred_runtime == 'auto' else f' Prefer runtime `{preferred_runtime}` if available.'
    return (
        f'Use `list_models(only_available=True, only_llm=True)` to inspect available local LLM models.{rt_hint} '
        f'Then call `run_inference` with an exact model id to complete the following task:\n\n{task}'
    )


# ---------------------------------------------------------------------------
# Health Route & Authentication Middleware
# ---------------------------------------------------------------------------

@mcp.custom_route('/health', methods=['GET'])
async def health_route(_request: Request):
    api_ok = False
    try:
        async with httpx.AsyncClient(base_url=_api_url(), timeout=3.0) as client:
            res = await client.get('/health')
            api_ok = res.status_code == 200
    except Exception:
        api_ok = False
    return JSONResponse(
        {
            'status': 'ok' if api_ok else 'degraded',
            'service': 'gpu-control-center-mcp',
            'api_reachable': api_ok,
            'static_api_key_configured': bool(_static_api_key()),
            'require_auth_env': _require_auth_env(),
        },
        status_code=200 if api_ok else 503,
    )


def _extract_bearer_token(scope: dict[str, Any]) -> str:
    headers = {k.lower(): v.decode('latin-1') for k, v in scope.get('headers', [])}
    auth = headers.get(b'authorization', '').strip()
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return headers.get(b'x-api-key', '').strip()


async def authenticate_http_token(raw_token: str) -> tuple[McpCaller | None, int, str]:
    """Authenticate an HTTP MCP request. Returns (caller, http_status, error_detail)."""
    static_key = _static_api_key()
    if raw_token and static_key and hmac.compare_digest(raw_token, static_key):
        return (
            McpCaller(
                authenticated=True,
                role='admin',
                source_tag=os.getenv('MCP_STATIC_KEY_SOURCE', 'mcp-api-key').strip() or 'mcp-api-key',
                client_name='static-api-key',
            ),
            200,
            '',
        )

    agent_tok = _agent_token()
    if agent_tok:
        try:
            async with httpx.AsyncClient(base_url=_api_url(), timeout=5.0) as client:
                res = await client.post(
                    '/api/mcp/tokens/verify',
                    headers={'X-Agent-Token': agent_tok},
                    json={'token': raw_token},
                )
        except httpx.RequestError:
            return None, 503, 'Central API unavailable to verify MCP authentication.'
        if res.status_code != 200:
            return None, 503, f'MCP token verification failed (HTTP {res.status_code}).'
        data = res.json()
        if data.get('valid'):
            return (
                McpCaller(
                    authenticated=True,
                    role=data.get('role', 'infer'),
                    source_tag=data.get('source_tag'),
                    client_name=data.get('name', 'mcp-client'),
                    token_id=data.get('token_id'),
                ),
                200,
                '',
            )
        if raw_token:
            reason = 'MCP API token has been revoked.' if data.get('reason') == 'revoked' else 'Invalid MCP API token.'
            return None, 401, reason
        if data.get('auth_required') or static_key or _require_auth_env():
            return None, 401, 'Authentication required. Supply Authorization: Bearer <token> or X-API-Key header.'
        return (
            McpCaller(authenticated=False, role=_default_role(), source_tag=None, client_name='open-local'),
            200,
            '',
        )

    # Fallback when AGENT_TOKEN is not provided (e.g., standalone local MCP without central token DB sync)
    if raw_token:
        return None, 401, 'Invalid MCP API token.'
    if static_key or _require_auth_env():
        return None, 401, 'Authentication required. Supply Authorization: Bearer <token> or X-API-Key header.'
    return (
        McpCaller(authenticated=False, role=_default_role(), source_tag=None, client_name='open-local'),
        200,
        '',
    )


class McpAuthMiddleware:
    """ASGI middleware enforcing Bearer / X-API-Key authentication and binding McpCaller context."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') == '/health':
            await self.app(scope, receive, send)
            return
        raw_token = _extract_bearer_token(scope)
        caller, status_code, detail = await authenticate_http_token(raw_token)
        if caller is None:
            headers = {'WWW-Authenticate': 'Bearer'} if status_code == 401 else None
            response = JSONResponse(
                {'error': 'unauthorized' if status_code == 401 else 'auth_unavailable', 'detail': detail},
                status_code=status_code,
                headers=headers,
            )
            await response(scope, receive, send)
            return
        ctx_token = _caller_var.set(caller)
        try:
            await self.app(scope, receive, send)
        finally:
            _caller_var.reset(ctx_token)


def build_http_app(transport: Literal['streamable-http', 'sse'] = 'streamable-http'):
    inner = mcp.streamable_http_app() if transport == 'streamable-http' else mcp.sse_app()
    return McpAuthMiddleware(inner)


def main():
    parser = argparse.ArgumentParser(description='GPU Control Center MCP server')
    parser.add_argument(
        '--transport',
        choices=['streamable-http', 'sse', 'stdio'],
        default=os.getenv('MCP_TRANSPORT', 'streamable-http'),
    )
    parser.add_argument('--host', default=os.getenv('MCP_HOST', '0.0.0.0'))
    parser.add_argument('--port', type=int, default=int(os.getenv('MCP_INTERNAL_PORT', '8001')))
    args = parser.parse_args()
    if args.transport == 'stdio':
        mcp.run(transport='stdio')
    else:
        app = build_http_app(args.transport)
        uvicorn.run(app, host=args.host, port=args.port, log_level=os.getenv('MCP_LOG_LEVEL', 'info').lower())


if __name__ == '__main__':
    main()
