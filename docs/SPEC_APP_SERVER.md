# Conclave 應用伺服器與前端實作規格書（SPEC_APP_SERVER）

> **定位**：本規格書對應「第二人」——軟體邏輯 / 狀態機 / 系統邊界 / 前端專長。
> 範圍是 Conclave 的**應用伺服器與前端**：FastAPI 路由 + 狀態機 + SSE 廣播 + 資料存取層 + 前端 SPA。
> 目標是讓一位具備軟體開發與系統可靠性經驗的工程師，透過 agent 從零實作出功能與現在完全相同的應用層，
> 並能與另外兩份（AI 層、基礎設施層）無縫整合。

---

## 與其他兩份規格書的關係

Conclave 系統分為三層，各由一份規格書涵蓋：

| 規格書 | 對應人設 | 範圍 |
|---|---|---|
| **SPEC_AI** | 第一人（AI/LLM/提示工程/隱私推理） | `llm.py`、`mcp.py`、`mcp_tools.py`：分析任務、共識/立場/個人化提問的 LLM 呼叫、研究階段 |
| **SPEC_APP_SERVER**（本文件） | 第二人（軟體邏輯/狀態機/系統邊界/前端） | `main.py`、`routes/`、`db.py`、`broadcast.py`、`tasks.py`、`pin.py`、`models.py`、`frontend/src/` |
| **SPEC_INFRA** | 第三人（基礎設施/部署/資料庫/DevOps） | `config.py`、`migrations/`、`compose.yml`、Dockerfile、環境變數 |

三層之間的接縫在第 0 章精確定義。實作順序建議：infra（資料庫 + 設定）→ app server（本層）→ AI 層。

> **搭配閱讀**：另有 [`SPEC_UNIFIED.md`](SPEC_UNIFIED.md) 整體規格書，提供全域視角——端到端工作流、三層接合地圖、跨層不變量紅線、整合驗證順序。實作自己的部分時，拿本份（細節）+ 整體份（看自己如何嵌進全域、與另兩塊的交集在哪）一起開發，避免走偏。三人實作完成後的整合，見 [`SPEC_INTEGRATION.md`](SPEC_INTEGRATION.md)。

---

## 0. 整合接縫（最重要）

本章是三層整合的合約。實作本層時，假設 AI 層與 infra 層已提供以下介面；實作完成後，依第 11 章檢核表驗證。

### 0.1 本層提供給其他層的介面

#### 0.1.1 db.py — 資料存取 helpers（精確簽名）

所有 helper 皆為 `async`，第一參數為 `pool: asyncpg.Pool`。

| 函式 | 簽名 | 回傳 | 語意 |
|---|---|---|---|
| `get_pool` | `async def get_pool() -> asyncpg.Pool` | `Pool` | 惰性建立連線池（min_size=2, max_size=10），單例 |
| `close_pool` | `async def close_pool() -> None` | `None` | 關閉連線池，重設全域 `_pool=None` |
| `init_db` | `async def init_db(pool: asyncpg.Pool) -> None` | `None` | sorted glob 執行 `migrations/*.sql`，冪等 |
| `get_group` | `async def get_group(pool, pin: str) -> asyncpg.Record \| None` | `Record \| None` | 以 PIN 查群組，顯式欄位（含 `adaptive_questions`），**永不 SELECT \*** |
| `get_group_by_id` | `async def get_group_by_id(pool, group_id) -> asyncpg.Record \| None` | `Record \| None` | 以 UUID 查群組，同上 |
| `try_enter_analyzing` | `async def try_enter_analyzing(pool, group_id) -> bool` | `bool` | 閘門：`collecting\|error → analyzing` |
| `try_open_next_round` | `async def try_open_next_round(pool, group_id) -> bool` | `bool` | 閘門：`done → collecting`（current_round+1，< max_rounds） |
| `try_close_group` | `async def try_close_group(pool, group_id) -> bool` | `bool` | 閘門：`done → closed` |
| `get_submitted_count` | `async def get_submitted_count(pool, group_id, round_number: int \| None = None) -> int` | `int` | round-scoped 回覆計數；`None` 表示當前輪 |
| `get_responses_ordered` | `async def get_responses_ordered(pool, group_id)` | `list[Record]` | 舊版：所有回覆（round-agnostic），向後相容 |
| `get_round_responses_ordered` | `async def get_round_responses_ordered(pool, group_id, round_number: int)` | `list[Record]` | round-scoped 回覆，含 `member_seq` + `participant_id`，ORDER BY `member_seq` |
| `get_round_consensus` | `async def get_round_consensus(pool, group_id, round_number: int) -> asyncpg.Record \| None` | `Record \| None` | 取某輪的 `consensus` + `stance_digest` + `stance_shift_summary` + `research_brief` + `question` |
| `get_rounds_history` | `async def get_rounds_history(pool, group_id)` | `list[Record]` | 全輪歷史，**公開安全欄位**（絕不含 `stance_digest`/`research_brief`） |
| `insert_round` | `async def insert_round(pool, group_id, round_number: int, question: str) -> None` | `None` | 插入新輪行，`ON CONFLICT DO NOTHING` |
| `set_round_done` | `async def set_round_done(pool, group_id, round_number: int, consensus: str, stance_digest: str \| None, stance_shift_summary: str \| None, research_brief: str \| None = None) -> None` | `None` | 事務：寫 `rounds`（consensus+stance_digest+stance_shift_summary+analyzed_at + research_brief）+ `groups`（status=done + consensus） |
| `set_group_status` | `async def set_group_status(pool, group_id, status: str, consensus: str \| None = None) -> None` | `None` | 設群組狀態（含可選 consensus） |
| `set_member_seq` | `async def set_member_seq(pool, group_id, participant_id) -> int` | `int` | 指派 `member_seq = MAX+1`，回傳 seq |
| `get_member_question` | `async def get_member_question(pool, group_id, round_number: int, participant_id) -> str \| None` | `str \| None` | 唯一讀取路徑：成員的個人化問題 |
| `replace_member_questions` | `async def replace_member_questions(pool, group_id, round_number: int, items: list[tuple[uuid.UUID, str]]) -> None` | `None` | 單筆 upsert，`ON CONFLICT DO UPDATE` |
| `clear_member_questions` | `async def clear_member_questions(pool, group_id, round_number: int) -> None` | `None` | 刪除該輪全部 personal questions（覆寫路徑） |
| `get_prior_member_questions` | `async def get_prior_member_questions(pool, group_id, participant_id, up_to_round: int) -> list[tuple[int, str]]` | `list[tuple[int, str]]` | 該成員自己的歷史問題（round < up_to_round） |
| `purge_future_member_questions` | `async def purge_future_member_questions(pool, group_id, current_round: int) -> None` | `None` | 刪除 round > current_round 的列 |
| `prune_delivered_member_questions` | `async def prune_delivered_member_questions(pool, group_id, opened_round: int) -> None` | `None` | 刪除 round < opened_round 的列 |
| `count_member_questions` | `async def count_member_questions(pool, group_id, round_number: int) -> int` | `int` | 計數 only（GET /state 的 `member_question_count`） |

#### 0.1.2 broadcast.py — SSE 廣播

| 函式 | 簽名 | 語意 |
|---|---|---|
| `subscribe` | `def subscribe(pin: str) -> asyncio.Queue` | 建立訂閱佇列（maxsize=16），加入 `_subscribers[pin]` |
| `unsubscribe` | `def unsubscribe(pin: str, q: asyncio.Queue) -> None` | 移除訂閱；空集合時刪 key |
| `broadcast` | `async def broadcast(pin: str, event_type: str, data: dict) -> None` | 廣播事件給該 PIN 的所有訂閱者；慢消費者丟棄 |

#### 0.1.3 pin.py — PIN 產生

| 函式 | 簽名 | 語意 |
|---|---|---|
| `generate_pin` | `def generate_pin() -> str` | 5 碼，`ascii_letters + digits`（62^5 ≈ 916M），`secrets.choice` |

#### 0.1.4 models.py — Pydantic models

見第 9 章。所有 request/response model 均為 Pydantic `BaseModel`。

#### 0.1.5 routes — API endpoints

見第 6 章。所有 router 皆 `APIRouter(prefix="/api")`。

### 0.2 本層依賴 AI 層的介面（全用 late import）

以下函式位於 `llm.py`（AI 層），本層透過 **late import**（函式內 `from ..llm import ...`）呼叫，不在模組頂層 import，以利測試隔離。

| 函式 | 簽名 | 呼叫點 | 語意 |
|---|---|---|---|
| `start_analysis_task` | `def start_analysis_task(pool, group_id) -> asyncio.Task` | `routes/groups.py`（POST /start）、`routes/responses.py`（收齊觸發）、`tasks.py`（deadline 觸發） | 啟動分析任務（tracked fire-and-forget），非 async |
| `generate_next_question` | `async def generate_next_question(prev_consensus: str, prev_question: str) -> str` | `routes/groups.py`（POST /rounds/next） | LLM 生成下一輪問題；失敗時 caller 退回 regex |
| `redact` | `def redact(value: object) -> str` | `routes/groups.py`（LLM 失敗時 log） | 遮蔽祕密值 |
| `shutdown_analysis_tasks` | `async def shutdown_analysis_tasks(timeout: float = 10.0) -> None` | `main.py`（lifespan shutdown） | 取消並等待在飛分析任務 |

### 0.3 本層依賴 infra 層的介面

