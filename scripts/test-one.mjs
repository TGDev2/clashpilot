// node scripts/test-one.mjs <path>: run one test file with the right runner.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { extname, relative, resolve } from 'node:path';
import { FRONTEND, ROOT, ensureEnvironment, pytestArgs, quote, venvPython } from './lib/env.mjs';

const target = process.argv[2];
if (!target) {
  console.error('usage: node scripts/test-one.mjs <path-to-test-file>');
  process.exit(2);
}
const file = resolve(process.cwd(), target);
if (!existsSync(file)) {
  console.error(`test file not found: ${target}`);
  process.exit(2);
}

const extension = extname(file);
if (!['.mjs', '.py', '.ts', '.tsx'].includes(extension)) {
  console.error(`unsupported test file type "${extension}": ${target}`);
  process.exit(2);
}

try {
  ensureEnvironment();
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}

let result;
if (extension === '.mjs') {
  // Inside another `node --test` run the inherited context would make the child skip every file.
  const { NODE_TEST_CONTEXT: _inherited, ...env } = process.env;
  result = spawnSync('node', ['--test', file], { stdio: 'inherit', cwd: ROOT, env });
} else if (extension === '.py') {
  result = spawnSync(venvPython(), pytestArgs(file), { stdio: 'inherit', cwd: ROOT });
} else {
  const inFrontend = relative(FRONTEND, file).replaceAll('\\', '/');
  result = spawnSync(`npx vitest run ${quote(inFrontend)}`, {
    stdio: 'inherit',
    cwd: FRONTEND,
    shell: true,
  });
}
process.exit(result.status ?? 1);
