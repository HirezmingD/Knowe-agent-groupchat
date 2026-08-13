/**
 * [v1.0.13][R5] main.ts — Electron 主进程。
 *
 * 干三件事，一件都不能少：
 *   1. 开一扇窗（BrowserWindow），把 React 那一坨渲染进程装进去；
 *   2. 把 Python 后端当亲儿子养（spawn / 健康检查 / 崩了知道 / 退出时收尸）；
 *   3. 在主进程和渲染进程之间架一座**只过数据、不过能力**的桥（IPC）。
 *
 * 桥能过什么，不由这里说了算——由 src/shared/bridge.ts 里的 KnoweBridge + IPC 说了算。
 * 频道名一个字都不在这里手写：全部从 IPC 常量取，写歪了 preload 那边对不上，前端就哑了。
 */

import {
  app, BrowserWindow, ipcMain, dialog, shell, Tray, Menu, nativeImage, session,
  type IpcMainEvent, type IpcMainInvokeEvent, type Session,
} from 'electron';
import { spawn, type ChildProcess } from 'node:child_process';
import { randomBytes, createHmac } from 'node:crypto';
import { join, dirname, resolve, basename, extname } from 'node:path';
import {
  cpSync,
  existsSync,
  mkdirSync,
  readdirSync,
  renameSync,
  rmSync,
  appendFileSync,
  statSync,
  readFileSync,
  writeFileSync,
} from 'node:fs';
import { get as httpGet, request as httpRequest } from 'node:http';
import { createConnection } from 'node:net'; // [1.2] 端口占用探测
import { pathToFileURL } from 'node:url';

import {
  isPreviewTopLevelUrlUnknown,
  isSafeExternalPreviewUrl,
  parsePreviewNavigationDetails,
  previewFrameNavigationAction,
} from './previewNavigation';

import {
  IPC,
  type BackendStatus,
  type BackendPhase,
  type PreviewFilePayload,
  type PreviewOpenPayload,
  type UnreadDetail,
} from '../src/shared/bridge';
// [v1.0 frameless] 窗口控制的频道名与动作类型——照家规不手写，preload 也从同一处取。
import { WINDOW_CONTROL_CHANNEL, type WindowControlAction } from '../src/shared/windowControl';
// [v1.0.19.1] 托盘新消息悬停卡片——独立 BrowserWindow，见 electron/trayCard.ts。
import {
  createTrayCard,
  destroyTrayCard,
  isTrayCardOpen,
  isTrayCardSender,
  scheduleDestroyTrayCard,
  cancelDestroyTrayCard,
} from './trayCard';
// [v1.0.25.4] 自动更新模块（electron-updater 封装），见 electron/updater.ts。
import {
  initAutoUpdater,
  checkForUpdates as updaterCheck,
  installUpdate as updaterInstall,
  getUpdateStatus,
} from './updater';
import {
  configureRemoteDebugging,
  isAuthorizedRuntimeWebSocket,
  isTrustedTopLevelRuntimeFrame,
  sanitizeBackendEnvironment,
  stripRuntimeTokenHeader,
} from './securityPolicy';

// Remote debugging can inspect API-key input and the preload runtime token.  It is
// prohibited in packaged builds; developers must opt in explicitly.
configureRemoteDebugging(app.commandLine, { isPackaged: app.isPackaged });


// ═══════════════════════════════════════════════════════════════
// 环境参数（prompt 明确「直接用，不要猜」）
// ═══════════════════════════════════════════════════════════════

/**
 * 项目根目录 = 从 out/main/ 往上两级。
 *   编译产物 __dirname 落在 <root>/out/main，`../..` 就是 <root>。
 *   （electron-vite 默认三段式输出：out/main、out/preload、out/renderer。）
 */
const PROJECT_ROOT = join(__dirname, '..', '..');

/** 安装根是桌面数据与打包资源的唯一定位基准。 */
const INSTALL_ROOT = app.isPackaged ? dirname(process.execPath) : PROJECT_ROOT;
const DATA_ROOT = join(INSTALL_ROOT, 'data');
const ELECTRON_DATA_ROOT = join(DATA_ROOT, 'electron');
const BACKEND_DATA_ROOT = join(DATA_ROOT, 'backend');

// [v1.0.28 R2] 更新缓存指路：electron-updater 缓存根 = %LOCALAPPDATA%\knowe-updater
//   （源码实锤：AppAdapter.getAppCacheDir 在 win32 读 process.env.LOCALAPPDATA，懒计算）。
//   启动早期（模块顶层）指到安装目录，让差分样本/索引/下载包全部落在
//   <安装目录>\cache\knowe-updater，不碰系统盘。仅打包版生效，开发态不污染系统变量。
if (app.isPackaged) {
  process.env.LOCALAPPDATA = join(INSTALL_ROOT, 'cache');
}

function localPort(name: string, fallback: number): number {
  const value = Number(process.env[name] || fallback);
  return Number.isInteger(value) && value > 0 && value <= 65535 ? value : fallback;
}

// [1.2] 端口避让会改写这三个值：HTTP_PORT / WS_PORT 由 const 改 let，
//       RUNTIME_ENDPOINTS 去掉 Object.freeze，改为整体重建（见 ensurePorts）。
let HTTP_PORT = localPort('KNOWE_HEALTH_PORT', 8081);
let WS_PORT = localPort('KNOWE_WS_PORT', 8080);
let RUNTIME_ENDPOINTS = {
  httpBase: `http://127.0.0.1:${HTTP_PORT}`,
  wsUrl: `ws://127.0.0.1:${WS_PORT}`,
};

/** 后端 Python 包与运行时在生产包中都走固定资源位置；开发态只允许显式覆盖解释器。 */
const BACKEND_DIR = app.isPackaged
  ? join(process.resourcesPath, 'backend')
  : join(PROJECT_ROOT, 'backend');
// [阶段1.5] 打包版后端 = PyInstaller 单文件产物（随 resources/backend 一起分发）。
const BACKEND_EXE = join(BACKEND_DIR, 'KnoweBackend.exe');
/**
 * The only native executor permitted to run model-authored commands.  The
 * packaged path is outside app.asar and is populated by electron-builder;
 * development uses the exact SDK version pinned in package-lock.json.
 */
const MXC_EXECUTABLE = app.isPackaged
  ? join(process.resourcesPath, 'sandbox', 'wxc-exec.exe')
  : join(
    PROJECT_ROOT,
    'node_modules',
    '@microsoft',
    'mxc-sdk',
    'bin',
    process.arch === 'arm64' ? 'arm64' : 'x64',
    'wxc-exec.exe',
  );
const SANDBOX_LAUNCHER = app.isPackaged
  ? join(process.resourcesPath, 'sandbox', 'knowe-sandbox-launcher.exe')
  : join(PROJECT_ROOT, 'build', 'native', 'knowe-sandbox-launcher.exe');
const BUNDLED_RUNTIME_ROOT = app.isPackaged
  ? join(process.resourcesPath, 'runtime')
  : join(PROJECT_ROOT, 'runtime');
const FIXED_PYTHON = process.platform === 'win32'
  ? join(BUNDLED_RUNTIME_ROOT, 'python.exe')
  : join(BUNDLED_RUNTIME_ROOT, 'bin', 'python3');
const DEV_PYTHON_OVERRIDE = app.isPackaged
  ? ''
  : (process.env.KNOWE_DEV_PYTHON ?? '').trim();
const PYTHON = DEV_PYTHON_OVERRIDE || FIXED_PYTHON;

// [阶段1.5] 统一启动入口：打包版直接执行 PyInstaller 产物（无 -m 参数）；
//           开发态维持 `python -m backend`。spawnBackend 里两分支共用这一对常量。
const BACKEND_CMD = app.isPackaged ? BACKEND_EXE : PYTHON;
const BACKEND_ARGS = app.isPackaged ? [] : ['-m', 'backend'];

type LegacyMove = Readonly<{ source: string; target: string }>;

function pathIdentity(value: string): string {
  const normalized = resolve(value);
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized;
}

function legacyRootMoves(
  sourceRoot: string,
  targetRoot: string,
  excludedNames: ReadonlySet<string> = new Set<string>(),
): LegacyMove[] {
  if (!existsSync(sourceRoot) || pathIdentity(sourceRoot) === pathIdentity(targetRoot)) return [];
  return readdirSync(sourceRoot)
    .filter((name) => !excludedNames.has(name.toLowerCase()))
    .map((name) => ({ source: join(sourceRoot, name), target: join(targetRoot, name) }));
}

function moveLegacyEntry(source: string, target: string): void {
  mkdirSync(dirname(target), { recursive: true });
  try {
    renameSync(source, target);
    return;
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code !== 'EXDEV') throw error;
  }

  const importing = `${target}.importing`;
  cpSync(source, importing, {
    recursive: true,
    errorOnExist: true,
    force: false,
    preserveTimestamps: true,
  });
  renameSync(importing, target);
  rmSync(source, { recursive: true, force: false });
}

/**
 * 把已知旧根一次性并入新的唯一数据根。迁移前先做全量冲突检查；任何冲突或 I/O
 * 错误都向上抛，绝不在运行期继续双读或悄悄回退。
 */
function migrateLegacyDataRoots(legacyElectronRoot: string): void {
  const moves = [
    ...legacyRootMoves(
      legacyElectronRoot,
      ELECTRON_DATA_ROOT,
      new Set(['knowe-data']),
    ),
    ...legacyRootMoves(join(legacyElectronRoot, 'knowe-data'), BACKEND_DATA_ROOT),
    // 旧版后端以 BACKEND_DIR 为 cwd，默认 ./data 因而落在这里。
    ...legacyRootMoves(join(BACKEND_DIR, 'data'), BACKEND_DATA_ROOT),
  ];

  const unique: LegacyMove[] = [];
  const seenPairs = new Set<string>();
  const targetSources = new Map<string, string>();
  for (const move of moves) {
    if (!existsSync(move.source)) continue;
    const sourceKey = pathIdentity(move.source);
    const targetKey = pathIdentity(move.target);
    if (sourceKey === targetKey) continue;
    const pairKey = `${sourceKey}\u0000${targetKey}`;
    if (seenPairs.has(pairKey)) continue;
    seenPairs.add(pairKey);

    const priorSource = targetSources.get(targetKey);
    if (priorSource && pathIdentity(priorSource) !== sourceKey) {
      throw new Error(
        '检测到两份 Knowe 数据，未自动合并。\n'
        + `来源：${priorSource}\n来源：${move.source}\n目标：${move.target}`,
      );
    }
    targetSources.set(targetKey, move.source);
    if (existsSync(move.target)) {
      throw new Error(
        '检测到两份 Knowe 数据，未自动合并。\n'
        + `来源：${move.source}\n目标：${move.target}`,
      );
    }
    unique.push(move);
  }

  // 上次跨盘复制被中断时，只清理尚未发布的临时目标；正式目标存在时绝不碰它。
  for (const { target } of unique) {
    const importing = `${target}.importing`;
    if (!existsSync(target) && existsSync(importing)) {
      rmSync(importing, { recursive: true, force: true });
    }
  }
  for (const { source, target } of unique) moveLegacyEntry(source, target);
}