| 項目 | 來源 | 語意 |
|---|---|---|
| `get_settings()` | `config.py`（`@lru_cache`） | 回傳 `Settings` 實例，含 `database_url`、`sse_keepalive_s`、`deadline_scan_s`、`adaptive_questions_enabled`、`adaptive_p_max_members` 等 |
| `migrations/*.sql` | `migrations/` | 提供 schema（groups/participants/responses/rounds/member_questions） |
| `compose.yml` | infra 層 | 提供執行環境（postgres:16、uvicorn、vite dev） |

### 0.4 並發閘門是本層核心

**所有狀態轉移用「條件式 UPDATE + rowcount == "UPDATE 1"」原子閘門**，不用先 SELECT 後判斷。

這是本層最重要的設計不變量：

- `try_enter_analyzing`：`UPDATE groups SET status='analyzing' WHERE id=$1 AND status IN ('collecting','error')` → `result == "UPDATE 1"`
- `try_open_next_round`：`UPDATE groups SET status='collecting', current_round=current_round+1 WHERE id=$1 AND status='done' AND current_round < max_rounds` → `result == "UPDATE 1"`
- `try_close_group`：`UPDATE groups SET status='closed' WHERE id=$1 AND status='done'` → `result == "UPDATE 1"`
- 收齊觸發：`UPDATE groups SET status='analyzing' WHERE id=$1 AND status='collecting'` → `result == "UPDATE 1"`

原子性保證：若條件不成立（已被其他路徑搶先），`rowcount` 為 0，`triggered=False`，不會啟動分析。三條觸發路徑（收齊/手動 start/deadline）收斂到同一個 `try_enter_analyzing`，天然互斥。

---

## 1. 定位與檔案範圍

### 1.1 後端檔案

| 檔案 | 職責 |
|---|---|
| `backend/app/main.py` | FastAPI app 構造、lifespan、router 註冊、`/api/health` |
| `backend/app/routes/groups.py` | 群組 CRUD、狀態操作、多輪、adaptive 端點 |
| `backend/app/routes/responses.py` | 回覆提交、收齊觸發 |
| `backend/app/routes/events.py` | SSE EventSource |
| `backend/app/db.py` | 資料存取層（所有 helper + 連線池 + migration 執行） |
| `backend/app/broadcast.py` | SSE 訂閱/廣播（行程內） |
| `backend/app/tasks.py` | deadline 掃描迴圈 + auto-close |
| `backend/app/pin.py` | PIN 產生 |
| `backend/app/models.py` | Pydantic request/response models |

### 1.2 前端檔案

| 檔案 | 職責 |
|---|---|
| `frontend/src/main.tsx` | React 入口、BrowserRouter |
| `frontend/src/App.tsx` | 路由表、主題切換 |
| `frontend/src/lib/api.ts` | API 函式 + 型別介面 + `ApiError` |
| `frontend/src/hooks/useGroupSSE.ts` | SSE 連線 + 事件分派 |
| `frontend/src/hooks/useTypewriter.ts` | 打字機動畫 |
| `frontend/src/pages/Home.tsx` | 首頁（建立/加入入口） |
| `frontend/src/pages/Create.tsx` | 建立群組表單 |
| `frontend/src/pages/Group.tsx` | 群組主畫面（useReducer 狀態機） |

### 1.3 不在本層範圍

- `backend/app/llm.py`、`mcp.py`、`mcp_tools.py` → AI 層
- `backend/app/config.py`、`migrations/`、`compose.yml` → infra 層

---

## 2. 應用生命週期（main.py）

### 2.1 lifespan 完整流程

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
```

| 步驟 | 動作 | 細節 |
|---|---|---|
| 1 | WEB_CONCURRENCY 警告 | `os.environ.get("WEB_CONCURRENCY", "1")`；`int(wc) > 1` → 印 `WARN: in-process SSE broadcast does not cross workers; use Redis or single worker`；`ValueError` 靜默 |
| 2 | `pool = await get_pool()` | 建立連線池 |
| 3 | `await init_db(pool)` | 執行所有 migrations（sorted glob） |
| 4 | Startup sweep | `UPDATE groups SET status='error' WHERE status='analyzing'`；若 `result != "UPDATE 0"` → 印 `WARN: recovered N stranded 'analyzing' group(s) -> error`。崩潰遺留的 analyzing 群組無法恢復（其 task 已消失），翻轉為 error 讓建立者可經 POST /start 重試 |
| 5 | `scan = asyncio.create_task(deadline_scan_loop(pool))` | 啟動背景掃描 |
| 6 | `yield` | FastAPI 服務期 |
| 7 | `scan.cancel()` | 停止掃描 |
| 8 | `from .llm import shutdown_analysis_tasks` → `await shutdown_analysis_tasks()` | late import；取消在飛分析任務 |
| 9 | `await close_pool()` | 關閉連線池 |

### 2.2 App 構造

```python
app = FastAPI(title="Conclave", lifespan=lifespan)
```

### 2.3 健康檢查

```python
@app.get("/api/health")
async def health():
    return {"ok": True}
```

### 2.4 Router 註冊順序

```python
from .routes import groups, events, responses
app.include_router(groups.router)
app.include_router(events.router)
app.include_router(responses.router)
```

順序不影響路由解析（路徑不衝突），但 groups.router 含最多端點。所有 router prefix 為 `/api`。

---

## 3. 資料存取層（db.py）

### 3.1 連線池與初始化

```python
_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    # min_size=2, max_size=10，dsn=get_settings().database_url

async def init_db(pool: asyncpg.Pool) -> None:
    # migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    # for sql_path in sorted(migrations_dir.glob("*.sql")):
    #     await pool.execute(sql_path.read_text(encoding="utf-8"))
    # sorted: 001 < 002 < 003；全部冪等

async def close_pool() -> None:
    # await _pool.close(); _pool = None
```

### 3.2 群組查詢

#### `get_group(pool, pin: str) -> asyncpg.Record | None`

```sql
SELECT id, pin, question, creator_token, expected_count, deadline,
       status, consensus, created_at, current_round, max_rounds,
       adaptive_questions
FROM groups WHERE pin=$1
```

隱私要點：**顯式欄位列表，永不 `SELECT *`**。未來新增的 private 欄位（stance_digest 相鄰）不會意外進入應用記憶體。

#### `get_group_by_id(pool, group_id) -> asyncpg.Record | None`

同上欄位列表，`WHERE id=$1`。

### 3.3 狀態機閘門

三個閘門的共同模式：條件式 `UPDATE` + `result == "UPDATE 1"` → `bool`。

#### `try_enter_analyzing(pool, group_id) -> bool`

```sql
UPDATE groups SET status='analyzing' WHERE id=$1 AND status IN ('collecting','error')
```

- `collecting` 或 `error` → `analyzing`
- 回傳 `result == "UPDATE 1"`

#### `try_open_next_round(pool, group_id) -> bool`

```sql
UPDATE groups SET status='collecting', current_round=current_round+1
WHERE id=$1 AND status='done' AND current_round < max_rounds
```

- `done` → `collecting`（current_round + 1）
- 條件含 `current_round < max_rounds`
- 這是唯一從 `done → collecting` 的函式

#### `try_close_group(pool, group_id) -> bool`

```sql
UPDATE groups SET status='closed' WHERE id=$1 AND status='done'
```

- `done` → `closed`（終態）
- 回傳 `result == "UPDATE 1"`

### 3.4 Round-scoped 讀取

#### `get_submitted_count(pool, group_id, round_number: int | None = None) -> int`

- `round_number=None`（當前輪）：
  ```sql
  SELECT count(*) FROM responses r
  JOIN groups g ON g.id=r.group_id
  WHERE r.group_id=$1 AND r.round_number=g.current_round
  ```
- 指定 round_number：
  ```sql
  SELECT count(*) FROM responses WHERE group_id=$1 AND round_number=$2
  ```

#### `get_responses_ordered(pool, group_id)` — 舊版

```sql
SELECT content FROM responses WHERE group_id=$1 ORDER BY submitted_at
```

round-agnostic，向後相容。

#### `get_round_responses_ordered(pool, group_id, round_number: int)`

```sql
SELECT r.content, p.member_seq, r.participant_id
FROM responses r JOIN participants p ON r.participant_id=p.id
WHERE r.group_id=$1 AND r.round_number=$2
ORDER BY p.member_seq, r.submitted_at
```

隱私要點：含 `member_seq`（供 stance call 的穩定標籤）+ `participant_id`（供 adaptive P stage 成員列舉）。ORDER BY `member_seq` 確保 stance 標籤（成員{member_seq}）決定性。

#### `get_round_consensus(pool, group_id, round_number: int) -> asyncpg.Record | None`

```sql
SELECT consensus, stance_digest, stance_shift_summary, research_brief, question
FROM rounds WHERE group_id=$1 AND round_number=$2
```

回傳 None 若該輪不存在。含**私有欄位** `stance_digest` + `research_brief`——只供 AI 層的 cross-round context 與 Jaccard cost control 使用。

#### `get_rounds_history(pool, group_id)`

```sql
SELECT round_number, question, consensus, stance_shift_summary,
       created_at, analyzed_at
FROM rounds WHERE group_id=$1 ORDER BY round_number
```

隱私要點：**公開安全欄位 only**——絕不含 `stance_digest` 或 `research_brief`。用於 GET /rounds。

### 3.5 Round 寫入

#### `insert_round(pool, group_id, round_number: int, question: str) -> None`

```sql
INSERT INTO rounds (group_id, round_number, question) VALUES ($1, $2, $3)
ON CONFLICT (group_id, round_number) DO NOTHING
```

#### `set_round_done(pool, group_id, round_number, consensus, stance_digest, stance_shift_summary, research_brief=None) -> None`

單一事務（參數編號以實際 execute 呼叫為準，下方用具名佔位符避免混淆）：

```sql
BEGIN;
-- 1. 寫 rounds（consensus / stance_digest / stance_shift_summary / analyzed_at）
UPDATE rounds SET consensus=:consensus, stance_digest=:stance_digest,
       stance_shift_summary=:shift, analyzed_at=now()
  WHERE group_id=:gid AND round_number=:rnd;
