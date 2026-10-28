import { Link } from "react-router-dom";
import { useAuth } from "../auth.jsx";
import ClockPanel from "../components/ClockPanel.jsx";
import ShiftBars from "../components/ShiftBars.jsx";
import useLoad from "../components/useLoad";
import { addDays, greeting, isoToday, period, pounds } from "../format";

function Problem({ text, retry }) {
  return (
    <div>
      <p className="problem" role="alert">{text}</p>
      <button type="button" className="quiet" onClick={retry}>Try again</button>
    </div>
  );
}

export default function Home() {
  const { user } = useAuth();
  const today = isoToday();
  const rota = useLoad(`/me/rota/?from=${today}&to=${addDays(today, 6)}`);
  const leave = useLoad("/me/leave/balance/");
  const slips = useLoad("/me/payslips/");

  // Owner and HR accounts may not be staff members themselves, so they have no rota, clock or payslips.
  const noProfile = rota.status === 404;

  return (
    <>
      <h1 className="hello">{greeting()}, {user.first_name || "there"}</h1>
      {noProfile ? (
        <section className="panel">
          <h2>No staff record on this account</h2>
          <p>
            This account is not set up as a member of staff, so there is no rota, clock or payslip to show.
            {(user.role === "owner" || user.role === "hr") && " Pay runs are under Payroll."} The other screens for running
            the shops (people, rotas, attendance) are being added next.
          </p>
          {(user.role === "owner" || user.role === "hr") && <Link to="/payroll">Go to payroll</Link>}
        </section>
      ) : (
        <>
          <ClockPanel />
          <section className="panel" aria-labelledby="week-title">
            <h2 id="week-title">Your next 7 days</h2>
            {rota.loading ? <p>Loading your shifts…</p> : rota.error ? <Problem text={rota.error} retry={rota.reload} /> : <ShiftBars shifts={rota.data} today={today} />}
          </section>
          <div className="pair">
            <section className="panel" aria-labelledby="leave-title">
              <h2 id="leave-title">Annual leave</h2>
              {leave.loading ? <p>Loading…</p> : leave.error ? <Problem text={leave.error} retry={leave.reload} /> : (
                <>
                  <p className="figure"><strong>{Number(leave.data.remaining)}</strong> days left</p>
                  <p>
                    {Number(leave.data.used)} taken of {Number(leave.data.allowance)}
                    {Number(leave.data.waiting) > 0 && `, ${Number(leave.data.waiting)} waiting for approval`}
                  </p>
                  <Link to="/leave">Request leave</Link>
                </>
              )}
            </section>
            <section className="panel" aria-labelledby="pay-title">
              <h2 id="pay-title">Latest payslip</h2>
              {slips.loading ? <p>Loading…</p> : slips.error ? <Problem text={slips.error} retry={slips.reload} /> : slips.data.length === 0 ? (
                <p className="empty">Your first payslip appears here once a pay run is approved.</p>
              ) : (
                <>
                  <p className="figure"><strong>{pounds(slips.data[0].net_pay)}</strong> take-home</p>
                  <p>{period(slips.data[0].days) || `Payslip ${slips.data[0].number}`}</p>
                  <Link to="/payslips">All payslips</Link>
                </>
              )}
            </section>
          </div>
        </>
      )}
    </>
  );
}
