/** [v1.0.13][R5] Behavioral test for the generated out/main/index.js preview guard. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import vm from 'node:vm';

const required = [
  new URL('../backend/workspace_layout.py', import.meta.url),
  new URL('../backend/server.py', import.meta.url),
  new URL('../backend/engine.py', import.meta.url),
  new URL('../electron/main.ts', import.meta.url),
];
for (const url of required) assert.equal(existsSync(url), true, `required runtime source missing: ${url.pathname}`);

const bundlePath = new URL('../out/main/index.js', import.meta.url);
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

/**
 * [阶段二 2.2 修复] 从 bundle 动态提取 preview 守卫所需的常量定义。
 * 背景：rollup 打包时会为跨 chunk 重名的 const 加 `$1` 后缀（如 HTTP_PORT → HTTP_PORT$1），
 * 函数体里引用的是重命名后的名字。测试脚本若硬编码 `const HTTP_PORT = '8081'`，
 * vm 里会 ReferenceError（被 parsePreviewUrl 的 catch 吞掉 → 恒返回 null）。
 * 修法：用正则提取 bundle 里真实的 `const X = ...;` 定义注入 vm，变量名永远跟产物走。
 * 注意：常量名要支持 `$1` 后缀（HTTP_PORT$1），且按 bundle 定义顺序注入
 * （CONTROL_HOST 依赖 HTTP_PORT$1，必须在其后）。
 */
function extractConstDefs(source, names) {
  const out = [];
  for (const name of names) {
    const re = new RegExp(`const ${name}(?:\\$\\d+)? = [^;]+;`);
    const m = re.exec(source);
    if (!m) throw new Error(`bundle 里找不到 const ${name} 的定义（preview 守卫结构变了？）`);
    out.push(m[0]);
  }
  return out;
}

// vm 里注入 process 但清空 KNOWE_* 环境变量——bundle 里 HTTP_PORT$1 = String(process.env.KNOWE_HEALTH_PORT || "8081")，
// 若继承宿主 shell 的污染值（如 18081）会导致端口失配、守卫恒 false。空 env → 回退默认 8081，与测试构造的 URL 一致。
const context = vm.createContext({ URL, decodeURIComponent, createHash, process: { env: {} } });
const code = [
  ...extractConstDefs(source, ['HTTP_PORT', 'CONTROL_HOST', 'PREVIEW_HOST_RE', 'TREE_PREFIX']),
  extractNamedFunction('previewTokenForProject'),
  extractNamedFunction('projectFromTreePath'),
  extractNamedFunction('parsePreviewUrl'),
  extractNamedFunction('isAllowedPreviewFrameUrl'),
  extractNamedFunction('isPreviewControlRequest'),
  'globalThis.__guard = isAllowedPreviewFrameUrl;',
  'globalThis.__control = isPreviewControlRequest;',
  'globalThis.__token = previewTokenForProject;',
].join('\n');
vm.runInContext(code, context, { timeout: 1000, filename: 'built-preview-guard.vm.js' });
const guard = context.__guard;
const blocksControl = context.__control;
const tokenFor = context.__token;
assert.equal(typeof guard, 'function');
assert.equal(typeof blocksControl, 'function');
assert.equal(typeof tokenFor, 'function');

const projectA = 'project_20260730203701';
const projectB = 'project_20260730203702';
const originA = `http://p-${tokenFor(projectA)}.preview.localhost:8081`;
const originB = `http://p-${tokenFor(projectB)}.preview.localhost:8081`;
const p1 = `${originA}/preview/tree/${projectA}/index.html`;
const p1Child = `${originA}/assets/a.png`;
const p2 = `${originB}/preview/tree/${projectB}/index.html`;
for (const malformed of [undefined, null, 0, false, {}, [], '']) {
  assert.doesNotThrow(() => guard(malformed, undefined));
  assert.equal(guard(malformed, undefined), false, `must fail closed for ${String(malformed)}`);
}
assert.match(tokenFor(projectA), /^[0-9a-f]{32}$/);
assert.equal(guard('about:blank', undefined), true);
assert.equal(guard('about:srcdoc#fragment', null), true);
assert.equal(guard(p1, 'about:blank'), true);
assert.equal(guard(p1Child, p1), true);
assert.equal(guard(p2, p1), false);
assert.equal(guard(p1Child, 'about:srcdoc'), false);
assert.equal(blocksControl('http://127.0.0.1:8081/settings', p1), true);
assert.match(source, /p-\(\[0-9a-f\]\{32\}\)/, 'built preview host must use a 32-hex token');

assert.match(
  source,
  /will-frame-navigate[\s\S]{0,2500}(?:catch\s*\(|catch\s*\{)[\s\S]{0,900}preventDefault\(\)/,
  'built listener must catch boundary errors and fail closed',
);
console.log('built main preview guard: OK');
