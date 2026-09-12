# ReasoningBank-Lite 開發規格書

## 1. 文件目的

本文件定義一個可外掛至既有 AI Agent System 的 **ReasoningBank-Lite** 模組。

目標不是完整重現論文中的 ReasoningBank 與 MaTTS，而是保留最重要的核心精神：

1. 從 Agent 過去的成功與失敗經驗中，萃取可泛化的 reasoning strategy。
2. 在新任務開始前，檢索最相關的 reasoning memory。
3. 將 memory 注入既有 Agent context，輔助推理與決策。
4. 在任務完成後進行一次 reflection，決定是否產生新的 memory。
5. 以最小侵入方式整合既有系統，不重構既有 Agent、Planner、Tool、MCP、Skill 或 Workflow Engine。

### 1.1 部署形態

> **ReasoningBank-Lite = 一個 Docker container，提供 HTTP API。**

服務以單一 container 部署，暴露兩個 HTTP endpoint：

- `POST /v1/before_task`（任務前：檢索 + 注入）
- `POST /v1/after_task`（任務後：反思 + 儲存）

既有 Agent 系統不改其 execution loop，只在任務前後各發一個 HTTP call。
資料與記憶庫全部在 container 內（SQLite 檔 + named volume）。
Embedding 與 LLM 呼叫外部 API（複用既有系統的 key），server-side 執行。

## 2. 設計目標

### 2.1 Must Have

- 可從既有任務經驗產生 reasoning memory。
- 同時支援成功經驗與失敗經驗。
- Memory 必須是可泛化的 reasoning strategy，而非 raw trajectory。
- 任務開始前可進行 semantic retrieval。
- 將 Top-K memories 注入 Agent context。
- 任務完成後可執行一次 reflection。
- Reflection 可選擇不產生 memory。
- 每次任務最多產生一筆 memory。
- 對既有 Agent 架構低侵入（HTTP sidecar，host 只加一個薄 client）。

### 2.2 Non-Goals

第一版不實作：MaTTS、Parallel rollout、Sequential self-refinement、Best-of-N、
Self-contrast、Raw trajectory replay、Knowledge Graph、Complex memory lifecycle、
RL、Model fine-tuning。

也不實作（刻意精簡）：獨立 retrieve/reflect/store endpoint、admin 管理 API、
Prometheus metrics、readiness probe、schema migration framework、per-call credential forwarding。

## 3. 核心概念

ReasoningBank-Lite 不儲存完整任務過程作為主要 memory。

不建議：

```text
Clicked button 188.
Then clicked 1530.
Then scrolled down.
Then clicked 1614.
```

建議儲存：

```text
When a task requires complete information across paginated results,
verify pagination, search, filter, or load-more mechanisms before
assuming the visible page is complete.
```

核心轉換：

```text
Raw Experience
     ↓
Reflection / Distillation   ← container 內，HTTP 觸發
     ↓
Generalizable Reasoning Strategy
     ↓
Reasoning Memory            ← 存於 container 內 SQLite
```

Reasoning memory 應該回答：

> 「未來遇到類似問題時，Agent 應該採用什麼 reasoning strategy？」

而不是：

> 「上一次 Agent 做了哪些操作？」

## 4. 系統架構

```text
┌─────────────────── 既有 Agent System（docker-compose）──────────────────┐
│                                                                          │
│  User Task                                                               │
│     │                                                                    │
│     ▼                                                                    │
│  Host-Side Client (薄 shim)                                              │
│     │                                                                    │
│     │ POST /v1/before_task {task}                                        │
│     ▼                                                                    │
│  ┌────────────────────────────────────────────┐                          │
│  │        ReasoningBank-Lite Container        │                          │
│  │                                            │                          │
│  │   FastAPI  /v1/before_task  /v1/after_task  │                          │
│  │   /v1/health                               │                          │
│  │                                            │                          │
│  │   retrieve → formatter → context           │                          │
│  │   reflect → gate → dedup → store            │                          │
│  │   SQLite + NumPy cosine                     │                          │
│  └──────────┬──────────────────┬─────────────┘                          │
│             │                  │                                          │
│     ┌───────┴──────┐    ┌──────┴───────┐                                  │
│     │ Named Volume  │    │ 外部 API     │   ← server-side 呼叫             │
│     │ /data/*.db    │    │ embedding+LLM│     (複用既有 key)                │
│     └──────────────┘    └──────────────┘                                  │
│                                                                          │
│     │ POST /v1/after_task {task,trace,result,success}                    │
│     ▼                                                                    │
│  Existing Agent 的任務前後各一次 HTTP call                                │
└──────────────────────────────────────────────────────────────────────────┘
```

