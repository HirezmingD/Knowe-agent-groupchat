/**
 * bridge.ts — 主进程 ↔ 渲染进程之间那座桥的**类型契约**。
 *
 * 主进程（electron/main.ts）、preload（electron/preload.ts）、渲染进程都 import 这一个文件。
 * 桥上能过什么，只有这里说了算——preload 的白名单就是照着这个 interface 写的。
 *
 * 铁律：桥上**只过数据，不过能力**。
 *   不暴露 `spawn`、不暴露 `fs`、不暴露 `ipcRenderer` 本体。
 *   渲染进程只能问「后端现在怎么样了」和「帮我重启一下后端」，
 *   不能说「帮我执行这条命令」。
 */

/** 后端子进程的状态机 */
export type BackendPhase =
  | 'starting'    // 已经 spawn，还没通过健康检查
  | 'ready'       // /health 返回 ok
  | 'crashed'     // 进程意外退出
  | 'failed'      // 起不来（找不到 python、端口占用、健康检查一直不过）
  | 'stopped';    // 正常停止（退出流程中）

export interface BackendStatus {
  phase: BackendPhase;
  /** 人话，直接可以显示给用户看的一句话 */
  message: string;
  /** 子进程 pid（没起来时为 null） */
  pid: number | null;
  /** 最近几行后端日志——起不来的时候，这几行就是全部线索 */
  logTail: string[];
}

/** Non-sensitive local endpoints selected once by Electron main. */
export interface RuntimeEndpoints {
  readonly httpBase: string;
  readonly wsUrl: string;
}


/**
 * [v1.0.19.4] 用户选/拖进来的一个本地文件。path 是绝对路径；sig 是主进程用
 * runtime_token 对该路径的 HMAC 签名——后端凭它确认「这条路径确实是用户亲手选进来的」，
 * 拒绝消息正文里凭空捏造的任意路径（DESIGN 决策 #9 凭证护栏）。
 */
export type AttachmentPick = {
  path: string;
  name: string;
  ext?: string;
  size?: number;
  sig: string;
};

/** [v1.0.19.4] 本地附件的打开/定位结果；reason 用于区分「文件没了」与「校验没过」。 */
export type LocalFileResult = { ok: boolean; reason?: 'missing' | 'guard' | 'error' };

/** 独立预览窗口只接收可序列化的文件身份数据。 */
export type PreviewFilePayload = {
  path: string;
  name: string;
  ext?: string;
  kind?: string;
  bytes?: number;
  mtime?: string;
  file_id?: string;
  mtime_ns?: number;
  source_path?: string;
};

/** 主窗口请求独立预览窗口打开一个标签。 */
export type PreviewOpenPayload = {
  projectId: string;
  sourceKey: string;
  file: PreviewFilePayload;
  /** Optional raw Markdown fragment (without '#') to reveal after the target tab renders. */
  fragment?: string;
};

/** window.knowe —— 渲染进程能看到的全部东西 */
export interface KnoweBridge {
  /** 这是不是跑在 Electron 里（浏览器里打开时为 undefined，前端要能容忍） */
  readonly isElectron: true;
  /**
   * [阶段一 1.5] 正式版（打包安装版）判定。
   *   true  → 打包版：界面零开发痕迹（ConnBadge / BackendGate 故障 UI / DevDrawer 全部隐藏）。
   *   false → 开发态（electron . / vite dev）。
   *   老 preload 上没有该字段 → 渲染侧一律用 ?. 调；缺失时按开发态处理（宁多显示、不误伤）。
   */
  readonly isPackaged?: boolean;
  readonly version: string;
  /** 平台标识：process.platform 原样透传（'darwin' | 'win32' | 'linux'），渲染端做平台化 UI 分支（如 mac 隐藏自绘窗口按钮）。 */
  readonly platform: string;
  readonly runtimeEndpoints: RuntimeEndpoints;

  /** 问一次当前状态 */
  getBackendStatus(): Promise<BackendStatus>;
  /** 订阅状态变化。返回退订函数。 */
  onBackendStatus(cb: (s: BackendStatus) => void): () => void;
  /** 重启后端（后端崩了之后，界面上那个「重试」按钮） */
  restartBackend(): Promise<BackendStatus>;
  /** [v0.7 A0] 打开系统目录选择器。返回绝对路径；取消 → null */
  selectDirectory(): Promise<string | null>;

