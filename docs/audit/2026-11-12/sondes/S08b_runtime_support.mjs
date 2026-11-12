// Sonde S08b (H08) : les versions minimales acceptées des environnements d'exécution ne sont pas
// à moins de six mois de leur fin de support.
// Dates relevées sur https://endoflife.date/api/nodejs.json et /python.json le 2026-11-12.
// Comportement sûr attendu : engines.node et requires-python n'admettent aucune version dont la
// fin de support tombe avant la date de la sonde + 6 mois.
// Exécution en lecture seule contre le dépôt : node <ce fichier> <racine du dépôt> [AAAA-MM-JJ]
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.argv[2] ?? '.';
const today = new Date(process.argv[3] ?? new Date().toISOString().slice(0, 10));
const horizon = new Date(today);
horizon.setMonth(horizon.getMonth() + 6);
const NODE_EOL = { 20: '2026-04-30', 22: '2027-04-30', 24: '2028-04-30', 26: '2029-04-30' };
const PYTHON_EOL = { '3.12': '2028-10-31', '3.13': '2029-10-31', '3.14': '2030-10-31' };

const problems = [];
const engines = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).engines.node;
const nodeMin = Number(/>=\s*(\d+)/.exec(engines)[1]);
if (new Date(NODE_EOL[nodeMin]) < horizon) problems.push(`Node ${nodeMin} (engines "${engines}") ends ${NODE_EOL[nodeMin]}`);
const pyproject = readFileSync(join(root, 'backend', 'pyproject.toml'), 'utf8');
const pyMin = /requires-python = ">=(\d+\.\d+)/.exec(pyproject)[1];
if (new Date(PYTHON_EOL[pyMin]) < horizon) problems.push(`Python ${pyMin} ends ${PYTHON_EOL[pyMin]}`);

if (problems.length) {
  console.error(`S08b FAIL: runtime reaching end of life before ${horizon.toISOString().slice(0, 10)}:\n  ${problems.join('\n  ')}`);
  process.exit(1);
}
console.log('S08b ok: minimum runtimes supported for at least six more months');
