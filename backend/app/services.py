"""Host-configured service adapters. Runtime URLs never come from job input."""
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

import httpx
from .placement import (
    COMFYUI_FOLDER_ROLES,
    COMFYUI_MODEL_EXTS,
    COMFYUI_NON_MODEL_FOLDERS,
    attach_lmstudio_observations,
    build_lmstudio_gpu_config,
    normalize_lmstudio_gpu_config,
    sort_comfyui_models,
)
from .runtimes import ExecutionUncertain

_UNSET = object()

_COMFYUI_LOADER_KEY_ROLES: dict[str, str] = {
    'ckpt_name': 'checkpoint',
    'unet_name': 'diffusion',
    'diffusion_model': 'diffusion',
    'clip_name': 'text_encoder',
    'clip_name1': 'text_encoder',
    'clip_name2': 'text_encoder',
    'clip_name3': 'text_encoder',
    'clip_name4': 'text_encoder',
    'vae_name': 'vae',
    'lora_name': 'lora',
    'control_net_name': 'controlnet',
    'clip_vision_name': 'clip_vision',
    'model_name': 'other',
}
_SAFE_MODEL_RELPATH_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/+\- ()]{0,179}$')


def _infer_model_name_role(class_type: str) -> str:
    low = (class_type or '').lower()
    if 'upscale' in low:
        return 'upscale'
    if any(tok in low for tok in ('ckpt', 'checkpoint')):
        return 'checkpoint'
    if any(tok in low for tok in ('unet', 'diffusion', 'video', 'wan', 'flux', 'hunyuan', 'ltx', 'minimax')):
        return 'diffusion'
    if any(tok in low for tok in ('clip_vision', 'clipvision')):
        return 'clip_vision'
    if any(tok in low for tok in ('clip', 'textencoder', 'text_encoder', 't5', 'qwen', 'llama', 'gemma')):
        return 'text_encoder'
    if 'lora' in low:
        return 'lora'
    if 'vae' in low:
        return 'vae'
    if 'controlnet' in low or 'control_net' in low:
        return 'controlnet'
    return 'other'


