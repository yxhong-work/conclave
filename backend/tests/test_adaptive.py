# backend/tests/test_adaptive.py
"""Adaptive per-member questioning tests (SPEC docs/ADAPTIVE_SPEC.md §14).

Unit tests cover prompt construction, stance-line extraction, and output
validation. Integration tests cover the /my-question matrix, rounds/next
override semantics, P-stage fail-open behavior in run_analysis, and close-path
cleanup. No real LLM: the P seam is monkeypatched (§14.3).
"""
import re

import pytest

from app import llm
from app.llm import (
    build_adaptive_question_prompt,
    build_consensus_prompt,
    extract_stance_line,
    validate_member_question,
    QUESTION_MAX_CHARS,
    QUESTION_NONE,
)

# ---------------------------------------------------------------------------
# Unit: build_adaptive_question_prompt (SPEC §6.4, §14.1)
# ---------------------------------------------------------------------------

OPINION_X = "我吃素三十年了，希望找有素食選項的地方"
OPINION_Y = "我想吃很多肉，最好是燒肉"

def _member_labels_absent(prompt: str) -> None:
    """Shared regression assertion: no 成員N label pattern may appear (SPEC §10.2)."""
    assert re.search(r"成員\s?\d", prompt) is None
    assert re.search(r"編號\s?\d", prompt) is None
    assert re.search(r"第[一二三四五六七八九十]+位", prompt) is None


def test_prompt_contains_own_opinion_not_others():
    system, user = build_adaptive_question_prompt(
        question="晚餐吃什麼？",
        current_consensus="共識：找葷素兼賣的店",
        prev_shift_summary=None,
        x_stance_body="偏好素食，預算 300 內",
        x_opinion=OPINION_X,
        x_prior_questions=None,
        rounds_remaining=3,
    )
    assert OPINION_X in user
    assert OPINION_Y not in system + user
    _member_labels_absent(system + user)


def test_prompt_label_blind_regression():
    """SPEC §14.1 洩漏回歸: stance body with a 成員N: prefix key must be stripped
    before use — the prompt must not contain any member label token."""
    _, user = build_adaptive_question_prompt(
        question="Q?",
        current_consensus="部分成員偏好素食",
        prev_shift_summary=None,
        x_stance_body="成員2: 偏好素食",
        x_opinion=OPINION_X,
        x_prior_questions=None,
        rounds_remaining=3,
    )
    _member_labels_absent(user)
    assert "偏好素食" in user  # the stance BODY survives, only the key is stripped


def test_prompt_aggregate_words_allowed():
    """SPEC §14.1 反向測試: consensus containing the aggregate phrase 「部分成員」
    is legitimate (§10.4) and MUST NOT be scrubbed from the prompt."""
    consensus = "部分成員希望增加預算彈性"
    _, user = build_adaptive_question_prompt(
        question="Q?", current_consensus=consensus, prev_shift_summary=None,
        x_stance_body=None, x_opinion=OPINION_X, x_prior_questions=None,
        rounds_remaining=3,
    )
    assert consensus in user


def test_prompt_explore_vs_converge_strategy_block():
    """SPEC §6.4: rounds_remaining >= 2 → EXPLORE only; == 1 → CONVERGE only."""
    sys_explore, _ = build_adaptive_question_prompt(
        question="Q?", current_consensus="C", prev_shift_summary=None,
        x_stance_body=None, x_opinion="O", x_prior_questions=None,
        rounds_remaining=3,
    )
    assert "EXPLORE" in sys_explore
    assert "CONVERGE" not in sys_explore

    sys_converge, _ = build_adaptive_question_prompt(
        question="Q?", current_consensus="C", prev_shift_summary=None,
        x_stance_body=None, x_opinion="O", x_prior_questions=None,
        rounds_remaining=1,
    )
    assert "CONVERGE" in sys_converge
    assert "EXPLORE" not in sys_converge


def test_prompt_converge_uses_current_round_consensus():
    """SPEC §14.1: the CONVERGE prompt must embed the CURRENT round consensus."""
    consensus = "單一推薦方案：素食吃到飽"
    _, user = build_adaptive_question_prompt(
        question="Q?", current_consensus=consensus, prev_shift_summary=None,
        x_stance_body=None, x_opinion="O", x_prior_questions=None,
        rounds_remaining=1,
    )
    assert consensus in user


def test_prompt_prior_questions_included():
    _, user = build_adaptive_question_prompt(
        question="Q?", current_consensus="C", prev_shift_summary=None,
        x_stance_body=None, x_opinion="O",
        x_prior_questions=["你能接受植物肉嗎？"],
        rounds_remaining=2,
    )
    assert "你能接受植物肉嗎？" in user


