# SPEC_INFRA — Conclave 基礎設施層實作規格書

> **定位**：本規格書對應「第三人」（MLOps / 基礎設施 / Docker / 部署專長），範圍是 Conclave 專案的**基礎設施層**——容器編排 + 配置系統 + 資料綱要 + 測試基礎設施 + 建置部署。
>
> **目標**：讓一位具備 MLOps / 基礎設施經驗的工程師，透過 agent 從零搭起與現在**完全相同**的執行環境，並能讓另外兩份規格書（AI 層、應用伺服器層）的實作直接跑起來。
>
> **與其他兩份規格書的關係**：
> - 本層是**地基**：被另外兩層依賴，幾乎不依賴另外兩層。
> - **AI 層規格書**（`SPEC_AI`）依賴本層的：`config.py` 的 `Settings` 類別（讀取 LLM/MCP 設定）、`migrations` 的資料表 schema（寫入 rounds / member_questions）、`.env.example` 的環境變數定義。
> - **應用伺服器層規格書**（`SPEC_APP`）依賴本層的：`config.py` 的 `Settings`、`migrations` 的完整 schema（讓 `db.py` 能正確讀寫）、`compose.yml` 的三服務編排（連 DB、暴露埠）、`conftest.py` 的測試基礎設施。
> - 唯一反向依賴：本層 `Settings` 的**欄位名**必須與 AI 層 / 應用層實際讀取的設定名一致（見 §0 與 §11）。
>
> **搭配閱讀**：另有 [`SPEC_UNIFIED.md`](SPEC_UNIFIED.md) 整體規格書，提供全域視角——端到端工作流、三層接合地圖、跨層不變量紅線、整合驗證順序。實作自己的部分時，拿本份（細節）+ 整體份（看自己如何嵌進全域、與另兩塊的交集在哪）一起開發，避免走偏。三人實作完成後的整合，見 [`SPEC_INTEGRATION.md`](SPEC_INTEGRATION.md)。

---

## 0. 整合接縫（最重要，放最前面）

本節定義本層提供給其他兩層的**精確介面**。實作時這些介面必須分毫不差，否則另外兩層無法運作。

### 0.1 本層提供：`config.py` 的 `Settings` 類別

`Settings(BaseSettings)` 是整個系統的單一設定來源。AI 層與應用層透過 `get_settings()` 取得唯一實例。**所有欄位名 / 型別 / 預設 / env var 名必須與下表完全一致**（pydantic-settings 以 `env_prefix=""` + `case_sensitive=False` 對應 env var，故 env var 名 = 欄位名不分大小寫）。

完整欄位表見 §4。此處僅列介面契約重點：

| 契約 | 說明 |
|------|------|
| `get_settings() -> Settings` | `@lru_cache` 包裹的 process 單例。AI 層 / 應用層所有設定讀取都走此函式，不可直接 `Settings()`。 |
| `model_config` | `{"env_prefix": "", "case_sensitive": False}`——無前綴，env var 名即欄位名（不分大小寫）。 |
| `_validate_mcp` | `@model_validator(mode="after")`；`mcp_enabled=true` 時必須 `mcp_research_model` 與 `mcp_providers` 非空，否則啟動即 `raise ValueError`。 |
| `database_url: str` | **唯一無預設的必填欄位**；缺少時 `Settings()` 建構即失敗。 |

### 0.2 本層提供：`migrations` 的完整 schema

四張表 + 所有欄位 + 約束 + 索引，由 `001_init.sql` → `002_rounds.sql` → `003_adaptive_questions.sql` 依序建立，全部冪等。應用層 `db.py` 的每個 `SELECT` 都明確列出欄位（絕不 `SELECT *`），故 schema 欄位順序與名稱必須穩定。完整 DDL 見 §5。

| 資料表 | 用途 | 隱私分級 |
|--------|------|----------|
| `groups` | 群組主檔（問題、狀態、輪次追蹤、adaptive 開關） | 公開 + consensus 公開 |
| `participants` | 成員（nickname、joined_at、member_seq） | member_seq 系統內部、永不暴露 |
| `responses` | 成員回覆（round_number 維度） | 內容公開給分析 |
| `rounds` | 逐輪歷史（consensus 公開 / stance_digest 私有 / stance_shift_summary 群體級 / research_brief 快取） | **stance_digest / research_brief 絕不經 API** |
| `member_questions` | 逐成員個人化問題（每輪每人一題） | **僅收件人可見**，永不進 SSE / GET /rounds / GET /state 內容 |

### 0.3 本層提供：`compose.yml` 三服務編排

| 服務 | 對外埠 | 對內角色 | 讓應用層能做到 |
|------|--------|----------|----------------|
| `db` | 5432 | postgres:16-alpine | 應用層 `DATABASE_URL` 連 `db:5432` |
| `backend` | 8000 | uvicorn --reload | 應用層 FastAPI 進程；對外 API + SSE |
| `frontend` | 5173 | vite dev server | 前端 dev server；proxy `/api` `/events` → backend:8000 |

### 0.4 本層提供：`.env.example` 環境變數模板

所有環境變數的權威清單。AI 層 / 應用層新增 env var 時，必須同時更新 `.env.example` 與 `compose.yml` 透傳表（或確認靠 `Settings` 預設）。完整模板見 §6。

### 0.5 本層依賴其他層什麼

**幾乎沒有**——infra 層是地基，被另外兩層依賴。唯一契約：

- `Settings` 欄位名必須與 AI 層 / 應用層實際讀取的屬性名一致（例如 AI 層讀 `get_settings().llm_model`、應用層讀 `get_settings().database_url`）。若其他層新增讀取，需回頭確認欄位存在。
- `mcp_config.yaml`（AI 層檔案，位於 project root）透過 `os.environ` 直接讀取 `LOCAL_OPENAI_BASE_URL` / `LOCAL_API_KEY` 等變數——這些**不在 `Settings`**，但本層的 `.env.example` 必須提供模板，且 `compose.yml` **未透傳**它們（靠 `mcp_tools.py` 自行 `load_dotenv` 或本地開發環境）。

---

## 1. 定位與檔案範圍

本規格書涵蓋以下檔案，實作時需逐一生成並保持內容與本規格一致：

| 檔案路徑 | 層級 | 本規格章節 |
|----------|------|-----------|
| `compose.yml` | repo root | §2 |
| `backend/Dockerfile` | backend | §3 |
| `frontend/Dockerfile` | frontend | §3 |
| `backend/app/config.py` | backend/app | §4 |
| `backend/migrations/001_init.sql` | backend/migrations | §5.1 |
| `backend/migrations/002_rounds.sql` | backend/migrations | §5.2 |
| `backend/migrations/003_adaptive_questions.sql` | backend/migrations | §5.3 |
| `.env.example` | repo root | §6 |
| `.gitignore` | repo root | §10 |
| `backend/pytest.ini` | backend | §7.1 |
| `backend/tests/conftest.py` | backend/tests | §7.2 |
| `backend/requirements.txt` | backend | §8.1 |
| `frontend/package.json` | frontend | §8.2 |
| `frontend/vite.config.ts` | frontend | §8.3 |

> **不在本層範圍**：`backend/app/db.py`（應用伺服器層）、`backend/app/main.py`（應用伺服器層）、`backend/app/llm.py` 與 `backend/app/mcp_tools.py` 與 `mcp_config.yaml`（AI 層）、前端 React 元件（應用層）。本層只負責讓這些檔案能跑起來的地基。

---

## 2. 容器編排（`compose.yml`）

三服務（`db` / `backend` / `frontend`）+ 一個 top-level volume（`pgdata`）。

### 2.1 服務完整表

