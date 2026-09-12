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
