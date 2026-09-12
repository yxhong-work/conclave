import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createGroup } from "../lib/api";

export default function Create() {
  const [q, setQ] = useState("");
  const [nick, setNick] = useState("");
  const [expected, setExpected] = useState("");
  const [timeout, setTimeout_] = useState("");
  const [maxRounds, setMaxRounds] = useState("");
  const [adaptive, setAdaptive] = useState(false);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  async function submit() {
    setErr("");
    if (!q.trim() || !nick.trim()) { setErr("問題與暱稱必填"); return; }
    try {
      const ec = expected ? parseInt(expected) : undefined;
      const ts = timeout ? parseInt(timeout) : undefined;
      const mr = maxRounds ? parseInt(maxRounds) : undefined;
      const r = await createGroup(q.trim(), nick.trim(), ec, ts, mr, adaptive);
      // Use sessionStorage (per-tab) so each browser tab is an independent user.
      sessionStorage.setItem(`pin:${r.pin}:pid`, r.participant_id);
      sessionStorage.setItem(`pin:${r.pin}:token`, r.creator_token);
      nav(`/g/${r.pin}`);
    } catch (e: any) { setErr(String(e.message || e)); }
  }

  return (
    <div>
      <h1 className="page-title">建立群組</h1>
      <div className="card">
        <label htmlFor="q">討論問題（必填）</label>
        <textarea id="q" placeholder="例：我們今晚去哪裡吃飯？" value={q}
          onChange={(e) => setQ(e.target.value)} rows={3} />
        <label htmlFor="nick">你的暱稱（必填）</label>
        <input id="nick" value={nick} onChange={(e) => setNick(e.target.value)} />
        <hr className="create-optional-divider" />
        <label htmlFor="expected">預期回覆數（選填，達標即觸發分析）</label>
        <input id="expected" type="number" min={1} value={expected}
          onChange={(e) => setExpected(e.target.value)} placeholder="例：3" />
        <label htmlFor="timeout">倒數秒數（選填，時間到即觸發）</label>
        <input id="timeout" type="number" min={1} max={86400} value={timeout}
          onChange={(e) => setTimeout_(e.target.value)} placeholder="例：300" />
        <label htmlFor="maxRounds">最多輪數（選填，預設 3，1–10）</label>
        <input id="maxRounds" type="number" min={1} max={10} value={maxRounds}
          onChange={(e) => setMaxRounds(e.target.value)} placeholder="例：3" />
        <label htmlFor="adaptive" style={{ display: "flex", gap: "0.5rem", alignItems: "flex-start", cursor: "pointer" }}>
          <input id="adaptive" type="checkbox" checked={adaptive}
            onChange={(e) => setAdaptive(e.target.checked)} style={{ marginTop: "0.25rem" }} />
          <span>
            <strong>適應性個人化提問</strong>
            <br />
            <span className="muted" style={{ fontSize: "0.85rem" }}>
              為每位成員生成不同的後續問題（依其立場探詢彈性界線，加速收斂）。建立後不可關閉。
            </span>
          </span>
        </label>
        <button className="btn btn-primary create-submit-btn" onClick={submit}>建立</button>
        {err && <p style={{ color: "var(--danger)", marginTop: "0.6rem", marginBottom: 0 }}>{err}</p>}
      </div>
    </div>
  );
}