-- 2. 寫 research_brief（僅當 research_brief is not None，另一次 execute）
UPDATE rounds SET research_brief=:brief
  WHERE group_id=:gid AND round_number=:rnd;
-- 3. 寫 groups（公開 latest consensus + status=done）
UPDATE groups SET status='done', consensus=:consensus WHERE id=:gid;
COMMIT;
```

> 實作時：三條 UPDATE 在同一 `async with conn.transaction()` 內，用 asyncpg 的 `$1..$N` 位置參數分別傳值（**勿將同一編號複用於不同值**——第二條的 research_brief 用獨立參數編號）。

隱私要點：**`stance_digest` 只寫 `rounds`，絕不寫 `groups`**。`groups.consensus` 是公開的 latest consensus（向後相容）。

### 3.6 群組狀態

#### `set_group_status(pool, group_id, status: str, consensus: str | None = None) -> None`

- `consensus is not None`：`UPDATE groups SET status=$2, consensus=$3 WHERE id=$1`
- 否則：`UPDATE groups SET status=$2 WHERE id=$1`

#### `set_member_seq(pool, group_id, participant_id) -> int`

```sql
-- $1 = group_id（用於 MAX 查詢）
SELECT COALESCE(MAX(member_seq), 0) + 1 FROM participants WHERE group_id=$1;
-- $2 = 新 seq, $3 = participant_id（注意：與上一句的 $1 不同參數）
UPDATE participants SET member_seq=$2 WHERE id=$3;
```

回傳指派的 seq。分兩步是為了讓 `UNIQUE(group_id, member_seq)` 的 race window 最小。

### 3.7 member_questions helpers（ADAPTIVE_SPEC §5.1）

敏感度層級：介於 consensus（全員可見）與 stance_digest（無人可見）之間——**只對收件者可見**。絕不進入 SSE 廣播、GET /rounds、GET /state payload。

#### `get_member_question(pool, group_id, round_number, participant_id) -> str | None`

```sql
SELECT question FROM member_questions
WHERE group_id=$1 AND round_number=$2 AND participant_id=$3
```

唯一讀取路徑（`/my-question`）。

#### `replace_member_questions(pool, group_id, round_number, items: list[tuple[uuid.UUID, str]]) -> None`

```sql
INSERT INTO member_questions (group_id, round_number, participant_id, question)
VALUES ($1, $2, $3, $4)
ON CONFLICT (group_id, round_number, participant_id) DO UPDATE SET question = EXCLUDED.question
```

- `items` 為空時直接 return
- `executemany` 批次 upsert
- 呼叫粒度：一次 P_X 成功 → 一筆 item

#### `clear_member_questions(pool, group_id, round_number: int) -> None`

```sql
DELETE FROM member_questions WHERE group_id=$1 AND round_number=$2
```

建立者覆寫路徑：刪除該輪**全部** personal questions。必須在 `try_open_next_round` 閘門成功後執行。

#### `get_prior_member_questions(pool, group_id, participant_id, up_to_round: int) -> list[tuple[int, str]]`

```sql
SELECT round_number, question FROM member_questions
WHERE group_id=$1 AND participant_id=$2 AND round_number < $3
ORDER BY round_number
```

只回**該成員自己**的歷史問題（round < up_to_round）。餵入該成員自己的 P_X 作為私有輸入。絕不回傳整組的問題。

#### `purge_future_member_questions(pool, group_id, current_round: int) -> None`

```sql
DELETE FROM member_questions WHERE group_id=$1 AND round_number > $2
```

群組終止時呼叫（try_close_group 成功、24h auto-close、建立者解散 via /leave）。

#### `prune_delivered_member_questions(pool, group_id, opened_round: int) -> None`

```sql
DELETE FROM member_questions WHERE group_id=$1 AND round_number < $2
```

成功開輪後呼叫：只有當前輪的列存活——已投遞的歷史不保留。

#### `count_member_questions(pool, group_id, round_number: int) -> int`

```sql
SELECT count(*) FROM member_questions WHERE group_id=$1 AND round_number=$2
```

計數 only（GET /state 的 `member_question_count`）——無內容面。

---

## 4. SSE 廣播（broadcast.py）

### 4.1 資料結構

```python
_subscribers: dict[str, set[Queue]] = {}
```

- key: `pin`（str）
- value: `set[Queue]`——同一 PIN 的所有訂閱者佇列

### 4.2 函式

#### `subscribe(pin: str) -> Queue`

```python
q: Queue = Queue(maxsize=16)
_subscribers.setdefault(pin, set()).add(q)
return q
```

#### `unsubscribe(pin: str, q: Queue) -> None`

```python
subs = _subscribers.get(pin)
if subs:
    subs.discard(q)
    if not subs:
        del _subscribers[pin]
```

空集合時刪 key，避免記憶體洩漏。

#### `broadcast(pin: str, event_type: str, data: dict) -> None`

```python
msg = {"event": event_type, "data": data}
for q in list(_subscribers.get(pin, ())):  # list() 快照遍歷
    try:
        q.put_nowait(msg)
    except QueueFull:
        unsubscribe(pin, q)  # 丟棄慢消費者；EventSource 會重連
```

關鍵設計：
- `Queue(maxsize=16)`：固定容量，背壓保護
- `put_nowait`：非阻塞；滿則 `QueueFull`
- `QueueFull → unsubscribe`：慢消費者直接斷線，前端 `EventSource` 會自動重連並觸發 `onDisconnect → fetchState`
- `list()` 快照：遍歷時不受 `unsubscribe` 的 `del` 影響

### 4.3 限制

- **行程內廣播**：不跨 worker。`WEB_CONCURRENCY > 1` 時 lifespan 會警告。多 worker 需 Redis pub/sub（未實作）。

---

## 5. 背景任務（tasks.py）

### 5.1 deadline_scan_loop

```python
async def deadline_scan_loop(pool) -> None:
```

| 階段 | 條件 | 動作 |
|---|---|---|
| 查 deadline 到期 | `status='collecting' AND deadline IS NOT NULL AND deadline < now()` | 取 `id, pin` |
| 零回覆守衛 | `sc == 0`（`get_submitted_count`） | `broadcast(pin, "error", {"message": "Deadline reached with no opinions"})`；**保持 collecting**（不觸發分析）；`continue` |
| 正常觸發 | `sc > 0` | `try_enter_analyzing(pool, gid)` → 成功則 `broadcast(pin, "phase", {"status": "analyzing"})` + `start_analysis_task(pool, gid)` |
| Auto-close | `status='done'` 且 `r.analyzed_at IS NOT NULL AND r.analyzed_at < now() - 24h` | `try_close_group` → 成功則 `purge_future_member_questions` + `broadcast(pin, "round", {"round": 0, "question": "", "status": "closed"})` |

### 5.2 迴圈結構

```python
while True:
    try:
        # deadline 掃描 + auto-close
    except Exception:
        log.exception("deadline scan iteration failed")
    await asyncio.sleep(s.deadline_scan_s)
