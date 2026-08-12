/**
 * agentParams.ts — Agent 运行参数（模型 / 温度 / 最大迭代）的独立 store。（[v0.39.1 #4]）
 *
 * ── 为什么从 ContactsView 的 useState 里搬出来 ──
 *
 *   v0.39 把参数存在联系人页组件的本地 state 里：换个视图再回来还在（组件没卸载），
 *   但它**结构上**就接不了后端——数据被焊死在一个 UI 组件的闭包里，socket 层够不着，
 *   别的界面（比如设置页、花名册右键菜单）也读不到。
 *
 *   现在它是一个和 records.ts 同款的轻量 zustand store：
 *     · 键 = `${projectId}::${agentId}`（per-agent、per-project，与后端将来的粒度一致）
 *     · 组件只调 patch()/reset()，读值走订阅——UI 与数据层解耦
 *
 * ── 接真实后端时怎么改（组件零改动）──
 *
 *   后端就绪后（例如新增 `set_agent_params` 命令 + `agent_params_updated` 事件）：
 *     1. 仿照 store.ts 的 setSocket，把 socket 注入本 store（bindSocket(socket)）；
 *     2. patch() 改成「乐观写入 byKey + socket 发 set_agent_params」；
 *     3. App 的 onEvent 里把 agent_params_updated 转给本 store 的 applyServer()——
 *        以服务端回执为准覆盖本地值（回执驱动，和审批卡 rev 的思路一致）；
 *     4. reset() 同理发「清除覆写」命令。
 *   组件侧的 useAgentParamsStore((s) => s.byKey[key]) / patch / reset 一行都不用动。
 *
 * ⚠ 在此之前，这些值**只活在前端**：不落盘、不过 socket、刷新即回默认。
 *   资料页的 pf-scope 文案已向用户说明这一点。
 */

import { create } from 'zustand';
import { isCoordinator } from './avatar';

/** 可选模型清单（与设计参考 v1.3 的下拉一致；后端接入后应改为由服务端下发）。 */
export const PARAM_MODELS = ['DeepSeek', 'GLM', 'Kimi', 'Qwen'] as const;

export interface AgentRunParams {
  model: string;
  /** 采样温度 0~1。角色默认：项目经理 0.5（更稳）/ Worker 0.7（更活）。 */
  temp: number;
  /** Worker 干活的轮次上限 1~50。 */
  iters: number;
}

/** 角色默认值——「恢复默认」= 删掉覆写记录，读值自然落回这里。 */
export function defaultRunParams(agentId: string): AgentRunParams {
  return { model: 'DeepSeek', temp: isCoordinator(agentId) ? 0.5 : 0.7, iters: 15 };
}

/** 统一键（per-agent、per-project）。将来后端命令/事件也按这两个字段定位。 */
export function paramKey(projectId: string, agentId: string): string {
  return `${projectId}::${agentId}`;
}

interface AgentParamsState {
  /** 只存**被改过**的条目；没改过的 agent 不占键，读值时落回 defaultRunParams。 */
  byKey: Record<string, AgentRunParams>;

  /** 非响应式读取（事件处理等场景用；组件请直接订阅 s.byKey[key]）。 */
  get: (projectId: string, agentId: string) => AgentRunParams;
  /** 覆写部分字段。（接后端后：这里加乐观更新 + socket 命令。） */
  patch: (projectId: string, agentId: string, patch: Partial<AgentRunParams>) => void;
  /** 恢复角色默认 = 删除覆写。（接后端后：这里发「清除覆写」命令。） */
  reset: (projectId: string, agentId: string) => void;
}

export const useAgentParamsStore = create<AgentParamsState>((set, get) => ({
  byKey: {},

  get(projectId, agentId) {
    return get().byKey[paramKey(projectId, agentId)] ?? defaultRunParams(agentId);
  },

  patch(projectId, agentId, patch) {
    set((s) => {
      const key = paramKey(projectId, agentId);
      const base = s.byKey[key] ?? defaultRunParams(agentId);
      /*
       * 只换动到的那一个键：其余条目保持原引用——
       * 订阅 s.byKey[key] 的资料页面板，只在**自己那条**变化时重渲。
       */
      return { byKey: { ...s.byKey, [key]: { ...base, ...patch } } };
    });
  },

  reset(projectId, agentId) {
    set((s) => {
      const key = paramKey(projectId, agentId);
      if (!(key in s.byKey)) return s;   // 本来就是默认值，不产生一次空更新
      const next = { ...s.byKey };
      delete next[key];
      return { byKey: next };
    });
  },
}));
