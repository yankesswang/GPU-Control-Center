"""GPU Control Center read-only telemetry endpoint for ComfyUI.

Exposes GET /gcc/loaded_models returning per-model VRAM / offloaded RAM usage
from comfy.model_management.current_loaded_models without modifying ComfyUI
core code, retaining model references, or leaking host paths/prompts.
"""
from __future__ import annotations

import logging
import os

from aiohttp import web

log = logging.getLogger('gpu_control_center_telemetry')

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

_FOLDER_ROLES = {
    'diffusion_models': 'diffusion',
    'unet_gguf': 'diffusion',
    'unet': 'diffusion',
    'checkpoints': 'checkpoint',
    'text_encoders': 'text_encoder',
    'clip': 'text_encoder',
    'clip_gguf': 'text_encoder',
    'loras': 'lora',
    'vae': 'vae',
    'vae_approx': 'vae',
    'controlnet': 'controlnet',
    'clip_vision': 'clip_vision',
    'upscale_models': 'upscale',
}


def _sanitize_model_path(raw_path: str) -> tuple[str, str | None]:
    norm = raw_path.replace('\\', '/').strip()
    low = norm.lower()
    idx = low.rfind('/models/')
    if idx != -1:
        rel = norm[idx + len('/models/'):].lstrip('/')
        if '/' in rel:
            folder, rest = rel.split('/', 1)
            if rest and '..' not in rest.split('/'):
                return rest[:180], folder
    return os.path.basename(norm)[:180], None


def _infer_role(fn_name: str, folder: str | None, model_class: str) -> str:
    fn_low = (fn_name or '').lower()
    folder_low = (folder or '').lower()
    cls_low = (model_class or '').lower()
    if folder_low in _FOLDER_ROLES:
        return _FOLDER_ROLES[folder_low]
    if 'vae' in fn_low or 'vae' in cls_low or 'autoencoder' in cls_low:
        return 'vae'
    if 'clip_vision' in fn_low or 'clipvision' in cls_low:
        return 'clip_vision'
    if 'clip' in fn_low or any(t in cls_low for t in ('clip', 't5', 'qwen', 'llama', 'gemma', 'te_model', 'textencoder')):
        return 'text_encoder'
    if 'controlnet' in fn_low or 'controlnet' in cls_low:
        return 'controlnet'
    if 'checkpoint' in fn_low:
        return 'checkpoint'
    if 'diffusion' in fn_low or 'unet' in fn_low:
        return 'diffusion'
    return 'diffusion' if cls_low else 'other'


def _extract_patcher_identity(patcher) -> tuple[str, list[str], str, str | None, str, str]:
    inner_model = getattr(patcher, 'model', None)
    model_class = inner_model.__class__.__name__ if inner_model is not None else patcher.__class__.__name__
    cached_init = getattr(patcher, 'cached_patcher_init', None)
    fn_name = ''
    raw_paths: list[str] = []
    if isinstance(cached_init, tuple) and len(cached_init) >= 2:
        fn = cached_init[0]
        fn_name = getattr(fn, '__name__', '') if callable(fn) else ''
        args = cached_init[1]
        if isinstance(args, (tuple, list)) and args:
            first = args[0]
            if isinstance(first, str) and first:
                raw_paths.append(first)
            elif isinstance(first, (tuple, list)):
                raw_paths.extend(p for p in first if isinstance(p, str) and p)

    files: list[str] = []
    folder: str | None = None
    for raw in raw_paths:
        rel, detected_folder = _sanitize_model_path(raw)
        if rel and rel not in files:
            files.append(rel)
        if detected_folder and folder is None:
            folder = detected_folder

    role = _infer_role(fn_name, folder, model_class)
    if files:
        name = files[0] if len(files) == 1 else ' + '.join(files[:3])
        name_source = 'cached_init'
    else:
        name = model_class or 'UnknownModel'
        name_source = 'class'
    return name, files, role, folder, model_class, name_source


def collect_loaded_models_snapshot() -> dict:
    import comfy.model_management as mm

    entries = []
    # Shallow copy to avoid iteration issues if ComfyUI loads/unloads concurrently.
    snapshot = list(getattr(mm, 'current_loaded_models', []) or [])
    for loaded_model in snapshot:
        try:
            patcher = loaded_model.model
            if patcher is None:
                continue
            device = getattr(loaded_model, 'device', None)
            if device is None and hasattr(patcher, 'load_device'):
                device = patcher.load_device
            device_type = getattr(device, 'type', None)
            if device_type == 'cuda':
                gpu_index = int(device.index) if device.index is not None else 0
            else:
                gpu_index = None
            is_cpu = bool(mm.is_device_cpu(device)) if device is not None else True

            total_bytes = max(0, int(loaded_model.model_memory()))
            vram_bytes = 0 if is_cpu else max(0, int(loaded_model.model_loaded_memory()))
            offloaded_bytes = max(0, int(loaded_model.model_offloaded_memory()))
            ram_bytes = max(0, int(patcher.loaded_ram_size())) if hasattr(patcher, 'loaded_ram_size') else 0
            is_dynamic = bool(patcher.is_dynamic()) if hasattr(patcher, 'is_dynamic') else False
            currently_used = bool(getattr(loaded_model, 'currently_used', False))

            name, files, role, folder, model_class, name_source = _extract_patcher_identity(patcher)
            entry = {
                'name': name,
                'files': files,
                'role': role,
                'folder': folder,
                'device': str(device) if device is not None else None,
                'gpu_index': gpu_index,
                'vram_bytes': vram_bytes,
                'total_bytes': total_bytes,
                'offloaded_bytes': offloaded_bytes,
                'ram_bytes': ram_bytes,
                'is_dynamic': is_dynamic,
                'currently_used': currently_used,
                'model_class': model_class,
                'name_source': name_source,
            }
            entries.append(entry)
        except Exception as exc:
            log.debug('Skipping ComfyUI loaded model telemetry entry: %s', exc)
            continue

    entries.sort(key=lambda e: (-e['vram_bytes'], -e['total_bytes'], e['name'].lower()))
    return {'schema_version': 1, 'models': entries}


def _register_routes() -> None:
    try:
        from server import PromptServer
        server = getattr(PromptServer, 'instance', None)
        if server is None:
            return

        @server.routes.get('/gcc/loaded_models')
        async def gcc_loaded_models(_request):
            return web.json_response(collect_loaded_models_snapshot())

        log.info('GPU Control Center telemetry route registered: GET /gcc/loaded_models')
    except Exception as exc:
        log.warning('Could not register GPU Control Center telemetry route: %s', exc)


_register_routes()
