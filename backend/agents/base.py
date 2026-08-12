"""
base.py — Agent 端口（引擎只认这个协议，不认具体实现）。

引擎给 agent 三样东西：
  · emit(payload)          出一条引擎级事件（seq / project_id / ts 由 hub 盖，agent 不管）
  · gate                   审批闸门（propose 会挂起直到有结果）
  · project 上下文
agent 只负责「这一回合怎么演」，不碰 seq、不碰广播、不碰 WebSocket。
换 fake / DeepSeek / 将来的 hermes，只是换这个类的实现。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from ..gate import Gate

Emit = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class Turn:
    """一个回合的上下文。"""

    def __init__(self, project_id: str, project_name: str, content: str,
                 history: list[dict[str, str]],
                 attachments: list[dict[str, object]] | None = None) -> None:
        self.project_id = project_id
        self.project_name = project_name
        self.content = content
        self.history = history          # [{'role': 'user'|'assistant', 'content': str}]
        # [v1.0.19.4] 本回合用户附件的 provider 内容块（image_url / file）。
        #   None 或空 = 没有附件（绝大多数消息），行为与以前完全一致。
        self.attachments = attachments or None


class AgentPort(Protocol):
    """
    引擎对 agent 的唯一要求（**单回合插头**）。

    知知（ZinniaAgent）、FakeAgent、DeepSeekAgent 走这个。
    """

    async def run_turn(self, turn: Turn, emit: Emit, gate: Gate) -> None:
        ...


class HarnessAgentPort(Protocol):
    """
    Harness 中长期存在的 Coordinator AgentLoop 插头。

    Phase F 后 Worker 不再实现这个端口：WorkerRuntime 以稳定核心 Prompt、Profile Prompt
    和 Task Capsule 组装单任务上下文，并持有 TaskRun 与任务日志。
    这里的历史、工具箱和 interrupt 生命周期只属于 Coordinator。

      ephemeral_system_prompt  Engine 每回合注入 Coordinator 人设与项目状态
      run_conversation(...)    通过 user_message / conversation_history 注入本轮内容
      interrupt()              打断当前 Coordinator 回合（例如提案被拒）
    """

    agent_id: str
    role: str
    ephemeral_system_prompt: str

    async def run_conversation(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        ...

    def interrupt(self) -> None:
        ...
