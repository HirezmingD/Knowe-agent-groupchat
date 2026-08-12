# knowe v0.6 — Harness 核心引擎
"""
Per-engine tool registry — 没有全局状态（这一点和 v0.1 一致）。

每个引擎实例持有自己的 ToolRegistry。工具 = OpenAI 兼容的 function schema + 一个 handler。

⚠ **和 v0.1 唯一的偏离：`execute()` 是 async 的。**

  v0.1 的 handler 是同步函数，里面靠 `threading.Event.wait()` 阻塞等审批——
  一个线程站着不动等人点头，代价是一个线程。
  v0.2 的 gate 是 asyncio 的（`await gate.propose()`），所以 handler 必须能 await。
  execute 不改成 async，工具就永远碰不到闸门——这不是可选项。

  为了兼容纯计算类工具（不需要 await 的那种），execute 两种 handler 都收：
  同步的直接调，返回 coroutine 的就 await。调用方一律 `await registry.execute(...)`。
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from knowe_core.errors import ToolExecutionError, ToolNotFoundError

# Handler signature: handler(args: dict, **context) -> str | Awaitable[str]
ToolHandler = Callable[..., Any]


class ToolDef:
    """一个工具的定义。"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: ToolHandler,
        requires_approval: bool = False,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.requires_approval = requires_approval

    def to_openai_schema(self) -> dict[str, Any]:
        """OpenAI 兼容的 function 定义。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, args: dict[str, Any], **context: Any) -> str:
        """执行 handler，把任何异常包成 ToolExecutionError。"""
        try:
            out = self.handler(args, **context)
            if inspect.isawaitable(out):
                out = await out          # ★ async handler（要等闸门的那种）
            return str(out)
        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(self.name, e) from e


class ToolRegistry:
    """每个引擎一个。

    用法::

        reg = ToolRegistry()
        reg.register(
            name="propose_agents",
            description="Propose a team",
            parameters={...},
            handler=handle_propose_agents,
            requires_approval=True,
        )
        schemas = reg.get_schemas()
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: ToolHandler,
        requires_approval: bool = False,
    ) -> None:
        """注册一个工具。重名直接 ValueError——静默覆盖是灾难。"""
        if name in self._tools:
            raise ValueError(f"Tool '{name}' already registered")
        self._tools[name] = ToolDef(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            requires_approval=requires_approval,
        )

    def get(self, name: str) -> ToolDef:
        if name not in self._tools:
            raise ToolNotFoundError(name)
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def get_schemas(self, only: "frozenset[str] | set[str] | None" = None) -> list[dict[str, Any]]:
        """
        only=None → 全量（老行为，一个字节不变）。
        only=集合 → 只导出名字在集合里的工具 schema。

        子集导出只影响模型可见 schema，不改变注册表中的 handler；调用方可用它
        构造最小能力面，同时仍由执行层对未知工具作确定性拒绝。
        """
        if only is None:
            return [t.to_openai_schema() for t in self._tools.values()]
        return [t.to_openai_schema() for t in self._tools.values() if t.name in only]

    async def execute(self, name: str, args: dict[str, Any], **context: Any) -> str:
        """按名字执行。找不到 → ToolNotFoundError。"""
        tool = self.get(name)
        return await tool.execute(args, **context)

    def is_gated(self, name: str) -> bool:
        """这个工具要不要先问过人。"""
        return self.get(name).requires_approval

    def names(self) -> list[str]:
        return list(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        return f"ToolRegistry({list(self._tools.keys())})"
