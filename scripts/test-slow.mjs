// node scripts/test-slow.mjs [path]: run the slow (benchmark) tests with pytest -m slow, one xdist worker
// per benchmark (5 of them, each single-threaded under the repro profile): in sequence they exceed 10 min.
// pytest exit code 5 (nothing collected) counts as success; any other code is propagated.
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { ROOT, ensureEnvironment, pytestArgs, venvPython } from './lib/env.mjs';

const NO_TESTS_COLLECTED = 5;
const target = resolve(process.argv[2] ? resolve(process.cwd(), process.argv[2]) : `${ROOT}/backend/tests`);

try {
  ensureEnvironment();
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}

const result = spawnSync(venvPython(), pytestArgs(target, ['-m', 'slow', '-n', '5']), {
  stdio: 'inherit',
  cwd: ROOT,
});
process.exit(result.status === NO_TESTS_COLLECTED ? 0 : (result.status ?? 1));
