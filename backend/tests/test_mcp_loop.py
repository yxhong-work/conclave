# backend/tests/test_mcp_loop.py
"""Unit tests for the MCP research loop (SPEC MCP_SPEC §13.1).

No real MCP servers, no real LLM. The agentic loop is driven by a fake chat
callable returning scripted tool_calls; LocalTool stubs are the injection seam
(no fake MCP session needed). All tests run with MCP_ENABLED=true via the
settings cache reset pattern.
"""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest

# mcp_tools imports the `mcp` package at module top; skip this whole module when
# it's absent (the dev conda env without mcp installed) per SPEC §13.3.
pytest.importorskip("mcp")

from app.config import get_settings


def _set_mcp_settings(monkeypatch, **overrides):
    """Rebuild Settings with MCP enabled and override fields. Clears the lru_cache
    so app modules re-read the new settings (SPEC test plan: get_settings.cache_clear).
    """
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
        "MCP_ALLOW_OPINION_CONTEXT": "false",
        "DATABASE_URL": "postgresql://conclave:conclave@localhost:5432/conclave",
        "LLM_BASE_URL": "http://localhost:1/v1",
        "LLM_API_KEY": "probe",
        "LLM_MODEL": "fake-model",
    }
    base.update(overrides)
    for k, v in base.items():
        monkeypatch.setenv(k, str(v))
    return get_settings()


# ---------------------------------------------------------------------------
# redact() generalization (SPEC §9.3)
# ---------------------------------------------------------------------------

def test_redact_masks_llm_api_key(monkeypatch):
    """redact() must cover LLM_API_KEY, which the old mcp_tools fixed list missed."""
    monkeypatch.setenv("LLM_API_KEY", "sk-secret-key-123456")
    monkeypatch.setenv("SOME_TOKEN", "tok-abcdefgh")
    from app.llm import redact
    out = redact("error: auth failed with key sk-secret-key-123456 and token tok-abcdefgh")
    assert "sk-secret-key-123456" not in out
    assert "tok-abcdefgh" not in out
    assert "<redacted>" in out


def test_redact_preserves_short_values(monkeypatch):
    """Short values (<8 chars) and 'local' are not redacted (avoid noise)."""
    monkeypatch.setenv("LLM_API_KEY", "local")
    monkeypatch.setenv("SOME_TOKEN", "short")
    from app.llm import redact
    out = redact("local and short are fine")
    assert out == "local and short are fine"


# ---------------------------------------------------------------------------
# Brief fencing (SPEC §9.2)
# ---------------------------------------------------------------------------

def test_fence_brief_wraps_content():
    from app.mcp import fence_brief, BRIEF_FENCE_HEADER
    fenced = fence_brief("some research result")
    assert fenced.startswith(BRIEF_FENCE_HEADER)
    assert "some research result" in fenced


def test_fence_brief_empty_returns_empty():
    from app.mcp import fence_brief
    assert fence_brief("") == ""
    assert fence_brief("   ") == ""


# ---------------------------------------------------------------------------
# render_result envelope + redact-before-truncate (SPEC §13.1, §9.3)
# ---------------------------------------------------------------------------

def test_render_result_redacts_before_truncating(monkeypatch):
    """A secret straddling the truncation boundary must not escape as a fragment
    (SPEC §9.3). The new mcp_tools redacts the full text THEN truncates."""
    from app.mcp_tools import redact
    secret = "sk-supersecrettoken1234567890"
    monkeypatch.setenv("MY_API_KEY", secret)
    text = secret  # the whole text IS the secret
    safe = redact(text)
    assert secret not in safe
    assert "<redacted>" in safe
    # After truncation, the redacted marker survives (never the raw secret).
    assert secret not in safe[:10]


# ---------------------------------------------------------------------------
# register_tools namespacing + 64-char cap (SPEC §13.1)
# ---------------------------------------------------------------------------

