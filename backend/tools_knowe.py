# knowe v0.6 — Harness 核心引擎
"""
工具矩阵 —— 项目经理能做什么、Worker 能做什么。

**权限是靠「给不同的 ToolRegistry」实现的，不是靠工具自己检查调用者。**
项目经理拿到的注册表里根本没有 safe_write_file 这个词；Worker 拿到的注册表里
根本没有 propose_agents。模型看不见的工具，它就调不了——
这比msg("tools_knowe.py.001")可靠得多。

| 谁 | 能调 |
|:--|:--|
| 项目经理 coordinator | propose_agents* · propose_next* · propose_remove_agent* · read_report · list_handoff_dir · search/read_project_memory · search/read_project_knowledge · read_external_file · list_external_dir |
| Worker（V2） | 项目文件 6 · 条件式外部只读/复制 3 · **safe_bash ×1** · web ×2 · **browser ×7**；精确清单由 `WorkerToolSurfaceV2` 生成 |

[v0.20 Batch 4] 加粗的那些是这一批新加的。**一个都没给项目经理**，这是有意的：

  项目经理的工作是**判断和分配**，不是干活。给他一把终端，他会自己动手改文件，
  然后队伍里那五个人在干什么就没人知道了 —— 交接、审批、报告这套东西
  全部绕过去了。权限矩阵在这里不是安全机制，是**组织设计**。
  （v0.6 起这条规矩靠「给不同的注册表」实现，见下文。）



⚠ **和 v0.1 的一处关键偏离：不用模块级全局。**

  v0.1 靠 `_current_engine` / `_current_state` 两个模块全局给 handler 传上下文，
  每个回合开始前 `set_current_engine(self)` 覆盖一次。同步单线程下没问题。

  **但在 asyncio 下这是一个真 bug**：两个项目的回合会在 `await` 点交错——
  项目 A 的工具正 await 闸门（用户还没点头，可能等 5 分钟），
  这期间项目 B 起了一个回合、把全局改成了 B 的引擎；
  A 的用户点了确认，handler 醒来，读到的是 **B 的引擎**——
  于是 A 的成员被创建到了 B 的项目里。

  所以这里改成**闭包绑定**：注册表是每个引擎现建的，handler 里的 engine 是捕获的局部变量。
  没有共享可变状态，就没有这一类 bug。
"""

from __future__ import annotations

import asyncio
import copy
import functools
import hashlib
import json
import logging
import mimetypes
import os
import tempfile
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping, Sequence

from knowe_core.tool_registry import ToolRegistry

from . import (
    browser_tools,
    file_ops,
    roles,
    runtime_settings,
    terminal_tools,
    tool_ledger,
    web_tools,
)
from .agent_runtime import ToolError, runtime_for
from .gate import ApprovalCancelled
from .config import CONFIG
from .runtime import WORKER_TOOL_NAMES
from .i18n_backend import msg

# [v0.9d] 名字不在这儿算了 —— 引擎 reserve_name 说了算（它会先去花名册里找旧名）

if TYPE_CHECKING:                       # 只为类型检查，避免循环导入
    from .engine import ProjectEngine

log = logging.getLogger("knowe.tools")

COORDINATOR = "coordinator"

# ═══════════════════════════════════════════════════════════════
# [v0.29 问题一] 项目经理想给一个**正在干活**的人再派第二件活时，回给他的话
#
# 住在这儿而不是 engine.py：engine 导入 tools_knowe，反过来再导一次就是循环。
# 而且这本来就该住在这儿 —— 它是一条**工具回执**，和 handle_propose_next 里
# 那几条（dispatch_frozen / 重发闸）是一家人，理由见 v0.23 的那段长注释：
# **工具回执是模型 composing 下一句话前读到的最后一样东西**。回执里写什么词，
# 它就跟用户说什么词。所以这里一个黑话都不留，并且直接告诉它下一句该怎么说。
WORKER_BUSY_DISPATCH = (
    msg("tools_knowe.py.002") +
    msg("tools_knowe.py.003") +
    msg("tools_knowe.py.004") +
    "\n" +
    msg("tools_knowe.py.005") +
    msg("tools_knowe.py.006") +
    msg("tools_knowe.py.007") +
    msg("tools_knowe.py.008") +
    msg("tools_knowe.py.009") +
    msg("tools_knowe.py.010") +
    msg("tools_knowe.py.011") +
    msg("tools_knowe.py.012")
)

# ═══════════════════════════════════════════════════════════════
# [v0.10a Issue 4] 标准角色注册表 —— agent 的角色只能从这里选。
#
#   老问题：项目经理建人时角色是它随口写的（「工程师」「Agent」「助手」……），
#   前端 displayInfo() 按 id 前缀去查角色，前缀对不上就兜底成「Agent」——
#   于是花名册里冒出一排「Agent」，谁也不知道他们是干什么的。
#
#   现在两头都收口：
#     · 后端（这里 + _parse_agents）：角色必须 ∈ KNOWN_ROLES.values()，否则打回让项目经理改。
#     · 前端（state.ts DEFAULT_ROLE_TYPES / DEFAULT_AGENTS）：同一张表，前缀 → 角色。
#   前缀（fe / be / …）既是 id 的头一截，也是前端认角色的钥匙，两边必须对得上。
#
#   表的来源：ref/agency-agents 那套开源角色库（17 大类），这里精选最常用的 24 个。
# ═══════════════════════════════════════════════════════════════
KNOWN_ROLES: dict[str, str] = {
    "fe":     msg("tools_knowe.py.013"),
    "be":     msg("tools_knowe.py.014"),
    "pm":     msg("tools_knowe.py.015"),
    "qa":     msg("tools_knowe.py.016"),
    "ux":     msg("tools_knowe.py.017"),
    "da":     msg("tools_knowe.py.018"),
    "devops": msg("tools_knowe.py.019"),
    "sec":    msg("tools_knowe.py.020"),
    "ml":     msg("tools_knowe.py.021"),
    "mobile": msg("tools_knowe.py.022"),
    "game":   msg("tools_knowe.py.023"),
    "gis":    msg("tools_knowe.py.024"),
    "mkt":    msg("tools_knowe.py.025"),
    "fin":    msg("tools_knowe.py.026"),
    "hc":     msg("tools_knowe.py.027"),
    "edu":    msg("tools_knowe.py.028"),
    "ar":     msg("tools_knowe.py.029"),
    "sup":    msg("tools_knowe.py.030"),
    "sre":    msg("tools_knowe.py.031"),
    "db":     msg("tools_knowe.py.032"),
    "arch":   msg("tools_knowe.py.033"),
    "writer": msg("tools_knowe.py.034"),
    "media":  msg("tools_knowe.py.035"),
    "legal":  msg("tools_knowe.py.036"),
}

#: 合法角色标签集合（校验用）
_KNOWN_ROLE_LABELS: frozenset[str] = frozenset(KNOWN_ROLES.values())
_ROLE_PREFIX_BY_LABEL: dict[str, str] = {label: prefix for prefix, label in KNOWN_ROLES.items()}
_AGENT_ID_RE = re.compile(r"^(?P<prefix>[a-z][a-z0-9]*)_(?P<seq>[1-9][0-9]*)$")


def _ok(**kw: Any) -> str:
    return json.dumps({"status": "ok", **kw}, ensure_ascii=False)


def _err(message: str, **kw: Any) -> str:
    """
    工具出错**不抛异常**——把话说回给模型，让它自己改。
    抛异常只会让引擎倒下；返回一句人话，模型下一轮就能修正。
    """
    return json.dumps({"status": "error", "message": message, **kw}, ensure_ascii=False)


def _register(reg: Any, **kw: Any) -> None:
    """Register a business or Coordinator tool with a task-local audit wrapper."""

    handler = kw.get("handler")
    name = str(kw.get("name") or "?")
    if callable(handler):
        kw["handler"] = tool_ledger.instrument(name, handler)
    reg.register(**kw)



# ═══════════════════════════════════════════════════════════════
# Schema（OpenAI 兼容，单一真源）—— 从 v0.1 移植
# [v1.0.21.3.r3 · 2026-08-03 整文件重写] description 全部 msg() 化；
# 构建函数化（模块级 msg() 求值 = 语言固化），按语言缓存。
# ═══════════════════════════════════════════════════════════════

#: [v0.9a B-1/B-2 ④] 派活 = 写一份 Instruction 文件。
#:   六段结构对应 handoff.py 的模板；模型填参数，Harness 填模板。
#:   除 target_id / instruction 外全是可选——**别逼模型为了填格式而编内容**，
#:   编出来的「验收标准」比空着更有害。

#: [v1.0.21.3.r3] schema 构建：description 在构建时经 msg() 按当前语言求值。
#: 语言切换后引擎重建 registry → 重建 schema → 新语言生效。
_schema_cache: dict[str, dict[str, Any]] = {}


def _coordinator_schemas() -> dict[str, Any]:
    """按当前语言返回全部 Coordinator schema（构建时 msg() 求值，按语言缓存）。"""
    lang = runtime_settings.language() or "zh"
    lang = lang if lang in ("zh", "en") else "zh"
    if lang not in _schema_cache:
        _schema_cache[lang] = {
            "propose_agents": _build_propose_agents_params(),
            "propose_next": _build_propose_next_params(),
            "propose_remove": _build_propose_remove_params(),
        }
    return _schema_cache[lang]


def _build_propose_agents_params() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "agents": {
                "type": "array",
                "description": (
                    msg("tools_knowe.py.134") +
                    msg("tools_knowe.py.135")
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string",
                                 "description": msg("tools_knowe.py.136")},
                        "id": {"type": "string",
                               "description": (
                                   msg("tools_knowe.py.137") +
                                   msg("tools_knowe.py.138") +
                                   msg("tools_knowe.py.139")
                               )},
                        "name": {"type": "string",
                                 "description": (
                                     msg("tools_knowe.py.140") +
                                     msg("tools_knowe.py.141") +
                                     msg("tools_knowe.py.142")
                                 )},
                    },
                    "required": ["role"],
                },
            },
        },
        "required": ["agents"],
    }


def _build_propose_next_params() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "target_id": {"type": "string",
                          "description": msg("tools_knowe.py.143")},
            "instruction": {"type": "string",
                            "description": msg("tools_knowe.py.144")},
            "keyword": {"type": "string",
                        "description": msg("tools_knowe.py.145")},
            "phase": {"type": "string",
                      "description": msg("tools_knowe.py.146")},
            "background": {"type": "string", "description": msg("tools_knowe.py.147")},
            "previous": {"type": "string", "description": msg("tools_knowe.py.148")},
            "inputs": {"type": "string", "description": msg("tools_knowe.py.149")},
            "acceptance": {"type": "string", "description": msg("tools_knowe.py.150")},
            "notes": {"type": "string", "description": msg("tools_knowe.py.151")},
            # ★ [v0.28] note —— **你就这次派活对用户说话的唯一出口**。
            #
            #   v0.27 我在 FIX_NOTES 的边界 4 里提过这个方案，当时没做（要改前端）。这次做了。
            #
            #   为什么它是「把嘴焊在手上」的字面实现：
            #     以前「想对用户说一句关于这次派活的话」有两条路——写进回复正文（便宜、
            #     不需要工具语法），或者调工具。模型永远走便宜那条，于是说了却没调。
            #     现在只剩一条：**要说，就得穿过这个工具调用。**
            #     说和做不再是两条平行的路，而是**同一个动作的两半**——手不动，嘴就出不了声。
            #
            #   note 会随审批卡保存，适合承载与本次派活直接相关、用户需要在批准前看到的信息。
            #   普通回复正文不再经过语义删句；这里不宣称自己是唯一表达通道。
            "note": {
                "type": "string",
                "description": (
                    msg("tools_knowe.py.152")
                ),
            },
        },
        "required": ["target_id", "instruction"],
    }


def _build_propose_remove_params() -> dict[str, Any]:
    #: [v0.9b] 减人 = 归档。彻底删除**不在这里**（将来走「联系人」）。
    return {
        "type": "object",
        "properties": {
            "target_id": {"type": "string",
                          "description": msg("tools_knowe.py.153")},
            "reason": {"type": "string",
                       "description": msg("tools_knowe.py.154")},
        },
        "required": ["target_id"],
    }


# 沙箱
# ═══════════════════════════════════════════════════════════════

#: 一眼就该拒的路径前缀
_FORBIDDEN_PREFIXES = ("/etc", "/usr", "/bin", "/sbin", "/var", "/root",
                       "/proc", "/sys", "C:\\Windows", "C:\\Program")

# 目录外只读工具仍要避开伪文件系统/设备目录；它们可能无限阻塞、暴露内核接口，
# 不属于“调研项目资料”的正常范围。普通用户文件夹不受影响。
_EXTERNAL_READ_FORBIDDEN = ("/proc", "/sys", "/dev", "C:\\Windows\\System32")
_EXTERNAL_LIST_LIMIT = 500


def resolve_in_sandbox(root: Path, rel: str, role: str = "worker",
                       operation: str = "read") -> Path:
    """Resolve a user-business path and reject traversal or legacy internal folders.

    v0.16 physically moved handoffs, Agent Profiles and Project Memory out of ``root``.
    The old names remain reserved as a defence-in-depth and migration boundary: a partially
    migrated legacy folder must never become visible through generic Worker tools.
    """
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError(msg("tools_knowe.py.037"))

    raw = rel.strip()
    if raw.startswith(("/", "\\")) or (len(raw) > 1 and raw[1] == ":"):
        raise ValueError(msg("tools_knowe.py.038", raw=raw))
    for bad in _FORBIDDEN_PREFIXES:
        if raw.startswith(bad):
            raise ValueError(msg("tools_knowe.py.039", raw=raw))

    root = root.resolve()
    target = (root / raw).resolve()
    if root != target and root not in target.parents:
        raise ValueError(msg("tools_knowe.py.040", raw=raw))

    for reserved in _LEGACY_INTERNAL_DIRS:
        if _under(target, root / reserved):
            raise ValueError(
                msg("tools_knowe.py.041", reserved=reserved)
            )
    return target


#: v0.16 migration boundary: these names are no longer active storage inside workspace_root.
_LEGACY_INTERNAL_DIRS = ("handoffs", ".project", ".agents")


def _under(path: Path, base: Path) -> bool:
    """path 是不是 base 本身或它底下（纯路径判断，不碰文件系统）。"""
    try:
        return path == base or base in path.parents
    except Exception:
        return False


def _internal_storage_root(engine: "ProjectEngine") -> Path:
    """Return the explicit complete backend-internal denial boundary."""
    root = getattr(engine, "backend_data_root", None)
    if root is not None:
        return Path(root).resolve()
    # Small test/third-party adapters may implement only the historical Engine protocol.
    # Production ProjectEngine always exposes backend_data_root.
    leaf = Path(engine.internal_workspace).resolve()
    return leaf.parent


def _is_internal_storage_path(engine: "ProjectEngine", path: Path) -> bool:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        resolved = path.absolute()
    return _under(resolved, _internal_storage_root(engine))


