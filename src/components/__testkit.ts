/**
 * __testkit.ts — 组件测试的共用夹具
 *
 * 原则：测试只通过 store 的真实形状喂数据，不 mock selectors、不 mock 组件。
 *      这样测试测的是「真接线」，不是「我以为的接线」。
 */

import { useKnoweStore } from '../store/store';
import i18n from '../i18n';
import type { Item, Member, Conv, ConnStatus } from '../store/state';
import type { SocketAPI } from '../transport/socket';

export function resetStore(): void {
  useKnoweStore.setState({
    convs: {},
    activeProjectId: null,
    activeView: 'chats',
    cmdKOpen: false,
    conn: 'closed' as ConnStatus,
    notices: [],
    projectOrder: [],
    _socket: null,
  });
}

export function member(id: string, name: string, role = i18n.t('common.05'), state: 'idle' | 'busy' = 'idle'): Member {
  return {
    id,
    state,
    display: { name, role, roleEn: role, glyph: name.charAt(0), pal: 'av-a', kind: 'agent' },
  };
}

export function seedConv(
  projectId: string,
  opts: { name?: string; items?: Item[]; members?: Member[]; banner?: string | null } = {},
): void {
  const conv: Conv = {
    projectId,
    projectName: opts.name ?? projectId,
    items: opts.items ?? [],
    members: opts.members ?? [],
    banner: opts.banner ?? null,
    draft: '',
    unread: 0,
  };
  useKnoweStore.setState((s) => ({
    convs: { ...s.convs, [projectId]: conv },
    projectOrder: s.projectOrder.includes(projectId) ? s.projectOrder : [...s.projectOrder, projectId],
  }));
}

export function activate(projectId: string): void {
  useKnoweStore.setState({ activeProjectId: projectId });
}

/** 装一个假 socket，只记录出站调用（组件永远不该直接碰 transport） */
export interface SocketSpy {
  sent: { content: string; projectId: string; forwarded?: unknown }[];
  approved: { id: string; projectId: string }[];
  rejected: { id: string; projectId: string }[];
  created: { id: string; name: string }[];
}

export function installSocketSpy(): SocketSpy {
  const spy: SocketSpy = { sent: [], approved: [], rejected: [], created: [] };
  const fake: SocketAPI = {
    connect: () => {},
    disconnect: () => {},
    sendMessage: (content: string, projectId: string, cmid?: string, _attachments?: unknown[], forwarded?: unknown) => {
      spy.sent.push({ content, projectId, ...(forwarded ? { forwarded } : {}) });
      return cmid ?? 'cm_test_' + spy.sent.length;
    },
    approve: (id: string, projectId: string) => { spy.approved.push({ id, projectId }); },
    reject: (id: string, projectId: string) => { spy.rejected.push({ id, projectId }); },
    feedbackInstruction: () => {},
    stopWorker: () => {},
    createProject: (id: string, name: string) => { spy.created.push({ id, name }); },
    setProjectDirectory: () => {},
    cancelProjectDirectory: () => {},
    addAgents: () => {},   // [v1.0.23.4] 中途添加：测试桩记录可选项，静默即可
    requestSnapshot: () => {},
    sendCommand: () => {},
    noteIncremental: () => {},
    watermarks: {},
    status: 'live' as ConnStatus,
    _debugReadyState: () => 1,
    _getHandshakeBuffer: () => [],
    _getPendingEchoes: () => ({}),
  };
  useKnoweStore.setState({ _socket: fake });
  return spy;
}
