"""Host-side GPU process observation and placement normalization.

Separates requested/configured placement (e.g. LM Studio load config) from
actually observed NVIDIA per-GPU process VRAM usage via nvidia-smi + /proc.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

COMFYUI_FOLDER_ROLES: dict[str, str] = {
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
COMFYUI_NON_MODEL_FOLDERS = {'custom_nodes', 'configs', 'datasets'}
COMFYUI_ROLE_ORDER: dict[str, int] = {
    'diffusion': 0,
    'checkpoint': 1,
    'text_encoder': 2,
    'lora': 3,
    'vae': 4,
    'controlnet': 5,
    'clip_vision': 6,
    'upscale': 7,
    'other': 8,
}
COMFYUI_MODEL_EXTS = ('.safetensors', '.gguf', '.ckpt', '.pt', '.pth', '.bin', '.sft', '.onnx')
_COMFYUI_STRICT_EXTS = ('.safetensors', '.gguf', '.ckpt', '.sft')
_COMFYUI_IGNORED_PATH_PARTS = ('/site-packages/', '/dist-packages/', '/usr/', '/lib/', '/lib64/')


def _float_or_none(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_proc_cmdline(pid: int) -> str:
    try:
        raw = Path(f'/proc/{pid}/cmdline').read_bytes()
        return raw.replace(b'\x00', b' ').decode('utf-8', 'replace').strip()
    except OSError:
        return ''


def _read_proc_cwd(pid: int) -> str:
    try:
        return os.readlink(f'/proc/{pid}/cwd')
    except OSError:
        return ''


def _read_proc_mapped_files(pid: int) -> set[str]:
    paths: set[str] = set()
    try:
        text = Path(f'/proc/{pid}/maps').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return paths
    for line in text.splitlines():
        parts = line.split(None, 5)
        if len(parts) == 6:
            path = parts[5].strip()
            if path.startswith('/'):
                if path.endswith(' (deleted)'):
                    path = path[:-10]
                paths.add(path)
    return paths


def _read_proc_fd_targets(pid: int) -> set[str]:
    paths: set[str] = set()
    fd_dir = Path(f'/proc/{pid}/fd')
    try:
        entries = list(fd_dir.iterdir())
    except OSError:
        return paths
    for entry in entries:
        try:
            target = os.readlink(entry)
            if target.startswith('/'):
                paths.add(target)
        except OSError:
            continue
    return paths


def parse_nvidia_compute_apps(output: str, gpu_uuid_to_index: dict[str, int]) -> dict[int, dict[str, Any]]:
    """Parse `nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory` CSV."""
    by_pid: dict[int, dict[str, Any]] = {}
    for raw_line in output.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 4:
            continue
        uuid, pid_text, process_name, mem_text = parts[0], parts[1], parts[2], parts[3]
        gpu_index = gpu_uuid_to_index.get(uuid)
        if gpu_index is None:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        used_mb = _float_or_none(mem_text)
        entry = by_pid.setdefault(pid, {'pid': pid, 'process_name': process_name, 'gpus': {}})
        # Aggregate if the same PID appears multiple times on the same GPU.
        prev = entry['gpus'].get(gpu_index)
        if prev is None:
            entry['gpus'][gpu_index] = used_mb
        elif used_mb is not None:
            entry['gpus'][gpu_index] = prev + used_mb
    result: dict[int, dict[str, Any]] = {}
    for pid, entry in by_pid.items():
        gpu_rows = [
            {'index': idx, 'memory_used_mb': round(mem, 1) if mem is not None else None}
            for idx, mem in entry['gpus'].items()
        ]
        gpu_rows.sort(key=lambda r: (-(r['memory_used_mb'] or -1.0), r['index']))
        result[pid] = {
            'pid': pid,
            'process_name': entry['process_name'],
            'gpus': gpu_rows,
        }
    return result


def collect_gpu_processes(gpus: list[dict[str, Any]]) -> dict[int, dict[str, Any]] | None:
    """Collect active NVIDIA compute processes enriched with runtime process hints."""
    uuid_to_index = {
        g['uuid']: int(g['index'])
        for g in gpus
        if isinstance(g.get('uuid'), str) and isinstance(g.get('index'), int)
    }
    if not uuid_to_index:
        return {}
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,process_name,used_memory', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=4, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    processes = parse_nvidia_compute_apps(result.stdout, uuid_to_index)
    for pid, info in processes.items():
        lower_name = info['process_name'].lower()
        info['cmdline'] = _read_proc_cmdline(pid)
        info['cwd'] = _read_proc_cwd(pid)
        if any(token in lower_name for token in ('lmstudio', 'lm-studio', 'ollama', 'llama')) or _is_comfyui_process(info):
            info['paths'] = _read_proc_mapped_files(pid) | _read_proc_fd_targets(pid)
        else:
            info['paths'] = set()
    return processes


def normalize_lmstudio_gpu_config(
    load_config: dict[str, Any] | None,
    available_gpu_indices: list[int],
) -> dict[str, Any] | None:
    """Normalize LM Studio SDK load config into an explicit configured placement dict."""
    if not isinstance(load_config, dict):
        return None
    gpu = load_config.get('gpu')
    if not isinstance(gpu, dict):
        return None
    ratio = gpu.get('ratio')
    main_gpu = gpu.get('mainGpu') if isinstance(gpu.get('mainGpu'), int) and not isinstance(gpu.get('mainGpu'), bool) else None
    split_strategy = gpu.get('splitStrategy') if isinstance(gpu.get('splitStrategy'), str) else None
    raw_disabled = gpu.get('disabledGpus')
    disabled_gpus = sorted({
        idx for idx in (raw_disabled or [])
        if isinstance(idx, int) and not isinstance(idx, bool) and idx >= 0
    })
    all_indices = sorted({idx for idx in available_gpu_indices if isinstance(idx, int) and idx >= 0})
    if ratio == 'off' or (isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and ratio <= 0):
        return {
            'mode': 'cpu',
            'gpu_indices': [],
            'main_gpu': None,
            'disabled_gpus': disabled_gpus,
            'split_strategy': split_strategy,
            'ratio': ratio,
        }
    enabled = [idx for idx in all_indices if idx not in disabled_gpus]
    if disabled_gpus and enabled:
        mode = 'pinned'
    elif not disabled_gpus and main_gpu is not None and split_strategy == 'favorMainGpu' and len(all_indices) > 1:
        mode = 'auto'
    else:
        mode = 'auto'
    return {
        'mode': mode,
        'gpu_indices': enabled,
        'main_gpu': main_gpu,
        'disabled_gpus': disabled_gpus,
        'split_strategy': split_strategy or 'evenly',
        'ratio': ratio,
    }


def build_lmstudio_gpu_config(
    target_gpus: list[int] | None,
    available_gpu_indices: list[int],
) -> dict[str, Any]:
    """Build LM Studio SDK `gpu` config dict from requested target GPU indices."""
    all_indices = sorted({idx for idx in available_gpu_indices if isinstance(idx, int) and idx >= 0})
    if target_gpus is None:
        return {'ratio': 1.0, 'splitStrategy': 'evenly', 'disabledGpus': []}
    selected = sorted({idx for idx in target_gpus if isinstance(idx, int) and idx >= 0})
    if not selected:
        raise ValueError('target_gpus must be non-empty when provided')
    disabled = [idx for idx in all_indices if idx not in selected]
    if len(selected) == 1:
        return {
            'ratio': 1.0,
            'mainGpu': selected[0],
            'splitStrategy': 'favorMainGpu',
            'disabledGpus': disabled,
        }
    return {
        'ratio': 1.0,
        'mainGpu': selected[0],
        'splitStrategy': 'evenly',
        'disabledGpus': disabled,
    }


def _is_lmstudio_worker(proc: dict[str, Any]) -> bool:
    name = proc.get('process_name', '').lower()
    cmd = proc.get('cmdline', '').lower()
    if 'systemresourcesworker' in cmd:
        return False
    return 'llmworker' in cmd or (('.lmstudio' in name or 'lm-studio' in name) and 'crashpad' not in cmd)


def _is_ollama_runner(proc: dict[str, Any]) -> bool:
    name = proc.get('process_name', '').lower()
    cmd = proc.get('cmdline', '').lower()
    return ('ollama' in name or 'ollama' in cmd) and ('llama-server' in name or 'llama-server' in cmd or 'runner' in cmd)


def _proc_tokens_and_hints(proc: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    cmdline = (proc.get('cmdline') or '').strip()
    try:
        tokens = shlex.split(cmdline) if cmdline else []
    except ValueError:
        tokens = cmdline.split() if cmdline else []
    return _parse_safe_hints(tokens)


def _is_comfyui_process(proc: dict[str, Any]) -> bool:
    lower_cwd = (proc.get('cwd') or '').strip().lower()
    lower_cmd = (proc.get('cmdline') or '').strip().lower()
    if 'comfyui' in lower_cwd or 'comfyui' in lower_cmd:
        return True
    script, _, port_hint = _proc_tokens_and_hints(proc)
    return script == 'main.py' and port_hint == '8188'


_COMFYUI_EXTRA_FIELDS = (
    'gpu_index',
    'device',
    'vram_bytes',
    'total_bytes',
    'offloaded_bytes',
    'ram_bytes',
    'is_dynamic',
    'currently_used',
    'memory_source',
    'files',
    'name_source',
)


def sort_comfyui_models(models: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    """Deduplicate and sort ComfyUI model descriptors by VRAM usage / role priority."""
    by_name: dict[str, dict[str, Any]] = {}
    for item in models:
        name = item.get('name')
        if not isinstance(name, str) or not name.strip():
            continue
        role = item.get('role') if isinstance(item.get('role'), str) else 'other'
        folder = item.get('folder') if isinstance(item.get('folder'), str) and item.get('folder') else None
        existing = by_name.get(name)
        if existing is None:
            entry: dict[str, Any] = {'name': name, 'role': role}
            if folder:
                entry['folder'] = folder
            for key in _COMFYUI_EXTRA_FIELDS:
                if key in item and item[key] is not None:
                    entry[key] = item[key]
            by_name[name] = entry
        else:
            if COMFYUI_ROLE_ORDER.get(role, 99) < COMFYUI_ROLE_ORDER.get(existing['role'], 99):
                existing['role'] = role
            if folder and not existing.get('folder'):
                existing['folder'] = folder
            has_new_mem = item.get('memory_source') == 'comfyui_internal' or item.get('vram_bytes') is not None
            has_old_mem = existing.get('memory_source') == 'comfyui_internal' or existing.get('vram_bytes') is not None
            if has_new_mem and not has_old_mem:
                for key in _COMFYUI_EXTRA_FIELDS:
                    if key in item and item[key] is not None:
                        existing[key] = item[key]
            elif has_new_mem and has_old_mem:
                # If the same model appears twice in telemetry, keep the higher-VRAM observation.
                if (item.get('vram_bytes') or 0) > (existing.get('vram_bytes') or 0):
                    for key in _COMFYUI_EXTRA_FIELDS:
                        if key in item and item[key] is not None:
                            existing[key] = item[key]
            else:
                for key in _COMFYUI_EXTRA_FIELDS:
                    if key in item and item[key] is not None and key not in existing:
                        existing[key] = item[key]
    values = list(by_name.values())
    has_vram_telemetry = any(isinstance(m.get('vram_bytes'), (int, float)) for m in values)
    if has_vram_telemetry:
        ordered = sorted(
            values,
            key=lambda m: (
                0 if (isinstance(m.get('vram_bytes'), (int, float)) and m['vram_bytes'] > 0) else 1,
                -(m['vram_bytes'] if isinstance(m.get('vram_bytes'), (int, float)) else -1),
                COMFYUI_ROLE_ORDER.get(m['role'], 99),
                m['name'].lower(),
            ),
        )
    else:
        ordered = sorted(values, key=lambda m: (COMFYUI_ROLE_ORDER.get(m['role'], 99), m['name'].lower()))
    return ordered[:limit]


def extract_comfyui_models_from_paths(paths: set[str] | list[str]) -> list[dict[str, str]]:
    """Extract sanitized ComfyUI model filenames from mapped/open process file paths."""
    found: list[dict[str, str]] = []
    for raw in paths or ():
        if not isinstance(raw, str) or not raw:
            continue
        norm = raw.replace('\\', '/')
        low = norm.lower()
        if any(part in low for part in _COMFYUI_IGNORED_PATH_PARTS):
            continue
        idx = low.rfind('/models/')
        if idx != -1:
            rel = norm[idx + len('/models/'):].lstrip('/')
            if '/' not in rel:
                continue
            folder, rest = rel.split('/', 1)
            folder_low = folder.lower()
            if not rest or len(rest) > 180 or '..' in rest.split('/'):
                continue
            if folder_low in COMFYUI_NON_MODEL_FOLDERS:
                continue
            if not rest.lower().endswith(COMFYUI_MODEL_EXTS):
                continue
            found.append({
                'name': rest,
                'role': COMFYUI_FOLDER_ROLES.get(folder_low, 'other'),
                'folder': folder,
            })
            continue
        if low.endswith(_COMFYUI_STRICT_EXTS):
            base = os.path.basename(norm)
            if base and len(base) <= 180:
                found.append({'name': base, 'role': 'other'})
    return sort_comfyui_models(found)


def _url_port(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url if '://' in url else f'http://{url}')
    return str(parsed.port) if parsed.port else None


def _matching_comfyui_procs(
    processes: dict[int, dict[str, Any]] | None,
    comfyui_url: str | None = None,
) -> list[dict[str, Any]]:
    if not processes:
        return []
    comfy_procs = [p for p in processes.values() if _is_comfyui_process(p)]
    if not comfy_procs:
        return []
    target_port = _url_port(comfyui_url)
    if target_port:
        port_matched = [p for p in comfy_procs if _proc_tokens_and_hints(p)[2] == target_port]
        if port_matched:
            return port_matched
    return comfy_procs


def _merge_telemetry_with_observed(
    telemetry_models: list[dict[str, Any]],
    observed_models: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine ComfyUI internal VRAM telemetry with queue/proc-observed filenames."""
    observed_by_role: dict[str, list[dict[str, Any]]] = {}
    for m in observed_models:
        observed_by_role.setdefault(m.get('role') or 'other', []).append(m)

    enriched: list[dict[str, Any]] = []
    covered_names: set[str] = set()
    for tm in telemetry_models:
        entry = dict(tm)
        files = [f for f in (entry.get('files') or []) if isinstance(f, str) and f]
        if entry.get('name_source') == 'class':
            candidates = [c for c in observed_by_role.get(entry.get('role') or 'other', []) if c['name'] not in covered_names]
            if len(candidates) == 1:
                entry['name'] = candidates[0]['name']
                if candidates[0].get('folder') and not entry.get('folder'):
                    entry['folder'] = candidates[0]['folder']
                files = [candidates[0]['name']]
        covered_names.add(entry['name'])
        covered_names.update(files)
        entry.pop('files', None)
        entry.pop('name_source', None)
        enriched.append(entry)

    extras = [m for m in observed_models if m.get('name') not in covered_names]
    return sort_comfyui_models(enriched + extras)


