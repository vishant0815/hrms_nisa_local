// One place that talks to the backend. The short-lived access token lives in memory only; the longer-lived refresh
// token is kept in this browser so staff stay signed in on their phone. It is cleared on sign out, and whenever the
// server refuses it.

const REFRESH_KEY = "hrms.refresh";
let access = null;
let onSignedOut = () => {};

export function setSignedOutHandler(fn) {
  onSignedOut = fn;
}

function store(action, value) {
  try {
    if (action === "get") return window.localStorage.getItem(REFRESH_KEY);
    if (action === "set") window.localStorage.setItem(REFRESH_KEY, value);
    if (action === "clear") window.localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* private browsing: the person simply signs in again next time */
  }
  return null;
}

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

// The two backend services report errors differently: Django sends {"field": "message"} or {"detail": "..."},
// the clock service sends {"code": "...", "message": "..."}. Turn either into one sentence for the screen.
export function errorText(data, fallback = "Something went wrong. Please try again.") {
  if (!data) return fallback;
  if (typeof data === "string") return data;
  if (typeof data.message === "string") return data.message;
  if (typeof data.detail === "string") return data.detail;
  const parts = [];
  for (const value of Object.values(data)) {
    if (Array.isArray(value)) parts.push(...value.map(String));
    else if (typeof value === "string") parts.push(value);
    else if (value && typeof value === "object") parts.push(errorText(value, ""));
  }
  return parts.filter(Boolean).join(" ") || fallback;
}

async function send(path, { method = "GET", body, headers = {} } = {}) {
  const init = { method, headers: { ...headers } };
  if (access) init.headers.Authorization = `Bearer ${access}`;
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  try {
    return await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError("Cannot reach the server. Check your connection and try again.", 0, null);
  }
}

async function refresh() {
  const token = store("get");
  if (!token) return false;
  const response = await send("/auth/refresh/", { method: "POST", body: { refresh: token } }).catch(() => null);
  if (!response || !response.ok) {
    if (response && response.status === 401) store("clear");
    return false;
  }
  const data = await response.json();
  access = data.access;
  if (data.refresh) store("set", data.refresh);
  return true;
}

async function parse(response) {
  const text = await response.text();
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return text;
  }
}

export async function api(path, options = {}) {
  let response = await send(path, options);
  if (response.status === 401 && !path.startsWith("/auth/")) {
    if (await refresh()) response = await send(path, options);
    else {
      access = null;
      onSignedOut();
    }
  }
  if (options.blob && response.ok) return response.blob();
  const data = await parse(response);
  if (!response.ok) throw new ApiError(errorText(data), response.status, data);
  return data;
}

export async function signIn(email, password) {
  const response = await send("/auth/login/", { method: "POST", body: { email, password } });
  const data = await parse(response);
  if (!response.ok) {
    const message =
      response.status === 401
        ? "That email and password do not match. Check them and try again."
        : response.status === 429
          ? "Too many attempts. Wait a few minutes and try again."
          : errorText(data);
    throw new ApiError(message, response.status, data);
  }
  access = data.access;
  store("set", data.refresh);
}

// Called once when the page opens: is there a saved sign-in that still works?
export async function resume() {
  return refresh();
}

export function signOut() {
  access = null;
  store("clear");
}
