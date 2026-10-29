import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = [];
const visa = { id: 4, employee: { id: 1, name: "Sam Patel" }, visa_type: { id: 6, name: "Student" }, start_date: "2026-09-01", expiry_date: "2027-06-30", sponsor_reference: "", work_conditions: "", manual_work_allowed: null, weekly_hours_cap_override: null, is_current: true, revoked_on: null, status: "pending_check", latest_check: null };
const letter = { id: 8, employee: { id: 1, name: "Sam Patel" }, institution: "UEL", original_filename: "term.pdf", created_at: "2026-10-20T10:00:00Z", status: "pending", suggested: { covers_from: "2026-09-21", covers_to: "2027-06-18", vacations: [{ start_date: "2026-12-14", end_date: "2027-01-08", label: "Christmas" }], note: "Read automatically from the file. Check it against the letter before confirming." } };
vi.mock("../api", () => ({
  api: vi.fn(async (path, opts = {}) => {
    calls.push([path, opts.method || "GET", opts.body]);
    if (path === "/employees/1/visas/") return [visa];
    if (path === "/employees/1/rtw-checks/") return opts.method === "POST" ? {} : [];
    if (path === "/employees/1/can-work/") return { allowed: false, reasons: [{ code: "check_missing", text: "No right-to-work check has been recorded." }], warnings: [], weekly_hours_limit: 20, paperwork_needed: true };
    if (path === "/visa-types/") return [{ id: 6, name: "Student", is_active: true, requires_expiry: true, requires_sponsorship: false, requires_manual_decision: false }, { id: 5, name: "Skilled Worker", is_active: true, requires_expiry: true, requires_sponsorship: true, requires_manual_decision: false }];
    if (path.startsWith("/visas/expiring/")) return [visa];
    if (path === "/visas/missing-term-letters/") return [];
    if (path.startsWith("/term-letters/?")) return [letter];
    return {};
  }),
}));
import RightToWork from "../components/RightToWork.jsx";
import RightToWorkDesk from "../pages/RightToWorkDesk.jsx";

describe("a person's right to work", () => {
  beforeEach(() => { calls.length = 0; });

  it("shows the status, why they cannot be rostered, and records a check against the current status", async () => {
    render(<MemoryRouter><RightToWork id={1} /></MemoryRouter>);
    expect(await screen.findByText("Right-to-work check needed")).toBeInTheDocument();
    expect(await screen.findByText("Cannot be rostered today")).toBeInTheDocument();
    expect(screen.getByText("No right-to-work check has been recorded.")).toBeInTheDocument();
    expect(screen.getByText(/limited to 20 hours a week/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Record a check" }));
    const form = screen.getByRole("heading", { name: "Record a right-to-work check" }).closest("form");
    await userEvent.type(within(form).getByLabelText("Where the evidence is kept"), "HR folder");
    await userEvent.click(within(form).getByRole("button", { name: "Save this check" }));
    await waitFor(() => {
      const c = calls.find((x) => x[0] === "/employees/1/rtw-checks/" && x[1] === "POST");
      expect(c[2].visa).toBe(4);
      expect(c[2].outcome).toBe("pass");
      expect(c[2].next_check_due).toBe("2027-06-30"); // the status runs out then, so the next check is due then
      expect(c[2].evidence_reference).toBe("HR folder");
    });
  });

  it("asks only for what the type of status needs", async () => {
    render(<MemoryRouter><RightToWork id={1} /></MemoryRouter>);
    await userEvent.click(await screen.findByRole("button", { name: "Record a new status" }));
    expect(screen.queryByLabelText("Sponsor reference")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Type of status"), "Skilled Worker");
    expect(screen.getByLabelText("Sponsor reference")).toBeRequired();
    expect(screen.getByLabelText("Expires on")).toBeRequired();
  });
});

describe("the right to work desk", () => {
  beforeEach(() => { calls.length = 0; });

  it("pre-fills the dates read from a letter, which HR must confirm", async () => {
    render(<MemoryRouter><RightToWorkDesk /></MemoryRouter>);
    await userEvent.click(await screen.findByRole("button", { name: "Confirm dates" }));
    expect(screen.getByText(/Check it against the letter before confirming/)).toBeInTheDocument();
    expect(screen.getByLabelText("Term dates covered: from")).toHaveValue("2026-09-21");
    await userEvent.click(screen.getByRole("button", { name: "Confirm these dates" }));
    await waitFor(() => {
      const c = calls.find((x) => x[0] === "/term-letters/8/verify/");
      expect(c[2]).toEqual({ covers_from: "2026-09-21", covers_to: "2027-06-18", vacations: [{ start_date: "2026-12-14", end_date: "2027-01-08", label: "Christmas" }] });
    });
  });

  it("rejects a letter only with a reason", async () => {
    render(<MemoryRouter><RightToWorkDesk /></MemoryRouter>);
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
    expect(screen.getByLabelText(/Why/)).toBeRequired();
    await userEvent.type(screen.getByLabelText(/Why/), "Unreadable");
    await userEvent.click(screen.getByRole("button", { name: "Reject this letter" }));
    await waitFor(() => expect(calls.some((c) => c[0] === "/term-letters/8/reject/" && c[2].reason === "Unreadable")).toBe(true));
  });
});
