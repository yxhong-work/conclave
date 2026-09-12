# backend/tests/test_integration.py
import asyncio
import pytest
from fastapi.testclient import TestClient
from app.main import app

pytestmark = pytest.mark.integration

def test_full_loop_create_join_submit_consensus(monkeypatch):
    """Full loop: create → join → submit → trigger (expected_count met) → mock LLM → done."""
    async def fake_run(pool, group_id):
        from app.db import set_group_status
        from app.broadcast import broadcast
        await asyncio.sleep(0.1)  # simulate LLM latency
        await set_group_status(pool, group_id, "done", consensus="# 共識\n大家都能接受的方向。")
        async with pool.acquire() as c:
            pin = await c.fetchval("SELECT pin FROM groups WHERE id=$1", group_id)
        await broadcast(pin, "consensus", {"content": "# 共識\n大家都能接受的方向。"})
        await broadcast(pin, "phase", {"status": "done"})

    monkeypatch.setattr("app.llm.run_analysis", fake_run)

    with TestClient(app) as client:
        g = client.post("/api/groups", json={
            "question": "Dinner?", "creator_nickname": "Alice", "expected_count": 2
        }).json()
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        # creator submits
        r = client.post(f"/api/groups/{pin}/responses",
                        json={"participant_id": pid, "content": "vegetarian"})
        assert r.status_code == 200
        # Bob joins + submits (2nd reply → triggers)
        bob = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"}).json()
        r2 = client.post(f"/api/groups/{pin}/responses",
                         json={"participant_id": bob["participant_id"], "content": "japanese"})
        assert r2.status_code == 200
        # allow fire-and-forget task to run
        import time; time.sleep(0.4)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "done"
        assert "共識" in (s["consensus"] or "")


def test_full_loop_creator_start_trigger(monkeypatch):
    """Creator manual start trigger (no expected_count set)."""
    async def fake_run(pool, group_id):
        from app.db import set_group_status
        await set_group_status(pool, group_id, "done", consensus="# Result")
        async with pool.acquire() as c:
            await c.execute("UPDATE groups SET status='done', consensus='# Result' WHERE id=$1", group_id)

    monkeypatch.setattr("app.llm.run_analysis", fake_run)

    with TestClient(app) as client:
        g = client.post("/api/groups", json={"question": "Q?", "creator_nickname": "A"}).json()
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        # creator submits one opinion
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": pid, "content": "my opinion"})
        # creator starts analysis manually
        r = client.post(f"/api/groups/{pin}/start", json={"creator_token": tok})
        assert r.status_code == 202
        import time; time.sleep(0.4)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "done"


def test_zero_reply_start_409():
    """Creator start with 0 replies → 409."""
    with TestClient(app) as client:
        g = client.post("/api/groups", json={"question": "Q?", "creator_nickname": "A"}).json()
        r = client.post(f"/api/groups/{g['pin']}/start", json={"creator_token": g["creator_token"]})
        assert r.status_code == 409


def test_responses_trigger_invokes_patched_run_analysis(monkeypatch):
    """Regression guard (SPEC §2): POST /responses must actually invoke the
    monkeypatched app.llm.run_analysis. The module-top import in responses.py
    previously defeated this patch, so the test silently hit the real LLM.
    """
    called = {"n": 0}

    async def fake_run(pool, group_id):
        called["n"] += 1
        from app.db import set_group_status
        await set_group_status(pool, group_id, "done", consensus="# patched consensus")

    monkeypatch.setattr("app.llm.run_analysis", fake_run)

    with TestClient(app) as client:
        g = client.post("/api/groups", json={
            "question": "Q?", "creator_nickname": "Alice", "expected_count": 2
        }).json()
        pin, pid = g["pin"], g["participant_id"]
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": pid, "content": "opinion-1"})
        bob = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"}).json()
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": bob["participant_id"], "content": "opinion-2"})
        import time; time.sleep(0.4)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert called["n"] == 1, "POST /responses must invoke the patched run_analysis"
        assert s["status"] == "done"
        assert "patched consensus" in (s["consensus"] or "")
