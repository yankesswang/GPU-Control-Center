import json
import httpx
import pytest
from app.placement import (
    attach_gpu_other_processes,
    attach_lmstudio_observations,
    attach_ollama_observations,
    build_lmstudio_gpu_config,
    extract_comfyui_models_from_paths,
    merge_comfyui_active_models,
    normalize_lmstudio_gpu_config,
    parse_nvidia_compute_apps,
    summarize_process_entry,
)
from app.runtimes import control_ollama, generate_ollama, resident_ollama, ExecutionUncertain
from app.services import (
    lmstudio_inventory,
    control_lmstudio,
    generate_lmstudio,
    comfyui_snapshot,
    comfyui_loaded_models_telemetry,
    comfyui_catalog_from_snapshot,
    control_comfyui,
)


def client_for(handler):
    return httpx.Client(base_url='http://runtime.test', transport=httpx.MockTransport(handler))


def inventory(loaded=False):
    return {'models':[{'key':'test','type':'llm','display_name':'Test',
                       'loaded_instances':[{'id':'real-instance-id'}] if loaded else []}]}


@pytest.mark.parametrize('action', ['load', 'unload'])
def test_lmstudio_controls_use_native_api_and_real_instance_ids(action):
    loaded = action == 'unload'
    def handler(request):
        nonlocal loaded
        if request.method == 'GET':
            return httpx.Response(200, json=inventory(loaded))
        body = json.loads(request.content)
        if action == 'unload':
            assert request.url.path == '/api/v1/models/unload'
            assert body == {'instance_id':'real-instance-id'}
        else:
            assert request.url.path == '/api/v1/models/load'
            assert body['model'] == 'test'
        loaded = action == 'load'
        return httpx.Response(200, json={'status':'loaded','instance_id':'real-instance-id'})
    reports = []
    with client_for(handler) as client:
        control_lmstudio(client, {'model':'lmstudio:test','parameters':{'model_action':action}}, lambda **p: reports.append(p) or {'status':'running'})
    assert reports[-1]['status'] == 'completed'
    assert reports[-1]['result']['loaded'] == (action == 'load')


def test_lmstudio_discovery_does_not_invent_memory_usage():
    with client_for(lambda r: httpx.Response(200, json=inventory(True))) as client:
        catalog, state = lmstudio_inventory(client)
    assert catalog[0]['runtime'] == 'lmstudio'
    assert state['loaded_models'][0] == {'name':'test','instance_id':'real-instance-id','size_bytes':None,'vram_bytes':None}


def test_lmstudio_stream_requires_done_and_keeps_model_resident():
    calls, reports = [], []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Hello"}}]}\n\ndata: {"choices":[],"usage":{"completion_tokens":1}}\n\ndata: [DONE]\n\n')
    with client_for(handler) as client:
        generate_lmstudio(client, {'model':'lmstudio:test','parameters':{'prompt':'Hi'}}, lambda **p: reports.append(p) or {'status':'running'})
    assert reports[-1]['result']['text'] == 'Hello'
    assert reports[-1]['result']['tokens_generated'] == 1
    assert calls == ['/v1/chat/completions']
    with client_for(lambda r: httpx.Response(200, text='data: {"choices":[]}\n')) as client:
        with pytest.raises(ExecutionUncertain):
            generate_lmstudio(client, {'model':'lmstudio:test','parameters':{'prompt':'Hi'}}, lambda **p: {'status':'running'})


def test_model_control_network_uncertainty_retains_slot():
    def handler(request):
        raise httpx.ReadTimeout('uncertain', request=request)
    with client_for(handler) as client:
        with pytest.raises(ExecutionUncertain):
            control_lmstudio(client, {'model':'lmstudio:test','parameters':{'model_action':'load'}}, lambda **p: {'status':'running'})


def test_ollama_manual_residency_is_preserved_after_generation():
    reports = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'models':[{'name':'test'}]})
        assert json.loads(request.content)['keep_alive'] == -1
        return httpx.Response(200, text='{"done":true,"message":{"content":"Hi"},"eval_count":1}\n')
    with client_for(handler) as client:
        generate_ollama(client, {'model':'ollama:test','parameters':{'prompt':'Hi'},'keep_model_loaded':True}, lambda **p: reports.append(p) or {'status':'running'})
    assert reports[-1]['status'] == 'completed'
    assert reports[-1]['result']['model_released'] is False


