/**
 * GlobalSearch.tsx — 全局内存搜索 + Cmd/Ctrl+K 命令面板
 *
 * 两个入口共用同一份 useGlobalSearchGroups：
 *   · Rail 的 searchBtn / Cmd/Ctrl+K → CommandPalette
 *   · ConvList 顶部 search-wrap     → GlobalSearchResults
 *
 * 搜索只遍历现有前端 store，不请求后端、不做缓存或防抖。
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import { roleLabel, memberNameLabel } from '../shared/roleLabel';
import { useKnoweStore, type KnoweStore } from '../store/store';
import { selectCmdKOpen } from '../store/selectors';
import { useFavoritesStore } from '../store/favorites';
import { useKnowledgeStore } from '../store/knowledge';
import { useSettingsStore } from '../store/settings';
import { PLATFORM_PROJECT_ID, getZinniaDisplayName } from '../store/avatar';
import { parseDmId } from '../store/chat';
import { itemKeyOf } from '../store/state';
import type { SettingsSection } from './SettingsView';
import {
  IconChats, IconContacts, IconSearchSm, IconSettings, IconSpark, IconStar,
} from './icons';

export type GlobalSearchTarget =
  | { kind: 'conversation'; projectId: string }
  | { kind: 'message'; projectId: string; itemKey: string }
  | { kind: 'contact'; projectId: string | null; agentId: string | null }
  | { kind: 'favorite'; favoriteId: string }
  | { kind: 'knowledge'; cardId: string }
  | { kind: 'settings'; section: SettingsSection };

type SearchIconKind = 'conversation' | 'message' | 'contact' | 'favorite' | 'knowledge' | 'settings';

export interface GlobalSearchResult {
  id: string;
  icon: SearchIconKind;
  text: string;
  meta: string;
  target: GlobalSearchTarget;
}

export interface GlobalSearchGroup {
  name: string;
  items: GlobalSearchResult[];
}

const MAX_PER_GROUP = 5;
const SETTINGS_SECTIONS: SettingsSection[] = ['账户与身份', '模型与提供方', '通知', '外观', '关于'];

/** section 是 SettingsSection 协议值；展示时经此表映射到 i18n key。 */
const SETTINGS_SECTION_T_KEY: Record<SettingsSection, string> = {
  '账户与身份': 'global.search.12',
  '模型与提供方': 'global.search.07',
  '通知': 'global.search.14',
  '外观': 'global.search.03',
  '关于': 'global.search.02',
};

function normalized(value: unknown): string {
  return String(value ?? '').toLocaleLowerCase('zh-CN');
}

