import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the SPA runs on Vite and forwards /api to the FastAPI backend.
const apiOrigin = process.env.CLASHPILOT_API_ORIGIN ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: apiOrigin, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
});
