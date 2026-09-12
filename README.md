# Conclave

> **每個人把真實想法私下交給系統，AI 整合出一份大家都能接受的共識——過程中沒有人需要向彼此攤開底牌。**

Conclave 是一個 Privacy-Preserving Multi-Agent Coordination 的 PoC 網頁應用。
使用者建立群組產生 5 碼 PIN，其他人憑 PIN 加入；成員私下輸入對某個問題的真實想法，
後端收齊所有人的回覆後，整合丟給一個 LLM，產出**單一共識摘要**，再即時推播到所有人的畫面。
過程中成員之間**看不到彼此的原始想法**——只看得到彙整後的共識。

完整規格書見 [`docs/SPEC.md`](docs/SPEC.md)；延伸規格：[`docs/MULTIROUND_SPEC.md`](docs/MULTIROUND_SPEC.md)（多輪審議）、
[`docs/MCP_SPEC.md`](docs/MCP_SPEC.md)（外部研究階段）、[`docs/ADAPTIVE_SPEC.md`](docs/ADAPTIVE_SPEC.md)
（**適應性個人化提問**——建立群組時勾選後，每輪分析會為每位成員依其立場生成個人化的下輪問題，
探詢彈性界線以加速收斂；生成採結構性隔離，個人化問題僅收件人本人可見）。

ReasoningBank-Lite 開發規格書見 [`reasoningbank/reasoningbank_lite_development_spec.md`](reasoningbank/reasoningbank_lite_development_spec.md)。

---

## 目錄

