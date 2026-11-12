// Sonde S05b (H05) : l'interface doit permettre de saisir un événement du jeu
// (réduction de durée ou de coût sur une file), sans quoi le plan ne peut jamais en tenir compte.
// Comportement sûr attendu : au moins un fichier de frontend/src (hors schéma généré et hors tests)
// lit ou écrit `duration_pct` ou `cost_pct`.
// Exécution en lecture seule contre le dépôt : node <ce fichier> <racine du dépôt>
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = process.argv[2] ?? '.';
const src = join(root, 'frontend', 'src');
const hits = [];
function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.(tsx?|jsx?)$/.test(name) && !/\.test\./.test(name) && !path.includes(join('src', 'api'))) {
      if (/duration_pct|cost_pct/.test(readFileSync(path, 'utf8'))) hits.push(relative(root, path));
    }
  }
}
walk(src);
if (hits.length === 0) {
  console.error('S05b FAIL: no game event input in the UI (no duration_pct/cost_pct outside the generated schema)');
  process.exit(1);
}
console.log(`S05b ok: event input found in ${hits.join(', ')}`);
