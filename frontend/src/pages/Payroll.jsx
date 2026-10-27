import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import useLoad from "../components/useLoad";
import { addDays, isoToday, mondayOf, pounds, shortDay } from "../format";

export const RUN_STATUS = { draft: "Draft: needs checking", approved: "Approved", sent: "Sent" };

function NewRun() {
  const navigate = useNavigate();
  // The usual run is for the week that just ended.
  const [start, setStart] = useState(addDays(mondayOf(isoToday()), -7));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const run = await api("/pay-runs/", { method: "POST", body: { period_start: start } });
      navigate(`/payroll/${run.id}`);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h2>Start a pay run</h2>
      <p>
        A draft is built for you every Monday morning. Use this if you need one yourself. Pick the Monday the pay week
        starts on.
      </p>
      <label>
        Pay week starts on (a Monday)
        <input type="date" required value={start} onChange={(e) => setStart(e.target.value)} />
      </label>
      {error && <p className="problem" role="alert">{error}</p>}
      <button type="submit" className="primary" disabled={busy}>{busy ? "Building…" : "Build draft"}</button>
    </form>
  );
}

export default function Payroll() {
  const runs = useLoad("/pay-runs/");
  return (
    <>
      <h1>Payroll</h1>
      <section className="panel" aria-labelledby="runs-title">
        <h2 id="runs-title">Pay runs</h2>
        {runs.loading ? <p>Loading…</p> : runs.error ? (
          <div>
            <p className="problem" role="alert">{runs.error}</p>
            <button type="button" className="quiet" onClick={runs.reload}>Try again</button>
          </div>
        ) : runs.data.length === 0 ? (
          <p className="empty">There are no pay runs yet. The first draft is built on the Monday after the first pay week, or you can start one below.</p>
        ) : (
          <ul className="rows">
            {runs.data.map((r) => (
              <li key={r.id}>
                <div>
                  <strong><Link to={`/payroll/${r.id}`}>{shortDay(r.period_start)} to {shortDay(r.period_end)}</Link></strong>
                  <span>
                    Paid on {shortDay(r.pay_date)}, {r.payslip_count} {r.payslip_count === 1 ? "payslip" : "payslips"}, gross {pounds(r.total_gross)}
                  </span>
                </div>
                <div className="side">
                  <span className={`state ${r.status === "draft" ? "pending" : "approved"}`}>{RUN_STATUS[r.status] || r.status}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      <NewRun />
    </>
  );
}