```

- **整輪 `try/except` 不殺迴圈**：任何例外只記 log，不中斷掃描
- 間隔：`s.deadline_scan_s`（預設 5s）
- late import：`from .llm import start_analysis_task`（§2 可測試性）；`from .db import try_close_group, purge_future_member_questions`

### 5.3 常數

```python
AUTO_CLOSE_HOURS = 24  # done > 24h → closed
```

### 5.4 Auto-close 查詢

```sql
SELECT g.id, g.pin, g.current_round FROM groups g
JOIN rounds r ON r.group_id = g.id AND r.round_number = g.current_round
WHERE g.status='done' AND r.analyzed_at IS NOT NULL AND r.analyzed_at < $1
```

`$1 = datetime.now(timezone.utc) - timedelta(hours=24)`

---

## 6. API 端點完整表（routes/）

### 6.1 端點總表

| # | 方法 | 路徑 | Request Model | Response | 狀態碼 | 說明 |
|---|---|---|---|---|---|---|
| 1 | POST | `/api/groups` | `CreateGroupRequest` | `CreateGroupResponse` | 201 | 建立群組 |
| 2 | POST | `/api/groups/{pin}/join` | `JoinRequest` | `JoinResponse` | 200 | 加入群組 |
| 3 | GET | `/api/groups/{pin}/state` | —（query: `token?`） | `GroupState` | 200 | 群組狀態 |
| 4 | POST | `/api/groups/{pin}/start` | `StartRequest` | `{"ok": True}` | 202 | 手動開始分析 |
| 5 | POST | `/api/groups/{pin}/leave` | `LeaveRequest` | `{"ok": True, "dissolved": bool}` | 200 | 退出/解散 |
| 6 | POST | `/api/groups/{pin}/responses` | `SubmitResponseRequest` | `{"ok": True}` | 200 | 提交回覆 |
| 7 | POST | `/api/groups/{pin}/rounds/next` | `OpenRoundRequest` | `{"round": int, "question": str}` | 202 | 開啟下一輪 |
| 8 | POST | `/api/groups/{pin}/rounds/close` | `CloseRoundRequest` | `{"ok": True}` | 200 | 結束討論 |
| 9 | GET | `/api/groups/{pin}/rounds` | — | `list[RoundInfo]` | 200 | 輪次歷史 |
| 10 | GET | `/api/groups/{pin}/my-question` | —（query: `participant_id?`, `round?`） | `MyQuestionResponse` | 200/404 | 個人化問題 |
| 11 | GET | `/api/events/{pin}` | — | SSE stream | 200 | SSE 事件流 |
| 12 | GET | `/api/health` | — | `{"ok": True}` | 200 | 健康檢查 |

### 6.2 逐端點詳解

#### 6.2.1 POST /groups（201）

- **Request**: `CreateGroupRequest`（question, creator_nickname, expected_count?, timeout_seconds?, max_rounds=3, adaptive_questions=False）
- **Response**: `CreateGroupResponse`（pin, participant_id, creator_token, status="collecting"）
- **流程**:
  1. `creator_token = secrets.token_urlsafe(16)`
  2. `deadline = now + timeout_seconds`（若 timeout_seconds is not None）
  3. PIN 重試 3 次迴圈：
     - `pin = generate_pin()`
     - 單一連線內事務：`INSERT INTO groups (...) RETURNING id` → `INSERT INTO participants (group_id, nickname, member_seq=1) RETURNING id` → `INSERT INTO rounds (group_id, 1, question) ON CONFLICT DO NOTHING`
     - `asyncpg.UniqueViolationError` → `continue`（PIN 碰撞重試）
  4. 3 次失敗 → `HTTPException(500, "PIN collision after retries")`
- **關鍵**: 建立者 member_seq=1；round 1 種子行立即插入（歷史時間線從第一輪開始）

#### 6.2.2 POST /groups/{pin}/join

- **Request**: `JoinRequest`（nickname）
- **Response**: `JoinResponse`（participant_id, status）
- **錯誤**: 404 "Group not found" / 409 "Nickname already taken in this group"
- **流程**:
  1. `SELECT id, status FROM groups WHERE pin=$1` → 404 if None
  2. `INSERT INTO participants (group_id, nickname) RETURNING id` → `UniqueViolationError` = 409
  3. `from ..db import set_member_seq` → `await set_member_seq(pool, g["id"], p["id"])`（指派穩定 member_seq）
  4. `pc = _count(participants)`, `sc = _count(responses)` → `broadcast(pin, "progress", {"participant_count": pc, "submitted_count": sc})`
- **隱私**: member_seq 分兩步（先 INSERT 再 MAX+1）以縮小 UNIQUE race window

#### 6.2.3 GET /groups/{pin}/state

- **Query**: `token: str | None`（建立者 token）
- **Response**: `GroupState`
- **錯誤**: 404 "Group not found"
- **流程**:
  1. 顯式欄位 SELECT（id, pin, question, creator_token, expected_count, deadline, status, consensus, current_round, max_rounds, adaptive_questions）→ 404 if None
  2. `pc = count(participants)`, `sc = count(responses WHERE round_number=current_round)`
  3. `is_creator = token is not None and token == g["creator_token"]`
  4. 冷卻計算：`status == "done" and current_round < max_rounds` → 查 `rounds.analyzed_at` → `elapsed = now - analyzed_at` → `cooldown_remaining = max(0, NEXT_ROUND_COOLDOWN_S - int(elapsed))`；否則 `None`
  5. Adaptive effective flag：`effective = get_settings().adaptive_questions_enabled and g["adaptive_questions"]`
  6. `member_question_count`：`effective and status == "done" and current_round < max_rounds` → `count(member_questions WHERE round_number=current_round + 1)`；否則 `None`
  7. 回傳 `GroupState`（adaptive_questions=effective, member_question_count）

#### 6.2.4 POST /groups/{pin}/start（202）

- **Request**: `StartRequest`（creator_token）
- **錯誤**: 404 / 403 "Not the creator" / 409 "Already analyzing or done" / 409 "No opinions yet"
- **流程**:
  1. `SELECT id, creator_token, status, current_round FROM groups WHERE pin=$1` → 404
  2. token 比對 → 403
  3. `status not in ("collecting", "error")` → 409
  4. `sc = count(responses WHERE round_number=current_round)` → `sc == 0` → 409
  5. `triggered = await try_enter_analyzing(pool, group_id)`
  6. `if triggered`: `broadcast(pin, "phase", {"status": "analyzing", "round": current_round})` + `from ..llm import start_analysis_task` + `start_analysis_task(pool, group_id)`
  7. 回傳 `{"ok": True}`

#### 6.2.5 POST /groups/{pin}/leave

- **Request**: `LeaveRequest`（participant_id, creator_token?）
- **Response**: `{"ok": True, "dissolved": bool}`
- **錯誤**: 404 / 409 "Group already ended" / 404 "Participant not found"
- **流程**:
  1. `SELECT id, status, creator_token, current_round FROM groups WHERE pin=$1` → 404
  2. `status not in ("collecting", "done")` → 409
  3. `SELECT id FROM participants WHERE id=$1 AND group_id=$2` → 404 if None
  4. `is_creator = req.creator_token is not None and req.creator_token == g["creator_token"]`
  5. **建立者解散**：
     - `UPDATE groups SET status='closed', consensus='群組已由建立者解散' WHERE id=$1`
     - `DELETE FROM participants WHERE id=$1`（CASCADE 移除 responses + member_questions）
  6. **非建立者退出**：
     - `DELETE FROM participants WHERE id=$1`
  7. if `is_creator`: `purge_future_member_questions(pool, g["id"], current_round=g["current_round"])` + `broadcast(pin, "consensus", {"content": "群組已由建立者解散"})` + `broadcast(pin, "round", {"round": 0, "question": "", "status": "closed"})`
  8. else: `pc = _count(participants)`, `sc = _count(responses)` → `broadcast(pin, "progress", {"participant_count": pc, "submitted_count": sc})`

#### 6.2.6 POST /groups/{pin}/responses

- **Request**: `SubmitResponseRequest`（participant_id, content）
- **Response**: `{"ok": True}`
- **錯誤**: 404 "Group not found" / 409 "Submissions closed" / 409 "Already submitted this round"
- **流程**（收齊觸發，核心路徑）:
  1. `SELECT id, status, expected_count, current_round FROM groups WHERE pin=$1` → 404
  2. `status != "collecting"` → 409 "Submissions closed"
  3. `INSERT INTO responses (group_id, round_number, participant_id, content)` → `UniqueViolationError` = 409 "Already submitted this round"
  4. **收齊觸發（同一事務內）**：
     ```sql
     SELECT status FROM groups WHERE id=$1 FOR UPDATE  -- 行鎖
     ```
     - `row["status"] == "collecting" and expected_count is not None`：
       ```sql
       SELECT count(*) FROM responses WHERE group_id=$1 AND round_number=$2
       ```
       - `sc >= expected_count`：
         ```sql
         UPDATE groups SET status='analyzing' WHERE id=$1 AND status='collecting'
         ```
         - `triggered = (result == "UPDATE 1")`
  5. `pc = _count(participants)`, `sc = _round_count(current_round)` → `broadcast(pin, "progress", {"participant_count": pc, "submitted_count": sc, "round": current_round})`
  6. `if triggered`: `broadcast(pin, "phase", {"status": "analyzing", "round": current_round})` + `from ..llm import start_analysis_task` + `start_analysis_task(pool, g["id"])`
- **關鍵**: `FOR UPDATE` 行鎖 + 條件 `UPDATE` 在同一事務內，確保只有一個請求能成功觸發

#### 6.2.7 POST /groups/{pin}/rounds/next（202）

- **Request**: `OpenRoundRequest`（creator_token, question?, timeout_seconds?）
- **Response**: `{"round": int, "question": str}`
- **錯誤**: 404 / 403 / 409 "Round not done yet" / 409 "已達回合上限" / 409 "冷卻中,請稍候(Ns)" / 409 "Could not open next round (race or gate)"
- **常數**: `NEXT_ROUND_COOLDOWN_S = 10`
- **流程**:
  1. `SELECT id, creator_token, status, current_round, max_rounds, question, adaptive_questions FROM groups WHERE pin=$1` → 404
  2. token 比對 → 403
  3. `status != "done"` → 409
  4. `current_round >= max_rounds` → 409 "已達回合上限"
  5. 冷卻檢查：查 `rounds.analyzed_at`（當前輪）→ `elapsed < NEXT_ROUND_COOLDOWN_S` → 409 `"冷卻中,請稍候({int(NEXT_ROUND_COOLDOWN_S - elapsed)}s)"`
  6. **決定下一輪問題（覆寫階梯，ADAPTIVE_SPEC §4.3）**：
     - `prev_round = get_round_consensus(pool, group_id, current_round)` → `prev_consensus = prev_round["consensus"] if prev_round else None`
     - `raw_q = (req.question or "").strip()`
     - `override = bool(raw_q)`
     - **覆寫分支**：
       - `override`：`next_question = raw_q`（不呼叫 Call Q）
       - `elif prev_consensus`：`from ..llm import generate_next_question, redact` → `next_question = await generate_next_question(prev_consensus, g["question"])`；例外 → `log.warning("LLM question-gen failed, using regex fallback: %s", redact(str(e)))` + `next_question = _seed_next_question(prev_consensus, g["question"])`
       - `else`：`next_question = _seed_next_question(prev_consensus, g["question"])`
  7. `new_deadline = now + timeout_seconds`（若提供）；否則 None（清除 deadline）
  8. **單一事務**（INSERT rounds + gate UPDATE + override clear）：
     ```python
     async with conn.transaction():
         INSERT INTO rounds (group_id, round_number, question) VALUES (...)
         ON CONFLICT (group_id, round_number) DO NOTHING
         result = UPDATE groups SET status='collecting', current_round=$2,
               question=$3, deadline=$4
               WHERE id=$1 AND status='done' AND current_round < max_rounds
         triggered = (result == "UPDATE 1")
         if triggered and override:
             from ..db import clear_member_questions
             await clear_member_questions(pool, group_id, next_round)
     ```
     - **gate 失敗回滾不留懸空列**：事務保證 INSERT + UPDATE 原子；gate 失敗時整個事務回滾
  9. `if not triggered`: 409 "Could not open next round (race or gate)"
  10. `prune_delivered_member_questions(pool, group_id, opened_round=next_round)`
  11. `adaptive_effective = get_settings().adaptive_questions_enabled and g["adaptive_questions"]`
  12. `broadcast(pin, "round", {"round": next_round, "question": next_question, "status": "opened", "adaptive": adaptive_effective})`
  13. 回傳 `{"round": next_round, "question": next_question}`

#### 6.2.8 POST /groups/{pin}/rounds/close

- **Request**: `CloseRoundRequest`（creator_token）
- **Response**: `{"ok": True}`
- **錯誤**: 404 / 403 / 409 "Round not done yet" / 409 "Could not close (race or gate)"
- **流程**:
  1. `SELECT id, creator_token, status, current_round FROM groups WHERE pin=$1` → 404
  2. token 比對 → 403
  3. `status != "done"` → 409
  4. `triggered = await try_close_group(pool, group_id)` → `if not triggered`: 409
  5. `purge_future_member_questions(pool, group_id, current_round=g["current_round"])`
  6. `broadcast(pin, "round", {"round": 0, "question": "", "status": "closed"})`

#### 6.2.9 GET /groups/{pin}/rounds

- **Response**: `list[RoundInfo]`
- **錯誤**: 404 "Group not found"
- **流程**:
  1. `SELECT id FROM groups WHERE pin=$1` → 404
  2. `rows = get_rounds_history(pool, g["id"])`（公開安全欄位）
  3. 映射為 `RoundInfo` list

#### 6.2.10 GET /groups/{pin}/my-question

- **Query**: `participant_id: str | None`, `round: int | None`
- **Response**: `MyQuestionResponse`（round, question, is_personal）
- **錯誤**: 統一 404 `"not found"`（`_NOT_FOUND` 常數）
- **流程**（ADAPTIVE_SPEC §7.2 統一 404 規則）:
  1. `SELECT id, question, status, current_round, max_rounds, adaptive_questions FROM groups WHERE pin=$1`
  2. `effective = g is not None and s.adaptive_questions_enabled and g["adaptive_questions"]`
  3. `if g is None or not effective or participant_id is None`: 404 `"not found"`
  4. `r = round if round is not None else current_round`
  5. `if r < 1 or r > current_round + 1`: 404 `"not found"`
  6. `if participant_id is not None`:
     - `mq = get_member_question(pool, g["id"], r, participant_id)`
     - `if mq is not None`: 回傳 `MyQuestionResponse(round=r, question=mq, is_personal=True)`
  7. **無 personal row → anchor**：
     - `r == current_round`：`anchor = g["question"]`（當前輪共用問題）
     - `r < current_round`：`anchor = SELECT question FROM rounds WHERE round_number=r` → None 則 404
     - `r == current_round + 1`：`anchor = ""`（下一輪未開，anchor 未生成）
  8. 回傳 `MyQuestionResponse(round=r, question=anchor, is_personal=False)`
- **關鍵規則**：
  - 群組不存在 / 旗標關 / round 越界 / 缺 pid → **統一 404 "not found"**
  - 有 row → `is_personal=True`
  - 無 row（有效 pid）→ anchor + `is_personal=False`（**非 404**）
  - 無效 pid → anchor + `is_personal=False`（與有效 pid 無 row 不可區分——一位元 oracle 縮減為「需有效 UUID 先決」）
  - round 範圍：`1..current_round+1`

#### 6.2.11 GET /events/{pin}

- **Response**: `EventSourceResponse`（SSE）
- **流程**:
  1. `settings = get_settings()`
  2. `q = subscribe(pin)`
  3. `generate()` async generator：
     ```python
     try:
         while True:
             msg = await q.get()
             yield {"event": msg["event"], "data": json.dumps(msg["data"])}
     finally:
         unsubscribe(pin, q)
     ```
  4. `return EventSourceResponse(generate(), ping=settings.sse_keepalive_s)`
- **關鍵**: `finally` 保證 unsubscribe；`ping` 為 keepalive 間隔（預設 15s）

### 6.3 輔助函式

#### `_seed_next_question(prev_consensus: str | None, prev_question: str) -> str`

- `prev_consensus` 為 None → 回 `prev_question`
- regex 搜尋 `"未解分歧|建議"` → 無 → 回 `prev_question`
- 取標題後至下一個 `#{1,6}\s` 標題的文字區塊
- strip `：:。\n\r\t `
- `block[:200]` if block else `prev_question`

