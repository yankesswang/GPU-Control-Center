"""Disk-based model discovery and direct managed llama-server execution.

Allows the host agent to list installed GGUF / ComfyUI models directly from
configured filesystem paths and load/unload/serve GGUF models on specific GPUs
using a local llama-server binary even when the LM Studio desktop app is closed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from .runtimes import ExecutionUncertain

log = logging.getLogger('agent.local_runtime')

_QUANT_SUFFIX_RE = re.compile(
    r'(?:[-._](?:UD|IQ\d_[A-Z0-9_]+|Q\d_[A-Z0-9_]+|Q\d-[A-Z0-9-]+|Q\d_0|Q\d_1|F16|F32|BF16|FP16|FP32|MXFP4(?:_MOE)?))+$',
    re.IGNORECASE,
)
_SHARD_RE = re.compile(r'-(\d{5})-of-(\d{5})$', re.IGNORECASE)
_EMBED_HINTS = ('embed', 'bge-m3', 'minilm', 'e5-small', 'e5-base', 'e5-large')
_COMFY_MODEL_EXTS = {'.safetensors', '.gguf', '.ckpt', '.pt', '.pth', '.bin', '.onnx', '.sft'}
_COMFY_SKIP_DIRS = {'custom_nodes', 'configs', 'datasets', '__pycache__'}
_VERSION_RE = re.compile(r'(\d+)\.(\d+)\.(\d+)')


def abbreviate_home(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(Path.home().resolve())
        return '~' if str(rel) == '.' else f'~/{rel.as_posix()}'
    except ValueError:
        return str(path)


def resolve_lmstudio_model_dirs() -> tuple[list[Path], str]:
    """Return existing LM Studio model directories and a short display label."""
    env_dirs = os.getenv('LMSTUDIO_MODELS_DIR', '').strip()
    candidates: list[Path] = []
    if env_dirs:
        for raw in env_dirs.split(os.pathsep):
            raw = raw.strip()
            if raw:
                candidates.append(Path(raw).expanduser())
    else:
        settings_path = Path.home() / '.lmstudio' / 'settings.json'
        downloads_dir: Path | None = None
        try:
            data = json.loads(settings_path.read_text(encoding='utf-8'))
            raw_dl = data.get('downloadsFolder')
            if isinstance(raw_dl, str) and raw_dl.strip():
                downloads_dir = Path(raw_dl).expanduser()
        except (OSError, ValueError, TypeError):
            downloads_dir = None
        candidates.append(downloads_dir or (Path.home() / '.lmstudio' / 'models'))
        bundled = Path.home() / '.lmstudio' / '.internal' / 'bundled-models'
        if bundled.is_dir():
            candidates.append(bundled)

    seen: set[Path] = set()
    existing: list[Path] = []
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in seen:
            seen.add(resolved)
            existing.append(resolved)
    display = abbreviate_home(existing[0]) if existing else abbreviate_home(candidates[0])
    return existing, display


def _derive_gguf_metadata(path: Path) -> tuple[str, str, str]:
    """Return (model_key, display_name, model_type) from a GGUF file path."""
    stem = path.stem
    stem = _SHARD_RE.sub('', stem)
    cleaned = _QUANT_SUFFIX_RE.sub('', stem).strip(' -._') or stem
    hint_text = f'{path.name} {path.parent.name}'.lower()
    model_type = 'embedding' if any(h in hint_text for h in _EMBED_HINTS) else 'llm'
    key = cleaned.lower().replace(' ', '-')
    if model_type == 'embedding' and not key.startswith('text-embedding-'):
        key = 'text-embedding-' + key
    if len(key) > 86:
        key = key[:86].rstrip('-._')
    return key, cleaned, model_type


def _walk_gguf_files(root: Path, max_depth: int = 4) -> list[Path]:
    found: list[Path] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        depth = len(current.parts) - root_depth
        dirnames[:] = [d for d in sorted(dirnames) if not d.startswith('.') and depth < max_depth]
        for fname in sorted(filenames):
            if not fname.lower().endswith('.gguf'):
                continue
            low = fname.lower()
            if 'mmproj' in low:
                continue
            shard = _SHARD_RE.search(Path(fname).stem)
            if shard and int(shard.group(1)) != 1:
                continue
            fpath = current / fname
            try:
                if fpath.stat().st_size <= 0:
                    continue
            except OSError:
                continue
            found.append(fpath)
    return found


def discover_disk_lmstudio_models(
    dirs: list[Path] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    """Scan local model directories for GGUF files without requiring LM Studio app."""
    if dirs is None:
        scan_dirs, display_dir = resolve_lmstudio_model_dirs()
    else:
        scan_dirs = [Path(d).expanduser() for d in dirs if Path(d).expanduser().is_dir()]
        display_dir = abbreviate_home(scan_dirs[0]) if scan_dirs else ''

    catalog: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for root in scan_dirs:
        for fpath in _walk_gguf_files(root):
            key, display_name, model_type = _derive_gguf_metadata(fpath)
            if key in index:
                parent_slug = re.sub(r'[^a-z0-9._-]+', '-', fpath.parent.name.lower()).strip('-')
                alt = f'{key}-{parent_slug}'[:86] if parent_slug else f'{key}-2'[:86]
                suffix = 2
                while alt in index:
                    alt = f'{key[:80]}-{suffix}'
                    suffix += 1
                key = alt
            try:
                size_bytes = fpath.stat().st_size
            except OSError:
                size_bytes = 0
            index[key] = {
                'key': key,
                'path': fpath,
                'size_bytes': size_bytes,
                'model_type': model_type,
                'display_name': display_name,
            }
            catalog.append(dict(
                id='lmstudio:' + key,
                name=key,
                runtime='lmstudio',
                runtime_model=key,
                model_type=model_type,
                capability='lmstudio:' + key,
                description=f'本機 GGUF（磁碟掃描）· {display_name} · {model_type}',
            ))
    catalog.sort(key=lambda item: item['name'])
    return catalog, index, display_dir


def resolve_comfyui_models_dir() -> Path | None:
    env_dir = os.getenv('COMFYUI_MODELS_DIR', '').strip()
    candidates = [Path(env_dir).expanduser()] if env_dir else [Path.home() / 'ComfyUI' / 'models']
    for cand in candidates:
        if cand.is_dir():
            return cand.resolve()
    return None


def discover_disk_comfyui_snapshot(models_dir: Path | None = None) -> dict[str, Any]:
    """Scan ComfyUI's models directory on disk when the ComfyUI HTTP server is offline."""
    root = models_dir if models_dir is not None else resolve_comfyui_models_dir()
    if root is None or not root.is_dir():
        return {'status': 'offline', 'queue_status': 'unavailable', 'models_status': 'unavailable'}

    groups: list[dict[str, Any]] = []
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.'))
    except OSError:
        return {'status': 'offline', 'queue_status': 'unavailable', 'models_status': 'unavailable'}

    for folder_path in entries[:32]:
        folder = folder_path.name
        if folder in _COMFY_SKIP_DIRS:
            continue
        files: list[str] = []
        base_depth = len(folder_path.parts)
        for dirpath, dirnames, filenames in os.walk(folder_path):
            cur = Path(dirpath)
            depth = len(cur.parts) - base_depth
            dirnames[:] = [d for d in sorted(dirnames) if not d.startswith('.') and depth < 3]
            for fname in sorted(filenames):
                if fname.startswith('.'):
                    continue
                fpath = cur / fname
                if fpath.suffix.lower() not in _COMFY_MODEL_EXTS:
                    continue
                try:
                    if fpath.stat().st_size <= 0:
                        continue
                except OSError:
                    continue
                rel = fpath.relative_to(folder_path).as_posix()
                files.append(rel)
        groups.append({
            'folder': folder,
            'status': 'online',
            'count': len(files),
            'files': files[:200],
            'truncated': len(files) > 200,
        })

    return {
        'status': 'offline',
        'queue_status': 'unavailable',
        'models_status': 'online',
        'catalog_source': 'disk',
        'models_dir': abbreviate_home(root),
        'model_groups': groups,
        'model_folders_total': len(groups),
        'gpu_placement_supported': False,
    }


