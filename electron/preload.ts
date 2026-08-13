/**
 * preload.ts — 那座桥的**渲染进程这一头**。
 *
 * contextIsolation 开着，渲染进程本来什么 Node 能力都没有。这个文件用
 * contextBridge 在 window 上凿出一个洞，名叫 `window.knowe`——但洞里只放**数据通道**，
 * 不放**能力本体**：
 *
 *   放的是：「问后端状态」「请求重启」「选个目录」——每一个都只是把参数序列化过桥、
 *           再把结果拿回来。
 *   不放的是：ipcRenderer 本体、spawn、fs。渲染进程永远说不出「帮我执行这条命令」。
 *
 * 白名单照着 src/shared/bridge.ts 的 KnoweBridge 抄，一项不多一项不少；
 * 频道名一律取自 IPC 常量，不在这里手写字符串（写歪了就和主进程对不上）。
 */

import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';
import {
  IPC,
  type BackendStatus,
  type KnoweBridge,
  type PreviewOpenPayload,
  type RuntimeEndpoints,
  type UpdateStatus,
} from '../src/shared/bridge';
import { WINDOW_CONTROL_CHANNEL, type WindowControlAction } from '../src/shared/windowControl';

function argumentValue(name: string): string {
  const prefix = `--${name}=`;
  return process.argv.find((value) => value.startsWith(prefix))?.slice(prefix.length) ?? '';
}

/**
 * [阶段一 1.5] 正式版（打包安装版）判定，值源自主进程侧信息：
 *   1) 主进程 additionalArguments 显式传入的 `--knowe-is-packaged=true/false`
 *      —— 最权威，见 05-UI调试件清理/改动说明.md「待 main.ts 配合点」；
 *   2) 未传时兜底 process.defaultApp：开发态 `electron .` 启动时为 true，
 *      打包态（从安装目录 exe 启动）为 undefined/false。
 */
function detectPackaged(): boolean {
  const explicit = argumentValue('knowe-is-packaged');
  if (explicit === 'true') return true;
  if (explicit === 'false') return false;
  return process.defaultApp !== true;
}

const runtimeEndpoints: RuntimeEndpoints = Object.freeze({
  httpBase: argumentValue('knowe-http-base') || 'http://127.0.0.1:8081',
  wsUrl: argumentValue('knowe-ws-url') || 'ws://127.0.0.1:8080',
});

