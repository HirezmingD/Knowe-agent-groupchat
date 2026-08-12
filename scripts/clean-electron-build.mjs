/** [v1.0.13][R5] Delete every Electron/package output before an authoritative build. */
/**
 * [阶段二 2.2 修改] 删除范围从 ['out','dist','release'] 收窄为 ['out','release']：
 *   - `dist/` 现在是 PyInstaller 后端产物目录（dist/KnoweBackend/），
 *     由 electron-builder 的 extraResources 整体拷为 resources/backend/。
 *     删掉它 = 后端没了 → 打包出来的安装包没有后端，必须保留！
 *   - electron-builder 输出目录 = release/（electron-builder.yml directories.output），
 *     clean 只负责清它和 out/。
 */
import { rmSync } from 'node:fs';
import { resolve } from 'node:path';

for (const name of ['out', 'release']) {
  rmSync(resolve(process.cwd(), name), { recursive: true, force: true });
}
