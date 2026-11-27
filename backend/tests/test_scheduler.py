"""Integration tests for the dynamic multi-service GPU scheduler and smart routing engine."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import host_auth, main
from app.db import Base, engine
from app.models import Job, now

AUTH = {'X-Agent-Token': 'scheduler-test-token'}


@pytest.fixture
def client(monkeypatch):
    host_auth.reset_rate_limit()
    schema = 'test_sched_' + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    test_engine = create_engine(engine.url, connect_args={'options': f'-csearch_path={schema}'})
    Base.metadata.create_all(test_engine)
    monkeypatch.setattr(main, 'Session', sessionmaker(test_engine, expire_on_commit=False))
    monkeypatch.setattr(main, 'engine', test_engine)
    monkeypatch.setenv('ENABLE_REMOTE_WORKERS', 'false')
    monkeypatch.setenv('AGENT_TOKEN', 'scheduler-test-token')
    monkeypatch.setenv('ENABLE_DEMO', 'true')
    with TestClient(main.app) as c:
        yield c
    test_engine.dispose()
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))


def register_gpu_host(
    client,
    node_id: str,
    *,
    models: list[str] | None = None,
    warm_models: list[str] | None = None,
    gpu_util: float = 10.0,
    vram_used_mb: float = 4096.0,
    vram_total_mb: float = 24576.0,
    temp_c: float = 52.0,
    power_w: float = 110.0,
    comfyui_running: int = 0,
):
    model_ids = models or ['ollama:qwen2.5:7b']
    warm = set(warm_models or [])
    catalog = []
    ollama_loaded = []
    lmstudio_loaded = []
    for mid in model_ids:
        rt, name = mid.split(':', 1)
        catalog.append({
            'id': mid,
            'name': name,
            'runtime': rt,
            'model_type': 'llm',
            'runtime_model': name,
            'capability': mid,
        })
        if mid in warm:
            if rt == 'ollama':
                ollama_loaded.append({'name': name, 'size_bytes': 4_000_000_000, 'vram_bytes': 4_000_000_000})
            elif rt == 'lmstudio':
                lmstudio_loaded.append({'name': name, 'instance_id': name, 'size_bytes': None, 'vram_bytes': None})

    metrics = {
        'simulated': False,
        'cpu_percent': 15.0,
        'ram_percent': 30.0,
        'gpus': [{
            'index': 0,
            'name': 'RTX 4090',
            'utilization': gpu_util,
            'memory_used_mb': vram_used_mb,
            'memory_total_mb': vram_total_mb,
            'temperature_c': temp_c,
            'power_w': power_w,
        }],
        'ollama': {'status': 'online', 'loaded_models_status': 'online', 'loaded_models': ollama_loaded},
        'lmstudio': {'status': 'online', 'loaded_models_status': 'online', 'loaded_models': lmstudio_loaded},
        'comfyui': {
            'status': 'online',
            'running': [{'id': f'p{i}'} for i in range(comfyui_running)],
            'pending': [],
        },
    }
    res = client.put(
        f'/api/agent/nodes/{node_id}',
        headers=AUTH,
        json={
            'name': node_id,
            'mode': 'host',
            'capabilities': model_ids + ['ollama.model_control', 'lmstudio.model_control'],
            'model_catalog': catalog,
            'metrics': metrics,
        },
    )
    assert res.status_code == 200, res.text


def submit_llm(client, *, name='req', source='svc-a', model='ollama:qwen2.5:7b', prompt='hi', **extra):
    res = client.post('/api/jobs', json={'name': name, 'source': source, 'model': model, 'prompt': prompt, **extra})
    assert res.status_code == 201, res.text
    return res.json()


def claim_job(client, node_id: str):
    res = client.post(f'/api/agent/nodes/{node_id}/claim', headers=AUTH)
    assert res.status_code == 200, res.text
    return res.json()['job']


def complete_job(client, job, duration_s: float = 60.0):
    with main.Session.begin() as db:
        row = db.get(Job, job['id'])
        row.started_at = now() - timedelta(seconds=duration_s)
    res = client.post(
        f"/api/agent/jobs/{job['id']}/report",
        headers=AUTH,
        json={'node_id': job['node_id'], 'attempt_id': job['attempt_id'], 'progress': 100, 'status': 'completed', 'result': {'text': 'ok'}},
    )
    assert res.status_code == 200, res.text


def test_multi_service_fairness_prevents_starvation(client):
    register_gpu_host(client, 'gpu-1')
    # Heavy batch service consumes 120s of recent GPU time
    for i in range(2):
        j = submit_llm(client, name=f'heavy-{i}', source='heavy-batch', priority=2)
        leased = claim_job(client, 'gpu-1')
        assert leased['id'] == j['id']
        complete_job(client, leased, duration_s=90.0)

    # Now both heavy-batch and light-chat enqueue at the exact same priority=2.
    heavy_q = submit_llm(client, name='heavy-queued', source='heavy-batch', priority=2)
    light_q = submit_llm(client, name='light-queued', source='light-chat', priority=2)

    state = client.get('/api/scheduler/state').json()
    assert state['queue_plan'][0]['job_id'] == light_q['id']
    assert state['queue_plan'][1]['job_id'] == heavy_q['id']

    # Scheduler dispatches light-chat first due to fairness pressure on heavy-batch.
    picked = claim_job(client, 'gpu-1')
    assert picked['id'] == light_q['id']
    assert 'scheduler_decision' in picked
    assert picked['scheduler_decision']['breakdown']['fairness'] >= 0


def test_sla_class_and_deadline_urgency_ordering(client):
    register_gpu_host(client, 'gpu-1')
    batch_job = submit_llm(client, name='batch', source='svc-a', priority=3, sla_class='batch', max_tokens=1024)
    std_job = submit_llm(client, name='standard', source='svc-b', priority=3, sla_class='standard', max_tokens=256)
    rt_job = submit_llm(client, name='realtime', source='svc-c', priority=3, sla_class='realtime', max_tokens=64)

    explain_rt = client.get(f"/api/jobs/{rt_job['id']}/explain").json()
    assert explain_rt['queue_rank'] == 1
    assert explain_rt['sla_class'] == 'realtime'
    assert explain_rt['recommended_node_id'] == 'gpu-1'

    picked1 = claim_job(client, 'gpu-1')
    assert picked1['id'] == rt_job['id']
    complete_job(client, picked1, duration_s=2.0)

    # Give batch_job a tight imminent deadline that elevates it above standard when deadline weight is active
    deadline_job = submit_llm(client, name='urgent-deadline', source='svc-d', priority=2, sla_class='standard', deadline_seconds=15)
    with main.Session.begin() as db:
        db.get(Job, deadline_job['id']).created_at = now() - timedelta(seconds=14)
    picked2 = claim_job(client, 'gpu-1')
    assert picked2['id'] == deadline_job['id']
    assert batch_job['id'] != picked2['id']
    assert std_job['id'] != picked2['id']


def test_smart_routing_yields_to_warm_healthy_host_and_falls_back_after_window(client):
    # host-hot: cold model, high temp (83C), high VRAM (85%), ComfyUI running
    register_gpu_host(
        client,
        'host-hot',
        models=['ollama:qwen2.5:7b'],
        warm_models=[],
        gpu_util=85.0,
        vram_used_mb=20800.0,
        temp_c=83.0,
        comfyui_running=1,
    )
    # host-warm: model already resident in VRAM, cool (48C), low VRAM (25%)
    register_gpu_host(
        client,
        'host-warm',
        models=['ollama:qwen2.5:7b'],
        warm_models=['ollama:qwen2.5:7b'],
        gpu_util=5.0,
        vram_used_mb=6144.0,
        temp_c=48.0,
        comfyui_running=0,
    )

    job = submit_llm(client, name='warm-routed', source='chat-api', model='ollama:qwen2.5:7b')

    # Simulation predicts host-warm wins with warm_hit=True.
    sim = client.post('/api/scheduler/simulate', json={'model': 'ollama:qwen2.5:7b', 'source': 'chat-api'}).json()
    assert sim['decision_action'] == 'dispatch'
    assert sim['selected_node_id'] == 'host-warm'
    assert any(c['node_id'] == 'host-warm' and c['warm_hit'] for c in sim['candidates'])

    # If host-hot polls first within reservation window, it yields to host-warm!
    assert claim_job(client, 'host-hot') is None
    decisions = client.get('/api/scheduler/decisions', params={'action': 'yield'}).json()
    assert len(decisions) == 1
    assert decisions[0]['selected_node_id'] == 'host-warm'

    # host-warm polls next and claims the job immediately.
    leased = claim_job(client, 'host-warm')
    assert leased is not None
    assert leased['id'] == job['id']
    assert leased['scheduler_decision']['warm_hit'] is True

    # Verify anti-deadlock: if another job waits past route_reservation_s, host-hot CAN claim it.
    complete_job(client, leased, duration_s=1.0)
    old_job = submit_llm(client, name='aged-job', source='chat-api', model='ollama:qwen2.5:7b')
    with main.Session.begin() as db:
        db.get(Job, old_job['id']).created_at = now() - timedelta(seconds=20)
    leased_hot = claim_job(client, 'host-hot')
    assert leased_hot is not None
    assert leased_hot['id'] == old_job['id']


def test_thermal_guard_and_peak_tariff_defer_batch_but_allow_realtime(client):
    # Host exceeds hard thermal guard (90C >= 88C)
    register_gpu_host(client, 'hot-node', temp_c=90.0)
    batch = submit_llm(client, name='bg-job', source='indexer', sla_class='batch', allow_defer=True)
    assert claim_job(client, 'hot-node') is None

    explain = client.get(f"/api/jobs/{batch['id']}/explain").json()
    assert explain['decision_action'] == 'deferred'
    assert 'thermal_guard' in explain['candidates'][0]['blockers']

    # Realtime job bypasses thermal batch deferral guard so critical traffic still completes.
    rt = submit_llm(client, name='rt-job', source='urgent-api', sla_class='realtime')
    leased = claim_job(client, 'hot-node')
    assert leased is not None
    assert leased['id'] == rt['id']
    complete_job(client, leased, duration_s=1.0)

    # Cool down host, enable thermal_eco preset (defer_batch_on_peak_tariff=True) and force peak tariff 24h
    register_gpu_host(client, 'hot-node', temp_c=55.0)
    tariff_res = client.put('/api/energy/tariff', json={
        'tariff_mode': 'tou_custom',
        'peak_start_hour': 0,
        'peak_end_hour': 24,
        'weekend_off_peak': False,
    })
    assert tariff_res.status_code == 200
    pol_res = client.put('/api/scheduler/policy', json={'mode': 'thermal_eco', 'apply_preset': True})
    assert pol_res.status_code == 200
    assert pol_res.json()['defer_batch_on_peak_tariff'] is True

    # Batch job is deferred during peak tariff
    assert claim_job(client, 'hot-node') is None
    explain_peak = client.get(f"/api/jobs/{batch['id']}/explain").json()
    assert 'peak_tariff_deferred' in explain_peak['candidates'][0]['blockers']

    # Turn off deferral -> batch job immediately dispatches
    pol_off = client.put('/api/scheduler/policy', json={'mode': 'balanced', 'apply_preset': True})
    assert pol_off.status_code == 200
    assert claim_job(client, 'hot-node')['id'] == batch['id']


def test_fallback_model_routing_when_primary_unavailable(client):
    register_gpu_host(client, 'node-primary', models=['ollama:big-70b'])
    register_gpu_host(client, 'node-fallback', models=['ollama:fast-7b'])

    # Occupy node-primary so primary model has no ready slot
    blocker = submit_llm(client, name='occupy', source='svc-1', model='ollama:big-70b')
    assert claim_job(client, 'node-primary')['id'] == blocker['id']

    # Submit request preferring big-70b with fallback fast-7b
    req = submit_llm(
        client,
        name='with-fallback',
        source='svc-2',
        model='ollama:big-70b',
        fallback_models=['ollama:fast-7b'],
    )
    leased = claim_job(client, 'node-fallback')
    assert leased is not None
    assert leased['id'] == req['id']
    assert leased['model'] == 'ollama:fast-7b'
    assert leased['parameters']['original_model'] == 'ollama:big-70b'

    fb_decisions = client.get('/api/scheduler/decisions', params={'action': 'fallback'}).json()
    assert len(fb_decisions) == 1
    assert fb_decisions[0]['model'] == 'ollama:fast-7b'


def test_admission_quota_and_policy_source_weights(client):
    register_gpu_host(client, 'gpu-1')
    updated = client.put(
        '/api/scheduler/policy',
        json={
            'mode': 'fair_share',
            'apply_preset': True,
            'max_queued_per_source': 2,
            'source_weights': {'vip-service': 3.0, 'bulk-crawler': 0.5},
        },
    )
    assert updated.status_code == 200, updated.text
    pol = updated.json()
    assert pol['max_queued_per_source'] == 2
    assert pol['source_weights'] == {'vip-service': 3.0, 'bulk-crawler': 0.5}

    submit_llm(client, name='b1', source='bulk-crawler')
    submit_llm(client, name='b2', source='bulk-crawler')
    # Third queued job from bulk-crawler is rejected with 429 and logged
    rejected = client.post(
        '/api/jobs',
        json={'name': 'b3', 'source': 'bulk-crawler', 'model': 'ollama:qwen2.5:7b', 'prompt': 'overflow'},
    )
    assert rejected.status_code == 429
    rej_logs = client.get('/api/scheduler/decisions', params={'action': 'reject', 'source': 'bulk-crawler'}).json()
    assert len(rej_logs) == 1

    # VIP service can still enqueue and jumps ahead of bulk-crawler due to higher source_weight
    vip = submit_llm(client, name='vip-1', source='vip-service')
    assert claim_job(client, 'gpu-1')['id'] == vip['id']
