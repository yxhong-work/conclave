# backend/app/tasks.py
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from .config import get_settings
from .db import get_pool, try_enter_analyzing, get_submitted_count
from .broadcast import broadcast

log = logging.getLogger("conclave.tasks")

AUTO_CLOSE_HOURS = 24  # SPEC MULTIROUND §4 — done > 24h -> closed

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
                    from .llm import start_analysis_task  # late import for testability (SPEC §2)
                    start_analysis_task(pool, gid)
            # Auto-close: done groups whose last round was analyzed > 24h ago (SPEC §4).
            cutoff = datetime.now(timezone.utc) - timedelta(hours=AUTO_CLOSE_HOURS)
            async with pool.acquire() as conn:
                stale = await conn.fetch(
                    "SELECT g.id, g.pin, g.current_round FROM groups g "
                    "JOIN rounds r ON r.group_id = g.id AND r.round_number = g.current_round "
                    "WHERE g.status='done' AND r.analyzed_at IS NOT NULL AND r.analyzed_at < $1",
                    cutoff,
                )
            for row in stale:
                from .db import try_close_group, purge_future_member_questions
                if await try_close_group(pool, row["id"]):
                    # Group terminated: future-round personal questions die with
                    # it (ADAPTIVE_SPEC §5.1 purge call site).
                    await purge_future_member_questions(pool, row["id"],
                                                        current_round=row["current_round"])
                    await broadcast(row["pin"], "round", {"round": 0, "question": "", "status": "closed"})
        except Exception:
            log.exception("deadline scan iteration failed")
        await asyncio.sleep(s.deadline_scan_s)
