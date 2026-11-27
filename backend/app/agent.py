"""Native host telemetry and real Ollama execution; demo is opt-in."""
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import argparse
import atexit
import logging
import json
import os
import signal
import socket
import subprocess
import threading
import time

import httpx
import psutil
from .local_runtime import (
    LocalLlamaManager,
    control_direct_llama,
    discover_disk_comfyui_snapshot,
    discover_disk_lmstudio_models,
    generate_direct_llama,
)
from .placement import attach_gpu_other_processes, collect_gpu_processes, merge_comfyui_active_models
from .runtimes import discover_ollama, resident_ollama, control_ollama, generate_ollama, ExecutionUncertain
from .services import (
    lmstudio_inventory,
    control_lmstudio,
    generate_lmstudio,
    comfyui_snapshot,
    comfyui_supports_gpu_select,
    comfyui_loaded_models_telemetry,
    comfyui_catalog_from_snapshot,
    control_comfyui,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('agent')


def host_metrics():
    memory = psutil.virtual_memory()
    gpus = []
    def number(value):
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    try:
        query = (
            'index,uuid,name,utilization.gpu,memory.used,memory.total,temperature.gpu,'
            'power.draw,power.limit,fan.speed,clocks.current.graphics,clocks.current.memory,pstate'
        )
        result = subprocess.run(
            ['nvidia-smi', f'--query-gpu={query}', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=4, check=True,
        )
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(',')]
            if len(parts) < 8:
                continue
            index, uuid, name, util, used, total, temp, power = parts[:8]
            gpu_entry = dict(
                index=int(index), uuid=uuid, name=name,
                utilization=number(util), memory_used_mb=number(used),
                memory_total_mb=number(total), temperature_c=number(temp),
                power_w=number(power),
            )
            if len(parts) >= 13:
                gpu_entry.update(
                    power_limit_w=number(parts[8]),
                    fan_speed_pct=number(parts[9]),
                    clock_sm_mhz=number(parts[10]),
                    clock_mem_mhz=number(parts[11]),
                    pstate=parts[12] if parts[12] and not parts[12].startswith('[') else None,
                )
            gpus.append(gpu_entry)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    cpu_temp_c = None
    try:
        temps = psutil.sensors_temperatures() or {}
        for key in ('k10temp', 'coretemp', 'zenpower', 'acpitz'):
            entries = temps.get(key)
            if entries:
                vals = [e.current for e in entries if getattr(e, 'current', None) is not None]
                if vals:
                    cpu_temp_c = round(max(vals), 1)
                    break
    except (AttributeError, OSError):
        pass
    load_avg_1m = None
    try:
        load_avg_1m = round(os.getloadavg()[0], 2)
    except OSError:
        pass
    disk_percent = None
    try:
        disk_percent = round(psutil.disk_usage('/').percent, 1)
    except OSError:
        pass
    return dict(
        cpu_percent=psutil.cpu_percent(),
        cpu_temp_c=cpu_temp_c,
        load_avg_1m=load_avg_1m,
        ram_percent=memory.percent,
        ram_used_gb=round(memory.used / 1024**3, 1),
        ram_total_gb=round(memory.total / 1024**3, 1),
        disk_percent=disk_percent,
        gpus=gpus,
        simulated=False,
    )


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
    remote_config = json.loads(os.getenv('GCC_REMOTE_CONFIG', 'null'))
    remote = remote_config is not None
    node_name = remote_config['name'] if remote else node_id
    stop = threading.Event()
    ollama_url = os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')

    lmstudio_url = os.getenv('LMSTUDIO_URL', 'http://127.0.0.1:1234').rstrip('/')
    comfyui_url = os.getenv('COMFYUI_URL', 'http://127.0.0.1:8188').rstrip('/')
    lm_headers = {'Authorization': 'Bearer ' + os.environ['LMSTUDIO_API_TOKEN']} if os.getenv('LMSTUDIO_API_TOKEN') else {}
    llama_manager = LocalLlamaManager()
    atexit.register(llama_manager.shutdown_all)

    def _handle_signal(_signum, _frame):
        stop.set()
        llama_manager.shutdown_all()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_signal)
        except ValueError:
            pass

    def _lmstudio_app_online(studio_client: httpx.Client) -> bool:
        try:
            res = studio_client.get('/api/v1/models', timeout=2.0)
            res.raise_for_status()
            return isinstance(res.json().get('models'), list)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return False

    def heartbeat_loop():
        with ExitStack() as stack:
            central = stack.enter_context(httpx.Client(base_url=api, headers=headers, timeout=10))
            runtime = stack.enter_context(httpx.Client(base_url=ollama_url, timeout=3, trust_env=not remote))
            studio = stack.enter_context(httpx.Client(base_url=lmstudio_url, headers=lm_headers, timeout=3, trust_env=not remote))
            comfy = stack.enter_context(httpx.Client(base_url=comfyui_url, timeout=2, trust_env=not remote))
            pool = stack.enter_context(ThreadPoolExecutor(max_workers=3))
            ollama_blob_cache = {}
            comfy_gpu_select_cache = {'checked': False, 'supported': False}
            def ollama_info(gpu_processes, matched_pids):
                try:
                    specs = discover_ollama(runtime)
                    info = {
                        'status': 'online',
                        'models': len(specs),
                        'gpu_placement_supported': False,
                        **resident_ollama(runtime, gpu_processes=gpu_processes, blob_cache=ollama_blob_cache, matched_pids=matched_pids),
                    }
                    return specs, info
                except (httpx.HTTPError, ValueError, KeyError):
                    return [], {'status': 'offline', 'models': 0, 'gpu_placement_supported': False, 'loaded_models_status': 'unavailable', 'loaded_models': None}
            def studio_info(gpu_indices, gpu_processes, matched_pids):
                if remote:
                    try:
                        specs, info = lmstudio_inventory(studio)
                        info.update(gpu_placement_supported=False, backend='lmstudio_app', catalog_source='api')
                        return specs, info
                    except (httpx.HTTPError, ValueError, KeyError, TypeError):
                        return [], {'status': 'offline', 'models': 0, 'gpu_placement_supported': False, 'loaded_models_status': 'unavailable', 'loaded_models': None}
                try:
                    specs, info = lmstudio_inventory(
                        studio,
                        lmstudio_url=lmstudio_url,
                        available_gpu_indices=gpu_indices,
                        gpu_processes=gpu_processes,
                        matched_pids=matched_pids,
                    )
                    info['backend'] = 'lmstudio_app'
                    info['catalog_source'] = 'api'
                    direct_loaded = llama_manager.loaded_models(gpu_processes, matched_pids=matched_pids)
                    if direct_loaded:
                        existing_names = {m['name'] for m in (info.get('loaded_models') or [])}
                        info['loaded_models'] = list(info.get('loaded_models') or []) + [
                            m for m in direct_loaded if m['name'] not in existing_names
                        ]
                    return specs, info
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    disk_catalog, _disk_index, models_dir = discover_disk_lmstudio_models()
                    if llama_manager.is_available():
                        direct_loaded = llama_manager.loaded_models(gpu_processes, matched_pids=matched_pids)
                        return disk_catalog, {
                            'status': 'online',
                            'backend': 'direct_llama',
                            'catalog_source': 'disk',
                            'models_dir': models_dir,
                            'models': len(disk_catalog),
                            'gpu_placement_supported': True,
                            'loaded_models_status': 'online',
                            'loaded_models': direct_loaded,
                        }
                    return disk_catalog, {
                        'status': 'offline',
                        'backend': 'disk_only',
                        'catalog_source': 'disk',
                        'models_dir': models_dir,
                        'models': len(disk_catalog),
                        'gpu_placement_supported': False,
                        'loaded_models_status': 'unavailable',
                        'loaded_models': None,
                    }
            def comfy_info():
                snap = comfyui_snapshot(comfy)
                if snap.get('status') == 'online':
                    snap['catalog_source'] = 'api'
                    if not comfy_gpu_select_cache['checked']:
                        comfy_gpu_select_cache['supported'] = comfyui_supports_gpu_select(comfy)
                        comfy_gpu_select_cache['checked'] = True
                    snap['gpu_placement_supported'] = comfy_gpu_select_cache['supported']
                    telemetry = comfyui_loaded_models_telemetry(comfy)
                    if telemetry is not None:
                        snap['loaded_models_telemetry'] = 'online'
                        snap['loaded_models'] = telemetry
                    else:
                        snap['loaded_models_telemetry'] = 'unavailable'
                else:
                    comfy_gpu_select_cache['checked'] = False
                    if not remote:
                        snap = discover_disk_comfyui_snapshot()
                    snap['loaded_models_telemetry'] = 'unavailable'
                return comfyui_catalog_from_snapshot(snap), snap
            while not stop.is_set():
                try:
                    catalog = []
                    if args.mode == 'demo':
                        metrics = {'simulated': True, 'cpu_percent': 12, 'ram_percent': 24, 'gpus': [{'name': 'Demo GPU (simulated)', 'utilization': 35, 'memory_used_mb': 4096, 'memory_total_mb': 24576, 'temperature_c': 48, 'power_w': 90}]}
                        capabilities = ['demo.progress']
                    else:
                        metrics = {'remote': True, 'simulated': False, 'gpus': []} if remote else host_metrics()
                        gpu_indices = [g['index'] for g in metrics['gpus'] if isinstance(g.get('index'), int)]
                        gpu_processes = collect_gpu_processes(metrics['gpus'])
                        ollama_matched = set()
                        studio_matched = set()
                        ollama_future = pool.submit(ollama_info, gpu_processes, ollama_matched)
                        studio_future = pool.submit(studio_info, gpu_indices, gpu_processes, studio_matched)
                        comfy_future = pool.submit(comfy_info)
                        ollama_catalog, metrics['ollama'] = ollama_future.result()
                        studio_catalog, metrics['lmstudio'] = studio_future.result()
                        comfy_catalog, metrics['comfyui'] = comfy_future.result()
                        if not remote:
                            merge_comfyui_active_models(metrics['comfyui'], gpu_processes, comfyui_url=comfyui_url)
                        attach_gpu_other_processes(
                            metrics['gpus'],
                            gpu_processes,
                            exclude_pids=ollama_matched | studio_matched,
                            comfyui=metrics['comfyui'],
                            comfyui_url=comfyui_url,
                        )
                        if remote:
                            metrics['comfyui']['gpu_placement_supported'] = False
                        catalog = ollama_catalog + studio_catalog + comfy_catalog
                        capabilities = [
                            m['id'] for m in catalog
                            if m.get('model_type', 'llm') == 'llm'
                            and (m['runtime'] != 'lmstudio' or metrics['lmstudio'].get('status') == 'online')
                        ] + ['ollama.model_control']
                        if remote and metrics['ollama'].get('status') != 'online':
                            capabilities.remove('ollama.model_control')
                        if metrics['lmstudio'].get('status') == 'online':
                            capabilities.append('lmstudio.model_control')
                        if metrics['comfyui'].get('status') == 'online':
                            capabilities.append('comfyui.model_control')
                    central.put(f'/api/agent/nodes/{node_id}', json={'name': node_name, 'mode': args.mode, 'capabilities': capabilities, 'model_catalog': catalog, 'metrics': metrics}).raise_for_status()
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
                            with httpx.Client(base_url=ollama_url, timeout=httpx.Timeout(30, connect=5), trust_env=not remote) as runtime:
                                if job.get('parameters', {}).get('model_action'):
                                    control_ollama(runtime, job, report)
                                else:
                                    generate_ollama(runtime, job, report)
                        elif args.mode == 'host' and job['model'].startswith('lmstudio:'):
                            model_name = job['model'].split(':', 1)[1]
                            gpu_indices = [] if remote else [g['index'] for g in host_metrics()['gpus'] if isinstance(g.get('index'), int)]
                            with httpx.Client(base_url=lmstudio_url, headers=lm_headers, timeout=httpx.Timeout(180, connect=5), trust_env=not remote) as studio:
                                if job.get('parameters', {}).get('model_action'):
                                    action_name = job['parameters']['model_action']
                                    if remote:
                                        control_lmstudio(studio, job, report)
                                    elif action_name == 'unload' and llama_manager.has_instance(model_name):
                                        control_direct_llama(llama_manager, {}, job, report, available_gpu_indices=gpu_indices)
                                    elif _lmstudio_app_online(studio):
                                        if action_name == 'load' and llama_manager.has_instance(model_name):
                                            llama_manager.unload(model_name)
                                        control_lmstudio(studio, job, report, lmstudio_url=lmstudio_url, available_gpu_indices=gpu_indices)
                                    else:
                                        _, disk_index, _ = discover_disk_lmstudio_models()
                                        control_direct_llama(llama_manager, disk_index, job, report, available_gpu_indices=gpu_indices)
                                else:
                                    if remote:
                                        generate_lmstudio(studio, job, report)
                                    elif llama_manager.has_instance(model_name):
                                        generate_direct_llama(llama_manager, {}, job, report, available_gpu_indices=gpu_indices)
                                    elif _lmstudio_app_online(studio):
                                        generate_lmstudio(studio, job, report)
                                    else:
                                        _, disk_index, _ = discover_disk_lmstudio_models()
                                        generate_direct_llama(llama_manager, disk_index, job, report, available_gpu_indices=gpu_indices)
                        elif args.mode == 'host' and job['model'].startswith('comfyui:'):
                            with httpx.Client(base_url=comfyui_url, timeout=httpx.Timeout(180, connect=5), trust_env=not remote) as comfy:
                                if job.get('parameters', {}).get('model_action'):
                                    control_comfyui(comfy, job, report)
                                else:
                                    report(status='failed', message='ComfyUI 僅支援模型載入與卸載控制。')
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
    finally:
        llama_manager.shutdown_all()


if __name__ == '__main__':
    main()
