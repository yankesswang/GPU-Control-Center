"""Discover supported remote runtime services on their default ports."""
import asyncio
import json
import time
from urllib.parse import urlsplit

import httpx
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from .host_auth import validate_new_password

DEFAULT_PORTS = {'lmstudio': 1234, 'ollama': 11434, 'comfyui': 8188}
PROBE_PATHS = {'lmstudio': '/api/v1/models', 'ollama': '/api/tags', 'comfyui': '/system_stats'}


class ConnectionInput(BaseModel):
    url: str = Field(min_length=1, max_length=500)

    @field_validator('url')
    @classmethod
    def origin_only(cls, value):
        value = value.strip()
        if '://' not in value:
            value = 'http://' + value
        validated = AnyHttpUrl(value)
        if validated.username or validated.password or validated.path not in ('', '/') or validated.query or validated.fragment:
            raise ValueError('請輸入主機 IP 或名稱，不含路徑或帳密。')
        return str(validated).rstrip('/')


class AddConnectionInput(ConnectionInput):
    name: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$')
    password: str | None = Field(default=None, max_length=128)

    @field_validator('password')
    @classmethod
    def check_password(cls, value):
        if value is None or value == '':
            return None
        return validate_new_password(value)


def service_origins(address):
    parsed = urlsplit(address)
    host = parsed.hostname
    authority = f'[{host}]' if ':' in host else host
    return {provider: (address if parsed.port is not None else f'{parsed.scheme}://{authority}:{port}')
            for provider, port in DEFAULT_PORTS.items()}


async def read_json(client, path):
    async with client.stream('GET', path) as response:
        if response.status_code != 200:
            return None, response.status_code
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > 1048576:
                return None, 200
        try:
            return json.loads(content), 200
        except (ValueError, UnicodeError):
            return None, 200


async def probe_service(provider, origin):
    started = time.monotonic()
    try:
        async with asyncio.timeout(4):
            async with httpx.AsyncClient(base_url=origin, timeout=3, follow_redirects=False, trust_env=False) as client:
                data, status = await read_json(client, PROBE_PATHS[provider])
        if status in (401, 403):
            return {'provider': provider, 'ok': False, 'reason': '服務要求 API 認證'}
        valid = isinstance(data, dict) and (
            isinstance(data.get('models'), list) if provider in ('lmstudio', 'ollama')
            else isinstance(data.get('system'), dict) and isinstance(data.get('devices'), list))
        if valid and provider == 'lmstudio':
            valid = all(isinstance(m, dict) and isinstance(m.get('key'), str) and m.get('type') in ('llm', 'embedding') for m in data['models'])
        if valid and provider == 'ollama':
            valid = all(isinstance(m, dict) and isinstance(m.get('name'), str) for m in data['models'])
        if not valid:
            return {'provider': provider, 'ok': False, 'reason': '回應不符合此服務格式'}
        return {'provider': provider, 'ok': True, 'url': origin,
                'model_count': len(data.get('models', [])), 'latency_ms': round((time.monotonic() - started) * 1000)}
    except (TimeoutError, httpx.TimeoutException):
        return {'provider': provider, 'ok': False, 'reason': '連線逾時'}
    except httpx.HTTPError:
        return {'provider': provider, 'ok': False, 'reason': '無法建立連線'}


async def discover_services(address):
    probes = await asyncio.gather(*(probe_service(provider, origin) for provider, origin in service_origins(address).items()))
    services = [item for item in probes if item['ok']]
    return {'ok': bool(services), 'services': services, 'checks': probes,
            'message': '已找到可連線的服務。' if services else '未找到可用服務，請確認遠端服務已啟動並允許區網連線。'}
