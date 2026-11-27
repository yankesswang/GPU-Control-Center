"""Native host telemetry and real Ollama execution; demo is opt-in."""
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import argparse
import logging
import os
import socket
import subprocess
import threading
import time

import httpx
import psutil
from .runtimes import discover_ollama, resident_ollama, control_ollama, generate_ollama, ExecutionUncertain
from .services import lmstudio_inventory, control_lmstudio, generate_lmstudio, comfyui_snapshot

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('agent')


def host_metrics():
    memory = psutil.virtual_memory()
    gpus = []
    def number(value):
        try:
            return float(value)
        except ValueError:
            return None
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=4, check=True)
        for line in result.stdout.strip().splitlines():
            index, uuid, name, util, used, total, temp, power = [p.strip() for p in line.split(',')]
            gpus.append(dict(index=int(index), uuid=uuid, name=name, utilization=number(util), memory_used_mb=number(used), memory_total_mb=number(total), temperature_c=number(temp), power_w=number(power)))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return dict(cpu_percent=psutil.cpu_percent(), ram_percent=memory.percent, ram_total_gb=round(memory.total / 1024**3, 1), gpus=gpus, simulated=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['demo', 'host'], default='host')
    args = parser.parse_args()
    api = os.getenv('API_URL', 'http://localhost:8088').rstrip('/')
    token = os.environ.get('AGENT_TOKEN')
    if not token:
        parser.error('AGENT_TOKEN is required')
    node_id = os.getenv('NODE_ID', socket.gethostname())
    headers = {'X-Agent-Token': token}
    stop = threading.Event()
    ollama_url = os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')

    lmstudio_url = os.getenv('LMSTUDIO_URL', 'http://127.0.0.1:1234').rstrip('/')
    comfyui_url = os.getenv('COMFYUI_URL', 'http://127.0.0.1:8188').rstrip('/')
    lm_headers = {'Authorization': 'Bearer ' + os.environ['LMSTUDIO_API_TOKEN']} if os.getenv('LMSTUDIO_API_TOKEN') else {}

    def heartbeat_loop():
        with ExitStack() as stack:
            central = stack.enter_context(httpx.Client(base_url=api, headers=headers, timeout=10))
            runtime = stack.enter_context(httpx.Client(base_url=ollama_url, timeout=3))
            studio = stack.enter_context(httpx.Client(base_url=lmstudio_url, headers=lm_headers, timeout=3))
            comfy = stack.enter_context(httpx.Client(base_url=comfyui_url, timeout=2))
            pool = stack.enter_context(ThreadPoolExecutor(max_workers=3))
            def ollama_info():
                try:
                    specs = discover_ollama(runtime)
                    info = {'status': 'online', 'models': len(specs), **resident_ollama(runtime)}
                    return specs, info
                except (httpx.HTTPError, ValueError, KeyError):
                    return [], {'status': 'offline', 'models': 0, 'loaded_models_status': 'unavailable', 'loaded_models': None}
            def studio_info():
                try:
                    return lmstudio_inventory(studio)
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    return [], {'status': 'offline', 'models': 0, 'loaded_models_status': 'unavailable', 'loaded_models': None}
            while not stop.is_set():
                try:
                    catalog = []
                    if args.mode == 'demo':
                        metrics = {'simulated': True, 'cpu_percent': 12, 'ram_percent': 24, 'gpus': [{'name': 'Demo GPU (simulated)', 'utilization': 35, 'memory_used_mb': 4096, 'memory_total_mb': 24576, 'temperature_c': 48, 'power_w': 90}]}
                        capabilities = ['demo.progress']
                    else:
                        metrics = host_metrics()
                        ollama_future = pool.submit(ollama_info)
                        studio_future = pool.submit(studio_info)
                        comfy_future = pool.submit(comfyui_snapshot, comfy)
                        ollama_catalog, metrics['ollama'] = ollama_future.result()
                        studio_catalog, metrics['lmstudio'] = studio_future.result()
                        metrics['comfyui'] = comfy_future.result()
                        catalog = ollama_catalog + studio_catalog
                        capabilities = [m['id'] for m in catalog if m.get('model_type', 'llm') == 'llm'] + ['ollama.model_control', 'lmstudio.model_control']
                    central.put(f'/api/agent/nodes/{node_id}', json={'name': node_id, 'mode': args.mode, 'capabilities': capabilities, 'model_catalog': catalog, 'metrics': metrics}).raise_for_status()
                except Exception as exc:
                    log.warning('Heartbeat failed: %s', type(exc).__name__)
                stop.wait(4)

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    log.info('Agent %s started in %s mode', node_id, args.mode)
    try:
        with httpx.Client(base_url=api, headers=headers, timeout=10) as central:
            while not stop.is_set():
                try:
                    response = central.post(f'/api/agent/nodes/{node_id}/claim')
                    response.raise_for_status()
                    job = response.json()['job']
                    if not job:
                        stop.wait(1)
                        continue
                    log.info('Executing %s job %s', job['model'], job['id'])

                    def send(client, cancel_event=None, **updates):
                        payload = {'node_id': node_id, 'attempt_id': job['attempt_id'], 'progress': 0, **updates}
                        while not stop.is_set() and not (cancel_event and cancel_event.is_set()):
                            try:
                                result = client.post(f"/api/agent/jobs/{job['id']}/report", json=payload)
                                if result.status_code == 409:
                                    raise ExecutionUncertain('Execution attempt no longer accepted by central API')
                                result.raise_for_status()
                                return result.json()
                            except httpx.HTTPError:
                                stop.wait(2)
                        raise ExecutionUncertain('Agent stopping')

                    def report(**updates):
                        return send(central, **updates)

                    pulse_stop = threading.Event()
                    def pulse():
                        # Independent lease renewals cover model loading and prefill.
                        with httpx.Client(base_url=api, headers=headers, timeout=10) as pulse_client:
                            while not pulse_stop.wait(5):
                                try:
                                    send(pulse_client, cancel_event=pulse_stop, status='running')
                                except ExecutionUncertain:
                                    return

                    pulse_thread = threading.Thread(target=pulse, daemon=True)
                    pulse_thread.start()
                    try:
                        if args.mode == 'demo' and job['model'] == 'demo.progress':
                            for step in range(1, job['steps'] + 1):
                                time.sleep(0.5)
                                ack = report(status='running', progress=step / job['steps'] * 100)
                                if ack.get('status') == 'cancelling':
                                    report(status='cancelled', message='Agent 已停止模擬工作。')
                                    break
                            else:
                                report(status='completed', progress=100, message='模擬任務完成。', result={'simulated':True, 'steps':job['steps']})
                        elif args.mode == 'host' and job['model'].startswith('ollama:'):
                            with httpx.Client(base_url=ollama_url, timeout=httpx.Timeout(30, connect=5)) as runtime:
                                if job.get('parameters', {}).get('model_action'):
                                    control_ollama(runtime, job, report)
                                else:
                                    generate_ollama(runtime, job, report)
                        elif args.mode == 'host' and job['model'].startswith('lmstudio:'):
                            with httpx.Client(base_url=lmstudio_url, headers=lm_headers, timeout=httpx.Timeout(180, connect=5)) as studio:
                                if job.get('parameters', {}).get('model_action'):
                                    control_lmstudio(studio, job, report)
                                else:
                                    generate_lmstudio(studio, job, report)
                        else:
                            report(status='failed', message='Agent 不支援此執行器。')
                    except ExecutionUncertain as exc:
                        # Stop the lease pulse before recording unknown. A late pulse
                        # must never turn an uncertain execution back into running.
                        pulse_stop.set()
                        pulse_thread.join(timeout=15)
                        report(status='unknown', message=str(exc))
                    except (httpx.HTTPStatusError, RuntimeError, KeyError) as exc:
                        report(status='failed', message=f'推論服務拒絕或明確回報失敗：{str(exc)[:500]}')
                    finally:
                        pulse_stop.set()
                        pulse_thread.join(timeout=15)
                except httpx.HTTPError as exc:
                    log.warning('Polling failed: %s', type(exc).__name__)
                    stop.wait(3)
    except KeyboardInterrupt:
        stop.set()


if __name__ == '__main__':
    main()
