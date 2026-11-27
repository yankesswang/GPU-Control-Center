"""Electricity telemetry ingestion, Time-of-Use pricing, statistical analytics & bill reconciliation."""
from __future__ import annotations

import csv
import io
import math
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, func, inspect, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from .models import ElectricityBill, Job, Node, PowerSample, TariffSetting, now

RANGE_SPECS: dict[str, tuple[timedelta | None, int]] = {
    # range_key: (lookback_delta, bucket_seconds)
    '1h': (timedelta(hours=1), 120),        # 2-minute buckets (~30 points)
    '6h': (timedelta(hours=6), 600),        # 10-minute buckets (~36 points)
    '24h': (timedelta(hours=24), 1800),     # 30-minute buckets (~48 points)
    '7d': (timedelta(days=7), 10800),       # 3-hour buckets (~56 points)
    '30d': (timedelta(days=30), 86400),     # 1-day buckets (~30 points)
    '90d': (timedelta(days=90), 86400),     # 1-day buckets (~90 points)
    'all': (None, 86400),                   # adaptive later
}

MAX_SAMPLE_INTERVAL_S = 30.0
DEFAULT_SAMPLE_INTERVAL_S = 4.0
IDLE_UTIL_THRESHOLD = 5.0


def _encode(obj) -> dict[str, Any]:
    return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}


def _finite_number(val: Any) -> float | None:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    f = float(val)
    return f if math.isfinite(f) else None


def get_or_create_tariff(db) -> TariffSetting:
    setting = db.get(TariffSetting, 'default')
    if setting is None:
        db.execute(
            pg_insert(TariffSetting)
            .values(id='default', updated_at=now())
            .on_conflict_do_nothing(index_elements=['id'])
        )
        setting = db.get(TariffSetting, 'default')
    return setting


def _local_datetime(ts: datetime, offset_hours: float) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    tz = timezone(timedelta(hours=offset_hours))
    return ts.astimezone(tz)


def is_peak_hour(local_dt: datetime, tariff: TariffSetting) -> bool:
    if tariff.weekend_off_peak and local_dt.weekday() >= 5:
        return False
    h = local_dt.hour
    start = int(tariff.peak_start_hour)
    end = int(tariff.peak_end_hour)
    if start == end:
        return False
    if start < end:
        return start <= h < end
    return h >= start or h < end


def rate_at_timestamp(ts: datetime, tariff: TariffSetting) -> tuple[float, str, bool]:
    """Return (unit_rate, tier, is_summer) for a UTC timestamp under the configured tariff."""
    local_dt = _local_datetime(ts, tariff.timezone_offset_hours)
    summer_months = set(int(m) for m in (tariff.summer_months or [6, 7, 8, 9]))
    is_summer = local_dt.month in summer_months
    if tariff.tariff_mode == 'flat':
        return float(tariff.flat_rate), 'flat', is_summer
    peak = is_peak_hour(local_dt, tariff)
    if tariff.tariff_mode == 'tou_tw' and not is_summer:
        rate = float(tariff.non_summer_peak_rate if peak else tariff.non_summer_off_peak_rate)
    else:
        rate = float(tariff.peak_rate if peak else tariff.off_peak_rate)
    return rate, ('peak' if peak else 'off_peak'), is_summer


def tou_rate_at_timestamp(ts: datetime, tariff: TariffSetting) -> tuple[float, bool]:
    """Compute what the TOU rate would be at ts regardless of current tariff_mode (for comparison)."""
    local_dt = _local_datetime(ts, tariff.timezone_offset_hours)
    summer_months = set(int(m) for m in (tariff.summer_months or [6, 7, 8, 9]))
    is_summer = local_dt.month in summer_months
    peak = is_peak_hour(local_dt, tariff)
    if not is_summer:
        return float(tariff.non_summer_peak_rate if peak else tariff.non_summer_off_peak_rate), peak
    return float(tariff.peak_rate if peak else tariff.off_peak_rate), peak


def record_power_sample(
    db,
    *,
    node_id: str,
    node_name: str,
    mode: str,
    metrics: dict[str, Any],
    recorded_at: datetime | None = None,
) -> PowerSample | None:
    """Persist a power telemetry sample from a node heartbeat when GPU power readings exist."""
    gpus = metrics.get('gpus') if isinstance(metrics, dict) else None
    if not isinstance(gpus, list) or not gpus:
        return None

    per_gpu: list[dict[str, Any]] = []
    gpu_power_sum = 0.0
    utils: list[float] = []
    temps: list[float] = []

    for pos, gpu in enumerate(gpus):
        if not isinstance(gpu, dict):
            continue
        pw = _finite_number(gpu.get('power_w'))
        if pw is None or pw < 0:
            continue
        idx = gpu.get('index')
        gpu_index = idx if isinstance(idx, int) and not isinstance(idx, bool) else pos
        util = _finite_number(gpu.get('utilization'))
        temp = _finite_number(gpu.get('temperature_c'))
        mem_used = _finite_number(gpu.get('memory_used_mb'))
        mem_total = _finite_number(gpu.get('memory_total_mb'))
        if util is not None:
            utils.append(util)
        if temp is not None:
            temps.append(temp)
        gpu_power_sum += pw
        per_gpu.append({
            'index': gpu_index,
            'name': str(gpu.get('name') or f'GPU {gpu_index}')[:80],
            'power_w': round(pw, 2),
            'utilization': round(util, 1) if util is not None else None,
            'temperature_c': round(temp, 1) if temp is not None else None,
            'memory_used_mb': round(mem_used, 1) if mem_used is not None else None,
            'memory_total_mb': round(mem_total, 1) if mem_total is not None else None,
        })

    if not per_gpu:
        return None

    ts = recorded_at or now()
    prev_ts = db.scalar(
        select(PowerSample.recorded_at)
        .where(PowerSample.node_id == node_id)
        .order_by(PowerSample.recorded_at.desc(), PowerSample.id.desc())
        .limit(1)
    )
    if prev_ts is not None:
        if prev_ts.tzinfo is None:
            prev_ts = prev_ts.replace(tzinfo=timezone.utc)
        delta = (ts - prev_ts).total_seconds()
        if 0.05 <= delta <= MAX_SAMPLE_INTERVAL_S:
            interval_s = round(delta, 3)
        else:
            interval_s = DEFAULT_SAMPLE_INTERVAL_S
    else:
        interval_s = DEFAULT_SAMPLE_INTERVAL_S

    active_job = db.scalar(
        select(Job)
        .where(Job.node_id == node_id, Job.status.in_(('running', 'cancelling')))
        .order_by(Job.started_at.desc())
        .limit(1)
    )
    active_job_id = active_job.id if active_job else None
    active_model = active_job.model if active_job else None
    simulated = (mode == 'demo') or bool(metrics.get('simulated'))

    sample = PowerSample(
        node_id=node_id,
        node_name=node_name[:100],
        recorded_at=ts,
        interval_s=interval_s,
        gpu_power_w=round(gpu_power_sum, 2),
        gpu_count=len(per_gpu),
        avg_utilization=round(sum(utils) / len(utils), 2) if utils else None,
        avg_temperature_c=round(sum(temps) / len(temps), 2) if temps else None,
        per_gpu=per_gpu,
        active_job_id=active_job_id,
        active_model=active_model,
        simulated=simulated,
    )
    db.add(sample)
    return sample


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _bucket_label(local_dt: datetime, bucket_s: int) -> str:
    if bucket_s >= 86400:
        return local_dt.strftime('%m/%d')
    if bucket_s >= 3600:
        return local_dt.strftime('%m/%d %H:00')
    return local_dt.strftime('%H:%M')


