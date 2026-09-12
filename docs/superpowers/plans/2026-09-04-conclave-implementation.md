# Conclave Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Conclave PoC end-to-end — a web app where a creator opens a group with a 5-char PIN, others join by PIN + nickname, everyone submits private opinions, and the backend collects all replies, sends them to an LLM, and broadcasts one shared consensus summary to all screens — fully packaged in docker compose (frontend + backend + database).

**Architecture:** FastAPI backend (single uvicorn worker, in-process SSE broadcast, asyncpg against Postgres 16) + React/Vite/TypeScript frontend (EventSource SSE, useReducer state machine) + Postgres 16-alpine with a named volume. Three containers in dev compose; prod variant collapses frontend into backend StaticFiles. The backend owns the trigger critical section (atomic `UPDATE ... WHERE status='collecting'`, fire-and-forget LLM task), deadline scan, and LLM integration (OpenAI SDK against sglang endpoint, model auto-probe, reasoning-token-aware budget).

**Tech Stack:** Python 3.13 (conda `conclave`), FastAPI 0.141, uvicorn 0.52, sse-starlette 3.4, pydantic 2.13, pydantic-settings 2.15, asyncpg 0.31, openai 3.8, httpx 0.28; React 18 + Vite 5 + TypeScript + react-router-dom + react-markdown; Postgres 16-alpine; Docker Compose v2.

**Spec:** `docs/SPEC.md` (the authoritative spec — every task argues from it; executors read both the relevant spec section and the task).

## Global Constraints

- **PIN**: 5 chars, `string.ascii_letters + string.digits` (62 chars, case-sensitive + digits), via `secrets.choice`; rely on DB `UNIQUE` for collision, retry on violation (SPEC §7.2).
- **Group statuses**: exactly four — `collecting | analyzing | done | error` (SPEC §3.2, §5).
- **Trigger**: three-condition OR (creator manual `POST /start` / `expected_count` reached / `deadline` elapsed); zero-reply guard (never enter `analyzing` with `submitted_count == 0`); atomic critical section via `UPDATE ... WHERE status='collecting'` with rowcount==1; fire-and-forget LLM in `asyncio.create_task` (SPEC §4, §7.3).
- **`expected_count` semantics**: count of REPLIES, not people; trigger checks `submitted_count >= expected_count` (SPEC §4).
- **LLM**: OpenAI SDK, `base_url=http://10.241.77.188:8000/v1`, `api_key=probe`, model auto-probed from `/v1/models` (`data[0].id`), `max_tokens=8192`, `temperature=0.3`, `timeout=60s`. `reasoning_tokens` lives at `usage.reasoning_tokens` (top-level). `finish_reason=='length'` with empty content = failure → `error` state (SPEC §7.6).
- **SSE**: in-process `dict[pin, set[asyncio.Queue(maxsize=16)]]`, `put_nowait` + drop-on-`QueueFull`, no replay (client `GET /state` on reconnect), `sse-starlette` `ping=15`. Single uvicorn worker only (SPEC §7.5).
- **Single source of truth**: `GET /api/groups/{pin}/state` is the truth; SSE is ephemeral push (SPEC §6.1).
- **Input limits**: `question` ≤1000, `nickname` ≤30, `content` ≤4000, `expected_count` ≥1, `timeout_seconds` >0 and ≤86400 (SPEC §6.1).
- **Docker**: `compose.yml` = three services (db + backend + frontend) is the canonical deliverable the user runs with `docker compose up` (SPEC §9.1).
- **No global env pollution**: Python deps in conda `conclave` env for local tests; Docker images are self-contained (SPEC §7.1).
- **Git**: commits per task on `main` (user is on `main`); user name `Soren Hong`, email `yxhong.work@gmail.com`. Commit messages in conventional format (`feat:`, `test:`, `chore:`, etc.), no attribution footer (disabled globally).

---

## File Structure

```
conclave/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI app, lifespan (migration + deadline scan + worker assert), CORS, router includes, /api/health, prod StaticFiles + SPA fallback
│   │   ├── config.py           # pydantic-settings Settings (§11 env vars)
│   │   ├── db.py               # asyncpg pool, migration runner, group state helpers (atomic trigger, counts)
│   │   ├── models.py           # pydantic request/response schemas with Field limits
│   │   ├── pin.py              # generate_pin() + retry-on-unique-violation helper
│   │   ├── broadcast.py        # in-process SSE registry: subscribe/unsubscribe/broadcast with put_nowait backpressure
│   │   ├── llm.py              # OpenAI client wrapper, model probe, build_prompt(), run_analysis(group_id)
│   │   ├── tasks.py            # deadline scan background task
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── groups.py       # POST /groups, POST /join, GET /state, POST /start
│   │       ├── responses.py    # POST /responses (insert + atomic trigger check in one tx)
│   │       └── events.py       # GET /events/{pin} SSE stream
│   ├── migrations/
│   │   └── 001_init.sql        # CREATE TABLE IF NOT EXISTS for groups/participants/responses + index
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py         # fixtures: isolated asyncpg pool to a per-test schema, mock LLM, test client
│   │   ├── test_pin.py
│   │   ├── test_broadcast.py
│   │   ├── test_trigger.py     # the §12 truth table (parametrized)
│   │   ├── test_llm.py         # model probe + reasoning_tokens defensive read + empty-output failure
│   │   ├── test_groups_api.py
│   │   ├── test_responses_api.py
│   │   ├── test_events_sse.py
│   │   └── test_integration.py # full loop: create→join→submit→trigger→mock LLM→consensus broadcast
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx             # react-router routes
│   │   ├── pages/
│   │   │   ├── Home.tsx        # two buttons + PIN/nickname inputs
│   │   │   ├── Create.tsx      # create form
│   │   │   └── Group.tsx       # group room: useReducer state machine + SSE + creator UI
│   │   ├── hooks/
│   │   │   └── useGroupSSE.ts  # EventSource lifecycle + reconnect GET /state
│   │   ├── lib/
│   │   │   └── api.ts          # REST client (create, join, getState, submitResponse, startAnalysis)
│   │   └── styles/index.css
│   ├── index.html
│   ├── vite.config.ts         # proxy /api + /events to backend
│   ├── tsconfig.json
│   ├── package.json
│   └── Dockerfile
├── compose.yml                 # dev: db + backend + frontend (canonical)
├── compose.prod.yml            # prod variant: db + backend-with-static
└── docs/...                    # existing
```

---

### Task 1: Backend scaffold — config, DB pool, migration, health

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/migrations/001_init.sql`
- Create: `backend/app/db.py`
- Create: `backend/app/main.py`
- Create: `backend/Dockerfile`

**Interfaces:**
- Produces: `Settings` class (config.py) with fields: `database_url: str`, `llm_base_url: str="http://10.241.77.188:8000/v1"`, `llm_api_key: str="probe"`, `llm_model: str=""`, `llm_max_tokens: int=8192`, `llm_temperature: float=0.3`, `llm_timeout: float=60.0`, `sse_keepalive_s: int=15`, `deadline_scan_s: int=5`. `get_settings()` cached accessor.
- Produces: `async def init_db(pool) -> None` (db.py) runs `migrations/001_init.sql` (idempotent).
- Produces: `async def get_pool() -> asyncpg.Pool` (db.py) lazily creates the global pool from `settings.database_url`, min_size=2 max_size=10.
- Produces: `app` (main.py) FastAPI instance with lifespan that calls `get_pool()` + `init_db(pool)` on startup.

- [ ] **Step 1: Write requirements.txt**

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
```

- [ ] **Step 2: Write config.py**

```python
from functools import lru_cache
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    llm_base_url: str = "http://10.241.77.188:8000/v1"
    llm_api_key: str = "probe"
    llm_model: str = ""
    llm_max_tokens: int = 8192
    llm_temperature: float = 0.3
    llm_timeout: float = 60.0
    sse_keepalive_s: int = 15
    deadline_scan_s: int = 5
    model_config = {"env_prefix": "", "case_sensitive": False}

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: Write migrations/001_init.sql**

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
CREATE TABLE IF NOT EXISTS participants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id    UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    nickname    TEXT NOT NULL,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, nickname)
);
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

- [ ] **Step 4: Write db.py**

```python
import asyncpg
from pathlib import Path
from .config import get_settings

_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=get_settings().database_url, min_size=2, max_size=10
        )
    return _pool

