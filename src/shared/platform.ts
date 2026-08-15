/**
 * platform.ts — 渲染端平台判断（[macOS R7]）。
 *
 * 平台信息来自 preload 桥暴露的 process.platform（见 bridge.ts / preload.ts），
 * 渲染进程据此做平台化 UI 分支。用 globalThis 而非 window：与 windowControl.ts
 * 同一理由，兼容无 DOM 的 import 上下文。
 *
 * App.tsx 的 IS_MAC 与 Composer 的 SEND_KEY 都从这里取，避免各组件重复判断。
 */

/** 是否 macOS（darwin）。 */
export const IS_MAC = (globalThis as { knowe?: { platform?: string } }).knowe?.platform === 'darwin';

/**
 * 发送快捷键的修饰键显示名：
 *   mac 用 ⌘（Command 符号），其余平台用 Ctrl。
 * 配合 i18n 的 interpolation（如「{{sendKey}}+Enter」）拼进提示文案。
 */
export const SEND_KEY = IS_MAC ? '⌘' : 'Ctrl';
