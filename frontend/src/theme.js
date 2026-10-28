// Light, Dark, or follow the device ("system"). The choice is kept in this browser.
const KEY = "hrms.theme";
export const THEMES = [["system", "Match my device"], ["light", "Light"], ["dark", "Dark"]];

export function getTheme() {
  try {
    const saved = window.localStorage.getItem(KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
  else root.removeAttribute("data-theme");
  try {
    if (theme === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, theme);
  } catch {
    /* private browsing: the choice lasts until the page is closed */
  }
}
