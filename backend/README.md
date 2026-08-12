# Knowe 后端 · v0.3 全量重建

从 0 重写。旧版 16 个 `.py` 一行没抄。

**Runtime v1.0.18.4 验证状态**：605 项 Python 测试与 10 个子测试通过；控制 HTTP、知识路由和 WebSocket 共用每进程 Runtime Secret，独立 8130 监听已移除。

---

## 跑起来

```bash
pip install -r requirements.txt          # Fake 档只需要 websockets
export KNOWE_RUNTIME_TOKEN=$(python -c "import secrets; print(secrets.token_hex(32))")
python -m backend.server                 # ws://127.0.0.1:8080 + http://127.0.0.1:8081
```

```bash
curl -H "X-Knowe-Runtime-Token: $KNOWE_RUNTIME_TOKEN" http://127.0.0.1:8081/health
# {"status": "ok", "project_count": 2, "ws_clients": 1}
```

桌面应用由 Electron 主进程为每次后端启动生成该 secret，并只通过请求头注入；不要把它写入 URL、renderer store 或日志。知识 API 位于同一 8081 服务的 `/api/knowledge/*`，不再监听 8130。

跑测试：

```bash
pip install -r requirements-dev.txt
python -m pytest -q                       # 40 passed
```

### 开关（环境变量）

| 变量 | 取值 | 默认 | 说明 |
|:--|:--|:--|:--|
| `KNOWE_AGENT` | `fake` / `deepseek` | `fake` | 零 token 联调 / 真 LLM |
| `KNOWE_SCRIPT` | `simple` / `full` | `full` | Fake 档剧本：纯消息往返 / 全链路带审批 |
| `KNOWE_STRICT` | `1` / `0` | `1` | 出站契约强校验：违规直接炸在服务端 |
| `DEEPSEEK_API_KEY` | — | 空 | 真 LLM 档必填（没配 → 发一条 error 事件，不崩） |
| `KNOWE_FAKE_DELAY/THINK/WORK` | 秒 | 0.06/0.3/0.8 | Fake 档节奏，调小可以跑得飞快 |

---

## ⚠️ 一处契约冲突，我按契约走了（需要你知道）

你的要求 §二写：Fake 档 `message` 收尾之后要发 **`turn_end`**。
但你的要求 §七写：**所有出站事件必须对齐前端契约**——而 `turn_end` **不在 envelope.ts 的
`InboundEventSchema` 联合类型里**。真发出去，前端 Zod 会当场拒收（走廊「Zod 拒收」计数 +1，
console.error，然后丢弃）。

两条要求打架，我选了后者，理由是：前端契约是我们唯一的机器可判真源，一个注定被拒收的事件
只会给排障添噪音。所以 **`turn_end` 默认不发**（`KNOWE_EMIT_TURN_END=1` 可以打开，
但打开后前端会拒收它）。

**你要 `turn_end` 的话，正确做法是先把它加进 `envelope.ts`，再来打开这个开关。**
前后端契约必须一起改，不能一边偷偷发一边悄悄扔——这正是 v0.2 栽跟头的地方。

---

## 六个 BUG 怎么修的（每个都有一条永久回归测试）

| BUG | 旧版毛病 | 修法 | 回归测试 |
|:--|:--|:--|:--|
| **BUG-1** | 广播失聪：`getattr(c, 'open', False)` 在 websockets≥14 恒为 False，所有连接被当成死的 | `hub._is_alive()` 是**全仓唯一判活口径**，只认 `ws.state is State.OPEN` | `test_BUG1_broadcast_reaches_all_clients_including_sender` |
| **BUG-2** | 控制/工作共用队列，`approve` 被 worker 抢走 | **结构性根治**：审批根本不走队列。gate 用 `asyncio.Future`，worker 挂在 future 上，`approve` 直接 `set_result` — 没有共享队列，就没有「抢」这回事 | `test_BUG2_approve_is_not_stolen_by_a_busy_worker` |
| **B-3** | 服务器级 error 无 project_id/seq，前端无处安放 | 错误**分两级**：能归因到项目 → 引擎级 error（带 project_id + seq，进会话流）；归因不了（畸形帧、未知指令）→ 服务器级 error（无 seq，进前端全局通知通道，这是契约要求的旁路形态） | `test_unknown_command_yields_server_level_error` / `test_server_error_has_no_seq_engine_error_has_seq` |
| **B-4** | 崩溃复提卡缺顶层字段 | 复提走的是**和首提同一个 `gate.propose()`**（只多传 `recovered=True`）——想缺字段都缺不了 | `test_B4_recovered_card_carries_full_top_level_fields` |
| **B-5** | RingBuffer `seq==0` 分支在淘汰后返回**残缺历史**当完整历史 | `replay_since()` 返回 `(events, gap)`。凡是要的区间被淘汰过 → `gap=True` 且**一条残缺历史都不返回**，改发 `resync_required` 让前端走快照重建。`since_seq=0` 不再有特殊分支 | `test_B5_since_zero_after_eviction_reports_gap_not_partial_history` |
| **B-6** | STRICT_CONTRACT 覆盖不全 | `contract.py` 的 `EVENT_SPEC` 是出站事件的**唯一真源**：没登记的事件类型**根本发不出去**（`ContractViolation`）。每条出站事件在 hub 里发前必过 `validate_outbound()` | `test_B6_unregistered_event_type_is_rejected` 等 6 条 |

