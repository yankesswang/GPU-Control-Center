"""Supervise stateless remote workers; scheduling remains in the central API."""
import asyncio
import json
import logging
import os
import sys

from sqlalchemy import select, text

from .db import Session, engine
from .models import Node

log = logging.getLogger(__name__)


def worker_environment(node):
    env = dict(os.environ)
    # Do not forward credentials or local runtime settings to remote services.
    for key in ('LMSTUDIO_API_TOKEN', 'LMSTUDIO_URL', 'OLLAMA_URL', 'COMFYUI_URL'):
        env.pop(key, None)
    env.update(API_URL=os.getenv('REMOTE_WORKER_API_URL', 'http://127.0.0.1:8000'),
               NODE_ID=node.id, GCC_REMOTE_CONFIG=json.dumps({'name': node.name, 'services': node.connection_services}))
    for provider in ('lmstudio', 'ollama', 'comfyui'):
        env[provider.upper() + '_URL'] = node.connection_services.get(provider, 'http://127.0.0.1:1')
    return env


async def stop_worker(process):
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 20)
    except TimeoutError:
        process.kill()
        await process.wait()


async def supervise_remote_connections():
    if os.getenv('ENABLE_REMOTE_WORKERS', 'true').lower() != 'true':
        return
    workers = {}
    while True:
        try:
            # A session advisory lock allows only one API process to supervise.
            with engine.connect() as lease:
                acquired = lease.scalar(text('SELECT pg_try_advisory_lock(714289900)'))
                lease.commit()
                if not acquired:
                    await asyncio.sleep(3)
                    continue
                try:
                    while True:
                        with Session() as db:
                            nodes = db.scalars(select(Node).where(Node.connection_host.is_not(None))).all()
                        ids = {node.id for node in nodes}
                        for node_id in list(workers):
                            if node_id not in ids:
                                await stop_worker(workers.pop(node_id))
                        for node in nodes:
                            if node.id not in workers or workers[node.id].returncode is not None:
                                workers[node.id] = await asyncio.create_subprocess_exec(
                                    sys.executable, '-m', 'app.agent', '--mode', 'host', env=worker_environment(node))
                        await asyncio.sleep(2)
                finally:
                    for process in workers.values():
                        await stop_worker(process)
                    workers.clear()
                    lease.execute(text('SELECT pg_advisory_unlock(714289900)'))
                    lease.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Remote connection supervisor failed; retrying')
            await asyncio.sleep(3)
