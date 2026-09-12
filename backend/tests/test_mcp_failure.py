# backend/tests/test_mcp_failure.py
"""Failure-path tests for the MCP research stage (SPEC MCP_SPEC §13.2).

Verifies the fail-open invariant: research failures degrade (never set group
status=error) unless MCP_REQUIRED=true; consensus failure is the only path to
status=error. No real MCP servers or LLM.
"""
import asyncio
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import get_settings

pytestmark = pytest.mark.integration


def _enable_mcp(monkeypatch, **overrides):
    get_settings.cache_clear()
    base = {
        "MCP_ENABLED": "true",
        "MCP_PROVIDERS": "web_search",
        "MCP_RESEARCH_MODEL": "fake-research-model",
        "MCP_MAX_ROUNDS": "3",
        "MCP_RESULT_CHARS": "3000",
        "MCP_TOTAL_RESULT_CHARS": "12000",
        "MCP_TOOL_TIMEOUT_S": "30",
        "MCP_CONNECT_TIMEOUT_S": "10",
        "MCP_RUN_TIMEOUT_S": "180",
        "MCP_RESEARCH_MAX_TOKENS": "1024",
        "DATABASE_URL": "postgresql://conclave:conclave@localhost:5432/conclave",
        "LLM_BASE_URL": "http://localhost:1/v1",
        "LLM_API_KEY": "probe",
        "LLM_MODEL": "fake-model",
    }
    base.update(overrides)
    for k, v in base.items():
        monkeypatch.setenv(k, str(v))


def _patch_research(monkeypatch, outcome):
    """Patch research_phase to return a fixed ResearchOutcome."""
    import app.mcp as mcp_mod

    async def fake_research(question, draft=""):
        return outcome
    monkeypatch.setattr(mcp_mod, "research_phase", fake_research)
    # llm.py imports research_phase lazily from .mcp, so patch the attribute on app.mcp
    import app.llm as llm_mod
    monkeypatch.setattr("app.mcp.research_phase", fake_research)


def _patch_consensus_success(monkeypatch):
    """Patch the consensus AsyncOpenAI call to always succeed."""
    from types import SimpleNamespace

    fake_resp = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="# 共識\n分析完成。"),
            finish_reason="stop",
        )],
        usage=SimpleNamespace(),
    )

    async def fake_create(**kwargs):
        return fake_resp

    class FakeClient:
        chat = type("c", (), {"completions": type("co", (), {"create": staticmethod(fake_create)})()})

    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: FakeClient())


def _patch_consensus_fail(monkeypatch):
    """Patch the consensus AsyncOpenAI call to always raise."""
    async def fake_create(**kwargs):
        raise RuntimeError("consensus model down")
    class FakeClient:
        chat = type("c", (), {"completions": type("co", (), {"create": staticmethod(fake_create)})()})()
    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: FakeClient())


# ---------------------------------------------------------------------------
# Fail-open: research degrades, consensus succeeds -> done
# ---------------------------------------------------------------------------

def test_all_providers_down_degrades_to_consensus_only(monkeypatch):
    """When research is skipped/failed, analysis still completes with consensus-only."""
    from app.mcp import ResearchOutcome
    _enable_mcp(monkeypatch)
    _patch_research(monkeypatch, ResearchOutcome("skipped", "", 0, 0, "no providers"))
    _patch_consensus_success(monkeypatch)

    with TestClient(app) as client:
        g = client.post("/api/groups", json={
            "question": "Q?", "creator_nickname": "A", "expected_count": 1
        }).json()
        client.post(f"/api/groups/{g['pin']}/responses",
                    json={"participant_id": g["participant_id"], "content": "op"})
        import time; time.sleep(0.4)
        s = client.get(f"/api/groups/{g['pin']}/state").json()
        assert s["status"] == "done"
        assert "共識" in (s["consensus"] or "")


