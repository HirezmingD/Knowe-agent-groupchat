/**
 * windowControl.ts — 无边框窗口控制的共享约定（[v1.0 frameless]）。
 *
 * frame:false 之后原生「— ▢ ✕」没了，标题栏由 App.tsx 自绘（.titlebar + .traffic 三颗点）。
 * 点是画在渲染进程里的，动窗口却是主进程的特权——中间必须过 preload 那座
 * 「只过数据、不过能力」的桥。本文件就是三方（main / preload / renderer）唯一的接头暗号：
 *
 *   · WINDOW_CONTROL_CHANNEL —— IPC 频道名。照 bridge.ts 的家规：频道名一个字不许
 *     在 main / preload 里手写，全部从这里取，两头永远对得上。
 *     （不直接塞进 bridge.ts 的 IPC 常量表，是因为本次变更集不动 bridge.ts；
 *       日后合并进去时，把这里的常量改成 re-export 即可，三处调用点一行不用改。）
 *   · WindowControlAction —— 三个动作：minimize / toggle-maximize / close。
 *   · invokeWindowControl —— 渲染端唯一入口，自带降级（见下），组件里不要自己摸 window.knowe。
 *
 * 接线全景（谁改哪儿）：
 *   renderer  App.tsx 圆点 onClick → invokeWindowControl(action)          ← 本包已改
 *   preload   exposeInMainWorld('knowe', { windowControl: (a) =>
 *               ipcRenderer.send(WINDOW_CONTROL_CHANNEL, a) })            ← 见 FRAMELESS_PATCH_NOTES.md
 *   main      registerIpc ⑦ 收到后调真·窗口 API                           ← 本包已改
 */

/** 窗口控制 IPC 频道名（main 收、preload 发，双方都从这里 import）。 */
export const WINDOW_CONTROL_CHANNEL = 'knowe:window-control';

/** 三颗点各自的动作。绿点是「最大化↔还原」开关，所以叫 toggle 而不是单向 maximize。 */
export type WindowControlAction = 'minimize' | 'toggle-maximize' | 'close';

/** preload 桥上（window.knowe）本功能关心的那一小块形状——不引 KnoweBridge，避免类型耦合。 */
interface WindowControlBridgeShape {
  windowControl?: (action: WindowControlAction) => void;
}

/**
 * 渲染端安全入口：优先走 preload 桥；桥上还没这个方法（老 preload 产物，
 * 同 App.tsx 里 setUnread 的处境）就降级——
 *   · close    → window.close()。DOM 标准能力，最终同样触发 BrowserWindow 的 close
 *                事件，closeToTray「收进托盘」逻辑照常生效，和点原生 ✕ 一个待遇；
 *   · minimize / toggle-maximize → 没有 DOM 等价物，静默作罢（绝不能崩）。
 *
 * 用 globalThis 而不是 window：本模块也会被主进程 import（取常量），
 * main 的 tsconfig 未必带 DOM lib，直接写 window 可能过不了编译。
 */
export function invokeWindowControl(action: WindowControlAction): void {
  const g = globalThis as unknown as { knowe?: WindowControlBridgeShape; close?: () => void };
  const viaBridge = g.knowe?.windowControl;
  if (typeof viaBridge === 'function') {
    viaBridge(action);
    return;
  }
  if (action === 'close' && typeof g.close === 'function') {
    g.close();
  }
}
