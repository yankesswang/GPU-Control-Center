"""Hardware telemetry history recording, anomaly detection, diagnostic analytics & CSV export."""
from __future__ import annotations

import csv
import io
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, func, inspect, select
from .energy import RANGE_SPECS, _bucket_label, _finite_number, _local_datetime, _percentile, get_or_create_tariff
from .models import HardwareEvent, HardwareSample, Job, Node, now

MAX_SAMPLE_INTERVAL_S = 30.0
DEFAULT_SAMPLE_INTERVAL_S = 4.0
ALERT_DEBOUNCE_S = 300.0  # 5 minutes per (node_id, gpu_index, event_type)

GPU_TEMP_WARN_C = 76.0
GPU_TEMP_CRIT_C = 83.0
GPU_VRAM_WARN_PCT = 88.0
GPU_VRAM_CRIT_PCT = 95.0
GPU_POWER_WARN_PCT = 95.0
HOST_CPU_WARN_PCT = 92.0
HOST_RAM_WARN_PCT = 90.0


def _encode(obj) -> dict[str, Any]:
    return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}


def _emit_debounced_event(
    db,
    *,
    ts: datetime,
    node_id: str,
    node_name: str,
    gpu_index: int | None,
    severity: str,
    event_type: str,
    metric_name: str | None,
    metric_value: float | None,
    threshold: float | None,
    message: str,
    simulated: bool,
) -> HardwareEvent | None:
    cutoff = ts - timedelta(seconds=ALERT_DEBOUNCE_S)
    q = (
        select(HardwareEvent.id)
        .where(
            HardwareEvent.node_id == node_id,
            HardwareEvent.event_type == event_type,
            HardwareEvent.recorded_at >= cutoff,
        )
        .limit(1)
    )
    if gpu_index is None:
        q = q.where(HardwareEvent.gpu_index.is_(None))
    else:
        q = q.where(HardwareEvent.gpu_index == gpu_index)
    if db.scalar(q) is not None:
        return None
    ev = HardwareEvent(
        recorded_at=ts,
        node_id=node_id,
        node_name=node_name[:100],
        gpu_index=gpu_index,
        severity=severity,
        event_type=event_type,
        metric_name=metric_name,
        metric_value=round(metric_value, 2) if metric_value is not None else None,
        threshold=threshold,
        message=message,
        acknowledged=False,
        simulated=simulated,
    )
    db.add(ev)
    return ev