@pytest.mark.parametrize('action', ['load', 'unload'])
def test_ollama_native_model_control(action):
    reports=[]
    def handler(request):
        if request.method=='POST':
            payload=json.loads(request.content)
            assert payload['messages']==[]
            assert payload['keep_alive']==(-1 if action=='load' else 0)
            return httpx.Response(200,json={'done':True})
        return httpx.Response(200,json={'models':[{'name':'test'}] if action=='load' else []})
    with client_for(handler) as client:
        control_ollama(client,{'model':'ollama:test','parameters':{'model_action':action}},lambda **p:reports.append(p) or {'status':'running'})
    assert reports[-1]['status']=='completed'


def test_comfyui_snapshot_is_read_only_and_does_not_forward_workflows():
    def handler(request):
        assert request.method == 'GET'
        return httpx.Response(200,json={
            '/system_stats':{'system':{'comfyui_version':'test','argv':['private']},'devices':[]},
            '/queue':{'queue_running':[[0,'prompt-id',{'secret':'workflow'}]],'queue_pending':[]},
            '/models':['checkpoints'], '/models/checkpoints':['model.safetensors'],
        }[request.url.path])
    with client_for(handler) as client:
        state = comfyui_snapshot(client)
    assert state['running']==[{'id':'prompt-id'}]
    assert state['model_groups'][0]['files']==['model.safetensors']
    assert 'secret' not in json.dumps(state) and 'private' not in json.dumps(state)


def test_comfyui_failure_does_not_report_empty_queue_as_fact():
    with client_for(lambda r:httpx.Response(503)) as client:
        state=comfyui_snapshot(client)
    assert state['status']=='offline'
    assert state['queue_status']=='unavailable'
    assert 'running' not in state


class FakeSdkHandle:
    def __init__(self, identifier, path, load_config):
        self.identifier = identifier
        self._info = {'identifier': identifier, 'path': path}
        self._cfg = load_config

    def get_info(self):
        return self._info

    def get_load_config(self):
        return self._cfg


class FakeSdkNamespace:
    def __init__(self, calls, kind, on_load):
        self._calls = calls
        self._kind = kind
        self._on_load = on_load

    def load_new_instance(self, model_key, instance_identifier=None, ttl=None, config=None):
        self._calls.append((self._kind, model_key, instance_identifier, ttl, config))
        self._on_load(model_key, config)


class FakeSdkClient:
    def __init__(self, calls, loaded_handles, on_load):
        self.llm = FakeSdkNamespace(calls, 'llm', on_load)
        self.embedding = FakeSdkNamespace(calls, 'embedding', on_load)
        self._loaded_handles = loaded_handles

    def list_loaded_models(self):
        return list(self._loaded_handles())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_lmstudio_gpu_config_build_and_normalize():
    cfg_single = build_lmstudio_gpu_config([1], [0, 1])
    assert cfg_single == {'ratio': 1.0, 'mainGpu': 1, 'splitStrategy': 'favorMainGpu', 'disabledGpus': [0]}
    norm_single = normalize_lmstudio_gpu_config({'gpu': cfg_single}, [0, 1])
    assert norm_single['mode'] == 'pinned'
    assert norm_single['gpu_indices'] == [1]
    assert norm_single['disabled_gpus'] == [0]

    cfg_auto = build_lmstudio_gpu_config(None, [0, 1])
    assert cfg_auto == {'ratio': 1.0, 'splitStrategy': 'evenly', 'disabledGpus': []}
    norm_auto = normalize_lmstudio_gpu_config({'gpu': cfg_auto}, [0, 1])
    assert norm_auto['mode'] == 'auto'
    assert norm_auto['gpu_indices'] == [0, 1]


