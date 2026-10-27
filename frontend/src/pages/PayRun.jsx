import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { saveBlob } from "../download";
import PayslipEditor from "../components/PayslipEditor.jsx";
import useLoad from "../components/useLoad";
import { minutesText, pounds, shortDay } from "../format";
import { RUN_STATUS } from "./Payroll.jsx";

export function slipState(slip) {
  if (slip.held.length) return { text: `${slip.held.length} held`, tone: "rejected" };
  if (Object.keys(slip.sick_dates || {}).length > 0 && Number(slip.sick_pay) === 0) return { text: "Enter sick pay", tone: "pending" };
  if (Number(slip.gross_pay) > 0 && !slip.deductions_confirmed) return { text: "Check and confirm", tone: "pending" };
  return { text: "Ready", tone: "approved" };
}

export default function PayRun() {
  const { id } = useParams();
  const load = useLoad(`/pay-runs/${id}/`);
  const [open, setOpen] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [problems, setProblems] = useState([]);
  const [note, setNote] = useState("");

  const run = load.data;
  const draft = run && run.status === "draft";
  const list = run ? run.payslips : [];

  const act = useCallback(async (label, request, after) => {
    setBusy(label);
    setError("");
    setProblems([]);
    setNote("");
    try {
      await request();
      after && after();
    } catch (e) {
      setError(e.message);
      if (e instanceof ApiError && e.data && Array.isArray(e.data.problems)) {
        setProblems(e.data.problems.map(String));
        setError("This pay run cannot be approved yet:");
      }
    }
    setBusy("");
  }, []);

  if (load.loading && !run) return <p>Loading the pay run…</p>;
  if (load.error) return <><h1>Pay run</h1><p className="problem" role="alert">{load.error}</p><Link to="/payroll">Back to payroll</Link></>;

  const rebuild = () => act("rebuild", () => api(`/pay-runs/${id}/rebuild/`, { method: "POST" }), () => { setNote("Draft rebuilt from the latest clock records."); load.reload(); });
  const approve = () => act("approve", () => api(`/pay-runs/${id}/approve/`, { method: "POST" }), () => { setConfirming(false); setNote("Approved. The payslip PDFs are made and will be emailed on the pay date."); load.reload(); });
  const bankFile = () => act("bank", async () => saveBlob(await api(`/pay-runs/${id}/bank-file/`, { blob: true }), `bank-payments-${run.pay_date}.csv`));
  const pdf = (slip) => act(`pdf${slip.id}`, async () => saveBlob(await api(`/payslips/${slip.id}/pdf/`, { blob: true }), `payslip-${slip.number}.pdf`));

  const total = list.reduce((sum, s) => sum + Number(s.net_pay), 0);

  return (
    <>
      <p className="crumb"><Link to="/payroll">Payroll</Link></p>
      <h1>Pay week {shortDay(run.period_start)} to {shortDay(run.period_end)}</h1>
      <section className="panel summary">
        <p><span className={`state ${draft ? "pending" : "approved"}`}>{RUN_STATUS[run.status]}</span></p>
        <p>Paid on {shortDay(run.pay_date)}. {list.length} {list.length === 1 ? "payslip" : "payslips"}, {pounds(total)} take-home in total.</p>
        {draft && run.problems.length > 0 && (
          <div className="blockers">
            <h2>Before this run can be approved</h2>
            <ul>{run.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
          </div>
        )}
        {error && (
          <div role="alert" className="problem">
            <p>{error}</p>
            {problems.length > 0 && <ul>{problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}
          </div>
        )}
        {note && <p className="note" role="status">{note}</p>}
        <div className="actions">
          {draft && !confirming && (
            <>
              <button type="button" className="primary" disabled={!!busy} onClick={() => { setConfirming(true); setError(""); setProblems([]); }}>Approve pay run</button>
              <button type="button" className="quiet" disabled={!!busy} onClick={rebuild}>{busy === "rebuild" ? "Rebuilding…" : "Rebuild draft"}</button>
            </>
          )}
          {!draft && <button type="button" className="quiet" disabled={!!busy} onClick={bankFile}>{busy === "bank" ? "Preparing…" : "Download bank file"}</button>}
        </div>
        {draft && confirming && (
          <div className="confirm" role="group" aria-label="Confirm approval">
            <p>
              <strong>Approve this pay run?</strong> The figures are locked, the payslip PDFs are made, and each person is emailed
              on {shortDay(run.pay_date)}. This cannot be undone.
            </p>
            <div className="actions">
              <button type="button" className="primary" disabled={!!busy} onClick={approve}>{busy === "approve" ? "Approving…" : "Yes, approve"}</button>
              <button type="button" className="quiet" disabled={!!busy} onClick={() => setConfirming(false)}>Not yet</button>
            </div>
          </div>
        )}
        {draft && <p className="hint">Rebuilding re-reads the clock records and throws away unsaved figures, so do it before you check the payslips.</p>}
      </section>

      <section className="panel" aria-labelledby="slips-title">
        <h2 id="slips-title">Payslips</h2>
        {list.length === 0 ? <p className="empty">Nobody has hours or leave to pay in this week.</p> : (
          <ul className="slips">
            {list.map((slip) => {
              const st = slipState(slip);
              const isOpen = open === slip.id;
              return (
                <li key={slip.id}>
                  <button type="button" className="slip-head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : slip.id)}>
                    <span className="who-slip"><strong>{slip.employee_name}</strong><small>{slip.employee_number}</small></span>
                    <span>{minutesText(slip.paid_minutes)}</span>
                    <span>Gross {pounds(slip.gross_pay)}</span>
                    <span><strong>{pounds(slip.net_pay)}</strong> net</span>
                    <span className={`state ${st.tone}`}>{st.text}</span>
                  </button>
                  {isOpen && (
                    <div className="slip-body">
                      <PayslipEditor key={slip.id} slip={slip} editable={draft} onChange={() => load.reload()} />
                      {!draft && (
                        <button type="button" className="quiet" disabled={!!busy} onClick={() => pdf(slip)}>{busy === `pdf${slip.id}` ? "Preparing…" : "Download payslip PDF"}</button>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}