| 服務 | image / build | container_name | ports | volumes | command | depends_on | healthcheck |
|------|---------------|---------------|-------|---------|---------|------------|-------------|
| `db` | `postgres:16-alpine` | `conclave-db` | `5432:5432` | `pgdata:/var/lib/postgresql/data` | （預設 postgres 入口） | 無 | `pg_isready -U conclave -d conclave`，5s/3s/10 retries |
| `backend` | `build: ./backend` | `conclave-backend` | `8000:8000` | `./backend:/app` + `./mcp_config.yaml:/mcp_config.yaml:ro` + `./.env:/.env:ro` | `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --no-access-log` | `db` (condition: `service_healthy`) | 無 |
| `frontend` | `build: ./frontend` | `conclave-frontend` | `5173:5173` | `./frontend:/app` + `/app/node_modules`（匿名 volume） | `npm run dev -- --host 0.0.0.0` | `[backend]`（無 condition，單純啟動順序） | 無 |

### 2.2 `db` 服務細節

```yaml
db:
  image: postgres:16-alpine
  container_name: conclave-db
  environment:
    POSTGRES_USER: conclave
    POSTGRES_PASSWORD: conclave
    POSTGRES_DB: conclave
  volumes: [pgdata:/var/lib/postgresql/data]
  ports: ["5432:5432"]
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U conclave -d conclave"]
    interval: 5s
    timeout: 3s
    retries: 10
```

- 環境變數 `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` 全部**寫死 `conclave`**（非 `${VAR}` 插值）。
- `healthcheck` 用 `pg_isready -U conclave -d conclave`；`interval: 5s` / `timeout: 3s` / `retries: 10`。backend 透過 `condition: service_healthy` 等待 DB 就緒後才啟動。

### 2.3 `backend` 服務細節

```yaml
backend:
  build: ./backend
  container_name: conclave-backend
  environment:
    DATABASE_URL: postgresql://conclave:conclave@db:5432/conclave
    # ... 透傳表見 §2.3.1
  volumes: ["./backend:/app", "./mcp_config.yaml:/mcp_config.yaml:ro", "./.env:/.env:ro"]
  ports: ["8000:8000"]
  command: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --no-access-log
  depends_on:
    db:
      condition: service_healthy
```

#### 2.3.1 backend 環境變數透傳表

`compose.yml` 用 `${VAR:-default}` 語法從 host `.env` 插值。下表列出 `backend.environment` 的**每一個**條目、來源語法與預設值：

| 環境變數 | compose 語法 | 預設 / 來源 | 說明 |
|----------|-------------|-------------|------|
| `DATABASE_URL` | 寫死字串 | `postgresql://conclave:conclave@db:5432/conclave` | 指向 db 服務；非 `${}` 插值 |
| `LLM_BASE_URL` | `${LLM_BASE_URL:-https://openrouter.ai/api/v1}` | OpenRouter | LLM API base URL |
| `LLM_API_KEY` | `${LLM_API_KEY}` | **無預設（必填）** | LLM API key |
| `LLM_MODEL` | `${LLM_MODEL:-google/gemma-4-26b-a4b-it:free}` | gemma free | 模型名 |
| `LLM_MAX_TOKENS` | `"8192"`（字面值） | 8192 | **寫死字串，非 `${}` 插值** |
| `LLM_TEMPERATURE` | `"0.3"`（字面值） | 0.3 | **寫死字串，非 `${}` 插值** |
| `ADAPTIVE_QUESTIONS_ENABLED` | `${ADAPTIVE_QUESTIONS_ENABLED:-true}` | true | adaptive 全域開關 |
| `ADAPTIVE_P_MAX_MEMBERS` | `${ADAPTIVE_P_MAX_MEMBERS:-20}` | 20 | P stage 人數上限 |
| `MCP_ENABLED` | `${MCP_ENABLED:-false}` | false | MCP 研究階段開關 |
| `MCP_PROVIDERS` | `${MCP_PROVIDERS:-}` | 空字串 | csv provider 子集 |
| `MCP_RESEARCH_MODEL` | `${MCP_RESEARCH_MODEL:-}` | 空字串 | planner 模型（啟用時必填） |
| `MCP_REQUIRED` | `${MCP_REQUIRED:-false}` | false | 研究降級視為失敗 |
| `MCP_MAX_ROUNDS` | `${MCP_MAX_ROUNDS:-3}` | 3 | v1.0 legacy（v1.1 未用） |
| `MCP_RESULT_CHARS` | `${MCP_RESULT_CHARS:-3000}` | 3000 | 單一工具結果字元上限 |
| `MCP_TOTAL_RESULT_CHARS` | `${MCP_TOTAL_RESULT_CHARS:-12000}` | 12000 | 全部結果字元上限 |
| `MCP_TOOL_TIMEOUT_S` | `${MCP_TOOL_TIMEOUT_S:-30}` | 30 | 單工具逾時 |
| `MCP_CONNECT_TIMEOUT_S` | `${MCP_CONNECT_TIMEOUT_S:-10}` | 10 | 連線逾時 |
| `MCP_RUN_TIMEOUT_S` | `${MCP_RUN_TIMEOUT_S:-180}` | 180 | 整體執行逾時 |
| `MCP_RESEARCH_MAX_TOKENS` | `${MCP_RESEARCH_MAX_TOKENS:-1024}` | 1024 | planner max tokens |
| `MCP_ALLOW_OPINION_CONTEXT` | `${MCP_ALLOW_OPINION_CONTEXT:-false}` | false | 私人意見外洩 opt-out |
| `MCP_LOG_VERBOSE` | `${MCP_LOG_VERBOSE:-false}` | false | 詳細日誌 |
| `OPENAI_API_KEY` | `${OPENAI_API_KEY:-}` | 空字串 | web_search（OpenAI-native） |
| `APIFY_TOKEN` | `${APIFY_TOKEN:-}` | 空字串 | apify_threads_post provider |
| `GOOGLE_MAPS_API_KEY` | `${GOOGLE_MAPS_API_KEY:-}` | 空字串 | maps provider |
| `REALPING_API_KEY` | `${REALPING_API_KEY:-}` | 空字串 | realping provider |

#### 2.3.2 **未透傳的變數（靠 `Settings` 預設）**

以下 `Settings` 欄位在 `compose.yml` 的 `backend.environment` **沒有對應條目**，故容器內 env 不含這些變數，`Settings` 會使用程式碼預設值：

| 變數 | `Settings` 預設 | 後果 |
|------|-----------------|------|
| `LLM_TIMEOUT` | `60.0` | LLM 呼叫逾時固定 60s |
| `SSE_KEEPALIVE_S` | `15` | SSE keepalive 固定 15s |
| `DEADLINE_SCAN_S` | `5` | deadline 掃描間隔固定 5s |
| `MCP_MAX_TOOL_CALLS` | `3` | planner 單次最多 3 個 tool call |

> 若要覆寫這些值，需在 `compose.yml` 的 `backend.environment` 補上對應條目（例如 `LLM_TIMEOUT: ${LLM_TIMEOUT:-60.0}`）。目前**刻意未透傳**，因為預設值在開發環境已足夠。

#### 2.3.3 `--no-access-log` 的隱私理由

`participant_id` 是 **bearer token**，以 query string 形式傳遞（例如 `/my-question?...&participant_id=...`，見 ADAPTIVE_SPEC §12.2）。uvicorn 預設 access log 會記錄完整 request line（含 query string），導致 bearer token 進 log。

- `command` 明確加 `--no-access-log` 關閉 access log。
- 註解警告：若未來重新啟用 access log，**必須**同時加上一個 strip query string 的 logging filter。

### 2.4 `frontend` 服務細節

```yaml
frontend:
  build: ./frontend
  container_name: conclave-frontend
  volumes: ["./frontend:/app", "/app/node_modules"]
  ports: ["5173:5173"]
  command: npm run dev -- --host 0.0.0.0
  depends_on: [backend]
```

