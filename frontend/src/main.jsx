import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@fontsource-variable/bricolage-grotesque/wght.css";
import "@fontsource-variable/instrument-sans/wght.css";
import { applyTheme, getTheme } from "./theme";
import "./styles.css";
import App from "./App.jsx";
import { AuthProvider } from "./auth.jsx";

applyTheme(getTheme()); // before the first paint, so there is no flash of the wrong theme

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
