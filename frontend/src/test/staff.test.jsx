import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = [];
let failProfile = false;
const person = (id, first, extra = {}) => ({ id, employee_number: `E${id}`, user: { id: id + 10, email: `${first.toLowerCase()}@example.com`, first_name: first, last_name: "Test", role: "employee" }, phone: "", job_title: "", start_date: "2026-01-05", end_date: null, home_store: { id: 1, name: "Shop A" }, is_current: true, ...extra });
const mockUser = { role: "hr" };
vi.mock("../auth.jsx", () => ({ useAuth: () => ({ user: mockUser }) }));
vi.mock("../api", () => ({
  api: vi.fn(async (path, opts = {}) => {
    calls.push([path, opts.method || "GET", opts.body]);
    if (path === "/employees/" && opts.method === "POST") {
      if (failProfile) { failProfile = false; const e = new Error("This person is not eligible."); throw e; }
      return person(9, "New");
    }
    if (path === "/users/") return { id: 77 };
    if (path === "/employees/") return [person(1, "Sam"), person(2, "Priya", { is_current: false }), person(3, "Ravi", { home_store: { id: 2, name: "Shop B" } })];
    if (path.startsWith("/stores/")) return [{ id: 1, name: "Shop A", is_active: true }, { id: 2, name: "Shop B", is_active: true }];
    if (path === "/employees/1/") return person(1, "Sam");
    if (path.endsWith("/visas/") || path.endsWith("/rtw-checks/") || path === "/visa-types/") return [];
    if (path.endsWith("/can-work/")) return { allowed: true, reasons: [], warnings: [], weekly_hours_limit: null, paperwork_needed: false, message: "" };
    if (path.endsWith("/leave-allowance/")) return { year_start: "2026-04-01", year_end: "2027-03-31", allowance: "28.0", used: "3.0", waiting: "0.0", remaining: "25.0" };
    if (path.endsWith("/pay-rates/")) return opts.method === "POST" ? {} : [{ id: 1, hourly_rate: "12.21", effective_from: "2026-04-01" }];
    if (path.endsWith("/pay-details/")) return { ni_number: "AB******C", tax_code: "1257L", ni_category: "A", student_loan_plan: "", postgrad_loan: false, pension_opted_out: false, apprentice_from: null, prior_pay: null, prior_tax: null, account_name: "S Test", sort_code: "**-**-56", account_number: "****4321", has_bank_details: true, date_of_birth: "**/**/1999", has_date_of_birth: true, typical_day_hours: null };
    return {};
  }),
}));
import Staff from "../pages/Staff.jsx";
import StaffMember from "../pages/StaffMember.jsx";

describe("staff list", () => {
  beforeEach(() => { calls.length = 0; });

  it("shows people working here by default, can show leavers, and searches", async () => {
    render(<MemoryRouter><Staff /></MemoryRouter>);
    expect(await screen.findByText("Sam Test")).toBeInTheDocument();
    expect(screen.queryByText("Priya Test")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Have left" }));
    expect(screen.getByText("Priya Test")).toBeInTheDocument();
    expect(screen.queryByText("Sam Test")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Working here" }));
    await userEvent.type(screen.getByLabelText("Find someone"), "ravi");
    expect(screen.getByText("Ravi Test")).toBeInTheDocument();
    expect(screen.queryByText("Sam Test")).not.toBeInTheDocument();
  });

  it("does not create the account twice when the staff record fails and is retried", async () => {
    failProfile = true;
    render(<MemoryRouter><Staff /></MemoryRouter>);
    await userEvent.click(await screen.findByRole("button", { name: "Add a member of staff" }));
    const form = screen.getByRole("heading", { name: "Add a member of staff" }).closest("form");
    const f = within(form);
    await userEvent.type(f.getByLabelText("First name"), "New");
    await userEvent.type(f.getByLabelText("Last name"), "Person");
    await userEvent.type(f.getByLabelText("Email"), "new@example.com");
    await userEvent.type(f.getByLabelText("Temporary password"), "Correct-Horse-9-Battery");
    await userEvent.click(f.getByRole("button", { name: "Add them" }));
    expect(await f.findByRole("alert")).toHaveTextContent("The sign-in account was created, but the staff record was not");
    await userEvent.click(f.getByRole("button", { name: "Finish adding" }));
    expect(await screen.findByText("New Test was added.")).toBeInTheDocument();
    expect(calls.filter((c) => c[0] === "/users/").length).toBe(1);
    expect(calls.filter((c) => c[0] === "/employees/" && c[1] === "POST").length).toBe(2);
  });
});

describe("a person's page", () => {
  beforeEach(() => { calls.length = 0; mockUser.role = "hr"; });
  const open = () => render(<MemoryRouter initialEntries={["/staff/1"]}><Routes><Route path="/staff/:id" element={<StaffMember />} /></Routes></MemoryRouter>);

  it("never shows full bank or NI numbers and sends only what was typed", async () => {
    open();
    expect(await screen.findByText(/sort code \*\*-\*\*-56, account \*\*\*\*4321/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("AB******C")).toHaveValue("");
    await userEvent.type(screen.getByPlaceholderText("12-34-56"), "20-30-40");
    await userEvent.click(within(screen.getByText("Tax and bank details").closest("form")).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      const c = calls.find((x) => x[0] === "/employees/1/pay-details/" && x[1] === "PUT");
      expect(c[2]).toEqual({ sort_code: "20-30-40" });
    });
  });

  it("adds a pay rate and lists the history", async () => {
    open();
    expect(await screen.findByText("£12.21 an hour")).toBeInTheDocument();
    const form = screen.getByRole("heading", { name: "Hourly rate" }).closest("form");
    await userEvent.type(within(form).getByLabelText("Pounds an hour"), "12.60");
    await userEvent.click(within(form).getByRole("button", { name: "Add this rate" }));
    await waitFor(() => expect(calls.some((c) => c[0] === "/employees/1/pay-rates/" && c[1] === "POST" && Number(c[2].hourly_rate) === 12.6)).toBe(true));
  });

  it("shows managers the basics only: no pay, bank or leave allowance", async () => {
    mockUser.role = "manager";
    open();
    expect(await screen.findByText("Details")).toBeInTheDocument();
    expect(screen.queryByText("Tax and bank details")).not.toBeInTheDocument();
    expect(screen.queryByText("Hourly rate")).not.toBeInTheDocument();
    expect(calls.some((c) => c[0].includes("pay-"))).toBe(false);
  });
});