def _parse_iso_date(val: str) -> date | None:
    try:
        return date.fromisoformat(val)
    except (TypeError, ValueError):
        return None


def _compute_live_power(db, tariff: TariffSetting, node_id: str | None, include_simulated: bool) -> dict[str, Any]:
    cutoff = now() - timedelta(seconds=20)
    stmt = select(Node).where(Node.last_seen >= cutoff).order_by(Node.name)
    if node_id:
        stmt = stmt.where(Node.id == node_id)
    nodes = db.scalars(stmt).all()

    gpu_w = 0.0
    host_overhead_w = 0.0
    active_nodes: list[dict[str, Any]] = []
    live_gpus: list[dict[str, Any]] = []

    for node in nodes:
        is_sim = (node.mode == 'demo') or bool((node.metrics or {}).get('simulated'))
        if is_sim and not include_simulated:
            continue
        gpus = (node.metrics or {}).get('gpus') or []
        node_gpu_w = 0.0
        has_power = False
        for pos, g in enumerate(gpus):
            pw = _finite_number(g.get('power_w'))
            if pw is None or pw < 0:
                continue
            has_power = True
            node_gpu_w += pw
            idx = g.get('index') if isinstance(g.get('index'), int) else pos
            live_gpus.append({
                'node_id': node.id,
                'node_name': node.name,
                'index': idx,
                'name': g.get('name') or f'GPU {idx}',
                'power_w': round(pw, 1),
                'utilization': _finite_number(g.get('utilization')),
                'temperature_c': _finite_number(g.get('temperature_c')),
                'simulated': is_sim,
            })
        if has_power:
            gpu_w += node_gpu_w
            host_overhead_w += float(tariff.base_host_watts)
            node_total_w = (node_gpu_w + float(tariff.base_host_watts)) * float(tariff.pue_factor)
            active_nodes.append({
                'node_id': node.id,
                'node_name': node.name,
                'gpu_power_w': round(node_gpu_w, 1),
                'total_power_w': round(node_total_w, 1),
                'gpu_count': len([g for g in gpus if _finite_number(g.get('power_w')) is not None]),
                'simulated': is_sim,
            })

    subtotal_w = gpu_w + host_overhead_w
    total_w = subtotal_w * float(tariff.pue_factor)
    psu_loss_w = max(0.0, total_w - subtotal_w)

    current_rate, current_tier, is_summer = rate_at_timestamp(now(), tariff)
    kw = total_w / 1000.0
    cost_per_hour = kw * current_rate
    # Project 24h using actual hourly TOU schedule at constant current load
    ref_utc = now().replace(minute=0, second=0, microsecond=0)
    cost_per_day = sum(kw * rate_at_timestamp(ref_utc + timedelta(hours=h), tariff)[0] for h in range(24))

    return {
        'gpu_power_w': round(gpu_w, 2),
        'host_overhead_w': round(host_overhead_w, 2),
        'psu_loss_w': round(psu_loss_w, 2),
        'total_power_w': round(total_w, 2),
        'current_rate': round(current_rate, 4),
        'current_tier': current_tier,
        'is_summer': is_summer,
        'cost_per_hour': round(cost_per_hour, 4),
        'cost_per_day': round(cost_per_day, 2),
        'projected_month_cost_at_current': round(cost_per_day * 30.0, 2),
        'active_nodes': active_nodes,
        'live_gpus': live_gpus,
    }


def _compute_bill_analytics(
    db,
    tariff: TariffSetting,
    *,
    node_id: str | None,
    telemetry_by_date: dict[str, dict[str, float]],
) -> dict[str, Any]:
    stmt = select(ElectricityBill).order_by(desc(ElectricityBill.billing_date), desc(ElectricityBill.created_at))
    if node_id:
        stmt = stmt.where((ElectricityBill.node_id == node_id) | (ElectricityBill.node_id.is_(None)))
    bills = db.scalars(stmt).all()

    bill_items = []
    for b in bills:
        item = _encode(b)
        item['unit_rate'] = round(b.cost / b.kwh, 4) if b.kwh > 0 else 0.0
        # Reconcile against telemetry captured during [period_start, period_end]
        p_start = _parse_iso_date(b.period_start)
        p_end = _parse_iso_date(b.period_end)
        tel_kwh = 0.0
        tel_cost = 0.0
        tel_days = 0
        if p_start and p_end and p_end >= p_start:
            for d_str, agg in telemetry_by_date.items():
                d_obj = _parse_iso_date(d_str)
                if d_obj and p_start <= d_obj <= p_end:
                    tel_kwh += agg['kwh']
                    tel_cost += agg['cost']
                    tel_days += 1
        item['telemetry_kwh'] = round(tel_kwh, 3)
        item['telemetry_cost'] = round(tel_cost, 2)
        item['telemetry_days'] = tel_days
        item['gpu_share_pct'] = round(min(100.0, (tel_kwh / b.kwh) * 100.0), 1) if (b.kwh > 0 and tel_kwh > 0) else None
        bill_items.append(item)

    total_kwh = sum(b.kwh for b in bills)
    total_cost = sum(b.cost for b in bills)
    avg_unit_rate = (total_cost / total_kwh) if total_kwh > 0 else 0.0

    mom_cost_change_pct = None
    mom_kwh_change_pct = None
    if len(bills) >= 2:
        latest, prev = bills[0], bills[1]
        if prev.cost > 0:
            mom_cost_change_pct = round(((latest.cost - prev.cost) / prev.cost) * 100.0, 1)
        if prev.kwh > 0:
            mom_kwh_change_pct = round(((latest.kwh - prev.kwh) / prev.kwh) * 100.0, 1)

    return {
        'count': len(bills),
        'total_kwh': round(total_kwh, 3),
        'total_cost': round(total_cost, 2),
        'avg_unit_rate': round(avg_unit_rate, 4),
        'mom_cost_change_pct': mom_cost_change_pct,
        'mom_kwh_change_pct': mom_kwh_change_pct,
        'latest_bill': bill_items[0] if bill_items else None,
        'bills': bill_items,
    }


