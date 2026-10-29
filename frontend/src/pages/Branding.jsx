import { useState } from "react";
import { api } from "../api";
import { useBranding } from "../branding.jsx";

export default function Branding() {
  const brand = useBranding();
  const [name, setName] = useState(brand.name);
  const [file, setFile] = useState(null);
  const [message, setMessage] = useState({ ok: true, text: "" });
  const [busy, setBusy] = useState("");

  async function run(what, action, done) {
    setBusy(what);
    setMessage({ ok: true, text: "" });
    try {
      await action();
      await brand.reload();
      setMessage({ ok: true, text: done });
    } catch (e) {
      setMessage({ ok: false, text: e.message });
    }
    setBusy("");
  }

  function upload(e) {
    e.preventDefault();
    const body = new FormData();
    body.append("file", file);
    run("logo", async () => { await api("/branding/edit/", { method: "POST", body }); setFile(null); e.target.reset(); }, "Logo saved. It now shows on every page.");
  }

  return (
    <>
      <h1>Branding</h1>
      <p className="lede">The name and logo shown at the top of every page and on the sign-in page. Changing them here changes them for everyone straight away.</p>
      {message.text && <p className={message.ok ? "note" : "problem"} role={message.ok ? "status" : "alert"}>{message.text}</p>}

      <form className="panel form" onSubmit={upload}>
        <h2>Logo</h2>
        {brand.logoUrl ? (
          <p className="logo-preview"><img src={brand.logoUrl} alt={`The current ${brand.name} logo`} /></p>
        ) : (
          <p className="empty">No logo yet, so the name is shown instead.</p>
        )}
        <p className="hint">A PNG, JPEG or WebP image up to 1 MB. A wide logo on a transparent or white background works best; it is shown about 30 pixels tall in the top bar. SVG files are not accepted.</p>
        <label>Choose a logo<input type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp" onChange={(e) => setFile(e.target.files[0] || null)} /></label>
        <div className="actions">
          <button type="submit" className="primary" disabled={!file || busy === "logo"}>{busy === "logo" ? "Uploading…" : brand.logoUrl ? "Replace the logo" : "Upload the logo"}</button>
          {brand.logoUrl && <button type="button" className="quiet danger" disabled={!!busy} onClick={() => run("remove", () => api("/branding/edit/", { method: "DELETE" }), "Logo removed.")}>Remove the logo</button>}
        </div>
      </form>

      <form className="panel form" onSubmit={(e) => { e.preventDefault(); run("name", () => api("/branding/edit/", { method: "PATCH", body: { company_name: name } }), "Name saved."); }}>
        <h2>Name</h2>
        <label>Shown when there is no logo, in the browser tab and for screen readers<input required maxLength={60} value={name} onChange={(e) => setName(e.target.value)} /></label>
        <button type="submit" className="primary" disabled={busy === "name" || name.trim() === brand.name}>{busy === "name" ? "Saving…" : "Save the name"}</button>
      </form>
    </>
  );
}
