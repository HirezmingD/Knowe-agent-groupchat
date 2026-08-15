/**
 * after-pack-sign.cjs — [macOS R9] electron-builder afterPack 钩子：对 mac 产物做 ad-hoc 签名。
 *
 * 背景：electron-builder 在没有 Developer ID 证书时会「跳过签名」，主二进制保持
 * Electron 的 linker-signed（flags=0x20002，无 CMS blob）→ macOS 14 直接判
 * 「将对你的电脑造成伤害」，连「仍要打开」都不给，真实用户完全无法启动。
 *
 * 本钩子在 electron-builder 组装好 app 之后、生成 dmg/zip 之前执行，对 app 做
 * codesign --force --deep --sign -（ad-hoc，生成 CMS blob，flags=0x2）。之后
 * electron-builder 生成的 dmg/zip/blockmap/latest-mac.yml 全部基于这个已签名的
 * app，校验值自动匹配。用户下载后走「系统设置→隐私与安全性→仍要打开」即可。
 *
 * 仅对 darwin 生效；win32 走 electron-builder 原有流程（无签名）。
 */
const { execSync } = require('child_process');
const { join } = require('path');

module.exports = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return;

  const productFilename = context.packager.appInfo.productFilename; // "Knowe"
  const appPath = join(context.appOutDir, `${productFilename}.app`);

  console.log(`[afterPack] macOS ad-hoc 签名：${appPath}`);
  execSync(`codesign --force --deep --sign - "${appPath}"`, { stdio: 'inherit' });
  console.log('[afterPack] ad-hoc 签名完成');
};
