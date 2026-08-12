/** 消息气泡下方的文件卡片；点击后只请求独立原生预览窗口。 */

import React, { useEffect, useRef, useState } from 'react';
import type { ProducedFile, AttachmentInput } from '../store/state';
import { openPreviewWindow, revealFileInFolder } from '../store/filePreview';
import { humanBytes, kindOf, typeLabel } from '../preview/fileKinds';
import { FolderRevealIcon, IconForKind } from '../preview/icons';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import '../styles/knowe-preview.css';

const COLLAPSED_FILE_LIMIT = 3;

const FileCard: React.FC<{ file: ProducedFile; projectId: string }> = ({ file, projectId }) => {
  const { t } = useTranslation();
  const [previewState, setPreviewState] = useState<'idle' | 'opening' | 'error'>('idle');
  const [previewError, setPreviewError] = useState('');
  const [revealState, setRevealState] = useState<'idle' | 'opening' | 'error'>('idle');
  const [revealError, setRevealError] = useState('');
  const mountedRef = useRef(true);
  const previewRunRef = useRef(0);
  const revealRunRef = useRef(0);
  const kind = kindOf(file);
  const size = humanBytes(file.bytes);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      previewRunRef.current += 1;
      revealRunRef.current += 1;
    };
  }, []);

  const activate = (): void => {
    if (previewState === 'opening') return;
    const run = ++previewRunRef.current;
    setPreviewState('opening');
    setPreviewError('');
    void openPreviewWindow(file, projectId)
      .then(() => {
        if (!mountedRef.current || run !== previewRunRef.current) return;
        setPreviewState('idle');
      })
      .catch((error: unknown) => {
        if (!mountedRef.current || run !== previewRunRef.current) return;
        setPreviewState('error');
        setPreviewError(error instanceof Error ? error.message : t('file.card.07'));
      });
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    activate();
  };

  const onReveal = (event: React.MouseEvent<HTMLButtonElement>): void => {
    event.preventDefault();
    event.stopPropagation();
    if (revealState === 'opening') return;
    const run = ++revealRunRef.current;
    setRevealState('opening');
    setRevealError('');
    void revealFileInFolder(projectId, file)
      .then(() => {
        if (!mountedRef.current || run !== revealRunRef.current) return;
        setRevealState('idle');
      })
      .catch((error: unknown) => {
        if (!mountedRef.current || run !== revealRunRef.current) return;
        setRevealState('error');
        setRevealError(error instanceof Error ? error.message : t('file.card.02'));
      });
  };

  return (
    <div
      className={'file-card' + (previewState === 'opening' ? ' busy' : '')}
      role="button"
      tabIndex={0}
      onClick={activate}
      onKeyDown={onKeyDown}
      title={previewState === 'error' ? previewError : t('file.card.openWindowTitle', { name: file.name })}
      aria-label={t('file.card.previewAria', { name: file.name })}
      aria-busy={previewState === 'opening'}
      data-fc-name={file.name}
    >
      <span className="fc-icon" aria-hidden="true">
        <IconForKind kind={kind} size={20} />
      </span>
      <span className="fc-body">
        <span className="fc-name">{file.name}</span>
        <span className="fc-meta">
          <span className="fc-tag">{typeLabel(file)}</span>
          {size && <span className="fc-size">{size}</span>}
        </span>
        {previewState === 'error' && (
          <span className="fc-preview-error" role="alert">{previewError}</span>
        )}
      </span>
      <button
        type="button"
        className={'fc-reveal' + (revealState === 'opening' ? ' busy' : '')}
        onClick={onReveal}
        onKeyDown={(event: React.KeyboardEvent<HTMLButtonElement>) => event.stopPropagation()}
        title={revealState === 'error' ? revealError : t('file.card.05')}
        aria-label={t('file.card.openDirAria', { name: file.name })}
        aria-busy={revealState === 'opening'}
      >
        <FolderRevealIcon size={16} />
      </button>
      {revealState === 'error' && (
        <span className="fc-reveal-error" role="alert">{revealError}</span>
      )}
    </div>
  );
};

