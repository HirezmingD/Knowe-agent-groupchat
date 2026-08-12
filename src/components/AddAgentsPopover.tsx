import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RolePicker } from './RolePicker';
import { useKnoweStore } from '../store/store';
import './add-agents-popover.css';

/**
 * [v1.0.23.4] 群聊中途添加 Agent 员工 — Popover 卡片。
 *
 * · 复用 RolePicker（hideExtras：隐藏「项目经理自动加入」行与说明）
 * · 每种职能只能勾选一次（点已选 = 取消）；要加多名同职能 → 再次点开主按钮
 * · 位置：卡片左上角跟随鼠标（anchor 为按钮点击处坐标，带视口防溢出）
 * · 确认 → store.addAgents（roles 数组无重复）→ 关闭；后端自动编号
 * · 动画：fade + zoom .95→1 + translateY 2px→0，320ms ease-out（丝滑）
 */
interface AddAgentsPopoverProps {
  projectId: string;
  /** 鼠标锚点（按钮点击处 clientX/clientY），卡片左上角跟随 */
  anchor: { x: number; y: number };
  onClose: () => void;
}

const POP_W = 600;   // 与 CSS .pop-add-agents width 同步
const POP_H = 400;   // 高度估算（头部+grid 240+操作行），防视口溢出

export const AddAgentsPopover: React.FC<AddAgentsPopoverProps> = ({
  projectId, anchor, onClose,
}) => {
  const { t } = useTranslation();
  const addAgents = useKnoweStore((s) => s.addAgents);
  const [selected, setSelected] = useState<string[]>([]);
  const [closing, setClosing] = useState(false);

  // 出场动画：先置 closing（450ms 反向动画）再真正卸载
  const close = () => {
    if (closing) return;
    setClosing(true);
    window.setTimeout(onClose, 450);
  };

  const confirm = () => {
    if (selected.length === 0) return;
    addAgents(projectId, selected);
    close();   // 防抖：确认即关，二次添加需重新打开
  };

  // 视口防溢出：卡片**右上角** = 鼠标位置（left = x - 宽），靠左/靠下时内收
  const left = Math.min(anchor.x - POP_W, window.innerWidth - POP_W - 8);
  const top = Math.min(anchor.y, window.innerHeight - POP_H - 8);

  return (
    <>
      <div className="pop-add-backdrop" onClick={close} />
      <div
        className={'pop-add-agents' + (closing ? ' closing' : '')}
        role="dialog"
        aria-label={t('addAgents.title')}
        style={{ left: Math.max(8, left), top: Math.max(8, top) }}
      >
        <div className="pop-add-head">
          <span className="pop-add-title">{t('addAgents.title')}</span>
          <button
            type="button"
            className="icon-btn pop-add-x"
            aria-label={t('addAgents.cancel')}
            onClick={close}
          >
            ✕
          </button>
        </div>

        <RolePicker
          selected={selected}
          onChange={setSelected}
          hideExtras
        />

        <div className="pop-add-actions">
          <button
            type="button"
            className="btn pop-add-btn"
            onClick={() => setSelected([])}
            disabled={selected.length === 0}
          >
            {t('addAgents.clear')}
          </button>
          <span className="pop-add-spacer" />
          <button type="button" className="btn pop-add-btn" onClick={close}>
            {t('addAgents.cancel')}
          </button>
          <button
            type="button"
            className="btn btn-primary pop-add-btn"
            onClick={confirm}
            disabled={selected.length === 0}
          >
            {t('addAgents.confirm')}
          </button>
        </div>
      </div>
    </>
  );
};

export default AddAgentsPopover;
