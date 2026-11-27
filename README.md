# GPU Control Center

多應用共用 GPU 的中央控制平台。React 管理介面、FastAPI、PostgreSQL、MCP，以及主機原生 Agent，已接通真實 LM Studio／Ollama 推論，以及 ComfyUI 唯讀監控。

## 已可使用

- 主機實際 CPU、RAM、NVIDIA GPU 使用率／顯存／溫度／功耗。
- 自動發現 LM Studio／Ollama 已安裝模型，從 GUI、HTTP API 或 MCP 提交提示詞。
- 持久化排程、文字逐步更新、最終 token 數／速度與執行結果。
- 優先級與等待時間排序、維護模式、等待原因及工作槽持有者查詢。
- 取消排隊工作；執行中的 Ollama 工作等待確認生成結束與卸載後取消。
- 網路中斷或執行狀態不確定時，保留工作槽，不自動重新派送。

預設不啟動模擬 Agent。模型由你現有的 LM Studio 或 Ollama 執行，不會重新部署執行器或搬動模型目錄。

## 安裝與啟動

需求：Docker、Docker Compose v2+、Python 3、[uv](https://docs.astral.sh/uv/)、Linux systemd user session，以及可連線的 LM Studio 或 Ollama。主機不用安裝 Node；前端在 Docker 的 Node 22 中建置。

```bash
git clone https://github.com/yankesswang/GPU-Control-Center.git
cd GPU-Control-Center
./scripts/start.sh
```

首次執行會產生 `.env`、隨機資料庫密碼與 Agent token，建立資料庫 schema，等待四個容器健康檢查。

在 `.env` 設定你的 Ollama 位址，例如：

```dotenv
OLLAMA_URL=http://127.0.0.1:11434
```

本次實際安裝主機使用 `http://127.0.0.1:11435`，已設定於不進入 Git 的本機 `.env`。安裝原生 Agent：

```bash
./scripts/install-host-agent.sh
```

會在 `backend/.venv` 安裝 Python 依賴，註冊並啟動 user service `gpu-control-center-agent.service`。Agent 原生執行，能讀取主機 GPU 及 loopback Ollama，不需要把 Docker socket 掛給它。

若 Ollama 尚無模型，可在其主機執行 `ollama pull qwen2.5:0.5b`，或呼叫該服務的 `/api/pull`。Agent 會在下一次心跳發現模型。此平台不會根據任務任意下載模型。

| 服務 | 預設位址 |
| --- | --- |
| GUI | http://localhost:5180 |
| API 文件 | http://localhost:8088/docs 或 http://localhost:5180/docs |
| 健康檢查 | http://localhost:8088/health |
| MCP | http://localhost:8090/mcp（Streamable HTTP） |
| PostgreSQL | localhost:5440，帳密見本機 `.env` |

## 選擇執行器與管理模型

在「模型與能力」或主機硬體區選擇「預設執行器」：LM Studio 或 Ollama。偏好儲存在 PostgreSQL，每台主機各自設定；推論表單優先顯示第一台主機偏好的文字模型。此設定不會停止另一個服務。

```dotenv
LMSTUDIO_URL=http://127.0.0.1:1234
COMFYUI_URL=http://127.0.0.1:8188
# LMSTUDIO_API_TOKEN=若 LM Studio 開啟認證，於本機 .env 設定
```

位址是原生 Agent 所在主機看到的位址；修改後重啟 Agent。LM Studio 需要支援原生 `/api/v1/models`、`/api/v1/models/load`、`/api/v1/models/unload` 的版本。UI 可載入／卸載 LLM 和 embedding；文字推論只接受 LLM。卸載會移除所選模型的所有已載入實例，其他模型不受影響。LM Studio 的自動卸載／auto-evict 設定仍由該服務控制；平台不偽造它沒有提供的每模型顯存大小。

```text
PATCH /api/nodes/{node_id}/default-runtime   {"runtime":"lmstudio"}
POST /api/nodes/{node_id}/model-actions      {"model":"lmstudio:model-key","action":"load"}
POST /api/nodes/{node_id}/model-actions      {"model":"lmstudio:model-key","action":"unload"}
```

ComfyUI 區塊唯讀顯示連線、版本、佇列數與 prompt ID、分類模型檔案，以及服務回報的裝置顯存。使用 GET `/system_stats`、`/queue`、`/models`、`/models/{folder}`，不提交／取消工作流或卸載模型。檔案清單不代表模型常駐狀態；每分類最多顯示 200 筆、最多讀取 32 類，超限或讀取失敗會標記。

## 開始推論

在 GUI 按「建立任務」，選擇已安裝模型、輸入提示詞與最大生成 tokens。任務詳情顯示文字、來源應用、模型與排程紀錄。LLM 生成長度不固定，所以執行中不顯示虛構完成百分比。

HTTP 範例（請先從 `GET /api/models` 確認模型 ID）：

```bash
curl http://localhost:8088/api/jobs -H 'Content-Type: application/json' -d '{
  "name": "文字推論",
  "source": "my-app",
  "model": "ollama:qwen2.5:0.5b",
  "prompt": "請用繁體中文說明 GPU 排程的用途。",
  "max_tokens": 128,
  "priority": 0
}'
```

回應 `id` 是任務 ID。使用 `GET /api/jobs/{id}` 查詢文字與結果、`GET /api/jobs/{id}/explain` 查排程說明、`POST /api/jobs/{id}/cancel` 取消。失敗或取消的任務可用 `POST /api/jobs/{id}/retry` 建立新的工作，保留原提示詞與參數。

[MCP 接入方式與五個工具](docs/mcp.md)。

## 啟停與檢查

```bash
./scripts/start.sh           # 啟動容器；若原生 Agent 已安裝，也會啟動它
./scripts/stop.sh            # 正常停止原生 Agent 與此專案容器，保留資料庫
make test                   # PostgreSQL schema 隔離測試與 adapter 邊界測試
make test-mcp               # 真實 MCP → 中央 API → Ollama 端對端測試
systemctl --user status gpu-control-center-agent.service
journalctl --user -u gpu-control-center-agent.service -f
docker compose logs -f --tail=100
```

修改 Agent 程式後用 `systemctl --user restart gpu-control-center-agent.service` 載入新程式。修改 `.env` 的 Ollama 位址後也需重啟 Agent。若修改 API 連接埠，重新執行安裝腳本更新 unit。

這台主機曾出現 Docker daemon 無法直接送出停止訊號的問題；`stop.sh` 從服務內正常關閉程序，僅操作本專案，不會重啟共用 Docker daemon。

## 執行與資源邊界

目前每個主機只有一個受管理的工作槽，即使有多張 GPU 也會序列化本平台的請求。Ollama 自己選擇 GPU；這版尚未提供每卡放置或顯存硬性預留。主機圖表是整台主機的數據，包含其他程序。

LM Studio／Ollama 的模型載入與卸載也會建立中央任務，使用同一個主機工作槽。只有指定主機能領取操作；執行中的操作必須等待服務確認，不能強行取消。

本平台只協調經過中央 API 的請求。其他應用若直接打 Ollama 或 ComfyUI，仍可能競爭 GPU。尚未接通 ComfyUI 執行器，不會停止既有 ComfyUI 工作或任意卸載其他服務。

Ollama 預設請求使用 `keep_alive=0`；經 UI 手動載入的模型，後續推論使用 `keep_alive=-1`，直到 UI 手動卸載。偏好由中央已完成操作紀錄決定，Agent 重啟後仍有效。每次生成後都檢查 `/api/ps`，確認常駐狀態符合要求後才釋放工作槽。若模型仍被其他 client 使用、連線中斷或收不到 done，任務會進入 `unknown`；保留工作槽等待人工確認，不會把不確定的執行當作已停止。

執行中取消是「等待本次生成結束並確認卸載」，不是任意中斷 GPU kernel。最大生成長度 2048 tokens，context 設為 2048；長提示詞可能受 Ollama context 限制。

若 Agent 重啟時有未完成任務，中央會保留待確認狀態。此版沒有強制清空工作槽按鈕，需確認遠端已停止後人工處理。

目前 GUI／管理 API 沒有使用者登入，MCP 沒有 OAuth；公開到主機的連接埠綁定 `127.0.0.1`，供本機使用。Agent API 使用共用 token。不要將此開發版直接公開到網際網路。

## 開發結構

```text
frontend/                     React + TypeScript + Vite，Nginx 同源代理
backend/app/main.py           中央排程、任務狀態、Agent API、等待說明
backend/app/agent.py          原生硬體監控、模型發現、租約與執行
backend/app/runtimes.py        Ollama 串流、模型控制與常駐確認
backend/app/services.py        LM Studio 推論／模型控制、ComfyUI 唯讀快照
backend/app/mcp_server.py      共用中央 API 的 MCP 工具
backend/migrations/           Alembic schema migrations
backend/tests/                PostgreSQL／adapter 測試與 MCP smoke test
scripts/                      啟動、停止、原生 Agent 安裝
```

使用 Node 22+ 可在 `frontend/` 執行 `npm ci && npm run dev -- --port 5181`，與容器 GUI 並存。Vite 將 `/api` 代理到 `127.0.0.1:8088`。

只在開發排程時需要模擬模式：設定 `ENABLE_DEMO=true` 後使用 `docker compose --profile demo up -d --build`。模擬主機與工作會明確標示 simulated；正式日常使用維持 `ENABLE_DEMO=false`。

GitHub：https://github.com/yankesswang/GPU-Control-Center
