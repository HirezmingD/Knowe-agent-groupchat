/**
 * i18n/index.ts — [v1.0.21.3] react-i18next 实例初始化
 *
 * 语言资源：src/locales/zh.json（中文，默认/回退）、en.json（英文）
 * 语言设置链路：PrimaryLanguageModule → useSettingsStore.language
 *   → pushToBackend（持久化） + i18n.changeLanguage（前端热切换）
 *
 * 多语言扩展：新增语言 = locales/<code>.json + 资源注册 + PrimaryLanguageModule
 *   的 LANGUAGES 配置加一项；组件代码零改动（t('key') 不变）。
 */
import { useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import zh from '../locales/zh.json';
import en from '../locales/en.json';

/** 可用语言清单（顺序即 UI 展示顺序）。新增语言在这里加。 */
export const LANGUAGES: { code: string; label: string }[] = [
  { code: 'zh', label: '中文 (Chinese)' },
  { code: 'en', label: 'English' },
];

/** 校验 language 值是否受支持；非法值回退 'zh'。 */
export function normalizeLanguage(value: string | null | undefined): string {
  const lang = (value || '').trim().toLowerCase();
  return LANGUAGES.some((l) => l.code === lang) ? lang : 'zh';
}

void i18n
  .use(initReactI18next)
  .init({
    resources: {
      zh: { translation: zh },
      en: { translation: en },
    },
    lng: 'zh',
    fallbackLng: 'zh',
    interpolation: {
      escapeValue: false, // React 已做 XSS 转义，无需 i18next 再转
      // [v1.0.21.3 修复] 语言文件占位符是单花括号 {n}/{name}/...（140+ 处），
      // i18next 默认 {{n}} 双花括号 → 插值不生效、界面原样显示 {n}人。
      // 显式声明单花括号前后缀，一次覆盖全部 key；文件里无双花括号用法，无冲突。
      prefix: '{',
      suffix: '}',
    },
    returnNull: false,
  });

/**
 * [v1.0.24.6-P1b] 高频渲染路径的翻译缓存 hook。
 *
 * 背景：ChatStream（56 处 t()）/ConvList/MessageBubble 等高频组件每次渲染都
 * 重新执行 i18next 的 key 解析（translate/getResource/extendTranslation），
 * Profiler 实测约占主线程 8%。无插值 key 的翻译是纯函数——同 key 每次结果相同，
 * 缓存后重复渲染直接命中。
 *
 * 机制：
 * - t(key) 单参（无插值）→ 结果按 key 缓存在 useRef（组件实例级），重复渲染归零；
 * - t(key, opts) 带插值 → 走 i18next 原路径（动态参数不能缓存）；
 * - 语言切换：i18n.language 变化 → 缓存按 lang 重建（自动失效，不残留旧语言）。
 *
 * 用法：`const { t } = useTranslation()` → `const { t } = useCachedT()`，调用点零改动。
 */
export function useCachedT(): { t: (key: string, opts?: Record<string, unknown>) => string } {
  const { t } = useTranslation();
  const lang = i18n.language;
  const cacheRef = useRef<{ lang: string; map: Record<string, string> }>({ lang: '', map: {} });
  const cachedT = useCallback(
    (key: string, opts?: Record<string, unknown>): string => {
      if (opts === undefined) {
        const c = cacheRef.current;
        if (c.lang !== lang) {
          c.lang = lang;
          c.map = {};
        }
        if (!(key in c.map)) c.map[key] = t(key) ?? '';
        return c.map[key] ?? '';
      }
      return t(key, opts) ?? '';
    },
    [t, lang],
  );
  return { t: cachedT };
}

export default i18n;
