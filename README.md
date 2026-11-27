# GPU Control Center

**多應用、多 Agent 共用本機與區網 GPU 的中央控制與排程平台。**

整合 React + TypeScript 雙語管理介面（繁體中文 / English）、FastAPI 中央排程 API、PostgreSQL 持久化狀態、原生主機硬體監控 Agent，以及面向外部 Agent 系統的 **Model Context Protocol (MCP)** 服務；已接通 **LM Studio**、**Ollama** 真實推論與模型控制，以及 **ComfyUI** 監控與模型快取管理。

---

## 核心亮點

- **中央排程與動態路由（Dynamic Scheduler）**
  - 單一真相來源：Web Console、HTTP API、原生 Agent 與 MCP 全數共用中央 API 與 PostgreSQL 任務佇列，不建立平行佇列。
  - 支援 **SLA 分級**（`realtime` / `interactive` / `standard` / `batch` / `background`）、**多服務公平權重（Fair-Share）**、**等待老化（Aging）**、**短任務優先（SJF）**、**常駐模型熱命中親和（Warm Affinity）**、**GPU 溫度與尖峰電價保護**，並提供不實際入列的 **Dispatch 試算（Dry-Run）** 與完整排程說明。
  - 安全租約與取消語意：網路中斷或狀態不確定時標記為 `unknown` 並保留工作槽，不自動重派；執行中的推論取消等待確認生成邊界與模型釋放後完成。
- **即插即用的外部 Agent MCP 接入（MCP Hub）**
  - 提供 **17 個標準 MCP Tools**、**3 個 MCP Resources**（`gpu://overview`、`gpu://nodes`、`gpu://models`）與 **2 個 Prompts**。
  - 支援 **Streamable HTTP（`/mcp`）**、**Legacy SSE（`/sse`）**、**Nginx 同源代理（`:5180/mcp`）**，以及獨立可安裝的 **stdio 橋接套件（[`mcp-package/`](mcp-package/)，`uvx` 免部署 Docker 即可使用）**。
  - 內建 **RBAC 分級 API Token**（`read` / `infer` / `admin`），僅儲存 SHA-256 雜湊、自動標註任務來源與呼叫統計；Web Console 提供 Claude Code、Codex CLI、Cursor / Windsurf、Claude Desktop、Python SDK 與 cURL 一鍵複製設定。
- **多執行器與多主機納管**
  - 自動發現 **LM Studio**、**Ollama** 與 **ComfyUI** 已安裝與常駐模型，支援模型別名、批次隱藏、切換主機預設執行器，以及 **LM Studio / ComfyUI 逐卡指定 GPU 載入與卸載**。
  - 支援**原生主機 Agent**（完整 NVIDIA GPU 遙測、CPU/RAM 與本地推論）以及**免安裝區網自動探索**（輸入主機 IP 即平行探測 1234 / 11434 / 8188 常用埠並納入中央管理）。
  - 支援**逐主機操作密碼保護**（`scrypt` 雜湊、簽章 HttpOnly Cookie / Header Token 與防暴力破解限流），保護模型載卸等高權限操作。
- **電費核算與長期硬體追蹤**
  - **電費與能耗分析**：依即時 GPU 功耗結合 PUE、主機基礎功耗與時間電價（尖峰／離峰／夏月／非夏月）核算度數、費用與 CO₂，提供 7×24 熱圖、逐卡／逐模型成本歸因、實際電費帳單對帳與 CSV 匯出。
  - **硬體歷史追蹤**：長期記錄 GPU 溫度、風扇、顯存、時脈、功耗與主機負載趨勢，自動偵測高溫／高顯存等異常事件並留存維運日誌。

---

## 系統架構

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  外部 Agent 系統 (Claude Code / Cursor / Codex / Claude Desktop / Python) │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ Streamable HTTP (/mcp) 或 uvx stdio bridge
                                ▼ (Bearer Token + RBAC: read / infer / admin)
