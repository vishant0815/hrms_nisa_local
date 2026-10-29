import { useState } from "react";
import { api } from "../api";
import useLoad from "../components/useLoad";
import { dateText } from "../format";

const STATUS = { pending: "Waiting for HR to check", verified: "Checked", rejected: "Sent back" };

export default function TermLetters() {
  const letters = useLoad("/me/term-letters/");
  const [institution, setInstitution] = useState("");
  const [file, setFile] = useState(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setDone("");
    const body = new FormData();
    body.append("file", file);
    body.append("institution", institution);
    try {
      await api("/me/term-letters/", { method: "POST", body });
      setDone("Uploaded. HR will check it and confirm your term dates.");
      setFile(null);
      e.target.reset();
      letters.reload();
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  }

  if (letters.status === 404) return <><h1>Term letter</h1><p className="panel">This account is not set up as a member of staff.</p></>;
  return (
    <>
      <h1>Term letter</h1>
      <form className="panel form" onSubmit={submit}>
        <h2>Upload your term-dates letter</h2>
        <p className="hint">Students need to give a letter from their university showing their term and vacation dates. Until HR has checked it, your weekly hours limit applies all year. A PDF, JPEG or PNG up to 5 MB.</p>
        <label>University or college<input maxLength={150} value={institution} onChange={(e) => setInstitution(e.target.value)} /></label>
        <label>The letter<input type="file" required accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" onChange={(e) => setFile(e.target.files[0] || null)} /></label>
        {error && <p className="problem" role="alert">{error}</p>}
        {done && <p className="note" role="status">{done}</p>}
        <button type="submit" className="primary" disabled={busy || !file}>{busy ? "Uploading…" : "Upload"}</button>
      </form>
      <section className="panel">
        <h2>Your letters</h2>
        {letters.loading && !letters.data ? <p>Loading…</p> : letters.error ? <p className="problem" role="alert">{letters.error}</p> : letters.data.length === 0 ? (
          <p className="empty">You have not uploaded a letter yet.</p>
        ) : (
          <ul className="rows">
            {letters.data.map((l) => (
              <li key={l.id}>
                <div>
                  <strong>{l.institution || l.original_filename}</strong>
                  <small>Uploaded {dateText(l.created_at.slice(0, 10))}</small>
                  {l.covers_from && <span>Covers {dateText(l.covers_from)} to {dateText(l.covers_to)}</span>}
                  {l.review_note && <em>HR said: {l.review_note}</em>}
                </div>
                <div className="side"><span className={`state ${l.status === "verified" ? "approved" : l.status === "rejected" ? "rejected" : "pending"}`}>{STATUS[l.status]}</span></div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