#### `_count(pool, table, group_id) -> int`

```sql
SELECT count(*) FROM {table} WHERE group_id=$1
```

注意：`table` 以 f-string 插入——只由信任的 caller 呼叫（`"participants"` / `"responses"`）。

---

## 7. 狀態機與並發閘門

### 7.1 狀態圖

```
collecting ──→ analyzing ──→ done ──→ collecting (next round, current_round+1)
    │            ↑           │ ──→ closed (建立者結束 / 24h auto-close)
    │            │           │
    │         error          │
    │            ↑           │
    └────────────┘           └→ closed (建立者解散 via /leave)
     (deadline 0 回覆守衛      └→ closed (建立者解散 via /leave)
      不觸發轉移,保持 collecting)
```

| 轉移 | 觸發 | 閘門函式 |
|---|---|---|
| `collecting → analyzing` | 收齊 / 手動 start / deadline(sc>0) | `try_enter_analyzing` |
| `error → analyzing` | 手動 start（重試） | `try_enter_analyzing` |
| `analyzing → done` | AI 層 `set_round_done` | `set_round_done`（事務內 `groups.status='done'`） |
| `analyzing → error` | AI 層失敗 | `set_group_status(pool, gid, "error")` |
| `done → collecting` | POST /rounds/next | `try_open_next_round`（單一事務內 gate） |
| `done → closed` | POST /rounds/close / 24h auto-close / 建立者解散 | `try_close_group` |
| `collecting → closed` | 建立者解散 via /leave | 直接 `UPDATE groups SET status='closed'` |
| `done → closed` | 建立者解散 via /leave | 直接 `UPDATE groups SET status='closed'` |

### 7.2 並發閘門模式表

| 閘門 | SQL 條件 | rowcount 判定 | 來源 |
|---|---|---|---|
| `try_enter_analyzing` | `WHERE id=$1 AND status IN ('collecting','error')` | `result == "UPDATE 1"` | db.py |
| `try_open_next_round` | `WHERE id=$1 AND status='done' AND current_round < max_rounds` | `result == "UPDATE 1"` | db.py |
| `try_close_group` | `WHERE id=$1 AND status='done'` | `result == "UPDATE 1"` | db.py |
| 收齊觸發 | `WHERE id=$1 AND status='collecting'`（FOR UPDATE 行鎖後） | `result == "UPDATE 1"` | routes/responses.py |
| open_next_round 事務 gate | `WHERE id=$1 AND status='done' AND current_round < max_rounds` | `result == "UPDATE 1"` | routes/groups.py |

### 7.3 三條觸發路徑收斂

三條路徑都呼叫同一個 `try_enter_analyzing`，天然互斥：

| 路徑 | 來源 | 閘門 |
|---|---|---|
| 收齊觸發 | `POST /responses` | `try_enter_analyzing`（via 條件 UPDATE） |
| 手動 start | `POST /start` | `try_enter_analyzing` |
| deadline | `deadline_scan_loop` | `try_enter_analyzing` |

只有第一個到達的會得到 `result == "UPDATE 1"`（`triggered=True`），其餘得到 `UPDATE 0`（`triggered=False`），不會重複啟動分析。

### 7.4 收齊的 FOR UPDATE 行鎖

```python
# routes/responses.py — 同一事務內
await conn.execute("INSERT INTO responses ...")  # 1. 插入回覆
row = await conn.fetchrow("SELECT status FROM groups WHERE id=$1 FOR UPDATE", g["id"])  # 2. 行鎖
if row["status"] == "collecting" and g["expected_count"] is not None:
    sc = await conn.fetchval("SELECT count(*) ...")  # 3. 計數
    if sc >= g["expected_count"]:
        result = await conn.execute(
            "UPDATE groups SET status='analyzing' WHERE id=$1 AND status='collecting'", ...)  # 4. 條件 UPDATE
        triggered = (result == "UPDATE 1")
```

- `FOR UPDATE` 行鎖：序列化對同一 group 的並發收齊判斷
- 條件 `UPDATE`：再次檢查 `status='collecting'`（雙重保險）
- `result == "UPDATE 1"`：只有一個請求成功觸發

### 7.5 open_next_round 單一事務

```python
async with conn.transaction():
    await conn.execute("INSERT INTO rounds (...) ON CONFLICT DO NOTHING", ...)
    result = await conn.execute(
        "UPDATE groups SET status='collecting', current_round=$2, question=$3, deadline=$4 "
        "WHERE id=$1 AND status='done' AND current_round < max_rounds", ...)
    triggered = (result == "UPDATE 1")
    if triggered and override:
        await clear_member_questions(pool, group_id, next_round)
```

- INSERT + gate UPDATE + override clear 在同一事務內
- **gate 失敗回滾不留懸空列**：事務保證原子性；gate 失敗時 INSERT 也回滾
- override 的 `clear_member_questions` 在 gate 成功後執行，P 寫已完成（done 前寫），無 late write 可跟隨

---

## 8. SSE 事件表

### 8.1 事件規格