┌─────────────────┐      ┌────────────────────┐      ┌────────────────────┐
│  React Web UI   │─────▶│  FastAPI 中央 API   │◀─────│    MCP Server      │
│  (Nginx :5180)  │ /api │  (:8088)           │ HTTP │    (:8090/mcp)     │
└─────────────────┘      └─────────┬──────────┘      └────────────────────┘
                                   │ SQLAlchemy + Alembic
                                   ▼
                         ┌────────────────────┐
                         │  PostgreSQL 17     │  (佇列 / 租約 / 遙測 / Token)
                         └─────────┬──────────┘
                                   │ Heartbeat / Claim / Report (X-Agent-Token)
                  ┌────────────────┴────────────────┐
                  ▼                                 ▼
      ┌───────────────────────┐         ┌───────────────────────┐
      │  本機原生 Host Agent   │         │  遠端主機 / 區網探索    │
      │  (systemd user svc)   │         │  (LAN / SSH Tunnel)   │
      │  NVIDIA GPU + Ollama  │         │  LM Studio / Ollama / │
      │  + LM Studio + ComfyUI│         │  ComfyUI              │
      └───────────────────────┘         └───────────────────────┘
```

---

## 快速開始

### 系統需求

- Docker 與 Docker Compose v2+
- Python 3.10+ 與 [uv](https://docs.astral.sh/uv/)（安裝原生主機 Agent 或執行 `uvx` 套件時需要）
- Linux `systemd --user`（若需安裝原生主機 Agent）
- 至少一個可連線的 **LM Studio** 或 **Ollama**（模型由既有執行器提供，本平台不會自動下載或搬動模型檔案）
- 主機端不需安裝 Node.js（前端於 Docker Node 22 映像中建置）

### 1. 啟動中央服務

```bash
git clone https://github.com/yankesswang/GPU-Control-Center.git
cd GPU-Control-Center
./scripts/start.sh
```

首次執行會自動建立權限 `600` 的 `.env`、產生隨機資料庫密碼與 `AGENT_TOKEN`、套用全部 Alembic migrations，並等待四個容器通過健康檢查。

| 服務 | 預設位址 | 說明 |
| --- | --- | --- |
| **Web Console（GUI）** | `http://localhost:5180` | 8 大工作區（總覽、任務、動態排程、主機、模型、電費、硬體追蹤、MCP 接入） |
| **中央 API 文件** | `http://localhost:8088/docs` 或 `http://localhost:5180/docs` | OpenAPI / Swagger 互動文件 |
| **API 健康檢查** | `http://localhost:8088/health` | API 與 PostgreSQL 連線檢查 |
| **MCP 端點** | `http://localhost:8090/mcp` 或 `http://localhost:5180/mcp` | Streamable HTTP MCP 服務（健康檢查：`http://localhost:8090/health`） |
| **PostgreSQL** | `127.0.0.1:5440` | 帳密見本機 `.env` |

### 2. 安裝本機原生 Host Agent

在 `.env` 確認本機執行器位址（若使用非預設連接埠或啟用 LM Studio Token，請於此調整）：

```dotenv
OLLAMA_URL=http://127.0.0.1:11434
LMSTUDIO_URL=http://127.0.0.1:1234
# LMSTUDIO_API_TOKEN=若 LM Studio 開啟認證再設定
COMFYUI_URL=http://127.0.0.1:8188
```

執行安裝腳本：

```bash
./scripts/install-host-agent.sh
```

腳本會在 `backend/.venv` 安裝依賴並啟動 systemd user 服務 `gpu-control-center-agent.service`。Agent 在主機原生執行，可直接讀取 NVIDIA GPU 資訊與本機 loopback 服務，不需掛載 Docker socket。

---

## 讓外部 Agent 系統直接透過 MCP 使用

完整設定指南與工具清單請見 **[docs/mcp.md](docs/mcp.md)**。

### 最快接入流程

