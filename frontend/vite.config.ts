import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In local dev the API runs at :8787 (uv run python -m helperpay.dev_server).
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8787" } },
});
