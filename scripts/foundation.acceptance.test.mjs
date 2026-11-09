// Acceptance tests for task E01 (verification foundation and empty application).
// Run with: node --test scripts/foundation.acceptance.test.mjs
// Never runs `npm run verify` nor `test:slow` without a path.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { after, test } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SETUP_TIMEOUT_MS = 300_000;
const STEP_TIMEOUT_MS = 120_000;

const MENTION =
  "This material is unofficial and is not endorsed by Supercell. For more information see Supercell's Fan Content Policy: www.supercell.com/fan-content-policy.";

// ---------------------------------------------------------------- helpers

const tempDirs = [];
const tempFiles = [];

function makeTempDir(prefix) {
  const dir = mkdtempSync(join(tmpdir(), `${prefix}-`));
  tempDirs.push(dir);
  return dir;
}

after(() => {
  for (const file of tempFiles) rmSync(file, { force: true });
  for (const dir of tempDirs) rmSync(dir, { recursive: true, force: true });
});

function run(command, args, { cwd = ROOT, timeout = STEP_TIMEOUT_MS, shell = false, env } = {}) {
  const result = spawnSync(command, args, {
    cwd,
    shell,
    timeout,
    encoding: 'utf8',
    windowsHide: true,
    env: { ...process.env, ...env },
  });
  return { ...result, out: `${result.stdout ?? ''}${result.stderr ?? ''}` };
}

function readText(...parts) {
  return readFileSync(join(ROOT, ...parts), 'utf8').replace(/\r\n/g, '\n');
}

let setupResult;
function ensureSetup() {
  setupResult ??= run('node', ['scripts/setup.mjs'], { timeout: SETUP_TIMEOUT_MS - 20_000 });
  return setupResult;
}