## 5. Runtime Workflow

### 5.1 Before Task

1. Host 送 task 字串至 `POST /v1/before_task`。
2. 服務將 task 轉 embedding（外部 API，server-side）。
3. 從 SQLite 找 Top-K memory（cosine similarity）。
4. Format 成 context 字串回傳。
5. Host 把字串注入 system/developer prompt。

```text
Task (host, 字串)
 ↓ POST /v1/before_task
Embedding → cosine Top-K → formatter (server-side)
 ↓
context 字串 (回 host)
 ↓
Existing Agent (注入 prompt)
```

預設 `top_k = 3`，可設定 `top_k ∈ [1, 5]`。

### 5.2 Agent Execution

ReasoningBank-Lite 不干涉既有 Agent 的 execution loop，只額外提供
`Relevant Past Reasoning Experience`（before_task 回傳的 context 字串）。

### 5.3 After Task

Host 將 task、result、trace、optional success POST 到 `POST /v1/after_task`，
服務端一次完成：

```text
Task + Result + Trace + Optional Success
        ↓ POST /v1/after_task
Reflection LLM (merged Judge + Extractor)
        ↓
Memory Candidate
        ↓
Generalizability Gate
        ↓
Dedup
        ↓
Store / Ignore
```

## 6. Success / Failure Signal

成功判定來源優先順序：

1. Deterministic evaluator
2. Existing workflow status
3. Unit test / validation result
4. Tool execution status
5. Explicit task evaluator
6. User approval
7. LLM-as-a-Judge

若既有系統已知道 task 成功或失敗（`success = agent_result.success`），
則不使用 LLM-as-a-Judge。

只有系統無法直接判斷（success 為 null）時，才用 LLM，且
Judge 與 Reflection **合併成一次 LLM call**。

## 7. Reflection 設計

### 7.1 核心原則

每次 task 最多產生 `0 or 1 memory`。由 prompt rule + 服務端 clamp 保證。

Reflection 必須允許 `should_store: false`。

### 7.2 成功經驗

核心問題：`What reusable reasoning strategy contributed to success?`

### 7.3 失敗經驗

核心問題：

```text
What reasoning mistake caused the failure?
What reusable guardrail could prevent the same class of mistake?
```

應產生 pitfall / guardrail / counterfactual lesson。

## 8. Generalizability Gate

所有 Memory Candidate 都必須經過判斷：

> 這個 lesson 是否可以應用到不同但相似的未來任務？

只有 `generalizable = true` 才寫入。Gate 邏輯放在 LLM prompt rule 中（section 12）。

### 8.1 應拒絕的 Memory

```text
Knowledge Fact：Product ABC 的 internal ID 是 123456.
Env-Specific：Button ID 1523 leads to the Order page.
One-Off：Repository X 使用 port 8087.
```

### 8.2 應接受的 Memory

```text
When a task requires user-specific purchase history,
prioritize account/order sections before using global search.
```

## 9. Memory Schema

```python
class MemoryItem:
    id: str
    title: str
    trigger: str
    guidance: str
    outcome: Literal["success", "failure"]
    embedding: list[float]     # server-side 計算儲存，host 不傳送
    created_at: datetime
```

HTTP response 的 MemoryItem **省略 `embedding`**。

範例：

