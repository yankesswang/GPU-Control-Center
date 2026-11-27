"""Integration and unit tests for electricity telemetry recording, tariff engine, and analytics."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import main
from app.db import Base, engine
from app.energy import rate_at_timestamp
from app.models import PowerSample, TariffSetting

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


def test_tou_rate_schedule_taiwan():
    tariff = TariffSetting(
        id='default',
        currency='TWD',
        tariff_mode='tou_tw',
        flat_rate=3.85,
        peak_rate=5.06,
        off_peak_rate=2.06,
        non_summer_peak_rate=4.86,
        non_summer_off_peak_rate=1.99,
        peak_start_hour=9,
        peak_end_hour=24,
        weekend_off_peak=True,
        summer_months=[6, 7, 8, 9],
        timezone_offset_hours=8.0,
    )
    # Summer weekday 14:00 UTC+8 -> 06:00 UTC (2026-07-15 is Wednesday)
    rate, tier, is_summer = rate_at_timestamp(datetime(2026, 7, 15, 6, 0, tzinfo=timezone.utc), tariff)
    assert (rate, tier, is_summer) == (5.06, 'peak', True)

    # Summer weekday 03:00 UTC+8 -> 19:00 UTC prev day
    rate, tier, is_summer = rate_at_timestamp(datetime(2026, 7, 14, 19, 0, tzinfo=timezone.utc), tariff)
    assert (rate, tier, is_summer) == (2.06, 'off_peak', True)

    # Summer Saturday 14:00 UTC+8 -> weekend off-peak (2026-07-18 is Saturday)
    rate, tier, is_summer = rate_at_timestamp(datetime(2026, 7, 18, 6, 0, tzinfo=timezone.utc), tariff)
    assert (rate, tier, is_summer) == (2.06, 'off_peak', True)

    # Non-summer weekday 14:00 UTC+8 (2026-11-27 is Friday)
    rate, tier, is_summer = rate_at_timestamp(datetime(2026, 11, 27, 6, 0, tzinfo=timezone.utc), tariff)
    assert (rate, tier, is_summer) == (4.86, 'peak', False)

    # Flat mode
    tariff.tariff_mode = 'flat'
    rate, tier, _ = rate_at_timestamp(datetime(2026, 7, 15, 6, 0, tzinfo=timezone.utc), tariff)
    assert (rate, tier) == (3.85, 'flat')


def test_heartbeat_records_power_and_analytics_excludes_demo_by_default(client):
    # Real host heartbeat with 2 GPUs
    r1 = client.put('/api/agent/nodes/gpu-box', headers=AUTH, json={
        'name': 'GPU Box',
        'mode': 'host',
        'capabilities': ['ollama:llama3'],
        'metrics': {
            'simulated': False,
            'gpus': [
                {'index': 0, 'name': 'RTX 4090', 'utilization': 88, 'memory_used_mb': 18000, 'memory_total_mb': 24576, 'temperature_c': 68, 'power_w': 320.0},
                {'index': 1, 'name': 'RTX 4090', 'utilization': 2, 'memory_used_mb': 1024, 'memory_total_mb': 24576, 'temperature_c': 36, 'power_w': 20.0},
            ],
        },
    })
    assert r1.status_code == 200

    # Demo node heartbeat (simulated)
    r2 = client.put('/api/agent/nodes/demo-01', headers=AUTH, json={
        'name': 'Demo Node',
        'mode': 'demo',
        'capabilities': ['demo.progress'],
        'metrics': {
            'simulated': True,
            'gpus': [{'index': 0, 'name': 'Demo GPU', 'utilization': 35, 'power_w': 90.0}],
        },
    })
    assert r2.status_code == 200

    # Default analytics excludes simulated samples
    res = client.get('/api/energy/analytics?range=24h')
    assert res.status_code == 200
    body = res.json()
    assert body['summary']['sample_count'] == 1
    assert body['summary']['has_simulated'] is False
    assert body['live']['gpu_power_w'] == pytest.approx(340.0, abs=0.1)
    assert len(body['gpu_breakdown']) == 2
    assert len(body['heatmap']['days']) == 7
    assert len(body['heatmap']['days'][0]['hours']) == 24
    # GPU 1 had 2% utilization -> classified as 100% idle
    gpu1 = next(g for g in body['gpu_breakdown'] if g['gpu_index'] == 1)
    assert gpu1['idle_pct'] == 100.0

    # Opt-in simulated includes demo sample
    res_sim = client.get('/api/energy/analytics?range=24h&include_simulated=true')
    assert res_sim.status_code == 200
    assert res_sim.json()['summary']['sample_count'] == 2
    assert res_sim.json()['summary']['has_simulated'] is True


def test_tariff_update_and_bill_ledger_reconciliation_and_csv(client):
    # Update tariff settings
    put_tariff = client.put('/api/energy/tariff', json={
        'currency': 'TWD',
        'tariff_mode': 'flat',
        'flat_rate': 4.0,
        'peak_rate': 5.2,
        'off_peak_rate': 2.1,
        'non_summer_peak_rate': 4.9,
        'non_summer_off_peak_rate': 2.0,
        'peak_start_hour': 9,
        'peak_end_hour': 24,
        'weekend_off_peak': True,
        'summer_months': [6, 7, 8, 9],
        'timezone_offset_hours': 8.0,
        'pue_factor': 1.1,
        'base_host_watts': 100.0,
        'co2_kg_per_kwh': 0.5,
        'monthly_budget': 2500.0,
    })
    assert put_tariff.status_code == 200
    assert put_tariff.json()['flat_rate'] == 4.0

    # Validation check
    assert client.put('/api/energy/tariff', json={'pue_factor': 0.5}).status_code == 422

    # Insert 1 hour of deterministic synthetic telemetry (400W GPU + 100W base = 500W * 1.1 PUE = 550W -> 0.55 kWh -> NT$ 2.20)
    base_ts = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)
    with main.Session.begin() as db:
        for i in range(12):
            db.add(PowerSample(
                node_id='trx50',
                node_name='TRX50',
                recorded_at=base_ts + timedelta(minutes=i * 5),
                interval_s=300.0,
                gpu_power_w=400.0,
                gpu_count=2,
                avg_utilization=75.0,
                avg_temperature_c=64.0,
                per_gpu=[
                    {'index': 0, 'name': 'RTX 4090', 'power_w': 350.0, 'utilization': 95.0, 'temperature_c': 70.0},
                    {'index': 1, 'name': 'RTX 4090', 'power_w': 50.0, 'utilization': 55.0, 'temperature_c': 58.0},
                ],
                active_model='lmstudio:qwen3-32b',
                simulated=False,
            ))

    # Create two electricity bills (older + newer covering 2026-11-20)
    b1 = client.post('/api/energy/bills', json={
        'billing_date': '2026-10-31',
        'period_start': '2026-10-01',
        'period_end': '2026-10-31',
        'label': '2026年10月電費帳單',
        'category': 'utility_bill',
        'kwh': 200.0,
        'cost': 800.0,
        'currency': 'TWD',
    })
    assert b1.status_code == 201

    b2 = client.post('/api/energy/bills', json={
        'billing_date': '2026-11-30',
        'period_start': '2026-11-01',
        'period_end': '2026-11-30',
        'label': '2026年11月電費帳單',
        'category': 'utility_bill',
        'node_id': 'trx50',
        'kwh': 10.0,
        'cost': 1000.0,
        'currency': 'TWD',
        'peak_kwh': 6.0,
        'off_peak_kwh': 4.0,
        'notes': 'GPU workstation meter',
    })
    assert b2.status_code == 201
    bill2_id = b2.json()['id']

    # Invalid bill date order rejected
    bad_bill = client.post('/api/energy/bills', json={
        'billing_date': '2026-11-30',
        'period_start': '2026-11-20',
        'period_end': '2026-11-10',
        'label': 'Invalid',
        'kwh': 10.0,
        'cost': 50.0,
    })
    assert bad_bill.status_code == 422

    # Check full analytics & reconciliation
    analytics = client.get('/api/energy/analytics?range=all').json()
    assert analytics['summary']['total_kwh'] == pytest.approx(0.55, abs=0.002)
    assert analytics['summary']['total_cost'] == pytest.approx(2.20, abs=0.01)
    assert analytics['summary']['stats']['mean_w'] == pytest.approx(550.0, abs=0.1)
    assert analytics['summary']['stats']['load_factor'] == pytest.approx(1.0, abs=0.01)
    assert analytics['bill_analytics']['count'] == 2
    assert analytics['bill_analytics']['mom_cost_change_pct'] == pytest.approx(25.0, abs=0.1)
    nov_bill = next(b for b in analytics['bill_analytics']['bills'] if b['id'] == bill2_id)
    assert nov_bill['telemetry_kwh'] == pytest.approx(0.55, abs=0.01)
    assert nov_bill['gpu_share_pct'] == pytest.approx(5.5, abs=0.2)

    # Update bill
    upd = client.put(f'/api/energy/bills/{bill2_id}', json={
        'billing_date': '2026-11-30',
        'period_start': '2026-11-01',
        'period_end': '2026-11-30',
        'label': '2026年11月台電帳單 (修正)',
        'category': 'smart_plug',
        'kwh': 11.0,
        'cost': 1100.0,
        'currency': 'TWD',
    })
    assert upd.status_code == 200
    assert upd.json()['label'] == '2026年11月台電帳單 (修正)'

    # CSV exports
    for target in ('timeseries', 'bills', 'gpu'):
        csv_res = client.get(f'/api/energy/export?target={target}&range=all')
        assert csv_res.status_code == 200
        assert 'text/csv' in csv_res.headers['content-type']
        assert csv_res.content.startswith(b'\xef\xbb\xbf')  # UTF-8 BOM

    # Delete bill
    assert client.delete(f'/api/energy/bills/{bill2_id}').status_code == 200
    assert client.delete(f'/api/energy/bills/{bill2_id}').status_code == 404
