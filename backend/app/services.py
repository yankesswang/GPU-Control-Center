"""Host-configured service adapters. Runtime URLs never come from job input."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx
from .runtimes import ExecutionUncertain


def lmstudio_inventory(client):
    response = client.get('/api/v1/models')
    response.raise_for_status()
    models = response.json()['models']
    if not isinstance(models, list):
        raise ValueError('Invalid model inventory')
    catalog, loaded = [], []
    for model in models:
        key = model['key']
        if not isinstance(key, str) or not key or len(key) > 90:
            continue
        model_type = model['type']
        catalog.append(dict(id='lmstudio:' + key, name=key, runtime='lmstudio', runtime_model=key,
                            model_type=model_type, capability='lmstudio:' + key,
                            description=f"LM Studio · {model.get('display_name') or key} · {model_type}"))
        for instance in model['loaded_instances']:
            loaded.append(dict(name=key, instance_id=instance['id'], size_bytes=None, vram_bytes=None))
    return catalog, dict(status='online', models=len(catalog), loaded_models_status='online', loaded_models=loaded)


def control_lmstudio(client, job, report):
    model = job['model'].split(':', 1)[1]
    action = job['parameters']['model_action']
    ack = report(status='running', runtime_state={'phase': action}, message=f'LM Studio 正在{action}模型。')
    if ack.get('status') == 'cancelling':
        report(status='cancelled', message='模型操作尚未送出，已取消。')
        return
    try:
        _, before = lmstudio_inventory(client)
        existing = [m for m in before['loaded_models'] if m['name'] == model]
        if action == 'load' and not existing:
            response = client.post('/api/v1/models/load', json={'model': model, 'context_length': 2048}, timeout=180)
            response.raise_for_status()
            if response.json().get('status') != 'loaded':
                raise ExecutionUncertain('LM Studio 未確認模型已載入。')
        elif action == 'unload':
            # Unload by the server's instance ID, never by a guessed model key.
            for instance in existing:
                response = client.post('/api/v1/models/unload', json={'instance_id': instance['instance_id']}, timeout=180)
                response.raise_for_status()
        for _ in range(20):
            _, inventory = lmstudio_inventory(client)
            loaded = any(m['name'] == model for m in inventory['loaded_models'])
            if loaded == (action == 'load'):
                report(status='completed', progress=100, runtime_state={'phase': 'completed'},
                       message=f"LM Studio 模型已{'載入' if loaded else '卸載'}。",
                       result={'runtime': 'lmstudio', 'model': model, 'model_action': action, 'loaded': loaded})
                return
            time.sleep(.5)
        raise ExecutionUncertain('LM Studio 常駐狀態尚未符合操作，保留工作槽。')
    except (httpx.RequestError, ValueError, KeyError, TypeError) as exc:
        raise ExecutionUncertain('無法確認 LM Studio 模型操作結果，保留工作槽。') from exc


def generate_lmstudio(client, job, report):
    model = job['model'].split(':', 1)[1]
    ack = report(status='running', runtime_state={'phase': 'loading', 'text': ''}, message='正在連接 LM Studio。')
    if ack.get('status') == 'cancelling':
        report(status='cancelled', message='尚未送出 LM Studio 推論，已取消。')
        return
    text, thinking, chunks, tokens = '', '', 0, None
    started = last = time.monotonic()
    done, cancel = False, False
    try:
        with client.stream('POST', '/v1/chat/completions', json={
            'model': model, 'messages': [{'role': 'user', 'content': job['parameters']['prompt']}],
            'stream': True, 'stream_options': {'include_usage': True},
            'max_tokens': job['parameters'].get('max_tokens', 256),
        }, timeout=httpx.Timeout(180, connect=5)) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if time.monotonic() - started > 180:
                    raise ExecutionUncertain('LM Studio 超過執行時間，保留工作槽。')
                if not line.startswith('data:'):
                    continue
                raw = line[5:].strip()
                if raw == '[DONE]':
                    done = True
                    break
                part = json.loads(raw)
                if part.get('error'):
                    raise RuntimeError(str(part['error']))
                for choice in part.get('choices', []):
                    delta = choice.get('delta', {})
                    text += delta.get('content') or ''
                    thinking += delta.get('reasoning_content') or ''
                    if delta.get('content') or delta.get('reasoning_content'):
                        chunks += 1
                if part.get('usage'):
                    tokens = part['usage'].get('completion_tokens')
                if time.monotonic() - last > .5:
                    ack = report(status='running', runtime_state={'phase': 'generating', 'text': text, 'thinking': thinking, 'chunks_received': chunks})
                    cancel = cancel or ack.get('status') == 'cancelling'
                    last = time.monotonic()
    except (httpx.RequestError, ValueError, KeyError, TypeError) as exc:
        raise ExecutionUncertain('LM Studio 串流中斷，無法確認推論已停止。') from exc
    if not done:
        raise ExecutionUncertain('LM Studio 未回報串流完成，保留工作槽。')
    state = {'phase': 'cancelled' if cancel else 'completed', 'text': text, 'thinking': thinking}
    if tokens is not None:
        state['tokens_generated'] = tokens
    report(status='cancelled' if cancel else 'completed', progress=100, runtime_state=state,
           message='LM Studio 已確認本次生成結束。',
           result={'runtime': 'lmstudio', 'model': model, 'text': text, 'thinking': thinking,
                   'tokens_generated': tokens, 'total_duration_ms': round((time.monotonic()-started)*1000),
                   'residency_policy': 'managed_by_lmstudio'})


def comfyui_snapshot(client):
    def get(path):
        response = client.get(path)
        response.raise_for_status()
        return response.json()
    try:
        stats = get('/system_stats')
        devices = [{k: d.get(k) for k in ('name', 'type', 'index', 'vram_total', 'vram_free', 'torch_vram_total', 'torch_vram_free')} for d in stats['devices']]
        result = {'status': 'online', 'version': stats['system'].get('comfyui_version'), 'devices': devices}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {'status': 'offline', 'queue_status': 'unavailable', 'models_status': 'unavailable'}
    try:
        queue = get('/queue')
        # Never forward workflow bodies, prompts, paths or client metadata.
        result.update(queue_status='online', running=[{'id': str(q[1])} for q in queue['queue_running']],
                      pending=[{'id': str(q[1])} for q in queue['queue_pending']])
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        result['queue_status'] = 'unavailable'
    try:
        folders = get('/models')
        if not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
            raise ValueError('Invalid model folders')
        def folder_inventory(folder):
            try:
                files = get('/models/' + quote(folder, safe=''))
                if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
                    raise ValueError('Invalid model files')
                return {'folder': folder, 'status': 'online', 'count': len(files), 'files': files[:200], 'truncated': len(files)>200}
            except (httpx.HTTPError, ValueError, TypeError):
                return {'folder': folder, 'status': 'unavailable', 'files': []}
        with ThreadPoolExecutor(max_workers=4) as pool:
            groups = list(pool.map(folder_inventory, folders[:32]))
        result.update(models_status='partial' if any(g['status']!='online' for g in groups) or len(folders)>32 else 'online',
                      model_groups=groups, model_folders_total=len(folders))
    except (httpx.HTTPError, ValueError, TypeError):
        result['models_status'] = 'unavailable'
    return result
