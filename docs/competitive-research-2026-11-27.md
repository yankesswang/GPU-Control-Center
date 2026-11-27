# 中央 GPU 協調平台：競品與利基點調查

調查日期：2026-11-27（Asia/Taipei）。

使用者已確認的優先情境：**多個應用共用自己的 GPU，避免互搶資源**。

## 結論

原構想已有高度重疊的開源實作。中央控制、多主機、Agent、顯存協調、優先級、模型卸載與 dashboard 都不能單獨作為差異化宣稱。最直接的參考對象是 argus-iris、Berth、gpuq 與 gpu-broker；GPUStack、Xinference、LocalAI 則覆蓋較完整的模型服務平台。

值得驗證的定位：**以應用／專案為單位的 GPU 使用政策，加上能重播與比較政策的資源診斷工具**。這是產品假設，尚非已證明的市場空缺。

本次僅查閱官方 repository、README、設計文件與文件網站，沒有安裝或實測競品。main 分支、開發文件與已發布版本可能有差異；README 的宣稱也不等於經過獨立效能驗證。以下「可做」均指官方文件列示的能力。

## 最直接的專案

| Repo | 已列示的能力 | 與原構想的關係 |
| --- | --- | --- |
| [smk762/argus-iris](https://github.com/smk762/argus-iris) | 多節點 Agent、GPU 租約、顯存記帳、優先級與 aging、跨節點選擇、idle model eviction、dashboard、observe/enforce | 最接近原構想。跨主機與 UI 也已重疊 |
| [T-Crypt/berth](https://github.com/T-Crypt/berth) | GPU 存取租約、在工作邊界交接、確認驅動層記憶體釋放、等待原因、UI、部分引擎 park in RAM | 單機共享體驗已深入；所查 roadmap 的 fleet 與 fleet router 仍列 planned，需依版本複查 |
| [elektricM/gpuq](https://github.com/elektricM/gpuq) | Linux 多 GPU、優先級、顯存記帳、協作式 yield、HTTP gate、Ollama/vLLM/ComfyUI 等整合 | 「避免既有服務搶 GPU」已有專案，不能把排隊或自動卸載當作首創 |
| [emergenthq-net/gpu-broker](https://github.com/emergenthq-net/gpu-broker) | 單卡聊天／圖片／影片輪流使用、請求排隊、模型駐留切換、dashboard、既有服務偵測與設定 | 單卡多模型輪替的直接對照組 |

### 關鍵一手文件

- [argus-iris DESIGN.md](https://github.com/smk762/argus-iris/blob/main/DESIGN.md)：明確區分 built 與 planned。節點、accounting、dashboard、lease admission 等列為 built；SQLite 歷史、learned p95 footprints 與 checkpoint/LoRA affinity 列 planned。這些未完成項目也可能很快被補上，不宜依賴單一 roadmap 缺口定位。
- [Berth roadmap](https://github.com/T-Crypt/berth/blob/main/docs/roadmap.md)：所查版本的 fleet、跨節點 router 列 planned；本地 UI、等待說明、歷史紀錄與交接能力已有實作。
- [gpuq how-it-works](https://github.com/elektricM/gpuq/blob/main/docs/how-it-works.md)：每卡記帳、多卡 all-or-nothing 取得、priority classes 與 cooperative yield。
- [gpuq integrations](https://github.com/elektricM/gpuq/blob/main/docs/integrations.md)：原服務前的 HTTP gate、systemd/Docker/Podman 管理與 native lease API。
- [Comfy 官方 MCP 的 VRAM coordination](https://github.com/Comfy-Org/comfy-mcp#using-with-local-llms-vram-coordination)：明列本地 LLM 和 ComfyUI 競爭顯存的情境，協調需由 client 處理。可視為整合痛點的技術證據，不能由此推定付費需求或市場規模。

## 平台與鄰近工具

| Repo | 官方定位／能力 | 對此 side project 的意義 |
| --- | --- | --- |
| [GPUStack](https://github.com/gpustack/gpustack) | GPU cluster management、可插拔引擎、排程、監控、模型服務 | 「自架 GPU 推論管理平台」正面重疊；README 的部分拓樸展示明標 Enterprise，不能全部視為社群版功能 |
| [Xinference](https://github.com/xorbitsai/inference) | 多模態模型、多引擎、workers／replicas、Web UI、相容 API | 支援更多模型與更漂亮的模型列表難形成持續差異 |
| [LocalAI](https://github.com/mudler/LocalAI) | 多模態 API、異質硬體、distributed mode、節點管理與模型 placement | 「不限 LLM」與多機也已有平台，開發文件需對照發布版本 |
| [dstack](https://github.com/dstackai/dstack) | 跨雲、Kubernetes、VM 與 bare metal 的 GPU 工作負載管理 | 通用基礎設施與部署編排的參考，不適合作為一人專案第一版的功能清單 |
| [LiteLLM](https://github.com/BerriAI/litellm) | API gateway、負載平衡、fallback、配額／用量等 | API gateway 不等於實體 GPU 記憶體協調；可互補，但「有佇列或優先級」也非獨特功能 |
| [llama-swap](https://github.com/mostlygeek/llama-swap) | 按需載入／停止多種本地服務、模型互斥／共存設定、TTL、UI、ComfyUI 整合 | 自動切模型、支援多模態與 GUI 都已有，適合整合或作對照 |
| [SwarmUI](https://github.com/mcmonkeyprojects/SwarmUI) | 生圖介面、跨 GPU／跨主機 backend 與 generation 排隊 | 若轉做創作工作分配，這也是直接參考，不是空白市場 |
| [Llama RPC Farm](https://github.com/dakotacookson/Llama-RPC-Farm) | 家用多機 llama.cpp RPC、llama-swap、多模型與 dashboard | 「家裡幾台主機接起來」已有類似 UX；其 README 指出 placement 為顯式設定，非自動裝置發現排程器 |

## 建議的目標使用者

有 1–3 台 GPU 主機、同時維護幾個獨立應用的開發者。例如聊天 bot、ComfyUI 生圖服務、語音轉錄與夜間批次工作。希望保留既有服務及模型目錄，又需要清楚的使用規則。

核心問題由「哪個模型部署在哪」收斂為：

1. 哪個應用正在占 GPU？占的是模型常駐、有效計算，還是閒置快取？
2. 哪個應用可以等、可以取消、可以在安全邊界讓出 GPU？
3. 某個應用連續送工作時，其他專案還能拿到多少機會？
4. 改了政策後，等待、吞吐和模型切換次數是否真的改善？

## 可驗證的差異化假設

### 假設 A：專案層政策比只看模型／程序更符合需求

用 app_id/project_id 追蹤來源，設定各應用的併發、等待期限、工作時間窗、資源權重與常駐要求。排程器同時看應用需求和實際引擎能力。

既有工具已有優先級與防飢餓機制，因此不能只換名稱。必須證明在多專案混合負載中，相比現有政策更容易設定，或能在相近吞吐下改善每個專案的尾端等待。

### 假設 B：排程重播與政策比較有獨立價值

保存請求抵達、來源、服務時間、模型載入與卸載、資源預估／實測、排程決策。可用同一份紀錄比較 FIFO、優先級、公平分配與偏好常駐模型的政策，並顯示哪些請求改善或變慢。

這不是單純的 dashboard：輸出可以是一份可重現的政策比較報告。重播結果是模型估計，真實引擎的 batching、快取、取消與切換成本仍需真機校正。

### 假設 C：接入成本足夠低，能成為使用理由

先支援現成 Ollama 和 ComfyUI，使用 API adapter／既有程序管理方式，不要求使用者搬模型或重裝整個服務。先觀察，再讓使用者指定哪些服務可被管理。

這是採用門檻的競爭，非獨家技術。gpuq、Berth、gpu-broker 和 argus-iris 都已有相關方向，必須實測接入步驟與限制，不能只宣稱「更簡單」。

## 最小驗證範圍

- 一台主機、一張 GPU、兩個 runtime（Ollama、ComfyUI），三個不同來源應用。
- 一組聊天請求、一組短生圖工作、一組持續背景工作。
- 持久化來源、工作狀態、取消意圖、排程決策及引擎釋放確認。
- 在同一實體 GPU 上比較：無協調、gpuq 或 Berth，以及自己的策略。
- 第一階段先量 OOM／失敗次數、每應用等待時間 p50/p95、總完成量、模型切換數與切換耗時。
- 第二階段才加第二台主機，驗證跨節點選擇與資料傳輸成本；不用一開始做 tensor parallel。

不可把「互動優先」等同於「長工作可隨時無損中斷」。沒有 checkpoint／resume 的工作只能等安全邊界、取消重跑，或分配至其他 GPU。未經協調的外部程序也可能占用顯存，因此軟體記帳無法無條件保證永不 OOM。

## 決策門檻

先試用直接競品，再決定是否自行實作：

- 如果主要只是手動卸載模型，現成工具可能已足夠；可改做 adapter、插件或 upstream contribution。
- 如果接入後仍無法管理跨專案政策，或沒辦法判斷哪套策略適合自己的流量，再投入應用政策與重播工具。
- 如果目的是作品集，重點可放在真實 trace、可重現比較、故障恢復、可用 adapter 與操作體驗；不需要宣稱自己發明新型 GPU 排程。
- 只有經過訪談或實際試用者回饋，才能將技術缺口進一步視為使用者願意採用的產品利基。

## 對目前 repo 的影響

保留前後端、PostgreSQL、Agent、租約與任務骨架。先暫停增加模型種類與通用部署能力；下一階段應先收集來源應用與執行事件，建立可重現測試工作負載。真正需要重做的不是 UI，而是證明排程策略與接入流程的價值。

開發在使用者要求先調查時暫停：原先 9 項整合測試已通過；其後新增的取消意圖修正與第 10 項測試尚未完成重新部署與驗證。目前 Compose 服務已停止，PostgreSQL volume 保留。此文件不代表平台已完成新版本驗收。
