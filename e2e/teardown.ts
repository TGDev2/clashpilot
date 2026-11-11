import { rmSync } from "node:fs";

/** Removes the throwaway data directory of the run (best effort: the server may still hold it). */
export default function teardown(): void {
  const dir = process.env.CLASHPILOT_E2E_DATA_DIR;
  if (dir?.includes("clashpilot-e2e-")) {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      // The temporary folder is left to the operating system.
    }
  }
}