| event | data 欄位 | 廣播來源 | 前端 handler |
|---|---|---|---|
| `phase` | `{"status": "analyzing"\|"done"\|"error", "round"?: int}` | `start_analysis`（groups.py）、收齊觸發（responses.py）、AI 層 `run_analysis` | `onPhase(status, round)` → `dispatch({type: "phase", status})` |
| `progress` | `{"participant_count": int, "submitted_count": int, "round"?: int}` | `join_group`、`leave_group`（非建立者）、`submit_response` | `onProgress(pc, sc, round?)` → `dispatch({type: "progress", participant_count, submitted_count})` |
| `consensus` | `{"content": str, "round"?: int}` | AI 層 `run_analysis`（done）、`leave_group`（解散） | `onConsensus(content, round?)` → `dispatch({type: "consensus", content})` |
| `round` | `{"round": int, "question": str, "status": "opened"\|"closed", "adaptive"?: bool}` | `open_next_round`（opened）、`close_round`（closed）、`leave_group`（closed）、auto-close（closed） | `onRound(round, question, status, adaptive?)` → `dispatch({type: "round_opened"\|"round_closed"})` |
| `error` | `{"message": str}` | `deadline_scan_loop`（零回覆）、AI 層（分析失敗） | `onError(message)` → `dispatch({type: "error", message})` |
| 原生 `onerror` | —（無 data） | EventSource 連線中斷 | `onDisconnect()` → `fetchState()` |

### 8.2 廣播呼叫點總表

| 呼叫點 | event | data |
|---|---|---|
| `join_group` | `progress` | `{"participant_count", "submitted_count"}` |
| `start_analysis`（triggered） | `phase` | `{"status": "analyzing", "round": current_round}` |
| `submit_response` | `progress` | `{"participant_count", "submitted_count", "round": current_round}` |
| `submit_response`（triggered） | `phase` | `{"status": "analyzing", "round": current_round}` |
| `leave_group`（建立者） | `consensus` | `{"content": "群組已由建立者解散"}` |
| `leave_group`（建立者） | `round` | `{"round": 0, "question": "", "status": "closed"}` |
| `leave_group`（非建立者） | `progress` | `{"participant_count", "submitted_count"}` |
| `open_next_round` | `round` | `{"round": next_round, "question": next_question, "status": "opened", "adaptive": adaptive_effective}` |
| `close_round` | `round` | `{"round": 0, "question": "", "status": "closed"}` |
| `deadline_scan_loop`（零回覆） | `error` | `{"message": "Deadline reached with no opinions"}` |
| `deadline_scan_loop`（觸發） | `phase` | `{"status": "analyzing"}` |
| `deadline_scan_loop`（auto-close） | `round` | `{"round": 0, "question": "", "status": "closed"}` |
| AI 層 `run_analysis`（done） | `consensus` | `{"content": consensus, "round": current_round}` |
| AI 層 `run_analysis`（done） | `phase` | `{"status": "done", "round": current_round}` |
| AI 層 `run_analysis`（error） | `error` | `{"message": "分析失敗,請重試"}` |
| AI 層 `run_analysis`（error） | `phase` | `{"status": "error"}` |
| AI 層 `run_analysis`（research） | `research` | `{"status": "started"\|"done"\|"skipped", "providers": int}` |

### 8.3 前端事件監聽

```typescript
es.addEventListener("phase", (e) => { const d = JSON.parse(e.data); ref.current.onPhase(d.status, d.round); });
es.addEventListener("progress", (e) => { const d = JSON.parse(e.data); ref.current.onProgress(d.participant_count, d.submitted_count, d.round); });
es.addEventListener("consensus", (e) => { const d = JSON.parse(e.data); ref.current.onConsensus(d.content, d.round); });
es.addEventListener("round", (e) => { if (ref.current.onRound) { const d = JSON.parse(e.data); ref.current.onRound(d.round, d.question, d.status, d.adaptive); } });
es.onerror = () => { ref.current.onDisconnect(); };
```

- `research` 事件前端未監聽（未使用）
- `onerror` 是原生 EventSource 事件（連線層錯誤，非自訂事件）
- **`error` 自訂事件的處理（重要設計事實）**：後端 `broadcast(pin, "error", {"message": ...})` 廣播的是 SSE 自訂事件 `event: error`，但前端**沒有** `addEventListener("error", ...)`——只有 `es.onerror`（原生連線錯誤）。`SSEHandlers.onError` 介面存在但實際上**不會被 server 廣播的 error 事件觸發**，形成死碼。server 端 error 廣播的效果是：斷線後 `es.onerror` 觸發 `onDisconnect → fetchState()`，fetchState 讀到 `GET /state` 的 `status: "error"` 並經 `mapPhase` 切到 error phase。實作時可選：(a) 維持現況（onError 死碼，靠 fetchState 兜底）或 (b) 補 `addEventListener("error", ...)` 讓 onError 生效。現況為 (a)，本規格書反映現況。

---

## 9. Pydantic models（models.py）

### 9.1 Request models

| Model | 欄位 | 驗證 |
|---|---|---|
| `CreateGroupRequest` | `question: str` | `max_length=1000` |
| | `creator_nickname: str` | `max_length=30` |
| | `expected_count: int \| None` | `default=None, ge=1` |
| | `timeout_seconds: int \| None` | `default=None, gt=0, le=86400` |
| | `max_rounds: int` | `default=3, ge=1, le=10` |
| | `adaptive_questions: bool` | `default=False`（建立時決定，不可變） |
| `JoinRequest` | `nickname: str` | `max_length=30` |
| `SubmitResponseRequest` | `participant_id: str` | — |
| | `content: str` | `max_length=4000` |
| `StartRequest` | `creator_token: str` | — |
| `LeaveRequest` | `participant_id: str` | — |
| | `creator_token: str \| None` | `default=None` |
| `OpenRoundRequest` | `creator_token: str` | — |
| | `question: str \| None` | `default=None, max_length=1000` |
| | `timeout_seconds: int \| None` | `default=None, gt=0, le=86400` |
| `CloseRoundRequest` | `creator_token: str` | — |

### 9.2 Response models

| Model | 欄位 | 預設/驗證 |
|---|---|---|
| `CreateGroupResponse` | `pin: str` | — |
| | `participant_id: str` | — |
| | `creator_token: str` | — |
| | `status: str` | `default="collecting"` |
| `JoinResponse` | `participant_id: str` | — |
| | `status: str` | — |
| `GroupState` | `pin: str` | — |
| | `question: str` | — |
| | `status: str` | — |
| | `expected_count: int \| None` | `default=None` |
| | `participant_count: int` | — |
| | `submitted_count: int` | — |
| | `deadline: str \| None` | `default=None`（ISO 字串） |
| | `consensus: str \| None` | `default=None` |
| | `is_creator: bool` | `default=False` |
| | `current_round: int` | `default=1` |
| | `max_rounds: int` | `default=3` |
| | `cooldown_remaining: int \| None` | `default=None`（秒數；`done` 且未達上限時非 None） |
| | `adaptive_questions: bool` | `default=False`（**effective flag** = 全域開關 AND 群組旗標） |
| | `member_question_count: int \| None` | `default=None`（done 且可開輪且旗標開 → 下一輪計數；否則 None） |
| `MyQuestionResponse` | `round: int` | — |
| | `question: str` | — |
| | `is_personal: bool` | — |
| `RoundInfo` | `round_number: int` | — |
| | `question: str` | — |
| | `consensus: str \| None` | `default=None` |
| | `stance_shift_summary: str \| None` | `default=None` |
| | `created_at: datetime` | — |
| | `analyzed_at: datetime \| None` | `default=None` |

### 9.3 隱私不變量

- `RoundInfo`：**公開安全**——絕不含 `stance_digest` 或 `research_brief`
- `GroupState`：`adaptive_questions` 是 effective flag（全域 AND 群組），`member_question_count` 是計數 only（無內容面）
- `MyQuestionResponse`：結構上無法攜帶其他成員的問題或 stance_digest 內容

---

## 10. 前端架構

### 10.1 路由與主題

```typescript
// App.tsx
<Routes>
  <Route path="/" element={<Home />} />
  <Route path="/create" element={<Create />} />
  <Route path="/g/:pin" element={<Group />} />
</Routes>
```

- 主題：`useState<"light" | "dark">("light")`；`useEffect` 設 `document.documentElement.setAttribute("data-theme", theme)`
- 切換按鈕：`toggleTheme()` — light→dark / dark→light
- `main.tsx`：`BrowserRouter` + `React.StrictMode`

### 10.2 api.ts

#### 型別介面

```typescript
interface CreateGroupResp { pin: string; participant_id: string; creator_token: string; status: string; }
interface JoinResp { participant_id: string; status: string; }
interface GroupStateResp {
  pin: string; question: string; status: string;
  expected_count: number | null; participant_count: number; submitted_count: number;
  deadline: string | null; consensus: string | null; is_creator: boolean;
  current_round: number; max_rounds: number;
  cooldown_remaining: number | null;
  adaptive_questions: boolean; member_question_count: number | null;
}
interface MyQuestionResp { round: number; question: string; is_personal: boolean; }
interface RoundInfo {
  round_number: number; question: string; consensus: string | null;
  stance_shift_summary: string | null;
  created_at: string; analyzed_at: string | null;
}
interface LeaveResp { ok: boolean; dissolved: boolean; }
```

#### 常數與工具

- `const BASE = "/api"`
- `class ApiError extends Error`：帶 `status: number`；`constructor(message, status)`
- `async function readError(r: Response): Promise<string>`：讀 `body.detail || body.error || r.statusText`
- `async function post<T>(path, body): Promise<T>`：POST JSON，非 OK 時 `throw new ApiError(await readError(r), r.status)`

#### API 函式簽名

