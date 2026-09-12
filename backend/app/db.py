import asyncpg
import uuid
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
    # Run every migration in sorted order (001 before 002). All are idempotent.
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    for sql_path in sorted(migrations_dir.glob("*.sql")):
        await pool.execute(sql_path.read_text(encoding="utf-8"))

async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Group lookups (explicit columns — never SELECT *, §5.3)
# ---------------------------------------------------------------------------

async def get_group(pool: asyncpg.Pool, pin: str) -> asyncpg.Record | None:
    # Explicit columns: future private columns (stance_digest-adjacent) must never
    # leak into application memory via SELECT * (SPEC MULTIROUND §5.3).
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, pin, question, creator_token, expected_count, deadline, "
            "status, consensus, created_at, current_round, max_rounds, "
            "adaptive_questions "
            "FROM groups WHERE pin=$1",
            pin,
        )


async def get_group_by_id(pool: asyncpg.Pool, group_id) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, pin, question, creator_token, expected_count, deadline, "
            "status, consensus, created_at, current_round, max_rounds, "
            "adaptive_questions "
            "FROM groups WHERE id=$1",
            group_id,
        )


# ---------------------------------------------------------------------------
# State machine gates
# ---------------------------------------------------------------------------

async def try_enter_analyzing(pool: asyncpg.Pool, group_id) -> bool:
    """collecting|error -> analyzing. Unchanged from single-round (SPEC §4)."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE groups SET status='analyzing' WHERE id=$1 AND status IN ('collecting','error')",
            group_id,
        )
        return result == "UPDATE 1"


async def try_open_next_round(pool: asyncpg.Pool, group_id) -> bool:
    """done -> collecting (current_round++). Atomic, race-free (SPEC §4).
    Only this function transitions done->collecting."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE groups SET status='collecting', current_round=current_round+1 "
            "WHERE id=$1 AND status='done' AND current_round < max_rounds",
            group_id,
        )
        return result == "UPDATE 1"


async def try_close_group(pool: asyncpg.Pool, group_id) -> bool:
    """done -> closed (final terminal). Atomic (SPEC §4)."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE groups SET status='closed' WHERE id=$1 AND status='done'",
            group_id,
        )
        return result == "UPDATE 1"


# ---------------------------------------------------------------------------
# Round-scoped reads
# ---------------------------------------------------------------------------

async def get_submitted_count(pool: asyncpg.Pool, group_id, round_number: int | None = None) -> int:
    """Round-scoped submission count. round_number=None means current round."""
    async with pool.acquire() as conn:
        if round_number is None:
            return await conn.fetchval(
                "SELECT count(*) FROM responses r "
                "JOIN groups g ON g.id=r.group_id WHERE r.group_id=$1 AND r.round_number=g.current_round",
                group_id,
            )
        return await conn.fetchval(
            "SELECT count(*) FROM responses WHERE group_id=$1 AND round_number=$2",
            group_id, round_number,
        )


async def get_responses_ordered(pool: asyncpg.Pool, group_id):
    """Legacy: all responses (round-agnostic). Kept for backward compat; prefer
    get_round_responses_ordered for multi-round."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT content FROM responses WHERE group_id=$1 ORDER BY submitted_at",
            group_id,
        )


async def get_round_responses_ordered(pool: asyncpg.Pool, group_id, round_number: int):
    """Round-scoped responses WITH member_seq for stable stance-call labeling (SPEC §7.2)
    and participant_id for the adaptive P-stage member enumeration (ADAPTIVE_SPEC §4.2).
    Ordered by member_seq so stance labels (成員{member_seq}) are deterministic."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT r.content, p.member_seq, r.participant_id "
            "FROM responses r JOIN participants p ON r.participant_id=p.id "
            "WHERE r.group_id=$1 AND r.round_number=$2 "
            "ORDER BY p.member_seq, r.submitted_at",
            group_id, round_number,
        )


async def get_round_consensus(pool: asyncpg.Pool, group_id, round_number: int) -> asyncpg.Record | None:
    """Fetch a prior round's consensus + stance_digest + stance_shift_summary +
    research_brief + question for cross-round context (SPEC §7.2 step 3) and
    Jaccard cost control (§7.3). Returns None if round not found."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT consensus, stance_digest, stance_shift_summary, research_brief, question "
            "FROM rounds WHERE group_id=$1 AND round_number=$2",
            group_id, round_number,
        )


async def get_rounds_history(pool: asyncpg.Pool, group_id):
    """All rounds for the round-history timeline (GET /rounds). Public-safe columns
    only — NEVER select stance_digest or research_brief here (SPEC §5.6)."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT round_number, question, consensus, stance_shift_summary, "
            "created_at, analyzed_at "
            "FROM rounds WHERE group_id=$1 ORDER BY round_number",
            group_id,
        )


async def insert_round(pool: asyncpg.Pool, group_id, round_number: int, question: str) -> None:
    """Insert a new round row when opening round N+1 (SPEC §6.1 POST /rounds/next)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO rounds (group_id, round_number, question) VALUES ($1, $2, $3) "
            "ON CONFLICT (group_id, round_number) DO NOTHING",
            group_id, round_number, question,
        )


