#!/usr/bin/env node
/**
 * make-icon-icns.mjs — 从 app-icon.png 生成 macOS icon.icns（macOS R5）
 *
 * 为什么需要：
 *   - macOS .app bundle 需要 .icns 图标（electron-builder 的 mac.icon）。
 *   - 用 macOS 原生 iconutil 从 iconset 生成 .icns，无需额外 npm 包。
 *
 * 输入：public/brand/app-icon.png（1147×1151 非正方形 → 居中裁剪为正方形再缩放）
 * 输出：build/icon.icns
 *
 * 幂等：输出文件存在且哈希一致时跳过（与 make-icon-ico.mjs 同模式）。
 * 依赖：sharp（devDependency）+ macOS 原生 iconutil（仅 macOS 可跑）。
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync, rmSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import sharp from 'sharp';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'public', 'brand', 'app-icon.png');
const OUT = join(ROOT, 'build', 'icon.icns');
const ICONSET_DIR = join(ROOT, 'build', 'icon.iconset');

// macOS iconset 规格：{ 输出名, 尺寸 }，@2x 即 Retina 双倍。
const ICONSET_SPEC = [
  ['icon_16x16.png', 16],
  ['icon_16x16@2x.png', 32],
  ['icon_32x32.png', 32],
  ['icon_32x32@2x.png', 64],
  ['icon_128x128.png', 128],
  ['icon_128x128@2x.png', 256],
  ['icon_256x256.png', 256],
  ['icon_256x256@2x.png', 512],
  ['icon_512x512.png', 512],
  ['icon_512x512@2x.png', 1024],
];

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

async function main() {
  if (process.platform !== 'darwin') {
    console.error('[make-icon-icns] 仅 macOS 可用（依赖 iconutil）。Windows 图标请用 make-icon-ico.mjs。');
    process.exit(1);
  }
  if (!existsSync(SRC)) {
    console.error(`[make-icon-icns] 源图不存在：${SRC}`);
    console.error('先跑 npm run fix:assets（或确认 public/brand/app-icon.png 在仓库）。');
    process.exit(1);
  }

  const srcHash = sha256(SRC);
  const stampFile = OUT + '.src-hash';
  if (existsSync(OUT) && existsSync(stampFile) && readFileSync(stampFile, 'utf8') === srcHash) {
    console.log(`[make-icon-icns] 幂等跳过：${OUT} 已由当前源图生成（sha256=${srcHash.slice(0, 12)}）`);
    return;
  }

  const meta = await sharp(SRC).metadata();
  const side = Math.min(meta.width, meta.height);

  // 居中裁剪为正方形 → 各尺寸 iconset PNG（与 make-icon-ico 同裁剪逻辑）
  mkdirSync(ICONSET_DIR, { recursive: true });
  for (const [name, size] of ICONSET_SPEC) {
    const buf = await sharp(SRC)
      .resize(side, side, { fit: 'cover', position: 'centre' })
      .resize(size, size, { fit: 'cover' })
      .png()
      .toBuffer();
    writeFileSync(join(ICONSET_DIR, name), buf);
  }

  // iconutil 打包 iconset → icns（macOS 原生）
  mkdirSync(dirname(OUT), { recursive: true });
  const r = spawnSync('iconutil', ['-c', 'icns', ICONSET_DIR, '-o', OUT], { encoding: 'utf8' });
  if (r.error || (r.status ?? 0) !== 0) {
    console.error('[make-icon-icns] iconutil 失败：', r.error?.message ?? `exit ${r.status}`);
    if (r.stderr) console.error(r.stderr);
    process.exit(1);
  }

  // 成功后再清理临时 iconset（失败则保留现场便于排障）
  rmSync(ICONSET_DIR, { recursive: true, force: true });
  writeFileSync(stampFile, srcHash);

  const kb = Math.round(statSync(OUT).size / 1024);
  console.log(`[make-icon-icns] 已生成 ${OUT}`);
  console.log(`  尺寸：16~1024px（含 @2x Retina），${kb}KB`);
}

main().catch((err) => {
  console.error('[make-icon-icns] 失败：', err.message);
  process.exit(1);
});
