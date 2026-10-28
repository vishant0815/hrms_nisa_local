import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import ShiftForm from "../components/ShiftForm.jsx";
import useLoad from "../components/useLoad";
import { addDays, clock, hoursText, isoToday, mondayOf, shortDay } from "../format";

function Controls({ stores, store, setStore, start, setStart }) {
  const thisWeek = mondayOf(isoToday());
  return (
    <div className="controls">
      {stores.length > 1 && (
        <label>
          Shop
          <select value={store || ""} onChange={(e) => setStore(Number(e.target.value))}>
            {stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
      )}
      <div className="weeknav" role="group" aria-label="Choose week">
        <button type="button" className="quiet" onClick={() => setStart(addDays(start, -7))}>Earlier week</button>
        <strong>{shortDay(start)} to {shortDay(addDays(start, 6))}</strong>
        <button type="button" className="quiet" onClick={() => setStart(addDays(start, 7))}>Later week</button>
        {start !== thisWeek && <button type="button" className="quiet" onClick={() => setStart(thisWeek)}>This week</button>}
      </div>
    </div>
  );
}

function CreateWeek({ store, start, onCreated }) {
  const [copy, setCopy] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState(null);
  async function create() {
    setBusy(true);
    setError("");
    try {
      const week = await api("/rota-weeks/", { method: "POST", body: { store, week_start: start, copy_from_previous: copy } });
      setSummary(week.copy_summary);
      onCreated(week);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <h2>No rota for this week yet</h2>
      <label className="check">
        <input type="checkbox" checked={copy} onChange={(e) => setCopy(e.target.checked)} />
        Copy last week's shifts as a starting point
      </label>
      {error && <p className="problem" role="alert">{error}</p>}
      {summary && <p className="note">Copied {summary.copied} shifts.</p>}
      <button type="button" className="primary" disabled={busy} onClick={create}>{busy ? "Creating…" : "Create this week's rota"}</button>
    </section>
  );
}

export default function Rota() {
  const stores = useLoad("/stores/");
  const [store, setStore] = useState(null);
  const [start, setStart] = useState(mondayOf(isoToday()));
  const [form, setForm] = useState(null);       // {day} to add, {shift} to change
  const [flash, setFlash] = useState(null);
  const [confirm, setConfirm] = useState(false);
  const [publishError, setPublishError] = useState("");
  const [busy, setBusy] = useState(false);

  const activeStores = useMemo(() => (stores.data || []).filter((s) => s.is_active), [stores.data]);
  useEffect(() => {
    if (store === null && activeStores.length) setStore(activeStores[0].id);
  }, [activeStores, store]);

  const weeks = useLoad(store ? `/rota-weeks/?store=${store}&week_start=${start}&with_shifts=1` : null);
  const people = useLoad(store ? "/employees/" : null);
  const templates = useLoad(store ? `/shift-templates/?store=${store}` : null);

  const week = weeks.data && weeks.data[0];
  const today = isoToday();
  const shifts = week ? week.shifts.filter((s) => s.status === "scheduled") : [];
  const roster = useMemo(
    () => (people.data || []).filter((p) => p.is_current).map((p) => ({ id: p.id, name: p.user.first_name || p.user.last_name ? `${p.user.first_name} ${p.user.last_name}`.trim() : p.user.email })),
    [people.data],
  );

  const refresh = weeks.reload;
  function saved(result, how) {
    const warnings = (result && result.warnings) || [];
    setFlash(
      how === "cancelled" ? { ok: true, text: "Shift cancelled." } : { ok: true, text: warnings.length ? "Saved. Please note: " + warnings.map((w) => w.message || String(w)).join(" ") : "Shift saved." },
    );
    setForm(null);
    refresh();
  }

  async function publish() {
    setBusy(true);
    setPublishError("");
    try {
      await api(`/rota-weeks/${week.id}/publish/`, { method: "POST" });
      setConfirm(false);
      setFlash({ ok: true, text: "Published. Staff can now see this week." });
      refresh();
    } catch (e) {
      setPublishError(e.message);
    }
    setBusy(false);
  }

  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  const byDay = (d) => shifts.filter((s) => s.date === d).sort((a, b) => a.start_time.localeCompare(b.start_time));
  const open = shifts.filter((s) => !s.employee).length;
  const totals = {};
  shifts.forEach((s) => { if (s.employee) { const t = totals[s.employee.id] || (totals[s.employee.id] = { name: s.employee.name, hours: 0 }); t.hours += Number(s.hours); } });

  if (stores.loading) return <p>Loading…</p>;
  if (stores.error) return <><h1>Rota</h1><p className="problem" role="alert">{stores.error}</p></>;
  if (!activeStores.length) return <><h1>Rota</h1><p className="panel">There are no shops for you to build a rota for.</p></>;

  return (
    <>
      <h1>Rota</h1>
      <Controls stores={activeStores} store={store} setStore={(s) => { setStore(s); setForm(null); setFlash(null); }} start={start} setStart={(s) => { setStart(s); setForm(null); setFlash(null); }} />
      {weeks.error && <p className="problem" role="alert">{weeks.error}</p>}
      {weeks.loading && !weeks.data ? <p>Loading the week…</p> : !week ? (
        <CreateWeek key={`${store}-${start}`} store={store} start={start} onCreated={refresh} />
      ) : (
        <>
          <section className="panel summary">
            <p>
              <span className={`state ${week.status === "published" ? "approved" : "pending"}`}>{week.status === "published" ? "Published: staff can see it" : "Draft: staff cannot see it yet"}</span>
            </p>
            <p>{shifts.length} {shifts.length === 1 ? "shift" : "shifts"}{open > 0 && <>, <strong className="problem">{open} with nobody on them</strong></>}.</p>
            {flash && <p className={flash.ok ? "note" : "problem"} role="status">{flash.text}</p>}
            {week.status === "draft" && !confirm && <button type="button" className="primary" onClick={() => { setConfirm(true); setPublishError(""); }}>Publish this rota</button>}
            {confirm && (
              <div className="confirm" role="group" aria-label="Confirm publishing">
                <p><strong>Publish this rota?</strong> Everyone with a shift will be able to see it. Every shift is checked against the rules first.</p>
                {publishError && <p className="problem" role="alert">{publishError}</p>}
                <div className="actions">
                  <button type="button" className="primary" disabled={busy} onClick={publish}>{busy ? "Checking…" : "Yes, publish"}</button>
                  <button type="button" className="quiet" disabled={busy} onClick={() => setConfirm(false)}>Not yet</button>
                </div>
              </div>
            )}
          </section>

          {days.map((d) => (
            <section key={d} className={d === today ? "panel day today" : "panel day"} aria-label={shortDay(d)}>
              <div className="day-head">
                <h2>{d === today ? `Today, ${shortDay(d)}` : shortDay(d)}</h2>
                {d >= today && <button type="button" className="quiet" onClick={() => setForm({ day: d })}>Add a shift</button>}
              </div>
              {byDay(d).length === 0 ? <p className="empty">No shifts.</p> : (
                <ul className="shifts">
                  {byDay(d).map((s) => (
                    <li key={s.id} className={s.employee ? "" : "openshift"}>
                      <strong>{clock(s.start_time)}–{clock(s.end_time)}</strong>
                      <span>{s.employee ? s.employee.name : "Open shift: nobody on it yet"}{s.role ? `, ${s.role}` : ""}</span>
                      <small>{hoursText(s.hours)}</small>
                      {s.date >= today && <button type="button" className="quiet" onClick={() => setForm({ shift: s })}>Change</button>}
                    </li>
                  ))}
                </ul>
              )}
              {form && ((form.day === d && !form.shift) || (form.shift && form.shift.date === d)) && (
                <ShiftForm key={form.shift ? form.shift.id : d} week={week} shift={form.shift} day={form.day} people={roster} templates={(templates.data || []).filter((t) => t.is_active)} onSaved={saved} onCancel={() => setForm(null)} />
              )}
            </section>
          ))}

          {Object.keys(totals).length > 0 && (
            <section className="panel" aria-labelledby="tot">
              <h2 id="tot">Hours this week</h2>
              <ul className="plain">
                {Object.values(totals).sort((a, b) => a.name.localeCompare(b.name)).map((t) => <li key={t.name}><span>{t.name}</span><strong>{hoursText(t.hours)}</strong></li>)}
              </ul>
            </section>
          )}
        </>
      )}
    </>
  );
}