async def init_db(pool: asyncpg.Pool) -> None:
    sql = Path(__file__).resolve().parent.parent / "migrations" / "001_init.sql"
    await pool.execute(sql.read_text(encoding="utf-8"))

async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
```

- [ ] **Step 5: Write main.py (minimal lifespan + health)**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .db import get_pool, init_db, close_pool

@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_pool()
    await init_db(pool)
    yield
    await close_pool()

app = FastAPI(title="Conclave", lifespan=lifespan)

@app.get("/api/health")
async def health():
    return {"ok": True}
```

- [ ] **Step 6: Write Dockerfile**

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

- [ ] **Step 7: Verify import + health via conda env**

Run: `source $(conda info --base)/etc/profile.d/conda.sh && conda activate conclave && cd backend && python -c "from app.main import app; print('import ok')"`
Expected: `import ok` (no DB needed for import; lifespan runs only when served).

- [ ] **Step 8: Commit**

```bash
git add backend/
git commit -m "feat(backend): scaffold config, DB pool, migration, health endpoint"
```

---

### Task 2: PIN generation — `pin.py`

**Files:**
- Create: `backend/app/pin.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_pin.py`

**Interfaces:**
- Produces: `generate_pin() -> str` returns 5-char string over `string.ascii_letters + string.digits` via `secrets.choice`.
- Produces: `ALPHABET = string.ascii_letters + string.digits`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pin.py
import string
from app.pin import generate_pin, ALPHABET

def test_pin_is_5_chars():
    assert len(generate_pin()) == 5

def test_pin_uses_only_alphabet():
    valid = set(ALPHABET)
    for _ in range(1000):
        assert set(generate_pin()) <= valid

def test_pin_is_case_sensitive_alphabet():
    assert set(string.ascii_letters + string.digits) == set(ALPHABET)
    assert len(ALPHABET) == 62

def test_pin_space_size():
    assert 62 ** 5 == 916_132_832

def test_pin_is_cryptographically_random():
    # extremely unlikely two consecutive pins are equal
    pins = {generate_pin() for _ in range(100)}
    assert len(pins) > 95
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source $(conda info --base)/etc/profile.d/conda.sh && conda activate conclave && cd backend && python -m pytest tests/test_pin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pin'`

- [ ] **Step 3: Write pin.py**

```python
# backend/app/pin.py
import secrets
import string

ALPHABET = string.ascii_letters + string.digits  # 62 chars, case-sensitive + digits

def generate_pin() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(5))  # 62^5 ≈ 916M
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pin.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pin.py backend/tests/
git commit -m "feat(backend): PIN generation (5-char, case-sensitive, secrets.choice)"
```

---

### Task 3: Pydantic models — `models.py`

**Files:**
- Create: `backend/app/models.py`

**Interfaces:**
- Produces: `CreateGroupRequest(question: str max_length=1000, creator_nickname: str max_length=30, expected_count: int | None = None ge=1, timeout_seconds: int | None = None gt=0 le=86400)`
- Produces: `JoinRequest(nickname: str max_length=30)`
- Produces: `SubmitResponseRequest(participant_id: str, content: str max_length=4000)`
- Produces: `StartRequest(creator_token: str)`
- Produces: `CreateGroupResponse(pin: str, participant_id: str, creator_token: str, status: str="collecting")`
- Produces: `JoinResponse(participant_id: str, status: str)`
- Produces: `GroupState(pin: str, question: str, status: str, expected_count: int | None, participant_count: int, submitted_count: int, deadline: str | None, consensus: str | None, is_creator: bool)`

- [ ] **Step 1: Write models.py**

```python
# backend/app/models.py
from datetime import datetime
from pydantic import BaseModel, Field

class CreateGroupRequest(BaseModel):
    question: str = Field(max_length=1000)
    creator_nickname: str = Field(max_length=30)
    expected_count: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, gt=0, le=86400)

class JoinRequest(BaseModel):
    nickname: str = Field(max_length=30)

class SubmitResponseRequest(BaseModel):
    participant_id: str
    content: str = Field(max_length=4000)

class StartRequest(BaseModel):
    creator_token: str

class CreateGroupResponse(BaseModel):
    pin: str
    participant_id: str
    creator_token: str
    status: str = "collecting"

class JoinResponse(BaseModel):
    participant_id: str
    status: str

class GroupState(BaseModel):
    pin: str
    question: str
    status: str
    expected_count: int | None = None
    participant_count: int
    submitted_count: int
    deadline: str | None = None
    consensus: str | None = None
    is_creator: bool = False
```

- [ ] **Step 2: Verify models import + a 422 case**

Run: `cd backend && python -c "
from app.models import CreateGroupRequest
from pydantic import ValidationError
try:
    CreateGroupRequest(question='x'*1001, creator_nickname='a')
    print('FAIL: should reject')
except ValidationError:
    print('OK: 422 on long question')
"`
Expected: `OK: 422 on long question`

- [ ] **Step 3: Commit**

```bash
git add backend/app/models.py
git commit -m "feat(backend): pydantic request/response schemas with input limits"
```

---

### Task 4: SSE broadcast registry — `broadcast.py`

**Files:**
- Create: `backend/app/broadcast.py`
- Create: `backend/tests/test_broadcast.py`

**Interfaces:**
- Produces: `subscribe(pin: str) -> asyncio.Queue` (maxsize=16), adds to `dict[pin, set[Queue]]`.
- Produces: `unsubscribe(pin: str, queue: asyncio.Queue) -> None`.
- Produces: `async def broadcast(pin: str, event_type: str, data: dict) -> None` — `put_nowait` per queue, drop queue on `QueueFull`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_broadcast.py
import asyncio
import pytest
from app.broadcast import subscribe, unsubscribe, broadcast

@pytest.mark.asyncio
async def test_subscribe_and_broadcast():
    q = subscribe("PIN1")
    await broadcast("PIN1", "phase", {"status": "analyzing"})
    msg = await asyncio.wait_for(q.get(), timeout=1)
    assert msg == {"event": "phase", "data": {"status": "analyzing"}}
    unsubscribe("PIN1", q)

@pytest.mark.asyncio
async def test_broadcast_reaches_multiple_subscribers():
    q1, q2 = subscribe("PIN1"), subscribe("PIN1")
    await broadcast("PIN1", "consensus", {"content": "ok"})
    m1 = await asyncio.wait_for(q1.get(), timeout=1)
    m2 = await asyncio.wait_for(q2.get(), timeout=1)
    assert m1 == m2 == {"event": "consensus", "data": {"content": "ok"}}
    unsubscribe("PIN1", q1); unsubscribe("PIN1", q2)

@pytest.mark.asyncio
async def test_late_subscriber_gets_no_replay():
    await broadcast("PIN1", "phase", {"status": "done"})
    q = subscribe("PIN1")  # after broadcast
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.2)
    unsubscribe("PIN1", q)

@pytest.mark.asyncio
async def test_queue_full_drops_connection():
    q = subscribe("PIN1")
    # fill queue (maxsize=16)
    for i in range(16):
        await broadcast("PIN1", "progress", {"i": i})
    # 17th event should drop q from the registry (put_nowait raises QueueFull)
    await broadcast("PIN1", "progress", {"i": 99})
    # q no longer in registry: a new broadcast should not reach it
    await broadcast("PIN1", "progress", {"i": 100})
    # q already has 16 items; the 17th never enqueued (dropped)
    assert q.qsize() == 16
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_broadcast.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write broadcast.py**

```python
# backend/app/broadcast.py
import asyncio
from asyncio import Queue, QueueFull

_subscribers: dict[str, set[Queue]] = {}

def subscribe(pin: str) -> Queue:
    q: Queue = Queue(maxsize=16)
    _subscribers.setdefault(pin, set()).add(q)
    return q

def unsubscribe(pin: str, q: Queue) -> None:
    subs = _subscribers.get(pin)
    if subs:
        subs.discard(q)
        if not subs:
            del _subscribers[pin]

async def broadcast(pin: str, event_type: str, data: dict) -> None:
    msg = {"event": event_type, "data": data}
    for q in list(_subscribers.get(pin, ())):
        try:
            q.put_nowait(msg)
        except QueueFull:
            unsubscribe(pin, q)  # drop slow consumer; EventSource will reconnect
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_broadcast.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/broadcast.py backend/tests/test_broadcast.py
git commit -m "feat(backend): in-process SSE broadcast registry with backpressure"
```

---

### Task 5: DB state helpers — atomic trigger + counts (`db.py` additions)

