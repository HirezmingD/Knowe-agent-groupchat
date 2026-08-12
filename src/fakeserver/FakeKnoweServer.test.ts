/**
 * FakeKnoweServer.test.ts — 夹具自测。
 *
 * 夹具本身必须先可信，才能用它去测别人。
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import WebSocket from 'ws';
import { FakeKnoweServer, NO_SEQ_TYPES, sleep } from './FakeKnoweServer';

let server: FakeKnoweServer;

beforeEach(async () => {
  server = new FakeKnoweServer({ ringMax: 5 });
  await server.start();
});

afterEach(async () => {
  await server.stop();
});

/** 连一个裸客户端，收集收到的所有事件 */
async function client(): Promise<{ ws: WebSocket; events: Record<string, unknown>[] }> {
  const ws = new WebSocket(server.url);
  const events: Record<string, unknown>[] = [];
  ws.on('message', (raw) => events.push(JSON.parse(String(raw))));
  await new Promise<void>((r) => ws.once('open', () => r()));
  return { ws, events };
}

function send(ws: WebSocket, msg: Record<string, unknown>): void {
  ws.send(JSON.stringify(msg));
}

async function waitFor(
  events: Record<string, unknown>[],
  type: string,
  timeoutMs = 1000,
): Promise<Record<string, unknown>> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const hit = events.find((e) => e.type === type);
    if (hit) return hit;
    await sleep(10);
  }
  throw new Error(`等不到事件 ${type}；收到的是：${events.map((e) => e.type).join(', ')}`);
}

// ═══════════════════════════════════════════════════════════════

describe('FakeKnoweServer · seq 与 ring', () => {
  it('seq 按项目独立递增（A 的事件不影响 B）', () => {
    const a1 = server.emit('A', { type: 'message', agent_id: 'x', content: '1' });
    const b1 = server.emit('B', { type: 'message', agent_id: 'x', content: '1' });
    const a2 = server.emit('A', { type: 'message', agent_id: 'x', content: '2' });

    expect([a1.seq, a2.seq]).toEqual([1, 2]);
    expect(b1.seq).toBe(1);
  });

  it('引擎级事件自动带 ts / project_name / seq', () => {
    const ev = server.emit('A', { type: 'message', agent_id: 'x', content: 'hi' });
    expect(ev.ts).toBeTruthy();
    expect(ev.project_name).toBe('A');
    expect(ev.seq).toBe(1);
  });

  it('user_echo 不带 project_name（契约里没这个字段）', () => {
    const ev = server.emit('A', { type: 'user_echo', content: 'hi', client_msg_id: 'cm_1' });
    expect(ev.project_name).toBeUndefined();
  });

  it('无 seq 白名单事件不能走 emit（会被盖上 seq，污染前端水位）', () => {
    for (const t of NO_SEQ_TYPES) {
      expect(() => server.emit('A', { type: t })).toThrow();
    }
  });

  it('快照本身消耗一个 seq 并进 ring', () => {
    server.emit('A', { type: 'message', agent_id: 'x', content: 'hi' });
    const snap = server.snapshot('A');

    expect(snap.last_seq).toBe(1);
    expect(snap.seq).toBe(2);
    expect(server.lastSeq('A')).toBe(2);
    expect(server.ringOf('A').at(-1)?.type).toBe('state_snapshot');
  });

  it('快照的 conversation 只放结构事件（瞬时事件不进时间线）', () => {
    server.say('A', 'coordinator', '你好', 2);   // thinking + deltas + message
    const snap = server.snapshot('A');
    const types = (snap.conversation as Record<string, unknown>[]).map((e) => e.type);

    expect(types).toEqual(['message']);
  });

  it('ring 超容量后淘汰最老的', () => {
    for (let i = 0; i < 8; i++) {
      server.emit('A', { type: 'message', agent_id: 'x', content: String(i) });
    }
    const ring = server.ringOf('A');
    expect(ring.length).toBe(5);                 // ringMax = 5
    expect(ring[0]!.seq).toBe(4);                // 1/2/3 已被淘汰
  });
});

