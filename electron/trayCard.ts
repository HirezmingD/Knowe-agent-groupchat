/**
 * trayCard.ts — 托盘新消息悬停卡片。
 *
 * 鼠标移到「正在闪烁」的托盘图标上时，创建一个极简 BrowserWindow 显示未读明细；
 * 鼠标移出、点击某一行、或应用退出时销毁。
 *
 * [v1.0.20.2] UI 按权威参考重做（Logs/v1.0.20.2 message_hovercard/【ui权威参考】hovercard_no_arrow.html）：
 *   403px 宽卡片、圆角 20px、淡蓝投影；每行 = 发送者头像 + 「项目名 · 姓名(职位)」
 *   + 右上时间 + 单行消息预览；底部「忽略全部」按钮（点了只是停闪，不清未读）。
 *   行高比参考收窄（76px），预览只排一行省略号。深浅两套令牌照旧。
 *
 * ⚠ 视觉铁律：卡片是独立 BrowserWindow，加载的是内联 data: HTML，不会自动继承主
 *   渲染进程的 knowe-tokens.css / knowe-components.css——所以下面把要用到的那一小撮
 *   令牌（浅色 + 深色两套）原样内联进 <style>，深色套用哪一份由 nativeTheme.
 *   shouldUseDarkColors 决定（这是主进程能拿到的、离用户系统偏好最近的信号）。
 */

import { BrowserWindow, screen, nativeTheme, app, type Rectangle } from 'electron';
import { join, dirname } from 'node:path';
import { existsSync, appendFileSync, mkdirSync, readFileSync } from 'node:fs';
import type { UnreadDetail } from '../src/shared/bridge';

/** 日志根目录：打包版 → 安装目录\Logs；开发版 → 项目根\Logs（与 main.ts 的 INSTALL_ROOT 分流一致）。 */
const LOG_ROOT = join(app.isPackaged ? dirname(process.execPath) : join(__dirname, '..', '..'), 'Logs');

/** 按天翻篇的文件名：backend_YYYYMMDD.log（与 main.ts 统一日志同目录同命名；旧文件由 main.ts 启动时统一清理）。 */
function logFileName(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `backend_${y}${m}${d}.log`;
}

/** 写盘失败不静默：console.error 可见。 */
function log(msg: string): void {
  const ts = new Date().toISOString().slice(11, 23);
  const file = join(LOG_ROOT, logFileName(new Date()));
  try {
    mkdirSync(LOG_ROOT, { recursive: true });
    appendFileSync(file, `[${ts}] [trayCard] ${msg}\n`);
  } catch (error) {
    console.error(`[trayCard] 日志写入失败（${file}）：${error instanceof Error ? error.message : String(error)}`);
  }
}

// ── 卡片尺寸（v1.0.20.2 按权威参考：403px 宽卡） ──
const CARD_WIDTH = 403;
const ITEM_HEIGHT = 76;    // 头像 44 + 上下 16 呼吸（参考 109/100 收窄）
const FOOTER_HEIGHT = 60;  // 「忽略全部」按钮（参考 60px）
const EMPTY_HEIGHT = 56;
const CARD_GAP = 0; // 紧贴托盘图标
const CLOSE_DELAY_MS = 800; // 鼠标离开后等这么久才关——足够人移动到卡片上且 JS 监听器就绪

/**
 * 阴影出血边：
 *   上/左/右 24px 用于投影晕开。
 *   下方仅 4px——box-shadow 向下延伸极小，不留冗余空间。
 */
const BLEED_TOP = 24;
const BLEED_BOTTOM = 4;
const BLEED_SIDE = 24;

let cardWindow: BrowserWindow | null = null;
let closeTimer: ReturnType<typeof setTimeout> | null = null;

/** 卡片当前是否开着（main.ts 用它判断「明细变了要不要就地刷新」）。 */
export function isTrayCardOpen(): boolean {
  return !!cardWindow && !cardWindow.isDestroyed();
}

/** 延迟关闭卡片——给人从托盘图标移动到卡片上的时间。如果在延迟期间调了 cancel 就不关。 */
export function scheduleDestroyTrayCard(): void {
  cancelDestroyTrayCard();
  log('scheduleDestroy — 800ms后关闭');
  closeTimer = setTimeout(() => {
    log('⏰ 定时器触发 -> destroy');
    destroyTrayCard();
  }, CLOSE_DELAY_MS);
}

/** 取消正在排队的关闭（鼠标移到了卡片上、或明细变化需要重建卡片时）。 */
export function cancelDestroyTrayCard(): void {
  if (closeTimer) {
    log('cancelDestroy — 定时器已取消');
    clearTimeout(closeTimer);
    closeTimer = null;
  }
}

