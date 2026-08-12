/**
 * make-logo-v4.mjs — 把带白底的品牌 logo 处理成**真透明**的 PNG（去白 + 裁边）。
 * ------------------------------------------------------------------------------
 * 背景：前端左栏顶上的 wordmark 加载 /brand/knowe-logo-v4.png，src/components/ConvList.tsx
 *       和 knowe-components.css 都写死了「这是一张真透明的 PNG」（alpha 通道，去了白底、
 *       裁掉留白）。而设计稿恢复出来的 `Knowe logo设计3.png` 是 **RGB 白底**——直接拷过去
 *       会在浅色界面上顶着一块白矩形。这个脚本负责把白底抠掉。
 *
 * 为什么用纯 Node（只依赖内置 zlib，不引 sharp/canvas）：
 *   package.json 的 dependencies 在禁改清单里，不能为了处理一张图去加原生依赖；
 *   而这张图是 8-bit、非隔行的普通 PNG，用内置 zlib 自己解码/编码完全够用，且跨平台。
 *
 * 算法（与「去白、反预乘羽化边缘、裁掉留白」一致）：
 *   1. 白度 w = min(R,G,B)（255=纯白）。
 *   2. alpha = clip((T_hi - w)/(T_hi - T_lo), 0, 1)：logo 主体(w 小)→不透明，白底(w≈255)→透明，
 *      只有贴近白的**边缘过渡带**做羽化——主体颜色一律保留，不会把中间调的彩色 logo 洗淡。
 *   3. 仅在羽化带内按「压在白底上」反预乘还原直色，消除白边；主体(alpha=1)保持原色不变。
 *   4. 按非透明像素的外接框裁掉四周留白，留 2px 余量。
 *
 * 用法：
 *   node scripts/make-logo-v4.mjs <src.png> <out1.png> [out2.png ...]
 * 退出码：0 成功；非 0 失败（源不存在 / 不是受支持的 PNG 格式）。fix-assets 会捕获失败并回退。
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { inflateSync, deflateSync } from 'node:zlib';

const T_LO = 200; // w ≤ 200 → 主体，完全不透明
const T_HI = 240; // w ≥ 240 → 白底，完全透明；(200,240) 之间羽化
const CROP_MARGIN = 2;
const ALPHA_CROP_THRESHOLD = 8; // 裁边时把 alpha≤8 当成空白

const PNG_SIG = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

// ── CRC32（PNG 块校验，zlib 没直接暴露，自己算一张表） ──
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

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  return pb <= pc ? b : c;
}

/** 解码 8-bit、非隔行、colorType 2(RGB)/6(RGBA) 的 PNG → {width,height,rgba(Uint8Array)} */
function decodePng(buf) {
  if (!buf.subarray(0, 8).equals(PNG_SIG)) throw new Error('不是 PNG（签名不符）');
  let off = 8, ihdr = null;
  const idat = [];
  while (off < buf.length) {
    const len = buf.readUInt32BE(off);
    const type = buf.toString('ascii', off + 4, off + 8);
    const data = buf.subarray(off + 8, off + 8 + len);
    if (type === 'IHDR') {
      ihdr = {
        width: data.readUInt32BE(0),
        height: data.readUInt32BE(4),
        bitDepth: data[8],
        colorType: data[9],
        interlace: data[12],
      };
    } else if (type === 'IDAT') {
      idat.push(Buffer.from(data));
    } else if (type === 'IEND') {
      break;
    }
    off += 12 + len;
  }
  if (!ihdr) throw new Error('缺 IHDR');
  if (ihdr.bitDepth !== 8) throw new Error(`只支持 8-bit（实际 ${ihdr.bitDepth}）`);
  if (ihdr.interlace !== 0) throw new Error('不支持隔行 PNG');
  if (ihdr.colorType !== 2 && ihdr.colorType !== 6) {
    throw new Error(`只支持 colorType 2/6（实际 ${ihdr.colorType}）`);
  }
  const channels = ihdr.colorType === 6 ? 4 : 3;
  const { width, height } = ihdr;
  const bpp = channels;
  const stride = width * bpp;
  const raw = inflateSync(Buffer.concat(idat));
  const expected = height * (stride + 1);
  if (raw.length < expected) throw new Error('IDAT 解压后长度不足');

  const out = new Uint8Array(height * stride); // 反滤波后的原始像素（channels 通道）
  let prevRow = new Uint8Array(stride);
  let p = 0;
  for (let y = 0; y < height; y++) {
    const filter = raw[p++];
    const row = out.subarray(y * stride, y * stride + stride);
    for (let x = 0; x < stride; x++) {
      const rawByte = raw[p++];
      const a = x >= bpp ? row[x - bpp] : 0;
      const b = prevRow[x];
      const c = x >= bpp ? prevRow[x - bpp] : 0;
      let val;
      switch (filter) {
        case 0: val = rawByte; break;
        case 1: val = rawByte + a; break;
        case 2: val = rawByte + b; break;
        case 3: val = rawByte + ((a + b) >> 1); break;
        case 4: val = rawByte + paeth(a, b, c); break;
        default: throw new Error(`未知滤波器 ${filter}`);
      }
      row[x] = val & 0xff;
    }
    prevRow = row;
  }

  // 统一转 RGBA
  const rgba = new Uint8Array(width * height * 4);
  for (let i = 0, s = 0, d = 0; i < width * height; i++, s += bpp, d += 4) {
    rgba[d] = out[s];
    rgba[d + 1] = out[s + 1];
    rgba[d + 2] = out[s + 2];
    rgba[d + 3] = channels === 4 ? out[s + 3] : 255;
  }
  return { width, height, rgba };
}

