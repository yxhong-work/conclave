import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTypewriter } from "../hooks/useTypewriter";

const SUBTITLE = "於密室之中，見眾人之光。";

export default function Home() {
  const [pin, setPin] = useState("");
  const [nick, setNick] = useState("");
  const [err, setErr] = useState("");
  const nav = useNavigate();
  const { displayed, done } = useTypewriter(SUBTITLE);

  async function join() {
    setErr("");
    if (!pin.trim() || !nick.trim()) { setErr("PIN 與暱稱皆必填"); return; }
    try {
      const r = await fetch(`/api/groups/${pin.trim()}/join`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nickname: nick.trim() }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setErr(body.detail || body.error || "加入失敗");
        return;
      }
      const body = await r.json();
      sessionStorage.setItem(`pin:${pin.trim()}:pid`, body.participant_id);
      sessionStorage.removeItem(`pin:${pin.trim()}:token`);
      nav(`/g/${pin.trim()}`);
    } catch { setErr("連線失敗"); }
  }

  return (
    <div>
      <div className="home-hero">
        <div className="home-hero-logo">
          {/* Logo: a round table — Conclave (chamber/conference) */}
          <svg width="40" height="40" viewBox="0 0 32 32" fill="none" stroke="var(--fg)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flexShrink: 0 }}>
            {/* The round table */}
            <ellipse cx="16" cy="20" rx="11" ry="3.5" />
            {/* Table top surface line */}
            <path d="M5 20 Q16 15 27 20" />
            {/* Seats around the table (three participants + one empty = open) */}
            <circle cx="16" cy="6" r="2.5" />
            <circle cx="5" cy="12" r="2.5" />
            <circle cx="27" cy="12" r="2.5" />
            {/* The shared consensus — a small light above the table */}
            <circle cx="16" cy="13" r="1.2" fill="var(--accent)" stroke="none" />
          </svg>
          <h1 className="home-hero-wordmark">Conclave</h1>
        </div>
        <p className="home-hero-subtitle">
          {displayed}
          {!done && <span className="cursor">▎</span>}
        </p>
      </div>

      <button className="btn btn-primary home-create-btn" onClick={() => nav("/create")}>
        建立群組
      </button>

      <div className="card home-join-card">
        <h3>加入群組</h3>
        <input placeholder="PIN 碼" value={pin} onChange={(e) => setPin(e.target.value)} />
        <input placeholder="你的暱稱" value={nick} onChange={(e) => setNick(e.target.value)} />
        <button className="btn btn-primary btn-block" onClick={join}>加入群組</button>
        {err && <p style={{ color: "var(--danger)", marginTop: "0.6rem", marginBottom: 0 }}>{err}</p>}
      </div>
    </div>
  );
}
