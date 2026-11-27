"""MCP client API token generation, hashing, RBAC metadata, and catalog definitions."""
import hashlib
import re
import secrets
from typing import Literal

TOKEN_PREFIX = 'gcc_mcp_'
McpRole = Literal['read', 'infer', 'admin']
ROLE_LEVEL: dict[str, int] = {'read': 1, 'infer': 2, 'admin': 3}

TOOL_CATALOG: list[dict[str, str]] = [
    {
        'name': 'list_resources',
        'role': 'read',
        'category': 'discovery',
        'summary_zh': '列出完整主機、模型目錄與平台概況（向下相容完整快照）。',
        'summary_en': 'List full registered nodes, model catalog, and cluster overview snapshot.',
    },
    {
        'name': 'list_nodes',
        'role': 'read',
        'category': 'discovery',
        'summary_zh': '精簡查詢各運算主機的 GPU 顯存、溫度、功耗、常駐模型與在線狀態。',
        'summary_en': 'List compute hosts with GPU VRAM, utilization, temperature, power, and loaded models.',
    },
    {
        'name': 'list_models',
        'role': 'read',
        'category': 'discovery',
        'summary_zh': '依執行器（Ollama / LM Studio / ComfyUI）與可用狀態篩選模型清單。',
        'summary_en': 'Filter installed and available models by runtime (Ollama, LM Studio, ComfyUI) and type.',
    },
    {
        'name': 'run_inference',
        'role': 'infer',
        'category': 'inference',
        'summary_zh': '一站式同步文字推論：提交任務、自動等待完成並直接回傳生成文字與速度。',
        'summary_en': 'Submit a prompt through the central queue, wait for completion, and return generated text.',
    },
    {
        'name': 'submit_job',
        'role': 'infer',
        'category': 'inference',
        'summary_zh': '非同步提交推論任務並立即回傳 job_id，適合批次排隊或長任務。',
        'summary_en': 'Queue an inference job asynchronously and return job_id immediately.',
    },
    {
        'name': 'wait_for_job',
        'role': 'read',
        'category': 'jobs',
        'summary_zh': '在伺服端等待指定任務進入終止狀態並回報進度，減少 Agent 輪詢回合。',
        'summary_en': 'Wait server-side until a job reaches a terminal state or times out, streaming progress.',
    },
    {
        'name': 'list_jobs',
        'role': 'read',
        'category': 'jobs',
        'summary_zh': '查詢近期任務清單，可依狀態（queued/running/completed 等）與來源篩選。',
        'summary_en': 'List recent jobs with optional filtering by status, source tag, and limit.',
    },
    {
        'name': 'get_job',
        'role': 'read',
        'category': 'jobs',
        'summary_zh': '讀取單一任務的狀態、即時生成文字、事件紀錄與最終結果。',
        'summary_en': 'Read a single job status, partial output, event timeline, and saved result.',
    },
    {
        'name': 'explain_wait',
        'role': 'read',
        'category': 'jobs',
        'summary_zh': '查詢任務在佇列中的即時排序、相容主機、工作槽持有者與阻擋原因。',
        'summary_en': 'Inspect live queue rank, compatible hosts, active slot holders, and blockers.',
    },
    {
        'name': 'cancel_job',
        'role': 'infer',
        'category': 'jobs',
        'summary_zh': '取消排隊中任務，或向執行中任務發送安全取消請求（等待生成邊界確認）。',
        'summary_en': 'Cancel a queued job or request confirmed cancellation of a running job.',
    },
    {
        'name': 'retry_job',
        'role': 'infer',
        'category': 'jobs',
        'summary_zh': '以相同提示詞與參數重新建立已失敗或已取消的任務。',
        'summary_en': 'Retry a confirmed failed or cancelled job with its original prompt and parameters.',
    },
    {
        'name': 'get_energy_analytics',
        'role': 'read',
        'category': 'analytics',
        'summary_zh': '查詢即時 GPU 功耗、區間用電度數、時間電價估算、CO2 與逐卡能耗分析。',
        'summary_en': 'Query live GPU power draw, kWh usage, Time-of-Use cost estimates, CO2, and per-GPU breakdown.',
    },
    {
        'name': 'get_scheduler_state',
        'role': 'read',
        'category': 'scheduler',
        'summary_zh': '查詢動態排程策略、各主機健康與熱模型、多服務公平壓力、即時派送計畫與決策日誌。',
        'summary_en': 'Inspect dynamic scheduler policy, node health/warm models, per-service fair-share pressure, and dispatch plan.',
    },
    {
        'name': 'simulate_dispatch',
        'role': 'read',
        'category': 'scheduler',
        'summary_zh': '以目前即時叢集狀態試算某筆推論請求會被派送至哪台主機、評分拆解與決策動作（不實際入列）。',
        'summary_en': 'Dry-run the dynamic scheduler against live cluster state to preview routing decision and score breakdown.',
    },
    {
        'name': 'manage_model',
        'role': 'admin',
        'category': 'admin',
        'summary_zh': '在指定主機載入或卸載模型（支援 LM Studio / ComfyUI 指定 GPU 與主機密碼解鎖）。',
        'summary_en': 'Load or unload a model on a host (supports GPU placement for LM Studio / ComfyUI).',
    },
    {
        'name': 'set_default_runtime',
        'role': 'admin',
        'category': 'admin',
        'summary_zh': '切換指定主機的預設推論執行器（lmstudio 或 ollama）。',
        'summary_en': 'Switch a compute host default inference runtime (lmstudio or ollama).',
    },
    {
        'name': 'set_node_maintenance',
        'role': 'admin',
        'category': 'admin',
        'summary_zh': '開啟或關閉指定主機的維護模式（維護中將暫停接單）。',
        'summary_en': 'Enable or disable maintenance mode on a compute host.',
    },
]

