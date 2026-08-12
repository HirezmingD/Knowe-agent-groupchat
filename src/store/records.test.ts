import { describe, expect, it } from 'vitest';
import {
  filterBySearch,
  groupFileHistory,
  type HistoryItem,
} from './records';

function history(
  seq: number,
  overrides: Partial<HistoryItem> = {},
): HistoryItem {
  return {
    seq,
    type: 'message',
    agent_id: 'worker-a',
    content: `message ${seq}`,
    ts: new Date(2026, 6, 30, 12, seq).getTime(),
    has_files: true,
    has_images: false,
    has_videos: false,
    has_links: false,
    files: [{ path: `output/file-${seq}.txt`, name: `file-${seq}.txt`, ext: 'txt' }],
    ...overrides,
  };
}

describe('groupFileHistory · 文件记录分组', () => {
  it('按发送人分组，保留时间线首次出现顺序并统计消息/文件数', () => {
    const items = [
      history(1, {
        type: 'user_echo',
        agent_id: '',
        files: [
          { path: 'input/a.txt', name: 'a.txt', ext: 'txt' },
          { path: 'input/b.txt', name: 'b.txt', ext: 'txt' },
        ],
      }),
      history(2, { agent_id: 'worker-a' }),
      history(3, {
        type: 'user_echo', agent_id: '',
        files: [{ path: 'input/c.txt', name: 'c.txt', ext: 'txt' }],
      }),
      history(4, { agent_id: 'worker-a', files: [], has_files: false }),
      history(5, { agent_id: 'worker-b' }),
    ];

    const groups = groupFileHistory(items, 'sender');

    expect(groups.map((group) => group.key)).toEqual([
      'sender:user',
      'sender:agent:worker-a',
      'sender:agent:worker-b',
    ]);
    expect(groups[0]).toMatchObject({ senderId: '', messageCount: 2, fileCount: 3 });
    expect(groups[0]?.items.map((item) => item.seq)).toEqual([1, 3]);
    expect(groups[1]).toMatchObject({ senderId: 'worker-a', messageCount: 1, fileCount: 1 });
  });

  it('按本地日期分组，缺失时间单独归入未知日期', () => {
    const firstDay = new Date(2026, 6, 30, 9, 0).getTime();
    const secondDay = new Date(2026, 6, 31, 9, 0).getTime();
    const groups = groupFileHistory([
      history(1, { ts: firstDay }),
      history(2, { ts: firstDay + 60_000 }),
      history(3, { ts: secondDay }),
      history(4, { ts: null }),
    ], 'date');

    expect(groups.map((group) => group.key)).toEqual([
      'date:2026-07-30',
      'date:2026-07-31',
      'date:__unknown__',
    ]);
    expect(groups[0]).toMatchObject({ dateKey: '2026-07-30', messageCount: 2, fileCount: 2 });
    expect(groups[2]?.dateKey).toBeNull();
  });
});

describe('filterBySearch · 文件元数据可搜索', () => {
  const items = [
    history(1, {
      agent_id: 'designer',
      content: '设计稿已完成',
      files: [{
        path: 'deliverables/mobile/home-screen.png',
        name: 'home-screen.png',
        ext: 'png',
        kind: 'image',
        media_type: 'image/png',
      }],
    }),
    history(2, { agent_id: 'backend', content: '接口完成' }),
  ];

  it.each(['设计稿', 'designer', 'home-screen', 'deliverables/mobile', 'image/png'])(
    '搜索 %s 能命中文件消息',
    (query) => {
      expect(filterBySearch(items, query).map((item) => item.seq)).toEqual([1]);
    },
  );

  it('空查询保持原数组引用，未知查询返回空数组', () => {
    expect(filterBySearch(items, '   ')).toBe(items);
    expect(filterBySearch(items, 'no-match')).toEqual([]);
  });
});
