/** 只读代码预览使用的零依赖、非执行式语法分类器。 */

export type CodeLanguage =
  | 'json' | 'javascript' | 'typescript' | 'python' | 'html' | 'css'
  | 'yaml' | 'toml' | 'xml' | 'sql' | 'shell' | 'powershell' | 'batch'
  | 'java' | 'kotlin' | 'c' | 'cpp' | 'csharp' | 'fsharp' | 'visualbasic' | 'go' | 'rust' | 'ruby'
  | 'php' | 'swift' | 'dart' | 'scala' | 'lua' | 'r' | 'perl' | 'elixir'
  | 'erlang' | 'clojure' | 'groovy' | 'graphql' | 'protobuf' | 'dockerfile'
  | 'makefile' | 'cmake' | 'ini' | 'vue' | 'svelte' | 'astro';

export type TokenKind =
  | 'plain' | 'comment' | 'string' | 'number' | 'keyword' | 'literal'
  | 'property' | 'tag' | 'operator' | 'variable' | 'meta';

export interface CodeToken { text: string; kind: TokenKind }
export interface HighlightedLine { tokens: CodeToken[] }

const EXT_LANG: Record<string, CodeLanguage> = {
  json: 'json', jsonc: 'json', json5: 'json',
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript', mts: 'typescript', cts: 'typescript',
  py: 'python', pyw: 'python',
  html: 'html', htm: 'html', xhtml: 'html',
  css: 'css', scss: 'css', sass: 'css', less: 'css',
  yaml: 'yaml', yml: 'yaml', toml: 'toml',
  xml: 'xml', xsd: 'xml', xsl: 'xml', xslt: 'xml',
  sql: 'sql',
  sh: 'shell', bash: 'shell', zsh: 'shell', fish: 'shell',
  ps1: 'powershell', psm1: 'powershell', psd1: 'powershell',
  bat: 'batch', cmd: 'batch',
  ini: 'ini', cfg: 'ini', conf: 'ini', env: 'ini', properties: 'ini',
  java: 'java', kt: 'kotlin', kts: 'kotlin',
  c: 'c', h: 'c', cc: 'cpp', cpp: 'cpp', cxx: 'cpp', hpp: 'cpp', hxx: 'cpp',
  cs: 'csharp', fs: 'fsharp', fsx: 'fsharp', vb: 'visualbasic', go: 'go', rs: 'rust', rb: 'ruby', php: 'php',
  swift: 'swift', dart: 'dart', scala: 'scala', lua: 'lua', r: 'r',
  pl: 'perl', pm: 'perl', ex: 'elixir', exs: 'elixir', erl: 'erlang', hrl: 'erlang',
  clj: 'clojure', cljs: 'clojure', cljc: 'clojure', edn: 'clojure',
  groovy: 'groovy', gradle: 'groovy', gql: 'graphql', graphql: 'graphql',
  proto: 'protobuf', vue: 'vue', svelte: 'svelte', astro: 'astro', cmake: 'cmake',
};

const SPECIAL_NAMES: Record<string, CodeLanguage> = {
  dockerfile: 'dockerfile', containerfile: 'dockerfile',
  makefile: 'makefile', gnumakefile: 'makefile',
  'cmakelists.txt': 'cmake', jenkinsfile: 'groovy',
  '.gitignore': 'ini', '.dockerignore': 'ini', '.editorconfig': 'ini',
};

const LABELS: Record<CodeLanguage, string> = {
  json: 'JSON', javascript: 'JavaScript', typescript: 'TypeScript', python: 'Python',
  html: 'HTML', css: 'CSS', yaml: 'YAML', toml: 'TOML', xml: 'XML', sql: 'SQL',
  shell: 'Shell', powershell: 'PowerShell', batch: 'Batch', java: 'Java', kotlin: 'Kotlin',
  c: 'C', cpp: 'C++', csharp: 'C#', fsharp: 'F#', visualbasic: 'Visual Basic', go: 'Go', rust: 'Rust', ruby: 'Ruby', php: 'PHP',
  swift: 'Swift', dart: 'Dart', scala: 'Scala', lua: 'Lua', r: 'R', perl: 'Perl',
  elixir: 'Elixir', erlang: 'Erlang', clojure: 'Clojure', groovy: 'Groovy',
  graphql: 'GraphQL', protobuf: 'Protocol Buffers', dockerfile: 'Dockerfile',
  makefile: 'Makefile', cmake: 'CMake', ini: 'Config', vue: 'Vue', svelte: 'Svelte', astro: 'Astro',
};

