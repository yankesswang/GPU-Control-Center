"""Dynamic multi-service GPU scheduling, fair-share prioritization, and state-aware routing engine.

All queue ownership and task transitions stay inside the central API/PostgreSQL.
This module evaluates incoming inference requests together with live host/GPU/runtime
telemetry and rolling per-service consumption to decide:
  1. Admission control (per-source queue limits)
  2. Multi-service fair-share ordering + SLA/deadline urgency + aging + SJF
  3. Node health, warm-model affinity, external GPU/ComfyUI pressure, and thermal/TOU guards
  4. Multi-host smart routing (yielding to a materially better ready host within reservation window)
  5. Optional fallback-model routing when primary model hosts are unavailable/blocked
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
import os
from typing import Any

from sqlalchemy import desc, inspect, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .energy import get_or_create_tariff, rate_at_timestamp
from .models import Job, Node, SchedulerDecision, SchedulerPolicy, TariffSetting, now

ACTIVE_STATUSES = ('running', 'cancelling', 'unknown')
SLA_CLASSES = ('realtime', 'interactive', 'standard', 'batch', 'background')
SLA_BASE_BOOST: dict[str, float] = {
    'realtime': 4.0,
    'interactive': 2.0,
    'standard': 0.0,
    'batch': -1.5,
    'background': -3.0,
}

POLICY_PRESETS: dict[str, dict[str, Any]] = {
    'balanced': {
        'label_zh': '均衡動態（推薦）',
        'label_en': 'Balanced Dynamic (Recommended)',
        'description_zh': '兼顧多服務公平排程、SLA 優先級、常駐模型熱命中與 GPU 負載健康度。',
        'description_en': 'Balances multi-service fairness, SLA urgency, warm-model affinity, and GPU health.',
        'weights': {
            'weight_priority': 1.0,
            'weight_aging': 1.0,
            'weight_deadline': 2.5,
            'weight_fairness': 2.2,
            'weight_warm_affinity': 2.0,
            'weight_node_health': 2.5,
            'weight_sjf': 0.8,
            'defer_batch_on_peak_tariff': False,
        },
    },
    'latency_first': {
        'label_zh': '低延遲互動優先',
        'label_en': 'Latency-First Interactive',
        'description_zh': '大幅強化即時 SLA、截止時間、短任務優先（SJF）與熱命中，降低互動請求等待時間。',
        'description_en': 'Prioritizes realtime SLA, tight deadlines, short jobs (SJF), and warm hits for interactive traffic.',
        'weights': {
            'weight_priority': 1.4,
            'weight_aging': 0.8,
            'weight_deadline': 4.0,
            'weight_fairness': 1.5,
            'weight_warm_affinity': 2.8,
            'weight_node_health': 2.5,
            'weight_sjf': 1.8,
            'defer_batch_on_peak_tariff': False,
        },
    },
    'fair_share': {
        'label_zh': '多服務公平分配',
        'label_en': 'Strict Multi-Service Fair Share',
        'description_zh': '強化來源服務公平權重（DRR 思路），避免高流量批次服務餓死其他微服務。',
        'description_en': 'Strongly enforces weighted fair-share across services so bursty batch apps cannot starve others.',
        'weights': {
            'weight_priority': 0.9,
            'weight_aging': 1.2,
            'weight_deadline': 2.0,
            'weight_fairness': 4.5,
            'weight_warm_affinity': 1.2,
            'weight_node_health': 2.0,
            'weight_sjf': 0.6,
            'defer_batch_on_peak_tariff': False,
        },
    },
    'throughput_warm': {
        'label_zh': '熱模型吞吐優先',
        'label_en': 'Warm-Affinity Throughput',
        'description_zh': '強烈優先消化已常駐在 VRAM 的模型請求，大幅減少模型切換與冷載入抖動。',
        'description_en': 'Strongly prefers already-loaded models in VRAM to minimize cold-load overhead and thrashing.',
        'weights': {
            'weight_priority': 0.9,
            'weight_aging': 0.9,
            'weight_deadline': 2.0,
            'weight_fairness': 1.5,
            'weight_warm_affinity': 4.5,
            'weight_node_health': 2.0,
            'weight_sjf': 0.5,
            'defer_batch_on_peak_tariff': False,
        },
    },
    'thermal_eco': {
        'label_zh': '溫控節能優先',
        'label_en': 'Thermal & Off-Peak Eco',
        'description_zh': '強化 GPU 溫度與顯存保護，並在尖峰電價時自動暫緩可延後的背景／批次任務。',
        'description_en': 'Emphasizes GPU thermal/VRAM headroom and defers eligible background/batch jobs during peak tariff hours.',
        'weights': {
            'weight_priority': 1.0,
            'weight_aging': 0.8,
            'weight_deadline': 2.2,
            'weight_fairness': 2.0,
            'weight_warm_affinity': 2.2,
            'weight_node_health': 4.0,
            'weight_sjf': 0.7,
            'defer_batch_on_peak_tariff': True,
        },
    },
}


def _encode(obj) -> dict[str, Any]:
    return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}


def _finite(val: Any) -> float | None:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    f = float(val)
    return f if math.isfinite(f) else None


def _ensure_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def get_or_create_policy(db) -> SchedulerPolicy:
    policy = db.get(SchedulerPolicy, 'default')
    if policy is None:
        db.execute(
            pg_insert(SchedulerPolicy)
            .values(id='default', updated_at=now())
            .on_conflict_do_nothing(index_elements=['id'])
        )
        policy = db.get(SchedulerPolicy, 'default')
    return policy


def serialize_policy(policy: SchedulerPolicy) -> dict[str, Any]:
    data = _encode(policy)
    data['presets'] = POLICY_PRESETS
    return data


def get_job_sla_class(params: dict[str, Any] | None) -> str:
    raw = (params or {}).get('sla_class')
    if isinstance(raw, str) and raw in SLA_CLASSES:
        return raw
    return 'standard'


def get_job_allow_defer(params: dict[str, Any] | None) -> bool:
    p = params or {}
    if isinstance(p.get('allow_defer'), bool):
        return p['allow_defer']
    return get_job_sla_class(p) in ('batch', 'background')


@dataclass
class NodeState:
    node: Node
    online: bool
    maintenance: bool
    holders: list[Job]
    ready_slot: bool
    is_remote: bool
    warm_models: set[str]
    gpu_count: int
    avg_util_pct: float | None
    max_util_pct: float | None
    max_temp_c: float | None
    avg_temp_c: float | None
    total_vram_mb: float | None
    used_vram_mb: float | None
    free_vram_mb: float | None
    vram_used_pct: float | None
    power_w: float | None
    comfyui_running: int
    comfyui_pending: int
    external_gpu_vram_mb: float
    health_score: float
    thermal_hard_blocked: bool
    vram_pressure_high: bool
    health_notes: list[str] = field(default_factory=list)


@dataclass
class ServiceUsage:
    source: str
    weight: float
    active_jobs: int
    queued_jobs: int
    recent_completed_jobs: int
    recent_compute_s: float
    weighted_load: float
    fairness_pressure: float


@dataclass
class CandidateEval:
    job: Job
    node_state: NodeState
    effective_model: str
    used_fallback: bool
    blockers: list[str]
    guard_blockers: list[str]
    warm_hit: bool
    score: float
    breakdown: dict[str, Any]

    @property
    def runnable(self) -> bool:
        return not self.blockers and not self.guard_blockers


def extract_warm_models(metrics: dict[str, Any] | None) -> set[str]:
    warm: set[str] = set()
    if not isinstance(metrics, dict):
        return warm
    for runtime in ('ollama', 'lmstudio'):
        info = metrics.get(runtime)
        if not isinstance(info, dict):
            continue
        for item in info.get('loaded_models') or []:
            if isinstance(item, dict) and isinstance(item.get('name'), str) and item['name'].strip():
                warm.add(f"{runtime}:{item['name'].strip()}")
    return warm


def evaluate_node_state(
    node: Node,
    holders: list[Job],
    policy: SchedulerPolicy,
    now_ts: datetime,
) -> NodeState:
    metrics = node.metrics if isinstance(node.metrics, dict) else {}
    last_seen = _ensure_utc(node.last_seen) or now_ts
    online = last_seen >= now_ts - timedelta(seconds=20)
    if node.connection_host and not any(
        isinstance(metrics.get(p), dict) and metrics[p].get('status') == 'online'
        for p in ('lmstudio', 'ollama', 'comfyui')
    ):
        online = False

    is_remote = bool(node.connection_host or metrics.get('remote'))
    warm_models = extract_warm_models(metrics)
    gpus = metrics.get('gpus') if isinstance(metrics.get('gpus'), list) else []

    utils: list[float] = []
    temps: list[float] = []
    total_vram = 0.0
    used_vram = 0.0
    has_vram = False
    power_sum = 0.0
    has_power = False
    external_vram = 0.0

    for g in gpus:
        if not isinstance(g, dict):
            continue
        u = _finite(g.get('utilization'))
        if u is not None:
            utils.append(max(0.0, min(100.0, u)))
        tc = _finite(g.get('temperature_c'))
        if tc is not None:
            temps.append(tc)
        mt = _finite(g.get('memory_total_mb'))
        mu = _finite(g.get('memory_used_mb'))
        if mt is not None and mt > 0 and mu is not None and mu >= 0:
            total_vram += mt
            used_vram += min(mu, mt)
            has_vram = True
        pw = _finite(g.get('power_w'))
        if pw is not None and pw >= 0:
            power_sum += pw
            has_power = True
        for proc in g.get('other_processes') or []:
            if isinstance(proc, dict):
                pm = _finite(proc.get('memory_used_mb'))
                if pm is not None and pm > 0:
                    external_vram += pm

    avg_util = (sum(utils) / len(utils)) if utils else None
    max_util = max(utils) if utils else None
    avg_temp = (sum(temps) / len(temps)) if temps else None
    max_temp = max(temps) if temps else None
    free_vram = max(0.0, total_vram - used_vram) if has_vram else None
    vram_pct = (used_vram / total_vram * 100.0) if (has_vram and total_vram > 0) else None

    comfy = metrics.get('comfyui') if isinstance(metrics.get('comfyui'), dict) else {}
    comfy_running = len(comfy.get('running') or []) if comfy.get('status') == 'online' else 0
    comfy_pending = len(comfy.get('pending') or []) if comfy.get('status') == 'online' else 0

    health_notes: list[str] = []
    # Compute normalized health score in roughly [-2.0, 1.0].
    if not gpus:
        # Remote service-only node or demo node without GPU telemetry: neutral baseline.
        health_score = 0.5
        if is_remote:
            health_notes.append('remote_no_gpu_telemetry')
    else:
        vram_score = (1.0 - (vram_pct / 100.0)) if vram_pct is not None else 0.5
        util_score = (1.0 - (avg_util / 100.0)) if avg_util is not None else 0.5

        if max_temp is None:
            temp_score = 0.6
        elif max_temp <= 65.0:
            temp_score = 1.0
        elif max_temp <= policy.thermal_soft_c:
            span = max(1.0, policy.thermal_soft_c - 65.0)
            temp_score = 1.0 - 0.6 * ((max_temp - 65.0) / span)
        elif max_temp < policy.thermal_hard_c:
            span = max(1.0, policy.thermal_hard_c - policy.thermal_soft_c)
            temp_score = 0.4 - 1.2 * ((max_temp - policy.thermal_soft_c) / span)
            health_notes.append(f'warm_gpu:{max_temp:.0f}C')
        else:
            temp_score = -1.2
            health_notes.append(f'overheat:{max_temp:.0f}C')

        health_score = 0.45 * vram_score + 0.30 * util_score + 0.25 * temp_score

    if comfy_running > 0:
        # Normalize penalty relative to default weight so it visibly discourages colliding with active ComfyUI renders.
        health_score -= min(1.5, float(policy.comfyui_busy_penalty) * 0.35 * comfy_running)
        health_notes.append(f'comfyui_busy:{comfy_running}')

    if vram_pct is not None and vram_pct >= policy.vram_guard_pct:
        health_notes.append(f'vram_high:{vram_pct:.0f}%')

    thermal_hard_blocked = max_temp is not None and max_temp >= policy.thermal_hard_c
    vram_pressure_high = vram_pct is not None and vram_pct >= policy.vram_guard_pct
    ready_slot = online and (not node.maintenance) and (len(holders) == 0)

    return NodeState(
        node=node,
        online=online,
        maintenance=bool(node.maintenance),
        holders=holders,
        ready_slot=ready_slot,
        is_remote=is_remote,
        warm_models=warm_models,
        gpu_count=len(gpus),
        avg_util_pct=round(avg_util, 1) if avg_util is not None else None,
        max_util_pct=round(max_util, 1) if max_util is not None else None,
        max_temp_c=round(max_temp, 1) if max_temp is not None else None,
        avg_temp_c=round(avg_temp, 1) if avg_temp is not None else None,
        total_vram_mb=round(total_vram, 1) if has_vram else None,
        used_vram_mb=round(used_vram, 1) if has_vram else None,
        free_vram_mb=round(free_vram, 1) if free_vram is not None else None,
        vram_used_pct=round(vram_pct, 1) if vram_pct is not None else None,
        power_w=round(power_sum, 1) if has_power else None,
        comfyui_running=comfy_running,
        comfyui_pending=comfy_pending,
        external_gpu_vram_mb=round(external_vram, 1),
        health_score=round(health_score, 4),
        thermal_hard_blocked=thermal_hard_blocked,
        vram_pressure_high=vram_pressure_high,
        health_notes=health_notes,
    )


def compute_service_usage(
    db,
    policy: SchedulerPolicy,
    active_jobs: list[Job],
    queued_jobs: list[Job],
    now_ts: datetime,
) -> dict[str, ServiceUsage]:
    """Compute weighted rolling GPU consumption and fairness pressure per service `source`."""
    window_s = max(30, int(policy.fairness_window_s or 300))
    cutoff = now_ts - timedelta(seconds=window_s)
    recent_finished = db.scalars(
        select(Job).where(
            Job.status.in_(('completed', 'failed', 'cancelled')),
            Job.finished_at >= cutoff,
        )
    ).all()

    weights_map = policy.source_weights if isinstance(policy.source_weights, dict) else {}
    sources: set[str] = set()
    active_counts: dict[str, int] = {}
    queued_counts: dict[str, int] = {}
    recent_counts: dict[str, int] = {}
    compute_s: dict[str, float] = {}

    for j in active_jobs:
        if (j.parameters or {}).get('model_action'):
            continue
        s = j.source or 'unknown'
        sources.add(s)
        active_counts[s] = active_counts.get(s, 0) + 1
        started = _ensure_utc(j.started_at) or now_ts
        elapsed = max(1.0, (now_ts - started).total_seconds())
        compute_s[s] = compute_s.get(s, 0.0) + elapsed

    for j in queued_jobs:
        if (j.parameters or {}).get('model_action'):
            continue
        s = j.source or 'unknown'
        sources.add(s)
        queued_counts[s] = queued_counts.get(s, 0) + 1

    for j in recent_finished:
        if (j.parameters or {}).get('model_action'):
            continue
        s = j.source or 'unknown'
        sources.add(s)
        recent_counts[s] = recent_counts.get(s, 0) + 1
        started = _ensure_utc(j.started_at)
        finished = _ensure_utc(j.finished_at)
        if started and finished and finished >= started:
            dur = max(1.0, min(float(window_s), (finished - started).total_seconds()))
        else:
            dur = 5.0
        compute_s[s] = compute_s.get(s, 0.0) + dur

    raw_loads: dict[str, tuple[float, float]] = {}
    for s in sources:
        raw_w = _finite(weights_map.get(s))
        weight = max(0.1, min(10.0, raw_w)) if (raw_w is not None and raw_w > 0) else 1.0
        # Active jobs carry an immediate concurrency weight so bursty callers don't monopolize multi-node clusters.
        w_load = (active_counts.get(s, 0) * 35.0 + compute_s.get(s, 0.0)) / weight
        raw_loads[s] = (weight, w_load)

    if not raw_loads:
        return {}

    min_load = min(v[1] for v in raw_loads.values())
    max_load = max(v[1] for v in raw_loads.values())
    # Use a smooth denominator (at least 30s) so tiny 1-2s differences don't swing wildly.
    denom = max(30.0, max_load - min_load)

    result: dict[str, ServiceUsage] = {}
    for s in sorted(sources):
        weight, w_load = raw_loads[s]
        # Pressure in [0.0, 2.0]: 0.0 for the least-served service, scales smoothly for heavier consumers.
        relative = max(0.0, (w_load - min_load) / denom)
        # Also reward explicit high source_weight even when loads are equal.
        weight_offset = max(-0.5, min(0.5, (1.0 - weight) * 0.4))
        pressure = max(0.0, min(2.0, relative + max(0.0, weight_offset)))
        if weight > 1.0 and relative == 0.0:
            pressure = max(-0.5, (1.0 - min(weight, 3.0)) * 0.25)
        result[s] = ServiceUsage(
            source=s,
            weight=round(weight, 2),
            active_jobs=active_counts.get(s, 0),
            queued_jobs=queued_counts.get(s, 0),
            recent_completed_jobs=recent_counts.get(s, 0),
            recent_compute_s=round(compute_s.get(s, 0.0), 1),
            weighted_load=round(w_load, 1),
            fairness_pressure=round(pressure, 4),
        )
    return result


def _job_compatible_model_for_node(
    job: Job,
    node_state: NodeState,
    *,
    allow_fallback: bool,
) -> tuple[str | None, bool]:
    """Return (effective_model, used_fallback) if node can execute job, else (None, False)."""
    params = job.parameters or {}
    caps = set(node_state.node.capabilities or [])
    if params.get('model_action'):
        provider = job.model.split(':', 1)[0] if ':' in job.model else ''
        if provider + '.model_control' in caps:
            return job.model, False
        return None, False

    if job.model in caps:
        return job.model, False

    if allow_fallback:
        fallbacks = params.get('fallback_models') or []
        if isinstance(fallbacks, list):
            for fb in fallbacks:
                if isinstance(fb, str) and fb in caps:
                    return fb, True
    return None, False


def evaluate_job_on_node(
    job: Job,
    node_state: NodeState,
    service_usage: dict[str, ServiceUsage],
    policy: SchedulerPolicy,
    tariff_tier: str,
    now_ts: datetime,
    *,
    allow_fallback: bool = True,
) -> CandidateEval:
    params = job.parameters or {}
    blockers: list[str] = []
    guard_blockers: list[str] = []

    effective_model, used_fallback = _job_compatible_model_for_node(
        job, node_state, allow_fallback=allow_fallback
    )
    if effective_model is None:
        blockers.append('unsupported_capability')

    if params.get('target_node_id') not in (None, node_state.node.id):
        blockers.append('different_target_node')
    if not node_state.online:
        blockers.append('offline')
    if node_state.maintenance:
        blockers.append('maintenance')
    if node_state.holders:
        if any(h.status == 'unknown' for h in node_state.holders):
            blockers.append('unconfirmed_execution')
        else:
            blockers.append('slot_occupied')

    is_control = bool(params.get('model_action'))
    sla_class = get_job_sla_class(params)
    allow_defer = get_job_allow_defer(params)
    warm_hit = bool(effective_model and effective_model in node_state.warm_models)

    if not is_control:
        if node_state.thermal_hard_blocked and (sla_class in ('batch', 'background') or allow_defer):
            guard_blockers.append('thermal_guard')
        if policy.defer_batch_on_peak_tariff and tariff_tier == 'peak' and (sla_class == 'background' or allow_defer):
            guard_blockers.append('peak_tariff_deferred')

    created_at = _ensure_utc(job.created_at) or now_ts
    wait_s = max(0.0, (now_ts - created_at).total_seconds())

    if is_control:
        total = 100.0 + float(job.priority) + wait_s / max(1.0, float(policy.aging_interval_s))
        breakdown = {
            'control_boost': 100.0,
            'priority': round(float(job.priority), 3),
            'aging': round(wait_s / max(1.0, float(policy.aging_interval_s)), 3),
            'sla': 0.0,
            'deadline': 0.0,
            'fairness': 0.0,
            'warm_affinity': 0.0,
            'node_health': 0.0,
            'sjf': 0.0,
            'preference': 0.0,
            'fallback_penalty': 0.0,
            'total': round(total, 3),
        }
        return CandidateEval(
            job=job,
            node_state=node_state,
            effective_model=effective_model or job.model,
            used_fallback=False,
            blockers=blockers,
            guard_blockers=guard_blockers,
            warm_hit=False,
            score=round(total, 4),
            breakdown=breakdown,
        )

    # 1. Priority & SLA urgency
    sla_boost = SLA_BASE_BOOST.get(sla_class, 0.0)
    priority_term = float(policy.weight_priority) * float(job.priority)
    sla_term = float(policy.weight_priority) * sla_boost

    # 2. Anti-starvation aging
    aging_term = float(policy.weight_aging) * (wait_s / max(1.0, float(policy.aging_interval_s)))

    # 3. Deadline urgency (EDF-like smooth boost)
    deadline_term = 0.0
    deadline_s = _finite(params.get('deadline_seconds'))
    if deadline_s is not None and deadline_s > 0:
        remaining = deadline_s - wait_s
        if remaining <= 0:
            urgency_factor = 4.0
        else:
            ratio = remaining / max(10.0, deadline_s)
            urgency_factor = min(4.0, max(0.0, (1.0 - ratio) * 2.5 + (0.6 if remaining <= 30 else 0.0)))
        deadline_term = float(policy.weight_deadline) * urgency_factor

    # 4. Multi-service fair-share adjustment
    usage = service_usage.get(job.source)
    fairness_pressure = usage.fairness_pressure if usage else 0.0
    fairness_term = -float(policy.weight_fairness) * fairness_pressure

    # 5. Warm-model affinity & runtime preference
    warm_term = 0.0
    if warm_hit:
        warm_term += float(policy.weight_warm_affinity)
    elif effective_model and ':' in effective_model:
        rt = effective_model.split(':', 1)[0]
        if rt == node_state.node.default_runtime:
            warm_term += 0.25 * float(policy.weight_warm_affinity)

    # 6. Node health & VRAM cold-load protection
    health_term = float(policy.weight_node_health) * node_state.health_score
    if node_state.vram_pressure_high and not warm_hit:
        health_term -= 2.5

    # 7. Short-Job-First (SJF) hint from max_tokens
    max_tokens = _finite(params.get('max_tokens'))
    if max_tokens is None:
        max_tokens = 256.0
    sjf_term = float(policy.weight_sjf) * (1.0 - min(max(1.0, max_tokens), 2048.0) / 2048.0)

    # 8. Soft preferred node bonus & fallback penalty
    pref_term = 1.5 if params.get('preferred_node_id') == node_state.node.id else 0.0
    fallback_penalty = -0.75 if used_fallback else 0.0

    total = (
        priority_term
        + sla_term
        + aging_term
        + deadline_term
        + fairness_term
        + warm_term
        + health_term
        + sjf_term
        + pref_term
        + fallback_penalty
    )
    breakdown = {
        'priority': round(priority_term, 3),
        'sla': round(sla_term, 3),
        'aging': round(aging_term, 3),
        'deadline': round(deadline_term, 3),
        'fairness': round(fairness_term, 3),
        'warm_affinity': round(warm_term, 3),
        'node_health': round(health_term, 3),
        'sjf': round(sjf_term, 3),
        'preference': round(pref_term, 3),
        'fallback_penalty': round(fallback_penalty, 3),
        'total': round(total, 3),
    }
    return CandidateEval(
        job=job,
        node_state=node_state,
        effective_model=effective_model or job.model,
        used_fallback=used_fallback,
        blockers=blockers,
        guard_blockers=guard_blockers,
        warm_hit=warm_hit,
        score=round(total, 4),
        breakdown=breakdown,
    )


def _visible_nodes(db) -> list[Node]:
    enable_demo = os.getenv('ENABLE_DEMO', 'true').lower() == 'true'
    rows = db.scalars(select(Node).order_by(Node.name)).all()
    return [n for n in rows if enable_demo or n.mode != 'demo']


def _cluster_snapshot(db, now_ts: datetime):
    policy = get_or_create_policy(db)
    tariff = get_or_create_tariff(db)
    _, tariff_tier, _ = rate_at_timestamp(now_ts, tariff)
    nodes = _visible_nodes(db)
    active_jobs = db.scalars(select(Job).where(Job.status.in_(ACTIVE_STATUSES))).all()
    queued_jobs = db.scalars(select(Job).where(Job.status == 'queued').order_by(Job.created_at, Job.id)).all()

    holders_by_node: dict[str, list[Job]] = {}
    for j in active_jobs:
        if j.node_id:
            holders_by_node.setdefault(j.node_id, []).append(j)

    node_states: dict[str, NodeState] = {
        n.id: evaluate_node_state(n, holders_by_node.get(n.id, []), policy, now_ts)
        for n in nodes
    }
    service_usage = compute_service_usage(db, policy, active_jobs, queued_jobs, now_ts)
    return policy, tariff, tariff_tier, node_states, active_jobs, queued_jobs, service_usage


def _record_decision(
    db,
    *,
    action: str,
    policy: SchedulerPolicy,
    reason: str,
    job: Job | None = None,
    node_state: NodeState | None = None,
    score: float | None = None,
    breakdown: dict[str, Any] | None = None,
    model_override: str | None = None,
    source_override: str | None = None,
    sla_override: str | None = None,
    throttle_s: int = 0,
    now_ts: datetime | None = None,
) -> SchedulerDecision | None:
    ts = now_ts or now()
    job_id = job.id if job else None
    if throttle_s > 0 and job_id is not None:
        recent = db.scalar(
            select(SchedulerDecision.id).where(
                SchedulerDecision.job_id == job_id,
                SchedulerDecision.action == action,
                SchedulerDecision.recorded_at >= ts - timedelta(seconds=throttle_s),
            ).limit(1)
        )
        if recent is not None:
            return None

    dec = SchedulerDecision(
        recorded_at=ts,
        action=action,
        job_id=job_id,
        job_name=(job.name[:120] if job and job.name else None),
        source=(source_override or (job.source if job else None)),
        model=(model_override or (job.model if job else None)),
        sla_class=(sla_override or (get_job_sla_class(job.parameters) if job else None)),
        selected_node_id=(node_state.node.id if node_state else None),
        selected_node_name=(node_state.node.name if node_state else None),
        score=round(score, 3) if score is not None else None,
        policy_mode=policy.mode,
        reason=reason,
        breakdown=breakdown or {},
    )
    db.add(dec)
    return dec


def check_admission(
    db,
    *,
    source: str,
    model: str,
    sla_class: str,
    job_name: str,
    now_ts: datetime | None = None,
) -> tuple[bool, str | None]:
    """Enforce per-source admission quota if configured in SchedulerPolicy."""
    ts = now_ts or now()
    policy = get_or_create_policy(db)
    limit = int(policy.max_queued_per_source or 0)
    if limit <= 0:
        return True, None
    queued_count = len(
        db.scalars(select(Job.id).where(Job.status == 'queued', Job.source == source)).all()
    )
    if queued_count >= limit:
        reason = f'服務 {source} 排隊任務數已達上限（{queued_count}/{limit}），已啟動限流保護。'
        _record_decision(
            db,
            action='reject',
            policy=policy,
            reason=reason,
            source_override=source,
            model_override=model,
            sla_override=sla_class,
            breakdown={'queued_for_source': queued_count, 'max_queued_per_source': limit, 'job_name': job_name},
            now_ts=ts,
        )
        return False, reason
    return True, None


def _summarize_dispatch_reason(cand: CandidateEval, policy: SchedulerPolicy, service_usage: dict[str, ServiceUsage]) -> str:
    tags: list[str] = []
    params = cand.job.parameters or {}
    if params.get('model_action'):
        return f"模型控制任務優先派送至指定主機 {cand.node_state.node.name}。"
    sla = get_job_sla_class(params)
    if sla != 'standard':
        tags.append(f'SLA={sla}')
    if cand.warm_hit:
        tags.append('常駐模型熱命中')
    if cand.used_fallback:
        tags.append(f'備援降級至 {cand.effective_model}')
    if cand.node_state.vram_used_pct is not None:
        tags.append(f'VRAM {cand.node_state.vram_used_pct:.0f}%')
    if cand.node_state.max_temp_c is not None:
        tags.append(f'{cand.node_state.max_temp_c:.0f}°C')
    usage = service_usage.get(cand.job.source)
    if usage and len(service_usage) > 1:
        tags.append(f'公平壓力 {usage.fairness_pressure:.2f}')
    tag_str = '、'.join(tags) if tags else f'模式 {policy.mode}'
    return f"動態排程評分 {cand.score:.2f} 派送至 {cand.node_state.node.name}（{tag_str}）。"


def select_job_for_claim(
    db,
    claiming_node: Node,
    *,
    now_ts: datetime | None = None,
) -> tuple[Job, str, dict[str, Any]] | None:
    """Select and row-lock the optimal queued job for `claiming_node`.

    Returns (locked_job, event_message, decision_meta) or None if no job should be claimed.
    """
    ts = now_ts or now()
    policy, _tariff, tariff_tier, node_states, _active_jobs, queued_jobs, service_usage = _cluster_snapshot(db, ts)
    claiming_state = node_states.get(claiming_node.id)
    if claiming_state is None:
        claiming_state = evaluate_node_state(claiming_node, [], policy, ts)
        node_states[claiming_node.id] = claiming_state

    if not claiming_state.ready_slot or not queued_jobs:
        return None

    other_ready_states = [
        st for nid, st in node_states.items()
        if nid != claiming_node.id and st.ready_slot
    ]

    runnable_candidates: list[CandidateEval] = []

    for job in queued_jobs:
        params = job.parameters or {}
        is_control = bool(params.get('model_action'))

        # Primary availability check across ALL ready nodes (determines whether fallback is permitted now).
        primary_ready_elsewhere = any(
            (not is_control)
            and (job.model in (st.node.capabilities or []))
            and evaluate_job_on_node(job, st, service_usage, policy, tariff_tier, ts, allow_fallback=False).runnable
            for st in other_ready_states
        )
        allow_fallback_here = (not primary_ready_elsewhere) and (job.model not in (claiming_node.capabilities or []))

        cand = evaluate_job_on_node(
            job,
            claiming_state,
            service_usage,
            policy,
            tariff_tier,
            ts,
            allow_fallback=allow_fallback_here,
        )

        if cand.blockers:
            continue

        if cand.guard_blockers:
            _record_decision(
                db,
                action='defer',
                policy=policy,
                job=job,
                node_state=claiming_state,
                score=cand.score,
                reason=f"因系統保護（{', '.join(cand.guard_blockers)}）暫緩派送至 {claiming_node.name}。",
                breakdown={'guard_blockers': cand.guard_blockers, 'scores': cand.breakdown},
                throttle_s=20,
                now_ts=ts,
            )
            continue

        # Multi-host smart routing: yield if another READY idle host is materially better within reservation window.
        if (
            not is_control
            and other_ready_states
            and int(policy.route_reservation_s or 0) > 0
            and params.get('target_node_id') != claiming_node.id
        ):
            created_at = _ensure_utc(job.created_at) or ts
            wait_s = max(0.0, (ts - created_at).total_seconds())
            if wait_s < float(policy.route_reservation_s):
                best_other: CandidateEval | None = None
                for other_st in other_ready_states:
                    other_eval = evaluate_job_on_node(
                        job, other_st, service_usage, policy, tariff_tier, ts, allow_fallback=False
                    )
                    if other_eval.runnable and (best_other is None or other_eval.score > best_other.score):
                        best_other = other_eval
                if best_other is not None and (best_other.score - cand.score) >= float(policy.routing_margin):
                    _record_decision(
                        db,
                        action='yield',
                        policy=policy,
                        job=job,
                        node_state=best_other.node_state,
                        score=best_other.score,
                        reason=(
                            f"{claiming_node.name}（評分 {cand.score:.2f}）讓位給更適合的閒置主機 "
                            f"{best_other.node_state.node.name}（評分 {best_other.score:.2f}）。"
                        ),
                        breakdown={
                            'claiming_node_id': claiming_node.id,
                            'claiming_score': cand.score,
                            'preferred_node_id': best_other.node_state.node.id,
                            'preferred_score': best_other.score,
                            'margin': round(best_other.score - cand.score, 3),
                            'claiming_breakdown': cand.breakdown,
                            'preferred_breakdown': best_other.breakdown,
                        },
                        throttle_s=10,
                        now_ts=ts,
                    )
                    continue

        runnable_candidates.append(cand)

    if not runnable_candidates:
        return None

    runnable_candidates.sort(
        key=lambda c: (
            0 if (c.job.parameters or {}).get('model_action') else 1,
            -c.score,
            _ensure_utc(c.job.created_at) or ts,
            c.job.id,
        )
    )

    for cand in runnable_candidates:
        locked = db.scalar(
            select(Job)
            .where(Job.id == cand.job.id, Job.status == 'queued')
            .with_for_update(skip_locked=True)
        )
        if locked is None:
            continue

        if cand.used_fallback and cand.effective_model != locked.model:
            original_model = locked.model
            new_params = dict(locked.parameters or {})
            new_params['original_model'] = original_model
            locked.parameters = new_params
            locked.model = cand.effective_model
            _record_decision(
                db,
                action='fallback',
                policy=policy,
                job=locked,
                node_state=claiming_state,
                score=cand.score,
                model_override=cand.effective_model,
                reason=f"主模型 {original_model} 目前無可用閒置主機，自動降級改由 {claiming_node.name} 執行備援模型 {cand.effective_model}。",
                breakdown={'original_model': original_model, 'fallback_model': cand.effective_model, 'scores': cand.breakdown},
                now_ts=ts,
            )

        reason = _summarize_dispatch_reason(cand, policy, service_usage)
        meta = {
            'score': cand.score,
            'breakdown': cand.breakdown,
            'warm_hit': cand.warm_hit,
            'used_fallback': cand.used_fallback,
            'effective_model': cand.effective_model,
            'policy_mode': policy.mode,
            'node_health': claiming_state.health_score,
            'gpu_util_pct': claiming_state.avg_util_pct,
            'vram_used_pct': claiming_state.vram_used_pct,
            'max_temp_c': claiming_state.max_temp_c,
        }
        _record_decision(
            db,
            action='dispatch',
            policy=policy,
            job=locked,
            node_state=claiming_state,
            score=cand.score,
            model_override=cand.effective_model,
            reason=reason,
            breakdown=meta,
            now_ts=ts,
        )
        return locked, reason, meta

    return None


def _rank_queued_jobs(
    queued_jobs: list[Job],
    node_states: dict[str, NodeState],
    service_usage: dict[str, ServiceUsage],
    policy: SchedulerPolicy,
    tariff_tier: str,
    now_ts: datetime,
) -> list[dict[str, Any]]:
    """Compute a deterministic cluster-wide ranked dispatch preview for all queued jobs."""
    entries: list[dict[str, Any]] = []
    states_list = list(node_states.values())

    for job in queued_jobs:
        params = job.parameters or {}
        is_control = bool(params.get('model_action'))
        evals: list[CandidateEval] = []
        for st in states_list:
            ev = evaluate_job_on_node(job, st, service_usage, policy, tariff_tier, now_ts, allow_fallback=True)
            evals.append(ev)

        runnable = [e for e in evals if e.runnable]
        compatible = [e for e in evals if 'unsupported_capability' not in e.blockers and 'different_target_node' not in e.blockers]
        guarded_only = [e for e in evals if (not e.blockers) and e.guard_blockers]

        if runnable:
            best = max(runnable, key=lambda e: (e.score, e.node_state.node.name))
            action = 'fallback_ready' if best.used_fallback else 'dispatch_ready'
        elif guarded_only:
            best = max(guarded_only, key=lambda e: (e.score, e.node_state.node.name))
            action = 'deferred'
        elif compatible:
            best = max(compatible, key=lambda e: (e.score, e.node_state.node.name))
            action = 'waiting_slot'
        else:
            # No compatible node at all: compute node-independent base priority score for global ordering.
            created_at = _ensure_utc(job.created_at) or now_ts
            wait_s = max(0.0, (now_ts - created_at).total_seconds())
            sla = get_job_sla_class(params)
            usage = service_usage.get(job.source)
            base = (
                (100.0 if is_control else 0.0)
                + float(policy.weight_priority) * (float(job.priority) + SLA_BASE_BOOST.get(sla, 0.0))
                + float(policy.weight_aging) * (wait_s / max(1.0, float(policy.aging_interval_s)))
                - float(policy.weight_fairness) * (usage.fairness_pressure if usage else 0.0)
            )
            best = None
            action = 'no_compatible_node'
            fallback_score = round(base, 4)

        entries.append({
            'job': job,
            'is_control': is_control,
            'score': best.score if best is not None else fallback_score,
            'best_eval': best,
            'evals': evals,
            'decision_action': action,
        })

    entries.sort(
        key=lambda item: (
            0 if item['is_control'] else 1,
            -item['score'],
            _ensure_utc(item['job'].created_at) or now_ts,
            item['job'].id,
        )
    )
    return entries


def explain_job_scheduling(db, job_id: str, *, now_ts: datetime | None = None) -> dict[str, Any] | None:
    """Build backward-compatible + enriched dynamic scheduling explanation for a single job."""
    ts = now_ts or now()
    job = db.get(Job, job_id)
    if not job:
        return None

    policy, _tariff, tariff_tier, node_states, _active_jobs, queued_jobs, service_usage = _cluster_snapshot(db, ts)
    ranked = _rank_queued_jobs(queued_jobs, node_states, service_usage, policy, tariff_tier, ts)
    rank_ids = [item['job'].id for item in ranked]

    evals = [
        evaluate_job_on_node(job, st, service_usage, policy, tariff_tier, ts, allow_fallback=True)
        for st in node_states.values()
    ]
    runnable = [e for e in evals if e.runnable]
    best_ready = max(runnable, key=lambda e: e.score) if runnable else None
    guarded_only = [e for e in evals if (not e.blockers) and e.guard_blockers]

    candidates = []
    for ev in evals:
        all_blockers = list(ev.blockers) + list(ev.guard_blockers)
        candidates.append({
            'node_id': ev.node_state.node.id,
            'name': ev.node_state.node.name,
            'ready': ev.runnable,
            'blockers': all_blockers,
            'holders': [
                {'id': h.id, 'name': h.name, 'source': h.source, 'status': h.status}
                for h in ev.node_state.holders
            ],
            'score': ev.score,
            'warm_hit': ev.warm_hit,
            'health_score': ev.node_state.health_score,
            'vram_used_pct': ev.node_state.vram_used_pct,
            'max_temp_c': ev.node_state.max_temp_c,
            'recommended': bool(best_ready and best_ready.node_state.node.id == ev.node_state.node.id),
            'effective_model': ev.effective_model,
            'used_fallback': ev.used_fallback,
            'score_breakdown': ev.breakdown,
        })

    candidates.sort(key=lambda c: (0 if c['recommended'] else 1, 0 if c['ready'] else 1, -(c['score'] or -999), c['name']))

    compatible = [n for n in candidates if 'unsupported_capability' not in n['blockers']]
    if job.status == 'queued':
        if not compatible:
            reason, summary = 'no_compatible_node', '尚無支援此能力的主機，請啟動或接入對應 Agent。'
            decision_action = 'no_compatible_node'
        elif best_ready is not None:
            reason = 'awaiting_dispatch'
            warm_note = '（常駐模型熱命中）' if best_ready.warm_hit else ''
            summary = f"動態排程推薦派送至 {best_ready.node_state.node.name}{warm_note}（評分 {best_ready.score:.2f}），等待 Agent 輪詢。"
            decision_action = 'fallback_ready' if best_ready.used_fallback else 'dispatch_ready'
        elif guarded_only:
            guards = sorted({g for e in guarded_only for g in e.guard_blockers})
            reason = 'nodes_blocked'
            summary = f"有空閒主機但觸發系統保護條件（{'、'.join(guards)}），任務目前自動暫緩。"
            decision_action = 'deferred'
        else:
            reason, summary = 'nodes_blocked', '符合能力的主機目前無法接單，請查看各主機的阻擋原因。'
            decision_action = 'waiting_slot'
    elif job.status == 'unknown':
        reason, summary, decision_action = 'execution_unconfirmed', '原執行租約已逾時，尚未確認停止；保留主機工作槽，不會自動重派。', 'unknown'
    elif job.status == 'cancelling':
        reason, summary, decision_action = 'awaiting_cancel_ack', '取消請求已保存，等待 Agent 確認停止後釋放工作槽。', 'cancelling'
    elif job.status == 'running':
        reason, summary, decision_action = 'executing', f'任務正在 {job.node_id} 執行。', 'running'
    else:
        reason, summary, decision_action = 'terminal', '任務已結束，沒有等待排程。', 'terminal'

    usage = service_usage.get(job.source)
    return {
        'job_id': job.id,
        'status': job.status,
        'source': job.source,
        'sla_class': get_job_sla_class(job.parameters),
        'reason': reason,
        'decision_action': decision_action,
        'summary': summary,
        'queue_rank': rank_ids.index(job.id) + 1 if job.id in rank_ids else None,
        'queue_size': len(rank_ids),
        'recommended_node_id': best_ready.node_state.node.id if best_ready else None,
        'recommended_score': best_ready.score if best_ready else None,
        'candidates': candidates,
        'fairness': (
            {
                'source': usage.source,
                'weight': usage.weight,
                'active_jobs': usage.active_jobs,
                'queued_jobs': usage.queued_jobs,
                'recent_compute_s': usage.recent_compute_s,
                'fairness_pressure': usage.fairness_pressure,
            }
            if usage
            else None
        ),
        'policy': f'dynamic_{policy.mode}',
        'policy_mode': policy.mode,
        'tariff_tier': tariff_tier,
        'note': '排序位置與評分為即時叢集狀態快照；多主機環境會優先將任務導向最佳健康度與常駐熱命中的主機。',
    }


def simulate_dispatch(
    db,
    *,
    model: str,
    source: str = 'simulator',
    priority: int = 0,
    max_tokens: int = 256,
    sla_class: str = 'standard',
    deadline_seconds: int | None = None,
    preferred_node_id: str | None = None,
    fallback_models: list[str] | None = None,
    allow_defer: bool | None = None,
    now_ts: datetime | None = None,
) -> dict[str, Any]:
    """Dry-run what the dynamic scheduler would decide right now for a hypothetical request."""
    ts = now_ts or now()
    policy, _tariff, tariff_tier, node_states, _active_jobs, queued_jobs, service_usage = _cluster_snapshot(db, ts)

    params: dict[str, Any] = {'prompt': '(simulation)', 'max_tokens': max_tokens}
    if sla_class:
        params['sla_class'] = sla_class
    if deadline_seconds is not None:
        params['deadline_seconds'] = deadline_seconds
    if preferred_node_id:
        params['preferred_node_id'] = preferred_node_id
    if fallback_models:
        params['fallback_models'] = fallback_models
    if allow_defer is not None:
        params['allow_defer'] = allow_defer

    fake_job = Job(
        id='00000000-0000-0000-0000-000000000000',
        name='Dry-Run Simulation',
        source=source,
        model=model,
        priority=priority,
        steps=20,
        parameters=params,
        status='queued',
        created_at=ts,
    )

    # Check admission quota without mutating DB
    limit = int(policy.max_queued_per_source or 0)
    queued_for_source = sum(1 for j in queued_jobs if j.source == source)
    admission_allowed = not (limit > 0 and queued_for_source >= limit)

    evals = [
        evaluate_job_on_node(fake_job, st, service_usage, policy, tariff_tier, ts, allow_fallback=True)
        for st in node_states.values()
    ]
    runnable = [e for e in evals if e.runnable]
    guarded_only = [e for e in evals if (not e.blockers) and e.guard_blockers]
    compatible = [e for e in evals if 'unsupported_capability' not in e.blockers]
    best = max(runnable, key=lambda e: e.score) if runnable else None

    if not admission_allowed:
        decision_action = 'reject_quota'
        summary = f'服務 {source} 排隊數已達上限（{queued_for_source}/{limit}），此請求將被限流拒絕（HTTP 429）。'
    elif best is not None:
        decision_action = 'fallback_dispatch' if best.used_fallback else 'dispatch'
        summary = _summarize_dispatch_reason(best, policy, service_usage)
    elif guarded_only:
        guards = sorted({g for e in guarded_only for g in e.guard_blockers})
        decision_action = 'defer'
        summary = f"請求將入列暫緩，因候選主機觸發保護條件：{', '.join(guards)}。"
    elif compatible:
        decision_action = 'queue_wait'
        summary = '請求將入列排隊，待相容主機釋放工作槽後依動態分數派送。'
    else:
        decision_action = 'no_compatible_node'
        summary = '目前沒有支援此模型或備援模型的在線主機。'

    ranked_candidates = sorted(
        evals,
        key=lambda e: (0 if e.runnable else 1, -e.score, e.node_state.node.name),
    )

    return {
        'generated_at': ts.isoformat(),
        'policy_mode': policy.mode,
        'tariff_tier': tariff_tier,
        'admission_allowed': admission_allowed,
        'decision_action': decision_action,
        'summary': summary,
        'selected_node_id': best.node_state.node.id if (admission_allowed and best) else None,
        'selected_node_name': best.node_state.node.name if (admission_allowed and best) else None,
        'selected_model': best.effective_model if (admission_allowed and best) else model,
        'used_fallback': bool(best and best.used_fallback),
        'selected_score': best.score if best else None,
        'candidates': [
            {
                'node_id': e.node_state.node.id,
                'node_name': e.node_state.node.name,
                'runnable': e.runnable,
                'recommended': bool(best and best.node_state.node.id == e.node_state.node.id),
                'effective_model': e.effective_model,
                'used_fallback': e.used_fallback,
                'warm_hit': e.warm_hit,
                'score': e.score,
                'health_score': e.node_state.health_score,
                'vram_used_pct': e.node_state.vram_used_pct,
                'max_temp_c': e.node_state.max_temp_c,
                'avg_util_pct': e.node_state.avg_util_pct,
                'blockers': list(e.blockers) + list(e.guard_blockers),
                'breakdown': e.breakdown,
            }
            for e in ranked_candidates
        ],
    }


def build_scheduler_state(db, *, decision_limit: int = 30, now_ts: datetime | None = None) -> dict[str, Any]:
    """Return full live cluster state, fairness table, ranked queue plan, and recent decisions."""
    ts = now_ts or now()
    policy, tariff, tariff_tier, node_states, active_jobs, queued_jobs, service_usage = _cluster_snapshot(db, ts)
    current_rate, _, _ = rate_at_timestamp(ts, tariff)
    ranked = _rank_queued_jobs(queued_jobs, node_states, service_usage, policy, tariff_tier, ts)

    recent_decisions = db.scalars(
        select(SchedulerDecision)
        .order_by(desc(SchedulerDecision.recorded_at), desc(SchedulerDecision.id))
        .limit(max(1, min(decision_limit, 200)))
    ).all()

    nodes_out = []
    for st in node_states.values():
        nodes_out.append({
            'node_id': st.node.id,
            'name': st.node.name,
            'mode': st.node.mode,
            'online': st.online,
            'maintenance': st.maintenance,
            'ready_slot': st.ready_slot,
            'default_runtime': st.node.default_runtime,
            'gpu_count': st.gpu_count,
            'avg_util_pct': st.avg_util_pct,
            'max_util_pct': st.max_util_pct,
            'max_temp_c': st.max_temp_c,
            'vram_used_pct': st.vram_used_pct,
            'free_vram_mb': st.free_vram_mb,
            'total_vram_mb': st.total_vram_mb,
            'power_w': st.power_w,
            'comfyui_running': st.comfyui_running,
            'comfyui_pending': st.comfyui_pending,
            'external_gpu_vram_mb': st.external_gpu_vram_mb,
            'warm_models': sorted(st.warm_models),
            'health_score': st.health_score,
            'thermal_hard_blocked': st.thermal_hard_blocked,
            'vram_pressure_high': st.vram_pressure_high,
            'health_notes': st.health_notes,
            'active_holders': [
                {'id': h.id, 'name': h.name, 'source': h.source, 'model': h.model, 'status': h.status}
                for h in st.holders
            ],
        })

    services_out = [
        {
            'source': u.source,
            'weight': u.weight,
            'active_jobs': u.active_jobs,
            'queued_jobs': u.queued_jobs,
            'recent_completed_jobs': u.recent_completed_jobs,
            'recent_compute_s': u.recent_compute_s,
            'weighted_load': u.weighted_load,
            'fairness_pressure': u.fairness_pressure,
        }
        for u in sorted(service_usage.values(), key=lambda x: (-x.active_jobs, -x.queued_jobs, -x.recent_compute_s, x.source))
    ]

    queue_plan = []
    for idx, item in enumerate(ranked, start=1):
        j: Job = item['job']
        best: CandidateEval | None = item['best_eval']
        created_at = _ensure_utc(j.created_at) or ts
        queue_plan.append({
            'rank': idx,
            'job_id': j.id,
            'name': j.name,
            'source': j.source,
            'model': j.model,
            'effective_model': best.effective_model if best else j.model,
            'used_fallback': bool(best and best.used_fallback),
            'priority': j.priority,
            'sla_class': get_job_sla_class(j.parameters),
            'wait_seconds': round(max(0.0, (ts - created_at).total_seconds()), 1),
            'deadline_seconds': (j.parameters or {}).get('deadline_seconds'),
            'decision_action': item['decision_action'],
            'recommended_node_id': best.node_state.node.id if best else None,
            'recommended_node_name': best.node_state.node.name if best else None,
            'warm_hit': bool(best and best.warm_hit),
            'score': item['score'],
            'breakdown': best.breakdown if best else {},
            'blockers': (list(best.blockers) + list(best.guard_blockers)) if best else ['no_compatible_node'],
        })

    return {
        'generated_at': ts.isoformat(),
        'policy': serialize_policy(policy),
        'cluster': {
            'total_nodes': len(nodes_out),
            'online_nodes': sum(1 for n in nodes_out if n['online'] and not n['maintenance']),
            'ready_nodes': sum(1 for n in nodes_out if n['ready_slot']),
            'active_jobs': len(active_jobs),
            'queued_jobs': len(queued_jobs),
            'active_services': len(services_out),
            'tariff_tier': tariff_tier,
            'current_rate': round(current_rate, 4),
            'currency': tariff.currency,
        },
        'nodes': nodes_out,
        'services': services_out,
        'queue_plan': queue_plan,
        'recent_decisions': [_encode(d) for d in recent_decisions],
    }