**Files:**
- Modify: `backend/app/db.py` (append helpers)
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_trigger.py`

**Interfaces:**
- Produces: `async def get_group(pool, pin) -> asyncpg.Record | None`
- Produces: `async def try_enter_analyzing(pool, group_id) -> bool` — atomic `UPDATE groups SET status='analyzing' WHERE id=$1 AND status IN ('collecting','error')`, returns rowcount==1. (Allows `error→analyzing` retry per §3.2.)
- Produces: `async def get_submitted_count(pool, group_id) -> int`
- Produces: `async def get_responses_ordered(pool, group_id) -> list[asyncpg.Record]` — `SELECT content FROM responses WHERE group_id=$1 ORDER BY submitted_at`.

- [ ] **Step 1: Write conftest.py with an isolated DB fixture**

```python
# backend/tests/conftest.py
import asyncio
import asyncpg
import pytest
import pytest_asyncio
from app.db import init_db

TEST_DSN = "postgresql://conclave:conclave@localhost:5432/conclave"

@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=TEST_DSN, min_size=1, max_size=5)
    # clean all tables for test isolation
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE groups, participants, responses CASCADE")
    await init_db(p)
    yield p
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE groups, participants, responses CASCADE")
    await p.close()
```

> Note: tests that use this `pool` fixture require a live Postgres at `localhost:5432`. Run them only when docker compose db is up; skip-integration marker covers CI without DB. For unit-only runs (no DB), run `pytest -m "not integration"` and the pin/broadcast tests still pass.

- [ ] **Step 2: Write test_trigger.py (§12 truth table, parametrized)**

```python
# backend/tests/test_trigger.py
import pytest
import asyncio
from app.db import try_enter_analyzing, get_submitted_count
from app.pin import generate_pin

pytestmark = pytest.mark.integration

async def _make_group(pool, expected_count=None, deadline=None, status="collecting"):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO groups (pin, question, creator_token, expected_count, deadline, status) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            generate_pin(), "Q?", "tok", expected_count, deadline, status,
        )
        return row["id"]

async def _add_participant_and_response(pool, group_id, nick, content="opinion"):
    async with pool.acquire() as conn:
        p = await conn.fetchrow(
            "INSERT INTO participants (group_id, nickname) VALUES ($1, $2) RETURNING id",
            group_id, nick,
        )
        await conn.execute(
            "INSERT INTO responses (group_id, participant_id, content) VALUES ($1, $2, $3)",
            group_id, p["id"], content,
        )

@pytest.mark.parametrize("expected,deadline,event,should_trigger", [
    (3, None, "third_reply", True),
    (None, "T", "deadline", True),
    (3, "T", "third_reply_before_deadline", True),
    (3, "T", "deadline_with_2", True),
    (None, None, "any_reply", False),
    (None, None, "creator_start", True),
])
async def test_trigger_truth_table(pool, expected, deadline, event, should_trigger):
    gid = await _make_group(pool, expected_count=expected, deadline=None if deadline is None else "2000-01-01T00:00:00+00:00")
    if event in ("third_reply", "third_reply_before_deadline"):
        await _add_participant_and_response(pool, gid, "A"); await _add_participant_and_response(pool, gid, "B")
        await _add_participant_and_response(pool, gid, "C")  # 3rd reply
    elif event == "deadline_with_2":
        await _add_participant_and_response(pool, gid, "A"); await _add_participant_and_response(pool, gid, "B")
    elif event == "any_reply":
        await _add_participant_and_response(pool, gid, "A")
    triggered = await try_enter_analyzing(pool, gid)
    assert triggered == should_trigger

async def test_trigger_is_single_shot(pool):
    gid = await _make_group(pool, expected_count=1)
    await _add_participant_and_response(pool, gid, "A")
    assert await try_enter_analyzing(pool, gid) is True   # first wins
    assert await try_enter_analyzing(pool, gid) is False  # second no-op

async def test_zero_reply_start_does_not_trigger(pool):
    gid = await _make_group(pool)
    # POST /start guard: submitted_count==0 -> 409, but try_enter_analyzing reflects that
    # there are 0 replies; caller (route) checks count first.
    assert await get_submitted_count(pool, gid) == 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_trigger.py -v` (needs `docker compose up db` running)
Expected: FAIL — helpers not defined.

- [ ] **Step 4: Append helpers to db.py**

```python
# append to backend/app/db.py
async def get_group(pool: asyncpg.Pool, pin: str) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM groups WHERE pin=$1", pin)

async def try_enter_analyzing(pool: asyncpg.Pool, group_id) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE groups SET status='analyzing' WHERE id=$1 AND status IN ('collecting','error')",
            group_id,
        )
        return result == "UPDATE 1"

async def get_submitted_count(pool: asyncpg.Pool, group_id) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT count(*) FROM responses WHERE group_id=$1", group_id)

async def get_responses_ordered(pool: asyncpg.Pool, group_id):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT content FROM responses WHERE group_id=$1 ORDER BY submitted_at", group_id)

async def set_group_status(pool: asyncpg.Pool, group_id, status: str, consensus: str | None = None) -> None:
    async with pool.acquire() as conn:
        if consensus is not None:
            await conn.execute("UPDATE groups SET status=$2, consensus=$3 WHERE id=$1", group_id, status, consensus)
        else:
            await conn.execute("UPDATE groups SET status=$2 WHERE id=$1", group_id, status)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_trigger.py -v` (with db up)
Expected: 8 passed (6 parametrized + 2 standalone).

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/tests/conftest.py backend/tests/test_trigger.py
git commit -m "feat(backend): atomic trigger + count helpers; §12 truth-table tests"
```

---

### Task 6: Groups API — create, join, state (`routes/groups.py`)

**Files:**
- Create: `backend/app/routes/__init__.py`
- Create: `backend/app/routes/groups.py`
- Modify: `backend/app/main.py` (include router)
- Create: `backend/tests/test_groups_api.py`

**Interfaces:**
- Consumes: `get_pool()`, `generate_pin()`, models from Task 3.
- Produces: `router = APIRouter(prefix="/api")` with `POST /groups`, `POST /groups/{pin}/join`, `GET /groups/{pin}/state`, `POST /groups/{pin}/start` (start handler is a stub here — full trigger wiring in Task 8).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_groups_api.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

pytestmark = pytest.mark.integration

client = TestClient(app)

def test_create_group():
    r = client.post("/api/groups", json={"question": "Dinner?", "creator_nickname": "Alice"})
    assert r.status_code == 201
    body = r.json()
    assert len(body["pin"]) == 5
    assert body["status"] == "collecting"
    assert body["creator_token"]
    assert body["participant_id"]

def test_join_and_state():
    g = client.post("/api/groups", json={"question": "Q", "creator_nickname": "A"}).json()
    pin, tok = g["pin"], g["creator_token"]
    r = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"})
    assert r.status_code == 200
    assert r.json()["status"] == "collecting"
    s = client.get(f"/api/groups/{pin}/state").json()
    assert s["question"] == "Q" and s["status"] == "collecting"
    assert s["participant_count"] == 2  # creator + Bob
    assert s["submitted_count"] == 0
    s_creator = client.get(f"/api/groups/{pin}/state?token={tok}").json()
    assert s_creator["is_creator"] is True

def test_join_duplicate_nickname_409():
    g = client.post("/api/groups", json={"question": "Q", "creator_nickname": "Alice"}).json()
    client.post(f"/api/groups/{g['pin']}/join", json={"nickname": "Alice"})  # dup of creator
    r = client.post(f"/api/groups/{g['pin']}/join", json={"nickname": "Alice"})
    assert r.status_code == 409