export function codeLanguageFor(name: string, ext?: string): CodeLanguage | null {
  const lowerName = (name || '').trim().toLowerCase();
  if (SPECIAL_NAMES[lowerName]) return SPECIAL_NAMES[lowerName];
  const normalizedExt = (ext || lowerName.split('.').pop() || '').replace(/^\./, '').toLowerCase();
  return EXT_LANG[normalizedExt] || null;
}

export function codeLanguageLabel(language: CodeLanguage): string {
  return LABELS[language];
}

const COMMON = new Set([
  'as', 'async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue',
  'default', 'do', 'else', 'enum', 'export', 'extends', 'finally', 'for', 'from',
  'function', 'if', 'implements', 'import', 'in', 'interface', 'let', 'match',
  'new', 'of', 'package', 'private', 'protected', 'public', 'return', 'static',
  'struct', 'super', 'switch', 'throw', 'throws', 'try', 'type', 'typeof', 'var',
  'while', 'with', 'yield', 'def', 'lambda', 'pass', 'raise', 'global', 'nonlocal',
  'fn', 'func', 'trait', 'impl', 'where', 'use', 'mod', 'pub', 'mut', 'unsafe',
  'namespace', 'using', 'virtual', 'override', 'abstract', 'readonly', 'operator',
]);

const SQL = new Set([
  'select', 'from', 'where', 'join', 'left', 'right', 'inner', 'outer', 'on', 'group',
  'by', 'order', 'having', 'limit', 'offset', 'insert', 'into', 'values', 'update',
  'set', 'delete', 'create', 'alter', 'drop', 'table', 'view', 'index', 'primary',
  'key', 'foreign', 'references', 'constraint', 'distinct', 'union', 'all', 'and',
  'or', 'not', 'is', 'null', 'like', 'between', 'exists', 'asc', 'desc', 'with',
]);

const SHELL = new Set([
  'if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'until', 'do', 'done', 'case',
  'esac', 'in', 'function', 'select', 'time', 'coproc', 'export', 'local', 'readonly',
]);

const LITERALS = new Set([
  'true', 'false', 'null', 'undefined', 'none', 'nil', 'nan', 'infinity', 'self',
  'this', 'yes', 'no', 'on', 'off',
]);

function keywordSet(language: CodeLanguage): Set<string> {
  if (language === 'sql') return SQL;
  if (language === 'shell' || language === 'powershell' || language === 'batch') return SHELL;
  return COMMON;
}

function push(tokens: CodeToken[], text: string, kind: TokenKind): void {
  if (!text) return;
  const last = tokens[tokens.length - 1];
  if (last?.kind === kind) last.text += text;
  else tokens.push({ text, kind });
}

function isIdentifierStart(ch: string): boolean {
  return /[A-Za-z_$\u0080-\uFFFF]/.test(ch);
}
function isIdentifierPart(ch: string): boolean {
  return /[\w$\u0080-\uFFFF]/.test(ch);
}
function nextNonSpace(line: string, from: number): string {
  let i = from;
  while (i < line.length && /\s/.test(line.charAt(i))) i += 1;
  return line.charAt(i);
}

interface LexState { blockEnd: string; markupComment: boolean; inTag: boolean; expectTag: boolean }

function lineCommentMarkers(language: CodeLanguage): string[] {
  if (['python', 'ruby', 'shell', 'powershell', 'yaml', 'toml', 'r', 'perl', 'makefile', 'dockerfile'].includes(language)) return ['#'];
  if (['sql', 'lua'].includes(language)) return ['--'];
  if (language === 'batch' || language === 'ini') return [';', 'REM '];
  if (['json'].includes(language)) return [];
  return ['//'];
}