def test_lmstudio_sdk_load_targets_gpu_and_replaces_existing_instance():
    loaded = True
    current_cfg = {'gpu': {'ratio': 1.0, 'splitStrategy': 'evenly', 'disabledGpus': []}}
    sdk_calls = []
    unloads = []

    def handler(request):
        nonlocal loaded
        if request.method == 'GET':
            return httpx.Response(200, json=inventory(loaded))
        if request.url.path == '/api/v1/models/unload':
            unloads.append(json.loads(request.content))
            loaded = False
            return httpx.Response(200, json={'status': 'unloaded'})
        raise AssertionError(f'Unexpected REST path {request.url.path}')

    def on_load(model_key, config):
        nonlocal loaded, current_cfg
        loaded = True
        current_cfg = config

    def factory(_url):
        return FakeSdkClient(
            sdk_calls,
            lambda: [FakeSdkHandle('real-instance-id', 'unsloth/test/test.gguf', current_cfg)] if loaded else [],
            on_load,
        )

    reports = []
    with client_for(handler) as client:
        control_lmstudio(
            client,
            {'model': 'lmstudio:test', 'parameters': {'model_action': 'load', 'gpu_indices': [1], 'replace': True}},
            lambda **p: reports.append(p) or {'status': 'running'},
            lmstudio_url='http://127.0.0.1:1234',
            available_gpu_indices=[0, 1],
            sdk_client_factory=factory,
        )

    assert unloads == [{'instance_id': 'real-instance-id'}]
    assert len(sdk_calls) == 1
    assert sdk_calls[0][0] == 'llm'
    assert sdk_calls[0][4]['gpu'] == {'ratio': 1.0, 'mainGpu': 1, 'splitStrategy': 'favorMainGpu', 'disabledGpus': [0]}
    assert reports[-1]['status'] == 'completed'
    assert reports[-1]['result']['requested_gpu_indices'] == [1]
    assert reports[-1]['result']['configured_placement']['mode'] == 'pinned'
    assert reports[-1]['result']['configured_placement']['gpu_indices'] == [1]


def test_process_observation_separates_configured_and_observed_gpus():
    csv_output = '\n'.join([
        'GPU-AAA, 1001, /home/u/.lmstudio/.internal/utils/node, 386',
        'GPU-BBB, 1001, /home/u/.lmstudio/.internal/utils/node, 3074',
        'GPU-AAA, 2002, /usr/lib/ollama/llama-server, 1328',
    ])
    procs = parse_nvidia_compute_apps(csv_output, {'GPU-AAA': 0, 'GPU-BBB': 1})
    procs[1001]['cmdline'] = 'node llmworker.js'
    procs[1001]['paths'] = {'/home/u/.lmstudio/models/unsloth/Qwen/model.gguf'}
    procs[2002]['cmdline'] = '/usr/lib/ollama/llama-server --model /home/u/.ollama/models/blobs/sha256-abc123'
    procs[2002]['paths'] = {'/home/u/.ollama/models/blobs/sha256-abc123'}

    lms_enriched = attach_lmstudio_observations(
        [{'name': 'qwen', 'instance_id': 'inst-1', 'size_bytes': None, 'vram_bytes': None,
          'configured_placement': {'mode': 'pinned', 'gpu_indices': [1], 'disabled_gpus': [0]}}],
        procs,
        {'inst-1': 'unsloth/Qwen/model.gguf'},
    )
    assert lms_enriched[0]['observed_status'] == 'observed'
    assert lms_enriched[0]['observed_gpus'] == [
        {'index': 1, 'memory_used_mb': 3074.0},
        {'index': 0, 'memory_used_mb': 386.0},
    ]

    ollama_enriched = attach_ollama_observations(
        [{'name': 'qwen2.5:0.5b', 'size_bytes': 900, 'vram_bytes': 900}],
        procs,
        {'qwen2.5:0.5b': '/home/u/.ollama/models/blobs/sha256-abc123'},
    )
    assert ollama_enriched[0]['configured_placement'] is None
    assert ollama_enriched[0]['observed_status'] == 'observed'
    assert ollama_enriched[0]['observed_gpus'] == [{'index': 0, 'memory_used_mb': 1328.0}]