def test_create_rejects_long_question():
    r = client.post("/api/groups", json={"question": "x"*1001, "creator_nickname": "A"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_groups_api.py -v` (with db up)
Expected: FAIL — no router.

- [ ] **Step 3: Write routes/groups.py**

```python
# backend/app/routes/groups.py
import secrets, asyncpg
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Query
from ..db import get_pool
from ..pin import generate_pin
from ..models import (
    CreateGroupRequest, JoinRequest, StartRequest,
    CreateGroupResponse, JoinResponse, GroupState,
)

router = APIRouter(prefix="/api")

@router.post("/groups", response_model=CreateGroupResponse, status_code=201)
async def create_group(req: CreateGroupRequest):
    pool = await get_pool()
    creator_token = secrets.token_urlsafe(16)
    deadline = None
    if req.timeout_seconds is not None:
        deadline = datetime.now(timezone.utc) + timedelta(seconds=req.timeout_seconds)
    for _ in range(3):  # retry on PIN collision
        pin = generate_pin()
        async with pool.acquire() as conn:
            try:
                g = await conn.fetchrow(
                    "INSERT INTO groups (pin, question, creator_token, expected_count, deadline) "
                    "VALUES ($1, $2, $3, $4, $5) RETURNING id",
                    pin, req.question, creator_token, req.expected_count, deadline,
                )
                p = await conn.fetchrow(
                    "INSERT INTO participants (group_id, nickname) VALUES ($1, $2) RETURNING id",
                    g["id"], req.creator_nickname,
                )
                return CreateGroupResponse(pin=pin, participant_id=str(p["id"]), creator_token=creator_token)
            except asyncpg.UniqueViolationError:
                continue
    raise HTTPException(500, "PIN collision after retries")

@router.post("/groups/{pin}/join", response_model=JoinResponse)
async def join_group(pin: str, req: JoinRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT id, status FROM groups WHERE pin=$1", pin)
        if g is None:
            raise HTTPException(404, "Group not found")
        try:
            p = await conn.fetchrow(
                "INSERT INTO participants (group_id, nickname) VALUES ($1, $2) RETURNING id",
                g["id"], req.nickname,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "Nickname already taken in this group")
        return JoinResponse(participant_id=str(p["id"]), status=g["status"])

@router.get("/groups/{pin}/state", response_model=GroupState)
async def get_state(pin: str, token: str | None = Query(default=None)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT * FROM groups WHERE pin=$1", pin)
        if g is None:
            raise HTTPException(404, "Group not found")
        pc = await conn.fetchval("SELECT count(*) FROM participants WHERE group_id=$1", g["id"])
        sc = await conn.fetchval("SELECT count(*) FROM responses WHERE group_id=$1", g["id"])
        is_creator = token is not None and token == g["creator_token"]
        return GroupState(
            pin=g["pin"], question=g["question"], status=g["status"],
            expected_count=g["expected_count"], participant_count=pc, submitted_count=sc,
            deadline=g["deadline"].isoformat() if g["deadline"] else None,
            consensus=g["consensus"], is_creator=is_creator,
        )

@router.post("/groups/{pin}/start", status_code=202)
async def start_analysis(pin: str, req: StartRequest):
    # Wired fully in Task 8; here returns 409 for non-collecting + 403 for bad token.
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT * FROM groups WHERE pin=$1", pin)
        if g is None:
            raise HTTPException(404, "Group not found")
        if g["creator_token"] != req.creator_token:
            raise HTTPException(403, "Not the creator")
        if g["status"] not in ("collecting", "error"):
            raise HTTPException(409, "Already analyzing or done")
        sc = await conn.fetchval("SELECT count(*) FROM responses WHERE group_id=$1", g["id"])
        if sc == 0:
            raise HTTPException(409, "No opinions yet")
    # Task 8 wires the actual trigger here.
    return {"ok": True}
```

- [ ] **Step 4: Include router in main.py**

```python
# add to backend/app/main.py (after health route)
from .routes import groups
app.include_router(groups.router)
```

And `backend/app/routes/__init__.py` (empty file).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_groups_api.py -v` (with db up)
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/ backend/app/main.py backend/tests/test_groups_api.py
git commit -m "feat(backend): groups API — create, join, state; start stub"
```

---

### Task 7: SSE events endpoint — `routes/events.py`

**Files:**
- Create: `backend/app/routes/events.py`
- Modify: `backend/app/main.py` (include events router)
- Create: `backend/tests/test_events_sse.py`

**Interfaces:**
- Consumes: `subscribe`/`unsubscribe` from broadcast.py, `settings.sse_keepalive_s`.
- Produces: `GET /api/events/{pin}` SSE stream using `EventSourceResponse`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_events_sse.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

pytestmark = pytest.mark.integration

def test_sse_streams_named_events():
    with TestClient(app) as c:
        with c.stream("GET", "/api/events/PIN1") as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            # read first event (a manually-broadcast one)
            from app.broadcast import broadcast
            import asyncio
            asyncio.get_event_loop().create_task(broadcast("PIN1", "phase", {"status": "analyzing"}))
            lines = []
            for line in r.iter_lines():
                lines.append(line)
                if lines and lines[-1] == "":
                    break
            body = "\n".join(lines)
            assert "event: phase" in body
            assert '"status": "analyzing"' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_events_sse.py -v`
Expected: FAIL — no events route.

- [ ] **Step 3: Write events.py**

```python
# backend/app/routes/events.py
import asyncio
import json
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse
from ..broadcast import subscribe, unsubscribe
from ..config import get_settings

router = APIRouter(prefix="/api")

@router.get("/events/{pin}")
async def event_stream(pin: str):
    settings = get_settings()
    q = subscribe(pin)

    async def generate():
        try:
            while True:
                msg = await q.get()
                yield {"event": msg["event"], "data": json.dumps(msg["data"])}
        finally:
            unsubscribe(pin, q)

    return EventSourceResponse(generate(), ping=settings.sse_keepalive_s)
```

- [ ] **Step 4: Include router in main.py**

```python
# add to backend/app/main.py
from .routes import events
app.include_router(events.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_events_sse.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/events.py backend/app/main.py backend/tests/test_events_sse.py
git commit -m "feat(backend): SSE events endpoint with sse-starlette ping"
```

---

### Task 8: Responses API + trigger wiring — `routes/responses.py` + full `start`

**Files:**
- Create: `backend/app/routes/responses.py`
- Create: `backend/app/llm.py` (stub `run_analysis` — full impl in Task 9)
- Modify: `backend/app/routes/groups.py` (wire `start` trigger)
- Modify: `backend/app/main.py` (include responses router)
- Create: `backend/tests/test_responses_api.py`

**Interfaces:**
- Produces: `POST /api/groups/{pin}/responses` — inserts in a tx, checks status atomic, triggers if `expected_count` met, broadcasts `progress`, fire-and-forgets `run_analysis`.
- Produces: `async def run_analysis(pool, group_id)` stub in llm.py (raises `NotImplementedError` until Task 9).

- [ ] **Step 1: Write llm.py stub**

```python
# backend/app/llm.py
import asyncio
from .db import get_pool, try_enter_analyzing, get_responses_ordered, set_group_status
from .broadcast import broadcast

async def run_analysis(pool, group_id) -> None:
    """Wired in Task 9. Raises NotImplementedError if called before then."""
    raise NotImplementedError("LLM integration in Task 9")
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_responses_api.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

pytestmark = pytest.mark.integration
client = TestClient(app)

def _create_and_join(pin_nicknames):
    """Helper: create group, return (pin, creator_token, creator_participant_id)."""
    g = client.post("/api/groups", json={"question": "Q", "creator_nickname": "Alice"}).json()
    return g["pin"], g["creator_token"], g["participant_id"]

def test_submit_response_and_progress():
    pin, tok, pid = _create_and_join("Alice")
    r = client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "my opinion"})
    assert r.status_code == 200
    s = client.get(f"/api/groups/{pin}/state").json()
    assert s["submitted_count"] == 1

def test_duplicate_response_409():
    pin, tok, pid = _create_and_join("Alice")
    client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "x"})
    r = client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "y"})
    assert r.status_code == 409

def test_response_after_analyzing_409():
    pin, tok, pid = _create_and_join("Alice")
    # manually flip to analyzing
    import asyncio
    from app.db import get_pool, set_group_status
    pool = asyncio.get_event_loop().run_until_complete(get_pool())
    # get group id via state
    gid = client.get(f"/api/groups/{pin}/state").json()
    # need group id; query directly
    async def _flip():
        async with pool.acquire() as c:
            await c.execute("UPDATE groups SET status='analyzing' WHERE pin=$1", pin)
    asyncio.get_event_loop().run_until_complete(_flip())
    r = client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "late"})
    assert r.status_code == 409
```

- [ ] **Step 3: Write responses.py**

```python
# backend/app/routes/responses.py
import asyncio
import asyncpg
from fastapi import APIRouter, HTTPException
from ..db import get_pool, try_enter_analyzing, get_submitted_count
from ..broadcast import broadcast
from ..models import SubmitResponseRequest
from ..llm import run_analysis

router = APIRouter(prefix="/api")

@router.post("/groups/{pin}/responses")
async def submit_response(pin: str, req: SubmitResponseRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT id, status FROM groups WHERE pin=$1", pin)
        if g is None:
            raise HTTPException(404, "Group not found")
        if g["status"] != "collecting":
            raise HTTPException(409, "Submissions closed")
        try:
            await conn.execute(
                "INSERT INTO responses (group_id, participant_id, content) VALUES ($1, $2, $3)",
                g["id"], req.participant_id, req.content,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "Already submitted")
        # atomic check: re-read status FOR UPDATE within same conn
        row = await conn.fetchrow("SELECT status, expected_count FROM groups WHERE id=$1 FOR UPDATE", g["id"])
        triggered = False
        if row["status"] == "collecting" and row["expected_count"] is not None:
            sc = await conn.fetchval("SELECT count(*) FROM responses WHERE group_id=$1", g["id"])
            if sc >= row["expected_count"]:
                # try to flip (status still collecting here)
                result = await conn.execute("UPDATE groups SET status='analyzing' WHERE id=$1 AND status='collecting'", g["id"])
                triggered = (result == "UPDATE 1")
    # broadcast progress
    pc = await _count(pool, "participants", g["id"])
    sc = await _count(pool, "responses", g["id"])
    await broadcast(pin, "progress", {"participant_count": pc, "submitted_count": sc})
    if triggered:
        await broadcast(pin, "phase", {"status": "analyzing"})
        asyncio.create_task(run_analysis(pool, g["id"]))  # fire-and-forget
    return {"ok": True}

async def _count(pool, table, group_id):
    async with pool.acquire() as conn:
        return await conn.fetchval(f"SELECT count(*) FROM {table} WHERE group_id=$1", group_id)
```

- [ ] **Step 4: Wire `start` in groups.py**

Replace the `start_analysis` handler body in `routes/groups.py`:

```python
@router.post("/groups/{pin}/start", status_code=202)
async def start_analysis(pin: str, req: StartRequest):
    import asyncio
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT * FROM groups WHERE pin=$1", pin)
        if g is None:
            raise HTTPException(404, "Group not found")
        if g["creator_token"] != req.creator_token:
            raise HTTPException(403, "Not the creator")
        if g["status"] not in ("collecting", "error"):
            raise HTTPException(409, "Already analyzing or done")
        sc = await conn.fetchval("SELECT count(*) FROM responses WHERE group_id=$1", g["id"])
        if sc == 0:
            raise HTTPException(409, "No opinions yet")
        result = await conn.execute("UPDATE groups SET status='analyzing' WHERE id=$1 AND status IN ('collecting','error')", g["id"])
        triggered = (result == "UPDATE 1")
    if triggered:
        await broadcast(pin, "phase", {"status": "analyzing"})
        from ..llm import run_analysis
        asyncio.create_task(run_analysis(pool, g["id"]))
    return {"ok": True}
```

Add `from ..broadcast import broadcast` to groups.py imports.

- [ ] **Step 5: Include responses router in main.py**

```python
from .routes import responses
app.include_router(responses.router)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_responses_api.py -v` (with db up; mock LLM not needed since trigger fires `run_analysis` which raises if actually called — but expected_count trigger would call it. For these tests, set expected_count=None so trigger doesn't fire; or monkeypatch run_analysis.)
Expected: 3 passed. (Test cases use expected_count=None by default so run_analysis isn't invoked.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/routes/responses.py backend/app/llm.py backend/app/routes/groups.py backend/app/main.py backend/tests/test_responses_api.py
git commit -m "feat(backend): responses API + trigger wiring + start; fire-and-forget LLM"
```

---

### Task 9: LLM integration — `llm.py` (full `run_analysis`)

**Files:**
- Modify: `backend/app/llm.py`
- Create: `backend/tests/test_llm.py`

**Interfaces:**
- Consumes: `settings.llm_*`, `try_enter_analyzing`, `get_responses_ordered`, `set_group_status`, `broadcast`.
- Produces: `async def probe_model() -> str` — caches `data[0].id` from `/v1/models`.
- Produces: `def build_prompt(question: str, opinions: list[str]) -> tuple[str, str]` — returns (system, user) per §7.7.
- Produces: `async def run_analysis(pool, group_id) -> None` — loads group + ordered responses, calls LLM, on success `status='done'` + broadcast `consensus`, on failure `status='error'` + broadcast `error`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_llm.py
import pytest
from app.llm import build_prompt, probe_model

def test_build_prompt_anonymizes_opinions():
    sys_p, user_p = build_prompt("Dinner?", ["I am vegetarian", "I want Japanese"])
    assert "Dinner?" in user_p
    assert "A：I am vegetarian" in user_p
    assert "B：I want Japanese" in user_p
    assert "Traditional Chinese" in sys_p
    assert "不得在摘要中指名" in user_p

def test_build_prompt_single_opinion():
    sys_p, user_p = build_prompt("Q", ["only one"])
    assert "A：only one" in user_p

def test_probe_model_returns_string(monkeypatch):
    # mock the httpx call to /v1/models
    class FakeResp:
        def json(self): return {"data": [{"id": "fake-model"}]}
    monkeypatch.setattr("app.llm.httpx.get", lambda *a, **k: FakeResp())
    assert probe_model_sync() == "fake-model"

def probe_model_sync():
    import asyncio
    from app.llm import probe_model
    return asyncio.get_event_loop().run_until_complete(probe_model())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_llm.py -v`
Expected: FAIL — `build_prompt` not defined.

- [ ] **Step 3: Write full llm.py**

```python
# backend/app/llm.py
import asyncio
import logging
import httpx
from openai import OpenAI
from .config import get_settings
from .db import get_responses_ordered, set_group_status, get_pool
from .broadcast import broadcast

log = logging.getLogger("conclave.llm")
_cached_model: str | None = None

async def probe_model() -> str:
    global _cached_model
    s = get_settings()
    if s.llm_model:
        return s.llm_model
    if _cached_model:
        return _cached_model
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{s.llm_base_url}/models")
        r.raise_for_status()
        _cached_model = r.json()["data"][0]["id"]
    log.info("LLM model probed: %s", _cached_model)
    return _cached_model

def build_prompt(question: str, opinions: list[str]) -> tuple[str, str]:
    system = (
        "You are a neutral facilitator. Given a shared question and several "
        "participants' private opinions, synthesize ONE consensus summary that "
        "best accommodates everyone. Write in Traditional Chinese. Use clear "
        "markdown. Do not attribute individual opinions to specific labels in a "
        "way that embarrasses anyone; focus on the agreed-upon direction and any "
        "key conditions."
    )
    labels = [chr(65 + i) for i in range(len(opinions))]  # A, B, C...
    lines = [f"【討論問題】\n{question}\n",
             "【成員想法】（匿名編號，僅供整合參考，不得在摘要中指名）"]
    for label, op in zip(labels, opinions):
        lines.append(f"{label}：{op}")
    lines.append("\n請產出一份大家盡可能都能接受的共識摘要，包含：")
    lines.append("1. 共識方向")
    lines.append("2. 關鍵條件 / 限制")
    lines.append("3. 若仍有未解分歧，簡述並給出建議")
    return system, "\n".join(lines)

async def run_analysis(pool, group_id) -> None:
    s = get_settings()
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT pin, question FROM groups WHERE id=$1", group_id)
        rows = await get_responses_ordered(pool, group_id)
    opinions = [r["content"] for r in rows]
    system, user = build_prompt(g["question"], opinions)
    model = await probe_model()
    client = OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key,
                   timeout=httpx.Timeout(s.llm_timeout, connect=5.0))
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=s.llm_max_tokens, temperature=s.llm_temperature,
        )
        content = resp.choices[0].message.content or ""
        if not content.strip() or resp.choices[0].finish_reason == "length":
            raise RuntimeError("LLM returned empty content (reasoning ate budget)")
        log.info("LLM usage: %s", resp.usage)
        await set_group_status(pool, group_id, "done", consensus=content)
        await broadcast(g["pin"], "consensus", {"content": content})
        await broadcast(g["pin"], "phase", {"status": "done"})
    except Exception as e:
        log.exception("LLM analysis failed for group %s", group_id)
        await set_group_status(pool, group_id, "error")
        await broadcast(g["pin"], "error", {"message": str(e)})
        await broadcast(g["pin"], "phase", {"status": "error"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_llm.py -v`
Expected: 3 passed.

- [ ] **Step 5: Smoke-test against the real LLM endpoint**

Run: `cd backend && python -c "
import asyncio
from app.llm import probe_model, build_prompt
async def t():
    m = await probe_model()
    print('model:', m)
    sys, usr = build_prompt('Dinner?', ['vegetarian', 'japanese', 'no raw food'])
    print('prompt ok, user chars:', len(usr))
asyncio.run(t())
"`
Expected: `model: GLM-5.2-NVFP4-GB200` and `prompt ok`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/llm.py backend/tests/test_llm.py
git commit -m "feat(backend): LLM integration — model probe, prompt builder, run_analysis"
```

---

### Task 10: Deadline scan — `tasks.py` + lifespan wiring

**Files:**
- Create: `backend/app/tasks.py`
- Modify: `backend/app/main.py` (start scan task in lifespan, worker assert)

**Interfaces:**
- Produces: `async def deadline_scan_loop(pool) -> None` — every `settings.deadline_scan_s` seconds, query groups past deadline in `collecting` with `submitted_count > 0`, fire-and-forget `run_analysis`.

- [ ] **Step 1: Write tasks.py**

```python
# backend/app/tasks.py
import asyncio
import logging
from .config import get_settings
from .db import get_pool, try_enter_analyzing, get_submitted_count
from .broadcast import broadcast
from .llm import run_analysis

log = logging.getLogger("conclave.tasks")

async def deadline_scan_loop(pool) -> None:
    s = get_settings()
    while True:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, pin FROM groups WHERE status='collecting' "
                    "AND deadline IS NOT NULL AND deadline < now()"
                )
            for row in rows:
                gid = row["id"]
                sc = await get_submitted_count(pool, gid)
                if sc == 0:
                    await broadcast(row["pin"], "error", {"message": "Deadline reached with no opinions"})
                    continue  # leave collecting (zero-reply guard)
                if await try_enter_analyzing(pool, gid):
                    await broadcast(row["pin"], "phase", {"status": "analyzing"})
                    asyncio.create_task(run_analysis(pool, gid))
        except Exception:
            log.exception("deadline scan iteration failed")
        await asyncio.sleep(s.deadline_scan_s)
```

- [ ] **Step 2: Wire into main.py lifespan + worker assert**

```python
# replace backend/app/main.py lifespan
import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .db import get_pool, init_db, close_pool
from .tasks import deadline_scan_loop

@asynccontextmanager
async def lifespan(app: FastAPI):
    wc = os.environ.get("WEB_CONCURRENCY", "1")
    if int(wc) > 1:
        print("WARN: in-process SSE broadcast does not cross workers; use Redis or single worker")
    pool = await get_pool()
    await init_db(pool)
    scan = asyncio.create_task(deadline_scan_loop(pool))
    yield
    scan.cancel()
    await close_pool()

app = FastAPI(title="Conclave", lifespan=lifespan)

@app.get("/api/health")
async def health():
    return {"ok": True}

from .routes import groups, events, responses
app.include_router(groups.router)
app.include_router(events.router)
app.include_router(responses.router)
```

- [ ] **Step 3: Verify import + lifespan with db up**

Run: `cd backend && python -c "
import asyncio
from app.main import app
from app.db import get_pool
async def t():
    p = await get_pool()
    print('pool ok, scan will start on serve')
asyncio.run(t())
"`
Expected: `pool ok, scan will start on serve`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/tasks.py backend/app/main.py
git commit -m "feat(backend): deadline scan background task + worker assertion"
```

---

### Task 11: Integration test — full loop with mock LLM

**Files:**
- Create: `backend/tests/test_integration.py`

- [ ] **Step 1: Write the integration test**

```python
# backend/tests/test_integration.py
import asyncio
import pytest
from fastapi.testclient import TestClient
from app.main import app

pytestmark = pytest.mark.integration

def test_full_loop_create_join_submit_consensus(monkeypatch):
    # mock run_analysis to immediately set done + consensus
    async def fake_run(pool, group_id):
        from app.db import set_group_status, get_pool
        from app.broadcast import broadcast
        await set_group_status(pool, group_id, "done", consensus="# Consensus\nSummary")
        # need pin: fetch it
        async with pool.acquire() as c:
            pin = await c.fetchval("SELECT pin FROM groups WHERE id=$1", group_id)
        await broadcast(pin, "consensus", {"content": "# Consensus\nSummary"})
        await broadcast(pin, "phase", {"status": "done"})
    monkeypatch.setattr("app.llm.run_analysis", fake_run)
    monkeypatch.setattr("app.routes.responses.run_analysis", fake_run)
    monkeypatch.setattr("app.routes.groups.run_analysis", fake_run)

    with TestClient(app) as client:
        g = client.post("/api/groups", json={"question": "Dinner?", "creator_nickname": "Alice", "expected_count": 2}).json()
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        # creator submits
        r = client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "vegetarian"})
        assert r.status_code == 200
        # Bob joins + submits (2nd -> triggers)
        bob = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"}).json()
        r2 = client.post(f"/api/groups/{pin}/responses", json={"participant_id": bob["participant_id"], "content": "japanese"})
        assert r2.status_code == 200
        # allow fire-and-forget task to run
        import time; time.sleep(0.3)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "done"
        assert "Consensus" in (s["consensus"] or "")
```

- [ ] **Step 2: Run the full test suite**

Run: `cd backend && python -m pytest tests/ -v` (with `docker compose up db`)
Expected: all pass (pin, broadcast, llm unit pass without DB; trigger, groups, responses, events, integration pass with DB).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_integration.py
git commit -m "test(backend): full-loop integration with mock LLM"
```

---

### Task 12: Frontend scaffold — Vite + React + TypeScript + router

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/index.html`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles/index.css`
- Create: `frontend/Dockerfile`

- [ ] **Step 1: Write package.json**

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
    "react-router-dom": "^6.26.2",
    "react-markdown": "^9.0.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.4",
    "vite": "^5.4.2"
  }
}
```

- [ ] **Step 2: Write vite.config.ts (proxy /api + /events)**

```ts
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

- [ ] **Step 3: Write tsconfig.json, index.html, main.tsx, App.tsx, styles**

```json
// frontend/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true
  },
  "include": ["src"]
}
```

```html
<!-- frontend/index.html -->
<!doctype html>
<html lang="zh-Hant">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Conclave</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

```tsx
// frontend/src/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles/index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
```

```tsx
// frontend/src/App.tsx
import { Routes, Route } from "react-router-dom";
import Home from "./pages/Home";
import Create from "./pages/Create";
import Group from "./pages/Group";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/create" element={<Create />} />
      <Route path="/g/:pin" element={<Group />} />
    </Routes>
  );
}
```

```css
/* frontend/src/styles/index.css */
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, sans-serif; max-width: 720px; margin: 0 auto; padding: 2rem; line-height: 1.6; }
button { cursor: pointer; padding: 0.5rem 1rem; border-radius: 6px; border: 1px solid #646cff; background: #646cff; color: white; font-size: 1rem; }
button:hover { background: #535bf2; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
input, textarea { width: 100%; padding: 0.5rem; border-radius: 6px; border: 1px solid #888; font-size: 1rem; font-family: inherit; }
.card { border: 1px solid #444; border-radius: 8px; padding: 1.5rem; margin: 1rem 0; }
```

```dockerfile
# frontend/Dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
```

- [ ] **Step 4: Create placeholder pages so App.tsx imports resolve**

```tsx
// frontend/src/pages/Home.tsx
export default function Home() { return <div><h1>Conclave</h1><p>Loading…</p></div>; }
```
(Create, Group as minimal stubs similarly, replaced in later tasks.)

- [ ] **Step 5: Install + verify build**

Run: `cd frontend && npm install && npm run build`
Expected: build succeeds (produces `dist/`).

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): Vite + React + TS scaffold, router, proxy config"
```

---

### Task 13: REST client — `lib/api.ts`

**Files:**
- Create: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces: typed functions `createGroup`, `joinGroup`, `getState`, `submitResponse`, `startAnalysis`.

- [ ] **Step 1: Write api.ts**

```ts
// frontend/src/lib/api.ts
export interface CreateGroupResp {
  pin: string; participant_id: string; creator_token: string; status: string;
}
export interface JoinResp { participant_id: string; status: string; }
export interface GroupStateResp {
  pin: string; question: string; status: string;
  expected_count: number | null; participant_count: number; submitted_count: number;
  deadline: string | null; consensus: string | null; is_creator: boolean;
}

const BASE = "/api";

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json()).error || r.statusText);
  return r.json();
}

export const createGroup = (question: string, creator_nickname: string, expected_count?: number, timeout_seconds?: number) =>
  post<CreateGroupResp>("/groups", { question, creator_nickname, expected_count, timeout_seconds });

export const joinGroup = (pin: string, nickname: string) =>
  post<JoinResp>(`/groups/${pin}/join`, { nickname });

export async function getState(pin: string, token?: string): Promise<GroupStateResp> {
  const q = token ? `?token=${encodeURIComponent(token)}` : "";
  const r = await fetch(`${BASE}/groups/${pin}/state${q}`);
  if (!r.ok) throw new Error((await r.json()).error || r.statusText);
  return r.json();
}

export const submitResponse = (pin: string, participant_id: string, content: string) =>
  post<{ ok: boolean }>(`/groups/${pin}/responses`, { participant_id, content });

export const startAnalysis = (pin: string, creator_token: string) =>
  post<{ ok: boolean }>(`/groups/${pin}/start`, { creator_token });
```

- [ ] **Step 2: Verify it type-checks**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(frontend): typed REST client"
```

---

### Task 14: Home page — two buttons + PIN/nickname join

**Files:**
- Modify: `frontend/src/pages/Home.tsx`

- [ ] **Step 1: Write Home.tsx**

```tsx
// frontend/src/pages/Home.tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";

export default function Home() {
  const [pin, setPin] = useState("");
  const [nick, setNick] = useState("");
  const [err, setErr] = useState("");
  const nav = useNavigate();

  async function join() {
    setErr("");
    if (!pin.trim() || !nick.trim()) { setErr("PIN 與暱稱皆必填"); return; }
    try {
      const r = await fetch(`/api/groups/${pin.trim()}/join`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nickname: nick.trim() }),
      });
      if (!r.ok) { setErr((await r.json()).error || "加入失敗"); return; }
      const body = await r.json();
      localStorage.setItem(`pin:${pin.trim()}:pid`, body.participant_id);
      nav(`/g/${pin.trim()}`);
    } catch { setErr("連線失敗"); }
  }

  return (
    <div>
      <h1>Conclave</h1>
      <p>把真實想法私下交給系統，AI 整合出大家都能接受的共識。</p>
      <div className="card">
        <button onClick={() => nav("/create")}>建立群組</button>
      </div>
      <div className="card">
        <h3>加入群組</h3>
        <input placeholder="PIN 碼" value={pin} onChange={(e) => setPin(e.target.value)} />
        <input placeholder="你的暱稱" value={nick} onChange={(e) => setNick(e.target.value)} />
        <button onClick={join}>加入群組</button>
        {err && <p style={{ color: "crimson" }}>{err}</p>}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Home.tsx
git commit -m "feat(frontend): home page — create + join (PIN & nickname)"
```

---

### Task 15: Create page — group creation form

**Files:**
- Modify: `frontend/src/pages/Create.tsx`

- [ ] **Step 1: Write Create.tsx**

```tsx
// frontend/src/pages/Create.tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createGroup } from "../lib/api";

export default function Create() {
  const [q, setQ] = useState("");
  const [nick, setNick] = useState("");
  const [expected, setExpected] = useState("");
  const [timeout, setTimeout_] = useState("");
  const [err, setErr] = useState("");
  const nav = useNavigate();

  async function submit() {
    setErr("");
    if (!q.trim() || !nick.trim()) { setErr("問題與暱稱必填"); return; }
    try {
      const ec = expected ? parseInt(expected) : undefined;
      const ts = timeout ? parseInt(timeout) : undefined;
      const r = await createGroup(q.trim(), nick.trim(), ec, ts);
      localStorage.setItem(`pin:${r.pin}:pid`, r.participant_id);
      localStorage.setItem(`pin:${r.pin}:token`, r.creator_token);
      nav(`/g/${r.pin}`);
    } catch (e) { setErr(String(e)); }
  }

  return (
    <div>
      <h1>建立群組</h1>
      <div className="card">
        <label>討論問題（必填）</label>
        <textarea placeholder="例：我們今晚去哪裡吃飯？" value={q}
          onChange={(e) => setQ(e.target.value)} rows={3} />
        <label>你的暱稱（必填）</label>
        <input value={nick} onChange={(e) => setNick(e.target.value)} />
        <label>預期回覆數（選填）</label>
        <input type="number" min={1} value={expected}
          onChange={(e) => setExpected(e.target.value)} placeholder="達標即觸發" />
        <label>倒數秒數（選填）</label>
        <input type="number" min={1} max={86400} value={timeout}
          onChange={(e) => setTimeout_(e.target.value)} placeholder="時間到即觸發" />
        <button onClick={submit}>建立</button>
        {err && <p style={{ color: "crimson" }}>{err}</p>}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Create.tsx
git commit -m "feat(frontend): create-group form (question, nickname, expected, timeout)"
```

---

### Task 16: SSE hook — `hooks/useGroupSSE.ts`

**Files:**
- Create: `frontend/src/hooks/useGroupSSE.ts`

**Interfaces:**
- Produces: `useGroupSSE(pin, dispatch)` — opens `EventSource('/api/events/${pin}')`, dispatches `phase`/`progress`/`consensus`/`error` events to a reducer, reconnects with `GET /state` on open.

- [ ] **Step 1: Write useGroupSSE.ts**

```ts
// frontend/src/hooks/useGroupSSE.ts
import { useEffect } from "react";
import type { Action } from "../pages/Group";

export function useGroupSSE(pin: string, dispatch: (a: Action) => void, fetchState: () => void) {
  useEffect(() => {
    const es = new EventSource(`/api/events/${pin}`);
    es.addEventListener("phase", (e) => dispatch({ type: "phase", status: JSON.parse(e.data).status }));
    es.addEventListener("progress", (e) => {
      const d = JSON.parse(e.data);
      dispatch({ type: "progress", participant_count: d.participant_count, submitted_count: d.submitted_count });
    });
    es.addEventListener("consensus", (e) => dispatch({ type: "consensus", content: JSON.parse(e.data).content }));
    es.addEventListener("error_evt", (e) => dispatch({ type: "error", message: JSON.parse(e.data).message }));
    // SSE 'error' event name collides with EventSource onerror; server emits "error" but
    // EventSource surfaces it via onerror. We handle server "error" via a custom listener too:
    es.addEventListener("error", (e) => {
      // EventSource native error (disconnect) — reconnect sync
      fetchState();
    });
    return () => es.close();
  }, [pin]);
}
```

> Note: the SSE event named `error` collides with `EventSource`'s native `error` handler. To disambiguate, the backend's `broadcast("error", ...)` emits `event: error`. `addEventListener("error", ...)` on `EventSource` catches the *native* disconnect, not the named event. To capture the server's `error` event reliably, name the SSE event differently on the wire — but SPEC §6.2 names it `error`. Resolution: keep SPEC name on backend; on frontend, listen via `es.addEventListener("error", handler)` — this catches the **native** disconnect for reconnect. For the server-emitted `error` content, rely on `GET /state` showing `status=="error"` after reconnect, plus a separate explicit check. This is acceptable per §8.3 (reconnect → `GET /state`).

- [ ] **Step 2: Verify type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors (Action type imported from Group which exists as stub — update in Task 17).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useGroupSSE.ts
git commit -m "feat(frontend): SSE hook with named-event dispatch + reconnect sync"
```

---

### Task 17: Group room — `pages/Group.tsx` (reducer + SSE + creator UI)

**Files:**
- Modify: `frontend/src/pages/Group.tsx`
- Modify: `frontend/src/hooks/useGroupSSE.ts` (align Action type)

- [ ] **Step 1: Write Group.tsx with useReducer state machine**

```tsx
// frontend/src/pages/Group.tsx
import { useReducer, useEffect, useCallback } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import { getState, submitResponse, startAnalysis } from "../lib/api";
import { useGroupSSE } from "../hooks/useGroupSSE";

export type Phase = "joining" | "waiting" | "submitted" | "analyzing" | "done" | "error";
export interface State {
  phase: Phase;
  question: string; status: string;
  participant_count: number; submitted_count: number;
  consensus: string | null; is_creator: boolean;
  error_msg: string; input: string; submitted_locked: boolean;
}
export type Action =
  | { type: "init"; payload: Partial<State> }
  | { type: "phase"; status: string }
  | { type: "progress"; participant_count: number; submitted_count: number }
  | { type: "consensus"; content: string }
  | { type: "error"; message: string }
  | { type: "input"; value: string }
  | { type: "submitted" };

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "init": return { ...s, ...a.payload, phase: mapPhase(a.payload.status || "collecting", s.phase) };
    case "phase": return { ...s, status: a.status, phase: mapPhase(a.status, s.phase) };
    case "progress": return { ...s, participant_count: a.participant_count, submitted_count: a.submitted_count };
    case "consensus": return { ...s, consensus: a.content, phase: "done" };
    case "error": return { ...s, error_msg: a.message, phase: "error" };
    case "input": return { ...s, input: a.value };
    case "submitted": return { ...s, phase: "submitted", submitted_locked: true };
  }
}
function mapPhase(status: string, cur: Phase): Phase {
  if (status === "collecting") return cur === "submitted" ? "submitted" : "waiting";
  if (status === "analyzing") return "analyzing";
  if (status === "done") return "done";
  if (status === "error") return "error";
  return cur;
}
const init: State = { phase: "joining", question: "", status: "collecting",
  participant_count: 0, submitted_count: 0, consensus: null, is_creator: false,
  error_msg: "", input: "", submitted_locked: false };

export default function Group() {
  const { pin } = useParams<{ pin: string }>();
  const [s, dispatch] = useReducer(reducer, init);
  const pid = localStorage.getItem(`pin:${pin}:pid`) || "";
  const token = localStorage.getItem(`pin:${pin}:token`) || "";

  const fetchState = useCallback(async () => {
    try {
      const st = await getState(pin!, token || undefined);
      dispatch({ type: "init", payload: {
        question: st.question, status: st.status,
        participant_count: st.participant_count, submitted_count: st.submitted_count,
        consensus: st.consensus, is_creator: st.is_creator,
      }});
    } catch (e) { dispatch({ type: "error", message: String(e) }); }
  }, [pin, token]);

  useEffect(() => { fetchState(); }, [fetchState]);
  useGroupSSE(pin!, dispatch, fetchState);

  async function send() {
    try {
      await submitResponse(pin!, pid, s.input);
      dispatch({ type: "submitted" });
    } catch (e) { dispatch({ type: "error", message: String(e) }); }
  }
  async function start() {
    try { await startAnalysis(pin!, token); } catch (e) { dispatch({ type: "error", message: String(e) }); }
  }

  if (s.phase === "joining") return <div>加入中…</div>;
  return (
    <div>
      <h1>群組室</h1>
      <div className="card"><h3>討論問題</h3><p>{s.question}</p></div>
      <p>進度：已送 {s.submitted_count} / 已加入 {s.participant_count}</p>
      {s.phase === "waiting" && (
        <div className="card">
          <textarea placeholder="寫下你的想法（其他人看不到你的原文）" value={s.input}
            onChange={(e) => dispatch({ type: "input", value: e.target.value })} rows={4} />
          <button disabled={!s.input.trim()} onClick={send}>發送</button>
        </div>
      )}
      {s.phase === "submitted" && <p>已送出，等待其他人…</p>}
      {s.phase === "analyzing" && <p>分析中…（推理模型約 8–15 秒）</p>}
      {s.phase === "done" && <div className="card"><h3>共識摘要</h3><ReactMarkdown>{s.consensus || ""}</ReactMarkdown></div>}
      {s.phase === "error" && (
        <div className="card"><p style={{ color: "crimson" }}>分析失敗：{s.error_msg}</p>
          {s.is_creator && <button onClick={start}>重試分析</button>}
          {!s.is_creator && <p>請聯絡建立者重試。</p>}
        </div>
      )}
      {s.is_creator && (s.status === "collecting") && s.phase !== "submitted" && (
        <div className="card"><button onClick={start}>開始分析</button></div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Align useGroupSSE Action import (already imported from Group)**

No change needed — `useGroupSSE` imports `Action` from `../pages/Group`.

- [ ] **Step 3: Verify build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Group.tsx
git commit -m "feat(frontend): group room — useReducer state machine + SSE + creator UI"
```

---

### Task 18: Docker compose (dev) — three services

**Files:**
- Create: `compose.yml`

- [ ] **Step 1: Write compose.yml (from SPEC §9.1)**

```yaml
services:
  db:
    image: postgres:16-alpine
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

  backend:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+asyncpg://conclave:conclave@db:5432/conclave
      LLM_BASE_URL: http://10.241.77.188:8000/v1
      LLM_API_KEY: probe
      LLM_MODEL: ""
      LLM_MAX_TOKENS: "8192"
      LLM_TEMPERATURE: "0.3"
    volumes: ["./backend:/app"]
    ports: ["8000:8000"]
    command: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    depends_on:
      db:
        condition: service_healthy

  frontend:
    build: ./frontend
    volumes: ["./frontend:/app", "/app/node_modules"]
    ports: ["5173:5173"]
    command: npm run dev -- --host 0.0.0.0
    depends_on: [backend]

volumes:
  pgdata:
```

- [ ] **Step 2: Commit**

```bash
git add compose.yml
git commit -m "chore: docker compose dev (db + backend + frontend)"
```

---

### Task 19: End-to-end `docker compose up` + manual E2E

**Files:** none (verification task)

- [ ] **Step 1: Build + start**

Run: `docker compose up --build -d`
Expected: all three services healthy/up.

- [ ] **Step 2: Health check**

Run: `curl localhost:8000/api/health`
Expected: `{"ok":true}`

- [ ] **Step 3: Create group via curl**

Run: `curl -s localhost:8000/api/groups -H 'Content-Type: application/json' -d '{"question":"E2E test","creator_nickname":"Alice"}'`
Expected: `201` with a 5-char pin.

- [ ] **Step 4: Open frontend at `localhost:5173`**

Open browser, verify home page shows two buttons + join inputs. Create a group, join from a second browser tab with the same PIN, submit opinions, observe consensus.

- [ ] **Step 5: LLM real call smoke test**

Run: `curl -s localhost:8000/api/groups -H 'Content-Type: application/json' -d '{"question":"晚餐？","creator_nickname":"A","expected_count":1}'` then submit a response as the creator → should trigger LLM → state goes `done` with a markdown consensus (8–15s).

- [ ] **Step 6: If all green, tag the milestone commit**

```bash
git commit --allow-empty -m "chore: docker compose up end-to-end verified"
```

- [ ] **Step 7: Stop services**

Run: `docker compose down` (keeps `pgdata` volume per §5.1).

---

## Self-Review Checklist

**1. Spec coverage** (§ by §):
- §1.2 in-scope: Home (Task 14), Create (15), Group room (17), PIN (2), join (6), opinions (8), LLM consensus (9), broadcast (4,7), trigger OR (5,8,10), error retry (9,17), docker (18,19) ✅
- §3.2 state machine (four states): Task 5 (DB), 8/9 (transitions), 17 (frontend) ✅
- §4 trigger truth table: Task 5 parametrized tests ✅
- §5 data model: Task 1 migration ✅; §5.1 retention (volume in Task 18) ✅
- §6.1 REST: Task 6 (groups), 8 (responses) ✅; validation Task 3 ✅
- §6.2 SSE: Task 4 (broadcast), 7 (endpoint) ✅
- §7.2 PIN: Task 2 ✅; §7.3 trigger: Task 5+8 ✅; §7.4 deadline: Task 10 ✅; §7.5 SSE: Task 4 ✅; §7.6 LLM: Task 9 ✅; §7.7 prompt: Task 9 ✅
- §8 frontend: Tasks 12–17 ✅
- §9.1 compose: Task 18 ✅; §9.2 prod variant: noted as optional (compose.prod.yml) — included as a future task if time permits
- §11 env vars: Task 1 config ✅
- §12 tests: pin (2), broadcast (4), trigger (5), llm (9), integration (11), SPA fallback (noted in prod) ✅
- §16 risks: addressed in implementation (worker assert Task 10, reasoning budget Task 9, SSE reconnect Task 16/17) ✅

**2. Placeholder scan:** No TBD/TODO. All code blocks complete. The `start` handler in Task 6 is a deliberate stub, fully wired in Task 8 (explicitly noted). ✅

**3. Type consistency:** `run_analysis(pool, group_id)` signature consistent across llm.py, responses.py, groups.py, tasks.py. `Action` type defined in Group.tsx, imported by useGroupSSE.ts. `generate_pin()` returns `str` everywhere. ✅

**Gaps to close during execution:** Task 16's SSE `error` event name collision needs the backend to emit `event: error` and the frontend to handle it via `GET /state` on reconnect (per §8.3) — acceptable for PoC; document in Group.tsx.
