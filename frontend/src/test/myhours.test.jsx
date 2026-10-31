import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = [];
const rec = (id, inAt, outAt, minutes, status, note = "") => ({ id, store: { id: 1, name: "Nisa High Street" }, clock_in_at: inAt, clock_out_at: outAt, worked_minutes: minutes, status, review_note: note });
vi.mock("../api", () => ({
  api: vi.fn(async (path) => {
    calls.push(path);
    return [
      rec(1, "2026-10-26T09:00:00Z", "2026-10-26T17:30:00Z", 480, "ok"),
      rec(2, "2026-10-27T10:00:00Z", "2026-10-27T14:00:00Z", 240, "under_review"),
      rec(3, "2026-10-28T10:00:00Z", "2026-10-28T12:00:00Z", 120, "rejected", "Not on the rota"),
      rec(4, "2026-10-29T08:00:00Z", null, 0, "clocked_in"),
    ];
  }),
}));
import MyHours, { byDay } from "../pages/MyHours.jsx";

describe("my hours", () => {
  beforeEach(() => { calls.length = 0; });

  it("groups by day and leaves rejected time out of the totals", () => {
    const days = byDay([
      rec(2, "2026-10-27T13:00:00Z", "2026-10-27T15:00:00Z", 120, "ok"),
      rec(1, "2026-10-27T09:00:00Z", "2026-10-27T11:00:00Z", 120, "ok"),
      rec(3, "2026-10-28T09:00:00Z", "2026-10-28T11:00:00Z", 120, "rejected"),
    ]);
    expect(days.map((d) => d.date)).toEqual(["2026-10-27", "2026-10-28"]);
    expect(days[0].records.map((r) => r.id)).toEqual([1, 2]);
    expect(days.map((d) => d.minutes)).toEqual([240, 0]);
  });

  it("shows each clock-in with plain wording and a week total", async () => {
    render(<MyHours />);
    expect(await screen.findByText("Waiting for your manager to check")).toBeInTheDocument();
    expect(screen.getByText("Not accepted")).toBeInTheDocument();
    expect(screen.getByText("Not on the rota")).toBeInTheDocument();
    expect(screen.getByText(/still working/)).toBeInTheDocument();
    expect(screen.getByText("12h").closest("p")).toHaveTextContent("12h counted so far");
  });

  it("asks for the week before when going back, and cannot go past this week", async () => {
    render(<MyHours />);
    await screen.findByText("Not accepted");
    expect(screen.getByRole("button", { name: "Later week" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Earlier week" }));
    await waitFor(() => expect(calls.length).toBe(2));
    expect(calls[0]).toMatch(/^\/me\/attendance\/\?start=\d{4}-\d\d-\d\d&end=/);
    expect(calls[1]).not.toBe(calls[0]);
  });
});