/** 编码 RGBA(Uint8Array) → PNG（colorType 6，逐行 filter 0） */
function encodePng(width, height, rgba) {
  const stride = width * 4;
  const rawWithFilters = Buffer.alloc(height * (stride + 1));
  for (let y = 0; y < height; y++) {
    rawWithFilters[y * (stride + 1)] = 0; // filter None
    Buffer.from(rgba.buffer, rgba.byteOffset + y * stride, stride)
      .copy(rawWithFilters, y * (stride + 1) + 1);
  }
  const idat = deflateSync(rawWithFilters, { level: 9 });

  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length, 0);
    const typeBuf = Buffer.from(type, 'ascii');
    const body = Buffer.concat([typeBuf, data]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body), 0);
    return Buffer.concat([len, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;

  return Buffer.concat([
    PNG_SIG,
    chunk('IHDR', ihdr),
    chunk('IDAT', idat),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/** 去白：thresholded whiteness key + 羽化带反预乘 */
function dewhite(width, height, rgba) {
  const span = T_HI - T_LO;
  for (let i = 0; i < width * height; i++) {
    const d = i * 4;
    const r = rgba[d], g = rgba[d + 1], b = rgba[d + 2];
    const w = Math.min(r, g, b);
    let alpha = (T_HI - w) / span;
    if (alpha <= 0) { rgba[d] = rgba[d + 1] = rgba[d + 2] = 0; rgba[d + 3] = 0; continue; }
    if (alpha >= 1) { rgba[d + 3] = 255; continue; } // 主体：保留原色
    // 羽化带：反预乘还原直色，消白边
    const inv = 1 / alpha;
    rgba[d] = Math.max(0, Math.min(255, Math.round((r - 255 * (1 - alpha)) * inv)));
    rgba[d + 1] = Math.max(0, Math.min(255, Math.round((g - 255 * (1 - alpha)) * inv)));
    rgba[d + 2] = Math.max(0, Math.min(255, Math.round((b - 255 * (1 - alpha)) * inv)));
    rgba[d + 3] = Math.round(alpha * 255);
  }
}

/** 按 alpha 外接框裁边（留 CROP_MARGIN） */
function cropToContent(width, height, rgba) {
  let minX = width, minY = height, maxX = -1, maxY = -1;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (rgba[(y * width + x) * 4 + 3] > ALPHA_CROP_THRESHOLD) {
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return { width, height, rgba }; // 全透明，别裁
  minX = Math.max(0, minX - CROP_MARGIN); minY = Math.max(0, minY - CROP_MARGIN);
  maxX = Math.min(width - 1, maxX + CROP_MARGIN); maxY = Math.min(height - 1, maxY + CROP_MARGIN);
  const nw = maxX - minX + 1, nh = maxY - minY + 1;
  const out = new Uint8Array(nw * nh * 4);
  for (let y = 0; y < nh; y++) {
    const srcStart = ((minY + y) * width + minX) * 4;
    out.set(rgba.subarray(srcStart, srcStart + nw * 4), y * nw * 4);
  }
  return { width: nw, height: nh, rgba: out };
}

export function processLogo(srcPath) {
  const { width, height, rgba } = decodePng(readFileSync(srcPath));
  dewhite(width, height, rgba);
  const cropped = cropToContent(width, height, rgba);
  return encodePng(cropped.width, cropped.height, cropped.rgba);
}

// ── CLI ──
const isMain = import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  const [src, ...outs] = process.argv.slice(2);
  if (!src || outs.length === 0) {
    console.error('用法：node scripts/make-logo-v4.mjs <src.png> <out1.png> [out2.png ...]');
    process.exit(2);
  }
  try {
    const png = processLogo(src);
    for (const o of outs) writeFileSync(o, png);
    console.log(`[make-logo-v4] 去白+裁边完成 → ${outs.join(', ')}（${png.length} 字节）`);
    process.exit(0);
  } catch (e) {
    console.error(`[make-logo-v4] 处理失败：${e.message}`);
    process.exit(1);
  }
}
