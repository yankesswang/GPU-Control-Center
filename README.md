# GPU Control Center

跨服務中央推論平台的可執行起點：React 管理介面、FastAPI 控制服務、PostgreSQL 持久化任務佇列，以及獨立 Agent。另提供 MCP 工具，與 GUI 共用同一套中央任務 API。

## 一鍵啟動

需求：Docker Engine、Docker Compose v2+、Python 3（首次產生本機設定）。主機不需要安裝 Node 或 Python 套件。

```bash
cd /home/trx50/Project/GPU-Control-Center
./scripts/start.sh
```

首次啟動會建立被 Git 忽略的 `.env`，自動產生資料庫密碼與 Agent token，建置映像、執行 Alembic migration，並等待服務健康檢查。

| 服務 | 位址 |
| --- | --- |
| GUI | http://localhost:5180 |
| API 文件 | http://localhost:8088/docs 或 http://localhost:5180/docs |
| API 健康檢查 | http://localhost:8088/health |
| MCP | http://localhost:8090/mcp（Streamable HTTP） |
| PostgreSQL | localhost:5440；帳號與資料庫預設 `inference`，密碼見 `.env` |

連接埠可在 `.env` 修改。資料庫存放於 Docker named volume，停止或重建容器後仍保留。

```bash
docker compose ps
docker compose logs -f --tail=100
./scripts/stop.sh                # 正常停止，保留資料
./scripts/start.sh              # 再次啟動
make test                       # 在獨立 PostgreSQL schema 執行整合測試
```

若主機的 Docker/AppArmor 出現 `cannot stop container: permission denied`，請先執行 `./scripts/stop.sh` 再重新啟動。此腳本僅操作此 Compose 專案，從服務內正常關閉程序，並保留資料庫 volume。此主機已遇到此問題，因此也提供 `make down` 使用同一腳本。

本機管理版的 GUI／管理 API 尚無使用者登入，所有對主機公開的連接埠均綁定 `127.0.0.1`。目前供本機開發使用；跨網段正式部署前需加入管理者認證、TLS 與逐節點身分驗證。Agent API 目前使用共用 token。

## 目前能做什麼

- 主機總覽、每兩秒更新狀態、維護模式（停止新接單，既有任務繼續）。
- 建立模擬工作、依優先級與等待時間排程、進度與事件紀錄、結果保存。
- 排隊任務立即取消；執行中任務由 Agent 確認停止後取消。
- 已確認失敗或取消的任務可以建立新的重試任務，保留原紀錄。
- Agent 定期心跳；超過 20 秒未回報即顯示離線。
- 使用 PostgreSQL row lock 與 `SKIP LOCKED`，避免同一任務被兩台主機領取；每台主機一個工作槽。
- 任務回報攜帶 `attempt_id`；租約 30 秒，回報時續租。過期任務進入 `unknown`，保留該節點工作槽，不自動重派。原 Agent 可確認完成／停止；Agent 遺失時需人工核實後處理資料庫，目前沒有強制釋放按鈕。

**預設的 `demo-agent` 不執行 AI 推論。它使用明確標記的模擬硬體資料，並執行 `demo.progress` 步驟工作，驗證 API → 排程 → Agent → 進度 → PostgreSQL 的流程。**

真正的模型推論、模型下載／載入、GPU 資源預留、LLM 串流、細粒度 GPU 排程、多租戶配額與高可用控制平面尚未實作。

## 結構與資料流

```text
frontend/                  React + TypeScript + Vite；Nginx 同源代理 /api
backend/app/main.py        管理 API、Agent API、中央領取規則、租約檢查
backend/app/agent.py       主動連線 Agent：demo 執行或 host 監控
backend/app/models.py      Node / Job / Event
backend/migrations/        Alembic 版本化 schema
backend/tests/             PostgreSQL 整合測試（獨立暫存 schema）
compose.yaml               web / api / db / demo-agent / mcp
scripts/start.sh            設定產生、建置、migration、健康檢查
```