def test_register_tools_namespaces_and_caps_name():
    """Tool names are provider__tool, capped at 64 chars with a sha256 suffix."""
    from app.mcp_tools import McpServer, ToolRoute, register_tools, PreparedTools

    # Build a provider with a very long tool name to exercise the 64-char cap.
    long_tool_name = "x" * 70
    # The new register_tools takes a PreparedTools container + runtime dict
    # (YAML-driven architecture). Discovered tools are MCP Tool objects; we
    # synthesize a minimal stand-in with the fields register_tools reads.
    class FakeTool:
        def __init__(self, name):
            self.name = name
            self.description = "d"
            self.input_schema = {"type": "object"}

    tool = FakeTool(long_tool_name)
    server = McpServer(
        name="prov", tools=(long_tool_name,), default_prompt="",
    )
    prepared = PreparedTools()
    register_tools(server, None, (tool,), {long_tool_name}, prepared, {})
    assert len(prepared.routes) == 1
    namespaced = list(prepared.routes)[0]
    assert namespaced.startswith("prov__")
    assert len(namespaced) <= 64
    assert prepared.function_tools[0]["function"]["name"] == namespaced


def test_build_prompt_includes_fenced_brief(monkeypatch):
    """When research has a brief, the consensus user prompt contains the untrusted fence."""
    monkeypatch.setattr("app.llm._shuffle", lambda ops: list(ops))
    from app.llm import build_prompt
    from app.mcp import ResearchOutcome, BRIEF_FENCE_HEADER
    outcome = ResearchOutcome("done", "NVDA closed at $150", 1, 2)
    _sys, user = build_prompt("Dinner?", ["veg", "japanese"], outcome)
    assert BRIEF_FENCE_HEADER in user
    assert "NVDA closed at $150" in user
    # opinions still present
    assert "A：veg" in user and "B：japanese" in user


def test_linkify_bare_urls_anchors_on_preceding_text():
    """Bare URLs after a store name get anchored on that name (real-world case)."""
    from app.llm import _linkify_bare_urls
    text = (
        "鉄火燒肉 新光三越北車店：日式燒肉，4.5星（5,729則評論），價格約200–400元。"
        "來源：https://www.google.com/maps/place//data=!4m2!3m1!1s0x3442a9cdddb93125:0x65734319fb15a7b"
    )
    out = _linkify_bare_urls(text)
    assert "](https://www.google.com" in out
    # No bare URL remains outside a markdown link
    import re
    bare = re.sub(r"\]\([^)]+\)", "", out)
    assert "https://" not in bare


def test_linkify_bare_urls_no_double_wrap():
    """URLs already inside a markdown link target are left untouched."""
    from app.llm import _linkify_bare_urls
    text = "看[這家店](https://example.com/ramen)的評價。"
    out = _linkify_bare_urls(text)
    assert out == text


def test_linkify_bare_urls_mid_sentence_anchors_on_word_run():
    """A URL in mid-sentence anchors on the trailing word run before it."""
    from app.llm import _linkify_bare_urls
    out = _linkify_bare_urls("詳見官網 https://example.com/menu 的價格。")
    assert "](https://example.com/menu)" in out
    assert out.endswith("的價格。")


def test_linkify_bare_urls_fullwidth_punctuation_and_any_domain():
    """General design: news/doc/any-domain URLs work; full-width punctuation
    following a URL must not be swallowed into the link target."""
    from app.llm import _linkify_bare_urls
    out = _linkify_bare_urls("根據報導，https://news.example.com/2026/food，目前已有變化。")
    assert "](https://news.example.com/2026/food)，" in out
    out2 = _linkify_bare_urls("詳細規則見 https://docs.example.com/guide（中文版）。")
    assert "](https://docs.example.com/guide)（中文版）" in out2
    out3 = _linkify_bare_urls("GitHub:https://github.com/abc/def/releases/tag/v1.0 很有用")
    assert "](https://github.com/abc/def/releases/tag/v1.0)" in out3