  /**
   * [v1.0.19.4] 打开系统文件选择器（可多选任意格式）。返回每个文件的
   *   路径 + 名称 + 扩展名 + 大小 + HMAC 签名；取消 → 空数组。
   *   老 preload 上没有 → 渲染侧用 ?. 调，缺了走「附件功能不可用」降级。
   */
  selectFiles?(): Promise<AttachmentPick[]>;
  /** [v1.0.19.4] 给拖拽进来的本地文件路径补签名（drop 的 path 只有渲染进程拿得到）。 */
  signDroppedFiles?(paths: string[]): Promise<AttachmentPick[]>;
  /** [v1.0.19.4] 用系统默认程序打开一个本地附件（回看预览）；校验签名 + 检查存在性。 */
  openLocalFile?(path: string, sig: string): Promise<LocalFileResult>;
  /** [v1.0.19.4] 在文件管理器里定位一个本地附件。 */
  revealLocalFile?(path: string, sig: string): Promise<LocalFileResult>;
  /** [v0.39.3] 用系统文件管理器打开指定路径（shell.openPath 语义）。 */
  openPath(dir: string): Promise<void>;

  /** 获取本次 Runtime 认证令牌（仅 Electron 内可用）。 */
  getRuntimeToken(): Promise<string>;
  /** 请求独立预览窗口打开或激活一个文件标签。 */
  openPreview(payload: PreviewOpenPayload): Promise<void>;
  /** 独立预览 renderer 订阅主进程转发的打开请求。 */
  onPreviewOpen(cb: (payload: PreviewOpenPayload) => void): () => void;
  /** 独立预览 renderer 已完成监听，可安全冲刷待发队列。 */
  previewReady(): void;

  /**
   * [v0.8d #5] 告诉主进程「现在总共有多少条未读」。
   *
   * 主进程拿它干两件事：
   *   · 窗口不在前台 + 有未读 → 任务栏图标闪烁（flashFrame）
   *   · macOS/Linux → Dock 上的数字角标
   *
   * 单向（send，不 invoke）：这是个**通知**，不是个问题，不需要回话。
   * 老 preload 上没有这个方法 —— 渲染进程必须容忍它是 undefined（用 ?. 调）。
   */
  setUnread?(total: number): void;

  /**
   * [v1.0.19.1] 推送「谁有未读、各几条」给主进程（托盘悬停卡片用）。
   *
   * 和 setUnread 是同一份账目的两个切面：setUnread 报的是总数（给任务栏/Dock 用），
   * 这个报的是明细（给托盘卡片列表用）——**一处记账（conv.unread），两处上报**，
   * 不会出现「总数对了，明细却是旧的」。
   * 只包含 unread > 0 的会话；单向（send，不 invoke），老 preload 上没有 → 用 ?. 调。
   *
   * [v1.0.20.2] 明细扩展：除 projectName/unread 外，还带该会话**最后一条消息**的
   * 发送者（姓名+职位）、预览、时间标签、头像 URL——托盘卡片按权威参考重做后
   * 需要这些字段渲染消息行。头像 URL 前端拼成完整地址（dev 是 http://127.0.0.1:5173
   * 前缀，因为托盘卡片是独立窗口，相对路径解析不到）。
   */
  setUnreadDetails?(details: UnreadDetail[]): void;

  /**
   * [v1.0.20.2] 托盘卡片点了「忽略全部」→ 告诉主进程熄灭所有图标闪烁。
   *
   * 语义：**只是不再闪，不是已读**——未读数、红圈、悬停明细全部保留，
   * 主界面打开后红点数字照旧。主进程只停 flashFrame + tray 闪烁，不动任何未读账目。
   * 单向（send，不 invoke）；老 preload 上没有 → 用 ?. 调。
   */
  trayCardIgnoreAll?(): void;

  /**
   * [v1.0.19.1] 订阅「托盘卡片点了某一行」→ 该跳到哪个项目。返回退订函数。
   *
   * 和 onBackendStatus 一个模式：只把 projectId 交给回调，event 对象不漏出去；
   * 组件卸载记得调退订函数。老 preload 上没有 → 用 ?. 调，返回 undefined 时不要崩。
   */
  onTrayCardNavigate?(cb: (projectId: string) => void): () => void;

