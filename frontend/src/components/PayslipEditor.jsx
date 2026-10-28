import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { pounds, shortDay } from "../format";

export const HELD = {
  still_clocked_in: "Still clocked in. They need to clock out first.",
  needs_review: "This clock entry is waiting for a manager's decision.",
  extra_hours: "Clocked more than the allowed time over the rota. It is not paid until a manager agrees.",
  no_rate: "No hourly rate for this day. Set one before the run can be approved.",
  no_typical_day: "Annual leave to pay but no pay history yet. Set the person's typical day length.",
};

const FIELDS = [
  ["income_tax", "Income tax"],
  ["national_insurance", "National Insurance"],
  ["pension", "Pension"],
  ["student_loan", "Student loan"],
  ["other_deductions", "Other deductions"],
];

export default function PayslipEditor({ slip, editable, onChange }) {
  const [values, setValues] = useState(() => Object.fromEntries(FIELDS.map(([k]) => [k, slip[k]])));
  const [sick, setSick] = useState(slip.sick_pay || "0.00");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");

  // The figures can change underneath this form (a rebuild, or sick pay changing the tax), so follow them, but keep
  // the message the person is reading.
  useEffect(() => {
    setValues(Object.fromEntries(FIELDS.map(([k]) => [k, slip[k]])));
    setSick(slip.sick_pay || "0.00");
  }, [slip.income_tax, slip.national_insurance, slip.pension, slip.student_loan, slip.other_deductions, slip.sick_pay]);

  async function run(label, request) {
    setBusy(label);
    setError("");
    setNote("");
    try {
      const updated = await request();
      if (updated && updated.id) {
        setValues(Object.fromEntries(FIELDS.map(([k]) => [k, updated[k]])));
        setSick(updated.sick_pay || "0.00");
        onChange(updated);
      }
      return updated;
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  const save = (confirmed) =>
    run("save", async () => {
      const updated = await api(`/payslips/${slip.id}/deductions/`, { method: "PATCH", body: { ...values, confirmed } });
      setNote(confirmed ? "Confirmed. These figures are checked." : "Saved. Still to be confirmed.");
      return updated;
    });
  const suggest = () =>
    run("suggest", async () => {
      const updated = await api(`/payslips/${slip.id}/suggest/`, { method: "POST" });
      setNote("Tax, National Insurance, student loan and pension worked out again from their pay details. Check them, then confirm.");
      return updated;
    });
  const saveSick = () =>
    run("sick", async () => {
      const updated = await api(`/payslips/${slip.id}/sick-pay/`, { method: "PATCH", body: { sick_pay: sick } });
      setNote("Sick pay saved. The tax figures were worked out again, so check and confirm them.");
      return updated;
    });
  const agree = (recordId) =>
    run("extra", async () => {
      await api(`/attendance/${recordId}/approve-extra-hours/`, { method: "POST", body: {} });
      setNote("Extra hours agreed. Rebuild the draft to include them.");
    });

  return (
    <div className="editor">
      <h3>Earnings</h3>
      {slip.earnings.length === 0 ? <p className="empty">Nothing to pay this week.</p> : (
        <ul className="plain">
          {slip.earnings.map((line, i) => (
            <li key={i}>
              <span>{line.label}{line.rate ? ` at ${pounds(line.rate)} an hour` : ""}</span>
              <strong>{pounds(line.amount)}</strong>
            </li>
          ))}
        </ul>
      )}

      {slip.held.length > 0 && (
        <>
          <h3>Held, not paid yet</h3>
          <ul className="plain">
            {slip.held.map((h, i) => (
              <li key={i}>
                <span>{shortDay(h.date)}: {HELD[h.reason] || h.reason}</span>
                {h.reason === "extra_hours" && h.record_id && editable && (
                  <button type="button" className="quiet" disabled={!!busy} onClick={() => agree(h.record_id)}>
                    {busy === "extra" ? "Saving…" : "Agree to pay these hours"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Deductions</h3>
      {editable ? (
        <form onSubmit={(e) => { e.preventDefault(); save(true); }}>
          <div className="money-grid">
            {FIELDS.map(([key, label]) => (
              <label key={key}>
                {label}
                <input inputMode="decimal" value={values[key]} onChange={(e) => setValues({ ...values, [key]: e.target.value })} />
              </label>
            ))}
          </div>
          <div className="actions">
            <button type="submit" className="primary" disabled={!!busy}>{busy === "save" ? "Saving…" : "Save and confirm"}</button>
            <button type="button" className="quiet" disabled={!!busy} onClick={() => save(false)}>Save without confirming</button>
            <button type="button" className="quiet" disabled={!!busy} onClick={suggest}>{busy === "suggest" ? "Working it out…" : "Work out again"}</button>
          </div>
          <p className="hint">The figures are suggestions from the tax rules built in. You must check and confirm each payslip before the run can be approved.</p>
        </form>
      ) : (
        <ul className="plain">
          {FIELDS.map(([key, label]) => <li key={key}><span>{label}</span><strong>{pounds(slip[key])}</strong></li>)}
        </ul>
      )}
      {slip.deduction_notes && (
        <p className="notice">
          <strong>Check before you confirm:</strong> {slip.deduction_notes}
        </p>
      )}

      {editable && Object.keys(slip.sick_dates || {}).length > 0 && (
        <form onSubmit={(e) => { e.preventDefault(); saveSick(); }}>
          <h3>Statutory sick pay</h3>
          <p>This person was off sick. Enter the statutory sick pay your accountant worked out for these days.</p>
          <label>
            Sick pay (£)
            <input inputMode="decimal" value={sick} onChange={(e) => setSick(e.target.value)} />
          </label>
          <button type="submit" className="quiet" disabled={!!busy}>{busy === "sick" ? "Saving…" : "Save sick pay"}</button>
        </form>
      )}

      {error && <p className="problem" role="alert">{error}</p>}
      {note && <p className="note" role="status">{note}</p>}
    </div>
  );
}