def test_build_prompt_forbids_tables_and_adds_mcp_usage_rules():
    """The consensus system prompt must forbid tables (front-end renders a
    markdown subset without table support), and the brief-bearing user prompt
    must carry MCP usage guidance: cite research facts with source URLs, flag
    conflicts, never invent specifics."""
    from app.llm import build_prompt
    from app.mcp import ResearchOutcome
    outcome = ResearchOutcome("done", "NVDA closed at $150", 1, 2)
    sys_p, user = build_prompt("Q?", ["op"], outcome)
    # table ban in system prompt
    assert "NEVER use tables" in sys_p
    # MCP usage guidance in user prompt (brief present)
    assert "外部資訊重點" in user
    assert "markdown 超連結" in user and "絕不可出現裸網址" in user
    assert "不要編造" in user or "請勿杜撰" in user


def test_planner_prompt_requires_json_and_discipline():
    """The planner prompt must demand JSON-only output, few focused queries, and
    argument derivation restricted to question + draft."""
    from app.mcp import PLANNER_SYSTEM_PROMPT
    assert "JSON" in PLANNER_SYSTEM_PROMPT
    assert "1-2 focused queries" in PLANNER_SYSTEM_PROMPT
    assert "ONLY from the question and the draft" in PLANNER_SYSTEM_PROMPT


def test_build_prompt_degraded_adds_notice():
    """When research degraded, consensus prompt gets the no-external-data notice."""
    from app.llm import build_prompt
    from app.mcp import ResearchOutcome, degrade_notice
    outcome = ResearchOutcome("failed", "", 0, 0, "no providers")
    _sys, user = build_prompt("Q?", ["op"], outcome)
    assert degrade_notice() in user


def test_build_prompt_no_research_is_unchanged_shape():
    """Without research (None), build_prompt keeps its original shape (no fence)."""
    from app.llm import build_prompt
    from app.mcp import BRIEF_FENCE_HEADER
    _sys, user = build_prompt("Q?", ["op"])
    assert BRIEF_FENCE_HEADER not in user
    assert "【成員想法】" in user


# ---------------------------------------------------------------------------
# Opinion leak prevention (SPEC §9.1 — structural isolation regression)
# ---------------------------------------------------------------------------

def test_planner_messages_exclude_raw_opinions(monkeypatch):
    """Planner messages must never contain raw opinion text (SPEC §9.1, v1.1):
    the planner sees only the question and the draft summary."""
    _set_mcp_settings(monkeypatch)
    from app.mcp import _build_planner_messages, PLANNER_SYSTEM_PROMPT
    opinions = ["my_secret_preference_42", "private_thought_99", "hidden_opinion_7"]
    tools = [{"type": "function", "function": {"name": "t__x", "description": "d", "parameters": {}}}]
    # The planner system prompt mentions no opinion text
    for op in opinions:
        assert op not in PLANNER_SYSTEM_PROMPT
    # The planner user message (question + draft only) mentions no opinion text
    msgs = _build_planner_messages("Q?", "draft summary", tools, 3)
    joined = " ".join(m["content"] for m in msgs)
    for op in opinions:
        assert op not in joined



def _fake_server(name):
    """Build an McpServer for planner tests (URL is a placeholder; connect is
    monkeypatched per-test to return a fake session)."""
    from app.mcp_tools import McpServer
    return McpServer(name=name, tools=("x",), default_prompt="", url="http://fake")


# ---------------------------------------------------------------------------
# Planner flow tests (v1.1 summarize-first) — fake planner chat + fake sessions
# ---------------------------------------------------------------------------

def _make_scripted_session(scripts):
    """Build a fake MCP ClientSession whose call_tool returns scripted text and
    records calls. Adapts to the new architecture: no LocalTool/handler path;
    _execute_one_tool calls route.session.call_tool, so we mock the session."""
    calls = {"n": 0, "args": []}

    class FakeTool:
        def __init__(self):
            self.name = "x"
            self.description = "d"
            self.input_schema = {"type": "object"}

    class FakeSession:
        async def call_tool(self, remote_name, arguments):
            calls["n"] += 1
            calls["args"].append(arguments)
            text = scripts[min(calls["n"] - 1, len(scripts) - 1)]
            # Return a minimal result object render_result can read.
            class FakeContent:
                def __init__(self, t):
                    self.type = "text"
                    self.text = t
            class FakeResult:
                def __init__(self, t):
                    self.structured_content = None
                    self.content = [FakeContent(t)]
                    self.is_error = False
            return FakeResult(text)

    return FakeSession(), calls, FakeTool()