- `volumes` 兩條：`./frontend:/app`（bind mount 原始碼，熱重載）+ `/app/node_modules`（**匿名 volume**，隔離 host 與 container 的 node_modules，避免 host 污染）。
- `depends_on: [backend]` **無 `condition`**——只是啟動順序，不等待 backend 健康（frontend 無 healthcheck）。
- `command: npm run dev -- --host 0.0.0.0` 傳 `--host` 給 vite，使其綁定 0.0.0.0（容器內可被 host 存取）。`package.json` 的 `dev` script 已含 `--host 0.0.0.0 --port 5173`，這裡的 `-- --host` 為額外保險。

### 2.5 top-level volumes

```yaml
volumes:
  pgdata:
```

- 只有一個 named volume `pgdata`，供 `db` 服務持久化 PostgreSQL 資料。
- `down`（不含 `-v`）保留 volume；`down -v` 才會刪除 volume（見 §9）。

### 2.6 重要陷阱

| 陷阱 | 說明 |
|------|------|
| **無 `env_file: .env`** | `compose.yml` 不用 `env_file` 指令。靠 `${VAR}` 插值——只有被 `compose.yml` `${VAR}` 引用的變數才會進容器。`.env` 裡未被引用的變數（如 `LOCAL_OPENAI_BASE_URL`）**不會**自動注入容器。 |
| **`LLM_TIMEOUT` / `SSE_KEEPALIVE_S` / `DEADLINE_SCAN_S` / `MCP_MAX_TOOL_CALLS` 未透傳** | 這四個 `Settings` 欄位在 compose `backend.environment` 無條目，靠 `Settings` 預設值（見 §2.3.2）。 |
| **`--no-access-log` 隱私** | participant_id bearer token 走 query string；access log 會洩漏。見 §2.3.3。 |
| **`LLM_MAX_TOKENS` / `LLM_TEMPERATURE` 寫死字串** | compose 用 `"8192"` / `"0.3"` 字面值（非 `${}` 插值），故 host `.env` 改這兩個值**不會**生效。要改需直接編 compose 或改為 `${}` 語法。 |
| **frontend `depends_on` 無 healthcheck** | frontend 不等 backend 健康，只等啟動順序。backend 尚未就緒時前端 proxy 可能短暫 502。 |
| **mcp_config.yaml 與 .env 必須掛入容器** | `mcp_tools.py` 的 `CONFIG_PATH = ROOT / "mcp_config.yaml"` 在容器內解析為 `/mcp_config.yaml`（`ROOT = Path(__file__).resolve().parents[2]` → `/`），且 `mcp_tools.py` 自行 `load_dotenv(ENV_PATH)` 讀 `.env` 的 `LOCAL_OPENAI_BASE_URL`/`LOCAL_API_KEY`。compose 的 backend volumes 必須額外掛 `./mcp_config.yaml:/mcp_config.yaml:ro` 與 `./.env:/.env:ro`，否則 MCP 研究啟用時 `load_config()` 會 `RuntimeError: Missing configuration file`。本規格的 compose 已包含此二掛載。 |

---

## 3. Dockerfiles

### 3.1 `backend/Dockerfile`

```dockerfile
#FROM python:3.13-slim
FROM python:3.14.6-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

| 要點 | 說明 |
|------|------|
| base image | `python:3.14.6-slim`。第一行 `#FROM python:3.13-slim` 是**舊註解**（被註解掉），保留作歷史記錄；實際用 3.14.6。 |
| `apt install git` | `yahoo-finance-mcp` 從 git 安裝（見 §8.1），需要 git。`--no-install-recommends` + `rm -rf /var/lib/apt/lists/*` 縮小 image。 |
| 分層 COPY | 先 `COPY requirements.txt` + `pip install`，再 `COPY . .`——利用 Docker layer cache，改程式碼不重裝依賴。 |
| `EXPOSE 8000` | 文件聲明埠；實際綁定由 `compose.yml` `command` 的 `--port 8000` 決定。 |
| `CMD` | `uvicorn app.main:app --reload`。**注意**：`compose.yml` 的 `command` 會**覆蓋**此 `CMD`，加上 `--no-access-log`。故容器實際執行的是 compose 的 command，此 `CMD` 主要供非 compose 直接 `docker run` 時使用。 |

### 3.2 `frontend/Dockerfile`

```dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
```

| 要點 | 說明 |
|------|------|
| base image | `node:20-alpine`。 |
| `package-lock.json*` | `*` 萬用字元——lockfile 不存在時不報錯（容許無 lockfile 的開發流程）。 |
| 分層 COPY | 先 COPY `package.json`（+ lockfile）+ `npm install`，再 `COPY . .`——layer cache。 |
| `EXPOSE 5173` | 聲明埠。 |
| `CMD` | `npm run dev`。同樣被 `compose.yml` `command`（`npm run dev -- --host 0.0.0.0`）覆蓋。 |

---

## 4. 配置系統（`backend/app/config.py`）

### 4.1 `Settings` 完整欄位表

`Settings(BaseSettings)`，`model_config = {"env_prefix": "", "case_sensitive": False}`。env var 名 = 欄位名（不分大小寫）。以下依功能分區列出**所有**欄位。

#### 4.1.1 核心 + LLM

| 欄位名 | 型別 | 預設 | env var 名 | 說明 |
|--------|------|------|-----------|------|
| `database_url` | `str` | **無（必填）** | `DATABASE_URL` | PostgreSQL DSN；缺少則建構失敗 |
| `llm_base_url` | `str` | `http://10.241.77.188:8000/v1` | `LLM_BASE_URL` | LLM API base URL（compose 預設 OpenRouter） |
| `llm_api_key` | `str` | `probe` | `LLM_API_KEY` | LLM API key |
| `llm_model` | `str` | `""` | `LLM_MODEL` | 模型名（compose 預設 gemma free） |
| `llm_max_tokens` | `int` | `8192` | `LLM_MAX_TOKENS` | 單次回應 max tokens |
| `llm_temperature` | `float` | `0.3` | `LLM_TEMPERATURE` | 取樣溫度 |
| `llm_timeout` | `float` | `60.0` | `LLM_TIMEOUT` | LLM 呼叫逾時（**compose 未透傳**，靠預設） |
| `sse_keepalive_s` | `int` | `15` | `SSE_KEEPALIVE_S` | SSE keepalive 間隔（**compose 未透傳**） |
| `deadline_scan_s` | `int` | `5` | `DEADLINE_SCAN_S` | deadline 掃描間隔（**compose 未透傳**） |

#### 4.1.2 Adaptive 逐成員提問（ADAPTIVE_SPEC §12.1）

| 欄位名 | 型別 | 預設 | env var 名 | 說明 |
|--------|------|------|-----------|------|
| `adaptive_questions_enabled` | `bool` | `True` | `ADAPTIVE_QUESTIONS_ENABLED` | 全域熱開關；false → P stage 跳過、`/my-question` 404、`/state` 回報 `adaptive_questions=false` |
| `adaptive_p_max_members` | `int` | `20` | `ADAPTIVE_P_MAX_MEMBERS` | P stage 人數閘門；成員數超過則跳過 P stage（§4.2 限制呼叫數與並發） |

#### 4.1.3 MCP 研究階段（MCP_SPEC §6.1）

