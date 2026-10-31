import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth.jsx";
import Shell from "./components/Shell.jsx";
import Login from "./pages/Login.jsx";
import Home from "./pages/Home.jsx";
import Leave from "./pages/Leave.jsx";
import Payslips from "./pages/Payslips.jsx";
import Payroll from "./pages/Payroll.jsx";
import PayRun from "./pages/PayRun.jsx";
import Attendance from "./pages/Attendance.jsx";
import LeaveRequests from "./pages/LeaveRequests.jsx";
import RightToWorkDesk from "./pages/RightToWorkDesk.jsx";
import TermLetters from "./pages/TermLetters.jsx";
import Branding from "./pages/Branding.jsx";
import Staff from "./pages/Staff.jsx";
import StaffMember from "./pages/StaffMember.jsx";
import Rota from "./pages/Rota.jsx";
import Swaps from "./pages/Swaps.jsx";
import MyHours from "./pages/MyHours.jsx";

export default function App() {
  const { status, user } = useAuth();
  const admin = user && (user.role === "owner" || user.role === "hr");
  const manager = admin || (user && user.role === "manager");

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
        {user.role === "employee" && <Route path="/my-hours" element={<MyHours />} />}
        <Route path="/payslips" element={<Payslips />} />
        <Route path="/swaps" element={<Swaps />} />
        {manager && <Route path="/rota" element={<Rota />} />}
        {manager && <Route path="/attendance" element={<Attendance />} />}
        {manager && <Route path="/leave-requests" element={<LeaveRequests />} />}
        {manager && <Route path="/staff" element={<Staff />} />}
        {manager && <Route path="/staff/:id" element={<StaffMember />} />}
        {admin && <Route path="/right-to-work" element={<RightToWorkDesk />} />}
        {user.role === "employee" && <Route path="/term-letter" element={<TermLetters />} />}
        {admin && <Route path="/branding" element={<Branding />} />}
        {admin && <Route path="/payroll" element={<Payroll />} />}
        {admin && <Route path="/payroll/:id" element={<PayRun />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