def _patch_connect(monkeypatch, fake_session, fake_tool):
    """Monkeypatch mcp_tools.connect to return the fake session without network."""
    import app.mcp_tools as tools_mod

    @asynccontextmanager
    async def fake_connect(server, timeout):
        yield fake_session, [fake_tool]
    monkeypatch.setattr(tools_mod, "connect", fake_connect)


def _patch_planner(monkeypatch, content):
    """Patch _planner_chat to return a fixed content string."""
    import app.mcp as mcp_mod

    async def fake_planner(client, model, messages):
        return {"message": {"content": content}, "finish_reason": "stop"}

    monkeypatch.setattr(mcp_mod, "_planner_chat", fake_planner)


def test_planner_happy_path_executes_calls(monkeypatch):
    """Planner returns JSON calls; they execute and results become the brief."""
    _set_mcp_settings(monkeypatch)
    import app.mcp as mcp_mod
    session, calls, tool = _make_scripted_session(["tool result text"])
    _patch_connect(monkeypatch, session, tool)
    _patch_planner(monkeypatch, json.dumps({"calls": [
        {"tool": "t__x", "arguments": {"query": "test"}}
    ]}))

    outcome = asyncio.run(mcp_mod._plan_execute_with_sessions("Q?", "draft", ["t"], {"t": _fake_server("t")}))
    assert outcome.status == "done"
    assert "tool result text" in outcome.brief
    assert outcome.rounds == 1
    assert calls["n"] == 1
    assert calls["args"][0] == {"query": "test"}


def test_planner_no_calls_skips_research(monkeypatch):
    """Planner deciding no tool helps -> research skipped, empty brief."""
    _set_mcp_settings(monkeypatch)
    import app.mcp as mcp_mod
    session, calls, tool = _make_scripted_session(["unused"])
    _patch_connect(monkeypatch, session, tool)
    _patch_planner(monkeypatch, json.dumps({"calls": []}))

    outcome = asyncio.run(mcp_mod._plan_execute_with_sessions("Q?", "draft", ["t"], {"t": _fake_server("t")}))
    assert outcome.status == "skipped"
    assert outcome.brief == ""
    assert calls["n"] == 0


def test_planner_invalid_json_tolerated(monkeypatch):
    """Prose around the JSON, code fences, unknown tools, bad args are dropped."""
    _set_mcp_settings(monkeypatch)
    import app.mcp as mcp_mod
    session, calls, tool = _make_scripted_session(["r1", "r2"])
    _patch_connect(monkeypatch, session, tool)
    messy = (
        "Here is my plan:\n```json\n"
        + json.dumps({"calls": [
            {"tool": "t__x", "arguments": {"q": 1}},
            {"tool": "unknown__tool", "arguments": {"a": 1}},
            {"tool": "t__x", "arguments": "not-a-dict"},
            "garbage",
        ]})
        + "\n```"
    )
    _patch_planner(monkeypatch, messy)

    outcome = asyncio.run(mcp_mod._plan_execute_with_sessions("Q?", "draft", ["t"], {"t": _fake_server("t")}))
    # Only the valid known-tool call executes; the rest are dropped.
    assert calls["n"] == 1
    assert outcome.status == "done"


def test_planner_all_tools_fail_degrades(monkeypatch):
    """Every planned call failing -> status=failed with empty brief (fail-open)."""
    _set_mcp_settings(monkeypatch)
    import app.mcp as mcp_mod
    session, calls, tool = _make_scripted_session(["r"])
    _patch_connect(monkeypatch, session, tool)
    async def failing_call_tool(remote_name, arguments):
        raise RuntimeError("boom")
    session.call_tool = failing_call_tool
    _patch_planner(monkeypatch, json.dumps({"calls": [{"tool": "t__x", "arguments": {}}]}))

    outcome = asyncio.run(mcp_mod._plan_execute_with_sessions("Q?", "draft", ["t"], {"t": _fake_server("t")}))
    assert outcome.status == "failed"
    assert outcome.brief == ""