function blockPair(language: CodeLanguage): [string, string] | null {
  if (['python', 'ruby', 'shell', 'powershell', 'yaml', 'toml', 'batch', 'ini', 'makefile', 'dockerfile'].includes(language)) return null;
  if (language === 'html' || language === 'xml' || language === 'vue' || language === 'svelte' || language === 'astro') return ['<!--', '-->'];
  return ['/*', '*/'];
}

function startsLineComment(line: string, i: number, markers: string[]): string {
  for (const marker of markers) {
    if (marker === 'REM ') {
      if (i === 0 && line.slice(i, i + 4).toUpperCase() === marker) return marker;
    } else if (line.startsWith(marker, i)) return marker;
  }
  return '';
}

function highlightMarkupLine(line: string, state: LexState): CodeToken[] {
  const tokens: CodeToken[] = [];
  let i = 0;
  while (i < line.length) {
    if (state.markupComment) {
      const end = line.indexOf('-->', i);
      if (end < 0) { push(tokens, line.slice(i), 'comment'); return tokens; }
      push(tokens, line.slice(i, end + 3), 'comment');
      state.markupComment = false; i = end + 3; continue;
    }
    if (line.startsWith('<!--', i)) {
      const end = line.indexOf('-->', i + 4);
      if (end < 0) { push(tokens, line.slice(i), 'comment'); state.markupComment = true; return tokens; }
      push(tokens, line.slice(i, end + 3), 'comment'); i = end + 3; continue;
    }
    const ch = line.charAt(i);
    if (!state.inTag) {
      if (ch === '<') {
        let n = i + 1;
        if (line.charAt(n) === '/' || line.charAt(n) === '!' || line.charAt(n) === '?') n += 1;
        push(tokens, line.slice(i, n), line.charAt(i + 1) === '!' || line.charAt(i + 1) === '?' ? 'meta' : 'operator');
        state.inTag = true; state.expectTag = true; i = n; continue;
      }
      if (ch === '&') {
        const end = line.indexOf(';', i + 1);
        if (end > i) { push(tokens, line.slice(i, end + 1), 'literal'); i = end + 1; continue; }
      }
      const next = line.indexOf('<', i);
      const entity = line.indexOf('&', i);
      const stop = [next, entity].filter((n) => n >= 0).sort((a, b) => a - b)[0] ?? line.length;
      push(tokens, line.slice(i, stop), 'plain'); i = stop; continue;
    }
    if (ch === '>') { push(tokens, ch, 'operator'); state.inTag = false; i += 1; continue; }
    if (line.startsWith('/>', i) || line.startsWith('?>', i)) {
      push(tokens, line.slice(i, i + 2), 'operator'); state.inTag = false; i += 2; continue;
    }
    if (/\s/.test(ch)) { let j = i + 1; while (j < line.length && /\s/.test(line.charAt(j))) j += 1; push(tokens, line.slice(i, j), 'plain'); i = j; continue; }
    if (ch === '"' || ch === "'") {
      let j = i + 1;
      while (j < line.length) { if (line.charAt(j) === ch && line.charAt(j - 1) !== '\\') { j += 1; break; } j += 1; }
      push(tokens, line.slice(i, j), 'string'); i = j; continue;
    }
    if (isIdentifierStart(ch) || ch === ':' || ch === '-') {
      let j = i + 1; while (j < line.length && /[\w:$.-]/.test(line.charAt(j))) j += 1;
      push(tokens, line.slice(i, j), state.expectTag ? 'tag' : 'property');
      state.expectTag = false; i = j; continue;
    }
    push(tokens, ch, 'operator'); i += 1;
  }
  return tokens;
}