| 欄位名 | 型別 | 預設 | env var 名 | 說明 |
|--------|------|------|-----------|------|
| `mcp_enabled` | `bool` | `False` | `MCP_ENABLED` | MCP 研究階段開關；false 時 mcp package 不匯入 |
| `mcp_providers` | `str` | `""` | `MCP_PROVIDERS` | csv 子集：`maps,apify_threads_post,realping`（session-backed）。`web_search` 不在此列（OpenAI-native）。啟用時必填 |
| `mcp_research_model` | `str` | `""` | `MCP_RESEARCH_MODEL` | planner 用的 tool-calling 模型；啟用時必填 |
| `mcp_required` | `bool` | `False` | `MCP_REQUIRED` | true 時研究降級視為分析失敗（群組 status=error） |
| `mcp_max_rounds` | `int` | `3` | `MCP_MAX_ROUNDS` | v1.0 legacy（agentic rounds）；v1.1 未使用 |
| `mcp_max_tool_calls` | `int` | `3` | `MCP_MAX_TOOL_CALLS` | v1.1：planner 單次最多 tool call 數（**compose 未透傳**） |
| `mcp_result_chars` | `int` | `3000` | `MCP_RESULT_CHARS` | 單一工具結果字元上限 |
| `mcp_total_result_chars` | `int` | `12000` | `MCP_TOTAL_RESULT_CHARS` | 全部結果字元上限 |
| `mcp_tool_timeout_s` | `float` | `30.0` | `MCP_TOOL_TIMEOUT_S` | 單工具逾時 |
| `mcp_connect_timeout_s` | `float` | `10.0` | `MCP_CONNECT_TIMEOUT_S` | 連線逾時 |
| `mcp_run_timeout_s` | `float` | `180.0` | `MCP_RUN_TIMEOUT_S` | 整體執行逾時 |
| `mcp_research_max_tokens` | `int` | `1024` | `MCP_RESEARCH_MAX_TOKENS` | planner max tokens |
| `mcp_allow_opinion_context` | `bool` | `False` | `MCP_ALLOW_OPINION_CONTEXT` | **legacy/未使用** — 定義於 Settings 但目前無任何程式碼消費（原為「私人意見外洩 opt-out hatch」設計，但 AI 層無條件不將 raw opinions 進入 planner/tools）。保留供未來擴充；實作時可定義但無需接線。 |
| `mcp_log_verbose` | `bool` | `False` | `MCP_LOG_VERBOSE` | 詳細日誌 |

#### 4.1.4 MCP provider 憑證（選填，對應 `mcp_config.yaml` 的 `api_key_env`）

| 欄位名 | 型別 | 預設 | env var 名 | 對應 provider |
|--------|------|------|-----------|---------------|
| `openai_api_key` | `str` | `""` | `OPENAI_API_KEY` | `web_search`（OpenAI-native，經 `run_openai_prompt`，**與 `llm_api_key` 不同**） |
| `apify_token` | `str` | `""` | `APIFY_TOKEN` | `apify_threads_post` |
| `google_maps_api_key` | `str` | `""` | `GOOGLE_MAPS_API_KEY` | `maps` |
| `realping_api_key` | `str` | `""` | `REALPING_API_KEY` | `realping` |

### 4.2 `model_config`

```python
model_config = {"env_prefix": "", "case_sensitive": False}
```

- `env_prefix=""`：env var 名**無前綴**，直接 = 欄位名。
- `case_sensitive=False`：env var 名不分大小寫（`DATABASE_URL` 與 `database_url` 皆對應 `database_url` 欄位）。

### 4.3 `_validate_mcp` 驗證器

```python
@model_validator(mode="after")
def _validate_mcp(self):
    if self.mcp_enabled:
        if not self.mcp_research_model.strip():
            raise ValueError(
                "MCP_ENABLED=true requires MCP_RESEARCH_MODEL (a tool-calling-capable model)."
            )
        if not self.mcp_providers.strip():
            raise ValueError(
                "MCP_ENABLED=true requires MCP_PROVIDERS (csv subset of "
                "maps,apify_threads_post,realping)."
            )
    return self
```

- `mode="after"`：在所有欄位載入後執行。
- `mcp_enabled=true` 時，`mcp_research_model` 與 `mcp_providers` 都必須**非空（strip 後）**，否則 `raise ValueError`——**啟動即失敗**（`Settings()` 建構拋錯 → `get_settings()` 拋錯 → app 無法啟動）。
- `mcp_enabled=false`（預設）時不檢查。

### 4.4 `get_settings()` singleton

