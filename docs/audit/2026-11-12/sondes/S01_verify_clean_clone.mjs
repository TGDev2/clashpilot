// Sonde S01 (H01) : la commande unique de vérification passe sur un clone propre.
// Comportement sûr attendu : `npm run verify` sort en 0 et aucun fichier temporaire `e01-*`
// écrit par la voie `node` n'est ramassé par Vitest dans la voie `frontend`.
// Exécution dans une copie temporaire installée (npm run setup) : node <ce fichier> <racine de la copie>
import { spawnSync } from 'node:child_process';

const root = process.argv[2] ?? '.';
const run = spawnSync('npm', ['run', 'verify'], { cwd: root, encoding: 'utf8', shell: true, maxBuffer: 64 * 1024 * 1024 });
const output = `${run.stdout}\n${run.stderr}`;
const stray = output.split('\n').filter((line) => /FAIL\s+src\/e01-/.test(line));
if (run.status !== 0 || stray.length > 0) {
  console.error(`S01 FAIL: verify exit ${run.status}; stray e01 files picked up by Vitest: ${stray.length}`);
  for (const line of stray) console.error(`  ${line.trim()}`);
  process.exit(1);
}
console.log('S01 ok: verify passed on a clean copy');
