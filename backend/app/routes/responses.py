# backend/app/routes/responses.py
import asyncio
import asyncpg
from fastapi import APIRouter, HTTPException
from ..db import get_pool
from ..broadcast import broadcast
from ..models import SubmitResponseRequest

router = APIRouter(prefix="/api")

@router.post("/groups/{pin}/responses")
async def submit_response(pin: str, req: SubmitResponseRequest):
    pool = await get_pool()
    triggered = False
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT id, status, expected_count, current_round FROM groups WHERE pin=$1", pin)
        if g is None:
            raise HTTPException(404, "Group not found")
        if g["status"] != "collecting":
            raise HTTPException(409, "Submissions closed")
        current_round = g["current_round"]
        try:
            await conn.execute(
                "INSERT INTO responses (group_id, round_number, participant_id, content) "
                "VALUES ($1, $2, $3, $4)",
                g["id"], current_round, req.participant_id, req.content,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "Already submitted this round")
        # atomic check within the same transaction (round-scoped count, SPEC §6.2)
        row = await conn.fetchrow("SELECT status FROM groups WHERE id=$1 FOR UPDATE", g["id"])
        if row["status"] == "collecting" and g["expected_count"] is not None:
            sc = await conn.fetchval(
                "SELECT count(*) FROM responses WHERE group_id=$1 AND round_number=$2",
                g["id"], current_round,
            )
            if sc >= g["expected_count"]:
                result = await conn.execute(
                    "UPDATE groups SET status='analyzing' WHERE id=$1 AND status='collecting'",
                    g["id"],
                )
                triggered = (result == "UPDATE 1")
    # broadcast progress (round-scoped submitted_count, SPEC §6.2)
    pc = await _count(pool, "participants", g["id"])
    sc = await _round_count(pool, g["id"], current_round)
    await broadcast(pin, "progress", {"participant_count": pc, "submitted_count": sc, "round": current_round})
    if triggered:
        await broadcast(pin, "phase", {"status": "analyzing", "round": current_round})
        from ..llm import start_analysis_task  # late import for testability (SPEC §2)
        start_analysis_task(pool, g["id"])  # tracked fire-and-forget
    return {"ok": True}

async def _count(pool, table, group_id):
    async with pool.acquire() as conn:
        return await conn.fetchval(f"SELECT count(*) FROM {table} WHERE group_id=$1", group_id)

async def _round_count(pool, group_id, round_number):
    """Round-scoped response count for progress broadcasts (SPEC §6.2)."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM responses WHERE group_id=$1 AND round_number=$2",
            group_id, round_number,
        )