```python
@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- `@lru_cache`：process 級單例。整個進程生命週期只建構一次 `Settings()`。
- **測試需 `cache_clear()`**：若測試修改 env var 後要重新載入設定，必須呼叫 `get_settings.cache_clear()`（否則 lru_cache 回傳舊實例）。`conftest.py` 的 `_reset_global_pool` 只重置 db pool，不重置 settings cache。

### 4.5 注意：不在 `Settings` 的變數 / 雙路徑讀取的憑證

以下變數由 `mcp_config.yaml` / `mcp_tools.py` 透過 `os.environ` 直接讀取，**不是 `Settings` 欄位**，但本層 `.env.example` 提供模板：

| 變數 | 用途 | 讀取者 |
|------|------|--------|
| `LOCAL_OPENAI_BASE_URL` | 本地 OpenAI-compatible model base URL（`mcp_config.yaml` `models.local.base_url_env`） | `mcp_tools.py`（`os.environ` / `load_dotenv`） |
| `LOCAL_API_KEY` | 本地 model bearer（`mcp_config.yaml` `models.local.api_key`，預設 `local`） | `mcp_tools.py` |

**雙路徑讀取的 provider 憑證（重要）**：`OPENAI_API_KEY`、`APIFY_TOKEN`、`GOOGLE_MAPS_API_KEY`、`REALPING_API_KEY` 雖然是 `Settings` 欄位（§4.4），但 AI 層的 `mcp_tools.py` 透過 `required_env(api_key_env)` 從 `os.environ` 直接讀取（`api_key_env` 名稱由 `mcp_config.yaml` 定義），**不經 `get_settings()`**。實務上不會壞（compose 設了 env var，Settings 與 `os.environ` 拿到同一個值），但這些 Settings 欄位形同從未被 `get_settings()` 消費——它們存在是為了文件可見性與未來可能改為 Settings 讀取。實作時：compose 透傳 env var 即可，兩條讀取路徑都拿得到。

- `compose.yml` **未透傳** `LOCAL_OPENAI_BASE_URL`/`LOCAL_API_KEY`（見 §2.3.2 與 §6）；但本規格的 backend volumes 已掛 `./.env:/.env:ro`，故 `mcp_tools.py` 的 `load_dotenv(ENV_PATH)` 在容器內能讀到這兩個變數。本地開發則直接靠 `.env` + `load_dotenv`。

---

## 5. 資料綱要（`backend/migrations/`）

三個 migration 檔，由 `db.py` 的 `init_db` 以 **sorted glob** 依序執行（`001` → `002` → `003`），全部冪等。以下逐表逐欄列出完整 DDL。

### 5.1 `001_init.sql` — 初始 schema

#### 5.1.1 `groups` 表

```sql
CREATE TABLE IF NOT EXISTS groups (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pin           CHAR(5) UNIQUE NOT NULL,
    question      TEXT NOT NULL,
    creator_token TEXT NOT NULL,
    expected_count INT NULL,
    deadline      TIMESTAMPTZ NULL,
    status        TEXT NOT NULL DEFAULT 'collecting',
    consensus     TEXT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

| 欄位 | 型別 | 約束 | 說明 |
|------|------|------|------|
| `id` | UUID | PRIMARY KEY, DEFAULT `gen_random_uuid()` | 群組 ID |
| `pin` | CHAR(5) | UNIQUE NOT NULL | 5 字元加入碼 |
| `question` | TEXT | NOT NULL | 討論問題 |
| `creator_token` | TEXT | NOT NULL | 建立者 token |
| `expected_count` | INT | NULL | 預期人數 |
| `deadline` | TIMESTAMPTZ | NULL | 截止時間 |
| `status` | TEXT | NOT NULL DEFAULT `'collecting'` | 狀態機：collecting/analyzing/done/closed/error |
| `consensus` | TEXT | NULL | 最新輪共識（向後相容） |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT `now()` | 建立時間 |

#### 5.1.2 `participants` 表

```sql
CREATE TABLE IF NOT EXISTS participants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id    UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    nickname    TEXT NOT NULL,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, nickname)
);
```

| 欄位 | 型別 | 約束 | 說明 |
|------|------|------|------|
| `id` | UUID | PRIMARY KEY, DEFAULT `gen_random_uuid()` | 成員 ID |
| `group_id` | UUID | NOT NULL, FK → `groups(id)` ON DELETE CASCADE | 所屬群組 |
| `nickname` | TEXT | NOT NULL | 暱稱 |
| `joined_at` | TIMESTAMPTZ | NOT NULL DEFAULT `now()` | 加入時間 |
| — | — | **UNIQUE (group_id, nickname)** | 同群組暱稱唯一 |

#### 5.1.3 `responses` 表（初始版，單輪）

```sql
CREATE TABLE IF NOT EXISTS responses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id        UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    participant_id  UUID NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, participant_id)
);
CREATE INDEX IF NOT EXISTS responses_group_id_idx ON responses (group_id);
```

| 欄位 | 型別 | 約束 | 說明 |
|------|------|------|------|
| `id` | UUID | PRIMARY KEY, DEFAULT `gen_random_uuid()` | 回覆 ID |
| `group_id` | UUID | NOT NULL, FK → `groups(id)` ON DELETE CASCADE | 群組 |
| `participant_id` | UUID | NOT NULL, FK → `participants(id)` ON DELETE CASCADE | 成員 |
| `content` | TEXT | NOT NULL | 回覆內容 |
| `submitted_at` | TIMESTAMPTZ | NOT NULL DEFAULT `now()` | 提交時間 |
| — | — | **UNIQUE (group_id, participant_id)**（初始；002 改為三欔） | 單輪時每人一回覆 |

- 索引：`responses_group_id_idx` ON `responses (group_id)`（IF NOT EXISTS）。

### 5.2 `002_rounds.sql` — 多輪審議

> 冪等：每個 statement 都有守衛（`IF NOT EXISTS` / `pg_constraint` 查詢 / `ON CONFLICT DO NOTHING`），可在新鮮或已填充的 DB 重跑。在 `001` 之後由 `init_db` sorted glob 執行。

#### 5.2.1 `responses`：加 round 維度

```sql
ALTER TABLE responses ADD COLUMN IF NOT EXISTS round_number INT NOT NULL DEFAULT 1;
```

- 既有 rows 得 `round_number=1`；因原本已滿足 `UNIQUE(group_id, participant_id)`，新三欔 key 也可滿足，無資料衝突。

```sql
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
    WHERE conname = 'responses_group_id_round_number_participant_id_key') THEN
    ALTER TABLE responses DROP CONSTRAINT IF EXISTS responses_group_id_participant_id_key;
    ALTER TABLE responses ADD CONSTRAINT responses_group_id_round_number_participant_id_key
      UNIQUE (group_id, round_number, participant_id);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS responses_group_round_idx ON responses (group_id, round_number);
```

| 變更 | 說明 |
|------|------|
| ADD COLUMN `round_number` | INT NOT NULL DEFAULT 1 |
| DROP + ADD UNIQUE | 先 `DROP CONSTRAINT IF EXISTS` 舊兩欔 key（`responses_group_id_participant_id_key`），再加新三欔 key（`responses_group_id_round_number_participant_id_key`）。守衛：先查 `pg_constraint` 確認新 key 不存在才動。 |
| 新索引 | `responses_group_round_idx` ON `(group_id, round_number)` |

#### 5.2.2 `groups`：加輪次追蹤

```sql
ALTER TABLE groups ADD COLUMN IF NOT EXISTS current_round INT NOT NULL DEFAULT 1;
ALTER TABLE groups ADD COLUMN IF NOT EXISTS max_rounds   INT NOT NULL DEFAULT 3;
```

| 新欄位 | 型別 | 預設 | 說明 |
|--------|------|------|------|
| `current_round` | INT NOT NULL | 1 | 當前輪次 |
| `max_rounds` | INT NOT NULL | 3 | 最大輪次 |

#### 5.2.3 `participants`：穩定 `member_seq`（私有軌）

```sql
ALTER TABLE participants ADD COLUMN IF NOT EXISTS member_seq INT NULL;
-- 回填
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='participants' AND column_name='member_seq')
     AND EXISTS (SELECT 1 FROM participants WHERE member_seq IS NULL) THEN
    WITH ranked AS (
      SELECT id, row_number() OVER (PARTITION BY group_id ORDER BY joined_at, id) AS rn
      FROM participants
    )
    UPDATE participants p SET member_seq = ranked.rn
    FROM ranked WHERE p.id = ranked.id;
  END IF;
END $$;
-- 唯一約束
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
    WHERE conname = 'participants_group_id_member_seq_key') THEN
    ALTER TABLE participants ADD CONSTRAINT participants_group_id_member_seq_key
      UNIQUE (group_id, member_seq);
  END IF;
END $$;
```

| 新欄位 | 型別 | 說明 |
|--------|------|------|
| `member_seq` | INT NULL → 回填後 NOT NULL 事實上 | 群組內穩定序號，供跨輪 stance 對齊；**系統內部、永不暴露** |

- 回填：`row_number() OVER (PARTITION BY group_id ORDER BY joined_at, id)`，每群組從 1 開始。
- 守衛：確認欄位存在且有 NULL 值才回填。
- UNIQUE `(group_id, member_seq)`：防止並發 join 產生重複序號；守衛查 `pg_constraint`。

#### 5.2.4 `rounds` 新表

```sql
CREATE TABLE IF NOT EXISTS rounds (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id       UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    round_number   INT NOT NULL,
    question       TEXT NOT NULL,
    consensus      TEXT NULL,
    stance_digest  TEXT NULL,
    stance_shift_summary TEXT NULL,
    research_brief TEXT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    analyzed_at    TIMESTAMPTZ NULL,
    UNIQUE (group_id, round_number)
);
CREATE INDEX IF NOT EXISTS rounds_group_id_idx ON rounds (group_id);
```

| 欄位 | 型別 | 約束 | 隱私分級 | 說明 |
|------|------|------|----------|------|
| `id` | UUID | PRIMARY KEY DEFAULT `gen_random_uuid()` | — | round ID |
| `group_id` | UUID | NOT NULL, FK → `groups(id)` ON DELETE CASCADE | — | 群組 |
| `round_number` | INT | NOT NULL, UNIQUE(group_id, round_number) | — | 輪次 |
| `question` | TEXT | NOT NULL | 公開 | 該輪問題 |
| `consensus` | TEXT | NULL | **公開**（群體級，無成員歸因） | 該輪共識 |
| `stance_digest` | TEXT | NULL | **PRIVATE**（僅 stance call 用，永不公開，§5.6） | 逐成員 stance 摘要 |
| `stance_shift_summary` | TEXT | NULL | 公開安全（群體級演化摘要，無成員標籤，§5.1） | stance 演變摘要 |
| `research_brief` | TEXT | NULL | **PRIVATE**（MCP 快取，跨輪成本控制，MCP_SPEC §17 extension） | MCP 研究 brief 快取 |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT `now()` | 公開 | 建立時間 |
| `analyzed_at` | TIMESTAMPTZ | NULL | 公開 | 分析完成時間 |

- 索引：`rounds_group_id_idx` ON `rounds (group_id)`。
- UNIQUE：`(group_id, round_number)`。

#### 5.2.5 回填既有單輪群組到 `rounds` 第 1 輪

```sql
INSERT INTO rounds (group_id, round_number, question, consensus)
SELECT id, 1, question, consensus FROM groups g
WHERE NOT EXISTS (SELECT 1 FROM rounds r WHERE r.group_id = g.id AND r.round_number = 1)
ON CONFLICT (group_id, round_number) DO NOTHING;
```

- 冪等：`WHERE NOT EXISTS` + `ON CONFLICT DO NOTHING` 雙保險。
- 將既有單輪群組的 `question` / `consensus` 複製到 `rounds` 第 1 輪。

### 5.3 `003_adaptive_questions.sql` — 逐成員個人化問題

> ADAPTIVE_SPEC §5。冪等，statement-guarded，與 001/002 同模式。

#### 5.3.1 `groups`：加 adaptive 開關

```sql
ALTER TABLE groups ADD COLUMN IF NOT EXISTS adaptive_questions BOOLEAN NOT NULL DEFAULT FALSE;
```

| 新欄位 | 型別 | 預設 | 說明 |
|--------|------|------|------|
| `adaptive_questions` | BOOLEAN NOT NULL | FALSE | 逐群組 adaptive 開關；建立群組時設定 |

#### 5.3.2 `member_questions` 新表

```sql
CREATE TABLE IF NOT EXISTS member_questions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id       UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    round_number   INT NOT NULL,
    participant_id UUID NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    question       TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, round_number, participant_id)
);

CREATE INDEX IF NOT EXISTS member_questions_group_round_idx
    ON member_questions (group_id, round_number);
```

| 欄位 | 型別 | 約束 | 說明 |
|------|------|------|------|
| `id` | UUID | PRIMARY KEY DEFAULT `gen_random_uuid()` | 問題 ID |
| `group_id` | UUID | NOT NULL, FK → `groups(id)` ON DELETE CASCADE | 群組 |
| `round_number` | INT | NOT NULL | 回答輪次（= N+1，於第 N 輪分析時產生） |
| `participant_id` | UUID | NOT NULL, FK → `participants(id)` ON DELETE CASCADE | 收件成員 |
| `question` | TEXT | NOT NULL | 個人化問題內容 |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT `now()` | 建立時間 |
| — | — | **UNIQUE (group_id, round_number, participant_id)** | 每輪每人一題 |

- 索引：`member_questions_group_round_idx` ON `(group_id, round_number)`。

### 5.4 `init_db` 執行機制

```python
async def init_db(pool: asyncpg.Pool) -> None:
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    for sql_path in sorted(migrations_dir.glob("*.sql")):
        await pool.execute(sql_path.read_text(encoding="utf-8"))
```

- **sorted glob**：確保 `001` → `002` → `003` 順序。
- 全部冪等：可在空 DB 或已有資料的 DB 重跑。
- 由 `main.py` 的 `lifespan` 在啟動時呼叫（`await init_db(pool)`），故 **migration 自動跑**（見 §9）。

### 5.5 隱私分級總表

| 欄位 | 分級 | 不可出現於 |
|------|------|------------|
| `rounds.stance_digest` | **PRIVATE** | 任何 API 回應（GET /rounds、GET /state、SSE） |
| `rounds.research_brief` | **PRIVATE** | 任何 API 回應 |
| `participants.member_seq` | 系統內部 | 任何 API 回應（用於 stance 標籤 `成員{member_seq}`，但回應只含標籤字串，不含 seq 數值） |
| `member_questions.question` | **僅收件人可見** | SSE broadcast、GET /rounds、GET /state 內容；只有 `/my-question`（收件人本人）可讀 |

---

## 6. 環境變數模板（`.env.example`）

`.env.example` 是所有環境變數的權威模板。複製為 `.env` 填入真實值。`.env` 已 gitignore（見 §10）。

### 6.1 完整變數分區表

#### 6.1.1 LLM（必填）

| 變數 | 範例 / 預設 | 必填 | 說明 |
|------|-------------|------|------|
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | 是 | LLM API base URL |
| `LLM_API_KEY` | `your-openrouter-api-key` | **是** | LLM API key |
| `LLM_MODEL` | `google/gemma-4-26b-a4b-it:free` | 是 | 模型名 |
| `# LLM_MAX_TOKENS` | `8192` | 否（註解） | compose 寫死，改 .env 不生效 |
| `# LLM_TEMPERATURE` | `0.3` | 否（註解） | compose 寫死 |
| `# LLM_TIMEOUT` | `60.0` | 否（註解） | compose 未透傳，靠 Settings 預設 |

> `.env.example` 中 `LLM_MAX_TOKENS` / `LLM_TEMPERATURE` / `LLM_TIMEOUT` 被註解（`#`），表示可選。其中前兩者在 compose 寫死，改 `.env` 不影響容器。

#### 6.1.2 MCP 研究階段

| 變數 | 範例 / 預設 | 必填 | 說明 |
|------|-------------|------|------|
| `MCP_ENABLED` | `false` | 否 | 設 true 啟用外部研究（A1 draft → research → A3 final） |
| `MCP_PROVIDERS` | （空） | 啟用時必填 | csv 子集：`maps,apify_threads_post,realping`。`web_search` 不在此列 |
| `MCP_RESEARCH_MODEL` | （空） | 啟用時必填 | tool-calling 模型（planner） |
| `# MCP_REQUIRED` | `false` | 否（註解） | true 時研究降級 → status=error |
| `# MCP_MAX_TOOL_CALLS` | `3` | 否（註解） | planner 單次 max tool calls（compose 未透傳） |
| `# MCP_RESULT_CHARS` | `3000` | 否（註解） | 單工具結果字元上限 |
| `# MCP_TOTAL_RESULT_CHARS` | `12000` | 否（註解） | 全部結果字元上限 |
| `# MCP_TOOL_TIMEOUT_S` | `30` | 否（註解） | 單工具逾時 |
| `# MCP_CONNECT_TIMEOUT_S` | `10` | 否（註解） | 連線逾時 |
| `# MCP_RUN_TIMEOUT_S` | `180` | 否（註解） | 整體執行逾時 |
| `# MCP_RESEARCH_MAX_TOKENS` | `1024` | 否（註解） | planner max tokens |
| `# MCP_ALLOW_OPINION_CONTEXT` | `false` | 否（註解） | 私人意見外洩 opt-out |
| `# MCP_LOG_VERBOSE` | `false` | 否（註解） | 詳細日誌 |

> `.env.example` 也有 `# MCP_MAX_ROUNDS=3`（v1.0 legacy，註解）。

#### 6.1.3 Adaptive

| 變數 | 範例 / 預設 | 必填 | 說明 |
|------|-------------|------|------|
| `ADAPTIVE_QUESTIONS_ENABLED` | `true` | 否 | 全域 kill-switch；false → P stage 跳過、`/my-question` 404 |
| `# ADAPTIVE_P_MAX_MEMBERS` | `20` | 否（註解） | P stage 人數上限 |

#### 6.1.4 MCP provider 憑證

| 變數 | 範例 / 預設 | 必填 | 對應 provider / 說明 |
|------|-------------|------|----------------------|
| `GOOGLE_MAPS_API_KEY` | （空） | `MCP_PROVIDERS` 含 `maps` 時 | maps（Google Maps） |
| `APIFY_TOKEN` | （空） | `MCP_PROVIDERS` 含 `apify_threads_post` 時 | apify_threads_post（Threads 搜尋） |
| `REALPING_API_KEY` | （空） | `MCP_PROVIDERS` 含 `realping` 時 | realping（台灣房產） |
| `OPENAI_API_KEY` | （空） | `web_search` 啟用時 | web_search（OpenAI-native，經 `run_openai_prompt`，**非 research_phase**） |
| `LOCAL_OPENAI_BASE_URL` | （空） | 否 | 本地 OpenAI-compatible model base URL（`mcp_config.yaml` 用，**不在 Settings**，**compose 未透傳**） |
| `LOCAL_API_KEY` | `local` | 否 | 本地 model bearer（`mcp_config.yaml` 用，**不在 Settings**，**compose 未透傳**） |

#### 6.1.5 Database / server

| 變數 | 範例 / 預設 | 必填 | 說明 |
|------|-------------|------|------|
| `DATABASE_URL` | `postgresql://conclave:conclave@localhost:5432/conclave` | 否（compose 覆蓋） | 本地非 Docker 開發用；compose 內寫死 `db:5432` |
| `# SSE_KEEPALIVE_S` | `15` | 否（註解） | compose 未透傳，靠 Settings 預設 |
| `# DEADLINE_SCAN_S` | `5` | 否（註解） | compose 未透傳，靠 Settings 預設 |

### 6.2 `.env.example` 檔頭註解

```
# Conclave environment variables — copy this file to .env and fill in real values.
# .env is gitignored; never commit real credentials.
```

---

## 7. 測試基礎設施

### 7.1 `backend/pytest.ini`

```ini
[pytest]
asyncio_mode = auto
markers =
    integration: tests requiring a live Postgres at localhost:5432
```

| 設定 | 說明 |
|------|------|
| `asyncio_mode = auto` | 所有 `async def` 測試自動當 asyncio 測試執行，不需 `@pytest.mark.asyncio`。 |
| `markers integration` | 標記需要 live Postgres（`localhost:5432`）的整合測試；無此標記的純單元測試不需 DB。 |

### 7.2 `backend/tests/conftest.py`

```python
TEST_DSN = "postgresql://conclave:conclave@localhost:5432/conclave"
```

#### 7.2.1 `_reset_global_pool` autouse fixture

```python
@pytest.fixture(autouse=True)
def _reset_global_pool(request):
    db._pool = None
    has_db = request.node.get_closest_marker("integration") is not None
    if has_db:
        async def _truncate():
            conn = await asyncpg.connect(dsn=TEST_DSN)
            try:
                await conn.execute("TRUNCATE groups, participants, responses, rounds, member_questions CASCADE")
            finally:
                await conn.close()
        asyncio.run(_truncate())
    yield
    db._pool = None
```

| 行為 | 說明 |
|------|------|
| **autouse** | 每個測試前後都執行。 |
| `db._pool = None`（前後各一次） | 重置 app module 的全域 asyncpg pool。原因：Starlette TestClient 每個請求開新 blocking portal / event loop，asyncpg 連線綁定建立時的 loop，跨 loop 重用會拋 "Future attached to a different loop"。設 None 強制 `get_pool()` 重建。 |
| `integration` marker 判斷 | 有標記才 TRUNCATE（需 DB）；無標記的純單元測試不碰 DB。 |
| TRUNCATE 表清單 | `groups, participants, responses, rounds, member_questions` 全部 `CASCADE`——五張表完整清單。 |
| `asyncio.run` | 在同步 fixture 內跑 async truncate。 |

#### 7.2.2 `pool` fixture

```python
@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=TEST_DSN, min_size=1, max_size=5)
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE groups, participants, responses, rounds, member_questions CASCADE")
    await init_db(p)
    yield p
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE groups, participants, responses, rounds, member_questions CASCADE")
    await p.close()
```

| 行為 | 說明 |
|------|------|
| 建立 pool | `min_size=1, max_size=5`（測試用小池，對比 app 的 `min_size=2, max_size=10`）。 |
| 前 TRUNCATE | 清空五張表（CASCADE）。 |
| `init_db(p)` | 跑 migrations（確保 schema 最新）。 |
| yield p | 測試用此 pool 直接操作 DB。 |
| 後 TRUNCATE + close | 清理 + 關池。 |

### 7.3 執行方式

```bash
cd backend
export DATABASE_URL=postgresql://conclave:conclave@localhost:5432/conclave
pytest tests/
```

- **純單元測試**（無 `integration` marker）：不需 DB，不需 `DATABASE_URL`。
- **整合測試**（`@pytest.mark.integration`）：需 live Postgres 在 `localhost:5432`，DSN = `TEST_DSN`。
- 容器內測試：DB 服務在 `db:5432`（非 `localhost`），需調整 `TEST_DSN` 或在 backend 容器內跑（本層 conftest 寫死 `localhost:5432`，故整合測試主要在 host 直連本地 DB 跑）。

---

## 8. 相依清單

### 8.1 `backend/requirements.txt`

```
fastapi==0.141.1
uvicorn[standard]==0.52.4
sse-starlette==3.4.10
pydantic==2.13.5
pydantic-settings==2.15.0
asyncpg==0.31.0
openai==3.8.0
httpx==0.28.1
pytest==8.3.4
pytest-asyncio==0.24.0
mcp[cli]==2.1.1
python-dotenv==1.2.3
yahoo-finance-mcp @ git+https://github.com/Alex2Yang97/yahoo-finance-mcp@3ae895950b13eaca79e167679aec125e3ca41119
```

| 套件 | 版本 pin | 用途 |
|------|---------|------|
| `fastapi` | `==0.141.1` | Web 框架 |
| `uvicorn[standard]` | `==0.52.4` | ASGI server（含 httptools 等加速） |
| `sse-starlette` | `==3.4.10` | SSE 支援 |
| `pydantic` | `==2.13.5` | 資料驗證 |
| `pydantic-settings` | `==2.15.0` | BaseSettings（config.py） |
| `asyncpg` | `==0.31.0` | async PostgreSQL driver |
| `openai` | `==3.8.0` | OpenAI SDK（LLM + web_search） |
| `httpx` | `==0.28.1` | async HTTP client |
| `pytest` | `==8.3.4` | 測試框架 |
| `pytest-asyncio` | `==0.24.0` | asyncio 測試支援 |
| `mcp[cli]` | `==2.1.1` | MCP client（含 CLI extras） |
| `python-dotenv` | `==1.2.3` | `.env` 載入（mcp_tools.py 用） |
| `yahoo-finance-mcp` | `@ git+...@3ae89595...` | **從 git 安裝**（pin commit hash），故 Dockerfile 需 `apt install git` |

> **重要**：`yahoo-finance-mcp` 是 git 依賴（`git+https://...@<commit>`），pip 需 `git` 可執行檔。這是 `backend/Dockerfile` 必須 `apt-get install git` 的原因（見 §3.1）。

### 8.2 `frontend/package.json`

```json
{
  "name": "conclave-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host 0.0.0.0 --port 5173",
    "build": "tsc -b && vite build",
    "preview": "vite preview --host 0.0.0.0 --port 5173"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-markdown": "^9.0.1",
    "react-router-dom": "^6.26.2",
    "remark-gfm": "^4.0.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.4",
    "vite": "^5.4.2"
  },
  "allowScripts": {
    "esbuild@0.21.5": true
  }
}
```

| 類別 | 套件 | 用途 |
|------|------|------|
| dependencies | `react` / `react-dom` | UI 框架 |
| dependencies | `react-markdown` | Markdown 渲染（consensus 顯示） |
| dependencies | `react-router-dom` | 路由 |
| dependencies | `remark-gfm` | GFM Markdown plugin |
| devDependencies | `@types/react` / `@types/react-dom` | TypeScript 型別 |
| devDependencies | `@vitejs/plugin-react` | Vite React plugin |
| devDependencies | `typescript` | TS 編譯 |
| devDependencies | `vite` | 打包/dev server |
| scripts | `dev` | `vite --host 0.0.0.0 --port 5173`（compose command 覆蓋加 `-- --host`） |
| scripts | `build` | `tsc -b && vite build` |
| scripts | `preview` | `vite preview --host 0.0.0.0 --port 5173` |

> **無前端測試框架**：`package.json` 不含 jest / vitest / testing-library，無 `test` script。前端無自動化測試。

### 8.3 `frontend/vite.config.ts`

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://backend:8000", changeOrigin: true },
      "/events": { target: "http://backend:8000", changeOrigin: true, ws: false },
    },
  },
});
```

| proxy 路徑 | target | 說明 |
|-----------|--------|------|
| `/api` | `http://backend:8000` | API 請求轉發 backend（compose 服務名 `backend`） |
| `/events` | `http://backend:8000` | SSE 路徑；**`ws: false`**——SSE 走 HTTP 長連線，**非 WebSocket**。`changeOrigin: true` 改 Host header。 |

