// Sonde S08 (H08) : aucune vulnérabilité connue dans le code livré, licences compatibles.
// Comportement sûr attendu :
//   - `npm audit --omit=dev` du frontend et de e2e : 0 vulnérabilité ;
//   - `pip-audit` du verrou backend : 0 vulnérabilité (sortie JSON passée en 2e argument) ;
//   - aucune dépendance de production du frontend sous licence copyleft (GPL, AGPL, LGPL, SSPL).
// Exécution dans une copie installée : node <ce fichier> <racine de la copie> <pip_audit.json>
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.argv[2] ?? '.';
const pipAuditJson = process.argv[3];
const problems = [];

for (const dir of ['frontend', 'e2e']) {
  const run = spawnSync('npm', ['audit', '--omit=dev', '--json', '--package-lock-only'], {
    cwd: join(root, dir), encoding: 'utf8', shell: true,
  });
  const total = JSON.parse(run.stdout).metadata.vulnerabilities.total;
  if (total !== 0) problems.push(`${dir}: ${total} production vulnerabilities`);
}

if (pipAuditJson) {
  const report = JSON.parse(readFileSync(pipAuditJson, 'utf8'));
  const vulns = report.dependencies.flatMap((d) => (d.vulns ?? []).map((v) => `${d.name} ${v.id}`));
  if (vulns.length) problems.push(`backend: ${vulns.join(', ')}`);
}

const lock = JSON.parse(readFileSync(join(root, 'frontend', 'package-lock.json'), 'utf8'));
for (const [path, meta] of Object.entries(lock.packages)) {
  if (!path || meta.dev) continue;
  const pkg = join(root, 'frontend', path, 'package.json');
  const license = existsSync(pkg) ? JSON.parse(readFileSync(pkg, 'utf8')).license ?? meta.license : meta.license;
  if (/\b(A?GPL|LGPL|SSPL)/i.test(String(license))) problems.push(`license ${path}: ${license}`);
}

if (problems.length) {
  console.error(`S08 FAIL:\n  ${problems.join('\n  ')}`);
  process.exit(1);
}
console.log('S08 ok: no production vulnerability, no copyleft license in production dependencies');