const LEGACY_ELECTRON_DATA_ROOT = app.getPath('userData');
let dataRootInitError: Error | null = null;
let electronDataRootBound = false;
try {
  migrateLegacyDataRoots(LEGACY_ELECTRON_DATA_ROOT);
  mkdirSync(ELECTRON_DATA_ROOT, { recursive: true });
  mkdirSync(BACKEND_DATA_ROOT, { recursive: true });
} catch (error: unknown) {
  dataRootInitError = error instanceof Error ? error : new Error(String(error));
  console.error(`[main] 数据根初始化失败：${dataRootInitError.message}`);
}
try {
  // 必须早于单实例锁、窗口和后端创建，确保 Electron 自身也只写唯一内部根。
  app.setPath('userData', ELECTRON_DATA_ROOT);
  electronDataRootBound = true;
} catch (error: unknown) {
  const detail = error instanceof Error ? error.message : String(error);
  dataRootInitError = new Error(
    dataRootInitError ? `${dataRootInitError.message}；绑定 Electron 数据根失败：${detail}` : detail,
  );
  console.error(`[main] 无法绑定 Electron 数据根：${detail}`);
}

/** 后端健康检查地址（HTTP 探针）；[1.2] 端口避让可能重建 RUNTIME_ENDPOINTS，故每次现取。 */
function healthUrl(): string {
  return `${RUNTIME_ENDPOINTS.httpBase}/health`;
}
/** 探针轮询间隔 */
const HEALTH_INTERVAL_MS = 500;
/** 起不来的耐心上限：30 秒还不 ok 就判 failed */
const HEALTH_TIMEOUT_MS = 30_000;
/** 后端日志留最近这么多行——起不来时，这几行就是全部线索 */
const LOG_TAIL_MAX = 50;
/** [1.3] 自动重拉：连续失败这么多次就放弃（下次启动软件重新尝试） */
const MAX_RELAUNCH_ATTEMPTS = 3;
/** [1.3] 自动重拉：首次重试等待；之后 1→2→4 秒退避（见 RELAUNCH_DELAY_MAX_MS） */
const RELAUNCH_DELAY_INITIAL_MS = 1_000;
/** [1.3] 自动重拉：退避间隔上限 */
const RELAUNCH_DELAY_MAX_MS = 4_000;

// ═══════════════════════════════════════════════════════════════
// 状态：一扇窗、一个后端子进程、一台状态机
// ═══════════════════════════════════════════════════════════════

let mainWindow: BrowserWindow | null = null;
/** 独立原生预览窗口；与主窗口共享后端，但生命周期互不隶属。 */
let previewWindow: BrowserWindow | null = null;
let previewRendererReady = false;
const pendingPreviewOpens: PreviewOpenPayload[] = [];
const PREVIEW_OPEN_QUEUE_MAX = 100;

/** [v1.0 fix-p3 #3] 系统托盘图标（没建起来时为 null） */
let tray: Tray | null = null;

/**
 * [v1.0 fix-p3 #3] 桌面通知/关闭行为偏好。
 *   默认值和 src/store/settings.ts 的初值对齐（notifyDesktop:true / closeToTray:true）；
 *   前端一起来会 pushToBackend → setNotifyPrefs 覆盖成用户真实设置。
 */
let notifyPrefs: { desktop: boolean; closeToTray: boolean } = { desktop: true, closeToTray: true };

/**
 * [v1.0 fix-p3 #3] 是不是「真的要退出」。
 *   closeToTray 开着时，点 ✕ 只是 hide，不是退。只有走托盘菜单「退出」/ before-quit
 *   才把这个置上——close 事件据此决定：拦下来 hide，还是放行让它真关。
 */
let isQuitting = false;
let allowQuit = false;
let quitCleanupStarted = false;

/** [v1.0 fix-p3 #3] 托盘图标闪烁定时器（没在闪时为 null） */
let trayFlashTimer: NodeJS.Timeout | null = null;

/** [v1.0.19.1] 托盘悬停卡片缓存——前端每次未读明细变化时推过来，mouse-enter 时读它渲染卡片。 */
let unreadDetailsCache: UnreadDetail[] = [];

/** [v1.0 fix-p3 #3] APP/托盘图标源文件 —— 和窗口 icon 用同一张 */
const APP_ICON = join(PROJECT_ROOT, 'public', 'brand', 'app-icon.png');

/** 当前后端子进程句柄（没起来时为 null） */
let backendProc: ChildProcess | null = null;
/** Per-Electron-process bearer; never persisted/logged and shared only with the trusted main frame. */
let runtimeToken = '';
/**
 * [v1.0.19.4] 附件路径签名密钥——**持久化**、与临时 WS runtimeToken 分离。
 *
 *   护栏要防的是「消息正文捏造路径→后端直读任意文件」，靠的是「只有主进程能签」。
 *   这个属性不要求密钥每次启动都换；反而要求**跨重启稳定**，否则历史消息里存的旧签名
 *   在重启后全部作废，点历史文件卡会被误判成「校验未通过」——文件明明还在硬盘上。
 *   所以签名密钥独立持久化在 userData 下（0600），WS 认证令牌照旧一进程一换。
 */
let attachmentSignKey = '';
function getAttachmentSignKey(): string {
  if (attachmentSignKey) return attachmentSignKey;
  try {
    const keyPath = join(app.getPath('userData'), 'attachment-sign.key');
    if (existsSync(keyPath)) {
      const saved = readFileSync(keyPath, 'utf8').trim();
      if (/^[0-9a-f]{64}$/.test(saved)) { attachmentSignKey = saved; return attachmentSignKey; }
    }
    const fresh = randomBytes(32).toString('hex');
    try { writeFileSync(keyPath, fresh, { encoding: 'utf8', mode: 0o600 }); } catch { /* 写不进也不致命：本会话仍可用，只是下次重启再生成 */ }
    attachmentSignKey = fresh;
  } catch {
    // userData 不可用等极端情况：退回一次性随机密钥（至少本会话内自洽）。
    if (!attachmentSignKey) attachmentSignKey = randomBytes(32).toString('hex');
  }
  return attachmentSignKey;
}
/** 健康检查轮询定时器 */
let healthTimer: NodeJS.Timeout | null = null;
/** 起不来超时定时器 */
let timeoutTimer: NodeJS.Timeout | null = null;
/**
 * 是不是「我们主动要停它」——退出流程 / 重启时的杀进程。
 *   置上这个标记，exit 事件就该判 stopped 而不是 crashed：
 *   同样是进程没了，「我叫它走的」和「它自己倒下的」得分清楚，否则退出时误报崩溃。
 */
let intentionalStop = false;

/**
 * [1.3] 自动重拉状态机（后端意外死亡自动拉起，用户无感知）：
 *   relaunching = true 表示正处于「崩溃 → 自动重拉」周期中；
 *   周期内任何一次「没到 ready 就没了」都计一次失败（recordRelaunchFailure），
 *   连续 MAX_RELAUNCH_ATTEMPTS 次失败 → 放弃；后端真正 ready 时整周期复位。
 */
let relaunching = false;
/** [1.3] 连续重拉失败计数——只在后端真正达到 ready（首次探活成功）时清零，不在 spawn 时清 */
let relaunchFailCount = 0;
/** [1.3] 下一次重试的等待毫秒数：1→2→4 退避，成功后复位 RELAUNCH_DELAY_INITIAL_MS */
let relaunchDelayMs = RELAUNCH_DELAY_INITIAL_MS;
/** [1.3] 挂起的重拉定时器：非 null 即重拉进行中（防重入；killBackend 会先取消它） */
let relaunchTimer: NodeJS.Timeout | null = null;

// ═══════════════════════════════════════════════════════════════
// [v1.0.25.4] 自动更新 & 产品版本号
// ═══════════════════════════════════════════════════════════════

/** 启动后延迟多久开始静默检查更新（不阻塞启动、不拖慢界面）。 */
const SILENT_CHECK_DELAY_MS = 10_000;

let productVersionCache = '';

/**
 * 产品版本号 = package.json productVersion（如 1.0.25.4，用户可见的「发版号」）。
 * 与 package.json version（三段 semver，自动更新比较基准）分开：四段号会被
 * electron-builder 的 semver.clean 拆坏（1.0.25.4 → 1.0.2-5.4），故比较基准必须三段。
 * 打包后 package.json 在 asar 根（files 白名单含 package.json），读法一致。
 */
function getProductVersion(): string {
  if (productVersionCache) return productVersionCache;
  try {
    const pkg = JSON.parse(readFileSync(join(PROJECT_ROOT, 'package.json'), 'utf8')) as {
      productVersion?: unknown; version?: unknown;
    };
    productVersionCache = String(pkg.productVersion || pkg.version || app.getVersion());
  } catch {
    productVersionCache = app.getVersion();
  }
  return productVersionCache;
}

/** 环形日志缓冲：只留最近 LOG_TAIL_MAX 行 */
const logRing: string[] = [];

/** 当前状态机快照（getBackendStatus 直接返回它的副本） */
let current: BackendStatus = {
  phase: 'stopped',
  message: '后端尚未启动',
  pid: null,
  logTail: [],
};

// ═══════════════════════════════════════════════════════════════
// 日志 & 状态推送
// ═══════════════════════════════════════════════════════════════

/** 日志根目录：打包版 → 安装目录\Logs；开发版 → 项目根\Logs（与 INSTALL_ROOT 分流机制一致）。 */
const LOG_ROOT = join(INSTALL_ROOT, 'Logs');

/** 按天翻篇的文件名：backend_YYYYMMDD.log */
function logFileName(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `backend_${y}${m}${d}.log`;
}

/**
 * 统一日志入口：写 <LOG_ROOT>/backend_YYYYMMDD.log（按天翻篇）。
 *   写盘失败绝不静默：console.error 可见，让日志问题本身能被发现。
 */
function writeLog(message: string): void {
  const ts = new Date().toISOString().slice(11, 23);
  const file = join(LOG_ROOT, logFileName(new Date()));
  try {
    mkdirSync(LOG_ROOT, { recursive: true });
    appendFileSync(file, `[${ts}] ${message}\n`);
  } catch (error) {
    console.error(`[main] 日志写入失败（${file}）：${error instanceof Error ? error.message : String(error)}`);
  }
}

/** 启动时清理 7 天前的 backend_*.log（按文件名日期判断；只清本应用自己的日志）。 */
function pruneOldLogs(): void {
  try {
    if (!existsSync(LOG_ROOT)) return;
    const now = new Date();
    const cutoff = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 7);
    const cutoffKey = cutoff.getFullYear() * 10_000 + (cutoff.getMonth() + 1) * 100 + cutoff.getDate();
    for (const name of readdirSync(LOG_ROOT)) {
      if (!/^backend_\d{8}\.log$/.test(name)) continue;
      const fileKey = Number(name.slice(8, 16));
      if (Number.isFinite(fileKey) && fileKey < cutoffKey) {
        try {
          rmSync(join(LOG_ROOT, name), { force: true });
        } catch (error) {
          console.error(`[main] 清理旧日志失败（${join(LOG_ROOT, name)}）：${error instanceof Error ? error.message : String(error)}`);
        }
      }
    }
  } catch (error) {
    console.error(`[main] 清理旧日志失败：${error instanceof Error ? error.message : String(error)}`);
  }
}

