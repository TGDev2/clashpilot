// node scripts/run-with-settings.mjs [--with-vite] uvicorn <uvicorn args...>
// Starts uvicorn on the host and port of clashpilot Settings (CLASHPILOT_HOST / CLASHPILOT_PORT).
// With --with-vite it also starts the Vite dev server, proxying /api to that backend.
import { spawn, spawnSync } from 'node:child_process';
import { FRONTEND, ROOT, ensureEnvironment, venvPython } from './lib/env.mjs';

const args = process.argv.slice(2);
const withVite = args[0] === '--with-vite';
if (withVite) args.shift();
if (args[0] !== 'uvicorn') {
  console.error('usage: node scripts/run-with-settings.mjs [--with-vite] uvicorn <uvicorn args>');
  process.exit(2);
}
const uvicornArgs = args.slice(1);

try {
  ensureEnvironment();
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}

const python = venvPython();
const probe = spawnSync(
  python,
  ['-c', 'from clashpilot.settings import Settings; s = Settings.from_env(); print(s.host, s.port)'],
  { cwd: ROOT, encoding: 'utf8' },
);
if (probe.status !== 0) {
  console.error(probe.stderr);
  process.exit(1);
}
const [host, port] = probe.stdout.trim().split(' ');

const children = [
  spawn(python, ['-m', 'uvicorn', ...uvicornArgs, '--host', host, '--port', port], {
    cwd: ROOT,
    stdio: 'inherit',
  }),
];
if (withVite) {
  const origin = `http://${host === '0.0.0.0' ? '127.0.0.1' : host}:${port}`;
  children.push(
    spawn('npm run dev', {
      cwd: FRONTEND,
      stdio: 'inherit',
      shell: true,
      env: { ...process.env, CLASHPILOT_API_ORIGIN: origin },
    }),
  );
}

let exiting = false;
const stopAll = (code) => {
  if (exiting) return;
  exiting = true;
  for (const child of children) {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore' });
    } else {
      child.kill('SIGTERM');
    }
  }
  process.exit(code);
};
for (const child of children) child.on('exit', (code) => stopAll(code ?? 0));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => stopAll(0));