def merge_comfyui_active_models(
    snapshot: dict[str, Any],
    processes: dict[int, dict[str, Any]] | None,
    *,
    comfyui_url: str | None = None,
) -> dict[str, Any]:
    """Enrich comfyui_snapshot with ComfyUI internal telemetry and process-observed model files."""
    if not isinstance(snapshot, dict) or snapshot.get('status') != 'online':
        return snapshot
    comfy_procs = _matching_comfyui_procs(processes, comfyui_url)
    proc_paths: set[str] = set()
    max_proc_mb = 0.0
    for proc in comfy_procs:
        proc_paths.update(proc.get('paths') or set())
        for g in proc.get('gpus') or []:
            mem = g.get('memory_used_mb')
            if isinstance(mem, (int, float)) and mem > max_proc_mb:
                max_proc_mb = float(mem)
    proc_models = extract_comfyui_models_from_paths(proc_paths)
    existing = list(snapshot.get('active_models') or [])
    source = snapshot.get('active_models_source') or 'none'
    telemetry_online = snapshot.get('loaded_models_telemetry') == 'online'
    telemetry_models = list(snapshot.get('loaded_models') or []) if telemetry_online else []

    if telemetry_models:
        observed = sort_comfyui_models((existing if source == 'queue' else []) + proc_models)
        snapshot['active_models'] = _merge_telemetry_with_observed(telemetry_models, observed)
        snapshot['active_models_source'] = 'queue' if (source == 'queue' and existing) else 'telemetry'
        return snapshot

    if source == 'queue' and existing:
        snapshot['active_models'] = sort_comfyui_models(existing + proc_models)
        snapshot['active_models_source'] = 'queue'
    elif proc_models:
        snapshot['active_models'] = proc_models
        snapshot['active_models_source'] = 'process'
    elif source == 'history' and existing:
        has_torch_vram = any((d.get('torch_vram_total') or 0) > 0 for d in (snapshot.get('devices') or []))
        if has_torch_vram or max_proc_mb >= 1024.0:
            snapshot['active_models'] = sort_comfyui_models(existing)
            snapshot['active_models_source'] = 'history'
        else:
            snapshot['active_models'] = []
            snapshot['active_models_source'] = 'none'
    else:
        snapshot['active_models'] = sort_comfyui_models(existing)
        snapshot['active_models_source'] = source if existing else 'none'
    return snapshot