def test_research_failed_degrades(monkeypatch):
    """A failed research outcome still lets consensus run and complete."""
    from app.mcp import ResearchOutcome
    _enable_mcp(monkeypatch)
    _patch_research(monkeypatch, ResearchOutcome("failed", "", 0, 0, "run timeout"))
    _patch_consensus_success(monkeypatch)

    with TestClient(app) as client:
        g = client.post("/api/groups", json={
            "question": "Q?", "creator_nickname": "A", "expected_count": 1
        }).json()
        client.post(f"/api/groups/{g['pin']}/responses",
                    json={"participant_id": g["participant_id"], "content": "op"})
        import time; time.sleep(0.4)
        s = client.get(f"/api/groups/{g['pin']}/state").json()
        assert s["status"] == "done"


# ---------------------------------------------------------------------------
# MCP_REQUIRED: research degrade -> error
# ---------------------------------------------------------------------------

def test_mcp_required_promotes_degrade_to_error(monkeypatch):
    """With MCP_REQUIRED=true, a degraded research outcome sets status=error."""
    from app.mcp import ResearchOutcome
    _enable_mcp(monkeypatch, MCP_REQUIRED="true")
    _patch_research(monkeypatch, ResearchOutcome("failed", "", 0, 0, "no providers"))
    _patch_consensus_success(monkeypatch)  # consensus would succeed, but shouldn't be reached

    with TestClient(app) as client:
        g = client.post("/api/groups", json={
            "question": "Q?", "creator_nickname": "A", "expected_count": 1
        }).json()
        client.post(f"/api/groups/{g['pin']}/responses",
                    json={"participant_id": g["participant_id"], "content": "op"})
        import time; time.sleep(0.4)
        s = client.get(f"/api/groups/{g['pin']}/state").json()
        assert s["status"] == "error"


# ---------------------------------------------------------------------------
# Consensus failure -> error (the only non-MCP path to error)
# ---------------------------------------------------------------------------

def test_consensus_failure_sets_error(monkeypatch):
    """Consensus-stage LLM failure sets status=error and broadcasts it."""
    from app.mcp import ResearchOutcome
    _enable_mcp(monkeypatch)
    _patch_research(monkeypatch, ResearchOutcome("done", "some brief", 1, 1))
    _patch_consensus_fail(monkeypatch)
    # Skip the real backoff sleeps so the 3 retries complete instantly. Use a
    # no-op coroutine (not asyncio.sleep, which we are NOT patching — we patch
    # the module attribute the loop reads).
    async def _noop_sleep(*a, **k):
        return None
    import app.llm as llm_mod
    monkeypatch.setattr(llm_mod.asyncio, "sleep", _noop_sleep)

    with TestClient(app) as client:
        g = client.post("/api/groups", json={
            "question": "Q?", "creator_nickname": "A", "expected_count": 1
        }).json()
        client.post(f"/api/groups/{g['pin']}/responses",
                    json={"participant_id": g["participant_id"], "content": "op"})
        import time; time.sleep(0.4)
        s = client.get(f"/api/groups/{g['pin']}/state").json()
        assert s["status"] == "error"


# ---------------------------------------------------------------------------
# MCP disabled = existing behavior (no research, no research SSE event)
# ---------------------------------------------------------------------------

def test_mcp_disabled_no_research_events(monkeypatch):
    """With MCP_ENABLED=false (default), no research phase runs and no research event broadcasts."""
    get_settings.cache_clear()
    monkeypatch.delenv("MCP_ENABLED", raising=False)
    _patch_consensus_success(monkeypatch)

    research_events = {"n": 0}
    import app.broadcast as bcast
    orig_broadcast = bcast.broadcast

    async def spy_broadcast(pin, event_type, data):
        if event_type == "research":
            research_events["n"] += 1
        await orig_broadcast(pin, event_type, data)
    monkeypatch.setattr(bcast, "broadcast", spy_broadcast)
    monkeypatch.setattr("app.broadcast.broadcast", spy_broadcast)

    with TestClient(app) as client:
        g = client.post("/api/groups", json={
            "question": "Q?", "creator_nickname": "A", "expected_count": 1
        }).json()
        client.post(f"/api/groups/{g['pin']}/responses",
                    json={"participant_id": g["participant_id"], "content": "op"})
        import time; time.sleep(0.4)
        s = client.get(f"/api/groups/{g['pin']}/state").json()
        assert s["status"] == "done"
        assert research_events["n"] == 0