| 函式 | 簽名 | 端點 |
|---|---|---|
| `createGroup` | `(question, creator_nickname, expected_count?, timeout_seconds?, max_rounds?, adaptive_questions?) => Promise<CreateGroupResp>` | POST /groups |
| `joinGroup` | `(pin, nickname) => Promise<JoinResp>` | POST /groups/{pin}/join |
| `getState` | `(pin, token?) => Promise<GroupStateResp>` | GET /groups/{pin}/state |
| `getRounds` | `(pin) => Promise<RoundInfo[]>` | GET /groups/{pin}/rounds |
| `submitResponse` | `(pin, participant_id, content) => Promise<{ok: boolean}>` | POST /groups/{pin}/responses |
| `startAnalysis` | `(pin, creator_token) => Promise<{ok: boolean}>` | POST /groups/{pin}/start |
| `openNextRound` | `(pin, creator_token, question?, timeout_seconds?) => Promise<{round, question}>` | POST /groups/{pin}/rounds/next |
| `closeGroup` | `(pin, creator_token) => Promise<{ok: boolean}>` | POST /groups/{pin}/rounds/close |
| `leaveGroup` | `(pin, participant_id, creator_token?) => Promise<LeaveResp>` | POST /groups/{pin}/leave |
| `getMyQuestion` | `(pin, participant_id, round?) => Promise<MyQuestionResp>` | GET /groups/{pin}/my-question |

- `getState`：`token ? ?token=... : ""`
- `getMyQuestion`：`round ? &round=... : ""`

### 10.3 useGroupSSE.ts

#### SSEHandlers 介面

```typescript
interface SSEHandlers {
  onPhase: (status: string, round?: number) => void;
  onProgress: (participant_count: number, submitted_count: number, round?: number) => void;
  onConsensus: (content: string, round?: number) => void;
  onError: (message: string) => void;
  onDisconnect: () => void;
  onRound?: (round: number, question: string, status: "opened" | "closed", adaptive?: boolean) => void;
}
```

#### 實作

```typescript
export function useGroupSSE(pin: string, handlers: SSEHandlers) {
  const ref = useRef(handlers);
  ref.current = handlers;  // 每次 render 更新 ref，避免 stale closure

  useEffect(() => {
    const es = new EventSource(`/api/events/${pin}`);
    es.addEventListener("phase", (e) => { const d = JSON.parse(e.data); ref.current.onPhase(d.status, d.round); });
    es.addEventListener("progress", (e) => { const d = JSON.parse(e.data); ref.current.onProgress(d.participant_count, d.submitted_count, d.round); });
    es.addEventListener("consensus", (e) => { const d = JSON.parse(e.data); ref.current.onConsensus(d.content, d.round); });
    es.addEventListener("round", (e) => { if (ref.current.onRound) { const d = JSON.parse(e.data); ref.current.onRound(d.round, d.question, d.status, d.adaptive); } });
    es.onerror = () => { ref.current.onDisconnect(); };
    return () => es.close();
  }, [pin]);  // 只依 pin 重建
}
```

關鍵：
- `ref` 避免 stale closure（`is_creator` 等在首次 render 捕獲的值不會隨後續 render 更新）
- `addEventListener("round", ...)` 讀 `d.adaptive` 欄
- `onerror` → `onDisconnect`（原生 EventSource 事件）
- effect 依賴只有 `[pin]`

### 10.4 Group.tsx — useReducer 狀態機

#### Phase 型別

```typescript
type Phase = "joining" | "waiting" | "submitted" | "analyzing" | "done" | "error";
```

#### State 介面

```typescript
interface State {
  phase: Phase;
  question: string;
  status: string;
  participant_count: number;
  submitted_count: number;
  consensus: string | null;
  is_creator: boolean;
  deadline: string | null;
  error_msg: string;
  input: string;
  current_round: number;
  max_rounds: number;
  rounds: RoundInfo[];
  showNextForm: boolean;
  nextQuestion: string;
  nextError: string;
  cooldown_remaining: number | null;
  adaptive_questions: boolean;
  member_question_count: number | null;
  my_question: { round: number; question: string; is_personal: boolean } | null;
}
```

#### Action 完整表

| Action type | Payload | 效果 |
|---|---|---|
| `init` | `Partial<State>` | 合併 payload；`phase = mapPhase(payload.status, s.phase)` |
| `phase` | `{status, round?}` | `status = a.status; phase = mapPhase(a.status, s.phase)` |
| `progress` | `{participant_count, submitted_count}` | 更新兩計數 |
| `consensus` | `{content, round?}` | `consensus = a.content; phase = "done"` |
| `error` | `{message}` | `error_msg = a.message; phase = "error"` |
| `input` | `{value}` | `input = a.value` |
| `submitted` | — | `if status === "collecting" && phase !== "analyzing": phase = "submitted"`；否則不變 |
| `round_opened` | `{round, question}` | `phase = "waiting"; status = "collecting"; current_round = a.round; question = a.question; consensus = null; input = ""; submitted_count = 0; showNextForm = false; nextQuestion = ""; nextError = ""; cooldown_remaining = null` |
| `round_closed` | — | `status = "closed"; showNextForm = false` |
| `rounds_loaded` | `{rounds: RoundInfo[]}` | `rounds = a.rounds` |
| `toggle_next_form` | `{open}` | `showNextForm = a.open; nextError = a.open ? s.nextError : ""` |
| `next_question` | `{value}` | `nextQuestion = a.value; nextError = ""` |
| `next_error` | `{message}` | `nextError = a.message` |
| `my_question` | `{question: {round, question, is_personal} \| null}` | `my_question = a.question` |

#### mapPhase

```typescript
function mapPhase(status: string, cur: Phase): Phase {
  if (status === "collecting") return cur === "submitted" ? "submitted" : "waiting";
  if (status === "analyzing") return "analyzing";
  if (status === "done") return "done";
  if (status === "closed") return "done";  // closed → done（前端統一為 done phase）
  if (status === "error") return "error";
  return cur;
}
```

#### 初始 State

```typescript
const init: State = {
  phase: "joining", question: "", status: "collecting",
  participant_count: 0, submitted_count: 0, consensus: null, is_creator: false,
  deadline: null, error_msg: "", input: "",
  current_round: 1, max_rounds: 3, rounds: [],
  showNextForm: false, nextQuestion: "", nextError: "", cooldown_remaining: null,
  adaptive_questions: false, member_question_count: null,
  my_question: null,
};
```

#### sessionStorage 鍵

- `pin:{pin}:pid` — 參與者 ID（Create + Home 都寫）
- `pin:{pin}:token` — 建立者 token（**只有 Create 寫**；Home 加入者明確 `removeItem`）
- sessionStorage 是 per-tab，每個瀏覽器分頁是獨立使用者

#### 關鍵 effects

1. **fetchState**（`useCallback` 依賴 `[pin, token]`）：
   - `getState(pin, token)` → `dispatch("init", payload)`
   - 若 `current_round > 1 || status === "done" || status === "closed"`：`getRounds(pin)` → `dispatch("rounds_loaded")`
   - 例外 → `dispatch("error", String(e))`

2. **my_question effect**（依賴 `[pin, pid, s.status, s.current_round]`）：
   - `if s.status !== "collecting" || !pid: return`
   - `getMyQuestion(pin, pid)` → `dispatch("my_question", mq)` / 例外 → `dispatch("my_question", null)`

3. **deadline 倒數 effect**（依賴 `[s.deadline, s.status]`）：
   - `if !s.deadline || s.status !== "collecting": setRemaining(null); return`
   - `setInterval` 每秒 `setRemaining(Math.max(0, Math.ceil((dl - Date.now()) / 1000)))`

4. **cooldown 倒數 effect**（依賴 `[s.cooldown_remaining]`）：
   - `if s.cooldown_remaining === null || s.cooldown_remaining <= 0: return`
   - `setTimeout` 1 秒後 `dispatch("init", {cooldown_remaining: s.cooldown_remaining - 1})`

5. **useGroupSSE**：
   - `onPhase`: `(status) => dispatch({type: "phase", status})`
   - `onProgress`: `(pc, sc) => dispatch({type: "progress", participant_count: pc, submitted_count: sc})`
   - `onConsensus`: `(content) => dispatch({type: "consensus", content})`
   - `onError`: `(message) => dispatch({type: "error", message})`
   - `onDisconnect`: `() => fetchState()`
   - `onRound`: `(round, question, status, adaptive?)`:
     - `status === "opened"`:
       - `dispatch({type: "round_opened", round, question})`
       - `if adaptive && pid`: `getMyQuestion(pin, pid)` → `dispatch("my_question", mq)` / 例外 → `dispatch("my_question", null)`
       - `else`: `dispatch({type: "my_question", question: null})`
       - `getRounds(pin)` → `dispatch("rounds_loaded", rounds)`
     - `else`（closed）: `dispatch({type: "round_closed"})`

#### send() — 409 處理

```typescript
async function send() {
  try {
    await submitResponse(pin!, pid, s.input);
    dispatch({ type: "submitted" });
  } catch (e: any) {
    if (e instanceof ApiError && e.status === 409) {
      await fetchState();  // 409 = "Submissions closed" 或 "Already submitted" → 同步狀態
    } else {
      dispatch({ type: "error", message: "送出失敗：" + String(e.message || e) });
    }
  }
}
```

#### Adaptive UX 邏輯

1. **個人化題卡片**（collecting + adaptive + my_question !== null）：
   ```jsx
   {(s.phase === "waiting" || s.phase === "submitted") && s.adaptive_questions &&
    s.my_question !== null && (
     <div className="card my-question-card">
       <h3>{s.my_question.is_personal ? "你的這一輪問題" : "本輪共同問題"}</h3>
       <p className="my-question-text">{s.my_question.question || s.question}</p>
       {s.my_question.is_personal && <p className="muted">這是為你個人生成的問題，請勿與他人比較。</p>}
     </div>
   )}
   ```

