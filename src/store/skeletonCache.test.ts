// @vitest-environment jsdom
import { describe, expect, it, beforeEach } from 'vitest';
import {
  loadSkeleton,
  saveSkeleton,
  clearSkeleton,
  loadAllSkeletons,
  skeletonKey,
} from './skeletonCache';

describe('skeletonCache · 会话骨架持久化', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('save → load 往返一致（heights/scrollTop/readSeq）', () => {
    saveSkeleton('proj-a', { heights: { ik_1: 42, ik_2: 88 }, scrollTop: 1234, readSeq: 99 });
    const sk = loadSkeleton('proj-a');
    expect(sk).not.toBeNull();
    expect(sk!.projectId).toBe('proj-a');
    expect(sk!.heights).toEqual({ ik_1: 42, ik_2: 88 });
    expect(sk!.scrollTop).toBe(1234);
    expect(sk!.readSeq).toBe(99);
    expect(sk!.schemaVersion).toBe(1);
  });

  it('部分更新保留其余字段（patch 语义）', () => {
    saveSkeleton('proj-a', { heights: { ik_1: 42 }, scrollTop: 100, readSeq: 10 });
    saveSkeleton('proj-a', { scrollTop: 200 });
    const sk = loadSkeleton('proj-a');
    expect(sk!.scrollTop).toBe(200);
    expect(sk!.heights).toEqual({ ik_1: 42 });   // 未覆盖字段保留
    expect(sk!.readSeq).toBe(10);
  });

  it('schemaVersion 不匹配 → 丢弃返回 null（旧格式安全降级）', () => {
    localStorage.setItem(skeletonKey('proj-a'), JSON.stringify({
      schemaVersion: 0, projectId: 'proj-a', heights: {}, scrollTop: 1, readSeq: 1,
    }));
    expect(loadSkeleton('proj-a')).toBeNull();
  });

  it('projectId 不匹配 → 丢弃（防串会话）', () => {
    localStorage.setItem(skeletonKey('proj-a'), JSON.stringify({
      schemaVersion: 1, projectId: 'proj-b', heights: {}, scrollTop: 1, readSeq: 1,
    }));
    expect(loadSkeleton('proj-a')).toBeNull();
  });

  it('JSON 损坏 → null 不抛异常', () => {
    localStorage.setItem(skeletonKey('proj-a'), '{broken json');
    expect(loadSkeleton('proj-a')).toBeNull();
  });

  it('clearSkeleton 删除对应 key', () => {
    saveSkeleton('proj-a', { scrollTop: 5 });
    clearSkeleton('proj-a');
    expect(loadSkeleton('proj-a')).toBeNull();
  });

  it('loadAllSkeletons 只收集合法骨架，跳过损坏/版本不符', () => {
    saveSkeleton('proj-a', { heights: { ik_1: 42 }, scrollTop: 100, readSeq: 7 });
    saveSkeleton('proj-b', { scrollTop: 200 });
    localStorage.setItem(skeletonKey('proj-c'), 'garbage');
    localStorage.setItem('unrelated-key', 'x');
    const all = loadAllSkeletons();
    expect(all.size).toBe(2);
    expect(all.has('proj-a')).toBe(true);
    expect(all.has('proj-b')).toBe(true);
    expect(all.has('proj-c')).toBe(false);
  });

  it('readSeq 负数归一为 0', () => {
    saveSkeleton('proj-a', { readSeq: -5 });
    expect(loadSkeleton('proj-a')!.readSeq).toBe(0);
  });
});