/** 往环形缓冲塞一行（按 \n 拆多行，超长自动丢最旧的） */
function pushLog(chunk: string): void {
  const lines = chunk.replace(/\r/g, '').split('\n');
  for (const line of lines) {
    if (line.length === 0) continue;
    logRing.push(line);
    if (logRing.length > LOG_TAIL_MAX) logRing.shift();
    // 后端日志统一落盘：按天翻篇 backend_YYYYMMDD.log（开发/打包路径分流见 writeLog）
    writeLog(line);
  }
}

/** 人话文案表：每个 phase 配一句可以直接摆给用户看的话 */
function defaultMessage(phase: BackendPhase): string {
  switch (phase) {
    case 'starting': return '后端启动中…';
    case 'ready':    return '后端已就绪';
    case 'crashed':  return '后端进程意外退出';
    case 'failed':   return '后端启动失败';
    case 'stopped':  return '后端已停止';
  }
}

/**
 * 更新状态机并**主动推给渲染进程**。
 *   message 不传就用该 phase 的默认人话；日志尾巴每次都带上最新的。
 */
function setStatus(phase: BackendPhase, message?: string): void {
  current = {
    phase,
    message: message ?? defaultMessage(phase),
    pid: backendProc?.pid ?? null,
    logTail: [...logRing],
  };
  // 窗口可能正在销毁：webContents 没了就别推，推了会抛。
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(IPC.statusChanged, current);
  }
}

/** 当前状态的只读副本（IPC.getStatus / IPC.restart 的返回值） */
function snapshot(): BackendStatus {
  return { ...current, logTail: [...current.logTail] };
}

// ═══════════════════════════════════════════════════════════════
// 端口探测 & 避让 [1.2]
// ═══════════════════════════════════════════════════════════════

/** 探测端口是否被占用：能连上 127.0.0.1:port 即视为被占；从不 reject。 */
function isPortInUse(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = createConnection({ host: '127.0.0.1', port });
    const done = (used: boolean): void => {
      socket.destroy();
      resolve(used);
    };
    socket.once('connect', () => done(true));
    socket.once('error', () => done(false));
    // 连不上也不干等：300ms 探不出就当空闲
    socket.setTimeout(300, () => done(false));
  });
}

/**
 * 从 startPort 起递增找第一个空闲端口（最多找 200 个）。
 *   即使全被占也返回一个端口（不抛），让后续 spawn/健康检查的失败路径自己暴露问题。
 */
async function findFreePort(startPort: number, excluded: ReadonlySet<number> = new Set()): Promise<number> {
  for (let port = startPort; port < startPort + 200 && port <= 65535; port++) {
    if (excluded.has(port)) continue;
    if (!(await isPortInUse(port))) return port;
  }
  return startPort;
}

/**
 * 端口避让：请求的 WS/HTTP 端口（默认 8080/8081，可用 env 覆盖）被占时，
 *   自动选空闲端口并写入 HTTP_PORT / WS_PORT / RUNTIME_ENDPOINTS。
 *   没被占就保持默认（开发态默认端口不变）。结果记入统一日志。
 *   必须在 registerRuntimeRequestPolicy / createWindow / spawnBackend 之前调用。
 */
async function ensurePorts(): Promise<void> {
  const requestedHttp = localPort('KNOWE_HEALTH_PORT', 8081);
  const requestedWs = localPort('KNOWE_WS_PORT', 8080);
  const wsPort = await findFreePort(requestedWs);
  // HTTP 端口从自己的请求值开始找，但排除已被 WS 选中的端口，避免两端口撞车
  const httpPort = await findFreePort(requestedHttp, new Set([wsPort]));

  if (wsPort !== requestedWs || httpPort !== requestedHttp) {
    writeLog(`[port] 端口避让：WS ${requestedWs}→${wsPort}，HTTP ${requestedHttp}→${httpPort}`);
  } else {
    writeLog(`[port] 端口探测：WS ${wsPort}、HTTP ${httpPort} 空闲，保持默认`);
  }

  HTTP_PORT = httpPort;
  WS_PORT = wsPort;
  RUNTIME_ENDPOINTS = {
    httpBase: `http://127.0.0.1:${httpPort}`,
    wsUrl: `ws://127.0.0.1:${wsPort}`,
  };
}

// ═══════════════════════════════════════════════════════════════
// 健康检查
// ═══════════════════════════════════════════════════════════════

/** 探一次 /health。ok（2xx）→ resolve(true)；连不上/非 2xx → resolve(false)，从不 reject。 */
function probeHealthOnce(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = httpGet(healthUrl(), {
      headers: { 'X-Knowe-Runtime-Token': runtimeToken },
    }, (res) => {
      const ok = res.statusCode != null && res.statusCode >= 200 && res.statusCode < 300;
      res.resume(); // 把响应体喝干，否则 socket 不释放
      resolve(ok);
    });
    req.on('error', () => resolve(false));
    // 单次探针也得有超时，别让一个卡住的连接拖垮 500ms 的节奏
    req.setTimeout(HEALTH_INTERVAL_MS, () => {
      req.destroy();
      resolve(false);
    });
  });
}

/** 清掉两个定时器（健康轮询 + 超时闹钟）——每次状态尘埃落定都要调 */
function clearHealthTimers(): void {
  if (healthTimer) { clearInterval(healthTimer); healthTimer = null; }
  if (timeoutTimer) { clearTimeout(timeoutTimer); timeoutTimer = null; }
}

/**
 * 开始健康检查循环：
 *   · 每 500ms 探一次，探到 ok → 转 ready，停表；
 *   · 30 秒还没 ok → 转 failed，停表、并把还在赖着的子进程杀掉。
 */
function beginHealthWatch(): void {
  clearHealthTimers();

  healthTimer = setInterval(() => {
    void probeHealthOnce().then((ok) => {
      // 只有还处在 starting 才认这次探针结果——已经 crashed/ready 了就别回头。
      if (!ok || current.phase !== 'starting') return;
      clearHealthTimers();
      setStatus('ready');
      // [1.3] 后端真正达到 ready（首次探活成功）→ 重拉周期结束、失败计数清零。
      //       必须在「探活成功」时清而不是 spawn 时清：否则后端每次崩溃都在计数上
      //       叠加，等于「崩一次就烧掉一次重拉机会」，与「ready 后崩溃另起新周期」不符。
      if (relaunching) writeLog('[relaunch] 后端已就绪：自动重拉成功，失败计数清零');
      relaunching = false;
      relaunchFailCount = 0;
      relaunchDelayMs = RELAUNCH_DELAY_INITIAL_MS;
    });
  }, HEALTH_INTERVAL_MS);

  timeoutTimer = setTimeout(() => {
    if (current.phase !== 'starting') return; // 期间已经崩了或起来了，超时作废
    clearHealthTimers();
    // 30 秒都不 ok：多半是端口占用 / import 失败卡在半路。把僵着的进程收掉，判 failed。
    intentionalStop = true;
    backendProc?.kill();
    setStatus('failed', `后端 ${HEALTH_TIMEOUT_MS / 1000} 秒内未通过健康检查`);
    // [1.3] 重拉周期内的健康检查超时：kill 会置 intentionalStop=true，但这不是「主动停止」，
    //       而是「重拉上来的进程又没起来」——必须计入重拉失败，否则 exit 会被误判成 stopped、
    //       重拉周期静默熄火。非重拉周期（首启失败）保持原样，只报 failed 不计数。
    if (relaunching) recordRelaunchFailure('健康检查超时');
  }, HEALTH_TIMEOUT_MS);
}

// ═══════════════════════════════════════════════════════════════
// 后端子进程：spawn / kill
// ═══════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════
// [v1.0 fix-cwd] cwd 错误 —— 后端 code=1 的真正根因
// ═══════════════════════════════════════════════════════════════
//
// 症状：用户在终端 `cd 项目根/backend && python -m backend` 一切正常，Electron 一拉起就
//       「后端启动过程中退出（code=1）」。
//
// 根因（已通过全量架构审计确认）：spawn 时 cwd 被设成了 PROJECT_ROOT，但后端的包结构
//       要求以 backend/ 为工作目录启动。cwd=PROJECT_ROOT 时，`python -m backend` 会把
//       PROJECT_ROOT 注入 sys.path[0]；server.py 里 `from knowe_provenance import ...`
//       于是去 PROJECT_ROOT/knowe_provenance/ 找——但 knowe_provenance/ 实际在
//       PROJECT_ROOT/backend/ 下面，这个路径根本不存在。ModuleNotFoundError → code=1，
//       恰好卡在 starting 阶段，前端就显示「后端启动过程中退出（code=1）」。
//
// 修法：spawn 的 cwd 从 PROJECT_ROOT 改成 BACKEND_DIR（见下方 spawnBackend）；
//       另外在 buildBackendEnv() 里补一道 PYTHONPATH=BACKEND_DIR 双保险，日后即使
//       又有地方把 cwd 带偏，knowe_provenance 仍能通过 PYTHONPATH 解析到。
//       同时把 stdio 钉成 UTF-8，并无条件注入安装目录下的唯一后端数据根。
//
// [v1.0.24.7 结构归一] 上述「cwd 必须是 backend/」的前提已变化：backend 上提为单层
//       实现包后，开发态 `python -m backend` 必须从 PROJECT_ROOT 启动才能解析
//       backend 包本身（cwd=backend/ 时 sys.path 里找不到 backend）。归一后 dev
//       cwd 改回 PROJECT_ROOT；knowe_* 顶层包仍由 PYTHONPATH=BACKEND_DIR 兜底
//       （与上方历史修法的双保险同一机制）。打包态不变（exe 自包含，cwd=BACKEND_DIR）。

/** spawn 后端用的环境变量：固定数据根、固定安装根与 UTF-8 stdio。 */
function buildBackendEnv(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {
    ...sanitizeBackendEnvironment(process.env),
    PYTHONUNBUFFERED: '1',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    KNOWE_INSTALL_ROOT: INSTALL_ROOT,
    KNOWE_DATA_DIR: BACKEND_DATA_ROOT,
    KNOWE_HEALTH_PORT: String(HTTP_PORT),
    KNOWE_WS_PORT: String(WS_PORT),
    KNOWE_RUNTIME_TOKEN: runtimeToken,
    KNOWE_ATTACHMENT_KEY: getAttachmentSignKey(),
    KNOWE_AGENT: process.env.KNOWE_AGENT ?? 'deepseek',
    // backend/sandbox_runner.py accepts no host-shell fallback.  If this exact
    // binary is missing or its native isolation probe fails, terminal tools
    // report unavailable instead of executing with the desktop user's token.
    KNOWE_MXC_EXECUTABLE: MXC_EXECUTABLE,
    KNOWE_SANDBOX_LAUNCHER: SANDBOX_LAUNCHER,
    KNOWE_PACKAGED: app.isPackaged ? '1' : '0',
  };
  // [阶段1.5] PYTHONPATH 双保险只对开发态有意义（`python -m backend` 的模块解析兜底）；
  //           打包版是 PyInstaller 自包含产物（依赖在 _internal/），不再注入。
  if (!app.isPackaged) {
    // 双保险：dev 后端 cwd=PROJECT_ROOT（归一后 python -m backend 的解析前提）；
    // PYTHONPATH 指向 backend/，让 knowe_* 顶层包可解析。
    env.PYTHONPATH = BACKEND_DIR;
  }
  if (app.isPackaged) {
    // 打包后：随包解释器不能继承宿主 Conda/virtualenv，否则依赖开发机环境。
    // 开发态：KNOWE_DEV_PYTHON 指向 Conda Python，必须保留 Conda 环境变量。
    delete env.PYTHONHOME;
    delete env.VIRTUAL_ENV;
    delete env.CONDA_PREFIX;
    delete env.CONDA_DEFAULT_ENV;
    // [阶段1.5] 打包版新增环境：浏览器内核随包分发（ms-playwright）并强制无头；
    //           KNOWE_VERSION 供后端上报版本（app 在模块层可用，直接取）。
    env.PLAYWRIGHT_BROWSERS_PATH = join(BACKEND_DIR, 'ms-playwright');
    env.KNOWE_BROWSER_HEADLESS = '1';
    env.KNOWE_VERSION = app.getVersion();
  }
  return env;
}