  /**
   * [v1.0 fix-p3 #3] 把「桌面通知偏好」推给主进程。
   *
   *   · desktop      —— 桌面通知总开关（关掉就不闪任务栏、不闪托盘）
   *   · closeToTray  —— 点窗口 ✕ 时是收进托盘（true）还是直接退出（false）
   *
   * 主进程据此决定：关窗口是 hide 还是 quit、有未读时托盘图标闪不闪。
   * 单向（send，不 invoke）：这是个**通知**，不需要回话。
   * 和 setUnread 一样，老 preload 上没有 → 渲染侧用 ?. 调，缺了也不能崩。
   */
  setNotifyPrefs?(prefs: { desktop: boolean; closeToTray: boolean }): void;

  /**
   * [v1.0 frameless] 窗口控制（最小化 / 最大化↔还原 / 关闭）。
   *
   * 单向（send，不 invoke）：按下通知主进程执行真·窗口 API。
   * 老 preload 上没有 → 渲染侧用 invokeWindowControl 降级，缺了也不能崩。
   */
  windowControl?(action: 'minimize' | 'toggle-maximize' | 'close'): void;

  /**
   * [v1.0.25.4] 自动更新。
   *
   * 交互（PRD）：启动静默检查 + 有新版默默下载，全程零弹窗；设置-关于里
   * 「重启安装更新」按钮仅在「检测到新版本且下载完成」时出现。
   * 老 preload 上没有 → 渲染侧用 ?. 调，缺了按「无更新能力」降级（按钮不显示）。
   */
  /** 查询当前更新状态（idle / checking / downloading / ready / error）。 */
  getUpdateStatus?(): Promise<UpdateStatus>;
  /** 手动检查更新（设置页「检查更新」按钮）。结果经 onUpdateStatusChanged 推送。 */
  checkForUpdates?(): Promise<void>;
  /** 触发「重启安装更新」：优雅退出后端 → quitAndInstall。 */
  installUpdate?(): Promise<void>;
  /** 订阅更新状态推送（含下载进度）。返回退订函数。 */
  onUpdateStatusChanged?(cb: (s: UpdateStatus) => void): () => void;
  /** 订阅「本次启动由更新安装器拉起」事件（升级完成后 → toast 提示）。返回退订函数。 */
  onJustUpdated?(cb: () => void): () => void;
  /** 取产品版本号（package.json productVersion；无则退回 app.getVersion()）。 */
  getProductVersion?(): Promise<string>;
}

/**
 * [v1.0.20.2] 托盘悬停卡片的一行明细——一个未读会话。
 *
 * 前端从 store 组装（每个未读会话取**最后一条消息**作预览），经 setUnreadDetails
 * 推到主进程缓存，鼠标悬停托盘图标时渲染进卡片。
 */
export interface UnreadDetail {
  projectId: string;
  projectName: string;
  unread: number;
  /** 发送者显示名（「我」或成员 display.name）。 */
  sender: string;
  /** 发送者姓名（成员 display.name；用户消息为「我」）。 */
  memberName: string;
  /** 发送者职位（成员 display.role；用户消息为空）。 */
  memberRole: string;
  /** 最后一条消息的预览文本（已去换行、截断）。 */
  preview: string;
  /** 时间标签（今天 HH:mm / 昨天 / M月D日）。 */
  timeLabel: string;
  /** 最后一条消息的时间戳（毫秒）——排序用，新的在上。 */
  ts: number;
  /** 发送者头像：相对路径（'./avatars/...'）或完整 URL。空串=无头像。托盘卡片由主进程读文件转 base64 内联（data: 窗口禁 file://）。 */
  avatarUrl: string;
}

/**
 * [v1.0.25.4] 自动更新状态（主进程 updater.ts 单例维护，经 IPC 推送渲染层）。
 *
 * state 语义：
 *   idle        未检查/无新版本（按钮不显示）
 *   checking    正在检查（静默检查不推送此态；手动检查时推送）
 *   downloading 发现新版本，后台下载中（progress 百分比）
 *   ready       新版本下载完成，等待用户点击「重启安装更新」
 *   up-to-date  手动检查后确认当前已是最新（2.5 秒后自动回 idle；启动自动检查不推送）
 *   error       检查/下载失败（静默检查的失败不推送，仅手动检查推送）
 */
export interface UpdateStatus {
  state: 'idle' | 'checking' | 'downloading' | 'ready' | 'up-to-date' | 'error';
  /** downloading 时的进度 0-100；其余状态可为 0。 */
  progress: number;
  /** 新版本号（有可用更新时）。 */
  version?: string;
  /** error 时的人类可读原因。 */
  message?: string;
}

