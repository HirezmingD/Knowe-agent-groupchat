/**
 * RosterPanel.tsx — 成员花名册（component-tree §E · Roster）
 *
 * DOM：.roster-wrap(.open) > (.roster > (.roster-head > .roster-title + button.icon-btn)
 *                                     + (.r-scroll > [.r-group] + (.r-row(.archived) > (.r-av(.busy) > .avatar(.dimmed))
 *                                                             + (.r-body > .r-name + .r-role)
 *                                                             + .status-dot(.busy/.idle/.archived) + .r-state)×N))
 *
 * 数据：selectActiveMembers（唯一入口）。
 * 成员状态由后端 instruction_injected / agent_idle 成对驱动；只有明确的 agent_idle
 * 才清除工作态，report_submitted、等待、失败、超时、中断和未知状态都不作空闲推断。
 *
 * [v0.9c] 已归档的成员（status='removed'）：
 *   **留在名单上，但灰掉，排在最下面，状态写「已归档」。**
 *
 *   为什么不像左栏宫格那样直接滤掉：这两处回答的是**不同的问题**。
 *     · 左栏宫格 → 「现在这个群里有谁」   → 走了的人不该占一格
 *     · 花名册面板 → 「这个项目有过谁」   → 走了的人还在名单上，只是灰着
 *   他交过报告、写过文件、在时间线上留着话——把他从名单里抹掉，
 *   用户再看到那些气泡时，会对着一个没有出处的名字发愣。
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useKnoweStore } from '../store/store';
import { roleLabel, memberNameLabel } from '../shared/roleLabel';
import { selectIsPlatform, selectActiveProjectId } from '../store/selectors';
import { selectActiveMembers } from '../store/selectors';
import { Avatar } from './Avatar';
import { IconChevR } from './icons';
import { openAgentMenu } from './ContextMenu';
import { useTranslation } from 'react-i18next';

const isExplicitlyIdle = (state: string): boolean => state === 'idle';

/**
 * [v0.29 问题二] 「停止」的二次确认，多久没动就自己收回去。
 *
 * 为什么要有这个超时：确认态是**危险态**——一个「确定停止」的红键停在那儿，
 * 用户滚动列表、误触，那个人的活就没了。他要是走开了、或者本来就是点错的，
 * 这个键不该一直等着他。5 秒是「看清楚 + 决定」够用、「忘了它还在」不够长的那个刻度。
 */
const CONFIRM_TIMEOUT_MS = 5_000;

export interface RosterPanelProps {
  open: boolean;
  onClose: () => void;
}

