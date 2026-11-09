// Environment preparation shared by setup, verify, test-one and test-slow.
// Idempotent: a witness file holding the hash of the lock files skips reinstalls.
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
export const BACKEND = join(ROOT, 'backend');
export const FRONTEND = join(ROOT, 'frontend');
export const VENV = join(BACKEND, '.venv');
export const PYTEST_CONFIG = 'backend/pyproject.toml';

const WITNESS = join(VENV, '.setup-hash');
const HASHED_FILES = [
  join(BACKEND, 'requirements.lock'),
  join(BACKEND, 'pyproject.toml'),
  join(FRONTEND, 'package-lock.json'),
];

export function venvPython() {
  return process.platform === 'win32'
    ? join(VENV, 'Scripts', 'python.exe')
    : join(VENV, 'bin', 'python');
}

/** Arguments for `python -m pytest` on a file or folder, with the project configuration. */
export function pytestArgs(target, extra = []) {
  return ['-m', 'pytest', '-c', PYTEST_CONFIG, '-p', 'no:cacheprovider', ...extra, target];
}

/** Quote an argument for a shell command line (cmd.exe and POSIX sh both accept double quotes). */
export function quote(value) {
  return /^[\w@%+=:,./\\-]+$/.test(value) ? value : `"${value.replaceAll('"', '\\"')}"`;
}

function lockHash() {
  const hash = createHash('sha256');
  for (const file of HASHED_FILES) {
    hash.update(readFileSync(file).toString('utf8').replace(/\r\n/g, '\n'));
  }
  return hash.digest('hex');
}

function step(label, command, args, options = {}) {
  console.log(`[setup] ${label}`);
  const result = spawnSync(command, args, { stdio: 'inherit', cwd: ROOT, ...options });
  if (result.error || result.status !== 0) {
    throw new Error(`setup step failed: ${label} (${result.error?.message ?? `exit ${result.status}`})`);
  }
}

function pythonLauncher() {
  if (process.platform === 'win32') return ['py', ['-3.13']];
  return ['python3.13', []];
}

export function isEnvironmentReady() {
  return (
    existsSync(venvPython()) &&
    existsSync(join(FRONTEND, 'node_modules')) &&
    existsSync(WITNESS) &&
    readFileSync(WITNESS, 'utf8').trim() === lockHash()
  );
}

/** Create or refresh backend/.venv and frontend/node_modules. Throws on failure. */
export function ensureEnvironment() {
  if (isEnvironmentReady()) return false;
  const expected = lockHash();

  if (!existsSync(venvPython())) {
    const [launcher, launcherArgs] = pythonLauncher();
    step('creating backend/.venv (Python 3.13)', launcher, [...launcherArgs, '-m', 'venv', VENV]);
  }
  const python = venvPython();
  step('installing backend/requirements.lock', python, [
    '-m',
    'pip',
    'install',
    '--disable-pip-version-check',
    '-r',
    join(BACKEND, 'requirements.lock'),
  ]);
  step('installing clashpilot (editable, no dependencies)', python, [
    '-m',
    'pip',
    'install',
    '--disable-pip-version-check',
    '--no-deps',
    '-e',
    BACKEND,
  ]);
  step('installing frontend dependencies (npm ci)', 'npm', ['ci'], { cwd: FRONTEND, shell: true });

  writeFileSync(WITNESS, `${expected}\n`);
  return true;
}