async def set_round_done(
    pool: asyncpg.Pool, group_id, round_number: int,
    consensus: str, stance_digest: str | None, stance_shift_summary: str | None,
    research_brief: str | None = None,
) -> None:
    """Write a completed round: rounds.consensus + stance_digest + stance_shift_summary +
    analyzed_at; AND groups.status='done' + groups.consensus (latest, backward compat).
    NEVER writes stance_digest to groups (SPEC §7.2 step 8)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE rounds SET consensus=$3, stance_digest=$4, stance_shift_summary=$5, "
                "analyzed_at=now() WHERE group_id=$1 AND round_number=$2",
                group_id, round_number, consensus, stance_digest, stance_shift_summary,
            )
            if research_brief is not None:
                await conn.execute(
                    "UPDATE rounds SET research_brief=$3 WHERE group_id=$1 AND round_number=$2",
                    group_id, round_number, research_brief,
                )
            await conn.execute(
                "UPDATE groups SET status='done', consensus=$2 WHERE id=$1",
                group_id, consensus,
            )


async def set_group_status(pool: asyncpg.Pool, group_id, status: str, consensus: str | None = None) -> None:
    async with pool.acquire() as conn:
        if consensus is not None:
            await conn.execute("UPDATE groups SET status=$2, consensus=$3 WHERE id=$1", group_id, status, consensus)
        else:
            await conn.execute("UPDATE groups SET status=$2 WHERE id=$1", group_id, status)


async def set_member_seq(pool: asyncpg.Pool, group_id, participant_id) -> int:
    """Assign member_seq = MAX+1 for a new participant (SPEC §3.2a). Returns the seq."""
    async with pool.acquire() as conn:
        seq = await conn.fetchval(
            "SELECT COALESCE(MAX(member_seq), 0) + 1 FROM participants WHERE group_id=$1",
            group_id,
        )
        await conn.execute(
            "UPDATE participants SET member_seq=$2 WHERE id=$1",
            participant_id, seq,
        )
        return seq


# ---------------------------------------------------------------------------
# member_questions (ADAPTIVE_SPEC §5.1) — sensitivity between consensus
# (everyone) and stance_digest (nobody): visible ONLY to the recipient.
# Never enters SSE broadcasts, GET /rounds, or GET /state payloads (§0 invariant 2).
# ---------------------------------------------------------------------------

async def get_member_question(pool: asyncpg.Pool, group_id, round_number: int, participant_id) -> str | None:
    """The ONLY read path for a member's personalized question (/my-question)."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT question FROM member_questions "
            "WHERE group_id=$1 AND round_number=$2 AND participant_id=$3",
            group_id, round_number, participant_id,
        )


async def replace_member_questions(
    pool: asyncpg.Pool, group_id, round_number: int,
    items: list[tuple[uuid.UUID, str]],
) -> None:
    """Upsert one batch (call granularity: ONE item per P_X success, §5.1).
    ON CONFLICT DO UPDATE so an analysis retry overwrites the previous batch."""
    if not items:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO member_questions (group_id, round_number, participant_id, question) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (group_id, round_number, participant_id) DO UPDATE SET question = EXCLUDED.question",
            [(group_id, round_number, pid, q) for pid, q in items],
        )


async def clear_member_questions(pool: asyncpg.Pool, group_id, round_number: int) -> None:
    """Creator override path: delete ALL rows for the round (§4.3). Must run
    AFTER the try_open_next_round gate succeeds."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM member_questions WHERE group_id=$1 AND round_number=$2",
            group_id, round_number,
        )


async def get_prior_member_questions(
    pool: asyncpg.Pool, group_id, participant_id, up_to_round: int,
) -> list[tuple[int, str]]:
    """Member X's OWN prior personalized questions (round < up_to_round, in round
    order) — fed ONLY to X's own P_X as private input (§6.4). NEVER returns the
    whole group's questions."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT round_number, question FROM member_questions "
            "WHERE group_id=$1 AND participant_id=$2 AND round_number < $3 "
            "ORDER BY round_number",
            group_id, participant_id, up_to_round,
        )
    return [(r["round_number"], r["question"]) for r in rows]


async def purge_future_member_questions(pool: asyncpg.Pool, group_id, current_round: int) -> None:
    """Delete rows for rounds > current_round. Called on group termination
    (try_close_group success, 24h auto-close, creator dissolve via /leave)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM member_questions WHERE group_id=$1 AND round_number > $2",
            group_id, current_round,
        )


async def prune_delivered_member_questions(pool: asyncpg.Pool, group_id, opened_round: int) -> None:
    """Delete rows for rounds < opened_round after a successful open (§5.1):
    only the current round's rows survive — delivered history is not kept."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM member_questions WHERE group_id=$1 AND round_number < $2",
            group_id, opened_round,
        )


async def count_member_questions(pool: asyncpg.Pool, group_id, round_number: int) -> int:
    """Count only (GET /state member_question_count) — no content surface (§7.4)."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM member_questions WHERE group_id=$1 AND round_number=$2",
            group_id, round_number,
        )