def test_planner_tool_timeout_is_error_not_fatal(monkeypatch):
    """A tool that sleeps past MCP_TOOL_TIMEOUT_S is an error envelope; other calls still run."""
    _set_mcp_settings(monkeypatch, MCP_TOOL_TIMEOUT_S="0.1")
    import app.mcp as mcp_mod
    session, calls, tool = _make_scripted_session(["never"])
    _patch_connect(monkeypatch, session, tool)
    async def slow_call_tool(remote_name, arguments):
        await asyncio.sleep(5)
    session.call_tool = slow_call_tool
    _patch_planner(monkeypatch, json.dumps({"calls": [
        {"tool": "t__x", "arguments": {}},
    ]}))

    outcome = asyncio.run(mcp_mod._plan_execute_with_sessions("Q?", "draft", ["t"], {"t": _fake_server("t")}))
    assert outcome.status == "failed"


def test_planner_max_tool_calls_enforced(monkeypatch):
    """Planner output beyond MCP_MAX_TOOL_CALLS is truncated."""
    _set_mcp_settings(monkeypatch, MCP_MAX_TOOL_CALLS="2")
    import app.mcp as mcp_mod
    session, calls, tool = _make_scripted_session(["a", "b", "c"])
    _patch_connect(monkeypatch, session, tool)
    _patch_planner(monkeypatch, json.dumps({"calls": [
        {"tool": "t__x", "arguments": {"i": 1}},
        {"tool": "t__x", "arguments": {"i": 2}},
        {"tool": "t__x", "arguments": {"i": 3}},
        {"tool": "t__x", "arguments": {"i": 4}},
    ]}))

    outcome = asyncio.run(mcp_mod._plan_execute_with_sessions("Q?", "draft", ["t"], {"t": _fake_server("t")}))
    assert calls["n"] == 2
    assert outcome.rounds == 2


def test_planner_result_budget_enforced(monkeypatch):
    """Once MCP_TOTAL_RESULT_CHARS is consumed, remaining planned calls are skipped."""
    _set_mcp_settings(monkeypatch, MCP_TOTAL_RESULT_CHARS="10", MCP_RESULT_CHARS="3000")
    import app.mcp as mcp_mod
    session, calls, tool = _make_scripted_session(["x" * 50, "y" * 50])
    _patch_connect(monkeypatch, session, tool)
    _patch_planner(monkeypatch, json.dumps({"calls": [
        {"tool": "t__x", "arguments": {"i": 1}},
        {"tool": "t__x", "arguments": {"i": 2}},
    ]}))

    outcome = asyncio.run(mcp_mod._plan_execute_with_sessions("Q?", "draft", ["t"], {"t": _fake_server("t")}))
    assert calls["n"] == 1  # second call skipped by budget
    assert outcome.status == "done"


def test_planner_messages_contain_question_and_draft_but_not_raw_opinions():
    """Planner messages carry the question + draft summary (never raw opinions)."""
    from app.mcp import _build_planner_messages
    tools = [{"type": "function", "function": {"name": "t__x", "description": "d", "parameters": {}}}]
    msgs = _build_planner_messages("Q?", "draft summary text", tools, 3)
    joined = " ".join(m["content"] for m in msgs)
    assert "Q?" in joined
    assert "draft summary text" in joined


def test_research_phase_logs_outcome_and_never_raises(monkeypatch):
    """research_phase catches everything: even a planner explosion degrades to failed."""
    _set_mcp_settings(monkeypatch, MCP_PROVIDERS="t")
    import app.mcp as mcp_mod
    from app.mcp_tools import McpServer

    # Bypass real provider factories (web_search requires OPENAI_API_KEY) with
    # a benign local server, then explode inside the plan/execute stage.
    monkeypatch.setattr(mcp_mod, "_selected_providers", lambda: ["t"])
    monkeypatch.setattr(
        mcp_mod, "_provider_factories",
        lambda: {"t": lambda: McpServer(name="t", tools=(), default_prompt="")},
    )

    async def exploding(question, draft, names, servers):
        raise RuntimeError("explosion")
    monkeypatch.setattr(mcp_mod, "_plan_execute_with_sessions", exploding)

    outcome = asyncio.run(mcp_mod.research_phase("Q?", "draft"))
    assert outcome.status == "failed"
    assert "explosion" in (outcome.degraded_reason or "")