def record_hardware_sample(
    db,
    *,
    node_id: str,
    node_name: str,
    mode: str,
    metrics: dict[str, Any],
    recorded_at: datetime | None = None,
) -> HardwareSample | None:
    """Persist host + per-GPU telemetry and trigger debounced backend anomaly events."""
    if not isinstance(metrics, dict):
        return None
    gpus_raw = metrics.get('gpus') if isinstance(metrics.get('gpus'), list) else []
    cpu_pct = _finite_number(metrics.get('cpu_percent'))
    ram_pct = _finite_number(metrics.get('ram_percent'))
    if not gpus_raw and cpu_pct is None and ram_pct is None:
        return None

    cpu_temp = _finite_number(metrics.get('cpu_temp_c'))
    load_1m = _finite_number(metrics.get('load_avg_1m'))
    ram_used_gb = _finite_number(metrics.get('ram_used_gb'))
    ram_total_gb = _finite_number(metrics.get('ram_total_gb'))
    disk_pct = _finite_number(metrics.get('disk_percent'))
    if ram_used_gb is None and ram_pct is not None and ram_total_gb is not None:
        ram_used_gb = round(ram_total_gb * (ram_pct / 100.0), 1)

    per_gpu: list[dict[str, Any]] = []
    utils: list[float] = []
    temps: list[float] = []
    powers: list[float] = []
    vram_used_sum = 0.0
    vram_total_sum = 0.0
    has_vram = False

    for pos, g in enumerate(gpus_raw):
        if not isinstance(g, dict):
            continue
        idx = g.get('index')
        gpu_index = idx if isinstance(idx, int) and not isinstance(idx, bool) else pos
        util = _finite_number(g.get('utilization'))
        used_mb = _finite_number(g.get('memory_used_mb'))
        total_mb = _finite_number(g.get('memory_total_mb'))
        temp = _finite_number(g.get('temperature_c'))
        power = _finite_number(g.get('power_w'))
        power_limit = _finite_number(g.get('power_limit_w'))
        fan = _finite_number(g.get('fan_speed_pct'))
        clk_sm = _finite_number(g.get('clock_sm_mhz'))
        clk_mem = _finite_number(g.get('clock_mem_mhz'))
        pstate = g.get('pstate') if isinstance(g.get('pstate'), str) else None

        if util is not None:
            utils.append(util)
        if temp is not None:
            temps.append(temp)
        if power is not None and power >= 0:
            powers.append(power)
        if used_mb is not None and total_mb is not None and total_mb > 0:
            has_vram = True
            vram_used_sum += used_mb
            vram_total_sum += total_mb

        per_gpu.append({
            'index': gpu_index,
            'name': str(g.get('name') or f'GPU {gpu_index}')[:80],
            'utilization': round(util, 1) if util is not None else None,
            'memory_used_mb': round(used_mb, 1) if used_mb is not None else None,
            'memory_total_mb': round(total_mb, 1) if total_mb is not None else None,
            'temperature_c': round(temp, 1) if temp is not None else None,
            'power_w': round(power, 2) if power is not None else None,
            'power_limit_w': round(power_limit, 1) if power_limit is not None else None,
            'fan_speed_pct': round(fan, 1) if fan is not None else None,
            'clock_sm_mhz': round(clk_sm, 0) if clk_sm is not None else None,
            'clock_mem_mhz': round(clk_mem, 0) if clk_mem is not None else None,
            'pstate': pstate[:12] if pstate else None,
        })

    ts = recorded_at or now()
    prev_ts = db.scalar(
        select(HardwareSample.recorded_at)
        .where(HardwareSample.node_id == node_id)
        .order_by(HardwareSample.recorded_at.desc(), HardwareSample.id.desc())
        .limit(1)
    )
    reconnected_after_s = None
    if prev_ts is not None:
        if prev_ts.tzinfo is None:
            prev_ts = prev_ts.replace(tzinfo=timezone.utc)
        delta = (ts - prev_ts).total_seconds()
        if 0.05 <= delta <= MAX_SAMPLE_INTERVAL_S:
            interval_s = round(delta, 3)
        else:
            interval_s = DEFAULT_SAMPLE_INTERVAL_S
            if delta >= 60.0:
                reconnected_after_s = delta
    else:
        interval_s = DEFAULT_SAMPLE_INTERVAL_S

    active_job = db.scalar(
        select(Job)
        .where(Job.node_id == node_id, Job.status.in_(('running', 'cancelling')))
        .order_by(Job.started_at.desc())
        .limit(1)
    )
    runtimes_online = {
        rt: bool(isinstance(metrics.get(rt), dict) and metrics[rt].get('status') == 'online')
        for rt in ('ollama', 'lmstudio', 'comfyui')
    }
    simulated = (mode == 'demo') or bool(metrics.get('simulated'))

    sample = HardwareSample(
        node_id=node_id,
        node_name=node_name[:100],
        recorded_at=ts,
        interval_s=interval_s,
        cpu_percent=round(cpu_pct, 1) if cpu_pct is not None else None,
        cpu_temp_c=round(cpu_temp, 1) if cpu_temp is not None else None,
        load_avg_1m=round(load_1m, 2) if load_1m is not None else None,
        ram_percent=round(ram_pct, 1) if ram_pct is not None else None,
        ram_used_gb=round(ram_used_gb, 1) if ram_used_gb is not None else None,
        ram_total_gb=round(ram_total_gb, 1) if ram_total_gb is not None else None,
        disk_percent=round(disk_pct, 1) if disk_pct is not None else None,
        gpu_count=len(per_gpu),
        avg_gpu_util=round(sum(utils) / len(utils), 2) if utils else None,
        max_gpu_util=round(max(utils), 2) if utils else None,
        total_vram_used_mb=round(vram_used_sum, 1) if has_vram else None,
        total_vram_total_mb=round(vram_total_sum, 1) if has_vram else None,
        avg_gpu_temp_c=round(sum(temps) / len(temps), 2) if temps else None,
        max_gpu_temp_c=round(max(temps), 2) if temps else None,
        total_power_w=round(sum(powers), 2) if powers else None,
        per_gpu=per_gpu,
        runtimes_online=runtimes_online,
        active_job_id=active_job.id if active_job else None,
        active_model=active_job.model if active_job else None,
        simulated=simulated,
    )
    db.add(sample)

    # Automatic backend anomaly & recovery detection
    if reconnected_after_s is not None:
        mins = reconnected_after_s / 60.0
        _emit_debounced_event(
            db, ts=ts, node_id=node_id, node_name=node_name, gpu_index=None,
            severity='info', event_type='node_recovered', metric_name='downtime_minutes',
            metric_value=mins, threshold=1.0,
            message=f'主機 {node_name} 中斷 {mins:.1f} 分鐘後恢復心跳連線。',
            simulated=simulated,
        )

    for g in per_gpu:
        g_idx = g['index']
        g_label = f"GPU {g_idx:02d} ({g['name']})"
        t_val = g.get('temperature_c')
        if t_val is not None:
            if t_val >= GPU_TEMP_CRIT_C:
                _emit_debounced_event(
                    db, ts=ts, node_id=node_id, node_name=node_name, gpu_index=g_idx,
                    severity='critical', event_type='high_temp', metric_name='temperature_c',
                    metric_value=t_val, threshold=GPU_TEMP_CRIT_C,
                    message=f'{g_label} 核心溫度達 {t_val:.0f}°C（超過危急閾值 {GPU_TEMP_CRIT_C:.0f}°C），請檢查散熱與風扇。',
                    simulated=simulated,
                )
            elif t_val >= GPU_TEMP_WARN_C:
                _emit_debounced_event(
                    db, ts=ts, node_id=node_id, node_name=node_name, gpu_index=g_idx,
                    severity='warning', event_type='high_temp', metric_name='temperature_c',
                    metric_value=t_val, threshold=GPU_TEMP_WARN_C,
                    message=f'{g_label} 溫度升高至 {t_val:.0f}°C（警戒線 {GPU_TEMP_WARN_C:.0f}°C）。',
                    simulated=simulated,
                )
        u_mb = g.get('memory_used_mb')
        tot_mb = g.get('memory_total_mb')
        if u_mb is not None and tot_mb and tot_mb > 0:
            v_pct = (u_mb / tot_mb) * 100.0
            if v_pct >= GPU_VRAM_CRIT_PCT:
                _emit_debounced_event(
                    db, ts=ts, node_id=node_id, node_name=node_name, gpu_index=g_idx,
                    severity='critical', event_type='vram_pressure', metric_name='vram_pct',
                    metric_value=v_pct, threshold=GPU_VRAM_CRIT_PCT,
                    message=f'{g_label} 顯存占用率達 {v_pct:.1f}%（{u_mb/1024:.1f}/{tot_mb/1024:.1f} GiB），接近 OOM 上限。',
                    simulated=simulated,
                )
            elif v_pct >= GPU_VRAM_WARN_PCT:
                _emit_debounced_event(
                    db, ts=ts, node_id=node_id, node_name=node_name, gpu_index=g_idx,
                    severity='warning', event_type='vram_pressure', metric_name='vram_pct',
                    metric_value=v_pct, threshold=GPU_VRAM_WARN_PCT,
                    message=f'{g_label} 顯存使用率高於警戒線：{v_pct:.1f}%（{u_mb/1024:.1f}/{tot_mb/1024:.1f} GiB）。',
                    simulated=simulated,
                )
        p_w = g.get('power_w')
        p_lim = g.get('power_limit_w')
        if p_w is not None and p_lim and p_lim > 0:
            p_pct = (p_w / p_lim) * 100.0
            if p_pct >= GPU_POWER_WARN_PCT:
                _emit_debounced_event(
                    db, ts=ts, node_id=node_id, node_name=node_name, gpu_index=g_idx,
                    severity='warning', event_type='high_power', metric_name='power_pct',
                    metric_value=p_pct, threshold=GPU_POWER_WARN_PCT,
                    message=f'{g_label} 功耗接近 TDP 上限：{p_w:.0f}W / {p_lim:.0f}W（{p_pct:.0f}%）。',
                    simulated=simulated,
                )

    if cpu_pct is not None and cpu_pct >= HOST_CPU_WARN_PCT:
        _emit_debounced_event(
            db, ts=ts, node_id=node_id, node_name=node_name, gpu_index=None,
            severity='warning', event_type='cpu_pressure', metric_name='cpu_percent',
            metric_value=cpu_pct, threshold=HOST_CPU_WARN_PCT,
            message=f'主機 {node_name} CPU 使用率偏高：{cpu_pct:.0f}%。',
            simulated=simulated,
        )
    if ram_pct is not None and ram_pct >= HOST_RAM_WARN_PCT:
        _emit_debounced_event(
            db, ts=ts, node_id=node_id, node_name=node_name, gpu_index=None,
            severity='warning', event_type='ram_pressure', metric_name='ram_percent',
            metric_value=ram_pct, threshold=HOST_RAM_WARN_PCT,
            message=f'主機 {node_name} 系統記憶體壓力偏高：{ram_pct:.0f}%。',
            simulated=simulated,
        )

    return sample


