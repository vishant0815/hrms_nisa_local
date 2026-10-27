import { useState } from "react";
import { api } from "../api";
import useLoad from "../components/useLoad";
import { isoToday, shortDay } from "../format";

export const KINDS = [
  ["annual", "Annual leave"],
  ["unpaid", "Unpaid leave"],
  ["sick", "Sick leave"],
  ["other", "Other (for example family or compassionate)"],
];
export const STATUS = { pending: "Waiting for a decision", approved: "Approved", rejected: "Rejected", cancelled: "Cancelled" };

function RequestForm({ onSaved }) {
  const [form, setForm] = useState({ kind: "annual", start_date: isoToday(), end_date: "", half_day: "", note: "" });
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setDone("");
    try {
      const body = { ...form, end_date: form.end_date || null };
      await api("/me/leave/", { method: "POST", body });
      setDone("Request sent. Your manager will see it and you will get an email with the decision.");
      setForm({ ...form, end_date: "", note: "", half_day: "" });
      onSaved();
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h2>Request leave</h2>
      <label>
        Type of leave
        <select value={form.kind} onChange={set("kind")}>
          {KINDS.map(([value, name]) => <option key={value} value={value}>{name}</option>)}
        </select>
      </label>
      <div className="two">
        <label>
          First day
          <input type="date" required value={form.start_date} onChange={set("start_date")} />
        </label>
        <label>
          Last day (leave empty for one day)
          <input type="date" value={form.end_date} min={form.start_date} onChange={set("end_date")} />
        </label>
      </div>
      <label>
        Half day
        <select value={form.half_day} onChange={set("half_day")}>
          <option value="">Whole day(s)</option>
          <option value="am">Morning only</option>
          <option value="pm">Afternoon only</option>
        </select>
      </label>
      <label>
        Note for your manager (optional)
        <input type="text" maxLength={300} value={form.note} onChange={set("note")} />
      </label>
      {error && <p className="problem" role="alert">{error}</p>}
      {done && <p className="note" role="status">{done}</p>}
      <button type="submit" className="primary" disabled={busy}>{busy ? "Sending…" : "Send request"}</button>
    </form>
  );
}

export default function Leave() {
  const balance = useLoad("/me/leave/balance/");
  const list = useLoad("/me/leave/");
  const [error, setError] = useState("");

  function refresh() {
    balance.reload();
    list.reload();
  }
  async function cancel(id) {
    setError("");
    try {
      await api(`/me/leave/${id}/cancel/`, { method: "POST" });
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  if (balance.status === 404 || list.status === 404) {
    return (
      <>
        <h1>Leave</h1>
        <p className="panel">This account is not set up as a member of staff, so there is no leave to request.</p>
      </>
    );
  }

  return (
    <>
      <h1>Leave</h1>
      <section className="panel" aria-labelledby="bal">
        <h2 id="bal">Your annual leave</h2>
        {balance.loading ? <p>Loading…</p> : balance.error ? <p className="problem" role="alert">{balance.error}</p> : (
          <p className="figure">
            <strong>{Number(balance.data.remaining)}</strong> days left of {Number(balance.data.allowance)}
            <small>
              {" "}
              ({shortDay(balance.data.year_start)} to {shortDay(balance.data.year_end)})
            </small>
          </p>
        )}
      </section>
      <RequestForm onSaved={refresh} />
      <section className="panel" aria-labelledby="mine">
        <h2 id="mine">Your requests</h2>
        {error && <p className="problem" role="alert">{error}</p>}
        {list.loading ? <p>Loading…</p> : list.error ? <p className="problem" role="alert">{list.error}</p> : list.data.length === 0 ? (
          <p className="empty">You have not asked for any leave yet.</p>
        ) : (
          <ul className="rows">
            {list.data.map((l) => (
              <li key={l.id}>
                <div>
                  <strong>{KINDS.find(([k]) => k === l.kind)?.[1] || l.kind}</strong>
                  <span>
                    {shortDay(l.start_date)}
                    {l.end_date !== l.start_date && ` to ${shortDay(l.end_date)}`}
                    {l.half_day === "am" && ", morning"}
                    {l.half_day === "pm" && ", afternoon"}
                    {" · "}
                    {Number(l.days_requested)} {Number(l.days_requested) === 1 ? "day" : "days"}
                  </span>
                  {l.decision_note && <em>{l.decision_note}</em>}
                </div>
                <div className="side">
                  <span className={`state ${l.status}`}>{STATUS[l.status] || l.status}</span>
                  {(l.status === "pending" || l.status === "approved") && (
                    <button type="button" className="quiet" onClick={() => cancel(l.id)}>Cancel request</button>
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
