"""Integration tests for MCP API tokens, RBAC enforcement, expanded tools, resources, prompts, and auth middleware."""
import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import host_auth, main, mcp_server
from app.db import Base, engine


@pytest.fixture
def env(monkeypatch):
    host_auth.reset_rate_limit()
    schema = 'test_' + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    test_engine = create_engine(engine.url, connect_args={'options': f'-csearch_path={schema}'})
    Base.metadata.create_all(test_engine)
    monkeypatch.setattr(main, 'Session', sessionmaker(test_engine, expire_on_commit=False))
    monkeypatch.setattr(main, 'engine', test_engine)
    monkeypatch.setenv('ENABLE_REMOTE_WORKERS', 'false')
    monkeypatch.setenv('AGENT_TOKEN', 'mcp-test-agent-token')
    monkeypatch.setenv('ENABLE_DEMO', 'true')
    monkeypatch.delenv('MCP_API_KEY', raising=False)
    monkeypatch.setenv('MCP_REQUIRE_AUTH', 'false')
    monkeypatch.setenv('MCP_DEFAULT_ROLE', 'admin')

    # Route all mcp_server HTTP calls straight to the in-process FastAPI app.
    original_async_client = httpx.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs['transport'] = httpx.ASGITransport(app=main.app)
        kwargs.setdefault('base_url', 'http://test-api')
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(mcp_server.httpx, 'AsyncClient', patched_async_client)

    with TestClient(main.app) as client:
        yield client

    test_engine.dispose()
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))


AUTH = {'X-Agent-Token': 'mcp-test-agent-token'}


def _register_host(client, node_id='gpu-node-01'):
    llm = {
        'id': 'lmstudio:qwen3-8b',
        'name': 'qwen3-8b',
        'runtime': 'lmstudio',
        'model_type': 'llm',
        'runtime_model': 'qwen3-8b',
        'capability': 'lmstudio:qwen3-8b',
        'description': 'Test LLM',
    }
    embed = {
        'id': 'lmstudio:bge-m3',
        'name': 'bge-m3',
        'runtime': 'lmstudio',
        'model_type': 'embedding',
        'runtime_model': 'bge-m3',
        'capability': 'lmstudio:bge-m3',
        'description': 'Test embedding',
    }
    payload = {
        'name': node_id,
        'mode': 'host',
        'capabilities': [llm['id'], embed['id'], 'lmstudio.model_control'],
        'model_catalog': [llm, embed],
        'metrics': {
            'simulated': False,
            'cpu_percent': 12.5,
            'ram_percent': 34.0,
            'ram_total_gb': 64.0,
            'gpus': [
                {
                    'index': 0,
                    'name': 'RTX 4090',
                    'utilization': 15,
                    'memory_used_mb': 4096,
                    'memory_total_mb': 24576,
                    'temperature_c': 44,
                    'power_w': 68.5,
                }
            ],
            'lmstudio': {
                'status': 'online',
                'models': 2,
                'loaded_models_status': 'online',
                'loaded_models': [{'name': 'qwen3-8b', 'vram_bytes': 4_000_000_000}],
                'gpu_placement_supported': True,
            },
        },
    }
    res = client.put(f'/api/agent/nodes/{node_id}', headers=AUTH, json=payload)
    assert res.status_code == 200, res.text
    return llm


