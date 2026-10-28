import { useState } from "react";
import { applyTheme, getTheme, THEMES } from "../theme";

export default function ThemeToggle() {
  const [theme, setTheme] = useState(getTheme);
  function change(e) {
    setTheme(e.target.value);
    applyTheme(e.target.value);
  }
  return (
    <label className="theme">
      <span className="sr">Colour theme</span>
      <select value={theme} onChange={change} aria-label="Colour theme">
        {THEMES.map(([value, name]) => <option key={value} value={value}>{name}</option>)}
      </select>
    </label>
  );
}
