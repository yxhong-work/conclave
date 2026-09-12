# backend/tests/test_rounds.py
"""Multi-round state-machine and API tests (SPEC MULTIROUND_SPEC.md §13.1).

No real LLM. Integration tests drive the app through TestClient with a mocked
run_analysis that sets status='done' (the terminal contract). Covers: round
transitions, max_rounds cap, cooldown, close, public-safe GET /rounds (no
stance_digest), per-round UNIQUE responses, auto-seed question.
"""
import asyncio
import time
import pytest
from fastapi.testclient import TestClient
from app.main import app

pytestmark = pytest.mark.integration


def _fake_run_done(monkeypatch, consensus="# 共識"):
    """Patch run_analysis to set done + broadcast + write the round row, matching
    the real terminal contract (set_round_done writes rounds.consensus too)."""
    async def fake_run(pool, group_id):
        from app.db import get_group_by_id, set_round_done
        from app.broadcast import broadcast
        g = await get_group_by_id(pool, group_id)
        rnd = g["current_round"]
        # Write the round row (consensus + null stance fields) and set groups.status=done.
        await set_round_done(pool, group_id, rnd, consensus, None, None)
        await broadcast(g["pin"], "consensus", {"content": consensus, "round": rnd})
        await broadcast(g["pin"], "phase", {"status": "done", "round": rnd})
    monkeypatch.setattr("app.llm.run_analysis", fake_run)


def _create(client, question="Q?", nickname="A", expected_count=2, max_rounds=3):
    return client.post("/api/groups", json={
        "question": question, "creator_nickname": nickname,
        "expected_count": expected_count, "max_rounds": max_rounds,
    }).json()


def _join_submit(client, pin, nick, content):
    j = client.post(f"/api/groups/{pin}/join", json={"nickname": nick}).json()
    r = client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": j["participant_id"], "content": content})
    assert r.status_code == 200, r.text
    return j["participant_id"]


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

def test_get_state_includes_round_fields(monkeypatch):
    _fake_run_done(monkeypatch)
    with TestClient(app) as client:
        g = _create(client, max_rounds=5)
        s = client.get(f"/api/groups/{g['pin']}/state").json()
        assert s["current_round"] == 1
        assert s["max_rounds"] == 5


def test_open_next_round_increments_current_round(monkeypatch):
    _fake_run_done(monkeypatch)
    with TestClient(app) as client:
        g = _create(client)
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        # Round 1: creator + Bob submit -> triggers
        client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "op1"})
        _join_submit(client, pin, "Bob", "op2")
        time.sleep(0.4)
        assert client.get(f"/api/groups/{pin}/state").json()["status"] == "done"
        # Wait out the 30s cooldown by patching it to 0 for this test.
        monkeypatch.setattr('app.routes.groups.NEXT_ROUND_COOLDOWN_S', 0)
        r = client.post(f"/api/groups/{pin}/rounds/next", json={"creator_token": tok})
        assert r.status_code == 202, r.text
        assert r.json()["round"] == 2
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "collecting"
        assert s["current_round"] == 2


def test_open_next_round_requires_creator_token(monkeypatch):
    _fake_run_done(monkeypatch)
    with TestClient(app) as client:
        g = _create(client)
        client.post(f"/api/groups/{g['pin']}/responses",
                    json={"participant_id": g["participant_id"], "content": "op1"})
        _join_submit(client, g["pin"], "Bob", "op2")
        time.sleep(0.4)
        monkeypatch.setattr('app.routes.groups.NEXT_ROUND_COOLDOWN_S', 0)
        r = client.post(f"/api/groups/{g['pin']}/rounds/next", json={"creator_token": "wrong"})
        assert r.status_code == 403


def test_open_next_round_requires_done_status(monkeypatch):
    _fake_run_done(monkeypatch)
    with TestClient(app) as client:
        g = _create(client)
        r = client.post(f"/api/groups/{g['pin']}/rounds/next", json={"creator_token": g["creator_token"]})
        assert r.status_code == 409  # still collecting


