import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { VitePWA, type VitePWAOptions } from "vite-plugin-pwa";
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

/**
 * The installable app (generateSW). The service worker only keeps the built files: it never
 * answers `/api`, neither from a navigation fallback nor from a runtime cache. It needs a secure
 * context (localhost, or HTTPS on a phone); elsewhere the app stays a plain web page.
 */
export const pwaOptions: Partial<VitePWAOptions> = {
  strategies: "generateSW",
  registerType: "autoUpdate",
  manifestFilename: "manifest.webmanifest",
  includeAssets: ["icons/apple-touch-icon.png"],
  manifest: {
    name: "ClashPilot",
    short_name: "ClashPilot",
    description: "Planificateur local d'améliorations",
    lang: "fr",
    display: "standalone",
    start_url: "/",
    scope: "/",
    theme_color: "#0a1114",
    background_color: "#0a1114",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      {
        src: "/icons/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  },
  workbox: {
    globPatterns: ["**/*.{js,css,html,png}"],
    navigateFallback: "index.html",
    navigateFallbackDenylist: [/^\/api(\/|$)/],
    cleanupOutdatedCaches: true,
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss(), VitePWA(pwaOptions), plainImportMetaUrl],
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
