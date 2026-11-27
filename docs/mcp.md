# MCP 接入

GPU Control Center 的 MCP server 是中央 HTTP API 的 adapter。排程、任務與取消意圖仍保存在同一套 PostgreSQL 中。

執行 `./scripts/start.sh` 後，Streamable HTTP endpoint 預設是：

```text
http://localhost:8090/mcp
```

連接埠可在 `.env` 的 `MCP_PORT` 調整。此版本只對 localhost 開放，沒有 MCP OAuth；不要直接公開到網際網路。

## 工具

| 工具 | 功能 |
| --- | --- |
| `list_resources` | 查看主機、能力、平台概況 |
| `submit_job` | 使用已發現模型建立真實推論，立即回傳 id |
| `get_job` | 查進度、事件及結果 |
| `explain_wait` | 查主機阻擋原因、持有工作槽的應用、目前排序位置 |
| `cancel_job` | 請求取消；執行中的工作等待 Agent 確認生成結束與卸載 |

`list_resources` 是 MCP tool 名稱，並非 MCP resources primitive。目前僅提供 tools。

`submit_job` 必須提供 `model`（`list_resources` 中的完整 ID），真實 Ollama 模型另需 `prompt`，可指定 `max_tokens`。主機 Agent 負責真實推論，ComfyUI 執行器尚未接入。

## Codex

在已安裝 Codex CLI 的終端機執行：

```bash
codex mcp add gpu-control-center --url http://localhost:8090/mcp
```

這是手動設定說明，啟動腳本不會修改使用者的 Codex 設定。

## 一般支援 HTTP 的 MCP client

依 client 的設定格式加入 server URL。例如支援 `url` 欄位的 client 可使用：

```json
{
  "mcpServers": {
    "gpu-control-center": {
      "url": "http://localhost:8090/mcp"
    }
  }
}
```

不同 client 的格式可能不同，請依其官方文件調整。

## stdio

需要 stdio 的 client，可從 repo 目錄使用既有 MCP 容器執行另一個 stdio server：

```bash
docker compose exec -T mcp python -m app.mcp_server --transport stdio
```

client 的 command 設為 `docker`，args 使用 `compose -f /home/trx50/Project/GPU-Control-Center/compose.yaml exec -T mcp python -m app.mcp_server --transport stdio`；需先啟動 Compose 服務。

## 驗證

```bash
make test-mcp
```

會透過官方 Python SDK 執行 initialize、列出五個 tools、建立任務、查等待原因、等待原生 Agent 與 Ollama 完成、取消任務及錯誤 ID 驗證。會在此開發平台留下明確標示 `mcp-smoke` 的工作紀錄。

等待原因是即時快照，不是鎖定資源的承諾。全域排序不等於每台主機的固定開工順序，因為不同能力的主機可跳過不相容工作。沒有可量測的執行時間模型時，不回傳虛構 ETA。

真實呼叫範例：`submit_job(name="摘要", model="ollama:qwen2.5:0.5b", prompt="請用繁體中文說你好。", max_tokens=64)`。先用 `list_resources` 確認該 ID 已安裝。生成結果在 `get_job` 的 `runtime_state.text` 與最終 `result.text`。取消執行中工作會等待生成邊界，不會僅斷開串流就假設 GPU 已停止。