const api: KnoweBridge = {
  isElectron: true,
  /**
   * [阶段一 1.5] 正式版（打包安装版）判定，值来自主进程侧信息（见 detectPackaged）。
   * 渲染进程据此隐藏全部开发调试件：ConnBadge / BackendGate 故障 UI / DevDrawer。
   */
  isPackaged: detectPackaged(),
  // [v1.0.25.4] 产品版本号改由主进程注入（--knowe-product-version，源=package.json
  //   productVersion），不再手写——消除「UI 显示与包版本不同源」的硬编码隐患。
  version: argumentValue('knowe-product-version') || '0.0.0',
  runtimeEndpoints,

  // ── 后端状态：问一次 ──
  getBackendStatus: (): Promise<BackendStatus> => ipcRenderer.invoke(IPC.getStatus),

  // ── 后端状态：重启（界面上的「重试」按钮）──
  restartBackend: (): Promise<BackendStatus> => ipcRenderer.invoke(IPC.restart),

  /**
   * ── 后端状态：订阅推送，返回退订函数 ──
   *
   * 只把「一条新状态」交给回调——不把 event 对象、更不把 ipcRenderer 漏出去。
   * 退订时精确摘掉这一个 listener（别用 removeAllListeners 误伤别人的订阅）。
   * 组件卸载时必须调这个退订函数，否则 listener 越攒越多 = 内存泄漏（前端测试盯着这条）。
   */
  onBackendStatus: (cb: (s: BackendStatus) => void): (() => void) => {
    const handler = (_evt: IpcRendererEvent, status: BackendStatus): void => cb(status);
    ipcRenderer.on(IPC.statusChanged, handler);
    return () => { ipcRenderer.removeListener(IPC.statusChanged, handler); };
  },

  // ── 目录选择器：返回绝对路径；取消 → null ──
  selectDirectory: (): Promise<string | null> => ipcRenderer.invoke(IPC.selectDirectory),

  // ── [v1.0.19.4] 附件：选择 / 拖拽补签名 / 打开 / 定位 ──
  selectFiles: () => ipcRenderer.invoke(IPC.selectFiles),
  signDroppedFiles: (paths: string[]) => ipcRenderer.invoke(IPC.signDroppedFiles, paths),
  openLocalFile: (path: string, sig: string) => ipcRenderer.invoke(IPC.openLocalFile, path, sig),
  revealLocalFile: (path: string, sig: string) => ipcRenderer.invoke(IPC.revealLocalFile, path, sig),

  // ── 用系统文件管理器打开目录 ──
  openPath: (dir: string): Promise<void> => ipcRenderer.invoke(IPC.openPath, dir),

  // ── 获取本次 Runtime 认证令牌（供 WebSocket 连接用）──
  getRuntimeToken: (): Promise<string> => ipcRenderer.invoke(IPC.getToken),

  // ── 独立预览窗口：主窗口发请求，预览 renderer 收纯数据 ──
  openPreview: (payload: PreviewOpenPayload): Promise<void> => (
    ipcRenderer.invoke(IPC.openPreview, payload)
  ),

  onPreviewOpen: (cb: (payload: PreviewOpenPayload) => void): (() => void) => {
    const handler = (_evt: IpcRendererEvent, payload: PreviewOpenPayload): void => cb(payload);
    ipcRenderer.on(IPC.previewOpened, handler);
    return () => { ipcRenderer.removeListener(IPC.previewOpened, handler); };
  },

  previewReady: (): void => { ipcRenderer.send(IPC.previewReady); },

  /**
   * ── 未读数：单向通知（send，不 invoke）──
   * 这是「告诉主进程一声」，不是「问主进程要个回话」，所以用 send，不等返回。
   */
  setUnread: (total: number): void => { ipcRenderer.send(IPC.setUnread, total); },

  /**
   * ── [v1.0.19.1] 未读明细：单向通知（send，不 invoke）──
   * 同 setUnread，告诉主进程一声就够；主进程拿它去填托盘悬停卡片。
   */
  setUnreadDetails: (
    details: Array<{ projectId: string; projectName: string; unread: number }>,
  ): void => { ipcRenderer.send(IPC.setUnreadDetails, details); },

  /**
   * ── [v1.0.19.1] 托盘卡片点击 → 跳转到指定项目：订阅推送，返回退订函数 ──
   * 和 onBackendStatus 一个模式：只把 projectId 交给回调，别的什么都不漏。
   */
  onTrayCardNavigate: (cb: (projectId: string) => void): (() => void) => {
    const handler = (_evt: IpcRendererEvent, projectId: string): void => cb(projectId);
    ipcRenderer.on(IPC.navigateToProject, handler);
    return () => { ipcRenderer.removeListener(IPC.navigateToProject, handler); };
  },

  /**
   * ── [v1.0 fix-p3 #3] 桌面通知偏好：单向通知（send，不 invoke）──
   * 同 setUnread，告诉主进程一声就够，不等回话。
   */
  setNotifyPrefs: (prefs: { desktop: boolean; closeToTray: boolean }): void => {
    ipcRenderer.send(IPC.setNotifyPrefs, prefs);
  },

  // [v1.0 frameless] 窗口控制：红黄绿三颗点 → 主进程真·窗口 API
  windowControl: (action: WindowControlAction): void => {
    ipcRenderer.send(WINDOW_CONTROL_CHANNEL, action);
  },

  // ═══ [v1.0.25.4] 自动更新（PRD：静默检查 + 手动安装）═══
  getProductVersion: (): Promise<string> => ipcRenderer.invoke(IPC.getProductVersion),
  getUpdateStatus: () => ipcRenderer.invoke(IPC.updateStatus),
  checkForUpdates: (): Promise<void> => ipcRenderer.invoke(IPC.updateCheck),
  installUpdate: (): Promise<void> => ipcRenderer.invoke(IPC.updateInstall),
  onUpdateStatusChanged: (cb: (s: UpdateStatus) => void): (() => void) => {
    const handler = (_evt: IpcRendererEvent, s: UpdateStatus): void => cb(s);
    ipcRenderer.on(IPC.updateStatusChanged, handler);
    return () => { ipcRenderer.removeListener(IPC.updateStatusChanged, handler); };
  },
  onJustUpdated: (cb: () => void): (() => void) => {
    const handler = (): void => cb();
    ipcRenderer.on(IPC.updateJustUpdated, handler);
    return () => { ipcRenderer.removeListener(IPC.updateJustUpdated, handler); };
  },
};

// ── [v1.0 fix-p3 #2] 初始化日志 ──
// preload 静默失败是最难查的 bug（window.knowe 直接不存在，前端只能走浏览器兜底）。
// 这里在挂载前后各打一行：能看到「即将挂载」但看不到「已挂载」，就说明 exposeInMainWorld 抛了。
// 连「即将挂载」都看不到，说明 preload 压根没被加载（多半是主进程那边 preload 路径/扩展名不对，
// 去看主进程的 'preload-error' 日志）。
if (process.isMainFrame) {
  console.log('[preload] 初始化中… 即将在 window.knowe 上挂载 KnoweBridge');
  try {
    // Only the trusted top-level app/preview document gets the bridge. Project
    // HTML lives in a subframe and must never inherit runtime tokens or IPC.
    contextBridge.exposeInMainWorld('knowe', api);
    console.log('[preload] ✔ window.knowe 已挂载，方法：', Object.keys(api).join(', '));
  } catch (err) {
    console.error('[preload] ✘ 暴露 window.knowe 失败：', err);
  }
}
