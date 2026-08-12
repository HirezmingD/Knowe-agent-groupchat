import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { DEFAULT_ROLE_TYPES } from '../store/state';
import './role-picker.css';

/**
 * [主动拉入worker] 职能选择区（新建项目弹窗 + 建群审批卡共用）。
 *
 * UI 权威参考：Logs/v1.0.23.2 主动拉入worker列表/Knowe-New-Project-Grid-List.html
 *   · 引导行（需要哪些职能？+ 已选计数）
 *   · 项目经理自动加入行（forced-row，勾选态不可取消）
 *   · 24 职能 3 列网格（grid-list，点选）
 *   · 说明文字
 *
 * 规则：
 *   · 一种身份只能选一次（selected 去重由组件保证，点已选 = 取消）
 *   · [20260805] 数量不再设限：移除 ≤8 上限（原团队上限 9 人含项目经理已作废），
 *     一个群聊内 agent 数量原则上无上限
 *   · 显示名走 ROLE_KEY_BY_TYPE → t('roles.<key>') 当前语言翻译，
 *     不依赖 DEFAULT_ROLE_TYPES.label（模块级固化值，切语言不刷新）
 */
interface RolePickerProps {
  selected: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
  /**
   * [v1.0.23.4] 中途添加场景：隐藏「项目经理自动加入」行与说明
   * （项目经理已在群里，不是本次新加入的）。默认 false，建群/审批卡不变。
   */
  hideExtras?: boolean;
}

/**
 * type → roles.* 翻译表 key。
 *
 * DEFAULT_ROLE_TYPES.label 是模块加载时的 i18n.t() 固化值（state.ts 顶层求值，
 * 切语言不刷新；若未来支持英文启动/HMR 重载时语言已是英文，label 会固化成
 * contacts.view.XX 的长英文名如 'Product'，而 roles.* 表用的是短名 'PM'，
 * roleLabel() 反查会 MISS）。这里直接按 type 取 key，显示名永远走当前语言
 * 的 roles.* 表，与启动语言/模块加载顺序无关。
 *
 * 对齐依据：zh.json 的 roles.* 值 == DEFAULT_ROLE_TYPES 中文 label（24/24 一致，
 * 见 v1.0.23.2 审计）；key 与后端 tools_knowe.py KNOWN_ROLES 的 key 同源。
 */
const ROLE_KEY_BY_TYPE: Record<string, string> = {
  fe: 'Frontend', be: 'Backend', pm: 'PM', qa: 'QA', ux: 'Design', da: 'Data',
  devops: 'DevOps', sec: 'Security', ml: 'ML', mobile: 'Mobile', game: 'Game',
  gis: 'GIS', mkt: 'Marketing', fin: 'Finance', hc: 'Healthcare', edu: 'Academic',
  ar: 'Spatial', sup: 'Support', sre: 'SRE', db: 'Database', arch: 'Architecture',
  writer: 'Writer', media: 'Media', legal: 'Legal',
};

const CHECK_SVG = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="m4.5 12.5 5 5 10-11" />
  </svg>
);

const SPARK_SVG = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 3v4M12 17v4M5 12H1M23 12h-4M6.3 6.3 4 4M20 20l-2.3-2.3M6.3 17.7 4 20M20 4l-2.3 2.3" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

export const RolePicker: React.FC<RolePickerProps> = ({
  selected, onChange, disabled = false, hideExtras = false,
}) => {
  const { t } = useTranslation();
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const toggle = (type: string) => {
    if (disabled) return;
    if (selectedSet.has(type)) {
      onChange(selected.filter((r) => r !== type));
    } else {
      onChange([...selected, type]);
    }
  };

  return (
    <div className="rp">
      <div className="scope-row">
        <p className="pf-scope">{t('new.project.roles.title')}</p>
        <span className="selection-summary">{t('new.project.roles.count', { n: selected.length })}</span>
      </div>

      {/* [v1.0.23.4] 中途添加：项目经理已在群里，不显示「自动加入」行与说明 */}
      {!hideExtras && (
        <div className="forced-row" aria-label={t('new.project.roles.forced')}>
          <span className="fr-nm">{t('new.project.roles.forced')}</span>
          <span className="pick-check" aria-hidden="true">{CHECK_SVG}</span>
        </div>
      )}

      <div className="flex w-full justify-center relative">
        <div className="grid-list" role="grid" aria-label={t('new.project.roles.gridAria')} aria-multiselectable="true">
          {DEFAULT_ROLE_TYPES.map((r) => {
            const on = selectedSet.has(r.type);
            const itemDisabled = disabled;
            return (
              <button
                key={r.type}
                type="button"
                className="grid-list-item"
                role="gridcell"
                aria-selected={on}
                aria-disabled={itemDisabled}
                aria-label={t(`roles.${ROLE_KEY_BY_TYPE[r.type]}`)}
                disabled={itemDisabled}
                onClick={() => toggle(r.type)}
              >
                <span className="pick-check" aria-hidden="true">{CHECK_SVG}</span>
                <span className="role-name">{t(`roles.${ROLE_KEY_BY_TYPE[r.type]}`)}</span>
              </button>
            );
          })}
        </div>
      </div>

      {!hideExtras && (
        <div className="role-note">
          {SPARK_SVG}
          <span>{t('new.project.roles.note')}</span>
        </div>
      )}
    </div>
  );
};

export default RolePicker;