def _resolve_external_read(raw: Any) -> Path:
    """解析目录外只读路径：必须是绝对路径，不创建、不修改源文件。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(msg("tools_knowe.py.168"))
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError(msg("tools_knowe.py.169"))
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError(msg("tools_knowe.py.323", raw=raw)) from None
    norm = str(resolved).replace("\\", "/").casefold()
    if any(norm == bad.replace("\\", "/").casefold()
           or norm.startswith(bad.replace("\\", "/").rstrip("/").casefold() + "/")
           for bad in _EXTERNAL_READ_FORBIDDEN):
        raise ValueError(msg("tools_knowe.py.322", raw=raw))
    return resolved


def _register_external_readonly(
    reg: ToolRegistry, engine: "ProjectEngine", role: str,
) -> None:
    """项目目录外只允许读取/列目录；所有工具都不修改源路径。"""

    def outside_project(path: Path) -> str | None:
        try:
            root = engine.workspace_root.resolve()
        except Exception as exc:
            return msg("tools_knowe.py.042", exc=exc)
        if _is_internal_storage_path(engine, path):
            return msg("tools_knowe.py.043")
        if _under(path, root):
            if role == "worker":
                return msg("tools_knowe.py.044")
            return (msg("tools_knowe.py.045") +
                    msg("tools_knowe.py.046"))
        return None

    def page_number(args: Mapping[str, Any], key: str, default: int, *, minimum: int,
                    maximum: int | None = None) -> int:
        raw = args.get(key)
        try:
            value = int(raw) if raw not in (None, "") else default
        except (TypeError, ValueError):
            value = default
        value = max(minimum, value)
        return min(maximum, value) if maximum is not None else value

    async def handle_read_external(args: dict[str, Any], **kw: Any) -> str:
        del kw
        try:
            path = _resolve_external_read(args.get("path"))
        except ValueError as exc:
            return _err(str(exc))
        denied = outside_project(path)
        if denied:
            return _err(denied)
        if not path.is_file():
            return _err(msg("tools_knowe.py.047", path=path))

        offset = page_number(args, "offset", 0, minimum=0)
        limit = page_number(args, "limit", 200, minimum=1, maximum=500)

        def read_page() -> tuple[list[str], bool]:
            rows: list[str] = []
            with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                for index, line in enumerate(handle):
                    if index < offset:
                        continue
                    if len(rows) >= limit:
                        return rows, True
                    rows.append(line.rstrip("\r\n"))
            return rows, False

        try:
            rows, truncated = await asyncio.to_thread(read_page)
            size = path.stat().st_size
        except OSError as exc:
            return _err(msg("tools_knowe.py.048", exc=exc))
        next_offset = offset + len(rows)
        payload: dict[str, Any] = {
            "path": str(path),
            "content": "\n".join(rows),
            "offset": offset,
            "limit": limit,
            "offset_unit": "lines",
            "returned_lines": len(rows),
            "byte_size": size,
            "truncated": truncated,
            "source_ref": f"external://{path}#lines={offset + 1}-{next_offset}",
        }
        if truncated:
            payload["next_offset"] = next_offset
            payload["continuation"] = _continuation(
                "read_external_file", path=str(path), offset=next_offset, limit=limit,
            )
        return _ok(**payload)

    _register(
        reg,
        name="read_external_file",
        description=(
            msg("tools_knowe.py.049") +
            msg("tools_knowe.py.050") +
            msg("tools_knowe.py.051")
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": msg("tools_knowe.py.052")},
                "offset": {"type": "integer", "minimum": 0, "description": msg("tools_knowe.py.053")},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["path"],
        },
        handler=handle_read_external,
    )

    async def handle_list_external(args: dict[str, Any], **kw: Any) -> str:
        del kw
        try:
            path = _resolve_external_read(args.get("path"))
        except ValueError as exc:
            return _err(str(exc))
        denied = outside_project(path)
        if denied:
            return _err(denied)
        if not path.is_dir():
            return _err(msg("tools_knowe.py.054", path=path))
        offset = page_number(args, "offset", 0, minimum=0)
        limit = page_number(args, "limit", 100, minimum=1, maximum=_EXTERNAL_LIST_LIMIT)
        try:
            children = sorted(path.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            return _err(msg("tools_knowe.py.055", exc=exc))

        rows: list[dict[str, Any]] = []
        for child in children:
            try:
                resolved_child = child.resolve()
            except (OSError, RuntimeError):
                resolved_child = child
            if _is_internal_storage_path(engine, resolved_child):
                continue
            try:
                kind = "dir" if child.is_dir() else "file" if child.is_file() else "other"
                size = child.stat().st_size if kind == "file" else None
            except OSError:
                kind, size = "unreadable", None
            row: dict[str, Any] = {"name": child.name, "type": kind, "path": str(child)}
            if isinstance(size, int):
                row["size"] = size
            rows.append(row)

        page = rows[offset: offset + limit]
        next_offset = offset + len(page)
        truncated = next_offset < len(rows)
        payload: dict[str, Any] = {
            "path": str(path), "entries": page, "count": len(page),
            "total_entries": len(rows), "offset": offset, "limit": limit,
            "truncated": truncated,
            "source_ref": f"external://{path}#entries={offset}-{max(offset, next_offset - 1)}",
        }
        if truncated:
            payload["next_offset"] = next_offset
            payload["continuation"] = _continuation(
                "list_external_dir", path=str(path), offset=next_offset, limit=limit,
            )
        return _ok(**payload)

    _register(
        reg,
        name="list_external_dir",
        description=(
            msg("tools_knowe.py.056")
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": msg("tools_knowe.py.057")},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["path"],
        },
        handler=handle_list_external,
    )


def _register_project_memory_readonly(
    reg: ToolRegistry, engine: "ProjectEngine", role: str,
) -> None:
    """逐回合 Project Memory 检索：主管和成员均可用，只读、不调用 LLM。"""

    async def handle_search(args: dict[str, Any], **kw: Any) -> str:
        del kw
        raw_query = args.get("query", "")
        if raw_query is None:
            raw_query = ""
        if not isinstance(raw_query, str):
            return _err(msg("tools_knowe.py.172"))

        def optional_text(name: str) -> str | None:
            value = args.get(name)
            if value is None or value == "":
                return None
            if not isinstance(value, str):
                raise ValueError(msg("tools_knowe.py.328", name=name))
            return value.strip() or None

        try:
            start_time = optional_text("start_time")
            end_time = optional_text("end_time")
            raw_limit = args.get("limit", 12)
            limit = min(50, max(1, int(raw_limit)))
            order = str(args.get("order", "newest") or "newest").strip().lower()
            if order not in {"newest", "oldest"}:
                return _err(msg("memory_manager.py.029"))
            cursor = optional_text("cursor")
            result = engine.search_project_memory(
                raw_query.strip(), start_time=start_time, end_time=end_time,
                limit=limit, order=order, cursor=cursor,
            )
        except (TypeError, ValueError) as exc:
            return _err(str(exc))
        except Exception:
            log.exception("Project Memory 检索失败")
            return _err(msg("tools_knowe.py.174"))
        return _ok(**result)

    perspective = (
        msg("tools_knowe.py.175")
        if role == "coordinator" else
        msg("tools_knowe.py.176")
    )
    _register(
        reg,
        name="search_project_memory",
        description=(
            perspective
            + msg("tools_knowe.py.177") +
            msg("tools_knowe.py.178") +
            msg("tools_knowe.py.179") + " " +
            msg("tools_knowe.py.180") +
            msg("tools_knowe.py.181") +
            msg("tools_knowe.py.182")
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": msg("tools_knowe.py.183"),
                },
                "start_time": {
                    "type": "string",
                    "description": msg("tools_knowe.py.184"),
                },
                "end_time": {
                    "type": "string",
                    "description": msg("tools_knowe.py.185"),
                },
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 50,
                    "description": msg("tools_knowe.py.186"),
                },
                "order": {
                    "type": "string", "enum": ["newest", "oldest"],
                    "description": msg("tools_knowe.py.187"),
                },
                "cursor": {
                    "type": "string",
                    "description": msg("tools_knowe.py.188"),
                },
            },
        },
        handler=handle_search,
    )

    async def handle_read(args: dict[str, Any], **kw: Any) -> str:
        del kw
        reference = args.get("reference")
        if isinstance(reference, int):
            clean_reference: str | int = reference
        elif isinstance(reference, str) and reference.strip():
            clean_reference = reference.strip()
        else:
            return _err(msg("tools_knowe.py.189"))
        try:
            return _ok(**engine.read_project_memory(clean_reference))
        except (TypeError, ValueError) as exc:
            return _err(str(exc))
        except Exception:
            log.exception("读取 Project Memory 记录失败")
            return _err(msg("tools_knowe.py.190"))

    _register(
        reg,
        name="read_project_memory",
        description=(
            msg("tools_knowe.py.191") +
            msg("tools_knowe.py.192") +
            msg("tools_knowe.py.193")
        ),
        parameters={
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": msg("tools_knowe.py.194"),
                },
            },
            "required": ["reference"],
        },
        handler=handle_read,
    )


def _register_agent_memory_readonly(
    reg: ToolRegistry, engine: "ProjectEngine", agent_id: str,
) -> None:
    """Worker 专属：闭包绑定当前 agent_id，只读搜索它自己的 worklog。"""

    async def handle_search(args: dict[str, Any], **kw: Any) -> str:
        del kw
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return _err(msg("tools_knowe.py.058"))
        try:
            raw_limit = args.get("limit", 12)
            limit = min(50, max(1, int(raw_limit)))
            order = str(args.get("order", "newest") or "newest").strip().lower()
            if order not in {"newest", "oldest"}:
                return _err(msg("tools_knowe.py.059"))
            # agent_id 只来自注册 Worker 工具箱时的闭包；schema 不接受“搜索谁”。
            result = engine.search_agent_memory(
                agent_id, query.strip(), limit=limit, order=order,
            )
        except (TypeError, ValueError) as exc:
            return _err(str(exc))
        except Exception:
            log.exception(msg("tools_knowe.py.060"), agent_id)
            return _err(msg("tools_knowe.py.061"))
        return _ok(**result)

    _register(
        reg,
        name="search_agent_memory",
        description=(
            msg("tools_knowe.py.062") +
            msg("tools_knowe.py.063") +
            msg("tools_knowe.py.064") +
            msg("tools_knowe.py.065") +
            msg("tools_knowe.py.066")
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": msg("tools_knowe.py.067"),
                },
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 50,
                    "description": msg("tools_knowe.py.068"),
                },
                "order": {
                    "type": "string", "enum": ["newest", "oldest"],
                    "description": msg("tools_knowe.py.069"),
                },
            },
            "required": ["query"],
        },
        handler=handle_search,
    )


def _register_knowledge_readonly(
    reg: ToolRegistry, engine: "ProjectEngine", role: str,
) -> None:
    """Project graph retrieval shared by Coordinator and Workers (read-only, precomputed)."""
    async def handle_search(args: dict[str, Any], **kw: Any) -> str:
        del kw
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return _err(msg("tools_knowe.py.195"))
        raw_limit = args.get("limit", 6)
        try:
            limit = min(20, max(1, int(raw_limit)))
        except (TypeError, ValueError):
            limit = 6
        include_contested = args.get("include_contested", True)
        result = engine.search_project_knowledge(
            query.strip(), limit=limit,
            include_contested=bool(include_contested),
        )
        return _ok(**result)

    # [v0.42] 描述改写降位（报告 §4.4）：情节层是**考古工具**——查「历史发生过什么」；
    #   查「怎么做事」先看系统提示里的知识索引 / 指令里的「相关知识」，
    #   用 read_knowledge_asset 展开。
    perspective = (
        msg("tools_knowe.py.196")
        if role == "coordinator" else
        msg("tools_knowe.py.197")
    )
    _register(
        reg,
        name="search_project_knowledge",
        description=(
            perspective
            + msg("tools_knowe.py.198") +
            msg("tools_knowe.py.199") +
            msg("tools_knowe.py.200") +
            msg("tools_knowe.py.201")
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": msg("tools_knowe.py.202"),
                },
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 20,
                    "description": msg("tools_knowe.py.203"),
                },
                "include_contested": {
                    "type": "boolean",
                    "description": msg("tools_knowe.py.204"),
                },
            },
            "required": ["query"],
        },
        handler=handle_search,
    )

    async def handle_read(args: dict[str, Any], **kw: Any) -> str:
        del kw
        reference = args.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            return _err(msg("tools_knowe.py.205"))
        return _ok(**engine.read_project_knowledge(reference.strip()))

    _register(
        reg,
        name="read_project_knowledge",
        description=(
            msg("tools_knowe.py.206") +
            msg("tools_knowe.py.207") +
            msg("tools_knowe.py.208")
        ),
        parameters={
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": msg("tools_knowe.py.209"),
                },
            },
            "required": ["reference"],
        },
        handler=handle_read,
    )

    # ═══ [v0.42] read_knowledge_asset —— 三级渐进披露的 L1 入口 ═══
    #
    #   L0（一行索引）常驻系统提示与指令「相关知识」区块 → 认为相关才用本工具展开
    #   L1（ASSET.md 全文，条件-行动结构）→ 需要原始现场再用正文里的 evidence 指针
    #   走 read_project_knowledge 深钻 L2。披露深度由需求驱动，不由排名驱动。
    async def handle_read_asset(args: dict[str, Any], **kw: Any) -> str:
        del kw
        asset_id = args.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            return _err(msg("tools_knowe.py.210"))
        return _ok(**engine.read_knowledge_asset(asset_id.strip()))

    _register(
        reg,
        name="read_knowledge_asset",
        description=(
            msg("tools_knowe.py.211") +
            msg("tools_knowe.py.212") +
            msg("tools_knowe.py.213") +
            " " + msg("tools_knowe.py.214") +
            msg("tools_knowe.py.215") +
            msg("tools_knowe.py.216")
        ),
        parameters={
            "type": "object",
            "properties": {
                "asset_id": {
                    "type": "string",
                    "description": msg("tools_knowe.py.217"),
                },
            },
            "required": ["asset_id"],
        },
        handler=handle_read_asset,
    )

    # ═══ [v0.43] read_skillpack —— Agent skill 的唯一执行入口 ═══
    # 系统提示只列 active 技能；本工具在 Harness 再做一次 active 门禁。即使模型从
    # 历史里记住了 pending/retired 的 pack_id，也读不到正文，生命周期不会被绕过。
    async def handle_read_skillpack(args: dict[str, Any], **kw: Any) -> str:
        del kw
        pack_id = args.get("pack_id")
        if not isinstance(pack_id, str) or not pack_id.strip():
            return _err(msg("tools_knowe.py.218"))
        return _ok(**engine.read_skillpack(pack_id.strip()))

    _register(
        reg,
        name="read_skillpack",
        description=(
            msg("tools_knowe.py.219") +
            msg("tools_knowe.py.220") +
            msg("tools_knowe.py.221") +
            msg("tools_knowe.py.222")
        ),
        parameters={
            "type": "object",
            "properties": {
                "pack_id": {
                    "type": "string",
                    "description": msg("tools_knowe.py.223"),
                },
            },
            "required": ["pack_id"],
        },
        handler=handle_read_skillpack,
    )


# ═══════════════════════════════════════════════════════════════
# 校验
# ═══════════════════════════════════════════════════════════════


_Handler = Callable[..., Awaitable[Any]]


def _is_workspace_unavailable(exc: BaseException) -> bool:
    """
    engine.WorkspaceUnavailable 要**继续往上抛**——用户把项目目录删了/移走了，
    Harness 必须停下来告诉他，而不是变成一句「工具出错了」被模型自己消化掉。

    这里按类名认，不 import：engine 导入 tools_knowe，反过来再导一次就是循环。
    """
    return type(exc).__name__ == "WorkspaceUnavailable"


def _guarded(name: str) -> Callable[[_Handler], _Handler]:
    """
    §七「所有工具在依赖缺失时优雅降级——不抛异常、不阻塞 Agent 回合」的**唯一执行点**。

    每个新 handler 都套上它，于是：
      · ToolError            → 中文 message 交回模型，它自己改（预见到的失败）
      · CancelledError       → 原样抛（用户撤回合/关机，不是错误）
      · WorkspaceUnavailable → 原样抛（Harness 要停）
      · 其它任何异常          → 记日志 + 一句中文，回合继续（没预见到的失败）

    ★ 为什么不让它崩：一个 Worker 回合里的工具异常会把整个引擎的 turn 带走，
      用户看到的是「转圈转到一半没了」。而模型看到一句「网络超时」，
      下一轮就能换个做法。**能说话的失败，永远好过一个 traceback。**
    """
    def deco(fn: _Handler) -> _Handler:
        @functools.wraps(fn)
        async def inner(args: dict[str, Any], **kw: Any) -> str:
            try:
                data = await fn(args, **kw)
            except ToolError as exc:
                return _err(str(exc))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if _is_workspace_unavailable(exc):
                    raise
                log.exception(msg("tools_knowe.py.080"), name)
                return _err(msg("tools_knowe.py.081", name=name, **{"type(exc).__name__": type(exc).__name__}, exc=exc))
            if data is None:
                return _ok()
            return _ok(**data)
        return inner
    return deco

def _num(args: dict[str, Any], key: str, default: float,
         *, lo: float, hi: float) -> float:
    """
    模型给的数字什么样都有："30"、30.0、None、"about 30"。
    一律不报错——夹到合法区间就好。为一个 timeout 打回去让它重来，不值得。
    """
    raw = args.get(key)
    if raw is None or raw == "":
        return default
    try:
        return max(lo, min(hi, float(raw)))
    except (TypeError, ValueError):
        return default

def _member_lookup_key(value: Any) -> str:
    """名字查找只做确定性规范化（去空白 + casefold），绝不做模糊猜测。"""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", "", value.strip()).casefold()


def _next_agent_id(role: str, occupied: set[str]) -> str:
    """给新成员生成不复用归档/已删除 id 的 `角色前缀_序号`。"""
    prefix = _ROLE_PREFIX_BY_LABEL[role]
    seq = 1
    while f"{prefix}_{seq}" in occupied:
        seq += 1
    return f"{prefix}_{seq}"


def _parse_agents(raw: Any, engine: "ProjectEngine") -> list[dict[str, str]]:
    """
    校验模型传来的 agents。**最容易被传歪的地方**——传成字符串、少了 role、
    重复的 id……一律不崩溃，抛 ValueError 让 handler 把话说回给模型。
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError(msg("tools_knowe.py.321"))

    members: list[dict[str, str]] = []
    seen: set[str] = set()
    skipped: list[str] = []             # [v0.9c] 已经在队里的（跳过，不报错）
    roster = engine.roster()
    full = engine.stored_roster_full()  # [v0.44.12] 含归档：身份匹配的真源

    # 纯内存模式没有持久花名册；至少把活跃成员补成同一形状，避免按名字重复拉人。
    for aid, active_role in roster.items():
        full.setdefault(aid, {
            "role": active_role,
            "name": engine.member_name(aid),
            "status": "active",
        })

    name_index: dict[str, list[str]] = {}
    for aid, row in full.items():
        # 永久删除只保留技术性 id 墓碑以防复用；旧名字不能再成为恢复入口。
        if row.get("status", "active") == "deleted":
            continue
        key = _member_lookup_key(row.get("name"))
        if key:
            name_index.setdefault(key, []).append(aid)
    occupied = set(full) | set(roster)

    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(msg("tools_knowe.py.082"))
        raw_mid, role = item.get("id"), item.get("role")
        raw_name = item.get("name")
        if raw_mid is not None and not isinstance(raw_mid, str):
            raise ValueError(msg("tools_knowe.py.083"))
        if raw_name is not None and not isinstance(raw_name, str):
            raise ValueError(msg("tools_knowe.py.084"))
        if not isinstance(role, str) or not role:
            raise ValueError(msg("tools_knowe.py.085"))
        role = role.strip()
        mid = raw_mid.strip() if isinstance(raw_mid, str) else ""
        requested_name = raw_name.strip() if isinstance(raw_name, str) else ""
        # [v0.10a Issue 4] ★ 角色必须来自标准库 —— 挡住「Agent」「助手」这类空泛标签。
        #   打回去让项目经理改，比放一个前端认不出的角色进来、最后显示成「Agent」强。
        if role not in _KNOWN_ROLE_LABELS:
            raise ValueError(
                msg("tools_knowe.py.086", role=role) +
                f"{'、'.join(KNOWN_ROLES.values())}"
            )
        # ── [v0.44.12] 身份解析：原 id > 精确名字 > 新 id ──
        #
        # 关键点：名字只拿来**查花名册**，不能直接变成一个新 id。
        # 「陆上初」命中 pm_1(status=removed) → 强制改用 pm_1；
        # 「Casey」查无此人 → 打回，不再制造一个与旧人无关的新 Worker。
        stored_row = full.get(mid) if mid else None
        if stored_row is not None:
            if stored_row.get("status", "active") == "deleted":
                raise ValueError(
                    msg("tools_knowe.py.087", mid=mid) +
                    msg("tools_knowe.py.088")
                )
            canonical_mid = mid
            if requested_name:
                by_requested_name = set(name_index.get(_member_lookup_key(requested_name), []))
                stored_name = str(stored_row.get("name") or "").strip()
                if by_requested_name and by_requested_name != {canonical_mid}:
                    raise ValueError(
                        msg("tools_knowe.py.089", mid=mid, requested_name=requested_name) +
                        msg("tools_knowe.py.090")
                    )
                if stored_name and not by_requested_name \
                        and _member_lookup_key(stored_name) != _member_lookup_key(requested_name):
                    raise ValueError(
                        msg("tools_knowe.py.091", mid=mid, stored_name=stored_name, requested_name=requested_name)
                    )
        else:
            matched_ids: set[str] = set()
            # name 是明确查找提示；同时兼容模型把「陆上初」误填进 id 的旧写法。
            for probe in (requested_name, mid):
                key = _member_lookup_key(probe)
                if key:
                    matched_ids.update(name_index.get(key, []))
            if len(matched_ids) > 1:
                labels = "、".join(
                    f"{full[aid].get('name') or aid}（{full[aid].get('role') or msg('tools_knowe.py.092')}）"
                    for aid in sorted(matched_ids)
                )
                raise ValueError(msg("tools_knowe.py.093", labels=labels))
            if matched_ids:
                canonical_mid = next(iter(matched_ids))
                stored_row = full[canonical_mid]
            elif requested_name:
                raise ValueError(
                    msg("tools_knowe.py.094", requested_name=requested_name) +
                    msg("tools_knowe.py.095")
                )
            elif not mid:
                canonical_mid = _next_agent_id(role, occupied | seen)
                stored_row = None
            else:
                match = _AGENT_ID_RE.fullmatch(mid)
                if match is None:
                    raise ValueError(
                        msg("tools_knowe.py.096", mid=mid) +
                        msg("tools_knowe.py.097")
                    )
                expected_prefix = _ROLE_PREFIX_BY_LABEL[role]
                if match.group("prefix") != expected_prefix:
                    raise ValueError(
                        msg("tools_knowe.py.098", mid=mid, expected_prefix=expected_prefix, role=role) +
                        msg("tools_knowe.py.099")
                    )
                canonical_mid = mid
                stored_row = None

        mid = canonical_mid
        if stored_row is not None:
            stored_role = str(stored_row.get("role") or "").strip()
            if stored_role and stored_role != role:
                stored_name = str(stored_row.get("name") or mid)
                raise ValueError(
                    msg("tools_knowe.py.100", stored_name=stored_name, stored_role=stored_role, role=role) +
                    msg("tools_knowe.py.101")
                )

        if mid == COORDINATOR:
            raise ValueError(msg("tools_knowe.py.102"))
        if mid in seen:
            continue                    # 模型偶尔把同一个人报两遍，去重就是了
        if mid in roster:
            # [v0.9c] ★ 已经在队里的人 → **跳过**，不要整单报错。
            #   v0.9b 我们告诉模型「加人是增量的」，而模型加人时的常见写法是
            #   **把现有成员连同新人一起报一遍**（它以为要给出完整名单）。
            #   老代码在这里 raise ValueError → 整个 propose_agents 失败 →
            #   用户说「再加个后端」，得到的却是一句「fe_1 已经在团队里了」。
            #   规矩改成：老人跳过，只提新人。一个新人都没有才报错。
            skipped.append(f"{engine.member_name(mid)}（{roster[mid]}）")
            continue
        seen.add(mid)
        occupied.add(mid)
        members.append({
            "id": mid,
            "role": role,
            # ★ [v0.9d] 名字**问引擎要**（reserve_name）：
            #     · 花名册里有他（哪怕是**归档**的）→ 旧名原样拿回来 —— 他回来了，他还是他
            #     · 没有 → 掷一个（中英各一半，同项目内不重名），先记在内存里
            #   为什么在这儿就定：审批卡上要显示他将来的名字。
            #   卡上写「前端 1」、进队之后变成「林知远」，用户会以为来的是另一个人。
            #   （用户按了拒绝 → 这名字就是内存里一条没人认领的记录，无害。）
            "name": engine.reserve_name(mid, role),
        })

    if not members:
        if skipped:
            listed = "、".join(skipped)
            raise ValueError(
                msg("tools_knowe.py.103", listed=listed) +
                msg("tools_knowe.py.104")
            )
        raise ValueError(msg("tools_knowe.py.105"))

    if skipped:
        log.info(msg("tools_knowe.py.106"), "、".join(skipped))
    return members


