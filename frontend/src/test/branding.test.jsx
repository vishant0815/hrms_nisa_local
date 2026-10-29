import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const calls = [];
vi.mock("../api", () => ({ api: vi.fn(async (path, opts = {}) => { calls.push([path, opts.method || "GET", opts.body]); return {}; }) }));
import { Brand, BrandingProvider } from "../branding.jsx";
import Branding from "../pages/Branding.jsx";

function serve(data) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => data })));
}
afterEach(() => vi.unstubAllGlobals());

describe("the logo", () => {
  it("shows the name until a logo exists, then the logo with the name as its description", async () => {
    serve({ company_name: "Nisa Local", has_logo: false, version: 1 });
    const { unmount } = render(<BrandingProvider><Brand /></BrandingProvider>);
    expect(await screen.findByText("Nisa Local")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    unmount();
    serve({ company_name: "Nisa Local", has_logo: true, version: 42 });
    render(<BrandingProvider><Brand /></BrandingProvider>);
    const img = await screen.findByRole("img", { name: "Nisa Local" });
    expect(img).toHaveAttribute("src", "/api/branding/logo/?v=42"); // the version makes browsers fetch a replaced logo
  });

  it("keeps working and shows the plain name when the server cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    render(<BrandingProvider><Brand /></BrandingProvider>);
    expect(await screen.findByText("Nisa Local")).toBeInTheDocument();
  });

  it("sets the browser tab title from the name", async () => {
    serve({ company_name: "Nisa Hackney", has_logo: false, version: 1 });
    render(<BrandingProvider><Brand /></BrandingProvider>);
    await waitFor(() => expect(document.title).toBe("Nisa Hackney staff portal"));
  });
});

describe("the branding page", () => {
  beforeEach(() => { calls.length = 0; });

  it("uploads the chosen file as a form upload and can remove it", async () => {
    serve({ company_name: "Nisa Local", has_logo: true, version: 3 });
    render(<BrandingProvider><Branding /></BrandingProvider>);
    await screen.findByRole("button", { name: "Replace the logo" });
    const file = new File([new Uint8Array([137, 80, 78, 71])], "logo.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("Choose a logo"), file);
    await userEvent.click(screen.getByRole("button", { name: "Replace the logo" }));
    await waitFor(() => {
      const c = calls.find((x) => x[0] === "/branding/edit/" && x[1] === "POST");
      expect(c[2]).toBeInstanceOf(FormData);
      expect(c[2].get("file").name).toBe("logo.png");
    });
    expect(await screen.findByText("Logo saved. It now shows on every page.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove the logo" }));
    await waitFor(() => expect(calls.some((c) => c[0] === "/branding/edit/" && c[1] === "DELETE")).toBe(true));
  });

  it("shows the server's reason when a file is refused", async () => {
    serve({ company_name: "Nisa Local", has_logo: false, version: 1 });
    const { api } = await import("../api");
    api.mockRejectedValueOnce(new Error("Upload a PNG, JPEG or WebP image. SVG files are not accepted."));
    render(<BrandingProvider><Branding /></BrandingProvider>);
    await userEvent.upload(await screen.findByLabelText("Choose a logo"), new File(["<svg/>"], "logo.svg", { type: "image/svg+xml" }), { applyAccept: false });
    await userEvent.click(screen.getByRole("button", { name: "Upload the logo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("SVG files are not accepted");
  });
});