function oneLine(value: string, max = 40): string {
  const text = value.replace(/\s+/g, ' ').trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function orderedConversationIds(
  convs: KnoweStore['convs'],
  projectOrder: string[],
): string[] {
  const ids: string[] = [];
  const seen = new Set<string>();
  const add = (id: string): void => {
    if (!id || seen.has(id) || !convs[id]) return;
    seen.add(id);
    ids.push(id);
  };

  add(PLATFORM_PROJECT_ID);
  projectOrder.forEach(add);
  Object.keys(convs).forEach(add);
  return ids;
}

function conversationLabel(
  projectId: string,
  convs: KnoweStore['convs'],
): { text: string; meta: string; searchFields: string[] } {
  if (projectId === PLATFORM_PROJECT_ID) {
    return { text: getZinniaDisplayName(), meta: i18n.t('global.search.20'), searchFields: [getZinniaDisplayName(), i18n.t('common.10'), i18n.t('global.search.20'), i18n.t('contacts.view.28')] };
  }

  const conv = convs[projectId];
  const dm = parseDmId(projectId);
  if (dm) {
    const parent = convs[dm.projectId];
    const member = parent?.members.find((candidate) => candidate.id === dm.agentId);
    const memberName = member?.display.name ? memberNameLabel(dm.agentId, member.display.name) : (conv?.projectName || dm.agentId);
    const parentName = parent?.projectName || conv?.parentProjectName || dm.projectId;
    return {
      text: `${parentName} · ${memberName}`,
      meta: i18n.t('context.menu.05'),
      searchFields: [memberName, parentName, member?.display.role || '', projectId],
    };
  }

  const name = conv?.projectName || projectId;
  return { text: name, meta: i18n.t('common.14'), searchFields: [name, projectId] };
}

/** 对现有前端 store 做最直接的包含匹配；返回值已按每组最多 5 条裁切。 */
export function useGlobalSearchGroups(query: string): GlobalSearchGroup[] {
  const { t } = useTranslation();
  const convs = useKnoweStore((s) => s.convs);
  const projectOrder = useKnoweStore((s) => s.projectOrder);
  const favorites = useFavoritesStore((s) => s.entries);
  const knowledge = useKnowledgeStore((s) => s.cards);

  const userName = useSettingsStore((s) => s.userName);
  const mainModel = useSettingsStore((s) => s.mainModel);
  const auxModel = useSettingsStore((s) => s.auxModel);
  const notifyDesktop = useSettingsStore((s) => s.notifyDesktop);
  const closeToTray = useSettingsStore((s) => s.closeToTray);
  const approvalTimeoutS = useSettingsStore((s) => s.approvalTimeoutS);
  const appearance = useSettingsStore((s) => s.appearance);
  const fontScale = useSettingsStore((s) => s.fontScale);

  return useMemo(() => {
    const keyword = normalized(query.trim());
    const matches = (...fields: unknown[]): boolean => (
      !keyword || fields.some((field) => normalized(field).includes(keyword))
    );
    const groups: GlobalSearchGroup[] = [];
    const convIds = orderedConversationIds(convs, projectOrder);

    const conversations: GlobalSearchResult[] = [];
    for (const projectId of convIds) {
      const label = conversationLabel(projectId, convs);
      if (!matches(...label.searchFields)) continue;
      conversations.push({
        id: `conversation:${projectId}`,
        icon: 'conversation',
        text: label.text,
        meta: label.meta,
        target: { kind: 'conversation', projectId },
      });
    }
    if (conversations.length) groups.push({ name: t('global.search.16'), items: conversations.slice(0, MAX_PER_GROUP) });

    const contacts: GlobalSearchResult[] = [];
    if (matches(getZinniaDisplayName(), t('common.10'), t('contacts.view.28'), t('global.search.20'), t('common.16'))) {
      contacts.push({
        id: 'contact:zinnia',
        icon: 'contact',
        text: getZinniaDisplayName(),
        meta: t('contacts.view.28'),
        target: { kind: 'contact', projectId: null, agentId: null },
      });
    }
    for (const projectId of convIds) {
      if (projectId === PLATFORM_PROJECT_ID || parseDmId(projectId)) continue;
      const conv = convs[projectId];
      if (!conv) continue;
      const projectName = conv.projectName || projectId;
      for (const member of conv.members) {
        if (member.status === 'removed') continue;
        if (!matches(projectName, member.display.name, member.display.role, roleLabel(member.display.role), member.id)) continue;
        contacts.push({
          id: `contact:${projectId}:${member.id}`,
          icon: 'contact',
          text: `${projectName} · ${memberNameLabel(member.id, member.display.name)}`,
          meta: roleLabel(member.display.role) || 'Agent',
          target: { kind: 'contact', projectId, agentId: member.id },
        });
      }
    }
    if (contacts.length) groups.push({ name: t('global.search.40'), items: contacts.slice(0, MAX_PER_GROUP) });

    // 空关键词时不铺满历史消息；与权威 UI 的 cmdkResults 行为一致。
    if (keyword) {
      const messages: GlobalSearchResult[] = [];
      for (const projectId of convIds) {
        const conv = convs[projectId];
        if (!conv) continue;
        const label = conversationLabel(projectId, convs);
        conv.items.forEach((item, index) => {
          if ((item.kind !== 'user' && item.kind !== 'agent') || !item.text.trim()) return;
          if (!matches(item.text)) return;
          const itemKey = itemKeyOf(item, index);
          messages.push({
            id: `message:${projectId}:${itemKey}`,
            icon: 'message',
            text: oneLine(item.text),
            meta: label.text,
            target: { kind: 'message', projectId, itemKey },
          });
        });
      }
      if (messages.length) groups.push({ name: t('global.search.36'), items: messages.slice(0, MAX_PER_GROUP) });
    }

    const favoriteResults = favorites
      .filter((entry) => matches(
        entry.title, entry.digest, entry.body, entry.sourceName, entry.sourceProject,
        entry.type, ...entry.tags,
      ))
      .map<GlobalSearchResult>((entry) => ({
        id: `favorite:${entry.id}`,
        icon: 'favorite',
        text: entry.title,
        meta: entry.type,
        target: { kind: 'favorite', favoriteId: entry.id },
      }));
    if (favoriteResults.length) groups.push({ name: t('common.09'), items: favoriteResults.slice(0, MAX_PER_GROUP) });

    const knowledgeResults = knowledge
      .filter((card) => matches(
        card.title, card.body, card.clsZh, card.cat, card.appliesWhen,
        card.projectId, convs[card.projectId]?.projectName,
      ))
      .map<GlobalSearchResult>((card) => ({
        id: `knowledge:${card.id}`,
        icon: 'knowledge',
        text: card.title,
        meta: card.clsZh || card.cat,
        target: { kind: 'knowledge', cardId: card.id },
      }));
    if (knowledgeResults.length) groups.push({ name: t('global.search.39'), items: knowledgeResults.slice(0, MAX_PER_GROUP) });

    const modelText = [
      mainModel?.provider, mainModel?.model, auxModel?.provider, auxModel?.model,
    ].filter(Boolean).join(' ');
    const settingsSearch: Record<SettingsSection, unknown[]> = {
      '账户与身份': [t('global.search.42'), t('global.search.43'), t('global.search.04'), t('global.search.24'), t('global.search.38'), t('global.search.33'), userName],
      '模型与提供方': [t('common.18'), t('global.search.30'), t('global.search.21'), 'API Key', t('global.search.01'), t('global.search.13'), modelText],
      '通知': [
        t('global.search.14'), t('global.search.06'), t('global.search.17'), t('global.search.25'), t('global.search.29'),
        notifyDesktop ? t('global.search.28') : t('global.search.27'),
        closeToTray ? t('global.search.18') : t('global.search.19'),
        approvalTimeoutS === 0 ? t('global.search.08') : t('global.search.seconds', { n: approvalTimeoutS }),
      ],
      '外观': [
        t('global.search.03'), t('global.search.15'), t('global.search.10'), t('global.search.09'), t('global.search.05'),
        appearance === 'dark' ? t('global.search.37') : t('global.search.35'),
        fontScale === 'large' ? t('global.search.23') : t('global.search.26'),
      ],
      '关于': [t('global.search.02'), t('global.search.11'), t('global.search.34'), 'Knowe', t('global.search.41')],
    };
    const settingsResults = SETTINGS_SECTIONS
      .filter((section) => matches(section, t('common.12'), ...settingsSearch[section]))
      .map<GlobalSearchResult>((section) => ({
        id: `settings:${section}`,
        icon: 'settings',
        text: t('global.search.openSection', { section: t(SETTINGS_SECTION_T_KEY[section]) }),
        meta: t('common.12'),
        target: { kind: 'settings', section },
      }));
    if (settingsResults.length) groups.push({ name: t('global.search.22'), items: settingsResults.slice(0, MAX_PER_GROUP) });

    return groups;
  }, [
    query, convs, projectOrder, favorites, knowledge, userName, mainModel, auxModel,
    notifyDesktop, closeToTray, approvalTimeoutS, appearance, fontScale,
  ]);
}

export function flattenSearchGroups(groups: GlobalSearchGroup[]): GlobalSearchResult[] {
  return groups.flatMap((group) => group.items);
}

const ResultIcon: React.FC<{ kind: SearchIconKind }> = ({ kind }) => {
  switch (kind) {
    case 'contact': return <IconContacts />;
    case 'favorite': return <IconStar />;
    case 'knowledge': return <IconSpark />;
    case 'settings': return <IconSettings />;
    default: return <IconChats />;
  }
};

const HighlightedText: React.FC<{ text: string; query: string }> = ({ text, query }) => {
  const keyword = query.trim();
  if (!keyword) return <>{text}</>;
  const index = normalized(text).indexOf(normalized(keyword));
  if (index < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, index)}
      <mark>{text.slice(index, index + keyword.length)}</mark>
      {text.slice(index + keyword.length)}
    </>
  );
};