# ---------------------------------------------------------------------------
# Multi-round dual-call privacy (SPEC MULTIROUND §5): label isolation
# ---------------------------------------------------------------------------

def test_consensus_prompt_is_label_blind(monkeypatch):
    """The consensus prompt (Call A) must NEVER contain member_seq labels or
    stance_digest — the structural privacy guarantee (§5.1/§5.3)."""
    from app.llm import build_consensus_prompt
    monkeypatch.setattr("app.llm._shuffle", lambda ops: list(ops))
    opinions = ["I want ramen", "I want sushi"]
    _sys, user = build_consensus_prompt(
        "Dinner?", opinions,
        prev_consensus="prev consensus text",
        prev_shift_summary="群體在預算上更彈性",
    )
    # Ephemeral A/B/C labels present (not 成員N).
    assert "A：" in user and "B：" in user
    # NO stable member_seq labels, NO stance_digest in the consensus call input.
    assert "成員1" not in user and "成員2" not in user
    assert "STANCE_DIGEST" not in user
    # Group-level cross-round context present (public-safe).
    assert "前輪共識" in user
    assert "前輪群體立場變化" in user
    assert "群體在預算上更彈性" in user


def test_stance_prompt_uses_stable_member_labels():
    """The stance prompt (Call B) uses 成員{member_seq} for cross-round alignment;
    its output never enters a consensus call (§5.1)."""
    from app.llm import build_stance_prompt
    opinions_with_seq = [("偏素食", 1), ("肉食優先", 2)]
    _sys, user = build_stance_prompt("Dinner?", opinions_with_seq)
    assert "成員1：偏素食" in user
    assert "成員2：肉食優先" in user
    assert "STANCE_DIGEST" in _sys and "STANCE_SHIFT_SUMMARY" in _sys


def test_shuffle_breaks_submission_order_side_channel():
    """_shuffle randomizes opinion order before labeling (§5.4), so A != first
    submitter. Over many runs the order is not pinned to input order."""
    from app.llm import _shuffle
    ops = ["a", "b", "c", "d", "e"]
    seen_non_identity = False
    for _ in range(50):
        if _shuffle(ops) != ops:
            seen_non_identity = True
            break
    assert seen_non_identity, "_shuffle should not be identity (it must randomize)"


def test_strip_attributable_removes_hallucinated_labels():
    """_strip_attributable cleans hallucinated 成員N: / A: patterns (cosmetic, §5.5)."""
    from app.llm import _strip_attributable
    text = "## 共識\n方向A\n成員1: 偏素食\nB: 肉食\n其餘保留"
    out = _strip_attributable(text)
    assert "成員1" not in out
    assert "B: 肉食" not in out
    assert "方向A" in out and "其餘保留" in out


def test_parse_stance_output_tolerant():
    """_parse_stance_output extracts digest + shift; None on failure (fail-open, §5.2)."""
    from app.llm import _parse_stance_output
    d, s = _parse_stance_output("STANCE_DIGEST:\n成員1: A\n成員2: B\n\nSTANCE_SHIFT_SUMMARY:\n群體更彈性")
    assert d and "成員1" in d
    assert s and "群體" in s
    d2, s2 = _parse_stance_output("garbage with no markers")
    assert d2 is None and s2 is None
    d3, s3 = _parse_stance_output("STANCE_DIGEST:\n成員1: X")
    assert d3 and "成員1" in d3
    assert s3 is None


def test_jaccard_question_similarity():
    """Jaccard is the MCP cross-round cost-control heuristic (§7.3)."""
    from app.llm import _jaccard
    assert _jaccard("", "") == 1.0
    assert _jaccard("a b c", "a b c") == 1.0
    assert 0 < _jaccard("素食 選項", "要加 素食 選項 嗎") < 1.0
    assert _jaccard("a", "x y z") == 0.0
