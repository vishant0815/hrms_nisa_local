import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ShiftBars, { span } from "../components/ShiftBars.jsx";
import Login from "../pages/Login.jsx";
import { addDays, clock, fullDay, shortDay, greeting, hoursText, isoToday, period, pounds, toDate } from "../format";

vi.mock("../auth.jsx", () => ({ useAuth: () => ({ login: async () => { throw new Error("That email and password do not match. Check them and try again."); } }) }));

describe("dates and money", () => {
  it("keeps plain dates on the right day", () => {
    expect(toDate("2026-10-27").getDate()).toBe(27);
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(isoToday(new Date(2026, 0, 5))).toBe("2026-01-05");
  });
  it("writes days the same way everywhere", () => {
    expect(shortDay("2026-10-27")).toBe("Tue 27 Oct");
    expect(fullDay("2026-10-27")).toBe("Tuesday 27 October");
  });
  it("formats pounds, times, hours and pay periods", () => {
    expect(pounds("367.28")).toBe("£367.28");
    expect(clock("09:00:00")).toBe("09:00");
    expect(hoursText("8.00")).toBe("8 hours");
    expect(hoursText("3.50")).toBe("3.5 hours");
    expect(period([{ date: "2026-10-21" }, { date: "2026-10-19" }, { date: "2026-10-24" }])).toBe("Mon 19 Oct to Sat 24 Oct");
    expect(period([])).toBeNull();
  });
  it("greets by the UK hour", () => {
    expect(greeting(new Date("2026-10-27T08:00:00Z"))).toBe("Good morning");
    expect(greeting(new Date("2026-10-27T13:00:00Z"))).toBe("Good afternoon");
    expect(greeting(new Date("2026-10-27T20:00:00Z"))).toBe("Good evening");
  });
});

describe("shift bars", () => {
  const shift = (id, date, extra = {}) => ({ id, date, start_time: "09:00:00", end_time: "17:00:00", hours: "8.00", store: { name: "Shop A" }, role: "", ...extra });
  it("labels today and tomorrow and marks today", () => {
    render(<ShiftBars today="2026-10-27" shifts={[shift(1, "2026-10-27"), shift(2, "2026-10-28"), shift(3, "2026-10-30", { role: "Till" })]} />);
    expect(screen.getByText("Today")).toBeInTheDocument();
    expect(screen.getByText("Tomorrow")).toBeInTheDocument();
    expect(screen.getByText("Shop A, Till")).toBeInTheDocument();
    expect(document.querySelectorAll(".shiftrow.today")).toHaveLength(1);
    expect(screen.getAllByText("09:00–17:00")).toHaveLength(3);
  });
  it("places a shift on the 6am to midnight track", () => {
    expect(span("06:00:00", "24:00:00")).toEqual({ left: "0%", width: "100%" });
    expect(span("15:00:00", "21:00:00")).toEqual({ left: "50%", width: "33.33333333333333%" });
    expect(span("22:00:00", "06:00:00").width).toBe("11.11111111111111%"); // runs past midnight: stops at the end
    expect(span("04:00:00", "09:00:00").left).toBe("0%"); // before 6am: starts at the left edge
  });
  it("explains an empty week instead of showing nothing", () => {
    render(<ShiftBars shifts={[]} />);
    expect(screen.getByText(/No shifts are published/)).toBeInTheDocument();
  });
});

describe("sign in page", () => {
  it("shows why a sign in failed and lets the person try again", async () => {
    render(<Login />);
    await userEvent.type(screen.getByLabelText("Email"), "sam@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("do not match");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });
});

import { slipState } from "../pages/PayRun.jsx";
describe("payslip state", () => {
  const slip = (extra) => ({ held: [], sick_dates: {}, sick_pay: "0.00", gross_pay: "100.00", deductions_confirmed: true, ...extra });
  it("asks for the thing that is missing, most urgent first", () => {
    expect(slipState(slip({ held: [{}] })).text).toBe("1 held");
    expect(slipState(slip({ sick_dates: { 7: ["2026-10-21"] } })).text).toBe("Enter sick pay");
    expect(slipState(slip({ deductions_confirmed: false })).text).toBe("Check and confirm");
    expect(slipState(slip()).text).toBe("Ready");
    expect(slipState(slip({ gross_pay: "0.00", deductions_confirmed: false })).text).toBe("Ready");
  });
});

import { mondayOf } from "../format";
import { shiftText, SWAP_STATUS } from "../pages/Swaps.jsx";
describe("rota helpers", () => {
  it("finds the Monday of any day, including Sunday and across a month end", () => {
    expect(mondayOf("2026-10-28")).toBe("2026-10-26");
    expect(mondayOf("2026-10-26")).toBe("2026-10-26");
    expect(mondayOf("2026-11-01")).toBe("2026-10-26"); // a Sunday belongs to the week before
    expect(mondayOf("2026-11-03")).toBe("2026-11-02");
  });
  it("describes a shift in one line", () => {
    expect(shiftText({ date: "2026-10-29", start_time: "07:00:00", end_time: "15:00:00", store: { name: "Shop A" } })).toBe("Thu 29 Oct, 07:00–15:00 at Shop A");
  });
  it("has plain words for every swap status", () => {
    for (const s of ["pending_colleague", "pending_manager", "approved", "declined", "rejected", "cancelled"]) expect(SWAP_STATUS[s]).toBeTruthy();
  });
});
