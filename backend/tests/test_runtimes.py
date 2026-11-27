"""Adapter contract tests for completion and ambiguous execution boundaries."""
import json

import httpx
import pytest
from app.runtimes import generate_ollama, resident_ollama, ExecutionUncertain

JOB = {'model':'ollama:test', 'parameters':{'prompt':'Hello', 'max_tokens':8}}


def test_residency_reports_models_loaded_outside_central_jobs():
    def handler(request):
        assert request.url.path == '/api/ps'
        return httpx.Response(200, json={'models': [
            {'name': 'external-model:latest', 'size': 8192, 'size_vram': 4096},
            {'model': 'cpu-model:latest', 'size': 2048, 'size_vram': 0},
        ]})
    with httpx.Client(base_url='http://ollama.test', transport=httpx.MockTransport(handler)) as client:
        state = resident_ollama(client)
    assert state['loaded_models_status'] == 'online'
    assert state['loaded_models'] == [
        {'name': 'external-model:latest', 'size_bytes': 8192, 'vram_bytes': 4096},
        {'name': 'cpu-model:latest', 'size_bytes': 2048, 'vram_bytes': 0},
    ]


@pytest.mark.parametrize('payload', [{}, {'models': None}, {'models': [{}]}])
def test_invalid_residency_is_unknown_instead_of_empty(payload):
    with httpx.Client(base_url='http://ollama.test', transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        assert resident_ollama(client) == {'loaded_models_status': 'unavailable', 'loaded_models': None}


def test_empty_residency_and_unreachable_runtime_are_distinct():
    with runtime([]) as client:
        assert resident_ollama(client) == {'loaded_models_status': 'online', 'loaded_models': []}
    def handler(request):
        raise httpx.ConnectError('offline', request=request)
    with httpx.Client(base_url='http://ollama.test', transport=httpx.MockTransport(handler)) as client:
        assert resident_ollama(client) == {'loaded_models_status': 'unavailable', 'loaded_models': None}


def runtime(parts, inventory=None):
    def handler(request):
        if request.url.path == '/api/ps':
            return httpx.Response(200, json={'models': inventory or []})
        body = json.loads(request.content)
        assert body['keep_alive'] == 0
        return httpx.Response(200, text='\n'.join(json.dumps(p) for p in parts))
    return httpx.Client(base_url='http://ollama.test', transport=httpx.MockTransport(handler))


def test_real_adapter_reports_text_and_confirmed_release():
    reports = []
    def report(**payload):
        reports.append(payload)
        return {'status':'running'}
    with runtime([{'message':{'content':'Hello'}}, {'done':True,'eval_count':1,'eval_duration':100000000}]) as client:
        generate_ollama(client, JOB, report)
    result = reports[-1]['result']
    assert reports[-1]['status'] == 'completed'
    assert result['text'] == 'Hello'
    assert result['simulated'] is False
    assert result['model_released'] is True


def test_missing_done_cannot_be_mistaken_for_completion():
    reports = []
    with runtime([{'message':{'content':'partial'}}]) as client:
        with pytest.raises(ExecutionUncertain):
            generate_ollama(client, JOB, lambda **p: reports.append(p) or {'status':'running'})
    assert not any(p['status'] == 'completed' for p in reports)


def test_cancel_before_dispatch_does_not_contact_upstream():
    reports = []
    def handler(request):
        raise AssertionError('Cancelled work must not reach Ollama')
    def report(**p):
        reports.append(p)
        return {'status':'cancelling'}
    with httpx.Client(base_url='http://ollama.test', transport=httpx.MockTransport(handler)) as client:
        generate_ollama(client, JOB, report)
    assert reports[-1]['status'] == 'cancelled'


def test_network_loss_after_dispatch_keeps_execution_uncertain():
    def handler(request):
        raise httpx.ReadTimeout('unknown execution', request=request)
    with httpx.Client(base_url='http://ollama.test', transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ExecutionUncertain):
            generate_ollama(client, JOB, lambda **p: {'status':'running'})