function highlightGenericLine(line: string, language: CodeLanguage, state: LexState): CodeToken[] {
  const tokens: CodeToken[] = [];
  const comments = lineCommentMarkers(language);
  const pair = blockPair(language);
  const keywords = keywordSet(language);
  let i = 0;
  while (i < line.length) {
    if (state.blockEnd) {
      const end = line.indexOf(state.blockEnd, i);
      if (end < 0) { push(tokens, line.slice(i), 'comment'); return tokens; }
      push(tokens, line.slice(i, end + state.blockEnd.length), 'comment');
      i = end + state.blockEnd.length; state.blockEnd = ''; continue;
    }
    const comment = startsLineComment(line, i, comments);
    if (comment) { push(tokens, line.slice(i), 'comment'); break; }
    if (pair && line.startsWith(pair[0], i)) {
      const end = line.indexOf(pair[1], i + pair[0].length);
      if (end < 0) { push(tokens, line.slice(i), 'comment'); state.blockEnd = pair[1]; break; }
      push(tokens, line.slice(i, end + pair[1].length), 'comment'); i = end + pair[1].length; continue;
    }
    const ch = line.charAt(i);
    if (/\s/.test(ch)) { let j = i + 1; while (j < line.length && /\s/.test(line.charAt(j))) j += 1; push(tokens, line.slice(i, j), 'plain'); i = j; continue; }
    if (ch === '"' || ch === "'" || ch === '`') {
      const quote = ch; let j = i + 1;
      while (j < line.length) { if (line.charAt(j) === quote && line.charAt(j - 1) !== '\\') { j += 1; break; } j += 1; }
      const keyLike = nextNonSpace(line, j) === ':' && ['json', 'yaml', 'toml'].includes(language);
      push(tokens, line.slice(i, j), keyLike ? 'property' : 'string'); i = j; continue;
    }
    if ((ch === '$' && /[A-Za-z_{]/.test(line[i + 1] || '')) || (ch === '@' && /[A-Za-z]/.test(line[i + 1] || ''))) {
      let j = i + 1;
      if (line.charAt(j) === '{') { j += 1; while (j < line.length && line.charAt(j) !== '}') j += 1; if (j < line.length) j += 1; }
      else while (j < line.length && /[\w:.-]/.test(line.charAt(j))) j += 1;
      push(tokens, line.slice(i, j), 'variable'); i = j; continue;
    }
    if (/\d/.test(ch) || (ch === '.' && /\d/.test(line[i + 1] || ''))) {
      let j = i + 1; while (j < line.length && /[\w.+-]/.test(line.charAt(j))) j += 1;
      push(tokens, line.slice(i, j), 'number'); i = j; continue;
    }
    if (isIdentifierStart(ch)) {
      let j = i + 1; while (j < line.length && isIdentifierPart(line.charAt(j))) j += 1;
      const word = line.slice(i, j); const lower = word.toLowerCase();
      let kind: TokenKind = 'plain';
      if (LITERALS.has(lower)) kind = 'literal';
      else if (keywords.has(language === 'sql' ? lower : word) || keywords.has(lower)) kind = 'keyword';
      else {
        const next = nextNonSpace(line, j);
        const before = line.slice(0, i).trim();
        if ((next === ':' && ['json', 'yaml', 'css'].includes(language))
          || (next === '=' && ['toml', 'ini'].includes(language))
          || (!before && next === ':' && language === 'graphql')) kind = 'property';
      }
      push(tokens, word, kind); i = j; continue;
    }
    if ((ch === '#' && language === 'css') || (ch === '@' && ['css', 'java', 'kotlin'].includes(language))) {
      let j = i + 1; while (j < line.length && /[\w-]/.test(line.charAt(j))) j += 1;
      push(tokens, line.slice(i, j), 'meta'); i = j; continue;
    }
    push(tokens, ch, /[{}()[\].,;:+\-*/%=&|!<>?~^]/.test(ch) ? 'operator' : 'plain');
    i += 1;
  }
  return tokens;
}

export function highlightCode(source: string, language: CodeLanguage): HighlightedLine[] {
  const state: LexState = { blockEnd: '', markupComment: false, inTag: false, expectTag: false };
  const markup = ['html', 'xml', 'vue', 'svelte', 'astro'].includes(language);
  return source.replace(/\r\n?/g, '\n').split('\n').map((line) => ({
    tokens: markup ? highlightMarkupLine(line, state) : highlightGenericLine(line, language, state),
  }));
}