def test_max_rounds_blocks_next(monkeypatch):
    _fake_run_done(monkeypatch)
    with TestClient(app) as client:
        g = _create(client, max_rounds=1)
        client.post(f"/api/groups/{g['pin']}/responses",
                    json={"participant_id": g["participant_id"], "content": "op1"})
        _join_submit(client, g["pin"], "Bob", "op2")
        time.sleep(0.4)
        monkeypatch.setattr('app.routes.groups.NEXT_ROUND_COOLDOWN_S', 0)
        r = client.post(f"/api/groups/{g['pin']}/rounds/next", json={"creator_token": g["creator_token"]})
        assert r.status_code == 409
        assert "上限" in r.json().get("detail", "")


def test_cooldown_blocks_immediate_next(monkeypatch):
    _fake_run_done(monkeypatch)
    monkeypatch.setattr("app.routes.groups.NEXT_ROUND_COOLDOWN_S", 30)
    with TestClient(app) as client:
        g = _create(client)
        client.post(f"/api/groups/{g['pin']}/responses",
                    json={"participant_id": g["participant_id"], "content": "op1"})
        _join_submit(client, g["pin"], "Bob", "op2")
        time.sleep(0.4)
        r = client.post(f"/api/groups/{g['pin']}/rounds/next", json={"creator_token": g["creator_token"]})
        assert r.status_code == 409
        assert "冷卻" in r.json().get("detail", "")


def test_close_group(monkeypatch):
    _fake_run_done(monkeypatch)
    with TestClient(app) as client:
        g = _create(client)
        client.post(f"/api/groups/{g['pin']}/responses",
                    json={"participant_id": g["participant_id"], "content": "op1"})
        _join_submit(client, g["pin"], "Bob", "op2")
        time.sleep(0.4)
        r = client.post(f"/api/groups/{g['pin']}/rounds/close", json={"creator_token": g["creator_token"]})
        assert r.status_code == 200
        s = client.get(f"/api/groups/{g['pin']}/state").json()
        assert s["status"] == "closed"


# ---------------------------------------------------------------------------
# Per-round UNIQUE + round history (public-safe)
# ---------------------------------------------------------------------------

def test_response_unique_per_round_allows_round2(monkeypatch):
    """Same participant can submit once per round — round 2 is not 409."""
    _fake_run_done(monkeypatch)
    with TestClient(app) as client:
        g = _create(client)
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        # Round 1
        client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "r1"})
        _join_submit(client, pin, "Bob", "r1bob")
        time.sleep(0.4)
        # Open round 2
        monkeypatch.setattr('app.routes.groups.NEXT_ROUND_COOLDOWN_S', 0)
        client.post(f"/api/groups/{pin}/rounds/next", json={"creator_token": tok})
        # Creator submits again in round 2 — must NOT be 409
        r = client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "r2"})
        assert r.status_code == 200


def test_get_rounds_returns_history_without_private_columns(monkeypatch):
    """GET /rounds returns public-safe fields; stance_digest/research_brief absent."""
    _fake_run_done(monkeypatch, consensus="# R1 共識\n## 未解分歧\n要不要加素食")
    with TestClient(app) as client:
        g = _create(client)
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "op1"})
        _join_submit(client, pin, "Bob", "op2")
        time.sleep(0.4)
        r = client.get(f"/api/groups/{pin}/rounds")
        assert r.status_code == 200
        rounds = r.json()
        assert len(rounds) == 1
        assert rounds[0]["round_number"] == 1
        assert "consensus" in rounds[0]
        # Private columns must never appear in the API response (§5.6).
        assert "stance_digest" not in rounds[0]
        assert "research_brief" not in rounds[0]


