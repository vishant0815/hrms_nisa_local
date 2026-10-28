import { useState } from "react";
import { api } from "../api";
import useLoad from "../components/useLoad";
import { addDays, isoToday, shortDay } from "../format";
import { KINDS, STATUS } from "./Leave.jsx";

const KIND = Object.fromEntries(KINDS.map(([k, v]) => [k, v.replace(/ \(.*\)/, "")]));
const HALF = { am: "morning only", pm: "afternoon only" };
const VIEWS = [
  ["pending", "Waiting for a decision"],
  ["approved", "Approved"],
  ["all", "Everything"],
];

export function dates(r) {
  const from = shortDay(r.start_date);
  const text = r.end_date && r.end_date !== r.start_date ? `${from} to ${shortDay(r.end_date)}` : from;
  return r.half_day ? `${text} (${HALF[r.half_day]})` : text;
}
export const dayCount = (n) => `${Number(n)} ${Number(n) === 1 ? "day" : "days"}`;

function Request({ r, onChanged }) {
  const [mode, setMode] = useState(null); // "reject" | "cancel" | "charge"
  const [reason, setReason] = useState("");
  const [charged, setCharged] = useState(String(Number(r.days_requested)));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const today = isoToday();
  const annual = r.kind === "annual";

  async function act(action, body, message) {
    setBusy(true);
    setError("");
    try {
      await api(`/leave/${r.id}/${action}/`, { method: "POST", body });
      setMode(null);
      onChanged(message);
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  return (
    <li>
      <div>
        <strong>{r.employee.name}</strong>
        <span>{KIND[r.kind]}: {dates(r)} ({dayCount(r.days_requested)})</span>
        {r.note && <em>“{r.note}”</em>}
        {r.status === "pending" && annual && r.annual_leave_left !== null && (
          <small>Annual leave left for this person: {dayCount(r.annual_leave_left)}</small>
        )}
        {r.status === "approved" && annual && <small>Charged to their allowance: {dayCount(r.days_charged)}</small>}
        {r.decision_note && <em>Reason given: {r.decision_note}</em>}
        {r.reviewed_by && r.status !== "pending" && <small>Decided by {r.reviewed_by}</small>}
        {mode === "charge" && (
          <form className="reject" onSubmit={(e) => { e.preventDefault(); act("approve", { days_charged: charged }, "Approved."); }}>
            <label>
              Days to take from their annual leave
              <input type="number" min="0.5" max={r.days_requested} step="0.5" required value={charged} onChange={(e) => setCharged(e.target.value)} />
            </label>
            <p className="hint">Use fewer than they asked for if part of the time falls on days they would not have worked.</p>
            <div className="actions">
              <button type="submit" className="primary" disabled={busy}>Approve</button>
              <button type="button" className="quiet" onClick={() => setMode(null)}>Not now</button>
            </div>
          </form>
        )}
        {(mode === "reject" || mode === "cancel") && (
          <form className="reject" onSubmit={(e) => { e.preventDefault(); act(mode, { reason }, mode === "reject" ? "Rejected. The employee can see your reason." : "Cancelled. The days go back to their allowance."); }}>
            <label>
              Reason (the employee will see it)
              <input type="text" required maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
            <div className="actions">
              <button type="submit" className="primary" disabled={busy}>{mode === "reject" ? "Reject this request" : "Cancel this leave"}</button>
              <button type="button" className="quiet" onClick={() => setMode(null)}>Not now</button>
            </div>
          </form>
        )}
        {error && <p className="problem" role="alert">{error}</p>}
      </div>
      <div className="side">
        <span className={`state ${r.status === "pending" ? "pending" : r.status}`}>{STATUS[r.status]}</span>
        {!mode && r.status === "pending" && (
          <div className="actions">
            <button type="button" className="primary" disabled={busy} onClick={() => (annual ? setMode("charge") : act("approve", {}, "Approved."))}>Approve</button>
            <button type="button" className="quiet" disabled={busy} onClick={() => { setMode("reject"); setReason(""); }}>Reject</button>
          </div>
        )}
        {!mode && r.status === "approved" && r.end_date >= today && (
          <div className="actions">
            <button type="button" className="quiet" onClick={() => { setMode("cancel"); setReason(""); }}>Cancel this leave</button>
          </div>
        )}
      </div>
    </li>
  );
}

function WhoIsOff() {
  const today = isoToday();
  const off = useLoad(`/leave/calendar/?start=${today}&end=${addDays(today, 13)}`);
  return (
    <section className="panel">
      <h2>Who is off in the next 2 weeks</h2>
      {off.loading ? <p>Loading…</p> : off.error ? <p className="problem" role="alert">{off.error}</p> : off.data.length === 0 ? (
        <p className="empty">Nobody has approved leave in the next two weeks.</p>
      ) : (
        <ul className="plain">
          {off.data.map((e) => (
            <li key={e.id}><span>{e.employee.name}</span><span>{dates(e)}</span></li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function LeaveRequests() {
  const [view, setView] = useState("pending");
  const list = useLoad(`/leave/?status=${view}`);
  const [flash, setFlash] = useState("");
  const [changes, setChanges] = useState(0); // a new key makes the "who is off" list read again
  function changed(message) {
    setFlash(message);
    setChanges((n) => n + 1);
    list.reload();
  }
  return (
    <>
      <h1>Leave requests</h1>
      <div className="tabs" role="group" aria-label="Which requests">
        {VIEWS.map(([key, label]) => (
          <button key={key} type="button" className={view === key ? "quiet on" : "quiet"} aria-pressed={view === key} onClick={() => { setView(key); setFlash(""); }}>{label}</button>
        ))}
      </div>
      {flash && <p className="note" role="status">{flash}</p>}
      <section className="panel">
        {list.loading && !list.data ? <p>Loading…</p> : list.error ? <p className="problem" role="alert">{list.error}</p> : list.data.length === 0 ? (
          <p className="empty">{view === "pending" ? "No leave requests are waiting for you." : "There are no requests to show."}</p>
        ) : (
          <ul className="rows">
            {list.data.map((r) => <Request key={r.id} r={r} onChanged={changed} />)}
          </ul>
        )}
      </section>
      <WhoIsOff key={changes} />
    </>
  );
}
