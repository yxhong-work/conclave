# backend/tests/test_llm.py
import pytest
from app.llm import build_prompt, probe_model

def test_build_prompt_anonymizes_opinions(monkeypatch):
    # Pin the shuffle order so label-opinion pairing is deterministic for the
    # shape assertions. Shuffle behavior itself is tested in test_mcp_loop.
    monkeypatch.setattr("app.llm._shuffle", lambda ops: list(ops))
    sys_p, user_p = build_prompt("Dinner?", ["I am vegetarian", "I want Japanese"])
    assert "Dinner?" in user_p
    assert "A：I am vegetarian" in user_p
    assert "B：I want Japanese" in user_p
    assert "Traditional Chinese" in sys_p
    assert "不得在摘要中指名" in user_p

def test_build_prompt_single_opinion(monkeypatch):
    monkeypatch.setattr("app.llm._shuffle", lambda ops: list(ops))
    sys_p, user_p = build_prompt("Q", ["only one"])
    assert "A：only one" in user_p

def test_probe_model_returns_string(monkeypatch):
    import asyncio
    import app.llm
    app.llm._cached_model = None  # reset cache so the mock is actually hit
    class FakeResp:
        def json(self): return {"data": [{"id": "fake-model"}]}
    monkeypatch.setattr("app.llm.httpx.get", lambda *a, **k: FakeResp())
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(probe_model())
        assert result == "fake-model"
    finally:
        loop.close()
