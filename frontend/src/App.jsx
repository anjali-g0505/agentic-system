import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const ROLE_LABEL = { cardholder: "Cardholder", issuer_ops: "Issuer operations", risk_supervisor: "Risk supervisor" };
const ROLE_BLURB = {
  cardholder: "View transactions, check eligibility, lodge a dispute with the bank",
  issuer_ops: "Check eligibility and raise chargebacks",
  risk_supervisor: "Review fraud referrals and withdraw cases",
};
const SUGGESTIONS = {
  cardholder: [
    "Why did my transaction 100000000003 fail?",
    "I paid 100000000001 but the shop says it never received the money",
    "I don't recognise transaction 100000000006",
    "Show me transaction 100000000009",
  ],
  issuer_ops: [
    "Merchant not credited on 100000000001 — check eligibility and raise a chargeback",
    "Can we raise a chargeback on 100000000002 for merchant not credited?",
    "Raise a chargeback on 100000000007 for duplicate processing",
    "Under RuPay rules, how is a deferred authorised transaction settled?",
  ],
  risk_supervisor: [
    "List all open chargeback cases and fraud referrals",
    "Withdraw case CB-SEED-000001 — merchant has confirmed the credit",
    "Cardholder says 100000000006 is fraud. What do we know?",
    "What is the chargeback time limit for RuPay transactions?",
  ],
};
const ACTION_VERB = {
  raise_chargeback: "Raise chargeback",
  lodge_dispute: "Lodge dispute",
  withdraw_case: "Withdraw case",
  refer_to_risk: "Refer to risk",
};
const TOOL_LABEL = {
  search_circulars: "Searched circulars",
  get_transaction_status: "Looked up transaction",
  check_chargeback_eligibility: "Checked eligibility",
  list_cases: "Listed cases",
  raise_chargeback: "Raise chargeback",
  lodge_dispute: "Lodge dispute",
  withdraw_case: "Withdraw case",
  refer_to_risk: "Refer to risk",
};

function tone(status) {
  if (!status) return "info";
  if (["OK", "ELIGIBLE", "RAISED", "LODGED", "WITHDRAWN", "REFERRED"].includes(status)) return "success";
  if (["REFER_TO_RISK", "ALREADY_RAISED", "ALREADY_LODGED", "ALREADY_WITHDRAWN", "ALREADY_REFERRED", "DEFERRED",
       "NOT_COVERED", "REJECTED_BY_USER"].includes(status)) return "attention";
  if (["DENIED", "NOT_ELIGIBLE", "REFUSED", "BLOCKED", "NOT_FOUND", "ERROR", "NOT_VERIFIED", "INVALID"].includes(status))
    return "error";
  return "info";
}

function caseTone(status) {
  return { RAISED: "attention", WITHDRAWN: "muted", LODGED: "info", OPEN: "attention", CLOSED: "muted" }[status] || "info";
}