def test_ollama_resident_attaches_observed_gpus_via_show():
    procs = {
        2002: {
            'pid': 2002,
            'process_name': '/home/u/.local/ollama/llama-server',
            'cmdline': 'llama-server --model /home/u/.ollama/models/blobs/sha256-xyz',
            'paths': set(),
            'gpus': [{'index': 1, 'memory_used_mb': 1024.0}],
        }
    }
    def handler(request):
        if request.url.path == '/api/ps':
            return httpx.Response(200, json={'models': [{'name': 'tiny', 'size': 1000, 'size_vram': 1000}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'modelfile': 'FROM /home/u/.ollama/models/blobs/sha256-xyz\n'})
        raise AssertionError(request.url.path)
    with client_for(handler) as client:
        state = resident_ollama(client, gpu_processes=procs, blob_cache={})
    assert state['loaded_models'][0]['observed_gpus'] == [{'index': 1, 'memory_used_mb': 1024.0}]
    assert state['loaded_models'][0]['configured_placement'] is None


def test_comfyui_catalog_and_control_load_unload():
    snap = {
        'status': 'online',
        'model_groups': [
            {'folder': 'diffusion_models', 'status': 'online', 'files': ['flux.safetensors']},
            {'folder': 'unet_gguf', 'status': 'online', 'files': ['qwen.gguf']},
            {'folder': 'loras', 'status': 'online', 'files': ['style.safetensors']},
        ],
    }
    catalog = comfyui_catalog_from_snapshot(snap)
    assert [m['id'] for m in catalog] == ['comfyui:flux.safetensors', 'comfyui:qwen.gguf']

    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == '/models/diffusion_models':
            return httpx.Response(200, json=['flux.safetensors'])
        if request.url.path == '/object_info/SelectModelDevice':
            return httpx.Response(200, json={'SelectModelDevice': {}})
        if request.url.path == '/free':
            assert json.loads(request.content) == {'unload_models': True, 'free_memory': True}
            return httpx.Response(200)
        if request.url.path == '/prompt':
            body = json.loads(request.content)['prompt']
            assert body['1']['class_type'] == 'UNETLoader'
            assert body['1']['inputs']['unet_name'] == 'flux.safetensors'
            assert body['2']['class_type'] == 'SelectModelDevice'
            assert body['2']['inputs']['device'] == 'gpu:1'
            return httpx.Response(200, json={'prompt_id': 'p-123', 'node_errors': {}})
        if request.url.path == '/history/p-123':
            return httpx.Response(200, json={'p-123': {'status': {'status_str': 'success', 'completed': True}}})
        raise AssertionError(request.url.path)

    reports = []
    with client_for(handler) as client:
        control_comfyui(
            client,
            {'model': 'comfyui:flux.safetensors', 'parameters': {'model_action': 'load', 'gpu_indices': [1], 'replace': True}},
            lambda **p: reports.append(p) or {'status': 'running'},
        )
    assert reports[-1]['status'] == 'completed'
    assert reports[-1]['result']['loaded'] is True
    assert reports[-1]['result']['target_device'] == 'gpu:1'
    assert ('POST', '/free') in calls

    unload_reports = []
    with client_for(handler) as client:
        control_comfyui(
            client,
            {'model': 'comfyui:*', 'parameters': {'model_action': 'unload'}},
            lambda **p: unload_reports.append(p) or {'status': 'running'},
        )
    assert unload_reports[-1]['status'] == 'completed'
    assert unload_reports[-1]['result']['cleared_all_cache'] is True


def test_other_gpu_processes_summarizes_safely_and_excludes_matched_llm():
    csv_output = '\n'.join([
        'GPU-AAA, 1001, /home/u/.lmstudio/.internal/utils/node, 3410',
        'GPU-AAA, 2001, /home/u/Project/LiveTalking/tts_server/.venv/bin/python, 5118',
        'GPU-AAA, 2002, .venv/bin/python, 548',
        'GPU-BBB, 2003, .venv/bin/python, 9174',
    ])
    procs = parse_nvidia_compute_apps(csv_output, {'GPU-AAA': 0, 'GPU-BBB': 1})
    procs[1001].update(cmdline='node llmworker.js', cwd='/home/u', paths={'/home/u/.lmstudio/models/qwen.gguf'})
    procs[2001].update(
        cmdline='.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8091 --api-key super-secret-token',
        cwd='/home/u/Project/LiveTalking/tts_server',
        paths=set(),
    )
    procs[2002].update(
        cmdline='.venv/bin/python main.py --listen 127.0.0.1 --port 8188 --enable-triton-backend',
        cwd='/home/u/ComfyUI',
        paths=set(),
    )
    procs[2003].update(
        cmdline='.venv/bin/python app.py --model musetalk --listenport 8012 --token my-secret',
        cwd='/home/u/Project/LiveTalking',
        paths=set(),
    )

    matched = set()
    attach_lmstudio_observations(
        [{'name': 'qwen', 'instance_id': 'inst-1', 'size_bytes': None, 'vram_bytes': None}],
        procs,
        {'inst-1': 'qwen.gguf'},
        matched_pids=matched,
    )
    assert matched == {1001}

    gpus = [{'index': 0, 'name': 'RTX 4090'}, {'index': 1, 'name': 'RTX 4090'}]
    attach_gpu_other_processes(gpus, procs, exclude_pids=matched)

    assert [p['pid'] for p in gpus[0]['other_processes']] == [2001, 2002]
    assert gpus[0]['other_processes'][0] == {
        'pid': 2001, 'label': 'tts_server (:8091)', 'category': 'external', 'memory_used_mb': 5118.0,
    }
    assert gpus[0]['other_processes'][1] == {
        'pid': 2002, 'label': 'ComfyUI (:8188)', 'category': 'comfyui', 'memory_used_mb': 548.0,
    }
    assert gpus[1]['other_processes'] == [{
        'pid': 2003, 'label': 'LiveTalking · musetalk (:8012)', 'category': 'external', 'memory_used_mb': 9174.0,
    }]
    serialized = json.dumps(gpus)
    assert 'super-secret-token' not in serialized
    assert 'my-secret' not in serialized
    assert summarize_process_entry({'process_name': '/usr/bin/blender', 'cmdline': 'blender --background'}) == ('blender', 'external')


def test_comfyui_active_models_from_queue_and_history_without_leaking_prompts():
    workflow = {
        'unet': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'minimax_h3.safetensors', 'weight_dtype': 'default'}},
        'clip': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': 'qwen3vl.safetensors', 'type': 'minimax'}},
        'lora': {'class_type': 'LoraLoaderModelOnly', 'inputs': {'model': ['unet', 0], 'lora_name': 'turbo_lora.safetensors'}},
        'vae': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'video_vae.safetensors'}},
        'img': {'class_type': 'LoadImage', 'inputs': {'image': 'private_user_photo.png'}},
        'cond': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'super secret prompt text'}},
    }
    def running_handler(request):
        return httpx.Response(200, json={
            '/system_stats': {'system': {'comfyui_version': '0.45.0'}, 'devices': [{'index': 0, 'torch_vram_total': 1024}]},
            '/queue': {'queue_running': [[0, 'p-1', workflow]], 'queue_pending': []},
            '/models': ['diffusion_models', 'text_encoders', 'loras', 'vae'],
            '/models/diffusion_models': ['minimax_h3.safetensors'],
            '/models/text_encoders': ['qwen3vl.safetensors'],
            '/models/loras': ['turbo_lora.safetensors'],
            '/models/vae': ['video_vae.safetensors'],
        }[request.url.path])
    with client_for(running_handler) as client:
        snap = comfyui_snapshot(client)
    assert snap['active_models_source'] == 'queue'
    assert [(m['role'], m['name']) for m in snap['active_models']] == [
        ('diffusion', 'minimax_h3.safetensors'),
        ('text_encoder', 'qwen3vl.safetensors'),
        ('lora', 'turbo_lora.safetensors'),
        ('vae', 'video_vae.safetensors'),
    ]
    dumped = json.dumps(snap)
    assert 'super secret prompt text' not in dumped
    assert 'private_user_photo.png' not in dumped

    def idle_history_handler(request):
        path = request.url.path
        if path == '/history':
            return httpx.Response(200, json={'p-1': {'prompt': [1, 'p-1', workflow]}})
        return httpx.Response(200, json={
            '/system_stats': {'system': {'comfyui_version': '0.45.0'}, 'devices': [{'index': 0, 'torch_vram_total': 2048}]},
            '/queue': {'queue_running': [], 'queue_pending': []},
            '/models': ['diffusion_models'],
            '/models/diffusion_models': ['minimax_h3.safetensors'],
        }[path])
    with client_for(idle_history_handler) as client:
        idle_snap = comfyui_snapshot(client)
    assert idle_snap['active_models_source'] == 'history'
    assert idle_snap['active_models'][0] == {'name': 'minimax_h3.safetensors', 'role': 'diffusion', 'folder': 'diffusion_models'}