def _build_insights(
    summary: dict[str, Any],
    live: dict[str, Any],
    tariff: TariffSetting,
    gpu_breakdown: list[dict[str, Any]],
    bill_analytics: dict[str, Any],
) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []
    cur = tariff.currency

    if summary['sample_count'] > 0:
        # 1. TOU vs Flat tariff optimization insight
        tou_diff = summary['flat_equivalent_cost'] - summary['tou_equivalent_cost']
        if abs(tou_diff) >= 0.05:
            if tou_diff > 0:
                pct = (tou_diff / summary['flat_equivalent_cost'] * 100.0) if summary['flat_equivalent_cost'] > 0 else 0.0
                insights.append({
                    'id': 'tou_advantage',
                    'severity': 'positive',
                    'title_zh': '時間電價優於單一費率',
                    'title_en': 'Time-of-Use outperforms Flat Rate',
                    'body_zh': f"依目前用電時段分布，採用二段式時間電價較單一費率（{tariff.flat_rate:.2f} {cur}/kWh）節省約 {tou_diff:.2f} {cur}（-{pct:.1f}%）。",
                    'body_en': f"Based on your load schedule, Time-of-Use saves ~{tou_diff:.2f} {cur} (-{pct:.1f}%) versus flat-rate ({tariff.flat_rate:.2f} {cur}/kWh).",
                    'metric': f'-{pct:.1f}%',
                })
            else:
                extra = abs(tou_diff)
                pct = (extra / summary['tou_equivalent_cost'] * 100.0) if summary['tou_equivalent_cost'] > 0 else 0.0
                insights.append({
                    'id': 'peak_exposure',
                    'severity': 'warning',
                    'title_zh': '尖峰時段負載偏高，可透過離峰排程節費',
                    'title_en': 'High peak-hour exposure — off-peak shifting recommended',
                    'body_zh': f"目前尖峰用電佔比 {summary['peak_kwh_share_pct']:.1f}%。若將批次推論或訓練移至離峰時段（24:00–09:00 或週末），可降低約 {summary['shift_50_peak_savings']:.2f} {cur}；改用單一費率亦可少付 {extra:.2f} {cur}。",
                    'body_en': f"{summary['peak_kwh_share_pct']:.1f}% of energy ran during peak hours. Shifting 50% of peak load to off-peak saves ~{summary['shift_50_peak_savings']:.2f} {cur}.",
                    'metric': f"{summary['peak_kwh_share_pct']:.0f}% peak",
                })

        # 2. Idle power waste insight
        total_idle_cost = sum(g['idle_cost'] for g in gpu_breakdown)
        total_gpu_cost = sum(g['cost'] for g in gpu_breakdown)
        if total_gpu_cost > 0:
            idle_share = (total_idle_cost / total_gpu_cost) * 100.0
            if idle_share >= 35.0:
                insights.append({
                    'id': 'idle_waste',
                    'severity': 'warning',
                    'title_zh': 'GPU 待機耗電佔比顯著',
                    'title_en': 'Significant GPU idle power overhead',
                    'body_zh': f"在分析區間內，GPU 利用率低於 5% 的待機功耗佔 GPU 電費約 {idle_share:.1f}%（{total_idle_cost:.2f} {cur}）。建議卸載閒置大型模型或調整持久模式省電狀態。",
                    'body_en': f"Idle time (<5% util) accounts for {idle_share:.1f}% ({total_idle_cost:.2f} {cur}) of GPU electricity cost. Consider unloading idle models.",
                    'metric': f'{idle_share:.1f}% idle',
                })

        # 3. Load factor / stability insight
        lf = summary['stats']['load_factor']
        if lf > 0:
            if lf >= 0.72:
                insights.append({
                    'id': 'load_factor_high',
                    'severity': 'info',
                    'title_zh': '負載因數高（穩定高輸出運轉）',
                    'title_en': 'High load factor (sustained compute)',
                    'body_zh': f"平均功率 {summary['stats']['mean_w']:.0f} W、P95 達 {summary['stats']['p95_w']:.0f} W，負載因數 {lf * 100:.1f}%，表示顯示卡持續高負載運作，需留意散熱與預算。",
                    'body_en': f"Mean {summary['stats']['mean_w']:.0f} W with P95 at {summary['stats']['p95_w']:.0f} W (load factor {lf * 100:.1f}%), indicating sustained heavy compute.",
                    'metric': f'LF {lf * 100:.0f}%',
                })
            elif lf <= 0.35 and summary['stats']['max_w'] >= 150:
                insights.append({
                    'id': 'load_factor_bursty',
                    'severity': 'info',
                    'title_zh': '突發型推論負載特徵',
                    'title_en': 'Bursty inference load profile',
                    'body_zh': f"峰值功率 {summary['stats']['max_w']:.0f} W 但平均僅 {summary['stats']['mean_w']:.0f} W（負載因數 {lf * 100:.1f}%），屬於典型互動式/突發型 AI 推論負載。",
                    'body_en': f"Peak {summary['stats']['max_w']:.0f} W vs mean {summary['stats']['mean_w']:.0f} W (load factor {lf * 100:.1f}%), typical of interactive bursty inference.",
                    'metric': f'LF {lf * 100:.0f}%',
                })

        # 4. Budget projection insight
        budget = tariff.monthly_budget
        proj_cost = summary['forecast']['projected_30d_cost']
        if budget and budget > 0 and proj_cost > 0:
            pct = (proj_cost / budget) * 100.0
            if pct >= 100.0:
                insights.append({
                    'id': 'budget_exceed',
                    'severity': 'warning',
                    'title_zh': '預估 30 天電費將超出月預算',
                    'title_en': '30-day projection exceeds monthly budget',
                    'body_zh': f"依目前區間日均耗電推算，30 天預估電費約 {proj_cost:.0f} {cur}，為設定預算 {budget:.0f} {cur} 的 {pct:.0f}%。",
                    'body_en': f"At the observed daily burn rate, projected 30-day cost ({proj_cost:.0f} {cur}) reaches {pct:.0f}% of your {budget:.0f} {cur} budget.",
                    'metric': f'{pct:.0f}%',
                })
            elif pct <= 60.0:
                insights.append({
                    'id': 'budget_healthy',
                    'severity': 'positive',
                    'title_zh': '月電費預測位於預算安全區間',
                    'title_en': 'Projected spend is well within budget',
                    'body_zh': f"預估 30 天電費約 {proj_cost:.0f} {cur}，僅佔月預算 {budget:.0f} {cur} 的 {pct:.1f}%，尚有充裕算力額度。",
                    'body_en': f"Projected 30-day spend ({proj_cost:.0f} {cur}) is {pct:.1f}% of your {budget:.0f} {cur} budget.",
                    'metric': f'{pct:.0f}%',
                })
    else:
        insights.append({
            'id': 'collecting_samples',
            'severity': 'info',
            'title_zh': '正在累積即時電力遙測樣本',
            'title_en': 'Collecting live power telemetry samples',
            'body_zh': '主機 Agent 每 4 秒自動回報各 GPU 瓦數並寫入資料庫；您也可立即在下方「電費紀錄簿」手動新增歷史帳單或電表紀錄。',
            'body_en': 'Host agents log per-GPU wattage every 4 seconds automatically. You can also add utility bills or meter readings in the Bill Ledger below.',
            'metric': f"{live['total_power_w']:.0f} W",
        })

    if bill_analytics['count'] >= 2 and bill_analytics['mom_cost_change_pct'] is not None:
        chg = bill_analytics['mom_cost_change_pct']
        direction_zh = '上升' if chg > 0 else '下降'
        direction_en = 'increased' if chg > 0 else 'decreased'
        insights.append({
            'id': 'bill_mom',
            'severity': 'warning' if chg > 15 else ('positive' if chg < -5 else 'info'),
            'title_zh': f'最新紀錄電費較前期{direction_zh} {abs(chg):.1f}%',
            'title_en': f'Latest recorded bill {direction_en} {abs(chg):.1f}% vs prior',
            'body_zh': f"已紀錄 {bill_analytics['count']} 筆帳單/電表資料，累計 {bill_analytics['total_kwh']:.1f} kWh（平均單價 {bill_analytics['avg_unit_rate']:.2f} {cur}/kWh）。",
            'body_en': f"{bill_analytics['count']} recorded bills totaling {bill_analytics['total_kwh']:.1f} kWh (avg {bill_analytics['avg_unit_rate']:.2f} {cur}/kWh).",
            'metric': f'{chg:+.1f}%',
        })

    return insights[:5]


