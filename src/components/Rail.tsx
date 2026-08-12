/**
 * Rail.tsx — 最左功能栏（component-tree §A）
 *
 * DOM：nav.rail > .rail-logo + button.rail-btn(.active)×N + .rail-spacer
 *                + button.rail-btn×N + .rail-me
 *
 * 数据：selectActiveView + setView（唯一入口）。
 * 搜索按钮不切 activeView：它只开关全局命令面板，位置与权威 UI 一致。
 */

import React from 'react';
import { useKnoweStore } from '../store/store';
import { selectActiveView, selectCmdKOpen } from '../store/selectors';
import { useSettingsStore } from '../store/settings';
import {
  IconChats, IconContacts, IconFavorites,
  IconKnowledge, IconSearchSm, IconSettings,
  IconMoon, IconSun,
} from './icons';
import { useTranslation } from 'react-i18next';

interface RailItem {
  view: string;
  label: string;
  icon: React.ReactNode;
}

export const Rail: React.FC = () => {
  const { t } = useTranslation();
  const activeView = useKnoweStore(selectActiveView);
  const cmdKOpen = useKnoweStore(selectCmdKOpen);
  const setView = useKnoweStore((s) => s.setView);
  const toggleCmdK = useKnoweStore((s) => s.toggleCmdK);

  const MAIN_ITEMS: RailItem[] = [
    { view: 'chats', label: t('common.14'), icon: <IconChats /> },
    { view: 'contacts', label: t('contacts.view.29'), icon: <IconContacts /> },
    { view: 'favorites', label: t('common.09'), icon: <IconFavorites /> },
    { view: 'knowledge', label: t('knowledge.view.06'), icon: <IconKnowledge /> },
  ];

  /*
   * [v0.44 设置 §2.1] 头像与称呼来自设置 store（唯一数据源）：
   *   · 上传了头像 → 真图（object-fit:cover，圆形裁切在 CSS）；
   *   · 没上传 → 名字首字当 glyph（原来硬编码的「洲」就是这一档的旧值）。
   *   点头像 = 直达「设置」（头像/姓名就在第一屏，改起来顺手）。
   */
  const userName = useSettingsStore((s) => s.userName);
  const avatarDataUrl = useSettingsStore((s) => s.avatarDataUrl);

  /*
   * [v1.0.19.2] 外观切换：读当前 appearance、写 setAppearance。
   *   浅色显示月牙（意为「可切到深色」），深色显示太阳（意为「可切回浅色」）——
   *   与设计稿 Knowe-UI_v1.3.html 的 Moon/Sun 互斥逻辑一致。
   */
  const appearance = useSettingsStore((s) => s.appearance);
  const setAppearance = useSettingsStore((s) => s.setAppearance);

  return (
    <nav className="rail">
      {/*
        [v0.5 #13] 用户头像放在最上方，下面是功能列表。
        搜索、外观、设置紧跟功能列表末项（知识库）之后，全在上面一列——
        恢复 v1.0.19.2 的既有顺序，不钉到底部。
      */}
      <div
        className="rail-me rail-me-top"
        title={userName}
        role="button"
        tabIndex={0}
        onClick={() => setView('settings')}
        onKeyDown={(e) => { if (e.key === 'Enter') setView('settings'); }}
      >
        {avatarDataUrl
          ? <img className="rail-me-img" src={avatarDataUrl} alt={userName} />
          : (userName || '我').slice(0, 1)}
      </div>

      {MAIN_ITEMS.map((it) => (
        <button
          key={it.view}
          className={'rail-btn' + (activeView === it.view ? ' active' : '')}
          data-view={it.view}
          data-tip={it.label}
          aria-label={it.label}
          aria-pressed={activeView === it.view}
          onClick={() => setView(it.view)}
        >
          {it.icon}
        </button>
      ))}

      <button
        className={'rail-btn' + (activeView === 'settings' ? ' active' : '')}
        data-view="settings"
        data-tip={t('common.12')}
        aria-label={t('common.12')}
        aria-pressed={activeView === 'settings'}
        onClick={() => setView('settings')}
      >
        <IconSettings />
      </button>

      {/* [v1.0.19.3] 全局搜索按钮：只开关命令面板，不切 activeView。 */}
      <button
        className="rail-btn"
        id="searchBtn"
        data-tip={t('rail.03')}
        aria-label={t('rail.02')}
        aria-expanded={cmdKOpen}
        aria-controls="globalSearchDialog"
        onClick={toggleCmdK}
      >
        <IconSearchSm />
      </button>

      {/*
        [v1.0.23.6] 外观切换按钮 —— 移到搜索下方（原在设置上方）。
        与其他 Rail 图标一致 19×19，点击在 light/dark 之间切换。
      */}
      <button
        className="rail-btn"
        data-tip={t('rail.01')}
        aria-label={t('rail.01')}
        onClick={() => setAppearance(appearance === 'dark' ? 'light' : 'dark')}
      >
        {appearance === 'dark' ? <IconSun /> : <IconMoon />}
      </button>

      <div className="rail-spacer" />
    </nav>
  );
};

export default Rail;
