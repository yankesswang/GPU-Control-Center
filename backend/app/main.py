import asyncio
import hmac
import os
from contextlib import asynccontextmanager, suppress
from datetime import date, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, or_, func, inspect, select, text
from sqlalchemy.dialects.postgresql import insert
from .db import Session, engine
from .models import ElectricityBill, Event, HardwareEvent, Job, McpToken, Node, SchedulerDecision, SchedulerPolicy, now
from .energy import compute_energy_analytics, export_energy_csv, get_or_create_tariff, record_power_sample
from .hardware_history import compute_hardware_analytics, export_hardware_csv, record_hardware_sample
from .scheduler import (
    POLICY_PRESETS,
    build_scheduler_state,
    check_admission,
    explain_job_scheduling,
    get_or_create_policy,
    select_job_for_claim,
    serialize_policy,
    simulate_dispatch,
)
from .connections import AddConnectionInput, ConnectionInput, discover_services
from .mcp_auth import (
    PROMPT_CATALOG,
    RESOURCE_CATALOG,
    TOOL_CATALOG,
    McpRole,
    generate_token,
    hash_token,
    sanitize_source_tag,
    serialize_token,
)
from .host_auth import (
    check_rate_limit,
    clear_auth_failures,
    get_node_unlock_expiry,
    hash_password,
    issue_unlock,
    record_auth_failure,
    revoke_unlock,
    validate_new_password,
    verify_password,
)
from .remote_connections import supervise_remote_connections
from sqlalchemy.exc import IntegrityError

ACTIVE = ('running', 'cancelling', 'unknown')
MODELS = [{'id': 'demo.progress', 'name': '排程驗證工作', 'runtime': 'demo',
           'description': '模擬步驟與進度，不執行 AI 推論。', 'capability': 'demo.progress'}]


def record(db, job, message):
    db.add(Event(job_id=job.id, message=message))


def encode(obj):
    return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}


def node_dict(node, request: Request | None = None):
    data = encode(node)
    data.pop('auth_password_hash', None)
    data.pop('auth_version', None)
    expires_at = get_node_unlock_expiry(request, node)
    data['auth_required'] = bool(node.auth_password_hash)
    data['auth_unlocked'] = expires_at is not None
    data['auth_unlock_expires_at'] = expires_at
    data['status'] = 'offline' if node.last_seen < now() - timedelta(seconds=20) else ('maintenance' if node.maintenance else 'online')
    if node.connection_host and not any(node.metrics.get(p, {}).get('status') == 'online' for p in ('lmstudio', 'ollama', 'comfyui')):
        data['status'] = 'offline'
    return data


def queue_order():
    effective = Job.priority + func.extract('epoch', now() - Job.created_at) / 30
    return effective.desc(), Job.created_at, Job.id


def reconcile():
    with Session.begin() as db:
        jobs = db.scalars(select(Job).where(Job.status.in_(('running', 'cancelling')), Job.lease_until < now()).with_for_update(skip_locked=True)).all()
        for job in jobs:
            job.status = 'unknown'
            job.error = '執行租約逾時，等待 Agent 確認；不會自動重新派送。'
            record(db, job, job.error)


async def sweep():
    while True:
        await asyncio.sleep(5)
        try:
            await asyncio.to_thread(reconcile)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Lease reconciliation failed')


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(sweep())
    remote_task = asyncio.create_task(supervise_remote_connections())
    yield
    remote_task.cancel()
    with suppress(asyncio.CancelledError):
        await remote_task
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    engine.dispose()


app = FastAPI(title='GPU Control Center', version='0.1.0', lifespan=lifespan)


def agent_auth(x_agent_token: str = Header(default='')):
    expected = os.getenv('AGENT_TOKEN', '')
    if not expected or not hmac.compare_digest(expected, x_agent_token):
        raise HTTPException(401, 'Invalid agent token')


class JobInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(default='console', min_length=1, max_length=80)
    model: str = Field(default='demo.progress', min_length=1, max_length=100)
    priority: int = Field(default=0, ge=0, le=10)
    steps: int = Field(default=20, ge=5, le=120)
    prompt: str = Field(default='', max_length=32000)
    max_tokens: int = Field(default=256, ge=1, le=2048)
    sla_class: Literal['realtime', 'interactive', 'standard', 'batch', 'background'] | None = None
    deadline_seconds: int | None = Field(default=None, ge=5, le=86400)
    preferred_node_id: str | None = Field(default=None, max_length=100)
    fallback_models: list[str] | None = Field(default=None, max_length=5)
    allow_defer: bool | None = None


class SchedulerPolicyUpdate(BaseModel):
    mode: Literal['balanced', 'latency_first', 'fair_share', 'throughput_warm', 'thermal_eco'] = 'balanced'
    apply_preset: bool = False
    weight_priority: float = Field(default=1.0, ge=0.0, le=10.0)
    weight_aging: float = Field(default=1.0, ge=0.0, le=10.0)
    weight_deadline: float = Field(default=2.5, ge=0.0, le=10.0)
    weight_fairness: float = Field(default=2.2, ge=0.0, le=10.0)
    weight_warm_affinity: float = Field(default=2.0, ge=0.0, le=10.0)
    weight_node_health: float = Field(default=2.5, ge=0.0, le=10.0)
    weight_sjf: float = Field(default=0.8, ge=0.0, le=10.0)
    aging_interval_s: float = Field(default=30.0, ge=5.0, le=600.0)
    fairness_window_s: int = Field(default=300, ge=30, le=3600)
    max_queued_per_source: int = Field(default=0, ge=0, le=500)
    source_weights: dict[str, float] = Field(default_factory=dict)
    thermal_soft_c: float = Field(default=80.0, ge=50.0, le=95.0)
    thermal_hard_c: float = Field(default=88.0, ge=55.0, le=100.0)
    vram_guard_pct: float = Field(default=94.0, ge=50.0, le=100.0)
    comfyui_busy_penalty: float = Field(default=2.0, ge=0.0, le=10.0)
    defer_batch_on_peak_tariff: bool = False
    route_reservation_s: int = Field(default=8, ge=0, le=60)
    routing_margin: float = Field(default=1.5, ge=0.1, le=20.0)

    @model_validator(mode='after')
    def validate_policy(self):
        if self.thermal_hard_c <= self.thermal_soft_c:
            raise ValueError('thermal_hard_c must be greater than thermal_soft_c')
        cleaned: dict[str, float] = {}
        for k, v in (self.source_weights or {}).items():
            key = str(k).strip()[:80]
            if not key:
                continue
            val = float(v)
            if val < 0.1 or val > 10.0:
                raise ValueError('source_weights values must be between 0.1 and 10.0')
            cleaned[key] = round(val, 2)
        if len(cleaned) > 100:
            raise ValueError('source_weights supports at most 100 services')
        self.source_weights = cleaned
        return self