def test_auto_seed_question_from_unresolved(monkeypatch):
    """POST /rounds/next without question seeds from the prior consensus's 未解分歧
    (regex fallback path — LLM question-gen is forced to fail)."""
    _fake_run_done(monkeypatch, consensus="# 共識\n方向A\n## 未解分歧\n要不要加素食選項\n## 建議\n快速表決")
    # Force the LLM question-gen to fail so the regex fallback is exercised.
    async def _fail_qg(prev_consensus, prev_question):
        raise RuntimeError("LLM unavailable")
    monkeypatch.setattr("app.llm.generate_next_question", _fail_qg)
    monkeypatch.setattr("app.routes.groups.NEXT_ROUND_COOLDOWN_S", 0)
    with TestClient(app) as client:
        g = _create(client)
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "op1"})
        _join_submit(client, pin, "Bob", "op2")
        time.sleep(0.4)
        r = client.post(f"/api/groups/{pin}/rounds/next", json={"creator_token": tok})
        assert r.status_code == 202
        seeded = r.json()["question"]
        assert "素食" in seeded  # regex fallback extracted it


def test_llm_question_gen_seeds_list(monkeypatch):
    """When the creator leaves the question blank and the LLM succeeds, the
    generated list becomes the next round's question (gap #2)."""
    _fake_run_done(monkeypatch, consensus="# 共識\n方向A\n## 未解分歧\n預算上限\n地點")
    async def _gen_q(prev_consensus, prev_question):
        return "1. 預算要放寬到多少?\n2. 地點是否限台北車站?"
    monkeypatch.setattr("app.llm.generate_next_question", _gen_q)
    monkeypatch.setattr("app.routes.groups.NEXT_ROUND_COOLDOWN_S", 0)
    with TestClient(app) as client:
        g = _create(client)
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "op1"})
        _join_submit(client, pin, "Bob", "op2")
        time.sleep(0.4)
        r = client.post(f"/api/groups/{pin}/rounds/next", json={"creator_token": tok})
        assert r.status_code == 202
        q = r.json()["question"]
        assert q.startswith("1.")
        assert "預算" in q


def test_creator_question_overrides_llm(monkeypatch):
    """A creator-typed question always wins; the LLM question-gen is never called."""
    _fake_run_done(monkeypatch)
    called = {"n": 0}
    async def _gen_q(prev_consensus, prev_question):
        called["n"] += 1
        return "SHOULD NOT BE USED"
    monkeypatch.setattr("app.llm.generate_next_question", _gen_q)
    monkeypatch.setattr("app.routes.groups.NEXT_ROUND_COOLDOWN_S", 0)
    with TestClient(app) as client:
        g = _create(client)
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "op1"})
        _join_submit(client, pin, "Bob", "op2")
        time.sleep(0.4)
        r = client.post(f"/api/groups/{pin}/rounds/next",
                        json={"creator_token": tok, "question": "我的下一輪問題"})
        assert r.status_code == 202
        assert r.json()["question"] == "我的下一輪問題"
        assert called["n"] == 0  # LLM never invoked


def test_question_gen_privacy_no_stance_leak(monkeypatch):
    """generate_next_question receives ONLY the consensus + question strings —
    never stance_digest, member_seq, or opinions (gap #2 privacy, §5.1)."""
    _fake_run_done(monkeypatch, consensus="# 共識\n方向A")
    captured = {"args": None}
    async def _spy_q(prev_consensus, prev_question):
        captured["args"] = {"consensus": prev_consensus, "question": prev_question}
        return "1. follow-up?"
    monkeypatch.setattr("app.llm.generate_next_question", _spy_q)
    monkeypatch.setattr("app.routes.groups.NEXT_ROUND_COOLDOWN_S", 0)
    with TestClient(app) as client:
        g = _create(client)
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": "secret-opinion-1"})
        _join_submit(client, pin, "Bob", "secret-opinion-2")
        time.sleep(0.4)
        client.post(f"/api/groups/{pin}/rounds/next", json={"creator_token": tok})
    assert captured["args"] is not None
    # The question-gen call saw the consensus (public) and the prior question.
    assert "方向A" in captured["args"]["consensus"]
    # It did NOT see raw opinions, member_seq labels, or stance_digest.
    assert "secret-opinion-1" not in captured["args"]["consensus"]
    assert "secret-opinion-2" not in captured["args"]["consensus"]
    assert "成員" not in captured["args"]["consensus"]
