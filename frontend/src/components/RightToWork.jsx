import { useState } from "react";
import { api } from "../api";
import { dateText, isoToday } from "../format";
import useLoad from "./useLoad";

export const VISA_STATUS = {
  valid: "Valid",
  expiring_soon: "Expiring soon",
  expired: "Expired",
  revoked: "Revoked",
  pending_check: "Right-to-work check needed",
  check_failed: "Check failed",
};
export const statusTone = (s) => (s === "valid" ? "approved" : s === "expiring_soon" || s === "pending_check" ? "pending" : "rejected");
export const CHECK_TYPES = [
  ["online", "Home Office online check (share code)"],
  ["manual", "Manual document check"],
  ["idsp", "Identity service provider (British/Irish passport)"],
];
export const OUTCOMES = [["pass", "Pass"], ["fail", "Fail"], ["follow_up", "Follow-up needed"]];
const outcomeText = Object.fromEntries(OUTCOMES);
const checkText = Object.fromEntries(CHECK_TYPES);

function Form({ title, hint, children, onSave, button, done, onCancel }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSave();
      done();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }
  return (
    <form className="shift-form" onSubmit={submit}>
      <h3>{title}</h3>
      {hint && <p className="hint">{hint}</p>}
      {children}
      {error && <p className="problem" role="alert">{error}</p>}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>{busy ? "Saving…" : button}</button>
        <button type="button" className="quiet" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

// Fields that depend on the visa type: only what the rules for that type need are asked for.
function VisaFields({ type, f, set }) {
  return (
    <>
      <div className="two">
        <label>Starts on (optional)<input type="date" value={f.start_date} onChange={set("start_date")} /></label>
        {type.requires_expiry && <label>Expires on<input type="date" required value={f.expiry_date} onChange={set("expiry_date")} /></label>}
      </div>
      {type.requires_sponsorship && <label>Sponsor reference<input required maxLength={50} value={f.sponsor_reference} onChange={set("sponsor_reference")} /></label>}
      {type.requires_manual_decision && (
        <label>
          Has HR decided this person may work?
          <select required value={f.manual_work_allowed} onChange={set("manual_work_allowed")}>
            <option value="">Not decided yet (they will be blocked)</option>
            <option value="yes">Yes, they may work</option>
            <option value="no">No, they may not work</option>
          </select>
        </label>
      )}
      <div className="two">
        <label>Weekly hours limit if different from the usual (optional)<input type="number" min="1" max="168" value={f.weekly_hours_cap_override} onChange={set("weekly_hours_cap_override")} /></label>
        <label>Conditions on their work (optional)<input value={f.work_conditions} onChange={set("work_conditions")} /></label>
      </div>
    </>
  );
}

const blank = { start_date: "", expiry_date: "", sponsor_reference: "", manual_work_allowed: "", weekly_hours_cap_override: "", work_conditions: "" };
const fromVisa = (v) => ({ start_date: v.start_date || "", expiry_date: v.expiry_date || "", sponsor_reference: v.sponsor_reference || "", manual_work_allowed: v.manual_work_allowed === true ? "yes" : v.manual_work_allowed === false ? "no" : "", weekly_hours_cap_override: v.weekly_hours_cap_override ? String(v.weekly_hours_cap_override) : "", work_conditions: v.work_conditions || "" });
function body(type, f) {
  return {
    start_date: f.start_date || null,
    expiry_date: type.requires_expiry ? f.expiry_date || null : null,
    sponsor_reference: type.requires_sponsorship ? f.sponsor_reference : "",
    manual_work_allowed: type.requires_manual_decision ? (f.manual_work_allowed === "" ? null : f.manual_work_allowed === "yes") : null,
    weekly_hours_cap_override: f.weekly_hours_cap_override ? Number(f.weekly_hours_cap_override) : null,
    work_conditions: f.work_conditions,
  };
}

function AddStatus({ id, types, onDone, onCancel }) {
  const [typeId, setTypeId] = useState("");
  const [f, setF] = useState(blank);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const type = types.find((t) => String(t.id) === typeId);
  return (
    <Form title="Record a new status" hint="Use this when someone's status changes or is renewed. The old status is kept as history. A new right-to-work check is needed afterwards." button="Save this status" onCancel={onCancel} done={onDone} onSave={() => api(`/employees/${id}/visas/`, { method: "POST", body: { visa_type: Number(typeId), ...body(type, f) } })}>
      <label>
        Type of status
        <select required value={typeId} onChange={(e) => { setTypeId(e.target.value); setF(blank); }}>
          <option value="">Choose one</option>
          {types.filter((t) => t.is_active).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </label>
      {type && <VisaFields type={type} f={f} set={set} />}
    </Form>
  );
}

function EditStatus({ visa, types, onDone, onCancel }) {
  const type = types.find((t) => t.id === visa.visa_type.id) || { requires_expiry: true, requires_sponsorship: true, requires_manual_decision: true };
  const [f, setF] = useState(fromVisa(visa));
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <Form title="Correct these details" hint="To change the type of status, record a new status instead." button="Save the correction" onCancel={onCancel} done={onDone} onSave={() => api(`/visas/${visa.id}/`, { method: "PATCH", body: body(type, f) })}>
      <VisaFields type={type} f={f} set={set} />
    </Form>
  );
}

function Revoke({ visa, onDone, onCancel }) {
  const [reason, setReason] = useState("");
  return (
    <Form title="End this status" hint="Use this if the status was withdrawn or was recorded by mistake. The person will be blocked from the rota." button="End this status" onCancel={onCancel} done={onDone} onSave={() => api(`/visas/${visa.id}/revoke/`, { method: "POST", body: { reason } })}>
      <label>Reason<input maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
    </Form>
  );
}

// Everything about one person's right to work, for owner and HR only.
export default function RightToWork({ id }) {
  const visas = useLoad(`/employees/${id}/visas/`);
  const checks = useLoad(`/employees/${id}/rtw-checks/`);
  const decision = useLoad(`/employees/${id}/can-work/`);
  const types = useLoad("/visa-types/");
  const [mode, setMode] = useState(null);
  const [flash, setFlash] = useState("");
  const current = (visas.data || []).find((v) => v.is_current);
  const history = (visas.data || []).filter((v) => !v.is_current);
  const reload = () => { visas.reload(); checks.reload(); decision.reload(); };
  const finished = (message) => () => { setMode(null); setFlash(message); reload(); };

  if (visas.error) return <section className="panel"><h2>Right to work</h2><p className="problem" role="alert">{visas.error}</p></section>;
  if (!visas.data || !types.data) return <section className="panel"><h2>Right to work</h2><p>Loading…</p></section>;

  return (
    <section className="panel">
      <h2>Right to work</h2>
      <p className="hint">Only the owner and HR can see this. Managers are only told whether someone can work.</p>
      {flash && <p className="note" role="status">{flash}</p>}
      {!current ? (
        <p className="problem">No immigration status is recorded, so this person cannot be put on the rota.</p>
      ) : (
        <>
          <p>
            <strong>{current.visa_type.name}</strong>{" "}
            <span className={`state ${statusTone(current.status)}`}>{VISA_STATUS[current.status]}</span>
          </p>
          <ul className="plain">
            {current.start_date && <li><span>Starts</span><span>{dateText(current.start_date)}</span></li>}
            {current.expiry_date && <li><span>Expires</span><span>{dateText(current.expiry_date)}</span></li>}
            {current.sponsor_reference && <li><span>Sponsor reference</span><span>{current.sponsor_reference}</span></li>}
            {current.weekly_hours_cap_override && <li><span>Weekly hours limit</span><span>{current.weekly_hours_cap_override}</span></li>}
            {current.work_conditions && <li><span>Conditions</span><span>{current.work_conditions}</span></li>}
            <li><span>Last check</span><span>{current.latest_check ? `${outcomeText[current.latest_check.outcome]} on ${dateText(current.latest_check.checked_on)}${current.latest_check.next_check_due ? `, check again by ${dateText(current.latest_check.next_check_due)}` : ""}` : "None yet"}</span></li>
          </ul>
          {decision.data && (
            <div className={decision.data.allowed ? "confirm" : "blockers"}>
              <strong>{decision.data.allowed ? "Can work today" : "Cannot be rostered today"}</strong>
              {decision.data.weekly_hours_limit && <span> · limited to {decision.data.weekly_hours_limit} hours a week</span>}
              {!decision.data.allowed && <ul>{decision.data.reasons.map((r) => <li key={r.code}>{r.text}</li>)}</ul>}
              {decision.data.warnings.length > 0 && <ul>{decision.data.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
            </div>
          )}
        </>
      )}
      {mode === "check" && current && <RecordCheckFor id={id} visa={current} onDone={finished("Check recorded.")} onCancel={() => setMode(null)} />}
      {mode === "add" && <AddStatus id={id} types={types.data} onDone={finished("Status recorded. Now record a right-to-work check for it.")} onCancel={() => setMode(null)} />}
      {mode === "edit" && current && <EditStatus visa={current} types={types.data} onDone={finished("Details corrected.")} onCancel={() => setMode(null)} />}
      {mode === "revoke" && current && <Revoke visa={current} onDone={finished("Status ended.")} onCancel={() => setMode(null)} />}
      {!mode && (
        <div className="actions">
          {current && <button type="button" className="primary" onClick={() => setMode("check")}>Record a check</button>}
          <button type="button" className="quiet" onClick={() => setMode("add")}>{current ? "Record a new status" : "Record their status"}</button>
          {current && <button type="button" className="quiet" onClick={() => setMode("edit")}>Correct details</button>}
          {current && <button type="button" className="quiet danger" onClick={() => setMode("revoke")}>End this status</button>}
        </div>
      )}
      <h3>Checks on record</h3>
      {checks.data && checks.data.length === 0 ? <p className="empty">No checks have been recorded.</p> : (
        <ul className="plain">
          {(checks.data || []).map((c) => (
            <li key={c.id}>
              <span>{dateText(c.checked_on)}: {outcomeText[c.outcome]} ({checkText[c.check_type]}){c.evidence_reference ? `. Evidence: ${c.evidence_reference}` : ""}</span>
              <small>{c.next_check_due ? `Again by ${dateText(c.next_check_due)}` : ""}</small>
            </li>
          ))}
        </ul>
      )}
      {history.length > 0 && (
        <>
          <h3>Earlier statuses</h3>
          <ul className="plain">
            {history.map((v) => <li key={v.id}><span>{v.visa_type.name}{v.expiry_date ? `, to ${dateText(v.expiry_date)}` : ""}</span><small>{v.revoked_on ? `Ended ${dateText(v.revoked_on)}` : "Replaced"}</small></li>)}
          </ul>
        </>
      )}
    </section>
  );
}

function RecordCheckFor({ id, visa, onDone, onCancel }) {
  const [f, setF] = useState({ check_type: "online", outcome: "pass", checked_on: isoToday(), next_check_due: visa.expiry_date && visa.expiry_date > isoToday() ? visa.expiry_date : "", evidence_reference: "", notes: "" });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <Form title="Record a right-to-work check" hint="Checks are never edited or deleted. If one was wrong, record a new one. Do not type document numbers here; say where the evidence is kept." button="Save this check" onCancel={onCancel} done={onDone} onSave={() => api(`/employees/${id}/rtw-checks/`, { method: "POST", body: { ...f, visa: visa.id, next_check_due: f.next_check_due || null } })}>
      <div className="two">
        <label>How was it checked<select value={f.check_type} onChange={set("check_type")}>{CHECK_TYPES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
        <label>Result<select value={f.outcome} onChange={set("outcome")}>{OUTCOMES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
      </div>
      <div className="two">
        <label>Checked on<input type="date" required max={isoToday()} value={f.checked_on} onChange={set("checked_on")} /></label>
        <label>Check again by (if it has an end date)<input type="date" min={f.checked_on} value={f.next_check_due} onChange={set("next_check_due")} /></label>
      </div>
      <label>Where the evidence is kept<input maxLength={200} placeholder="For example: HR folder, shelf 2" value={f.evidence_reference} onChange={set("evidence_reference")} /></label>
      <label>Notes (optional)<input value={f.notes} onChange={set("notes")} /></label>
    </Form>
  );
}
