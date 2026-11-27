"""Integration tests use an isolated temporary schema on the configured PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import host_auth, main
from app.db import Base, engine
from app.models import Job, now


@pytest.fixture
def client(monkeypatch):
    host_auth.reset_rate_limit()
    schema = 'test_' + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    test_engine = create_engine(engine.url, connect_args={'options': f'-csearch_path={schema}'})
    Base.metadata.create_all(test_engine)
    monkeypatch.setattr(main, 'Session', sessionmaker(test_engine, expire_on_commit=False))
    monkeypatch.setattr(main, 'engine', test_engine)
    monkeypatch.setenv('ENABLE_REMOTE_WORKERS', 'false')
    monkeypatch.setenv('AGENT_TOKEN', 'integration-test-token')
    monkeypatch.setenv('ENABLE_DEMO', 'true')
    with TestClient(main.app) as c:
        yield c
    test_engine.dispose()
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))


AUTH = {'X-Agent-Token': 'integration-test-token'}


def register(client, node='test-node'):
    response = client.put(f'/api/agent/nodes/{node}', headers=AUTH, json={'name': node, 'mode':'demo', 'capabilities':['demo.progress']})
    assert response.status_code == 200


def new_job(client, **kwargs):
    response = client.post('/api/jobs', json={'name': 'Test job', **kwargs})
    assert response.status_code == 201
    return response.json()


def claim(client, node='test-node'):
    response = client.post(f'/api/agent/nodes/{node}/claim', headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()['job']


def report(client, job, **kwargs):
    return client.post(f"/api/agent/jobs/{job['id']}/report", headers=AUTH, json={'node_id':job['node_id'], 'attempt_id':job['attempt_id'], 'progress':25, **kwargs})


def test_lifecycle_and_result_persistence(client):
    register(client)
    created = new_job(client)
    job = claim(client)
    assert job['id'] == created['id']
    assert report(client, job).json()['status'] == 'running'
    assert report(client, job, status='completed', progress=100, result={'ok':True}).json()['terminal']
    saved = client.get(f"/api/jobs/{job['id']}").json()
    assert saved['result'] == {'ok':True}
    assert saved['finished_at']
    assert len(saved['events']) == 3
    assert client.get('/api/overview').json()['jobs']['completed'] == 1


def test_claim_is_exclusive_across_concurrent_agents(client):
    register(client, 'a')
    register(client, 'b')
    new_job(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda n: claim(client, n), ['a','b']))
    assert sum(j is not None for j in results) == 1


def test_one_active_job_per_node_and_priority(client):
    register(client)
    new_job(client, priority=0)
    high = new_job(client, priority=10)
    assert claim(client)['id'] == high['id']
    assert claim(client) is None


def test_maintenance_survives_heartbeat(client):
    register(client)
    client.patch('/api/nodes/test-node/maintenance', json={'enabled':True})
    register(client)
    new_job(client)
    assert claim(client) is None
    assert client.get('/api/nodes').json()[0]['status'] == 'maintenance'
    client.patch('/api/nodes/test-node/maintenance', json={'enabled':False})
    assert claim(client)


def test_cancel_requires_agent_ack_and_retry_creates_new_job(client):
    register(client)
    new_job(client)
    job = claim(client)
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()['status'] == 'cancelling'
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409
    assert report(client, job).json()['status'] == 'cancelling'
    assert report(client, job, status='cancelled').json()['terminal']
    retried = client.post(f"/api/jobs/{job['id']}/retry")
    assert retried.status_code == 201
    assert retried.json()['id'] != job['id']


def test_stale_attempt_and_lease_timeout_do_not_redispatch(client):
    register(client)
    new_job(client)
    job = claim(client)
    assert report(client, job, attempt_id='stale').status_code == 409
    with main.Session.begin() as db:
        db.get(Job, job['id']).lease_until = now() - timedelta(seconds=60)
    main.reconcile()
    assert client.get(f"/api/jobs/{job['id']}").json()['status'] == 'unknown'
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409
    new_job(client)
    assert claim(client) is None
    assert report(client, job, status='failed', message='Agent confirmed stopped').json()['terminal']
    assert claim(client)


def test_validation_and_agent_auth(client):
    assert client.get('/health').json()['database'] == 'ok'
    assert client.post('/api/jobs', json={'name':'x', 'steps':0}).status_code == 422
    assert client.post('/api/jobs', json={'name':'x', 'model':'unknown'}).status_code == 422
    assert client.post('/api/agent/nodes/x/claim').status_code == 401
    assert client.get('/api/jobs/missing').status_code == 404


def test_queued_cancel_never_runs(client):
    register(client)
    job = new_job(client)
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()['status'] == 'cancelled'
    assert claim(client) is None


def test_completion_after_cancel_does_not_publish_result(client):
    register(client)
    new_job(client)
    job = claim(client)
    client.post(f"/api/jobs/{job['id']}/cancel")
    assert report(client, job, status='completed', progress=100, result={'late':True}).json()['status'] == 'cancelled'
    assert client.get(f"/api/jobs/{job['id']}").json()['result'] is None


def test_cancellation_intent_survives_lease_loss(client):
    register(client)
    new_job(client)
    job = claim(client)
    client.post(f"/api/jobs/{job['id']}/cancel")
    with main.Session.begin() as db:
        db.get(Job, job['id']).lease_until = now() - timedelta(seconds=60)
    main.reconcile()
    assert client.get(f"/api/jobs/{job['id']}").json()['status'] == 'unknown'
    assert report(client, job).json()['status'] == 'cancelling'
    assert report(client, job, status='cancelled').json()['terminal']


def test_explanation_reflects_maintenance_and_holder(client):
    register(client)
    first = new_job(client, priority=10)
    second = new_job(client, source='other-app')
    assert claim(client)['id'] == first['id']
    explanation = client.get(f"/api/jobs/{second['id']}/explain").json()
    assert explanation['reason'] == 'nodes_blocked'
    assert explanation['queue_rank'] == 1
    assert explanation['candidates'][0]['holders'][0]['source'] == 'console'
    assert 'slot_occupied' in explanation['candidates'][0]['blockers']
    client.patch('/api/nodes/test-node/maintenance', json={'enabled':True})
    assert 'maintenance' in client.get(f"/api/jobs/{second['id']}/explain").json()['candidates'][0]['blockers']


def test_explanation_and_scheduler_share_queue_order(client):
    register(client)
    low = new_job(client, priority=0)
    high = new_job(client, priority=10)
    assert client.get(f"/api/jobs/{high['id']}/explain").json()['queue_rank'] == 1
    assert client.get(f"/api/jobs/{low['id']}/explain").json()['queue_rank'] == 2
    assert claim(client)['id'] == high['id']


def test_explanation_without_workers_and_unknown_execution(client):
    job = new_job(client)
    assert client.get(f"/api/jobs/{job['id']}/explain").json()['reason'] == 'no_compatible_node'
    register(client)
    claim(client)
    with main.Session.begin() as db:
        db.get(Job, job['id']).lease_until = now() - timedelta(seconds=60)
    main.reconcile()
    assert client.get(f"/api/jobs/{job['id']}/explain").json()['reason'] == 'execution_unconfirmed'
    assert client.get('/api/jobs/missing/explain').status_code == 404


def test_real_model_catalog_input_and_partial_output(client, monkeypatch):
    monkeypatch.setenv('ENABLE_DEMO', 'false')
    model = dict(id='ollama:test-model', name='test-model', runtime='ollama', runtime_model='test-model', capability='ollama:test-model')
    response = client.put('/api/agent/nodes/real-host', headers=AUTH, json={'name':'real-host', 'mode':'host', 'capabilities':[model['id']], 'model_catalog':[model], 'metrics':{'simulated':False}})
    assert response.status_code == 200
    assert client.get('/api/models').json()[0]['available'] is True
    assert client.post('/api/jobs', json={'name':'missing prompt', 'model':model['id']}).status_code == 422
    job = new_job(client, model=model['id'], prompt='Hello', max_tokens=16)
    leased = claim(client, 'real-host')
    assert leased['parameters'] == {'prompt':'Hello', 'max_tokens':16}
    state = {'phase':'generating', 'text':'Actual partial output'}
    assert report(client, leased, runtime_state=state).status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").json()['runtime_state'] == state
    report(client, leased, status='failed', message='Upstream explicitly rejected request')
    retried = client.post(f"/api/jobs/{job['id']}/retry").json()
    assert retried['parameters'] == leased['parameters']


def test_demo_disabled_and_unknown_report_retains_slot(client, monkeypatch):
    register(client)
    new_job(client)
    job = claim(client)
    report(client, job, status='unknown', message='Upstream disconnected; execution unconfirmed')
    assert claim(client) is None
    monkeypatch.setenv('ENABLE_DEMO', 'false')
    assert client.get('/api/nodes').json() == []
    assert client.get('/api/models').json() == []
    assert client.post('/api/jobs', json={'name':'demo'}).status_code == 422


def register_host(client, node='host', runtime='lmstudio'):
    spec = dict(id=f'{runtime}:test', name='test', runtime=runtime, runtime_model='test', capability=f'{runtime}:test')
    payload = {'name':node, 'mode':'host', 'capabilities':[spec['id'], f'{runtime}.model_control'], 'model_catalog':[spec],
               'metrics':{runtime:{'loaded_models_status':'online','loaded_models':[]}}}
    assert client.put(f'/api/agent/nodes/{node}', headers=AUTH, json=payload).status_code == 200
    return spec


def control(client, node='host', action='load', runtime='lmstudio'):
    return client.post(f'/api/nodes/{node}/model-actions', json={'model':f'{runtime}:test','action':action})


def test_runtime_preference_persists_across_heartbeat(client):
    register_host(client)
    assert client.get('/api/nodes').json()[0]['default_runtime'] == 'lmstudio'
    assert client.patch('/api/nodes/host/default-runtime', json={'runtime':'ollama'}).status_code == 200
    register_host(client)
    assert client.get('/api/nodes').json()[0]['default_runtime'] == 'ollama'
    assert client.patch('/api/nodes/host/default-runtime', json={'runtime':'comfyui'}).status_code == 422


def test_model_action_targets_only_selected_host_and_shares_slot(client):
    register_host(client, 'a')
    register_host(client, 'b')
    created = control(client, 'a')
    assert created.status_code == 201, created.text
    assert claim(client, 'b') is None
    leased = claim(client, 'a')
    assert leased['id'] == created.json()['id']
    assert claim(client, 'a') is None
    assert control(client, 'a').status_code == 409
    assert client.post(f"/api/jobs/{leased['id']}/cancel").status_code == 409
    report(client, leased, status='completed', result={'loaded':True})
    assert client.get(f"/api/jobs/{leased['id']}").json()['result']['loaded']


def test_duplicate_model_actions_are_serialized(client):
    register_host(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: control(client).status_code, [1,2]))
    assert sorted(results) == [201,409]


def test_manual_ollama_residency_survives_inference_claims(client):
    register_host(client, runtime='ollama')
    assert control(client, runtime='ollama').status_code == 201
    report(client, claim(client, 'host'), status='completed')
    new_job(client, model='ollama:test', prompt='Hi')
    inference = claim(client, 'host')
    assert inference['keep_model_loaded'] is True
    assert control(client, action='unload', runtime='ollama').status_code == 409
    report(client, inference, status='completed')
    assert control(client, action='unload', runtime='ollama').status_code == 201
    report(client, claim(client, 'host'), status='completed')
    new_job(client, model='ollama:test', prompt='Hi')
    assert claim(client, 'host')['keep_model_loaded'] is False


def test_model_action_validation_and_cancel_before_claim(client):
    register_host(client)
    assert client.post('/api/nodes/missing/model-actions', json={'model':'lmstudio:test','action':'load'}).status_code == 404
    assert control(client, runtime='ollama').status_code == 409
    assert client.post('/api/nodes/host/model-actions', json={'model':'lmstudio:missing','action':'load'}).status_code == 422
    created = control(client).json()
    assert client.post(f"/api/jobs/{created['id']}/cancel").json()['status'] == 'cancelled'
    assert claim(client, 'host') is None
    assert client.post(f"/api/jobs/{created['id']}/retry").status_code == 409
    client.patch('/api/nodes/host/maintenance', json={'enabled':True})
    assert control(client).status_code == 409


def test_model_action_gpu_indices_validation_and_persistence(client):
    spec = dict(id='lmstudio:test', name='test', runtime='lmstudio', runtime_model='test', capability='lmstudio:test')
    ollama_spec = dict(id='ollama:test', name='test', runtime='ollama', runtime_model='test', capability='ollama:test')
    payload = {
        'name': 'gpu-host', 'mode': 'host',
        'capabilities': [spec['id'], ollama_spec['id'], 'lmstudio.model_control', 'ollama.model_control'],
        'model_catalog': [spec, ollama_spec],
        'metrics': {
            'gpus': [{'index': 0, 'name': 'RTX 4090'}, {'index': 1, 'name': 'RTX 4090'}],
            'lmstudio': {'loaded_models_status': 'online', 'loaded_models': [], 'gpu_placement_supported': True},
            'ollama': {'loaded_models_status': 'online', 'loaded_models': [], 'gpu_placement_supported': False},
        },
    }
    assert client.put('/api/agent/nodes/gpu-host', headers=AUTH, json=payload).status_code == 200

    # Ollama rejects per-model GPU selection.
    assert client.post('/api/nodes/gpu-host/model-actions', json={'model': 'ollama:test', 'action': 'load', 'gpu_indices': [1]}).status_code == 422
    # Unload rejects GPU selection.
    assert client.post('/api/nodes/gpu-host/model-actions', json={'model': 'lmstudio:test', 'action': 'unload', 'gpu_indices': [1]}).status_code == 422
    # Out-of-range GPU index is rejected.
    assert client.post('/api/nodes/gpu-host/model-actions', json={'model': 'lmstudio:test', 'action': 'load', 'gpu_indices': [2]}).status_code == 422
    # Duplicate or empty list is rejected.
    assert client.post('/api/nodes/gpu-host/model-actions', json={'model': 'lmstudio:test', 'action': 'load', 'gpu_indices': []}).status_code == 422
    assert client.post('/api/nodes/gpu-host/model-actions', json={'model': 'lmstudio:test', 'action': 'load', 'gpu_indices': [1, 1]}).status_code == 422

    # Valid LM Studio GPU selection succeeds and persists in job parameters.
    res = client.post('/api/nodes/gpu-host/model-actions', json={'model': 'lmstudio:test', 'action': 'load', 'gpu_indices': [1], 'replace': True})
    assert res.status_code == 201, res.text
    job = res.json()
    assert job['parameters']['gpu_indices'] == [1]
    assert job['parameters']['replace'] is True
    assert 'GPU 01' in job['name']


def test_comfyui_model_actions_validation_and_claim(client):
    spec = dict(id='comfyui:s3.safetensors', name='s3.safetensors', runtime='comfyui', model_type='diffusion', runtime_model='s3.safetensors', capability='comfyui:s3.safetensors')
    payload = {
        'name': 'comfy-host', 'mode': 'host',
        'capabilities': ['comfyui.model_control'],
        'model_catalog': [spec],
        'metrics': {
            'gpus': [{'index': 0, 'name': 'RTX 4090'}, {'index': 1, 'name': 'RTX 4090'}],
            'comfyui': {'status': 'online', 'gpu_placement_supported': True},
        },
    }
    assert client.put('/api/agent/nodes/comfy-host', headers=AUTH, json=payload).status_code == 200
    # ComfyUI only allows a single GPU index per load.
    assert client.post('/api/nodes/comfy-host/model-actions', json={'model': 'comfyui:s3.safetensors', 'action': 'load', 'gpu_indices': [0, 1]}).status_code == 422
    # Valid load with single GPU index succeeds and is claimable.
    res = client.post('/api/nodes/comfy-host/model-actions', json={'model': 'comfyui:s3.safetensors', 'action': 'load', 'gpu_indices': [1]})
    assert res.status_code == 201, res.text
    leased = claim(client, 'comfy-host')
    assert leased['model'] == 'comfyui:s3.safetensors'
    assert leased['parameters']['gpu_indices'] == [1]
    report(client, leased, status='completed', result={'loaded': True})
    # Wildcard unload frees all ComfyUI cache.
    unload = client.post('/api/nodes/comfy-host/model-actions', json={'model': 'comfyui:*', 'action': 'unload'})
    assert unload.status_code == 201, unload.text


def test_model_aliases_persist_across_heartbeat_and_clear(client):
    register_host(client)
    res = client.patch('/api/nodes/host/model-aliases', json={'model': 'lmstudio:test', 'alias': '我的測試模型'})
    assert res.status_code == 200
    assert res.json()['model_aliases'] == {'lmstudio:test': '我的測試模型'}
    # Survives heartbeat updates
    register_host(client)
    assert client.get('/api/nodes').json()[0]['model_aliases'] == {'lmstudio:test': '我的測試模型'}
    model_entry = next(m for m in client.get('/api/models').json() if m['id'] == 'lmstudio:test')
    assert model_entry['display_name'] == '我的測試模型'
    # Clearing alias restores default
    cleared = client.patch('/api/nodes/host/model-aliases', json={'model': 'lmstudio:test', 'alias': '   '})
    assert cleared.status_code == 200
    assert cleared.json()['model_aliases'] == {}
    # Reject invalid runtime prefix
    assert client.patch('/api/nodes/host/model-aliases', json={'model': 'invalid', 'alias': 'x'}).status_code == 422


def test_hidden_models_batch_update_and_persistence(client):
    register_host(client)
    res = client.patch('/api/nodes/host/hidden-models', json={'hide': ['lmstudio:test', 'comfyui:s3.safetensors']})
    assert res.status_code == 200
    assert res.json()['hidden_models'] == ['comfyui:s3.safetensors', 'lmstudio:test']
    # Survives heartbeat updates and marks /api/models entry hidden
    register_host(client)
    assert client.get('/api/nodes').json()[0]['hidden_models'] == ['comfyui:s3.safetensors', 'lmstudio:test']
    model_entry = next(m for m in client.get('/api/models').json() if m['id'] == 'lmstudio:test')
    assert model_entry['hidden'] is True
    # Batch unhide restores visibility
    restored = client.patch('/api/nodes/host/hidden-models', json={'unhide': ['lmstudio:test']})
    assert restored.status_code == 200
    assert restored.json()['hidden_models'] == ['comfyui:s3.safetensors']
    model_entry2 = next(m for m in client.get('/api/models').json() if m['id'] == 'lmstudio:test')
    assert model_entry2['hidden'] is False
    # Reject invalid model IDs
    assert client.patch('/api/nodes/host/hidden-models', json={'hide': ['bad-id']}).status_code == 422


@pytest.fixture
def remote_transport(monkeypatch):
    import httpx
    from app import connections
    calls = []
    mode = {'value': 'online'}
    def respond(request):
        calls.append(request)
        assert request.method == 'GET'
        assert 'x-agent-token' not in request.headers
        if mode['value'] == 'timeout':
            raise httpx.ReadTimeout('timeout', request=request)
        if mode['value'] == 'refused':
            raise httpx.ConnectError('refused', request=request)
        if mode['value'] == 'auth':
            return httpx.Response(401)
        if mode['value'] == 'wrong':
            return httpx.Response(200, json={'error': 'Unexpected endpoint or method'})
        if mode['value'] == 'oversized':
            return httpx.Response(200, content=b'x' * 1048577)
        if mode['value'] == 'redirect':
            return httpx.Response(302, headers={'location': 'http://other'})
        if request.url.path == '/api/v1/models':
            return httpx.Response(200, json={'models': [{'key': 'remote-model', 'type': 'llm', 'loaded_instances': []}]})
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'remote-ollama'}]})
        return httpx.Response(200, json={'system': {}, 'devices': []})
    original = httpx.AsyncClient
    monkeypatch.setattr(connections.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    return calls, mode


def test_discovery_without_port_checks_runtime_apis(client, remote_transport):
    calls, _ = remote_transport
    response = client.post('/api/connections/test', json={'url': '10.8.22.92'})
    assert response.status_code == 200
    data = response.json()
    assert data['ok'] and len(data['services']) == 3
    assert {r.url.port for r in calls} == {1234, 11434, 8188}
    assert {r.url.path for r in calls} == {'/api/v1/models', '/api/tags', '/system_stats'}
    assert client.get('/api/nodes').json() == []


@pytest.mark.parametrize('address', ['http://worker.test', 'https://worker.test', 'http://[::1]'])
def test_discovery_accepts_hostnames_and_ipv6(client, remote_transport, address):
    response = client.post('/api/connections/test', json={'url': address})
    assert response.json()['ok']


def test_discovery_explicit_custom_port(client, remote_transport):
    calls, _ = remote_transport
    assert client.post('/api/connections/test', json={'url': 'http://worker.test:4567'}).json()['ok']
    assert {r.url.port for r in calls} == {4567}


@pytest.mark.parametrize('scenario', ['timeout', 'refused', 'auth', 'wrong', 'oversized', 'redirect'])
def test_discovery_failure_never_adds_host(client, remote_transport, scenario):
    _, mode = remote_transport
    mode['value'] = scenario
    data = client.post('/api/connections/test', json={'url': 'worker.test'}).json()
    assert data['ok'] is False and len(data['checks']) == 3
    assert all(c.get('reason') for c in data['checks'])
    assert client.post('/api/connections', json={'name': 'remote', 'url': 'worker.test'}).status_code == 422
    assert client.get('/api/nodes').json() == []


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'ftp://host', 'http://user:secret@host', 'http://host/path', 'http://host?token=secret', 'http://host/#fragment', 'http://host:99999'])
def test_discovery_rejects_invalid_origin(client, url):
    assert client.post('/api/connections/test', json={'url': url}).status_code == 422


def test_add_remote_connection_persists_and_uses_central_slot(client, remote_transport):
    from app.remote_connections import worker_environment
    from app.models import Node
    result = client.post('/api/connections', json={'name': 'emily-pc', 'url': '10.8.22.92'})
    assert result.status_code == 201, result.text
    saved = result.json()
    assert saved['connection_host'] == '10.8.22.92'
    assert saved['connection_services']['lmstudio'] == 'http://10.8.22.92:1234'
    assert saved['status'] == 'offline'  # No invented successful heartbeat.
    assert client.post('/api/connections', json={'name': 'emily-pc', 'url': 'other-host'}).status_code == 409
    assert client.post('/api/connections', json={'name': 'another-name', 'url': 'http://10.8.22.92:1234'}).status_code == 409
    with main.Session() as db:
        env = worker_environment(db.get(Node, 'emily-pc'))
    assert env['LMSTUDIO_URL'] == 'http://10.8.22.92:1234'
    assert 'LMSTUDIO_API_TOKEN' not in env
    assert env['NODE_ID'] == 'emily-pc'
    spec = {'id': 'lmstudio:remote', 'name': 'remote', 'runtime': 'lmstudio', 'runtime_model': 'remote', 'capability': 'lmstudio:remote'}
    heartbeat = {'name': 'emily-pc', 'mode': 'host', 'capabilities': ['lmstudio:remote', 'lmstudio.model_control'],
                 'model_catalog': [spec], 'metrics': {'remote': True, 'gpus': [], 'lmstudio': {'status': 'online', 'loaded_models_status': 'online', 'loaded_models': []}}}
    assert client.put('/api/agent/nodes/emily-pc', headers=AUTH, json=heartbeat).status_code == 200
    assert client.get('/api/nodes').json()[0]['status'] == 'online'
    first = new_job(client, model='lmstudio:remote', prompt='hello')
    new_job(client, model='lmstudio:remote', prompt='hello')
    leased = claim(client, 'emily-pc')
    assert leased['id'] == first['id']
    assert claim(client, 'emily-pc') is None
    report(client, leased, status='completed', result={'simulated': False, 'text': 'hello'})
    assert claim(client, 'emily-pc') is not None
    heartbeat['metrics']['lmstudio']['status'] = 'offline'
    heartbeat['capabilities'] = []
    client.put('/api/agent/nodes/emily-pc', headers=AUTH, json=heartbeat)
    assert client.get('/api/nodes').json()[0]['status'] == 'offline'


def test_add_remote_host_with_password_protects_model_actions(client, remote_transport):
    # Short or blank passwords are rejected when adding a host.
    assert client.post('/api/connections', json={'name': 'pw-host', 'url': '10.8.22.99', 'password': '123'}).status_code == 422
    assert client.post('/api/connections', json={'name': 'pw-host', 'url': '10.8.22.99', 'password': '    '}).status_code == 422

    created = client.post('/api/connections', json={'name': 'pw-host', 'url': '10.8.22.99', 'password': 'secret-pass'})
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved['auth_required'] is True
    assert saved['auth_unlocked'] is False
    assert 'auth_password_hash' not in saved
    assert 'auth_version' not in saved

    spec = {'id': 'lmstudio:remote', 'name': 'remote', 'runtime': 'lmstudio', 'runtime_model': 'remote', 'capability': 'lmstudio:remote'}
    heartbeat = {'name': 'pw-host', 'mode': 'host', 'capabilities': ['lmstudio:remote', 'lmstudio.model_control'],
                 'model_catalog': [spec], 'metrics': {'remote': True, 'gpus': [], 'lmstudio': {'status': 'online', 'loaded_models_status': 'online', 'loaded_models': []}}}
    assert client.put('/api/agent/nodes/pw-host', headers=AUTH, json=heartbeat).status_code == 200

    # Regular inference job does not require unlocking.
    inf = new_job(client, model='lmstudio:remote', prompt='hello')
    assert inf['status'] == 'queued'
    # Cancel queued inference so slot is free for model actions.
    assert client.post(f"/api/jobs/{inf['id']}/cancel").status_code == 200

    # Model action is blocked until unlocked.
    blocked = client.post('/api/nodes/pw-host/model-actions', json={'model': 'lmstudio:remote', 'action': 'load'})
    assert blocked.status_code == 403
    assert blocked.headers.get('x-host-auth-required') == 'true'

    # Wrong password rejected.
    assert client.post('/api/nodes/pw-host/auth/unlock', json={'password': 'wrong'}).status_code == 401

    # Correct password unlocks session via cookie and returns token.
    unlocked = client.post('/api/nodes/pw-host/auth/unlock', json={'password': 'secret-pass'})
    assert unlocked.status_code == 200, unlocked.text
    payload = unlocked.json()
    assert payload['unlocked'] is True
    assert payload['auth_required'] is True
    assert payload['expires_at']
    assert payload['token']

    node_state = client.get('/api/nodes').json()[0]
    assert node_state['auth_required'] is True
    assert node_state['auth_unlocked'] is True
    assert node_state['auth_unlock_expires_at']

    # Model action succeeds via cookie.
    ok_job = client.post('/api/nodes/pw-host/model-actions', json={'model': 'lmstudio:remote', 'action': 'load'})
    assert ok_job.status_code == 201, ok_job.text
    assert client.post(f"/api/jobs/{ok_job.json()['id']}/cancel").status_code == 200

    # Locking revokes cookie; explicit token still works until password rotates, cookie fails.
    locked = client.post('/api/nodes/pw-host/auth/lock')
    assert locked.status_code == 200
    assert locked.json()['unlocked'] is False
    assert client.get('/api/nodes').json()[0]['auth_unlocked'] is False
    assert client.post('/api/nodes/pw-host/model-actions', json={'model': 'lmstudio:remote', 'action': 'load'}).status_code == 403

    # Header token works for non-cookie callers.
    header_job = client.post(
        '/api/nodes/pw-host/model-actions',
        headers={'X-Host-Auth-Token': payload['token']},
        json={'model': 'lmstudio:remote', 'action': 'load'},
    )
    assert header_job.status_code == 201, header_job.text


def test_local_host_password_lifecycle_heartbeat_and_rotation(client):
    register(client, 'demo-node')
    assert client.patch('/api/nodes/demo-node/auth/password', json={'new_password': 'demo-pass'}).status_code == 422

    register_host(client, 'local-host')
    # Unprotected host allows model actions directly.
    first = control(client, 'local-host')
    assert first.status_code == 201
    assert client.post(f"/api/jobs/{first.json()['id']}/cancel").status_code == 200

    # Set password on existing local host.
    set_pw = client.patch('/api/nodes/local-host/auth/password', json={'new_password': 'alpha-pass'})
    assert set_pw.status_code == 200, set_pw.text
    assert set_pw.json()['auth_required'] is True
    assert set_pw.json()['auth_unlocked'] is False

    # Password survives subsequent heartbeats.
    register_host(client, 'local-host')
    local_node = next(n for n in client.get('/api/nodes').json() if n['id'] == 'local-host')
    assert local_node['auth_required'] is True
    assert control(client, 'local-host').status_code == 403

    # Unlock and grab token.
    unlock = client.post('/api/nodes/local-host/auth/unlock', json={'password': 'alpha-pass'})
    assert unlock.status_code == 200
    old_token = unlock.json()['token']

    # Changing password requires current password.
    assert client.patch('/api/nodes/local-host/auth/password', json={'new_password': 'beta-pass'}).status_code == 401
    assert client.patch('/api/nodes/local-host/auth/password', json={'current_password': 'wrong', 'new_password': 'beta-pass'}).status_code == 401

    # Rotating password invalidates both cookie and previously issued token.
    rotated = client.patch('/api/nodes/local-host/auth/password', json={'current_password': 'alpha-pass', 'new_password': 'beta-pass'})
    assert rotated.status_code == 200
    assert rotated.json()['auth_unlocked'] is False
    assert control(client, 'local-host').status_code == 403
    assert client.post(
        '/api/nodes/local-host/model-actions',
        headers={'X-Host-Auth-Token': old_token},
        json={'model': 'lmstudio:test', 'action': 'load'},
    ).status_code == 403

    # Old password fails; new password succeeds.
    assert client.post('/api/nodes/local-host/auth/unlock', json={'password': 'alpha-pass'}).status_code == 401
    assert client.post('/api/nodes/local-host/auth/unlock', json={'password': 'beta-pass'}).status_code == 200
    loaded = control(client, 'local-host')
    assert loaded.status_code == 201
    assert client.post(f"/api/jobs/{loaded.json()['id']}/cancel").status_code == 200

    # Removing password requires current password and restores direct actions.
    assert client.patch('/api/nodes/local-host/auth/password', json={'current_password': 'wrong', 'new_password': ''}).status_code == 401
    cleared = client.patch('/api/nodes/local-host/auth/password', json={'current_password': 'beta-pass', 'new_password': ''})
    assert cleared.status_code == 200
    assert cleared.json()['auth_required'] is False
    assert control(client, 'local-host').status_code == 201


def test_per_host_unlock_isolation_and_rate_limit(client):
    register_host(client, 'host-a')
    register_host(client, 'host-b')
    assert client.patch('/api/nodes/host-a/auth/password', json={'new_password': 'pass-aaaa'}).status_code == 200
    assert client.patch('/api/nodes/host-b/auth/password', json={'new_password': 'pass-bbbb'}).status_code == 200

    assert client.post('/api/nodes/host-a/auth/unlock', json={'password': 'pass-aaaa'}).status_code == 200
    assert control(client, 'host-a').status_code == 201
    assert control(client, 'host-b').status_code == 403

    assert client.post('/api/nodes/host-b/auth/unlock', json={'password': 'pass-bbbb'}).status_code == 200
    assert client.post('/api/nodes/host-a/auth/lock').status_code == 200
    nodes_by_id = {n['id']: n for n in client.get('/api/nodes').json()}
    assert nodes_by_id['host-a']['auth_unlocked'] is False
    assert nodes_by_id['host-b']['auth_unlocked'] is True

    # 5 consecutive failures trigger rate limit on host-a only.
    for _ in range(5):
        assert client.post('/api/nodes/host-a/auth/unlock', json={'password': 'wrong-pw'}).status_code == 401
    assert client.post('/api/nodes/host-a/auth/unlock', json={'password': 'pass-aaaa'}).status_code == 429
    # Host B is unaffected.
    assert client.post('/api/nodes/host-b/auth/unlock', json={'password': 'pass-bbbb'}).status_code == 200
