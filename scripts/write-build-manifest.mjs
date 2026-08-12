/** [v1.0.13][R5] Emit reproducible build provenance and source/bundle hashes. */
import { createHash, randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}
function gitCommit() {
  try {
    return execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  } catch {
    return 'unknown';
  }
}

const root = process.cwd();
const packageJson = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'));
const output = resolve(root, 'out', 'build-manifest.json');
const manifest = {
  schema: 'knowe-build-manifest-v1',
  app_version: String(packageJson.version ?? ''),
  git_commit: gitCommit(),
  build_id: randomUUID(),
  built_at: new Date().toISOString(),
  node_version: process.version,
  package_manager_lock_hash: sha256(resolve(root, 'package-lock.json')),
  main_source_hash: sha256(resolve(root, 'electron', 'main.ts')),
  preview_policy_source_hash: sha256(resolve(root, 'electron', 'previewNavigation.ts')),
  main_bundle_hash: sha256(resolve(root, 'out', 'main', 'index.js')),
};
mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
console.log(`build manifest: ${output}`);