/** 可见卡片高度（不含阴影出血边）。有明细 = 行 × 行高 + 底部按钮；无明细 = 空态。 */
function visibleCardHeight(itemCount: number): number {
  if (itemCount === 0) return EMPTY_HEIGHT;
  return itemCount * ITEM_HEIGHT + FOOTER_HEIGHT;
}

/** 根据托盘图标坐标算外层窗口应在的位置：始终优先图标正上方、水平居中。放不下才放下方。 */
function calcWindowBounds(trayBounds: Rectangle, itemCount: number): Rectangle {
  const display = screen.getDisplayMatching(trayBounds);
  const { x: sx, y: sy, width: sw, height: sh } = display.workArea;

  const visibleH = visibleCardHeight(itemCount);
  const winW = CARD_WIDTH + BLEED_SIDE * 2;
  const winH = visibleH + BLEED_TOP + BLEED_BOTTOM;

  // 始终优先：图标正上方、水平居中。
  let cardX = trayBounds.x + trayBounds.width / 2 - CARD_WIDTH / 2;
  let cardY = trayBounds.y - visibleH - CARD_GAP;

  if (cardY < sy) {
    // 上方放不下 → 改为下方
    cardY = trayBounds.y + trayBounds.height + CARD_GAP;
  }

  let winX = cardX - BLEED_SIDE;
  let winY = cardY - BLEED_TOP;
  winX = Math.max(sx, Math.min(winX, sx + sw - winW));
  winY = Math.max(sy, Math.min(winY, sy + sh - winH));

  return {
    x: Math.round(winX), y: Math.round(winY),
    width: Math.round(winW), height: Math.round(winH),
  };
}

/** 找到真正存在的托盘卡片 preload 产物；照 main.ts resolvePreloadPath() 的探测方式抄一遍。 */
function resolveTrayCardPreloadPath(): string {
  const dir = join(__dirname, '..', 'preload');
  const candidates = ['trayCard.mjs', 'trayCard.js', 'trayCard.cjs'];
  for (const name of candidates) {
    const p = join(dir, name);
    if (existsSync(p)) return p;
  }
  const fallback = join(dir, 'trayCard.js');
  console.error(
    `[trayCard] ⚠ 在 ${dir} 下没找到托盘卡片 preload 产物（试过 ${candidates.join(' / ')}）。` +
    `先跑一次 electron-vite build/dev；现回退到 ${fallback}。`,
  );
  return fallback;
}

/**
 * 头像 → 卡片窗口能显示的数据。
 *
 * [v1.0.25.3] 实锤：托盘卡片是 data: URL 窗口（opaque origin），Chromium 禁止它加载
 * 任何 file:// 子资源（实测 naturalWidth=0，真实磁盘与 asar 内 file:// 一律失败；
 * 此前 prod 用 pathToFileURL 方案从没在打包版验证过——renderer 拼 origin 时代打包版
 * 直接是 file://./ 无效 URL，dev 下 http 能加载掩盖了问题）。
 * 所以不走 URL 加载：直接读头像文件 → base64 data: URL 内联进卡片数据。
 * data: 页面加载 data: 图片天然同源允许。卡片最多几行未读，44px 小图 base64 几 KB，无性能负担。
 *
 * 输入形态（前端传的）：
 *   './avatars/agent/avatar_0001.png'（相对路径，主力）
 *   'http(s)://...'（外链/老数据，data: 页面可加载 http 图片，原样返回）
 *   'data:image/...'（已是内联，原样返回）
 *   ''（无头像）
 */
