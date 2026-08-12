# [v1.0.13][R2] Legacy coordinator uses the shared identity contract.
# knowe v0.6 — Harness 核心引擎
"""
deepseek.py — 真 LLM 档（DeepSeek，流式 + function calling）。

和 Fake 档演的是同一出戏，只是台词由真模型现写：
    用户说事 → 项目经理觉得一个人干不完 → 调 propose_agents → 组队审批卡 → 等你点头
             → agents_created → 调 propose_next → 派活审批卡 → 等你点头
             → instruction_injected → 成员真的写一份方案（也是模型生成，流式）
             → report_submitted → 项目经理收口

三条铁律（一条都不能破）：

  1. **任何异常都变成 error 事件，引擎不倒。**
     没配 key、网络断、HTTP 4xx/5xx、流中途炸、模型把 proposed 传成字符串——
     全部变成一条引擎级 error 事件（带 project_id + seq，你在会话里看得见），
     然后本回合安静结束。引擎绝不因为模型抽风而挂掉。

  2. **审批只走 gate.propose()，不自己造卡。**
     gate 保证「恰好一条 approval_resolved」和 300 秒超时，绕过它就绕过了所有保证。

  3. **普通对话依然是流式的。** 不带工具调用的消息，一个字一个字地出，和以前一样。

⚠ 模型不是msg("deepseek.py.001")了工具——它只是**提议**。真正决定的是屏幕前的人。
   所以工具执行的第一步永远是 gate.propose()，等人点头；模型只会拿到一条
   tool 消息告诉它结果（approved / rejected / timeout），然后自己圆场。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Callable

from knowe_core.provider_client import ProviderClient, build_http_timeout
from knowe_core.stream_assembler import StreamAssembler

from ..context_compressor import project_messages
from ..i18n_backend import msg

from ..config import CONFIG
from ..agent_identity import coordinator_identity, identity_for
from ..feature_flags import FeatureFlag, enabled as feature_enabled
from ..gate import ApprovalCancelled, Gate
from ..attachments import inject_into_last_user
from .base import Emit, Turn

log = logging.getLogger("knowe.deepseek")

COORDINATOR = "coordinator"

_LEGACY_COORDINATOR_PROMPT = """You are the coordinator of this project, helping the user move things forward inside the Knowe software platform.

★ The two most important rules, above everything else:
  ① The team is **mutable** — you can add members anytime with propose_agents, and remove members with propose_remove_agent.
     Adding is **incremental**: when the team already has people, calling it again **adds** to the existing team; it does not rebuild the team.
     **There has never been a limitation like "the team can only be created once" or "the existing team cannot be modified"** — if you think that, you are wrong,
     don't believe it, and don't say it to the user. When the user says "add a backend" → you propose only that one backend; never reply "I can't add".
  ② Any action involving team members (add / remove / bring back someone who left) **must call the corresponding tool**
     (propose_agents / propose_remove_agent), which pops an approval card and waits for the user's confirmation. **Never fob the user off with a bare
     "already added / already brought back / already removed"** — if you don't call the tool, nothing has happened,
     and that is lying to the user. Until the tool actually passes, don't tell the user anything is "done".

You have three tools:
- propose_agents: propose **adding people to the team**.
- propose_next: assign a concrete task to a member.
- propose_remove_agent: propose **removing** (archiving) a member who is no longer needed.

Important rules:
1. These three tools are **not your call** — they merely propose to the user, an approval card pops up in the UI,
   and the user clicks confirm or reject. You will receive the result (approved / rejected / timeout / cancelled).
2. If the user rejects or times out, don't push it. Explain clearly why the thing didn't move forward and ask whether they want a different approach.
3. Answer simple questions directly; don't form a team at the drop of a hat — teaming has a cost and it annoys the user.
4. Be concise, direct, and actionable. Don't parrot the user's words, and skip hollow pleasantries.