1. 開啟 Web Console（`http://localhost:5180`）左側 **「MCP 接入」** 分頁。
2. 點選 **「發行 Token」**，指定 Agent 名稱、來源標記（`source_tag`）與角色：
   - `read`：唯讀查詢硬體、模型、任務、排程與電費
   - `infer`：**推薦給一般外部 Agent**（可執行 `run_inference` 同步推論、`submit_job`、`cancel_job`、`retry_job`）
   - `admin`：額外開放模型載入／卸載（含指定 GPU）、切換執行器與維護模式
3. 複製頁面自動產生的設定檔給外部 Agent：

#### Claude Code（Streamable HTTP）

```bash
claude mcp add --transport http gpu-control-center \
  http://<host>:8090/mcp \
  --header "Authorization: Bearer gcc_mcp_YOUR_TOKEN"
```

#### Cursor / Windsurf / 一般 HTTP MCP Client

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

#### Claude Desktop 或僅支援 stdio 的 Agent（免部署 Docker，使用 `uvx`）

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

#### 跨主機 / 區網開放設定

預設 MCP 僅綁定 `127.0.0.1:8090`。若需開放給區網其他機器或透過反向代理對外提供服務，請先發行至少一組 Token（系統將自動強制驗證 Bearer Token），再於 `.env` 設定並重啟：

```dotenv
MCP_BIND_HOST=0.0.0.0
MCP_ALLOWED_HOSTS=192.168.1.50:*,gpu.lab.internal:*   # 或設為 *（搭配反向代理／區網）
```

---

## 新增其他運算主機

### 方式 A：GUI 免安裝區網加入（最快）

在 **「運算主機」** 或 **「運算總覽」** 點選 **「新增主機」**，輸入名稱與 IP／主機名稱（例如 `10.8.22.92`），可選填主機操作密碼。系統會平行偵測 LM Studio (`1234`)、Ollama (`11434`)、ComfyUI (`8188`)（亦支援自訂埠），確認後即持久化至 PostgreSQL 並由中央監督器自動接管推論與模型控制。

### 方式 B：安裝原生 Agent（含完整 GPU / CPU / 功耗監控）

若遠端主機需要完整逐卡溫度、顯存、功耗與歷史追蹤，先建立 SSH 通道指向中央 API，再執行連線腳本：

```bash
ssh -N -L 8088:127.0.0.1:8088 使用者@中央主機
# 於另一終端機執行：
./scripts/connect-host.sh http://127.0.0.1:8088 gpu-worker-02
```

安裝時會隱藏輸入中央 `AGENT_TOKEN` 並產生權限 `600` 的 `.env.agent`（已被 Git 忽略）。

---

## 透過 HTTP API 提交推論與控制模型

```bash
# 1. 查詢可用模型 ID
curl -s http://localhost:8088/api/models

# 2. 建立推論任務（支援 SLA 等級、截止時間、備援模型與優先級）
curl -s http://localhost:8088/api/jobs -H 'Content-Type: application/json' -d '{
  "name": "摘要生成",
  "source": "my-app",
  "model": "ollama:qwen2.5:0.5b",
  "prompt": "請用繁體中文三點說明集中式 GPU 排程的優點。",
  "max_tokens": 256,
  "priority": 0
}'

# 3. 查詢結果、排程說明、取消與重試
curl -s http://localhost:8088/api/jobs/<job_id>
curl -s http://localhost:8088/api/jobs/<job_id>/explain
curl -s -X POST http://localhost:8088/api/jobs/<job_id>/cancel
curl -s -X POST http://localhost:8088/api/jobs/<job_id>/retry
```

模型控制與執行器偏好：

```text
PATCH /api/nodes/{node_id}/default-runtime   {"runtime": "lmstudio"}
POST  /api/nodes/{node_id}/model-actions     {"model": "lmstudio:model-key", "action": "load", "gpu_indices": [0]}
POST  /api/nodes/{node_id}/model-actions     {"model": "lmstudio:model-key", "action": "unload"}
```

---

## 日常維運、啟停與測試

