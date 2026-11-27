import asyncio
import hmac
import os
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, inspect, select, text
from sqlalchemy.dialects.postgresql import insert
from .db import Session, engine
from .models import Event, Job, Node, now

ACTIVE = ('running', 'cancelling', 'unknown')
MODELS = [{'id': 'demo.progress', 'name': '排程驗證工作', 'runtime': 'demo',
           'description': '模擬步驟與進度，不執行 AI 推論。', 'capability': 'demo.progress'}]


def record(db, job, message):
    db.add(Event(job_id=job.id, message=message))


def encode(obj):
    return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}


def node_dict(node):
    data = encode(node)
    data['status'] = 'offline' if node.last_seen < now() - timedelta(seconds=20) else ('maintenance' if node.maintenance else 'online')
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
    yield
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


class ModelSpec(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    runtime: Literal['ollama']
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


class Report(BaseModel):
    node_id: str
    attempt_id: str
    progress: float = Field(ge=0, le=100)
    status: Literal['running', 'completed', 'cancelled', 'failed', 'unknown'] = 'running'
    message: str | None = Field(default=None, max_length=2000)
    result: dict | None = None
    runtime_state: dict | None = None


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
            for spec in node.model_catalog:
                item = catalog.setdefault(spec['id'], {**spec, 'available': False, 'nodes': []})
                if not node.maintenance and node.last_seen >= now() - timedelta(seconds=20) and spec['id'] in node.capabilities:
                    item['available'] = True
                    item['nodes'].append(node.id)
        return list(catalog.values())


@app.get('/api/nodes')
def nodes():
    with Session() as db:
        return [node_dict(n) for n in db.scalars(select(Node).order_by(Node.name))
                if n.mode != 'demo' or os.getenv('ENABLE_DEMO', 'true').lower() == 'true']


@app.patch('/api/nodes/{node_id}/maintenance')
def maintenance(node_id: str, body: Maintenance):
    with Session.begin() as db:
        node = db.get(Node, node_id, with_for_update=True)
        if not node:
            raise HTTPException(404, 'Node not found')
        node.maintenance = body.enabled
        return node_dict(node)


@app.get('/api/overview')
def overview():
    with Session() as db:
        counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        online = select(func.count()).select_from(Node).where(Node.last_seen >= now() - timedelta(seconds=20), Node.maintenance.is_(False))
        if os.getenv('ENABLE_DEMO', 'true').lower() != 'true':
            online = online.where(Node.mode != 'demo')
        return {'jobs': counts, 'total': sum(counts.values()), 'online_nodes': db.scalar(online)}


@app.get('/api/jobs')
def jobs(limit: int = 100):
    with Session() as db:
        return [encode(j) for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(max(1, min(limit, 500))))]


@app.post('/api/jobs', status_code=201)
def create_job(body: JobInput):
    catalog = {m['id']: m for m in models()}
    if body.model not in catalog:
        raise HTTPException(422, 'Unknown model; select one from /api/models')
    if body.model != 'demo.progress' and not body.prompt.strip():
        raise HTTPException(422, 'A prompt is required for real inference')
    with Session.begin() as db:
        fields = body.model_dump(exclude={'prompt', 'max_tokens'})
        job = Job(**fields, parameters={'prompt': body.prompt, 'max_tokens': body.max_tokens})
        db.add(job)
        db.flush()
        record(db, job, '任務已建立，等待符合能力的 Agent。')
        return encode(job)


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
            job.cancel_requested = True
            job.status = 'cancelling'
            record(db, job, '已請求取消，等待 Agent 確認停止。')
        return encode(job)


