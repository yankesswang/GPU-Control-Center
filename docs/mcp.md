# MCP 接入指南（給外部 Agent 系統直接使用）

GPU Control Center 提供標準 **Model Context Protocol (MCP)** 介面，讓任何外部 Agent 系統（Claude Code、Claude Desktop、Cursor、Windsurf、Codex、OpenAI Agents SDK、LangChain / LangGraph、n8n、Dify 或自建 Agent）直接透過中央排程安全共用 GPU 叢集。

所有 MCP 請求都透過中央 HTTP API 與同一套 PostgreSQL 執行排程與租約，不建立平行佇列，且在 Web Console 的「任務佇列」即時可見。

---

## 1. 端點與傳輸方式

| 模式 | 位址 / 指令 | 適用對象 |
| --- | --- | --- |
| **Streamable HTTP（推薦）** | `http://<host>:8090/mcp` 或同源代理 `http://<host>:5180/mcp` | Claude Code、Codex、Cursor、Windsurf、Python SDK、LangChain、遠端多主機 Agent |
| **獨立 stdio 橋接套件** | `uvx --from .../mcp-package gpu-control-center-mcp --url ... --token ...` | Claude Desktop 或僅支援本機 stdio 的 MCP Client |
| **Legacy SSE** | `python -m app.mcp_server --transport sse` (`/sse`) | 舊版僅支援 SSE 的 MCP Client |
| **健康檢查** | `GET http://<host>:8090/health` | 監控與反向代理探測 |

---

## 2. 認證與角色權限（RBAC）

建議直接開啟 Web Console 左側 **「MCP 接入」** 頁面（`http://localhost:5180`）為每個外部 Agent 系統發行獨立 API Token：

- 原始金鑰格式為 `gcc_mcp_...`，僅於發行當下顯示一次，資料庫僅儲存 SHA-256 雜湊值。
- 每個 Token 皆可設定**來源標記 (`source_tag`)**（自動標註在任務佇列中）與**權限角色 (`role`)**，並紀錄呼叫次數與最後使用時間，可隨時一鍵撤銷。

| 角色 | 允許操作 | 建議對象 |
| --- | --- | --- |
| `read` | 查詢主機 GPU／顯存／溫度、可用模型、任務清單與排程說明、能耗電費、MCP Resources | 監控機器人、看板 Agent |
| `infer` | 包含 `read` ＋ 同步推論 (`run_inference`)、非同步提交 (`submit_job`)、取消與重試 | **大多數外部 Agent 系統（推薦預設）** |
| `admin` | 包含 `infer` ＋ 模型載入／卸載（含指定 GPU）、切換主機預設執行器、維護模式 | 維運／自動化部署 Agent |

> **自動防護機制**：本機尚未發行任何 Token 且未設定 `MCP_API_KEY` 時，維持本機免密開放模式方便開發。一旦在 GUI 發行第一組有效 Token（或設定 `MCP_API_KEY` / `MCP_REQUIRE_AUTH=true`），所有 HTTP MCP 請求將自動強制驗證 `Authorization: Bearer <token>`（或 `X-API-Key: <token>`）。
> 若主機另有啟用「主機操作密碼」，`admin` 角色執行 `manage_model` 仍需傳入 `host_password` 走正規解鎖流程，不會繞過主機密碼保護。

---

## 3. 跨主機 / 區網 / 外部網路開放設定

預設 `compose.yaml` 僅綁定 `127.0.0.1:8090`。若要讓其他電腦上的 Agent 系統直接連線，請在專案根目錄 `.env` 調整以下參數並重新執行 `./scripts/start.sh`：

```dotenv
# 監聽所有網卡（或指定區網 IP）
MCP_BIND_HOST=0.0.0.0
MCP_PORT=8090

# 放寬 Host Header 檢查：可填特定主機白名單，或在反向代理／區網環境設為 *
MCP_ALLOWED_HOSTS=192.168.1.50:*,gpu.lab.internal:*
# 若透過瀏覽器或跨域代理，也可設定 MCP_ALLOWED_ORIGINS

# 可選：若要強制啟動即需認證（即使尚未在 GUI 發行 Token）
MCP_REQUIRE_AUTH=true
```

若要暴露到公網，強烈建議前方架設 HTTPS 反向代理（Nginx / Caddy / Cloudflare Tunnel / Tailscale Serve）指向 `http://127.0.0.1:8090/mcp` 或 `http://127.0.0.1:5180/mcp`，並為每個合作夥伴發行獨立的 `infer` 或 `read` Token。

---

## 4. 各大 Agent 系統接入範例

Web Console 的 **「MCP 接入」** 頁面可自動填入端點與剛發行的 Token 並一鍵複製。以下為常用設定格式：

### Claude Code

```bash
claude mcp add --transport http gpu-control-center \
  http://<host>:8090/mcp \
  --header "Authorization: Bearer gcc_mcp_YOUR_TOKEN"
```

### Codex CLI

```bash
export GPU_MCP_TOKEN="gcc_mcp_YOUR_TOKEN"
codex mcp add gpu-control-center \
  --url http://<host>:8090/mcp \
  --bearer-token-env-var GPU_MCP_TOKEN
```

