"""Integration tests for hardware telemetry history recording, anomaly detection, and diagnostics."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import main
from app.db import Base, engine
from app.models import HardwareSample

AUTH = {'X-Agent-Token': 'integration-test-token'}


@pytest.fixture
def client(monkeypatch):
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


def test_heartbeat_records_hardware_history_and_debounced_alerts(client):
    payload = {
        'name': 'TRX50 Rig',
        'mode': 'host',
        'capabilities': ['lmstudio:qwen3'],
        'metrics': {
            'simulated': False,
            'cpu_percent': 95.0,
            'cpu_temp_c': 72.0,
            'load_avg_1m': 6.4,
            'ram_percent': 62.0,
            'ram_used_gb': 79.4,
            'ram_total_gb': 128.0,
            'disk_percent': 54.0,
            'lmstudio': {'status': 'online'},
            'ollama': {'status': 'offline'},
            'comfyui': {'status': 'online'},
            'gpus': [
                {
                    'index': 0, 'name': 'RTX 4090', 'utilization': 92.0,
                    'memory_used_mb': 23500.0, 'memory_total_mb': 24576.0,
                    'temperature_c': 84.0, 'power_w': 435.0, 'power_limit_w': 450.0,
                    'fan_speed_pct': 82.0, 'clock_sm_mhz': 2655.0, 'clock_mem_mhz': 10501.0, 'pstate': 'P0',
                },
                {
                    'index': 1, 'name': 'RTX 4090', 'utilization': 4.0,
                    'memory_used_mb': 512.0, 'memory_total_mb': 24576.0,
                    'temperature_c': 35.0, 'power_w': 19.0, 'power_limit_w': 450.0,
                    'fan_speed_pct': 0.0, 'clock_sm_mhz': 210.0, 'clock_mem_mhz': 405.0, 'pstate': 'P8',
                },
            ],
        },
    }
    # Send two consecutive heartbeats — anomalies must debounce so alerts are NOT duplicated!
    assert client.put('/api/agent/nodes/trx50', headers=AUTH, json=payload).status_code == 200
    assert client.put('/api/agent/nodes/trx50', headers=AUTH, json=payload).status_code == 200

    # Also register demo node to verify simulated isolation
    assert client.put('/api/agent/nodes/demo-01', headers=AUTH, json={
        'name': 'Demo', 'mode': 'demo', 'capabilities': ['demo.progress'],
        'metrics': {'simulated': True, 'cpu_percent': 12, 'ram_percent': 20, 'gpus': [{'index': 0, 'name': 'Demo GPU', 'utilization': 30, 'temperature_c': 48}]},
    }).status_code == 200

    res = client.get('/api/hardware/analytics?range=24h')
    assert res.status_code == 200
    data = res.json()
    assert data['summary']['sample_count'] == 2
    assert data['summary']['has_simulated'] is False
    assert data['summary']['thermal']['max_c'] == 84.0
    assert data['summary']['runtimes_uptime_pct']['lmstudio'] == 100.0
    assert data['summary']['runtimes_uptime_pct']['ollama'] == 0.0
    assert len(data['gpu_profiles']) == 2

    gpu0 = next(g for g in data['gpu_profiles'] if g['gpu_index'] == 0)
    assert gpu0['health_grade'] == 'hot'
    assert gpu0['avg_fan_pct'] == 82.0
    assert gpu0['avg_clock_mhz'] == 2655.0

    # Check debounced alerts: 1 critical temp + 1 critical VRAM + 1 warning power + 1 warning CPU = 4 events (NOT 8!)
    events = data['events']
    event_types = {(e['event_type'], e['gpu_index'], e['severity']) for e in events}
    assert ('high_temp', 0, 'critical') in event_types
    assert ('vram_pressure', 0, 'critical') in event_types
    assert ('high_power', 0, 'warning') in event_types
    assert ('cpu_pressure', None, 'warning') in event_types
    assert len(events) == 4
    assert data['summary']['unacked_critical'] == 2
    assert data['summary']['health_score'] < 100

    # Filter by gpu_index=1
    res_g1 = client.get('/api/hardware/analytics?range=24h&gpu_index=1').json()
    assert len(res_g1['gpu_profiles']) == 1
    assert res_g1['gpu_profiles'][0]['gpu_index'] == 1
    assert res_g1['gpu_profiles'][0]['health_grade'] == 'healthy'


def test_hardware_events_crud_maintenance_audit_and_csv_export(client):
    assert client.put('/api/agent/nodes/rig-1', headers=AUTH, json={
        'name': 'Rig 1', 'mode': 'host', 'capabilities': [],
        'metrics': {'simulated': False, 'cpu_percent': 25, 'ram_percent': 40, 'gpus': [{'index': 0, 'name': 'RTX 4090', 'utilization': 55, 'memory_used_mb': 12000, 'memory_total_mb': 24576, 'temperature_c': 62, 'power_w': 260}]},
    }).status_code == 200

    # Toggling maintenance records an audit event automatically
    assert client.patch('/api/nodes/rig-1/maintenance', json={'enabled': True}).status_code == 200
    ev_list = client.get('/api/hardware/events').json()
    assert any(e['event_type'] == 'maintenance_on' for e in ev_list)

    # Create manual ops note
    created = client.post('/api/hardware/events', json={
        'node_id': 'rig-1',
        'gpu_index': 0,
        'severity': 'warning',
        'event_type': 'manual_note',
        'message': '更換 RTX 4090 散熱膏並調整風扇曲線',
    })
    assert created.status_code == 201
    ev_id = created.json()['id']
    assert created.json()['acknowledged'] is False

    # Acknowledge single event
    acked = client.patch(f'/api/hardware/events/{ev_id}/ack', json={'acknowledged': True})
    assert acked.status_code == 200
    assert acked.json()['acknowledged'] is True

    # Ack-all endpoint works cleanly
    assert client.post('/api/hardware/events/ack-all').status_code == 200

    # Insert multi-bucket history for thermal curve & CSV check
    base = datetime(2026, 11, 27, 2, 0, tzinfo=timezone.utc)
    with main.Session.begin() as db:
        for i in range(5):
            db.add(HardwareSample(
                node_id='rig-1', node_name='Rig 1',
                recorded_at=base + timedelta(minutes=i * 10),
                interval_s=600.0, cpu_percent=30.0, ram_percent=45.0, gpu_count=1,
                avg_gpu_util=20.0 * i, max_gpu_util=20.0 * i,
                avg_gpu_temp_c=40.0 + 8.0 * i, max_gpu_temp_c=40.0 + 8.0 * i,
                per_gpu=[{'index': 0, 'name': 'RTX 4090', 'utilization': 20.0 * i, 'temperature_c': 40.0 + 8.0 * i, 'memory_used_mb': 8192, 'memory_total_mb': 24576, 'fan_speed_pct': 30.0 + 10.0 * i}],
                simulated=False,
            ))

    for target in ('timeseries', 'gpus', 'events'):
        csv_res = client.get(f'/api/hardware/export?target={target}&range=all')
        assert csv_res.status_code == 200
        assert 'text/csv' in csv_res.headers['content-type']
        assert csv_res.content.startswith(b'\xef\xbb\xbf')

    # Delete manual note
    assert client.delete(f'/api/hardware/events/{ev_id}').status_code == 200
    assert client.delete(f'/api/hardware/events/{ev_id}').status_code == 404
