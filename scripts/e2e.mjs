// npm run e2e: build the frontend and run the Playwright journey from e2e/.
// A stub until the e2e/ package exists: it reports that and exits 0.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { FRONTEND, ROOT } from './lib/env.mjs';

const e2eDir = join(ROOT, 'e2e');
if (!existsSync(join(e2eDir, 'playwright.config.ts'))) {
  console.log('e2e: the end-to-end journey does not exist yet (e2e/playwright.config.ts is missing).');
  process.exit(0);
}

const run = (command, cwd) => spawnSync(command, { cwd, stdio: 'inherit', shell: true });
const build = run('npm run build', FRONTEND);
if (build.status !== 0) process.exit(build.status ?? 1);
const tests = run('npx playwright test -c playwright.config.ts --project=chromium', e2eDir);
process.exit(tests.status ?? 1);
