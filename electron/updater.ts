/**
 * [v1.0.25.4] updater.ts — 自动更新模块（electron-updater 封装）。
 *
 * 交互（PRD 01-需求/01-PRD-自动更新.md）：
 *   · 启动静默检查，有新版默默下载（autoDownload），全程零弹窗零提醒；
 *   · 设置-关于「重启安装更新」按钮仅在「检测到新版本且下载完成」（ready）时出现；
 *   · 点击按钮 → 先优雅退出后端 → quitAndInstall（安装器静默升级，保留用户数据）；
 *   · 下载前清空缓存目录（PRD 3.3 防积压：本地永远只留最新一个更新包）；
 *   · 安装完成后 electron-updater 自动清理安装包。
 *
 * 静默铁律：静默检查/下载的任何失败都不推送渲染层（保持 idle，下次启动再试）；
 * 仅「手动检查更新」的失败推送 error 供设置页反馈。
 */

import { BrowserWindow, shell } from 'electron';
// electron-updater 是 CommonJS 模块：ESM 命名导入（import { autoUpdater }）在 Electron 主进程
// 运行时解析失败（SyntaxError: Named export not found——cjs-module-lexer 无法静态分析其导出）。
// esModuleInterop 下 default import 拿到整个 module.exports，解构出 autoUpdater（官方建议写法）。
import updaterPkg from 'electron-updater';
const { autoUpdater } = updaterPkg;

import { IPC, type UpdateStatus } from '../src/shared/bridge';

/** updaterCacheDirName（app-update.yml 已定，与 electron-builder 生成一致）。 */
const UPDATER_CACHE_DIR_NAME = 'knowe-updater';

/** 当前状态单例（渲染层 getUpdateStatus 直接拿它）。 */
let status: UpdateStatus = { state: 'idle', progress: 0 };
let initialized = false;

/** [v1.0.26.2] 手动检查「已是最新」提示的展示时长，超时自动回 idle。 */
const UP_TO_DATE_HINT_MS = 2500;

/** [v1.0.26.2] 最近一次检查是否用户手动触发：仅手动检查的「无新版」推送 up-to-date。 */
let lastCheckWasManual = false;

type UpdaterLogger = (message: string) => void;

/** 推状态到所有窗口（渲染层订阅 IPC.updateStatusChanged）。 */
function push(s: UpdateStatus): void {
  status = s;
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) win.webContents.send(IPC.updateStatusChanged, s);
  }
}

/**
 * 初始化（app ready 后调用一次）。
 *
 * @param hooks.log        主进程日志（main.ts 的 writeLog）
 * @param hooks.onInstall  安装前回调（main.ts 注入：优雅退出后端）
 */
export function initAutoUpdater(hooks: { log: UpdaterLogger; onInstall: () => Promise<void> }): void {
  if (initialized) return;
  initialized = true;
  const log = hooks.log;

  // [macOS 手动下载] ad-hoc 签名无法走 ShipIt 静默安装（签名校验必失败），mac 上不后台下载
  //   （下载了也装不上）；检查到新版直接引导去 GitHub 下载 dmg。Windows 保持后台下载 + 一键安装。
  autoUpdater.autoDownload = process.platform !== 'darwin';
  autoUpdater.autoInstallOnAppQuit = false;  // PRD 3.4：用户手动触发安装
  autoUpdater.logger = null;                 // 静默（不往 electron-updater 默认 logger 刷屏）

  autoUpdater.on('checking-for-update', () => {
    push({ state: 'checking', progress: 0 });
  });
  autoUpdater.on('update-available', (info) => {
    if (process.platform === 'darwin') {
      // [macOS 手动下载] 不下载，直接进 ready（语义 = 有新版可去下载），前端显示「去下载」按钮
      log(`[updater] 发现新版本 ${info.version}（mac 手动下载模式，引导去 GitHub）`);
      push({ state: 'ready', progress: 100, version: info.version });
    } else {
      log(`[updater] 发现新版本 ${info.version}，开始后台下载`);
      push({ state: 'downloading', progress: 0, version: info.version });
    }
  });
  autoUpdater.on('update-not-available', () => {
    // [v1.0.26.2] 手动检查无新版 → 推送 up-to-date 提示（2.5 秒后自动回 idle）；
    //   启动自动检查保持静默（不打扰用户，行为同旧版）。
    if (lastCheckWasManual) {
      push({ state: 'up-to-date', progress: 0 });
      setTimeout(() => {
        if (status.state === 'up-to-date') push({ state: 'idle', progress: 0 });
      }, UP_TO_DATE_HINT_MS);
    } else {
      push({ state: 'idle', progress: 0 });
    }
    lastCheckWasManual = false;
  });
  autoUpdater.on('download-progress', (p) => {
    push({ state: 'downloading', progress: Math.round(p.percent * 10) / 10, version: status.version });
  });
  autoUpdater.on('update-downloaded', (info) => {
    log(`[updater] 新版本 ${info.version} 下载完成，等待用户安装`);
    push({ state: 'ready', progress: 100, version: info.version });
  });
  autoUpdater.on('error', (err) => {
    // PRD 3.1/3.2 静默铁律：检查/下载失败不打扰用户，回 idle 下次再试。
    // 手动检查的失败由 checkForUpdates(silent=false) 的 catch 分支推送 error。
    const msg = err instanceof Error ? err.message : String(err);
    log(`[updater] 更新错误（静默忽略）：${msg}`);
    if (status.state === 'checking') push({ state: 'idle', progress: 0 });
    else if (status.state === 'downloading') push({ state: 'idle', progress: 0 });
  });
}

/** 查询当前更新状态（渲染层 getUpdateStatus）。 */
export function getUpdateStatus(): UpdateStatus {
  return status;
}

/**
 * 检查更新。
 *
 * @param silent true = 启动静默检查（失败不推送 error，保持 idle）；false = 手动检查（失败推送 error）。
 */
export async function checkForUpdates(silent: boolean): Promise<void> {
  lastCheckWasManual = !silent;
  try {
    await autoUpdater.checkForUpdates();
  } catch (e) {
    lastCheckWasManual = false;
    const msg = e instanceof Error ? e.message : String(e);
    if (silent) {
      // PRD 3.1：静默忽略
      if (status.state === 'idle') return;
    } else {
      push({ state: 'error', progress: 0, message: msg });
    }
  }
}

/**
 * [PRD 3.4] 触发「重启安装更新」：先优雅退出后端（避免安装器强杀导致后端残留），
 * 再 quitAndInstall（安装器静默升级、保留数据，完成后以 --updated 拉起新版）。
 */
export async function installUpdate(hooks: { onInstall: () => Promise<void> }): Promise<void> {
  if (status.state !== 'ready') return;
  if (process.platform === 'darwin') {
    // [macOS 手动下载] ad-hoc 签名无法走 ShipIt 静默安装，改为打开 GitHub 最新 release 下载页，
    //   用户手动下载 dmg 拖入「应用程序」安装（数据在 Application Support，替换 app 外壳不丢数据）。
    void shell.openExternal('https://github.com/HirezmingD/Knowe-agent-groupchat/releases/latest');
    return;
  }
  try {
    await hooks.onInstall();
  } catch (e) {
    // 退出后端失败不阻塞安装（安装器有强杀兜底，最坏是后端进程残留，端口有避让机制）
    console.error(`[updater] 安装前退出后端失败（继续安装）：${e instanceof Error ? e.message : String(e)}`);
  }
  autoUpdater.quitAndInstall(false, true);
}
