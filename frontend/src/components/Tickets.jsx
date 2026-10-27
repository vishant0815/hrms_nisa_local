import { addDays, clock, fullDay, hoursText, isoToday, shortDay } from "../format";

function label(date, today) {
  if (date === today) return "Today";
  if (date === addDays(today, 1)) return "Tomorrow";
  return shortDay(date);
}

// A week of shifts drawn as shelf-edge price tickets: the day on the left, the hours big, the shop underneath.
export default function Tickets({ shifts, today = isoToday() }) {
  if (!shifts.length) {
    return (
      <p className="empty">
        No shifts are published for the next 7 days. Your manager publishes the rota once it is ready, so check back later.
      </p>
    );
  }
  return (
    <ol className="tickets">
      {shifts.map((s) => (
        <li key={s.id} className={s.date === today ? "ticket today" : "ticket"}>
          <time dateTime={s.date} title={fullDay(s.date)}>
            {label(s.date, today)}
          </time>
          <strong>
            {clock(s.start_time)}–{clock(s.end_time)}
          </strong>
          <span>
            {s.store.name}
            {s.role ? `, ${s.role}` : ""}
          </span>
          <small>{hoursText(s.hours)}</small>
        </li>
      ))}
    </ol>
  );
}
