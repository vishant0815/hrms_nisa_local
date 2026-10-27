import { useState } from "react";
import { api } from "../api";
import { ukTime } from "../format";
import useLoad from "./useLoad";

function deviceId() {
  try {
    let id = window.localStorage.getItem("hrms.device");
    if (!id) {
      id = "web-" + Math.random().toString(36).slice(2, 12);
      window.localStorage.setItem("hrms.device", id);
    }
    return id;
  } catch {
    return "";
  }
}

// Asks the phone where it is. Refusing is allowed, but the manager is then asked to check the entry.
function where() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy_m: p.coords.accuracy }),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 15000 },
    );
  });
}

export default function ClockPanel() {
  const status = useLoad("/clock/status");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  async function press(kind) {
    setBusy(true);
    setResult(null);
    const place = await where();
    try {
      const done = await api(`/clock/${kind}`, { method: "POST", body: { ...(place || {}), device_id: deviceId() } });
      // The heading already says whether you are in or out; only show what else the server or the phone has to add.
      const extra = done.message.replace(/^You are clocked (in|out)\.\s*/, "");
      const note = place ? "" : "Location was not shared, so your manager will check this entry.";
      const text = [extra, note].filter(Boolean).join(" ");
      setResult(text ? { ok: true, text } : null);
    } catch (e) {
      setResult({ ok: false, text: e.message });
    }
    setBusy(false);
    status.reload();
  }

  if (status.loading && !status.data) return <section className="clock" aria-busy="true"><p>Checking your clock status…</p></section>;
  if (status.error) {
    return (
      <section className="clock">
        <h2>Clock</h2>
        <p className="problem" role="alert">{status.error}</p>
        <button type="button" className="quiet" onClick={status.reload}>Try again</button>
      </section>
    );
  }
  const clockedIn = status.data.clocked_in;
  return (
    <section className={clockedIn ? "clock on" : "clock"} aria-labelledby="clock-title">
      <div>
        <h2 id="clock-title">{clockedIn ? "You are clocked in" : "You are clocked out"}</h2>
        {clockedIn && (
          <p>
            Since {ukTime(status.data.since)} at {status.data.store.name}
          </p>
        )}
      </div>
      <button type="button" className="primary big" disabled={busy} onClick={() => press(clockedIn ? "out" : "in")}>
        {busy ? "One moment…" : clockedIn ? "Clock out" : "Clock in"}
      </button>
      {result && (
        <p className={result.ok ? "note" : "problem"} role={result.ok ? "status" : "alert"}>
          {result.text}
        </p>
      )}
    </section>
  );
}
