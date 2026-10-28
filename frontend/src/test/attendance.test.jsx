import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ukDate, ukInstant } from "../format";

const calls = [];
vi.mock("../api", () => ({
  api: vi.fn(async (path, opts = {}) => {
    calls.push([path, opts.method || "GET", opts.body]);
    if (path.startsWith("/stores/")) return [{ id: 1, name: "Shop A", is_active: true }];
    if (path.startsWith("/employees/")) return [];
    if (path.startsWith("/attendance/?")) {
      return [
        {
          id: 7, employee: { id: 3, name: "Sam Patel" }, store: { id: 1, name: "Shop A" }, shift: 11,
          clock_in_at: "2026-10-27T08:00:00Z", clock_out_at: "2026-10-27T17:30:00Z", worked_minutes: 570, late_minutes: 0,
          flags: [{ code: "late_clock_out", message: "Clocked out more than 4 hours after the shift ended." }],
          source: "device", review_status: "pending", review_note: "",
        },
      ];
    }
    return {};
  }),
}));
import Attendance from "../pages/Attendance.jsx";

describe("UK times", () => {
  it("turns a UK wall-clock time into the right instant either side of the clock change", () => {
    expect(ukInstant("2026-10-27", "09:30")).toBe("2026-10-27T09:30:00.000Z"); // GMT again after 25 Oct
    expect(ukInstant("2026-07-01", "09:30")).toBe("2026-07-01T08:30:00.000Z"); // summer time
    expect(ukInstant("2026-10-24", "23:30")).toBe("2026-10-24T22:30:00.000Z");
  });
  it("reads the UK date of an instant", () => {
    expect(ukDate("2026-07-01T23:30:00Z")).toBe("2026-07-02");
    expect(ukDate("2026-12-01T23:30:00Z")).toBe("2026-12-01");
  });
});

describe("attendance review", () => {
  beforeEach(() => { calls.length = 0; });

  it("shows what needs a decision, with the reasons, and rejects only with a reason", async () => {
    render(<Attendance />);
    expect(await screen.findByText("Sam Patel")).toBeInTheDocument();
    expect(screen.getByText(/Clocked out more than 4 hours/)).toBeInTheDocument();
    expect(screen.getByText("Waiting for a manager")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    const why = screen.getByLabelText(/Why/);
    expect(why).toBeRequired();
    await userEvent.type(why, "Forgot to clock out");
    await userEvent.click(screen.getByRole("button", { name: "Reject this entry" }));
    await waitFor(() => expect(calls.some((c) => c[0] === "/attendance/7/reject/" && c[2].note === "Forgot to clock out")).toBe(true));
  });

  it("approves with one click", async () => {
    render(<Attendance />);
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some((c) => c[0] === "/attendance/7/approve/" && c[1] === "POST")).toBe(true));
    expect(await screen.findByText("Approved.")).toBeInTheDocument();
  });

  it("changes times in UK time and asks for a reason", async () => {
    render(<Attendance />);
    await userEvent.click(await screen.findByRole("button", { name: "Change times" }));
    expect(screen.getByLabelText(/Reason/)).toBeRequired();
    await userEvent.type(screen.getByLabelText(/Reason/), "Left at 5pm");
    await userEvent.click(screen.getByRole("button", { name: "Save the change" }));
    await waitFor(() => {
      const c = calls.find((x) => x[0] === "/attendance/7/" && x[1] === "PATCH");
      expect(c[2].clock_in_at).toBe("2026-10-27T08:00:00.000Z");
      expect(c[2].clock_out_at).toBe("2026-10-27T17:30:00.000Z");
      expect(c[2].reason).toBe("Left at 5pm");
    });
  });
});