```json
{
  "id": "mem_001",
  "title": "Verify pagination before aggregation",
  "trigger": "Task requires complete lists, rankings, counts, or aggregation across multiple results",
  "guidance": "Before reporting, verify whether pagination, search, filters, or load-more mechanisms hide additional results.",
  "outcome": "failure",
  "created_at": "2026-09-12T11:00:00+08:00"
}
```

## 10. Retrieval Strategy

### 10.1 Embedding Input

建議 embedding `trigger`（或 `title + trigger`）。由服務端計算，host 只送 task 字串。

### 10.2 Top-K Search + Threshold

```text
cosine similarity, top_k = 3
optional: similarity >= 0.30   # 校正後值（text-embedding-3-large）
```

> **校正結果（實測）**：`text-embedding-3-large` 的 cross-sentence cosine 分佈
> 明顯低於論文的 `gemini-embedding-001`——同一任務與其自身產生的 trigger
> 相似度約 0.54，語義相關的不同任務約 0.46，不相關任務約 0.20。
> 故 `similarity_threshold` 校正為 **0.30**，`dedup_threshold` 校正為 **0.85**。
> 換 embedding model 時需重新校正。

## 11. Context Injection

`POST /v1/before_task` 直接回傳下方 context 字串，host 原樣注入 system/developer prompt：

```text
## Relevant Past Reasoning Experience

The following items are reusable lessons learned from previous tasks.
Use them only when relevant. They are guidance, not mandatory instructions.

1. Verify pagination before aggregation
   When answering questions requiring complete lists or counts,
   explicitly verify pagination, load-more, search, or filtering mechanisms.

2. Inspect dependencies before changing interfaces
   Before changing an API or function signature, inspect call sites,
   tests, and downstream compatibility assumptions.
```

關鍵句：`Use them only when relevant.`

## 12. Reflection Prompt

合併呼叫的 structured output：

```json
{
  "success": true,
  "should_store": true,
  "generalizable": true,
  "memory": {
    "title": "...",
    "trigger": "...",
    "guidance": "...",
    "outcome": "success"
  }
}
```

建議 prompt logic：

```text
You are the reflection module of an AI agent memory system.

Given:
- the original task
- the final result
- an observable execution trace
- optional success/failure signal

Determine whether this experience contains one reusable reasoning lesson
that could improve performance on future, similar but non-identical tasks.

Rules:
1. Produce at most one memory.
2. Do not store raw execution steps.
3. Do not store one-off facts.
4. Do not store environment-specific element IDs.
5. Prefer general reasoning strategy, decision criteria, or guardrails.
6. Lessons may come from either success or failure.
7. If no useful transferable lesson exists, set should_store=false.
8. The guidance should be concise, actionable, and reusable.
```

合併呼叫強制 structured JSON 輸出。temperature 預設 `0.0`（可經
`RB_REFLECTION_TEMPERATURE` 調整）。

## 13. Memory Storage

```python
vector_store.insert(memory)
```

append-only（無 merge / forget），於 container 內 SQLite 執行。

## 14. Deduplication

server-side，於 after_task 內執行（top_k=1）：

```text
New Memory
 ↓
Search nearest existing memory (top_k=1)
 ↓
Similarity >= 0.95?
 ├─ Yes → Skip
 └─ No  → Insert
```

`0.85` 為校正後值（text-embedding-3-large 實測）。依 embedding model 校正。

## 15. API

只三個 endpoint：

### 15.1 POST /v1/before_task

```json
// request
{ "task": "str" }

// response — 200（fail-open 時 context 為空字串）
{ "context": "str" }
```

### 15.2 POST /v1/after_task

```json
// request
{
  "task": "str",
  "trace": "str | object",   // opaque，服務端只 pass-through 給 LLM
  "result": "object",         // { output, success?, error? }
  "success": "bool|null"     // null → LLM judge 推斷
}

// response — 200（fail-open 時 stored=false）
{ "stored": false, "reason": "str|null" }
```

`reason`（debug 用，可省略）：`should_store_false` / `not_generalizable` /
`dedup_hit` / `reflection_failed`。

