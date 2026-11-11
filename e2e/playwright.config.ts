import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// The application under test is the built one: FastAPI serves /api and frontend/dist, from the
// backend virtual environment, on a throwaway data directory and a short solver budget.
const root = resolve(import.meta.dirname, "..");
const python =
  process.platform === "win32"
    ? join(root, "backend", ".venv", "Scripts", "python.exe")
    : join(root, "backend", ".venv", "bin", "python");
const port = Number(process.env.CLASHPILOT_E2E_PORT ?? 8765);
// Workers load this file again: the environment hands them the directory of the main process.
const dataDir = (process.env.CLASHPILOT_E2E_DATA_DIR ??= mkdtempSync(join(tmpdir(), "clashpilot-e2e-")));
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  outputDir: join(tmpdir(), "clashpilot-e2e-results"),
  globalTeardown: "./teardown.ts",
  use: {
    baseURL,
    // The service worker only keeps built files; blocking it keeps every run on a fresh build.
    serviceWorkers: "block",
    locale: "fr-FR",
    timezoneId: "Europe/Paris",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // clock_app is the built application with a clock the journey can move forward.
    command: `"${python}" -m uvicorn clock_app:create_app --factory --app-dir e2e --host 127.0.0.1 --port ${port}`,
    cwd: root,
    url: `${baseURL}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      ...(process.env as Record<string, string>),
      CLASHPILOT_DATA_DIR: dataDir,
      CLASHPILOT_SOLVER_MAX_TIME_S: "5",
      CLASHPILOT_HOST: "127.0.0.1",
      CLASHPILOT_PORT: String(port),
      CLASHPILOT_COC_TOKEN: "",
    },
  },
});
