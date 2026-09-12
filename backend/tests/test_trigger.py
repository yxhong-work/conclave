# backend/tests/test_trigger.py
import pytest
from datetime import datetime, timezone
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

async def _route_decision(pool, group_id, expected_count, is_creator_start=False, is_deadline=False):
    """Simulates the route-layer trigger decision per SPEC §7.3:
    - zero-reply guard (never trigger if submitted_count == 0)
    - creator start: trigger if count >= 1
    - expected_count: trigger if submitted_count >= expected_count
    - deadline: the scan task calls try_enter_analyzing directly (after zero-reply guard)
    - none set, not creator start: do not trigger (wait for manual)
    """
    sc = await get_submitted_count(pool, group_id)
    if sc == 0:
        return False  # zero-reply guard
    if is_creator_start:
        return await try_enter_analyzing(pool, group_id)
    if is_deadline:
        return await try_enter_analyzing(pool, group_id)
    if expected_count is not None and sc >= expected_count:
        return await try_enter_analyzing(pool, group_id)
    return False

@pytest.mark.parametrize("expected,deadline,event,should_trigger", [
    (3, None, "third_reply", True),
    (None, "T", "deadline", True),
    (3, "T", "third_reply_before_deadline", True),
    (3, "T", "deadline_with_2", True),
    (None, None, "any_reply", False),
    (None, None, "creator_start", True),
])
async def test_trigger_truth_table(pool, expected, deadline, event, should_trigger):
    dl = None if deadline is None else datetime.fromisoformat("2000-01-01T00:00:00+00:00")
    gid = await _make_group(pool, expected_count=expected, deadline=dl)
    is_creator_start = (event == "creator_start")
    is_deadline = (deadline == "T" and not is_creator_start)

    if event in ("third_reply", "third_reply_before_deadline"):
        await _add_participant_and_response(pool, gid, "A")
        await _add_participant_and_response(pool, gid, "B")
        await _add_participant_and_response(pool, gid, "C")  # 3rd reply
    elif event == "deadline_with_2":
        await _add_participant_and_response(pool, gid, "A")
        await _add_participant_and_response(pool, gid, "B")
    elif event == "any_reply":
        await _add_participant_and_response(pool, gid, "A")
    elif event == "deadline":
        await _add_participant_and_response(pool, gid, "A")  # deadline path: has 1 reply
    elif event == "creator_start":
        await _add_participant_and_response(pool, gid, "A")  # creator start: has 1 reply

    triggered = await _route_decision(pool, gid, expected, is_creator_start, is_deadline)
    assert triggered == should_trigger

async def test_trigger_is_single_shot(pool):
    gid = await _make_group(pool, expected_count=1)
    await _add_participant_and_response(pool, gid, "A")
    assert await try_enter_analyzing(pool, gid) is True   # first wins
    assert await try_enter_analyzing(pool, gid) is False  # second no-op (already analyzing)

async def test_zero_reply_start_does_not_trigger(pool):
    gid = await _make_group(pool)
    assert await get_submitted_count(pool, gid) == 0
    # route layer checks count first -> would return 409, never calls try_enter_analyzing
    assert await _route_decision(pool, gid, None, is_creator_start=True) is False

async def test_error_to_analyzing_retry(pool):
    """Retry from error state: try_enter_analyzing allows error->analyzing (§3.2)."""
    gid = await _make_group(pool, status="error")
    await _add_participant_and_response(pool, gid, "A")
    assert await try_enter_analyzing(pool, gid) is True  # error -> analyzing allowed
