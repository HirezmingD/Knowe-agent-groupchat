#!/usr/bin/env node
/**
 * make-icon-ico.mjs — 从 app-icon.png 生成多尺寸 icon.ico（阶段二 2.1）
 *
 * 为什么需要：
 *   - 阶段 1.5 WP0 的 icon.ico 是 256×256 单帧，只够 PyInstaller 后端 exe 用。
 *   - NSIS 安装器/卸载器/桌面快捷方式要求多尺寸 ICO（Windows 按显示场景选帧）：
 *     16（任务栏小图标）/ 32（资源管理器）/ 48（大图标）/ 64 / 128 / 256（属性页）。
 *
 * 输入：public/brand/app-icon.png（1147×1151 非正方形 → 居中裁剪为正方形再缩放）
 * 输出：build/icon.ico（多帧）
 *
 * 幂等：输出文件存在且哈希一致时跳过（与 scripts/ 现有工具链一致）。
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import sharp from 'sharp';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'public', 'brand', 'app-icon.png');
const OUT = join(ROOT, 'build', 'icon.ico');
const SIZES = [16, 32, 48, 64, 128, 256];

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

async function main() {
  if (!existsSync(SRC)) {
    console.error(`[make-icon-ico] 源图不存在：${SRC}`);
    console.error('先跑 npm run fix:assets（或确认 public/brand/app-icon.png 在仓库）。');
    process.exit(1);
  }

  const srcHash = sha256(SRC);
  const stampFile = OUT + '.src-hash';
  if (existsSync(OUT) && existsSync(stampFile) && readFileSync(stampFile, 'utf8') === srcHash) {
    console.log(`[make-icon-ico] 幂等跳过：${OUT} 已由当前源图生成（sha256=${srcHash.slice(0, 12)}）`);
    return;
  }

  const meta = await sharp(SRC).metadata();
  const side = Math.min(meta.width, meta.height);

  // 居中裁剪为正方形 → 各尺寸缩略帧
  const frames = [];
  for (const size of SIZES) {
    const buf = await sharp(SRC)
      .resize(side, side, { fit: 'cover', position: 'centre' })
      .resize(size, size, { fit: 'cover' })
      .png()
      .toBuffer();
    frames.push({ size, buf });
  }

  // ICO 容器：16 个字节的头 + 每帧 16 字节目录项 + 帧数据（PNG 压缩体，Vista+ 支持）
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type: icon
  header.writeUInt16LE(frames.length, 4);

  const entries = [];
  const payloads = [];
  let offset = 6 + frames.length * 16;
  for (const { size, buf } of frames) {
    const entry = Buffer.alloc(16);
    entry.writeUInt8(size === 256 ? 0 : size, 0); // width（256 用 0 表示）
    entry.writeUInt8(size === 256 ? 0 : size, 1); // height
    entry.writeUInt8(0, 2); // palette
    entry.writeUInt8(0, 3); // reserved
    entry.writeUInt16LE(1, 4); // planes
    entry.writeUInt16LE(32, 6); // bpp
    entry.writeUInt32LE(buf.length, 8); // size
    entry.writeUInt32LE(offset, 12); // offset
    entries.push(entry);
    payloads.push(buf);
    offset += buf.length;
  }

  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, Buffer.concat([header, ...entries, ...payloads]));
  writeFileSync(stampFile, srcHash);

  const kb = Math.round(statSync(OUT).size / 1024);
  console.log(`[make-icon-ico] 已生成 ${OUT}`);
  console.log(`  尺寸：${SIZES.join('/')}px，${frames.length} 帧，${kb}KB`);
}

main().catch((err) => {
  console.error('[make-icon-ico] 失败：', err.message);
  process.exit(1);
});