# ---------------------------------------------------------------------------
# Unit: extract_stance_line strict contract (SPEC §6.2, §14.1)
# ---------------------------------------------------------------------------

DIGEST = "成員1: 偏好日式，重視安靜\n成員2: 想吃燒肉，預算上限 500\n成員3: 任何都好"

def test_extract_stance_line_happy_path():
    assert extract_stance_line(DIGEST, 2) == "想吃燒肉，預算上限 500"


def test_extract_stance_line_strips_key():
    body = extract_stance_line(DIGEST, 1)
    assert body == "偏好日式，重視安靜"
    assert "成員" not in body


def test_extract_stance_line_missing_line_returns_none():
    assert extract_stance_line("成員1: X", 4) is None


def test_extract_stance_line_duplicate_match_fails_closed():
    """A seq matching more than one line is a parse failure → None (no wrong row)."""
    dup = "成員2: A\n成員2: B"
    assert extract_stance_line(dup, 2) is None


def test_extract_stance_line_out_of_range_label_fails_closed():
    """Out-of-range labels mean the digest is misaligned → None (SPEC §6.2).
    Production callers always pass valid_seqs (the round's seq set); the
    group-wide gate is validate_stance_label_set."""
    bad = "成員1: X\n成員9: Y"
    assert extract_stance_line(bad, 1, valid_seqs={1}) is None
    # A well-formed digest passes with the round's seq set.
    assert extract_stance_line("成員1: X", 1, valid_seqs={1}) == "X"


def test_extract_stance_line_embedded_reference_is_rejected():
    """An opinion quoting '成員N:' inline breaks the one-line-per-seq contract → None."""
    tricky = "成員1: 我同意成員2: 的說法\n成員2: B"
    assert extract_stance_line(tricky, 1) is None


def test_extract_stance_line_null_digest():
    assert extract_stance_line(None, 1) is None


def test_validate_label_set_missing_or_extra_fails():
    """Label-set validation: the digest must have exactly one line per member_seq
    of the round, no more, no less (SPEC §6.2 rule 2)."""
    assert llm.validate_stance_label_set(DIGEST, {1, 2, 3}) is True
    assert llm.validate_stance_label_set("成員1: X", {1, 2, 3}) is False
    assert llm.validate_stance_label_set(DIGEST + "\n成員9: Z", {1, 2, 3}) is False
    assert llm.validate_stance_label_set(None, {1, 2}) is False


# ---------------------------------------------------------------------------
# Unit: output validation (SPEC §6.5, §14.1)
# ---------------------------------------------------------------------------

def test_validate_output_normal():
    ok, text = validate_member_question("你能接受有植物肉選項的燒肉店嗎？")
    assert ok and text == ["你能接受有植物肉選項的燒肉店嗎？"]


def test_validate_output_two_numbered_questions():
    """The requested numbered form: both questions survive, numbering stripped."""
    ok, qs = validate_member_question(
        "1. 你能接受有植物肉選項的燒肉店嗎？\n2. 你能接受葷素火鍋嗎？")
    assert ok
    assert qs == ["你能接受有植物肉選項的燒肉店嗎？", "你能接受葷素火鍋嗎？"]


def test_validate_output_three_numbered_questions():
    """Up to QUESTION_MAX_COUNT (3) questions survive."""
    ok, qs = validate_member_question(
        "1. A嗎？\n2. B嗎？\n3. C嗎？\n4. D嗎？")
    assert ok
    assert len(qs) == 3


def test_validate_output_none_sentinel():
    """NONE = the LLM decided no probing is needed → ok=False → member falls
    back to the anchor (§6.5: 個人化問答視需求決定,0 題是合法結果)."""
    ok, qs = validate_member_question(QUESTION_NONE)
    assert ok is False
    assert qs == []


def test_validate_output_second_question_labelled_first_survives():
    """§10.4 enforcement is per-question: a labelled second question is dropped,
    the clean first one survives (per-member fairness — one bad probe must not
    erase the other)."""
    ok, qs = validate_member_question(
        "1. 你能接受有植物肉選項的燒肉店嗎？\n2. 身為成員2的你，能接受嗎？")
    assert ok
    assert qs == ["你能接受有植物肉選項的燒肉店嗎？"]


def test_validate_output_empty_after_scrub():
    ok, _ = validate_member_question("成員2: 開頭標籤整行被吃掉後剩殘句")
    assert ok is False


def test_validate_output_too_long():
    ok, _ = validate_member_question("問" * (QUESTION_MAX_CHARS + 1))
    assert ok is False