def test_comfyui_proc_paths_and_multi_gpu_attachment():
    paths = {
        '/home/u/ComfyUI/models/diffusion_models/flux_dev.safetensors',
        '/home/u/ComfyUI/models/text_encoders/t5xxl.safetensors',
        '/home/u/ComfyUI/models/vae/ae.safetensors',
        '/home/u/ComfyUI/.venv/lib/python3.12/site-packages/safetensors/_safetensors_rust.abi3.so',
        '/home/u/ComfyUI/models/custom_nodes/ignore_me.pt',
    }
    extracted = extract_comfyui_models_from_paths(paths)
    assert extracted == [
        {'name': 'flux_dev.safetensors', 'role': 'diffusion', 'folder': 'diffusion_models'},
        {'name': 't5xxl.safetensors', 'role': 'text_encoder', 'folder': 'text_encoders'},
        {'name': 'ae.safetensors', 'role': 'vae', 'folder': 'vae'},
    ]
    assert '/home/u/' not in json.dumps(extracted)

    procs = {
        27203: {
            'pid': 27203,
            'process_name': '.venv/bin/python',
            'cmdline': '.venv/bin/python main.py --listen 127.0.0.1 --port 8188',
            'cwd': '/home/u/ComfyUI',
            'paths': paths,
            'gpus': [
                {'index': 0, 'memory_used_mb': 22664.0},
                {'index': 1, 'memory_used_mb': 386.0},
            ],
        }
    }
    comfy_snap = {
        'status': 'online',
        'devices': [
            {'index': 0, 'torch_vram_total': 1073741824},
            {'index': 1, 'torch_vram_total': 0},
        ],
        'active_models': [],
        'active_models_source': 'none',
    }
    merge_comfyui_active_models(comfy_snap, procs, comfyui_url='http://127.0.0.1:8188')
    assert comfy_snap['active_models_source'] == 'process'
    assert [m['name'] for m in comfy_snap['active_models']] == ['flux_dev.safetensors', 't5xxl.safetensors', 'ae.safetensors']

    gpus = [{'index': 0, 'name': 'RTX 4090'}, {'index': 1, 'name': 'RTX 4090'}]
    attach_gpu_other_processes(gpus, procs, comfyui=comfy_snap, comfyui_url='http://127.0.0.1:8188')
    # GPU 0 has the actual VRAM allocation and receives the model list.
    assert [m['name'] for m in gpus[0]['other_processes'][0]['models']] == [
        'flux_dev.safetensors', 't5xxl.safetensors', 'ae.safetensors',
    ]
    # GPU 1 only has CUDA context overhead (386 MiB, torch_vram_total=0) and should not claim the model.
    assert 'models' not in gpus[1]['other_processes'][0]