def _backend_rank(binary_path: Path) -> tuple[int, tuple[int, int, int]]:
    parent = binary_path.parent.name.lower()
    if 'nvidia-cuda12' in parent or 'cuda12' in parent:
        family = 4
    elif 'nvidia-cuda' in parent or 'cuda' in parent:
        family = 3
    elif 'vulkan' in parent:
        family = 2
    else:
        family = 1
    match = _VERSION_RE.search(parent)
    version = tuple(int(g) for g in match.groups()) if match else (0, 0, 0)
    return family, version


def find_llama_server() -> tuple[Path, Path] | None:
    """Locate an executable llama-server binary and its shared library directory."""
    env_bin = os.getenv('LLAMA_SERVER_BIN', '').strip()
    if env_bin:
        candidate = Path(env_bin).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate, candidate.parent

    on_path = shutil.which('llama-server')
    if on_path:
        p = Path(on_path)
        return p, p.parent

    backends_root = Path.home() / '.lmstudio' / 'extensions' / 'backends'
    if backends_root.is_dir():
        candidates = [
            p for p in backends_root.glob('llama.cpp-*/llama-server')
            if p.is_file() and os.access(p, os.X_OK)
        ]
        if candidates:
            best = max(candidates, key=_backend_rank)
            return best, best.parent
    return None