def test_validate_output_labellecho_no_colon():
    """「身為成員2的你…」 — no colon, _ATTRIBUTABLE can't scrub it; the label
    check must reject it outright (SPEC §6.5 (c))."""
    ok, _ = validate_member_question("身為成員2的你，能接受植物肉嗎？")
    assert ok is False


def test_validate_output_descriptive_reference_rejected():
    ok, _ = validate_member_question("對吃素的那位，你願意妥協嗎？")
    assert ok is False


def test_validate_output_aggregate_allowed():
    ok, _ = validate_member_question("部分成員偏好素食，你能接受改吃植物肉嗎？")
    assert ok is True


# ---------------------------------------------------------------------------
# Unit: build_consensus_prompt final_round (SPEC §6.3)
# ---------------------------------------------------------------------------

def test_consensus_prompt_final_round_false_is_unchanged():
    """final_round=False output must be byte-identical to the current behavior."""
    a = build_consensus_prompt("Q?", ["o1"], None, None, None)
    b = build_consensus_prompt("Q?", ["o1"], None, None, None, final_round=False)
    assert a == b


def test_consensus_prompt_final_round_true_requires_single_plan():
    sys_with = build_consensus_prompt("Q?", ["o1"], None, None, None, final_round=True)[0]
    sys_without = build_consensus_prompt("Q?", ["o1"], None, None, None, final_round=False)[0]
    assert "單一推薦方案" in sys_with
    assert "單一推薦方案" not in sys_without


# ---------------------------------------------------------------------------
# Integration (SPEC §14.2) — below this line a live Postgres is required.
# ---------------------------------------------------------------------------

import asyncio

from fastapi.testclient import TestClient

from app.main import app as fastapi_app

pytestmark = pytest.mark.integration

ADAPTIVE_ON = {"ADAPTIVE_QUESTIONS_ENABLED": "true"}


def _create(client, question="Q?", nickname="A", expected_count=None, max_rounds=3,
            adaptive=False):
    return client.post("/api/groups", json={
        "question": question, "creator_nickname": nickname,
        "expected_count": expected_count, "max_rounds": max_rounds,
        "adaptive_questions": adaptive,
    }).json()


async def _count_rows(group_id, round_number):
    import asyncpg
    conn = await asyncpg.connect(dsn="postgresql://conclave:conclave@localhost:5432/conclave")
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM member_questions WHERE group_id=$1 AND round_number=$2",
            group_id, round_number,
        )
    finally:
        await conn.close()


# --- db helpers ------------------------------------------------------------

async def test_member_question_crud(pool):
    from app.db import (replace_member_questions, get_member_question,
                        clear_member_questions, get_prior_member_questions,
                        purge_future_member_questions, prune_delivered_member_questions,
                        count_member_questions)
    from app.db import init_db
    await init_db(pool)
    g = await pool.fetchrow(
        "INSERT INTO groups (pin, question, creator_token, adaptive_questions) "
        "VALUES ('PIN01', 'Q', 'tok', TRUE) RETURNING id")
    p1 = await pool.fetchrow(
        "INSERT INTO participants (group_id, nickname, member_seq) VALUES ($1, 'A', 1) RETURNING id",
        g["id"])
    p2 = await pool.fetchrow(
        "INSERT INTO participants (group_id, nickname, member_seq) VALUES ($1, 'B', 2) RETURNING id",
        g["id"])

    # upsert single item
    await replace_member_questions(pool, g["id"], 2, [(p1["id"], "題1")])
    assert await get_member_question(pool, g["id"], 2, p1["id"]) == "題1"
    # re-upsert overwrites (analysis retry path)
    await replace_member_questions(pool, g["id"], 2, [(p1["id"], "題1b")])
    assert await get_member_question(pool, g["id"], 2, p1["id"]) == "題1b"
    # no row → None
    assert await get_member_question(pool, g["id"], 2, p2["id"]) is None
    # no row for other round → None
    assert await get_member_question(pool, g["id"], 3, p1["id"]) is None

    # prior questions: only that participant's, only rounds < up_to (strictly)
    await replace_member_questions(pool, g["id"], 3, [(p1["id"], "題3")])
    priors = await get_prior_member_questions(pool, g["id"], p1["id"], up_to_round=3)
    assert priors == [(2, "題1b")]
    assert await get_prior_member_questions(pool, g["id"], p1["id"], up_to_round=4) == [
        (2, "題1b"), (3, "題3")]
    # other member sees nothing
    assert await get_prior_member_questions(pool, g["id"], p2["id"], up_to_round=3) == []

    # count
    assert await count_member_questions(pool, g["id"], 2) == 1

    # prune delivered (< opened_round)
    await prune_delivered_member_questions(pool, g["id"], opened_round=3)
    assert await count_member_questions(pool, g["id"], 2) == 0
    assert await count_member_questions(pool, g["id"], 3) == 1

    # purge future (> current_round)
    await purge_future_member_questions(pool, g["id"], current_round=2)
    assert await count_member_questions(pool, g["id"], 3) == 0

    # clear round
    await replace_member_questions(pool, g["id"], 2, [(p1["id"], "x")])
    await clear_member_questions(pool, g["id"], 2)
    assert await count_member_questions(pool, g["id"], 2) == 0