/* Minimal, safe markdown: escapes everything, then bold, inline code, bullets, citation markers. */
function renderMarkdown(text) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[(\d{1,2})\]/g, '<sup class="ref">$1</sup>');
  const out = [];
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (bullet) {
      list = list || [];
      list.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    if (list) {
      out.push(`<ul>${list.join("")}</ul>`);
      list = null;
    }
    if (/^#{1,4}\s+/.test(line)) out.push(`<p class="md-h">${inline(line.replace(/^#{1,4}\s+/, ""))}</p>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return out.join("");
}

function shortSource(name) {
  if (/ru_?pay_consolidated/i.test(name)) return "RuPay Consolidated Circular";
  if (/upi_consolidated/i.test(name)) return "UPI Consolidated Circular";
  if (/product_booklet/i.test(name)) return "RuPay Product Booklet";
  return name.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ").slice(0, 38);
}

function CitationChips({ citations }) {
  const [open, setOpen] = useState(null);
  if (!citations?.length) return null;
  return (
    <div className="citations">
      <div className="citations-label">Sources</div>
      <div className="chips">
        {citations.map((c, i) => {
          const label =
            c.kind === "circular"
              ? `${shortSource(c.source_filename)} · p.${c.page_number}${c.clause ? ` · cl. ${c.clause}` : ""}`
              : c.kind === "rule"
              ? `Rules table · ${c.claim_type}`
              : `Response codes · ${c.code}`;
          return (
            <button key={i} className={`chip chip-${c.kind} ${open === i ? "chip-open" : ""}`}
                    onClick={() => setOpen(open === i ? null : i)}>
              <span className="chip-kind">{c.kind === "circular" ? (c.ref ? `[${c.ref}]` : "Circular") : "Data"}</span>
              {label}
              {c.provisional && <span className="chip-flag">Provisional</span>}
            </button>
          );
        })}
      </div>
      {open !== null && (
        <div className="citation-detail">
          {(() => {
            const c = citations[open];
            if (c.kind === "circular")
              return (
                <>
                  <div><span>Document</span>{c.source_filename}</div>
                  <div><span>Circular</span>{c.circular_no || "—"}</div>
                  <div><span>Page</span>{c.page_number}</div>
                  <div><span>Clause</span>{c.clause || "—"}</div>
                  <div><span>Rail</span>{c.product}</div>
                </>
              );
            return (
              <>
                <div><span>File</span>{c.source_filename}</div>
                {c.claim_type && <div><span>Rule</span>{c.claim_type}</div>}
                {c.code && <div><span>Code</span>{c.code}</div>}
                <div><span>Circular</span>{c.source_circular || "Not yet verified against a circular"}</div>
                {c.provisional && (
                  <p className="detail-warn">Provisional figure: computed from the rules table, not backed by a circular.</p>
                )}
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}

function ToolTrace({ events }) {
  if (!events?.length) return null;
  return (
    <div className="trace">
      {events.map((e, i) => (
        <span key={i} className={`trace-item tone-${tone(e.status)}`}>
          {TOOL_LABEL[e.tool] || e.tool}
          {e.status && <b>{e.status.replaceAll("_", " ").toLowerCase()}</b>}
        </span>
      ))}
    </div>
  );
}

function ConfirmModal({ pending, busy, onDecide }) {
  const { tool, preview, requested_by } = pending;
  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
      <div className="modal">
        <div className="modal-head">
          <div className="modal-eyebrow">Human confirmation required</div>
          <h2 id="confirm-title">{preview?.title || ACTION_VERB[tool]}</h2>
          <p className="modal-sub">The assistant has prepared this action. Nothing happens until you confirm.</p>
        </div>
        <dl className="facts">
          {Object.entries(preview?.facts || {}).map(([k, v]) => (
            <div key={k} className="fact">
              <dt>{k}</dt>
              <dd>{String(v ?? "—")}</dd>
            </div>
          ))}
        </dl>
        {preview?.provisional && (
          <div className="notice notice-attention">
            <strong>Provisional rule figures.</strong> The time limit and evidence list come from the rules table
            and have not yet been verified against a circular.
          </div>
        )}
        <div className="notice notice-irreversible">
          <strong>This action is not reversible.</strong> {preview?.consequence}
        </div>
        <div className="modal-foot">
          <span className="requested">Requested by {requested_by}</span>
          <div className="modal-actions">
            <button className="btn btn-ghost" disabled={busy} onClick={() => onDecide("reject")}>Cancel</button>
            <button className="btn btn-primary" disabled={busy} onClick={() => onDecide("approve")}>
              {busy ? "Working…" : `Confirm · ${ACTION_VERB[tool] || "Proceed"}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function CasesPanel({ cases, onRefresh, loading }) {
  const rows = [
    ...(cases?.chargeback_cases || []).map((c) => ({ id: c.case_id, kind: "Chargeback", status: c.status, rrn: c.rrn,
      meta: `${c.claim_type.replaceAll("_", " ")} · ₹${c.amount}`, by: c.withdrawn_by ? `withdrawn by ${c.withdrawn_by}` : `raised by ${c.raised_by}` })),
    ...(cases?.disputes || []).map((d) => ({ id: d.dispute_id, kind: "Dispute", status: d.status, rrn: d.rrn,
      meta: d.claim_type.replaceAll("_", " "), by: d.cardholder_id })),
    ...(cases?.fraud_referrals || []).map((f) => ({ id: f.referral_id, kind: "Fraud referral", status: f.status, rrn: f.rrn,
      meta: "awaiting risk review", by: `by ${f.referred_by}` })),
  ];
  return (
    <aside className="panel">
      <div className="panel-head">
        <h3>Cases</h3>
        <button className="link" onClick={onRefresh} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
      </div>
      {rows.length === 0 && <p className="empty">No cases visible to you yet.</p>}
      <ul className="case-list">
        {rows.map((r) => (
          <li key={r.id} className="case">
            <div className="case-top">
              <span className="case-id">{r.id}</span>
              <span className={`badge tone-${caseTone(r.status)}`}>{r.status}</span>
            </div>
            <div className="case-kind">{r.kind} · RRN {r.rrn}</div>
            <div className="case-meta">{r.meta} · {r.by}</div>
          </li>
        ))}
      </ul>
    </aside>
  );
}

function Login({ onLogin }) {
  const [users, setUsers] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  useEffect(() => {
    api("/auth/users").then(setUsers).catch((e) => setError(e.message));
  }, []);
  const pick = async (id) => {
    setBusy(id);
    setError(null);
    try {
      onLogin(await api("/auth/login", { method: "POST", body: { user_id: id } }));
    } catch (e) {
      setError(e.message);
      setBusy(null);
    }
  };
  return (
    <main className="login">
      <h1>Sign in to the chargeback desk</h1>
      <p className="lede">Choose a demo identity. What you can see and do is enforced by the server for that role.</p>
      {error && <div className="banner-error">{error}</div>}
      <div className="user-grid">
        {users.map((u) => (
          <button key={u.cardholder_id} className="user-card" onClick={() => pick(u.cardholder_id)} disabled={!!busy}>
            <span className={`role-dot role-${u.role}`} />
            <span className="user-name">{u.name}</span>
            <span className="user-id">{u.cardholder_id} · {ROLE_LABEL[u.role]}</span>
            <span className="user-blurb">{ROLE_BLURB[u.role]}</span>
            {busy === u.cardholder_id && <span className="user-busy">Signing in…</span>}
          </button>
        ))}
      </div>
    </main>
  );
}

function Header({ user, onSwitch }) {
  return (
    <header className="header">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true" />
        <div>
          <div className="brand-title">Chargeback Desk</div>
          <div className="brand-sub">RuPay Credit Card on UPI · Issuer workspace</div>
        </div>
      </div>
      <div className="header-right">
        {user && (
          <div className="who">
            <div className="who-name">{user.name}</div>
            <div className="who-role">
              <span className={`role-dot role-${user.role}`} />{ROLE_LABEL[user.role]} · {user.user_id}
            </div>
          </div>
        )}
        {user && <button className="btn btn-ghost btn-sm" onClick={onSwitch}>Switch user</button>}
        <img src="/npci-logo.png" alt="NPCI" className="logo" />
      </div>
    </header>
  );
}

export default function App() {
  const [session, setSession] = useState(() => {
    const s = sessionStorage.getItem("cb-session");
    return s ? JSON.parse(s) : null;
  });
  const [messages, setMessages] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);
  const [cases, setCases] = useState(null);
  const [casesLoading, setCasesLoading] = useState(false);
  const scroller = useRef(null);

  const token = session?.access_token;

  const signOut = () => {
    sessionStorage.removeItem("cb-session");
    setSession(null);
    setMessages([]);
    setConversationId(null);
    setPending(null);
    setCases(null);
    setError(null);
  };

  const loadCases = useCallback(async () => {
    if (!token) return;
    setCasesLoading(true);
    try {
      setCases(await api("/cases", { token }));
    } catch (e) {
      if (e.status === 401) signOut();
    } finally {
      setCasesLoading(false);
    }
  }, [token]);

  useEffect(() => { loadCases(); }, [loadCases]);
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  const handle = (res) => {
    setConversationId(res.conversation_id);
    if (res.status === "awaiting_confirmation") {
      setMessages((m) => [...m, { role: "system", tool_events: res.tool_events, citations: res.citations,
        text: `Prepared: ${ACTION_VERB[res.confirmation.tool] || res.confirmation.tool}. Waiting for your confirmation.` }]);
      setPending(res.confirmation);
    } else {
      setMessages((m) => [...m, { role: "assistant", text: res.answer || "(no answer)", citations: res.citations,
        tool_events: res.tool_events }]);
    }
  };

  const send = async (text) => {
    const msg = (text ?? input).trim();
    if (!msg || busy || pending) return;
    setInput("");
    setError(null);
    setMessages((m) => [...m, { role: "user", text: msg }]);
    setBusy(true);
    try {
      handle(await api("/chat", { method: "POST", token, body: { message: msg, conversation_id: conversationId } }));
    } catch (e) {
      if (e.status === 401) return signOut();
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const decide = async (decision) => {
    setBusy(true);
    setError(null);
    try {
      const res = await api("/chat/confirm", { method: "POST", token, body: { conversation_id: conversationId, decision } });
      setPending(null);
      setMessages((m) => [...m, { role: "decision", text: decision === "approve" ? "You confirmed the action." : "You cancelled the action." }]);
      handle(res);
      loadCases();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!session) {
    return (
      <div className="app">
        <Header />
        <Login onLogin={(s) => { sessionStorage.setItem("cb-session", JSON.stringify(s)); setSession(s); }} />
      </div>
    );
  }

  return (
    <div className="app">
      <Header user={session.user} onSwitch={signOut} />
      <div className="workspace">
        <section className="chat">
          <div className="thread" ref={scroller}>
            {messages.length === 0 && (
              <div className="welcome">
                <h2>How can the desk help, {session.user.name.split(" ")[0]}?</h2>
                <p>Answers about rules are grounded in NPCI circulars and cited. Eligibility is computed from the rules
                  table, never guessed. Any action with a side effect asks for your confirmation first.</p>
                <div className="suggestions">
                  {SUGGESTIONS[session.user.role].map((s) => (
                    <button key={s} className="suggestion" onClick={() => send(s)}>{s}</button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`msg msg-${m.role}`}>
                {m.role === "assistant" ? (
                  <div className="bubble">
                    <ToolTrace events={m.tool_events} />
                    <div className="md" dangerouslySetInnerHTML={{ __html: renderMarkdown(m.text) }} />
                    <CitationChips citations={m.citations} />
                  </div>
                ) : m.role === "system" ? (
                  <div className="bubble bubble-system">
                    <ToolTrace events={m.tool_events} />
                    <p>{m.text}</p>
                  </div>
                ) : (
                  <div className="bubble">{m.text}</div>
                )}
              </div>
            ))}
            {busy && !pending && (
              <div className="msg msg-assistant">
                <div className="bubble bubble-loading">
                  <span className="dots"><i /><i /><i /></span>
                  Checking circulars, transaction data and rules…
                </div>
              </div>
            )}
          </div>
          {error && <div className="banner-error">{error}</div>}
          <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
            <input value={input} onChange={(e) => setInput(e.target.value)} disabled={busy || !!pending}
                   placeholder={pending ? "Confirm or cancel the pending action first" : "Ask about a transaction (RRN/ARN) or a RuPay/UPI rule"} />
            <button className="btn btn-primary" disabled={busy || !!pending || !input.trim()}>Send</button>
          </form>
          <p className="footnote">The assistant cannot move money, reverse payments or promise refunds. Card numbers are shown as last four digits only.</p>
        </section>
        <CasesPanel cases={cases} onRefresh={loadCases} loading={casesLoading} />
      </div>
      {pending && <ConfirmModal pending={pending} busy={busy} onDecide={decide} />}
    </div>
  );
}
