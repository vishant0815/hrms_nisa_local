import { useState } from "react";
import { api } from "../api";
import useLoad from "../components/useLoad";
import { period, pounds } from "../format";

export default function Payslips() {
  const slips = useLoad("/me/payslips/");
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState("");

  async function download(slip) {
    setBusy(slip.id);
    setError("");
    try {
      const blob = await api(`/me/payslips/${slip.id}/pdf/`, { blob: true });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `payslip-${slip.number}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e.message);
    }
    setBusy(null);
  }

  return (
    <>
      <h1>Payslips</h1>
      <p className="lede">
        Payslips are also emailed to you every Thursday. The emailed PDF is locked: it opens with your first name followed
        by your birthday as month, year, day, for example sam030003. The copy you download here opens normally.
      </p>
      {error && <p className="problem" role="alert">{error}</p>}
      <section className="panel">
        {slips.loading ? <p>Loading…</p> : slips.error ? <p className="problem" role="alert">{slips.error}</p> : slips.data.length === 0 ? (
          <p className="empty">Your first payslip appears here once a pay run is approved.</p>
        ) : (
          <ul className="rows">
            {slips.data.map((s) => (
              <li key={s.id}>
                <div>
                  <strong>{period(s.days) || s.number}</strong>
                  <span>
                    Gross {pounds(s.gross_pay)}, deductions {pounds(s.total_deductions)}
                  </span>
                </div>
                <div className="side">
                  <span className="figure"><strong>{pounds(s.net_pay)}</strong> take-home</span>
                  <button type="button" className="quiet" disabled={busy === s.id} onClick={() => download(s)}>
                    {busy === s.id ? "Preparing…" : "Download PDF"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