應用提交任務 → PostgreSQL 保存 `queued` → Agent 向中央請求工作 → 中央鎖定節點、檢查能力與工作槽、選擇任務並建立租約 → Agent 執行及回報 → GUI 定期查詢進度。

第一版的排程邏輯位於 API 的 claim 交易內，無另設記憶體佇列；PostgreSQL 是唯一任務狀態來源。租約檢查在 API 背景程序執行，這版部署為單一 API instance。

```text
queued → running → completed / failed
queued → cancelled
running → cancelling → cancelled
running / cancelling → unknown（租約逾時，不重新派送）
unknown → 原 Agent 回報後確認狀態
```

## 應用提交任務

```bash
curl -X POST http://localhost:8088/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"name":"排程驗證","source":"my-app","model":"demo.progress","steps":20,"priority":0}'
```

回應包含 `id`；使用 `GET /api/jobs/{id}` 查詢進度、事件與結果。`POST /api/jobs/{id}/cancel` 取消；`POST /api/jobs/{id}/retry` 為可重試的任務建立新工作。

## 接入實際主機監控

在要監控的主機，使用 Python 3.12+，從此 repo 安裝依賴。取得中央 `.env` 的 `AGENT_TOKEN` 後，透過安全的環境變數或服務管理器傳入，不要加入 Git。

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export API_URL=http://127.0.0.1:8088
export NODE_ID=my-gpu-host
read -rs -p 'Agent token: ' AGENT_TOKEN
export AGENT_TOKEN
python -m app.agent --mode host
```

`host` 模式回報實際 CPU、RAM；若 `nvidia-smi` 可用，另回報 NVIDIA GPU 顯存、使用率、溫度及功耗。沒有 GPU 時仍能監控 CPU 與 RAM。請在主機原生執行以取得主機視角；容器中的 CPU／記憶體數據可能受 namespace 與 cgroup 影響。

`host` 模式目前只有監控能力，不會接收 `demo.progress` 或其他推論工作。每台主機必須使用不同的 `NODE_ID`。同一 ID 不可同時啟動多個 Agent；Agent 重啟後未完成的舊任務可能停留在待確認狀態。

遠端主機可先透過 SSH tunnel 接入中央的 loopback API，例如在遠端執行 `ssh -N -L 8088:127.0.0.1:8088 user@central-host`，再將 `API_URL` 指向遠端自身的 `http://127.0.0.1:8088`。

## 開發

全容器建置不依賴宿主機 Node 版本。若要前端熱更新，請使用 Node 22+：

```bash
cd frontend
npm ci
npm run dev
```

Vite 預設使用 5180，若 Compose GUI 已占用，可用 `npm run dev -- --port 5181`。`/api` 代理至 `127.0.0.1:8088`，可調整 `vite.config.ts`。API 使用 Alembic 管理 schema；後續修改模型時新增 migration，避免直接對正式資料執行 `create_all`。

## 下一步

1. 加入真實執行器 adapter（例如 vLLM HTTP API、生圖 worker 或語音程序），並提供取消與健康檢查。
2. 在模型與部署表加入 GPU UUID、所需顯存、常駐狀態及資源預留。
3. 加入應用 API key、逐節點認證、配額、公平排程及稽核紀錄。
4. 將 GUI 更新改為可恢復的事件訂閱，並加入可量測的任務階段。

GitHub repository：https://github.com/yankesswang/GPU-Control-Center

## MCP 與排程說明

五個 MCP 工具與設定方式見 [MCP 接入文件](docs/mcp.md)。使用 `make test-mcp` 驗證真實 MCP 協定與 demo-agent。GUI 的任務詳情會顯示排程說明；HTTP 可用 `GET /api/jobs/{id}/explain` 查詢工作槽持有者、主機維護／離線及能力限制。
