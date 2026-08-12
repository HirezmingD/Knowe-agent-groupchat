/**
 * DirectoryPicker.tsx — 「项目目录」这一行（[v0.7 A0]）
 *
 * DOM：.dir-row > input.modal-input.dir-path(readonly) + button.btn.btn-ghost
 *
 * 为什么单独抽一个组件：**两个地方要选目录**——
 *   · 用户自己点加号建群 → NewProjectModal
 *   · 知知提议建群 → 审批卡（ApprovalCard，见 README 的接入片段）
 * 两处的规矩必须一样（同一个路径格式、同一个兜底），所以只写一遍。
 *
 * 取路径的两条路：
 *   1. ★ Electron：主进程的 dialog.showOpenDialog（preload 桥出 window.knowe.selectDirectory）。
 *      **这是正路**——只有它能拿到真正的绝对路径。
 *   2. 兜底：<input type="file" webkitdirectory>。纯浏览器里跑（Vite dev / 单测）时用。
 *      注意：浏览器出于安全**不给绝对路径**，只给得到文件夹名。所以兜底时会明说
 *      「拿不到完整路径」，而不是假装选好了——后端收到一个相对名字会当成默认目录处理。
 */

import React, { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

export interface DirectoryPickerProps {
  /** 当前选中的目录（受控） */
  value: string;
  /** 选好了 → 把绝对路径交出去 */
  onChange: (dir: string) => void;
  /** 只读输入框的 aria-label */
  label?: string;
  /** 占位文案 */
  placeholder?: string;
  /**
   * [v0.8b #6] ★ 报错**只在用户按了「创建」之后**才亮。
   *
   *   v0.7b 的做法是「空着就标红」——于是弹窗一打开，用户还没来得及看清有几个字段，
   *   眼前已经是一道红边加一行红字。**他什么都还没做错，凭什么先骂他一句。**
   *   报错是对「你刚才那一下不对」的回应，不是欢迎语。
   *
   *   现在：卡片刚出现时安安静静（只有一句中性的提示告诉他这儿要做什么），
   *   点了「创建」而目录还空着，才亮红边和红字。
   *   由父组件（NewProjectModal / ApprovalCard）在提交失败那一刻把它置为 true。
   */
  showError?: boolean;
  /**
   * [v0.7b #1 · 保留] 这个字段是必填的。
   *
   * 现在它只管一件事：占位文案说不说「必选」。**标红归 showError 管**——
   * 老的调用方（ApprovalCard）传 required 也不会再一打开就飘红。
   */
  required?: boolean;
}

/** Electron 的 File 上多一个 path（浏览器没有） */
type MaybeElectronFile = File & { path?: string };

export const DirectoryPicker: React.FC<DirectoryPickerProps> = ({
  value,
  onChange,
  label,
  // [v0.8b #6] 中性的一句话：说清这儿要干什么，不带任何「你错了」的味道。
  placeholder,
  required = true,
  showError = false,
}) => {
  const { t } = useTranslation();
  const fileRef = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState('');

  const pick = useCallback(async () => {
    setNote('');
    const bridge = window.knowe?.selectDirectory;

    // ── 正路：Electron IPC ──
    if (typeof bridge === 'function') {
      try {
        const dir = await bridge();
        if (dir) onChange(dir);          // 用户按了取消 → dir 为 null → 保持原样
      } catch (err) {
        console.error('[DirectoryPicker] selectDirectory 失败', err);
        setNote(t('directory.picker.02'));
      }
      return;
    }

    // ── 兜底：浏览器的 webkitdirectory ──
    fileRef.current?.click();
  }, [onChange, t]);

  const onFallbackPick = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] as MaybeElectronFile | undefined;
    e.target.value = '';                 // 允许连选同一个目录两次
    if (!f) return;

    const rel = f.webkitRelativePath || '';
    if (f.path) {
      // Electron 里的 <input> 也带 path —— 从「文件的绝对路径」里减掉「目录内相对路径」，
      // 剩下的就是被选中的那个目录本身。
      const abs = f.path.replace(/\\/g, '/');
      // [v1.0.21.2] head 已经是选中目录的完整路径（abs 减去目录内相对路径）。
      // 旧的 head + root 会把目录名重复拼一遍（如 .../测试/测试/测试 三层畸形路径）。
      const head = rel ? abs.slice(0, abs.length - rel.length) : abs;
      onChange(rel ? head.replace(/\/+$/, '') : abs);
      return;
    }

    // 纯浏览器：只给得到文件夹名，给不了绝对路径。**说实话，别假装选好了。**
    const folder = rel.split('/')[0] ?? '';
    onChange(folder);
    setNote(t('directory.picker.04'));
  }, [onChange, t]);

  const empty = !value.trim();
  // [v0.8b #6] 只有「必填 + 空着 + 用户已经按过创建」三者同时成立，才亮红
  const bad = required && empty && showError;

  return (
    <div className="dir-row">
      <input
        className={'modal-input dir-path' + (bad ? ' needs-pick' : '')}
        value={value}
        readOnly
        placeholder={placeholder ?? t('directory.picker.05')}
        aria-label={label ?? t('common.04')}
        title={value || (placeholder ?? t('directory.picker.05'))}
      />
      {/* [v0.8b #7] 这颗按钮的宽度 = 下面那颗「创建」的宽度（.dir-row 用的是和
          .modal-acts 一样的两栏栅格，见 CSS）。上下对齐，不再是一条歪的。 */}
      <button
        type="button"
        className={'btn dir-pick ' + (bad ? 'btn-primary' : 'btn-ghost')}
        onClick={pick}
      >
        {empty ? t('directory.picker.06') : t('directory.picker.03')}
      </button>

      {/* webkitdirectory 不在 React 的 HTML 属性表里，得这么塞进去 */}
      <input
        ref={fileRef}
        type="file"
        className="dir-fallback"
        tabIndex={-1}
        aria-hidden="true"
        onChange={onFallbackPick}
        {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
      />

      {/* [v0.8b #6] 红字只在「按了创建但没选目录」时出现——不是一打开就骂人 */}
      {bad && !note && (
        <div className="dir-hint" role="alert">
          {t('directory.picker.hint')}
        </div>
      )}
      {note && <div className="dir-note" role="status">{note}</div>}
    </div>
  );
};

export default DirectoryPicker;
