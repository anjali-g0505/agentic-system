import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, mirror the nginx proxy so the code always calls the relative /api path.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: "http://localhost:8000", rewrite: (p) => p.replace(/^\/api/, "") } } },
});
