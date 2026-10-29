import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth.jsx";
import useLoad from "../components/useLoad";
import { isoToday, pounds, shortDay, toDate } from "../format";

const dayMonth = (iso) => toDate(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long" });
import RightToWork from "../components/RightToWork.jsx";
import { personName } from "./Staff.jsx";

// A little form that saves one thing and says so. `fields` render inside; `onSave` does the call.
function Section({ title, hint, children, onSave, button = "Save", done = "Saved." }) {
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await onSave();
      setMessage(done);
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  }
  return (
    <form className="panel form" onSubmit={submit}>
      <h2>{title}</h2>
      {hint && <p className="hint">{hint}</p>}
      {children}
      {error && <p className="problem" role="alert">{error}</p>}
      {message && <p className="note" role="status">{message}</p>}
      <button type="submit" className="primary" disabled={busy}>{busy ? "Saving…" : button}</button>
    </form>
  );
}

function Basics({ p, admin, onSaved }) {
  const [phone, setPhone] = useState(p.phone);
  const [title, setTitle] = useState(p.job_title);
  const [end, setEnd] = useState(p.end_date || "");
  if (!admin) {
    return (
      <section className="panel">
        <h2>Details</h2>
        <ul className="plain">
          <li><span>Email</span><span>{p.user.email}</span></li>
          <li><span>Phone</span><span>{p.phone || "Not given"}</span></li>
          <li><span>Job title</span><span>{p.job_title || "Not given"}</span></li>
          <li><span>Started</span><span>{shortDay(p.start_date)}</span></li>
        </ul>
      </section>
    );
  }
  return (
    <Section title="Details" hint={`Started ${shortDay(p.start_date)}. Set a last day when someone leaves; they stay on record and are kept off new rotas.`} onSave={async () => { await api(`/employees/${p.id}/`, { method: "PATCH", body: { phone, job_title: title, end_date: end || null } }); onSaved(); }}>
      <div className="two">
        <label>Job title<input maxLength={100} value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <label>Phone<input maxLength={30} value={phone} onChange={(e) => setPhone(e.target.value)} /></label>
      </div>
      <label>Last working day (leave empty if they are staying)<input type="date" value={end} min={p.start_date} onChange={(e) => setEnd(e.target.value)} /></label>
    </Section>
  );
}