def test_mcp_token_crud_and_verification(env):
    client = env
    info0 = client.get('/api/mcp/info').json()
    assert info0['auth_enabled'] is False
    assert len(info0['tools']) == 17
    tool_names = {t['name'] for t in info0['tools']}
    assert {'get_scheduler_state', 'simulate_dispatch'}.issubset(tool_names)
    assert len(info0['resources']) == 3
    assert len(info0['prompts']) == 2

    # Unauthenticated verify before any token exists -> auth_required False
    v0 = client.post('/api/mcp/tokens/verify', headers=AUTH, json={'token': ''})
    assert v0.status_code == 200
    assert v0.json()['auth_required'] is False
    assert v0.json()['valid'] is False

    # Verify endpoint requires agent_auth
    assert client.post('/api/mcp/tokens/verify', json={'token': ''}).status_code == 401

    # Issue a token
    created = client.post('/api/mcp/tokens', json={
        'name': 'Research Agent',
        'role': 'infer',
        'notes': 'Lab shared agent',
    })
    assert created.status_code == 201, created.text
    tok = created.json()
    assert tok['token'].startswith('gcc_mcp_')
    assert tok['source_tag'] == 'mcp:research-agent'
    assert tok['role'] == 'infer'
    assert tok['active'] is True
    assert 'token_hash' not in tok

    # Listing tokens never leaks raw token or hash
    listed = client.get('/api/mcp/tokens').json()
    assert len(listed) == 1
    assert 'token' not in listed[0]
    assert 'token_hash' not in listed[0]

    # Auth is now automatically enforced
    assert client.get('/api/mcp/info').json()['auth_enabled'] is True
    v_empty = client.post('/api/mcp/tokens/verify', headers=AUTH, json={'token': ''}).json()
    assert v_empty['auth_required'] is True
    assert v_empty['valid'] is False

    # Valid token passes and increments counter
    v_ok = client.post('/api/mcp/tokens/verify', headers=AUTH, json={'token': tok['token']}).json()
    assert v_ok['valid'] is True
    assert v_ok['role'] == 'infer'
    assert v_ok['source_tag'] == 'mcp:research-agent'
    listed2 = client.get('/api/mcp/tokens').json()
    assert listed2[0]['request_count'] == 1
    assert listed2[0]['last_used_at'] is not None

    # Revoke invalidates token immediately
    rev = client.post(f"/api/mcp/tokens/{tok['id']}/revoke")
    assert rev.status_code == 200
    assert rev.json()['active'] is False
    v_rev = client.post('/api/mcp/tokens/verify', headers=AUTH, json={'token': tok['token']}).json()
    assert v_rev['valid'] is False
    assert v_rev['reason'] == 'revoked'

    # Delete removes token record
    assert client.delete(f"/api/mcp/tokens/{tok['id']}").status_code == 200
    assert client.get('/api/mcp/tokens').json() == []


def test_mcp_auth_middleware_and_rbac(env, monkeypatch):
    client = env
    _register_host(client)

    # Open mode when no tokens exist
    caller, status, _ = asyncio.run(mcp_server.authenticate_http_token(''))
    assert status == 200
    assert caller is not None and caller.role == 'admin'

    # Create read-only and infer tokens
    read_tok = client.post('/api/mcp/tokens', json={'name': 'Observer', 'role': 'read'}).json()['token']
    infer_tok = client.post('/api/mcp/tokens', json={'name': 'Agent Alpha', 'role': 'infer', 'source_tag': 'alpha-agent'}).json()['token']

    # Missing token now rejected with 401
    caller, status, _ = asyncio.run(mcp_server.authenticate_http_token(''))
    assert caller is None and status == 401

    # Invalid token rejected with 401
    caller, status, _ = asyncio.run(mcp_server.authenticate_http_token('gcc_mcp_bogus'))
    assert caller is None and status == 401

    # Read token authenticates as read and cannot submit_job or manage_model
    read_caller, status, _ = asyncio.run(mcp_server.authenticate_http_token(read_tok))
    assert status == 200 and read_caller.role == 'read'
    tk = mcp_server._caller_var.set(read_caller)
    try:
        nodes_res = asyncio.run(mcp_server.list_nodes(compact=True))
        assert nodes_res['count'] == 1
        assert nodes_res['nodes'][0]['gpus'][0]['memory_free_mb'] == 20480
        with pytest.raises(ValueError, match="requires 'infer' role"):
            asyncio.run(mcp_server.submit_job(name='blocked', model='lmstudio:qwen3-8b', prompt='hi'))
        with pytest.raises(ValueError, match="requires 'admin' role"):
            asyncio.run(mcp_server.set_node_maintenance(node_id='gpu-node-01', enabled=True))
    finally:
        mcp_server._caller_var.reset(tk)

    # Infer token authenticates, auto-tags job source, and cannot call admin tools
    infer_caller, status, _ = asyncio.run(mcp_server.authenticate_http_token(infer_tok))
    assert status == 200 and infer_caller.role == 'infer'
    tk = mcp_server._caller_var.set(infer_caller)
    try:
        job = asyncio.run(mcp_server.submit_job(name='tagged', model='lmstudio:qwen3-8b', prompt='hello'))
        assert job['source'] == 'alpha-agent'
        cancelled = asyncio.run(mcp_server.cancel_job(job['id']))
        assert cancelled['status'] == 'cancelled'
        retried = asyncio.run(mcp_server.retry_job(job['id']))
        assert retried['id'] != job['id']
        assert retried['source'] == 'alpha-agent'
        with pytest.raises(ValueError, match="requires 'admin' role"):
            asyncio.run(mcp_server.manage_model(node_id='gpu-node-01', model='lmstudio:qwen3-8b', action='unload'))
    finally:
        mcp_server._caller_var.reset(tk)

    # Static MCP_API_KEY grants admin role
    monkeypatch.setenv('MCP_API_KEY', 'static-secret-key')
    admin_caller, status, _ = asyncio.run(mcp_server.authenticate_http_token('static-secret-key'))
    assert status == 200 and admin_caller.role == 'admin'