async def test_migration_member_questions_table(pool):
    from app.db import init_db
    await init_db(pool)
    cols = await pool.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='member_questions'")
    names = {c["column_name"] for c in cols}
    assert {"id", "group_id", "round_number", "participant_id", "question",
            "created_at"} <= names
    # groups.adaptive_questions exists with default FALSE
    d = await pool.fetchval(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name='groups' AND column_name='adaptive_questions'")
    assert d is not None and "FALSE" in d.upper()


# --- run_analysis P stage (SPEC §14.2) --------------------------------------

def _stub_openai(monkeypatch, p_behavior="ok", captured=None):
    """Stub AsyncOpenAI so consensus/stance calls succeed and the P seam behaves
    per p_behavior: 'ok' → one-line question; 'raise' → exception; a callable
    (system, user) -> str for per-call routing (§14.3 system-prompt marker).
    Also stubs the /v1/models probe (httpx.get) so run_analysis never needs a
    live LLM endpoint."""
    class FakeResp:
        def __init__(self, content):
            self.choices = [type("C", (), {"message": type("M", (), {"content": content})(),
                                          "finish_reason": "stop"})()]
        usage = None

    monkeypatch.setattr("app.llm.httpx.get",
                        lambda *a, **k: type("R", (), {"json": staticmethod(
                            lambda: {"data": [{"id": "fake-model"}]})})())
    monkeypatch.setattr(llm, "_cached_model", None)

    class FakeCompletions:
        async def create(self, model=None, messages=None, **kw):
            system = messages[0]["content"]
            if captured is not None:
                captured.append(system + "\n---\n" + messages[1]["content"])
            if "personalized follow-up question" in system:
                if p_behavior == "raise":
                    raise RuntimeError("P_X forced failure")
                if callable(p_behavior):
                    return FakeResp(p_behavior(system, messages[1]["content"]))
                if p_behavior == "label-echo":
                    return FakeResp("身為成員2的你，能接受嗎？")
                if p_behavior == "long":
                    return FakeResp("問" * (QUESTION_MAX_CHARS + 1))
                if p_behavior == "none":
                    return FakeResp("NONE")
                return FakeResp("1. 你能接受有植物肉選項的燒肉店嗎？\n2. 你能接受素食與葵食分開料理的火鍋店嗎？")
            if "private note-taker" in system:
                return FakeResp("STANCE_DIGEST:\n成員1: 偏好素食\n成員2: 想吃肉\n\n"
                                "STANCE_SHIFT_SUMMARY:\n群體立場維持分歧")
            return FakeResp("## 共識\n- 方向 A")

    class FakeClient:
        def __init__(self, **kw):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    # llm.py does `from openai import AsyncOpenAI` inside run_analysis, so the
    # module attribute is looked up at call time — patch the openai module.
    monkeypatch.setattr("openai.AsyncOpenAI", FakeClient)


@pytest.fixture
def adaptive_on(monkeypatch):
    """Enable the adaptive feature for a test; reset the settings cache after
    so other tests are unaffected by the lru_cache (§14.3 discipline)."""
    monkeypatch.setenv("ADAPTIVE_QUESTIONS_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_run_analysis_p_stage_writes_rows(monkeypatch, adaptive_on):
    """Consensus succeeds + P seam succeeds → N rows for round N+1 (§14.2)."""
    _stub_openai(monkeypatch, p_behavior="ok")
    with TestClient(fastapi_app) as client:
        g = _create(client, adaptive=True, expected_count=2, max_rounds=3)
        pin, pid = g["pin"], g["participant_id"]
        bob = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"}).json()
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": pid, "content": OPINION_X})
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": bob["participant_id"], "content": OPINION_Y})
        import time; time.sleep(0.6)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "done"
        assert s["member_question_count"] == 2
        # The personal question must be fetchable via /my-question (next round)
        r = client.get(f"/api/groups/{pin}/my-question",
                       params={"participant_id": pid, "round": 2})
        assert r.json()["is_personal"] is True


def test_run_analysis_p_fail_open(monkeypatch, adaptive_on):
    """P seam raises for everyone → still done, zero rows, anchor fallback (§11 row 2)."""
    monkeypatch.setattr("app.llm._RETRY_BACKOFF_S", (0, 0))
    _stub_openai(monkeypatch, p_behavior="raise")
    with TestClient(fastapi_app) as client:
        g = _create(client, adaptive=True, expected_count=2)
        pin, pid = g["pin"], g["participant_id"]
        bob = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"}).json()
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": pid, "content": OPINION_X})
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": bob["participant_id"], "content": OPINION_Y})
        import time; time.sleep(0.6)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "done"
        # P fully failed → 0 prepared rows (count is a legal 0, §7.4)
        assert s["member_question_count"] == 0
        r = client.get(f"/api/groups/{pin}/my-question",
                       params={"participant_id": pid, "round": 2})
        assert r.status_code == 200
        body = r.json()
        assert body["is_personal"] is False
        assert body["question"] == ""  # next round not open → empty anchor (§7.2 rule 4)


def test_run_analysis_p_label_echo_rejected(monkeypatch, adaptive_on):
    """P output containing a member-label pattern → rejected, no row (§6.5 (c))."""
    _stub_openai(monkeypatch, p_behavior="label-echo")
    with TestClient(fastapi_app) as client:
        g = _create(client, adaptive=True, expected_count=2)
        pin, pid = g["pin"], g["participant_id"]
        bob = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"}).json()
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": pid, "content": OPINION_X})
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": bob["participant_id"], "content": OPINION_Y})
        import time; time.sleep(0.6)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "done"
        # Label echo → all rejected → 0 rows (§6.5 (c) output-side enforcement)
        assert s["member_question_count"] == 0


def test_run_analysis_p_non_adaptive_group_skipped(monkeypatch, adaptive_on):
    """adaptive_questions=false group → no P calls at all, identical to today."""
    captured = []
    _stub_openai(monkeypatch, p_behavior="ok", captured=captured)
    with TestClient(fastapi_app) as client:
        g = _create(client, adaptive=False, expected_count=2)
        pin, pid = g["pin"], g["participant_id"]
        bob = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"}).json()
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": pid, "content": OPINION_X})
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": bob["participant_id"], "content": OPINION_Y})
        import time; time.sleep(0.6)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "done"
        assert s["adaptive_questions"] is False
        assert s["member_question_count"] is None
        assert captured, "consensus+stance calls must still happen"


def test_run_analysis_p_isolation_no_other_opinions(monkeypatch, adaptive_on):
    """§6.6/§10.2 structural isolation: X's prompt contains X's opinion but never
    another member's opinion text or any 成員N label."""
    captured = []
    _stub_openai(monkeypatch, p_behavior="ok", captured=captured)
    with TestClient(fastapi_app) as client:
        g = _create(client, adaptive=True, expected_count=2)
        pin, pid = g["pin"], g["participant_id"]
        bob = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"}).json()
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": pid, "content": OPINION_X})
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": bob["participant_id"], "content": OPINION_Y})
        import time; time.sleep(0.6)
        p_prompts = [c for c in captured if "personalized follow-up question" in c]
        assert len(p_prompts) == 2
        # Each prompt sees exactly one opinion, and the two prompts differ.
        with_x = [p for p in p_prompts if OPINION_X in p]
        with_y = [p for p in p_prompts if OPINION_Y in p]
        assert len(with_x) == 1 and len(with_y) == 1
        assert OPINION_Y not in with_x[0]
        assert OPINION_X not in with_y[0]
        assert re.search(r"成員\s?\d", with_x[0]) is None
        assert re.search(r"成員\s?\d", with_y[0]) is None


def test_run_analysis_p_log_redline(monkeypatch, adaptive_on, caplog):
    """§10.6: P-stage failure logs must not contain opinion text or question content."""
    import logging as _logging
    monkeypatch.setattr("app.llm._RETRY_BACKOFF_S", (0, 0))
    _stub_openai(monkeypatch, p_behavior="raise")
    with caplog.at_level(_logging.WARNING, logger="conclave.llm"):
        with TestClient(fastapi_app) as client:
            g = _create(client, adaptive=True, expected_count=1)
            pin, pid = g["pin"], g["participant_id"]
            client.post(f"/api/groups/{pin}/responses",
                        json={"participant_id": pid, "content": OPINION_X})
            import time; time.sleep(0.6)
            s = client.get(f"/api/groups/{pin}/state").json()
            assert s["status"] == "done"
    leaky = [r.getMessage() for r in caplog.records
             if OPINION_X[:10] in r.getMessage() or "個人化" in r.getMessage()]
    assert not leaky, f"opinion/question content leaked into logs: {leaky}"

def test_my_question_matrix(monkeypatch):
    """The full resolution matrix of GET /my-question (SPEC §7.2)."""
    monkeypatch.setattr("app.llm.run_analysis", _fake_run_adaptive(monkeypatch))
    with TestClient(fastapi_app) as client:
        g = _create(client, adaptive=True, expected_count=2, max_rounds=5)
        pin, pid = g["pin"], g["participant_id"]
        bob = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"}).json()
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": pid, "content": "op1"})
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": bob["participant_id"], "content": "op2"})
        import time; time.sleep(0.4)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "done"
        assert s["adaptive_questions"] is True
        # done + adaptive + current<max → count of prepared questions
        assert s["member_question_count"] == 2

        # Default round=current_round(1): rows are for the NEXT round (2) → anchor.
        r = client.get(f"/api/groups/{pin}/my-question", params={"participant_id": pid})
        assert r.status_code == 200
        body = r.json()
        assert body["is_personal"] is False
        assert body["question"] == "Q?"  # anchor = groups.question
        assert body["round"] == 1

        # Preview the next round (current_round+1) → personal row.
        r = client.get(f"/api/groups/{pin}/my-question",
                       params={"participant_id": pid, "round": 2})
        assert r.status_code == 200
        body = r.json()
        assert body["is_personal"] is True
        assert body["round"] == 2
        assert "個人化問題" in body["question"]

        # Invalid pid → anchor, NOT 404, is_personal must be False (§7.2 rule 5)
        r = client.get(f"/api/groups/{pin}/my-question",
                       params={"participant_id": "00000000-0000-0000-0000-000000000000"})
        assert r.status_code == 200
        assert r.json()["is_personal"] is False

        # Group not found → unified 404
        r = client.get("/api/groups/ZZZZZ/my-question", params={"participant_id": pid})
        assert r.status_code == 404
        assert r.json()["detail"] == "not found"

        # Round out of range → unified 404
        r = client.get(f"/api/groups/{pin}/my-question",
                       params={"participant_id": pid, "round": 5})
        assert r.status_code == 404
        assert r.json()["detail"] == "not found"

        # Missing pid → unified 404
        r = client.get(f"/api/groups/{pin}/my-question")
        assert r.status_code == 404


def test_my_question_kill_switch(monkeypatch):
    """Global kill-switch off → /my-question unified 404, /state reports false (§12.1)."""
    monkeypatch.setenv("ADAPTIVE_QUESTIONS_ENABLED", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr("app.llm.run_analysis", _fake_run_adaptive(monkeypatch))
    try:
        with TestClient(fastapi_app) as client:
            g = _create(client, adaptive=True)
            pin, pid = g["pin"], g["participant_id"]
            r = client.get(f"/api/groups/{pin}/my-question", params={"participant_id": pid})
            assert r.status_code == 404
            assert r.json()["detail"] == "not found"
            s = client.get(f"/api/groups/{pin}/state").json()
            assert s["adaptive_questions"] is False
            assert s["member_question_count"] is None
    finally:
        get_settings.cache_clear()


def _fake_run_adaptive(monkeypatch, with_rows=True):
    """A run_analysis fake that ALSO writes member_questions rows for every
    submitted member of the current round, mimicking a successful P stage
    (with_rows=False mimics P fully degraded: zero rows)."""
    async def fake_run(pool, group_id):
        from app.db import (get_group_by_id, set_round_done,
                            get_round_responses_ordered, replace_member_questions)
        from app.broadcast import broadcast
        g = await get_group_by_id(pool, group_id)
        rnd = g["current_round"]
        if with_rows:
            rows = await get_round_responses_ordered(pool, group_id, rnd)
            for r in rows:
                await replace_member_questions(
                    pool, group_id, rnd + 1,
                    [(r["participant_id"], f"個人化問題(第{rnd + 1}輪)")])
        await set_round_done(pool, group_id, rnd, "# 共識", None, None)
        await broadcast(g["pin"], "consensus", {"content": "# 共識", "round": rnd})
        await broadcast(g["pin"], "phase", {"status": "done", "round": rnd})
    monkeypatch.setattr("app.llm.run_analysis", fake_run)
    return fake_run



# --- rounds/next override & lifecycle cleanup (SPEC §14.2) ------------------

def _drive_to_done(client, monkeypatch, adaptive=True, with_rows=True):
    """Create an adaptive group, drive it to done with 2 submitted members."""
    monkeypatch.setattr("app.llm.run_analysis", _fake_run_adaptive(monkeypatch, with_rows=with_rows))
    g = _create(client, adaptive=adaptive, expected_count=2, max_rounds=5)
    pin, pid, tok = g["pin"], g["participant_id"], g["creator_token"]
    bob = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"}).json()
    client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": OPINION_X})
    client.post(f"/api/groups/{pin}/responses", json={"participant_id": bob["participant_id"], "content": OPINION_Y})
    import time; time.sleep(0.4)
    s = client.get(f"/api/groups/{pin}/state").json()
    assert s["status"] == "done"
    return g, bob["participant_id"]


def test_override_clears_prepared_questions_and_skips_call_q(monkeypatch):
    """Creator override → all prepared rows for the new round deleted; the Call Q
    ladder is NOT executed (no wasted LLM call, §4.3)."""
    with TestClient(fastapi_app) as client:
        g, bob_pid = _drive_to_done(client, monkeypatch)
        pin, pid, tok = g["pin"], g["participant_id"], g["creator_token"]
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["member_question_count"] == 2
        monkeypatch.setattr("app.routes.groups.NEXT_ROUND_COOLDOWN_S", 0)
        # Question-gen seam must NOT be hit (override path).
        def _boom(*a, **kw):
            raise AssertionError("generate_next_question must not run on override")
        monkeypatch.setattr("app.llm.generate_next_question", _boom)
        r = client.post(f"/api/groups/{pin}/rounds/next",
                        json={"creator_token": tok, "question": "共同問題覆寫"})
        assert r.status_code == 202, r.text
        # rows for the new round are gone → /my-question falls to anchor
        r = client.get(f"/api/groups/{pin}/my-question",
                       params={"participant_id": pid, "round": 2})
        assert r.status_code == 200
        body = r.json()
        assert body["is_personal"] is False
        assert body["question"] == "共同問題覆寫"


def test_open_next_prunes_delivered_history(monkeypatch):
    """After a successful open, only the current round's rows survive (§5.1)."""
    with TestClient(fastapi_app) as client:
        g, bob_pid = _drive_to_done(client, monkeypatch)
        pin, tok = g["pin"], g["creator_token"]
        monkeypatch.setattr("app.routes.groups.NEXT_ROUND_COOLDOWN_S", 0)
        r = client.post(f"/api/groups/{pin}/rounds/next", json={"creator_token": tok})
        assert r.status_code == 202
        # rows moved: round 1 rows pruned, round 2 rows live
        import asyncio, asyncpg
        async def _counts():
            conn = await asyncpg.connect(dsn="postgresql://conclave:conclave@localhost:5432/conclave")
            try:
                c1 = await conn.fetchval(
                    "SELECT count(*) FROM member_questions WHERE group_id=$1 AND round_number=1",
                    g["pin"] and (await _gid(conn, g["pin"])))
                c2 = await conn.fetchval(
                    "SELECT count(*) FROM member_questions WHERE group_id=$1 AND round_number=2",
                    await _gid(conn, g["pin"]))
                return c1, c2
            finally:
                await conn.close()
        async def _gid(conn, pin):
            return await conn.fetchval("SELECT id FROM groups WHERE pin=$1", pin)
        c1, c2 = asyncio.run(_counts())
        assert (c1, c2) == (0, 2)


def test_close_purges_future_questions(monkeypatch):
    """Manual close → future-round rows deleted (§5.1)."""
    with TestClient(fastapi_app) as client:
        g, _ = _drive_to_done(client, monkeypatch)
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        # sanity: rows exist for round 2
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["member_question_count"] == 2
        r = client.post(f"/api/groups/{pin}/rounds/close", json={"creator_token": tok})
        assert r.status_code == 200
        # Purged round-2 rows: /my-question for round 2 now falls to the anchor
        # (round range still 1..current_round+1=2, so 200 + is_personal=false).
        r = client.get(f"/api/groups/{pin}/my-question",
                       params={"participant_id": pid, "round": 2})
        assert r.status_code == 200
        assert r.json()["is_personal"] is False
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["member_question_count"] is None  # status closed → null (§7.4)


def test_creator_dissolve_purges_future_questions(monkeypatch):
    """Creator leave (dissolve) → future-round rows deleted (§5.1)."""
    with TestClient(fastapi_app) as client:
        g, bob_pid = _drive_to_done(client, monkeypatch)
        pin, tok, pid = g["pin"], g["creator_token"], g["participant_id"]
        r = client.post(f"/api/groups/{pin}/leave",
                        json={"participant_id": pid, "creator_token": tok})
        assert r.status_code == 200
        import asyncio, asyncpg
        async def _count():
            conn = await asyncpg.connect(dsn="postgresql://conclave:conclave@localhost:5432/conclave")
            try:
                gid = await conn.fetchval("SELECT id FROM groups WHERE pin=$1", pin)
                return await conn.fetchval(
                    "SELECT count(*) FROM member_questions WHERE group_id=$1", gid)
            finally:
                await conn.close()
        assert asyncio.run(_count()) == 0


def test_non_submitting_member_gets_no_row(monkeypatch):
    """A member who joined but did not submit this round → no P_X, no row (§4.2)."""
    captured = []
    _stub_openai(monkeypatch, p_behavior="ok", captured=captured)
    monkeypatch.setattr("app.llm.run_analysis", _real_run_seam(monkeypatch))
    with TestClient(fastapi_app) as client:
        g = _create(client, adaptive=True)  # no expected_count → manual start
        pin, pid, tok = g["pin"], g["participant_id"], g["creator_token"]
        lazy = client.post(f"/api/groups/{pin}/join", json={"nickname": "Lazy"}).json()
        # only the creator submits; Lazy joins but never submits
        client.post(f"/api/groups/{pin}/responses", json={"participant_id": pid, "content": OPINION_X})
        r = client.post(f"/api/groups/{pin}/start", json={"creator_token": tok})
        assert r.status_code == 202
        import time; time.sleep(0.6)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "done"
        p_prompts = [c for c in captured if "personalized follow-up question" in c]
        assert len(p_prompts) == 1  # only the submitter
        # Lazy has no row → anchor at current round; also NOT is_personal for round 2
        r = client.get(f"/api/groups/{pin}/my-question",
                       params={"participant_id": lazy["participant_id"], "round": 2})
        assert r.status_code == 200
        assert r.json()["is_personal"] is False


def _real_run_seam(monkeypatch):
    """A run_analysis double that mimics the REAL P-stage behavior: uses the
    production _run_p_stage with the stubbed AsyncOpenAI (not a wholesale fake),
    so P-stage gating/enumeration/validation are exercised for real."""
    async def fake_run(pool, group_id):
        from app.db import (get_group_by_id, set_round_done, get_round_responses_ordered)
        g = await get_group_by_id(pool, group_id)
        rnd = g["current_round"]
        rows = await get_round_responses_ordered(pool, group_id, rnd)
        from app.llm import _run_p_stage, build_consensus_prompt, build_stance_prompt
        from app.llm import _chat_with_retry, _parse_stance_output, _strip_attributable
        from app.config import get_settings
        s = get_settings()
        client = __import__("openai", fromlist=["AsyncOpenAI"]).AsyncOpenAI()
        model = "fake-model"
        # real consensus + stance path (stubbed client)
        sys_p, user_p = build_consensus_prompt(g["question"], [r["content"] for r in rows])
        content = await _chat_with_retry(client, model, sys_p, user_p, s)
        st_sys, st_user = build_stance_prompt(
            g["question"], [(r["content"], r["member_seq"]) for r in rows])
        stance_text = await _chat_with_retry(client, model, st_sys, st_user, s)
        digest, shift = _parse_stance_output(stance_text)
        if shift:
            shift = _strip_attributable(shift)
        adaptive_on = s.adaptive_questions_enabled and g["adaptive_questions"]
        if adaptive_on and rnd < g["max_rounds"] and rows:
            await _run_p_stage(pool, s, client, model, g, rows, content, shift, digest)
        await set_round_done(pool, group_id, rnd, content, digest, shift)
    monkeypatch.setattr("app.llm.run_analysis", fake_run)
    return fake_run


def test_p_none_sentinel_member_uses_anchor(monkeypatch):
    """LLM decides no probing is needed (NONE) → no row → member uses the
    anchor; 個人化問答視需求決定, 0 題是合法結果 (§6.5)."""
    monkeypatch.setattr("app.llm._RETRY_BACKOFF_S", (0, 0))
    _stub_openai(monkeypatch, p_behavior="none")
    monkeypatch.setattr("app.llm.run_analysis", _real_run_seam(monkeypatch))
    with TestClient(fastapi_app) as client:
        g = _create(client, adaptive=True, expected_count=2)
        pin, pid = g["pin"], g["participant_id"]
        bob = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"}).json()
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": pid, "content": OPINION_X})
        client.post(f"/api/groups/{pin}/responses",
                    json={"participant_id": bob["participant_id"], "content": OPINION_Y})
        import time; time.sleep(0.6)
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["status"] == "done"
        # NONE → no rows prepared → count 0 (legal), members use the anchor
        assert s["member_question_count"] == 0
        r = client.get(f"/api/groups/{pin}/my-question",
                       params={"participant_id": pid, "round": 2})
        assert r.status_code == 200
        assert r.json()["is_personal"] is False