export const RosterPanel: React.FC<RosterPanelProps> = ({ open, onClose }) => {
  const { t } = useTranslation();
  // [v0.4] 知知没有团队——平台会话里不该有花名册这块东西
  const isPlatform = useKnoweStore(selectIsPlatform);
  const members = useKnoweStore(selectActiveMembers);
  const panelRef = useRef<HTMLElement>(null);

  // [v0.37] 双击成员行 → 进入与他的私聊。花名册只在群聊里出现（App.tsx: !privateChat），
  //   所以此刻的 activeId 就是这个群。
  const activeId = useKnoweStore(selectActiveProjectId);
  const enterDm = useKnoweStore((s) => s.enterDm);

  /*
   * ── [v0.29 问题二] 「停止」 ────────────────────────────────────
   *
   *   在这一版之前，用户想打断一个正在干活的成员，只有两条路：干等，
   *   或者把整个项目重启。**两条都不是给人用的。**
   *
   *   二态，不多不少：
   *     null      → 【工作中】旁边一个安静的「停止」
   *     该成员 id → 那个键变成「确定停止？」，5 秒不点自己变回去
   *
   *   为什么用行内二次确认，而不是 window.confirm 或者一个模态框：
   *     · 用户要确认的是「**是不是这个人**」——那张脸、那个名字就在旁边半厘米处。
   *       弹一个盖住整个花名册的框问「确定要停止林知远吗？」，反而把他要核对的
   *       东西挡住了，只剩一个名字要他凭记忆比对。
   *     · 一次只允许一个成员处于确认态（存 id 不存 boolean）：点了 A 的确认、
   *       又去点 B 的停止 → A 自动退回安静态。手不会同时停两个人。
   */
  const stopWorker = useKnoweStore((s) => s.stopWorker);
  const [confirming, setConfirming] = useState<string | null>(null);

  // 停在确认态上没人管 → 自己收回去（见 CONFIRM_TIMEOUT_MS 的注释）。
  useEffect(() => {
    if (!confirming) return;
    const t = setTimeout(() => setConfirming(null), CONFIRM_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [confirming]);

  // 面板关上 / 那个人自己干完了 → 确认态必须散掉。
  // 少了后半句会出一个很坏的 bug：用户点开确认，那一秒他交差了，行变成【空闲】，
  // 而这个 state 还记着他 —— 他下一次开工，那个红键会**直接带着确认态**出现。
  useEffect(() => {
    if (!open) { setConfirming(null); return; }
    if (confirming && !members.some((m) => m.id === confirming && m.status !== 'removed' && !isExplicitlyIdle(m.state))) {
      setConfirming(null);
    }
  }, [open, members, confirming]);

  const onStopClick = (id: string): void => {
    if (confirming !== id) {
      setConfirming(id);        // ★ 第一下只是**问**。验收标准 8：误触到此为止。
      return;
    }
    setConfirming(null);
    stopWorker(id);             // 第二下才真的动手
  };

  // [v0.15] selectActiveMembers 已完成动态排序：忙碌成员按最近开工时间在前，
  // 其余仍保持“项目经理第一 + 加入顺序”；全部 idle 后自动恢复。
  const { active, archived } = useMemo(() => ({
    active: members.filter((m) => m.status !== 'removed'),
    archived: members.filter((m) => m.status === 'removed'),
  }), [members]);

  /*
   * [v0.10b Bug5] ★ 点击面板外部 / 按 ESC → 收起花名册。
   *
   *   只在**面板打开时**才挂 document 监听：关着的时候不占事件，也顺带避开
   *   「开面板那一下点击又立刻被判成外部」的竞态——effect 是渲染完才跑的，
   *   开面板那次交互早结束了。
   *
   *   按下的位置在面板内部（panelRef）或在触发按钮上（[data-roster-toggle]，
   *   即 ChatStream 右上角那个「查看成员」头像堆）→ **不关**：
   *     · 面板内部：用户正在用它；
   *     · 触发按钮：关不关交给按钮自己 toggle —— 否则「点按钮→外部逻辑先关→
   *       按钮再 toggle 开」会来回抖（死循环）。
   *   其它任何地方按下 → onClose()。
   */
  useEffect(() => {
    if (!open) return;

    const onPointerDown = (e: MouseEvent): void => {
      const t = e.target as Node | null;
      if (!t) return;
      if (panelRef.current && panelRef.current.contains(t)) return;
      if (t instanceof Element && t.closest('[data-roster-toggle]')) return;
      onClose();
    };
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose();
    };

    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open, onClose]);

  if (isPlatform) return null;

  return (
    <div className={'roster-wrap' + (open ? ' open' : '')}>
      <aside className="roster" aria-label={t('roster.panel.02')} aria-hidden={!open} ref={panelRef}>
        <div className="roster-head">
          {/* 头上的数字是**在队的人数** —— 归档的不算「成员」，他不再接活了 */}
          <div className="roster-title">{t('roster.panel.memberCount', { n: active.length })}</div>
          <button className="icon-btn" aria-label={t('roster.panel.03')} onClick={onClose}>
            <IconChevR />
          </button>
        </div>

        <div className="r-scroll">
          {active.map((m) => {
            // 只有后端明确发来 idle 才算空闲；等待、错误、超时、中断等状态都按工作中呈现。
            const working = !isExplicitlyIdle(m.state);
            const openDm = (): void => { if (activeId && !working) enterDm(activeId, m.id); };
            return (
              <div
                className={'r-row' + (working ? ' working' : '')}
                key={m.id}
                style={working ? { background: 'rgba(79,124,255,.07)' } : undefined}
              >
              {/* [v0.37] 双击「头像 + 名字」这块身份区 → 进私聊。刻意不含右侧「停止」按钮，
                  免得双击停止键被当成两次点击（确认→执行）。dm-pressable 给按压回弹。 */}
              <div
                className={'r-av dm-pressable' + (working ? ' busy' : '')}
                onDoubleClick={openDm}
                onContextMenu={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  if (activeId && !working) {
                    openAgentMenu(activeId, m.id, e.clientX, e.clientY);
                  }
                }}
                title={working ? t('roster.panel.notIdle', { name: m.display.name }) : t('roster.panel.doubleClickDm', { name: m.display.name })}
                style={working ? {
                  borderRadius: '50%',
                  boxShadow: '0 0 0 2px var(--accent, #4f7cff), 0 0 14px rgba(79,124,255,.45)',
                } : undefined}
              >
                <Avatar glyph={m.display.glyph} pal={m.display.pal} size={40}
                  title={working ? t('roster.panel.working', { name: m.display.name }) : m.display.name}
                  src={m.display.avatarUrl} />   {/* [v0.4] 花名册的头像终于用上了 */}
              </div>
              <div
                className="r-body dm-pressable"
                onDoubleClick={openDm}
                title={working
                  ? t('roster.panel.notIdle', { name: m.display.name })
                  : t('roster.panel.doubleClickDm', { name: m.display.name })}
              >
                <div className="r-name">{memberNameLabel(m.id, m.display.name)}</div>
                <div className="r-role">{roleLabel(m.display.role)}</div>
              </div>
              {/*
                * [v0.30 Bug1] ★ 右侧状态区改成**一根竖列**（.r-side）：
                *
                *     ● 工作中          ← 状态行（圆点 + 文字），永远这一行
                *       [停止]          ← 按钮长在状态行**下面**，不再横向挤
                *
                *   v0.29 把「停止」横排在【工作中】右边——行宽只有 260px 出头，
                *   头像 40 + 间距就去了一半；按钮一出现（确认态还会变宽成
                *   「确定停止？」），dot、状态字被推着往左撞进名字里。
                *   名字又没有截断样式，两段文字就叠在一起（用户报的「重叠」）。
                *
                *   竖排之后：右列宽度 = max(状态行, 按钮) ≈ 60px，**恒定**；
                *   按钮出现、变宽、消失都发生在自己那一行里，名字区一个像素
                *   都不用让。配套的 .r-name/.r-role 截断兜底见 CSS——
                *   再长的名字也只会变成省略号，不会叠到别人身上。
                */}
              <div className="r-side">
                <div className="r-state-line">
                  <div className={'status-dot ' + (working ? 'busy' : 'idle')} />
                  <div
                    className="r-state"
                    style={working ? { color: 'var(--accent, #4f7cff)', fontWeight: 600 } : undefined}
                  >
                    {working ? t('contacts.view.11') : t('contacts.view.24')}
                  </div>
                </div>
                {/*
                  * [v0.29 问题二] 「停止」只长在**正在工作**的人身上。
                  *
                  *   空闲的人没有活可以停，给他一个键只会让用户去猜它是干什么的
                  *   （「停止」他？把他开除？）。按钮该在的地方，是那个让用户
                  *   干等的东西**正在发生**的地方 —— 也就是【工作中】这三个字旁边。
                  *   （样式上它一直在，只是很淡：它不该喊，但它得在。见 .r-stop）
                  */}
                {working && (
                  <button
                    type="button"
                    className={'r-stop' + (confirming === m.id ? ' confirming' : '')}
                    onClick={() => onStopClick(m.id)}
                    title={confirming === m.id
                      ? t('roster.panel.confirmStopHint', { name: m.display.name })
                      : t('roster.panel.stopTask', { name: m.display.name })}
                    aria-label={confirming === m.id
                      ? t('roster.panel.confirmStopTask', { name: m.display.name })
                      : t('roster.panel.stopTask', { name: m.display.name })}
                  >
                    {confirming === m.id ? t('roster.panel.04') : t('roster.panel.01')}
                  </button>
                )}
              </div>
              </div>
            );
          })}

          {archived.length > 0 && (
            <>
              <div className="r-group">{t('roster.panel.archivedCount', { n: archived.length })}</div>
              {archived.map((m) => (
                <div className="r-row archived" key={m.id}>
                  <div className="r-av">
                    <Avatar
                      glyph={m.display.glyph} pal={m.display.pal} size={40}
                      title={t('roster.panel.archivedName', { name: m.display.name })}
                      src={m.display.avatarUrl}
                      dimmed                          /* [v0.9b] 灰掉 */
                    />
                  </div>
                  <div className="r-body">
                    <div className="r-name">{memberNameLabel(m.id, m.display.name)}</div>
                    <div className="r-role">{roleLabel(m.display.role)}</div>
                  </div>
                  {/* [v0.30 Bug1] 和在册行同一副骨架（.r-side），两组行的状态列才对得齐 */}
                  <div className="r-side">
                    <div className="r-state-line">
                      <div className="status-dot archived" />
                      {/* 「已归档」而不是「空闲」—— 他不是闲着，他是走了 */}
                      <div className="r-state">{t('contacts.view.12')}</div>
                    </div>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </aside>
    </div>
  );
};

export default RosterPanel;
