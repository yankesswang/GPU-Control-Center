"""Per-host operation password hashing and signed short-lived unlock sessions."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from fastapi import HTTPException, Request, Response

COOKIE_NAME = 'gcc_host_auth'
HEADER_NAME = 'X-Host-Auth-Token'
DEFAULT_TTL_SECONDS = 1800  # 30 minutes
MAX_COOKIE_NODES = 50
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_FAILURES = 5

_EPHEMERAL_FALLBACK = secrets.token_hex(32)
_rate_lock = threading.Lock()
_failed_attempts: dict[tuple[str, str], list[float]] = {}


def ttl_seconds() -> int:
    try:
        value = int(os.getenv('HOST_AUTH_TTL_SECONDS', str(DEFAULT_TTL_SECONDS)))
        return max(60, min(value, 86400))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def validate_new_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 4 or not password.strip():
        raise ValueError('操作密碼長度需至少 4 個字元。')
    if len(password) > 128:
        raise ValueError('操作密碼長度不可超過 128 個字元。')
    return password


def hash_password(password: str) -> str:
    validate_new_password(password)
    n, r, p = 16384, 8, 1
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=n, r=r, p=p, dklen=32)
    return f'scrypt${n}${r}${p}${base64.b64encode(salt).decode("ascii")}${base64.b64encode(derived).decode("ascii")}'


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not password or not stored_hash:
        return False
    try:
        scheme, n_s, r_s, p_s, salt_b64, digest_b64 = stored_hash.split('$', 5)
        if scheme != 'scrypt':
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.b64decode(salt_b64.encode('ascii'))
        expected = base64.b64decode(digest_b64.encode('ascii'))
        derived = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=n, r=r, p=p, dklen=len(expected))
        return hmac.compare_digest(derived, expected)
    except (ValueError, TypeError):
        return False


def _signing_key() -> bytes:
    secret = os.getenv('HOST_AUTH_SECRET') or os.getenv('AGENT_TOKEN') or _EPHEMERAL_FALLBACK
    return hmac.new(b'gcc-host-auth-v1', secret.encode('utf-8'), hashlib.sha256).digest()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _b64url_decode(encoded: str) -> bytes:
    pad = '=' * (-len(encoded) % 4)
    return base64.urlsafe_b64decode((encoded + pad).encode('ascii'))


def _sign_payload(payload: dict) -> str:
    body = _b64url_encode(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8'))
    sig = _b64url_encode(hmac.new(_signing_key(), body.encode('ascii'), hashlib.sha256).digest())
    return f'{body}.{sig}'


def _verify_payload(token: str | None) -> dict | None:
    if not token or '.' not in token:
        return None
    body, sig = token.rsplit('.', 1)
    expected = _b64url_encode(hmac.new(_signing_key(), body.encode('ascii'), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_b64url_decode(body))
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def _is_secure_request(request: Request) -> bool:
    proto = request.headers.get('x-forwarded-proto', '').split(',')[0].strip().lower()
    return proto == 'https' or request.url.scheme == 'https'


def _read_cookie_entries(request: Request, now_ts: float | None = None) -> dict[str, tuple[int, int]]:
    now_ts = time.time() if now_ts is None else now_ts
    data = _verify_payload(request.cookies.get(COOKIE_NAME))
    raw_map = data.get('u') if data else None
    if not isinstance(raw_map, dict):
        return {}
    entries: dict[str, tuple[int, int]] = {}
    for node_id, pair in raw_map.items():
        if (
            isinstance(node_id, str)
            and isinstance(pair, list)
            and len(pair) == 2
            and isinstance(pair[0], int)
            and isinstance(pair[1], int)
            and pair[1] > now_ts
        ):
            entries[node_id] = (pair[0], pair[1])
    return entries


def _write_cookie_entries(request: Request, response: Response, entries: dict[str, tuple[int, int]], now_ts: float) -> None:
    pruned = {nid: pair for nid, pair in entries.items() if pair[1] > now_ts}
    if len(pruned) > MAX_COOKIE_NODES:
        keep = sorted(pruned.items(), key=lambda item: item[1][1], reverse=True)[:MAX_COOKIE_NODES]
        pruned = dict(keep)
    if not pruned:
        response.delete_cookie(COOKIE_NAME, path='/api', samesite='lax')
        return
    max_exp = max(exp for _, exp in pruned.values())
    max_age = max(1, int(max_exp - now_ts))
    signed = _sign_payload({'u': {nid: [ver, exp] for nid, (ver, exp) in pruned.items()}})
    response.set_cookie(
        key=COOKIE_NAME,
        value=signed,
        max_age=max_age,
        httponly=True,
        samesite='lax',
        secure=_is_secure_request(request),
        path='/api',
    )


def issue_unlock(request: Request, response: Response, node_id: str, auth_version: int) -> tuple[str, str, int]:
    """Grant a short-lived unlock for `node_id` via HttpOnly cookie and single-host token."""
    now_ts = time.time()
    ttl = ttl_seconds()
    exp = int(now_ts + ttl)
    entries = _read_cookie_entries(request, now_ts)
    entries[node_id] = (auth_version, exp)
    _write_cookie_entries(request, response, entries, now_ts)
    single_token = _sign_payload({'n': node_id, 'v': auth_version, 'e': exp})
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
    return single_token, expires_at, ttl


def revoke_unlock(request: Request, response: Response, node_id: str) -> None:
    """Remove `node_id` from the caller's unlock cookie."""
    now_ts = time.time()
    entries = _read_cookie_entries(request, now_ts)
    entries.pop(node_id, None)
    _write_cookie_entries(request, response, entries, now_ts)


