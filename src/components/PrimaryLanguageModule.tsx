/**
 * PrimaryLanguageModule.tsx — [v1.0.21.3] 选择主要语言模块
 *
 * 挂载点（两处共用）：
 *   ① 设置 → 模型与提供方 界面上方（SettingsView ModelsPane）
 *   ② 首次安装配置模型卡片（FirstRunModelGate）
 *
 * 交互：选择语言 → 不立即生效 → 点【确认 Apply】才生效（二次点击）。
 * 生效动作：
 *   1. useSettingsStore.setLanguage(lang)   —— 本地 state（含对账）
 *   2. i18n.changeLanguage(lang)            —— 前端全界面热切换
 *   3. pushToBackend()                      —— 持久化到 settings.json
 *
 * 多语言扩展：选项来自 LANGUAGES 配置（src/i18n），新增语言加配置即可，本组件零改动。
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import i18n, { LANGUAGES, normalizeLanguage } from '../i18n';
import { useSettingsStore } from '../store/settings';

interface Props {
  /** 紧凑模式（首启卡）：无标题、更小间距 */
  compact?: boolean;
}

const PrimaryLanguageModule: React.FC<Props> = ({ compact = false }) => {
  const { t } = useTranslation();
  const language = useSettingsStore((s) => s.language);
  const setLanguage = useSettingsStore((s) => s.setLanguage);
  const pushToBackend = useSettingsStore((s) => s.pushToBackend);

  // 本地草稿：选择后不立即生效，Apply 才提交
  const [draft, setDraft] = useState<string>(normalizeLanguage(language));
  const [applying, setApplying] = useState(false);

  const handleApply = async () => {
    if (draft === normalizeLanguage(language)) return; // 未变化不动作
    setApplying(true);
    try {
      setLanguage(draft);
      await i18n.changeLanguage(draft);
      await pushToBackend();
    } finally {
      setApplying(false);
    }
  };

  return (
    <section className={compact ? 'plm plm-compact' : 'plm'}>
      {!compact && (
        <h3 className="plm-title">{t('primaryLanguage.title')}</h3>
      )}
      <div className="plm-options" role="radiogroup" aria-label={t('primaryLanguage.title')}>
        {LANGUAGES.map((lang) => (
          <label key={lang.code} className={`plm-option${draft === lang.code ? ' plm-active' : ''}`}>
            <input
              type="radio"
              name="primary-language"
              value={lang.code}
              checked={draft === lang.code}
              onChange={() => setDraft(lang.code)}
            />
            <span>{lang.label}</span>
          </label>
        ))}
      </div>
      <div className="plm-actions">
        <button
          type="button"
          className="plm-apply"
          disabled={applying || draft === normalizeLanguage(language)}
          onClick={() => void handleApply()}
        >
          {t('primaryLanguage.apply')}
        </button>
        {!compact && (
          <p className="plm-hint">{t('primaryLanguage.hint')}</p>
        )}
      </div>
    </section>
  );
};

export default PrimaryLanguageModule;
