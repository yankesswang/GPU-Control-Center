"""Real Ollama execution. URLs are operator configuration, never caller input."""
import json
import time

import httpx


class ExecutionUncertain(Exception):
    """The upstream may still be computing; do not release the central slot."""


def discover_ollama(client):
    response = client.get('/api/tags')
    response.raise_for_status()
    specs = []
    for model in response.json().get('models', []):
        name = model['name']
        if len(name) > 90:
            continue
        specs.append(dict(id='ollama:' + name, name=name, runtime='ollama', runtime_model=name,
                          capability='ollama:' + name, description='真實 Ollama 本機推論；完成後卸載本次模型。'))
    return specs


def resident_ollama(client):
    """Report runtime residency separately from the installed model catalog."""
    try:
        response = client.get('/api/ps')
        response.raise_for_status()
        payload = response.json()
        models = payload['models']
        if not isinstance(models, list):
            raise ValueError('Invalid resident model list')
        loaded = []
        for model in models:
            name = model.get('name') or model.get('model')
            if not isinstance(name, str) or not name:
                raise ValueError('Invalid resident model name')
            def byte_count(key):
                value = model.get(key)
                return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None
            loaded.append(dict(name=name, size_bytes=byte_count('size'), vram_bytes=byte_count('size_vram')))
        return {'loaded_models_status': 'online', 'loaded_models': loaded}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        # Unreachable or invalid inventory must not look like an empty runtime.
        return {'loaded_models_status': 'unavailable', 'loaded_models': None}


def generate_ollama(client, job, report):
    model = job['model'].removeprefix('ollama:')
    parameters = job.get('parameters', {})
    state = {'phase': 'loading', 'text': '', 'thinking': '', 'tokens_generated': 0}
    response = report(status='running', runtime_state=state, message='正在連線 Ollama 並載入模型。')
    if response.get('status') == 'cancelling':
        report(status='cancelled', message='已取消，尚未向 Ollama 提交推論。', runtime_state={**state, 'phase': 'cancelled'})
        return
    text, thinking, chunks = '', '', 0
    last_report = time.monotonic()
    started = time.monotonic()
    cancel_requested = False
    final = None
    gpu_memory_bytes = None
    try:
        with client.stream('POST', '/api/chat', json={
            'model': model, 'messages': [{'role': 'user', 'content': parameters['prompt']}],
            'stream': True, 'keep_alive': 0,
            'options': {'num_predict': parameters.get('max_tokens', 256), 'num_ctx': 2048},
        }) as stream:
            stream.raise_for_status()
            for line in stream.iter_lines():
                if time.monotonic() - started > 180:
                    raise ExecutionUncertain('Ollama 推論超過 180 秒，無法確認已停止。')
                if not line:
                    continue
                part = json.loads(line)
                if part.get('error'):
                    raise RuntimeError(part['error'])
                if gpu_memory_bytes is None:
                    try:
                        resident = client.get('/api/ps')
                        resident.raise_for_status()
                        current = next((m for m in resident.json().get('models', []) if m.get('name') == model or m.get('model') == model), None)
                        if current:
                            gpu_memory_bytes = current.get('size_vram')
                    except (httpx.HTTPError, ValueError):
                        pass
                text += part.get('message', {}).get('content', '')
                thinking += part.get('message', {}).get('thinking', '')
                if part.get('message', {}).get('content') or part.get('message', {}).get('thinking'):
                    chunks += 1
                if part.get('done'):
                    final = part
                    break
                if time.monotonic() - last_report >= 0.5:
                    state = dict(phase='generating', text=text, thinking=thinking, chunks_received=chunks, gpu_memory_bytes=gpu_memory_bytes)
                    ack = report(status='running', runtime_state=state)
                    cancel_requested = cancel_requested or ack.get('status') == 'cancelling'
                    # Closing a stream alone does not prove the GPU stopped. Drain to
                    # the upstream's explicit done boundary before acknowledging cancel.
                    last_report = time.monotonic()
    except httpx.HTTPStatusError:
        raise  # The server explicitly rejected the request.
    except (httpx.RequestError, json.JSONDecodeError) as exc:
        raise ExecutionUncertain('Ollama 連線中斷，無法確認遠端推論是否停止。') from exc
    if final is None:
        raise ExecutionUncertain('Ollama 串流結束但沒有 done 確認。')
    # keep_alive=0 requests unloading, but only the model inventory confirms it.
    released = False
    try:
        for _ in range(20):
            resident = client.get('/api/ps')
            resident.raise_for_status()
            if not any(m.get('name') == model or m.get('model') == model for m in resident.json().get('models', [])):
                released = True
                break
            time.sleep(0.5)
    except (httpx.HTTPError, ValueError) as exc:
        raise ExecutionUncertain('生成已結束，但無法確認 Ollama 模型已卸載。') from exc
    if not released:
        raise ExecutionUncertain('生成已結束，但模型仍常駐；保留工作槽等待人工確認。')
    # The central API also resolves a cancellation racing with this final report.
    tokens = final.get('eval_count', 0)
    state = dict(phase='completed', text=text, thinking=thinking, tokens_generated=tokens, gpu_memory_bytes=gpu_memory_bytes)
    if cancel_requested:
        report(status='cancelled', message='已等待 Ollama 生成結束，確認停止後取消。', runtime_state={**state, 'phase': 'cancelled'})
    else:
        duration = final.get('eval_duration', 0) / 1e9
        report(status='completed', progress=100, runtime_state=state,
               message='真實 Ollama 推論完成。', result={
                   'simulated': False, 'runtime': 'ollama', 'model': model, 'text': text,
                   'thinking': thinking, 'tokens_generated': tokens,
                   'tokens_per_second': round(tokens / duration, 2) if duration else None,
                   'gpu_memory_bytes': gpu_memory_bytes,
                   'model_released': True,
                   'total_duration_ms': round(final.get('total_duration', 0) / 1e6, 2),
               })