def compute_energy_analytics(
    db,
    *,
    range_key: str = '24h',
    node_id: str | None = None,
    include_simulated: bool = False,
) -> dict[str, Any]:
    if range_key not in RANGE_SPECS:
        range_key = '24h'
    tariff = get_or_create_tariff(db)
    lookback, bucket_s = RANGE_SPECS[range_key]
    current_utc = now()
    start_utc = (current_utc - lookback) if lookback is not None else None

    stmt = select(PowerSample).order_by(PowerSample.recorded_at.asc(), PowerSample.id.asc())
    if start_utc is not None:
        stmt = stmt.where(PowerSample.recorded_at >= start_utc)
    if node_id:
        stmt = stmt.where(PowerSample.node_id == node_id)
    if not include_simulated:
        stmt = stmt.where(PowerSample.simulated.is_(False))

    # Count first so huge multi-month datasets stay fast (downsample if > 36,000 raw rows)
    count_stmt = select(func.count()).select_from(PowerSample)
    if start_utc is not None:
        count_stmt = count_stmt.where(PowerSample.recorded_at >= start_utc)
    if node_id:
        count_stmt = count_stmt.where(PowerSample.node_id == node_id)
    if not include_simulated:
        count_stmt = count_stmt.where(PowerSample.simulated.is_(False))
    total_rows = int(db.scalar(count_stmt) or 0)

    stride = max(1, total_rows // 30000) if total_rows > 30000 else 1
    if stride > 1:
        stmt = stmt.where((PowerSample.id % stride) == 0)
    samples = db.scalars(stmt).all()

    if range_key == 'all' and samples:
        span_s = max(1.0, (samples[-1].recorded_at - samples[0].recorded_at).total_seconds())
        if span_s <= 3600:
            bucket_s = 120
        elif span_s <= 21600:
            bucket_s = 600
        elif span_s <= 86400:
            bucket_s = 1800
        elif span_s <= 7 * 86400:
            bucket_s = 10800
        else:
            bucket_s = 86400

    pue = float(tariff.pue_factor)
    base_w = float(tariff.base_host_watts)
    co2_factor = float(tariff.co2_kg_per_kwh)

    total_gpu_kwh = 0.0
    total_host_kwh = 0.0
    total_psu_kwh = 0.0
    total_kwh = 0.0
    total_cost = 0.0
    peak_kwh = 0.0
    off_peak_kwh = 0.0
    peak_cost = 0.0
    off_peak_cost = 0.0
    flat_equiv_cost = 0.0
    tou_equiv_cost = 0.0
    shift_50_peak_savings = 0.0
    total_observed_s = 0.0
    has_simulated = False

    power_values: list[float] = []
    util_values: list[float] = []
    temp_values: list[float] = []

    buckets: dict[int, dict[str, Any]] = {}
    heatmap_acc: list[list[dict[str, float]]] = [
        [{'w_sum': 0.0, 'kwh': 0.0, 'cost': 0.0, 'n': 0.0} for _ in range(24)]
        for _ in range(7)
    ]
    gpu_acc: dict[tuple[str, int], dict[str, Any]] = {}
    node_acc: dict[str, dict[str, Any]] = {}
    model_acc: dict[str, dict[str, Any]] = {}
    telemetry_by_date: dict[str, dict[str, float]] = defaultdict(lambda: {'kwh': 0.0, 'cost': 0.0})

    for s in samples:
        if s.simulated:
            has_simulated = True
        ts = s.recorded_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        local_dt = _local_datetime(ts, tariff.timezone_offset_hours)
        dt_s = float(s.interval_s) * stride
        hours = dt_s / 3600.0

        gpu_w = max(0.0, float(s.gpu_power_w))
        host_w = base_w
        subtotal_w = gpu_w + host_w
        total_w = subtotal_w * pue
        psu_w = max(0.0, total_w - subtotal_w)

        s_gpu_kwh = (gpu_w * hours) / 1000.0
        s_host_kwh = (host_w * hours) / 1000.0
        s_psu_kwh = (psu_w * hours) / 1000.0
        s_total_kwh = (total_w * hours) / 1000.0

        rate, tier, is_summer = rate_at_timestamp(ts, tariff)
        s_cost = s_total_kwh * rate
        s_flat_cost = s_total_kwh * float(tariff.flat_rate)
        tou_rate, is_tou_peak = tou_rate_at_timestamp(ts, tariff)
        s_tou_cost = s_total_kwh * tou_rate

        total_gpu_kwh += s_gpu_kwh
        total_host_kwh += s_host_kwh
        total_psu_kwh += s_psu_kwh
        total_kwh += s_total_kwh
        total_cost += s_cost
        flat_equiv_cost += s_flat_cost
        tou_equiv_cost += s_tou_cost
        total_observed_s += dt_s

        if tier == 'peak' or (tier == 'flat' and is_tou_peak):
            peak_kwh += s_total_kwh
            peak_cost += s_cost
            off_rate = float(tariff.off_peak_rate if is_summer else tariff.non_summer_off_peak_rate)
            if rate > off_rate:
                shift_50_peak_savings += (s_total_kwh * 0.5) * (rate - off_rate)
        else:
            off_peak_kwh += s_total_kwh
            off_peak_cost += s_cost

        power_values.append(total_w)
        if s.avg_utilization is not None:
            util_values.append(float(s.avg_utilization))
        if s.avg_temperature_c is not None:
            temp_values.append(float(s.avg_temperature_c))

        # Daily tally for bill reconciliation
        day_key = local_dt.strftime('%Y-%m-%d')
        telemetry_by_date[day_key]['kwh'] += s_total_kwh
        telemetry_by_date[day_key]['cost'] += s_cost

        # Time-series bucket
        epoch_s = int(ts.timestamp())
        b_key = (epoch_s // bucket_s) * bucket_s
        b = buckets.get(b_key)
        if b is None:
            b_utc = datetime.fromtimestamp(b_key, tz=timezone.utc)
            b_local = _local_datetime(b_utc, tariff.timezone_offset_hours)
            b = {
                'ts': b_utc.isoformat(),
                'label': _bucket_label(b_local, bucket_s),
                'total_w_sum': 0.0,
                'gpu_w_sum': 0.0,
                'max_total_w': total_w,
                'min_total_w': total_w,
                'kwh': 0.0,
                'cost': 0.0,
                'peak_kwh': 0.0,
                'off_peak_kwh': 0.0,
                'util_sum': 0.0,
                'util_n': 0,
                'temp_sum': 0.0,
                'temp_n': 0,
                'samples': 0,
                'tier': tier,
            }
            buckets[b_key] = b
        b['total_w_sum'] += total_w
        b['gpu_w_sum'] += gpu_w
        if total_w > b['max_total_w']:
            b['max_total_w'] = total_w
        if total_w < b['min_total_w']:
            b['min_total_w'] = total_w
        b['kwh'] += s_total_kwh
        b['cost'] += s_cost
        if tier == 'peak':
            b['peak_kwh'] += s_total_kwh
        else:
            b['off_peak_kwh'] += s_total_kwh
        if s.avg_utilization is not None:
            b['util_sum'] += float(s.avg_utilization)
            b['util_n'] += 1
        if s.avg_temperature_c is not None:
            b['temp_sum'] += float(s.avg_temperature_c)
            b['temp_n'] += 1
        b['samples'] += 1

        # 7x24 Heatmap cell (Mon=0..Sun=6)
        cell = heatmap_acc[local_dt.weekday()][local_dt.hour]
        cell['w_sum'] += total_w
        cell['kwh'] += s_total_kwh
        cell['cost'] += s_cost
        cell['n'] += 1.0

        # Node breakdown
        na = node_acc.get(s.node_id)
        if na is None:
            na = {
                'node_id': s.node_id,
                'node_name': s.node_name,
                'kwh': 0.0,
                'gpu_kwh': 0.0,
                'cost': 0.0,
                'w_sum': 0.0,
                'max_w': total_w,
                'samples': 0,
                'hours': 0.0,
                'simulated': s.simulated,
            }
            node_acc[s.node_id] = na
        na['kwh'] += s_total_kwh
        na['gpu_kwh'] += s_gpu_kwh
        na['cost'] += s_cost
        na['w_sum'] += total_w
        if total_w > na['max_w']:
            na['max_w'] = total_w
        na['samples'] += 1
        na['hours'] += hours

        # Per-GPU breakdown
        for g in (s.per_gpu or []):
            if not isinstance(g, dict):
                continue
            g_idx = int(g.get('index', 0))
            g_pw = _finite_number(g.get('power_w'))
            if g_pw is None or g_pw < 0:
                continue
            g_util = _finite_number(g.get('utilization'))
            g_temp = _finite_number(g.get('temperature_c'))
            g_key = (s.node_id, g_idx)
            ga = gpu_acc.get(g_key)
            if ga is None:
                ga = {
                    'node_id': s.node_id,
                    'node_name': s.node_name,
                    'gpu_index': g_idx,
                    'gpu_name': str(g.get('name') or f'GPU {g_idx}'),
                    'kwh': 0.0,
                    'cost': 0.0,
                    'w_sum': 0.0,
                    'max_w': g_pw,
                    'util_sum': 0.0,
                    'util_n': 0,
                    'temp_sum': 0.0,
                    'temp_n': 0,
                    'idle_samples': 0,
                    'idle_kwh': 0.0,
                    'idle_cost': 0.0,
                    'samples': 0,
                    'hours': 0.0,
                }
                gpu_acc[g_key] = ga
            g_kwh = (g_pw * pue * hours) / 1000.0
            g_cost = g_kwh * rate
            ga['kwh'] += g_kwh
            ga['cost'] += g_cost
            ga['w_sum'] += g_pw
            if g_pw > ga['max_w']:
                ga['max_w'] = g_pw
            if g_util is not None:
                ga['util_sum'] += g_util
                ga['util_n'] += 1
                if g_util < IDLE_UTIL_THRESHOLD:
                    ga['idle_samples'] += 1
                    ga['idle_kwh'] += g_kwh
                    ga['idle_cost'] += g_cost
            if g_temp is not None:
                ga['temp_sum'] += g_temp
                ga['temp_n'] += 1
            ga['samples'] += 1
            ga['hours'] += hours

        # Workload / Model attribution
        m_key = s.active_model or '__idle__'
        ma = model_acc.get(m_key)
        if ma is None:
            ma = {
                'model': m_key,
                'kwh': 0.0,
                'cost': 0.0,
                'w_sum': 0.0,
                'samples': 0,
                'hours': 0.0,
                'job_ids': set(),
            }
            model_acc[m_key] = ma
        ma['kwh'] += s_total_kwh
        ma['cost'] += s_cost
        ma['w_sum'] += total_w
        ma['samples'] += 1
        ma['hours'] += hours
        if s.active_job_id:
            ma['job_ids'].add(s.active_job_id)

    # Enrich model attribution with completed job token counts
    all_job_ids = {jid for m in model_acc.values() for jid in m['job_ids']}
    job_tokens: dict[str, int] = {}
    if all_job_ids:
        for j in db.scalars(select(Job).where(Job.id.in_(all_job_ids))).all():
            toks = None
            if isinstance(j.result, dict):
                toks = j.result.get('tokens_generated')
            if isinstance(toks, (int, float)) and toks > 0:
                job_tokens[j.id] = int(toks)

    # Statistical distribution of total power
    sorted_pw = sorted(power_values)
    n_pw = len(sorted_pw)
    mean_w = (sum(sorted_pw) / n_pw) if n_pw else 0.0
    median_w = _percentile(sorted_pw, 50.0)
    p95_w = _percentile(sorted_pw, 95.0)
    max_w = sorted_pw[-1] if n_pw else 0.0
    min_w = sorted_pw[0] if n_pw else 0.0
    variance = (sum((x - mean_w) ** 2 for x in sorted_pw) / n_pw) if n_pw else 0.0
    std_w = math.sqrt(variance)
    load_factor = (mean_w / max_w) if max_w > 0 else 0.0

    # Forecast (30-day projection from observed burn rate, or fallback to live burn rate)
    live = _compute_live_power(db, tariff, node_id, include_simulated)
    observed_hours = total_observed_s / 3600.0
    if observed_hours >= 0.005:  # >= 18 seconds of telemetry
        daily_avg_kwh = (total_kwh / observed_hours) * 24.0
        daily_avg_cost = (total_cost / observed_hours) * 24.0
    else:
        daily_avg_kwh = (live['total_power_w'] / 1000.0) * 24.0
        daily_avg_cost = live['cost_per_day']
    projected_30d_kwh = daily_avg_kwh * 30.0
    projected_30d_cost = daily_avg_cost * 30.0
    projected_30d_co2 = projected_30d_kwh * co2_factor

    budget = float(tariff.monthly_budget) if tariff.monthly_budget and tariff.monthly_budget > 0 else None
    budget_util_pct = round((projected_30d_cost / budget) * 100.0, 1) if budget else None
    days_to_budget = round(budget / daily_avg_cost, 1) if (budget and daily_avg_cost > 0) else None

    effective_rate = (total_cost / total_kwh) if total_kwh > 0 else live['current_rate']
    peak_kwh_share_pct = (peak_kwh / total_kwh * 100.0) if total_kwh > 0 else 0.0

    # Format timeseries
    timeseries = []
    for k in sorted(buckets.keys()):
        b = buckets[k]
        cnt = max(1, b['samples'])
        tier = b['tier']
        if tariff.tariff_mode != 'flat':
            tier = 'peak' if b['peak_kwh'] >= b['off_peak_kwh'] else 'off_peak'
        timeseries.append({
            'ts': b['ts'],
            'label': b['label'],
            'avg_total_w': round(b['total_w_sum'] / cnt, 1),
            'avg_gpu_w': round(b['gpu_w_sum'] / cnt, 1),
            'max_total_w': round(b['max_total_w'], 1),
            'min_total_w': round(b['min_total_w'], 1),
            'kwh': round(b['kwh'], 4),
            'cost': round(b['cost'], 4),
            'tariff_tier': tier,
            'avg_utilization': round(b['util_sum'] / b['util_n'], 1) if b['util_n'] else None,
            'avg_temperature_c': round(b['temp_sum'] / b['temp_n'], 1) if b['temp_n'] else None,
            'samples': b['samples'],
        })

    # Format 7x24 heatmap
    ref_monday = datetime(2026, 1, 5, 0, 0, tzinfo=timezone(timedelta(hours=tariff.timezone_offset_hours)))
    heatmap_max_w = 0.0
    heatmap_days = []
    for d_idx in range(7):
        hours_out = []
        for h_idx in range(24):
            c = heatmap_acc[d_idx][h_idx]
            avg_hw = (c['w_sum'] / c['n']) if c['n'] > 0 else 0.0
            if avg_hw > heatmap_max_w:
                heatmap_max_w = avg_hw
            sample_local = ref_monday + timedelta(days=d_idx, hours=h_idx)
            hours_out.append({
                'hour': h_idx,
                'avg_w': round(avg_hw, 1),
                'kwh': round(c['kwh'], 4),
                'cost': round(c['cost'], 4),
                'samples': int(c['n']),
                'is_peak': False if tariff.tariff_mode == 'flat' else is_peak_hour(sample_local, tariff),
            })
        heatmap_days.append({'day': d_idx, 'hours': hours_out})

    # Format GPU breakdown
    gpu_total_kwh = sum(g['kwh'] for g in gpu_acc.values())
    gpu_breakdown = []
    for g in sorted(gpu_acc.values(), key=lambda x: x['cost'], reverse=True):
        cnt = max(1, g['samples'])
        avg_w = g['w_sum'] / cnt
        avg_u = (g['util_sum'] / g['util_n']) if g['util_n'] else None
        avg_t = (g['temp_sum'] / g['temp_n']) if g['temp_n'] else None
        idle_pct = (g['idle_samples'] / g['util_n'] * 100.0) if g['util_n'] else 0.0
        eff = (avg_w / (avg_u / 10.0)) if (avg_u is not None and avg_u >= 5.0) else None
        gpu_breakdown.append({
            'node_id': g['node_id'],
            'node_name': g['node_name'],
            'gpu_index': g['gpu_index'],
            'gpu_name': g['gpu_name'],
            'kwh': round(g['kwh'], 4),
            'cost': round(g['cost'], 3),
            'share_pct': round((g['kwh'] / gpu_total_kwh * 100.0), 1) if gpu_total_kwh > 0 else 0.0,
            'avg_w': round(avg_w, 1),
            'max_w': round(g['max_w'], 1),
            'avg_utilization': round(avg_u, 1) if avg_u is not None else None,
            'avg_temperature_c': round(avg_t, 1) if avg_t is not None else None,
            'idle_pct': round(idle_pct, 1),
            'idle_kwh': round(g['idle_kwh'], 4),
            'idle_cost': round(g['idle_cost'], 3),
            'w_per_10pct_util': round(eff, 1) if eff is not None else None,
            'hours': round(g['hours'], 2),
        })

    # Format Node breakdown
    node_breakdown = []
    for na in sorted(node_acc.values(), key=lambda x: x['cost'], reverse=True):
        node_breakdown.append({
            'node_id': na['node_id'],
            'node_name': na['node_name'],
            'kwh': round(na['kwh'], 4),
            'gpu_kwh': round(na['gpu_kwh'], 4),
            'cost': round(na['cost'], 3),
            'share_pct': round((na['kwh'] / total_kwh * 100.0), 1) if total_kwh > 0 else 0.0,
            'avg_w': round(na['w_sum'] / max(1, na['samples']), 1),
            'max_w': round(na['max_w'], 1),
            'hours': round(na['hours'], 2),
            'simulated': na['simulated'],
        })

    # Format Model breakdown
    model_breakdown = []
    for ma in sorted(model_acc.values(), key=lambda x: x['cost'], reverse=True):
        j_count = len(ma['job_ids'])
        toks = sum(job_tokens.get(jid, 0) for jid in ma['job_ids'])
        model_breakdown.append({
            'model': ma['model'],
            'is_idle': ma['model'] == '__idle__',
            'kwh': round(ma['kwh'], 4),
            'cost': round(ma['cost'], 3),
            'share_pct': round((ma['kwh'] / total_kwh * 100.0), 1) if total_kwh > 0 else 0.0,
            'avg_w': round(ma['w_sum'] / max(1, ma['samples']), 1),
            'hours': round(ma['hours'], 2),
            'job_count': j_count,
            'cost_per_job': round(ma['cost'] / j_count, 4) if j_count > 0 else None,
            'total_tokens': toks,
            'cost_per_1k_tokens': round((ma['cost'] / toks) * 1000.0, 5) if toks > 0 else None,
        })

    summary = {
        'sample_count': total_rows,
        'observed_hours': round(observed_hours, 3),
        'has_simulated': has_simulated,
        'total_kwh': round(total_kwh, 4),
        'gpu_kwh': round(total_gpu_kwh, 4),
        'host_overhead_kwh': round(total_host_kwh, 4),
        'psu_loss_kwh': round(total_psu_kwh, 4),
        'total_cost': round(total_cost, 3),
        'peak_kwh': round(peak_kwh, 4),
        'off_peak_kwh': round(off_peak_kwh, 4),
        'peak_kwh_share_pct': round(peak_kwh_share_pct, 1),
        'peak_cost': round(peak_cost, 3),
        'off_peak_cost': round(off_peak_cost, 3),
        'effective_rate': round(effective_rate, 4),
        'flat_equivalent_cost': round(flat_equiv_cost, 3),
        'tou_equivalent_cost': round(tou_equiv_cost, 3),
        'shift_50_peak_savings': round(shift_50_peak_savings, 3),
        'co2_kg': round(total_kwh * co2_factor, 4),
        'stats': {
            'mean_w': round(mean_w, 1),
            'median_w': round(median_w, 1),
            'p95_w': round(p95_w, 1),
            'max_w': round(max_w, 1),
            'min_w': round(min_w, 1),
            'std_w': round(std_w, 1),
            'load_factor': round(load_factor, 3),
            'avg_utilization': round(sum(util_values) / len(util_values), 1) if util_values else None,
            'avg_temperature_c': round(sum(temp_values) / len(temp_values), 1) if temp_values else None,
        },
        'forecast': {
            'daily_avg_kwh': round(daily_avg_kwh, 3),
            'daily_avg_cost': round(daily_avg_cost, 2),
            'projected_30d_kwh': round(projected_30d_kwh, 2),
            'projected_30d_cost': round(projected_30d_cost, 2),
            'projected_30d_co2_kg': round(projected_30d_co2, 2),
            'monthly_budget': budget,
            'budget_utilization_pct': budget_util_pct,
            'projected_days_to_budget': days_to_budget,
        },
    }

    # For bill reconciliation, also include telemetry from all time if range_key wasn't 'all'
    if range_key != 'all':
        bill_count = int(db.scalar(select(func.count()).select_from(ElectricityBill)) or 0)
        if bill_count > 0:
            all_by_date: dict[str, dict[str, float]] = defaultdict(lambda: {'kwh': 0.0, 'cost': 0.0})
            recon_q = select(PowerSample.recorded_at, PowerSample.interval_s, PowerSample.gpu_power_w)
            if not include_simulated:
                recon_q = recon_q.where(PowerSample.simulated.is_(False))
            if node_id:
                recon_q = recon_q.where(PowerSample.node_id == node_id)
            for rec_at, iv_s, g_pw in db.execute(recon_q):
                ts = rec_at if rec_at.tzinfo else rec_at.replace(tzinfo=timezone.utc)
                local_d = _local_datetime(ts, tariff.timezone_offset_hours).strftime('%Y-%m-%d')
                s_kwh = ((max(0.0, float(g_pw)) + base_w) * pue * (float(iv_s) / 3600.0)) / 1000.0
                r_unit, _, _ = rate_at_timestamp(ts, tariff)
                all_by_date[local_d]['kwh'] += s_kwh
                all_by_date[local_d]['cost'] += s_kwh * r_unit
            telemetry_by_date = all_by_date

    bill_analytics = _compute_bill_analytics(db, tariff, node_id=node_id, telemetry_by_date=telemetry_by_date)
    insights = _build_insights(summary, live, tariff, gpu_breakdown, bill_analytics)

    # Available recorded node options (union of registered nodes + historical samples)
    seen_nodes: dict[str, str] = {}
    for n in db.scalars(select(Node).order_by(Node.name)).all():
        if n.mode == 'demo' and not include_simulated and os.getenv('ENABLE_DEMO', 'true').lower() != 'true':
            continue
        seen_nodes[n.id] = n.name
    for nid, nname in db.execute(select(PowerSample.node_id, PowerSample.node_name).distinct()).all():
        seen_nodes.setdefault(nid, nname)

    return {
        'range': range_key,
        'bucket_seconds': bucket_s,
        'generated_at': current_utc.isoformat(),
        'tariff': _encode(tariff),
        'live': live,
        'summary': summary,
        'timeseries': timeseries,
        'heatmap': {'max_w': round(heatmap_max_w, 1), 'days': heatmap_days},
        'gpu_breakdown': gpu_breakdown,
        'node_breakdown': node_breakdown,
        'model_breakdown': model_breakdown,
        'bill_analytics': bill_analytics,
        'insights': insights,
        'available_nodes': [{'id': k, 'name': v} for k, v in sorted(seen_nodes.items(), key=lambda kv: kv[1])],
    }


def export_energy_csv(
    db,
    *,
    target: str = 'timeseries',
    range_key: str = '24h',
    node_id: str | None = None,
    include_simulated: bool = False,
) -> tuple[str, str]:
    """Build a UTF-8 BOM CSV export and suggested filename."""
    data = compute_energy_analytics(db, range_key=range_key, node_id=node_id, include_simulated=include_simulated)
    cur = data['tariff']['currency']
    buf = io.StringIO()
    buf.write('\ufeff')  # UTF-8 BOM for clean Excel CJK display
    writer = csv.writer(buf)

    if target == 'bills':
        writer.writerow([
            'ID', 'Billing Date', 'Period Start', 'Period End', 'Label', 'Category',
            'Node ID', 'kWh', f'Cost ({cur})', f'Unit Rate ({cur}/kWh)',
            'Peak kWh', 'Off-Peak kWh', 'Telemetry kWh', 'GPU Share (%)', 'Notes',
        ])
        for b in data['bill_analytics']['bills']:
            writer.writerow([
                b['id'], b['billing_date'], b['period_start'], b['period_end'],
                b['label'], b['category'], b['node_id'] or '',
                b['kwh'], b['cost'], b['unit_rate'],
                b['peak_kwh'] if b['peak_kwh'] is not None else '',
                b['off_peak_kwh'] if b['off_peak_kwh'] is not None else '',
                b['telemetry_kwh'],
                b['gpu_share_pct'] if b['gpu_share_pct'] is not None else '',
                b['notes'] or '',
            ])
        return buf.getvalue(), f'gpu-electricity-bills-{range_key}.csv'

    if target == 'gpu':
        writer.writerow([
            'Node', 'GPU Index', 'GPU Name', 'Avg Power (W)', 'Peak Power (W)',
            'Energy (kWh)', f'Cost ({cur})', 'Share (%)', 'Avg Util (%)',
            'Avg Temp (C)', 'Idle Time (%)', 'Idle kWh', f'Idle Cost ({cur})', 'Hours',
        ])
        for g in data['gpu_breakdown']:
            writer.writerow([
                g['node_name'], g['gpu_index'], g['gpu_name'], g['avg_w'], g['max_w'],
                g['kwh'], g['cost'], g['share_pct'],
                g['avg_utilization'] if g['avg_utilization'] is not None else '',
                g['avg_temperature_c'] if g['avg_temperature_c'] is not None else '',
                g['idle_pct'], g['idle_kwh'], g['idle_cost'], g['hours'],
            ])
        return buf.getvalue(), f'gpu-power-breakdown-{range_key}.csv'

    # Default: timeseries
    writer.writerow([
        'Timestamp (UTC)', 'Local Label', 'Tariff Tier',
        'Avg Total Power (W)', 'Avg GPU Power (W)', 'Peak Power (W)', 'Min Power (W)',
        'Energy (kWh)', f'Estimated Cost ({cur})', 'Avg Util (%)', 'Avg Temp (C)', 'Samples',
    ])
    for pt in data['timeseries']:
        writer.writerow([
            pt['ts'], pt['label'], pt['tariff_tier'],
            pt['avg_total_w'], pt['avg_gpu_w'], pt['max_total_w'], pt['min_total_w'],
            pt['kwh'], pt['cost'],
            pt['avg_utilization'] if pt['avg_utilization'] is not None else '',
            pt['avg_temperature_c'] if pt['avg_temperature_c'] is not None else '',
            pt['samples'],
        ])
    return buf.getvalue(), f'gpu-energy-timeseries-{range_key}.csv'
