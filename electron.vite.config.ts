/**
 * electron.vite.config.ts — electron-vite 的三段式构建配置。
 *
 * electron-vite 把一个 Electron 应用拆成三块分别打包，各进各的 out 子目录：
 *   · main     → out/main/index.js      （主进程，Node 环境）
 *   · preload  → out/preload/index.js   （桥的渲染侧，特殊的沙箱前环境）
 *   · renderer → out/renderer/          （React 页面，浏览器环境）
 *
 * main 和 preload 都用 externalizeDepsPlugin()：把 electron 及 node_modules 里的依赖
 *   留成 require 外链，不打进 bundle——主进程/preload 跑在 Node 里，本来就能 require，
 *   打进去只会变大变慢、还可能把原生模块打坏。
 * renderer 用 @vitejs/plugin-react：和纯前端那套一致（JSX / Fast Refresh）。
 *
 * 三个入口路径都和 package.json 的 "main": "out/main/index.js" 对齐。
 */

import { resolve } from 'node:path';
import { defineConfig, externalizeDepsPlugin } from 'electron-vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  // ── 主进程 ──
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'electron/main.ts') },
      },
    },
  },

  // ── preload（桥的渲染侧）──
  //   两个入口打两份独立产物：index.* 是主窗口的桥（KnoweBridge 全套），
  //   trayCard.* 是托盘卡片窗口的桥（只有一个 clickProject，见 trayCardPreload.ts）。
  //   互不相干——卡片窗口没有理由拿到主窗口那一整套桥方法。
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'electron/preload.ts'),
          trayCard: resolve(__dirname, 'electron/trayCardPreload.ts'),
        },
      },
    },
  },

  // ── renderer（React 页面）──
  renderer: {
    // 渲染进程的根就是项目根，入口是根目录的 index.html。
    root: '.',
    plugins: [react()],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src'),
      },
    },
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'index.html'),
          preview: resolve(__dirname, 'preview.html'),
        },
      },
    },
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: false,
      watch: { ignored: ['**/data/**', '**/Logs/**'] },
    },
  },
});
