"""Adapter contract tests for completion and ambiguous execution boundaries."""
import json

import httpx
import pytest
from app.runtimes import generate_ollama, ExecutionUncertain

JOB = {'model':'ollama:test', 'parameters':{'prompt':'Hello', 'max_tokens':8}}


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