function resolveAvatarUrl(relOrAbs: string): string {
  if (!relOrAbs) return '';
  if (/^data:image\//i.test(relOrAbs)) return relOrAbs;
  if (/^https?:\/\//i.test(relOrAbs)) return relOrAbs;
  // 相对路径 → 磁盘文件 → base64
  const rel = relOrAbs.replace(/^\.?\//, ''); // './avatars/x.png' → 'avatars/x.png'
  // dev：public/ 在项目根（vite dev server 直接服务它，out/renderer 无拷贝）
  // 打包：electron-vite 把 public/ 内容拷进 out/renderer 根 → asar 内 out/renderer/avatars/...
  const base = app.isPackaged
    ? join(__dirname, '..', 'renderer')
    : join(__dirname, '..', '..', 'public');
  const p = join(base, rel);
  try {
    const buf = readFileSync(p);
    return `data:image/png;base64,${buf.toString('base64')}`;
  } catch (error) {
    log(`头像读取失败（${p}）：${error instanceof Error ? error.message : String(error)}`);
    return '';
  }
}

/**
 * 卡片内联 HTML/CSS——Knowe 令牌子集（浅色 + 深色），不引用外部文件。
 * [v1.0.20.2] 视觉照权威参考 hovercard_no_arrow.html：403px 宽、圆角 20px、
 * 淡蓝投影；消息行 = 圆形头像 + 「项目名 · 姓名(职位)」+ 右上时间 + 单行预览；
 * 底部「忽略全部」。深浅两套令牌（深色 = Knowe 深色纸墨 + 参考蓝调提亮）。
 * 所有用户内容一律 textContent 写入（render 里做），不拼 HTML 字符串，天然免疫 XSS。
 */
function buildHtml(dark: boolean): string {
  return `<!DOCTYPE html>
<html class="${dark ? 'dark' : ''}">
<head>
<meta charset="utf-8">
<style>
  :root {
    --surface:#FFFFFF; --surface-hover:rgba(247,250,255,.72); --surface-active:#edf5ff;
    --ink:#111111; --ink-2:#898d90; --ink-3:#a8abad;
    --line:#e5e5f1; --link:#2878c8; --link-hover-bg:#f6faff;
    --avatar-ring:#FFFFFF;
    --shadow:0 0 0 1px rgba(104,140,175,.12),0 2px 10px rgba(105,145,184,.08),0 12px 32px rgba(105,145,184,.18);
    --font-sans:"SF Pro Text","Inter","PingFang SC","Noto Sans SC",system-ui,-apple-system,"Segoe UI",sans-serif;
  }
  html.dark {
    --surface:#232220; --surface-hover:rgba(42,44,57,.55); --surface-active:#2A2C39;
    --ink:#ECEAE4; --ink-2:#A6A39B; --ink-3:#6F6D66;
    --line:#312F2B; --link:#6FB1E8; --link-hover-bg:#2A2C39;
    --avatar-ring:#232220;
    --shadow:0 0 0 1px rgba(255,255,255,.07),0 2px 10px rgba(0,0,0,.25),0 12px 32px rgba(0,0,0,.45);
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body { width:100%; height:100%; overflow:hidden; }
  body {
    font-family:var(--font-sans);
    -webkit-app-region:no-drag;
    padding:${BLEED_TOP}px ${BLEED_SIDE}px ${BLEED_BOTTOM}px ${BLEED_SIDE}px;
  }
  .card {
    width:100%; height:100%;
    display:flex; flex-direction:column;
    background:var(--surface);
    border-radius:20px;
    box-shadow:var(--shadow);
    overflow:hidden;
  }
  .row {
    display:flex; align-items:center; gap:16px;
    height:${ITEM_HEIGHT}px; padding:0 24px;
    cursor:pointer;
    transition:background-color 120ms ease;
  }
  .row + .row { border-top:1px solid var(--line); }
  .row:hover, .row:focus-visible { background:var(--surface-hover); outline:none; }
  .row:active { background:var(--surface-active); }
  .avatar {
    width:44px; height:44px; border-radius:50%;
    object-fit:cover; flex-shrink:0;
    border:1px solid var(--line);
    background:var(--surface-hover);
    user-select:none; -webkit-user-drag:none;
  }
  .body { flex:1; min-width:0; display:flex; flex-direction:column; justify-content:center; gap:5px; }
  .sender-line { display:flex; align-items:baseline; justify-content:space-between; gap:12px; }
  .sender {
    flex:1; min-width:0;
    font-size:12px; line-height:18px; font-weight:400; color:var(--ink-2);
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .time {
    flex-shrink:0;
    font-size:11px; line-height:18px; color:var(--ink-3);
    white-space:nowrap;
  }
  /* [v1.0.20.2] 预览行复用 sender-line 布局：右侧放一个**同内容、不可见**的幽灵时间，
     把「省略号截断点」精确推到时间戳的左边界——预览文字永远不挤到时间戳下面。 */
  .preview-line { display:flex; align-items:baseline; justify-content:space-between; gap:12px; }
  .preview {
    flex:1; min-width:0;
    font-size:13px; line-height:18px; font-weight:400; color:var(--ink);
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .time.ghost { visibility:hidden; }
  .empty {
    display:flex; align-items:center; justify-content:center;
    height:100%; color:var(--ink-3); font-size:13px;
  }
  .footer {
    height:${FOOTER_HEIGHT}px;
    border:0; border-top:1px solid var(--line);
    background:var(--surface);
    color:var(--link);
    font:400 14px/1 var(--font-sans);
    cursor:pointer; outline:none;
    transition:background-color 120ms ease;
  }
  .footer:hover, .footer:focus-visible { background:var(--link-hover-bg); }
  .footer:active { background:var(--surface-active); }
</style>
</head>
<body>
  <div class="card" id="card"></div>
  <script>
    // mouseover: 每次鼠标移动 → 通知主进程保持卡片打开
    document.body.addEventListener('mouseover', () => {
      window.trayCard && window.trayCard.cardMouseEnter();
    });
    // 兜底：每 500ms 检查鼠标是否还在卡片上。mouseover 只在移动时触发，
    // 鼠标静止不动时没有 mouseover，必须用轮询确保卡片不因定时器超时而消失。
    setInterval(() => {
      if (document.body.matches(':hover')) {
        window.trayCard && window.trayCard.cardMouseEnter();
      }
    }, 500);
    // 鼠标离开卡片 → 通知主进程准备关闭
    document.body.addEventListener('mouseleave', () => {
      window.trayCard && window.trayCard.cardMouseLeave();
    });
    // 所有用户内容一律 textContent，不拼 HTML 字符串——天然免疫 XSS。
    function render(details) {
      const card = document.getElementById('card');
      card.textContent = '';
      if (!details || details.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = '暂无未读消息';
        card.appendChild(empty);
        return;
      }
      for (const d of details) {
        const row = document.createElement('div');
        row.className = 'row';
        row.addEventListener('click', () => {
          window.trayCard && window.trayCard.clickProject(d.projectId);
        });
        if (d.avatarUrl) {
          const img = document.createElement('img');
          img.className = 'avatar';
          img.src = d.avatarUrl;
          img.alt = '';
          row.appendChild(img);
        }
        const body = document.createElement('div');
        body.className = 'body';
        const senderLine = document.createElement('div');
        senderLine.className = 'sender-line';
        const sender = document.createElement('span');
        sender.className = 'sender';
        // 「项目名 · 姓名(职位)」；职位为空时省略括号
        const who = d.memberRole ? d.memberName + '(' + d.memberRole + ')' : (d.memberName || d.sender);
        sender.textContent = d.projectName + (who ? ' · ' + who : '');
        const time = document.createElement('span');
        time.className = 'time';
        time.textContent = d.timeLabel || '';
        senderLine.appendChild(sender);
        senderLine.appendChild(time);
        const previewLine = document.createElement('div');
        previewLine.className = 'preview-line';
        const preview = document.createElement('span');
        preview.className = 'preview';
        preview.textContent = d.preview || '';
        // 幽灵时间：与上方时间戳同文本、不可见，只负责把省略号截断点推到时间戳左边界
        const ghostTime = document.createElement('span');
        ghostTime.className = 'time ghost';
        ghostTime.textContent = d.timeLabel || '';
        previewLine.appendChild(preview);
        previewLine.appendChild(ghostTime);
        body.appendChild(senderLine);
        body.appendChild(previewLine);
        row.appendChild(body);
        card.appendChild(row);
      }
      const footer = document.createElement('button');
      footer.className = 'footer';
      footer.type = 'button';
      footer.textContent = '忽略全部';
      footer.addEventListener('click', () => {
        window.trayCard && window.trayCard.ignoreAll();
      });
      card.appendChild(footer);
    }
    // 数据由主进程在 did-finish-load 后通过 executeJavaScript 注入到 window.__TRAY_CARD_DATA__。
    render(window.__TRAY_CARD_DATA__ || []);
  </script>
</body>
</html>`;
}

/** 创建（或重建）托盘卡片窗口，定位到托盘图标旁边。 */
export function createTrayCard(trayBounds: Rectangle, details: UnreadDetail[]): void {
  cancelDestroyTrayCard();
  destroyTrayCard();

  const bounds = calcWindowBounds(trayBounds, details.length);
  const dark = nativeTheme.shouldUseDarkColors;
  // 头像相对路径在前端已拼完整 URL；这里再兜底一道（老数据/空串）
  const normalized = details.map((d) => ({ ...d, avatarUrl: resolveAvatarUrl(d.avatarUrl || '') }));
  const dataJson = JSON.stringify(normalized);

  cardWindow = new BrowserWindow({
    ...bounds,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    movable: false,
    focusable: false, // 卡片不抢焦点——它是个悬停提示，不是一个窗口
    hasShadow: false, // 阴影是 CSS box-shadow 画的，不用系统原生窗口阴影（会变成一个方框）
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      preload: resolveTrayCardPreloadPath(),
    },
  });

  cardWindow.webContents.on('did-finish-load', () => {
    if (!cardWindow || cardWindow.isDestroyed()) return;
    void cardWindow.webContents.executeJavaScript(
      `window.__TRAY_CARD_DATA__ = ${dataJson}; typeof render === 'function' && render(${dataJson});`,
    );
  });

  cardWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(buildHtml(dark))}`);
}

/** 关闭并释放托盘卡片窗口。已经关着时调用是安全的空操作。 */
export function destroyTrayCard(): void {
  log('💀 destroyTrayCard 被调用');
  cancelDestroyTrayCard();
  if (cardWindow && !cardWindow.isDestroyed()) {
    cardWindow.close();
  }
  cardWindow = null;
}