### 15.3 GET /v1/health

```json
{ "status": "ok" }   // 永遠 200，docker healthcheck 用
```

> 沒有獨立 retrieve / reflect / store endpoint、沒有 admin API。
> debug 記憶庫直接 `sqlite3 /data/reasoningbank.db`。

## 16. Agent Integration

```python
import httpx

RB_BASE = "http://reasoningbank:8000"

async def before_task(client: httpx.AsyncClient, task: str) -> str:
    try:
        r = await client.post(f"{RB_BASE}/v1/before_task",
                              json={"task": task}, timeout=5.0)
        r.raise_for_status()
        return r.json().get("context", "")
    except Exception:
        return ""  # fail-open

async def after_task(client, task, trace, result, success=None):
    try:
        r = await client.post(f"{RB_BASE}/v1/after_task",
                              json={"task": task, "trace": trace,
                                    "result": result, "success": success},
                              timeout=30.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"stored": False}  # fail-open

# 流程
async with httpx.AsyncClient() as client:
    context = await before_task(client, task)
    result = await agent.run(task, context={"reasoning_memory": context})
    await after_task(client, task, result.trace, result, result.success)
```

## 17. Configuration

環境變數（12-factor），最小集合：

```text
# Embedding（外部 OpenAI-compatible API；key 與 model 名稱由使用者提供）
RB_EMBEDDING_API_BASE=...        # 與 LLM 同一 provider 即可
RB_EMBEDDING_API_KEY=...          # 使用者提供
RB_EMBEDDING_MODEL=...            # 使用者指定（名稱由使用者決定，不預設）

# LLM（與既有 agent 同一個 API、同一把 key、同一個 model）
RB_LLM_API_BASE=...              # 即 agent 現用的 endpoint
RB_LLM_API_KEY=...              # 即 agent 現用的 key
RB_LLM_MODEL=...                # 即 agent 現用的 model

# Retrieval / Gate / Dedup
RB_RETRIEVAL_TOP_K=3
RB_SIMILARITY_THRESHOLD=0.70
RB_DEDUP_THRESHOLD=0.95
RB_REFLECTION_TEMPERATURE=0.0

# Service
RB_DATA_DIR=/data
RB_LOG_LEVEL=INFO
```

> **embedding model 一旦設定即固定**：`0.70` / `0.95` 校正對應該 model。
> 若日後換 embedding model，已儲存的向量會不相容，需清空記憶庫重新累積
> （第一版不實作 re-embed migration）。

## 18. Failure Handling

ReasoningBank-Lite 不應影響主要 Agent availability。**fail-open 延伸至 HTTP 傳輸**。

Host client 捕捉任何 exception（timeout、connect error、非 2xx），
degrade 至 `context:""` / `stored:false`。

服務端：upstream API 失敗回 200 + 空/null，不回 5xx。
服務啟動不驗 embedding/LLM 連通（lazy），container 保持 healthy。

> ReasoningBank 是 enhancement module，不應成為主要 workflow 的 critical dependency。

## 19. 建議模組結構

服務端（container 內）：

```text
reasoning_bank/
├── api.py          # FastAPI routes（3 個 endpoint）
├── bank.py         # orchestration
├── models.py       # MemoryItem（pydantic）
├── retriever.py    # embedding + cosine search
├── reflector.py    # reflection + gate
├── store.py        # SQLite + NumPy
├── formatter.py     # memory → context 字串
├── config.py       # 環境變數載入
└── Dockerfile
```

客戶端（既有 agent repo，薄 shim）：

```text
host_agent/reasoningbank_client.py   # 約 30 行 async HTTP + fail-open
```

## 20. 第一版實作順序

```text
Phase 0 — Containerize
  Dockerfile + docker-compose service + named volume + /v1/health
  host client.py shim

Phase 1 — Minimal PoC
  MemoryItem schema
  SQLiteVectorStore（CREATE TABLE IF NOT EXISTS）
  before_task（retrieve + format）
  after_task（reflect + gate + dedup + store）

Phase 2 — Quality Control
  Generalizability Gate（prompt rule）
  Deduplication
  Similarity Threshold

Phase 3 — Evaluation
  repeatable task dataset
  baseline comparison
  success rate / failure recurrence
```