def _allocate_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _tail_log_file(log_path: Path, max_chars: int = 400) -> str:
    try:
        text = log_path.read_text(encoding='utf-8', errors='replace').strip()
        if not text:
            return ''
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        snippet = ' | '.join(lines[-4:])
        return snippet[-max_chars:]
    except OSError:
        return ''


@dataclass
class ManagedLlamaInstance:
    model: str
    model_type: str
    path: Path
    port: int
    process: subprocess.Popen[bytes]
    target_gpus: list[int] | None
    available_gpu_indices: list[int]
    size_bytes: int
    log_path: Path

    @property
    def pid(self) -> int:
        return self.process.pid

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def configured_placement(self) -> dict[str, Any]:
        all_indices = sorted({i for i in self.available_gpu_indices if isinstance(i, int) and i >= 0})
        if self.target_gpus:
            selected = sorted(self.target_gpus)
            disabled = [i for i in all_indices if i not in selected]
            return {
                'mode': 'pinned',
                'gpu_indices': selected,
                'main_gpu': selected[0],
                'disabled_gpus': disabled,
                'split_strategy': 'favorMainGpu' if len(selected) == 1 else 'evenly',
                'ratio': 1.0,
            }
        return {
            'mode': 'auto',
            'gpu_indices': all_indices,
            'main_gpu': None,
            'disabled_gpus': [],
            'split_strategy': 'evenly',
            'ratio': 1.0,
        }


