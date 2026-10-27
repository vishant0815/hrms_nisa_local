import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, errorText, resume, signIn, signOut, setSignedOutHandler } from "../api";

const reply = (status, body) => Promise.resolve(new Response(body === undefined ? "" : JSON.stringify(body), { status }));

beforeEach(() => {
  signOut();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("errorText", () => {
  it("reads Django field errors, detail messages and clock service messages", () => {
    expect(errorText({ file: "Upload a PDF." })).toBe("Upload a PDF.");
    expect(errorText({ days: ["Too many.", "Try less."] })).toBe("Too many. Try less.");
    expect(errorText({ detail: "Not found." })).toBe("Not found.");
    expect(errorText({ code: "no_shift", message: "You are not rostered to work right now." })).toBe("You are not rostered to work right now.");
    expect(errorText({ problems: ["EMP-0001 is below the minimum wage."] })).toBe("EMP-0001 is below the minimum wage.");
  });
  it("never shows nothing", () => {
    expect(errorText(null)).toMatch(/try again/i);
    expect(errorText({})).toMatch(/try again/i);
  });
});

describe("signing in", () => {
  it("keeps the refresh token and uses the access token on later calls", async () => {
    const fetch = vi.fn()
      .mockImplementationOnce(() => reply(200, { access: "A1", refresh: "R1" }))
      .mockImplementationOnce(() => reply(200, { ok: true }));
    vi.stubGlobal("fetch", fetch);
    await signIn("sam@example.com", "pw");
    expect(window.localStorage.getItem("hrms.refresh")).toBe("R1");
    await api("/me/rota/");
    expect(fetch.mock.calls[1][1].headers.Authorization).toBe("Bearer A1");
  });

  it("says plainly when the password is wrong or there were too many tries", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => reply(401, { detail: "x" })));
    await expect(signIn("a@b.c", "bad")).rejects.toThrow(/do not match/);
    vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => reply(429, { detail: "x" })));
    await expect(signIn("a@b.c", "bad")).rejects.toThrow(/Too many attempts/);
  });

  it("says so when the server cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fail")));
    await expect(signIn("a@b.c", "pw")).rejects.toThrow(/Cannot reach the server/);
  });
});

describe("staying signed in", () => {
  it("refreshes an expired access token once and retries the request", async () => {
    window.localStorage.setItem("hrms.refresh", "R1");
    const fetch = vi.fn()
      .mockImplementationOnce(() => reply(200, { access: "A1" }))      // resume
      .mockImplementationOnce(() => reply(401, { detail: "expired" })) // call fails
      .mockImplementationOnce(() => reply(200, { access: "A2" }))      // refresh
      .mockImplementationOnce(() => reply(200, [1, 2]));               // retry
    vi.stubGlobal("fetch", fetch);
    expect(await resume()).toBe(true);
    expect(await api("/me/payslips/")).toEqual([1, 2]);
    expect(fetch.mock.calls[3][1].headers.Authorization).toBe("Bearer A2");
  });

  it("signs out when the refresh token is refused", async () => {
    window.localStorage.setItem("hrms.refresh", "dead");
    const out = vi.fn();
    setSignedOutHandler(out);
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => reply(401, { detail: "no" })));
    await expect(api("/me/rota/")).rejects.toThrow();
    expect(out).toHaveBeenCalled();
    expect(window.localStorage.getItem("hrms.refresh")).toBeNull();
  });
});
