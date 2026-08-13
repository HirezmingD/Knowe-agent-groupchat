/** Startup/build preflight: dependencies, required runtime sources, and Python import smoke. */
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { delimiter, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const [nodeMajor = 0, nodeMinor = 0] = process.versions.node.split('.').map(Number);
if (nodeMajor < 22 || (nodeMajor === 22 && nodeMinor < 12)) {
  console.error(`❌ preflight: Node.js ${process.versions.node} 太旧；需要 22.12.0 或更高版本。`);
  process.exit(1);
}

const required = [
  ['package.json', resolve(projectRoot, 'package.json')],
  ['package-lock.json', resolve(projectRoot, 'package-lock.json')],
  ['node_modules', resolve(projectRoot, 'node_modules')],
  ['build/installer.nsh', resolve(projectRoot, 'build', 'installer.nsh')],
  ['@microsoft/mxc-sdk x64 wxc-exec.exe', resolve(projectRoot, 'node_modules', '@microsoft', 'mxc-sdk', 'bin', 'x64', 'wxc-exec.exe')],
  ['@microsoft/mxc-sdk LICENSE.md', resolve(projectRoot, 'node_modules', '@microsoft', 'mxc-sdk', 'LICENSE.md')],
  ['native sandbox launcher source', resolve(projectRoot, 'native', 'knowe-sandbox-launcher', 'src', 'main.rs')],
  ['native sandbox launcher lockfile', resolve(projectRoot, 'native', 'knowe-sandbox-launcher', 'Cargo.lock')],
  ['built knowe-sandbox-launcher.exe', resolve(projectRoot, 'build', 'native', 'knowe-sandbox-launcher.exe')],
  ['backend/workspace_layout.py', resolve(projectRoot, 'backend/workspace_layout.py')],
  ['backend/server.py', resolve(projectRoot, 'backend/server.py')],
  ['backend/engine.py', resolve(projectRoot, 'backend/engine.py')],
  ['electron/main.ts', resolve(projectRoot, 'electron/main.ts')],
];
const missing = required.filter(([, path]) => !existsSync(path));
if (missing.length) {
  for (const [name, path] of missing) console.error(`❌ preflight: 缺少 ${name}（期望位置：${path}）`);
  if (missing.some(([name]) => name === 'node_modules')) console.error('   → 先跑 `npm ci` 再启动。');
  if (missing.some(([name]) => name === 'built knowe-sandbox-launcher.exe')) {
    console.error('   → 先跑 `npm run sandbox:build` 构建强制 Job Object 启动器。');
  }
  process.exit(1);
}

const repositoryPython = process.platform === 'win32'
  ? resolve(projectRoot, '.venv', 'Scripts', 'python.exe')
  : resolve(projectRoot, '.venv', 'bin', 'python');
const candidates = [
  process.env.KNOWE_DEV_PYTHON,
  repositoryPython,
  process.platform === 'win32' ? 'python' : 'python3',
  'python',
]
  .filter((value, index, all) => value && all.indexOf(value) === index);
let smoke = null;
for (const executable of candidates) {
  const result = spawnSync(executable, ['-c', 'import backend.server; import backend.engine'], {
    cwd: projectRoot,
    env: {
      ...process.env,
      PYTHONPATH: [resolve(projectRoot, 'backend'), process.env.PYTHONPATH].filter(Boolean).join(delimiter),
      KNOWE_RUNTIME_TOKEN: process.env.KNOWE_RUNTIME_TOKEN || '0'.repeat(64),
    },
    encoding: 'utf8',
  });
  if (result.error?.code === 'ENOENT') continue;
  smoke = { executable, result };
  break;
}
if (!smoke) {
  console.error('❌ preflight: 找不到 Python；请用 KNOWE_DEV_PYTHON 指向 Python 3.11。');
  process.exit(1);
}
if (smoke.result.status !== 0) {
  console.error(`❌ preflight: Python import smoke 失败（${smoke.executable}）`);
  process.stderr.write(smoke.result.stderr || smoke.result.stdout || 'unknown error\n');
  process.exit(smoke.result.status || 1);
}

console.log('✅ preflight OK');
