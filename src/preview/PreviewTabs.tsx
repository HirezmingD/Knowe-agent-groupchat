/** 独立预览窗口的可激活、可关闭、可拖拽排序标签栏。 */

import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconClose, IconForKind } from './icons';
import { kindOf } from './fileKinds';
import type { PreviewTab } from './PreviewApp';

interface PreviewTabsProps {
  tabs: PreviewTab[];
  activeKey: string | null;
  onActivate: (key: string) => void;
  onClose: (key: string) => void;
  onMove: (fromKey: string, toKey: string, side: 'before' | 'after') => void;
}

const PreviewTabs: React.FC<PreviewTabsProps> = ({
  tabs,
  activeKey,
  onActivate,
  onClose,
  onMove,
}) => {
  const { t } = useTranslation();
  const draggingKeyRef = useRef<string | null>(null);
  const tabRefs = useRef(new Map<string, HTMLDivElement>());
  const [dropTarget, setDropTarget] = useState<{
    key: string;
    side: 'before' | 'after';
  } | null>(null);

  const closeAndRestoreFocus = (key: string): void => {
    const currentIndex = tabs.findIndex((candidate) => candidate.key === key);
    const focusTarget = key === activeKey
      ? tabs[currentIndex + 1] ?? tabs[currentIndex - 1]
      : undefined;
    onClose(key);
    if (focusTarget) {
      requestAnimationFrame(() => tabRefs.current.get(focusTarget.key)?.focus());
    }
  };

  return (
    <div className="preview-tabs" role="tablist" aria-label={t('preview.tabs.02')}>
      {tabs.map((tab) => {
        const active = tab.key === activeKey;
        const kind = kindOf(tab.file);
        const dropClass = dropTarget?.key === tab.key ? ` drop-${dropTarget.side}` : '';
        return (
          <div
            key={tab.key}
            ref={(node: HTMLDivElement | null) => {
              if (node) tabRefs.current.set(tab.key, node);
              else tabRefs.current.delete(tab.key);
            }}
            id={`preview-tab-${encodeURIComponent(tab.key)}`}
            className={`preview-tab${active ? ' active' : ''}${tab.status === 'error' ? ' error' : ''}${dropClass}`}
            role="tab"
            aria-selected={active}
            aria-controls={`preview-pane-${encodeURIComponent(tab.key)}`}
            tabIndex={active ? 0 : -1}
            draggable
            onClick={() => onActivate(tab.key)}
            onKeyDown={(event: React.KeyboardEvent<HTMLDivElement>) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onActivate(tab.key);
                return;
              }

              const currentIndex = tabs.findIndex((candidate) => candidate.key === tab.key);
              let targetIndex = -1;
              if (event.key === 'ArrowLeft') {
                targetIndex = (currentIndex - 1 + tabs.length) % tabs.length;
              } else if (event.key === 'ArrowRight') {
                targetIndex = (currentIndex + 1) % tabs.length;
              } else if (event.key === 'Home') {
                targetIndex = 0;
              } else if (event.key === 'End') {
                targetIndex = tabs.length - 1;
              } else if (event.key === 'Delete') {
                event.preventDefault();
                closeAndRestoreFocus(tab.key);
                return;
              } else {
                return;
              }

              const target = tabs[targetIndex];
              if (!target) return;
              event.preventDefault();
              onActivate(target.key);
              requestAnimationFrame(() => tabRefs.current.get(target.key)?.focus());
            }}
            onDragStart={(event: React.DragEvent<HTMLDivElement>) => {
              draggingKeyRef.current = tab.key;
              setDropTarget(null);
              event.dataTransfer.effectAllowed = 'move';
              event.dataTransfer.setData('text/plain', tab.key);
            }}
            onDragOver={(event: React.DragEvent<HTMLDivElement>) => {
              event.preventDefault();
              event.dataTransfer.dropEffect = 'move';
              if (draggingKeyRef.current === tab.key) {
                setDropTarget(null);
                return;
              }
              const rect = event.currentTarget.getBoundingClientRect();
              const side = event.clientX < rect.left + rect.width / 2 ? 'before' : 'after';
              setDropTarget((current) => (
                current?.key === tab.key && current.side === side ? current : { key: tab.key, side }
              ));
            }}
            onDragLeave={(event: React.DragEvent<HTMLDivElement>) => {
              const related = event.relatedTarget;
              if (related instanceof Node && event.currentTarget.contains(related)) return;
              setDropTarget((current) => current?.key === tab.key ? null : current);
            }}
            onDrop={(event: React.DragEvent<HTMLDivElement>) => {
              event.preventDefault();
              const source = draggingKeyRef.current || event.dataTransfer.getData('text/plain');
              const rect = event.currentTarget.getBoundingClientRect();
              const side = event.clientX < rect.left + rect.width / 2 ? 'before' : 'after';
              if (source && source !== tab.key) onMove(source, tab.key, side);
              draggingKeyRef.current = null;
              setDropTarget(null);
            }}
            onDragEnd={() => {
              draggingKeyRef.current = null;
              setDropTarget(null);
            }}
            title={tab.file.path}
          >
            <span className="preview-tab-icon" aria-hidden="true">
              <IconForKind kind={kind} size={15} />
            </span>
            <span className="preview-tab-name">{tab.file.name}</span>
            {tab.status === 'resolving' && <span className="preview-tab-dot" aria-label={t('preview.app.03')} />}
            {tab.status === 'error' && <span className="preview-tab-error-mark" aria-label={t('preview.tabs.03')}>!</span>}
            <button
              type="button"
              className="preview-tab-close"
              onClick={(event: React.MouseEvent<HTMLButtonElement>) => {
                event.stopPropagation();
                closeAndRestoreFocus(tab.key);
              }}
              onKeyDown={(event: React.KeyboardEvent<HTMLButtonElement>) => {
                event.stopPropagation();
              }}
              tabIndex={active ? 0 : -1}
              aria-label={`${t('app.01')} ${tab.file.name}`}
              title={t('preview.tabs.01')}
            >
              <IconClose size={13} />
            </button>
          </div>
        );
      })}
    </div>
  );
};

export default PreviewTabs;
