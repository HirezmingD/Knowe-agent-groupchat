import { access, readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname, join, resolve } from 'node:path';

import {
  FuseState,
  FuseV1Options,
  getCurrentFuseWire,
} from '@electron/fuses';

const executable = resolve(process.argv[2] || 'release/win-unpacked/Knowe.exe');
await access(executable);

const current = await getCurrentFuseWire(executable);
const expected = new Map([
  [FuseV1Options.RunAsNode, FuseState.DISABLE],
  [FuseV1Options.EnableCookieEncryption, FuseState.ENABLE],
  [FuseV1Options.EnableNodeOptionsEnvironmentVariable, FuseState.DISABLE],
  [FuseV1Options.EnableNodeCliInspectArguments, FuseState.DISABLE],
  [FuseV1Options.EnableEmbeddedAsarIntegrityValidation, FuseState.ENABLE],
  [FuseV1Options.OnlyLoadAppFromAsar, FuseState.ENABLE],
  [FuseV1Options.LoadBrowserProcessSpecificV8Snapshot, FuseState.DISABLE],
  [FuseV1Options.GrantFileProtocolExtraPrivileges, FuseState.ENABLE],
]);

const mismatches = [];
for (const [option, wanted] of expected) {
  const actual = current[option];
  if (actual !== wanted) {
    mismatches.push(`${FuseV1Options[option]}=${FuseState[actual]} (expected ${FuseState[wanted]})`);
  }
}

if (mismatches.length > 0) {
  throw new Error(`Packaged Electron fuse verification failed: ${mismatches.join(', ')}`);
}

const resources = join(dirname(executable), 'resources');
const packagedExecutables = [
  {
    name: 'wxc-exec.exe',
    source: resolve('node_modules/@microsoft/mxc-sdk/bin/x64/wxc-exec.exe'),
    packaged: join(resources, 'sandbox', 'wxc-exec.exe'),
  },
  {
    name: 'knowe-sandbox-launcher.exe',
    source: resolve('build/native/knowe-sandbox-launcher.exe'),
    packaged: join(resources, 'sandbox', 'knowe-sandbox-launcher.exe'),
  },
];

const sha256 = async (path) => createHash('sha256').update(await readFile(path)).digest('hex');
for (const item of packagedExecutables) {
  await access(item.source);
  await access(item.packaged);
  const [sourceHash, packagedHash] = await Promise.all([
    sha256(item.source),
    sha256(item.packaged),
  ]);
  if (sourceHash !== packagedHash) {
    throw new Error(`${item.name} packaged hash mismatch: ${packagedHash} != ${sourceHash}`);
  }
  console.log(`[security] ${item.name} packaged hash passed: ${packagedHash}`);
}

console.log(`[security] Electron fuse verification passed: ${executable}`);
