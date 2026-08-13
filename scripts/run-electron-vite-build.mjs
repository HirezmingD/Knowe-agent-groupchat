/**
 * Run electron-vite's programmatic build in both interactive terminals and CI.
 *
 * electron-vite 5's isolatedEntries reporter assumes stdout is a TTY and calls
 * cursor methods unconditionally. Windows CI/agent pipes do not expose those
 * methods, so provide harmless no-ops before loading electron-vite. This does
 * not change the build; it only keeps progress rendering from crashing it.
 */
for (const method of ['clearLine', 'cursorTo', 'moveCursor']) {
  if (typeof process.stdout[method] !== 'function') {
    process.stdout[method] = () => {};
  }
}

const { build } = await import('electron-vite');
await build();
