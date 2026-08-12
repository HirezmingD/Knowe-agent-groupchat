/**
 * KnowledgePreview.tsx — [v0.43] 知识资产 / 技能包的右侧预览面板
 *
 * 交互沿用 v0.36.1 文件预览机制：面板浮在 .stage 右侧之上，不挤压卡片墙；
 * 支持左缘拖拽调宽、跨开合记忆宽度、Esc 关闭，以及在面板保持打开时切换目标。
 *
 * 知识资产读取 ASSET.md，技能包读取真实 SKILL.md；两者都剥离 front-matter 后交给
 * 共享 <Markdown/> 渲染。PROFILE.md 继续作为全局知识的画像层入口。
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  useKnowledgeStore, knowDateLabel, USAGE_ZH, KN_PREVIEW_MIN, knPreviewMax,
  type KnowAssetDetail, type SkillPackDetail,
} from '../store/knowledge';
import { useTranslation } from 'react-i18next';
import { Markdown } from './markdown';
import { IconBook } from './icons';
import ZoomPanViewport from './ZoomPan';

/** ✕（16px，与文件预览同形） */
const IcClose: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round">
    <path d="m6 6 12 12M18 6 6 18" />
  </svg>
);

/** 拼图（技能包预览） */
const IcPuzzle: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10 3.5a1.8 1.8 0 0 1 3.6 0V5H17a2 2 0 0 1 2 2v3.2h-1.5a1.8 1.8 0 0 0 0 3.6H19V17a2 2 0 0 1-2 2h-3.2v-1.5a1.8 1.8 0 0 0-3.6 0V19H7a2 2 0 0 1-2-2v-3.2h1.5a1.8 1.8 0 0 0 0-3.6H5V7a2 2 0 0 1 2-2h3z" />
  </svg>
);

/** 把 ASSET.md / SKILL.md 的 front-matter 剥掉，只把正文交给 Markdown 渲染。 */
function splitFrontMatter(md: string): string {
  const m = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(md);
  return m ? md.slice(m[0].length) : md;
}

const SKILL_KIND_ZH = {
  system_builtin: 'knowledge.preview.12',
  project_experience: 'knowledge.preview.14',
  third_party: 'knowledge.preview.11',
} as const;

const SKILL_STATUS_ZH = {
  active: 'knowledge.preview.10',
  pending: 'knowledge.preview.05',
  retired: 'knowledge.preview.04',
} as const;