---

## 结构

```
backend/
  config.py      所有可调项（环境变量在此汇总，别处不许写死）
  contract.py    ★ 出站契约唯一真源 + 强校验（B-6）
  ring.py        每项目 1000 条环形缓冲（B-5）
  hub.py         项目注册表 / seq 单点加锁盖号 / 广播 / 客户端剪枝（BUG-1）
  gate.py        审批闸门：Future 直达，恰好一次解决（BUG-2 / B-4）
  engine.py      每项目一个引擎：工作队列 vs 控制直达，回合异常不倒（B-3）
  server.py      WS 入口 + 握手回放 + 指令路由 + /health
  agents/
    base.py      AgentPort 协议（引擎只认协议，不认实现）
    fake.py      零 token，两条剧本
    deepseek.py  真 LLM 流式，错误变 error 事件不崩
tests/
  test_unit.py   23 条：ring / 契约 / seq / gate
  test_e2e.py    17 条：真服务 + 真 WS 客户端走全链路
```

### 三条把 bug 挡在门外的设计

1. **seq 只有一个地方能盖**（`hub.emit` 的锁内），引擎和 agent 都碰不到 `seq`。
   并发 100 条 emit 也不会撞号（有测试）。
2. **agent 只会「演」，不会「发」**。它拿到的 `emit` 只接受 payload，
   `project_id` / `project_name` / `ts` / `seq` 全由 hub 注入——想漏 `ts` 都漏不了。
3. **契约在代码里，不在文档里**。加一个新事件，第一步不是写代码，是往 `EVENT_SPEC`
   （和 `envelope.ts`）里登记；不登记，`validate_outbound` 当场拒发。

---

## 和前端联调的验收单

前端起在 `npm run dev`，后端 `python -m backend.server`，然后：

| # | 动作 | 屏幕上应该看到 |
|:--|:--|:--|
| 1 | 打开前端 | 右上角徽章：绿「已连接」 |
| 2 | 新建项目 | 左栏出现项目（后端 `project_created` 回来才建，前端不本地伪造） |
| 3 | 发一条消息 | 自己的气泡立刻出现（pending）→ 小点消失（`user_echo` 到了） |
| 4 | 等回复 | 左边气泡逐字长出（`stream_delta`）→ 光标消失定格（`message`） |
| 5 | 组队卡 | 中间出现审批卡，倒计时从 5:00 往下走（`card.expires_at`，服务端时钟） |
| 6 | 点确认 | 按钮变「已提交…」→ 后端 `approval_resolved` 回来 → 卡片翻「已确认」 |
| 7 | 成员入驻 | 系统行「小前、小后 已加入项目」，右侧花名册出现 2 人 |
| 8 | 派活卡 → 确认 | 「已收到任务」，该成员状态变「工作中」 |
| 9 | 报告回来 | 「已提交报告」，成员状态变回「空闲」 |
| 10 | 挂着审批时发新消息 | 旧卡自动变「已取消」（后端只发一条 `cancelled`），新回合开始 |
| 11 | 杀掉后端 | 徽章变黄「重连中」 |
| 12 | 重启后端 | 徽章回绿；`replay_complete.last_seq` 变小 → 前端识别为新纪元 → 清会话 + 拉快照 |

---

## 已知边界（没做，也不假装做了）

- **没有持久化**。进程一停，ring 和项目就没了。「历史落盘」是后端 B2 的活，
  不在这次范围内（v0.3 计划里也诚实标注了这一点）。
- **未读数**：后端维护 `last_read_seq`，客户端可发 `mark_read {project_id, seq}` 上报，
  `project_created` / `replay_complete` 会带 `unread_count`。但**前端的出站契约里没有
  `mark_read`**，所以现在没人发它 —— 前端也可以直接用 `last_seq - 本地已读` 自己算。
  要真做，得先把 `mark_read` 加进 `envelope.ts` 的出站联合类型。
- **`__platform__` 知知通道**没实现（等契约审计定稿）。
- **真 LLM 档只接了 DeepSeek 的对话**，没接工具调用——所以真 LLM 档下不会出审批卡。
  全链路审批目前只有 Fake 档能演。这是个真缺口，不是遗漏：让 LLM 真的去调
  `propose_agents` 需要 function calling 那一整套，属于下一批的活。