def _resolve_target(target: Any, engine: "ProjectEngine") -> tuple[str | None, str | None]:
    """
    [v0.10a Issue 1 配套] 把项目经理给的「目标」解析成花名册里的 agent_id。

    自从【当前团队】名单不再摆 id（红线修复），项目经理眼前只有名字，
    于是它很自然会拿**名字**当 target 传进来。为了不让这变成一句
    「林知远 不在团队里」的冤枉话，这里名字、id 都认：

      ① target 本身就是活跃成员的 id（fe_1）→ 直接用（向后兼容，也含「加回归档成员后再派活」）
      ② target 精确等于某个活跃成员的名字（林知远）→ 换成他的 id
      ③ 都不是 → 返回一句人话，把现有成员按**名字**列出来（绝不列 id）

    返回 (agent_id, error)：成功 → (id, None)；失败 → (None, 错误话)。
    """
    roster = engine.roster()                        # {agent_id: role}，只含活跃成员
    if not roster:
        return None, msg("tools_knowe.py.107")

    if not isinstance(target, str) or not target.strip():
        return None, msg("tools_knowe.py.108")
    t = target.strip()

    # ① 直接就是 id
    if t in roster:
        return t, None

    # ② 按名字精确匹配（名字在项目内唯一，所以不会有歧义）
    by_name = [aid for aid in roster if engine.member_name(aid) == t]
    if len(by_name) == 1:
        return by_name[0], None
    if len(by_name) > 1:                             # 理论上不会发生，真发生了也别瞎猜
        names = "、".join(engine.member_name(a) for a in by_name)
        return None, msg("tools_knowe.py.109", t=t, names=names)

    # ③ 对不上 → 把现有成员用名字列出来（不列 id）
    known = "、".join(
        f"{engine.member_name(aid)}（{role}）" for aid, role in roster.items()
    ) or msg("tools_knowe.py.110")
    return None, msg("tools_knowe.py.111", t=t, known=known)


def _zh(decision: str) -> str:
    return {"rejected": msg("tk.112a"), "timeout": msg("tk.112b"),
            "cancelled": msg("tools_knowe.py.113")}.get(decision, decision)

# ═══════════════════════════════════════════════════════════════
# 注册表工厂 —— 每个引擎现建，handler 用闭包绑住引擎
# ═══════════════════════════════════════════════════════════════