def get_node_unlock_expiry(request: Request | None, node) -> str | None:
    """Return ISO UTC expiry if `request` holds a valid unlock for `node`, else None."""
    if request is None or not node.auth_password_hash:
        return None
    now_ts = time.time()
    # 1. Explicit header token (for API/CLI clients)
    header_token = request.headers.get(HEADER_NAME)
    if header_token:
        data = _verify_payload(header_token)
        if (
            data
            and data.get('n') == node.id
            and data.get('v') == node.auth_version
            and isinstance(data.get('e'), int)
            and data['e'] > now_ts
        ):
            return datetime.fromtimestamp(data['e'], tz=timezone.utc).isoformat()
    # 2. Signed HttpOnly cookie (for browser console)
    entries = _read_cookie_entries(request, now_ts)
    pair = entries.get(node.id)
    if pair and pair[0] == node.auth_version and pair[1] > now_ts:
        return datetime.fromtimestamp(pair[1], tz=timezone.utc).isoformat()
    return None


def _rate_key(request: Request, node_id: str) -> tuple[str, str]:
    forwarded = request.headers.get('x-forwarded-for', '').split(',')[0].strip()
    client = forwarded or (request.client.host if request.client else 'unknown')
    return (client, node_id)


def check_rate_limit(request: Request, node_id: str) -> None:
    key = _rate_key(request, node_id)
    cutoff = time.monotonic() - RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        recent = [ts for ts in _failed_attempts.get(key, []) if ts >= cutoff]
        if recent:
            _failed_attempts[key] = recent
        else:
            _failed_attempts.pop(key, None)
        if len(recent) >= RATE_LIMIT_MAX_FAILURES:
            raise HTTPException(429, '密碼嘗試次數過多，請稍後再試。')


def record_auth_failure(request: Request, node_id: str) -> None:
    key = _rate_key(request, node_id)
    now_mono = time.monotonic()
    cutoff = now_mono - RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        recent = [ts for ts in _failed_attempts.get(key, []) if ts >= cutoff]
        recent.append(now_mono)
        _failed_attempts[key] = recent


def clear_auth_failures(request: Request, node_id: str) -> None:
    key = _rate_key(request, node_id)
    with _rate_lock:
        _failed_attempts.pop(key, None)


def reset_rate_limit() -> None:
    with _rate_lock:
        _failed_attempts.clear()
