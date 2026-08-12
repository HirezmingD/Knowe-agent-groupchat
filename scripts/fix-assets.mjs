/**
 * fix-assets.mjs — Knowe v1.0 静态资源路径修复（Node 版，跨平台）
 * ------------------------------------------------------------
 * 与 fix-assets.ps1 等价。项目本身就是 Node/Electron，直接：
 *
 *     node scripts/fix-assets.mjs
 *     node scripts/fix-assets.mjs "D:\\Projects\\knowe\\known_v1.0_React"
 *
 * 幂等，只动 public/ 下资源，不碰代码；结尾自带校验。
 */

import { existsSync, mkdirSync, copyFileSync, renameSync, readdirSync, statSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { processLogo } from './make-logo-v4.mjs';

/** 粗判一个 PNG 是否已经带 alpha 通道（colorType 6/4）——用来避免把处理好的透明 logo 又拿白底覆盖掉。 */
function isTransparentPng(path) {
  try {
    const buf = readFileSync(path);
    // PNG 签名(8) + IHDR 长度(4)+类型(4) 之后：宽4 高4 位深1 颜色类型1 → 颜色类型在偏移 25
    if (buf.length < 26) return false;
    const colorType = buf[25];
    return colorType === 6 || colorType === 4; // 6=RGBA, 4=灰度+alpha
  } catch { return false; }
}

const projectRoot = process.argv[2] || process.cwd();
const PUB   = join(projectRoot, 'public');
const BRAND = join(PUB, 'brand');
const AVA   = join(PUB, 'avatars');
const SUP   = join(PUB, 'supervisors');

const C = { g: '\x1b[90m', ok: '\x1b[32m', warn: '\x1b[33m', err: '\x1b[31m', cy: '\x1b[36m', z: '\x1b[0m' };
const info = (m) => console.log(`  ${C.g}${m}${C.z}`);
const ok   = (m) => console.log(`  ${C.ok}[OK]   ${m}${C.z}`);
const warn = (m) => console.log(`  ${C.warn}[WARN] ${m}${C.z}`);
const fail = (m) => console.log(`  ${C.err}[MISS] ${m}${C.z}`);

console.log(`\n${C.cy}Knowe v1.0 静态资源修复${C.z}`);
console.log(`${C.cy}项目根：${projectRoot}${C.z}`);
console.log('-'.repeat(60));

if (!existsSync(PUB)) {
  console.error(`找不到 public 目录：${PUB}（请在项目根运行，或把根目录作为参数传入）`);
  process.exit(1);
}
mkdirSync(AVA, { recursive: true });

const isFile = (p) => { try { return statSync(p).isFile(); } catch { return false; } };

// ── 1. Logo（去白 + 裁边，不是裸拷贝）────────────────────────────
// 前端加载 /brand/knowe-logo-v4.png 时**认定它是真透明 PNG**（ConvList.tsx + CSS 都这么写）。
// 而设计稿恢复的 `Knowe logo设计3.png` 是 RGB 白底 —— 直接拷会在浅色界面顶一块白矩形。
// 这里用纯 Node 处理器去白裁边；处理失败/源缺失时，**保留**已有的透明版本，绝不用白底覆盖。
console.log('\n[1/5] Logo（去白 + 裁边）');
const logoSrc = join(BRAND, 'Knowe logo设计3.png');
const logoOut = [join(BRAND, 'knowe-logo-v4.png'), join(BRAND, 'knowe-logo.png')];
if (isFile(logoSrc)) {
  try {
    const png = processLogo(logoSrc);
    for (const o of logoOut) writeFileSync(o, png);
    ok('knowe-logo-v4.png / knowe-logo.png（已去白、裁边、透明）');
  } catch (e) {
    // 处理器出意外：只要已有透明版就留着，别回退成白底裸拷贝（那会把 bug 又拷回来）。
    if (logoOut.every((o) => isFile(o) && isTransparentPng(o))) {
      warn(`去白处理失败（${e.message}）——沿用已存在的透明 logo，不覆盖。`);
    } else {
      fail(`去白处理失败且无可用透明版本：${e.message}`);
    }
  }
} else if (logoOut.every((o) => isFile(o) && isTransparentPng(o))) {
  info('源图缺失，但透明 logo 已就位（可能上一次已处理过），跳过。');
} else {
  warn(`源文件缺失：${logoSrc}`);
}

// ── 2. Zinnia ───────────────────────────────────────────────────
console.log('\n[2/5] Zinnia 头像');
const zinniaSrc = join(BRAND, '知知（Zinnia）头像1.png');
if (isFile(zinniaSrc)) {
  copyFileSync(zinniaSrc, join(AVA, 'zinnia.png'));
  ok('avatars/zinnia.png');
} else warn(`源文件缺失：${zinniaSrc}`);

// ── 3. Agent 头像池：avatars/*.png → avatars/agent/*.png ────────
console.log('\n[3/5] Agent 头像池 -> avatars/agent/');
const agentDir = join(AVA, 'agent');
mkdirSync(agentDir, { recursive: true });
let agentMoved = 0;
for (const name of readdirSync(AVA)) {
  if (/^avatar_\d+\.png$/.test(name) && isFile(join(AVA, name))) {
    renameSync(join(AVA, name), join(agentDir, name));
    agentMoved++;
  }
}
agentMoved > 0 ? ok(`移动 ${agentMoved} 个 -> avatars/agent/`)
               : info('扁平层无 avatar_*.png（可能已移动过，跳过）');

// ── 4. Coordinator：supervisors/avatar_XXXX.png → ───────────────
//        avatars/Coordinator/Coordinator_XXXX.png（移动 + 重命名）
console.log('\n[4/5] Coordinator 头像池 -> avatars/Coordinator/');
const coordDir = join(AVA, 'Coordinator');
mkdirSync(coordDir, { recursive: true });
let coordMoved = 0;
if (existsSync(SUP)) {
  for (const name of readdirSync(SUP)) {
    const m = name.match(/^avatar_(\d+)\.png$/);
    if (m && isFile(join(SUP, name))) {
      renameSync(join(SUP, name), join(coordDir, `Coordinator_${m[1]}.png`));
      coordMoved++;
    }
  }
}
coordMoved > 0 ? ok(`移动并重命名 ${coordMoved} 个 -> avatars/Coordinator/Coordinator_XXXX.png`)
               : info('supervisors/ 无 avatar_*.png（可能已移动过，跳过）');

// ── 5. APP 图标 ─────────────────────────────────────────────────
console.log('\n[5/5] APP 图标');
const iconSrc = join(BRAND, 'Knowe图标2.png');
if (isFile(iconSrc)) {
  copyFileSync(iconSrc, join(BRAND, 'app-icon.png'));
  ok('brand/app-icon.png');
} else warn(`源文件缺失：${iconSrc}`);

// ═══ 校验 ═══════════════════════════════════════════════════════
console.log('\n' + '-'.repeat(60));
console.log(`${C.cy}校验结果${C.z}`);
let pass = true;
const checkFile = (rel) => {
  if (isFile(join(PUB, rel))) ok(rel);
  else { fail(rel); pass = false; }
};
const checkCount = (relDir, re, expected) => {
  const dir = join(PUB, relDir);
  if (!existsSync(dir)) { fail(`${relDir}/ (目录不存在)`); pass = false; return; }
  const n = readdirSync(dir).filter((f) => re.test(f) && isFile(join(dir, f))).length;
  if (n >= expected) ok(`${relDir}/ -> ${n} 个`);
  else { fail(`${relDir}/ -> 仅 ${n} 个（期望 >= ${expected}）`); pass = false; }
};

const checkTransparentLogo = (rel) => {
  const p = join(PUB, rel);
  if (!isFile(p)) { fail(rel); pass = false; return; }
  if (isTransparentPng(p)) ok(`${rel}（透明）`);
  else { fail(`${rel}（不是透明 PNG——白底没去掉）`); pass = false; }
};
checkTransparentLogo('brand/knowe-logo.png');
checkTransparentLogo('brand/knowe-logo-v4.png');
checkFile('brand/app-icon.png');
checkFile('avatars/zinnia.png');
checkCount('avatars/agent', /^avatar_\d+\.png$/, 396);
checkCount('avatars/Coordinator', /^Coordinator_\d+\.png$/, 25);

console.log('-'.repeat(60));
if (pass) {
  console.log(`${C.ok}全部就位 ✔  可以重新启动应用验证头像/logo/图标。${C.z}`);
  process.exit(0);
} else {
  console.log(`${C.err}存在缺失项 ✘  请检查标红条目（多为源文件缺失或已被移动）。${C.z}`);
  process.exit(1);
}
