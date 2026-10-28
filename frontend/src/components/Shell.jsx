import { NavLink } from "react-router-dom";
import { ROLE_NAMES, useAuth } from "../auth.jsx";
import ThemeToggle from "./ThemeToggle.jsx";

const LINKS = [
  { to: "/", label: "Home", end: true },
  { to: "/leave", label: "Leave" },
  { to: "/payslips", label: "Payslips" },
  { to: "/rota", label: "Rota", roles: ["owner", "hr", "manager"] },
  { to: "/attendance", label: "Attendance", roles: ["owner", "hr", "manager"] },
  { to: "/leave-requests", label: "Leave requests", roles: ["owner", "hr", "manager"] },
  { to: "/staff", label: "Staff", roles: ["owner", "hr", "manager"] },
  { to: "/swaps", label: "Swaps" },
  { to: "/payroll", label: "Payroll", roles: ["owner", "hr"] },
];

export default function Shell({ children }) {
  const { user, logout } = useAuth();
  const name = [user.first_name, user.last_name].filter(Boolean).join(" ") || user.email;
  return (
    <div className="shell">
      <header className="topbar">
        <a className="skip" href="#main">
          Skip to content
        </a>
        <span className="brand">Nisa Local</span>
        <nav aria-label="Main">
          {LINKS.filter((link) => !link.roles || link.roles.includes(user.role)).map((link) => (
            <NavLink key={link.to} to={link.to} end={link.end}>
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="who">
          <span>
            {name}
            <small>{ROLE_NAMES[user.role] || user.role}</small>
          </span>
          <ThemeToggle />
          <button type="button" className="quiet" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>
      <main id="main">{children}</main>
    </div>
  );
}