RESOURCE_CATALOG: list[dict[str, str]] = [
    {
        'uri': 'gpu://overview',
        'summary_zh': '叢集總覽快照（在線主機數、任務統計、可用模型數）',
        'summary_en': 'Live cluster overview snapshot (online hosts, job counts, available models)',
    },
    {
        'uri': 'gpu://nodes',
        'summary_zh': '所有運算主機的精簡硬體與常駐模型快照',
        'summary_en': 'Compact hardware telemetry and resident model snapshot across all hosts',
    },
    {
        'uri': 'gpu://models',
        'summary_zh': '所有可用推論模型目錄',
        'summary_en': 'Catalog of available inference models',
    },
]

PROMPT_CATALOG: list[dict[str, str]] = [
    {
        'name': 'cluster_health_check',
        'summary_zh': '檢查叢集 GPU 負載、排隊瓶頸、離線主機與能耗狀況並產出維運報告',
        'summary_en': 'Inspect GPU load, queue bottlenecks, offline hosts, and power draw for an ops report',
    },
    {
        'name': 'run_local_llm',
        'summary_zh': '自動挑選最佳可用本機模型並透過中央排程執行提示詞',
        'summary_en': 'Pick the best available local model and execute a prompt through the central scheduler',
    },
]


def role_allows(granted_role: str, required_role: str) -> bool:
    return ROLE_LEVEL.get(granted_role, 0) >= ROLE_LEVEL.get(required_role, 99)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """Return (raw_token, display_prefix, sha256_hex)."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    prefix = raw[:16]
    return raw, prefix, hash_token(raw)


def sanitize_source_tag(name: str, source_tag: str | None = None) -> str:
    candidate = (source_tag or '').strip()
    if not candidate:
        slug = re.sub(r'[^a-zA-Z0-9._-]+', '-', name.strip().lower()).strip('-')
        candidate = f'mcp:{slug[:60]}' if slug else 'mcp-agent'
    return candidate[:80]


def serialize_token(tok) -> dict:
    return {
        'id': tok.id,
        'name': tok.name,
        'source_tag': tok.source_tag,
        'role': tok.role,
        'token_prefix': tok.token_prefix,
        'notes': tok.notes,
        'request_count': tok.request_count,
        'last_used_at': tok.last_used_at,
        'created_at': tok.created_at,
        'revoked_at': tok.revoked_at,
        'active': tok.revoked_at is None,
    }
