"""Unit tests for disk-based model discovery and direct managed llama-server runtime."""
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.local_runtime import (
    LocalLlamaManager,
    control_direct_llama,
    discover_disk_comfyui_snapshot,
    discover_disk_lmstudio_models,
    _backend_rank,
)


def test_discover_disk_lmstudio_models_skips_mmproj_shards_and_empty(tmp_path):
    (tmp_path / 'unsloth' / 'Qwen3.5-4B-GGUF').mkdir(parents=True)
    (tmp_path / 'unsloth' / 'Qwen3.5-4B-GGUF' / 'Qwen3.5-4B-Q4_K_S.gguf').write_bytes(b'gguf-main')
    (tmp_path / 'unsloth' / 'Qwen3.5-4B-GGUF' / 'mmproj-F32.gguf').write_bytes(b'mmproj')
    (tmp_path / 'unsloth' / 'Qwen3.8-27B-GGUF').mkdir(parents=True)
    (tmp_path / 'unsloth' / 'Qwen3.8-27B-GGUF' / 'Qwen3.8-27B-UD-Q4_K_S-00001-of-00002.gguf').write_bytes(b'part1')
    (tmp_path / 'unsloth' / 'Qwen3.8-27B-GGUF' / 'Qwen3.8-27B-UD-Q4_K_S-00002-of-00002.gguf').write_bytes(b'part2')
    (tmp_path / 'gpustack' / 'bge-m3-GGUF').mkdir(parents=True)
    (tmp_path / 'gpustack' / 'bge-m3-GGUF' / 'bge-m3-Q8_0.gguf').write_bytes(b'embed')
    (tmp_path / 'empty.gguf').write_bytes(b'')

    catalog, index, _ = discover_disk_lmstudio_models([tmp_path])
    by_name = {m['name']: m for m in catalog}
    assert set(by_name) == {'qwen3.5-4b', 'qwen3.8-27b', 'text-embedding-bge-m3'}
    assert by_name['qwen3.5-4b']['model_type'] == 'llm'
    assert by_name['qwen3.8-27b']['model_type'] == 'llm'
    assert by_name['text-embedding-bge-m3']['model_type'] == 'embedding'
    assert index['qwen3.5-4b']['path'].name == 'Qwen3.5-4B-Q4_K_S.gguf'


def test_discover_disk_comfyui_snapshot_lists_models_and_ignores_placeholders(tmp_path):
    (tmp_path / 'diffusion_models').mkdir()
    (tmp_path / 'diffusion_models' / 'flux.safetensors').write_bytes(b'weights')
    (tmp_path / 'diffusion_models' / 'put_diffusion_model_files_here').write_bytes(b'')
    (tmp_path / 'unet_gguf').mkdir()
    (tmp_path / 'unet_gguf' / 'qwen.gguf').write_bytes(b'gguf')
    (tmp_path / 'custom_nodes').mkdir()
    (tmp_path / 'custom_nodes' / 'ignored.safetensors').write_bytes(b'skip')

    snap = discover_disk_comfyui_snapshot(tmp_path)
    assert snap['status'] == 'offline'
    assert snap['catalog_source'] == 'disk'
    assert snap['models_status'] == 'online'
    groups = {g['folder']: g['files'] for g in snap['model_groups']}
    assert groups['diffusion_models'] == ['flux.safetensors']
    assert groups['unet_gguf'] == ['qwen.gguf']
    assert 'custom_nodes' not in groups


def test_backend_rank_prefers_newest_cuda12():
    paths = [
        Path('/x/llama.cpp-linux-x86_64-avx2-2.20.1/llama-server'),
        Path('/x/llama.cpp-linux-x86_64-vulkan-avx2-2.99.0/llama-server'),
        Path('/x/llama.cpp-linux-x86_64-nvidia-cuda12-avx2-2.47.0/llama-server'),
        Path('/x/llama.cpp-linux-x86_64-nvidia-cuda12-avx2-2.83.0/llama-server'),
    ]
    assert max(paths, key=_backend_rank) == paths[-1]


def test_local_llama_manager_load_observe_and_unload(tmp_path, monkeypatch):
    # Create a tiny fake llama-server python script that serves /health and prints CUDA_VISIBLE_DEVICES.
    fake_bin = tmp_path / 'llama-server'
    fake_bin.write_text(
        '#!/usr/bin/env python3\n'
        'import os, sys, json\n'
        'from http.server import BaseHTTPRequestHandler, HTTPServer\n'
        'port = int(sys.argv[sys.argv.index("--port") + 1])\n'
        'class H(BaseHTTPRequestHandler):\n'
        '    def do_GET(self):\n'
        '        self.send_response(200)\n'
        '        self.end_headers()\n'
        '        self.wfile.write(json.dumps({"status": "ok", "cuda": os.getenv("CUDA_VISIBLE_DEVICES")}).encode())\n'
        '    def log_message(self, *a): pass\n'
        'HTTPServer(("127.0.0.1", port), H).serve_forever()\n',
        encoding='utf-8',
    )
    fake_bin.chmod(0o755)
    gguf = tmp_path / 'Qwen3.5-4B-Q4_K_S.gguf'
    gguf.write_bytes(b'fake-gguf-data')

    manager = LocalLlamaManager(binary_resolver=lambda: (fake_bin, tmp_path))
    disk_index = {'qwen3.5-4b': {'key': 'qwen3.5-4b', 'path': gguf, 'size_bytes': 14, 'model_type': 'llm'}}
    reports = []
    try:
        control_direct_llama(
            manager,
            disk_index,
            {'model': 'lmstudio:qwen3.5-4b', 'parameters': {'model_action': 'load', 'gpu_indices': [1]}},
            lambda **p: reports.append(p) or {'status': 'running'},
            available_gpu_indices=[0, 1],
        )
        assert reports[-1]['status'] == 'completed'
        assert reports[-1]['result']['loaded'] is True
        assert reports[-1]['result']['configured_placement']['mode'] == 'pinned'
        assert reports[-1]['result']['configured_placement']['gpu_indices'] == [1]

        inst = manager.get_instance('qwen3.5-4b')
        assert inst is not None and inst.is_alive()

        matched = set()
        loaded = manager.loaded_models(
            {inst.pid: {'pid': inst.pid, 'gpus': [{'index': 1, 'memory_used_mb': 2800.0}]}},
            matched_pids=matched,
        )
        assert matched == {inst.pid}
        assert loaded[0]['observed_status'] == 'observed'
        assert loaded[0]['observed_gpus'] == [{'index': 1, 'memory_used_mb': 2800.0}]

        unload_reports = []
        control_direct_llama(
            manager,
            disk_index,
            {'model': 'lmstudio:qwen3.5-4b', 'parameters': {'model_action': 'unload'}},
            lambda **p: unload_reports.append(p) or {'status': 'running'},
            available_gpu_indices=[0, 1],
        )
        assert unload_reports[-1]['status'] == 'completed'
        assert unload_reports[-1]['result']['loaded'] is False
        assert manager.get_instance('qwen3.5-4b') is None
    finally:
        manager.shutdown_all()
