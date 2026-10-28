import { useState } from "react";
import { api } from "../api";
import { addDays, shortDay } from "../format";

const hm = (t) => (t || "").slice(0, 5);

// Add a shift to a week, or change one. The backend checks every rule (overlaps, rest, limits, opening hours) and
// its reasons are shown as they come.
export default function ShiftForm({ week, shift, day, people, templates, onSaved, onCancel }) {
  const days = Array.from({ length: 7 }, (_, i) => addDays(week.week_start, i));
  const [form, setForm] = useState({
    date: shift ? shift.date : day,
    employee: shift && shift.employee ? String(shift.employee.id) : "",
    template: "",
    start_time: shift ? hm(shift.start_time) : "09:00",
    end_time: shift ? hm(shift.end_time) : "17:00",
    break_minutes: shift ? String(shift.break_minutes) : "0",
    role: shift ? shift.role : "",
    notes: shift ? shift.notes : "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  function pickTemplate(e) {
    const t = templates.find((x) => String(x.id) === e.target.value);
    setForm(t ? { ...form, template: e.target.value, start_time: hm(t.start_time), end_time: hm(t.end_time), break_minutes: String(t.break_minutes) } : { ...form, template: "" });
  }

  async function submit(event) {
    event.preventDefault();
    setBusy("save");
    setError("");
    const body = {
      employee: form.employee ? Number(form.employee) : null,
      date: form.date,
      start_time: form.start_time,
      end_time: form.end_time,
      break_minutes: Number(form.break_minutes || 0),
      role: form.role,
      notes: form.notes,
    };
    try {
      const saved = shift
        ? await api(`/shifts/${shift.id}/`, { method: "PATCH", body })
        : await api(`/rota-weeks/${week.id}/shifts/`, { method: "POST", body });
      onSaved(saved);
    } catch (e) {
      setError(e.message);
      setBusy("");
    }
  }

  async function cancelShift() {
    setBusy("cancel");
    setError("");
    try {
      onSaved(await api(`/shifts/${shift.id}/cancel/`, { method: "POST" }), "cancelled");
    } catch (e) {
      setError(e.message);
      setBusy("");
    }
  }

  return (
    <form className="shift-form" onSubmit={submit} aria-label={shift ? "Change shift" : "Add shift"}>
      <h3>{shift ? "Change this shift" : `Add a shift on ${shortDay(form.date)}`}</h3>
      <div className="two">
        <label>
          Day
          <select value={form.date} onChange={set("date")}>
            {days.map((d) => <option key={d} value={d}>{shortDay(d)}</option>)}
          </select>
        </label>
        <label>
          Who works
          <select value={form.employee} onChange={set("employee")}>
            <option value="">Open shift (nobody yet)</option>
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      </div>
      {templates.length > 0 && !shift && (
        <label>
          Start from a saved pattern
          <select value={form.template} onChange={pickTemplate}>
            <option value="">None, I will type the times</option>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.name} ({hm(t.start_time)}–{hm(t.end_time)})</option>)}
          </select>
        </label>
      )}
      <div className="three">
        <label>
          Starts
          <input type="time" required value={form.start_time} onChange={set("start_time")} />
        </label>
        <label>
          Ends
          <input type="time" required value={form.end_time} onChange={set("end_time")} />
        </label>
        <label>
          Unpaid break (minutes)
          <input type="number" min="0" max="480" value={form.break_minutes} onChange={set("break_minutes")} />
        </label>
      </div>
      <div className="two">
        <label>
          Job on the shift (optional)
          <input type="text" maxLength={50} value={form.role} onChange={set("role")} />
        </label>
        <label>
          Note for managers (staff do not see it)
          <input type="text" value={form.notes} onChange={set("notes")} />
        </label>
      </div>
      {error && <p className="problem" role="alert">{error}</p>}
      <div className="actions">
        <button type="submit" className="primary" disabled={!!busy}>{busy === "save" ? "Saving…" : shift ? "Save changes" : "Add shift"}</button>
        <button type="button" className="quiet" disabled={!!busy} onClick={onCancel}>Close</button>
        {shift && <button type="button" className="quiet danger" disabled={!!busy} onClick={cancelShift}>{busy === "cancel" ? "Cancelling…" : "Cancel this shift"}</button>}
      </div>
    </form>
  );
}