/**
 * 启动后端子进程：打包版执行 KnoweBackend.exe（PyInstaller 产物），
 * 开发态 `python -m backend`（cwd=PROJECT_ROOT，归一后单层包解析前提；
 * knowe_* 由 PYTHONPATH=BACKEND_DIR 兜底）。
 *   前置检查放在这里——可执行文件或 backend/ 不在，直接判 failed，
 *   连 spawn 都不试（否则 ENOENT 抛在别处，日志里看不明白）。
 */
function spawnBackend(): void {
  intentionalStop = false;
  // Keep one bearer for the Electron process lifetime. Renderer HTTP/WS clients
  // cache it, so rotating only the backend child would strand them after an
  // automatic or user-requested backend restart.
  if (!runtimeToken) runtimeToken = randomBytes(32).toString('hex');
  logRing.length = 0; // 每次启动清空日志尾巴，别把上一条命的遗言混进来

  if (dataRootInitError) {
    setStatus('failed', `数据目录初始化失败：${dataRootInitError.message}`);
    if (relaunching) recordRelaunchFailure('数据目录初始化失败'); // [1.3]
    return;
  }

  // ── 前置检查：可执行文件在不在 ──
  // [阶段1.5] 打包版检查 PyInstaller 产物（exe 本体 + _internal 依赖目录）；
  //           开发态维持检查随包 Python runtime。
  if (app.isPackaged) {
    if (!existsSync(BACKEND_EXE) || !existsSync(join(BACKEND_DIR, '_internal'))) {
      setStatus('failed', `后端可执行文件缺失：${BACKEND_EXE}，安装包可能损坏，请重新安装`);
      if (relaunching) recordRelaunchFailure('后端可执行文件缺失'); // [1.3]
      return;
    }
  } else if (!existsSync(PYTHON)) {
    setStatus('failed', `随包 Python runtime 缺失：${PYTHON}。请重新安装 Knowe。`);
    if (relaunching) recordRelaunchFailure('Python runtime 缺失'); // [1.3]
    return;
  }
  // ── 前置检查：后端目录在不在 ──
  if (!existsSync(BACKEND_DIR)) {
    setStatus('failed', `找不到后端目录：${BACKEND_DIR}`);
    if (relaunching) recordRelaunchFailure('后端目录缺失'); // [1.3]
    return;
  }

  // 先亮 starting，再 spawn：让界面立刻显示「启动中…」，别等进程起来才更新。
  setStatus('starting');

  // 起不来时，这几行诊断就是第一手线索（进日志尾巴，前端 logTail 直接能看到）。
  const backendEnv = buildBackendEnv();
  pushLog(`[main] 后端可执行文件：${BACKEND_CMD}`);
  pushLog(`[main] 后端 cwd：${app.isPackaged ? BACKEND_DIR : PROJECT_ROOT}`);
  pushLog(`[main] 安装根：${INSTALL_ROOT}`);
  pushLog(`[main] KNOWE_AGENT：${backendEnv.KNOWE_AGENT}（real=deepseek / fake=固定剧本）`);
  pushLog(`[main] KNOWE_DATA_DIR：${backendEnv.KNOWE_DATA_DIR}`);

  let proc: ChildProcess;
  try {
    proc = spawn(BACKEND_CMD, BACKEND_ARGS, {
      // [v1.0 fix-cwd] 历史修复：cwd 必须是 BACKEND_DIR（backend/），不是 PROJECT_ROOT。
      //   归一前开发态：`python -m backend` 在 cwd=backend/ 下命中内层 backend/backend/ 包，
      //   server.py 的 `from knowe_provenance import ...` 依赖 PYTHONPATH=BACKEND_DIR 兜底。
      //   [v1.0.24.7 结构归一] backend 上提为单层实现包，`python -m backend` 必须从
      //   PROJECT_ROOT 启动才能解析 backend 包；knowe_* 顶层包仍由 PYTHONPATH=BACKEND_DIR 兜底。
      //   [阶段1.5] 打包态：KnoweBackend.exe 以 BACKEND_DIR 为 cwd，_internal/ 依赖按相对路径解析。
      cwd: app.isPackaged ? BACKEND_DIR : PROJECT_ROOT,
      // 固定打包运行时 + UTF-8 stdio + 唯一后端数据根（见 buildBackendEnv）。
      env: backendEnv,
      windowsHide: true,
    });
  } catch (err) {
    setStatus('failed', `启动后端失败：${(err as Error).message}`);
    if (relaunching) recordRelaunchFailure(`spawn 抛错：${(err as Error).message}`); // [1.3]
    return;
  }

  backendProc = proc;
  setStatus('starting'); // 刷新一次，把刚拿到的 pid 带上

  // stdout / stderr 都进同一条环形日志（起不来时两路都是线索）
  proc.stdout?.on('data', (d: Buffer) => pushLog(d.toString()));
  proc.stderr?.on('data', (d: Buffer) => pushLog(d.toString()));

  // spawn 本身失败（ENOENT 等）：error 事件
  proc.on('error', (err) => {
    clearHealthTimers();
    backendProc = null;
    setStatus('failed', `后端进程错误：${err.message}`);
    // [1.3] 重拉周期内的 spawn 失败也计一次重拉失败。
    //       Node 里 spawn 失败后 exit 可能还会再触发一次，recordRelaunchFailure 内有防重入。
    if (relaunching) recordRelaunchFailure(`进程错误：${err.message}`);
  });

  // 进程退出：分三种情况（[1.3] 多了「崩溃自动重拉」与「重拉周期失败计数」两条腿）
  proc.on('exit', (code, signal) => {
    clearHealthTimers();
    backendProc = null;

    if (intentionalStop) {
      // 我们主动杀的（退出流程 / 手动重启 / 健康检查超时收尸）
      if (relaunching || current.phase === 'failed') {
        // [1.3] 重拉周期内被杀，或健康检查超时已判 failed：
        //       状态保持 failed，绝不覆盖成 stopped——「重拉上来的进程没起来」不是「主动停止」。
        //       注意 phase 兜底不能省：第 3 次重拉超时放弃时 relaunching 已被 recordRelaunchFailure
        //       置 false，若只看 relaunching，这里就会把 failed 错覆盖成 stopped（原代码即有此缺陷：
        //       首启健康检查超时后最终状态被 exit 洗成 stopped）。
        return;
      }
      // 正常主动停（退出 / 手动 restart）→ stopped，不是崩溃，不触发自动重拉
      setStatus('stopped');
      return;
    }
    if (current.phase === 'ready') {
      // 本来好好的，突然没了 → 崩溃 → [1.3] 自动重拉
      setStatus('crashed', `后端进程意外退出（code=${code ?? signal}）`);
      scheduleRelaunch(`后端意外退出（code=${code ?? signal}）`);
      return;
    }
    // 还没 ready 就退了（starting 阶段死掉）→ 起不来
    setStatus('failed', `后端启动过程中退出（code=${code ?? signal}）`);
    // [1.3] 重拉周期内起跑阶段就死 → 计一次失败，安排下次重试（首启失败不计数、不重拉）
    if (relaunching) recordRelaunchFailure(`启动过程中退出（code=${code ?? signal}）`);
  });

  // 进程活着 → 开始盯健康检查
  beginHealthWatch();
}

/**
 * 杀后端子进程，等它真的没了再 resolve。
 *   打上 intentionalStop 标记，让 exit 判 stopped。
 *   先请求认证的 /shutdown；宽限期内不走，再 SIGKILL 强制带走。
 */
function requestBackendShutdown(): Promise<void> {
  return new Promise((resolve) => {
    if (!runtimeToken) { resolve(); return; }
    const req = httpRequest(`${RUNTIME_ENDPOINTS.httpBase}/shutdown`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': '2',
        'X-Knowe-Runtime-Token': runtimeToken,
      },
    }, (res) => {
      res.resume();
      resolve();
    });
    req.on('error', () => resolve());
    req.setTimeout(600, () => { req.destroy(); resolve(); });
    req.end('{}');
  });
}

/** Ask the authenticated backend to close its resources, then force only as a final fallback. */
async function killBackend(): Promise<void> {
  clearHealthTimers();
  // [1.3] 主动停（用户退出 / 手动 restart）：先取消挂起的自动重拉。
  //       否则重拉定时器会在退出/重启后继续把后端拉起来——「主动关闭不触发自动重拉」就靠这一刀。
  cancelRelaunch();
  const proc = backendProc;
  if (!proc || proc.exitCode !== null || proc.signalCode !== null) {
    backendProc = null;
    return;
  }

  intentionalStop = true;
  await requestBackendShutdown();
  if (proc.exitCode !== null || proc.signalCode !== null) {
    backendProc = null;
    return;
  }
  await new Promise<void>((resolve) => {
    let finished = false;
    const finish = (): void => {
      if (finished) return;
      finished = true;
      clearTimeout(fallback);
      proc.removeListener('exit', finish);
      resolve();
    };
    proc.once('exit', finish);
    const fallback = setTimeout(() => {
      if (proc.exitCode === null && proc.signalCode === null) proc.kill('SIGKILL');
      finish();
    }, 3000);
  });
  backendProc = null;
}

// ═══════════════════════════════════════════════════════════════
// 后端自动重拉 [1.3]
// ═══════════════════════════════════════════════════════════════
// 后端进程意外死亡（crashed）→ 自动拉起，用户无感知。要点：
//   1. 重拉复用当前 HTTP_PORT / WS_PORT，绝不重跑 ensurePorts——
//      registerRuntimeRequestPolicy 的 filter 与窗口 additionalArguments 的端口
//      都是创建时固化的字符串，运行时换端口会让 token 注入失效、渲染进程连旧端口。
//   2. 触发只有一条路：exit 分类为 crashed（phase==='ready' 且非 intentionalStop）。
//   3. 退避：1→2→4 秒递增；连续 MAX_RELAUNCH_ATTEMPTS 次失败 → 放弃并写日志，
//      下次启动软件重新尝试；后端真正 ready（首次探活成功）时计数清零。
//   4. 防重入：relaunchTimer 非 null（重拉进行中）不重复触发；killBackend（用户退出/
//      手动 restart）先 cancelRelaunch，保证主动关闭绝不触发自动重拉。

