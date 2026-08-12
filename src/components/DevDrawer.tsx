/**
 * DevDrawer.tsx — 开发走廊抽屉（Ctrl+Shift+D 开关）
 *
 * 走廊页从「/corridor 独立路由」降级为覆盖抽屉：DevCorridor.tsx 本身一行不改，
 * 这里只给它一个可开关的容器。
 *
 * ⚠ 一条必须知道的事：DevCorridor 会自己建一条独立 WebSocket。
 *   后端对「同项目的新连接」会用 close code 4001 让旧连接让位——
 *   也就是说，在走廊里点「连接」有可能把主界面的连接顶掉（主界面会显示「未连接」且不自动重连）。
 *   所以抽屉里挂了一条醒目提示；走廊默认不连，只有你手点「连接」才连。
 */

import React, { useEffect, useState } from 'react';
import { DevCorridor } from '../observe/DevCorridor';
import { useTranslation } from 'react-i18next';

export const DevDrawer: React.FC = () => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // [阶段一 1.5] 正式版（打包安装版）：整个抽屉不渲染 + 快捷键不挂（双保险）。
  const isPackaged = window.knowe?.isPackaged === true;

  useEffect(() => {
    if (isPackaged) return; // 正式版：不注册 Ctrl+Shift+D
    const onKey = (e: KeyboardEvent): void => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'D' || e.key === 'd')) {
        e.preventDefault();
        setOpen((v) => !v);
      }
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isPackaged]);

  if (isPackaged || !open) return null;

  return (
    <div className="devdrawer" role="dialog" aria-label={t('dev.drawer.01')}>
      <div className="dd-head">
        <span className="dd-title">{t('dev.drawer.02')}</span>
        <span className="dd-warn">{t('dev.drawer.03')}</span>
        <button className="btn btn-ghost" onClick={() => setOpen(false)}>{t('app.01')}</button>
      </div>
      <div className="dd-body">
        <DevCorridor />
      </div>
    </div>
  );
};

export default DevDrawer;