def _build_hardware_insights(
    summary: dict[str, Any],
    gpu_profiles: list[dict[str, Any]],
    host_profiles: list[dict[str, Any]],
    unacked_critical: int,
    unacked_warning: int,
) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []

    if summary['sample_count'] == 0:
        insights.append({
            'id': 'hw_collecting',
            'severity': 'info',
            'title_zh': '正在累積硬體歷史遙測資料',
            'title_en': 'Collecting hardware telemetry history',
            'body_zh': '主機 Agent 每 4 秒記錄 GPU 溫度、顯存、利用率、風扇、時脈與 CPU/RAM 狀態；您也可於下方登錄硬體維運紀錄。',
            'body_en': 'Host agents log GPU temp, VRAM, utilization, fan, clocks, and CPU/RAM every 4 seconds. You can also add manual ops logs below.',
            'metric': 'LIVE',
        })
        return insights

    if unacked_critical > 0:
        insights.append({
            'id': 'hw_crit_alerts',
            'severity': 'warning',
            'title_zh': f'尚有 {unacked_critical} 筆危急異常待確認',
            'title_en': f'{unacked_critical} unacknowledged critical alert(s)',
            'body_zh': '偵測到核心高溫或顯存接近 OOM 上限事件，請至下方異常追蹤日誌檢視並確認處置。',
            'body_en': 'Critical thermal or VRAM saturation events were logged. Review and acknowledge them in the audit log below.',
            'metric': f'{unacked_critical} crit',
        })

    # Thermal headroom check
    max_t = summary['thermal']['max_c']
    p95_t = summary['thermal']['p95_c']
    if max_t is not None:
        if max_t >= GPU_TEMP_WARN_C:
            insights.append({
                'id': 'hw_thermal_high',
                'severity': 'warning',
                'title_zh': 'GPU 峰值溫度偏高，建議檢視機殼風道與風扇曲線',
                'title_en': 'Elevated peak GPU temperature detected',
                'body_zh': f"分析區間最高核心溫度達 {max_t:.0f}°C（P95 為 {p95_t:.0f}°C），處於高溫區（≥75°C）的時間佔 {summary['thermal']['hot_pct']:.1f}%。",
                'body_en': f"Peak GPU temp reached {max_t:.0f}°C (P95 {p95_t:.0f}°C), spending {summary['thermal']['hot_pct']:.1f}% of time ≥75°C.",
                'metric': f'{max_t:.0f}°C max',
            })
        else:
            insights.append({
                'id': 'hw_thermal_ok',
                'severity': 'positive',
                'title_zh': '散熱餘裕充裕，溫度曲線穩定',
                'title_en': 'Healthy thermal headroom across GPUs',
                'body_zh': f"區間最高溫僅 {max_t:.0f}°C、均溫 {summary['thermal']['mean_c']:.1f}°C，無熱節流風險，適合長時間批次推論或訓練。",
                'body_en': f"Max temp stayed at {max_t:.0f}°C (mean {summary['thermal']['mean_c']:.1f}°C) with zero thermal throttling risk.",
                'metric': f"{summary['thermal']['mean_c']:.0f}°C avg",
            })

    # Multi-GPU load balance check
    if len(gpu_profiles) >= 2:
        utils = [g['avg_util'] for g in gpu_profiles if g['avg_util'] is not None]
        if len(utils) >= 2 and (max(utils) - min(utils)) >= 35.0:
            busiest = max(gpu_profiles, key=lambda x: x['avg_util'] or 0.0)
            quietest = min(gpu_profiles, key=lambda x: x['avg_util'] or 0.0)
            insights.append({
                'id': 'hw_gpu_imbalance',
                'severity': 'info',
                'title_zh': '多顯示卡負載分布不均',
                'title_en': 'Multi-GPU utilization imbalance observed',
                'body_zh': f"GPU {busiest['gpu_index']:02d} 平均負載 {busiest['avg_util']:.0f}%，而 GPU {quietest['gpu_index']:02d} 僅 {quietest['avg_util']:.0f}%。可至「模型與服務」拖曳模型分散顯卡負載。",
                'body_en': f"GPU {busiest['gpu_index']:02d} averaged {busiest['avg_util']:.0f}% while GPU {quietest['gpu_index']:02d} averaged {quietest['avg_util']:.0f}%. Consider rebalancing pinned models.",
                'metric': f"Δ{(max(utils) - min(utils)):.0f}%",
            })

    # VRAM headroom check
    peak_vram = summary['vram']['max_pct']
    if peak_vram is not None and peak_vram >= GPU_VRAM_WARN_PCT:
        insights.append({
            'id': 'hw_vram_pressure',
            'severity': 'warning',
            'title_zh': '顯存峰值接近滿載',
            'title_en': 'Peak VRAM utilization near capacity',
            'body_zh': f"區間峰值顯存占用達 {peak_vram:.1f}%（P95 為 {summary['vram']['p95_pct']:.1f}%），建議卸載非必要常駐模型以預留 KV Cache 空間。",
            'body_en': f"Peak VRAM reached {peak_vram:.1f}% (P95 {summary['vram']['p95_pct']:.1f}%). Consider unloading unused resident models for KV cache headroom.",
            'metric': f'{peak_vram:.0f}% VRAM',
        })

    # Host memory / CPU bottleneck
    if host_profiles:
        max_ram = max((h['max_ram_pct'] or 0.0) for h in host_profiles)
        if max_ram >= HOST_RAM_WARN_PCT:
            insights.append({
                'id': 'hw_ram_high',
                'severity': 'warning',
                'title_zh': '主機系統記憶體使用率偏高',
                'title_en': 'Host system RAM pressure elevated',
                'body_zh': f'主機 RAM 峰值曾達 {max_ram:.0f}%，進行大型 GGUF/ComfyUI CPU Offload 時可能觸發 Swap 降速。',
                'body_en': f'Host RAM peaked at {max_ram:.0f}%, which may trigger swap overhead during model CPU offloading.',
                'metric': f'{max_ram:.0f}% RAM',
            })

    if unacked_warning == 0 and unacked_critical == 0 and len(insights) < 3:
        insights.append({
            'id': 'hw_clean_sla',
            'severity': 'positive',
            'title_zh': '區間內無未處理硬體異常事件',
            'title_en': 'Zero unacknowledged hardware anomalies',
            'body_zh': f"已累計監控 {summary['observed_hours']:.2f} 小時（{summary['sample_count']:,} 筆遙測），健康評分 {summary['health_score']}/100。",
            'body_en': f"Monitored {summary['observed_hours']:.2f} hours ({summary['sample_count']:,} samples) with health score {summary['health_score']}/100.",
            'metric': f"{summary['health_score']}/100",
        })

    return insights[:5]


