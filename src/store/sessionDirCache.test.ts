// @vitest-environment jsdom
/**
 * [v1.0.39] 会话目录缓存单测：读写 roundtrip / 损坏降级 / schema 守卫 / 防抖 / 清空。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  loadSessionDir,
  saveSessionDir,
  scheduleSessionDirSave,
  flushSessionDir,
  clearSessionDir,
  emptySessionDir,
  type SessionDirCache,
} from './sessionDirCache';

describe('sessionDirCache', () => {
  const KEY = 'knowe.sessionDir.v1';

  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function sampleCache(): SessionDirCache {
    return {
      schemaVersion: 1,
      savedAt: 123,
      projects: [
        {
          projectId: 'p1', projectName: '官网改版', projectDir: 'C:/work/p1',
          pinned: true, folded: false, muted: false, pinned_at: 999,
          members: [{ id: 'coordinator', role: '项目经理', name: '项目经理', avatar: undefined }],
        },
        {
          projectId: 'p2', projectName: 'GIS 分析', projectDir: undefined,
          pinned: false, folded: true, muted: true, pinned_at: 0,
          members: [],
        },
      ],
      dm: [{ sessionId: 'dm:p1:fe_1', projectId: 'p1', agentId: 'fe_1', displayName: '界面设计助手' }],
      activeView: 'chats',
    };
  }

  it('保存→读取 roundtrip 完整', () => {
    saveSessionDir(sampleCache());
    const loaded = loadSessionDir();
    expect(loaded).not.toBeNull();
    expect(loaded!.projects).toHaveLength(2);
    expect(loaded!.projects[0]!.projectName).toBe('官网改版');
    expect(loaded!.projects[0]!.pinned).toBe(true);
    expect(loaded!.projects[0]!.members[0]!.role).toBe('项目经理');
    expect(loaded!.dm[0]!.sessionId).toBe('dm:p1:fe_1');
    expect(loaded!.activeView).toBe('chats');
    // savedAt 被保存时刷新（> 传入值）
    expect(loaded!.savedAt).toBeGreaterThan(0);
  });

  it('损坏 JSON → null（降级走现状流程）', () => {
    localStorage.setItem(KEY, '{not valid json');
    expect(loadSessionDir()).toBeNull();
  });

  it('schemaVersion 不符 → null（升级即弃）', () => {
    localStorage.setItem(KEY, JSON.stringify({ ...sampleCache(), schemaVersion: 99 }));
    expect(loadSessionDir()).toBeNull();
  });

  it('缺字段 → 容错为默认（不崩）', () => {
    localStorage.setItem(KEY, JSON.stringify({ schemaVersion: 1, savedAt: 1 }));
    const loaded = loadSessionDir();
    expect(loaded).not.toBeNull();
    expect(loaded!.projects).toEqual([]);
    expect(loaded!.dm).toEqual([]);
  });

  it('无缓存 → null', () => {
    expect(loadSessionDir()).toBeNull();
  });

  it('防抖：短时间多次 schedule 只写一次（800ms 合并）', () => {
    const build = vi.fn(() => sampleCache());
    scheduleSessionDirSave(build);
    scheduleSessionDirSave(build);
    scheduleSessionDirSave(build);
    expect(localStorage.getItem(KEY)).toBeNull();   // 未到 800ms 不落盘
    vi.advanceTimersByTime(900);
    expect(build).toHaveBeenCalledTimes(1);          // 只取最后一次构建
    expect(localStorage.getItem(KEY)).not.toBeNull();
  });

  it('flush 立即写且清掉待写定时器', () => {
    const build = vi.fn(() => sampleCache());
    scheduleSessionDirSave(build);
    flushSessionDir(build);
    expect(localStorage.getItem(KEY)).not.toBeNull();
    vi.advanceTimersByTime(900);                     // 定时器已清，不再二次写
    expect(build).toHaveBeenCalledTimes(1);
  });

  it('clear 清空', () => {
    saveSessionDir(sampleCache());
    clearSessionDir();
    expect(localStorage.getItem(KEY)).toBeNull();
    expect(loadSessionDir()).toBeNull();
  });

  it('emptySessionDir 默认空结构', () => {
    const e = emptySessionDir();
    expect(e.schemaVersion).toBe(1);
    expect(e.projects).toEqual([]);
    expect(e.dm).toEqual([]);
  });
});