def build_coordinator_registry(engine: "ProjectEngine") -> ToolRegistry:
    """项目经理的工具箱：只能提议和读，不能自己动手。"""
    reg = ToolRegistry()


    # ── propose_agents（gated）──
    async def handle_propose_agents(args: dict[str, Any], **kw: Any) -> str:
        agent_id = kw.get("agent_id", COORDINATOR)
        raw = args.get("agents")

        try:
            members = _parse_agents(raw, engine)
        except ValueError as e:
            return _err(str(e))          # 参数传歪了 → 告诉模型，别弹卡

        decision = await engine.gate.propose(          # ★ 弹卡，等人点头
            tool="propose_agents",
            agent_id=agent_id,
            card_body={"proposed": members},
        )
        if decision != "approved":
            # ═══ [v0.33 Bug1b] 拒绝 ≠ 静默：把「这些人没进队」广播出去 ═══
            #
            #   复现现场：卡被拒后，Jesse 和霍琅朴还赖在前端的花名册面板里——
            #   后端这边其实从没把他们进册（add_member 只在 approved 之后跑），
            #   但审批卡事件里带着他们的 {id, role, name}，前端为了渲染头像会
            #   先把人注册上；而「没通过」这件事，过去**没有任何事件说出口**。
            #   现在补上 agents_rejected（已入 contract.EVENT_SPEC）：明确列出
            #   这批 id，前端照单撤掉乐观注册的鬼影。名字保留在内存缓存里是
            #   故意的——同一个 id 下次再被提议，卡上还是同一个名字。
            await engine.emit({
                "type": "agents_rejected",
                "agent_id": agent_id,
                "decision": str(decision),
                "members": [dict(m) for m in members],
            })
            await engine.on_proposal_rejected(decision, msg("tools_knowe.py.235"))
            return _err(msg("tools_knowe.py.324", **{"_zh(decision)": _zh(decision)}), status=decision)

        for m in members:
            # [v0.9d] 名字跟着人一起进队 —— 花名册里从此有他的名字（旧名 or 新掷的）。
            #   归档的人被加回来走的也是这条路：reserve_name 已经把旧名捞回来了，
            #   add_member → _persist_member → upsert_agent 会把 status 翻回 active。
            engine.add_member(m["id"], m["role"], m.get("name"))

        await engine.emit({
            "type": "agents_created",
            "agent_id": agent_id,
            "count": len(members),
            "members": members,        # [v0.9c] 每一项现在是 {id, role, name}
        })
        # [v1.0.23.3] 初入群打招呼：审批通过的新 worker 也走欢迎机制，在群里
        #   说一句话。welcome_worker 内部 fire-and-forget + 失败吞异常，不阻塞拉人。
        for m in members:
            try:
                await engine.welcome_worker(m["id"])
            except Exception:
                log.exception("[%s] 初入群打招呼触发失败 %s", engine.project_id, m.get("id"))
        engine.record_committed_action("propose_agents")
        # [v0.10a Issue 1] 回话只列名字·角色，不带 id —— 这条会进项目经理上下文，别让它顺手念给用户。
        listed = "、".join(f"{m['name']}（{m['role']}）" for m in members)
        return _ok(
            agents_created=len(members),
            message=msg("tools_knowe.py.325", listed=listed),
        )

    _register(
        reg,
        name="propose_agents",
        description=(
            msg("tools_knowe.py.240") +
            msg("tools_knowe.py.241") +
            msg("tools_knowe.py.242") +
            msg("tools_knowe.py.243") +
            msg("tools_knowe.py.244") +
            msg("tools_knowe.py.245") +
            msg("tools_knowe.py.246") +
            msg("tools_knowe.py.247") +
            msg("tools_knowe.py.248") +
            msg("tools_knowe.py.249") +
            msg("tools_knowe.py.250") + "\n" +
            "\n" + msg("tools_knowe.py.251") +
            msg("tools_knowe.py.252") +
            msg("tools_knowe.py.253") + "\n" +
            msg("tools_knowe.py.254") +
            msg("tools_knowe.py.255") + "\n"
            + roles.catalog_for_tool() + "\n" +
            msg("tools_knowe.py.256")
        ),
        parameters=_coordinator_schemas()["propose_agents"],
        handler=handle_propose_agents,
        requires_approval=True,
    )

    # ── propose_next（gated）── [v0.9a B-2 ④] Harness 在这里拦一道
    async def handle_propose_next(args: dict[str, Any], **kw: Any) -> str:
        agent_id = kw.get("agent_id", COORDINATOR)
        instruction = args.get("instruction")

        # ★ [v0.24 问题二] 用户刚按了「拒绝」，收口那一轮**不许再提案**。
        #
        #   REJECTION_FOLLOWUP 里本来就写着「不要重新提案」—— 而线上照样把
        #   用户刚拒绝的那张卡原样又弹了一次。因为另一段 prompt（v0.22 的纠正器）
        #   正朝反方向喊「立刻调用 propose_next」，而模型听了后者。
        #   **两段 prompt 打架的时候，谁也不知道哪段会赢。所以这一道用代码。**
        #
        #   回执写成 _ok 而不是 _err：这不是它做错了什么，是此刻不该做——
        #   报错会让它以为要重试。这里直接告诉它下一步该干嘛。
        #   用户下一次开口（engine.submit）自动解冻，他改主意了照样派得动。
        if engine.dispatch_frozen():
            return _ok(
                dispatched=False,
                message=(
                    msg("tools_knowe.py.257") +
                    msg("tools_knowe.py.258") + "\n" +
                    msg("tools_knowe.py.259") +
                    msg("tools_knowe.py.260")
                ),
            )

        if not isinstance(instruction, str) or not instruction:
            return _err(msg("deepseek.py.032"))

        # [v0.28] 卡上那句话。空/超长都当没有——卡是让人扫一眼就点的，不是读文章的地方。
        note = args.get("note")
        note = note.strip() if isinstance(note, str) else ""
        if len(note) > 300:
            note = note[:300] + "…"

        # [v0.10a Issue 1] target 支持名字或 id —— 名单里已经没有 id 了，项目经理多半填名字。
        target_id, err = _resolve_target(args.get("target_id"), engine)
        if err:
            return _err(err)
        assert target_id is not None

        # ═══ [v0.30 Bug3] ★ 同一时刻只许有一张派活卡在等审批 ═══
        #
        #   v0.29 的雪崩现场里有一幕：屏幕上同时挂着两张一模一样的派活卡。
        #   结构上，两张 propose_next 卡并存本不该发生（项目经理回合在主循环里
        #   串行，提案时它卡在闸门上）——可它发生了：崩溃恢复的复提
        #   （engine.recover 是 create_task，游离在回合之外）、以及纠正器
        #   曾经的「立刻调用 propose_next」都能在旧卡未落定时再弹一张。
        #   用户对着两张同卡，点哪张都是坑：点 A，B 还挂着倒计时；两张都点，
        #   同一个人被派两次，旧可变注入状态会覆盖第一件活。
        #
        #   所以在**入口**焊死：桌上还有一张派活卡没落定 → 这一张不弹。
        #   回执用 _ok 不用 _err（老规矩：报错=教它重试，而重试还是会被挡）。
        if engine.gate.has_pending_tool("propose_next"):
            log.info("[%s] 拦下一次并发派活提案：已有派活卡在等审批", engine.project_id)
            return _ok(
                dispatched=False,
                message=(
                    msg("tools_knowe.py.261") + "\n" +
                    msg("tools_knowe.py.262") +
                    msg("tools_knowe.py.263")
                ),
            )


        # ═══ [v0.29 问题一] ★ 他手上那件活还没干完 → 这张卡**不弹** ═══
        #
        #   用户报的：Worker 一开工，项目经理就开始不正常 —— 其中最刺眼的一种，
        #   是他转头又给同一个人派一件活。用户看到第二张卡，点了确认，
        #   旧实现会用一份可变的模型注入状态覆盖前一件任务，导致队列语义丢失；
        #   现在 Runtime 队列仍在这里拒绝同一成员的重复权威任务。
        #   ——两张卡，一份产出，另一件人间蒸发。**用户永远不会知道它去哪了。**
        #
        #   为什么是代码而不是 prompt：项目经理的上下文里**早就有**【此刻的工作状态】那一块
        #   （v0.22 加的，每轮都在说「谁正在执行任务」），他照样派。这个代码库的
        #   每一版都在重新学同一件事：**能用代码保证的事，不要用祈使句去求。**
        #
        #   为什么放在 `_resolve_target` 之后：得先知道「他说的这个名字是谁」，
        #   才谈得上「这个人忙不忙」。放前面的话，项目经理填个昵称就绕过去了。
        #
        #   ★ 只挡这一种。给**别人**派活、项目经理回话、聊天，一切照常
        #     （真正让项目「冻住」的不是这道闸，是主循环——见 engine._spawn_pending_workers）。
        if engine.worker_is_busy(target_id):
            who = engine.member_name(target_id)
            log.info("[%s] 拦下一次重复派活：%s 手上还有活", engine.project_id, target_id)
            # _ok 不是 _err：他没做错什么，只是此刻不该做。报错会让模型以为要重试，
            # 而重试一次还是会被挡 —— 白烧一轮 token，用户白等一轮（v0.24 的教训）。
            return _ok(
                dispatched=False,
                busy=True,
                target_name=who,
                message=WORKER_BUSY_DISPATCH.format(who=who),
            )

        # ★ 卡上仍然只有 {target_id, instruction, note?} —— 契约白名单认得这三样。
        #   六段结构是**磁盘上的事**，不是屏幕上的事：用户在卡上要看的是「派谁、干什么」，
        #   不是六个小标题。
        # [v0.30 Bug2/3] v0.25 的「意见重发闸」（instruction_repeat_after_feedback /
        #   remember_pending_instruction）随老路一并拆除：意见如今走控制面
        #   adjust_instruction 原地改卡，项目经理从头到尾不经手意见文本，
        #   「原样重发」这个失败模式在结构上已经不存在了。

        # ★ [v0.26] card_out：卡在等审批期间可能被**原地改过**（用户点了「我有新意见」）。
        #   不接这条回程的话，卡面显示新指令、派下去的却还是旧的 —— 用户改了个寂寞，
        #   而且这个 bug 极安静：卡是对的，他点确认，然后 Worker 干了件旧活。
        final_card: dict[str, Any] = {}
        decision = await engine.gate.propose(          # 弹卡，等人点头
            tool="propose_next",
            agent_id=agent_id,
            # ★ [v0.28] note 跟着卡走。card 在契约里就是 dict —— **契约一个字不用改**。
            card_body={"target_id": target_id, "instruction": instruction,
                       **({"note": note} if note else {})},
            card_out=final_card,
        )
        # 用户批的是**卡上那份**，不是我手里这份。以卡为准。
        instruction = str(final_card.get("instruction") or instruction)

        # 审批卡可能挂几分钟。卡弹出时空闲，不代表用户点「批准」时仍空闲；
        # 这期间恢复任务、私聊任务或另一条控制路径都可能占用同一个 Worker。
        # commit_handoff_step 是同步函数，因此在这里重新检查后，到 Runtime 入队之间
        # 没有 await 竞态窗口。状态已变化就让本次批准安全失效，绝不覆盖旧任务。
        if decision == "approved" and engine.worker_is_busy(target_id):
            who = engine.member_name(target_id)
            log.warning(
                "[%s] 用户批准派活时 %s 已被另一任务占用；本次批准不落盘、不入队",
                engine.project_id,
                target_id,
            )
            return _ok(
                dispatched=False,
                busy=True,
                approval_obsolete=True,
                target_name=who,
                message=(
                    msg("tools_knowe.py.332", who=who) +
                    msg("tools_knowe.py.273")
                ),
            )

        # [B-2 ④] 闸门有结果了 → Harness 落文件：
        #   通过 → instruction-NN + .approval-NN + 指令全文进入 Runtime 队列
        #   没通过 → 只留 .approval-NN（记一笔「用户没同意」），不写指令文件
        step_info = engine.commit_handoff_step(
            target_id=target_id,
            instruction=instruction,
            decision=decision,
            keyword=str(args.get("keyword") or ""),
            phase=str(args.get("phase") or ""),
            background=str(args.get("background") or ""),
            previous=str(args.get("previous") or ""),
            inputs=str(args.get("inputs") or ""),
            acceptance=str(args.get("acceptance") or ""),
            notes=str(args.get("notes") or ""),
        )
        if decision != "approved":
            await engine.on_proposal_rejected(decision, msg("tools_knowe.py.274"))
            return _err(msg("tools_knowe.py.326", **{"_zh(decision)": _zh(decision)}), status=decision)

        # ★ Runtime 任务已在 commit_handoff_step 里入队（保存指令全文）。
        #   这里只同步“踢一下”后台调度，不 await Worker 回合；因此项目经理不会等到
        #   Worker 干完。这样启动不再依赖项目经理后续旁白/历史处理成功返回。
        engine.record_committed_action("propose_next")
        engine.record_dispatch(target_id)   # [v0.28] 告诉引擎卡上写的是谁
        try:
            engine.start_committed_workers()
        except Exception:
            # _start_worker_turn 会把出队项完整回滚；_harness_turn.finally 还会再试。
            # 这里不能把已成功提交的工具调用反报成失败，否则项目经理会重复派同一件活。
            log.exception(
                "[%s] Worker 即时拉起失败；任务仍在队列，回合收尾时重试",
                engine.project_id,
            )
        try:
            await engine.emit({
                "type": "instruction_injected",
                "agent_id": agent_id,
                "target_id": target_id,
            })
        except Exception:
            # 到这里 TaskEnvelope 已经提交，Worker 也已被即时调度。状态广播
            # 只是投影；即使状态投影/前端拒收，也不能反向撤销权威任务。
            log.exception(
                "[%s] instruction_injected 投影失败，但任务已提交并调度",
                engine.project_id,
            )
        # [v0.10a Issue 1] 回话用**名字**，不回显 id / 文件路径 —— 这条消息会进项目经理上下文，
        #   它可能原样复述给用户。target_id / instruction_file 仍留在结构化字段里供内部追溯。
        who = engine.member_name(target_id)

        # [v1.0.24.3] ★ 改卡回执：卡在审批期间被用户【我有新意见】改过。
        #
        #   PM 的上下文里只有它自己最初写的旧指令 —— 用户改了卡它根本不知道，
        #   于是后续所有判断（口头承诺、验收标准、下一张卡）全对着旧指令来。
        #   上一版的教训：光改卡面（card_out 回程）不够，**回执不告诉它 = 它照旧**。
        #
        #   feedback_history 是 adjust_instruction 每轮 append 进卡体的（engine.py），
        #   card_out 回程自然带出；这里只需要拼一段「以卡面最终指令为准」的提示。
        #   未改过的卡没有这个字段 → 回执与 v0.26 之前完全一致（零回归）。
        #   到这里 decision 必为 approved（1537 行已把非 approved 全部 return 走）。
        amended_note = ""
        fh = final_card.get("feedback_history")
        if isinstance(fh, list) and fh:
            history_text = "\n".join(f"- {f}" for f in fh)
            final_instruction = str(instruction)
            if len(final_instruction) > 1500:
                final_instruction = final_instruction[:1500] + msg("tools_knowe.py.338")
            amended_note = (
                msg("tools_knowe.py.335", who=who) + "\n" +
                msg("tools_knowe.py.336", n=len(fh)) + "\n" +
                history_text + "\n" +
                msg("tools_knowe.py.337") + final_instruction
            )
        return _ok(
            target_id=target_id,
            step=step_info.get("step"),
            instruction_file=step_info.get("instruction_path"),
            # ★ [v0.23 问题一] 这一行是「等他交报告我来审阅」的**出处**。
            #
            #   老回执是：「任务已派给 {who}，他马上开始。等他交报告。」
            #   用户实际看到的是：「任务已派给宋陈（后端），他去网上搜图。等他交报告我来审阅。」
            #   —— 几乎是逐字复述。上面那条注释早就写了「它可能原样复述给用户」，
            #   可它复述的偏偏是我们**自己塞进去的黑话**。
            #
            #   工具回执是模型下一句话的原材料。回执里写什么词，它就跟用户说什么词。
            #   （v0.22 的问题一是同一个病：Runtime Delivery 的描述写着「提交给项目经理审阅」，
            #     于是 Worker 张口就是「项目经理会收到报告进行验收」。）
            #
            #   所以回执里**一个黑话都不留**，并且直接告诉它这一句该怎么说。
            # ★ [v0.27] 这一行**曾经是这个 bug 的第四个源头**。
            #
            #   v0.23 我在这里写的是：
            #       「★ 现在跟用户说一句话就够了：**谁去做、做什么**。（例：「宋陈去搜图了」）」
            #
            #   而 coordinator.txt 的铁律②同时禁止「XX 已经去做了」。**那是同一句话。**
            #   区别只在卡弹没弹——而那是关于模型自己有没有调工具的事实，句子表达不了。
            #
            #   工具回执是**模型 composing 最终回复前读到的最后一样东西**（v0.23 学到的）。
            #   我们把那句话放在了最有说服力的位置上，然后花四个版本写检测器去抓它。
            #   **五版没修好，不是它不听话，是我们一直在教它。**
            #
            #   现在这里给的是**一个可执行的动作**（回 NOTHING_TO_ADD），
            #   不是一个要它自己拿捏的分寸。模型对「做 Y」的服从度远高于「别做 X」。
            message=(
                msg("tools_knowe.py.334", who=who) +
                msg("tools_knowe.py.278") + "\n" +
                msg("tools_knowe.py.279") + "\n" +
                "  " + msg("tools_knowe.py.280", who=who) +
                msg("tools_knowe.py.281") +
                msg("tools_knowe.py.282") + "\n" +
                "  " + msg("tools_knowe.py.283") +
                msg("tools_knowe.py.284") +
                amended_note
            ).replace("{who}", who),
        )

    _register(
        reg,
        name="propose_next",
        description=(
            # [v0.22 问题二] 工具描述是模型**决定调不调它**的那一刻读的东西。
            #   把「不调 = 没派活」这句钉在这里，比钉在一万字之外的人设里管用得多。
            # [v0.27] 再加一条：**告诉它卡会自己说话**——
            #   它之所以想在前面补一句「我让 XX 去做」，是因为它以为用户需要被告知。
            #   不需要：卡上有头像、名字、整段指令，比那句话说得清楚得多。
            #   把「用户不知道」这个**动机**拿掉，比禁止那句话有效。
            msg("tools_knowe.py.285") + "\n" +
            msg("tools_knowe.py.286") +
            msg("tools_knowe.py.287") +
            msg("tools_knowe.py.288") + "\n" +
            msg("tools_knowe.py.289") +
            msg("tools_knowe.py.290") +
            msg("tools_knowe.py.291") +
            msg("tools_knowe.py.292") + "\n" +
            msg("tools_knowe.py.293") +
            msg("tools_knowe.py.294") + "\n" +
            msg("tools_knowe.py.295") +
            msg("tools_knowe.py.296") +
            msg("tools_knowe.py.297") + "\n" +
            msg("tools_knowe.py.298")
        ),
        parameters=_coordinator_schemas()["propose_next"],
        handler=handle_propose_next,
        requires_approval=True,
    )

    # ── propose_remove_agent（gated）── [v0.9b] 减人
    async def handle_propose_remove_agent(args: dict[str, Any], **kw: Any) -> str:
        agent_id = kw.get("agent_id", COORDINATOR)
        reason = args.get("reason") or ""

        raw_target = args.get("target_id")
        if raw_target == COORDINATOR:
            return _err(msg("deepseek.py.028"))

        # [v0.10a Issue 1] 同 propose_next：target 认名字也认 id。
        target_id, err = _resolve_target(raw_target, engine)
        if err:
            return _err(err)
        assert target_id is not None
        roster = engine.roster()
        decision = await engine.gate.propose(          # ★ 弹卡，等人点头
            tool="propose_remove_agent",
            agent_id=agent_id,
            card_body={"target_id": target_id, "reason": str(reason)},
        )
        if decision != "approved":
            await engine.on_proposal_rejected(decision, msg("tools_knowe.py.305"))
            return _err(msg("tools_knowe.py.327", **{"_zh(decision)": _zh(decision)}), status=decision)

        role = roster.get(target_id, msg("engine.141.fb"))
        who = engine.member_name(target_id)            # [v0.9c] 说人话：「前端 1」，不是 fe_1
        engine.archive_worker(target_id, str(reason))  # 内存 + 磁盘 + 变更日志，一次做完

        await engine.emit({
            "type": "agent_removed",
            "agent_id": agent_id,
            "target_id": target_id,
            "reason": str(reason),
        })
        engine.record_committed_action("propose_remove_agent")
        # [v0.10a Issue 1] 回话只用名字和角色，不回显 id。
        return _ok(
            target_id=target_id,
            name=who,
            message=(
                msg("tools_knowe.py.330", who=who, role=role) +
                msg("tools_knowe.py.308")
            ),
        )

    _register(
        reg,
        name="propose_remove_agent",
        description=(
            msg("tools_knowe.py.309") +
            msg("tools_knowe.py.310") +
            msg("tools_knowe.py.311") +
            msg("tools_knowe.py.312")
        ),
        parameters=_coordinator_schemas()["propose_remove"],
        handler=handle_propose_remove_agent,
        requires_approval=True,
    )

    # ── [v0.11 C-1] read_harness_memory —— 全局公告栏（只读）──
    async def handle_read_harness(args: dict[str, Any], **kw: Any) -> str:
        del args, kw
        return _ok(content=engine.read_harness_memory())

    _register(
        reg,
        name="read_harness_memory",
        description=(
            msg("tools_knowe.py.313") +
            msg("tools_knowe.py.314")
        ),
        parameters={"type": "object", "properties": {}},
        handler=handle_read_harness,
    )

    _register_project_memory_readonly(reg, engine, role="coordinator")
    _register_knowledge_readonly(reg, engine, role="coordinator")
    _register_coordinator_eyes(reg, engine)          # [v0.29 问题三] 他自己有眼睛
    _register_external_readonly(reg, engine, role="coordinator")
    return reg


