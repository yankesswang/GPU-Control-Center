from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def now():
    return datetime.now(timezone.utc)


class Node(Base):
    __tablename__ = 'nodes'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(20))
    connection_host: Mapped[str | None] = mapped_column(String(500), nullable=True, unique=True)
    connection_services: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    auth_password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    auth_version: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    default_runtime: Mapped[str] = mapped_column(String(20), default='lmstudio', server_default='lmstudio')
    model_aliases: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    hidden_models: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    maintenance: Mapped[bool] = mapped_column(Boolean, default=False)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    model_catalog: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(100), default='demo.progress')
    priority: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=20)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    runtime_state: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    progress: Mapped[float] = mapped_column(Float, default=0)
    node_id: Mapped[str | None] = mapped_column(ForeignKey('nodes.id'), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'), index=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PowerSample(Base):
    __tablename__ = 'power_samples'
    __table_args__ = (
        Index('ix_power_samples_node_recorded', 'node_id', 'recorded_at'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(100), index=True)
    node_name: Mapped[str] = mapped_column(String(100))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    interval_s: Mapped[float] = mapped_column(Float, default=4.0)
    gpu_power_w: Mapped[float] = mapped_column(Float, default=0.0)
    gpu_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_utilization: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    per_gpu: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    active_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')


class TariffSetting(Base):
    __tablename__ = 'tariff_settings'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default='default')
    currency: Mapped[str] = mapped_column(String(8), default='TWD', server_default='TWD')
    tariff_mode: Mapped[str] = mapped_column(String(20), default='tou_tw', server_default='tou_tw')
    flat_rate: Mapped[float] = mapped_column(Float, default=3.85)
    peak_rate: Mapped[float] = mapped_column(Float, default=5.06)
    off_peak_rate: Mapped[float] = mapped_column(Float, default=2.06)
    non_summer_peak_rate: Mapped[float] = mapped_column(Float, default=4.86)
    non_summer_off_peak_rate: Mapped[float] = mapped_column(Float, default=1.99)
    peak_start_hour: Mapped[int] = mapped_column(Integer, default=9)
    peak_end_hour: Mapped[int] = mapped_column(Integer, default=24)
    weekend_off_peak: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true')
    summer_months: Mapped[list] = mapped_column(JSON, default=lambda: [6, 7, 8, 9], server_default='[6, 7, 8, 9]')
    timezone_offset_hours: Mapped[float] = mapped_column(Float, default=8.0)
    pue_factor: Mapped[float] = mapped_column(Float, default=1.12)
    base_host_watts: Mapped[float] = mapped_column(Float, default=85.0)
    co2_kg_per_kwh: Mapped[float] = mapped_column(Float, default=0.494)
    monthly_budget: Mapped[float | None] = mapped_column(Float, nullable=True, default=3000.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ElectricityBill(Base):
    __tablename__ = 'electricity_bills'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    billing_date: Mapped[str] = mapped_column(String(10), index=True)
    period_start: Mapped[str] = mapped_column(String(10))
    period_end: Mapped[str] = mapped_column(String(10))
    label: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32), default='utility_bill', server_default='utility_bill')
    node_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kwh: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default='TWD', server_default='TWD')
    peak_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    off_peak_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class McpToken(Base):
    __tablename__ = 'mcp_tokens'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(100))
    source_tag: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20), default='infer', server_default='infer')
    token_prefix: Mapped[str] = mapped_column(String(24))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default='0')
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HardwareSample(Base):
    __tablename__ = 'hardware_samples'
    __table_args__ = (
        Index('ix_hardware_samples_node_recorded', 'node_id', 'recorded_at'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(100), index=True)
    node_name: Mapped[str] = mapped_column(String(100))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    interval_s: Mapped[float] = mapped_column(Float, default=4.0)
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpu_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_avg_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    ram_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    ram_used_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    ram_total_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    gpu_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_gpu_util: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_gpu_util: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_vram_used_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_vram_total_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_gpu_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_gpu_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_power_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    per_gpu: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    runtimes_online: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    active_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')


class HardwareEvent(Base):
    __tablename__ = 'hardware_events'
    __table_args__ = (
        Index('ix_hardware_events_node_recorded', 'node_id', 'recorded_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    node_id: Mapped[str] = mapped_column(String(100), index=True)
    node_name: Mapped[str] = mapped_column(String(100))
    gpu_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(String(16), default='info', server_default='info')
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    metric_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')


class SchedulerPolicy(Base):
    __tablename__ = 'scheduler_policies'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default='default')
    mode: Mapped[str] = mapped_column(String(32), default='balanced', server_default='balanced')
    weight_priority: Mapped[float] = mapped_column(Float, default=1.0, server_default='1.0')
    weight_aging: Mapped[float] = mapped_column(Float, default=1.0, server_default='1.0')
    weight_deadline: Mapped[float] = mapped_column(Float, default=2.5, server_default='2.5')
    weight_fairness: Mapped[float] = mapped_column(Float, default=2.2, server_default='2.2')
    weight_warm_affinity: Mapped[float] = mapped_column(Float, default=2.0, server_default='2.0')
    weight_node_health: Mapped[float] = mapped_column(Float, default=2.5, server_default='2.5')
    weight_sjf: Mapped[float] = mapped_column(Float, default=0.8, server_default='0.8')
    aging_interval_s: Mapped[float] = mapped_column(Float, default=30.0, server_default='30.0')
    fairness_window_s: Mapped[int] = mapped_column(Integer, default=300, server_default='300')
    max_queued_per_source: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    source_weights: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    thermal_soft_c: Mapped[float] = mapped_column(Float, default=80.0, server_default='80.0')
    thermal_hard_c: Mapped[float] = mapped_column(Float, default=88.0, server_default='88.0')
    vram_guard_pct: Mapped[float] = mapped_column(Float, default=94.0, server_default='94.0')
    comfyui_busy_penalty: Mapped[float] = mapped_column(Float, default=2.0, server_default='2.0')
    defer_batch_on_peak_tariff: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    route_reservation_s: Mapped[int] = mapped_column(Integer, default=8, server_default='8')
    routing_margin: Mapped[float] = mapped_column(Float, default=1.5, server_default='1.5')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SchedulerDecision(Base):
    __tablename__ = 'scheduler_decisions'
    __table_args__ = (
        Index('ix_scheduler_decisions_recorded', 'recorded_at'),
        Index('ix_scheduler_decisions_source_recorded', 'source', 'recorded_at'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    action: Mapped[str] = mapped_column(String(32), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    job_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sla_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    selected_node_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    selected_node_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    policy_mode: Mapped[str] = mapped_column(String(32), default='balanced', server_default='balanced')
    reason: Mapped[str] = mapped_column(Text)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