function HomeShop({ p, stores, onSaved }) {
  const [store, setStore] = useState("");
  const [date, setDate] = useState(isoToday());
  const others = stores.filter((s) => s.is_active && (!p.home_store || s.id !== p.home_store.id));
  return (
    <Section title="Home shop" hint={`Based at ${p.home_store ? p.home_store.name : "no shop"} now. A move keeps the history; it cannot start in the past.`} button="Move them" done="Moved. Their history is kept." onSave={async () => { await api(`/employees/${p.id}/transfer/`, { method: "POST", body: { store: Number(store), effective_date: date } }); setStore(""); onSaved(); }}>
      <div className="two">
        <label>
          Move to
          <select required value={store} onChange={(e) => setStore(e.target.value)}>
            <option value="">Choose a shop</option>
            {others.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <label>From<input type="date" required min={isoToday()} value={date} onChange={(e) => setDate(e.target.value)} /></label>
      </div>
    </Section>
  );
}

function Allowance({ id }) {
  const bal = useLoad(`/employees/${id}/leave-allowance/`);
  const [days, setDays] = useState("");
  const shown = days !== "" ? days : bal.data ? String(Number(bal.data.allowance)) : "";
  if (bal.error) return <section className="panel"><h2>Annual leave</h2><p className="problem" role="alert">{bal.error}</p></section>;
  return (
    <Section title="Annual leave" hint={bal.data ? `Leave year ${dayMonth(bal.data.year_start)} to ${dayMonth(bal.data.year_end)}. Taken: ${Number(bal.data.used)} days. Waiting for a decision: ${Number(bal.data.waiting)} days. Left: ${Number(bal.data.remaining)} days.` : "Loading…"} onSave={async () => { await api(`/employees/${id}/leave-allowance/`, { method: "PUT", body: { days: shown } }); setDays(""); bal.reload(); }}>
      <label>Days for this leave year (half days allowed)<input type="number" min="0" max="366" step="0.5" required value={shown} onChange={(e) => setDays(e.target.value)} /></label>
    </Section>
  );
}

function PayRates({ id }) {
  const rates = useLoad(`/employees/${id}/pay-rates/`);
  const [rate, setRate] = useState("");
  const [from, setFrom] = useState(isoToday());
  return (
    <>
      <Section title="Hourly rate" hint="A new rate applies from the date you choose; earlier pay stays as it was. The rate cannot be below the minimum wage for their age on that date, so save their date of birth under tax and bank details first." button="Add this rate" done="Rate added." onSave={async () => { await api(`/employees/${id}/pay-rates/`, { method: "POST", body: { hourly_rate: rate, effective_from: from } }); setRate(""); rates.reload(); }}>
        <div className="two">
          <label>Pounds an hour<input type="number" min="0" step="0.01" required value={rate} onChange={(e) => setRate(e.target.value)} /></label>
          <label>Starting on<input type="date" required value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        </div>
        {rates.data && (rates.data.length === 0 ? <p className="problem">No rate is set yet, so this person cannot be paid.</p> : (
          <ul className="plain">
            {rates.data.map((r) => <li key={r.id}><span>From {shortDay(r.effective_from)}</span><strong>{pounds(r.hourly_rate)} an hour</strong></li>)}
          </ul>
        ))}
      </Section>
    </>
  );
}

const LOANS = [["", "None"], ["1", "Plan 1"], ["2", "Plan 2"], ["4", "Plan 4"], ["5", "Plan 5"]];

function PayDetails({ id }) {
  const d = useLoad(`/employees/${id}/pay-details/`);
  const [edit, setEdit] = useState({});
  const [dirty, setDirty] = useState(new Set());
  if (d.error) return <section className="panel"><h2>Tax and bank details</h2><p className="problem" role="alert">{d.error}</p></section>;
  if (!d.data) return <section className="panel"><h2>Tax and bank details</h2><p>Loading…</p></section>;
  const cur = d.data;
  const val = (k) => (k in edit ? edit[k] : cur[k] ?? "");
  const set = (k) => (e) => {
    const v = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setEdit({ ...edit, [k]: v });
    setDirty(new Set(dirty).add(k));
  };

  async function save() {
    const body = {};
    for (const k of dirty) body[k] = edit[k];
    if (dirty.has("prior_pay") || dirty.has("prior_tax")) {
      body.prior_pay = val("prior_pay") === "" ? null : val("prior_pay");
      body.prior_tax = val("prior_tax") === "" ? null : val("prior_tax");
    }
    for (const k of ["apprentice_from", "date_of_birth", "typical_day_hours"]) if (k in body && body[k] === "") body[k] = null;
    if (!Object.keys(body).length) throw new Error("Nothing was changed.");
    await api(`/employees/${id}/pay-details/`, { method: "PUT", body });
    setEdit({});
    setDirty(new Set());
    d.reload();
  }

  return (
    <Section title="Tax and bank details" hint="Needed to work out tax and National Insurance and to pay people. Numbers are hidden once saved: type a new one to replace it, or leave the box empty to keep it." onSave={save}>
      <h3>Tax</h3>
      <div className="three">
        <label>National Insurance number<input autoComplete="off" placeholder={cur.ni_number || "AB123456C"} value={"ni_number" in edit ? edit.ni_number : ""} onChange={set("ni_number")} /></label>
        <label>Tax code<input autoComplete="off" placeholder="1257L" aria-describedby="taxcode-hint" value={val("tax_code")} onChange={set("tax_code")} /></label>
        <label>
          NI letter
          <select value={val("ni_category")} onChange={set("ni_category")}>
            {["", "A", "M", "H", "J", "Z", "C"].map((c) => <option key={c} value={c}>{c || "Not set"}</option>)}
          </select>
        </label>
      </div>
      <p id="taxcode-hint" className="hint">Tax is worked out automatically for codes such as 1257L, K475, BR, D0, D1 and NT (and their Week 1/Month 1 forms). Scottish and Welsh codes, or any other code, mean the tax has to be typed in by hand on the pay run.</p>
      <div className="three">
        <label>
          Student loan
          <select value={val("student_loan_plan")} onChange={set("student_loan_plan")}>
            {LOANS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
        </label>
        <label>Date of birth{cur.has_date_of_birth ? ` (saved, born ${cur.date_of_birth.slice(-4)})` : ""}<input type="date" value={"date_of_birth" in edit ? edit.date_of_birth : ""} onChange={set("date_of_birth")} /></label>
        <label>Apprenticeship started<input type="date" value={val("apprentice_from")} onChange={set("apprentice_from")} /></label>
      </div>
      <label className="check"><input type="checkbox" checked={!!val("postgrad_loan")} onChange={set("postgrad_loan")} /> Has a postgraduate loan</label>
      <label className="check"><input type="checkbox" checked={!!val("pension_opted_out")} onChange={set("pension_opted_out")} /> Has opted out of the workplace pension</label>
      <div className="three">
        <label>Pay so far this tax year (from a P45)<input type="number" min="0" step="0.01" value={val("prior_pay")} onChange={set("prior_pay")} /></label>
        <label>Tax so far this tax year (from a P45)<input type="number" min="0" step="0.01" value={val("prior_tax")} onChange={set("prior_tax")} /></label>
        <label>Usual hours in a day<input type="number" min="0.5" max="16" step="0.25" value={val("typical_day_hours")} onChange={set("typical_day_hours")} /></label>
      </div>
      <h3>Bank account</h3>
      <p className="hint">{cur.has_bank_details ? `Saved: ${cur.account_name}, sort code ${cur.sort_code}, account ${cur.account_number}.` : "No bank details yet, so the bank payment file cannot include this person."}</p>
      <div className="three">
        <label>Name on the account<input autoComplete="off" value={val("account_name")} onChange={set("account_name")} /></label>
        <label>Sort code<input autoComplete="off" inputMode="numeric" placeholder="12-34-56" value={"sort_code" in edit ? edit.sort_code : ""} onChange={set("sort_code")} /></label>
        <label>Account number<input autoComplete="off" inputMode="numeric" placeholder="8 digits" value={"account_number" in edit ? edit.account_number : ""} onChange={set("account_number")} /></label>
      </div>
    </Section>
  );
}

// What a manager may know: yes or no, the hours limit, and nothing about why.
function CanWork({ id }) {
  const d = useLoad(`/employees/${id}/can-work/`);
  if (!d.data) return null;
  return (
    <section className="panel">
      <h2>Right to work</h2>
      <p>
        <strong>{d.data.allowed ? "Can work today." : d.data.message || "Not cleared to work. Please contact HR."}</strong>
        {d.data.weekly_hours_limit ? ` Limited to ${d.data.weekly_hours_limit} hours a week.` : ""}
        {d.data.paperwork_needed ? " Some paperwork is still outstanding with HR." : ""}
      </p>
    </section>
  );
}

export default function StaffMember() {
  const { id } = useParams();
  const { user } = useAuth();
  const admin = user.role === "owner" || user.role === "hr";
  const person = useLoad(`/employees/${id}/`);
  const stores = useLoad("/stores/");

  return (
    <>
      <p className="crumb"><Link to="/staff">Staff</Link></p>
      {person.loading && !person.data ? <p>Loading…</p> : person.error ? (
        <p className="problem" role="alert">{person.status === 404 ? "That person was not found, or they are not at one of your shops." : person.error}</p>
      ) : (
        <>
          <h1>{personName(person.data.user)}</h1>
          <p className="lede">
            No. {person.data.employee_number} · {person.data.home_store ? person.data.home_store.name : "No shop"} · {person.data.is_current ? "Working here" : "Has left"}
          </p>
          <Basics key={`b${person.data.end_date}${person.data.phone}${person.data.job_title}`} p={person.data} admin={admin} onSaved={person.reload} />
          {!admin && <CanWork id={id} />}
          {admin && (
            <>
              <RightToWork id={id} />
              <HomeShop key={`h${person.data.home_store && person.data.home_store.id}`} p={person.data} stores={stores.data || []} onSaved={person.reload} />
              <Allowance id={id} />
              <PayRates id={id} />
              <PayDetails id={id} />
            </>
          )}
        </>
      )}
    </>
  );
}