def test_mcp_expanded_tools_resources_and_prompts(env):
    client = env
    _register_host(client)

    # list_models filtering
    llm_only = asyncio.run(mcp_server.list_models(runtime='lmstudio', only_available=True, only_llm=True))
    assert llm_only['count'] == 1
    assert llm_only['models'][0]['id'] == 'lmstudio:qwen3-8b'

    # run_inference auto-picks LLM model, waits, and returns generated text
    async def run_and_complete():
        async def worker():
            await asyncio.sleep(0.15)
            claimed = client.post('/api/agent/nodes/gpu-node-01/claim', headers=AUTH).json()['job']
            assert claimed is not None
            assert claimed['model'] == 'lmstudio:qwen3-8b'
            client.post(
                f"/api/agent/jobs/{claimed['id']}/report",
                headers=AUTH,
                json={
                    'node_id': 'gpu-node-01',
                    'attempt_id': claimed['attempt_id'],
                    'progress': 100,
                    'status': 'completed',
                    'result': {'text': 'Hello from local GPU!', 'tokens_per_second': 48.2, 'simulated': False},
                },
            )

        task = asyncio.create_task(worker())
        outcome = await mcp_server.run_inference(prompt='Say hello', max_tokens=32, timeout_seconds=10)
        await task
        return outcome

    res = asyncio.run(run_and_complete())
    assert res['completed'] is True
    assert res['timed_out'] is False
    assert res['text'] == 'Hello from local GPU!'
    assert res['tokens_per_second'] == 48.2

    # list_jobs filter
    completed_jobs = asyncio.run(mcp_server.list_jobs(status='completed', limit=10))
    assert completed_jobs['count'] >= 1
    assert completed_jobs['jobs'][0]['preview_text'] == 'Hello from local GPU!'

    # get_energy_analytics
    energy = asyncio.run(mcp_server.get_energy_analytics(range='24h'))
    assert energy['range'] == '24h'
    assert 'live' in energy and 'summary' in energy

    # Admin controls: default runtime, maintenance, and password-protected manage_model
    rt = asyncio.run(mcp_server.set_default_runtime(node_id='gpu-node-01', runtime='ollama'))
    assert rt['default_runtime'] == 'ollama'
    maint = asyncio.run(mcp_server.set_node_maintenance(node_id='gpu-node-01', enabled=True))
    assert maint['maintenance'] is True
    asyncio.run(mcp_server.set_node_maintenance(node_id='gpu-node-01', enabled=False))

    # Enable host password and verify manage_model respects it and works when host_password is supplied
    assert client.patch('/api/nodes/gpu-node-01/auth/password', json={'new_password': 'host-pass'}).status_code == 200
    with pytest.raises(ValueError, match='403'):
        asyncio.run(mcp_server.manage_model(node_id='gpu-node-01', model='lmstudio:qwen3-8b', action='load', gpu_indices=[0]))
    ok_action = asyncio.run(
        mcp_server.manage_model(
            node_id='gpu-node-01',
            model='lmstudio:qwen3-8b',
            action='load',
            gpu_indices=[0],
            host_password='host-pass',
        )
    )
    assert ok_action['parameters']['gpu_indices'] == [0]

    # MCP resources & prompts
    ov = json.loads(asyncio.run(mcp_server.resource_overview()))
    assert ov['overview']['online_nodes'] == 1
    nd = json.loads(asyncio.run(mcp_server.resource_nodes()))
    assert nd['count'] == 1
    md = json.loads(asyncio.run(mcp_server.resource_models()))
    assert md['count'] >= 1
    assert 'list_nodes' in mcp_server.cluster_health_check()
    assert 'run_inference' in mcp_server.run_local_llm('Summarize logs')


def test_mcp_http_middleware_health_and_auth_status(env):
    client = env
    client.post('/api/mcp/tokens', json={'name': 'Guard', 'role': 'read'})
    asgi_app = mcp_server.McpAuthMiddleware(mcp_server.mcp.streamable_http_app())
    with TestClient(asgi_app) as mcp_client:
        # /health bypasses Bearer auth
        health = mcp_client.get('/health')
        assert health.status_code == 200
        assert health.json()['api_reachable'] is True
        # /mcp rejects missing token with 401 + WWW-Authenticate
        unauth = mcp_client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        assert unauth.status_code == 401
        assert unauth.headers.get('www-authenticate') == 'Bearer'