function venvPython() {
  const candidates = [
    join(ROOT, 'backend', '.venv', 'Scripts', 'python.exe'),
    join(ROOT, 'backend', '.venv', 'bin', 'python'),
  ];
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

function preparedPython() {
  const setup = ensureSetup();
  assert.equal(setup.status, 0, `node scripts/setup.mjs failed:\n${setup.out}`);
  const python = venvPython();
  assert.ok(python, 'backend/.venv has no python executable after setup');
  return python;
}

/** Guard against a verify.mjs that would launch itself (recursion) on import. */
function assertVerifyHasMainGuard() {
  const file = join(ROOT, 'scripts', 'verify.mjs');
  assert.ok(existsSync(file), 'scripts/verify.mjs does not exist');
  const source = readFileSync(file, 'utf8');
  assert.match(
    source,
    /import\.meta\.(url|main|filename)|process\.argv\[1\]/,
    'scripts/verify.mjs must only run its main when executed directly',
  );
}

async function loadRunLanes() {
  assertVerifyHasMainGuard();
  const mod = await import(pathToFileURL(join(ROOT, 'scripts', 'verify.mjs')).href);
  assert.equal(typeof mod.runLanes, 'function', 'verify.mjs must export runLanes');
  return mod.runLanes;
}

// ---------------------------------------------------------- package.json

test('package.json defines the root scripts', () => {
  const pkg = JSON.parse(readText('package.json'));
  for (const name of ['setup', 'verify', 'test:slow', 'e2e', 'verify:full', 'dev', 'start']) {
    assert.equal(typeof pkg.scripts?.[name], 'string', `missing script ${name}`);
  }
  assert.match(pkg.scripts.setup, /scripts[\\/]setup\.mjs/);
  assert.match(pkg.scripts.verify, /scripts[\\/]verify\.mjs/);
  assert.match(pkg.scripts.e2e, /scripts[\\/]e2e\.mjs/);
  assert.match(pkg.scripts['test:slow'], /scripts[\\/]test-slow\.mjs/);
  assert.match(pkg.scripts['verify:full'], /verify/);
  assert.match(pkg.scripts['verify:full'], /test[:-]slow/);
  assert.match(pkg.scripts['verify:full'], /e2e/);
});

test('start builds the frontend then runs uvicorn with the app factory', () => {
  const { start } = JSON.parse(readText('package.json')).scripts;
  const build = start.search(/build/);
  const uvicorn = start.indexOf('uvicorn');
  assert.ok(build >= 0, 'start must build the frontend');
  assert.ok(uvicorn >= 0, 'start must run uvicorn');
  assert.ok(build < uvicorn, 'start must build before launching uvicorn');
  assert.ok(start.includes('clashpilot.api.app:create_app'), 'start must use clashpilot.api.app:create_app');
  assert.ok(start.includes('--factory'), 'start must pass --factory to uvicorn');
  assert.ok(
    (start.includes('--host') && start.includes('--port')) ||
      /settings/i.test(start) ||
      /CLASHPILOT_(HOST|PORT)/.test(start),
    'start must use the host and port of Settings',
  );
});

test('dev runs uvicorn and vite, with an /api proxy declared in vite.config.ts', () => {
  const { dev } = JSON.parse(readText('package.json')).scripts;
  assert.ok(dev.includes('uvicorn'), 'dev must run uvicorn');
  assert.ok(/vite/.test(dev), 'dev must run the vite dev server');
  const config = readText('frontend', 'vite.config.ts');
  assert.match(config, /proxy/);
  assert.ok(config.includes('/api'), 'vite.config.ts must proxy /api');
});

// ------------------------------------------------------------- git files

test('.gitignore ignores data, venv, node_modules, dist and .env', () => {
  const ignored = [
    'data/clashpilot.db',
    'backend/.venv/pyvenv.cfg',
    'node_modules/pkg/index.js',
    'frontend/node_modules/pkg/index.js',
    'frontend/dist/index.html',
    '.env',
  ];
  for (const path of ignored) {
    const result = run('git', ['check-ignore', '-q', '--', path]);
    assert.equal(result.status, 0, `${path} must be ignored by git`);
  }
  for (const path of ['README.md', 'backend/src/clashpilot/api/app.py', 'scripts/verify.mjs']) {
    const result = run('git', ['check-ignore', '-q', '--', path]);
    assert.equal(result.status, 1, `${path} must not be ignored by git`);
  }
});

test('.gitattributes normalises line endings to lf', () => {
  assert.match(readText('.gitattributes'), /^\*\s+text=auto\s+eol=lf\s*$/m);
});

// -------------------------------------------------------------- runLanes

test('importing scripts/verify.mjs launches nothing', () => {
  assertVerifyHasMainGuard();
  const code =
    "import('./scripts/verify.mjs').then((m) => { console.log(typeof m.runLanes); process.exit(0); });";
  const result = run('node', ['--input-type=module', '-e', code], { timeout: 30_000 });
  assert.equal(result.status, 0, result.out);
  assert.equal(result.out.trim(), 'function');
});

function makeLaneFixture() {
  const dir = makeTempDir('lane');
  const script = join(dir, 'lane.mjs');
  writeFileSync(
    script,
    [
      "import { writeFileSync } from 'node:fs';",
      'const [mode, value, pidFile] = process.argv.slice(2);',
      "if (pidFile) writeFileSync(pidFile, String(process.pid));",
      "if (mode === 'exit') process.exit(Number(value));",
      "if (mode === 'sleep') setTimeout(() => process.exit(0), Number(value));",
      '',
    ].join('\n'),
  );
  return { dir, script };
}

test('runLanes: every lane exiting 0 gives ok true', async () => {
  const runLanes = await loadRunLanes();
  const { script } = makeLaneFixture();
  const outcome = await runLanes(
    [
      { name: 'a', command: 'node', args: [script, 'exit', '0'] },
      { name: 'b', command: 'node', args: [script, 'exit', '0'] },
    ],
    { timeoutMs: 30_000 },
  );
  assert.equal(outcome.ok, true);
  assert.deepEqual(
    outcome.results.map((r) => [r.name, r.code, r.timedOut]),
    [
      ['a', 0, false],
      ['b', 0, false],
    ],
  );
});

test('runLanes: a non-zero lane gives ok false and keeps its exit code', async () => {
  const runLanes = await loadRunLanes();
  const { script } = makeLaneFixture();
  const outcome = await runLanes(
    [
      { name: 'good', command: 'node', args: [script, 'exit', '0'] },
      { name: 'bad', command: 'node', args: [script, 'exit', '3'] },
    ],
    { timeoutMs: 30_000 },
  );
  assert.equal(outcome.ok, false);
  const good = outcome.results.find((r) => r.name === 'good');
  const bad = outcome.results.find((r) => r.name === 'bad');
  assert.deepEqual([good.code, good.timedOut], [0, false]);
  assert.deepEqual([bad.code, bad.timedOut], [3, false]);
});

test('runLanes: lanes run in parallel', async () => {
  const runLanes = await loadRunLanes();
  const { script } = makeLaneFixture();
  const started = Date.now();
  const outcome = await runLanes(
    [
      { name: 'one', command: 'node', args: [script, 'sleep', '2000'] },
      { name: 'two', command: 'node', args: [script, 'sleep', '2000'] },
    ],
    { timeoutMs: 30_000 },
  );
  const elapsed = Date.now() - started;
  assert.equal(outcome.ok, true);
  assert.ok(elapsed < 3_600, `two 2 s lanes took ${elapsed} ms: they are not parallel`);
});

test('runLanes: a lane over timeoutMs is killed, flagged timedOut, and ok is false', async () => {
  const runLanes = await loadRunLanes();
  const { dir, script } = makeLaneFixture();
  const pidFile = join(dir, 'sleeper.pid');
  const started = Date.now();
  const outcome = await runLanes(
    [
      { name: 'sleeper', command: 'node', args: [script, 'sleep', '60000', pidFile] },
      { name: 'quick', command: 'node', args: [script, 'exit', '0'] },
    ],
    { timeoutMs: 2_500 },
  );
  assert.ok(Date.now() - started < 20_000, 'runLanes did not enforce timeoutMs');
  assert.equal(outcome.ok, false);
  const sleeper = outcome.results.find((r) => r.name === 'sleeper');
  const quick = outcome.results.find((r) => r.name === 'quick');
  assert.equal(sleeper.timedOut, true);
  assert.deepEqual([quick.code, quick.timedOut], [0, false]);

  assert.ok(existsSync(pidFile), 'the sleeping lane never started');
  const pid = Number(readFileSync(pidFile, 'utf8'));
  const isAlive = () => {
    try {
      process.kill(pid, 0);
      return true;
    } catch {
      return false;
    }
  };
  for (let i = 0; i < 50 && isAlive(); i += 1) {
    await new Promise((done) => setTimeout(done, 100));
  }
  assert.equal(isAlive(), false, 'the timed-out lane process is still running');
});

// -------------------------------------------------------------- test-one

test('test-one exits 2 and names the path when the file does not exist', () => {
  const missing = 'no/such/dir/missing-e01.test.mjs';
  const result = run('node', ['scripts/test-one.mjs', missing], { timeout: SETUP_TIMEOUT_MS });
  assert.equal(result.status, 2, result.out);
  assert.ok(result.out.includes(missing) || result.out.includes(missing.replaceAll('/', '\\')), result.out);
});

test('test-one runs .mjs through node --test and propagates the exit code', { timeout: SETUP_TIMEOUT_MS }, () => {
  const dir = makeTempDir('one-mjs');
  const pass = join(dir, 'pass.test.mjs');
  const fail = join(dir, 'fail.test.mjs');
  writeFileSync(pass, "import { test } from 'node:test';\ntest('ok', () => {});\n");
  writeFileSync(fail, "import { test } from 'node:test';\ntest('ko', () => { throw new Error('boom'); });\n");
  const ok = run('node', ['scripts/test-one.mjs', pass], { timeout: SETUP_TIMEOUT_MS });
  assert.equal(ok.status, 0, ok.out);
  const ko = run('node', ['scripts/test-one.mjs', fail], { timeout: SETUP_TIMEOUT_MS });
  assert.equal(ko.status, 1, ko.out);
});

test('test-one runs .py through pytest and propagates the exit code', { timeout: SETUP_TIMEOUT_MS }, () => {
  const dir = makeTempDir('one-py');
  const pass = join(dir, 'test_pass_e01.py');
  const fail = join(dir, 'test_fail_e01.py');
  writeFileSync(pass, 'def test_ok():\n    assert True\n');
  writeFileSync(fail, 'def test_ko():\n    assert False\n');
  const ok = run('node', ['scripts/test-one.mjs', pass], { timeout: SETUP_TIMEOUT_MS });
  assert.equal(ok.status, 0, ok.out);
  const ko = run('node', ['scripts/test-one.mjs', fail], { timeout: SETUP_TIMEOUT_MS });
  assert.equal(ko.status, 1, ko.out);
});

test('test-one runs .ts through Vitest and propagates the exit code', { timeout: SETUP_TIMEOUT_MS }, () => {
  const srcDir = join(ROOT, 'frontend', 'src');
  mkdirSync(srcDir, { recursive: true });
  const stamp = `${process.pid}-${Date.now()}`;
  const passRel = `frontend/src/e01-pass-${stamp}.test.ts`;
  const failRel = `frontend/src/e01-fail-${stamp}.test.ts`;
  tempFiles.push(join(ROOT, passRel), join(ROOT, failRel));
  writeFileSync(
    join(ROOT, passRel),
    "import { expect, test } from 'vitest';\ntest('ok', () => { expect(1).toBe(1); });\n",
  );
  writeFileSync(
    join(ROOT, failRel),
    "import { expect, test } from 'vitest';\ntest('ko', () => { expect(1).toBe(2); });\n",
  );
  const ok = run('node', ['scripts/test-one.mjs', passRel], { timeout: SETUP_TIMEOUT_MS });
  assert.equal(ok.status, 0, ok.out);
  const ko = run('node', ['scripts/test-one.mjs', failRel], { timeout: SETUP_TIMEOUT_MS });
  assert.equal(ko.status, 1, ko.out);
});

// ----------------------------------------------------------------- setup

test('setup builds backend/.venv with Python 3.13 and OR-Tools 9.15.6755', { timeout: SETUP_TIMEOUT_MS }, () => {
  const setup = ensureSetup();
  assert.equal(setup.status, 0, `node scripts/setup.mjs failed:\n${setup.out}`);
  const python = venvPython();
  assert.ok(python, 'backend/.venv has no python executable');
  const probe = run(python, [
    '-c',
    [
      'import sys',
      'import ortools',
      'from ortools.sat.python import cp_model',
      'print(sys.version_info.major, sys.version_info.minor)',
      'print(ortools.__version__)',
      'print(hasattr(cp_model, "CpModel"))',
    ].join('\n'),
  ]);
  assert.equal(probe.status, 0, probe.out);
  const [version, ortools, hasModel] = probe.stdout.trim().split(/\r?\n/);
  assert.equal(version, '3 13');
  assert.equal(ortools, '9.15.6755');
  assert.equal(hasModel, 'True');
});

test('setup installs the frontend dependencies and the backend test plugins', { timeout: SETUP_TIMEOUT_MS }, () => {
  const python = preparedPython();
  assert.ok(existsSync(join(ROOT, 'frontend', 'node_modules')), 'frontend/node_modules is missing');
  const probe = run(python, ['-c', 'import clashpilot, pytest_timeout, xdist, fastapi, uvicorn, httpx']);
  assert.equal(probe.status, 0, probe.out);
});

test('setup is idempotent and fast when nothing changed', { timeout: SETUP_TIMEOUT_MS }, () => {
  preparedPython();
  const started = Date.now();
  const again = run('node', ['scripts/setup.mjs'], { timeout: 120_000 });
  assert.equal(again.status, 0, again.out);
  assert.ok(Date.now() - started < 30_000, `second setup took ${Date.now() - started} ms`);
});

// ------------------------------------------------------------ backend API

let backendRun;
function backendResults() {
  if (backendRun) return backendRun;
  const python = preparedPython();
  const work = makeTempDir('api');
  const dist = join(work, 'dist');
  mkdirSync(join(dist, 'assets'), { recursive: true });
  writeFileSync(join(dist, 'index.html'), '<!doctype html><html><body>INDEX-MARKER-7f3a</body></html>\n');
  writeFileSync(join(dist, 'assets', 'a.js'), "console.log('asset-marker-91c2');\n");
  writeFileSync(join(work, 'secret.txt'), 'SECRET-MARKER-5d1e\n');
  const scriptDir = makeTempDir('apiscript');
  const script = join(scriptDir, 'probe.py');
  writeFileSync(
    script,
    [
      'import json',
      'import sys',
      'import time',
      'import tomllib',
      'from pathlib import Path',
      '',
      'from fastapi.testclient import TestClient',
      '',
      'from clashpilot.api.app import create_app',
      'from clashpilot.settings import Settings',
      '',
      'dist = Path(sys.argv[1])',
      'root = Path(sys.argv[2])',
      'out = {}',
      '',
      '',
      'def snap(r):',
      '    try:',
      '        body = r.json()',
      '    except ValueError:',
      '        body = None',
      '    return {"status": r.status_code, "ctype": r.headers.get("content-type", ""), "text": r.text, "json": body}',
      '',
      '',
      'pyproject = tomllib.loads((root / "backend" / "pyproject.toml").read_text(encoding="utf-8"))',
      'out["pyproject_version"] = pyproject["project"]["version"]',
      '',
      'default_app = create_app()',
      'out["health"] = snap(TestClient(default_app).get("/api/health"))',
      'before = int(time.time())',
      'value = default_app.state.clock()',
      'after = int(time.time())',
      'out["default_clock"] = {"is_int": type(value) is int, "value": value, "before": before, "after": after}',
      'out["custom_clock"] = create_app(clock=lambda: 1234).state.clock()',
      '',
      'settings = Settings()',
      'out["settings"] = {',
      '    "host": settings.host,',
      '    "port": settings.port,',
      '    "frontend_dist": str(settings.frontend_dist).replace(chr(92), "/"),',
      '}',
      '',
      'client = TestClient(create_app(Settings(frontend_dist=dist), clock=lambda: 1))',
      'paths = [',
      '    "/", "/importer", "/a/b/c", "/assets/a.js", "/api/inconnu", "/api/inconnu/profond", "/api/",',
      '    "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/%2e%2e/%2e%2e/secret.txt",',
      '    "/assets/..%5c..%5csecret.txt", "/assets/%252e%252e/%252e%252e/secret.txt",',
      ']',
      'out["served"] = {p: snap(client.get(p)) for p in paths}',
      'out["served_health"] = snap(client.get("/api/health"))',
      '',
      'absent = TestClient(create_app(Settings(frontend_dist=dist / "absent")))',
      'out["absent_root"] = snap(absent.get("/"))',
      'out["absent_health"] = snap(absent.get("/api/health"))',
      '',
      'print(json.dumps(out))',
      '',
    ].join('\n'),
  );
  const result = run(python, ['-X', 'utf8', script, dist, ROOT], { cwd: work });
  assert.equal(result.status, 0, `backend probe failed:\n${result.out}`);
  const lastLine = result.stdout.trim().split(/\r?\n/).at(-1);
  backendRun = { ...JSON.parse(lastLine), indexHtml: readFileSync(join(dist, 'index.html'), 'utf8') };
  return backendRun;
}

test('create_app serves GET /api/health with the pyproject version', { timeout: SETUP_TIMEOUT_MS }, () => {
  const r = backendResults();
  assert.equal(r.health.status, 200);
  assert.deepEqual(r.health.json, { status: 'ok', app: 'ClashPilot', version: r.pyproject_version });
});

test('create_app stores an integer epoch clock in app.state.clock', { timeout: SETUP_TIMEOUT_MS }, () => {
  const r = backendResults();
  assert.equal(r.default_clock.is_int, true, 'default clock must return an int');
  assert.ok(r.default_clock.value >= r.default_clock.before - 1);
  assert.ok(r.default_clock.value <= r.default_clock.after + 1);
  assert.equal(r.custom_clock, 1234);
});

test('Settings defaults to 127.0.0.1:8000 and frontend/dist', { timeout: SETUP_TIMEOUT_MS }, () => {
  const { settings } = backendResults();
  assert.equal(settings.host, '127.0.0.1');
  assert.equal(settings.port, 8000);
  assert.match(settings.frontend_dist, /(^|\/)frontend\/dist\/?$/);
});

test('the SPA fallback serves index.html and assets from frontend_dist', { timeout: SETUP_TIMEOUT_MS }, () => {
  const { served, indexHtml } = backendResults();
  for (const path of ['/', '/importer', '/a/b/c']) {
    assert.equal(served[path].status, 200, `GET ${path}`);
    assert.equal(served[path].text.trim(), indexHtml.trim(), `GET ${path} must return index.html`);
  }
  assert.equal(served['/assets/a.js'].status, 200);
  assert.equal(served['/assets/a.js'].text, "console.log('asset-marker-91c2');\n");
  assert.ok(!served['/assets/a.js'].text.includes('INDEX-MARKER'));
});

test('unknown /api routes answer a 404 JSON not_found, never index.html', { timeout: SETUP_TIMEOUT_MS }, () => {
  const { served, served_health: health } = backendResults();
  for (const path of ['/api/inconnu', '/api/inconnu/profond', '/api/']) {
    assert.equal(served[path].status, 404, `GET ${path}`);
    assert.ok(!served[path].text.includes('INDEX-MARKER'), `GET ${path} must not return index.html`);
    assert.equal(served[path].json?.detail?.code, 'not_found', `GET ${path} must use detail.code`);
  }
  assert.equal(health.status, 200);
  assert.equal(health.json.status, 'ok');
});

test('the SPA fallback refuses paths leaving frontend_dist', { timeout: SETUP_TIMEOUT_MS }, () => {
  const { served } = backendResults();
  const escapes = [
    '/%2e%2e/secret.txt',
    '/..%2fsecret.txt',
    '/assets/%2e%2e/%2e%2e/secret.txt',
    '/assets/..%5c..%5csecret.txt',
    '/assets/%252e%252e/%252e%252e/secret.txt',
  ];
  for (const path of escapes) {
    assert.ok(served[path].status < 500, `GET ${path} must not crash (${served[path].status})`);
    assert.ok(!served[path].text.includes('SECRET-MARKER'), `GET ${path} leaked a file outside frontend_dist`);
  }
});

test('without a frontend_dist folder GET / is a 404 JSON and the API still works', { timeout: SETUP_TIMEOUT_MS }, () => {
  const { absent_root: root, absent_health: health } = backendResults();
  assert.equal(root.status, 404);
  assert.match(root.ctype, /json/);
  assert.notEqual(root.json, null, 'the 404 body must be JSON');
  assert.equal(health.status, 200);
  assert.equal(health.json.status, 'ok');
});

// ---------------------------------------------------------------- pytest

test('pytest declares the slow marker and a 20 s per-test timeout', { timeout: SETUP_TIMEOUT_MS }, () => {
  const python = preparedPython();
  const markers = run(python, ['-m', 'pytest', '-c', 'backend/pyproject.toml', '--markers']);
  assert.equal(markers.status, 0, markers.out);
  assert.match(markers.stdout, /@pytest\.mark\.slow/);

  const dir = makeTempDir('ini');
  const file = join(dir, 'test_ini_e01.py');
  writeFileSync(
    file,
    'def test_timeout_is_20(request):\n    assert float(request.config.getini("timeout")) == 20\n',
  );
  const ini = run(python, ['-m', 'pytest', '-c', 'backend/pyproject.toml', '-p', 'no:cacheprovider', file]);
  assert.equal(ini.status, 0, ini.out);
});

test('test-slow runs pytest -m slow on a path and treats "no test collected" as success', { timeout: SETUP_TIMEOUT_MS }, () => {
  preparedPython();
  const pkg = JSON.parse(readText('package.json'));
  assert.match(pkg.scripts['test:slow'], /scripts[\\/]test-slow\.mjs/);

  const noSlow = makeTempDir('noslow');
  writeFileSync(join(noSlow, 'test_fast_e01.py'), 'def test_fast():\n    assert True\n');
  const none = run('node', ['scripts/test-slow.mjs', noSlow]);
  assert.equal(none.status, 0, none.out);

  const slowOk = makeTempDir('slowok');
  writeFileSync(
    join(slowOk, 'test_slow_ok_e01.py'),
    'import pytest\n\n\n@pytest.mark.slow\ndef test_slow_ok():\n    assert True\n',
  );
  const ok = run('node', ['scripts/test-slow.mjs', slowOk]);
  assert.equal(ok.status, 0, ok.out);
  assert.match(ok.out, /1 passed/);

  const slowFail = makeTempDir('slowfail');
  writeFileSync(
    join(slowFail, 'test_slow_fail_e01.py'),
    'import pytest\n\n\n@pytest.mark.slow\ndef test_slow_ko():\n    assert False\n',
  );
  const ko = run('node', ['scripts/test-slow.mjs', slowFail]);
  assert.notEqual(ko.status, 0, ko.out);
  assert.equal(ko.status, 1, ko.out);
});

// -------------------------------------------------------------- frontend

test('fr.ts carries the exact Fan Content Policy notice', () => {
  const source = readText('frontend', 'src', 'i18n', 'fr.ts').replaceAll("\\'", "'");
  assert.ok(source.includes(MENTION), 'frontend/src/i18n/fr.ts must contain the exact notice');
});

test('App.test.tsx checks the notice and passes under Vitest', { timeout: SETUP_TIMEOUT_MS }, () => {
  const testFile = join(ROOT, 'frontend', 'src', 'App.test.tsx');
  assert.ok(existsSync(testFile), 'frontend/src/App.test.tsx does not exist');
  const source = readFileSync(testFile, 'utf8');
  assert.match(source, /render\(/, 'App.test.tsx must render the App');
  assert.ok(
    /Fan Content Policy/.test(source) || /i18n\/fr/.test(source),
    'App.test.tsx must check the Fan Content Policy notice',
  );

  const setup = ensureSetup();
  assert.equal(setup.status, 0, `node scripts/setup.mjs failed:\n${setup.out}`);
  const result = run('npx vitest run src/App.test.tsx', [], {
    cwd: join(ROOT, 'frontend'),
    shell: true,
    timeout: STEP_TIMEOUT_MS,
  });
  assert.equal(result.status, 0, result.out);
});