### Cursor / Windsurf / 一般 Streamable HTTP Client

```json
{
  "mcpServers": {
    "gpu-control-center": {
      "url": "http://<host>:8090/mcp",
      "headers": {
        "Authorization": "Bearer gcc_mcp_YOUR_TOKEN"
      }
    }
  }
}
```

### Claude Desktop 或僅支援 stdio 的 Agent（使用獨立橋接套件）

不需在對方機器部署 Docker 或整個 repo，直接用 `uvx` 啟動本專案內建的 `mcp-package`（`gpu-control-center-mcp`）：

```json
{
  "mcpServers": {
    "gpu-control-center": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/yankesswang/GPU-Control-Center.git#subdirectory=mcp-package",
        "gpu-control-center-mcp"
      ],
      "env": {
        "GPU_CONTROL_CENTER_MCP_URL": "http://<host>:8090/mcp",
        "GPU_CONTROL_CENTER_MCP_TOKEN": "gcc_mcp_YOUR_TOKEN"
      }
    }
  }
}
```

本機開發也可直接指向目錄：`--from /path/to/GPU-Control-Center/mcp-package`。

### Python Agent（官方 MCP SDK / LangChain / OpenAI Agents）

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    url = "http://<host>:8090/mcp"
    headers = {"Authorization": "Bearer gcc_mcp_YOUR_TOKEN"}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # 一站式同步推論（若省略 model，自動挑選在線主機偏好的可用 LLM）
            res = await session.call_tool("run_inference", {
                "prompt": "請用繁體中文簡述集中式 GPU 排程的優點。",
                "max_tokens": 256,
            })
            print(res.structuredContent["text"])

asyncio.run(main())
```

---

## 5. 工具、資源與提示詞總覽

### Tools（17 個）

| 工具 | 權限 | 說明 |
| --- | --- | --- |
| `list_resources` | `read` | 取得完整主機、模型與平台概況（向下相容） |
| `list_nodes` | `read` | 精簡列出主機 GPU 顯存／使用率／溫度／功耗、常駐模型與目前占用工作槽的任務 |
| `list_models` | `read` | 依執行器（`ollama` / `lmstudio` / `comfyui`）、可用狀態與模型類型篩選模型 |
| `run_inference` | `infer` | **推薦主力工具**：提交推論並在伺服端等待完成，直接回傳生成文字與 tokens/s（省略 `model` 時自動挑選可用 LLM） |
| `submit_job` | `infer` | 非同步建立推論任務並立即回傳 `job_id`（支援 SLA、deadline 與備援模型參數） |
| `wait_for_job` | `read` | 在伺服端等待指定任務進入終止狀態（並發送 MCP 進度通知） |
| `list_jobs` | `read` | 查詢近期任務清單，可依 `status` 與 `source` 篩選 |
| `get_job` | `read` | 查詢單一任務狀態、即時生成文字、事件時間軸與最終結果 |
| `explain_wait` | `read` | 查詢即時佇列排序、相容主機、工作槽持有者與阻擋原因（不虛構 ETA） |
| `cancel_job` | `infer` | 取消排隊任務，或對執行中任務請求安全取消（等待生成邊界確認） |
| `retry_job` | `infer` | 以原提示詞與參數重試已失敗或已取消的推論任務 |
| `get_energy_analytics` | `read` | 查詢即時 GPU 功耗、區間用電度數、時間電價估算、CO2 與逐卡／逐模型能耗 |
| `get_scheduler_state` | `read` | 查詢動態排程策略、各主機健康與熱模型、多服務公平壓力、即時派送計畫與決策日誌 |
| `simulate_dispatch` | `read` | 以即時叢集狀態試算推論請求會派送至哪台主機、評分拆解與決策動作（不實際入列） |
| `manage_model` | `admin` | 在指定主機載入或卸載模型（支援 LM Studio / ComfyUI `gpu_indices`、`replace` 與 `host_password`） |
| `set_default_runtime` | `admin` | 切換指定主機預設執行器（`lmstudio` 或 `ollama`） |
| `set_node_maintenance` | `admin` | 開啟或關閉指定主機維護模式 |

### Resources（3 個）

- `gpu://overview` — 叢集總覽 JSON 快照
- `gpu://nodes` — 所有運算主機與 GPU 精簡 JSON 快照
- `gpu://models` — 可用模型目錄 JSON 快照

### Prompts（2 個）

- `cluster_health_check` — 引導 Agent 檢查叢集硬體、排隊瓶頸、待確認工作與電費功耗
- `run_local_llm` — 引導 Agent 挑選本機可用模型並執行指定任務

---

## 6. 驗證

```bash
make test       # 隔離 PostgreSQL 單元與整合測試（含 MCP Token、RBAC、全部工具）
make test-mcp   # 真實 MCP → 中央 API → Worker 端對端協議測試
```

若已啟用認證，可在執行 `make test-mcp` 時傳入環境變數：`docker compose exec -T -e MCP_API_KEY=<your-token> mcp python tests/smoke_mcp.py`。