```bash
./scripts/start.sh           # 建置並啟動容器；若已安裝原生 Agent 也會一併啟動
./scripts/stop.sh            # 安全停止原生 Agent 與本專案容器，保留 PostgreSQL 資料
make test                    # 隔離 PostgreSQL schema 整合與單元測試（93+ 項）
make test-mcp                # 真實 MCP → 中央 API → Worker 端對端協議測試
make logs                    # 檢視容器即時日誌

# 原生 Agent 狀態與日誌
systemctl --user status gpu-control-center-agent.service
systemctl --user restart gpu-control-center-agent.service
journalctl --user -u gpu-control-center-agent.service -f
```

> **關機提醒**：部分 Linux 主機環境下 Docker daemon 直接送出停止訊號可能權限受限，請一律使用 `./scripts/stop.sh` 進行安全關閉，切勿重啟共用 Docker daemon。

---

## 執行與安全邊界

- **排程工作槽**：目前每台受管主機維持**單一受管工作槽（1 slot per host）**以確保模型載卸與推論互斥；不對外宣稱硬性 VRAM 預留。LM Studio 與 ComfyUI 支援載入時指定目標 GPU，Ollama 則由該服務本身決定 GPU 配置。
- **不協調外部直連流量**：本平台僅協調經過中央 API／MCP 的請求；若外部應用直接呼叫 Ollama / ComfyUI，動態排程器會偵測外部 GPU 壓力與 ComfyUI 佇列狀態進行避讓，但不會強行中止非受管工作。
- **Ollama 常駐管理**：預設推論帶 `keep_alive=0`，經平台手動載入的模型維持 `keep_alive=-1` 直到手動卸載；生成結束後會核對 `/api/ps` 才釋放工作槽。
- **模擬模式**：預設 `ENABLE_DEMO=false`。僅於開發排程測試時啟用 `ENABLE_DEMO=true` 並執行 `docker compose --profile demo up -d --build`，所有模擬主機與指標皆明確標記 `simulated`。
- **安全提醒**：Web Console 與管理 HTTP API 目前無使用者帳號登入，預設僅綁定 `127.0.0.1`；對外共享 GPU 建議透過 **MCP 端點＋分級 Bearer Token**（搭配 HTTPS 反向代理），切勿將未設防的管理埠直接暴露至公網。

---

## 專案結構

```text
frontend/                     React + TypeScript + Vite 雙語工作台，Nginx 同源代理（含 /api 與 /mcp）
backend/app/main.py           FastAPI 中央 API、佇列狀態、Agent/MCP 端點
backend/app/scheduler.py      動態排程引擎（Fair-Share、SLA、熱模型親和、健康評分、試算）
backend/app/mcp_server.py     MCP Server（17 Tools、3 Resources、2 Prompts、RBAC Auth Middleware）
backend/app/mcp_auth.py       MCP Token 發行／SHA-256 驗證與工具目錄定義
backend/app/agent.py          主機原生硬體監控、模型探索、租約與執行器
backend/app/runtimes.py       Ollama 串流推論、模型控制與常駐驗證
backend/app/services.py       LM Studio 推論／模型控制與 ComfyUI 監控／快取控制
backend/app/placement.py      NVIDIA per-GPU 程序顯存觀測與模型落點正規化
backend/app/energy.py         GPU 功耗採樣、時間電價估算、統計分析與帳單對帳
backend/app/hardware_history.py 長期硬體歷史遙測、異常告警與維運事件紀錄
backend/app/host_auth.py      逐主機操作密碼（scrypt + 簽章會話 + 限流）
backend/app/connections.py    區網運算主機自動服務探測與遠端監督
backend/migrations/           Alembic PostgreSQL schema migrations（0001–0012）
backend/tests/                隔離 PostgreSQL 整合測試與即時 MCP smoke test
mcp-package/                  獨立可安裝 stdio↔HTTP MCP 橋接 CLI 套件（gpu-control-center-mcp）
integrations/comfyui/         選用 ComfyUI 內部模型顯存遙測擴充
scripts/                      啟停、本機與遠端原生 Agent 安裝腳本
docs/                         MCP 接入指南、前端設計與驗證文件
```

---

GitHub：https://github.com/yankesswang/GPU-Control-Center
