import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vitest/config";

// In development the SPA runs on Vite and forwards /api to the FastAPI backend.
const apiOrigin = process.env.CLASHPILOT_API_ORIGIN ?? "http://127.0.0.1:8000";

/**
 * Under Vitest's jsdom environment Vite rewrites `new URL("../x", import.meta.url)` into a dev
 * server URL, so a test cannot read a file next to itself. Spelling the property as
 * `import.meta["url"]` in test files keeps the real file URL.
 */
const plainImportMetaUrl: Plugin = {
  name: "plain-import-meta-url-in-tests",
  enforce: "pre",
  transform(code, id) {
    if (!process.env.VITEST || !/\.test\.tsx?$/.test(id.split("?")[0] ?? id)) {
      return null;
    }
    return code.includes("import.meta.url")
      ? code.replaceAll("import.meta.url", 'import.meta["url"]')
      : null;
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss(), plainImportMetaUrl],
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