class LocalLlamaManager:
    """Manages host-local llama-server processes for direct GGUF loading and inference."""

    def __init__(self, binary_resolver=find_llama_server):
        self._binary_resolver = binary_resolver
        self._lock = threading.RLock()
        self._instances: dict[str, ManagedLlamaInstance] = {}

    def binary_info(self) -> tuple[Path, Path] | None:
        return self._binary_resolver()

    def is_available(self) -> bool:
        return self.binary_info() is not None

    def _cleanup_instance(self, inst: ManagedLlamaInstance) -> None:
        try:
            if inst.is_alive():
                inst.process.terminate()
                try:
                    inst.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    inst.process.kill()
                    inst.process.wait(timeout=5)
        except OSError:
            pass
        try:
            inst.log_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _prune_dead_locked(self) -> None:
        dead = [name for name, inst in self._instances.items() if not inst.is_alive()]
        for name in dead:
            inst = self._instances.pop(name)
            try:
                inst.log_path.unlink(missing_ok=True)
            except OSError:
                pass

    def get_instance(self, model_name: str) -> ManagedLlamaInstance | None:
        with self._lock:
            self._prune_dead_locked()
            return self._instances.get(model_name)

    def has_instance(self, model_name: str) -> bool:
        return self.get_instance(model_name) is not None

    def unload(self, model_name: str) -> bool:
        with self._lock:
            inst = self._instances.pop(model_name, None)
        if inst is None:
            return False
        self._cleanup_instance(inst)
        return True

    def shutdown_all(self) -> None:
        with self._lock:
            instances = list(self._instances.values())
            self._instances.clear()
        for inst in instances:
            self._cleanup_instance(inst)

    def load(
        self,
        model_name: str,
        model_info: dict[str, Any],
        *,
        target_gpus: list[int] | None,
        available_gpu_indices: list[int],
        replace: bool = False,
        timeout_s: float = 180.0,
    ) -> ManagedLlamaInstance:
        normalized_gpus = sorted({i for i in target_gpus if isinstance(i, int) and i >= 0}) if target_gpus is not None else None
        if target_gpus is not None and not normalized_gpus:
            raise ValueError('target_gpus must be non-empty when provided')

        with self._lock:
            self._prune_dead_locked()
            existing = self._instances.get(model_name)
            if existing is not None:
                if not replace and existing.target_gpus == normalized_gpus:
                    return existing
                self._instances.pop(model_name, None)
        if existing is not None:
            self._cleanup_instance(existing)

        resolved = self.binary_info()
        if resolved is None:
            raise RuntimeError('找不到可用的 llama-server 執行檔，請設定 LLAMA_SERVER_BIN 或開啟 LM Studio。')
        binary_path, lib_dir = resolved
        model_path = Path(model_info['path'])
        if not model_path.is_file():
            raise RuntimeError(f'磁碟上找不到模型檔案：{model_path.name}')

        model_type = model_info.get('model_type') or 'llm'
        port = _allocate_free_port()
        env = os.environ.copy()
        existing_ld = env.get('LD_LIBRARY_PATH', '')
        env['LD_LIBRARY_PATH'] = f'{lib_dir}:{existing_ld}' if existing_ld else str(lib_dir)
        env['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
        if normalized_gpus is not None:
            env['CUDA_VISIBLE_DEVICES'] = ','.join(str(i) for i in normalized_gpus)
        else:
            env.pop('CUDA_VISIBLE_DEVICES', None)

        ctx_size = os.getenv('LLAMA_DIRECT_CTX_SIZE', '4096' if model_type != 'embedding' else '2048')
        cmd = [
            str(binary_path),
            '--model', str(model_path),
            '--alias', model_name,
            '--host', '127.0.0.1',
            '--port', str(port),
            '--ctx-size', str(ctx_size),
            '--n-gpu-layers', '999',
        ]
        if model_type == 'embedding':
            cmd.append('--embeddings')
        if normalized_gpus is not None and len(normalized_gpus) == 1:
            cmd.extend(['--split-mode', 'none', '--main-gpu', '0'])
        else:
            cmd.extend(['--split-mode', 'layer'])

        fd, raw_log = tempfile.mkstemp(prefix='gcc-llama-', suffix='.log')
        log_path = Path(raw_log)
        try:
            with os.fdopen(fd, 'wb') as log_file:
                proc = subprocess.Popen(
                    cmd,
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as exc:
            log_path.unlink(missing_ok=True)
            raise RuntimeError(f'無法啟動 llama-server：{exc}') from exc

        inst = ManagedLlamaInstance(
            model=model_name,
            model_type=model_type,
            path=model_path,
            port=port,
            process=proc,
            target_gpus=normalized_gpus,
            available_gpu_indices=list(available_gpu_indices),
            size_bytes=int(model_info.get('size_bytes') or 0),
            log_path=log_path,
        )

        deadline = time.monotonic() + timeout_s
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=3.0) as probe:
                while time.monotonic() < deadline:
                    rc = proc.poll()
                    if rc is not None:
                        tail = _tail_log_file(log_path)
                        self._cleanup_instance(inst)
                        detail = f'：{tail}' if tail else ''
                        raise RuntimeError(f'llama-server 載入模型失敗（exit {rc}）{detail}')
                    try:
                        res = probe.get('/health')
                        if res.status_code == 200:
                            with self._lock:
                                self._instances[model_name] = inst
                            return inst
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.4)
        except RuntimeError:
            raise
        except Exception as exc:
            self._cleanup_instance(inst)
            raise ExecutionUncertain(f'等待 llama-server 就緒時發生異常：{exc}') from exc

        self._cleanup_instance(inst)
        raise ExecutionUncertain('llama-server 載入逾時，已中止並釋放程序。')

    def loaded_models(
        self,
        gpu_processes: dict[int, dict[str, Any]] | None = None,
        matched_pids: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._prune_dead_locked()
            instances = list(self._instances.values())

        entries: list[dict[str, Any]] = []
        for inst in instances:
            entry: dict[str, Any] = {
                'name': inst.model,
                'instance_id': f'direct:{inst.model}',
                'size_bytes': inst.size_bytes or None,
                'vram_bytes': None,
                'configured_placement': inst.configured_placement(),
            }
            if gpu_processes is None:
                entry['observed_gpus'] = []
                entry['observed_status'] = 'unavailable'
            else:
                proc_info = gpu_processes.get(inst.pid)
                if proc_info:
                    gpus = proc_info.get('gpus') or []
                    entry['observed_gpus'] = gpus
                    entry['observed_status'] = 'observed' if gpus else 'cpu_only'
                    if matched_pids is not None:
                        matched_pids.add(inst.pid)
                else:
                    entry['observed_gpus'] = []
                    entry['observed_status'] = 'unmatched'
            entries.append(entry)
        return entries


def control_direct_llama(
    manager: LocalLlamaManager,
    disk_index: dict[str, dict[str, Any]],
    job: dict[str, Any],
    report,
    *,
    available_gpu_indices: list[int],
) -> None:
    """Execute load/unload directly via managed llama-server without LM Studio app."""
    model = job['model'].split(':', 1)[1]
    params = job.get('parameters', {})
    action = params['model_action']
    target_gpus = params.get('gpu_indices')
    replace = bool(params.get('replace', False))

    ack = report(status='running', runtime_state={'phase': action}, message=f'正在透過本地 llama-server 執行模型 {action}。')
    if ack.get('status') == 'cancelling':
        report(status='cancelled', message='模型操作尚未執行，已取消。')
        return

    if action == 'unload':
        manager.unload(model)
        report(
            status='completed',
            progress=100,
            runtime_state={'phase': 'completed'},
            message='已卸載本地 llama-server 模型並釋放顯存。',
            result={'runtime': 'lmstudio', 'backend': 'direct_llama', 'model': model, 'model_action': 'unload', 'loaded': False},
        )
        return

    model_info = disk_index.get(model)
    if not model_info:
        raise RuntimeError(f'磁碟模型目錄中找不到 {model}。')

    inst = manager.load(
        model,
        model_info,
        target_gpus=target_gpus,
        available_gpu_indices=available_gpu_indices,
        replace=replace,
    )
    result: dict[str, Any] = {
        'runtime': 'lmstudio',
        'backend': 'direct_llama',
        'model': model,
        'model_action': 'load',
        'loaded': True,
        'configured_placement': inst.configured_placement(),
    }
    if target_gpus is not None:
        result['requested_gpu_indices'] = target_gpus
    report(
        status='completed',
        progress=100,
        runtime_state={'phase': 'completed'},
        message='本地 llama-server 已載入模型並就緒。',
        result=result,
    )


def generate_direct_llama(
    manager: LocalLlamaManager,
    disk_index: dict[str, dict[str, Any]],
    job: dict[str, Any],
    report,
    *,
    available_gpu_indices: list[int],
) -> None:
    """Run real OpenAI-compatible streaming inference directly on a managed llama-server."""
    from .services import generate_lmstudio

    model = job['model'].split(':', 1)[1]
    inst = manager.get_instance(model)
    loaded_temporarily = False
    if inst is None:
        model_info = disk_index.get(model)
        if not model_info:
            raise RuntimeError(f'磁碟模型目錄中找不到 {model}。')
        ack = report(status='running', runtime_state={'phase': 'loading', 'text': ''}, message='正在啟動本地 llama-server 載入模型。')
        if ack.get('status') == 'cancelling':
            report(status='cancelled', message='尚未啟動本地推論，已取消。')
            return
        inst = manager.load(
            model,
            model_info,
            target_gpus=None,
            available_gpu_indices=available_gpu_indices,
            replace=False,
        )
        loaded_temporarily = True

    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{inst.port}', timeout=httpx.Timeout(180, connect=5)) as direct_client:
            generate_lmstudio(direct_client, job, report)
    finally:
        if loaded_temporarily and not job.get('keep_model_loaded', False):
            manager.unload(model)
