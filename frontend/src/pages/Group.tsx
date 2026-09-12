import { useReducer, useEffect, useCallback, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  getState, submitResponse, startAnalysis, leaveGroup, ApiError,
  getRounds, openNextRound, closeGroup, RoundInfo, getMyQuestion,
} from "../lib/api";
import { useGroupSSE } from "../hooks/useGroupSSE";
import { useTypewriter } from "../hooks/useTypewriter";

type Phase = "joining" | "waiting" | "submitted" | "analyzing" | "done" | "error";
interface State {
  phase: Phase;
  question: string; status: string;
  participant_count: number; submitted_count: number;
  consensus: string | null; is_creator: boolean;
  deadline: string | null;
  error_msg: string; input: string;
  current_round: number; max_rounds: number;
  rounds: RoundInfo[];
  showNextForm: boolean; nextQuestion: string;
  nextError: string;  // transient error for the next-round action (cooldown/race), does NOT switch phase
  cooldown_remaining: number | null;  // seconds until the creator can open the next round (null = not applicable)
  // Adaptive per-member questioning (ADAPTIVE_SPEC §9)
  adaptive_questions: boolean;
  member_question_count: number | null;
  my_question: { round: number; question: string; is_personal: boolean } | null;
}
type Action =
  | { type: "init"; payload: Partial<State> }
  | { type: "phase"; status: string; round?: number }
  | { type: "progress"; participant_count: number; submitted_count: number }
  | { type: "consensus"; content: string; round?: number }
  | { type: "error"; message: string }
  | { type: "input"; value: string }
  | { type: "submitted" }
  | { type: "round_opened"; round: number; question: string }
  | { type: "round_closed" }
  | { type: "rounds_loaded"; rounds: RoundInfo[] }
  | { type: "toggle_next_form"; open: boolean }
  | { type: "next_question"; value: string }
  | { type: "next_error"; message: string }
  | { type: "my_question"; question: { round: number; question: string; is_personal: boolean } | null };

function mapPhase(status: string, cur: Phase): Phase {
  if (status === "collecting") return cur === "submitted" ? "submitted" : "waiting";
  if (status === "analyzing") return "analyzing";
  if (status === "done") return "done";
  if (status === "closed") return "done";
  if (status === "error") return "error";
  return cur;
}

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "init":
      return { ...s, ...a.payload, phase: mapPhase(a.payload.status || "collecting", s.phase) };
    case "phase":
      return { ...s, status: a.status, phase: mapPhase(a.status, s.phase) };
    case "progress":
      return { ...s, participant_count: a.participant_count, submitted_count: a.submitted_count };
    case "consensus":
      return { ...s, consensus: a.content, phase: "done" };
    case "error":
      return { ...s, error_msg: a.message, phase: "error" };
    case "input":
      return { ...s, input: a.value };
    case "submitted":
      if (s.status === "collecting" && s.phase !== "analyzing") {
        return { ...s, phase: "submitted" };
      }
      return s;
    case "round_opened":
      // A new round started: back to collecting, fresh input, new question.
      return {
        ...s, phase: "waiting", status: "collecting",
        current_round: a.round, question: a.question,
        consensus: null, input: "", submitted_count: 0,
        showNextForm: false, nextQuestion: "", nextError: "", cooldown_remaining: null,
      };
    case "round_closed":
      return { ...s, status: "closed", showNextForm: false };
    case "rounds_loaded":
      return { ...s, rounds: a.rounds };
    case "toggle_next_form":
      return { ...s, showNextForm: a.open, nextError: a.open ? s.nextError : "" };
    case "next_question":
      return { ...s, nextQuestion: a.value, nextError: "" };
    case "next_error":
      return { ...s, nextError: a.message };
    case "my_question":
      return { ...s, my_question: a.question };
  }
}

const init: State = {
  phase: "joining", question: "", status: "collecting",
  participant_count: 0, submitted_count: 0, consensus: null, is_creator: false,
  deadline: null, error_msg: "", input: "",
  current_round: 1, max_rounds: 3, rounds: [],
  showNextForm: false, nextQuestion: "", nextError: "", cooldown_remaining: null,
  adaptive_questions: false, member_question_count: null,
  my_question: null,
};

