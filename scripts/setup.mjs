// node scripts/setup.mjs: prepare backend/.venv and frontend/node_modules.
import { ensureEnvironment } from './lib/env.mjs';

try {
  const changed = ensureEnvironment();
  console.log(changed ? '[setup] environment ready' : '[setup] environment already up to date');
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}
