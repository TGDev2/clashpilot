// node scripts/gen-api.mjs [--check]: regenerate frontend/src/api/schema.d.ts from the backend
// OpenAPI document (read from the app object, no server). With --check nothing is written: the
// exit code is 1 when the committed file differs from what the backend would produce.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { FRONTEND, ROOT, ensureEnvironment, venvPython } from './lib/env.mjs';

const SCHEMA = join(FRONTEND, 'src', 'api', 'schema.d.ts');
const check = process.argv.includes('--check');

function fail(message) {
  console.error(message);
  process.exit(1);
}

try {
  ensureEnvironment();
} catch (error) {
  fail(error instanceof Error ? error.message : String(error));
}

const dump = spawnSync(
  venvPython(),
  [
    '-c',
    'import json, sys; from clashpilot.settings import Settings; from clashpilot.api.app import create_app; ' +
      'sys.stdout.write(json.dumps(create_app(Settings()).openapi(), indent=2, sort_keys=True))',
  ],
  { cwd: ROOT, encoding: 'utf8' },
);
if (dump.status !== 0) {
  fail(`could not read the OpenAPI document from the backend:\n${dump.stderr}`);
}

/**
 * Request bodies declared through `openapi_extra` embed `model_json_schema()` output, whose
 * `$ref`s point at a local `#/$defs/...` that no generator can resolve from the document root.
 * Move those definitions into `components.schemas` and repoint the references.
 */
function hoistLocalDefs(document) {
  const components = (document.components ??= {});
  const schemas = (components.schemas ??= {});
  const walk = (node) => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (node === null || typeof node !== 'object') return;
    if (node.$defs !== undefined) {
      for (const [name, schema] of Object.entries(node.$defs)) {
        const known = schemas[name];
        if (known !== undefined && JSON.stringify(known) !== JSON.stringify(schema)) {
          fail(`two different schemas are both named ${name}: cannot hoist them into components`);
        }
        schemas[name] = schema;
      }
      delete node.$defs;
    }
    for (const [key, value] of Object.entries(node)) {
      if (key === '$ref' && typeof value === 'string' && value.startsWith('#/$defs/')) {
        node[key] = `#/components/schemas/${value.slice('#/$defs/'.length)}`;
      } else {
        walk(value);
      }
    }
  };
  walk(document);
  return document;
}

const document = hoistLocalDefs(JSON.parse(dump.stdout));
const work = mkdtempSync(join(tmpdir(), 'clashpilot-openapi-'));
try {
  const input = join(work, 'openapi.json');
  const output = join(work, 'schema.d.ts');
  writeFileSync(input, JSON.stringify(document, null, 2));
  const generated = spawnSync(`npx openapi-typescript "${input}" -o "${output}"`, {
    cwd: FRONTEND,
    shell: true,
    encoding: 'utf8',
  });
  if (generated.status !== 0) {
    fail(`openapi-typescript failed:\n${generated.stdout}${generated.stderr}`);
  }
  const fresh = readFileSync(output, 'utf8').replace(/\r\n/g, '\n');
  if (check) {
    let current = '';
    try {
      current = readFileSync(SCHEMA, 'utf8').replace(/\r\n/g, '\n');
    } catch {
      // A missing file counts as drift.
    }
    if (current !== fresh) {
      fail(
        'frontend/src/api/schema.d.ts is out of date with the backend API. ' +
          'Run: node scripts/gen-api.mjs',
      );
    }
    console.log('schema.d.ts is up to date');
  } else {
    writeFileSync(SCHEMA, fresh);
    console.log(`wrote ${SCHEMA}`);
  }
} finally {
  rmSync(work, { recursive: true, force: true });
}
