import { addDays, clock, fullDay, hoursText, isoToday, shortDay } from "../format";

const FROM = 6 * 60; // the track runs from 06:00 to midnight, which covers a shop's day
const TO = 24 * 60;

function minutes(time) {
  const [h, m] = time.split(":").map(Number);
  return h * 60 + m;
}

// Where a shift sits on the day's track, as percentages. A shift running past midnight stops at the end of the track.
export function span(start, end) {
  const a = Math.max(FROM, minutes(start));
  let b = minutes(end);
  if (b <= minutes(start)) b = TO;
  b = Math.min(TO, b);
  const left = ((a - FROM) / (TO - FROM)) * 100;
  const width = Math.max(2, ((Math.max(a, b) - a) / (TO - FROM)) * 100);
  return { left: `${left}%`, width: `${width}%` };
}

export function Track({ start, end }) {
  return (
    <span className="track" aria-hidden="true">
      <span className="bar" style={span(start, end)} />
    </span>
  );
}

function label(date, today) {
  if (date === today) return "Today";
  if (date === addDays(today, 1)) return "Tomorrow";
  return shortDay(date);
}

// A week of shifts: the day and the hours in words, and the same shift drawn on a track from 6am to midnight so
// early, late and long shifts can be compared at a glance.
export default function ShiftBars({ shifts, today = isoToday() }) {
  if (!shifts.length) {
    return (
      <p className="empty">
        No shifts are published for the next 7 days. Your manager publishes the rota once it is ready, so check back later.
      </p>
    );
  }
  return (
    <ol className="shiftrows">
      {shifts.map((s) => (
        <li key={s.id} className={s.date === today ? "shiftrow today" : "shiftrow"}>
          <time dateTime={s.date} title={fullDay(s.date)}>{label(s.date, today)}</time>
          <strong>{clock(s.start_time)}–{clock(s.end_time)}</strong>
          <Track start={s.start_time} end={s.end_time} />
          <span className="where">
            {s.store.name}
            {s.role ? `, ${s.role}` : ""}
          </span>
          <small>{hoursText(s.hours)}</small>
        </li>
      ))}
    </ol>
  );
}
