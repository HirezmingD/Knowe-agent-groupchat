/** [v1.0.13][R5] Behavioral test for the generated out/main/index.js preview guard. */
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import vm from 'node:vm';

const required = [
  new URL('../backend/workspace_layout.py', import.meta.url),
  new URL('../backend/server.py', import.meta.url),
  new URL('../backend/engine.py', import.meta.url),
  new URL('../electron/main.ts', import.meta.url),
];
for (const url of required) assert.equal(existsSync(url), true, `required runtime source missing: ${url.pathname}`);

const bundlePath = new URL('../out/main/index.js', import.meta.url);
const mainPreloadPath = new URL('../out/preload/index.cjs', import.meta.url);
const trayPreloadPath = new URL('../out/preload/trayCard.cjs', import.meta.url);
let source;
try {
  source = readFileSync(bundlePath, 'utf8');
} catch (error) {
  throw new Error(`built main bundle is missing: ${bundlePath.pathname}`, { cause: error });
}

function extractNamedFunction(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} must remain identifiable in the built main bundle`);
  const brace = source.indexOf('{', start);
  assert.notEqual(brace, -1, `${name} has no body`);
  let depth = 0;
  let quote = '';
  let escaped = false;
  for (let index = brace; index < source.length; index += 1) {
    const ch = source[index];
    if (quote) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === quote) quote = '';
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') {
      quote = ch;
      continue;
    }
    if (ch === '{') depth += 1;
    if (ch === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`unterminated function ${name}`);
}

const context = vm.createContext({});
const code = [
  extractNamedFunction('isAllowedPreviewFrameUrl'),
  'globalThis.__guard = isAllowedPreviewFrameUrl;',
].join('\n');
vm.runInContext(code, context, { timeout: 1000, filename: 'built-preview-guard.vm.js' });
const guard = context.__guard;
assert.equal(typeof guard, 'function');

const retiredTree = 'http://p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.preview.localhost:8081/preview/tree/project-a/index.html';
for (const malformed of [undefined, null, 0, false, {}, [], '']) {
  assert.doesNotThrow(() => guard(malformed, undefined));
  assert.equal(guard(malformed, undefined), false, `must fail closed for ${String(malformed)}`);
}

for (const preloadPath of [mainPreloadPath, trayPreloadPath]) {
  assert.equal(
    existsSync(preloadPath),
    true,
    `sandboxed preload must be emitted as CommonJS: ${preloadPath.pathname}`,
  );
  const preloadSource = readFileSync(preloadPath, 'utf8');
  assert.match(preloadSource, /require\(["']electron["']\)/, 'preload must use Electron restricted require');
  assert.doesNotMatch(
    preloadSource,
    /(?:require\s*\(|from\s+|import\s*\()["']\.{1,2}[\\/]/,
    'sandboxed preload must be self-contained and must not load relative modules',
  );
  const requiredSpecifiers = Array.from(
    preloadSource.matchAll(/require\s*\(\s*["']([^"']+)["']\s*\)/g),
    (match) => match[1],
  );
  assert.deepEqual(
    [...new Set(requiredSpecifiers)].sort(),
    ['electron'],
    'sandboxed preload may only require Electron from its restricted loader',
  );
}
assert.deepEqual(
  readdirSync(new URL('../out/preload/', import.meta.url)).sort(),
  ['index.cjs', 'trayCard.cjs'],
  'sandboxed preload output must contain only the two isolated entry files',
);
assert.equal(
  existsSync(new URL('../out/preload/index.mjs', import.meta.url)),
  false,
  'sandboxed main preload must not regress to ESM',
);
assert.equal(
  existsSync(new URL('../out/preload/trayCard.mjs', import.meta.url)),
  false,
  'sandboxed tray preload must not regress to ESM',
);
assert.equal(guard('about:blank', undefined), true);
assert.equal(guard('about:srcdoc#fragment', null), true);
assert.equal(guard('about:srcdoc-escape', 'about:srcdoc'), false);
assert.equal(guard('data:text/html,<img src=https://example.com>', 'about:srcdoc'), false);
assert.equal(guard('blob:null/example', 'about:srcdoc'), false);
assert.equal(guard(retiredTree, 'about:blank'), false);
assert.equal(guard('http://127.0.0.1:8081/preview/tree/project-a/index.html', 'about:blank'), false);
assert.equal(source.includes('previewTokenForProject'), false, 'built app must not retain the retired preview capability');

assert.match(
  source,
  /will-frame-navigate[\s\S]{0,2500}(?:catch\s*\(|catch\s*\{)[\s\S]{0,900}preventDefault\(\)/,
  'built listener must catch boundary errors and fail closed',
);
console.log('built main preview guard: OK');