> **重要**：`target: "http://backend:8000"` 依賴 compose 的服務名 `backend`（Docker 內部 DNS）。在容器外本地跑 vite 時需改為 `localhost:8000`。

---

## 9. 從零部署檢核表

### 9.1 部署步驟

| 步驟 | 指令 / 動作 | 驗證 |
|------|-----------|------|
| 1. 前置 | 確認 Docker Engine 24+ / Compose v2+ | `docker --version` / `docker compose version` |
| 2. 複製 env | `cp .env.example .env` | `.env` 存在 |
| 3. 填 LLM key | 編輯 `.env`，填 `LLM_API_KEY`（必填） | `LLM_API_KEY` 非空 |
| 4. 建置啟動 | `docker compose up --build` | 三服務皆 Running |
| 5. 驗證 backend | `curl http://localhost:8000/api/health` | 回 200 |
| 6. 驗證 frontend | 瀏覽器開 `http://localhost:5173` | 前端頁面載入 |
| 7. migration 自動跑 | backend 啟動時 `lifespan` → `init_db` 跑 001→002→003 | DB 有四張表 + rounds + member_questions |
| 8. 停止 | `docker compose down` | 服務停，volume 保留 |
| 9. 清除資料 | `docker compose down -v` | 刪除 `pgdata` volume |