def _path_matches(candidate: str, model_path: str) -> bool:
    if not candidate or not model_path:
        return False
    norm_cand = candidate.replace('\\', '/')
    norm_model = model_path.replace('\\', '/').lstrip('/')
    return norm_cand == '/' + norm_model or norm_cand.endswith('/' + norm_model) or norm_cand == model_path


def attach_lmstudio_observations(
    loaded_models: list[dict[str, Any]],
    processes: dict[int, dict[str, Any]] | None,
    model_paths: dict[str, str] | None = None,
    matched_pids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Attach observed NVIDIA GPU usage to LM Studio loaded model entries."""
    paths_by_id = model_paths or {}
    if processes is None:
        return [{**m, 'observed_gpus': [], 'observed_status': 'unavailable'} for m in loaded_models]
    workers = [p for p in processes.values() if _is_lmstudio_worker(p)]
    path_counts: dict[str, int] = {}
    for m in loaded_models:
        mp = paths_by_id.get(m.get('instance_id') or '') or m.get('model_path')
        if isinstance(mp, str) and mp:
            path_counts[mp] = path_counts.get(mp, 0) + 1

    enriched = []
    for m in loaded_models:
        item = {k: v for k, v in m.items() if k != 'model_path'}
        mp = paths_by_id.get(item.get('instance_id') or '') or m.get('model_path')
        cfg = item.get('configured_placement')
        if isinstance(mp, str) and mp and path_counts.get(mp, 0) > 1:
            item['observed_gpus'] = []
            item['observed_status'] = 'ambiguous'
            enriched.append(item)
            continue
        matched = []
        if isinstance(mp, str) and mp:
            for proc in workers:
                paths = proc.get('paths') or set()
                if any(_path_matches(p, mp) for p in paths) or mp in (proc.get('cmdline') or ''):
                    matched.append(proc)
        if not matched and len(loaded_models) == 1 and len(workers) == 1:
            # Safe fallback only when a single model and single LM Studio worker exist.
            matched = [workers[0]]
        if len(matched) == 1:
            if matched_pids is not None and isinstance(matched[0].get('pid'), int):
                matched_pids.add(matched[0]['pid'])
            gpus = matched[0].get('gpus') or []
            item['observed_gpus'] = gpus
            item['observed_status'] = 'observed' if gpus else 'cpu_only'
        elif len(matched) > 1:
            item['observed_gpus'] = []
            item['observed_status'] = 'ambiguous'
        elif isinstance(cfg, dict) and cfg.get('mode') == 'cpu':
            item['observed_gpus'] = []
            item['observed_status'] = 'cpu_only'
        else:
            item['observed_gpus'] = []
            item['observed_status'] = 'unmatched'
        enriched.append(item)
    return enriched


def attach_ollama_observations(
    loaded_models: list[dict[str, Any]],
    processes: dict[int, dict[str, Any]] | None,
    blob_paths: dict[str, str],
    matched_pids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Attach observed NVIDIA GPU usage to Ollama loaded model entries."""
    if processes is None:
        return [{**m, 'configured_placement': None, 'observed_gpus': [], 'observed_status': 'unavailable'} for m in loaded_models]
    runners = [p for p in processes.values() if _is_ollama_runner(p)]
    enriched = []
    for m in loaded_models:
        item = {**m, 'configured_placement': None}
        if item.get('vram_bytes') == 0:
            item['observed_gpus'] = []
            item['observed_status'] = 'cpu_only'
            enriched.append(item)
            continue
        blob_path = blob_paths.get(item['name'])
        matched = []
        if blob_path:
            blob_name = os.path.basename(blob_path)
            for proc in runners:
                cmd = proc.get('cmdline') or ''
                paths = proc.get('paths') or set()
                if blob_path in cmd or blob_name in cmd or any(blob_name in p for p in paths):
                    matched.append(proc)
        if not matched and len(loaded_models) == 1 and len(runners) == 1:
            matched = [runners[0]]
        if len(matched) == 1:
            if matched_pids is not None and isinstance(matched[0].get('pid'), int):
                matched_pids.add(matched[0]['pid'])
            gpus = matched[0].get('gpus') or []
            item['observed_gpus'] = gpus
            item['observed_status'] = 'observed' if gpus else 'cpu_only'
        elif len(matched) > 1:
            item['observed_gpus'] = []
            item['observed_status'] = 'ambiguous'
        else:
            item['observed_gpus'] = []
            item['observed_status'] = 'unmatched'
        enriched.append(item)
    return enriched


_GENERIC_DIRS = {'', '/', 'home', 'root', 'tmp', 'var', 'usr', 'opt', 'bin', 'sbin', 'app', 'src', '.venv', 'venv', 'env'}
_SAFE_TOKEN_RE = re.compile(r'^[A-Za-z0-9._-]{1,36}$')
_PORT_RE = re.compile(r'^\d{2,5}$')
_PORT_FLAGS = {'--port', '-p', '--listenport', '--listen-port'}


def _extract_project_dir(cwd: str, process_name: str) -> str:
    if cwd:
        base = os.path.basename(cwd.rstrip('/'))
        if base and base.lower() not in _GENERIC_DIRS and not base.startswith('.'):
            # Ignore direct /home/<username> directories.
            parent = os.path.basename(os.path.dirname(cwd.rstrip('/')))
            if parent != 'home':
                return base
    if process_name:
        parts = [p for p in process_name.replace('\\', '/').split('/') if p]
        for idx, part in enumerate(parts):
            if part in ('.venv', 'venv', 'env') and idx > 0:
                candidate = parts[idx - 1]
                if candidate.lower() not in _GENERIC_DIRS:
                    return candidate
    return ''


def _parse_safe_hints(tokens: list[str]) -> tuple[str | None, str | None, str | None]:
    """Return (script_or_module, model_hint, port_hint) without exposing secrets."""
    script: str | None = None
    model: str | None = None
    port: str | None = None
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()
        if low in _PORT_FLAGS and i + 1 < len(tokens):
            val = tokens[i + 1].strip()
            if _PORT_RE.fullmatch(val):
                port = val
            i += 2
            continue
        if any(low.startswith(f + '=') for f in _PORT_FLAGS):
            val = tok.split('=', 1)[1].strip()
            if _PORT_RE.fullmatch(val):
                port = val
            i += 1
            continue
        if low == '--model' and i + 1 < len(tokens):
            val = os.path.basename(tokens[i + 1].strip().rstrip('/'))
            if _SAFE_TOKEN_RE.fullmatch(val):
                model = val
            i += 2
            continue
        if low.startswith('--model='):
            val = os.path.basename(tok.split('=', 1)[1].strip().rstrip('/'))
            if _SAFE_TOKEN_RE.fullmatch(val):
                model = val
            i += 1
            continue
        if low == '-m' and i + 1 < len(tokens) and script is None:
            val = tokens[i + 1].strip()
            if _SAFE_TOKEN_RE.fullmatch(val):
                script = val
            i += 2
            continue
        if not tok.startswith('-') and script is None:
            base = os.path.basename(tok.rstrip('/'))
            if base and _SAFE_TOKEN_RE.fullmatch(base.replace(':', '.')):
                script = base
        i += 1
    return script, model, port


def summarize_process_entry(proc: dict[str, Any]) -> tuple[str, str]:
    """Return a concise, secret-free (label, category) for a GPU compute process."""
    process_name = (proc.get('process_name') or '').strip()
    cmdline = (proc.get('cmdline') or '').strip()
    cwd = (proc.get('cwd') or '').strip()
    try:
        tokens = shlex.split(cmdline) if cmdline else []
    except ValueError:
        tokens = cmdline.split() if cmdline else []
    script, model_hint, port_hint = _parse_safe_hints(tokens)
    lower_cwd = cwd.lower()
    lower_cmd = cmdline.lower()

    if _is_comfyui_process(proc):
        label = f'ComfyUI (:{port_hint})' if port_hint else 'ComfyUI'
        return label, 'comfyui'
    if _is_lmstudio_worker(proc):
        return 'LM Studio Worker', 'lmstudio'
    if _is_ollama_runner(proc):
        return 'Ollama Runner', 'ollama'

    exe = os.path.basename(process_name.replace('\\', '/')) if process_name else 'process'
    is_python = 'python' in exe.lower()
    if is_python:
        project = _extract_project_dir(cwd, process_name)
        base_label = project or script or exe
        parts = [base_label]
        if model_hint and model_hint.lower() != base_label.lower():
            parts.append(model_hint)
        elif project and script and script.lower() not in ('app.py', 'main.py', 'server.py', 'uvicorn', 'gunicorn', 'server:app', 'main:app', 'app:app', project.lower()):
            parts.append(script)
        label = ' · '.join(parts)
        if port_hint:
            label = f'{label} (:{port_hint})'
        return label[:64], 'external'

    label = exe[:64]
    if port_hint:
        label = f'{label} (:{port_hint})'
    return label, 'external'


def attach_gpu_other_processes(
    gpus: list[dict[str, Any]],
    processes: dict[int, dict[str, Any]] | None,
    exclude_pids: set[int] | None = None,
    *,
    comfyui: dict[str, Any] | None = None,
    comfyui_url: str | None = None,
    max_per_gpu: int = 12,
) -> list[dict[str, Any]]:
    """Attach sanitized non-tracked compute processes to each GPU metrics dict."""
    excluded = exclude_pids or set()
    by_gpu: dict[int, list[dict[str, Any]]] = {}
    comfy_port = _url_port(comfyui_url)
    comfy_snap_models = list((comfyui or {}).get('active_models') or [])
    comfy_torch_vram: dict[int, int] = {}
    for dev in (comfyui or {}).get('devices') or []:
        dev_idx = dev.get('index')
        tv = dev.get('torch_vram_total')
        if isinstance(dev_idx, int) and isinstance(tv, (int, float)):
            comfy_torch_vram[dev_idx] = int(tv)
    comfy_pids = {
        pid for pid, proc in (processes or {}).items()
        if pid not in excluded and _is_comfyui_process(proc)
    }
    if processes:
        for pid, proc in processes.items():
            if pid in excluded:
                continue
            label, category = summarize_process_entry(proc)
            proc_models: list[dict[str, Any]] = []
            has_gpu_tagged_models = False
            if category == 'comfyui':
                proc_models = extract_comfyui_models_from_paths(proc.get('paths') or set())
                _, _, proc_port = _proc_tokens_and_hints(proc)
                matches_configured = bool(
                    comfy_snap_models
                    and (
                        (comfy_port and proc_port == comfy_port)
                        or (not comfy_port and len(comfy_pids) == 1)
                        or (len(comfy_pids) == 1 and not proc_port)
                    )
                )
                if matches_configured:
                    proc_models = sort_comfyui_models(comfy_snap_models + proc_models)
                has_gpu_tagged_models = any(isinstance(m.get('gpu_index'), int) for m in proc_models)
            gpu_rows = [r for r in (proc.get('gpus') or []) if isinstance(r.get('index'), int)]
            active_model_gpus: set[int] = set()
            if proc_models and gpu_rows:
                if len(gpu_rows) == 1:
                    active_model_gpus.add(gpu_rows[0]['index'])
                else:
                    for r in gpu_rows:
                        r_idx = r['index']
                        mem = r.get('memory_used_mb')
                        has_telemetry_on_gpu = any(
                            m.get('gpu_index') == r_idx and ((m.get('vram_bytes') or 0) > 0 or (m.get('offloaded_bytes') or 0) > 0)
                            for m in proc_models
                        )
                        if has_telemetry_on_gpu or (isinstance(mem, (int, float)) and mem >= 600.0) or comfy_torch_vram.get(r_idx, 0) > 0:
                            active_model_gpus.add(r_idx)
                    if not active_model_gpus:
                        best = max(gpu_rows, key=lambda r: (r.get('memory_used_mb') or -1.0, -r['index']))
                        active_model_gpus.add(best['index'])
            for gpu_row in gpu_rows:
                idx = gpu_row['index']
                entry: dict[str, Any] = {
                    'pid': pid,
                    'label': label,
                    'category': category,
                    'memory_used_mb': gpu_row.get('memory_used_mb'),
                }
                if proc_models and idx in active_model_gpus:
                    if has_gpu_tagged_models:
                        gpu_models = [
                            m for m in proc_models
                            if m.get('gpu_index') == idx
                            or (m.get('gpu_index') is None and m.get('memory_source') != 'comfyui_internal')
                        ]
                        if gpu_models:
                            entry['models'] = gpu_models
                    else:
                        entry['models'] = proc_models
                by_gpu.setdefault(idx, []).append(entry)
    for gpu in gpus:
        idx = gpu.get('index')
        if not isinstance(idx, int):
            continue
        rows = by_gpu.get(idx, [])
        rows.sort(key=lambda r: (-(r['memory_used_mb'] or -1.0), r['pid']))
        if len(rows) > max_per_gpu:
            head = rows[:max_per_gpu - 1]
            tail = rows[max_per_gpu - 1:]
            tail_mb = round(sum(r['memory_used_mb'] or 0.0 for r in tail), 1)
            head.append({
                'pid': None,
                'label': f'其他 {len(tail)} 個程序',
                'category': 'external',
                'memory_used_mb': tail_mb,
            })
            rows = head
        gpu['other_processes'] = rows
    return gpus