# ═══════════════════════════════════════════════════════════════
# [v0.29 问题三] 项目经理自己的眼睛：项目目录的只读三件套
#
# ## PRD 的诊断是错的，而错得很有价值
#
#   PRD 说：「项目经理明明有 `_register_readonly` 注册的文件读取工具，却不用」，
#   让我去「检查一下是否正常工作」。
#
#   ★ **他没有。一个都没有。**
#     `_register_readonly` 从头到尾只注册两样东西：`list_handoff_dir` 和
#     `read_report` —— 都是交接目录里的东西，跟项目文件半点关系没有。
#     `safe_read_file` / `safe_list_dir` / `safe_search_files` 全部注册在
#     `build_worker_registry` 里，**项目经理的注册表里从来没有过它们**。
#
#   铁证在这个文件的 `_register_external_readonly` 里：项目经理拿目录外工具去读项目内
#   路径时，代码回他一句「请使用**项目经理已有的项目只读工具**」——
#   而那个工具**不存在**。我们对着模型指了一样不存在的东西，然后怪它不用。
#
#   所以那句「抱歉，我这边目前看不到项目目录里具体有哪些文件」**不是偷懒，是实话**。
#   他拉一个 agent 去扫目录，是在他那个工具箱里**唯一能做的正确动作**。
#   PRD 里那段对话读起来像模型犯蠢，其实是它在一个残缺的工具箱里做了对的事。
#
#   ★ 这也是为什么这一条不能只改 Prompt：给一个没有眼睛的人写「你有眼睛」，
#     换来的不是他去看，是他**编**一份目录出来。**Prompt 治不了缺工具。**
#
# ## 为什么不直接复用 Worker 那三个 handler
#
#   ① **权限不一样，而这正是项目经理这个角色的定义。** 他只提议、不动手
#     （人设第一段就是这么写的）。所以这里给的是 read / list / search，
#     **没有 write / delete / patch / terminal**。把 `_register_file_power`
#     整个搬过来会顺手塞给他一把 `safe_patch` —— 那不是加个功能，那是换个产品。
#   ② **描述不一样，而描述才是这一条真正的疗效。** 模型是在读 schema 的那一刻
#     决定「自己看还是派人」的。Worker 版写的是「动手改之前先用它定位」——
#     对项目经理毫无意义。这里每一句都在回答他此刻的那个问题：
#     **这事我自己看，还是拉个人？**
#
#   代价是三个 handler 的实现有点像 Worker 那边。认了：这两处**本来就该分开演化**
#   （项目经理的清单要更克制、要挡住噪音目录），而合并它们的收益，
#   远小于哪天有人改 Worker 的写权限时顺手把项目经理一起改了的代价。
# ═══════════════════════════════════════════════════════════════

#: 项目经理自己看目录时，这些一律不列。
#:   他要回答的是「这项目里有些什么」，不是「node_modules 里有几万个文件」——
#:   把 500 个格子让给依赖包，正经文件反而被挤出去（截断是从前面数的）。
_COORDINATOR_NOISE_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv",
    ".next", ".nuxt", ".cache", ".idea", ".vscode", "target", "vendor",
})

