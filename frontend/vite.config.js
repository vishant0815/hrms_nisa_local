import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the site talks to the two backend services through one address, as the ingress does in production:
// /api/clock/... goes to the clock service (which serves /clock/...), everything else under /api/ goes to Django.
const DJANGO = process.env.HRMS_DJANGO || "http://127.0.0.1:8000";
const CLOCK = process.env.HRMS_CLOCK || "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api/clock": { target: CLOCK, rewrite: (path) => path.replace(/^\/api/, "") },
      "/api": { target: DJANGO },
    },
  },
  test: { environment: "jsdom", setupFiles: "./src/test/setup.js", globals: true },
});