/** 一条消息可产出多个文件；按路径去重后逐张显示。 */
export const FileCardList: React.FC<{ files: ProducedFile[]; projectId: string }> = ({
  files, projectId,
}) => {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const seen = new Set<string>();
  const unique = files.filter((file) => {
    if (!file?.path || seen.has(file.path)) return false;
    seen.add(file.path);
    return true;
  });
  if (unique.length === 0) return null;
  const collapsible = unique.length > COLLAPSED_FILE_LIMIT;
  const visible = collapsible && !expanded
    ? unique.slice(0, COLLAPSED_FILE_LIMIT)
    : unique;
  const hiddenCount = unique.length - visible.length;
  return (
    <div className="file-card-list">
      {visible.map((file) => (
        <FileCard key={file.path} file={file} projectId={projectId} />
      ))}
      {collapsible && (
        <div className="file-card-fold">
          <span className="file-card-fold-summary">
            {t('file.card.countHidden', { n: unique.length, m: hiddenCount })}
          </span>
          <button
            type="button"
            className="file-card-fold-toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((current) => !current)}
          >
            {expanded ? t('file.card.01') : t('file.card.expandMore', { n: hiddenCount })}
          </button>
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════
// [v1.0.19.4] 本地附件卡片
//
//   和 Worker 产物卡（上面的 FileCard）长得一样，但数据源不同：这里是**用户本机的
//   绝对路径**，走不了「项目内相对路径」的独立预览通道（filePreview 会拒绝绝对路径）。
//   所以点卡片改为用系统默认程序打开原文件（直读原路径、不复制字节，DESIGN 决策 #1/#3）；
//   文件被移动/删除时后端 stat 不到 → 明确提示（验收 #6）。
// ═══════════════════════════════════════════════════════════════

type LocalResult = { ok: boolean; reason?: 'missing' | 'guard' | 'error' };

function localAttachmentBridge(): {
  openLocalFile?: (path: string, sig: string) => Promise<LocalResult>;
  revealLocalFile?: (path: string, sig: string) => Promise<LocalResult>;
} | undefined {
  if (typeof window === 'undefined') return undefined;
  return (window as unknown as {
    knowe?: {
      openLocalFile?: (path: string, sig: string) => Promise<LocalResult>;
      revealLocalFile?: (path: string, sig: string) => Promise<LocalResult>;
    };
  }).knowe;
}

function reasonText(reason?: string): string {
  if (reason === 'missing') return i18n.t('file.card.03');
  if (reason === 'guard') return i18n.t('file.card.06');
  return i18n.t('file.card.08');
}

const AttachmentCard: React.FC<{ file: AttachmentInput }> = ({ file }) => {
  const { t } = useTranslation();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const previewFile = { path: file.path, name: file.name, ext: file.ext };
  const kind = kindOf(previewFile);
  const size = humanBytes(typeof file.size === 'number' ? file.size : undefined);

  const open = (): void => {
    const bridge = localAttachmentBridge();
    if (!bridge?.openLocalFile || !file.sig) {
      setError(t('file.card.04'));
      return;
    }
    setBusy(true);
    setError('');
    void bridge.openLocalFile(file.path, file.sig)
      .then((result) => {
        setBusy(false);
        if (!result || !result.ok) setError(reasonText(result?.reason));
      })
      .catch(() => { setBusy(false); setError(t('file.card.08')); });
  };

  const reveal = (event: React.MouseEvent<HTMLButtonElement>): void => {
    event.preventDefault();
    event.stopPropagation();
    const bridge = localAttachmentBridge();
    if (!bridge?.revealLocalFile || !file.sig) return;
    void bridge.revealLocalFile(file.path, file.sig)
      .then((result) => { if (!result || !result.ok) setError(reasonText(result?.reason)); })
      .catch(() => { /* ignore */ });
  };

  return (
    <div
      className={'file-card' + (busy ? ' busy' : '')}
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }}
      title={error || t('file.card.openTitle', { name: file.name })}
      aria-label={t('file.card.openAttachmentAria', { name: file.name })}
      aria-busy={busy}
      data-fc-name={file.name}
    >
      <span className="fc-icon" aria-hidden="true">
        <IconForKind kind={kind} size={20} />
      </span>
      <span className="fc-body">
        <span className="fc-name">{file.name}</span>
        <span className="fc-meta">
          <span className="fc-tag">{typeLabel(previewFile)}</span>
          {size && <span className="fc-size">{size}</span>}
        </span>
        {error && <span className="fc-preview-error" role="alert">{error}</span>}
      </span>
      <button
        type="button"
        className="fc-reveal"
        onClick={reveal}
        onKeyDown={(event: React.KeyboardEvent<HTMLButtonElement>) => event.stopPropagation()}
        title={t('file.card.05')}
        aria-label={t('file.card.openDirAria', { name: file.name })}
      >
        <FolderRevealIcon size={16} />
      </button>
    </div>
  );
};

/** 一条用户消息可带多个本地附件；按路径去重后逐张显示。 */
export const AttachmentCardList: React.FC<{ files: AttachmentInput[] }> = ({ files }) => {
  const seen = new Set<string>();
  const unique = files.filter((file) => {
    if (!file?.path || seen.has(file.path)) return false;
    seen.add(file.path);
    return true;
  });
  if (unique.length === 0) return null;
  return (
    <div className="file-card-list">
      {unique.map((file) => (
        <AttachmentCard key={file.path} file={file} />
      ))}
    </div>
  );
};

export default FileCard;
