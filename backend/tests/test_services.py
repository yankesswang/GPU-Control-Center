import json
import httpx
import pytest
from app.runtimes import control_ollama, generate_ollama, ExecutionUncertain
from app.services import lmstudio_inventory, control_lmstudio, generate_lmstudio, comfyui_snapshot


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