### 9.2 關鍵陷阱

| 陷阱 | 說明 |
|------|------|
| **`MCP_ENABLED=true` 需同時設 `MCP_RESEARCH_MODEL` + `MCP_PROVIDERS`** | 否則 `_validate_mcp` 在 `Settings()` 建構時 `raise ValueError`，backend 啟動即失敗。 |
| **`--no-access-log`** | participant_id bearer token 走 query string；access log 會洩漏。不可移除。 |
| **未透傳變數** | `LLM_TIMEOUT` / `SSE_KEEPALIVE_S` / `DEADLINE_SCAN_S` / `MCP_MAX_TOOL_CALLS` 在 compose 無條目，靠 `Settings` 預設。要改需編 compose。 |
| **`LLM_MAX_TOKENS` / `LLM_TEMPERATURE` 寫死** | compose 用字面值 `"8192"` / `"0.3"`，改 `.env` 不影響容器。 |
| **frontend `depends_on` 無 healthcheck** | frontend 不等 backend 健康；剛啟動時 proxy 可能短暫 502，整理後即正常。 |
| **`down` vs `down -v`** | `down` 保留 `pgdata`（重啟資料還在）；`down -v` 刪 volume（資料清空）。 |
| **無 `env_file`** | 只有被 compose `${VAR}` 引用的 `.env` 變數進容器。`LOCAL_OPENAI_BASE_URL` 等未引用變數不會注入。 |
| **migration 自動跑** | `main.py` lifespan 呼叫 `init_db`，不需手動跑 migration。但首啟時 DB 必須已 healthy（`depends_on: service_healthy` 確保）。 |

