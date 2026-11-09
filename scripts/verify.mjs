// npm run verify: prepare the environment, then run four lanes in parallel.
import { spawn, spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BACKEND, FRONTEND, PYTEST_CONFIG, ROOT, ensureEnvironment, quote, venvPython } from './lib/env.mjs';

const LANE_TIMEOUT_MS = 180_000;

function killTree(child) {
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore' });
  } else {
    try {
      process.kill(-child.pid, 'SIGKILL');
    } catch {
      child.kill('SIGKILL');
    }
  }
}

function runLane(lane, timeoutMs) {
  return new Promise((resolveLane) => {
    const started = Date.now();
    let output = '';
    let timedOut = false;
    let settled = false;
    const child = spawn(lane.command, lane.args ?? [], {
      cwd: lane.cwd ?? ROOT,
      shell: true,
      detached: process.platform !== 'win32',
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const collect = (chunk) => {
      output += chunk.toString('utf8');
    };
    child.stdout.on('data', collect);
    child.stderr.on('data', collect);

    const finish = (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolveLane({ name: lane.name, code, timedOut, durationMs: Date.now() - started, output });
    };
    const timer = setTimeout(() => {
      timedOut = true;
      killTree(child);
      // Do not wait for pipes that grandchildren may still hold open.
      setTimeout(() => finish(null), 1_000);
    }, timeoutMs);
    child.on('error', () => finish(1));
    child.on('close', (code) => finish(code));
  });
}

/**
 * Run lanes ({name, command, args, cwd?}) in parallel, each limited to timeoutMs.
 * Resolves with {ok, results: [{name, code, timedOut, durationMs, output}]}.
 */
export async function runLanes(lanes, { timeoutMs }) {
  const results = await Promise.all(lanes.map((lane) => runLane(lane, timeoutMs)));
  return { ok: results.every((r) => r.code === 0 && !r.timedOut), results };
}

function projectLanes() {
  const py = quote(venvPython());
  const nodeTests = readdirSync(join(ROOT, 'scripts'))
    .filter((file) => file.endsWith('.test.mjs'))
    .map((file) => `scripts/${file}`);
  return [
    { name: 'node', command: 'node', args: ['--test', ...nodeTests] },
    {
      name: 'backend-lint',
      command: [
        `${py} -m ruff check .`,
        `${py} -m ruff format --check .`,
        `${py} -m mypy --strict src`,
      ].join(' && '),
      cwd: BACKEND,
    },
    {
      name: 'backend-test',
      command: `${py} -m pytest -c ${PYTEST_CONFIG} -q -m "not slow" -n 4 backend/tests`,
    },
    {
      name: 'frontend',
      command: 'npm run lint && npm run typecheck && npm run test && npm run build',
      cwd: FRONTEND,
    },
  ];
}

function report({ ok, results }) {
  for (const result of results) {
    if (result.code === 0 && !result.timedOut) continue;
    console.log(`\n--- ${result.name} output ---\n${result.output.trimEnd()}`);
  }
  console.log('\nverify summary');
  for (const r of results) {
    const state = r.timedOut ? 'TIMEOUT' : r.code === 0 ? 'ok' : `FAIL (exit ${r.code})`;
    console.log(`  ${r.name.padEnd(13)} ${state.padEnd(16)} ${(r.durationMs / 1000).toFixed(1)}s`);
  }
  console.log(ok ? 'verify: all lanes passed' : 'verify: FAILED');
}

async function main() {
  try {
    ensureEnvironment();
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  }
  const outcome = await runLanes(projectLanes(), { timeoutMs: LANE_TIMEOUT_MS });
  report(outcome);
  process.exit(outcome.ok ? 0 : 1);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  await main();
}