const ANALYZING_PHRASES = [
  "分析中…",
  "正在整合不同意見…",
  "尋找最大公約數…",
  "梳理未解分歧…",
  "即將完成…",
];

function AnalyzingLabel() {
  const [idx, setIdx] = useState(0);
  const { displayed, done } = useTypewriter(ANALYZING_PHRASES[idx], 90);
  useEffect(() => {
    if (done) {
      const t = setTimeout(() => setIdx(i => (i + 1) % ANALYZING_PHRASES.length), 1200);
      return () => clearTimeout(t);
    }
  }, [done]);
  return (
    <div className="analyzing-wrap">
      <div className="analyzing-light" />
      <p className="analyzing-label">
        {displayed}
        {!done && <span className="cursor">▎</span>}
      </p>
    </div>
  );
}

export default function Group() {
  const { pin } = useParams<{ pin: string }>();
  const nav = useNavigate();
  const [s, dispatch] = useReducer(reducer, init);
  const [copied, setCopied] = useState(false);
  const [copiedLink, setCopiedLink] = useState(false);
  const [remaining, setRemaining] = useState<number | null>(null);
  const pid = sessionStorage.getItem(`pin:${pin}:pid`) || "";
  // Only Create.tsx (creator flow) stores a token; Home.tsx (join) never does.
  // sessionStorage is per-tab, so each browser tab is an independent user.
  const token = sessionStorage.getItem(`pin:${pin}:token`) || "";

  const fetchState = useCallback(async () => {
    try {
      const st = await getState(pin!, token || undefined);
      dispatch({ type: "init", payload: {
        question: st.question, status: st.status,
        participant_count: st.participant_count, submitted_count: st.submitted_count,
        consensus: st.consensus, is_creator: st.is_creator, deadline: st.deadline,
        current_round: st.current_round, max_rounds: st.max_rounds,
        cooldown_remaining: st.cooldown_remaining,
        adaptive_questions: st.adaptive_questions,
        member_question_count: st.member_question_count,
      }});
      // Load round history when there is at least one completed round.
      if (st.current_round > 1 || st.status === "done" || st.status === "closed") {
        try {
          const rounds = await getRounds(pin!);
          dispatch({ type: "rounds_loaded", rounds });
        } catch { /* history optional */ }
      }
    } catch (e) { dispatch({ type: "error", message: String(e) }); }
  }, [pin, token]);

  // ADAPTIVE_SPEC §9.4: fetch this member's question for the CURRENT round.
  // A round-opened event dispatches round_opened first, then this runs — the
  // effect below fires whenever the round changes while the group collects.
  useEffect(() => {
    if (s.status !== "collecting" || !pid) return;
    let cancelled = false;
    getMyQuestion(pin!, pid)
      .then(mq => { if (!cancelled) dispatch({ type: "my_question", question: mq }); })
      .catch(() => { if (!cancelled) dispatch({ type: "my_question", question: null }); });
    return () => { cancelled = true; };
  }, [pin, pid, s.status, s.current_round]);

  useEffect(() => { fetchState(); }, [fetchState]);

  // Countdown timer: update remaining seconds every second while deadline is set
  // and group is still collecting.
  useEffect(() => {
    if (!s.deadline || s.status !== "collecting") { setRemaining(null); return; }
    const dl = new Date(s.deadline).getTime();
    const tick = () => {
      const diff = Math.max(0, Math.ceil((dl - Date.now()) / 1000));
      setRemaining(diff);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [s.deadline, s.status]);

  // Next-round cooldown countdown: tick down cooldown_remaining each second
  // so the button shows "開啟下一輪(Ns)" and enables at 0.
  useEffect(() => {
    if (s.cooldown_remaining === null || s.cooldown_remaining <= 0) return;
    const id = setTimeout(() => {
      dispatch({ type: "init", payload: { cooldown_remaining: s.cooldown_remaining! - 1 } });
    }, 1000);
    return () => clearTimeout(id);
  }, [s.cooldown_remaining]);

  useGroupSSE(pin!, {
    onPhase: (status, round) => dispatch({ type: "phase", status }),
    onProgress: (pc, sc) => dispatch({ type: "progress", participant_count: pc, submitted_count: sc }),
    onConsensus: (content, round) => dispatch({ type: "consensus", content }),
    onError: (message) => dispatch({ type: "error", message }),
    onDisconnect: () => fetchState(),
    onRound: (round, question, status, adaptive) => {
      if (status === "opened") {
        dispatch({ type: "round_opened", round, question });
        // ADAPTIVE_SPEC §9.4: adaptive groups fetch their own question for the
        // new round. The collecting-effect below would also refetch on the
        // current_round change; the explicit fetch here just makes it instant.
        if (adaptive && pid) {
          getMyQuestion(pin!, pid)
            .then(mq => dispatch({ type: "my_question", question: mq }))
            .catch(() => dispatch({ type: "my_question", question: null }));
        } else {
          dispatch({ type: "my_question", question: null });
        }
        // Refresh round history so the timeline shows the just-closed round.
        getRounds(pin!).then(rs => dispatch({ type: "rounds_loaded", rounds: rs })).catch(() => {});
      } else {
        dispatch({ type: "round_closed" });
      }
    },
  });

  async function send() {
    try {
      await submitResponse(pin!, pid, s.input);
      dispatch({ type: "submitted" });
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409) {
        // 409 = "Submissions closed" or "Already submitted" — sync state, not an error
        await fetchState();
      } else {
        // Network/server error on submit — show a transient message but stay in waiting
        dispatch({ type: "error", message: "送出失敗：" + String(e.message || e) });
      }
    }
  }
  async function start() {
    try { await startAnalysis(pin!, token); } catch (e) { dispatch({ type: "error", message: String(e) }); }
  }

  async function openNext() {
    try {
      // ADAPTIVE_SPEC §9.3: non-empty question = explicit override (the escape
      // hatch); empty = smart personalized questions. SSE drives the UI after.
      const q = s.nextQuestion.trim() || undefined;
      await openNextRound(pin!, token, q);
      dispatch({ type: "next_error", message: "" });
    } catch (e: any) {
      // Cooldown/race is a transient retry — keep the done screen up, show the
      // message inline next to the button (do NOT switch to the error phase).
      dispatch({ type: "next_error", message: String(e.message || e) });
    }
  }

  async function closeNow() {
    if (!window.confirm("結束討論後將無法再開新輪。確定嗎？")) return;
    try { await closeGroup(pin!, token); } catch (e: any) {
      // Transient: stay on the done screen, show inline.
      dispatch({ type: "next_error", message: "結束失敗：" + String(e.message || e) });
    }
  }

  async function leave() {
    if (s.is_creator) {
      if (!window.confirm("你是建立者，退出將解散群組，所有成員會收到通知。確定嗎？")) return;
    }
    try {
      await leaveGroup(pin!, pid, token || undefined);
      sessionStorage.removeItem(`pin:${pin}:pid`);
      sessionStorage.removeItem(`pin:${pin}:token`);
      nav("/");
    } catch (e: any) {
      dispatch({ type: "error", message: "退出失敗：" + String(e.message || e) });
    }
  }

  function copyPin() {
    navigator.clipboard.writeText(pin!);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function shareResult() {
    const url = `${window.location.origin}/g/${pin}`;
    navigator.clipboard.writeText(url);
    setCopiedLink(true);
    setTimeout(() => setCopiedLink(false), 2000);
  }

  if (s.phase === "joining") return <div>加入中…</div>;

  const progressPct = s.participant_count > 0
    ? Math.round((s.submitted_count / s.participant_count) * 100)
    : 0;

  return (
    <div>
      <h1 className="page-title">群組室</h1>

      {/* PIN card: only during collecting (waiting/submitted), not analyzing/done/error */}
      {(s.phase === "waiting" || s.phase === "submitted") && (
        <div className="card">
          <h3>群組 PIN 碼</h3>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            <code className="group-pin-code">{pin}</code>
            <button className="btn btn-outline" onClick={copyPin}>{copied ? "已複製 ✓" : "複製 PIN"}</button>
          </div>
          <p className="group-pin-hint">把這組 PIN 碼分享給要加入的人。</p>
        </div>
      )}

      {/* Common question hero: HIDDEN in adaptive groups — the personalized
          question card below is the single source (dual display confused
          members). Shown only for non-adaptive groups, the error phase, or
          as a fallback when the card data failed to load during collecting. */}
      {(!s.adaptive_questions || s.phase === "error" ||
        ((s.phase === "waiting" || s.phase === "submitted") && s.my_question === null)) && (
        <p className="group-hero-question">{s.question}</p>
      )}

      {/* Your personalized question card (adaptive groups, collecting phase).
          white-space: pre-wrap renders the two newline-joined questions. */}
      {(s.phase === "waiting" || s.phase === "submitted") && s.adaptive_questions &&
        s.my_question !== null && (
        <div className="card my-question-card">
          <h3>{s.my_question.is_personal ? "你的這一輪問題" : "本輪共同問題"}</h3>
          <p className="my-question-text">{s.my_question.question || s.question}</p>
          {s.my_question.is_personal && (
            <p className="muted my-question-hint" style={{ fontSize: "0.85rem", marginBottom: 0 }}>
              這是為你個人生成的問題，請勿與他人比較。
            </p>
          )}
        </div>
      )}

      {s.phase !== "done" && (
        <div className="progress-wrap">
          <div className="progress-bar">
            <div className="progress-bar-fill" style={{ width: `${progressPct}%` }} />
          </div>
          <p className="progress-label">已發送 {s.submitted_count} / 已加入 {s.participant_count}</p>
        </div>
      )}

      {/* Collapsible prior-round consensus during collecting (Gap #3) */}
      {(s.phase === "waiting" || s.phase === "submitted") && s.current_round > 1 &&
        s.rounds.filter(r => r.round_number < s.current_round && r.consensus).length > 0 && (
        <details className="round-history">
          <summary>前輪共識({s.rounds.filter(r => r.round_number < s.current_round && r.consensus).length} 輪)</summary>
          {s.rounds.filter(r => r.round_number < s.current_round && r.consensus).map(r => (
            <details key={r.round_number} className="round-history-round">
              <summary>第 {r.round_number} 輪共識</summary>
              <div className="consensus-body round-history-full">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" />,
                  }}
                >{r.consensus || ""}</ReactMarkdown>
              </div>
            </details>
          ))}
        </details>
      )}

      {remaining !== null && remaining > 0 && s.status === "collecting" && (
        <p className={`countdown ${remaining <= 10 ? "countdown-danger" : "countdown-accent"}`}>
          剩餘時間：{Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, "0")}
        </p>
      )}
      {remaining === 0 && s.status === "collecting" && (
        <p className="countdown countdown-danger">倒數結束，即將開始分析…</p>
      )}

      {s.phase === "waiting" && (
        <div className="card">
          <textarea placeholder="寫下你的想法（其他人看不到你的原文）" value={s.input}
            onChange={(e) => dispatch({ type: "input", value: e.target.value })} rows={4} />
          <button className="btn btn-primary btn-block" disabled={!s.input.trim()} onClick={send}>發送</button>
        </div>
      )}
      {s.phase === "submitted" && (
        <p className="submitted-msg"><span className="submitted-dot" />已送出，等待其他人…</p>
      )}
      {s.phase === "analyzing" && <AnalyzingLabel />}
      {s.phase === "done" && (
        <div className="consensus-card">
          {s.status === "closed" && (
            <p className="round-closed-banner">討論已結束</p>
          )}
          <div className="round-indicator">第 {s.current_round} 輪 / 最多 {s.max_rounds} 輪</div>
          <div className="consensus-body">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" />,
              }}
            >{s.consensus || ""}</ReactMarkdown>
          </div>

          {/* Round history timeline (collapsible) */}
          {s.rounds.length > 1 && (
            <details className="round-history">
              <summary>輪次歷史({s.rounds.length} 輪)</summary>
              {s.rounds.filter(r => r.consensus).map(r => (
                <details key={r.round_number} className="round-history-round">
                  <summary>第 {r.round_number} 輪</summary>
                  <div className="consensus-body round-history-full">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" />,
                      }}
                    >{r.consensus || ""}</ReactMarkdown>
                  </div>
                </details>
              ))}
            </details>
          )}

          {/* Next-round affordance (creator only, not closed, not maxed) */}
          {s.is_creator && s.status === "done" && s.current_round < s.max_rounds && (
            <div className="next-round-wrap">
              {!s.showNextForm ? (
                <button
                  className="btn btn-outline"
                  disabled={(s.cooldown_remaining ?? 0) > 0}
                  onClick={() => dispatch({ type: "toggle_next_form", open: true })}
                >
                  {(s.cooldown_remaining ?? 0) > 0
                    ? `開啟下一輪（${s.cooldown_remaining}s）`
                    : s.adaptive_questions && s.member_question_count != null
                      ? `開啟下一輪（已為 ${s.member_question_count} 位成員備妥個人化問題）`
                      : "開啟下一輪"}
                </button>
              ) : (
                <div className="next-round-form">
                  {s.adaptive_questions ? (
                    <>
                      <p style={{ margin: "0 0 0.5rem" }}>
                        <strong>智能個人化提問</strong>：下一輪將為每位成員生成個人化問題。
                      </p>
                      <details>
                        <summary>改用共同問題（自行輸入）</summary>
                        <textarea
                          placeholder="輸入共同問題，全員使用（覆寫個人化問題）"
                          value={s.nextQuestion}
                          onChange={(e) => dispatch({ type: "next_question", value: e.target.value })}
                          rows={2}
                        />
                      </details>
                    </>
                  ) : (
                    <textarea
                      placeholder="下一輪的問題(留空則自動從未解分歧生成)"
                      value={s.nextQuestion}
                      onChange={(e) => dispatch({ type: "next_question", value: e.target.value })}
                      rows={2}
                    />
                  )}
                  <div className="action-row">
                    <button className="btn btn-primary" onClick={openNext}>確認開啟</button>
                    <button className="btn btn-outline" onClick={() => dispatch({ type: "toggle_next_form", open: false })}>取消</button>
                  </div>
                  {s.nextError && <p className="next-error-msg">{s.nextError}</p>}
                </div>
              )}
            </div>
          )}

          {/* Non-creator wait hint (Gap #5): the creator may be deciding whether
              to open the next round; show this so non-creators don't leave early. */}
          {!s.is_creator && s.status === "done" && s.current_round < s.max_rounds && (
            <p className="creator-pending-hint">
              <span className="creator-pending-dot" />
              建立者正在考慮是否開啟下一輪…
            </p>
          )}

          {/* Close button (creator only, done, not already closed) */}
          {s.is_creator && s.status === "done" && (
            <button className="btn btn-outline" onClick={closeNow}>結束討論</button>
          )}
        </div>
      )}
      {s.phase === "error" && (
        <div className="card">
          <p className="error-msg">分析失敗：{s.error_msg}</p>
          {s.is_creator && <button className="btn btn-danger" onClick={start}>重試分析</button>}
          {!s.is_creator && <p className="muted">請聯絡建立者重試。</p>}
        </div>
      )}

      {/* Action row during collecting: start analysis (creator only) + leave */}
      {s.status === "collecting" && (
        <div className="action-row">
          {s.is_creator && <button className="btn btn-primary" onClick={start}>開始分析</button>}
          <button className="btn btn-danger" onClick={leave}>
            {s.is_creator ? "退出並解散群組" : "退出群組"}
          </button>
        </div>
      )}

      {/* Back to home + share result — shown after done (or error) */}
      {(s.phase === "done" || s.phase === "error") && (
        <div className="action-row">
          <button className="btn btn-outline" onClick={() => nav("/")}>回到首頁</button>
          {s.phase === "done" && (
            <button className="btn btn-outline" onClick={shareResult}>
              {copiedLink ? "連結已複製 ✓" : "分享結果"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