def compute_hardware_analytics(
    db,
    *,
    range_key: str = '24h',
    node_id: str | None = None,
    gpu_index: int | None = None,
    include_simulated: bool = False,
) -> dict[str, Any]:
    if range_key not in RANGE_SPECS:
        range_key = '24h'
    tariff = get_or_create_tariff(db)
    tz_offset = float(tariff.timezone_offset_hours)
    lookback, bucket_s = RANGE_SPECS[range_key]
    current_utc = now()
    start_utc = (current_utc - lookback) if lookback is not None else None

    stmt = select(HardwareSample).order_by(HardwareSample.recorded_at.asc(), HardwareSample.id.asc())
    count_stmt = select(func.count()).select_from(HardwareSample)
    if start_utc is not None:
        stmt = stmt.where(HardwareSample.recorded_at >= start_utc)
        count_stmt = count_stmt.where(HardwareSample.recorded_at >= start_utc)
    if node_id:
        stmt = stmt.where(HardwareSample.node_id == node_id)
        count_stmt = count_stmt.where(HardwareSample.node_id == node_id)
    if not include_simulated:
        stmt = stmt.where(HardwareSample.simulated.is_(False))
        count_stmt = count_stmt.where(HardwareSample.simulated.is_(False))

    total_rows = int(db.scalar(count_stmt) or 0)
    stride = max(1, total_rows // 25000) if total_rows > 25000 else 1
    if stride > 1:
        stmt = stmt.where((HardwareSample.id % stride) == 0)
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

    # Live node & GPU snapshot
    online_cutoff = current_utc - timedelta(seconds=20)
    node_stmt = select(Node).order_by(Node.name)
    if node_id:
        node_stmt = node_stmt.where(Node.id == node_id)
    all_nodes = db.scalars(node_stmt).all()
    live_gpu_map: dict[tuple[str, int], dict[str, Any]] = {}
    live_hosts: dict[str, dict[str, Any]] = {}
    for n in all_nodes:
        is_sim = (n.mode == 'demo') or bool((n.metrics or {}).get('simulated'))
        if is_sim and not include_simulated:
            continue
        is_online = n.last_seen >= online_cutoff
        m = n.metrics or {}
        live_hosts[n.id] = {
            'node_id': n.id,
            'node_name': n.name,
            'online': is_online,
            'maintenance': n.maintenance,
            'cpu_percent': _finite_number(m.get('cpu_percent')),
            'cpu_temp_c': _finite_number(m.get('cpu_temp_c')),
            'ram_percent': _finite_number(m.get('ram_percent')),
            'ram_used_gb': _finite_number(m.get('ram_used_gb')),
            'ram_total_gb': _finite_number(m.get('ram_total_gb')),
            'disk_percent': _finite_number(m.get('disk_percent')),
            'load_avg_1m': _finite_number(m.get('load_avg_1m')),
        }
        for pos, g in enumerate(m.get('gpus') or []):
            if not isinstance(g, dict):
                continue
            idx = g.get('index') if isinstance(g.get('index'), int) else pos
            live_gpu_map[(n.id, idx)] = {
                'online': is_online,
                'name': g.get('name') or f'GPU {idx}',
                'utilization': _finite_number(g.get('utilization')),
                'memory_used_mb': _finite_number(g.get('memory_used_mb')),
                'memory_total_mb': _finite_number(g.get('memory_total_mb')),
                'temperature_c': _finite_number(g.get('temperature_c')),
                'power_w': _finite_number(g.get('power_w')),
                'power_limit_w': _finite_number(g.get('power_limit_w')),
                'fan_speed_pct': _finite_number(g.get('fan_speed_pct')),
                'clock_sm_mhz': _finite_number(g.get('clock_sm_mhz')),
                'clock_mem_mhz': _finite_number(g.get('clock_mem_mhz')),
                'pstate': g.get('pstate'),
            }

    # Accumulators
    observed_s = 0.0
    has_simulated = False
    util_vals: list[float] = []
    vram_pct_vals: list[float] = []
    vram_gb_vals: list[float] = []
    temp_vals: list[float] = []
    cpu_vals: list[float] = []
    ram_vals: list[float] = []
    fan_vals: list[float] = []
    clk_vals: list[float] = []
    cool_n = warm_n = hot_n = 0
    idle_u_n = mod_u_n = heavy_u_n = 0
    rt_counts = {'ollama': [0, 0], 'lmstudio': [0, 0], 'comfyui': [0, 0]}

    buckets: dict[int, dict[str, Any]] = {}
    gpu_acc: dict[tuple[str, int], dict[str, Any]] = {}
    host_acc: dict[str, dict[str, Any]] = {}
    # 10 utilization bins (0-10, 10-20, ..., 90-100) for thermal-load engineering curve
    thermal_bins: list[dict[str, Any]] = [
        {'bin_start': b * 10, 'bin_end': (b + 1) * 10, 'temps': [], 'powers': [], 'fans': []}
        for b in range(10)
    ]

    for s in samples:
        if s.simulated:
            has_simulated = True
        ts = s.recorded_at if s.recorded_at.tzinfo else s.recorded_at.replace(tzinfo=timezone.utc)
        dt_s = float(s.interval_s) * stride
        observed_s += dt_s

        # Host-level stats
        if s.cpu_percent is not None:
            cpu_vals.append(float(s.cpu_percent))
        if s.ram_percent is not None:
            ram_vals.append(float(s.ram_percent))
        rt_map = s.runtimes_online or {}
        for rt in ('ollama', 'lmstudio', 'comfyui'):
            if rt in rt_map:
                rt_counts[rt][1] += 1
                if rt_map[rt]:
                    rt_counts[rt][0] += 1

        ha = host_acc.get(s.node_id)
        if ha is None:
            ha = {
                'node_id': s.node_id, 'node_name': s.node_name, 'simulated': s.simulated,
                'cpus': [], 'rams': [], 'cpu_temps': [], 'disks': [], 'loads': [],
                'hours': 0.0, 'samples': 0,
                'rt': {'ollama': [0, 0], 'lmstudio': [0, 0], 'comfyui': [0, 0]},
            }
            host_acc[s.node_id] = ha
        ha['hours'] += dt_s / 3600.0
        ha['samples'] += 1
        if s.cpu_percent is not None:
            ha['cpus'].append(float(s.cpu_percent))
        if s.ram_percent is not None:
            ha['rams'].append(float(s.ram_percent))
        if s.cpu_temp_c is not None:
            ha['cpu_temps'].append(float(s.cpu_temp_c))
        if s.disk_percent is not None:
            ha['disks'].append(float(s.disk_percent))
        if s.load_avg_1m is not None:
            ha['loads'].append(float(s.load_avg_1m))
        for rt in ('ollama', 'lmstudio', 'comfyui'):
            if rt in rt_map:
                ha['rt'][rt][1] += 1
                if rt_map[rt]:
                    ha['rt'][rt][0] += 1

        # Filter GPUs if gpu_index requested
        sample_gpus = [
            g for g in (s.per_gpu or [])
            if isinstance(g, dict) and (gpu_index is None or int(g.get('index', -1)) == gpu_index)
        ]

        # Bucket initialization
        epoch_s = int(ts.timestamp())
        b_key = (epoch_s // bucket_s) * bucket_s
        b = buckets.get(b_key)
        if b is None:
            b_utc = datetime.fromtimestamp(b_key, tz=timezone.utc)
            b_local = _local_datetime(b_utc, tz_offset)
            b = {
                'ts': b_utc.isoformat(),
                'label': _bucket_label(b_local, bucket_s),
                'util_sum': 0.0, 'util_n': 0, 'max_util': None,
                'vram_pct_sum': 0.0, 'vram_pct_n': 0, 'max_vram_pct': None,
                'vram_gb_sum': 0.0, 'vram_gb_n': 0,
                'temp_sum': 0.0, 'temp_n': 0, 'max_temp': None,
                'cpu_sum': 0.0, 'cpu_n': 0,
                'ram_sum': 0.0, 'ram_n': 0,
                'power_sum': 0.0, 'power_n': 0,
                'fan_sum': 0.0, 'fan_n': 0,
                'clk_sum': 0.0, 'clk_n': 0,
                'per_gpu_temp': {},  # key -> [sum, count]
                'per_gpu_util': {},
                'samples': 0,
            }
            buckets[b_key] = b
        b['samples'] += 1
        if s.cpu_percent is not None:
            b['cpu_sum'] += float(s.cpu_percent)
            b['cpu_n'] += 1
        if s.ram_percent is not None:
            b['ram_sum'] += float(s.ram_percent)
            b['ram_n'] += 1

        # Per-GPU aggregation
        s_vram_used = 0.0
        s_vram_tot = 0.0
        s_power = 0.0
        has_s_power = False
        for g in sample_gpus:
            g_idx = int(g.get('index', 0))
            g_key = (s.node_id, g_idx)
            g_label_key = f'{s.node_id}#{g_idx}'
            ga = gpu_acc.get(g_key)
            if ga is None:
                ga = {
                    'node_id': s.node_id, 'node_name': s.node_name,
                    'gpu_index': g_idx, 'gpu_name': str(g.get('name') or f'GPU {g_idx}'),
                    'utils': [], 'temps': [], 'vram_pcts': [], 'vram_used_gbs': [],
                    'vram_total_gb': None, 'powers': [], 'fans': [], 'clocks': [],
                    'cool': 0, 'warm': 0, 'hot': 0,
                    'idle': 0, 'mod': 0, 'heavy': 0,
                    'hours': 0.0, 'samples': 0,
                }
                gpu_acc[g_key] = ga
            ga['hours'] += dt_s / 3600.0
            ga['samples'] += 1

            u = _finite_number(g.get('utilization'))
            t = _finite_number(g.get('temperature_c'))
            vu = _finite_number(g.get('memory_used_mb'))
            vt = _finite_number(g.get('memory_total_mb'))
            pw = _finite_number(g.get('power_w'))
            fn = _finite_number(g.get('fan_speed_pct'))
            ck = _finite_number(g.get('clock_sm_mhz'))

            if u is not None:
                util_vals.append(u)
                ga['utils'].append(u)
                b['util_sum'] += u
                b['util_n'] += 1
                b['max_util'] = u if b['max_util'] is None else max(b['max_util'], u)
                pg_u = b['per_gpu_util'].setdefault(g_label_key, [0.0, 0])
                pg_u[0] += u
                pg_u[1] += 1
                if u < 10.0:
                    idle_u_n += 1
                    ga['idle'] += 1
                elif u < 70.0:
                    mod_u_n += 1
                    ga['mod'] += 1
                else:
                    heavy_u_n += 1
                    ga['heavy'] += 1
            if t is not None:
                temp_vals.append(t)
                ga['temps'].append(t)
                b['temp_sum'] += t
                b['temp_n'] += 1
                b['max_temp'] = t if b['max_temp'] is None else max(b['max_temp'], t)
                pg_t = b['per_gpu_temp'].setdefault(g_label_key, [0.0, 0])
                pg_t[0] += t
                pg_t[1] += 1
                if t < 60.0:
                    cool_n += 1
                    ga['cool'] += 1
                elif t < 75.0:
                    warm_n += 1
                    ga['warm'] += 1
                else:
                    hot_n += 1
                    ga['hot'] += 1
            if vu is not None and vt is not None and vt > 0:
                vp = (vu / vt) * 100.0
                vram_pct_vals.append(vp)
                ga['vram_pcts'].append(vp)
                ga['vram_used_gbs'].append(vu / 1024.0)
                ga['vram_total_gb'] = round(vt / 1024.0, 1)
                s_vram_used += vu
                s_vram_tot += vt
            if pw is not None and pw >= 0:
                ga['powers'].append(pw)
                s_power += pw
                has_s_power = True
            if fn is not None:
                fan_vals.append(fn)
                ga['fans'].append(fn)
                b['fan_sum'] += fn
                b['fan_n'] += 1
            if ck is not None:
                clk_vals.append(ck)
                ga['clocks'].append(ck)
                b['clk_sum'] += ck
                b['clk_n'] += 1

            # Thermal vs Load engineering curve binning
            if u is not None and t is not None:
                bin_idx = min(9, max(0, int(u // 10)))
                thermal_bins[bin_idx]['temps'].append(t)
                if pw is not None and pw >= 0:
                    thermal_bins[bin_idx]['powers'].append(pw)
                if fn is not None:
                    thermal_bins[bin_idx]['fans'].append(fn)

        if s_vram_tot > 0:
            vp_tot = (s_vram_used / s_vram_tot) * 100.0
            vgb = s_vram_used / 1024.0
            vram_gb_vals.append(vgb)
            b['vram_pct_sum'] += vp_tot
            b['vram_pct_n'] += 1
            b['max_vram_pct'] = vp_tot if b['max_vram_pct'] is None else max(b['max_vram_pct'], vp_tot)
            b['vram_gb_sum'] += vgb
            b['vram_gb_n'] += 1
        if has_s_power:
            b['power_sum'] += s_power
            b['power_n'] += 1

    # Query recent hardware events
    ev_stmt = select(HardwareEvent).order_by(desc(HardwareEvent.recorded_at)).limit(120)
    if node_id:
        ev_stmt = ev_stmt.where(HardwareEvent.node_id == node_id)
    if gpu_index is not None:
        ev_stmt = ev_stmt.where((HardwareEvent.gpu_index == gpu_index) | (HardwareEvent.gpu_index.is_(None)))
    if not include_simulated:
        ev_stmt = ev_stmt.where(HardwareEvent.simulated.is_(False))
    events_rows = db.scalars(ev_stmt).all()
    events_out = [_encode(e) for e in events_rows]
    unacked_crit = sum(1 for e in events_rows if not e.acknowledged and e.severity == 'critical')
    unacked_warn = sum(1 for e in events_rows if not e.acknowledged and e.severity == 'warning')

    # Helper for summary stats
    def _stats(arr: list[float]) -> dict[str, float | None]:
        if not arr:
            return {'mean': None, 'p50': None, 'p95': None, 'max': None, 'min': None}
        sv = sorted(arr)
        return {
            'mean': round(sum(sv) / len(sv), 1),
            'p50': round(_percentile(sv, 50.0), 1),
            'p95': round(_percentile(sv, 95.0), 1),
            'max': round(sv[-1], 1),
            'min': round(sv[0], 1),
        }

    u_st = _stats(util_vals)
    t_st = _stats(temp_vals)
    v_st = _stats(vram_pct_vals)
    c_st = _stats(cpu_vals)
    r_st = _stats(ram_vals)

    total_t_n = cool_n + warm_n + hot_n
    total_u_n = idle_u_n + mod_u_n + heavy_u_n

    # Deterministic Fleet Health Score (0-100)
    health_score = 100
    health_score -= min(40, unacked_crit * 20)
    health_score -= min(24, unacked_warn * 6)
    if t_st['p95'] is not None:
        if t_st['p95'] >= GPU_TEMP_CRIT_C:
            health_score -= 20
        elif t_st['p95'] >= GPU_TEMP_WARN_C:
            health_score -= 10
    if v_st['p95'] is not None and v_st['p95'] >= GPU_VRAM_CRIT_PCT:
        health_score -= 12
    if r_st['p95'] is not None and r_st['p95'] >= HOST_RAM_WARN_PCT:
        health_score -= 8
    health_score = max(0, min(100, int(round(health_score))))

    # Format per-GPU series keys (up to 6 GPUs for crisp multi-line overlay)
    gpu_series_meta = [
        {
            'key': f'{ga["node_id"]}#{ga["gpu_index"]}',
            'node_id': ga['node_id'],
            'node_name': ga['node_name'],
            'gpu_index': ga['gpu_index'],
            'label': f'GPU {ga["gpu_index"]:02d}',
            'full_label': f'{ga["node_name"]} · GPU {ga["gpu_index"]:02d}',
        }
        for _, ga in sorted(gpu_acc.items(), key=lambda kv: (kv[0][0], kv[0][1]))[:6]
    ]

    timeseries = []
    for k in sorted(buckets.keys()):
        b = buckets[k]
        per_gpu_t = {gk: round(v[0] / v[1], 1) for gk, v in b['per_gpu_temp'].items() if v[1] > 0}
        per_gpu_u = {gk: round(v[0] / v[1], 1) for gk, v in b['per_gpu_util'].items() if v[1] > 0}
        timeseries.append({
            'ts': b['ts'],
            'label': b['label'],
            'avg_gpu_util': round(b['util_sum'] / b['util_n'], 1) if b['util_n'] else None,
            'max_gpu_util': round(b['max_util'], 1) if b['max_util'] is not None else None,
            'avg_vram_pct': round(b['vram_pct_sum'] / b['vram_pct_n'], 1) if b['vram_pct_n'] else None,
            'max_vram_pct': round(b['max_vram_pct'], 1) if b['max_vram_pct'] is not None else None,
            'avg_vram_gb': round(b['vram_gb_sum'] / b['vram_gb_n'], 2) if b['vram_gb_n'] else None,
            'avg_temp_c': round(b['temp_sum'] / b['temp_n'], 1) if b['temp_n'] else None,
            'max_temp_c': round(b['max_temp'], 1) if b['max_temp'] is not None else None,
            'avg_cpu_pct': round(b['cpu_sum'] / b['cpu_n'], 1) if b['cpu_n'] else None,
            'avg_ram_pct': round(b['ram_sum'] / b['ram_n'], 1) if b['ram_n'] else None,
            'avg_power_w': round(b['power_sum'] / b['power_n'], 1) if b['power_n'] else None,
            'avg_fan_pct': round(b['fan_sum'] / b['fan_n'], 1) if b['fan_n'] else None,
            'avg_clock_mhz': round(b['clk_sum'] / b['clk_n'], 0) if b['clk_n'] else None,
            'per_gpu_temp': per_gpu_t,
            'per_gpu_util': per_gpu_u,
            'samples': b['samples'],
        })

    # Format GPU diagnostic profiles
    gpu_profiles = []
    for g_key in sorted(gpu_acc.keys()):
        ga = gpu_acc[g_key]
        gu = _stats(ga['utils'])
        gt = _stats(ga['temps'])
        gv = _stats(ga['vram_pcts'])
        gp = _stats(ga['powers'])
        gf = _stats(ga['fans'])
        gc = _stats(ga['clocks'])
        tn = ga['cool'] + ga['warm'] + ga['hot']
        un = ga['idle'] + ga['mod'] + ga['heavy']
        live_g = live_gpu_map.get(g_key)
        grade = 'healthy'
        if (gt['max'] is not None and gt['max'] >= GPU_TEMP_CRIT_C) or (gv['max'] is not None and gv['max'] >= GPU_VRAM_CRIT_PCT):
            grade = 'hot'
        elif (gt['p95'] is not None and gt['p95'] >= GPU_TEMP_WARN_C) or (gv['p95'] is not None and gv['p95'] >= GPU_VRAM_WARN_PCT):
            grade = 'watch'
        gpu_profiles.append({
            'node_id': ga['node_id'],
            'node_name': ga['node_name'],
            'gpu_index': ga['gpu_index'],
            'gpu_name': ga['gpu_name'],
            'health_grade': grade,
            'hours': round(ga['hours'], 2),
            'samples': ga['samples'],
            'avg_util': gu['mean'], 'p95_util': gu['p95'], 'max_util': gu['max'],
            'avg_temp_c': gt['mean'], 'p95_temp_c': gt['p95'], 'max_temp_c': gt['max'],
            'avg_vram_pct': gv['mean'], 'p95_vram_pct': gv['p95'], 'max_vram_pct': gv['max'],
            'peak_vram_gb': round(max(ga['vram_used_gbs']), 2) if ga['vram_used_gbs'] else None,
            'total_vram_gb': ga['vram_total_gb'],
            'avg_power_w': gp['mean'], 'max_power_w': gp['max'],
            'avg_fan_pct': gf['mean'], 'max_fan_pct': gf['max'],
            'avg_clock_mhz': gc['mean'], 'max_clock_mhz': gc['max'],
            'cool_pct': round(ga['cool'] / tn * 100.0, 1) if tn else 0.0,
            'warm_pct': round(ga['warm'] / tn * 100.0, 1) if tn else 0.0,
            'hot_pct': round(ga['hot'] / tn * 100.0, 1) if tn else 0.0,
            'idle_pct': round(ga['idle'] / un * 100.0, 1) if un else 0.0,
            'moderate_pct': round(ga['mod'] / un * 100.0, 1) if un else 0.0,
            'heavy_pct': round(ga['heavy'] / un * 100.0, 1) if un else 0.0,
            'live': live_g,
        })

    # Format Host profiles
    host_profiles = []
    for nid, ha in sorted(host_acc.items(), key=lambda kv: kv[1]['node_name']):
        hc = _stats(ha['cpus'])
        hr = _stats(ha['rams'])
        ht = _stats(ha['cpu_temps'])
        hd = _stats(ha['disks'])
        hl = _stats(ha['loads'])
        host_profiles.append({
            'node_id': nid,
            'node_name': ha['node_name'],
            'simulated': ha['simulated'],
            'hours': round(ha['hours'], 2),
            'samples': ha['samples'],
            'avg_cpu_pct': hc['mean'], 'p95_cpu_pct': hc['p95'], 'max_cpu_pct': hc['max'],
            'avg_ram_pct': hr['mean'], 'p95_ram_pct': hr['p95'], 'max_ram_pct': hr['max'],
            'avg_cpu_temp_c': ht['mean'], 'max_cpu_temp_c': ht['max'],
            'latest_disk_pct': round(ha['disks'][-1], 1) if ha['disks'] else None,
            'avg_load_1m': hl['mean'],
            'runtimes_uptime_pct': {
                rt: round((pair[0] / pair[1]) * 100.0, 1) if pair[1] > 0 else None
                for rt, pair in ha['rt'].items()
            },
            'live': live_hosts.get(nid),
        })

    # Format thermal-load curve
    thermal_curve = []
    for tb in thermal_bins:
        ts_list = tb['temps']
        thermal_curve.append({
            'bin_start': tb['bin_start'],
            'bin_end': tb['bin_end'],
            'label': f"{tb['bin_start']}-{tb['bin_end']}%",
            'samples': len(ts_list),
            'avg_temp_c': round(sum(ts_list) / len(ts_list), 1) if ts_list else None,
            'max_temp_c': round(max(ts_list), 1) if ts_list else None,
            'avg_power_w': round(sum(tb['powers']) / len(tb['powers']), 1) if tb['powers'] else None,
            'avg_fan_pct': round(sum(tb['fans']) / len(tb['fans']), 1) if tb['fans'] else None,
        })

    summary = {
        'health_score': health_score,
        'sample_count': total_rows,
        'observed_hours': round(observed_s / 3600.0, 3),
        'has_simulated': has_simulated,
        'unacked_critical': unacked_crit,
        'unacked_warning': unacked_warn,
        'thermal': {
            'mean_c': t_st['mean'], 'p50_c': t_st['p50'], 'p95_c': t_st['p95'], 'max_c': t_st['max'], 'min_c': t_st['min'],
            'cool_pct': round(cool_n / total_t_n * 100.0, 1) if total_t_n else 0.0,
            'warm_pct': round(warm_n / total_t_n * 100.0, 1) if total_t_n else 0.0,
            'hot_pct': round(hot_n / total_t_n * 100.0, 1) if total_t_n else 0.0,
            'avg_fan_pct': round(sum(fan_vals) / len(fan_vals), 1) if fan_vals else None,
            'avg_clock_mhz': round(sum(clk_vals) / len(clk_vals), 0) if clk_vals else None,
        },
        'utilization': {
            'mean_pct': u_st['mean'], 'p50_pct': u_st['p50'], 'p95_pct': u_st['p95'], 'max_pct': u_st['max'],
            'idle_pct': round(idle_u_n / total_u_n * 100.0, 1) if total_u_n else 0.0,
            'moderate_pct': round(mod_u_n / total_u_n * 100.0, 1) if total_u_n else 0.0,
            'heavy_pct': round(heavy_u_n / total_u_n * 100.0, 1) if total_u_n else 0.0,
        },
        'vram': {
            'mean_pct': v_st['mean'], 'p95_pct': v_st['p95'], 'max_pct': v_st['max'],
            'peak_gb': round(max(vram_gb_vals), 2) if vram_gb_vals else None,
            'mean_gb': round(sum(vram_gb_vals) / len(vram_gb_vals), 2) if vram_gb_vals else None,
        },
        'host': {
            'cpu_mean_pct': c_st['mean'], 'cpu_p95_pct': c_st['p95'], 'cpu_max_pct': c_st['max'],
            'ram_mean_pct': r_st['mean'], 'ram_p95_pct': r_st['p95'], 'ram_max_pct': r_st['max'],
        },
        'runtimes_uptime_pct': {
            rt: round((pair[0] / pair[1]) * 100.0, 1) if pair[1] > 0 else None
            for rt, pair in rt_counts.items()
        },
    }

    insights = _build_hardware_insights(summary, gpu_profiles, host_profiles, unacked_crit, unacked_warn)

    # Available filter options
    seen_nodes: dict[str, str] = {}
    for n in db.scalars(select(Node).order_by(Node.name)).all():
        if n.mode == 'demo' and not include_simulated and os.getenv('ENABLE_DEMO', 'true').lower() != 'true':
            continue
        seen_nodes[n.id] = n.name
    for nid, nname in db.execute(select(HardwareSample.node_id, HardwareSample.node_name).distinct()).all():
        seen_nodes.setdefault(nid, nname)

    available_gpu_indices = sorted({g['gpu_index'] for g in gpu_profiles} | {idx for _, idx in live_gpu_map.keys()})

    return {
        'range': range_key,
        'bucket_seconds': bucket_s,
        'generated_at': current_utc.isoformat(),
        'thresholds': {
            'gpu_temp_warn_c': GPU_TEMP_WARN_C,
            'gpu_temp_crit_c': GPU_TEMP_CRIT_C,
            'gpu_vram_warn_pct': GPU_VRAM_WARN_PCT,
            'gpu_vram_crit_pct': GPU_VRAM_CRIT_PCT,
        },
        'summary': summary,
        'gpu_series_meta': gpu_series_meta,
        'timeseries': timeseries,
        'thermal_curve': thermal_curve,
        'gpu_profiles': gpu_profiles,
        'host_profiles': host_profiles,
        'events': events_out,
        'insights': insights,
        'available_nodes': [{'id': k, 'name': v} for k, v in sorted(seen_nodes.items(), key=lambda kv: kv[1])],
        'available_gpu_indices': available_gpu_indices,
    }


def export_hardware_csv(
    db,
    *,
    target: str = 'timeseries',
    range_key: str = '24h',
    node_id: str | None = None,
    gpu_index: int | None = None,
    include_simulated: bool = False,
) -> tuple[str, str]:
    data = compute_hardware_analytics(
        db, range_key=range_key, node_id=node_id, gpu_index=gpu_index, include_simulated=include_simulated
    )
    buf = io.StringIO()
    buf.write('\ufeff')
    writer = csv.writer(buf)

    if target == 'gpus':
        writer.writerow([
            'Node', 'GPU Index', 'GPU Name', 'Health Grade', 'Hours',
            'Avg Util (%)', 'P95 Util (%)', 'Max Util (%)',
            'Avg Temp (C)', 'P95 Temp (C)', 'Max Temp (C)',
            'Avg VRAM (%)', 'Max VRAM (%)', 'Peak VRAM (GiB)', 'Total VRAM (GiB)',
            'Avg Fan (%)', 'Avg SM Clock (MHz)', 'Avg Power (W)', 'Max Power (W)',
            'Cool <60C (%)', 'Warm 60-75C (%)', 'Hot >=75C (%)',
        ])
        for g in data['gpu_profiles']:
            writer.writerow([
                g['node_name'], g['gpu_index'], g['gpu_name'], g['health_grade'], g['hours'],
                g['avg_util'], g['p95_util'], g['max_util'],
                g['avg_temp_c'], g['p95_temp_c'], g['max_temp_c'],
                g['avg_vram_pct'], g['max_vram_pct'], g['peak_vram_gb'], g['total_vram_gb'],
                g['avg_fan_pct'], g['avg_clock_mhz'], g['avg_power_w'], g['max_power_w'],
                g['cool_pct'], g['warm_pct'], g['hot_pct'],
            ])
        return buf.getvalue(), f'hardware-gpu-profiles-{range_key}.csv'

    if target == 'events':
        writer.writerow([
            'ID', 'Recorded At (UTC)', 'Severity', 'Event Type', 'Node', 'GPU Index',
            'Metric', 'Value', 'Threshold', 'Acknowledged', 'Message',
        ])
        for ev in data['events']:
            writer.writerow([
                ev['id'], ev['recorded_at'], ev['severity'], ev['event_type'],
                ev['node_name'], ev['gpu_index'] if ev['gpu_index'] is not None else '',
                ev['metric_name'] or '', ev['metric_value'] if ev['metric_value'] is not None else '',
                ev['threshold'] if ev['threshold'] is not None else '',
                ev['acknowledged'], ev['message'],
            ])
        return buf.getvalue(), f'hardware-events-{range_key}.csv'

    writer.writerow([
        'Timestamp (UTC)', 'Local Label', 'Avg GPU Util (%)', 'Max GPU Util (%)',
        'Avg VRAM (%)', 'Max VRAM (%)', 'Avg VRAM (GiB)',
        'Avg Temp (C)', 'Max Temp (C)', 'Avg Fan (%)', 'Avg SM Clock (MHz)',
        'Avg CPU (%)', 'Avg RAM (%)', 'Avg Power (W)', 'Samples',
    ])
    for pt in data['timeseries']:
        writer.writerow([
            pt['ts'], pt['label'], pt['avg_gpu_util'], pt['max_gpu_util'],
            pt['avg_vram_pct'], pt['max_vram_pct'], pt['avg_vram_gb'],
            pt['avg_temp_c'], pt['max_temp_c'], pt['avg_fan_pct'], pt['avg_clock_mhz'],
            pt['avg_cpu_pct'], pt['avg_ram_pct'], pt['avg_power_w'], pt['samples'],
        ])
    return buf.getvalue(), f'hardware-telemetry-{range_key}.csv'
