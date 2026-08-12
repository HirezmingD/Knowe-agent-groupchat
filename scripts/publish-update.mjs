/**
 * [v1.0.25.4] publish-update.mjs — 自动更新发布脚本。
 *
 * 用法：node scripts/publish-update.mjs [--server 用户@IP] [--dir /var/www/knowe-update]
 * ⚠ Windows git-bash 坑：不要传 --dir！bash 会把 --dir=/var/... 转成 C:/Program Files/Git/var/...
 *   （等号后以 / 开头即触发路径转换）→ scp 目标错乱。默认值在脚本内部不经 bash，直接可用。
 *   若确有需要覆盖目录：MSYS_NO_PATHCONV=1 node scripts/publish-update.mjs --server=... --dir=/xxx
 *
 * 做什么：
 *   1. 校验 release/ 下最新构建的 latest.yml 引用文件名 == 磁盘实际产物名
 *      （审计阻断项 1 的回归护栏：url 与产物名失配 → 下载 404）；
 *   2. 把 {安装包, latest.yml, blockmap} 上传到服务器更新目录（scp）；
 *   3. 打印下一步提示（Caddy 目录核对）。
 *
 * 依赖：本机有 scp；服务器有 ssh 访问（密码/密钥由环境提供，不在此脚本内）。
 * 服务器 Caddy 配置见 02-计划/01-架构设计-自动更新.md §3.5。
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, basename } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const RELEASE = join(ROOT, 'release');

const args = process.argv.slice(2);
const serverFlag = args.find((a) => a.startsWith('--server='))?.split('=')[1];
const dirFlag = args.find((a) => a.startsWith('--dir='))?.split('=')[1];

if (!serverFlag) {
  console.error('用法: node scripts/publish-update.mjs --server=user@host [--dir=/var/www/knowe-update]');
  console.error('服务器访问方式就绪后启用（S5 步骤）。');
  process.exit(1);
}

const serverDir = dirFlag || '/var/www/knowe-update';

// ── 1. 找最新构建产物 ──
const exes = readdirSync(RELEASE)
  .filter((f) => f.endsWith('.exe') && !f.includes('__uninstaller') && !f.includes('Uninstall'))
  .map((f) => ({ name: f, mtime: statSync(join(RELEASE, f)).mtimeMs }))
  .sort((a, b) => b.mtime - a.mtime);

if (exes.length === 0) {
  console.error('✘ release/ 下没有安装包产物。先跑 dist:win 再发布。');
  process.exit(1);
}
const exe = exes[0];
const exePath = join(RELEASE, exe.name);
console.log(`✔ 最新安装包：${exe.name}（${(statSync(exePath).size / 1024 / 1024).toFixed(1)} MB）`);

// ── 2. 校验 latest.yml 引用名 == 实际产物名（防 404 回归护栏）──
const latestYmlPath = join(RELEASE, 'latest.yml');
const latestYml = readFileSync(latestYmlPath, 'utf8');
const urlMatch = /^\s*url:\s*(.+)$/m.exec(latestYml);
const pathMatch = /^\s*path:\s*(.+)$/m.exec(latestYml);
const ymlFile = urlMatch?.[1]?.trim() ?? pathMatch?.[1]?.trim() ?? '';

if (ymlFile !== exe.name) {
  console.error(`✘ latest.yml 引用的文件名（${ymlFile}）与磁盘产物（${exe.name}）不一致！`);
  console.error('  下载会 404。请检查 electron-builder.yml 的 artifactName / publish 配置。');
  process.exit(1);
}
console.log('✔ latest.yml 文件名与产物一致（无 404 风险）');

// ── 3. 上传：安装包 + latest.yml + blockmap ──
const blockmap = exe.name + '.blockmap';
const files = [exePath, latestYmlPath, join(RELEASE, blockmap)];

console.log(`→ scp 上传到 ${serverFlag}:${serverDir}`);
const result = spawnSync('scp', [...files, `${serverFlag}:${serverDir}/`], {
  stdio: 'inherit',
  shell: false,
});
if (result.status !== 0) {
  console.error(`✘ scp 失败（exit ${result.status ?? 'signal'}）。检查服务器访问。`);
  process.exit(1);
}

// ── 4. 生成固定名副本（官网下载按钮指向它，永不改版号）──
//    硬链接（ln -f）零额外磁盘；覆盖旧固定名指向最新包。
console.log(`→ SSH 更新固定名副本 ${serverFlag}:${serverDir}/Knowe-Setup.exe`);
const lnResult = spawnSync('ssh', [serverFlag, `ln -f '${serverDir}/${exe.name}' ${serverDir}/Knowe-Setup.exe`], {
  stdio: 'inherit',
  shell: false,
});
if (lnResult.status !== 0) {
  console.error(`✘ 固定名副本生成失败（exit ${lnResult.status ?? 'signal'}）——官网下载按钮会 404！`);
  process.exit(1);
}
console.log('✔ 固定名副本已更新（Knowe-Setup.exe → 最新安装包）');

// ── 5. 收尾提示 ──
console.log('\n✔ 发布完成。核对：');
console.log(`  1. 服务器目录应含：${exe.name}、latest.yml、${blockmap}、Knowe-Setup.exe（固定名）`);
console.log('  2. Caddy 更新目录 root 指向该目录，HTTP Range 默认支持（差分更新可用）');
console.log('  3. 客户端下次启动静默检查即发现新版本（设置-关于出现「重启安装更新」）');
console.log('  4. 官网下载按钮（/knowe-agent 首页 → /knowe-update/Knowe-Setup.exe）自动指向最新包');
