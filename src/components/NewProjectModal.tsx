/**
 * NewProjectModal.tsx — 新建项目弹窗（component-tree §I · Modal）
 *
 * DOM：.scrim.center > .modal > .modal-title + .modal-body
 *                             + .modal-field(项目名) + .modal-field(项目目录)
 *                             + (.modal-acts > button.btn.btn-ghost + button.btn.btn-primary)
 *
 * [v0.7 A0] ★ 目录是**必填**的，不是可选项。
 *   没有目录，Worker 的沙箱就没有根：他要读的文件不知道在哪、写出来的东西不知道去哪。
 *   与其让用户建完群之后在某个设置页里补，不如在这里就问清楚——建群这件事，
 *   本来就是「给这摊活找个地方」。
 *
 * [v0.7 #5] 跳转不在这里做：store.createProject 会先本地注册项目、把 activeProjectId
 *   切过去，再发指令。弹窗只管关掉自己。
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useKnoweStore } from '../store/store';
import { newProjectRequestId } from '../store/platform';
import DirectoryPicker from './DirectoryPicker';
import RolePicker from './RolePicker';
import { useTranslation } from 'react-i18next';

// Preserve the existing import surface for tests/consumers that imported this helper from the
// component before v0.18. The implementation now lives in platform.ts so all creation paths share
// one monotonic allocator.
export { newProjectRequestId } from '../store/platform';

export interface NewProjectModalProps {
  open: boolean;
  onClose: () => void;
}

export const NewProjectModal: React.FC<NewProjectModalProps> = ({ open, onClose }) => {
  const { t } = useTranslation();
  const createProject = useKnoweStore((s) => s.createProject);
  const [name, setName] = useState('');
  const [dir, setDir] = useState('');
  /**
   * [v0.8b #6] 用户按过「创建」了吗？
   *
   *   报错是对「你刚才那一下不对」的回应——**在他动手之前，没有什么可回应的**。
   *   所以弹窗打开时这里是 false，界面上一个红字都没有；
   *   他按了创建、而字段还空着，才亮红。
   *
   *   顺带：「创建」键不再是灰的了。灰键什么也不说，用户只能对着它猜自己漏了什么；
   *   可点的键 + 按下去之后精确标红那一行，才叫告诉他。
   */
  const [attempted, setAttempted] = useState(false);
  /** [主动拉入worker] 建群时勾选的职能前缀（最多 8 个）；空 = 不选，行为与旧版一致。 */
  const [roles, setRoles] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setName('');
      setDir('');
      setRoles([]);
      setAttempted(false);
      inputRef.current?.focus();
    }
  }, [open]);

  const nameBad = attempted && !name.trim();
  const dirBad = attempted && !dir.trim();

  const submit = useCallback(() => {
    const n = name.trim();
    const d = dir.trim();
    if (!n || !d) {
      setAttempted(true);          // ← 现在才有资格标红
      if (!n) inputRef.current?.focus();
      return;
    }
    createProject(newProjectRequestId(), n, d, undefined, roles);
    onClose();
  }, [name, dir, roles, createProject, onClose]);

  if (!open) return null;

  return (
    <div
      className="scrim center"
      role="dialog"
      aria-modal="true"
      aria-label={t('conv.list.02')}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="modal modal-np">
        <div className="modal-title">{t('new.project.modal.03')}</div>
        <div className="modal-body">
          {t('new.project.modal.hint1')}
          <strong>{t('new.project.modal.06')}</strong>
          {t('new.project.modal.hint2')}
        </div>

        <div className="modal-field">
          <label className="modal-label" htmlFor="np-name">{t('new.project.modal.04')}</label>
          <input
            id="np-name"
            ref={inputRef}
            className={'modal-input' + (nameBad ? ' needs-pick' : '')}
            value={name}
            placeholder={t('new.project.modal.01')}
            aria-label={t('common.23')}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit();
              if (e.key === 'Escape') onClose();
            }}
          />
          {nameBad && <div className="dir-hint" role="alert">{t('new.project.modal.05')}</div>}
        </div>

        <div className="modal-field">
          <label className="modal-label" htmlFor="np-dir">{t('common.24')}</label>
          <DirectoryPicker
            value={dir}
            onChange={setDir}
            label={t('common.04')}
            required
            showError={dirBad}     /* [v0.8b #6] 按过创建、还空着 → 这才标红 */
          />
        </div>

        {/* [主动拉入worker] 建群前选择职能：建群后自动实例化对应 Worker 拉入（可不选） */}
        <RolePicker selected={roles} onChange={setRoles} />

        {/* [v0.8b #7] 这两颗键的宽度 = 上面那一行（路径框 / 选择目录）的宽度：
            .dir-row 和 .modal-acts 用的是同一套两栏栅格，见 CSS。 */}
        <div className="modal-acts">
          <button className="btn btn-ghost" onClick={onClose}>{t('chat.stream.03')}</button>
          <button className="btn btn-primary" onClick={submit}>{t('new.project.modal.02')}</button>
        </div>
      </div>
    </div>
  );
};

export default NewProjectModal;