def extract_comfyui_models_from_prompt(
    prompt_graph,
    known_files_by_name: dict[str, tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Extract only safe model filenames from a ComfyUI prompt graph.

    Never returns prompt text, image paths, secrets, or arbitrary node fields.
    """
    if not isinstance(prompt_graph, dict):
        return []
    known = known_files_by_name or {}
    found: list[dict[str, str]] = []
    for node in prompt_graph.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get('class_type') if isinstance(node.get('class_type'), str) else ''
        inputs = node.get('inputs')
        if not isinstance(inputs, dict):
            continue
        for key, raw_val in inputs.items():
            if not isinstance(key, str) or not isinstance(raw_val, str):
                continue
            val = raw_val.strip().replace('\\', '/')
            if not val or len(val) > 180 or '\n' in val or '\r' in val or '://' in val:
                continue
            parts = [p for p in val.split('/') if p]
            if not parts or any(p == '..' for p in parts):
                continue
            rel = '/'.join(parts)
            base = os.path.basename(rel)
            matched = known.get(rel) or known.get(base)
            if matched:
                folder, role = matched
                found.append({'name': rel if rel in known else base, 'role': role, 'folder': folder})
                continue
            key_low = key.lower()
            if key_low not in _COMFYUI_LOADER_KEY_ROLES:
                continue
            if not rel.lower().endswith(COMFYUI_MODEL_EXTS):
                continue
            if not _SAFE_MODEL_RELPATH_RE.fullmatch(rel):
                continue
            role = _COMFYUI_LOADER_KEY_ROLES[key_low]
            if role == 'other':
                role = _infer_model_name_role(class_type)
            found.append({'name': rel, 'role': role})
    return sort_comfyui_models(found)


def lmstudio_api_host(base_url: str) -> str:
    parsed = urlparse(base_url if '://' in base_url else f'http://{base_url}')
    return parsed.netloc or base_url.rstrip('/')


def _default_sdk_client(base_url: str):
    import lmstudio as lms
    return lms.Client(lmstudio_api_host(base_url))


def _lmstudio_sdk_details(lmstudio_url: str, available_gpu_indices: list[int], sdk_client_factory=None):
    factory = sdk_client_factory or _default_sdk_client
    details = {}
    try:
        with factory(lmstudio_url) as sdk_client:
            for handle in sdk_client.list_loaded_models():
                info_obj = handle.get_info()
                info = info_obj.to_dict() if hasattr(info_obj, 'to_dict') else dict(info_obj)
                cfg_obj = handle.get_load_config()
                cfg = cfg_obj.to_dict() if hasattr(cfg_obj, 'to_dict') else dict(cfg_obj)
                ident = getattr(handle, 'identifier', None) or info.get('identifier')
                if not ident:
                    continue
                details[ident] = {
                    'model_path': info.get('path'),
                    'configured_placement': normalize_lmstudio_gpu_config(cfg, available_gpu_indices),
                }
        return details, True
    except Exception:
        return {}, False


def lmstudio_inventory(
    client,
    *,
    lmstudio_url: str | None = None,
    available_gpu_indices: list[int] | None = None,
    gpu_processes=_UNSET,
    sdk_client_factory=None,
    matched_pids=None,
):
    response = client.get('/api/v1/models')
    response.raise_for_status()
    models = response.json()['models']
    if not isinstance(models, list):
        raise ValueError('Invalid model inventory')
    sdk_details, sdk_ok = {}, False
    if lmstudio_url or sdk_client_factory:
        sdk_details, sdk_ok = _lmstudio_sdk_details(
            lmstudio_url or '',
            available_gpu_indices or [],
            sdk_client_factory=sdk_client_factory,
        )
    catalog, loaded, model_paths = [], [], {}
    for model in models:
        key = model['key']
        if not isinstance(key, str) or not key or len(key) > 90:
            continue
        model_type = model['type']
        catalog.append(dict(id='lmstudio:' + key, name=key, runtime='lmstudio', runtime_model=key,
                            model_type=model_type, capability='lmstudio:' + key,
                            description=f"LM Studio · {model.get('display_name') or key} · {model_type}"))
        for instance in model['loaded_instances']:
            inst_id = instance['id']
            entry = dict(name=key, instance_id=inst_id, size_bytes=None, vram_bytes=None)
            if lmstudio_url or sdk_client_factory:
                detail = sdk_details.get(inst_id)
                entry['configured_placement'] = detail['configured_placement'] if detail else None
                if detail and detail.get('model_path'):
                    model_paths[inst_id] = detail['model_path']
            loaded.append(entry)
    if gpu_processes is not _UNSET:
        loaded = attach_lmstudio_observations(loaded, gpu_processes, model_paths, matched_pids=matched_pids)
    state = dict(status='online', models=len(catalog), loaded_models_status='online', loaded_models=loaded)
    if lmstudio_url or sdk_client_factory:
        state['gpu_placement_supported'] = sdk_ok
    return catalog, state


def control_lmstudio(
    client,
    job,
    report,
    *,
    lmstudio_url: str | None = None,
    available_gpu_indices: list[int] | None = None,
    sdk_client_factory=None,
):
    model = job['model'].split(':', 1)[1]
    params = job.get('parameters', {})
    action = params['model_action']
    target_gpus = params.get('gpu_indices')
    replace = bool(params.get('replace', False))
    ack = report(status='running', runtime_state={'phase': action}, message=f'LM Studio 正在{action}模型。')
    if ack.get('status') == 'cancelling':
        report(status='cancelled', message='模型操作尚未送出，已取消。')
        return
    try:
        catalog, before = lmstudio_inventory(client)
        spec = next((m for m in catalog if m['runtime_model'] == model), None)
        model_type = spec['model_type'] if spec else 'llm'
        existing = [m for m in before['loaded_models'] if m['name'] == model]
        if action == 'load' and existing and (replace or target_gpus is not None):
            report(status='running', runtime_state={'phase': 'unloading'}, message='正在卸載既有實例以套用新的 GPU 配置。')
            for instance in existing:
                response = client.post('/api/v1/models/unload', json={'instance_id': instance['instance_id']}, timeout=180)
                response.raise_for_status()
            for _ in range(30):
                _, check = lmstudio_inventory(client)
                if not any(m['name'] == model for m in check['loaded_models']):
                    existing = []
                    break
                time.sleep(.5)
            else:
                raise ExecutionUncertain('LM Studio 既有實例尚未卸載完成，保留工作槽。')
        if action == 'load' and not existing:
            use_sdk = sdk_client_factory is not None or lmstudio_url is not None
            if target_gpus is not None and not use_sdk:
                raise RuntimeError('LM Studio SDK 未啟用，無法指定 GPU 載入。')
            if use_sdk:
                gpu_cfg = build_lmstudio_gpu_config(target_gpus, available_gpu_indices or [])
                load_cfg = {'gpu': gpu_cfg} if model_type == 'embedding' else {'contextLength': 2048, 'gpu': gpu_cfg}
                factory = sdk_client_factory or _default_sdk_client
                try:
                    with factory(lmstudio_url or '') as sdk_client:
                        namespace = sdk_client.embedding if model_type == 'embedding' else sdk_client.llm
                        namespace.load_new_instance(model, instance_identifier=model, ttl=None, config=load_cfg)
                except Exception as exc:
                    exc_name = type(exc).__name__
                    if 'ServerError' in exc_name or isinstance(exc, ValueError):
                        raise RuntimeError(f'LM Studio 載入模型失敗：{exc}') from exc
                    if target_gpus is None and not replace:
                        # Fallback to REST API when no explicit GPU placement was requested.
                        response = client.post('/api/v1/models/load', json={'model': model, 'context_length': 2048}, timeout=180)
                        response.raise_for_status()
                        if response.json().get('status') != 'loaded':
                            raise ExecutionUncertain('LM Studio 未確認模型已載入。')
                    else:
                        raise ExecutionUncertain(f'透過 LM Studio SDK 指定 GPU 載入時連線異常，保留工作槽：{exc}') from exc
            else:
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
            _, inventory = lmstudio_inventory(
                client,
                lmstudio_url=lmstudio_url,
                available_gpu_indices=available_gpu_indices,
                sdk_client_factory=sdk_client_factory,
            )
            matched = [m for m in inventory['loaded_models'] if m['name'] == model]
            loaded = bool(matched)
            if loaded == (action == 'load'):
                result = {'runtime': 'lmstudio', 'model': model, 'model_action': action, 'loaded': loaded}
                if action == 'load':
                    if target_gpus is not None:
                        result['requested_gpu_indices'] = target_gpus
                    if matched and 'configured_placement' in matched[0]:
                        result['configured_placement'] = matched[0]['configured_placement']
                report(status='completed', progress=100, runtime_state={'phase': 'completed'},
                       message=f"LM Studio 模型已{'載入' if loaded else '卸載'}。",
                       result=result)
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
    running_prompts = []
    try:
        queue = get('/queue')
        # Never forward workflow bodies, prompts, paths or client metadata.
        running_items = queue['queue_running']
        for q in running_items:
            if isinstance(q, list) and len(q) >= 3 and isinstance(q[2], dict):
                running_prompts.append(q[2])
        result.update(queue_status='online', running=[{'id': str(q[1])} for q in running_items],
                      pending=[{'id': str(q[1])} for q in queue['queue_pending']])
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        result['queue_status'] = 'unavailable'
    known_files: dict[str, tuple[str, str]] = {}
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
        for group in groups:
            folder = group.get('folder')
            if not isinstance(folder, str) or folder.lower() in COMFYUI_NON_MODEL_FOLDERS or group.get('status') != 'online':
                continue
            role = COMFYUI_FOLDER_ROLES.get(folder.lower(), 'other')
            for filename in group.get('files') or []:
                if isinstance(filename, str) and filename:
                    norm = filename.replace('\\', '/')
                    known_files.setdefault(norm, (folder, role))
                    known_files.setdefault(os.path.basename(norm), (folder, role))
        result.update(models_status='partial' if any(g['status']!='online' for g in groups) or len(folders)>32 else 'online',
                      model_groups=groups, model_folders_total=len(folders))
    except (httpx.HTTPError, ValueError, TypeError):
        result['models_status'] = 'unavailable'

    active_models: list[dict[str, str]] = []
    active_source = 'none'
    if running_prompts:
        extracted = []
        for prompt_graph in running_prompts:
            extracted.extend(extract_comfyui_models_from_prompt(prompt_graph, known_files))
        active_models = sort_comfyui_models(extracted)
        if active_models:
            active_source = 'queue'
    elif (
        result.get('queue_status') == 'online'
        and not result.get('running')
        and any((d.get('torch_vram_total') or 0) > 0 for d in devices)
    ):
        try:
            history = get('/history?max_items=1')
            if isinstance(history, dict) and history:
                latest_entry = max(
                    (e for e in history.values() if isinstance(e, dict)),
                    key=lambda e: e['prompt'][0] if isinstance(e.get('prompt'), list) and e['prompt'] and isinstance(e['prompt'][0], (int, float)) else -1,
                    default=None,
                )
                prompt_tuple = latest_entry.get('prompt') if isinstance(latest_entry, dict) else None
                if isinstance(prompt_tuple, list) and len(prompt_tuple) >= 3 and isinstance(prompt_tuple[2], dict):
                    active_models = extract_comfyui_models_from_prompt(prompt_tuple[2], known_files)
                    if active_models:
                        active_source = 'history'
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
    result['active_models'] = active_models
    result['active_models_source'] = active_source
    return result


def comfyui_supports_gpu_select(client) -> bool:
    """Check whether ComfyUI exposes the built-in SelectModelDevice node."""
    try:
        response = client.get('/object_info/SelectModelDevice', timeout=2)
        response.raise_for_status()
        payload = response.json()
        return isinstance(payload, dict) and 'SelectModelDevice' in payload
    except (httpx.HTTPError, ValueError, TypeError):
        return False


def comfyui_loaded_models_telemetry(client) -> list[dict] | None:
    """Query optional GPU Control Center ComfyUI telemetry endpoint (/gcc/loaded_models).

    Returns normalized per-model VRAM/RAM entries when the plugin is active,
    or None when the endpoint is not installed/reachable.
    """
    try:
        response = client.get('/gcc/loaded_models', timeout=2)
        if response.status_code != 200:
            return None
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('models'), list):
            return None
    except (httpx.HTTPError, ValueError, TypeError):
        return None

    def _non_neg_int(val) -> int:
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return 0
        return max(0, int(val))

    normalized = []
    for raw in payload['models'][:32]:
        if not isinstance(raw, dict):
            continue
        name = raw.get('name')
        if not isinstance(name, str) or not name.strip():
            continue
        role = raw.get('role') if isinstance(raw.get('role'), str) else 'other'
        folder = raw.get('folder') if isinstance(raw.get('folder'), str) and raw.get('folder') else None
        raw_gpu = raw.get('gpu_index')
        gpu_index = raw_gpu if isinstance(raw_gpu, int) and not isinstance(raw_gpu, bool) and raw_gpu >= 0 else None
        device = raw.get('device') if isinstance(raw.get('device'), str) and raw.get('device') else None
        files = [f.strip()[:180] for f in (raw.get('files') or []) if isinstance(f, str) and f.strip()][:6]
        entry = {
            'name': name.strip()[:180],
            'role': role,
            'gpu_index': gpu_index,
            'device': device,
            'vram_bytes': _non_neg_int(raw.get('vram_bytes')),
            'total_bytes': _non_neg_int(raw.get('total_bytes')),
            'offloaded_bytes': _non_neg_int(raw.get('offloaded_bytes')),
            'ram_bytes': _non_neg_int(raw.get('ram_bytes')),
            'is_dynamic': bool(raw.get('is_dynamic', False)),
            'currently_used': bool(raw.get('currently_used', False)),
            'memory_source': 'comfyui_internal',
        }
        if folder:
            entry['folder'] = folder
        if files:
            entry['files'] = files
        if isinstance(raw.get('name_source'), str):
            entry['name_source'] = raw['name_source']
        normalized.append(entry)
    return sort_comfyui_models(normalized)


def comfyui_catalog_from_snapshot(snapshot: dict) -> list[dict]:
    """Build loadable ComfyUI model catalog entries from a comfyui_snapshot."""
    if snapshot.get('status') != 'online':
        return []
    folder_types = {
        'diffusion_models': 'diffusion',
        'unet_gguf': 'diffusion_gguf',
        'checkpoints': 'checkpoint',
    }
    catalog = []
    seen = set()
    for group in snapshot.get('model_groups') or []:
        folder = group.get('folder')
        model_type = folder_types.get(folder)
        if not model_type or group.get('status') != 'online':
            continue
        for filename in group.get('files') or []:
            if not isinstance(filename, str) or not filename or len(filename) > 90 or filename in seen:
                continue
            seen.add(filename)
            catalog.append(dict(
                id='comfyui:' + filename,
                name=filename,
                runtime='comfyui',
                runtime_model=filename,
                model_type=model_type,
                capability='comfyui:' + filename,
                description=f'ComfyUI · {folder} · {filename}',
            ))
    return catalog


def control_comfyui(client, job, report):
    """Load (preload into ComfyUI cache) or unload (POST /free) ComfyUI models."""
    model = job['model'].split(':', 1)[1]
    params = job.get('parameters', {})
    action = params['model_action']
    target_gpus = params.get('gpu_indices')
    replace = bool(params.get('replace', False))
    ack = report(status='running', runtime_state={'phase': action}, message=f'ComfyUI 正在執行模型{action}。')
    if ack.get('status') == 'cancelling':
        report(status='cancelled', message='ComfyUI 模型操作尚未送出，已取消。')
        return
    try:
        if action == 'unload':
            response = client.post('/free', json={'unload_models': True, 'free_memory': True}, timeout=30)
            response.raise_for_status()
            report(
                status='completed', progress=100, runtime_state={'phase': 'completed'},
                message='ComfyUI 已卸載快取模型並釋放顯存。',
                result={'runtime': 'comfyui', 'model': model, 'model_action': 'unload', 'loaded': False, 'cleared_all_cache': True},
            )
            return

        # Discover which loadable folder owns this file.
        loader_node = None
        for folder, spec in (
            ('diffusion_models', ('UNETLoader', {'unet_name': model, 'weight_dtype': 'default'})),
            ('unet_gguf', ('UnetLoaderGGUF', {'unet_name': model})),
            ('checkpoints', ('CheckpointLoaderSimple', {'ckpt_name': model})),
        ):
            try:
                res = client.get('/models/' + quote(folder, safe=''), timeout=5)
                res.raise_for_status()
                files = res.json()
                if isinstance(files, list) and model in files:
                    loader_node = {'class_type': spec[0], 'inputs': spec[1]}
                    break
            except (httpx.HTTPError, ValueError, TypeError):
                continue
        if loader_node is None:
            raise RuntimeError(f'在 ComfyUI 的可載入模型目錄中找不到 {model}。')

        if replace:
            report(status='running', runtime_state={'phase': 'unloading'}, message='正在先清除 ComfyUI 既有快取模型。')
            free_res = client.post('/free', json={'unload_models': True, 'free_memory': True}, timeout=30)
            free_res.raise_for_status()

        target_device = f'gpu:{target_gpus[0]}' if target_gpus else 'default'
        if target_gpus:
            if not comfyui_supports_gpu_select(client):
                raise RuntimeError('此 ComfyUI 服務不支援 SelectModelDevice 節點，無法指定目標 GPU。')
            workflow = {
                '1': loader_node,
                '2': {'class_type': 'SelectModelDevice', 'inputs': {'model': ['1', 0], 'device': target_device}},
                '3': {'class_type': 'PreviewAny', 'inputs': {'source': ['2', 0]}},
            }
        else:
            workflow = {
                '1': loader_node,
                '2': {'class_type': 'PreviewAny', 'inputs': {'source': ['1', 0]}},
            }

        submit = client.post('/prompt', json={'prompt': workflow}, timeout=30)
        submit.raise_for_status()
        submit_body = submit.json()
        if submit_body.get('node_errors'):
            raise RuntimeError(f"ComfyUI 拒絕載入流程：{json.dumps(submit_body['node_errors'], ensure_ascii=False)[:300]}")
        prompt_id = submit_body.get('prompt_id')
        if not prompt_id:
            raise ExecutionUncertain('ComfyUI 未回傳 prompt_id，無法確認模型是否載入。')

        started = time.monotonic()
        while time.monotonic() - started < 180:
            hist_res = client.get('/history/' + quote(str(prompt_id), safe=''), timeout=10)
            hist_res.raise_for_status()
            history = hist_res.json()
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(entry, dict):
                status_info = entry.get('status') or {}
                status_str = status_info.get('status_str')
                if status_str == 'success' or status_info.get('completed') is True:
                    if status_str == 'error':
                        raise RuntimeError(f'ComfyUI 載入模型失敗：{status_info}')
                    result = {
                        'runtime': 'comfyui',
                        'model': model,
                        'model_action': 'load',
                        'loaded': True,
                        'prompt_id': prompt_id,
                        'target_device': target_device,
                        'cache_mode': 'comfyui_prompt_cache',
                    }
                    if target_gpus is not None:
                        result['requested_gpu_indices'] = target_gpus
                    report(
                        status='completed', progress=100, runtime_state={'phase': 'completed'},
                        message=f'ComfyUI 模型已預載至快取（目標 {target_device}）。',
                        result=result,
                    )
                    return
                if status_str == 'error':
                    raise RuntimeError(f'ComfyUI 載入模型失敗：{json.dumps(status_info, ensure_ascii=False)[:300]}')
            time.sleep(0.5)
        raise ExecutionUncertain('ComfyUI 模型載入逾時，尚未取得完成確認，保留工作槽。')
    except (httpx.RequestError, ValueError, KeyError, TypeError) as exc:
        raise ExecutionUncertain('無法確認 ComfyUI 模型操作結果，保留工作槽。') from exc