# ---------------------------------------------------------------------------
# Run-level (umbrella) timeout (SPEC §13.2)
# ---------------------------------------------------------------------------

def test_run_timeout_degrades_to_empty_brief(monkeypatch):
    """When the research stage exceeds MCP_RUN_TIMEOUT_S, it degrades (empty brief, done).

    We simulate the post-timeout ResearchOutcome that research_phase returns after
    its internal wait_for cancels the stage — testing run_analysis's fail-open
    handling of that outcome, not the timeout mechanism itself.
    """
    from app.mcp import ResearchOutcome
    _enable_mcp(monkeypatch, MCP_RUN_TIMEOUT_S="0.2")
    _patch_research(monkeypatch, ResearchOutcome("failed", "", 0, 0, "run timeout"))
    _patch_consensus_success(monkeypatch)

    with TestClient(app) as client:
        g = client.post("/api/groups", json={
            "question": "Q?", "creator_nickname": "A", "expected_count": 1
        }).json()
        client.post(f"/api/groups/{g['pin']}/responses",
                    json={"participant_id": g["participant_id"], "content": "op"})
        import time; time.sleep(0.4)
        s = client.get(f"/api/groups/{g['pin']}/state").json()
        # Fail-open: research timed out, but consensus still completes -> done.
        assert s["status"] == "done"


# ---------------------------------------------------------------------------
# Consensus failure -> error -> retry -> done (SPEC §13.2)
# ---------------------------------------------------------------------------

def test_consensus_failure_then_retry_succeeds(monkeypatch):
    """Full error→analyzing→done retry path: consensus fails (error), then retry succeeds."""
    from app.mcp import ResearchOutcome
    _enable_mcp(monkeypatch)
    _patch_research(monkeypatch, ResearchOutcome("done", "brief", 1, 1))

    # Skip the real backoff sleeps so retries complete instantly.
    async def _noop_sleep(*a, **k):
        return None
    import app.llm as llm_mod
    monkeypatch.setattr(llm_mod.asyncio, "sleep", _noop_sleep)

    # First analysis run: 3 attempts all fail -> error. Retry run: succeeds.
    call_state = {"n": 0}
    from types import SimpleNamespace

    ok_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="# 共識\n重試成功。"),
                                 finish_reason="stop")],
        usage=SimpleNamespace(),
    )

    async def fake_create(**kwargs):
        call_state["n"] += 1
        # First analysis's 3 attempts (call 1-3) all fail; retry (call 4+) succeeds.
        if call_state["n"] <= 3:
            raise RuntimeError("consensus model down")
        return ok_resp

    class FakeClient:
        chat = type("c", (), {"completions": type("co", (), {"create": staticmethod(fake_create)})()})()

    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: FakeClient())

    # Broadcast spy: capture error + phase events.
    events = []
    import app.broadcast as bcast
    orig = bcast.broadcast

    async def spy(pin, event_type, data):
        events.append((event_type, data))
        await orig(pin, event_type, data)
    monkeypatch.setattr(bcast, "broadcast", spy)
    monkeypatch.setattr("app.broadcast.broadcast", spy)
    # llm.py and tasks.py bound broadcast at import time; patch there too.
    import app.llm as llm_mod
    monkeypatch.setattr(llm_mod, "broadcast", spy)

    with TestClient(app) as client:
        g = client.post("/api/groups", json={
            "question": "Q?", "creator_nickname": "A", "expected_count": 1
        }).json()
        pin, tok = g["pin"], g["creator_token"]
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": g["participant_id"], "content": "op"})
        import time; time.sleep(0.6)
        # First analysis failed -> error.
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "error"
        assert any(et == "error" for et, _ in events)
        assert any(et == "phase" and d.get("status") == "error" for et, d in events)
        # Creator retries -> analyzing -> done.
        client.post(f"/api/groups/{pin}/start", json={"creator_token": tok})
        time.sleep(0.6)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "done"
        assert "重試成功" in (s["consensus"] or "")
