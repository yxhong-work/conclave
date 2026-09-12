# backend/app/routes/groups.py
import secrets, asyncio, asyncpg
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Query
from ..db import get_pool, try_enter_analyzing, get_submitted_count
from ..broadcast import broadcast
from ..pin import generate_pin
from ..models import (
    CreateGroupRequest, JoinRequest, StartRequest, LeaveRequest,
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
                    "INSERT INTO groups (pin, question, creator_token, expected_count, deadline, max_rounds, adaptive_questions) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
                    pin, req.question, creator_token, req.expected_count, deadline,
                    req.max_rounds, req.adaptive_questions,
                )
                p = await conn.fetchrow(
                    "INSERT INTO participants (group_id, nickname, member_seq) VALUES ($1, $2, 1) RETURNING id",
                    g["id"], req.creator_nickname,
                )
                # Seed round 1 row so the history timeline starts immediately.
                await conn.execute(
                    "INSERT INTO rounds (group_id, round_number, question) VALUES ($1, 1, $2) "
                    "ON CONFLICT (group_id, round_number) DO NOTHING",
                    g["id"], req.question,
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
    # Assign a stable member_seq (join order, cross-round identity for the stance call).
    # Fetched separately to keep the UNIQUE(group_id, member_seq) race window tiny.
    from ..db import set_member_seq
    await set_member_seq(pool, g["id"], p["id"])
    # broadcast progress so all members see updated participant count in real-time
    pc = await _count(pool, "participants", g["id"])
    sc = await _count(pool, "responses", g["id"])
    await broadcast(pin, "progress", {"participant_count": pc, "submitted_count": sc})
    return JoinResponse(participant_id=str(p["id"]), status=g["status"])

async def _count(pool, table, group_id):
    async with pool.acquire() as conn:
        return await conn.fetchval(f"SELECT count(*) FROM {table} WHERE group_id=$1", group_id)

@router.get("/groups/{pin}/state", response_model=GroupState)
async def get_state(pin: str, token: str | None = Query(default=None)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow(
            "SELECT id, pin, question, creator_token, expected_count, deadline, status, "
            "consensus, current_round, max_rounds, adaptive_questions FROM groups WHERE pin=$1", pin,
        )
        if g is None:
            raise HTTPException(404, "Group not found")
        pc = await conn.fetchval("SELECT count(*) FROM participants WHERE group_id=$1", g["id"])
        sc = await conn.fetchval(
            "SELECT count(*) FROM responses WHERE group_id=$1 AND round_number=$2",
            g["id"], g["current_round"],
        )
        is_creator = token is not None and token == g["creator_token"]
        # Cooldown remaining (seconds) for opening the next round; null unless done.
        cooldown_remaining = None
        if g["status"] == "done" and g["current_round"] < g["max_rounds"]:
            last_analyzed = await conn.fetchval(
                "SELECT analyzed_at FROM rounds WHERE group_id=$1 AND round_number=$2",
                g["id"], g["current_round"],
            )
            if last_analyzed:
                elapsed = (datetime.now(timezone.utc) - last_analyzed).total_seconds()
                cooldown_remaining = max(0, NEXT_ROUND_COOLDOWN_S - int(elapsed))
        # ADAPTIVE_SPEC §7.1/§7.4: effective flag (global switch AND group flag)
        # + prepared-question count (done & openable & flag on → count for the
        # NEXT round; anything else → null). Count only — no content surface.
        from ..config import get_settings
        effective = (get_settings().adaptive_questions_enabled
                     and g["adaptive_questions"])
        member_question_count = None
        if effective and g["status"] == "done" and g["current_round"] < g["max_rounds"]:
            member_question_count = await conn.fetchval(
                "SELECT count(*) FROM member_questions WHERE group_id=$1 AND round_number=$2",
                g["id"], g["current_round"] + 1,
            )
        return GroupState(
            pin=g["pin"], question=g["question"], status=g["status"],
            expected_count=g["expected_count"], participant_count=pc, submitted_count=sc,
            deadline=g["deadline"].isoformat() if g["deadline"] else None,
            consensus=g["consensus"], is_creator=is_creator,
            current_round=g["current_round"], max_rounds=g["max_rounds"],
            cooldown_remaining=cooldown_remaining,
            adaptive_questions=effective,
            member_question_count=member_question_count,
        )

@router.post("/groups/{pin}/start", status_code=202)
async def start_analysis(pin: str, req: StartRequest):
    pool = await get_pool()
    group_id = None
    current_round = None
    async with pool.acquire() as conn:
        g = await conn.fetchrow(
            "SELECT id, creator_token, status, current_round FROM groups WHERE pin=$1", pin,
        )
        if g is None:
            raise HTTPException(404, "Group not found")
        if g["creator_token"] != req.creator_token:
            raise HTTPException(403, "Not the creator")
        if g["status"] not in ("collecting", "error"):
            raise HTTPException(409, "Already analyzing or done")
        sc = await conn.fetchval(
            "SELECT count(*) FROM responses WHERE group_id=$1 AND round_number=$2",
            g["id"], g["current_round"],
        )
        if sc == 0:
            raise HTTPException(409, "No opinions yet")
        group_id = g["id"]
        current_round = g["current_round"]
    triggered = await try_enter_analyzing(pool, group_id)
    if triggered:
        await broadcast(pin, "phase", {"status": "analyzing", "round": current_round})
        from ..llm import start_analysis_task
        start_analysis_task(pool, group_id)
    return {"ok": True}

@router.post("/groups/{pin}/leave")
async def leave_group(pin: str, req: LeaveRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow(
            "SELECT id, status, creator_token, current_round FROM groups WHERE pin=$1", pin,
        )
        if g is None:
            raise HTTPException(404, "Group not found")
        if g["status"] not in ("collecting", "done"):
            raise HTTPException(409, "Group already ended")
        p = await conn.fetchrow("SELECT id FROM participants WHERE id=$1 AND group_id=$2",
                                req.participant_id, g["id"])
        if p is None:
            raise HTTPException(404, "Participant not found")
        is_creator = req.creator_token is not None and req.creator_token == g["creator_token"]
        if is_creator:
            # Dissolve: set status=closed (SPEC §4 — prevents accidental reopening).
            await conn.execute(
                "UPDATE groups SET status='closed', consensus='群組已由建立者解散' WHERE id=$1",
                g["id"],
            )
        # Delete the participant (CASCADE removes their responses AND their
        # member_questions rows — no ghost data, ADAPTIVE_SPEC §5).
        await conn.execute("DELETE FROM participants WHERE id=$1", req.participant_id)
    if is_creator:
        # Dissolution terminates the group: future-round personal questions die
        # with it (§5.1 purge call site).
        from ..db import purge_future_member_questions
        await purge_future_member_questions(pool, g["id"], current_round=g["current_round"])
        await broadcast(pin, "consensus", {"content": "群組已由建立者解散"})
        await broadcast(pin, "round", {"round": 0, "question": "", "status": "closed"})
    else:
        pc = await _count(pool, "participants", g["id"])
        sc = await _count(pool, "responses", g["id"])
        await broadcast(pin, "progress", {"participant_count": pc, "submitted_count": sc})
    return {"ok": True, "dissolved": is_creator}

# ---------------------------------------------------------------------------
# Multi-round endpoints (SPEC MULTIROUND_SPEC.md §6.1)
# ---------------------------------------------------------------------------

import re
import logging
from ..db import (
    try_open_next_round, try_close_group, insert_round, get_rounds_history,
    get_round_consensus,
)
from ..models import OpenRoundRequest, CloseRoundRequest, RoundInfo, MyQuestionResponse

log = logging.getLogger("conclave.groups")

NEXT_ROUND_COOLDOWN_S = 10  # SPEC §4 anti-fatigue (short: creator-initiated, not auto)

def _seed_next_question(prev_consensus: str | None, prev_question: str) -> str:
    """Auto-seed the next round's question from the prior consensus's
    '未解分歧/建議' section; fall back to the prior question (SPEC §5.1)."""
    if not prev_consensus:
        return prev_question
    m = re.search(r"未解分歧|建議", prev_consensus)
    if not m:
        return prev_question
    # Take the text block following the heading (up to the next heading or ~200 chars).
    after = prev_consensus[m.end():]
    stop = re.search(r"\n#{1,6}\s", after)
    block = after[: stop.start()] if stop else after
    block = block.strip("：:。\n\r\t ")
    return block[:200] if block else prev_question


@router.post("/groups/{pin}/rounds/next", status_code=202)
async def open_next_round(pin: str, req: OpenRoundRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow(
            "SELECT id, creator_token, status, current_round, max_rounds, question, adaptive_questions FROM groups WHERE pin=$1", pin,
        )
        if g is None:
            raise HTTPException(404, "Group not found")
        if g["creator_token"] != req.creator_token:
            raise HTTPException(403, "Not the creator")
        if g["status"] != "done":
            raise HTTPException(409, "Round not done yet")
        if g["current_round"] >= g["max_rounds"]:
            raise HTTPException(409, "已達回合上限")
        # Cooldown: must be >= 30s since the previous round was analyzed.
        prev = await conn.fetchrow(
            "SELECT analyzed_at FROM rounds WHERE group_id=$1 AND round_number=$2",
            g["id"], g["current_round"],
        )
        if prev and prev["analyzed_at"]:
            elapsed = (datetime.now(timezone.utc) - prev["analyzed_at"]).total_seconds()
            if elapsed < NEXT_ROUND_COOLDOWN_S:
                raise HTTPException(409, f"冷卻中,請稍候({int(NEXT_ROUND_COOLDOWN_S - elapsed)}s)")
        # Determine the next round's question (ADAPTIVE_SPEC §4.3):
        # override (req.question non-empty) > member_questions (prepared at
        # analysis time) > Call Q ladder (anchor). With an override, the Call Q
        # ladder is NOT executed — no wasted 8-15s LLM call.
        prev_round = await get_round_consensus(pool, g["id"], g["current_round"])
        prev_consensus = prev_round["consensus"] if prev_round else None
        raw_q = (req.question or "").strip()
        override = bool(raw_q)
        if override:
            next_question = raw_q
        elif prev_consensus:
            # LLM question-gen from the prior consensus (gap #2). Sees ONLY the
            # public consensus (label-free); fail-open to the regex on any error.
            try:
                from ..llm import generate_next_question, redact
                next_question = await generate_next_question(prev_consensus, g["question"])
            except Exception as e:
                log.warning("LLM question-gen failed, using regex fallback: %s", redact(str(e)))
                next_question = _seed_next_question(prev_consensus, g["question"])
        else:
            next_question = _seed_next_question(prev_consensus, g["question"])
        # Optional new deadline; omitting clears it (subsequent rounds default open-ended).
        new_deadline = None
        if req.timeout_seconds is not None:
            new_deadline = datetime.now(timezone.utc) + timedelta(seconds=req.timeout_seconds)
        group_id = g["id"]
        next_round = g["current_round"] + 1
    # Insert the round row + atomically open it + set the new question/deadline,
    # all in one transaction so a gate failure leaves no dangling rounds row
    # (SPEC §6.1 — try_open_next_round is the atomic done->collecting gate).
    triggered = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO rounds (group_id, round_number, question) VALUES ($1, $2, $3) "
                "ON CONFLICT (group_id, round_number) DO NOTHING",
                group_id, next_round, next_question,
            )
            result = await conn.execute(
                "UPDATE groups SET status='collecting', current_round=$2, "
                "question=$3, deadline=$4 WHERE id=$1 AND status='done' AND current_round < max_rounds",
                group_id, next_round, next_question, new_deadline,
            )
            triggered = (result == "UPDATE 1")
            if triggered and override:
                # Creator override escape hatch (§4.3): clear ALL prepared
                # personal questions for the new round, inside the same
                # transaction, AFTER the gate rowcount=1. P writes are already
                # complete (done before open), so no late write can follow.
                from ..db import clear_member_questions
                await clear_member_questions(pool, group_id, next_round)
    if not triggered:
        raise HTTPException(409, "Could not open next round (race or gate)")
    # Delivered history is not kept (§5.1): only the current round's rows survive.
    from ..db import prune_delivered_member_questions
    await prune_delivered_member_questions(pool, group_id, opened_round=next_round)
    from ..config import get_settings
    adaptive_effective = get_settings().adaptive_questions_enabled and g["adaptive_questions"]
    await broadcast(pin, "round", {"round": next_round, "question": next_question,
                                   "status": "opened", "adaptive": adaptive_effective})
    return {"round": next_round, "question": next_question}


@router.post("/groups/{pin}/rounds/close")
async def close_round(pin: str, req: CloseRoundRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow(
            "SELECT id, creator_token, status, current_round FROM groups WHERE pin=$1", pin,
        )
        if g is None:
            raise HTTPException(404, "Group not found")
        if g["creator_token"] != req.creator_token:
            raise HTTPException(403, "Not the creator")
        if g["status"] != "done":
            raise HTTPException(409, "Round not done yet")
        group_id = g["id"]
    triggered = await try_close_group(pool, group_id)
    if not triggered:
        raise HTTPException(409, "Could not close (race or gate)")
    # Group terminated: future-round personal questions die with it (§5.1).
    from ..db import purge_future_member_questions
    await purge_future_member_questions(pool, group_id, current_round=g["current_round"])
    await broadcast(pin, "round", {"round": 0, "question": "", "status": "closed"})
    return {"ok": True}


@router.get("/groups/{pin}/rounds", response_model=list[RoundInfo])
async def list_rounds(pin: str):
    """Public-safe round history. NEVER returns stance_digest or research_brief (§5.6)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT id FROM groups WHERE pin=$1", pin)
        if g is None:
            raise HTTPException(404, "Group not found")
        rows = await get_rounds_history(pool, g["id"])
    return [
        RoundInfo(
            round_number=r["round_number"], question=r["question"],
            consensus=r["consensus"], stance_shift_summary=r["stance_shift_summary"],
            created_at=r["created_at"], analyzed_at=r["analyzed_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Adaptive per-member questioning (SPEC ADAPTIVE_SPEC.md §7)
# ---------------------------------------------------------------------------

_NOT_FOUND = "not found"  # unified 404 message constant (§7.2 rule 1)


@router.get("/groups/{pin}/my-question", response_model=MyQuestionResponse)
async def my_question(pin: str, participant_id: str | None = Query(default=None),
                      round: int | None = Query(default=None)):
    """The ONLY read path for a member's personalized question (§7.2).

    Resolution: group missing / effective flag off / round out of range →
    unified 404 ("not found"). Valid pid with a row → personal question;
    without → the round's anchor question with is_personal=false. An invalid
    pid is indistinguishable from a valid pid without a personal question
    (both get the anchor) — the one-bit oracle is reduced to "requires a valid
    UUID first" (§7.2 rule 5), NOT eliminated.
    """
    from ..config import get_settings
    from ..db import get_member_question
    pool = await get_pool()
    s = get_settings()
    async with pool.acquire() as conn:
        g = await conn.fetchrow(
            "SELECT id, question, status, current_round, max_rounds, adaptive_questions "
            "FROM groups WHERE pin=$1", pin,
        )
        effective = g is not None and s.adaptive_questions_enabled and g["adaptive_questions"]
        if g is None or not effective or participant_id is None:
            raise HTTPException(404, _NOT_FOUND)
        current_round = g["current_round"]
        r = round if round is not None else current_round
        if r < 1 or r > current_round + 1:
            raise HTTPException(404, _NOT_FOUND)
        if participant_id is not None:
            mq = await get_member_question(pool, g["id"], r, participant_id)
            if mq is not None:
                return MyQuestionResponse(round=r, question=mq, is_personal=True)
        # No personal row: resolve the anchor for the requested round.
        if r == current_round:
            anchor = g["question"]
        elif r < current_round:
            anchor = await conn.fetchval(
                "SELECT question FROM rounds WHERE group_id=$1 AND round_number=$2",
                g["id"], r,
            )
            if anchor is None:
                raise HTTPException(404, _NOT_FOUND)
        else:  # r == current_round + 1: next round not open yet, anchor not generated
            anchor = ""
        return MyQuestionResponse(round=r, question=anchor, is_personal=False)