/**
 * [1.3] 崩溃后安排一次自动重拉（走完整 spawnBackend：前置检查 + 30s 健康检查）。
 *   仅由 exit 的 crashed 分支调用；连续失败已在 recordRelaunchFailure 里计数，
 *   到上限就不再来这里了。复用当前端口：spawnBackend 读的是 HTTP_PORT/WS_PORT 现值。
 */
function scheduleRelaunch(reason: string): void {
  if (relaunchTimer) {
    // [1.3] 防重入：已有未完成的重拉定时器，说明周期已在走，忽略这次触发
    writeLog(`[relaunch] 重拉已在排队，忽略重复触发（${reason}）`);
    return;
  }
  if (isQuitting) {
    writeLog(`[relaunch] 应用正在退出，不自动重拉（${reason}）`);
    return;
  }
  relaunching = true;
  const delay = relaunchDelayMs;
  // [1.3] 退避递增放在这里：本次等 delay 秒，下次等待翻倍（1→2→4，上限 4s）。
  //       连续失败 3 次的等待序列正好是 1s→2s→4s；ready/放弃/cancel 时复位回 1s。
  relaunchDelayMs = Math.min(relaunchDelayMs * 2, RELAUNCH_DELAY_MAX_MS);
  writeLog(`[relaunch] ${delay / 1000} 秒后自动重拉后端（${reason}，已连续失败 ${relaunchFailCount} 次）`);
  relaunchTimer = setTimeout(() => {
    relaunchTimer = null;
    spawnBackend();
  }, delay);
}

/**
 * [1.3] 重拉周期内一次失败：计数 +1，按 1→2→4 退避安排下次重试；
 *   连续 MAX_RELAUNCH_ATTEMPTS 次失败 → 放弃并写日志（下次启动软件重新尝试）。
 *   非重拉周期（首启失败）调用会直接 no-op——首启失败仍按原样报 failed，不计数不重拉。
 */
function recordRelaunchFailure(reason: string): void {
  if (!relaunching) return;      // 不在重拉周期：不计数（首启失败、主动 restart 等）
  if (relaunchTimer) return;     // 防重入：同一次失败可能被 error+exit 双触发，只计一次

  relaunchFailCount += 1;
  if (relaunchFailCount >= MAX_RELAUNCH_ATTEMPTS) {
    relaunching = false;
    relaunchDelayMs = RELAUNCH_DELAY_INITIAL_MS;
    writeLog(`[relaunch] 连续 ${relaunchFailCount} 次重拉失败，放弃自动重拉（${reason}）。下次启动软件将重新尝试。`);
    return;
  }
  // 等待时间在 scheduleRelaunch 里已翻倍好（1→2→4），这里直接用现值
  const delay = relaunchDelayMs;
  writeLog(`[relaunch] 重拉失败（${reason}），${delay / 1000} 秒后重试（第 ${relaunchFailCount} 次失败）`);
  relaunchTimer = setTimeout(() => {
    relaunchTimer = null;
    spawnBackend();
  }, delay);
}

/**
 * [1.3] 取消挂起的自动重拉并复位整个重拉状态（killBackend 主动停时调用）。
 *   退出流程 / 手动 restart 后绝不允许定时器再把后端拉起来。
 */
function cancelRelaunch(): void {
  if (relaunchTimer) {
    clearTimeout(relaunchTimer);
    relaunchTimer = null;
  }
  relaunching = false;
  relaunchFailCount = 0;
  relaunchDelayMs = RELAUNCH_DELAY_INITIAL_MS;
}

// ═══════════════════════════════════════════════════════════════
// [v1.0 fix-p3 #2] preload 路径解析
// ═══════════════════════════════════════════════════════════════

/**
 * 找到真正存在的 preload 产物，返回它的绝对路径。
 *
 * 为什么优先 `preload/index.cjs`：
 *   主窗口启用了 Electron sandbox。sandboxed preload 只能使用 Electron 提供的
 *   受限 CommonJS loader；若跟随 package.json 的 `"type": "module"` 产出 .mjs，
 *   preload 会在 contextBridge 暴露 window.knowe 前失败。
 *   （这正是「点选择目录弹出浏览器上传框」的根因。）
 *
 * 因此只接受构建门禁生成的 index.cjs。缺失时让 Electron 的 preload-error
 * 明确暴露问题，绝不悄悄选取已知与 sandbox 不兼容的旧 ESM 产物。
 * 一个都找不到就大声报错（多半是还没 build），并回退到 index.js 让
 * 'preload-error' 把真实错误打出来。
 */
function resolvePreloadPath(): string {
  const dir = join(__dirname, '..', 'preload');
  const candidates = ['index.cjs'];
  for (const name of candidates) {
    const p = join(dir, name);
    if (existsSync(p)) {
      console.log(`[main] preload 命中：${p}`);
      return p;
    }
  }
  const fallback = join(dir, 'index.cjs');
  console.error(
    `[main] ⚠ 在 ${dir} 下没找到任何 preload 产物（试过 ${candidates.join(' / ')}）。\n` +
    `        先跑一次 electron-vite build/dev 生成 out/preload；` +
    `现回退到 ${fallback}，Electron 大概率会抛 preload-error。`,
  );
  return fallback;
}

// ═══════════════════════════════════════════════════════════════
// [v1.0 fix-p3 #3] 系统托盘 & 窗口显隐
// ═══════════════════════════════════════════════════════════════

/** 把窗口拉回前台：最小化的先还原，隐藏的先显示，最后聚焦；窗口没了就重开一扇。 */
function showMainWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  if (!mainWindow.isVisible()) mainWindow.show();
  mainWindow.focus();
}

/**
 * 托盘图标闪烁开关。
 *   Tray 没有原生「闪烁」API，这里用「正常图标 ↔ 空图标」定时切换来模拟。
 *   停止时务必把图标切回正常那张，别让它停在空的那一帧上。
 */
function setTrayFlashing(on: boolean): void {
  if (!tray || tray.isDestroyed()) return;

  if (on) {
    if (trayFlashTimer) return; // 已经在闪了
    const normal = nativeImage.createFromPath(APP_ICON);
    const blank = nativeImage.createEmpty();
    let showBlank = false;
    trayFlashTimer = setInterval(() => {
      if (!tray || tray.isDestroyed()) { setTrayFlashing(false); return; }
      tray.setImage(showBlank ? blank : normal);
      showBlank = !showBlank;
    }, 600);
  } else {
    if (trayFlashTimer) { clearInterval(trayFlashTimer); trayFlashTimer = null; }
    if (tray && !tray.isDestroyed()) tray.setImage(nativeImage.createFromPath(APP_ICON));
  }
}

/** 创建系统托盘：图标 + 右键菜单（显示/退出）+ 双击恢复窗口。已建过就不重复建。 */
function createTray(): void {
  if (tray && !tray.isDestroyed()) return;

  if (!existsSync(APP_ICON)) {
    console.error(`[main] ⚠ 托盘图标不存在：${APP_ICON} —— 托盘不创建（不影响主功能）。`);
    return;
  }

  const icon = nativeImage.createFromPath(APP_ICON);
  tray = new Tray(icon);
  tray.setToolTip('Knowe');

  const menu = Menu.buildFromTemplate([
    { label: '显示 Knowe', click: () => showMainWindow() },
    { type: 'separator' },
    {
      label: '退出 Knowe',
      click: () => {
        isQuitting = true; // 让 close 事件放行，别再拦成 hide
        app.quit();
      },
    },
  ]);
  // 不用 setContextMenu——Windows 上设了原生菜单后 click/mouse-down 全被吞掉。
  // 改为手动监听右键弹菜单，左键专注「显示/隐藏窗口」。

  // 左键单击 → 显示/隐藏主窗口
  tray.on('click', () => {
    destroyTrayCard();
    showMainWindow();
  });

  // 右键 → 弹出菜单
  tray.on('right-click', () => {
    tray!.popUpContextMenu(menu);
  });

  /*
   * [v1.0.19.1] 鼠标悬停 → 弹新消息卡片。
   *   只在「正在闪烁」时弹：不闪 = 没有需要现在告诉你的未读，别没事弹个空卡片出来。
   *   缓存为空同理——没有明细就没什么可列的，弹出来也是句「暂无未读消息」的空话。
   */
  tray.on('mouse-enter', () => {
    if (!tray || tray.isDestroyed()) return;
    if (!trayFlashTimer) return;                  // 没在闪 → 不弹
    if (unreadDetailsCache.length === 0) return;   // 无未读明细 → 不弹
    cancelDestroyTrayCard();                       // 取消可能正在排队的关闭（鼠标刚离开又回来）
    writeLog('[trayCard] tray mouse-enter → createTrayCard');
    createTrayCard(tray.getBounds(), unreadDetailsCache);
  });

  // 鼠标移出托盘图标区域 → 延迟关卡片（给人移动到卡片上的时间）。
  tray.on('mouse-leave', () => {
    writeLog('[trayCard] tray mouse-leave → scheduleDestroy');
    scheduleDestroyTrayCard();
  });
}

// ═══════════════════════════════════════════════════════════════
// 独立预览窗口
// ═══════════════════════════════════════════════════════════════

function isPlainDataObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function previewString(
  record: Record<string, unknown>,
  key: string,
  maxLength: number,
  required = false,
  allowNul = false,
  trim = true,
): string | undefined {
  const raw = record[key];
  if (raw === undefined || raw === null) {
    if (required) throw new Error(`preview payload missing ${key}`);
    return undefined;
  }
  if (typeof raw !== 'string') throw new Error(`preview payload invalid ${key}`);
  const value = trim ? raw.trim() : raw;
  if (
    (required && value.trim().length === 0)
    || value.length > maxLength
    || (!allowNul && value.includes('\u0000'))
  ) {
    throw new Error(`preview payload invalid ${key}`);
  }
  return value.length > 0 ? value : undefined;
}

function previewNumber(
  record: Record<string, unknown>,
  key: string,
  requireSafeInteger = true,
): number | undefined {
  const raw = record[key];
  if (raw === undefined || raw === null) return undefined;
  if (
    typeof raw !== 'number'
    || !Number.isFinite(raw)
    || raw < 0
    || !Number.isInteger(raw)
    || (requireSafeInteger && !Number.isSafeInteger(raw))
  ) {
    throw new Error(`preview payload invalid ${key}`);
  }
  return raw;
}

