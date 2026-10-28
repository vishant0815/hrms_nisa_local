import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import useLoad from "../components/useLoad";
import { addDays, isoToday, minutesText, shortDay, ukDate, ukInstant, ukTime } from "../format";

const VIEWS = [
  ["pending", "Needs a decision"],
  ["flagged", "Flagged"],
  ["all", "Everything"],
];
export const REVIEW_TEXT = { not_required: "", pending: "Waiting for a manager", approved: "Approved", rejected: "Rejected" };
const SOURCE = { device: "Clock-in on the website", fingerprint: "Fingerprint device", manual: "Added by a manager" };

function EditTimes({ record, onDone, onCancel }) {
  const [inDay, setInDay] = useState(ukDate(record.clock_in_at));
  const [inTime, setInTime] = useState(ukTime(record.clock_in_at));
  const [outDay, setOutDay] = useState(record.clock_out_at ? ukDate(record.clock_out_at) : "");
  const [outTime, setOutTime] = useState(record.clock_out_at ? ukTime(record.clock_out_at) : "");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const body = { reason, clock_in_at: ukInstant(inDay, inTime) };
    if (outDay && outTime) body.clock_out_at = ukInstant(outDay, outTime);
    try {
      await api(`/attendance/${record.id}/`, { method: "PATCH", body });
      onDone("Times changed. The change and your reason are kept on the record.");
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <form className="shift-form" onSubmit={submit}>
      <h3>Change the times</h3>
      <div className="two">
        <label>Clocked in on<input type="date" required value={inDay} onChange={(e) => setInDay(e.target.value)} /></label>
        <label>At<input type="time" required value={inTime} onChange={(e) => setInTime(e.target.value)} /></label>
      </div>
      {record.clock_out_at && (
        <div className="two">
          <label>Clocked out on<input type="date" required value={outDay} onChange={(e) => setOutDay(e.target.value)} /></label>
          <label>At<input type="time" required value={outTime} onChange={(e) => setOutTime(e.target.value)} /></label>
        </div>
      )}
      <label>
        Reason (kept on the record)
        <input type="text" required maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      {error && <p className="problem" role="alert">{error}</p>}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>{busy ? "Saving…" : "Save the change"}</button>
        <button type="button" className="quiet" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function History({ id }) {
  const edits = useLoad(`/attendance/${id}/edits/`);
  if (edits.loading) return <p>Loading…</p>;
  if (edits.error) return <p className="problem" role="alert">{edits.error}</p>;
  if (!edits.data.length) return <p className="empty">Nobody has changed this record.</p>;
  return (
    <ul className="plain">
      {edits.data.map((x) => (
        <li key={x.id}>
          <span>{x.edited_by || "Someone"}: {x.reason}</span>
          <small>{ukDate(x.created_at)} {ukTime(x.created_at)}</small>
        </li>
      ))}
    </ul>
  );
}

function Record({ record: r, onChanged, canPayExtra }) {
  const [mode, setMode] = useState(null); // "reject" | "edit" | "history"
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function act(path, body, message) {
    setBusy(true);
    setError("");
    try {
      await api(path, { method: "POST", body });
      setMode(null);
      setNote("");
      onChanged(message);
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  const open = r.clock_out_at === null;
  return (
    <li>
      <div>
        <strong>{r.employee.name}</strong>
        <span>
          {shortDay(ukDate(r.clock_in_at))}, {ukTime(r.clock_in_at)}–{open ? "still clocked in" : ukTime(r.clock_out_at)}
          {!open && <> ({minutesText(r.worked_minutes)})</>} at {r.store.name}
        </span>
        <small>{SOURCE[r.source] || r.source}{r.late_minutes > 0 && `, ${r.late_minutes} minutes late`}</small>
        {r.flags.length > 0 && (
          <ul className="flags">
            {r.flags.map((f) => <li key={f.code}>{f.message}</li>)}
          </ul>
        )}
        {r.review_note && <em>{r.review_status === "rejected" ? "Rejected" : "Note"}: {r.review_note}</em>}
        {mode === "reject" && (
          <form className="reject" onSubmit={(e) => { e.preventDefault(); act(`/attendance/${r.id}/reject/`, { note }, "Rejected. The employee can see your reason."); }}>
            <label>
              Why (the employee will see it)
              <input type="text" required maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
            <div className="actions">
              <button type="submit" className="primary" disabled={busy}>Reject this entry</button>
              <button type="button" className="quiet" onClick={() => setMode(null)}>Not now</button>
            </div>
          </form>
        )}
        {mode === "edit" && <EditTimes record={r} onCancel={() => setMode(null)} onDone={(m) => { setMode(null); onChanged(m); }} />}
        {mode === "history" && <History id={r.id} />}
        {error && <p className="problem" role="alert">{error}</p>}
      </div>
      <div className="side">
        {REVIEW_TEXT[r.review_status] && <span className={`state ${r.review_status === "pending" ? "pending" : r.review_status}`}>{REVIEW_TEXT[r.review_status]}</span>}
        <div className="actions">
          {r.review_status === "pending" && !open && mode !== "reject" && (
            <>
              <button type="button" className="primary" disabled={busy} onClick={() => act(`/attendance/${r.id}/approve/`, {}, "Approved.")}>Approve</button>
              <button type="button" className="quiet" disabled={busy} onClick={() => setMode("reject")}>Reject</button>
            </>
          )}
          {mode !== "edit" && <button type="button" className="quiet" onClick={() => setMode("edit")}>Change times</button>}
          {canPayExtra && r.shift && !open && (
            <button type="button" className="quiet" disabled={busy} title="Use this when they stayed past their rota shift and you agree it should be paid" onClick={() => act(`/attendance/${r.id}/approve-extra-hours/`, { note: "" }, "The time beyond the rota will be paid.")}>Pay time beyond the rota</button>
          )}
          <button type="button" className="quiet" onClick={() => setMode(mode === "history" ? null : "history")}>{mode === "history" ? "Hide history" : "History"}</button>
        </div>
      </div>
    </li>
  );
}

function MissedClockIn({ stores, people, onDone, onCancel }) {
  const [f, setF] = useState({ employee: "", store: stores[0] ? String(stores[0].id) : "", date: isoToday(), start: "09:00", end: "17:00", reason: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/attendance/manual/", {
        method: "POST",
        body: { employee: Number(f.employee), store: Number(f.store), clock_in_at: ukInstant(f.date, f.start), clock_out_at: ukInstant(f.date, f.end), reason: f.reason },
      });
      onDone("Added. It is marked as added by a manager.");
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h2>Add a missed clock-in</h2>
      <p className="hint">For a forgotten clock-in or clock-out. Use it only when you know the person worked these hours.</p>
      <div className="two">
        <label>
          Who
          <select required value={f.employee} onChange={set("employee")}>
            <option value="">Choose someone</option>
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        {stores.length > 1 ? (
          <label>
            Shop
            <select required value={f.store} onChange={set("store")}>
              {stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        ) : <div />}
      </div>
      <div className="three">
        <label>Day<input type="date" required max={isoToday()} value={f.date} onChange={set("date")} /></label>
        <label>Clocked in<input type="time" required value={f.start} onChange={set("start")} /></label>
        <label>Clocked out<input type="time" required value={f.end} onChange={set("end")} /></label>
      </div>
      <label>Reason (kept on the record)<input type="text" required maxLength={300} value={f.reason} onChange={set("reason")} /></label>
      {error && <p className="problem" role="alert">{error}</p>}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>{busy ? "Adding…" : "Add it"}</button>
        <button type="button" className="quiet" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

export default function Attendance({ canPayExtra = true }) {
  const stores = useLoad("/stores/");
  const people = useLoad("/employees/");
  const [view, setView] = useState("pending");
  const [store, setStore] = useState("");
  const [from, setFrom] = useState(addDays(isoToday(), -27));
  const [to, setTo] = useState(isoToday());
  const [flash, setFlash] = useState("");
  const [adding, setAdding] = useState(false);

  const activeStores = useMemo(() => (stores.data || []).filter((s) => s.is_active), [stores.data]);
  const roster = useMemo(
    () => (people.data || []).filter((p) => p.is_current).map((p) => ({ id: p.id, name: [p.user.first_name, p.user.last_name].filter(Boolean).join(" ") || p.user.email })),
    [people.data],
  );
  useEffect(() => { setFlash(""); }, [view, store, from, to]);

  const query = `/attendance/?review=${view}&start=${from}&end=${to}${store ? `&store=${store}` : ""}`;
  const list = useLoad(query);

  function changed(message) {
    setFlash(message);
    list.reload();
  }

  return (
    <>
      <h1>Attendance</h1>
      <div className="controls">
        <div className="tabs" role="group" aria-label="Which entries">
          {VIEWS.map(([key, label]) => (
            <button key={key} type="button" className={view === key ? "quiet on" : "quiet"} aria-pressed={view === key} onClick={() => setView(key)}>{label}</button>
          ))}
        </div>
        {activeStores.length > 1 && (
          <label>
            Shop
            <select value={store} onChange={(e) => setStore(e.target.value)}>
              <option value="">All my shops</option>
              {activeStores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        )}
        <label>From<input type="date" value={from} max={to} onChange={(e) => e.target.value && setFrom(e.target.value)} /></label>
        <label>To<input type="date" value={to} min={from} onChange={(e) => e.target.value && setTo(e.target.value)} /></label>
        <button type="button" className="quiet" onClick={() => setAdding(!adding)}>{adding ? "Close" : "Add a missed clock-in"}</button>
      </div>
      {flash && <p className="note" role="status">{flash}</p>}
      {adding && <MissedClockIn stores={activeStores} people={roster} onCancel={() => setAdding(false)} onDone={(m) => { setAdding(false); changed(m); }} />}
      <section className="panel">
        {list.loading && !list.data ? <p>Loading…</p> : list.error ? <p className="problem" role="alert">{list.error}</p> : list.data.length === 0 ? (
          <p className="empty">
            {view === "pending" ? "Nothing is waiting for a decision in these dates." : view === "flagged" ? "No entries are flagged in these dates." : "There are no entries in these dates."}
          </p>
        ) : (
          <ul className="rows">
            {list.data.map((r) => <Record key={r.id} record={r} onChanged={changed} canPayExtra={canPayExtra} />)}
          </ul>
        )}
      </section>
    </>
  );
}