def test_comfyui_internal_vram_telemetry_merges_and_routes_per_gpu():
    def handler(request):
        if request.url.path == '/gcc/loaded_models':
            return httpx.Response(200, json={
                'schema_version': 1,
                'models': [
                    {
                        'name': 'minimax_h3.safetensors',
                        'files': ['minimax_h3.safetensors'],
                        'role': 'diffusion',
                        'folder': 'diffusion_models',
                        'device': 'cuda:0',
                        'gpu_index': 0,
                        'vram_bytes': 18 * 1024**3,
                        'total_bytes': 20 * 1024**3,
                        'offloaded_bytes': 2 * 1024**3,
                        'ram_bytes': 2 * 1024**3,
                        'is_dynamic': True,
                        'currently_used': True,
                        'name_source': 'cached_init',
                    },
                    {
                        'name': 'qwen3vl.safetensors',
                        'files': ['qwen3vl.safetensors'],
                        'role': 'text_encoder',
                        'folder': 'text_encoders',
                        'device': 'cuda:0',
                        'gpu_index': 0,
                        'vram_bytes': 0,
                        'total_bytes': 15 * 1024**3,
                        'offloaded_bytes': 15 * 1024**3,
                        'ram_bytes': 15 * 1024**3,
                        'is_dynamic': True,
                        'currently_used': False,
                        'name_source': 'cached_init',
                    },
                    {
                        'name': 'video_vae.safetensors',
                        'files': ['video_vae.safetensors'],
                        'role': 'vae',
                        'folder': 'vae',
                        'device': 'cuda:1',
                        'gpu_index': 1,
                        'vram_bytes': 2 * 1024**3,
                        'total_bytes': 2 * 1024**3,
                        'offloaded_bytes': 0,
                        'ram_bytes': 0,
                        'is_dynamic': False,
                        'currently_used': False,
                        'name_source': 'cached_init',
                    },
                ],
            })
        return httpx.Response(404)

    with client_for(handler) as client:
        telemetry = comfyui_loaded_models_telemetry(client)
    assert telemetry is not None
    assert [m['name'] for m in telemetry] == ['minimax_h3.safetensors', 'video_vae.safetensors', 'qwen3vl.safetensors']
    assert telemetry[0]['vram_bytes'] == 18 * 1024**3
    assert telemetry[0]['memory_source'] == 'comfyui_internal'

    with client_for(lambda _r: httpx.Response(404)) as client:
        assert comfyui_loaded_models_telemetry(client) is None

    procs = {
        27203: {
            'pid': 27203,
            'process_name': '.venv/bin/python',
            'cmdline': '.venv/bin/python main.py --listen 127.0.0.1 --port 8188',
            'cwd': '/home/u/ComfyUI',
            'paths': {
                '/home/u/ComfyUI/models/diffusion_models/minimax_h3.safetensors',
                '/home/u/ComfyUI/models/loras/turbo_lora.safetensors',
                '/home/u/ComfyUI/models/text_encoders/qwen3vl.safetensors',
                '/home/u/ComfyUI/models/vae/video_vae.safetensors',
            },
            'gpus': [
                {'index': 0, 'memory_used_mb': 19500.0},
                {'index': 1, 'memory_used_mb': 2400.0},
            ],
        }
    }
    snap = {
        'status': 'online',
        'devices': [{'index': 0, 'torch_vram_total': 1024}, {'index': 1, 'torch_vram_total': 1024}],
        'loaded_models_telemetry': 'online',
        'loaded_models': telemetry,
        'active_models': [],
        'active_models_source': 'none',
    }
    merge_comfyui_active_models(snap, procs, comfyui_url='http://127.0.0.1:8188')
    assert snap['active_models_source'] == 'telemetry'
    by_name = {m['name']: m for m in snap['active_models']}
    assert by_name['minimax_h3.safetensors']['vram_bytes'] == 18 * 1024**3
    assert by_name['qwen3vl.safetensors']['vram_bytes'] == 0
    assert by_name['qwen3vl.safetensors']['offloaded_bytes'] == 15 * 1024**3
    assert 'turbo_lora.safetensors' in by_name

    gpus = [{'index': 0, 'name': 'RTX 4090'}, {'index': 1, 'name': 'RTX 4090'}]
    attach_gpu_other_processes(gpus, procs, comfyui=snap, comfyui_url='http://127.0.0.1:8188')
    gpu0_names = [m['name'] for m in gpus[0]['other_processes'][0]['models']]
    gpu1_names = [m['name'] for m in gpus[1]['other_processes'][0]['models']]
    assert 'minimax_h3.safetensors' in gpu0_names
    assert 'qwen3vl.safetensors' in gpu0_names
    assert 'video_vae.safetensors' not in gpu0_names
    assert 'video_vae.safetensors' in gpu1_names
    assert 'minimax_h3.safetensors' not in gpu1_names
