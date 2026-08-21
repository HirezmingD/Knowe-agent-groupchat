/**
 * Avatar.tsx — 共享原子件（component-tree §H）
 *
 * DOM：div.avatar.av-{28|32|36|40|44}.av-{a|b|c|d|n}「字形」
 *
 * [v0.4] 现在支持图片头像：传 src 就渲染 <img>，不传就还是字形。
 *   样式不用新增——knowe-tokens.css 第 221 行早就有 `.avatar img` 了（圆形裁切），
 *   只是从来没人往里塞过图片。
 *
 * ★ 图片加载失败（文件缺了、路径错了）→ **自动退回字形**，绝不留一个空白圆圈。
 *   头像池有 396 张，少一张就白一块，这种事必须自己兜住。
 */

import React, { useLayoutEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { preloadAvatar, avatarCacheState } from '../store/avatarPreload';

export type AvatarSize = 28 | 32 | 36 | 40 | 44;
export type AvatarPal = 'av-a' | 'av-b' | 'av-c' | 'av-d' | 'av-n';

const PALS: AvatarPal[] = ['av-a', 'av-b', 'av-c', 'av-d'];

/**
 * [v1.0.21.3] 中文单字 glyph → 英文首字母（与 DEFAULT_AGENTS 的 roleEn 首字母一致）。
 * 英文模式下头像字形用英文字母，避免满屏汉字。
 */
const GLYPH_EN: Record<string, string> = {
  '总': 'C', '知': 'R', '前': 'F', '后': 'B', '产': 'P', '测': 'Q', '设': 'D',
  '数': 'D', '运': 'D', '安': 'S', '智': 'M', '移': 'M', '游': 'G', '图': 'G',
  '销': 'M', '财': 'F', '医': 'H', '学': 'A', '空': 'S', '支': 'S', '稳': 'S',
  '库': 'D', '构': 'A', '写': 'W', '媒': 'M', '法': 'L',
};

/** 按当前语言取字形：中文模式用汉字 glyph，英文模式用映射的英文字母（查不到保持原样） */
export function displayGlyph(glyph: string, lang: string): string {
  if (lang === 'en') return GLYPH_EN[glyph] ?? glyph;
  return glyph;
}

/** 由任意 id 稳定派生配色（同一 id 永远同一色） */
export function palOf(id: string): AvatarPal {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return PALS[h % PALS.length] as AvatarPal;
}

/** 由任意名字派生字形（取首字） */
export function glyphOf(name: string): string {
  return (name || '?').trim().charAt(0) || '?';
}

export interface AvatarProps {
  glyph: string;
  pal?: string;
  size?: AvatarSize;
  title?: string;
  /** [v0.4] 图片头像的 URL。加载失败会自动退回字形。 */
  src?: string;
  /**
   * [v0.9b] 灰掉（已归档的成员）。
   *
   * 左栏宫格是**直接把归档的人滤掉**的（ConvList）——那儿要回答的问题是
   * 「现在这个群里有谁」。
   * 这个 prop 是给**花名册面板**留的：那儿要回答的是「这个项目有过谁」——
   * 走了的人应该还在名单上，只是灰着，旁边写一句「已离开」。
   * （RosterPanel 不在这次的改动范围里，所以现在还没人用它。留好口子。）
   */
  dimmed?: boolean;
}

export const Avatar: React.FC<AvatarProps> = ({
  glyph, pal = 'av-n', size = 36, title, src, dimmed = false,
}) => {
  const [broken, setBroken] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const { i18n } = useTranslation();

  /*
   * [v1.0.23.6] 模块级头像预加载缓存——根治「跳转后头像卡字形」。
   *
   *   旧逻辑每次挂载都 new Image() 重新加载：从私聊切回群聊时列表重排、
   *   群卡片重新挂载，4+ 个头像同时重新请求（dev 下 no-cache 强制重新验证），
   *   加载完成前一直显示文字字形——用户看到「卡两秒」。
   *   现在同一 src 页面生命周期内只加载一次。useLayoutEffect 同步查缓存：
   *   已 ok 的图在 paint 前直接亮图，连一帧字形都不会闪。
   */
  useLayoutEffect(() => {
    if (!src) { setLoaded(false); setBroken(false); return; }
    const st = avatarCacheState(src);
    if (st === 'ok') { setLoaded(true); setBroken(false); return; }
    if (st === 'err') { setBroken(true); setLoaded(false); return; }
    let alive = true;
    setLoaded(false);
    setBroken(false);
    void preloadAvatar(src).then((s) => {
      if (!alive) return;
      if (s === 'ok') setLoaded(true);
      else setBroken(true);
    });
    return () => { alive = false; };
  }, [src]);

  const showImage = Boolean(src) && loaded && !broken;
  const shownGlyph = displayGlyph(glyph, i18n.language);

  return (
    <div
      className={`avatar av-${size} ${pal}` + (dimmed ? ' dimmed' : '')}
      title={title}
      aria-hidden="true"
    >
      {showImage ? (
        <img src={src} alt="" onError={() => setBroken(true)} />
      ) : (
        shownGlyph
      )}
    </div>
  );
};

export default Avatar;

// ═══════════════════════════════════════════════════════════════
// [v0.5b #6] 群聊头像宫格
// ═══════════════════════════════════════════════════════════════

/** 宫格里的空位：一个白圆圈（只有项目经理一个人时，右边那格） */
export const AvatarBlank: React.FC = () => (
  <div className="cav-blank" aria-hidden="true" />
);

export interface GridMember {
  id: string;
  glyph: string;
  pal: string;
  avatarUrl?: string;
}

/**
 * 群聊头像宫格。
 *
 * 排布规则（成员数 → 每行几个）：
 *   1  → [2]      项目经理 + 一个空白圆圈（一个人也不能显得孤零零一个圆点）
 *   2  → [2]      左项目经理、右成员
 *   3  → [1, 2]   三角
 *   4  → [2, 2]   正方
 *   5  → [1, 3, 1] 五角
 *   6~9 → 每行 3 个（九宫格封顶）
 *
 * 项目经理永远排第一个（左上）。超过 9 个人的截断——**头像框就那么大，
 * 塞十个人进去谁也看不清**。
 */
function rowsFor(n: number): number[] {
  if (n <= 2) return [2];
  if (n === 3) return [1, 2];
  if (n === 4) return [2, 2];
  if (n === 5) return [1, 3, 1];
  const rows: number[] = [];
  let left = Math.min(n, 9);
  while (left > 0) {
    rows.push(Math.min(3, left));
    left -= 3;
  }
  return rows;
}

export const AvatarGrid: React.FC<{ members: GridMember[]; title?: string }> = ({
  members, title,
}) => {
  const shown = members.slice(0, 9);
  const n = Math.max(shown.length, 1);
  const rows = rowsFor(n);
  const cells = rows.reduce((a, b) => a + b, 0);   // 1 人时 = 2（多出来那格是空白）
  const size = cells <= 2 ? 'lg' : cells <= 4 ? 'md' : 'sm';

  let i = 0;
  return (
    <div className={`cav-grid cav-${size}`} title={title} aria-hidden="true">
      {rows.map((count, r) => (
        <div className="cav-row" key={r}>
          {Array.from({ length: count }, () => {
            const m = shown[i];
            i += 1;
            if (!m) return <AvatarBlank key={`blank-${i}`} />;
            return (
              <Avatar
                key={m.id}
                glyph={m.glyph}
                pal={m.pal}
                src={m.avatarUrl}
                size={28}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
};