/** 对跨进程数据做白名单复制，拒绝原型对象、能力值和超长字段。 */
function normalizePreviewOpenPayload(value: unknown): PreviewOpenPayload {
  if (!isPlainDataObject(value)) throw new Error('preview payload must be a plain object');
  const rawFile = value.file;
  if (!isPlainDataObject(rawFile)) throw new Error('preview payload missing file');

  const path = previewString(rawFile, 'path', 8192, true, false, false)!;
  const name = previewString(rawFile, 'name', 1024, true, false, false)!;
  const file: PreviewFilePayload = { path, name };
  type PreviewStringKey = 'ext' | 'kind' | 'mtime' | 'file_id' | 'source_path';
  const optionalStrings: Array<[PreviewStringKey, number]> = [
    ['ext', 64],
    ['kind', 64],
    ['mtime', 128],
    ['file_id', 512],
    ['source_path', 8192],
  ];
  for (const [key, maxLength] of optionalStrings) {
    const next = previewString(
      rawFile,
      key,
      maxLength,
      false,
      false,
      key !== 'source_path',
    );
    if (next !== undefined) file[key] = next;
  }
  const bytes = previewNumber(rawFile, 'bytes');
  // 纳秒时间戳通常超过 JavaScript 安全整数；这里只要求它仍是有限非负整数。
  const mtimeNs = previewNumber(rawFile, 'mtime_ns', false);
  if (bytes !== undefined) file.bytes = bytes;
  if (mtimeNs !== undefined) file.mtime_ns = mtimeNs;

  return {
    projectId: previewString(value, 'projectId', 512, true)!,
    sourceKey: previewString(value, 'sourceKey', 12_288, true, true)!,
    file,
  };
}

function previewEntryUrl(): string {
  const devUrl = process.env.ELECTRON_RENDERER_URL;
  if (devUrl) return `${devUrl.replace(/\/$/, '')}/preview.html`;
  return pathToFileURL(join(__dirname, '..', 'renderer', 'preview.html')).href;
}

function mainEntryUrl(): string {
  const devUrl = process.env.ELECTRON_RENDERER_URL;
  if (devUrl) return devUrl;
  return pathToFileURL(join(__dirname, '..', 'renderer', 'index.html')).href;
}

function isMainTopLevelUrl(raw: unknown): boolean {
  return isPreviewTopLevelUrlUnknown(raw, mainEntryUrl());
}

function isPreviewTopLevelUrl(raw: unknown): boolean {
  return isPreviewTopLevelUrlUnknown(raw, previewEntryUrl());
}

function loadPreviewRenderer(win: BrowserWindow): void {
  const devUrl = process.env.ELECTRON_RENDERER_URL;
  if (devUrl) {
    void win.loadURL(`${devUrl.replace(/\/$/, '')}/preview.html`);
  } else {
    void win.loadFile(join(__dirname, '..', 'renderer', 'preview.html'));
  }
}

let runtimePolicyRegistered = false;

/** A runtime request is trusted only when Electron identifies the exact top frame. */
function trustedRuntimeRequest(
  details: Pick<Electron.OnBeforeSendHeadersListenerDetails, 'webContentsId' | 'frame'>,
): boolean {
  const win = appWindowForWebContents(details.webContentsId);
  if (!win) return false;
  return isTrustedTopLevelRuntimeFrame(
    details.frame,
    win.webContents.mainFrame,
    win === mainWindow
      ? isMainTopLevelUrl(win.webContents.getURL())
      : isPreviewTopLevelUrl(win.webContents.getURL()),
  );
}

/** WebSocket auth remains fail-closed without depending on optional frame metadata. */
function trustedRuntimeWebSocket(
  details: Pick<Electron.OnBeforeRequestListenerDetails,
    'url' | 'method' | 'resourceType' | 'webContentsId' | 'frame'>,
): boolean {
  if (!mainWindow || mainWindow.isDestroyed()) return false;
  return isAuthorizedRuntimeWebSocket({
    requestUrl: details.url,
    expectedWsUrl: RUNTIME_ENDPOINTS.wsUrl,
    resourceType: details.resourceType,
    method: details.method,
    requestWebContentsId: details.webContentsId,
    mainWebContentsId: mainWindow.webContents.id,
    requestFrame: details.frame,
    mainFrame: mainWindow.webContents.mainFrame,
    appOwnPage: isMainTopLevelUrl(mainWindow.webContents.getURL()),
    runtimeToken,
  });
}

/** 按 webContentsId 找应用自家窗口（主窗口/预览窗口）；找不到返回 null。 */
function appWindowForWebContents(webContentsId: number | undefined): BrowserWindow | null {
  if (typeof webContentsId !== 'number') return null;
  for (const win of [mainWindow, previewWindow]) {
    if (win && !win.isDestroyed() && win.webContents.id === webContentsId) return win;
  }
  return null;
}

/** One listener per event: inject the secret only for trusted outer Knowe renderers. */
function registerRuntimeRequestPolicy(targetSession: Session): void {
  if (runtimePolicyRegistered) return;
  runtimePolicyRegistered = true;
  const httpFilter = `${RUNTIME_ENDPOINTS.httpBase}/*`;
  // WebSocket 升级请求在 Chromium 中是 HTTP 请求，ws:// filter 可能不匹配。
  // 用 http:// 匹配 WS 端口，确保 token 能注入到升级请求头。
  const wsFilter = `${RUNTIME_ENDPOINTS.wsUrl.replace(/\/$/, '')}/*`;

  targetSession.webRequest.onBeforeSendHeaders(
    { urls: [httpFilter, wsFilter] },
    (details, callback) => {
      if (details.resourceType === 'webSocket') {
        callback({ requestHeaders: stripRuntimeTokenHeader(details.requestHeaders) });
        return;
      }
      if (runtimeToken && trustedRuntimeRequest(details)) {
        callback({
          requestHeaders: {
            ...details.requestHeaders,
            'X-Knowe-Runtime-Token': runtimeToken,
          },
        });
        return;
      }
      callback({ requestHeaders: details.requestHeaders });
    },
  );

  // Isolated project HTML shares the outer preview webContents, so the frame source is the decisive
  // boundary.  Block every control request even when a no-CORS tag or form would hide failure.
  targetSession.webRequest.onBeforeRequest(
    { urls: [httpFilter, wsFilter] },
    (details, callback) => {
      // Runtime endpoints are capabilities, not ordinary local web pages. Any
      // unknown, destroyed, or child-frame request is denied. In particular,
      // srcdoc frames have opaque/missing initiators but share webContentsId.
      const trusted = details.resourceType === 'webSocket'
        ? trustedRuntimeWebSocket(details)
        : trustedRuntimeRequest(details);
      callback({ cancel: !trusted });
    },
  );
}

function createPreviewWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 720,
    minHeight: 500,
    show: false,
    frame: true,
    title: 'Knowe 预览',
    icon: APP_ICON,
    backgroundColor: '#f5f5f5',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: resolvePreloadPath(),
      additionalArguments: [
        `--knowe-http-base=${RUNTIME_ENDPOINTS.httpBase}`,
        `--knowe-ws-url=${RUNTIME_ENDPOINTS.wsUrl}`,
        // [1.5] 告诉渲染进程当前是不是打包版（preload/渲染层据此决定日志、路径等行为）
        `--knowe-is-packaged=${app.isPackaged}`,
      ],
    },
  });
  previewWindow = win;
  previewRendererReady = false;

  win.webContents.on('preload-error', (_evt, preloadPath, error) => {
    console.error(`[main] ✘ 预览窗口 preload 加载失败：${preloadPath}\n${error?.stack ?? String(error)}`);
  });
  win.webContents.on('render-process-gone', () => {
    previewRendererReady = false;
  });
  win.webContents.on('did-start-navigation', (_event, _url, _isInPlace, isMainFrame) => {
    if (isMainFrame) previewRendererReady = false;
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isSafeExternalPreviewUrl(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (!isPreviewTopLevelUrl(url)) event.preventDefault();
  });
  win.webContents.on('will-frame-navigate', (details) => {
    try {
      const decision = parsePreviewNavigationDetails(details);
      if (!decision) {
        details.preventDefault();
        return;
      }
      if (decision.mainFrame) {
        if (!isPreviewTopLevelUrl(decision.url)) details.preventDefault();
        return;
      }
      const action = previewFrameNavigationAction(decision.url, decision.currentFrameUrl);
      if (action === 'allow') return;
      details.preventDefault();
      // Frame navigation is never promoted into a host-browser launch.  Trusted
      // Markdown links use target=_blank and the window-open handler above;
      // project HTML cannot turn a scripted location change into host activity.
    } catch (error: unknown) {
      // External Electron payloads are untrusted at runtime.  Any parser/policy failure
      // fails closed and only emits a bounded diagnostic; it must never crash main.
      details.preventDefault();
      const detail = error instanceof Error ? error.message.slice(0, 300) : String(error).slice(0, 300);
      console.error(`[main] preview frame navigation rejected after boundary error: ${detail}`);
    }
  });

  win.once('ready-to-show', () => {
    if (win.isDestroyed()) return;
    win.show();
    win.focus();
  });
  win.on('closed', () => {
    if (previewWindow === win) previewWindow = null;
    previewRendererReady = false;
    pendingPreviewOpens.length = 0;
  });
  loadPreviewRenderer(win);
  return win;
}

function ensurePreviewWindow(): BrowserWindow {
  if (!previewWindow || previewWindow.isDestroyed()) return createPreviewWindow();
  return previewWindow;
}

function enqueuePreviewOpen(payload: PreviewOpenPayload): void {
  const oldIndex = pendingPreviewOpens.findIndex((item) => item.sourceKey === payload.sourceKey);
  if (oldIndex >= 0) pendingPreviewOpens.splice(oldIndex, 1);
  pendingPreviewOpens.push(payload);
  if (pendingPreviewOpens.length > PREVIEW_OPEN_QUEUE_MAX) pendingPreviewOpens.shift();
}

function flushPendingPreviewOpens(): void {
  const win = previewWindow;
  if (!win || win.isDestroyed() || !previewRendererReady) return;
  const queued = pendingPreviewOpens.splice(0, pendingPreviewOpens.length);
  for (const payload of queued) win.webContents.send(IPC.previewOpened, payload);
}

async function openPreviewWindow(payload: PreviewOpenPayload): Promise<void> {
  const win = ensurePreviewWindow();
  if (win.isMinimized()) win.restore();
  if (!win.webContents.isLoadingMainFrame()) {
    if (!win.isVisible()) win.show();
    win.focus();
  }
  if (previewRendererReady) win.webContents.send(IPC.previewOpened, payload);
  else enqueuePreviewOpen(payload);
}

// ═══════════════════════════════════════════════════════════════
// IPC 接线（频道名全部取自 IPC 常量，一个字都不手写）
// ═══════════════════════════════════════════════════════════════