- [架構](#架構)
- [前置需求](#前置需求)
- [快速開始（Docker Compose）](#快速開始docker-compose)
- [環境變數](#環境變數)
- [使用流程](#使用流程)
- [本地開發（不用 Docker）](#本地開發不用-docker)
- [測試](#測試)
- [API 參考](#api-參考)
- [專案結構](#專案結構)
- [技術棧](#技術棧)
- [已知限制](#已知限制)

---

## 架構

```
瀏覽器 (React + Vite)  ──HTTP/SSE──▶  後端 (FastAPI)  ──SQL──▶  資料庫 (Postgres)
                                        │
                                        ├──OpenAI SDK──▶  LLM (OpenAI API)
                                        │
                                        ├──HTTP──▶  ReasoningBank-Lite (記憶 sidecar)
                                        │
                                        └──MCP──▶  外部工具 (Google Maps, Realping, ...)
```

四個 Docker 容器：

| 容器 | 角色 | 埠 |
|---|---|---|
| `db` | Postgres 16-alpine，儲存群組/成員/回覆/輪次資料 | 5432 |
| `reasoningbank` | ReasoningBank-Lite 記憶 sidecar（FastAPI + SQLite/NumPy），提供 `before_task`/`after_task` HTTP API | 8000（內部） |
| `backend` | FastAPI + uvicorn（單 worker），REST API + SSE 廣播 + LLM 整合 + MCP 研究 + ReasoningBank hooks | 8000 |
| `frontend` | Vite dev server，代理 `/api` 與 `/events` 到 backend | 5173 |

後端以 in-process 註冊表實作 SSE 廣播（單 uvicorn worker），LLM 呼叫採 fire-and-forget 背景任務。
群組狀態機為 `collecting → analyzing → done`（失敗分支 `analyzing → error`，建立者可重試）。

ReasoningBank-Lite 在每輪分析前檢索相關記憶注入共識 prompt，分析後反思產生可泛化的 reasoning memory。
全程 fail-open：sidecar 故障不影響審議流程。

---

## 前置需求

| 工具 | 最低版本 | 用途 |
|---|---|---|
| Docker Engine | 24+ | 容器執行 |
| Docker Compose | v2+ | 多容器編排 |
| curl 或瀏覽器 | — | 測試 API / 使用介面 |

> 後端依賴 OpenAI API（`gpt-5.6-luna`）。需提供有效的 OpenAI API key。

---

## 快速開始（Docker Compose）

### 1. Clone 專案

```bash
git clone <repo-url> conclave
cd conclave
```

### 2. 設定環境變數

建立 `.env` 檔（此檔已被 gitignore，不會被 commit）：

```bash
cp .env.example .env
# 編輯 .env，至少填入：
#   LLM_API_KEY=<你的 OpenAI API key>
#   GOOGLE_MAPS_API_KEY=<Google Maps API key>（MCP maps 用，選填）
```

### 3. 啟動所有服務

```bash
docker compose up --build
```

首次建置約 2–3 分鐘（拉取 image + pip install + npm install）。
建置完成後四個容器同時在背景執行。

> 加 `-d` 可背景執行：`docker compose up --build -d`

### 4. 驗證服務

```bash
# 後端健康檢查
curl localhost:8000/api/health
# 預期: {"ok":true}

# ReasoningBank 健康檢查
docker exec reasoningbank python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/v1/health').read())"
# 預期: {"status":"ok"}

# 前端頁面
# 用瀏覽器打開 http://localhost:5173
```

### 5. 使用

1. 打開瀏覽器到 `http://localhost:5173`
2. 按「建立群組」，填寫討論問題、你的暱稱、（選填）預期回覆數或倒數秒數
3. 記下產生的 5 碼 PIN
4. 在其他瀏覽器/分頁打開 `http://localhost:5173`，輸入 PIN 與暱稱加入
5. 每人在輸入框寫下想法並發送（建立者也要送）
6. 觸發條件達成後，後端呼叫 LLM（約 10–30 秒）
7. 全體畫面顯示共識摘要

### 6. 停止

```bash
# 前景執行時按 Ctrl+C，或：
docker compose down

# 資料保留在 pgdata + reasoningbank_data volume（下次啟動仍在）。
# 若要完全清除資料：
docker compose down -v
```

---

## 環境變數

以下變數在 `.env` 檔中設定，由 `compose.yml` 注入各容器。參考 `.env.example` 取得完整範本。

### LLM（必填）

| 變數 | 預設值 | 說明 |
|---|---|---|
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI 相容端點 |
| `LLM_API_KEY` | — | OpenAI API key（**必填**） |
| `LLM_MODEL` | `gpt-5.6-luna` | 模型名稱 |
| `LLM_MAX_TOKENS` | `8192` | 推理模型 token 上限（使用 `max_completion_tokens`） |
| `LLM_TEMPERATURE` | `1` | 取樣溫度（gpt-5.6-luna 只接受 `1`） |
| `LLM_TIMEOUT` | `60` | 單次 LLM 呼叫最長秒數（connect 5s） |

### MCP 外部資訊接入（選用）

分析觸發時可讓 LLM 先透過 MCP servers 查外部資料（Google Maps 餐廳、Realping 房價等），再整合進共識摘要。
預設關閉（`MCP_ENABLED=false`），開啟前詳見 [`docs/MCP_SPEC.md`](docs/MCP_SPEC.md)。

| 變數 | 預設 | 說明 |
|---|---|---|
| `MCP_ENABLED` | `false` | 總開關。`true` 時需填 `MCP_PROVIDERS` 與 `MCP_RESEARCH_MODEL` |
| `MCP_PROVIDERS` | (空) | CSV 子集：`maps`, `apify_threads_post`, `realping` |
| `MCP_RESEARCH_MODEL` | (空) | 研究階段 planner 模型（需支援 tool calling）；**`MCP_ENABLED=true` 時必填** |
| `MCP_REQUIRED` | `false` | `true` 時研究降級視同分析失敗 |
| `MCP_MAX_TOOL_CALLS` | `3` | planner 單次最多規劃的工具呼叫數 |
| `MCP_RUN_TIMEOUT_S` | `180` | 研究階段總超時（秒） |
| `MCP_ALLOW_OPINION_CONTEXT` | `false` | ⚠ 把成員想法注入研究（會外洩給第三方）；預設關 |

#### MCP Provider 憑證

| 變數 | 用途 | 何时需要 |
|---|---|---|
| `GOOGLE_MAPS_API_KEY` | Google Maps 搜尋 | `MCP_PROVIDERS` 包含 `maps` 時 |
| `REALPING_API_KEY` | 台灣實價登錄查詢 | `MCP_PROVIDERS` 包含 `realping` 時 |
| `APIFY_TOKEN` | Threads 貼文搜尋 | `MCP_PROVIDERS` 包含 `apify_threads_post` 時 |
| `OPENAI_API_KEY` | OpenAI 原生 web_search 工具 | 使用 `test_mcp.py` CLI 或 `openaiTools` 時（與 `LLM_API_KEY` 分開設定） |

> **隱私提醒**：預設研究模型只看得到討論問題，看不到成員想法——想法不會外流給
> 第三方服務。開啟 `MCP_ALLOW_OPINION_CONTEXT=true` 會打破此保證。

### ReasoningBank-Lite 記憶（選用）

每輪分析前檢索相關記憶注入共識 prompt，分析後反思並儲存可泛化的 reasoning memory。
預設開啟（`RBANK_ENABLED=true`），fail-open：sidecar 故障不影響審議。

| 變數 | 預設 | 說明 |
|---|---|---|
| `RBANK_ENABLED` | `true` | 總開關。`false` 時 backend 不呼叫記憶 hooks |
| `RBANK_BASE_URL` | `http://reasoningbank:8000` | sidecar HTTP 位址（compose 內部 DNS） |
| `RB_EMBEDDING_MODEL` | `text-embedding-3-large` | 記憶 embedding 模型（OpenAI 相容） |
| `RB_RETRIEVAL_TOP_K` | `3` | 每次檢索的記憶數 |
| `RB_SIMILARITY_THRESHOLD` | `0.30` | 檢索相似度門檻（已為 text-embedding-3-large 校正） |
| `RB_DEDUP_THRESHOLD` | `0.85` | 去重相似度門檻（已校正） |
| `RB_LOG_LEVEL` | `INFO` | sidecar 日誌等級 |

> ReasoningBank-Lite 的 embedding 與 LLM 呼叫共用 `LLM_API_KEY` 與 `LLM_BASE_URL`，
> 不需額外的 API key。完整開發規格見 [`reasoningbank/`](reasoningbank/) 目錄。

### 適應性個人化提問（選用）

| 變數 | 預設 | 說明 |
|---|---|---|
| `ADAPTIVE_QUESTIONS_ENABLED` | `true` | 全域開關。建立群組時可勾選啟用 |
| `ADAPTIVE_P_MAX_MEMBERS` | `20` | 超過此人數的群組跳過 P 階段 |

### 其他

| 變數 | 預設 | 說明 |
|---|---|---|
| `DATABASE_URL` | `postgresql://conclave:conclave@db:5432/conclave` | Postgres 連線字串（compose 設定，本地開發時需自行提供） |
| `SSE_KEEPALIVE_S` | `15` | SSE keepalive 間隔 |
| `DEADLINE_SCAN_S` | `5` | 截止時間掃描間隔 |

---

## 使用流程

### 三條件觸發（OR 邏輯）

建立群組時可設定「預期回覆數」與「倒數秒數」（皆選填）。三個觸發來源任一達標即開始分析：

| 設定情況 | 觸發時機 |
|---|---|
| 建立者按「開始分析」 | 立即觸發（忽略人數與時間） |
| 只設預期回覆數 | 收到第 N 份回覆時觸發 |
| 只設倒數秒數 | 時間到時觸發 |
| 兩者都設 | 任一達標即觸發 |
| 兩者都未設 | 僅等建立者手動按「開始分析」 |

> 零回覆保護：若 `submitted_count == 0`，不會觸發分析。
> 觸發後才送到的回覆被忽略，不納入本次分析。

### 群組狀態

| 狀態 | 說明 |
|---|---|
| `collecting` | 收集中，可加入、可送出想法、可看進度 |
| `analyzing` | 分析中，不可送出；晚到者可加入但只看到「分析中」 |
| `done` | 完成，顯示共識摘要；晚到者加入直接看到摘要 |
| `error` | 分析失敗；建立者可按「重試分析」重新跑一次 |

---

## 本地開發（不用 Docker）

若要直接在本機跑後端（便於除錯），需先具備：

- Python 3.12+（建議用 conda 隔離環境）
- Node.js 18+（建議用 nvm）
- 一個活的 Postgres（可用 `docker run -d -p 5432:5432 -e POSTGRES_USER=conclave -e POSTGRES_PASSWORD=conclave -e POSTGRES_DB=conclave postgres:16-alpine`）

### 後端

```bash
conda create -n conclave python=3.13 -y
conda activate conclave
cd backend
pip install -r requirements.txt

export DATABASE_URL="postgresql://conclave:conclave@localhost:5432/conclave"
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_API_KEY="<your-openai-api-key>"
export LLM_MODEL="gpt-5.6-luna"

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev   # 打開 http://localhost:5173
```

> 本地開發時，Vite dev server 的 `vite.config.ts` 把 `/api` 與 `/events`
> 代理到 `http://backend:8000`。若後端跑在本機而非 Docker，改為
> `http://localhost:8000`。

> ReasoningBank-Lite 需單獨啟動（`cd reasoningbank && uvicorn reasoning_bank.api:app --port 8000`），
> 並設定 `RBANK_BASE_URL=http://localhost:8000`。

---

## 測試

### 後端測試

```bash
cd backend
export DATABASE_URL="postgresql://conclave:conclave@localhost:5432/conclave"
python -m pytest tests/ -v
```

> 需要活的 Postgres。整合測試標記為 `@pytest.mark.integration`，
> 純單元測試（PIN、broadcast）不需 DB：
> `python -m pytest tests/test_pin.py tests/test_broadcast.py -v`

### 測試清單

| 測試檔 | 覆蓋 |
|---|---|
| `test_pin.py` | PIN 格式、字元集、空間大小、密碼學隨機 |
| `test_broadcast.py` | SSE 訂閱/推播/移除、queue 滿丟連線 |
| `test_trigger.py` | §12 觸發真值表（6 參數化 + single-shot + zero-reply + error 重試） |
| `test_llm.py` | Prompt 匿名化、模型探測 mock |
| `test_groups_api.py` | 建立群組、加入、狀態查詢、暱稱重複 409、長問題 422 |
| `test_events_sse.py` | SSE endpoint（跳過，由 E2E 覆蓋） |
| `test_integration.py` | 完整 loop：建立→加入→送出→觸發→mock LLM→共識；手動觸發；零回覆 409 |
| `test_mcp_failure.py` | MCP 研究降級（provider 全掛、研究失敗、MCP_REQUIRED 促升錯誤） |
| `test_mcp_loop.py` | MCP 研究 loop（planner 規劃、工具執行、brief 注入） |

---

## API 參考

Base path: `/api`。所有回應為 JSON。錯誤回 `{"error": "..."}`。

### REST

| 方法 | 路徑 | 說明 | 請求 / 回應 |
|---|---|---|---|
| `POST` | `/api/groups` | 建立群組 | req: `{question, creator_nickname, expected_count?, timeout_seconds?, max_rounds?, adaptive_questions?}` → `201 {pin, participant_id, creator_token, status}` |
| `POST` | `/api/groups/{pin}/join` | 加入群組 | req: `{nickname}` → `200 {participant_id, status}` |
| `GET` | `/api/groups/{pin}/state` | 真相來源 | `200 {pin, question, status, expected_count, participant_count, submitted_count, deadline, consensus, is_creator, current_round, max_rounds, adaptive_questions}` |
| `POST` | `/api/groups/{pin}/responses` | 送出想法 | req: `{participant_id, content}` → `200 {ok}` |
| `POST` | `/api/groups/{pin}/start` | 建立者手動觸發 | req: `{creator_token}` → `202 {ok}` |
| `POST` | `/api/groups/{pin}/rounds/next` | 開啟下一輪 | req: `{creator_token}` → `202` |
| `POST` | `/api/groups/{pin}/rounds/close` | 結束討論 | req: `{creator_token}` → `202` |
| `GET` | `/api/groups/{pin}/rounds` | 列出各輪共識 | `200 [{round_number, question, consensus, stance_shift_summary, created_at, analyzed_at}]` |
| `GET` | `/api/groups/{pin}/my-question` | 個人化問題 | `200 {questions}` / `404`（適應性提問用） |

### SSE

| 路徑 | 說明 |
|---|---|
| `GET /api/events/{pin}` | Server-Sent Events 串流 |

事件類型：

| 事件 | data | 觸發時機 |
|---|---|---|
| `phase` | `{status}` | 狀態轉換 |
| `progress` | `{participant_count, submitted_count}` | 有人加入或送出 |
| `consensus` | `{content, round}` | LLM 分析完成 |
| `research` | `{status, providers}` | MCP 研究階段開始/完成 |
| `error` | `{message}` | 分析失敗 |

---

## 專案結構

```
conclave/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, lifespan, 路由掛載
│   │   ├── config.py            # pydantic-settings 環境變數
│   │   ├── db.py                # asyncpg 連線池 + migration + 狀態 helpers
│   │   ├── models.py            # pydantic request/response schema
│   │   ├── pin.py               # PIN 產生
│   │   ├── broadcast.py         # in-process SSE 廣播註冊表
│   │   ├── llm.py               # OpenAI SDK + 模型探測 + prompt + run_analysis + ReasoningBank hooks
│   │   ├── mcp.py               # MCP 研究階段（planner + tool execution）
│   │   ├── mcp_tools.py         # MCP 工具定義（maps, realping, web_search, apify）
│   │   ├── reasoning_bank.py    # ReasoningBank-Lite HTTP client shim（fail-open）
│   │   ├── tasks.py             # deadline 掃描背景任務
│   │   └── routes/
│   │       ├── groups.py        # POST /groups, /join, GET /state, POST /start, /rounds/*
│   │       ├── responses.py     # POST /responses
│   │       └── events.py        # GET /events/{pin} SSE
│   ├── migrations/001_init.sql
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
├── reasoningbank/
│   ├── reasoning_bank/          # ReasoningBank-Lite 服務端
│   │   ├── api.py               # FastAPI: /v1/before_task, /v1/after_task, /v1/health
│   │   ├── bank.py              # orchestration
│   │   ├── reflector.py         # 合併 Judge + Extractor LLM 呼叫
│   │   ├── retriever.py         # embedding + cosine search
│   │   ├── embedder.py          # OpenAI-compatible embedding API client
│   │   ├── store.py             # SQLite + NumPy vector store
│   │   ├── formatter.py        # memory → context 字串
│   │   ├── models.py           # pydantic schemas
│   │   └── config.py           # 環境變數設定
│   ├── Dockerfile
│   ├── requirements.txt
│   └── reasoningbank_lite_development_spec.md
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # 路由
│   │   ├── pages/               # Home, Create, Group
│   │   ├── hooks/useGroupSSE.ts
│   │   ├── lib/api.ts           # REST client
│   │   └── styles/index.css
│   ├── vite.config.ts           # proxy /api, /events
│   └── Dockerfile
├── docs/                        # 規格書
├── mcp_config.yaml              # MCP 工具設定
├── compose.yml                  # 四服務（db, reasoningbank, backend, frontend）
├── .env.example                 # 環境變數範本
└── README.md
```

---

## 技術棧

| 層 | 技術 | 版本 |
|---|---|---|
| 後端 | FastAPI + uvicorn | 0.141 / 0.52 |
| SSE | sse-starlette | 3.4 |
| ORM | asyncpg | 0.31 |
| Schema | pydantic + pydantic-settings | 2.13 / 2.15 |
| LLM | OpenAI Python SDK | 3.8 |
| 資料庫 | PostgreSQL | 16-alpine |
| 記憶 sidecar | FastAPI + SQLite + NumPy | — |
| 前端 | React + Vite + TypeScript | 18 / 5 / 5.5 |
| 路由 | react-router-dom | 6 |
| Markdown | react-markdown | 9 |
| 測試 | pytest + pytest-asyncio | 8.3 / 0.24 |

---

## 已知限制

1. **非密碼學隱私**：後端可讀到所有原始想法。本版正確說法是「成員之間的 selective disclosure」，不是「連系統都看不到」。
2. **單 worker 廣播**：in-process SSE 註冊表只在不開多 worker 時有效；水平擴展需換 Redis pub/sub。
3. **推理模型延遲**：每次分析約 10–30 秒；`LLM_MAX_TOKENS` 太小會因 reasoning tokens 吃光額度而空輸出。
4. **SSE 無 replay**：斷線重連後需 `GET /state` 對齊真相。
5. **資料永久保留**：不做清理；刪除機制為 v2。
6. **無帳號**：`participant_id` 存 localStorage；換瀏覽器/清快取即失身分。
7. **ReasoningBank fail-open**：記憶 sidecar 故障時降級為空 context / 不儲存，不影響審議；但期間不會累積記憶。
8. **MCP provider 不穩定**：第三方 MCP server（如 realping）偶爾不回資料；研究階段 fail-open，降級為空 brief。

完整限制清單見 [`docs/SPEC.md` §16](docs/SPEC.md)。

---

## License

PoC 專案，未授權。