describe('FakeKnoweServer · 握手与回放', () => {
  it('首帧 replay_request → 补发 project_created + 回放历史 + replay_complete', async () => {
    server.emit('demo', { type: 'user_echo', content: '旧消息', client_msg_id: 'cm_0' });

    const { ws, events } = await client();
    send(ws, { type: 'replay_request', project_id: 'demo', since_seq: 0 });

    const done = await waitFor(events, 'replay_complete');
    expect(done.project_id).toBe('demo');
    expect(done.last_seq).toBe(1);
    expect(events.some((e) => e.type === 'project_created')).toBe(true);
    expect(events.some((e) => e.type === 'user_echo')).toBe(true);
    ws.close();
  });

  it('增量回放：since_seq 之后的才发', async () => {
    server.emit('demo', { type: 'message', agent_id: 'x', content: '1' });
    server.emit('demo', { type: 'message', agent_id: 'x', content: '2' });

    const { ws, events } = await client();
    send(ws, { type: 'replay_request', project_id: 'demo', since_seq: 1 });

    await waitFor(events, 'replay_complete');
    const msgs = events.filter((e) => e.type === 'message');
    expect(msgs).toHaveLength(1);
    expect(msgs[0]!.seq).toBe(2);
    ws.close();
  });

  it('★ ring 已淘汰 → 发 resync_required，绝不给残缺历史（对齐后端 B-5）', async () => {
    for (let i = 0; i < 8; i++) {                // ringMax=5 → 1/2/3 被淘汰
      server.emit('demo', { type: 'message', agent_id: 'x', content: String(i) });
    }

    const { ws, events } = await client();
    send(ws, { type: 'replay_request', project_id: 'demo', since_seq: 0 });

    await waitFor(events, 'replay_complete');
    expect(events.some((e) => e.type === 'resync_required')).toBe(true);
    expect(events.filter((e) => e.type === 'message')).toHaveLength(0);  // 一条残缺历史都没有
    ws.close();
  });

  it('5 秒不发首帧 → replay_complete{last_seq:0}（无 project_id 的超时分支）', async () => {
    const quick = new FakeKnoweServer({ handshakeTimeoutMs: 50 });
    await quick.start();
    const ws = new WebSocket(quick.url);
    const events: Record<string, unknown>[] = [];
    ws.on('message', (raw) => events.push(JSON.parse(String(raw))));
    await new Promise<void>((r) => ws.once('open', () => r()));

    const done = await waitFor(events, 'replay_complete');
    expect(done.last_seq).toBe(0);
    expect(done.project_id).toBeUndefined();

    ws.close();
    await quick.stop();
  });

  it('[v1.0.24.4] 配置 replayActivity → replay_complete 携带账本字段', async () => {
    const activity = [
      { agent_id: 'pm_1', scope_id: '', channel_id: 'demo', started_at: 1720000000000 },
    ];
    const srv = new FakeKnoweServer({ ringMax: 5, replayActivity: activity });
    await srv.start();
    const ws = new WebSocket(srv.url);
    const events: Record<string, unknown>[] = [];
    ws.on('message', (raw) => events.push(JSON.parse(String(raw))));
    await new Promise<void>((r) => ws.once('open', () => r()));

    send(ws, { type: 'replay_request', project_id: 'demo', since_seq: 0 });
    const done = await waitFor(events, 'replay_complete');
    expect(done.activity).toEqual(activity);

    ws.close();
    await srv.stop();
  });

  it('[v1.0.24.4] 不配置 → replay_complete 不带 activity（等价旧后端）', async () => {
    const srv = new FakeKnoweServer({ ringMax: 5 });
    await srv.start();
    const ws = new WebSocket(srv.url);
    const events: Record<string, unknown>[] = [];
    ws.on('message', (raw) => events.push(JSON.parse(String(raw))));
    await new Promise<void>((r) => ws.once('open', () => r()));

    send(ws, { type: 'replay_request', project_id: 'demo', since_seq: 0 });
    const done = await waitFor(events, 'replay_complete');
    expect(done.activity).toBeUndefined();

    ws.close();
    await srv.stop();
  });
});

