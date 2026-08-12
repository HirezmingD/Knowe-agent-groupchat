/**
 * trayCardPreload.ts — 托盘卡片窗口的 preload 脚本。
 *
 * 极简：卡片窗口不是主窗口，不需要 KnoweBridge 那一整套。暴露三个方法——
 * clickProject（点某行跳转）、cardMouseEnter（鼠标进了卡片）、cardMouseLeave（鼠标离开）。
 * 频道名照家规从 bridge.ts 的 IPC 常量取，不在这里手写字符串。
 */

import { contextBridge, ipcRenderer } from 'electron';
import { IPC } from '../src/shared/bridge';

const api = {
  clickProject: (projectId: string): void => {
    ipcRenderer.send(IPC.trayCardClick, projectId);
  },
  cardMouseEnter: (): void => {
    ipcRenderer.send(IPC.trayCardMouseEnter);
  },
  cardMouseLeave: (): void => {
    ipcRenderer.send(IPC.trayCardMouseLeave);
  },
  /** [v1.0.20.2] 点了「忽略全部」：停闪、不清未读。 */
  ignoreAll: (): void => {
    ipcRenderer.send(IPC.trayCardIgnoreAll);
  },
};

contextBridge.exposeInMainWorld('trayCard', api);