2. **共同問題 hero 隱藏守衛**：
   ```jsx
   {(!s.adaptive_questions || s.phase === "error" ||
    ((s.phase === "waiting" || s.phase === "submitted") && s.my_question === null)) && (
     <p className="group-hero-question">{s.question}</p>
   )}
   ```
   - adaptive 關 → 顯示
   - error phase → 顯示
   - adaptive 開 + collecting + my_question === null（卡片載入失敗）→ 顯示為 fallback
   - adaptive 開 + collecting + my_question !== null → **隱藏**（個人化卡片是唯一來源）

3. **智能個人化開輪 + 覆寫逃生口**（done + creator + 未達上限）：
   ```jsx
   {s.adaptive_questions ? (
     <>
       <p><strong>智能個人化提問</strong>：下一輪將為每位成員生成個人化問題。</p>
       <details>
         <summary>改用共同問題（自行輸入）</summary>
         <textarea ... value={s.nextQuestion} onChange={...} />
       </details>
     </>
   ) : (
     <textarea placeholder="下一輪的問題(留空則自動從未解分歧生成)" ... />
   )}
   ```
   - adaptive 開：預設智能個人化；`<details>` 展開才顯示覆寫 textarea
   - adaptive 關：直接 textarea

4. **member_question_count 按鈕文案**：
   ```jsx
   {(s.cooldown_remaining ?? 0) > 0
     ? `開啟下一輪（${s.cooldown_remaining}s）`
     : s.adaptive_questions && s.member_question_count != null
       ? `開啟下一輪（已為 ${s.member_question_count} 位成員備妥個人化問題）`
       : "開啟下一輪"}
   ```

#### closed → done 映射

- `mapPhase("closed", _)` = `"done"`
- `round_closed` action 只設 `status = "closed"`，phase 維持 done
- done phase 中 `s.status === "closed"` → 顯示 `<p className="round-closed-banner">討論已結束</p>`

### 10.5 Create.tsx

- 欄位：`q`, `nick`, `expected`, `timeout`, `maxRounds`, `adaptive`（核選框）, `err`
- adaptive 核選框標籤：「適應性個人化提問」+ 說明「為每位成員生成不同的後續問題（依其立場探詢彈性界線，加速收斂）。建立後不可關閉。」
- `submit()`：
  - 必填檢查：`q.trim() && nick.trim()`
  - `createGroup(q.trim(), nick.trim(), ec, ts, mr, adaptive)`
  - `sessionStorage.setItem(pin:{r.pin}:pid, r.participant_id)`
  - `sessionStorage.setItem(pin:{r.pin}:token, r.creator_token)` — **建立者才寫 token**
  - `nav(/g/${r.pin})`

### 10.6 Home.tsx

- 欄位：`pin`, `nick`, `err`
- 副標題打字機動畫：「於密室之中，見眾人之光。」
- `join()`：
  - 必填檢查：`pin.trim() && nick.trim()`
  - `fetch /api/groups/${pin}/join`（直接 fetch，不用 api.ts）
  - 成功：`sessionStorage.setItem(pin:${pin}:pid, body.participant_id)` + **`sessionStorage.removeItem(pin:${pin}:token)`**（加入者非建立者，明確清除 token）+ `nav(/g/${pin})`
  - 失敗：`body.detail || body.error || "加入失敗"`
  - 網路例外：「連線失敗」
- 「建立群組」按鈕 → `nav("/create")`

### 10.7 前端設計原則

- **無狀態庫**：不用 Redux/Zustand；`useReducer` + `useState` + `useCallback` + `useEffect`
- **EventSource 原生**：不套庫；`addEventListener` + `onerror`
- **sessionStorage per-tab**：每分頁獨立使用者
- **SSE 驅動 UI**：所有狀態變化透過 SSE 事件到達；`onDisconnect → fetchState` 作為回復機制

---

## 11. 與其他層的整合檢核表

實作完成後，逐項驗證以下接縫：

### 11.1 AI 層接縫

| # | 項目 | 驗證 |
|---|---|---|
| 1 | `start_analysis_task(pool, group_id) -> asyncio.Task` | 三個呼叫點（POST /start、收齊觸發、deadline）的 late import 簽名一致 |
| 2 | `generate_next_question(prev_consensus: str, prev_question: str) -> str` | POST /rounds/next 的 late import 簽名一致；例外時 caller 退回 regex |
| 3 | `redact(value: object) -> str` | POST /rounds/next 的 `log.warning("LLM question-gen failed, using regex fallback: %s", redact(str(e)))` |
| 4 | `shutdown_analysis_tasks(timeout: float = 10.0) -> None` | main.py lifespan shutdown 的 late import + `await` |
| 5 | `set_round_done` 事務語意 | AI 層 `run_analysis` 呼叫 `set_round_done` 後 `groups.status` 必須為 `'done'` |
| 6 | `set_group_status` error 路徑 | AI 層失敗時呼叫 `set_group_status(pool, gid, "error")` + 廣播 `error` + `phase` |
| 7 | `get_round_responses_ordered` 回傳欄位 | AI 層依賴 `r["content"]`, `r["member_seq"]`, `r["participant_id"]` |
| 8 | `get_round_consensus` 回傳欄位 | AI 層依賴 `r["consensus"]`, `r["stance_digest"]`, `r["stance_shift_summary"]`, `r["research_brief"]`, `r["question"]` |
| 9 | `replace_member_questions` items 格式 | AI 層 P stage 傳 `list[tuple[uuid.UUID, str]]` |
| 10 | `get_prior_member_questions` 回傳格式 | AI 層依賴 `list[tuple[int, str]]`（round_number, question） |

### 11.2 Infra 層接縫

| # | 項目 | 驗證 |
|---|---|---|
| 11 | `get_settings().database_url` | `get_pool` 用此建立連線池 |
| 12 | `get_settings().sse_keepalive_s` | `EventSourceResponse(generate(), ping=settings.sse_keepalive_s)` |
| 13 | `get_settings().deadline_scan_s` | `deadline_scan_loop` 的 `asyncio.sleep(s.deadline_scan_s)` |
| 14 | `get_settings().adaptive_questions_enabled` | GET /state、POST /rounds/next、GET /my-question 的 effective flag 計算 |
| 15 | `get_settings().adaptive_p_max_members` | AI 層 P stage 使用（本層不直接用，但確保 Settings 有此欄位） |
| 16 | `migrations/001_init.sql` schema | `groups`（含 `pin CHAR(5) UNIQUE`）、`participants`（含 `UNIQUE(group_id, nickname)`）、`responses`（含 `UNIQUE(group_id, participant_id)`） |
| 17 | `migrations/002_rounds.sql` schema | `responses.round_number`、`groups.current_round/max_rounds`、`participants.member_seq`、`rounds` 表（含 `stance_digest`/`research_brief`） |
| 18 | `migrations/003_adaptive_questions.sql` schema | `groups.adaptive_questions`、`member_questions` 表（含 `UNIQUE(group_id, round_number, participant_id)`） |
| 19 | `init_db` sorted glob | 001 < 002 < 003；全部冪等（`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`） |
| 20 | compose.yml 執行環境 | uvicorn `--no-access-log`（participant_id 在 query string）；vite proxy `/api` + `/events` |

### 11.3 廣播事件名接縫

| # | 事件名 | 廣播來源層 | 前端監聯 |
|---|---|---|---|
| 21 | `phase` | app server + AI 層 | `addEventListener("phase")` |
| 22 | `progress` | app server only | `addEventListener("progress")` |
| 23 | `consensus` | app server（leave）+ AI 層 | `addEventListener("consensus")` |
| 24 | `round` | app server only | `addEventListener("round")`（含 `adaptive?` 欄） |
| 25 | `error` | app server（deadline 零回覆）+ AI 層 | `addEventListener("error")` — 注意：前端未監聽 `error` 事件名，只靠 `onerror`（原生）；但 `dispatch({type: "error"})` 來自 `onError` handler，此 handler 由 AI 層廣播的 `error` 事件觸發 |
| 26 | `research` | AI 層 only | 前端未監聽（忽略） |

### 11.4 db helper 簽名接縫

| # | helper | AI 層/本層呼叫點 | 驗證 |
|---|---|---|---|
| 27 | `get_group_by_id` | AI 層 `run_analysis` | 回傳含 `pin`, `question`, `current_round`, `max_rounds`, `adaptive_questions` |
| 28 | `get_round_responses_ordered` | AI 層 `run_analysis` | 回傳含 `content`, `member_seq`, `participant_id` |
| 29 | `get_round_consensus` | AI 層 + POST /rounds/next | 回傳含 `consensus`, `stance_digest`, `stance_shift_summary`, `research_brief`, `question` |
| 30 | `set_round_done` | AI 層 `run_analysis` | 事務內寫 `rounds` + `groups`；`stance_digest` 只寫 `rounds` |
| 31 | `set_group_status` | AI 層 error 路徑 | `set_group_status(pool, gid, "error")` |
| 32 | `get_prior_member_questions` | AI 層 P stage | 回傳 `list[tuple[int, str]]`，只回該成員自己 |
| 33 | `replace_member_questions` | AI 層 P stage | upsert，`ON CONFLICT DO UPDATE` |

---

> **實作備註**：本規格書的所有簽名、SQL、事件欄位、Action 表均從現有程式碼逐行提取。實作時以本文件為合約，逐章對照驗證。若本文件與實際程式碼衝突，以程式碼為準——但請在整合時記錄差異，以便修正本規格書。