class SchedulerSimulateInput(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    source: str = Field(default='simulator', min_length=1, max_length=80)
    priority: int = Field(default=0, ge=0, le=10)
    max_tokens: int = Field(default=256, ge=1, le=2048)
    sla_class: Literal['realtime', 'interactive', 'standard', 'batch', 'background'] = 'standard'
    deadline_seconds: int | None = Field(default=None, ge=5, le=86400)
    preferred_node_id: str | None = Field(default=None, max_length=100)
    fallback_models: list[str] | None = Field(default=None, max_length=5)
    allow_defer: bool | None = None


class ModelSpec(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    runtime: Literal['ollama', 'lmstudio', 'comfyui']
    model_type: str = Field(default='llm', max_length=30)
    runtime_model: str = Field(min_length=1, max_length=90)
    capability: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=500)


class Heartbeat(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    mode: Literal['demo', 'host']
    capabilities: list[str] = Field(default_factory=list, max_length=200)
    model_catalog: list[ModelSpec] = Field(default_factory=list, max_length=200)
    metrics: dict = Field(default_factory=dict)


class Maintenance(BaseModel):
    enabled: bool


class RuntimePreference(BaseModel):
    runtime: Literal['lmstudio', 'ollama']


class ModelAliasUpdate(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    alias: str = Field(default='', max_length=100)


class HiddenModelsUpdate(BaseModel):
    hide: list[str] = Field(default_factory=list, max_length=200)
    unhide: list[str] = Field(default_factory=list, max_length=200)


class ModelAction(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    action: Literal['load', 'unload']
    gpu_indices: list[int] | None = Field(default=None, max_length=32)
    replace: bool = False


class HostUnlockInput(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class HostPasswordUpdate(BaseModel):
    current_password: str | None = Field(default=None, max_length=128)
    new_password: str | None = Field(default=None, max_length=128)


class Report(BaseModel):
    node_id: str
    attempt_id: str
    progress: float = Field(ge=0, le=100)
    status: Literal['running', 'completed', 'cancelled', 'failed', 'unknown'] = 'running'
    message: str | None = Field(default=None, max_length=2000)
    result: dict | None = None
    runtime_state: dict | None = None


class TariffUpdate(BaseModel):
    currency: Literal['TWD', 'USD', 'EUR', 'JPY', 'CNY'] = 'TWD'
    tariff_mode: Literal['flat', 'tou_tw', 'tou_custom'] = 'tou_tw'
    flat_rate: float = Field(default=3.85, ge=0, le=1000)
    peak_rate: float = Field(default=5.06, ge=0, le=1000)
    off_peak_rate: float = Field(default=2.06, ge=0, le=1000)
    non_summer_peak_rate: float = Field(default=4.86, ge=0, le=1000)
    non_summer_off_peak_rate: float = Field(default=1.99, ge=0, le=1000)
    peak_start_hour: int = Field(default=9, ge=0, le=23)
    peak_end_hour: int = Field(default=24, ge=1, le=24)
    weekend_off_peak: bool = True
    summer_months: list[int] = Field(default_factory=lambda: [6, 7, 8, 9], max_length=12)
    timezone_offset_hours: float = Field(default=8.0, ge=-12.0, le=14.0)
    pue_factor: float = Field(default=1.12, ge=1.0, le=3.0)
    base_host_watts: float = Field(default=85.0, ge=0.0, le=5000.0)
    co2_kg_per_kwh: float = Field(default=0.494, ge=0.0, le=5.0)
    monthly_budget: float | None = Field(default=3000.0, ge=0.0, le=10000000.0)

    @model_validator(mode='after')
    def check_months(self):
        cleaned = sorted({int(m) for m in self.summer_months if 1 <= int(m) <= 12})
        if len(cleaned) != len(self.summer_months):
            raise ValueError('summer_months must contain unique integers between 1 and 12')
        self.summer_months = cleaned
        return self


class BillInput(BaseModel):
    billing_date: str = Field(min_length=10, max_length=10)
    period_start: str = Field(min_length=10, max_length=10)
    period_end: str = Field(min_length=10, max_length=10)
    label: str = Field(min_length=1, max_length=120)
    category: Literal['utility_bill', 'smart_plug', 'meter_reading', 'other'] = 'utility_bill'
    node_id: str | None = Field(default=None, max_length=100)
    kwh: float = Field(gt=0, le=10000000)
    cost: float = Field(ge=0, le=100000000)
    currency: Literal['TWD', 'USD', 'EUR', 'JPY', 'CNY'] = 'TWD'
    peak_kwh: float | None = Field(default=None, ge=0, le=10000000)
    off_peak_kwh: float | None = Field(default=None, ge=0, le=10000000)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode='after')
    def validate_dates(self):
        try:
            date.fromisoformat(self.billing_date)
            d_start = date.fromisoformat(self.period_start)
            d_end = date.fromisoformat(self.period_end)
        except ValueError as exc:
            raise ValueError('Dates must follow YYYY-MM-DD format') from exc
        if d_end < d_start:
            raise ValueError('period_end must be on or after period_start')
        self.label = self.label.strip()
        if not self.label:
            raise ValueError('label cannot be blank')
        if self.node_id is not None:
            self.node_id = self.node_id.strip() or None
        if self.notes is not None:
            self.notes = self.notes.strip() or None
        return self


class HardwareEventInput(BaseModel):
    node_id: str = Field(min_length=1, max_length=100)
    gpu_index: int | None = Field(default=None, ge=0, le=32)
    severity: Literal['info', 'warning', 'critical'] = 'info'
    event_type: str = Field(default='manual_note', min_length=1, max_length=40)
    message: str = Field(min_length=1, max_length=1000)


class HardwareEventAck(BaseModel):
    acknowledged: bool = True


class McpTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    source_tag: str | None = Field(default=None, max_length=80)
    role: McpRole = 'infer'
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode='after')
    def clean_fields(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError('name cannot be blank')
        if self.notes is not None:
            self.notes = self.notes.strip() or None
        self.source_tag = sanitize_source_tag(self.name, self.source_tag)
        return self


class McpTokenVerify(BaseModel):
    token: str = Field(default='', max_length=256)


@app.get('/health')
def health():
    with Session() as db:
        db.execute(text('SELECT 1'))
    return {'status': 'ok', 'database': 'ok'}


@app.get('/api/models')
def models():
    with Session() as db:
        catalog = {}
        if os.getenv('ENABLE_DEMO', 'true').lower() == 'true':
            catalog['demo.progress'] = {**MODELS[0], 'available': True, 'nodes': []}
        for node in db.scalars(select(Node)):
            aliases = node.model_aliases or {}
            hidden = set(node.hidden_models or [])
            for spec in node.model_catalog:
                item = catalog.setdefault(spec['id'], {**spec, 'available': False, 'nodes': [], 'hidden': True})
                if spec['id'] not in hidden:
                    item['hidden'] = False
                if aliases.get(spec['id']) and not item.get('display_name'):
                    item['display_name'] = aliases[spec['id']]
                if not node.maintenance and node.last_seen >= now() - timedelta(seconds=20) and spec['id'] in node.capabilities:
                    item['available'] = True
                    item['nodes'].append(node.id)
        return list(catalog.values())


@app.get('/api/nodes')
def nodes(request: Request):
    with Session() as db:
        return [node_dict(n, request) for n in db.scalars(select(Node).order_by(Node.name))
                if n.mode != 'demo' or os.getenv('ENABLE_DEMO', 'true').lower() == 'true']


@app.post('/api/connections/test')
async def test_connection(body: ConnectionInput):
    return await discover_services(body.url)


@app.post('/api/connections', status_code=201)
async def add_connection(body: AddConnectionInput, request: Request):
    discovered = await discover_services(body.url)
    if not discovered['ok']:
        raise HTTPException(422, discovered['message'])
    services = {s['provider']: s['url'] for s in discovered['services']}
    try:
        with Session.begin() as db:
            # The normalized host, independent of an optional explicit port,
            # represents one scheduler slot even if it exposes several services.
            from urllib.parse import urlsplit
            host = urlsplit(body.url).hostname.lower()
            if db.get(Node, body.name) or db.scalar(select(Node.id).where(Node.connection_host == host)):
                raise HTTPException(409, '此主機名稱或位址已加入。')
            password_hash = hash_password(body.password) if body.password else None
            node = Node(id=body.name, name=body.name, mode='host', connection_host=host,
                        connection_services=services,
                        auth_password_hash=password_hash,
                        auth_version=1 if password_hash else 0,
                        default_runtime='ollama' if 'ollama' in services and 'lmstudio' not in services else 'lmstudio',
                        capabilities=[], model_catalog=[], metrics={'remote': True, 'simulated': False, 'gpus': []})
            db.add(node)
            db.flush()
            return node_dict(node, request)
    except IntegrityError:
        raise HTTPException(409, '此主機名稱或位址已加入。')


@app.post('/api/nodes/{node_id}/auth/unlock')
def unlock_node(node_id: str, body: HostUnlockInput, request: Request, response: Response):
    with Session() as db:
        node = db.get(Node, node_id)
        if not node:
            raise HTTPException(404, 'Node not found')
        if not node.auth_password_hash:
            return {'unlocked': True, 'auth_required': False, 'expires_at': None}
        check_rate_limit(request, node_id)
        if not verify_password(body.password, node.auth_password_hash):
            record_auth_failure(request, node_id)
            raise HTTPException(401, '主機操作密碼錯誤。')
        clear_auth_failures(request, node_id)
        token, expires_at, ttl = issue_unlock(request, response, node.id, node.auth_version)
        return {'unlocked': True, 'auth_required': True, 'expires_at': expires_at, 'ttl_seconds': ttl, 'token': token}


@app.post('/api/nodes/{node_id}/auth/lock')
def lock_node(node_id: str, request: Request, response: Response):
    with Session() as db:
        node = db.get(Node, node_id)
        if not node:
            raise HTTPException(404, 'Node not found')
        revoke_unlock(request, response, node.id)
        return {'unlocked': False, 'auth_required': bool(node.auth_password_hash), 'expires_at': None}


@app.patch('/api/nodes/{node_id}/auth/password')
def update_node_password(node_id: str, body: HostPasswordUpdate, request: Request, response: Response):
    new_pw = body.new_password if body.new_password else None
    if new_pw is not None:
        try:
            validate_new_password(new_pw)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    with Session.begin() as db:
        node = db.get(Node, node_id, with_for_update=True)
        if not node:
            raise HTTPException(404, 'Node not found')
        if node.mode == 'demo':
            raise HTTPException(422, '模擬主機不支援操作密碼設定。')
        if node.auth_password_hash:
            check_rate_limit(request, node_id)
            if not body.current_password or not verify_password(body.current_password, node.auth_password_hash):
                record_auth_failure(request, node_id)
                raise HTTPException(401, '目前密碼錯誤。')
            clear_auth_failures(request, node_id)
        node.auth_password_hash = hash_password(new_pw) if new_pw is not None else None
        node.auth_version = (node.auth_version or 0) + 1
        revoke_unlock(request, response, node.id)
        return node_dict(node, request)


@app.patch('/api/nodes/{node_id}/maintenance')
def maintenance(node_id: str, body: Maintenance, request: Request):
    with Session.begin() as db:
        node = db.get(Node, node_id, with_for_update=True)
        if not node:
            raise HTTPException(404, 'Node not found')
        changed = node.maintenance != body.enabled
        node.maintenance = body.enabled
        if changed:
            db.add(HardwareEvent(
                recorded_at=now(),
                node_id=node.id,
                node_name=node.name,
                gpu_index=None,
                severity='info',
                event_type='maintenance_on' if body.enabled else 'maintenance_off',
                message=f"主機 {node.name} 已{'進入維護模式（暫停接單）' if body.enabled else '解除維護模式（恢復接單）'}。",
                acknowledged=True,
                simulated=node.mode == 'demo',
            ))
        return node_dict(node, request)


@app.patch('/api/nodes/{node_id}/default-runtime')
def default_runtime(node_id: str, body: RuntimePreference, request: Request):
    with Session.begin() as db:
        node = db.get(Node, node_id, with_for_update=True)
        if not node:
            raise HTTPException(404, 'Node not found')
        node.default_runtime = body.runtime
        return node_dict(node, request)


@app.patch('/api/nodes/{node_id}/model-aliases')
def update_model_alias(node_id: str, body: ModelAliasUpdate, request: Request):
    provider = body.model.split(':', 1)[0] if ':' in body.model else ''
    if provider not in ('ollama', 'lmstudio', 'comfyui') or not body.model.split(':', 1)[1].strip():
        raise HTTPException(422, 'Unsupported model identifier')
    with Session.begin() as db:
        node = db.get(Node, node_id, with_for_update=True)
        if not node:
            raise HTTPException(404, 'Node not found')
        aliases = dict(node.model_aliases or {})
        cleaned = body.alias.strip()
        if cleaned:
            aliases[body.model] = cleaned
        else:
            aliases.pop(body.model, None)
        node.model_aliases = aliases
        return node_dict(node, request)


@app.patch('/api/nodes/{node_id}/hidden-models')
def update_hidden_models(node_id: str, body: HiddenModelsUpdate, request: Request):
    def valid_model_id(mid: str) -> bool:
        if not isinstance(mid, str) or len(mid) > 100 or ':' not in mid:
            return False
        provider, rest = mid.split(':', 1)
        return provider in ('ollama', 'lmstudio', 'comfyui') and bool(rest.strip())

    if any(not valid_model_id(m) for m in body.hide + body.unhide):
        raise HTTPException(422, 'Unsupported model identifier')
    with Session.begin() as db:
        node = db.get(Node, node_id, with_for_update=True)
        if not node:
            raise HTTPException(404, 'Node not found')
        current = set(node.hidden_models or [])
        current.update(body.hide)
        current.difference_update(body.unhide)
        node.hidden_models = sorted(current)
        return node_dict(node, request)


@app.get('/api/overview')
def overview():
    with Session() as db:
        counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        online = select(func.count()).select_from(Node).where(Node.last_seen >= now() - timedelta(seconds=20), Node.maintenance.is_(False))
        if os.getenv('ENABLE_DEMO', 'true').lower() != 'true':
            online = online.where(Node.mode != 'demo')
        return {'jobs': counts, 'total': sum(counts.values()), 'online_nodes': db.scalar(online)}


@app.post('/api/nodes/{node_id}/model-actions', status_code=201)
def model_action(node_id: str, body: ModelAction, request: Request):
    with Session.begin() as db:
        node = db.get(Node, node_id, with_for_update=True)
        if not node:
            raise HTTPException(404, 'Node not found')
        if node.auth_password_hash and get_node_unlock_expiry(request, node) is None:
            raise HTTPException(
                403,
                '此主機已啟用操作密碼保護，請先驗證密碼以解鎖模型操作。',
                headers={'X-Host-Auth-Required': 'true'},
            )
        provider = body.model.split(':', 1)[0]
        if provider not in ('ollama', 'lmstudio', 'comfyui'):
            raise HTTPException(422, 'Unsupported runtime')
        if node.mode != 'host' or provider + '.model_control' not in node.capabilities:
            raise HTTPException(409, '請更新主機 Agent 以支援模型載入與卸載。')
        if node.maintenance or node.last_seen < now() - timedelta(seconds=20):
            raise HTTPException(409, '主機目前離線或停止接單，無法操作模型。')
        if provider == 'comfyui':
            if node.metrics.get('comfyui', {}).get('status') != 'online':
                raise HTTPException(409, '尚未連線至 ComfyUI 服務，無法操作模型。')
            installed = {m['id'] for m in node.model_catalog if m.get('runtime') == 'comfyui'}
            if body.action == 'load' and body.model not in installed:
                raise HTTPException(422, '請選擇此主機 ComfyUI 可載入的模型。')
            if body.action == 'unload' and body.model != 'comfyui:*' and body.model not in installed:
                raise HTTPException(422, '請選擇有效的 ComfyUI 模型或卸載全部快取。')
        else:
            if node.metrics.get(provider, {}).get('loaded_models_status') != 'online':
                raise HTTPException(409, '尚未取得執行器狀態，請等待 Agent 更新。')
            loaded = node.metrics.get(provider, {}).get('loaded_models') or []
            installed = {m['id'] for m in node.model_catalog}
            resident = {provider + ':' + m['name'] for m in loaded}
            if body.model not in (installed if body.action == 'load' else installed | resident):
                raise HTTPException(422, '請選擇此主機已安裝或已載入的模型。')
        normalized_gpus = None
        if body.gpu_indices is not None:
            if body.action != 'load':
                raise HTTPException(422, '只有載入模型時可以指定 GPU。')
            if provider == 'ollama':
                raise HTTPException(422, 'Ollama 目前的連線方式不支援逐模型指定 GPU。')
            if provider == 'comfyui':
                if node.metrics.get('comfyui', {}).get('gpu_placement_supported') is False:
                    raise HTTPException(409, '此 ComfyUI 服務不支援指定目標 GPU。')
                if len(body.gpu_indices) != 1:
                    raise HTTPException(422, 'ComfyUI 單一模型僅支援指定一張 GPU。')
            if provider == 'lmstudio' and node.metrics.get('lmstudio', {}).get('gpu_placement_supported') is False:
                raise HTTPException(409, 'LM Studio SDK 尚未就緒，暫時無法指定 GPU 載入。')
            if not body.gpu_indices or any(i < 0 for i in body.gpu_indices) or len(set(body.gpu_indices)) != len(body.gpu_indices):
                raise HTTPException(422, '請選擇有效的 GPU 編號。')
            host_gpus = {g.get('index') for g in node.metrics.get('gpus', []) if isinstance(g.get('index'), int)}
            if not host_gpus or any(i not in host_gpus for i in body.gpu_indices):
                raise HTTPException(422, '指定的 GPU 編號不在此主機的可用裝置清單中。')
            normalized_gpus = sorted(body.gpu_indices)
        if body.replace and (body.action != 'load' or provider not in ('lmstudio', 'comfyui')):
            raise HTTPException(422, '只有 LM Studio 或 ComfyUI 載入模型時可以切換配置。')
        pending = db.scalars(select(Job).where(or_(Job.node_id == node_id, Job.parameters['target_node_id'].as_string() == node_id), Job.status.in_(('queued', *ACTIVE)))).all()
        if any(j.status in ACTIVE or j.parameters.get('model_action') for j in pending):
            raise HTTPException(409, '主機有執行中或待確認的工作，請等待完成後操作模型。')
        raw_short = 'ComfyUI 快取' if body.model == 'comfyui:*' else body.model.split(':', 1)[1]
        short_model = (node.model_aliases or {}).get(body.model) or raw_short
        gpu_suffix = f"（{'、'.join(f'GPU {i:02d}' for i in normalized_gpus)}）" if normalized_gpus is not None else ('（自動分配）' if body.replace and provider == 'lmstudio' else '')
        job_title = f"{'切換 GPU 載入' if (body.replace and provider == 'lmstudio') else ('載入' if body.action == 'load' else '卸載')} {short_model}{gpu_suffix}"
        params = {'model_action': body.action, 'target_node_id': node_id}
        if normalized_gpus is not None:
            params['gpu_indices'] = normalized_gpus
        if body.replace:
            params['replace'] = True
        job = Job(name=job_title[:120],
                  source='model-control', model=body.model, priority=10,
                  parameters=params)
        db.add(job)
        db.flush()
        record(db, job, f'已請求模型{body.action}{gpu_suffix}，等待指定主機 Agent。')
        return encode(job)


@app.get('/api/jobs')
def jobs(limit: int = 100, status: str | None = None, source: str | None = None):
    with Session() as db:
        stmt = select(Job)
        if status:
            stmt = stmt.where(Job.status == status)
        if source:
            stmt = stmt.where(Job.source == source)
        stmt = stmt.order_by(Job.created_at.desc()).limit(max(1, min(limit, 500)))
        return [encode(j) for j in db.scalars(stmt)]


@app.post('/api/jobs', status_code=201)
def create_job(body: JobInput):
    catalog = {m['id']: m for m in models()}
    if body.model not in catalog:
        raise HTTPException(422, 'Unknown model; select one from /api/models')
    if catalog[body.model].get('model_type', 'llm') != 'llm':
        raise HTTPException(422, '此模型不支援文字推論，僅提供載入與卸載。')
    if body.model != 'demo.progress' and not body.prompt.strip():
        raise HTTPException(422, 'A prompt is required for real inference')
    cleaned_fallbacks: list[str] = []
    if body.fallback_models:
        for fb in body.fallback_models:
            fb_id = fb.strip()
            if not fb_id or fb_id == body.model or fb_id in cleaned_fallbacks:
                continue
            if fb_id not in catalog:
                raise HTTPException(422, f'Unknown fallback model: {fb_id}')
            if catalog[fb_id].get('model_type', 'llm') != 'llm':
                raise HTTPException(422, f'備援模型 {fb_id} 不支援文字推論。')
            cleaned_fallbacks.append(fb_id)

    rejection_reason: str | None = None
    with Session.begin() as db:
        allowed, reason = check_admission(
            db,
            source=body.source,
            model=body.model,
            sla_class=body.sla_class or 'standard',
            job_name=body.name,
        )
        if not allowed:
            rejection_reason = reason
        else:
            fields = body.model_dump(
                exclude={
                    'prompt',
                    'max_tokens',
                    'sla_class',
                    'deadline_seconds',
                    'preferred_node_id',
                    'fallback_models',
                    'allow_defer',
                }
            )
            params: dict = {'prompt': body.prompt, 'max_tokens': body.max_tokens}
            if body.sla_class is not None:
                params['sla_class'] = body.sla_class
            if body.deadline_seconds is not None:
                params['deadline_seconds'] = body.deadline_seconds
            if body.preferred_node_id and body.preferred_node_id.strip():
                params['preferred_node_id'] = body.preferred_node_id.strip()
            if cleaned_fallbacks:
                params['fallback_models'] = cleaned_fallbacks
            if body.allow_defer is not None:
                params['allow_defer'] = body.allow_defer
            job = Job(**fields, parameters=params)
            db.add(job)
            db.flush()
            sla_note = f'（SLA={body.sla_class}）' if body.sla_class else ''
            record(db, job, f'任務已建立{sla_note}，交由動態排程引擎分配主機。')
            return encode(job)
    raise HTTPException(429, rejection_reason or 'Queue admission quota exceeded for this service')


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    with Session() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, 'Job not found')
        return {**encode(job), 'events': [encode(e) for e in db.scalars(select(Event).where(Event.job_id == job_id).order_by(Event.id))]}


@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str):
    with Session.begin() as db:
        job = db.get(Job, job_id, with_for_update=True)
        if not job:
            raise HTTPException(404, 'Job not found')
        if job.status == 'queued':
            job.cancel_requested = True
            job.status = 'cancelled'
            job.finished_at = now()
            record(db, job, '已取消排隊任務。')
        elif job.status in ('running', 'unknown'):
            if job.parameters.get('model_action'):
                raise HTTPException(409, '模型操作已送出，必須等待執行器確認完成。')
            job.cancel_requested = True
            job.status = 'cancelling'
            record(db, job, '已請求取消，等待 Agent 確認停止。')
        return encode(job)


@app.get('/api/jobs/{job_id}/explain')
def explain_wait(job_id: str):
    """A snapshot of dynamic scheduling scores and constraints, not an execution time guarantee."""
    with Session.begin() as db:
        result = explain_job_scheduling(db, job_id)
        if result is None:
            raise HTTPException(404, 'Job not found')
        return result


@app.post('/api/jobs/{job_id}/retry', status_code=201)
def retry(job_id: str):
    with Session.begin() as db:
        old = db.get(Job, job_id, with_for_update=True)
        if not old:
            raise HTTPException(404, 'Job not found')
        if old.status not in ('failed', 'cancelled'):
            raise HTTPException(409, 'Only confirmed failed or cancelled jobs may be retried')
        if old.parameters.get('model_action'):
            raise HTTPException(409, '請從模型管理重新提交操作，以重新檢查主機狀態。')
        job = Job(name=old.name, source=old.source, model=old.model, priority=old.priority, steps=old.steps, parameters=dict(old.parameters))
        db.add(job)
        db.flush()
        record(db, job, f'重新建立自任務 {old.id}。')
        return encode(job)


@app.put('/api/agent/nodes/{node_id}', dependencies=[Depends(agent_auth)])
def heartbeat(node_id: str, body: Heartbeat):
    if len(node_id) > 100 or not node_id:
        raise HTTPException(422, 'Invalid node ID')
    with Session.begin() as db:
        existing = db.get(Node, node_id, with_for_update=True)
        if existing and bool(existing.connection_host) != bool(body.metrics.get('remote')):
            raise HTTPException(409, '主機識別名稱已由另一種連線方式使用。')
        ts = now()
        values = dict(id=node_id, **body.model_dump(), last_seen=ts, maintenance=False)
        update = {k: v for k, v in values.items() if k not in ('id', 'maintenance')}
        db.execute(insert(Node).values(**values).on_conflict_do_update(index_elements=['id'], set_=update))
        record_power_sample(db, node_id=node_id, node_name=body.name, mode=body.mode, metrics=body.metrics, recorded_at=ts)
        record_hardware_sample(db, node_id=node_id, node_name=body.name, mode=body.mode, metrics=body.metrics, recorded_at=ts)
    return {'ok': True}


@app.get('/api/energy/analytics')
def energy_analytics(
    range: Literal['1h', '6h', '24h', '7d', '30d', '90d', 'all'] = '24h',
    node_id: str | None = None,
    include_simulated: bool = False,
):
    with Session.begin() as db:
        return compute_energy_analytics(db, range_key=range, node_id=node_id or None, include_simulated=include_simulated)


@app.get('/api/energy/tariff')
def get_energy_tariff():
    with Session.begin() as db:
        return encode(get_or_create_tariff(db))


@app.put('/api/energy/tariff')
def update_energy_tariff(body: TariffUpdate):
    with Session.begin() as db:
        tariff = get_or_create_tariff(db)
        for field, value in body.model_dump().items():
            setattr(tariff, field, value)
        tariff.updated_at = now()
        db.flush()
        return encode(tariff)


@app.get('/api/energy/bills')
def list_energy_bills():
    with Session() as db:
        bills = db.scalars(select(ElectricityBill).order_by(ElectricityBill.billing_date.desc(), ElectricityBill.created_at.desc())).all()
        return [encode(b) for b in bills]


@app.post('/api/energy/bills', status_code=201)
def create_energy_bill(body: BillInput):
    with Session.begin() as db:
        bill = ElectricityBill(**body.model_dump())
        db.add(bill)
        db.flush()
        return encode(bill)


@app.put('/api/energy/bills/{bill_id}')
def update_energy_bill(bill_id: str, body: BillInput):
    with Session.begin() as db:
        bill = db.get(ElectricityBill, bill_id, with_for_update=True)
        if not bill:
            raise HTTPException(404, 'Bill record not found')
        for field, value in body.model_dump().items():
            setattr(bill, field, value)
        db.flush()
        return encode(bill)


@app.delete('/api/energy/bills/{bill_id}')
def delete_energy_bill(bill_id: str):
    with Session.begin() as db:
        bill = db.get(ElectricityBill, bill_id, with_for_update=True)
        if not bill:
            raise HTTPException(404, 'Bill record not found')
        db.delete(bill)
        return {'ok': True, 'id': bill_id}


@app.get('/api/energy/export')
def export_energy(
    target: Literal['timeseries', 'bills', 'gpu'] = 'timeseries',
    range: Literal['1h', '6h', '24h', '7d', '30d', '90d', 'all'] = '24h',
    node_id: str | None = None,
    include_simulated: bool = False,
):
    with Session.begin() as db:
        csv_text, filename = export_energy_csv(
            db, target=target, range_key=range, node_id=node_id or None, include_simulated=include_simulated
        )
    return Response(
        content=csv_text.encode('utf-8'),
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@app.get('/api/hardware/analytics')
def hardware_analytics(
    range: Literal['1h', '6h', '24h', '7d', '30d', '90d', 'all'] = '24h',
    node_id: str | None = None,
    gpu_index: int | None = None,
    include_simulated: bool = False,
):
    with Session.begin() as db:
        return compute_hardware_analytics(
            db,
            range_key=range,
            node_id=node_id or None,
            gpu_index=gpu_index,
            include_simulated=include_simulated,
        )


@app.get('/api/hardware/events')
def list_hardware_events(
    limit: int = 100,
    node_id: str | None = None,
    severity: Literal['info', 'warning', 'critical'] | None = None,
    include_simulated: bool = False,
):
    with Session() as db:
        stmt = select(HardwareEvent).order_by(HardwareEvent.recorded_at.desc()).limit(max(1, min(limit, 500)))
        if node_id:
            stmt = stmt.where(HardwareEvent.node_id == node_id)
        if severity:
            stmt = stmt.where(HardwareEvent.severity == severity)
        if not include_simulated:
            stmt = stmt.where(HardwareEvent.simulated.is_(False))
        return [encode(e) for e in db.scalars(stmt).all()]


@app.post('/api/hardware/events', status_code=201)
def create_hardware_event(body: HardwareEventInput):
    with Session.begin() as db:
        node = db.get(Node, body.node_id)
        node_name = node.name if node else body.node_id
        ev = HardwareEvent(
            recorded_at=now(),
            node_id=body.node_id,
            node_name=node_name,
            gpu_index=body.gpu_index,
            severity=body.severity,
            event_type=body.event_type.strip(),
            message=body.message.strip(),
            acknowledged=body.severity == 'info',
            simulated=bool(node and node.mode == 'demo'),
        )
        db.add(ev)
        db.flush()
        return encode(ev)


@app.patch('/api/hardware/events/{event_id}/ack')
def ack_hardware_event(event_id: str, body: HardwareEventAck):
    with Session.begin() as db:
        ev = db.get(HardwareEvent, event_id, with_for_update=True)
        if not ev:
            raise HTTPException(404, 'Hardware event not found')
        ev.acknowledged = body.acknowledged
        db.flush()
        return encode(ev)


@app.post('/api/hardware/events/ack-all')
def ack_all_hardware_events(node_id: str | None = None):
    with Session.begin() as db:
        stmt = select(HardwareEvent).where(HardwareEvent.acknowledged.is_(False))
        if node_id:
            stmt = stmt.where(HardwareEvent.node_id == node_id)
        rows = db.scalars(stmt).all()
        for ev in rows:
            ev.acknowledged = True
        return {'ok': True, 'acknowledged_count': len(rows)}


@app.delete('/api/hardware/events/{event_id}')
def delete_hardware_event(event_id: str):
    with Session.begin() as db:
        ev = db.get(HardwareEvent, event_id, with_for_update=True)
        if not ev:
            raise HTTPException(404, 'Hardware event not found')
        db.delete(ev)
        return {'ok': True, 'id': event_id}


@app.get('/api/hardware/export')
def export_hardware(
    target: Literal['timeseries', 'gpus', 'events'] = 'timeseries',
    range: Literal['1h', '6h', '24h', '7d', '30d', '90d', 'all'] = '24h',
    node_id: str | None = None,
    gpu_index: int | None = None,
    include_simulated: bool = False,
):
    with Session.begin() as db:
        csv_text, filename = export_hardware_csv(
            db,
            target=target,
            range_key=range,
            node_id=node_id or None,
            gpu_index=gpu_index,
            include_simulated=include_simulated,
        )
    return Response(
        content=csv_text.encode('utf-8'),
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@app.get('/api/mcp/info')
def mcp_info():
    with Session() as db:
        tokens = db.scalars(select(McpToken)).all()
    active_tokens = sum(1 for t in tokens if t.revoked_at is None)
    total_requests = sum(int(t.request_count or 0) for t in tokens)
    static_key = bool(os.getenv('MCP_API_KEY', '').strip())
    require_auth_env = os.getenv('MCP_REQUIRE_AUTH', 'false').lower() == 'true'
    try:
        mcp_port = int(os.getenv('MCP_PORT', '8090'))
    except ValueError:
        mcp_port = 8090
    return {
        'protocol': 'streamable-http',
        'default_port': mcp_port,
        'path': '/mcp',
        'auth_enabled': active_tokens > 0 or static_key or require_auth_env,
        'auth_enforced_by_tokens': active_tokens > 0,
        'static_api_key_configured': static_key,
        'require_auth_env': require_auth_env,
        'default_role': os.getenv('MCP_DEFAULT_ROLE', 'admin'),
        'active_tokens': active_tokens,
        'total_tokens': len(tokens),
        'total_requests': total_requests,
        'tools': TOOL_CATALOG,
        'resources': RESOURCE_CATALOG,
        'prompts': PROMPT_CATALOG,
    }


@app.get('/api/mcp/tokens')
def list_mcp_tokens():
    with Session() as db:
        tokens = db.scalars(select(McpToken).order_by(McpToken.created_at.desc())).all()
        return [serialize_token(t) for t in tokens]


@app.post('/api/mcp/tokens', status_code=201)
def create_mcp_token(body: McpTokenCreate):
    raw, prefix, digest = generate_token()
    with Session.begin() as db:
        tok = McpToken(
            name=body.name,
            source_tag=body.source_tag,
            role=body.role,
            token_prefix=prefix,
            token_hash=digest,
            notes=body.notes,
        )
        db.add(tok)
        db.flush()
        return {**serialize_token(tok), 'token': raw}


@app.post('/api/mcp/tokens/{token_id}/revoke')
def revoke_mcp_token(token_id: str):
    with Session.begin() as db:
        tok = db.get(McpToken, token_id, with_for_update=True)
        if not tok:
            raise HTTPException(404, 'MCP token not found')
        if tok.revoked_at is None:
            tok.revoked_at = now()
        db.flush()
        return serialize_token(tok)


@app.delete('/api/mcp/tokens/{token_id}')
def delete_mcp_token(token_id: str):
    with Session.begin() as db:
        tok = db.get(McpToken, token_id, with_for_update=True)
        if not tok:
            raise HTTPException(404, 'MCP token not found')
        db.delete(tok)
        return {'ok': True, 'id': token_id}


@app.post('/api/mcp/tokens/verify', dependencies=[Depends(agent_auth)])
def verify_mcp_token(body: McpTokenVerify):
    raw = body.token.strip()
    with Session.begin() as db:
        active_count = db.scalar(select(func.count()).select_from(McpToken).where(McpToken.revoked_at.is_(None))) or 0
        if not raw:
            return {'valid': False, 'auth_required': active_count > 0, 'active_tokens': active_count}
        digest = hash_token(raw)
        tok = db.scalar(select(McpToken).where(McpToken.token_hash == digest).with_for_update())
        if not tok:
            return {'valid': False, 'auth_required': active_count > 0, 'reason': 'invalid'}
        if tok.revoked_at is not None:
            return {'valid': False, 'auth_required': active_count > 0, 'reason': 'revoked'}
        tok.request_count = int(tok.request_count or 0) + 1
        tok.last_used_at = now()
        return {
            'valid': True,
            'auth_required': True,
            'token_id': tok.id,
            'name': tok.name,
            'source_tag': tok.source_tag,
            'role': tok.role,
        }


@app.get('/api/scheduler/policy')
def get_scheduler_policy():
    with Session.begin() as db:
        return serialize_policy(get_or_create_policy(db))


@app.put('/api/scheduler/policy')
def update_scheduler_policy(body: SchedulerPolicyUpdate):
    with Session.begin() as db:
        policy = get_or_create_policy(db)
        data = body.model_dump(exclude={'apply_preset'})
        if body.apply_preset and body.mode in POLICY_PRESETS:
            data.update(POLICY_PRESETS[body.mode]['weights'])
        for field, value in data.items():
            setattr(policy, field, value)
        policy.updated_at = now()
        db.flush()
        return serialize_policy(policy)


@app.get('/api/scheduler/state')
def get_scheduler_state(decision_limit: int = 30):
    with Session.begin() as db:
        return build_scheduler_state(db, decision_limit=decision_limit)


@app.get('/api/scheduler/decisions')
def list_scheduler_decisions(
    limit: int = 50,
    action: Literal['dispatch', 'yield', 'defer', 'reject', 'fallback'] | None = None,
    source: str | None = None,
    node_id: str | None = None,
):
    with Session() as db:
        stmt = select(SchedulerDecision).order_by(SchedulerDecision.recorded_at.desc(), SchedulerDecision.id.desc())
        if action:
            stmt = stmt.where(SchedulerDecision.action == action)
        if source:
            stmt = stmt.where(SchedulerDecision.source == source)
        if node_id:
            stmt = stmt.where(SchedulerDecision.selected_node_id == node_id)
        stmt = stmt.limit(max(1, min(limit, 300)))
        return [encode(d) for d in db.scalars(stmt).all()]


@app.post('/api/scheduler/simulate')
def simulate_scheduler(body: SchedulerSimulateInput):
    with Session.begin() as db:
        return simulate_dispatch(
            db,
            model=body.model,
            source=body.source,
            priority=body.priority,
            max_tokens=body.max_tokens,
            sla_class=body.sla_class,
            deadline_seconds=body.deadline_seconds,
            preferred_node_id=body.preferred_node_id.strip() if body.preferred_node_id else None,
            fallback_models=body.fallback_models,
            allow_defer=body.allow_defer,
        )


@app.post('/api/agent/nodes/{node_id}/claim', dependencies=[Depends(agent_auth)])
def claim(node_id: str):
    with Session.begin() as db:
        node = db.get(Node, node_id, with_for_update=True)
        if not node or node.maintenance or node.last_seen < now() - timedelta(seconds=20):
            return {'job': None}
        if db.scalar(select(Job.id).where(Job.node_id == node_id, Job.status.in_(ACTIVE)).limit(1)):
            return {'job': None}
        picked = select_job_for_claim(db, node)
        if not picked:
            return {'job': None}
        job, dispatch_reason, decision_meta = picked
        job.status = 'running'
        job.node_id = node_id
        job.attempt_id = str(uuid4())
        job.started_at = now()
        job.lease_until = now() + timedelta(seconds=30)
        record(db, job, dispatch_reason)
        data = encode(job)
        data['scheduler_decision'] = decision_meta
        if not job.parameters.get('model_action'):
            last_control = db.scalar(select(Job).where(Job.node_id == node_id, Job.model == job.model, Job.status == 'completed', Job.parameters['model_action'].as_string().in_(('load', 'unload'))).order_by(Job.finished_at.desc(), Job.id).limit(1))
            data['keep_model_loaded'] = bool(last_control and last_control.parameters['model_action'] == 'load')
        return {'job': data}


@app.post('/api/agent/jobs/{job_id}/report', dependencies=[Depends(agent_auth)])
def report(job_id: str, body: Report):
    with Session.begin() as db:
        job = db.get(Job, job_id, with_for_update=True)
        if not job:
            raise HTTPException(404, 'Job not found')
        if job.node_id != body.node_id or job.attempt_id != body.attempt_id:
            raise HTTPException(409, 'Stale or mismatched execution attempt')
        if job.status not in ACTIVE:
            return {'status': job.status, 'terminal': True}
        if body.runtime_state is not None:
            job.runtime_state = body.runtime_state
        if job.cancel_requested and body.status == 'running':
            job.status = 'cancelling'
            job.lease_until = now() + timedelta(seconds=30)
            return {'status': 'cancelling', 'terminal': False}
        # Cancellation wins over a completion arriving after the cancellation request.
        job.status = 'cancelled' if job.cancel_requested and body.status == 'completed' else body.status
        job.progress = max(job.progress, body.progress)
        job.lease_until = now() + timedelta(seconds=30)
        job.error = body.message if job.status in ('failed', 'unknown') else None
        if job.status in ('completed', 'cancelled', 'failed'):
            job.finished_at = now()
            job.lease_until = None
            job.result = body.result if job.status == 'completed' else None
            if job.status == 'completed':
                job.progress = 100
            record(db, job, body.message or f'任務狀態：{job.status}')
        elif body.message:
            record(db, job, body.message)
        return {'status': job.status, 'terminal': job.status not in ACTIVE}
