import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { saveBlob } from "../download";
import useLoad from "../components/useLoad";
import { VISA_STATUS, statusTone } from "../components/RightToWork.jsx";
import { dateText } from "../format";

function VisaList({ list, empty }) {
  if (list.loading && !list.data) return <p>Loading…</p>;
  if (list.error) return <p className="problem" role="alert">{list.error}</p>;
  if (list.data.length === 0) return <p className="empty">{empty}</p>;
  return (
    <ul className="rows">
      {list.data.map((v) => (
        <li key={v.id}>
          <div>
            <strong><Link to={`/staff/${v.employee.id}`}>{v.employee.name}</Link></strong>
            <span>{v.visa_type.name}{v.expiry_date ? `, expires ${dateText(v.expiry_date)}` : ""}</span>
          </div>
          <div className="side"><span className={`state ${statusTone(v.status)}`}>{VISA_STATUS[v.status]}</span></div>
        </li>
      ))}
    </ul>
  );
}

function Verify({ letter, onDone, onCancel }) {
  const s = letter.suggested || {};
  const [from, setFrom] = useState(s.covers_from || "");
  const [to, setTo] = useState(s.covers_to || "");
  const [vacations, setVacations] = useState((s.vacations || []).map((v) => ({ start_date: v.start_date, end_date: v.end_date, label: v.label || "" })));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const setV = (i, k) => (e) => setVacations(vacations.map((v, j) => (j === i ? { ...v, [k]: e.target.value } : v)));

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(`/term-letters/${letter.id}/verify/`, { method: "POST", body: { covers_from: from, covers_to: to, vacations } });
      onDone("Verified. The dates are now used on the rota.");
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }
  return (
    <form className="shift-form" onSubmit={submit}>
      <h3>Confirm the dates from the letter</h3>
      {s.note && <p className="notice">{s.note}</p>}
      <div className="two">
        <label>Term dates covered: from<input type="date" required value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>to<input type="date" required value={to} onChange={(e) => setTo(e.target.value)} /></label>
      </div>
      <p className="hint">Add every vacation the letter shows. Outside vacations the student's weekly hours limit applies.</p>
      {vacations.map((v, i) => (
        <div className="three" key={i}>
          <label>Vacation starts<input type="date" required value={v.start_date} onChange={setV(i, "start_date")} /></label>
          <label>Ends<input type="date" required value={v.end_date} onChange={setV(i, "end_date")} /></label>
          <label>Name (optional)<input maxLength={100} value={v.label} onChange={setV(i, "label")} /></label>
        </div>
      ))}
      <button type="button" className="quiet" onClick={() => setVacations([...vacations, { start_date: "", end_date: "", label: "" }])}>Add a vacation</button>
      {error && <p className="problem" role="alert">{error}</p>}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>Confirm these dates</button>
        <button type="button" className="quiet" onClick={onCancel}>Not now</button>
      </div>
    </form>
  );
}

function Letter({ letter, onChanged }) {
  const [mode, setMode] = useState(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function download() {
    setBusy(true);
    setError("");
    try {
      const blob = await api(`/term-letters/${letter.id}/file/`, { blob: true });
      saveBlob(blob, letter.original_filename || `term-letter-${letter.id}`);
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }
  async function reject(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(`/term-letters/${letter.id}/reject/`, { method: "POST", body: { reason } });
      onChanged("Rejected. The student can see your reason.");
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }
  return (
    <li>
      <div>
        <strong><Link to={`/staff/${letter.employee.id}`}>{letter.employee.name}</Link></strong>
        <span>{letter.institution || "Institution not given"} · {letter.original_filename}</span>
        <small>Uploaded {dateText(letter.created_at.slice(0, 10))}</small>
        {mode === "verify" && <Verify letter={letter} onCancel={() => setMode(null)} onDone={onChanged} />}
        {mode === "reject" && (
          <form className="reject" onSubmit={reject}>
            <label>Why (the student will see it)<input required maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
            <div className="actions">
              <button type="submit" className="primary" disabled={busy}>Reject this letter</button>
              <button type="button" className="quiet" onClick={() => setMode(null)}>Not now</button>
            </div>
          </form>
        )}
        {error && <p className="problem" role="alert">{error}</p>}
      </div>
      <div className="side">
        {!mode && (
          <div className="actions">
            <button type="button" className="quiet" disabled={busy} onClick={download}>Open the letter</button>
            <button type="button" className="primary" onClick={() => setMode("verify")}>Confirm dates</button>
            <button type="button" className="quiet" onClick={() => { setMode("reject"); setReason(""); }}>Reject</button>
          </div>
        )}
      </div>
    </li>
  );
}

export default function RightToWorkDesk() {
  const [days, setDays] = useState("60");
  const expiring = useLoad(`/visas/expiring/?days=${days}`);
  const missing = useLoad("/visas/missing-term-letters/");
  const letters = useLoad("/term-letters/?status=pending");
  const [flash, setFlash] = useState("");
  function changed(message) {
    setFlash(message);
    letters.reload();
    missing.reload();
  }
  return (
    <>
      <h1>Right to work</h1>
      {flash && <p className="note" role="status">{flash}</p>}
      <section className="panel">
        <h2>Term letters waiting for you</h2>
        {letters.loading && !letters.data ? <p>Loading…</p> : letters.error ? <p className="problem" role="alert">{letters.error}</p> : letters.data.length === 0 ? (
          <p className="empty">No letters are waiting.</p>
        ) : (
          <ul className="rows">{letters.data.map((l) => <Letter key={l.id} letter={l} onChanged={changed} />)}</ul>
        )}
      </section>
      <section className="panel">
        <h2>Expired, or expiring soon</h2>
        <label>
          Show those ending within
          <select value={days} onChange={(e) => setDays(e.target.value)}>
            {[["30", "30 days"], ["60", "60 days"], ["90", "90 days"], ["180", "6 months"]].map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
        </label>
        <VisaList list={expiring} empty="Nobody's status is expired or about to expire." />
      </section>
      <section className="panel">
        <h2>Students with no verified term letter</h2>
        <p className="hint">Until a letter is verified, a student's weekly hours limit applies all year. Ask them to upload their letter.</p>
        <VisaList list={missing} empty="Every student has a verified letter." />
      </section>
    </>
  );
}
