/**
 * make-avatars.mjs — 复原**丢失的头像源池**（Node 版，纯内置模块，跨平台）。
 * ---------------------------------------------------------------------------
 * 背景：v0.2 那次部署事故连带把两个头像源池弄丢了 —— 前端 `src/store/avatar.ts`
 *       （禁改）里写死了两条路径与两个池子大小：
 *
 *         · 成员池 396 张：/avatars/agent/avatar_XXXX.png    （AGENT_AVATAR_COUNT=396）
 *         · 总管池  25 张：/avatars/Coordinator/Coordinator_XXXX.png（COORDINATOR_AVATAR_COUNT=25）
 *
 *       `scripts/fix-assets.mjs`（禁改）只负责**搬运 + 改名 + 校验**：它把扁平的
 *       `public/avatars/avatar_XXXX.png` 收进 `avatars/agent/`，把
 *       `public/supervisors/avatar_XXXX.png` 收进 `avatars/Coordinator/` 并重命名。
 *       但它没有「无中生有」的能力 —— 源池不在，它只会报 MISS。
 *
 *       本脚本就补上那一步：按 avatar.ts 认定的数量，生成两套**确定性、互不撞脸**的
 *       头像，落在 fix-assets 期望的**暂存位置**，随后交给 fix-assets 走完既定管线。
 *
 * 为什么用纯 Node（只依赖内置 zlib，不引 sharp/canvas）：
 *   和 make-logo-v4.mjs 同一条纪律 —— package.json 的 dependencies 在禁改清单里，
 *   不能为处理/生成图片去加原生依赖。这里自绘 RGBA 像素、内置 zlib 编码 PNG，够用且跨平台。
 *
 * 生成规则（要点是「一眼能区分、且成员/总管两族气质不同」）：
 *   · 成员（agent）：彩色对角渐变底 + 左右对称的 identicon 方块纹（GitHub 风），
 *     色相用黄金角散开（137.508°·i），396 张几乎不重样。
 *   · 总管（Coordinator）：更深、更「庄重」的暖色渐变底 + 居中的钻石徽记，
 *     一眼就跟成员那族区分开 —— 花名册里总管排第一个，气质上也该压得住。
 *   · 全部 2× 超采样后盒式降采样，边缘带抗锯齿；CSS 会把方图裁成圆形（.avatar{border-radius:50%}），
 *     所以直接出满幅方图即可，无需自己画圆。
 *
 * 用法：
 *   node scripts/make-avatars.mjs                 # 默认写到项目根 public/ 下
 *   node scripts/make-avatars.mjs <projectRoot>   # 指定项目根
 * 幂等：直接覆盖同名文件。跑完请再跑 `node scripts/fix-assets.mjs` 完成搬运 + 校验。
 */

import { mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { deflateSync } from 'node:zlib';

// ── avatar.ts 的两条铁律（数量必须与之一致；改了这里对不上前端就会 404）──
const AGENT_COUNT = 396;
const COORD_COUNT = 25;

const OUT = 128;   // 最终边长（UI 里最大用到 64px，128 足够清晰）
const SS = 2;      // 超采样倍率
const R = OUT * SS;

// ═══════════════════════════════════════════════════════════════
// PNG 编码（RGBA / colorType 6 / 8-bit / filter 0）—— 与 make-logo-v4 同构
// ═══════════════════════════════════════════════════════════════
const PNG_SIG = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([len, body, crc]);
}
function encodePng(width, height, rgba) {
  const stride = width * 4;
  const raw = Buffer.alloc(height * (stride + 1));
  for (let y = 0; y < height; y++) {
    raw[y * (stride + 1)] = 0; // filter None
    Buffer.from(rgba.buffer, rgba.byteOffset + y * stride, stride)
      .copy(raw, y * (stride + 1) + 1);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([
    PNG_SIG,
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

// ═══════════════════════════════════════════════════════════════
// 颜色 & 哈希小工具
// ═══════════════════════════════════════════════════════════════
/** 确定性 32 位哈希（FNV-1a）——同一 seed 永远同一张脸 */
function hash32(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return h >>> 0;
}
/** HSL(0..360,0..1,0..1) → [r,g,b] 0..255 */
function hsl(h, s, l) {
  h = ((h % 360) + 360) % 360;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let r = 0, g = 0, b = 0;
  if (h < 60) [r, g, b] = [c, x, 0];
  else if (h < 120) [r, g, b] = [x, c, 0];
  else if (h < 180) [r, g, b] = [0, c, x];
  else if (h < 240) [r, g, b] = [0, x, c];
  else if (h < 300) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)];
}

// ═══════════════════════════════════════════════════════════════
// 绘制：先在 R×R 超采样画布上画，再盒式降采样到 OUT×OUT
// ═══════════════════════════════════════════════════════════════
/** 在超采样画布 buf 上按 (x,y)->[r,g,b] 填底色（对角渐变） */
function fillGradient(buf, c1, c2) {
  for (let y = 0; y < R; y++) {
    for (let x = 0; x < R; x++) {
      const t = (x + y) / (2 * (R - 1));           // 0(左上)→1(右下)
      const i = (y * R + x) * 4;
      buf[i] = c1[0] + (c2[0] - c1[0]) * t;
      buf[i + 1] = c1[1] + (c2[1] - c1[1]) * t;
      buf[i + 2] = c1[2] + (c2[2] - c1[2]) * t;
      buf[i + 3] = 255;
    }
  }
}
/** 把 [r,g,b] 以不透明度 a 叠在 buf 的 (x,y) 上（超采样坐标） */
function blendPx(buf, x, y, col, a) {
  if (x < 0 || y < 0 || x >= R || y >= R) return;
  const i = (y * R + x) * 4;
  buf[i] = buf[i] * (1 - a) + col[0] * a;
  buf[i + 1] = buf[i + 1] * (1 - a) + col[1] * a;
  buf[i + 2] = buf[i + 2] * (1 - a) + col[2] * a;
}
/** 实心矩形（超采样坐标，含叠加不透明度） */
function fillRect(buf, x0, y0, w, h, col, a) {
  for (let y = y0; y < y0 + h; y++) for (let x = x0; x < x0 + w; x++) blendPx(buf, x, y, col, a);
}
/** 盒式降采样 R×R → OUT×OUT，返回 Uint8Array(OUT*OUT*4) */
function downsample(buf) {
  const out = new Uint8Array(OUT * OUT * 4);
  const n = SS * SS;
  for (let y = 0; y < OUT; y++) {
    for (let x = 0; x < OUT; x++) {
      let r = 0, g = 0, b = 0, a = 0;
      for (let dy = 0; dy < SS; dy++) {
        for (let dx = 0; dx < SS; dx++) {
          const i = ((y * SS + dy) * R + (x * SS + dx)) * 4;
          r += buf[i]; g += buf[i + 1]; b += buf[i + 2]; a += buf[i + 3];
        }
      }
      const o = (y * OUT + x) * 4;
      out[o] = Math.round(r / n); out[o + 1] = Math.round(g / n);
      out[o + 2] = Math.round(b / n); out[o + 3] = Math.round(a / n);
    }
  }
  return out;
}

/** 成员头像：彩色渐变底 + 左右对称 identicon 方块 */
function agentPng(seed) {
  const buf = new Float64Array(R * R * 4);
  const h = hash32(`agent:${seed}`);
  const hue = (seed * 137.508) % 360;                       // 黄金角散色相
  const c1 = hsl(hue, 0.55, 0.52);
  const c2 = hsl((hue + 34) % 360, 0.60, 0.38);             // 同族深一档，出层次
  fillGradient(buf, c1, c2);

  // 5×5 identicon：左 3 列由哈希位决定，右 2 列镜像；块色用高亮白，留内边距成方块纹
  const grid = 5, area = R * 0.62, pad = (R - area) / 2, cell = area / grid, gap = cell * 0.16;
  const mark = hue > 40 && hue < 200 ? hsl(hue, 0.25, 0.16) : [255, 255, 255]; // 亮底用深墨、暗底用白，保证对比
  let bits = h;
  for (let cx = 0; cx < 3; cx++) {
    for (let cy = 0; cy < grid; cy++) {
      const on = (bits & 1) === 1; bits >>>= 1;
      if (!on) continue;
      for (const col of cx === 2 ? [2] : [cx, grid - 1 - cx]) {       // 中列不镜像
        const x0 = Math.round(pad + col * cell + gap);
        const y0 = Math.round(pad + cy * cell + gap);
        const s = Math.round(cell - 2 * gap);
        fillRect(buf, x0, y0, s, s, mark, 0.92);
      }
    }
  }
  return encodePng(OUT, OUT, downsample(buf));
}

/** 总管头像：庄重暖色渐变底 + 居中钻石徽记（与成员那族一眼区分） */
function coordPng(seed) {
  const buf = new Float64Array(R * R * 4);
  // 暖/庄重色域：琥珀→绯红→靛紫，25 张均匀铺开
  const hue = (18 + (seed - 1) * (312 / Math.max(1, COORD_COUNT - 1))) % 360;
  const c1 = hsl(hue, 0.58, 0.44);
  const c2 = hsl((hue + 26) % 360, 0.64, 0.26);            // 更深，压得住
  fillGradient(buf, c1, c2);

  // 居中钻石（旋转 45° 的方形）：|dx|+|dy|<=1。外描一圈亮金，内填半透白 → 徽记感
  const cx = R / 2, cy = R / 2, rad = R * 0.30;
  const gold = hsl(46, 0.85, 0.62), inner = [255, 255, 255];
  for (let y = 0; y < R; y++) {
    for (let x = 0; x < R; x++) {
      const d = Math.abs(x - cx) / rad + Math.abs(y - cy) / rad;
      if (d <= 1.0) blendPx(buf, x, y, inner, 0.30);        // 内芯
      if (d > 0.82 && d <= 1.0) blendPx(buf, x, y, gold, 0.95); // 金边
    }
  }
  return encodePng(OUT, OUT, downsample(buf));
}

// ═══════════════════════════════════════════════════════════════
// 落盘（暂存位置：交给 fix-assets 搬运 + 改名）
// ═══════════════════════════════════════════════════════════════
const root = process.argv[2] || process.cwd();
const PUB = join(root, 'public');
if (!existsSync(PUB)) { console.error(`找不到 public 目录：${PUB}（请在项目根运行或传入项目根）`); process.exit(1); }

const stageAgent = join(PUB, 'avatars');        // fix-assets 从这里扁平层收 avatar_*.png → avatars/agent/
const stageCoord = join(PUB, 'supervisors');    // fix-assets 从这里收 avatar_*.png → avatars/Coordinator/Coordinator_*.png
mkdirSync(stageAgent, { recursive: true });
mkdirSync(stageCoord, { recursive: true });

const pad4 = (n) => String(n).padStart(4, '0');

process.stdout.write(`生成成员头像 ${AGENT_COUNT} 张 → public/avatars/avatar_XXXX.png `);
for (let i = 1; i <= AGENT_COUNT; i++) {
  writeFileSync(join(stageAgent, `avatar_${pad4(i)}.png`), agentPng(i));
  if (i % 66 === 0) process.stdout.write('.');
}
process.stdout.write(' 完成\n');

process.stdout.write(`生成总管头像 ${COORD_COUNT} 张 → public/supervisors/avatar_XXXX.png `);
for (let i = 1; i <= COORD_COUNT; i++) {
  writeFileSync(join(stageCoord, `avatar_${pad4(i)}.png`), coordPng(i));
}
process.stdout.write('完成\n');

console.log('\n✔ 源池已就位。下一步请运行：node scripts/fix-assets.mjs（搬运 + 改名 + 处理 logo/图标 + 校验）');
