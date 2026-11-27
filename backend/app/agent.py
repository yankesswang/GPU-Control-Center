"""Pull-based demo executor or native host telemetry agent. No real AI runtime yet."""
import argparse
import logging
import os
import socket
import subprocess
import threading
import time

import httpx
import psutil

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('agent')


def host_metrics():
    memory = psutil.virtual_memory()
    gpus = []
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=4, check=True)
        for line in result.stdout.strip().splitlines():
            name, util, used, total, temp, power = [p.strip() for p in line.split(',')]
            gpus.append(dict(name=name, utilization=float(util), memory_used_mb=float(used), memory_total_mb=float(total), temperature_c=float(temp) if temp != '[N/A]' else None, power_w=float(power) if power != '[N/A]' else None))
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

    def heartbeat_loop():
        with httpx.Client(base_url=api, headers=headers, timeout=10) as client:
            while not stop.is_set():
                try:
                    metrics = {'simulated': True, 'cpu_percent': 12, 'ram_percent': 24, 'gpus': [{'name': 'Demo GPU (simulated)', 'utilization': 35, 'memory_used_mb': 4096, 'memory_total_mb': 24576, 'temperature_c': 48, 'power_w': 90}]} if args.mode == 'demo' else host_metrics()
                    client.put(f'/api/agent/nodes/{node_id}', json={'name': node_id, 'mode': args.mode, 'capabilities': ['demo.progress'] if args.mode == 'demo' else [], 'metrics': metrics}).raise_for_status()
                except Exception as exc:
                    log.warning('Heartbeat failed: %s', type(exc).__name__)
                stop.wait(4)

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    log.info('Agent %s started in %s mode', node_id, args.mode)
    try:
        with httpx.Client(base_url=api, headers=headers, timeout=10) as client:
            while True:
                if args.mode == 'host':
                    time.sleep(5)
                    continue
                try:
                    response = client.post(f'/api/agent/nodes/{node_id}/claim')
                    response.raise_for_status()
                    job = response.json()['job']
                    if not job:
                        time.sleep(1)
                        continue
                    log.info('Executing demo job %s', job['id'])
                    for step in range(1, job['steps'] + 1):
                        time.sleep(0.5)
                        # Retry the same report while disconnected; never claim a new job
                        # until this attempt has been acknowledged or rejected as stale.
                        payload = dict(node_id=node_id, attempt_id=job['attempt_id'], progress=step / job['steps'] * 100, status='completed' if step == job['steps'] else 'running')
                        if step == job['steps']:
                            payload.update(message='模擬任務完成；未執行 AI 推論。', result={'simulated': True, 'steps': job['steps'], 'message': '排程、進度回報與資料庫保存已驗證。'})
                        while True:
                            try:
                                response = client.post(f"/api/agent/jobs/{job['id']}/report", json=payload)
                                if response.status_code == 409:
                                    state = {'terminal': True}
                                    break
                                response.raise_for_status()
                                state = response.json()
                                break
                            except httpx.HTTPError:
                                time.sleep(2)
                        if state.get('status') == 'cancelling':
                            payload.update(status='cancelled', message='Agent 已停止模擬工作。', result=None)
                            while True:
                                try:
                                    response = client.post(f"/api/agent/jobs/{job['id']}/report", json=payload)
                                    if response.status_code == 409:
                                        break
                                    response.raise_for_status()
                                    break
                                except httpx.HTTPError:
                                    time.sleep(2)
                            break
                        if state.get('terminal'):
                            break
                except httpx.HTTPError as exc:
                    log.warning('Polling failed: %s', type(exc).__name__)
                    time.sleep(3)
    except KeyboardInterrupt:
        stop.set()


if __name__ == '__main__':
    main()