declare global {
  interface Window {
    /** 只有在 Electron 里才有；浏览器里是 undefined */
    knowe?: KnoweBridge;
  }
}

/** IPC 频道名——主进程和 preload 各写一遍就会写歪，所以只写一遍 */
export const IPC = {
  getStatus: 'knowe:backend:get-status',
  restart: 'knowe:backend:restart',
  statusChanged: 'knowe:backend:status-changed',
  /** [v0.7 A0] 目录选择器（原来在 main.ts 里写死的字符串，收进来） */
  selectDirectory: 'knowe:selectDirectory',
  /** [v0.39.3] 打开系统文件管理器到指定路径 */
  openPath: 'knowe:openPath',
  /** [v0.8d #5] 渲染进程 → 主进程：当前未读总数 */
  setUnread: 'knowe:unread:set',
  /** [v1.0.19.1] 渲染进程 → 主进程：推送有未读的会话明细（托盘悬停卡片用） */
  setUnreadDetails: 'knowe:unread:set-details',
  /** [v1.0.19.1] 主进程 → 渲染进程：托盘卡片点了某一行 → 跳转到该项目 */
  navigateToProject: 'knowe:navigate-project',
  /** [v1.0.19.1] 托盘卡片 renderer（独立 preload）→ 主进程：点了哪一行 */
  trayCardClick: 'knowe:tray-card-click',
  /** [v1.0.19.1] 托盘卡片 renderer → 主进程：鼠标进入了卡片区域 → 取消关闭 */
  trayCardMouseEnter: 'knowe:tray-card-mouse-enter',
  /** [v1.0.19.1] 托盘卡片 renderer → 主进程：鼠标离开了卡片区域 → 准备关闭 */
  trayCardMouseLeave: 'knowe:tray-card-mouse-leave',
  /** [v1.0.20.2] 托盘卡片 renderer → 主进程：点了「忽略全部」→ 停闪、不清未读 */
  trayCardIgnoreAll: 'knowe:tray-card-ignore-all',
  /** [v1.0 fix-p3 #3] 渲染进程 → 主进程：桌面通知/关闭行为偏好 */
  setNotifyPrefs: 'knowe:notify:set-prefs',
  /** 主窗口 → 主进程：打开独立预览窗口。 */
  openPreview: 'knowe:preview:open',
  /** 主进程 → 预览 renderer：打开或激活文件标签。 */
  previewOpened: 'knowe:preview:opened',
  /** 预览 renderer → 主进程：监听器已就绪。 */
  previewReady: 'knowe:preview:ready',
  /** 渲染进程 → 主进程：获取本次 Runtime 认证令牌。 */
  getToken: 'knowe:get-runtime-token',
  /** [v1.0.19.4] 打开文件选择器（多选），返回带签名的附件列表。 */
  selectFiles: 'knowe:attachments:select',
  /** [v1.0.19.4] 给拖拽的本地路径补签名。 */
  signDroppedFiles: 'knowe:attachments:sign',
  /** [v1.0.19.4] 用系统默认程序打开本地附件。 */
  openLocalFile: 'knowe:attachments:open',
  /** [v1.0.19.4] 在文件管理器定位本地附件。 */
  revealLocalFile: 'knowe:attachments:reveal',
  /** [v1.0.25.4] 渲染进程 → 主进程：查询当前更新状态。 */
  updateStatus: 'knowe:update:status',
  /** [v1.0.25.4] 渲染进程 → 主进程：手动检查更新。 */
  updateCheck: 'knowe:update:check',
  /** [v1.0.25.4] 渲染进程 → 主进程：触发重启安装更新。 */
  updateInstall: 'knowe:update:install',
  /** [v1.0.25.4] 主进程 → 渲染进程：更新状态推送（含下载进度）。 */
  updateStatusChanged: 'knowe:update:status-changed',
  /** [v1.0.25.4] 主进程 → 渲染进程：本次启动由更新安装器拉起（--updated）→ 设置页 toast。 */
  updateJustUpdated: 'knowe:update:just-updated',
  /** [v1.0.25.4] 渲染进程 → 主进程：取产品版本号（package.json productVersion）。 */
  getProductVersion: 'knowe:get-product-version',
} as const;