---

## 10. `.gitignore`

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/

# Node
node_modules/
frontend/dist/
frontend/tsconfig.tsbuildinfo

# Secrets
.env

# IDE
.claude/

# OS
.DS_Store
```

| 類別 | 條目 | 說明 |
|------|------|------|
| Python | `__pycache__/`、`*.py[cod]`、`*.egg-info/`、`.pytest_cache/` | Python 編譯產物 + pytest cache |
| Node | `node_modules/`、`frontend/dist/`、`frontend/tsconfig.tsbuildinfo` | Node 依賴 + 建置產物 + TS 增量建置資訊 |
| Secrets | `.env` | **絕不 commit 真實憑證** |
| IDE | `.claude/` | Claude Code 設定目錄 |
| OS | `.DS_Store` | macOS 檔案 |

---

## 11. 與其他層的整合檢核表

實作完成後，逐項驗證本層與 AI 層 / 應用伺服器層的接縫一致：

| # | 檢核項 | 驗證方式 | 不一致的後果 |
|---|--------|----------|-------------|
| 1 | **Settings 欄位名與 AI 層 / 應用層讀取一致** | grep AI 層 / 應用層所有 `get_settings().<field>` 呼叫，比對 §4.1 欄位表 | AttributeError 或讀到錯誤預設 |
| 2 | **migration schema 欄位與 `db.py` SELECT 欄位清單一致** | 比對 `db.py` 每個 `SELECT` 的明確欄位名與 §5 DDL | `get_group` 等讀不到欄位（`Record` 缺欄） |
| 3 | **compose 透傳的 env var 與 Settings 欄位對應** | 比對 §2.3.1 透傳表與 §4.1 欄位表的 env var 名 | env var 傳了但 Settings 無對應欄位（被忽略）或 Settings 有欄位但 compose 未透傳（用預設） |
| 4 | **埠號與 vite proxy 一致** | `compose.yml` backend `8000` ↔ `vite.config.ts` `target: http://backend:8000`；frontend `5173` ↔ vite `--port 5173` | proxy 連不上 backend 或前端埠衝突 |
| 5 | **`depends_on` 條件** | backend `db: service_healthy` ↔ db healthcheck `pg_isready` | backend 在 DB 未就緒時啟動 → 連線失敗 |
| 6 | **migration 執行順序** | `init_db` sorted glob → `001` < `002` < `003`（檔名前綴確保） | 002 引用 001 的表，順序錯則失敗 |
| 7 | **TRUNCATE 表清單完整** | `conftest.py` TRUNCATE `groups, participants, responses, rounds, member_questions` 與 §5 五張表一致 | 測試殘留資料汙染 |
| 8 | **`--no-access-log` 保留** | compose backend command 含 `--no-access-log` | participant_id bearer token 進 access log |
| 9 | **`mcp_enabled` 啟用條件** | `MCP_ENABLED=true` 時 `.env` 同時有 `MCP_RESEARCH_MODEL` + `MCP_PROVIDERS` | backend 啟動即 ValueError 失敗 |
| 10 | **git 依賴可建置** | Dockerfile `apt install git` + requirements `yahoo-finance-mcp @ git+...` | pip install 失敗（無 git） |
| 11 | **`LOCAL_OPENAI_BASE_URL` / `LOCAL_API_KEY` 不在 Settings** | 確認 AI 層 `mcp_tools.py` 自行 `os.environ` 讀取，不經 `Settings` | 若誤加進 Settings 會與 mcp_config.yaml 讀取路徑重複/衝突 |
| 12 | **匿名 node_modules volume** | frontend volumes 含 `/app/node_modules`（匿名） | host node_modules 掛入容器導致 platform 不符或版本衝突 |

---

> **規格書結束**。本檔案為基礎設施層的完整實作規格。AI 層（`SPEC_AI`）與應用伺服器層（`SPEC_APP`）規格書各自定義其範圍內的實作，並依賴本層提供的接縫（Settings / migrations / compose / env template / test infra）。