@app.get('/api/jobs/{job_id}/explain')
def explain_wait(job_id: str):
    """A snapshot of scheduling constraints, not an execution time guarantee."""
    with Session() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, 'Job not found')
        queued = list(db.scalars(select(Job.id).where(Job.status == 'queued').order_by(*queue_order())))
        active = db.scalars(select(Job).where(Job.status.in_(ACTIVE))).all()
        by_node = {}
        for running in active:
            by_node.setdefault(running.node_id, []).append(running)
        candidates = []
        for node in db.scalars(select(Node).order_by(Node.name)):
            if node.mode == 'demo' and os.getenv('ENABLE_DEMO', 'true').lower() != 'true':
                continue
            blockers = []
            if job.model not in node.capabilities:
                blockers.append('unsupported_capability')
            if node.last_seen < now() - timedelta(seconds=20):
                blockers.append('offline')
            if node.maintenance:
                blockers.append('maintenance')
            holders = by_node.get(node.id, [])
            if holders:
                blockers.append('unconfirmed_execution' if any(j.status == 'unknown' for j in holders) else 'slot_occupied')
            candidates.append({'node_id': node.id, 'name': node.name, 'ready': not blockers,
                               'blockers': blockers, 'holders': [{'id': j.id, 'name': j.name, 'source': j.source, 'status': j.status} for j in holders]})
        compatible = [n for n in candidates if 'unsupported_capability' not in n['blockers']]
        if job.status == 'queued':
            if not compatible:
                reason, summary = 'no_compatible_node', '尚無支援此能力的主機，請啟動或接入對應 Agent。'
            elif any(n['ready'] for n in compatible):
                reason, summary = 'awaiting_dispatch', '有符合條件的主機，等待 Agent 輪詢；依優先級與等待時間派送。'
            else:
                reason, summary = 'nodes_blocked', '符合能力的主機目前無法接單，請查看各主機的阻擋原因。'
        elif job.status == 'unknown':
            reason, summary = 'execution_unconfirmed', '原執行租約已逾時，尚未確認停止；保留主機工作槽，不會自動重派。'
        elif job.status == 'cancelling':
            reason, summary = 'awaiting_cancel_ack', '取消請求已保存，等待 Agent 確認停止後釋放工作槽。'
        elif job.status == 'running':
            reason, summary = 'executing', f'任務正在 {job.node_id} 執行。'
        else:
            reason, summary = 'terminal', '任務已結束，沒有等待排程。'
        return {'job_id': job.id, 'status': job.status, 'source': job.source,
                'reason': reason, 'summary': summary,
                'queue_rank': queued.index(job.id) + 1 if job.id in queued else None,
                'queue_size': len(queued), 'candidates': candidates,
                'policy': 'priority_with_aging',
                'note': '排序位置是目前全域快照；能力不同的主機可跳過不相容工作，並非保證開工順序或 ETA。'}


@app.post('/api/jobs/{job_id}/retry', status_code=201)
def retry(job_id: str):
    with Session.begin() as db:
        old = db.get(Job, job_id, with_for_update=True)
        if not old:
            raise HTTPException(404, 'Job not found')
        if old.status not in ('failed', 'cancelled'):
            raise HTTPException(409, 'Only confirmed failed or cancelled jobs may be retried')
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
        values = dict(id=node_id, **body.model_dump(), last_seen=now(), maintenance=False)
        update = {k: v for k, v in values.items() if k not in ('id', 'maintenance')}
        db.execute(insert(Node).values(**values).on_conflict_do_update(index_elements=['id'], set_=update))
    return {'ok': True}


@app.post('/api/agent/nodes/{node_id}/claim', dependencies=[Depends(agent_auth)])
def claim(node_id: str):
    with Session.begin() as db:
        node = db.get(Node, node_id, with_for_update=True)
        if not node or node.maintenance or node.last_seen < now() - timedelta(seconds=20):
            return {'job': None}
        if db.scalar(select(Job.id).where(Job.node_id == node_id, Job.status.in_(ACTIVE)).limit(1)):
            return {'job': None}
        # Priority aging: every 30 seconds of waiting adds one effective priority.
        job = db.scalar(select(Job).where(Job.status == 'queued', Job.model.in_(node.capabilities)).order_by(*queue_order()).with_for_update(skip_locked=True).limit(1))
        if not job:
            return {'job': None}
        job.status = 'running'
        job.node_id = node_id
        job.attempt_id = str(uuid4())
        job.started_at = now()
        job.lease_until = now() + timedelta(seconds=30)
        record(db, job, f'已派送至 {node.name}，建立獨立執行租約。')
        return {'job': encode(job)}


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
