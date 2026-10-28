import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth.jsx";
import useLoad from "../components/useLoad";
import { addDays, clock, isoToday, shortDay } from "../format";

export const SWAP_STATUS = {
  pending_colleague: "Waiting for the colleague",
  pending_manager: "Waiting for the manager",
  approved: "Approved",
  declined: "Declined by the colleague",
  rejected: "Rejected by the manager",
  cancelled: "Cancelled",
};
const tone = (s) => (s === "approved" ? "approved" : s.startsWith("pending") ? "pending" : "rejected");

export function shiftText(s) {
  return `${shortDay(s.date)}, ${clock(s.start_time)}–${clock(s.end_time)} at ${s.store.name}`;
}

// --- Managers, HR and the owner ------------------------------------------------------------------------------
function ManagerSwaps() {
  const [view, setView] = useState("pending_manager");
  const list = useLoad(`/swaps/?status=${view}`);
  const [error, setError] = useState("");
  const [rejecting, setRejecting] = useState(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(null);

  async function decide(swap, action) {
    setBusy(swap.id);
    setError("");
    try {
      await api(`/swaps/${swap.id}/${action}/`, { method: "POST", body: action === "reject" ? { reason } : undefined });
      setRejecting(null);
      setReason("");
      list.reload();
    } catch (e) {
      setError(e.message);
    }
    setBusy(null);
  }

  return (
    <>
      <h1>Shift swaps</h1>
      <div className="tabs" role="group" aria-label="Which swaps">
        <button type="button" className={view === "pending_manager" ? "quiet on" : "quiet"} aria-pressed={view === "pending_manager"} onClick={() => setView("pending_manager")}>Waiting for you</button>
        <button type="button" className={view === "all" ? "quiet on" : "quiet"} aria-pressed={view === "all"} onClick={() => setView("all")}>All swaps</button>
      </div>
      {error && <p className="problem" role="alert">{error}</p>}
      <section className="panel">
        {list.loading ? <p>Loading…</p> : list.error ? <p className="problem" role="alert">{list.error}</p> : list.data.length === 0 ? (
          <p className="empty">{view === "pending_manager" ? "Nothing is waiting for your decision." : "There are no swaps yet."}</p>
        ) : (
          <ul className="rows">
            {list.data.map((w) => (
              <li key={w.id}>
                <div>
                  <strong>{w.requester.name} {w.kind === "swap" ? "swaps with" : "hands over a shift to"} {w.colleague.name}</strong>
                  <span>{w.requester.name} gives: {shiftText(w.offered_shift)}</span>
                  {w.requested_shift && <span>{w.colleague.name} gives: {shiftText(w.requested_shift)}</span>}
                  {w.note && <em>“{w.note}”</em>}
                  {w.problems_if_approved.length > 0 && (
                    <div className="blockers">
                      <strong>This would break the rules:</strong>
                      <ul>{w.problems_if_approved.map((p, i) => <li key={i}>{p.message}</li>)}</ul>
                    </div>
                  )}
                  {w.decision_note && <em>Reason given: {w.decision_note}</em>}
                  {rejecting === w.id && (
                    <form className="reject" onSubmit={(e) => { e.preventDefault(); decide(w, "reject"); }}>
                      <label>
                        Reason (the staff will see it)
                        <input type="text" maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} />
                      </label>
                      <div className="actions">
                        <button type="submit" className="primary" disabled={busy === w.id}>Reject swap</button>
                        <button type="button" className="quiet" onClick={() => setRejecting(null)}>Not now</button>
                      </div>
                    </form>
                  )}
                </div>
                <div className="side">
                  <span className={`state ${tone(w.status)}`}>{SWAP_STATUS[w.status]}</span>
                  {w.status === "pending_manager" && rejecting !== w.id && (
                    <div className="actions">
                      <button type="button" className="primary" disabled={busy === w.id} onClick={() => decide(w, "approve")}>{busy === w.id ? "Working…" : "Approve"}</button>
                      <button type="button" className="quiet" disabled={busy === w.id} onClick={() => { setRejecting(w.id); setReason(""); }}>Reject</button>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

// --- Staff ---------------------------------------------------------------------------------------------------
function RequestSwap({ onSent }) {
  const today = isoToday();
  const shifts = useLoad(`/me/rota/?from=${today}&to=${addDays(today, 27)}`);
  const [shiftId, setShiftId] = useState("");
  const colleagues = useLoad(shiftId ? `/me/swaps/colleagues/?shift=${shiftId}` : null);
  const [colleague, setColleague] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/me/swaps/", { method: "POST", body: { offered_shift: Number(shiftId), colleague: Number(colleague), note } });
      setShiftId(""); setColleague(""); setNote("");
      onSent();
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h2>Ask someone to take a shift</h2>
      {shifts.data && shifts.data.length === 0 ? <p className="empty">You have no published shifts in the next four weeks.</p> : (
        <>
          <label>
            Which of your shifts
            <select required value={shiftId} onChange={(e) => { setShiftId(e.target.value); setColleague(""); }}>
              <option value="">Choose a shift</option>
              {(shifts.data || []).map((s) => <option key={s.id} value={s.id}>{shiftText(s)}</option>)}
            </select>
          </label>
          {shiftId && (
            <label>
              Who could take it
              <select required value={colleague} onChange={(e) => setColleague(e.target.value)}>
                <option value="">{colleagues.loading ? "Loading…" : "Choose a colleague"}</option>
                {(colleagues.data || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
          )}
          {colleagues.data && colleagues.data.length === 0 && <p className="empty">Nobody can take this shift without breaking a rule.</p>}
          <label>
            Note (optional)
            <input type="text" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          {error && <p className="problem" role="alert">{error}</p>}
          <button type="submit" className="primary" disabled={busy || !colleague}>{busy ? "Sending…" : "Send request"}</button>
        </>
      )}
    </form>
  );
}

function MySwaps() {
  const list = useLoad("/me/swaps/");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(null);
  async function act(swap, action) {
    setBusy(swap.id);
    setError("");
    try {
      await api(`/me/swaps/${swap.id}/${action}/`, { method: "POST", body: action === "accept" ? {} : undefined });
      list.reload();
    } catch (e) {
      setError(e.message);
    }
    setBusy(null);
  }
  if (list.status === 404) return <><h1>Shift swaps</h1><p className="panel">This account is not set up as a member of staff.</p></>;
  return (
    <>
      <h1>Shift swaps</h1>
      <RequestSwap onSent={list.reload} />
      <section className="panel">
        <h2>Your requests</h2>
        {error && <p className="problem" role="alert">{error}</p>}
        {list.loading ? <p>Loading…</p> : list.error ? <p className="problem" role="alert">{list.error}</p> : list.data.length === 0 ? (
          <p className="empty">You have no swap requests.</p>
        ) : (
          <ul className="rows">
            {list.data.map((w) => (
              <li key={w.id}>
                <div>
                  <strong>{w.direction === "outgoing" ? `You asked ${w.other_person.name} to take` : `${w.other_person.name} asks you to take`}</strong>
                  <span>{shiftText(w.offered_shift)}</span>
                  {w.requested_shift && <span>In return: {shiftText(w.requested_shift)}</span>}
                  {w.note && <em>“{w.note}”</em>}
                  {w.decision_note && <em>Reason given: {w.decision_note}</em>}
                </div>
                <div className="side">
                  <span className={`state ${tone(w.status)}`}>{SWAP_STATUS[w.status]}</span>
                  {w.status === "pending_colleague" && w.direction === "incoming" && (
                    <div className="actions">
                      <button type="button" className="primary" disabled={busy === w.id} onClick={() => act(w, "accept")}>Take the shift</button>
                      <button type="button" className="quiet" disabled={busy === w.id} onClick={() => act(w, "decline")}>Decline</button>
                    </div>
                  )}
                  {["pending_colleague", "pending_manager"].includes(w.status) && w.direction === "outgoing" && (
                    <button type="button" className="quiet" disabled={busy === w.id} onClick={() => act(w, "cancel")}>Cancel request</button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

export default function Swaps() {
  const { user } = useAuth();
  return user.role === "employee" ? <MySwaps /> : <ManagerSwaps />;
}