interface GlobalSearchResultsProps {
  query: string;
  groups: GlobalSearchGroup[];
  onSelect: (target: GlobalSearchTarget) => void;
  activeIndex?: number;
  onActiveIndexChange?: (index: number) => void;
}

/** 只负责分组和行渲染；搜索数据由两个入口共同传入。 */
export const GlobalSearchResults: React.FC<GlobalSearchResultsProps> = ({
  query, groups, onSelect, activeIndex, onActiveIndexChange,
}) => {
  const { t } = useTranslation();
  if (!groups.length) {
    return (
      <div className="cmdk-empty">
        {t('global.search.noResults', { query: query.trim() })}
      </div>
    );
  }

  let rowIndex = 0;
  return (
    <>
      {groups.map((group) => (
        <React.Fragment key={group.name}>
          <div className="cmdk-grp">{group.name}</div>
          {group.items.map((item) => {
            const index = rowIndex;
            rowIndex += 1;
            return (
              <div
                key={item.id}
                className={'cmdk-item' + (activeIndex === index ? ' hl' : '')}
                role="option"
                aria-selected={activeIndex === index}
                tabIndex={0}
                onFocus={() => onActiveIndexChange?.(index)}
                onMouseEnter={() => onActiveIndexChange?.(index)}
                onMouseDown={(event: React.MouseEvent<HTMLDivElement>) => event.preventDefault()}
                onClick={() => onSelect(item.target)}
                onKeyDown={(event: React.KeyboardEvent<HTMLDivElement>) => {
                  if (event.key !== 'Enter' && event.key !== ' ') return;
                  event.preventDefault();
                  onSelect(item.target);
                }}
              >
                <span className="ci-ic"><ResultIcon kind={item.icon} /></span>
                <span className="ci-tx"><HighlightedText text={item.text} query={query} /></span>
                <span className="ci-meta">{item.meta}</span>
              </div>
            );
          })}
        </React.Fragment>
      ))}
    </>
  );
};

