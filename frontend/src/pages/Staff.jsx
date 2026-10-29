import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth.jsx";
import useLoad from "../components/useLoad";
import { isoToday } from "../format";

export const personName = (user) => [user.first_name, user.last_name].filter(Boolean).join(" ") || user.email;

function AddStaff({ stores, onDone, onCancel }) {
  const [f, setF] = useState({ email: "", first_name: "", last_name: "", role: "employee", password: "", home_store: stores[0] ? String(stores[0].id) : "", start_date: isoToday(), phone: "", job_title: "" });
  const [userId, setUserId] = useState(null); // the account exists but the staff record did not save: retry only that part
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    let id = userId;
    try {
      if (!id) {
        const user = await api("/users/", { method: "POST", body: { email: f.email.trim(), first_name: f.first_name.trim(), last_name: f.last_name.trim(), role: f.role, password: f.password } });
        id = user.id;
        setUserId(id);
      }
      const profile = await api("/employees/", { method: "POST", body: { user: id, home_store: Number(f.home_store), start_date: f.start_date, phone: f.phone, job_title: f.job_title } });
      onDone(profile);
    } catch (err) {
      setError(id ? `The sign-in account was created, but the staff record was not: ${err.message} Fix this and press the button again; the account will not be created twice.` : err.message);
      setBusy(false);
    }
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h2>Add a member of staff</h2>
      <p className="hint">This creates their sign-in and their staff record. Give them the temporary password yourself; they can change it after signing in.</p>
      <div className="two">
        <label>First name<input required value={f.first_name} disabled={!!userId} onChange={set("first_name")} /></label>
        <label>Last name<input required value={f.last_name} disabled={!!userId} onChange={set("last_name")} /></label>
      </div>
      <div className="two">
        <label>Email<input type="email" required autoComplete="off" value={f.email} disabled={!!userId} onChange={set("email")} /></label>
        <label>Temporary password<input type="password" required autoComplete="new-password" minLength={10} value={f.password} disabled={!!userId} onChange={set("password")} /></label>
      </div>
      <div className="three">
        <label>
          Role
          <select value={f.role} disabled={!!userId} onChange={set("role")}>
            <option value="employee">Staff</option>
            <option value="manager">Store manager</option>
          </select>
        </label>
        <label>
          Home shop
          <select required value={f.home_store} onChange={set("home_store")}>
            {stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <label>Starts on<input type="date" required value={f.start_date} onChange={set("start_date")} /></label>
      </div>
      <div className="two">
        <label>Job title (optional)<input maxLength={100} value={f.job_title} onChange={set("job_title")} /></label>
        <label>Phone (optional)<input maxLength={30} value={f.phone} onChange={set("phone")} /></label>
      </div>
      {error && <p className="problem" role="alert">{error}</p>}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>{busy ? "Adding…" : userId ? "Finish adding" : "Add them"}</button>
        <button type="button" className="quiet" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

export default function Staff() {
  const { user } = useAuth();
  const admin = user.role === "owner" || user.role === "hr";
  const people = useLoad("/employees/");
  const stores = useLoad("/stores/");
  const [show, setShow] = useState("current");
  const [store, setStore] = useState("");
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const [flash, setFlash] = useState("");

  const activeStores = useMemo(() => (stores.data || []).filter((s) => s.is_active), [stores.data]);
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (people.data || [])
      .filter((p) => (show === "current" ? p.is_current : show === "left" ? !p.is_current : true))
      .filter((p) => !store || (p.home_store && String(p.home_store.id) === store))
      .filter((p) => !q || `${personName(p.user)} ${p.user.email} ${p.employee_number} ${p.job_title}`.toLowerCase().includes(q))
      .sort((a, b) => personName(a.user).localeCompare(personName(b.user)));
  }, [people.data, show, store, search]);

  return (
    <>
      <h1>Staff</h1>
      <div className="controls">
        <label>
          Find someone
          <input type="search" placeholder="Name, email or number" value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
        <div className="tabs" role="group" aria-label="Who to show">
          {[["current", "Working here"], ["left", "Have left"], ["all", "Everyone"]].map(([k, label]) => (
            <button key={k} type="button" className={show === k ? "quiet on" : "quiet"} aria-pressed={show === k} onClick={() => setShow(k)}>{label}</button>
          ))}
        </div>
        {activeStores.length > 1 && (
          <label>
            Shop
            <select value={store} onChange={(e) => setStore(e.target.value)}>
              <option value="">All shops</option>
              {activeStores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        )}
        {admin && <button type="button" className="quiet" onClick={() => setAdding(!adding)}>{adding ? "Close" : "Add a member of staff"}</button>}
      </div>
      {flash && <p className="note" role="status">{flash}</p>}
      {adding && <AddStaff stores={activeStores} onCancel={() => setAdding(false)} onDone={(p) => { setAdding(false); setFlash(`${personName(p.user)} was added.`); people.reload(); }} />}
      <section className="panel">
        {people.loading && !people.data ? <p>Loading…</p> : people.error ? <p className="problem" role="alert">{people.error}</p> : rows.length === 0 ? (
          <p className="empty">Nobody matches.</p>
        ) : (
          <ul className="rows">
            {rows.map((p) => (
              <li key={p.id}>
                <div>
                  <strong><Link to={`/staff/${p.id}`}>{personName(p.user)}</Link></strong>
                  <span>{p.job_title || (p.user.role === "manager" ? "Store manager" : "Staff")}{p.home_store ? `, ${p.home_store.name}` : ""}</span>
                  <small>No. {p.employee_number} · {p.user.email}</small>
                </div>
                <div className="side">{!p.is_current && <span className="state cancelled">Has left</span>}</div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