function registerIpc(): void {
  type FrameIpcEvent = Pick<IpcMainEvent | IpcMainInvokeEvent, 'sender' | 'senderFrame'>;
  const isTopFrame = (evt: FrameIpcEvent): boolean => {
    try {
      return evt.senderFrame !== null && evt.senderFrame === evt.sender.mainFrame;
    } catch {
      return false;
    }
  };
  const isMainSender = (evt: FrameIpcEvent): boolean => (
    isTopFrame(evt)
    && !!mainWindow
    && !mainWindow.isDestroyed()
    && evt.sender === mainWindow.webContents
  );
  const isPreviewSender = (evt: FrameIpcEvent): boolean => (
    isTopFrame(evt)
    && !!previewWindow
    && !previewWindow.isDestroyed()
    && evt.sender === previewWindow.webContents
  );
  const isAppSender = (evt: FrameIpcEvent): boolean => isMainSender(evt) || isPreviewSender(evt);
  const requireMainSender = (evt: FrameIpcEvent): void => {
    if (!isMainSender(evt)) throw new Error('IPC sender rejected');
  };
  const requireAppSender = (evt: FrameIpcEvent): void => {
    if (!isAppSender(evt)) throw new Error('IPC sender rejected');
  };

  // ① 问一次当前状态
  ipcMain.handle(IPC.getStatus, (evt) => {
    requireAppSender(evt);
    return snapshot();
  });

  // ①-b 获取本次 Runtime 认证令牌（供渲染进程注入 WebSocket URL）
  ipcMain.handle(IPC.getToken, (evt) => {
    requireMainSender(evt);
    return runtimeToken;
  });

  // ═══ [v1.0.25.4] 自动更新（PRD：静默检查 + 手动安装）═══
  // 产品版本号（package.json productVersion；UI 显示用，不再写死）
  ipcMain.handle(IPC.getProductVersion, (evt) => {
    requireAppSender(evt);
    return getProductVersion();
  });
  // 查询当前更新状态（idle/checking/downloading/ready/error）
  ipcMain.handle(IPC.updateStatus, (evt) => {
    requireMainSender(evt);
    return getUpdateStatus();
  });
  // 手动检查更新（设置页「检查更新」按钮；结果经 updateStatusChanged 推送）
  ipcMain.handle(IPC.updateCheck, async (evt) => {
    requireMainSender(evt);
    await updaterCheck(false);
  });
  // 触发「重启安装更新」：先优雅退出后端（避免安装器强杀残留）→ quitAndInstall
  ipcMain.handle(IPC.updateInstall, async (evt) => {
    requireMainSender(evt);
    await updaterInstall({ onInstall: killBackend });
  });

  // ② 重启后端：杀旧的 → 复用本 Electron 生命周期固定端口 → 起新的。
  // webRequest filters 与 renderer additionalArguments 在窗口创建时固化；
  // killBackend 已释放旧后端监听，重新避让会让 UI 和策略仍指向旧端口。
  ipcMain.handle(IPC.restart, async (evt) => {
    requireMainSender(evt);
    await killBackend();
    spawnBackend();
    return snapshot();
  });

  // ═══ [v1.0.19.4] 附件：HMAC 签名（与后端 attachments.sign_path 逐字一致）═══
  //   key = 持久附件签名密钥（getAttachmentSignKey，64 hex，按 utf-8 字节直接当密钥，不解码）；
  //   msg = 规范绝对路径的 utf-8 字节。后端用同一密钥 + os.path.abspath 复算校验。
  //   ★ 用持久密钥而非一进程一换的 runtimeToken：历史消息里的旧签名要在重启后仍然有效。
  const signAttachmentPath = (absPath: string): string =>
    createHmac('sha256', getAttachmentSignKey()).update(absPath, 'utf8').digest('hex');

  interface AttachmentPickWire {
    path: string; name: string; ext: string; size: number; sig: string;
  }
  const toAttachmentPick = (raw: string): AttachmentPickWire | null => {
    try {
      const abs = resolve(raw);
      const st = statSync(abs);
      if (!st.isFile()) return null;
      return {
        path: abs,
        name: basename(abs),
        ext: extname(abs).replace(/^\./, '').toLowerCase(),
        size: st.size,
        sig: signAttachmentPath(abs),
      };
    } catch {
      return null;
    }
  };
  const localFileAction = async (
    rawPath: unknown, sig: unknown, mode: 'open' | 'reveal',
  ): Promise<{ ok: boolean; reason?: 'missing' | 'guard' | 'error' }> => {
    if (typeof rawPath !== 'string' || typeof sig !== 'string') return { ok: false, reason: 'error' };
    const abs = resolve(rawPath);
    // 护栏：只肯打开主进程自己签发过的路径（渲染进程无法伪造签名）。
    if (signAttachmentPath(abs) !== sig) return { ok: false, reason: 'guard' };
    try {
      if (!statSync(abs).isFile()) return { ok: false, reason: 'missing' };
    } catch {
      return { ok: false, reason: 'missing' };   // 原文件已被移动/重命名/删除
    }
    try {
      if (mode === 'reveal') {
        shell.showItemInFolder(abs);
      } else {
        const err = await shell.openPath(abs);
        if (err) return { ok: false, reason: 'error' };
      }
      return { ok: true };
    } catch {
      return { ok: false, reason: 'error' };
    }
  };

  // ③-a [v1.0.19.4] 文件选择器（多选任意格式）→ 带签名的附件列表；取消 → []
  ipcMain.handle(IPC.selectFiles, async (evt) => {
    requireMainSender(evt);
    const win = mainWindow ?? undefined;
    const options: Electron.OpenDialogOptions = {
      title: '选择要发送的文件',
      buttonLabel: '添加',
      properties: ['openFile', 'multiSelections'],
    };
    const res = win
      ? await dialog.showOpenDialog(win, options)
      : await dialog.showOpenDialog(options);
    if (res.canceled || res.filePaths.length === 0) return [];
    return res.filePaths
      .map(toAttachmentPick)
      .filter((pick): pick is AttachmentPickWire => pick !== null);
  });

  // ③-b [v1.0.19.4] 给拖拽进来的本地路径补签名（drop 的 file.path 只有渲染进程拿得到）。
  ipcMain.handle(IPC.signDroppedFiles, async (evt, paths: unknown) => {
    requireMainSender(evt);
    if (!Array.isArray(paths)) return [];
    return paths
      .map((p) => (typeof p === 'string' ? toAttachmentPick(p) : null))
      .filter((pick): pick is AttachmentPickWire => pick !== null);
  });

  // ③-c [v1.0.19.4] 回看：用系统默认程序打开 / 在文件管理器定位一个本地附件。
  ipcMain.handle(IPC.openLocalFile, async (evt, path: unknown, sig: unknown) => {
    requireMainSender(evt);
    return localFileAction(path, sig, 'open');
  });
  ipcMain.handle(IPC.revealLocalFile, async (evt, path: unknown, sig: unknown) => {
    requireMainSender(evt);
    return localFileAction(path, sig, 'reveal');
  });

  // ③ 目录选择器：返回绝对路径；取消 → null
  ipcMain.handle(IPC.selectDirectory, async (evt) => {
    requireMainSender(evt);
    const win = mainWindow ?? undefined;
    const res = win
      ? await dialog.showOpenDialog(win, {
          title: '选择项目目录',
          buttonLabel: '选择这个目录',
          properties: ['openDirectory', 'createDirectory'],
        })
      : await dialog.showOpenDialog({
          title: '选择项目目录',
          buttonLabel: '选择这个目录',
          properties: ['openDirectory', 'createDirectory'],
        });
    if (res.canceled || res.filePaths.length === 0) return null;
    return res.filePaths[0] ?? null;
  });

  // ④ 用系统文件管理器打开目录（shell.openPath 语义；接口签名是 Promise<void>）
  ipcMain.handle(IPC.openPath, async (evt, dir: string) => {
    requireMainSender(evt);
    if (typeof dir === 'string' && dir.trim()) {
      await shell.openPath(dir);
    }
  });

  // ⑤ 未读数：单向通知（on，不 handle）。更新角标 + 有未读就闪任务栏 & 托盘图标。
  // [v1.0.20.2] 去掉「窗口不在前台才闪」的条件——用户明确：只要还有未读，图标就一直闪；
  // 点开一个会话只消化它自己的未读，剩下没点开的继续闪，直到全部点开或点「忽略全部」。
  ipcMain.on(IPC.setUnread, (evt, total: number) => {
    if (!isMainSender(evt)) return;
    const n = Number.isFinite(total) ? Math.max(0, Math.floor(total)) : 0;

    // Dock/任务栏数字角标（macOS / 部分 Linux 支持；Windows 上是 no-op，调了也不报错）
    if (typeof app.setBadgeCount === 'function') {
      app.setBadgeCount(n);
    }

    // 有未读 → 闪任务栏；没未读 → 停（把用户叫回来，正看着也继续闪，直到他处理完）
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.flashFrame(n > 0);
    }

    // 托盘图标闪烁：有未读 + 桌面通知开着，才闪（关桌面通知 = 不想被提醒）。
    setTrayFlashing(n > 0 && notifyPrefs.desktop);
  });

  // ⑤-b [v1.0.19.1] 未读明细：单向通知（on，不 handle）。只是存起来，mouse-enter 时才用。
  ipcMain.on(IPC.setUnreadDetails, (evt, details: UnreadDetail[]) => {
    if (!isMainSender(evt)) return;
    if (!Array.isArray(details)) return;
    unreadDetailsCache = details;
    // 卡片正开着的时候明细又变了（比如又来了一条）→ 就地刷新，别等下一次 mouse-enter。
    if (isTrayCardOpen() && tray && !tray.isDestroyed()) {
      if (unreadDetailsCache.length === 0) {
        destroyTrayCard(); // 未读清空了 → 没什么可列的，直接收掉卡片
      } else {
        cancelDestroyTrayCard();
        createTrayCard(tray.getBounds(), unreadDetailsCache);
      }
    }
  });

  // ⑤-c [v1.0.19.1] 托盘卡片点了某一行：关卡片 → 主窗口拉到前台 → 让它切到对应项目。
  ipcMain.on(IPC.trayCardClick, (evt, projectId: string) => {
    if (!isTopFrame(evt) || !isTrayCardSender(evt.sender)) return;
    destroyTrayCard();
    if (typeof projectId !== 'string' || !projectId) return;
    showMainWindow(); // 窗口可能缩在托盘/最小化里，先拉出来
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send(IPC.navigateToProject, projectId);
    }
  });

  // ⑤-d [v1.0.19.1] 鼠标进入了卡片 → 取消关闭定时器，卡片留在屏幕上。
  ipcMain.on(IPC.trayCardMouseEnter, (evt) => {
    if (!isTopFrame(evt) || !isTrayCardSender(evt.sender)) return;
    writeLog('[trayCard] IPC cardMouseEnter → cancel');
    cancelDestroyTrayCard();
  });

  // ⑤-e [v1.0.19.1] 鼠标离开了卡片 → 开始倒计时关闭。
  ipcMain.on(IPC.trayCardMouseLeave, (evt) => {
    if (!isTopFrame(evt) || !isTrayCardSender(evt.sender)) return;
    writeLog('[trayCard] IPC cardMouseLeave → schedule');
    scheduleDestroyTrayCard();
  });

  // ⑤-f [v1.0.20.2] 卡片点了「忽略全部」→ 熄灭所有闪烁，但**不动未读账目**。
  //   · 任务栏 flashFrame 停、托盘图标停（切回正常帧）
  //   · unreadDetailsCache 原样保留——悬停明细、主界面红圈数字都还在，
  //     只是图标不再跳动（忽略 ≠ 已读）
  //   · 之后前端任何一次 setUnread 推送（新消息来了、用户点开了会话）都会
  //     按新状态重新决定闪不闪——新消息来就重新闪，全部读完就自然停。
  ipcMain.on(IPC.trayCardIgnoreAll, (evt) => {
    if (!isTopFrame(evt) || !isTrayCardSender(evt.sender)) return;
    writeLog('[trayCard] IPC trayCardIgnoreAll → 停闪（未读保留）');
    destroyTrayCard();
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.flashFrame(false);
    setTrayFlashing(false);
  });

  // ⑥ [v1.0 fix-p3 #3] 桌面通知/关闭行为偏好：单向通知（on，不 handle）。
  ipcMain.on(IPC.setNotifyPrefs, (evt, prefs: { desktop?: boolean; closeToTray?: boolean }) => {
    if (!isMainSender(evt)) return;
    if (prefs && typeof prefs === 'object') {
      notifyPrefs = {
        desktop: prefs.desktop !== false,       // 缺省当开
        closeToTray: prefs.closeToTray !== false, // 缺省当开
      };
    }
    // 桌面通知刚被关掉 → 立刻停掉可能正在闪的托盘图标。
    if (!notifyPrefs.desktop) setTrayFlashing(false);
  });

  // ⑦ [v1.0 frameless] 窗口控制：frame:false 之后原生「— ▢ ✕」没了，App.tsx 标题栏上
  //    那三颗 .traffic 圆点（红=关闭 / 黄=最小化 / 绿=最大化↔还原）经 preload 桥打到这里。
  //    单向通知（on，不 handle）：按下去就完事，渲染端不需要回执。
  //    「关闭」走 win.close() 而**不是** destroy()：让上面 createWindow 里的 close 事件
  //    照常拦截，closeToTray「收进托盘」对自绘红点同样生效——和点原生 ✕ 一个待遇。
  ipcMain.on(WINDOW_CONTROL_CHANNEL, (evt, action: WindowControlAction) => {
    if (!isAppSender(evt)) return;
    // 从发件的 webContents 反查窗口，比全局 mainWindow 稳（将来多窗口也不串线）。
    const win = BrowserWindow.fromWebContents(evt.sender) ?? mainWindow;
    if (!win || win.isDestroyed()) return;
    switch (action) {
      case 'minimize':
        win.minimize();
        break;
      case 'toggle-maximize':
        if (win.isMaximized()) win.unmaximize();
        else win.maximize();
        break;
      case 'close':
        win.close();
        break;
      default:
        // 渲染端来的数据不可信：不认识的动作一律忽略（家规：桥只过数据，不过能力）。
        break;
    }
  });

  // ⑧ 主窗口请求打开独立预览；sender 必须正是主窗口。
  ipcMain.handle(IPC.openPreview, async (evt, raw: unknown) => {
    if (!isMainSender(evt)) {
      throw new Error('preview open sender rejected');
    }
    await openPreviewWindow(normalizePreviewOpenPayload(raw));
  });

  // ⑨ 预览 renderer 建好 listener 后再冲刷队列，避免首个打开请求丢失。
  ipcMain.on(IPC.previewReady, (evt) => {
    if (!isPreviewSender(evt)) return;
    previewRendererReady = true;
    flushPendingPreviewOpens();
  });

  // IPC.statusChanged 是主 → 渲染的单向推送，没有 handler，只在 setStatus 里 send。
}

