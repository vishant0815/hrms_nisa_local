// Built from parts so the text is the same in every browser ("Mon 19 Oct"), whatever punctuation its locale data adds.
function dayText(date, weekday, month) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", { weekday, day: "numeric", month }).formatToParts(date).map((p) => [p.type, p.value]),
  );
  return `${parts.weekday} ${parts.day} ${parts.month}`;
}
const money = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" });

// Dates from the API are plain "2026-10-27" values; build them as local dates so the day never shifts.
export function toDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function isoToday(now = new Date()) {
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

export function addDays(iso, n) {
  const date = toDate(iso);
  date.setDate(date.getDate() + n);
  return isoToday(date);
}

export const shortDay = (iso) => dayText(toDate(iso), "short", "short");
export const fullDay = (iso) => dayText(toDate(iso), "long", "long");
export const pounds = (value) => money.format(Number(value));

// "09:00:00" -> "09:00"
export const clock = (time) => (time || "").slice(0, 5);

// An instant from the server, shown in UK time whatever the phone's setting is.
export function ukTime(instant) {
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/London" }).format(new Date(instant));
}

export function greeting(now = new Date()) {
  const hour = Number(new Intl.DateTimeFormat("en-GB", { hour: "numeric", hour12: false, timeZone: "Europe/London" }).format(now));
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export function hoursText(hours) {
  const n = Number(hours);
  return Number.isInteger(n) ? `${n} hours` : `${n.toFixed(2).replace(/0$/, "")} hours`;
}

export function period(days) {
  if (!days || !days.length) return null;
  const dates = days.map((d) => d.date).sort();
  return `${shortDay(dates[0])} to ${shortDay(dates[dates.length - 1])}`;
}

// Minutes as "38h 50m" for pay hours.
export function minutesText(total) {
  const m = Number(total) || 0;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  if (!h) return `${rest}m`;
  return rest ? `${h}h ${rest}m` : `${h}h`;
}

// The Monday on or before a date, as "2026-10-19".
export function mondayOf(iso) {
  const date = toDate(iso);
  const back = (date.getDay() + 6) % 7;
  return addDays(iso, -back);
}