export const KnowledgePreview: React.FC = () => {
  const { t } = useTranslation();
  const preview = useKnowledgeStore((s) => s.preview);
  const width = useKnowledgeStore((s) => s.previewWidth);
  const setWidth = useKnowledgeStore((s) => s.setPreviewWidth);
  const closePreview = useKnowledgeStore((s) => s.closePreview);

  const [detail, setDetail] = useState<KnowAssetDetail | null>(null);
  const [skillDetail, setSkillDetail] = useState<SkillPackDetail | null>(null);
  const [profileText, setProfileText] = useState<string>('');
  const [state, setState] = useState<'idle' | 'loading' | 'error'>('idle');
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);

  const open = preview !== null;

  // 换目标 → 拉取内容（面板不关不跳，只换内容——与文件预览同规矩）。
  useEffect(() => {
    if (!preview) {
      setState('idle');
      return undefined;
    }

    let alive = true;
    setDetail(null);
    setSkillDetail(null);
    setProfileText('');
    setState('loading');

    const st = useKnowledgeStore.getState();
    if (preview.kind === 'asset' && preview.card) {
      void st.fetchDetail(preview.card).then((d) => {
        if (!alive) return;
        if (d) { setDetail(d); setState('idle'); } else setState('error');
      });
    } else if (preview.kind === 'skill' && preview.pack) {
      void st.fetchSkillpackDetail(preview.pack).then((d) => {
        if (!alive) return;
        if (d) { setSkillDetail(d); setState('idle'); } else setState('error');
      });
    } else if (preview.kind === 'profile') {
      void st.fetchProfile(preview.projectId).then((text) => {
        if (!alive) return;
        if (text !== null) { setProfileText(text); setState('idle'); } else setState('error');
      });
    } else {
      setState('error');
    }

    return () => { alive = false; };
  }, [preview]);

  // Esc 关闭（非模态，只拦 Esc）。
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') closePreview();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, closePreview]);

  // 窗口缩放 → 上限跟着变；超了就收窄（含 150ms debounce）。
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const reclamp = (): void => {
      const st = useKnowledgeStore.getState();
      const max = knPreviewMax();
      if (st.previewWidth > max) st.setPreviewWidth(max);
    };
    const debounced = (): void => { clearTimeout(timer); timer = setTimeout(reclamp, 150); };
    window.addEventListener('resize', debounced);
    return () => { window.removeEventListener('resize', debounced); clearTimeout(timer); };
  }, []);

  // ── 拖拽调宽（手柄在左缘：往左拖 = 变宽）──
  const onHandleDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    dragRef.current = { startX: e.clientX, startW: width };
    setDragging(true);
  }, [width]);
  const onHandleMove = useCallback((e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    setWidth(d.startW + (d.startX - e.clientX));
  }, [setWidth]);
  const endDrag = useCallback(() => { dragRef.current = null; setDragging(false); }, []);

  const card = preview?.kind === 'asset' ? (detail?.card ?? preview.card) : null;
  const pack = preview?.kind === 'skill' ? (skillDetail?.pack ?? preview.pack ?? null) : null;
  const title = preview?.kind === 'profile'
    ? t('knowledge.preview.16')
    : preview?.kind === 'skill'
      ? (pack?.name ?? '')
      : (card?.title ?? '');

  const previewKey = preview?.kind === 'asset'
    ? (card?.id ?? 'asset')
    : preview?.kind === 'skill'
      ? `skill:${pack?.packId ?? 'unknown'}`
      : `profile:${preview?.projectId ?? ''}`;

  return (
    <div
      className={'kn-preview-wrap' + (open ? ' open' : '') + (dragging ? ' dragging' : '')}
      style={{ ['--knpw' as string]: `${width}px` }}
      aria-hidden={!open}
    >
      {preview && (
        <section className="kn-preview" role="complementary" aria-label={t('knowledge.preview.aria', { title })}>
          {/* 左缘拖拽手柄 */}
          <div
            className="kn-preview-resize"
            role="separator"
            aria-label={t('knowledge.preview.18')}
            aria-valuemin={KN_PREVIEW_MIN}
            aria-valuemax={knPreviewMax()}
            aria-valuenow={width}
            onPointerDown={onHandleDown}
            onPointerMove={onHandleMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
          />

          {/* 头部：图标 + 标题 + 类型 chip + ✕ */}
          <header className="kn-preview-head">
            <span className="kn-pv-icon" aria-hidden="true">
              {preview.kind === 'skill' ? <IcPuzzle /> : <IconBook />}
            </span>
            <span className="kn-pv-name" title={title}>{title}</span>
            {card && <span className={`chip ${card.chip}`}>{card.cat}</span>}
            {pack && (
              <span className={`chip ${pack.kind === 'system_builtin' ? 'info' : pack.kind === 'project_experience' ? 'acc' : 'ok'}`}>
                {t(SKILL_KIND_ZH[pack.kind])}
              </span>
            )}
            <button
              type="button" className="kn-pv-close" onClick={closePreview}
              title={t('knowledge.preview.17')} aria-label={t('favorites.view.01')}
            >
              <IcClose />
            </button>
          </header>

          {/* 元信息行（front-matter 的人话版） */}
          {card && (
            <div className="kn-pv-meta">
              <span>{card.clsZh || card.cls}</span>
              <span className="sep">·</span>
              <span>{card.scope === 'global' ? t('knowledge.preview.03') : t('knowledge.preview.13')}</span>
              <span className="sep">·</span>
              <span>
                {card.status === 'core' ? t('knowledge.preview.07')
                  : card.status === 'validated' ? t('knowledge.preview.10')
                    : card.status === 'retired' ? t('knowledge.preview.04') : t('knowledge.preview.05')}
              </span>
              <span className="sep">·</span>
              <span title={t('knowledge.preview.15')}>
                {t('knowledge.preview.utility', { n: card.utility.toFixed(2) })}
              </span>
              <span className="sep">·</span>
              <span title={t('knowledge.view.sourceCount', { n: card.sourceCount })}>{t('knowledge.view.citeCount', { n: card.cites })}</span>
            </div>
          )}
          {pack && (
            <div className="kn-pv-meta">
              <span>{t(SKILL_KIND_ZH[pack.kind])}</span>
              <span className="sep">·</span>
              <span>{pack.scope === 'global' ? t('knowledge.preview.02') : t('knowledge.preview.06')}</span>
              <span className="sep">·</span>
              <span>{pack.immutable ? t('knowledge.preview.09') : t(SKILL_STATUS_ZH[pack.status])}</span>
              {pack.projectId && (
                <>
                  <span className="sep">·</span>
                  <span title={pack.projectId}>{t('common.14')} {pack.projectId}</span>
                </>
              )}
              {pack.updatedAt && (
                <>
                  <span className="sep">·</span>
                  <span>{t('knowledge.preview.updatedAt', { date: knowDateLabel(pack.updatedAt) })}</span>
                </>
              )}
            </div>
          )}

          {/* 内容区：key 到目标 id —— 换目标整块重挂，滚动位置不串台
              [v0.44 §3.4] 知识卡预览与文件预览同权：ZoomPanViewport 提供缩放 + 拖拽平移，
              100% 档保持原生滚动与文字选择。 */}
          <div className="kn-preview-body" key={previewKey}>
            <ZoomPanViewport label={t('knowledge.preview.19')}>
            {state === 'loading' ? (
              <div className="kn-pv-state">{t('knowledge.preview.08')}</div>
            ) : state === 'error' ? (
              <div className="kn-pv-state">{t('knowledge.preview.20')}</div>
            ) : preview.kind === 'profile' ? (
              <div className="kn-md">
                <Markdown text={profileText || t('knowledge.preview.21')} />
              </div>
            ) : preview.kind === 'skill' ? (
              skillDetail ? (
                <div className="kn-md">
                  <Markdown text={splitFrontMatter(skillDetail.bodyMd) || t('knowledge.preview.22')} />
                </div>
              ) : null
            ) : detail ? (
              <>
                <div className="kn-md">
                  <Markdown text={splitFrontMatter(detail.bodyMd)} />
                </div>
                {detail.usage.length > 0 && (
                  <div className="kn-pv-usage">
                    <div className="kn-pv-usage-head">{t('knowledge.preview.01')}</div>
                    {detail.usage.map((u, i) => (
                      <div className="kn-pv-usage-row" key={`${u.kind}-${u.step}-${i}`}>
                        <span>{USAGE_ZH[u.kind] || u.kind}</span>
                        {typeof u.step === 'number' && <span>{t('knowledge.view.stepCount', { n: u.step })}</span>}
                        <span className="kn-pv-usage-date">{knowDateLabel(u.at)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : null}
            </ZoomPanViewport>
          </div>
        </section>
      )}
    </div>
  );
};

export default KnowledgePreview;