// ═══════════════════════════════════════════════════════════════
// 窗口
// ═══════════════════════════════════════════════════════════════

function createWindow(): void {
  Menu.setApplicationMenu(null);
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    show: false, // 先不显示，等页面 ready-to-show 再亮，避免白屏闪一下
    // [v1.0 frameless] 无边框：去掉原生系统标题栏与窗框，标题栏由渲染进程自绘
    //   （App.tsx 的 .titlebar：左侧空白可拖拽，右侧 .traffic 三颗控制点）。
    //   可拖拽区/让位区全由既有 CSS 决定（.titlebar 是 -webkit-app-region:drag，
    //   .traffic / .title-right 是 no-drag），主进程只管两件事：这里把框拿掉、
    //   registerIpc ⑦ 把三颗点的动作接到真·窗口 API。
    //   不用 titleBarStyle/titleBarOverlay：那套画的是**原生**按钮，而需求就是要
    //   自绘的红黄绿圆点。窗口的缩放边、Aero Snap、投影由默认 thickFrame 保留，不受影响。
    frame: false,
    title: 'Knowe',
    // APP 窗口/任务栏图标：public/brand/app-icon.png（由 fix-assets 脚本从 Knowe图标2.png 复制而来）
    icon: join(PROJECT_ROOT, 'public', 'brand', 'app-icon.png'),
    webPreferences: {
      // 安全三件套：隔离开、Node 关、preload 只从编译产物取。
      contextIsolation: true,
      nodeIntegration: false,
      // Sandboxed preload still has Electron's contextBridge/ipcRenderer subset;
      // it does not need unrestricted Node.js or host filesystem access.
      sandbox: true,
      // Sandboxed preload 必须使用构建生成的 CommonJS 产物。
      preload: resolvePreloadPath(),
      additionalArguments: [
        `--knowe-http-base=${RUNTIME_ENDPOINTS.httpBase}`,
        `--knowe-ws-url=${RUNTIME_ENDPOINTS.wsUrl}`,
        // [1.5] 告诉渲染进程当前是不是打包版（preload/渲染层据此决定日志、路径等行为）
        `--knowe-is-packaged=${app.isPackaged}`,
        // [v1.0.25.4] 产品版本号（package.json productVersion）——UI 显示不再写死，
        //   与自动更新比较基准（package.json version，三段）分离。
        `--knowe-product-version=${getProductVersion()}`,
      ],
    },
  });

  // [v1.0 fix-p3 #2] preload 加载失败不再静默：把路径和错误堆栈大声打出来。
  //   —— 之前 window.knowe 缺方法却查不出原因，就是因为这个错误被吞了。
  mainWindow.webContents.on('preload-error', (_evt, preloadPath, error) => {
    console.error(`[main] ✘ preload 加载失败：${preloadPath}\n${error?.stack ?? String(error)}`);
  });
  // [v1.0.26.2] 主窗口外链拦截（此前漏装，设置-关于官网/GitHub 链接在内置窗口打开）：
  //   安全外链（http/https 非本机回环）→ 系统默认浏览器；其余一律 deny——主窗口没有合法
  //   新窗口需求（预览窗口由主进程 IPC 直建，不走 window.open）。
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isSafeExternalPreviewUrl(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  // 主窗口自身导航只允许精确的应用入口（不信任任意 file:// 页面），
  //   其他一律拦截；安全外链转系统浏览器（防 target=_self、JS 重定向绕过 window-open）。
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (isMainTopLevelUrl(url)) return;
    event.preventDefault();
    if (isSafeExternalPreviewUrl(url)) void shell.openExternal(url);
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
    // [v1.0.25.4] PRD 3.1：启动后延迟静默检查更新（不阻塞启动、不拖慢界面；
    //   检查/下载失败一律静默，由 updater.ts 内部处理）。
    setTimeout(() => {
      void updaterCheck(true);
    }, SILENT_CHECK_DELAY_MS);
  });

  // [v1.0 fix-p3 #3] 窗口重新拿到焦点 → 人回来了，停掉托盘闪烁。
  mainWindow.on('focus', () => setTrayFlashing(false));

  // [v1.0 fix-p3 #3] 点窗口 ✕：closeToTray 开着且不是真退出 → 拦下来，收进托盘（hide），
  //   不销毁窗口、不杀后端。closeToTray 关着，或走了「退出」→ 放行，照常关闭。
  mainWindow.on('close', (event) => {
    if (notifyPrefs.closeToTray && !isQuitting) {
      event.preventDefault();
      mainWindow?.hide();
    }
  });

  // Dev：electron-vite 注入的 renderer dev server 地址；Prod：编译后的静态 HTML。
  const devUrl = process.env.ELECTRON_RENDERER_URL;
  if (devUrl) {
    void mainWindow.loadURL(devUrl);
  } else {
    void mainWindow.loadFile(join(__dirname, '..', 'renderer', 'index.html'));
  }

  // 主窗口关闭只清引用；后端由应用真正退出时统一终止，预览窗口可继续工作。
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ═══════════════════════════════════════════════════════════════
// 应用生命周期
// ═══════════════════════════════════════════════════════════════

// 单实例锁：开第二个实例时，把已开的窗口顶到前面，自己退下。
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  // [v1.0 fix-p3 #3] 用 showMainWindow：窗口若正缩在托盘里（hidden），也能被顶出来。
  app.on('second-instance', () => showMainWindow());

  void app.whenReady().then(async () => {
    if (!electronDataRootBound) {
      dialog.showErrorBox('Knowe 无法启动', dataRootInitError?.message ?? '无法绑定内部数据目录。');
      app.quit();
      return;
    }
    // [1.2] 端口探测/避让必须先于 policy 注册与窗口创建：
    //       webRequest filter 的 URL 与 additionalArguments 的端口都是注册/创建时固化的。
    await ensurePorts();
    registerRuntimeRequestPolicy(session.defaultSession);
    registerIpc();
    // [v1.0.25.4] 自动更新模块初始化（状态事件经 IPC.updateStatusChanged 推送渲染层）
    initAutoUpdater({ log: writeLog, onInstall: killBackend });
    createWindow();
    // [v1.0.25.4] 升级后启动（安装器 --updated 拉起）：记日志 + 页面加载完成后通知渲染层
    //   （设置页 toast「已更新到最新版本」）。安装器 isUpdated 分支会传 --updated。
    if (process.argv.includes('--updated')) {
      writeLog('[main] 通过更新安装器启动（--updated）');
      mainWindow?.webContents.once('did-finish-load', () => {
        mainWindow?.webContents.send(IPC.updateJustUpdated);
      });
    }
    createTray();   // [v1.0 fix-p3 #3] 建系统托盘（图标 + 右键菜单 + 双击恢复）
    if (dataRootInitError) {
      setStatus('failed', `数据目录初始化失败：${dataRootInitError.message}`);
    } else {
      pruneOldLogs(); // [1.1] 启动时清理 7 天前的 backend_*.log（按天翻篇的旧文件）
      spawnBackend(); // 应用一起来就把后端拉起来（prompt：启动时自动 spawn）
    }

    // macOS：Dock 图标被点、且没有窗口时，重新开一扇。
    app.on('activate', () => {
      if (!mainWindow || mainWindow.isDestroyed()) createWindow();
      else showMainWindow();
    });
  });
}

// 关掉所有窗口时要不要退出应用：
//   · 正在真退出（isQuitting）→ 退。
//   · [v1.0 fix-p3 #3] closeToTray 开着 → 不退，留在托盘里候着
//     （正常这条走不到：closeToTray 下 ✕ 是 hide、窗口没销毁，本事件根本不触发；
//      这里是双保险，防某些平台/路径真把窗口关了也别顺手退了应用）。
//   · macOS 的 Dock 规矩：关掉所有窗口不等于退出应用。
//   · 其余情况照常退。
app.on('window-all-closed', () => {
  if (isQuitting) { app.quit(); return; }
  if (notifyPrefs.closeToTray) return;
  if (process.platform !== 'darwin') app.quit();
});

// 真正退出前：打上退出标记（让 close 事件放行）、把后端带走、销毁托盘。
app.on('before-quit', (event) => {
  isQuitting = true;
  if (allowQuit) return;
  event.preventDefault();
  if (quitCleanupStarted) return;
  quitCleanupStarted = true;
  void killBackend().finally(() => {
    setTrayFlashing(false);
    destroyTrayCard(); // [v1.0.19.1] 卡片是独立 BrowserWindow，退出前一并收掉
    if (tray && !tray.isDestroyed()) { tray.destroy(); tray = null; }
    allowQuit = true;
    app.quit();
  });
});