★ About adding members ([v0.9b] this wasn't stated before, and you assumed the team could only be created once):
   **If the team already has members, calling propose_agents again **adds** new members to the existing team;
   it does not rebuild the team. The same id will not be created twice.**
   When the user says "add a backend" → you propose only that one backend; don't re-report the people already on the team,
   and never reply "the team can only be created in one shot" — that's not true.

★ About removing members:
   If a member is truly no longer needed (task done, role mismatch), you can use propose_remove_agent
   to propose removal. It also requires user approval. Once approved, they get **archived**: they no longer take new tasks,
   but their reports and outputs **are all kept**. Don't use it as an eraser — explain to the user why before removing anyone.

Member id format: role prefix + underscore + sequence number, e.g. fe_1 (frontend), be_1 (backend),
pm_1 (product), qa_1 (testing), ux_1 (design). The role field uses Chinese, e.g. "frontend".
"""

SYSTEM_PROMPT = "\n\n".join(
    part for part in (
        coordinator_identity().system_block() if feature_enabled(FeatureFlag.IDENTITY_CONTRACT_V1) else "",
        _LEGACY_COORDINATOR_PROMPT,
    )
    if part
)

# ═══════════════════════════════════════════════════════════════
# 一、工具定义（交给 DeepSeek 的那份说明书）
# ═══════════════════════════════════════════════════════════════

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "propose_agents",
            "description": (
                msg("deepseek.py.003") +
                msg("deepseek.py.004") +
                msg("deepseek.py.005") +
                msg("deepseek.py.006") +
                msg("deepseek.py.007")
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposed": {
                        "type": "array",
                        "description": msg("deepseek.py.008"),
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": msg("deepseek.py.009"),
                                },
                                "role": {
                                    "type": "string",
                                    "description": msg("deepseek.py.010"),
                                },
                            },
                            "required": ["id", "role"],
                        },
                    },
                },
                "required": ["proposed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_next",
            "description": (
                msg("deepseek.py.011") +
                msg("deepseek.py.012") +
                msg("deepseek.py.013")
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_id": {
                        "type": "string",
                        "description": msg("deepseek.py.014"),
                    },
                    "instruction": {
                        "type": "string",
                        "description": msg("deepseek.py.015"),
                    },
                },
                "required": ["target_id", "instruction"],
            },
        },
    },
    {
        # [v0.9b] 减人 = 归档。**彻底删除不在这里**（将来走「联系人」功能）。
        "type": "function",
        "function": {
            "name": "propose_remove_agent",
            "description": (
                msg("deepseek.py.016") +
                msg("deepseek.py.006") +
                msg("deepseek.py.017")
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_id": {
                        "type": "string",
                        "description": msg("deepseek.py.018"),
                    },
                    "reason": {
                        "type": "string",
                        "description": msg("deepseek.py.019"),
                    },
                },
                "required": ["target_id"],
            },
        },
    },
]


class ToolArgError(Exception):
    """模型把工具参数传歪了。不是崩溃的理由——告诉它，让它重来。"""


# ═══════════════════════════════════════════════════════════════
# 二、Agent
# ═══════════════════════════════════════════════════════════════

# 注入点：测试用 httpx.MockTransport 顶掉真实网络（不然测一次要花真钱）
ClientFactory = Callable[[], Any]


class DeepSeekAgent:
    """对接 DeepSeek /chat/completions（stream=true + tools）。"""

    def __init__(self, client_factory: ClientFactory | None = None) -> None:
        self.api_key = CONFIG.deepseek_api_key
        self.model = CONFIG.deepseek_model
        self.base_url = CONFIG.deepseek_base_url.rstrip("/")
        self._client_factory = client_factory
        # 每个项目的花名册（propose_next 要校验目标在不在队里）
        self._members: dict[str, list[dict[str, str]]] = {}

    # ── 唯一入口 ──
    async def run_turn(self, turn: Turn, emit: Emit, gate: Gate) -> None:
        try:
            await self._run(turn, emit, gate)
        except ApprovalCancelled:
            raise                      # 用户发了新消息 → 引擎负责收摊，不在这里吞掉
        except Exception as exc:       # ★ 铁律 1：引擎不许因为模型抽风倒下
            log.exception(msg("deepseek.py.020"), turn.project_id)
            await emit({
                "type": "error",
                "agent_id": COORDINATOR,
                "message": msg("deepseek.py.021", exc=exc),
            })

    # ═══════════════════════════════════════════════════════════
    # 主循环：对话 ⇄ 工具。结束由模型的普通回复或外部取消决定。
    # ═══════════════════════════════════════════════════════════
    async def _run(self, turn: Turn, emit: Emit, gate: Gate) -> None:
        client = self._make_client()
        if client is None:
            await emit({
                "type": "error",
                "agent_id": COORDINATOR,
                "message": self._why_unavailable(),
            })
            return

        authoritative: list[dict[str, Any]] = [
            dict(message) for message in turn.history if isinstance(message, dict)
        ]
        authoritative.append({"role": "user", "content": turn.content})
        projected, _ = project_messages(authoritative)
        # [v1.0.19.4] ★ 附件在这里才真正进入发给 provider 的 messages。
        #   投影后当前回合永远是尾部 verbatim 的最后一条 user；把文本+附件块合成
        #   OpenAI 多模态数组替换它。历史/权威副本仍是纯文本，不重发 base64。
        inject_into_last_user(projected, turn.attachments)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *projected,
        ]

        while True:
            await emit({"type": "agent_thinking", "agent_id": COORDINATOR})

            text, tool_calls, reasoning, reasoning_seconds = await self._stream_completion(
                client, messages, emit, agent_id=COORDINATOR, tools=TOOLS,
            )

            # ── 模型选择说话 → 收尾，回合结束 ──
            if not tool_calls:
                await emit({
                    "type": "message",
                    "agent_id": COORDINATOR,
                    "content": text,
                    "reasoning": reasoning or None,           # [v1.0.23.3]
                    "reasoning_seconds": reasoning_seconds,   # [v1.0.23.3]
                })
                return

            # ── 模型选择提议工具 → 交给用户裁决 ──
            # 把这轮的 assistant 消息原样记进上下文（带 tool_calls），
            # 否则下一轮模型会不认得自己刚说过什么。
            messages.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                result = await self._execute(call, turn, emit, gate)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result,
                })


    # ═══════════════════════════════════════════════════════════
    # 工具执行：**第一步永远是问人**
    # ═══════════════════════════════════════════════════════════
    async def _execute(self, call: dict[str, Any], turn: Turn,
                       emit: Emit, gate: Gate) -> str:
        name = (call.get("function") or {}).get("name", "")
        raw_args = (call.get("function") or {}).get("arguments") or "{}"

        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            if not isinstance(args, dict):
                raise ToolArgError(msg("deepseek.py.022"))
        except (json.JSONDecodeError, ToolArgError) as exc:
            # ★ 铁律 1：参数传歪了不崩溃——把话说回给模型，让它自己改
            return msg("deepseek.py.023", exc=exc)

        try:
            if name == "propose_agents":
                return await self._do_propose_agents(args, turn, emit, gate)
            if name == "propose_next":
                return await self._do_propose_next(args, turn, emit, gate)
            if name == "propose_remove_agent":
                return await self._do_propose_remove_agent(args, turn, emit, gate)
            return msg("deepseek.py.024", name=name)
        except ApprovalCancelled:
            raise                                   # 往上抛，引擎收摊
        except ToolArgError as exc:
            return f"error: {exc}"                  # 模型能看懂，会自己改

    # ── 组队 ──
    async def _do_propose_agents(self, args: dict[str, Any], turn: Turn,
                                 emit: Emit, gate: Gate) -> str:
        members = _parse_members(args.get("proposed"))


        decision = await gate.propose(              # ★ 弹卡，等人点头
            tool="propose_agents",
            agent_id=COORDINATOR,
            card_body={"proposed": members},
        )
        if decision != "approved":
            return decision                        # rejected / timeout —— 让模型自己圆场

        await emit({
            "type": "agents_created",
            "agent_id": COORDINATOR,
            "count": len(members),
            "members": members,
        })
        # [v0.9b Bug1] 增量：已经在队里的人不重复加。这本来就是对的——
        #   模型不知道而已（我们从没在 prompt 里说过）。现在说了。
        roster = self._members.setdefault(turn.project_id, [])
        for m in members:
            if not any(x["id"] == m["id"] for x in roster):
                roster.append(m)

        listed = "、".join(f"{m['id']}（{m['role']}）" for m in members)
        whole = "、".join(f"{m['id']}（{m['role']}）" for m in roster)
        return (msg("deepseek.py.025", listed=listed, whole=whole) +
                msg("deepseek.py.026"))

    # ── 减人（归档）── [v0.9b]
    async def _do_propose_remove_agent(self, args: dict[str, Any], turn: Turn,
                                       emit: Emit, gate: Gate) -> str:
        """
        提议移除一个成员 → 审批卡 → 通过则归档。

        ⚠ 这一档（单 agent 的 DeepSeekAgent）**没有落盘的花名册**——
          它的队伍只活在 self._members 这个内存字典里（v0.5 就是这样）。
          所以这里的「归档」只是把人从内存名单里摘掉 + 发事件。
          真正带落盘状态（status=removed）、带变更日志的那一套，在 Harness 档
          （engine.archive_worker）。两档的**用户可见行为一致**：
          卡 → 通过 → agent_removed 事件 → 他不再接活。
        """
        target_id = args.get("target_id")
        reason = args.get("reason") or ""
        if not isinstance(target_id, str) or not target_id:
            raise ToolArgError(msg("deepseek.py.027"))
        if target_id == COORDINATOR:
            raise ToolArgError(msg("deepseek.py.028"))

        roster = self._members.get(turn.project_id, [])
        member = next((m for m in roster if m["id"] == target_id), None)
        if member is None:
            known = "、".join(m["id"] for m in roster) or msg("deepseek.py.029")
            raise ToolArgError(msg("deepseek.py.030", target_id=target_id, known=known))

        decision = await gate.propose(              # ★ 弹卡，等人点头
            tool="propose_remove_agent",
            agent_id=COORDINATOR,
            card_body={"target_id": target_id, "reason": str(reason)},
        )
        if decision != "approved":
            return decision                        # 让模型自己圆场

        roster.remove(member)
        await emit({
            "type": "agent_removed",
            "agent_id": COORDINATOR,
            "target_id": target_id,
            "reason": str(reason),
        })
        left = "、".join(msg("deepseek.py.046", m_id=m["id"], m_role=m["role"]) for m in roster) or msg("deepseek.py.047")
        return (msg("deepseek.py.045", target_id=target_id, **{"member['role']": member['role']}) +
                msg("deepseek.py.031", left=left))

    # ── 派活 ──
    async def _do_propose_next(self, args: dict[str, Any], turn: Turn,
                               emit: Emit, gate: Gate) -> str:
        target_id = args.get("target_id")
        instruction = args.get("instruction")
        if not isinstance(target_id, str) or not target_id:
            raise ToolArgError(msg("deepseek.py.027"))
        if not isinstance(instruction, str) or not instruction:
            raise ToolArgError(msg("deepseek.py.032"))

        roster = self._members.get(turn.project_id, [])
        if not roster:
            raise ToolArgError(msg("deepseek.py.033"))
        if not any(m["id"] == target_id for m in roster):
            known = "、".join(m["id"] for m in roster)
            raise ToolArgError(msg("deepseek.py.030", target_id=target_id, known=known))

        decision = await gate.propose(              # ★ 弹卡，等人点头
            tool="propose_next",
            agent_id=COORDINATOR,
            card_body={"target_id": target_id, "instruction": instruction},
        )
        if decision != "approved":
            return decision

        await emit({
            "type": "instruction_injected",
            "agent_id": COORDINATOR,
            "target_id": target_id,
        })

        # 成员真的去干活：再叫一次模型，让它以这个成员的身份写方案（流式，用户看得到他在写）
        report = await self._member_works(target_id, instruction, turn, emit)

        await emit({
            "type": "report_submitted",
            "agent_id": target_id,
            "report_hash": hashlib.sha256(report.encode()).hexdigest()[:16],
        })
        return (
            msg("deepseek.py.034", target_id=target_id) +
            msg("deepseek.py.035", report=report)
        )

    async def _member_works(self, target_id: str, instruction: str,
                            turn: Turn, emit: Emit) -> str:
        """成员干活 = 以成员身份再跑一次流式生成。失败了也要有个交代，不能留空。"""
        role = next(
            (m["role"] for m in self._members.get(turn.project_id, []) if m["id"] == target_id),
            msg("deepseek.py.048"),
        )
        client = self._make_client()
        if client is None:
            return msg("deepseek.py.049", target_id=target_id, **{"why": self._why_unavailable()})

        messages = [
            {
                "role": "system",
                "content": (
                    (
                        identity_for(
                            target_id,
                            display_name=target_id,
                            role_name=role,
                        ).system_block() + "\n\n"
                    ) if feature_enabled(FeatureFlag.IDENTITY_CONTRACT_V1) else ""
                ) + (
                    msg("deepseek.py.050") + msg("deepseek.py.051")
                ),
            },
            {"role": "user", "content": msg("deepseek.py.052", **{"background": turn.content, "task": instruction})},
        ]

        await emit({"type": "agent_thinking", "agent_id": target_id})
        text, _, reasoning, reasoning_seconds = await self._stream_completion(
            client, messages, emit, agent_id=target_id, tools=None,
        )

        text = text or msg("deepseek.py.053", target_id=target_id)
        await emit({
            "type": "message",
            "agent_id": target_id,
            "content": text,
            "reasoning": reasoning or None,           # [v1.0.23.3]
            "reasoning_seconds": reasoning_seconds,   # [v1.0.23.3]
        })
        return text

    # ═══════════════════════════════════════════════════════════
    # 流式：一边出字，一边攒工具调用
    # ═══════════════════════════════════════════════════════════
    async def _stream_completion(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        emit: Emit,
        *,
        agent_id: str,
        tools: list[dict[str, Any]] | None,
    ) -> tuple[str, list[dict[str, Any]], str, float]:
        """
        返回 (正文, tool_calls, reasoning, reasoning_seconds)。[v1.0.23.3 加后两项]

        【v0.6】这里原来有 60 行手写的 httpx + SSE 解析 + tool_calls 分片合并。
        **全都搬进 knowe_core 了**——现在这个方法只做三件事：
          1. 让 ProviderClient 说 HTTP（它管重试、管端点探测、管类型化异常）
          2. 让 StreamAssembler 把分片攒成完整消息（tool_calls 的 arguments
             是一个字一个字来的，这是全项目 bug 密度最高的一段代码，
             现在只有一份实现，项目经理和 Worker 共用）
          3. 把 stream_delta 转成事件广播出去

        DeepSeekAgent 因此变成一个**薄封装**：它只剩「工具怎么走闸门」这件业务。
        """
        del client                      # ProviderClient 自己管连接

        provider = ProviderClient(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            timeout=build_http_timeout(
                connect=CONFIG.provider_connect_timeout_s,
                read=CONFIG.provider_read_timeout_s,
                write=CONFIG.provider_write_timeout_s,
                pool=CONFIG.provider_pool_timeout_s,
            ),
            max_retries=CONFIG.provider_max_retries,
            client_factory=self._client_factory,
        )

        deltas: list[str] = []

        def on_delta(text: str) -> None:
            deltas.append(text)
            # emit 是 async，回调是同步的 → 排一个任务出去。
            # 顺序有保证：asyncio 任务队列 FIFO，且都在 hub 的 seq 锁里排队。
            asyncio.create_task(emit({
                "type": "stream_delta", "agent_id": agent_id, "content": text,
            }))

        # [v1.0.23.3] 推理增量实时广播（推理在工具调用前，不受正文闸限制）
        def on_reasoning(text: str) -> None:
            asyncio.create_task(emit({
                "type": "reasoning_delta", "agent_id": agent_id, "content": text,
            }))

        assembler = StreamAssembler(
            stream_delta_callback=on_delta,
            reasoning_delta_callback=on_reasoning,   # [v1.0.23.3]
            tool_schemas=tools,
            # 带工具的回合先完整组装、类型判定，再把真正的自然语言交给公开流。
            # 无工具的 Worker 回合保持原来的逐字流式体验。
            tool_protocol_mode="normalize" if tools else "off",
        )

        t0 = time.monotonic()  # [v1.0.23.3] 思考耗时起点

        async for event in provider.chat_stream(
            messages=messages,
            tools=tools,
            extra_body={"tool_choice": "auto"} if tools else None,
        ):
            assembler.feed(event)

        turn = assembler.finalize_turn()
        if turn.kind in {"protocol_error", "stream_error"}:
            log.error(
                msg("deepseek.py.036"),
                agent_id, turn.kind, turn.protocol_encoding, turn.error,
            )
            raise RuntimeError(msg("deepseek.py.037"))

        # guarded 模式的正文回调在 finalize_turn() 才触发；让排出去的
        # stream_delta 先落地，再发最终 message，避免前端气泡闪烁。
        await asyncio.sleep(0)
        return (
            turn.content,
            list(turn.tool_calls),
            turn.reasoning,                        # [v1.0.23.3]
            round(time.monotonic() - t0, 1),       # [v1.0.23.3]
        )

    # ═══════════════════════════════════════════════════════════
    # 杂项
    # ═══════════════════════════════════════════════════════════
    def _make_client(self) -> Any | None:
        """
        [v0.6] 现在只用来回答一个问题：**这一档能不能用**（有没有 key）。
        真正的连接由 knowe_core 的 ProviderClient 建。
        返回一个 truthy 的哨兵就行。
        """
        if self._client_factory is not None:
            return self._client_factory
        if not self.api_key:
            return None
        return True

    def _why_unavailable(self) -> str:
        if not self.api_key:
            return (msg("deepseek.py.038") +
                    msg("deepseek.py.039"))
        return msg("deepseek.py.040")


# ═══════════════════════════════════════════════════════════════
# 解析工具
# ═══════════════════════════════════════════════════════════════

def _parse_sse(line: str) -> dict[str, Any] | str | None:
    """
    一行 SSE → delta 字典 / '[DONE]' / None（心跳、空行、没内容）。
    """
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if payload == "[DONE]":
        return "[DONE]"
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return None
    choices = obj.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta")
    return delta if isinstance(delta, dict) else None


def _merge_tool_call(pending: dict[int, dict[str, Any]], frag: dict[str, Any]) -> None:
    """把一个 tool_call 分片并进 pending[index]（arguments 是拼出来的）。"""
    idx = frag.get("index", 0)
    if not isinstance(idx, int):
        idx = 0
    slot = pending.setdefault(idx, {
        "id": "", "type": "function",
        "function": {"name": "", "arguments": ""},
    })

    if frag.get("id"):
        slot["id"] = frag["id"]

    fn = frag.get("function") or {}
    if fn.get("name"):
        slot["function"]["name"] = fn["name"]
    if fn.get("arguments"):
        slot["function"]["arguments"] += fn["arguments"]


def _parse_members(raw: Any) -> list[dict[str, str]]:
    """
    校验模型传来的 proposed。**这是最容易被传歪的地方**——
    传成字符串、传成 [{name:...}]、少了 role……一律不崩溃，抛 ToolArgError 让模型重来。
    """
    if not isinstance(raw, list) or not raw:
        raise ToolArgError(msg("deepseek.py.041"))

    members: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ToolArgError(msg("deepseek.py.042"))
        mid, role = item.get("id"), item.get("role")
        if not isinstance(mid, str) or not mid:
            raise ToolArgError(msg("deepseek.py.043"))
        if not isinstance(role, str) or not role:
            raise ToolArgError(msg("deepseek.py.044"))
        if mid in seen:
            continue                      # 模型偶尔会把同一个人报两遍——去重，不必为此报错
        seen.add(mid)
        members.append({"id": mid, "role": role})
    return members


__all__ = ["DeepSeekAgent", "TOOLS", "SYSTEM_PROMPT", "ToolArgError"]