describe('FakeKnoweServer · 广播与指令', () => {
  it('广播到达所有客户端，含发送者自己的 user_echo', async () => {
    const c1 = await client();
    const c2 = await client();
    send(c1.ws, { type: 'replay_request', project_id: 'demo', since_seq: 0 });
    send(c2.ws, { type: 'replay_request', project_id: 'demo', since_seq: 0 });
    await waitFor(c1.events, 'replay_complete');

    send(c1.ws, { type: 'user_message', project_id: 'demo', content: '喂', client_msg_id: 'cm_9' });

    const e1 = await waitFor(c1.events, 'user_echo');
    const e2 = await waitFor(c2.events, 'user_echo');
    expect(e1.client_msg_id).toBe('cm_9');
    expect(e2.seq).toBe(e1.seq);

    c1.ws.close();
    c2.ws.close();
  });

  it('ping → pong（无 seq）', async () => {
    const { ws, events } = await client();
    send(ws, { type: 'ping' });
    const pong = await waitFor(events, 'pong');
    expect(pong.seq).toBeUndefined();
    ws.close();
  });

  it('未知指令 → 服务器级 error（无 seq、无 project_id）', async () => {
    const { ws, events } = await client();
    send(ws, { type: '没这个指令' });
    const err = await waitFor(events, 'error');
    expect(err.seq).toBeUndefined();
    expect(err.project_id).toBeUndefined();
    ws.close();
  });
});

describe('FakeKnoweServer · 故障注入', () => {
  it('mute → 一条事件都广播不出去（模拟 BUG-1 广播失聪）', async () => {
    const { ws, events } = await client();
    send(ws, { type: 'replay_request', project_id: 'demo', since_seq: 0 });
    await waitFor(events, 'replay_complete');

    server.muted = true;
    send(ws, { type: 'user_message', project_id: 'demo', content: '喂', client_msg_id: 'cm_1' });
    await sleep(80);

    expect(events.some((e) => e.type === 'user_echo')).toBe(false);   // 静默失聪
    expect(server.lastSeq('demo')).toBe(1);                            // 但 seq 照涨（这才像真失聪）
    ws.close();
  });

  it('restart → seq 归零、ring 清空（新纪元）', () => {
    server.emit('demo', { type: 'message', agent_id: 'x', content: '1' });
    expect(server.lastSeq('demo')).toBe(1);

    server.restart();
    expect(server.lastSeq('demo')).toBe(0);
    expect(server.ringOf('demo')).toHaveLength(0);
  });

  it('crash → 断连但 seq/ring 保留（重连后可增量回放）', async () => {
    server.emit('demo', { type: 'message', agent_id: 'x', content: '1' });
    const { ws } = await client();

    server.crash();
    await sleep(30);

    expect(ws.readyState).not.toBe(WebSocket.OPEN);
    expect(server.lastSeq('demo')).toBe(1);      // 崩溃不丢历史
  });

  it('gap → 凭空跳 seq', () => {
    server.emit('demo', { type: 'message', agent_id: 'x', content: '1' });
    server.gap('demo', 5);
    const ev = server.emit('demo', { type: 'message', agent_id: 'x', content: '2' });
    expect(ev.seq).toBe(7);                       // 1 → (跳过 2..6) → 7
  });

  it('doubleResolve → 同一张卡先后两条 resolution', () => {
    server.doubleResolve('demo', 'ap_1', 'timeout', 'cancelled');
    const resolved = server.ringOf('demo').filter((e) => e.type === 'approval_resolved');
    expect(resolved.map((e) => e.resolution)).toEqual(['timeout', 'cancelled']);
  });

  it('runScript 按脚本编排', async () => {
    await server.runScript([
      { kind: 'emit', projectId: 'demo', event: { type: 'message', agent_id: 'x', content: '1' } },
      { kind: 'mute' },
      { kind: 'emit', projectId: 'demo', event: { type: 'message', agent_id: 'x', content: '2' } },
      { kind: 'unmute' },
      { kind: 'restart' },
    ]);
    expect(server.muted).toBe(false);
    expect(server.lastSeq('demo')).toBe(0);       // restart 归零
  });
});
