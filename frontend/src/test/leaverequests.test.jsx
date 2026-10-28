import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = [];
const annual = { id: 5, employee: { id: 3, name: "Sam Patel" }, kind: "annual", start_date: "2099-11-02", end_date: "2099-11-04", half_day: "", days_requested: "3.0", days_charged: null, status: "pending", note: "Family wedding", decision_note: "", reviewed_by: null, annual_leave_left: "10.5" };
const sick = { ...annual, id: 6, kind: "sick", employee: { id: 4, name: "Priya Shah" }, annual_leave_left: null, note: "" };
vi.mock("../api", () => ({
  api: vi.fn(async (path, opts = {}) => {
    calls.push([path, opts.method || "GET", opts.body]);
    if (path.startsWith("/leave/calendar/")) return [{ id: 9, employee: { id: 4, name: "Priya Shah" }, start_date: "2099-11-10", end_date: "2099-11-10", half_day: "am" }];
    if (path.startsWith("/leave/?")) return [annual, sick];
    return {};
  }),
}));
import LeaveRequests, { dates, dayCount } from "../pages/LeaveRequests.jsx";

describe("leave request text", () => {
  it("writes dates and day counts plainly", () => {
    expect(dates(annual)).toBe("Mon 2 Nov to Wed 4 Nov");
    expect(dates({ start_date: "2099-11-10", end_date: "2099-11-10", half_day: "am" })).toBe("Tue 10 Nov (morning only)");
    expect(dayCount("1.0")).toBe("1 day");
    expect(dayCount("2.5")).toBe("2.5 days");
  });
});

describe("leave approvals", () => {
  beforeEach(() => { calls.length = 0; });

  it("shows what is left and lets annual leave be approved for fewer days", async () => {
    render(<LeaveRequests />);
    expect(await screen.findByText("Sam Patel")).toBeInTheDocument();
    expect(screen.getByText("Annual leave left for this person: 10.5 days")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Approve" })[0]);
    const days = screen.getByLabelText(/Days to take/);
    await userEvent.clear(days);
    await userEvent.type(days, "2");
    await userEvent.click(within(days.closest("form")).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some((c) => c[0] === "/leave/5/approve/" && c[2].days_charged === "2")).toBe(true));
  });

  it("approves other kinds of leave in one click and rejects only with a reason", async () => {
    render(<LeaveRequests />);
    await screen.findByText("Priya Shah", { selector: "strong" });
    const buttons = screen.getAllByRole("button", { name: "Approve" });
    await userEvent.click(buttons[1]);
    await waitFor(() => expect(calls.some((c) => c[0] === "/leave/6/approve/")).toBe(true));
    await userEvent.click(screen.getAllByRole("button", { name: "Reject" })[0]);
    expect(screen.getByLabelText(/Reason/)).toBeRequired();
    await userEvent.type(screen.getByLabelText(/Reason/), "Busy week");
    await userEvent.click(screen.getByRole("button", { name: "Reject this request" }));
    await waitFor(() => expect(calls.some((c) => c[0] === "/leave/5/reject/" && c[2].reason === "Busy week")).toBe(true));
  });

  it("lists who is off", async () => {
    render(<LeaveRequests />);
    expect(await screen.findByText("Tue 10 Nov (morning only)")).toBeInTheDocument();
  });
});
