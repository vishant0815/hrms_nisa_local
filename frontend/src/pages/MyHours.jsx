import { useState } from "react";
import useLoad from "../components/useLoad";
import { addDays, fullDay, isoToday, minutesText, mondayOf, shortDay, ukDate, ukTime } from "../format";

export const STATUS = {
  clocked_in: "Clocked in now",
  under_review: "Waiting for your manager to check",
  rejected: "Not accepted",
  ok: "Counted",
};

// Records grouped by the UK day they started, oldest day first, each day's total included.
export function byDay(records) {
  const days = new Map();
  for (const r of records) {
    const date = ukDate(r.clock_in_at);
    if (!days.has(date)) days.set(date, []);
    days.get(date).push(r);
  }
  return [...days.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([date, list]) => ({
      date,
      records: list.sort((a, b) => (a.clock_in_at < b.clock_in_at ? -1 : 1)),
      minutes: list.filter((r) => r.status !== "rejected").reduce((sum, r) => sum + (r.worked_minutes || 0), 0),
    }));
}

export default function MyHours() {
  const [monday, setMonday] = useState(mondayOf(isoToday()));
  const sunday = addDays(monday, 6);
  const { data, loading, error, status } = useLoad(`/me/attendance/?start=${monday}&end=${sunday}`);
  const thisWeek = monday === mondayOf(isoToday());

  if (status === 404) {
    return (
      <>
        <h1>My hours</h1>
        <p className="panel">This account is not set up as a member of staff, so there are no hours to show.</p>
      </>
    );
  }

  const days = data ? byDay(data) : [];
  const total = days.reduce((sum, d) => sum + d.minutes, 0);

  return (
    <>
      <h1>My hours</h1>
      <div className="weeknav" role="group" aria-label="Choose a week">
        <button type="button" className="quiet" onClick={() => setMonday(addDays(monday, -7))}>Earlier week</button>
        <strong>{shortDay(monday)} to {shortDay(sunday)}</strong>
        <button type="button" className="quiet" onClick={() => setMonday(addDays(monday, 7))} disabled={thisWeek}>Later week</button>
      </div>
      <section className="panel" aria-labelledby="wk">
        <h2 id="wk">Hours this week</h2>
        {loading ? <p>Loading…</p> : error ? <p className="problem" role="alert">{error}</p> : days.length === 0 ? (
          <p className="empty">No clock-ins this week.</p>
        ) : (
          <>
            <p className="figure"><strong>{minutesText(total)}</strong> counted so far</p>
            <ul className="rows">
              {days.map((d) => (
                <li key={d.date}>
                  <div>
                    <strong>{fullDay(d.date)}</strong>
                    {d.records.map((r) => (
                      <span key={r.id}>
                        {r.store.name}: {ukTime(r.clock_in_at)} to {r.clock_out_at ? ukTime(r.clock_out_at) : "still working"}
                        {r.clock_out_at && ` · ${minutesText(r.worked_minutes)}`}
                        {" · "}
                        <span className={`state ${r.status === "ok" ? "approved" : r.status === "rejected" ? "rejected" : ""}`}>{STATUS[r.status] || r.status}</span>
                        {r.review_note && <em> {r.review_note}</em>}
                      </span>
                    ))}
                  </div>
                  <div className="side"><strong>{minutesText(d.minutes)}</strong></div>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </>
  );
}
