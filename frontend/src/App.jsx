import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth.jsx";
import Shell from "./components/Shell.jsx";
import Login from "./pages/Login.jsx";
import Home from "./pages/Home.jsx";
import Leave from "./pages/Leave.jsx";
import Payslips from "./pages/Payslips.jsx";
import Payroll from "./pages/Payroll.jsx";
import PayRun from "./pages/PayRun.jsx";

export default function App() {
  const { status, user } = useAuth();
  const admin = user && (user.role === "owner" || user.role === "hr");

  if (status === "loading") {
    return (
      <p className="splash" role="status">
        Opening the portal…
      </p>
    );
  }
  if (status === "out") {
    return (
      <Routes>
        <Route path="*" element={<Login />} />
      </Routes>
    );
  }
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/leave" element={<Leave />} />
        <Route path="/payslips" element={<Payslips />} />
        {admin && <Route path="/payroll" element={<Payroll />} />}
        {admin && <Route path="/payroll/:id" element={<PayRun />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