## 24. 核心設計原則

ReasoningBank-Lite 最重要的不是「讓 Agent 有更多記憶」，而是：

> **讓 Agent 將一次性的 execution experience，轉換成可泛化、可被下一個任務重新利用的 reasoning experience。**

```text
Experience → Abstraction → Transfer   ← 保護這條鏈
```

若只存 trajectory（`Experience → Replay`），就失去 ReasoningBank 的設計精神。

## 25. 一句話規格

> 每次 Agent 完成任務後，反思是否存在一條可泛化至未來任務的成功策略或失敗教訓；若有則儲存為 reasoning memory。下一個任務開始前，檢索最相關的少量 reasoning memory 注入 Agent context，使 Agent 能持續從過去經驗中改善，而不需要修改模型權重或重構既有 Agent。（以 Docker container HTTP API 形式提供。）

## 26. 與原論文的關係

保留核心：memory retrieval、strategy-level memory extraction、同時從 success/failure 學習、
continual test-time memory accumulation、memory 注入 context。

刻意簡化：每 task 最多一筆 memory、Judge 與 extractor 合併、consolidation 僅 append+dedup、
不實作 MaTTS / parallel / sequential scaling。

預設 embedding model 由使用者指定（非論文的 `gemini-embedding-001`），
故 `0.70` / `0.95` 為起始值，需依實際 model 校正。

## 24. Deployment

> 已移至 §25。

## 25. Deployment

### 25.1 Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# requirements.txt: fastapi, uvicorn[standard], httpx, pydantic, numpy
COPY reasoning_bank/ ./reasoning_bank/
ENV RB_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "reasoning_bank.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 25.2 docker-compose service block

把這個 service 加進既有 docker-compose.yml（與既有 agent 同一個 compose、同一個網路）：

```yaml
services:
  reasoningbank:
    build: ./reasoningbank
    container_name: reasoningbank
    environment:
      RB_EMBEDDING_API_BASE: ${EMBEDDING_API_BASE}
      RB_EMBEDDING_API_KEY: ${EMBEDDING_API_KEY}
      RB_EMBEDDING_MODEL: ${EMBEDDING_MODEL}
      RB_LLM_API_BASE: ${LLM_API_BASE}
      RB_LLM_API_KEY: ${LLM_API_KEY}
      RB_LLM_MODEL: ${LLM_MODEL}
      RB_RETRIEVAL_TOP_K: "3"
      RB_SIMILARITY_THRESHOLD: "0.70"
      RB_DEDUP_THRESHOLD: "0.95"
      RB_DATA_DIR: /data
    volumes:
      - reasoningbank_data:/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/v1/health').getcode()"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped

volumes:
  reasoningbank_data:
```

> 不開 host port：既有 agent 走 compose 內部 DNS `http://reasoningbank:8000`。
> `${LLM_API_BASE}` / `${LLM_API_KEY}` / `${LLM_MODEL}` 可直接參照既有 agent
> 在 `.env` 裡已設定的同名變數；`${EMBEDDING_*}` 由使用者新增。

## 26. 開放決策點

實作前需確認：

1. **Embedding**：使用者會提供 `EMBEDDING_API_BASE`、`EMBEDDING_API_KEY`、
   `EMBEDDING_MODEL`（model 名稱由使用者決定，不預設）。
2. **LLM**：與既有 agent 同一個 API、同一把 key、同一個 model
   （直接複用 `.env` 中既有 agent 的 `LLM_*` 變數）。
3. **網路**：與既有 agent 同一個 docker-compose、同一個網路；
   agent 以內部 DNS `http://reasoningbank:8000` 呼叫，不開 host port。

> 三點已確認。待使用者提供 embedding key/model 名稱與 LLM endpoint/key/model
> 即可進入實作。