def _register_coordinator_eyes(reg: ToolRegistry, engine: "ProjectEngine") -> None:
    """给 Coordinator 可续取的项目只读视图；权限仍严格停留在 read/list/search。"""

    def page_int(args: Mapping[str, Any], key: str, default: int, *, minimum: int,
                 maximum: int | None = None) -> int:
        raw = args.get(key)
        try:
            value = int(raw) if raw not in (None, "") else default
        except (TypeError, ValueError):
            value = default
        value = max(minimum, value)
        return min(maximum, value) if maximum is not None else value

    # ── safe_list_dir（只读） ──
    async def handle_list_dir(args: dict[str, Any], **kw: Any) -> str:
        del kw
        raw = args.get("path", ".")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            raw = "."
        try:
            path = resolve_in_sandbox(
                engine.workspace_root, raw, role="coordinator", operation="read",
            )
        except ValueError as exc:
            return _err(str(exc))
        if not path.is_dir():
            return _err(msg("tools_knowe.py.114", raw=raw))

        offset = page_int(args, "offset", 0, minimum=0)
        limit = page_int(args, "limit", 100, minimum=1, maximum=500)
        include_ignored = args.get("include_ignored") is True
        try:
            children = sorted(path.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            return _err(msg("tools_knowe.py.055", exc=exc))

        root = engine.workspace_root.resolve()
        rows: list[dict[str, Any]] = []
        skipped: list[str] = []
        for child in children:
            if child.name in _LEGACY_INTERNAL_DIRS:
                continue
            if not include_ignored and child.name in _COORDINATOR_NOISE_DIRS:
                skipped.append(child.name)
                continue
            try:
                if child.is_symlink():
                    kind, size = "symlink", None
                elif child.is_dir():
                    kind, size = "dir", None
                elif child.is_file():
                    kind, size = "file", child.stat().st_size
                else:
                    kind, size = "other", None
            except OSError:
                kind, size = "unreadable", None
            try:
                rel = child.relative_to(root).as_posix()
            except ValueError:
                rel = child.name
            row: dict[str, Any] = {"name": child.name, "type": kind, "path": rel}
            if isinstance(size, int):
                row["size"] = size
            rows.append(row)

        page = rows[offset: offset + limit]
        next_offset = offset + len(page)
        truncated = next_offset < len(rows)
        payload: dict[str, Any] = {
            "path": str(raw),
            "entries": page,
            "count": len(page),
            "total_entries": len(rows),
            "offset": offset,
            "limit": limit,
            "include_ignored": include_ignored,
            "truncated": truncated,
            "source_ref": f"project://{raw}#entries={offset}-{max(offset, next_offset - 1)}",
        }
        if skipped:
            payload["skipped_dirs"] = sorted(set(skipped))
            payload["message"] = msg("tools_knowe.py.115")
        if truncated:
            payload["next_offset"] = next_offset
            payload["continuation"] = _continuation(
                "safe_list_dir", path=str(raw), offset=next_offset, limit=limit,
                include_ignored=include_ignored,
            )
        if not rows and not skipped:
            payload["message"] = msg("tools_knowe.py.116")
        return _ok(**payload)

    _register(
        reg,
        name="safe_list_dir",
        description=(
            msg("tools_knowe.py.315") +
            msg("tools_knowe.py.117") +
            msg("tools_knowe.py.118")
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": msg("tools_knowe.py.316")},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "include_ignored": {"type": "boolean"},
            },
        },
        handler=handle_list_dir,
    )

    # ── safe_read_file（只读，按行续取） ──
    async def handle_read(args: dict[str, Any], **kw: Any) -> str:
        del kw
        raw_path = args.get("path", "")
        try:
            path = resolve_in_sandbox(
                engine.workspace_root, raw_path, role="coordinator", operation="read",
            )
        except ValueError as exc:
            return _err(str(exc))
        if not path.is_file():
            return _err(msg("tools_knowe.py.119", raw_path=raw_path))

        start_line = page_int(args, "start_line", 1, minimum=1)
        raw_end = args.get("end_line")
        try:
            requested_end = int(raw_end) if raw_end not in (None, "") else start_line + 199
        except (TypeError, ValueError):
            return _err(msg("tools_knowe.py.120"))
        if requested_end < start_line:
            return _err(msg("tools_knowe.py.121"))
        page_end = min(requested_end, start_line + 499)

        def read_page() -> tuple[list[str], int]:
            selected: list[str] = []
            total = 0
            with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                for total, line in enumerate(handle, start=1):
                    if start_line <= total <= page_end:
                        selected.append(line.rstrip("\r\n"))
            return selected, total

        try:
            rows, total_lines = await asyncio.to_thread(read_page)
            byte_size = path.stat().st_size
        except OSError as exc:
            return _err(msg("tools_knowe.py.048", exc=exc))
        if total_lines and start_line > total_lines:
            return _err(msg("tools_knowe.py.122", total_lines=total_lines, start_line=start_line))

        returned_end = start_line + len(rows) - 1 if rows else 0
        width = max(4, len(str(max(1, total_lines))))
        content = "\n".join(
            f"{line_no:>{width}}│ {line}"
            for line_no, line in zip(range(start_line, start_line + len(rows)), rows)
        )
        truncated = returned_end < total_lines or requested_end > page_end
        rel = path.relative_to(engine.workspace_root.resolve()).as_posix()
        payload: dict[str, Any] = {
            "path": rel,
            "content": content,
            "total_lines": total_lines,
            "byte_size": byte_size,
            "returned_start_line": start_line if rows else None,
            "returned_end_line": returned_end if rows else None,
            "truncated": truncated,
            "source_ref": (
                f"project://{rel}#L{start_line}-L{returned_end}" if rows else f"project://{rel}#empty"
            ),
        }
        if truncated:
            next_line = returned_end + 1 if rows else start_line
            payload["next_start_line"] = next_line
            payload["continuation"] = _continuation(
                "safe_read_file", path=rel, start_line=next_line,
                end_line=next_line + min(499, max(1, requested_end - page_end)) - 1,
            )
        return _ok(**payload)

    _register(
        reg,
        name="safe_read_file",
        description=(
            msg("tools_knowe.py.123") +
            msg("tools_knowe.py.124")
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": msg("tools_knowe.py.125")},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        },
        handler=handle_read,
    )

    # ── safe_search_files（只读，稳定分页） ──
    @_guarded("safe_search_files")
    async def handle_search_files(args: dict[str, Any], **kw: Any) -> dict[str, Any]:
        del kw
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ToolError(msg("tools_knowe.py.126"))
        raw_path = args.get("path") or "."
        try:
            base = resolve_in_sandbox(
                engine.workspace_root, raw_path, role="coordinator", operation="read",
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        if not base.is_dir():
            raise ToolError(msg("tools_knowe.py.127", raw_path=raw_path))

        offset = page_int(args, "offset", 0, minimum=0)
        limit = page_int(args, "limit", 30, minimum=1, maximum=100)
        context = page_int(args, "context", 0, minimum=0, maximum=10)
        include_ignored = args.get("include_ignored") is True
        outcome = await asyncio.to_thread(
            functools.partial(
                file_ops.search_files,
                engine.workspace_root.resolve(), base, pattern,
                file_glob=args.get("file_glob"),
                offset=offset,
                limit=limit,
                context=context,
                include_ignored=include_ignored,
                reserved_root_dirs=_LEGACY_INTERNAL_DIRS,
            )
        )
        next_offset = offset + len(outcome.matches)
        payload: dict[str, Any] = {
            "path": str(raw_path),
            "matches": outcome.matches,
            "count": len(outcome.matches),
            "offset": offset,
            "limit": limit,
            "files_scanned": outcome.files_scanned,
            "truncated": outcome.truncated,
            "source_ref": f"project-search://{raw_path}?offset={offset}&pattern={pattern}",
        }
        if outcome.truncated:
            payload["next_offset"] = next_offset
            continuation_args: dict[str, Any] = {
                "pattern": pattern, "path": str(raw_path), "offset": next_offset, "limit": limit,
                "context": context, "include_ignored": include_ignored,
            }
            if args.get("file_glob"):
                continuation_args["file_glob"] = args.get("file_glob")
            payload["continuation"] = _continuation("safe_search_files", **continuation_args)
        if not outcome.matches and not outcome.truncated:
            payload["message"] = msg("tools_knowe.py.128", **{"outcome.files_scanned": outcome.files_scanned})
        return payload

    _register(
        reg,
        name="safe_search_files",
        description=(
            msg("tools_knowe.py.129") +
            msg("tools_knowe.py.130") +
            msg("tools_knowe.py.131")
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": msg("tools_knowe.py.132")},
                "path": {"type": "string", "description": msg("tools_knowe.py.317")},
                "file_glob": {"type": "string", "description": msg("tools_knowe.py.133")},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "context": {"type": "integer", "minimum": 0, "maximum": 10},
                "include_ignored": {"type": "boolean"},
            },
            "required": ["pattern"],
        },
        handler=handle_search_files,
    )


def _missing_artifacts(engine: "ProjectEngine", artifacts: list[Any]) -> list[str]:
    """
    [v0.25 问题二] Worker 报的交付物，哪些在盘上其实不存在。

    用户报的：「Worker 说文件已保存到项目目录，去看**文件不存在**」。
    长上下文里模型会「记得」自己写过——那份记忆来自它自己的**计划**，不是磁盘。

    ★ 引擎一个 exists() 就能查出来的事，不该让用户去发现。

    只挑**看起来像文件**的条目查（带扩展名、或带路径分隔符）。
    交付物里常有说明性的条目（「登录接口已联调」），那些不是路径，别去为难它们。
    """
    out: list[str] = []
    for item in artifacts:
        name = item if isinstance(item, str) else (
            item.get("path") or item.get("file") or "" if isinstance(item, dict) else "")
        name = (name or "").strip()
        if not name or len(name) > 200:
            continue
        looks_like_path = ("/" in name or "\\" in name
                           or re.fullmatch(r"[^\s]+\.[A-Za-z0-9]{1,8}", name) is not None)
        if not looks_like_path:
            continue
        try:
            path = resolve_in_sandbox(engine.workspace_root, name,
                                      role="worker", operation="read")
        except Exception:
            continue          # 越界/解析不了 → 不是我们该在这儿管的事（沙箱已经拦过了）
        try:
            if not path.exists():
                out.append(name)
        except OSError:
            continue
    return out

def _is_workspace_unavailable(exc: BaseException) -> bool:
    """Avoid swallowing the Engine's project-root terminal exception."""

    return type(exc).__name__ == "WorkspaceUnavailable"


def _number(
    args: Mapping[str, Any],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float | None,
) -> float:
    raw = args.get(key)
    if raw in (None, ""):
        return default
    try:
        value = max(minimum, float(raw))
        return min(maximum, value) if maximum is not None else value
    except (TypeError, ValueError):
        return default


def _integer(
    args: Mapping[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None,
) -> int:
    return int(_number(args, key, default, minimum=minimum, maximum=maximum))


def _stable_error_code(exc: BaseException, fallback: str) -> str:
    explicit = getattr(exc, "code", "")
    if explicit:
        return str(explicit)
    message = str(exc).casefold()
    if "不存在" in message or "not found" in message:
        return "not_found"
    if "越界" in message or "absolute" in message or "绝对路径" in message:
        return "path_outside_project"
    if "symlink" in message or "符号链接" in message:
        return "symlink_denied"
    if "权限" in message or "permission" in message:
        return "permission_denied"
    if "超时" in message or "timeout" in message:
        return "timeout"
    return fallback


def _guard_worker_tool(
    name: str,
    *,
    cancellation_event: asyncio.Event | None,
) -> Callable[[Callable[..., Awaitable[Mapping[str, Any] | None]]], Callable[..., Awaitable[str]]]:
    """Convert expected handler failures to stable model-visible JSON.

    Cancellation and WorkspaceUnavailable deliberately cross the handler boundary so Runtime
    can own terminal state and cleanup.
    """

    def decorate(
        fn: Callable[..., Awaitable[Mapping[str, Any] | None]],
    ) -> Callable[..., Awaitable[str]]:
        @functools.wraps(fn)
        async def wrapped(args: dict[str, Any], **kwargs: Any) -> str:
            if cancellation_event is not None and cancellation_event.is_set():
                raise asyncio.CancelledError
            try:
                value = await fn(args, **kwargs)
            except asyncio.CancelledError:
                raise
            except ToolError as exc:
                return _err(
                    str(exc),
                    code=_stable_error_code(exc, f"{name}_rejected"),
                )
            except (ValueError, TypeError) as exc:
                return _err(str(exc), code="invalid_arguments")
            except Exception as exc:
                if _is_workspace_unavailable(exc):
                    raise
                log.exception("[worker-tool:%s] unexpected failure", name)
                return _err(
                    f"{name} failed: {type(exc).__name__}: {str(exc)[:300]}",
                    code=f"{name}_failed",
                )
            data = dict(value or {})
            status = str(data.get("status") or "ok").lower()
            if status in {"error", "failed", "failure"}:
                data["status"] = "error"
                return json.dumps(data, ensure_ascii=False)
            data.pop("status", None)
            return _ok(**data)

        return wrapped

    return decorate


def _project_rel(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("path must be a non-empty project-relative string")
    value = raw.strip().replace("\\", "/")
    parts = Path(value).parts
    if value.startswith("/") or (len(value) > 1 and value[1] == ":"):
        raise ToolError("absolute paths are not allowed; use a project-relative path")
    if ".." in parts:
        raise ToolError("'..' path traversal is not allowed")
    normalized = Path(value).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or "."


def _project_path(
    engine: "ProjectEngine",
    raw: Any,
    *,
    operation: str,
) -> tuple[Path, str]:
    rel = _project_rel(raw)
    # [v1.0.19.3] Knowe 内部文件（.knowe/ 前缀）不写用户项目根目录。
    # 物理位置收拢到 <backend_data_root>/<project_id>/runtime/ 下；
    # Worker 视角的相对路径（.knowe/…）保持不变，读/写/删统一走这里。
    if rel.startswith(".knowe/"):
        rest = rel[len(".knowe/"):]
        internal_root = Path(engine.internal_workspace).resolve()
        target = (internal_root / "runtime" / rest).resolve()
        if target != internal_root and internal_root not in target.parents:
            raise ToolError(f"internal path escapes data root: {rel}")
        lexical = internal_root
        for part in Path(rest).parts:
            lexical = lexical / part
            try:
                if lexical.is_symlink():
                    raise ToolError(f"symbolic links are not allowed in internal paths: {rel}")
            except OSError as exc:
                raise ToolError(f"cannot inspect internal path {rel}: {exc}") from None
        return target, rel
    try:
        root = Path(engine.workspace_root).resolve(strict=True)
    except FileNotFoundError as exc:
        workspace_error = type("WorkspaceUnavailable", (RuntimeError,), {})
        raise workspace_error(f"project workspace is unavailable: {exc}") from exc
    # Reject every existing symlink component before resolving the target.  Doing this
    # after realpath resolution would lose the caller's symlink spelling and could turn
    # a link escape into a generic traversal error.
    lexical = root
    for part in Path(rel).parts:
        if part in ("", "."):
            continue
        lexical = lexical / part
        try:
            if lexical.is_symlink():
                raise ToolError(f"symbolic links are not allowed in Worker file paths: {rel}")
        except OSError as exc:
            raise ToolError(f"cannot inspect project path {rel}: {exc}") from None
    target = resolve_in_sandbox(root, rel, role="worker", operation=operation)
    return target, target.relative_to(root).as_posix() if target != root else "."


def _stream_file_digest(path: Path) -> tuple[int, str]:
    total = 0
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
    return total, digest.hexdigest()


def _artifact_fact(path: Path, rel: str, *, kind: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ToolError(f"post-effect verification failed: {rel} is not a regular file")
    size, digest = _stream_file_digest(path)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "path": rel,
        "kind": kind,
        "size": size,
        "sha256": digest,
        "verified": True,
        "media_type": media_type,
    }


def _notify_produced(engine: "ProjectEngine", agent_id: str, rel: str) -> None:
    """Best-effort UI notification only; Runtime never trusts it for completion."""

    callback = getattr(engine, "note_file_produced", None)
    if callable(callback):
        try:
            callback(agent_id, rel)
        except Exception:
            log.debug("file-card notification failed for %s", rel, exc_info=True)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise ToolError("refusing to write through a symbolic link")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _copy_external_atomic(source: Path, destination: Path) -> tuple[int, str]:
    """Stream-copy to a same-directory temp file, then verify size/hash before atomic replace."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_size = source.stat().st_size
    try:
        free_bytes = shutil.disk_usage(destination.parent).free
    except OSError as exc:
        raise ToolError(f"cannot determine destination free space: {exc}") from None
    if source_size > free_bytes:
        raise ToolError(
            f"destination has insufficient free space: source={source_size} bytes, free={free_bytes} bytes"
        )

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".copy-tmp", dir=str(destination.parent)
    )
    tmp = Path(tmp_name)
    digest = hashlib.sha256()
    copied = 0
    try:
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if copied != source_size:
            raise ToolError(
                f"source changed during copy: expected {source_size} bytes, read {copied} bytes"
            )
        try:
            shutil.copystat(source, tmp, follow_symlinks=False)
        except OSError:
            pass
        tmp_size, tmp_hash = _stream_file_digest(tmp)
        if tmp_size != source_size or tmp_hash != digest.hexdigest():
            raise ToolError("post-copy verification failed before commit")
        os.replace(tmp, destination)
        final_size, final_hash = _stream_file_digest(destination)
        if final_size != source_size or final_hash != digest.hexdigest():
            try:
                destination.unlink()
            except OSError:
                pass
            raise ToolError("post-copy verification failed after commit")
        return final_size, final_hash
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _continuation(name: str, **kwargs: Any) -> str:
    rendered = ", ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in kwargs.items())
    return f"Continue with {name}({rendered})"


def _split_background_command(command: str) -> tuple[str, bool]:
    """Recognize one unquoted trailing '&'; reject detached-process shell tricks elsewhere."""

    quote: str | None = None
    escaped = False
    unquoted: set[int] = set()
    for index, char in enumerate(command):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            continue
        unquoted.add(index)
    if quote:
        raise ToolError("shell command has an unclosed quote")
    last = len(command.rstrip()) - 1
    if last >= 0 and command[last] == "&" and last in unquoted:
        previous = command[:last].rstrip()
        if previous.endswith("&"):
            return command.strip(), False
        if not previous:
            raise ToolError("background command is empty")
        return previous, True
    return command.strip(), False


def _authorized_roots(values: Sequence[str | Path]) -> tuple[Path, ...]:
    rows: list[Path] = []
    for value in values:
        try:
            root = Path(value).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, TypeError):
            continue
        if root not in rows:
            rows.append(root)
    return tuple(rows)


def _external_path(
    engine: "ProjectEngine",
    raw: Any,
    roots: Sequence[Path],
    *,
    expect: str,
) -> tuple[Path, str]:
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("external path must be a non-empty absolute path")
    lexical = Path(raw.strip()).expanduser()
    if not lexical.is_absolute():
        raise ToolError("external path must be absolute")
    # Resolve only after walking the caller-supplied spelling.  Otherwise a symlink
    # component disappears and an in-root link can masquerade as a regular file.
    cursor = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        cursor = cursor / part
        try:
            if cursor.is_symlink():
                raise ToolError("external symbolic links are not allowed")
        except OSError as exc:
            raise ToolError(f"cannot inspect external path: {exc}") from None
    path = _resolve_external_read(raw)
    if not roots:
        raise ToolError("no external root is authorized for this task")
    owner_index = -1
    owner: Path | None = None
    for index, root in enumerate(roots, start=1):
        if _under(path, root):
            owner_index, owner = index, root
            break
    if owner is None:
        raise ToolError("external path is outside the task's authorized roots")
    if hasattr(engine, "internal_workspace") and _is_internal_storage_path(engine, path):
        raise ToolError("Knowe internal storage cannot be accessed by Worker tools")
    try:
        workspace = Path(engine.workspace_root).resolve(strict=True)
    except Exception as exc:
        if _is_workspace_unavailable(exc):
            raise
        raise ToolError(f"project workspace is unavailable: {exc}") from None
    if _under(path, workspace):
        raise ToolError("use project file tools for paths inside the current workspace")
    if path.is_symlink():
        raise ToolError("external symbolic links are not allowed")
    if expect == "file" and not path.is_file():
        raise ToolError("authorized external path is not a regular file")
    if expect == "dir" and not path.is_dir():
        raise ToolError("authorized external path is not a directory")
    relative = path.relative_to(owner).as_posix()
    safe_label = f"external_root_{owner_index}" + (f"/{relative}" if relative != "." else "")
    return path, safe_label


def _browser_pool(engine: "ProjectEngine") -> browser_tools.BrowserPool:
    return runtime_for(engine.project_id).slot(
        "browser",
        lambda: browser_tools.BrowserPool(
            engine.project_id,
            headless=CONFIG.browser_headless,
            timeout_s=CONFIG.browser_timeout_s,
            idle_s=CONFIG.browser_idle_s,
            max_sessions=CONFIG.browser_max_sessions,
            snapshot_max=CONFIG.browser_snapshot_max,
        ),
    )


async def close_worker_browser_session(engine: "ProjectEngine", agent_id: str) -> bool:
    """Runtime-owned cleanup hook; intentionally not a model-facing tool."""

    pool = runtime_for(engine.project_id).peek("browser")
    if pool is None:
        return False
    closer = getattr(pool, "close_session", None)
    if not callable(closer):
        return False
    return bool(await closer(agent_id))


def build_worker_registry(
    engine: "ProjectEngine",
    agent_id: str,
    *,
    registry: ToolRegistry | None = None,
    authorized_external_roots: Sequence[str | Path] = (),
    cancellation_event: asyncio.Event | None = None,
    process_registry: Any | None = None,
    **_ignored_legacy: Any,
) -> ToolRegistry:
    """Register the exact fixed Worker toolset in canonical provider order.

    Service readiness is checked inside handlers.  It never changes tool names, order, or
    schemas, so every Provider turn receives the same 19 definitions.
    """

    reg = registry if registry is not None else ToolRegistry()
    if len(reg):
        raise ValueError("Worker registry must be empty before fixed tool registration")
    external_roots = _authorized_roots(tuple(authorized_external_roots))
    guard = lambda name: _guard_worker_tool(name, cancellation_event=cancellation_event)

    @guard("safe_read_file")
    async def handle_read(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        path, rel = _project_path(engine, args.get("path"), operation="read")
        if path.is_symlink() or not path.is_file():
            raise ToolError(f"file does not exist or is not a regular file: {rel}")
        raw = await asyncio.to_thread(path.read_bytes)
        if b"\x00" in raw[:8192]:
            raise ToolError("safe_read_file only reads text files; this file appears binary")
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        start = _integer(args, "start_line", 1, minimum=1, maximum=max(1, total + 1))
        requested_end = args.get("end_line")
        if requested_end is None:
            wanted_end = start + 199
        else:
            try:
                wanted_end = int(requested_end)
            except (TypeError, ValueError):
                raise ToolError("end_line must be an integer") from None
            if wanted_end < start:
                raise ToolError("end_line must be greater than or equal to start_line")
        hard_end = min(wanted_end, start + 499)
        if total and start > total:
            raise ToolError(f"file has {total} lines; start_line={start} is out of range")
        end = min(total, hard_end)
        width = max(4, len(str(max(1, total))))
        selected = lines[start - 1 : end]
        content = "\n".join(
            f"{line_no:>{width}}│ {line}"
            for line_no, line in zip(range(start, end + 1), selected)
        )
        truncated = end < total or wanted_end > hard_end
        payload: dict[str, Any] = {
            "path": rel,
            "content": content,
            "total_lines": total,
            "returned_start_line": start,
            "returned_end_line": end,
            "truncated": truncated,
        }
        if truncated:
            next_start = end + 1
            payload["next_start_line"] = next_start
            payload["continuation"] = _continuation(
                "safe_read_file",
                path=rel,
                start_line=next_start,
                end_line=min(total, next_start + max(1, end - start)),
            )
        return payload

    _register(
        reg,
        name="safe_read_file",
        description=(
            "Read a UTF-8 text file inside the project sandbox with stable 1-based line numbers. " +
            "Use start_line/end_line to continue a truncated read; only project-relative paths are accepted."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project-relative file path."},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=handle_read,
    )

    @guard("safe_write_file")
    async def handle_write(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        path, rel = _project_path(engine, args.get("path"), operation="write")
        payload = content.encode("utf-8")
        await asyncio.to_thread(_atomic_write, path, payload)
        verified = await asyncio.to_thread(path.read_bytes)
        if verified != payload:
            raise ToolError("post-write verification failed: persisted bytes differ from requested content")
        artifact = await asyncio.to_thread(_artifact_fact, path, rel, kind="file")
        _notify_produced(engine, agent_id, rel)
        return {
            "path": rel,
            "bytes": len(payload),
            "artifact": artifact,
            "artifacts": [artifact],
            "message": "File written and re-opened successfully; size and SHA-256 were verified.",
        }

    _register(
        reg,
        name="safe_write_file",
        description=(
            "Create or fully overwrite one project-relative UTF-8 file. The handler re-opens, stats, " +
            "and hashes the persisted file before returning a verified artifact."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        handler=handle_write,
    )

    @guard("safe_patch")
    async def handle_patch(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        path, rel = _project_path(engine, args.get("path"), operation="write")
        if path.is_symlink() or not path.is_file():
            raise ToolError(f"file does not exist or is not a regular file: {rel}")
        old_string = args.get("old_string", "")
        new_string = args.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            raise ToolError("old_string and new_string must be strings")
        before = await asyncio.to_thread(path.read_bytes)
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        outcome = await asyncio.to_thread(
            file_ops.patch_file,
            path,
            rel,
            old_string,
            new_string,
            replace_all=args.get("replace_all") is True,
            start_line=int(start_line) if start_line is not None else None,
            end_line=int(end_line) if end_line is not None else None,
        )
        after = await asyncio.to_thread(path.read_bytes)
        if before == after:
            raise ToolError("post-patch verification failed: file bytes did not change")
        artifact = await asyncio.to_thread(_artifact_fact, path, rel, kind="file")
        _notify_produced(engine, agent_id, rel)
        return {
            "path": rel,
            "replacements": outcome.replacements,
            "line_range": list(outcome.line_range) if outcome.line_range else None,
            "diff": outcome.diff,
            "diff_truncated": outcome.diff_truncated,
            "syntax_checked": outcome.syntax_checked,
            "syntax_note": outcome.syntax_note,
            "syntax_warning": outcome.syntax_warning,
            "self_check": outcome.self_check,
            "artifact": artifact,
            "artifacts": [artifact],
            "message": "Patch persisted; final bytes were re-read, statted, and hashed.",
        }

    _register(
        reg,
        name="safe_patch",
        description=(
            "Precisely edit a project text file using either old_string/new_string or a 1-based " +
            "start_line/end_line range. Ambiguous matches are rejected and final bytes are verified."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "new_string"],
            "additionalProperties": False,
        },
        handler=handle_patch,
    )

    @guard("safe_list_dir")
    async def handle_list(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        path, rel = _project_path(engine, args.get("path", "."), operation="read")
        if path.is_symlink() or not path.is_dir():
            raise ToolError(f"directory does not exist or is not a regular directory: {rel}")
        offset = _integer(args, "offset", 0, minimum=0, maximum=None)
        limit = _integer(args, "limit", 100, minimum=1, maximum=500)
        root = Path(engine.workspace_root).resolve(strict=True)
        children = sorted(path.iterdir(), key=lambda item: item.name.casefold())
        visible: list[dict[str, Any]] = []
        for child in children:
            if path == root and child.name in _LEGACY_INTERNAL_DIRS:
                continue
            if child.is_symlink():
                continue
            try:
                child_rel = child.relative_to(root).as_posix()
                if child.is_dir():
                    row: dict[str, Any] = {"name": child.name, "path": child_rel, "type": "dir"}
                elif child.is_file():
                    row = {
                        "name": child.name,
                        "path": child_rel,
                        "type": "file",
                        "size": child.stat().st_size,
                    }
                else:
                    continue
            except OSError:
                continue
            visible.append(row)
        page = visible[offset : offset + limit]
        next_offset = offset + len(page)
        truncated = next_offset < len(visible)
        payload: dict[str, Any] = {
            "path": rel,
            "entries": page,
            "count": len(page),
            "offset": offset,
            "limit": limit,
            "truncated": truncated,
        }
        if truncated:
            payload["next_offset"] = next_offset
            payload["continuation"] = _continuation(
                "safe_list_dir", path=rel, offset=next_offset, limit=limit
            )
        return payload

    _register(
        reg,
        name="safe_list_dir",
        description=(
            "List one level of a project directory in stable order. Use offset/limit for continuation; " +
            "reserved internal paths and symbolic links are not exposed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        handler=handle_list,
    )

    @guard("safe_search_files")
    async def handle_search(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ToolError("pattern must be a non-empty regular expression")
        base, rel = _project_path(engine, args.get("path", "."), operation="read")
        if base.is_symlink() or not base.is_dir():
            raise ToolError(f"search path is not a directory: {rel}")
        offset = _integer(args, "offset", 0, minimum=0, maximum=None)
        limit = _integer(args, "limit", 50, minimum=1, maximum=100)
        context = _integer(args, "context", 0, minimum=0, maximum=10)
        include_ignored = args.get("include_ignored") is True
        outcome = await asyncio.to_thread(
            file_ops.search_files,
            Path(engine.workspace_root),
            base,
            pattern,
            file_glob=str(args.get("file_glob") or "") or None,
            offset=offset,
            limit=limit,
            context=context,
            include_ignored=include_ignored,
            cancel_check=(cancellation_event.is_set if cancellation_event is not None else None),
            reserved_root_dirs=_LEGACY_INTERNAL_DIRS,
        )
        page = outcome.matches
        next_offset = offset + len(page)
        payload: dict[str, Any] = {
            "path": rel,
            "matches": page,
            "count": len(page),
            "offset": offset,
            "limit": limit,
            "files_scanned": outcome.files_scanned,
            "truncated": outcome.truncated,
        }
        if outcome.truncated:
            payload["next_offset"] = next_offset
            continuation_args: dict[str, Any] = {
                "pattern": pattern,
                "path": rel,
                "offset": next_offset,
                "limit": limit,
                "context": context,
                "include_ignored": include_ignored,
            }
            if args.get("file_glob"):
                continuation_args["file_glob"] = args.get("file_glob")
            payload["continuation"] = _continuation(
                "safe_search_files", **continuation_args
            )
        return payload

    _register(
        reg,
        name="safe_search_files",
        description=(
            "Search project-visible text files with a regular expression. Results are project-relative, " +
            "bounded, and continued with the same tool using offset/limit."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "default": "."},
                "file_glob": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "context": {"type": "integer", "minimum": 0, "maximum": 10},
                "include_ignored": {"type": "boolean"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        handler=handle_search,
    )

    @guard("safe_delete_file")
    async def handle_delete(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        # [v1.0.23.5] 删除意图安全门已整体移除：worker 可直接删除项目内普通文件。
        #   保留的硬校验：仅限项目工作区内普通文件、拒绝符号链接、删除后验证不存在。
        path, rel = _project_path(engine, args.get("path"), operation="delete")
        if path.is_symlink() or not path.is_file():
            raise ToolError("safe_delete_file deletes project ordinary files only")
        size_before = path.stat().st_size
        await asyncio.to_thread(path.unlink)
        if path.exists() or path.is_symlink():
            raise ToolError("post-delete verification failed: target still exists")
        return {
            "path": rel,
            "deletion": {
                "path": rel,
                "verified_absent": True,
                "size_before": size_before,
            },
            "message": "File deleted and absence verified.",
        }

    _register(
        reg,
        name="safe_delete_file",
        description=(
            "Delete one ordinary project file. Directories and symbolic links are refused; "
            "success requires post-unlink absence verification."
        ),
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=handle_delete,
    )

    @guard("read_external_file")
    async def handle_external_read(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        path, label = _external_path(
            engine, args.get("path"), external_roots, expect="file"
        )
        offset = _integer(args, "offset", 0, minimum=0, maximum=None)
        limit = _integer(args, "limit", 200, minimum=1, maximum=500)

        def read_page() -> tuple[list[str], bool]:
            rows: list[str] = []
            more = False
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for index, line in enumerate(handle):
                    if index < offset:
                        continue
                    if len(rows) >= limit:
                        more = True
                        break
                    rows.append(line.rstrip("\r\n"))
            return rows, more

        rows, truncated = await asyncio.to_thread(read_page)
        next_offset = offset + len(rows)
        payload: dict[str, Any] = {
            "path": label,
            "content": "\n".join(rows),
            "offset": offset,
            "limit": limit,
            "offset_unit": "lines",
            "returned_lines": len(rows),
            "truncated": truncated,
        }
        if truncated:
            payload["next_offset"] = next_offset
            payload["continuation"] = (
                f"Continue with read_external_file(path=<same authorized path>, " +
                f"offset={next_offset}, limit={limit})"
            )
        return payload

    _register(
        reg,
        name="read_external_file",
        description=(
            "Read a regular file under a task-authorized external root without modifying it. " +
            "The source path must be absolute; output is paged by line offset and never exposes the absolute root."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=handle_external_read,
    )

    @guard("list_external_dir")
    async def handle_external_list(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        path, label = _external_path(
            engine, args.get("path"), external_roots, expect="dir"
        )
        offset = _integer(args, "offset", 0, minimum=0, maximum=None)
        limit = _integer(args, "limit", 100, minimum=1, maximum=500)
        rows: list[dict[str, Any]] = []
        for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
            if child.is_symlink():
                continue
            try:
                if hasattr(engine, "internal_workspace") and _is_internal_storage_path(engine, child):
                    continue
                if child.is_file():
                    rows.append({"name": child.name, "type": "file", "size": child.stat().st_size})
                elif child.is_dir():
                    rows.append({"name": child.name, "type": "dir"})
            except OSError:
                continue
        page = rows[offset : offset + limit]
        next_offset = offset + len(page)
        truncated = next_offset < len(rows)
        payload: dict[str, Any] = {
            "path": label,
            "entries": page,
            "count": len(page),
            "offset": offset,
            "limit": limit,
            "truncated": truncated,
        }
        if truncated:
            payload["next_offset"] = next_offset
            payload["continuation"] = (
                f"Continue with list_external_dir(path=<same authorized path>, " +
                f"offset={next_offset}, limit={limit})"
            )
        return payload

    _register(
        reg,
        name="list_external_dir",
        description=(
            "List one level of an absolute directory under a task-authorized external root. " +
            "Results are stably paginated and do not disclose unauthorized parent paths."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=handle_external_list,
    )

    @guard("copy_external_file")
    async def handle_external_copy(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        source, source_label = _external_path(
            engine, args.get("source"), external_roots, expect="file"
        )
        destination, rel = _project_path(
            engine, args.get("destination"), operation="write"
        )
        if destination.exists() and args.get("overwrite") is not True:
            raise ToolError("destination already exists; pass overwrite=true to replace it")
        copied_size, copied_hash = await asyncio.to_thread(
            _copy_external_atomic, source, destination
        )
        artifact = {
            "path": rel,
            "kind": "copied_file",
            "size": copied_size,
            "sha256": copied_hash,
            "verified": True,
            "media_type": mimetypes.guess_type(destination.name)[0] or "application/octet-stream",
        }
        _notify_produced(engine, agent_id, rel)
        return {
            "source": source_label,
            "destination": rel,
            "artifact": artifact,
            "artifacts": [artifact],
            "message": "External source remained untouched; project copy size/hash were verified.",
        }

    _register(
        reg,
        name="copy_external_file",
        description=(
            "Copy one ordinary file from a task-authorized external root into a project-relative destination. " +
            "The source is never moved or modified; destination size and SHA-256 are verified."
        ),
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["source", "destination"],
            "additionalProperties": False,
        },
        handler=handle_external_copy,
    )

    @guard("safe_bash")
    async def handle_bash(args: dict[str, Any], **kwargs: Any) -> Mapping[str, Any]:
        if not CONFIG.terminal_enabled:
            return {
                "status": "error",
                "code": "terminal_unavailable",
                "message": "Terminal service is disabled for this installation.",
            }
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolError("command must be a non-empty string")
        managed, background = _split_background_command(command)
        if re.search(r"(?:^|[;&|\s])(?:nohup|setsid|disown)(?:$|[;&|\s])", managed):
            raise ToolError("detached process commands are not allowed; use one trailing '&' instead")
        terminal_tools.guard_command(managed)
        timeout = _number(
            args,
            "timeout",
            CONFIG.terminal_timeout_s,
            minimum=1,
            maximum=CONFIG.terminal_max_timeout_s,
        )
        active_registry = kwargs.get("attempt_process_registry") or process_registry
        if background:
            starter = getattr(active_registry, "start", None)
            if not callable(starter):
                raise ToolError("attempt-owned background process registry is unavailable")
            try:
                info = await starter(managed, cwd=Path(engine.workspace_root))
            except TypeError:
                info = await starter(managed, agent_id=agent_id, cwd=Path(engine.workspace_root))
            if hasattr(info, "info") and callable(info.info):
                info = info.info()
            elif not isinstance(info, Mapping):
                info = {"process": str(info)}
            return {
                **dict(info),
                "background": True,
                "message": "Background process is attempt-owned and will be terminated during Runtime cleanup.",
            }

        cap = max(1, int(CONFIG.terminal_max_output))
        task_run = kwargs.get("task_run")
        envelope = getattr(task_run, "envelope", None)

        def safe_component(value: Any, fallback: str) -> str:
            cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
            return cleaned[:80] or fallback

        task_label = safe_component(getattr(envelope, "task_id", ""), "task")
        attempt_label = safe_component(getattr(envelope, "attempt_id", ""), "attempt")
        run_label = safe_component(getattr(task_run, "run_id", ""), "run")
        log_token = hashlib.sha256(
            f"{task_label}\0{attempt_label}\0{run_label}\0{time.time_ns()}\0{managed}".encode("utf-8")
        ).hexdigest()[:16]
        log_rel = (
            f".knowe/attempt-logs/{task_label}/{attempt_label}/" +
            f"terminal-{log_token}.log"
        )
        log_path, log_rel = _project_path(engine, log_rel, operation="write")
        result = await terminal_tools.run_command(
            managed,
            cwd=Path(engine.workspace_root),
            timeout_s=timeout,
            max_output=cap,
            log_path=log_path,
        )
        payload: dict[str, Any] = {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_s": result.duration_s,
            "bytes_total": result.bytes_total,
            "output_sha256": result.output_sha256,
            "truncated": result.truncated,
        }
        if result.truncated:
            payload["stdout_head"] = result.output_head
            payload["stdout_tail"] = result.output_tail
            payload["source_ref"] = {
                "type": "project_file",
                "path": log_rel,
                "size": result.bytes_total,
                "sha256": result.output_sha256,
                "required": "Read this attempt log with safe_read_file to inspect the complete terminal output.",
            }
            payload["continuation"] = _continuation(
                "safe_read_file", path=log_rel, start_line=1, end_line=200
            )
        else:
            payload["output"] = result.output
            try:
                log_path.unlink(missing_ok=True)
            except OSError:
                pass
        if result.timed_out:
            payload["message"] = "Command timed out and its process tree was terminated."
        elif result.exit_code not in (0, None):
            payload["message"] = f"Command exited with code {result.exit_code}."
        return payload

    _register(
        reg,
        name="safe_bash",
        description=(
            "Run one guarded shell command with the project root as cwd and a hard timeout. A single " +
            "unquoted trailing '&' starts an attempt-owned background process. Large output returns bounded head/tail only."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number", "minimum": 1},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        handler=handle_bash,
    )

    @guard("web_search")
    async def handle_web_search(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        if not CONFIG.web_enabled:
            return {
                "status": "error",
                "code": "web_unavailable",
                "message": "Web search service is disabled for this installation.",
            }
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolError("query must be a non-empty string")
        offset = _integer(args, "offset", 0, minimum=0, maximum=19)
        limit = _integer(args, "limit", 5, minimum=1, maximum=20)
        rows = await web_tools.search(
            query,
            limit=min(20, offset + limit + 1),
            backend=CONFIG.web_search_backend,
            searxng_url=CONFIG.searxng_url,
        )
        page = rows[offset : offset + limit]
        next_offset = offset + len(page)
        truncated = next_offset < len(rows)
        payload: dict[str, Any] = {
            "query": query,
            "results": page,
            "count": len(page),
            "offset": offset,
            "limit": limit,
            "truncated": truncated,
        }
        if truncated:
            payload["next_offset"] = next_offset
            payload["continuation"] = _continuation(
                "web_search", query=query, offset=next_offset, limit=limit
            )
        return payload

    _register(
        reg,
        name="web_search",
        description=(
            "Search the public web and return bounded structured results. Use offset/limit for the next page; " +
            "service unavailability returns a stable error without changing the schema."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=handle_web_search,
    )

    @guard("web_extract")
    async def handle_web_extract(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        if not CONFIG.web_enabled:
            return {
                "status": "error",
                "code": "web_unavailable",
                "message": "Web extraction service is disabled for this installation.",
            }
        urls = web_tools.normalize_urls(args.get("urls"))
        fmt = str(args.get("format") or "markdown").lower()
        if fmt not in {"markdown", "text", "html"}:
            raise ToolError("format must be markdown, text, or html")
        offset = _integer(args, "offset", 0, minimum=0, maximum=None)
        limit = _integer(args, "limit", min(12_000, CONFIG.web_max_chars), minimum=1, maximum=20_000)
        fetched = await web_tools.fetch_many(urls, timeout_s=CONFIG.web_timeout_s)
        # Render each complete downloaded source first.  Pagination is applied only
        # to the resulting combined text, so every character up to the explicit
        # network byte boundary remains reachable through offset/limit.
        rendered = [web_tools.render(page, fmt) for page in fetched]
        sections: list[str] = []
        page_meta: list[dict[str, Any]] = []
        for page in rendered:
            source_ref = {
                "type": "web_url",
                "url": page.url,
                "bytes_downloaded": page.bytes_downloaded,
            }
            meta: dict[str, Any] = {
                "url": page.url,
                "ok": page.ok,
                "title": page.title,
                "bytes_downloaded": page.bytes_downloaded,
                "source_complete": not page.truncated,
                "source_ref": source_ref,
            }
            if page.truncated:
                # This is a transport resource boundary, not a semantic crop.  The
                # original URL is the authoritative source reference.
                meta["truncated"] = True
                meta["truncation_reason"] = "network_download_byte_boundary"
            if not page.ok:
                meta["error"] = page.error
            page_meta.append(meta)
            if page.ok:
                sections.append(f"URL: {page.url}\nTitle: {page.title}\n\n{page.content}")
            else:
                sections.append(f"URL: {page.url}\nERROR: {page.error}")
        combined = "\n\n---\n\n".join(sections)
        chunk = combined[offset : offset + limit]
        next_offset = offset + len(chunk)
        truncated = next_offset < len(combined)
        combined_sha256 = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        payload: dict[str, Any] = {
            "pages": page_meta,
            "content": chunk,
            "offset": offset,
            "limit": limit,
            "offset_unit": "characters",
            "returned_characters": len(chunk),
            "total_characters": len(combined),
            "content_sha256": combined_sha256,
            "source_ref": {
                "type": "web_extract_projection",
                "urls": urls,
                "format": fmt,
                "sha256": combined_sha256,
                "range": [offset, next_offset],
                "total_characters": len(combined),
            },
            "truncated": truncated,
        }
        if args.get("summarize") is True and chunk:
            try:
                focus = str(args.get("focus") or "")
                payload["summary"] = await aux_client_chat(
                    web_tools.SUMMARY_SYSTEM(),
                    f"Extracted pages:\n\n{chunk}\n\nFocus: {focus}",
                )
            except ToolError as exc:
                payload["summary_error"] = str(exc)
        if truncated:
            payload["next_offset"] = next_offset
            payload["continuation"] = _continuation(
                "web_extract",
                urls=urls,
                format=fmt,
                offset=next_offset,
                limit=limit,
            )
        return payload

    _register(
        reg,
        name="web_extract",
        description=(
            "Fetch and render up to five HTTP/HTTPS pages in stable input order. Output is bounded and " +
            "continued by character offset with the same tool; no hidden result store is used."
        ),
        parameters={
            "type": "object",
            "properties": {
                "urls": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    ]
                },
                "format": {"type": "string", "enum": ["markdown", "text", "html"]},
                "summarize": {"type": "boolean"},
                "focus": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20000},
            },
            "required": ["urls"],
            "additionalProperties": False,
        },
        handler=handle_web_extract,
    )

    current_refs: set[str] = set()

    async def browser_session() -> browser_tools.Session:
        if not CONFIG.browser_enabled:
            raise ToolError("browser_unavailable: browser service is disabled")
        pool = _browser_pool(engine)
        mark_active = getattr(pool, "mark_active", None)
        if callable(mark_active):
            mark_active(agent_id)
        session = await pool.session(agent_id)
        session.dialog_policy = "dismiss"
        session.dialog_text = None
        return session

    async def browser_snapshot_page(
        session: browser_tools.Session,
        *,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        nonlocal current_refs
        raw = await browser_tools.snapshot(
            session,
            offset=offset,
            limit=limit,
            text_offset=offset,
        )
        elements = str(raw.get("elements") or "")
        current_refs = set(re.findall(r"@?(e\d+)\b", elements))

        returned = int(raw.get("element_returned") or 0)
        element_total = int(raw.get("element_count") or 0)
        element_next = offset + returned
        element_has_more = bool(raw.get("element_has_more"))

        body_offset = int(raw.get("body_text_offset") or 0)
        body_end = int(raw.get("body_text_end") or body_offset)
        body_total = int(raw.get("body_text_total_characters") or 0)
        body_has_more = bool(raw.get("body_text_has_more"))
        truncated = element_has_more or body_has_more
        url = str(raw.get("url") or "")
        source_ref = {
            "type": "browser_page",
            "url": url,
            "session": agent_id,
            "element_range": [offset, element_next],
            "body_text_range": [body_offset, body_end],
        }
        payload: dict[str, Any] = {
            "url": url,
            "title": str(raw.get("title") or ""),
            "elements": elements,
            "page_text": str(raw.get("page_text") or ""),
            "offset": offset,
            "limit": limit,
            "offset_unit": "snapshot_elements; page_text uses character offset",
            "returned": returned,
            "element_total": element_total,
            "element_range": [offset, element_next],
            "body_text_offset": body_offset,
            "body_text_end": body_end,
            "body_text_total_characters": body_total,
            "body_text_range": [body_offset, body_end],
            "source_ref": source_ref,
            "truncated": truncated,
        }

        continuations: dict[str, Any] = {}
        if element_has_more:
            continuations["elements"] = _continuation(
                "browser_snapshot", offset=element_next, limit=limit
            )
        if body_has_more:
            continuations["body_text"] = _continuation(
                "browser_snapshot", offset=body_end, limit=limit
            )
        if continuations:
            payload["continuations"] = continuations
            # Preserve the historic singular continuation for generic clients.
            # Prefer the element page while it exists, then continue body text.
            if element_has_more:
                payload["continuation"] = continuations["elements"]
                payload["next_offset"] = element_next
            else:
                payload["continuation"] = continuations["body_text"]
                payload["next_offset"] = body_end
        return payload

    @guard("browser_navigate")
    async def handle_browser_navigate(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        if not CONFIG.browser_enabled:
            return {
                "status": "error",
                "code": "browser_unavailable",
                "message": "Browser service is disabled for this installation.",
            }
        url = browser_tools.check_url(args.get("url"))
        wait_until = str(args.get("wait_until") or "domcontentloaded")
        if wait_until not in {"load", "domcontentloaded", "networkidle", "commit"}:
            raise ToolError("wait_until must be load, domcontentloaded, networkidle, or commit")
        session = await browser_session()
        await browser_tools.act(
            session,
            session.page.goto(
                url,
                wait_until=wait_until,
                timeout=int(CONFIG.browser_timeout_s * 1000),
            ),
            what="navigate",
        )
        return await browser_snapshot_page(session, offset=0, limit=40)

    _register(
        reg,
        name="browser_navigate",
        description=(
            "Navigate the current attempt-owned browser session to an HTTP/HTTPS URL. Returns a bounded " +
            "initial snapshot; use browser_snapshot for further pages."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "wait_until": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle", "commit"],
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        handler=handle_browser_navigate,
    )

    @guard("browser_snapshot")
    async def handle_browser_snapshot(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        if not CONFIG.browser_enabled:
            return {
                "status": "error",
                "code": "browser_unavailable",
                "message": "Browser service is disabled for this installation.",
            }
        offset = _integer(args, "offset", 0, minimum=0, maximum=None)
        limit = _integer(args, "limit", 120, minimum=1, maximum=250)
        return await browser_snapshot_page(await browser_session(), offset=offset, limit=limit)

    _register(
        reg,
        name="browser_snapshot",
        description=(
            "Read the current attempt browser page structure and interactive references. Use offset/limit " +
            "for large snapshots; references are valid only for the current session and latest snapshot."
        ),
        parameters={
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 250},
            },
            "additionalProperties": False,
        },
        handler=handle_browser_snapshot,
    )

    def checked_ref(raw: Any) -> str:
        value = str(raw or "").strip().lstrip("@")
        if not re.fullmatch(r"e\d+", value):
            raise ToolError("ref must look like e3 and come from browser_snapshot")
        if value not in current_refs:
            raise ToolError("browser ref is stale or does not belong to the current session snapshot")
        return value

    @guard("browser_click")
    async def handle_browser_click(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        session = await browser_session()
        ref = checked_ref(args.get("ref"))
        locator = browser_tools.locator(session, ref)
        await browser_tools.act(
            session,
            locator.click(timeout=int(CONFIG.browser_timeout_s * 1000)),
            what="click",
        )
        await session.page.wait_for_timeout(400)
        return await browser_snapshot_page(session, offset=0, limit=40)

    _register(
        reg,
        name="browser_click",
        description="Click a ref from the current session's latest snapshot, then return a bounded new snapshot.",
        parameters={
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
            "additionalProperties": False,
        },
        handler=handle_browser_click,
    )

    @guard("browser_type")
    async def handle_browser_type(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        text = args.get("text")
        if not isinstance(text, str):
            raise ToolError("text must be a string")
        session = await browser_session()
        ref = checked_ref(args.get("ref"))
        locator = browser_tools.locator(session, ref)
        await browser_tools.act(
            session,
            locator.fill(text, timeout=int(CONFIG.browser_timeout_s * 1000)),
            what="type",
        )
        if args.get("submit") is True:
            await browser_tools.act(session, locator.press("Enter"), what="submit")
            await session.page.wait_for_timeout(500)
            return await browser_snapshot_page(session, offset=0, limit=40)
        return {
            "url": str(session.page.url),
            "typed_characters": len(text),
            "message": "Text entered. Use submit=true or browser_click to submit, then refresh the snapshot.",
        }

    _register(
        reg,
        name="browser_type",
        description=(
            "Fill a current-session input ref with text. Optional submit=true presses Enter; stale/cross-session refs are rejected."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "text": {"type": "string"},
                "submit": {"type": "boolean"},
            },
            "required": ["ref", "text"],
            "additionalProperties": False,
        },
        handler=handle_browser_type,
    )

    @guard("browser_scroll")
    async def handle_browser_scroll(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        direction = str(args.get("direction") or "").lower()
        if direction not in {"up", "down"}:
            raise ToolError("direction must be 'up' or 'down'")
        amount = _integer(args, "amount", 800, minimum=1, maximum=20_000)
        session = await browser_session()
        delta = -amount if direction == "up" else amount
        await browser_tools.act(session, session.page.mouse.wheel(0, delta), what="scroll")
        await session.page.wait_for_timeout(300)
        return await browser_snapshot_page(session, offset=0, limit=40)

    _register(
        reg,
        name="browser_scroll",
        description="Scroll the current attempt browser page up or down and return a bounded refreshed snapshot.",
        parameters={
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"]},
                "amount": {"type": "integer", "minimum": 1, "maximum": 20000},
            },
            "required": ["direction"],
            "additionalProperties": False,
        },
        handler=handle_browser_scroll,
    )

    @guard("browser_back")
    async def handle_browser_back(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        del args
        session = await browser_session()
        await browser_tools.act(session, session.page.go_back(), what="back")
        return await browser_snapshot_page(session, offset=0, limit=40)

    _register(
        reg,
        name="browser_back",
        description="Navigate backward in the current attempt-owned browser session and return a bounded snapshot.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handle_browser_back,
    )

    @guard("browser_screenshot")
    async def handle_browser_screenshot(args: dict[str, Any], **_: Any) -> Mapping[str, Any]:
        if not CONFIG.browser_enabled:
            return {
                "status": "error",
                "code": "browser_unavailable",
                "message": "Browser service is disabled for this installation.",
            }
        raw = args.get("path")
        if not isinstance(raw, str) or not raw.strip():
            raw = f"screenshots/{agent_id}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        rel_input = raw.strip()
        if not rel_input.lower().endswith(".png"):
            rel_input += ".png"
        path, rel = _project_path(engine, rel_input, operation="write")
        path.parent.mkdir(parents=True, exist_ok=True)
        session = await browser_session()
        try:
            await session.page.screenshot(
                path=str(path),
                full_page=args.get("full_page") is True,
                timeout=int(CONFIG.browser_timeout_s * 1000),
            )
        except Exception as exc:
            raise ToolError(f"screenshot failed: {str(exc).splitlines()[0][:240]}") from None
        artifact = await asyncio.to_thread(_artifact_fact, path, rel, kind="screenshot")
        if artifact["size"] <= 0:
            raise ToolError("post-screenshot verification failed: image is empty")
        _notify_produced(engine, agent_id, rel)
        return {
            "path": rel,
            "url": str(session.page.url),
            "artifact": artifact,
            "artifacts": [artifact],
            "message": "Screenshot exists in the project and its size/SHA-256 were verified.",
        }

    _register(
        reg,
        name="browser_screenshot",
        description=(
            "Save the current page as a project-relative PNG. The file is statted and hashed after the browser writes it, " +
            "and a verified screenshot artifact is returned."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "full_page": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        handler=handle_browser_screenshot,
    )

    names = tuple(reg.names())
    if names != WORKER_TOOL_NAMES:
        raise RuntimeError(
            f"fixed Worker registry mismatch: expected {WORKER_TOOL_NAMES!r}, got {names!r}"
        )
    return reg


async def aux_client_chat(system: str, user: str) -> str:
    """Optional web-extract summary; failure never discards the fetched source text."""

    from . import aux_client

    aux = runtime_settings.aux_effective()
    configured = bool(aux and aux.get("api_key") and aux.get("base_url") and aux.get("model"))
    return await aux_client.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        api_key=aux["api_key"] if configured else CONFIG.deepseek_api_key,
        base_url=aux["base_url"] if configured else CONFIG.deepseek_base_url,
        model=aux["model"] if configured else CONFIG.deepseek_model,
        timeout_s=CONFIG.web_timeout_s + 20,
        what="web summary",
    )


__all__ = [
    "COORDINATOR",
    "KNOWN_ROLES",
    "WORKER_TOOL_NAMES",
    "build_coordinator_registry",
    "build_worker_registry",
    "close_worker_browser_session",
    "resolve_in_sandbox",
]