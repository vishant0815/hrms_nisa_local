import { useState } from "react";
import { useAuth } from "../auth.jsx";
import { Brand, useBranding } from "../branding.jsx";
import ThemeToggle from "../components/ThemeToggle.jsx";

export default function Login() {
  const { login } = useAuth();
  const { name, logoUrl } = useBranding();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email.trim(), password);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <main className="signin">
      <div className="signin-theme">
        <ThemeToggle />
      </div>
      <div className="signin-sign">
        {logoUrl && <Brand className="signin-logo" />}
        <h1>{name}</h1>
        <p>Staff portal for rotas, hours, leave and payslips.</p>
      </div>
      <form onSubmit={submit} className="signin-form" aria-describedby={error ? "signin-error" : undefined}>
        <h2>Sign in</h2>
        <label>
          Email
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && (
          <p id="signin-error" className="problem" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="hint">Forgotten your password? Ask HR or the owner to reset it.</p>
      </form>
    </main>
  );
}
