"""Integration tests use an isolated temporary schema on the configured PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import main
from app.db import Base, engine
from app.models import Job, now


@pytest.fixture
def client(monkeypatch):
    schema = 'test_' + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    test_engine = create_engine(engine.url, connect_args={'options': f'-csearch_path={schema}'})
    Base.metadata.create_all(test_engine)
    monkeypatch.setattr(main, 'Session', sessionmaker(test_engine, expire_on_commit=False))
    monkeypatch.setattr(main, 'engine', test_engine)
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
