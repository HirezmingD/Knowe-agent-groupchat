/**
 * EmptyState.tsx — 未选会话空态（component-tree §G · EmptyChat）
 *
 * DOM：.empty.enter > (.mark > svg「含 .core」) + h2 + p(> span.lk)
 *
 * 设计稿的「知知」链接指向平台通道会话。当前后端把知知放在
 * `__platform__` 通道（总体计划 B1），该通道要等契约审计定稿再接，
 * 所以这里的链接改为「新建一个项目」——不是偷懒，是不给尚未接线的按钮。
 */

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconEmptyMark } from './icons';
import NewProjectModal from './NewProjectModal';

export const EmptyState: React.FC = () => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <>
      <div className="empty enter">
        <div className="mark">
          <IconEmptyMark />
        </div>
        <h2>{t('empty.state.01')}</h2>
        <p>
          {t('common.or')}
          <span className="lk" role="button" tabIndex={0}
            onClick={() => setOpen(true)}
            onKeyDown={(e) => { if (e.key === 'Enter') setOpen(true); }}
          >
            {t('empty.state.02')}
          </span>
        </p>
      </div>
      <NewProjectModal open={open} onClose={() => setOpen(false)} />
    </>
  );
};

export default EmptyState;