interface CommandPaletteProps {
  onNavigate: (target: GlobalSearchTarget) => void;
}

/** Rail 按钮与 Cmd/Ctrl+K 共用的全局命令面板。 */
export const CommandPalette: React.FC<CommandPaletteProps> = ({ onNavigate }) => {
  const { t } = useTranslation();
  const open = useKnoweStore(selectCmdKOpen);
  const toggleCmdK = useKnoweStore((s) => s.toggleCmdK);
  const closeCmdK = useKnoweStore((s) => s.closeCmdK);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const groups = useGlobalSearchGroups(query);
  const flat = useMemo(() => flattenSearchGroups(groups), [groups]);

  const close = (): void => {
    setQuery('');
    setActiveIndex(0);
    closeCmdK();
  };

  useEffect(() => {
    const onGlobalKeyDown = (event: KeyboardEvent): void => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === 'k') {
        event.preventDefault();
        if (useKnoweStore.getState().cmdKOpen) inputRef.current?.focus();
        else toggleCmdK();
        return;
      }
      if (event.key === 'Escape' && useKnoweStore.getState().cmdKOpen) {
        event.preventDefault();
        closeCmdK();
      }
    };
    window.addEventListener('keydown', onGlobalKeyDown);
    return () => window.removeEventListener('keydown', onGlobalKeyDown);
  }, [closeCmdK, toggleCmdK]);

  useEffect(() => {
    setQuery('');
    setActiveIndex(0);
    if (!open) return undefined;
    const frame = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open]);

  useEffect(() => {
    if (activeIndex < flat.length) return;
    setActiveIndex(flat.length ? flat.length - 1 : 0);
  }, [activeIndex, flat.length]);

  useEffect(() => {
    resultsRef.current?.querySelector<HTMLElement>('.cmdk-item.hl')
      ?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  if (!open) return null;

  const select = (target: GlobalSearchTarget): void => {
    onNavigate(target);
    close();
  };

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    if (!flat.length) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % flat.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + flat.length) % flat.length);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const target = flat[activeIndex] ? flat[activeIndex].target : flat[0]?.target;
      if (target) select(target);
    }
  };

  return (
    <div
      className="scrim"
      onMouseDown={(event: React.MouseEvent<HTMLDivElement>) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div id="globalSearchDialog" className="cmdk" role="dialog" aria-modal="true" aria-label={t('conv.list.01')}>
        <div className="cmdk-in">
          <IconSearchSm />
          <input
            ref={inputRef}
            value={query}
            placeholder={t('global.search.31')}
            aria-label={t('global.search.32')}
            onChange={(event: React.ChangeEvent<HTMLInputElement>) => {
              setQuery(event.target.value);
              setActiveIndex(0);
            }}
            onKeyDown={onInputKeyDown}
          />
          <span className="cmdk-hint">Esc</span>
        </div>
        <div ref={resultsRef} className="cmdk-res" role="listbox">
          <GlobalSearchResults
            query={query}
            groups={groups}
            onSelect={select}
            activeIndex={activeIndex}
            onActiveIndexChange={setActiveIndex}
          />
        </div>
      </div>
    </div>
  );
};

export default CommandPalette;
