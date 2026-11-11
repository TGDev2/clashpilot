// npm run e2e: run the Playwright journey of e2e/ in Chromium on the built application.
//   node scripts/e2e.mjs            prepare, build, run e2e/journey.spec.ts
//   node scripts/e2e.mjs --list     same preparation, then list the tests without running them
// Order: environment; `npm ci` in e2e when its lock file changed (witness file); the Chromium
// download when the browser is missing; `vite build`; then `playwright test` from e2e/.
// Extra arguments go to `playwright test`. Exit code: Playwright's (non-zero when a scenario fails).
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { FRONTEND, ROOT, ensureEnvironment } from './lib/env.mjs';

const E2E = join(ROOT, 'e2e');
const WITNESS = join(E2E, 'node_modules', '.lock-hash');

const run = (command, cwd, options = {}) =>
  spawnSync(command, { cwd, stdio: 'inherit', shell: true, ...options });

function fail(label, result) {
  console.error(`e2e: ${label} failed (exit ${result.status ?? 'signal'})`);
  process.exit(result.status ?? 1);
}

function lockHash() {
  const lock = readFileSync(join(E2E, 'package-lock.json'), 'utf8').replace(/\r\n/g, '\n');
  return createHash('sha256').update(lock).digest('hex');
}

function browserInstalled() {
  const probe = spawnSync(
    process.execPath,
    [
      '--input-type=module',
      '-e',
      "import { chromium } from '@playwright/test'; import { existsSync } from 'node:fs'; process.exit(existsSync(chromium.executablePath()) ? 0 : 1);",
    ],
    { cwd: E2E, stdio: 'ignore' },
  );
  return probe.status === 0;
}

try {
  ensureEnvironment();
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}

const expected = lockHash();
const installed = existsSync(WITNESS) ? readFileSync(WITNESS, 'utf8').trim() : '';
if (installed !== expected) {
  console.log('[e2e] installing e2e dependencies (npm ci)');
  const install = run('npm ci', E2E);
  if (install.status !== 0) fail('npm ci', install);
  writeFileSync(WITNESS, `${expected}\n`);
}

if (!browserInstalled()) {
  console.log('[e2e] downloading Chromium (playwright install chromium)');
  const download = run('npx playwright install chromium', E2E);
  if (download.status !== 0) fail('playwright install chromium', download);
}

const build = run('npm run build', FRONTEND);
if (build.status !== 0) fail('vite build', build);

const extra = process.argv.slice(2).map((arg) => (/^[\w@%+=:,./\-]+$/.test(arg) ? arg : `"${arg}"`));
const tests = run(
  ['npx playwright test -c playwright.config.ts --project=chromium', ...extra].join(' '),
  E2E,
);
process.exit(tests.status ?? 1);
