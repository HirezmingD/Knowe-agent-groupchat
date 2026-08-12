# knowe v0.11 — Harness C · 三层 Memory
"""
memory_manager.py — Harness Memory（全局公告栏）+ Project Memory（项目上下文）的自动维护。

三层里的第三层（Agent Memory = 每个 Worker 的 Profile）是 Hermes 原生的，不在这里。

一条铁律贯穿全文：**记忆是尽力而为的，绝不阻塞主流程。**
  auxiliary LLM 调不通（没 key、网络断、额度光）→ 跳过这次摘要、记一条 WARNING，
  主回合照样把消息发出去。宁可记忆旧一点，也不能因为记不上账就把回合卡死。

落盘策略：Project Memory 位于项目的 internal_workspace/memory/。其中
  memory/.context.json + memory/context.md 只保存**有界快照**；逐回合输入/产出追加到
  memory/history/.active.jsonl，达到阈值后封成 gzip 段长期保存并供 Agent 主动检索。
  用户业务目录不再出现 .project/；更新时也不用去解析 markdown
  （解析 markdown 是脆的：模型摘要里带个 `##` 就把结构冲了）。
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Mapping

from . import runtime_settings
from .i18n_backend import msg
from .config import CONFIG
from knowe_provenance import (
    current_provenance_dict,
    normalize_provenance,
    unknown_legacy_provenance,
)

from .worker_completion import (
    completion_from_mapping,
    format_completion,
    has_runtime_outcome_metadata,
)

log = logging.getLogger("knowe.memory")

# ── 平台级对话记忆（v1.0.22.1-对齐 B：知知频道沉淀）──
_PLATFORM_MEMORY_MAX = 20
_PLATFORM_MEMORY_HEADER = "# 平台记忆（知知频道对话沉淀）"
_PLATFORM_MEMORY_ATTACH = 3   # 每轮注进知知上下文的最近条数

# ── Project Memory 落点（v0.16：仅在 internal_workspace 内）──
_PROJECT_MEMORY_DIR = "memory"
_CONTEXT_MD = "context.md"
_CONTEXT_JSON = ".context.json"

# ── auxiliary LLM 参数（小调用，只做摘要）──
_AUX_MAX_TOKENS = 200
_AUX_TEMPERATURE = 0.0
_AUX_TIMEOUT_S = 20.0

# ── Project Memory v2：有界快照 + 永久历史 ──
_STATE_SCHEMA = 4
_HISTORY_SCHEMA = 3
_HISTORY_DIR = "history"
_HISTORY_ACTIVE = ".active.jsonl"
_HISTORY_SEGMENT_RE = re.compile(
    r"^segment-(?P<first>\d{12})-(?P<last>\d{12})\.jsonl\.gz$"
)

# 配置字段不存在时（把本文件单独回贴到更老的 Config）仍可运行的安全缺省。
_DEFAULT_FULL_EVERY = 5
_DEFAULT_RECENT_KEEP = 24
_DEFAULT_SEGMENT_RECORDS = 128
_DEFAULT_SEGMENT_BYTES = 512 * 1024
_DEFAULT_SEARCH_MAX = 50
_SUMMARY_CHARS = 280
_STATE_TEXT_CHARS = 2400
_SEARCH_QUERY_CHARS = 240

# ── v0.44.6：长期记忆静默预检索 ──
# query expansion 只是“想起旧事”的辅助信号：宁可本轮没有线索，也不能拖慢主对话。
_PRE_RETRIEVAL_TIMEOUT_S = 3.0
_PRE_RETRIEVAL_MESSAGE_CHARS = 1200
_PRE_RETRIEVAL_KEYWORD_CHARS = 48
_PRE_RETRIEVAL_MIN_KEYWORDS = 2
_PRE_RETRIEVAL_MAX_KEYWORDS = 5

#: auxiliary 调用的注入点：async (system_prompt, user_content) -> str。
#:   传了就用它（测试拿它顶掉真实网络）；没传就按运行时 auxiliary 绑定走 httpx。
AuxCall = Callable[[str, str], Awaitable[str]]


class MemoryManager:
    """Harness Memory + Project Memory 的看门人。所有 update_* 都不抛异常。"""

    def __init__(self, data_dir: Path | str, aux_call: AuxCall | None = None) -> None:
        self.data_dir = Path(data_dir)
        # Harness 层只认 data/harness/。根级旧文件只在初始化时做一次已知路径搬移；
        # 普通读取绝不再双读，否则旧数据会在迁移后继续“复活”。
        self.harness_dir = self.data_dir / "harness"
        self.harness_path = self.harness_dir / "harness_memory.md"
        self._import_legacy_harness_memory()
        self._aux_call = aux_call
        self._harness_lock = asyncio.Lock()
        # 同一项目的历史追加、滚动摘要和快照落盘必须是一笔事务。正常引擎已经用
        # `_memory_tail` 串行，这把锁再挡住重复引擎/测试直调造成的并发写。
        self._project_locks: dict[str, asyncio.Lock] = {}

    def _import_legacy_harness_memory(self) -> None:
        """Move the one known root-level Harness file into the authoritative directory.

        The import is deliberately tiny: source-only moves, target collisions fail loudly,
        and no marker/inventory is retained.  A real mkdir/rename failure propagates through
        the existing backend start-up error path instead of silently falling back.
        """
        legacy = self.data_dir / "harness_memory.md"
        if not legacy.exists():
            return
        if self.harness_path.exists():
            raise FileExistsError(
                msg("memory_manager.py.001") +
                msg("memory_manager.py.002", legacy=legacy, **{"self.harness_path": self.harness_path})
            )
        self.harness_dir.mkdir(parents=True, exist_ok=True)
        os.replace(legacy, self.harness_path)

    # ═══════════════════════════════════════════════════════════
    # 一、Harness Memory（全局公告栏）—— 系统写，Agent 只读
    # ═══════════════════════════════════════════════════════════
    async def update_harness(self, projects_summary: list[dict[str, Any]]) -> None:
        """
        用项目摘要列表**覆盖写** harness_memory.md。

        projects_summary 每项形如 {"project_id", "name", "members", "recent"}。
        纯拼装、不调 LLM —— 便宜且必成。失败也只记日志，不抛。

        [v0.12 D 5d] 同时落一份结构化 JSON（.harness.json），供 read_harness_brief 渲染
        极简版注进知知上下文——省得再去解析 markdown。
        """
        try:
            async with self._harness_lock:
                projects = self._merge_harness_projects(projects_summary or [])
                body = self._render_harness(projects)
                _atomic_write(self.harness_path, body)
                _atomic_write(self._harness_state_path,
                              json.dumps(projects, ensure_ascii=False, indent=2))
        except Exception:
            log.exception(msg("memory_manager.py.003"))

    @property
    def _harness_state_path(self) -> Path:
        return self.harness_dir / ".harness.json"

    @property
    def _deleted_projects_path(self) -> Path:
        return self.harness_dir / ".deleted_projects.json"

    def _load_deleted_projects(self) -> list[dict[str, Any]]:
        """读永久项目墓碑。它们属于 Harness 记录，不会随项目本体删除。"""
        try:
            path = self._deleted_projects_path
            if path.is_file():
                data = json.loads(path.read_text("utf-8"))
                if isinstance(data, list):
                    return [
                        row for row in data
                        if isinstance(row, dict)
                        and isinstance(row.get("project_id"), str)
                        and row.get("project_id")
                    ]
        except (OSError, json.JSONDecodeError):
            log.warning("[memory] 已删除项目记录读取失败", exc_info=True)
        return []

    def deleted_project_ids(self) -> set[str]:
        """返回历史上彻底删除过的项目 id，供分配器永久避让。"""
        return {str(row["project_id"]) for row in self._load_deleted_projects()}

    def _merge_harness_projects(
        self, active_projects: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        deleted = {
            str(row["project_id"]): dict(row)
            for row in self._load_deleted_projects()
        }
        active = [
            dict(row) for row in active_projects
            if isinstance(row, dict)
            and str(row.get("project_id") or "") not in deleted
        ]
        tombstones = sorted(
            deleted.values(), key=lambda row: str(row.get("deleted_at") or ""), reverse=True,
        )
        return active + tombstones

    async def record_deleted_project(self, project_id: str, name: str) -> bool:
        """提交项目删除墓碑，并尽力刷新 Harness 的两个派生视图。

        ``.deleted_projects.json`` 是不可逆删除的逻辑提交点；``harness_memory.md`` 和
        ``.harness.json`` 只是由它派生的人读/模型读投影。旧实现把三次写入包在同一个
        ``try`` 里：墓碑已经原子落盘后，任一投影写失败仍返回 ``False``，调用方就会
        回滚项目目录，造成“数据恢复了、id 却永久标记已删除”的半死状态。

        现在只有墓碑写失败才返回 ``False``。提交后的投影失败会记录日志，并由下一次
        Harness 刷新或删除事务恢复链重建；调用方绝不能再回滚逻辑已提交的删除。
        """
        if not isinstance(project_id, str) or not project_id:
            return False
        async with self._harness_lock:
            try:
                rows = {
                    str(row["project_id"]): dict(row)
                    for row in self._load_deleted_projects()
                }
                rows[project_id] = {
                    "project_id": project_id,
                    "name": str(name or project_id),
                    "deleted": True,
                    "deleted_at": _now_iso(),
                    "recent": msg("memory_manager.py.004"),
                }
                deleted_rows = sorted(
                    rows.values(), key=lambda row: str(row.get("deleted_at") or ""), reverse=True,
                )
                _atomic_write(
                    self._deleted_projects_path,
                    json.dumps(deleted_rows, ensure_ascii=False, indent=2),
                )
            except Exception:
                log.exception(msg("memory_manager.py.005"), project_id)
                return False

            try:
                active = [
                    dict(row) for row in self._load_harness_state()
                    if str(row.get("project_id") or "") not in rows
                    and not bool(row.get("deleted"))
                ]
                merged = active + deleted_rows
                _atomic_write(self.harness_path, self._render_harness(merged))
                _atomic_write(
                    self._harness_state_path,
                    json.dumps(merged, ensure_ascii=False, indent=2),
                )
            except Exception:
                log.exception(
                    msg("memory_manager.py.006"),
                    project_id,
                )
            return True

    def _load_harness_state(self) -> list[dict[str, Any]]:
        """读回结构化的项目摘要列表（read_harness_brief 用）。没有/坏了 → []。"""
        try:
            p = self._harness_state_path
            if p.is_file():
                data = json.loads(p.read_text("utf-8"))
                if isinstance(data, list):
                    return [d for d in data if isinstance(d, dict)]
        except (OSError, json.JSONDecodeError):
            pass
        return []

    def read_harness(self) -> str:
        """读 authoritative Harness Memory。没有 → 一句占位。"""
        try:
            if self.harness_path.is_file():
                return self.harness_path.read_text("utf-8", errors="replace")
        except OSError:
            log.exception(msg("memory_manager.py.007"), self.harness_path)
        return msg("memory_manager.py.008")

    def read_harness_brief(self, max_projects: int = 40) -> str:
        """
        [v0.12 D · 问题五 5d] 极简版公告栏 —— 注进接待（知知）上下文用。

        用尽可能少的 token 说清「平台上现在有哪些项目、各自到哪一步」：一行一个项目，
        `项目名（成员N）— 最近动态`。项目多时只列前 max_projects 个，其余折叠成一句。
        目的：项目再多也不把知知的上下文顶爆（问题 5d 的诉求）。
        """
        projects = self._load_harness_state()
        if not projects:
            return msg("memory_manager.py.009")
        lines: list[str] = []
        for p in projects[:max_projects]:
            name = str(p.get("name") or p.get("project_id") or msg("memory_manager.py.010"))
            if p.get("deleted"):
                lines.append(msg("memory_manager.py.011", name=name))
                continue
            members = p.get("members")
            recent = str(p.get("recent") or "").strip()
            head = name if members is None else msg("memory_manager.py.012", name=name, members=members)
            lines.append(f"{head} — {recent}" if recent else head)
        if len(projects) > max_projects:
            lines.append(msg("memory_manager.py.013", **{"len(projects) - max_projects": len(projects) - max_projects}))
        return "\n".join(lines)

    # ── [v1.0.22.1-对齐 B] 平台级对话记忆（知知频道沉淀）──
    # 独立于 harness_memory.md：公告栏由 update_harness 覆盖写，平台记忆不能被冲掉。
    @property
    def platform_memory_path(self) -> Path:
        return self.harness_dir / "platform_memory.md"

    def append_platform_memory(self, line: str) -> None:
        """追加一条平台级对话记录；有界保留最近 _PLATFORM_MEMORY_MAX 条。

        尽力而为：失败只记日志，绝不抛——知知回合不受影响（usage_sink 同款容错）。
        """
        try:
            lines = self.read_platform_memory_lines()
            lines.append(line)
            body = "\n".join(lines[-_PLATFORM_MEMORY_MAX:])
            _atomic_write(
                self.platform_memory_path,
                _PLATFORM_MEMORY_HEADER + "\n" + body + "\n",
            )
        except Exception:
            log.exception("[memory] 平台记忆写入失败（忽略）")

    def read_platform_memory_lines(self) -> list[str]:
        """读回平台记忆内容行（不含头部）。没有/损坏 → []。"""
        try:
            path = self.platform_memory_path
            if path.is_file():
                return [
                    ln for ln in path.read_text("utf-8", errors="replace").splitlines()
                    if ln.strip() and not ln.startswith("#")
                ]
        except OSError:
            pass
        return []

    def read_platform_memory_brief(self, attach: int = _PLATFORM_MEMORY_ATTACH) -> str:
        """最近 attach 条平台记忆，拼成一段注进知知上下文。空 → 空串。"""
        lines = self.read_platform_memory_lines()
        if not lines:
            return ""
        return "\n".join(lines[-attach:])

    @staticmethod
    def _render_harness(projects: list[dict[str, Any]]) -> str:
        """
        [v0.12 D 5d] 紧凑渲染：一行一个项目，尽量省 token。
        格式：`- 项目名（成员N）— 最近动态`。给人看的 md 和给知知的 brief 同源同风格。
        """
        now = _now_iso()
        out = [
            msg("memory_manager.py.014"),
            msg("memory_manager.py.015", now=now, **{"len(projects)": len(projects)}),
            "",
        ]
        if not projects:
            out.append(msg("memory_manager.py.016"))
        for p in projects:
            name = str(p.get("name") or p.get("project_id") or msg("memory_manager.py.010"))
            if p.get("deleted"):
                out.append(msg("memory_manager.py.017", name=name))
                continue
            members = p.get("members")
            head = f"- {name}" if members is None else msg("memory_manager.py.018", name=name, members=members)
            recent = str(p.get("recent") or "").strip()
            out.append(f"{head} — {recent}" if recent else head)
        return "\n".join(out) + "\n"

    # ═══════════════════════════════════════════════════════════
    # 二、Project Memory v2（有界快照 + 可检索长期历史）
    # ═══════════════════════════════════════════════════════════
    def project_context_path(self, internal_workspace: Path | str) -> Path:
        return Path(internal_workspace) / _PROJECT_MEMORY_DIR / _CONTEXT_MD

    def _json_path(self, internal_workspace: Path | str) -> Path:
        return Path(internal_workspace) / _PROJECT_MEMORY_DIR / _CONTEXT_JSON

    def _history_path(self, internal_workspace: Path | str) -> Path:
        return Path(internal_workspace) / _PROJECT_MEMORY_DIR / _HISTORY_DIR

    def _active_history_path(self, internal_workspace: Path | str) -> Path:
        return self._history_path(internal_workspace) / _HISTORY_ACTIVE

    def _project_lock(self, internal_workspace: Path | str) -> asyncio.Lock:
        key = str(Path(internal_workspace).expanduser().resolve())
        lock = self._project_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._project_locks[key] = lock
        return lock

    def read_project_context(self, internal_workspace: Path | str) -> str:
        """Read the bounded Project Memory snapshot from the internal workspace only."""
        path = self.project_context_path(internal_workspace)
        try:
            if path.is_file():
                return path.read_text("utf-8", errors="replace")
        except OSError:
            log.exception("读 Project Memory 失败：%s", path)
        return ""

    def read_project_state(self, internal_workspace: Path | str) -> dict[str, Any]:
        """读取结构化快照，并用历史尾记录修正可能落后的累计回合数。"""
        try:
            internal = Path(internal_workspace)
            state = self._load_state(internal)

            stats = _history_stats(self._history_path(internal))
            state["turn_count"] = max(
                _as_nonnegative_int(state.get("turn_count")),
                _as_nonnegative_int(stats.get("max_turn")),
            )
            _apply_history_stats(state, stats)
            return dict(state)
        except Exception:
            log.exception(msg("memory_manager.py.019"))
            return _empty_state()

    def project_turn_count(self, internal_workspace: Path | str) -> int:
        """返回磁盘上的累计回合数；引擎初始化/重启时必须从这里恢复。"""
        state = self.read_project_state(internal_workspace)
        return _as_nonnegative_int(state.get("turn_count"))

    async def update_project_context(
        self,
        internal_workspace: Path | str,
        turn_result: dict[str, Any],
        turn_count: int,
        force_full: bool = False,
        members: int | None = None,
    ) -> None:
        """追加一轮长期历史，并刷新有界快照。失败只记日志，绝不阻塞主流程。

        v2 的关键不变量：
        1. ``turn_count`` 只增不减；传入旧值绝不会覆盖磁盘累计值。
        2. 每轮先写 append-only JSONL 历史，再写 ``.context.json`` / ``context.md`` 快照。
        3. 快照里的 ``recent`` 有界；完整历史通过封段 gzip 永久保存并可主动检索。
        """
        try:
            internal = Path(internal_workspace)
            async with self._project_lock(internal):
                state = self._load_state(internal)
                self._ensure_history_initialized(internal, state)
                stats = _history_stats(self._history_path(internal))

                persisted_turn = max(
                    _as_nonnegative_int(state.get("turn_count")),
                    _as_nonnegative_int(stats.get("max_turn")),
                )
                requested_turn = _as_nonnegative_int(turn_count)
                effective_turn = max(persisted_turn, requested_turn)

                input_text = _turn_input(turn_result)
                output_text = _turn_excerpt(turn_result)
                internal_turn = bool(
                    isinstance(turn_result, dict) and turn_result.get("_memory_internal")
                )
                material = _turn_material(input_text, output_text, internal_turn)
                actor_id = _clean_inline(
                    (turn_result or {}).get("_memory_agent_id") if isinstance(turn_result, dict) else "",
                    80,
                ) or "unknown"
                actor_name = _clean_inline(
                    (turn_result or {}).get("_memory_agent_name") if isinstance(turn_result, dict) else "",
                    120,
                )
                requested_kind = _clean_inline(
                    (turn_result or {}).get("_memory_kind") if isinstance(turn_result, dict) else "",
                    40,
                )
                kind = requested_kind or ("internal" if internal_turn else "conversation")
                fingerprint = _history_fingerprint(
                    actor_id, kind, input_text, output_text,
                )
                raw_provenance = (turn_result or {}).get("_provenance") if isinstance(turn_result, dict) else None
                provenance = normalize_provenance(
                    raw_provenance if isinstance(raw_provenance, Mapping)
                    else current_provenance_dict()
                ).to_dict()
                raw_lineage = (turn_result or {}).get("_lineage") if isinstance(turn_result, dict) else None
                lineage = {
                    str(key): str(value or "")
                    for key, value in (raw_lineage.items() if isinstance(raw_lineage, Mapping) else [])
                    if str(key) in {"task_id", "run_id", "delivery_id", "project_id"}
                }

                record: dict[str, Any] | None = None
                appended = False
                if material:
                    last = _last_history_record(self._history_path(internal))
                    # 崩溃恢复：历史已追加、快照还没 replace 时，同一更新可能被重放。
                    # turn + 内容指纹都相同才视为重放；用户真的重复说同一句时，引擎传入的
                    # turn 已经是新值，所以仍会形成一条新记录。
                    replay = bool(
                        requested_turn > 0
                        and requested_turn <= persisted_turn
                        and last
                        and _as_nonnegative_int(last.get("n")) == requested_turn
                        and str(last.get("h") or "") == fingerprint
                    )
                    if replay:
                        record = last
                        effective_turn = max(
                            persisted_turn, _as_nonnegative_int(last.get("n")),
                        )
                    else:
                        effective_turn = (
                            requested_turn if requested_turn > persisted_turn
                            else persisted_turn + 1
                        )
                        # 先用确定性摘要把原始输入/产出落盘，再做任何网络调用。这样即使
                        # auxiliary 卡住、进程被杀或断电，这一轮长期历史也已经安全存在。
                        record = _make_history_record(
                            seq=_as_nonnegative_int(stats.get("last_seq")) + 1,
                            turn=effective_turn,
                            at=_now_iso(),
                            actor_id=actor_id,
                            actor_name=actor_name,
                            kind=kind,
                            summary=_fallback_memory_line(
                                input_text, output_text, internal_turn,
                            ),
                            input_text=input_text,
                            output_text=output_text,
                            fingerprint=fingerprint,
                            provenance=provenance,
                            lineage=lineage,
                        )
                        _append_history_record(self._history_path(internal), record)
                        appended = True

                    # 历史已持久化后再尝试生成更顺口的近期摘要；失败只影响快照文案，
                    # 不影响可检索原文。崩在这里时，启动期会从历史尾自动修复 recent。
                    recent_record = record
                    one = await self._auxiliary_summary(material, mode="incremental")
                    if one and record is not None:
                        recent_record = dict(record)
                        recent_record["s"] = _clean_inline(one, _SUMMARY_CHARS)

                    if recent_record is not None:
                        recent = [
                            _clean_inline(x, 420)
                            for x in (state.get("recent") or [])
                            if str(x).strip()
                        ]
                        line = _recent_line(recent_record)
                        memory_id = _memory_id(_as_nonnegative_int(recent_record.get("i")))
                        replaced = False
                        for idx, old_line in enumerate(recent):
                            if memory_id in old_line:
                                recent[idx] = line
                                replaced = True
                                break
                        if not replaced:
                            recent.append(line)
                        state["recent"] = recent[-_recent_keep():]

                state["turn_count"] = max(
                    effective_turn,
                    _as_nonnegative_int(state.get("turn_count")),
                )
                if members is not None:
                    state["members"] = members

                last_attempt = _as_nonnegative_int(state.get("last_full_attempt_turn"))
                do_full = bool(
                    force_full
                    or (
                        record is not None
                        and effective_turn > 0
                        and effective_turn - last_attempt >= _full_every()
                    )
                )
                if do_full and (
                    material or state.get("state_text") or state.get("recent")
                ):
                    state["last_full_attempt_turn"] = effective_turn
                    recent_material = "\n".join(
                        f"- {line}" for line in (state.get("recent") or [])[-_recent_keep():]
                    )
                    full_material = (
                        msg("memory_manager.py.073", **{"state_text": state.get('state_text') or msg('memory_manager.py.020')})
                        + msg("memory_manager.py.074", **{"recent_material": recent_material or msg('memory_manager.py.020')})
                        + msg("memory_manager.py.075", **{"material": material or msg('memory_manager.py.021')})
                    )
                    full = await self._auxiliary_summary(full_material, mode="full")
                    if full:
                        state["state_text"] = _clip_text(full.strip(), _STATE_TEXT_CHARS)
                        state["last_full_turn"] = effective_turn
                    else:
                        log.info(msg("memory_manager.py.022"))

                state["provenance"] = provenance
                state["lineage"] = lineage
                state["updated_at"] = _now_iso()
                state["schema_version"] = _STATE_SCHEMA
                self._save(internal, state)
                if appended:
                    log.debug(
                        "[memory] appended %s turn=%s",
                        _memory_id(_as_nonnegative_int(record.get("i"))) if record else "?",
                        effective_turn,
                    )
        except Exception:
            log.exception(msg("memory_manager.py.023"))

    def ensure_project_context(
        self, internal_workspace: Path | str, members: int | None = None,
    ) -> None:
        """创建/升级 internal Project Memory；旧 schema 快照会导入长期历史。"""
        try:
            internal = Path(internal_workspace)
            state = self._load_state(internal)
            self._ensure_history_initialized(internal, state)
            if members is not None:
                state["members"] = members
            if not state.get("updated_at"):
                state["updated_at"] = _now_iso()
            state["schema_version"] = _STATE_SCHEMA
            self._save(internal, state)
        except Exception:
            log.exception("ensure_project_context 失败（忽略）")

    async def record_deleted_agent(
        self,
        internal_workspace: Path | str,
        agent_id: str,
        name: str,
        role: str,
    ) -> bool:
        """清除该 Agent 的 Project Memory 明细，只保留一条“已被删除”项目级墓碑。

        这是身份删除和项目历史可解释性的边界：该成员作为发言者的原始记录、摘要索引
        与滚动状态会被移除；项目仍记得“曾有此成员且已被删除”，不会把删除误解为归档。
        """
        if not isinstance(agent_id, str) or not agent_id:
            return False
        try:
            internal = Path(internal_workspace)
            async with self._project_lock(internal):
                state = self._load_state(internal)
                self._ensure_history_initialized(internal, state)
                history_dir = self._history_path(internal)
                records = list(_iter_history_records(history_dir))
                clean_name = _clean_inline(name, 120) or agent_id
                clean_role = _clean_inline(role, 120) or msg("memory_manager.py.024")

                kept = [
                    record for record in records
                    if str(record.get("a") or "") != agent_id
                    and not (
                        clean_name
                        and str(record.get("x") or "").strip() == clean_name
                    )
                ]
                _rewrite_history_records(history_dir, kept)

                # 滚动摘要不是长期真源；逐行剔除身份痕迹，再由保留下来的历史重建 recent。
                state["state_text"] = _remove_identity_lines(
                    str(state.get("state_text") or ""), agent_id, clean_name,
                )
                state["recent"] = []
                stats = _history_stats(history_dir)
                summary = msg("memory_manager.py.025", clean_name=clean_name, clean_role=clean_role)
                tombstone = _make_history_record(
                    seq=_as_nonnegative_int(stats.get("last_seq")) + 1,
                    turn=0,
                    at=_now_iso(),
                    actor_id="system",
                    actor_name=msg("memory_manager.py.026"),
                    kind="agent_deleted",
                    summary=summary,
                    input_text="",
                    output_text=summary,
                    fingerprint=_history_fingerprint(
                        "system", "agent_deleted", agent_id, summary,
                    ),
                    provenance=current_provenance_dict(),
                    lineage={"agent_id": agent_id},
                )
                _append_history_record(history_dir, tombstone)
                if state.get("members") is not None:
                    state["members"] = max(0, _as_nonnegative_int(state.get("members")) - 1)
                state["updated_at"] = _now_iso()
                state["schema_version"] = _STATE_SCHEMA
                self._save(internal, state)
            return True
        except Exception:
            log.exception(msg("memory_manager.py.027"), agent_id)
            return False

    def search_project_history(
        self,
        internal_workspace: Path | str,
        *,
        query: str = "",
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 12,
        order: str = "newest",
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """按关键词、时间或先后顺序检索完整 Project Memory 历史。

        ``query`` 为空即浏览；``order=oldest`` 可回答“最开始说了什么”。结果只给摘要
        与命中片段，完整输入/产出再用 :meth:`read_project_history` 按 ``memory_id`` 展开。
        有更多结果时把 ``next_cursor`` 原样传回 ``cursor``，可无状态翻遍完整历史。
        """
        internal = Path(internal_workspace)
        state = self._load_state(internal)
        # 只读工具也要让尚未产生新回合的老项目完成一次迁移，否则它仍然只能看到快照。
        changed = self._ensure_history_initialized(internal, state)
        if changed or not self._json_path(internal).is_file():
            if not state.get("updated_at"):
                state["updated_at"] = _now_iso()
            self._save(internal, state)

        cleaned_query = " ".join(str(query or "").split())[:_SEARCH_QUERY_CHARS]
        start_dt = _parse_time_bound(start_time, is_end=False)
        end_dt = _parse_time_bound(end_time, is_end=True)
        if start_dt is not None and end_dt is not None and start_dt > end_dt:
            raise ValueError(msg("memory_manager.py.028"))

        normalized_order = str(order or "newest").strip().lower()
        if normalized_order in {"desc", "latest", "newest", "new"}:
            normalized_order = "newest"
        elif normalized_order in {"asc", "earliest", "oldest", "old"}:
            normalized_order = "oldest"
        else:
            raise ValueError(msg("memory_manager.py.029"))

        try:
            wanted = int(limit)
        except (TypeError, ValueError):
            wanted = 12
        wanted = min(_search_max(), max(1, wanted))
        cursor_seq = _parse_memory_cursor(cursor)

        rows: list[dict[str, Any]] = []
        has_more = False
        for rec in _iter_history_records(
            self._history_path(internal), reverse=(normalized_order == "newest"),
        ):
            seq = _as_nonnegative_int(rec.get("i"))
            if cursor_seq is not None:
                if normalized_order == "newest" and seq >= cursor_seq:
                    continue
                if normalized_order == "oldest" and seq <= cursor_seq:
                    continue
            if not _record_in_time(rec, start_dt, end_dt):
                continue
            if not _record_matches_query(rec, cleaned_query):
                continue
            if len(rows) >= wanted:
                has_more = True
                break
            rows.append(_record_preview(rec, cleaned_query))

        current = self.read_project_state(internal)
        result: dict[str, Any] = {
            "query": cleaned_query,
            "order": normalized_order,
            "start_time": start_time or "",
            "end_time": end_time or "",
            "cursor": cursor or "",
            "results": rows,
            "count": len(rows),
            "has_more": has_more,
            "next_cursor": rows[-1]["memory_id"] if has_more and rows else "",
            "turn_count": _as_nonnegative_int(current.get("turn_count")),
            "history_records": _as_nonnegative_int(current.get("history_records")),
            "coverage_note": _coverage_note(current),
        }
        if not rows:
            result["message"] = (
                msg("memory_manager.py.030")
                if current.get("history_records") else
                msg("memory_manager.py.031")
            )
        return result

    def read_project_history(
        self,
        internal_workspace: Path | str,
        reference: str | int,
    ) -> dict[str, Any]:
        """按 ``memory_id``（推荐）或回合号读取一条完整长期记忆。"""
        internal = Path(internal_workspace)
        state = self._load_state(internal)
        if self._ensure_history_initialized(internal, state):
            if not state.get("updated_at"):
                state["updated_at"] = _now_iso()
            self._save(internal, state)

        raw = str(reference).strip()
        if not raw:
            raise ValueError(msg("memory_manager.py.076"))

        record: dict[str, Any] | None = None
        memory_match = re.fullmatch(r"[mM](\d{1,12})", raw)
        turn_match = re.fullmatch(r"(?:turn\s*[:#]?\s*|#)?(\d+)", raw, re.I)
        if memory_match:
            record = _record_by_seq(
                self._history_path(internal), int(memory_match.group(1)),
            )
        elif turn_match:
            turn = int(turn_match.group(1))
            for rec in _iter_history_records(self._history_path(internal), reverse=True):
                if _as_nonnegative_int(rec.get("n")) == turn:
                    record = rec
                    break
            # 纯数字既可能是回合号，也可能是用户手抄了 memory_id 的数字部分。
            if record is None and raw.isdigit():
                record = _record_by_seq(self._history_path(internal), int(raw))
        else:
            raise ValueError(msg("memory_manager.py.077"))

        current = self.read_project_state(internal)
        if record is None:
            return {
                "found": False,
                "reference": raw,
                "message": msg("event.memory_manager.01"),
                "coverage_note": _coverage_note(current),
            }
        return {
            "found": True,
            "reference": raw,
            "record": _record_detail(record),
            "coverage_note": _coverage_note(current),
        }

    # ── 结构化快照 / 历史迁移 ──
    def _load_state(self, internal_workspace: Path) -> dict[str, Any]:
        path = self._json_path(internal_workspace)
        try:
            if path.is_file():
                data = json.loads(path.read_text("utf-8"))
                if isinstance(data, dict):
                    return _normalize_state(data)
        except (OSError, json.JSONDecodeError):
            log.warning("[memory] .context.json 读失败/损坏 —— 从可用历史修复")
        return _empty_state()

    def _save(self, internal_workspace: Path, state: dict[str, Any]) -> None:
        normalized = _normalize_state(state)
        history_dir = self._history_path(internal_workspace)
        _reconcile_recent_from_history(normalized, history_dir)
        stats = _history_stats(history_dir)
        normalized["turn_count"] = max(
            _as_nonnegative_int(normalized.get("turn_count")),
            _as_nonnegative_int(stats.get("max_turn")),
        )
        _apply_history_stats(normalized, stats)
        normalized["schema_version"] = _STATE_SCHEMA
        if not normalized.get("updated_at"):
            normalized["updated_at"] = _now_iso()

        pj = self._json_path(internal_workspace)
        pj.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(pj, json.dumps(normalized, ensure_ascii=False, indent=2))
        _atomic_write(self.project_context_path(internal_workspace), _render_project(normalized))

    def _ensure_history_initialized(
        self, internal_workspace: Path, state: dict[str, Any],
    ) -> bool:
        """把 v1 快照中尚存的内容幂等导入历史；已丢失的更早内容不会被伪造。"""
        history_dir = self._history_path(internal_workspace)
        stats = _history_stats(history_dir)
        history_records = _as_nonnegative_int(stats.get("records"))
        has_snapshot = bool(state.get("recent") or str(state.get("state_text") or "").strip())
        initialized = bool(state.get("history_initialized"))
        legacy_turn = _as_nonnegative_int(state.get("turn_count"))
        changed = False

        # 已完成初始化时，历史文件是长期真源；只补齐可能来自早期 v2 的元数据。
        if initialized:
            if history_records:
                if not state.get("history_capture_started_at"):
                    state["history_capture_started_at"] = stats.get("first_at") or _now_iso()
                    changed = True
                if not _as_nonnegative_int(state.get("history_complete_from_turn")):
                    state["history_complete_from_turn"] = 1
                    changed = True
            return changed

        # 没有任何旧快照的新项目：只落初始化标记，不制造一条“空记忆”。
        if not has_snapshot and not history_records:
            state["history_initialized"] = True
            state["history_capture_started_at"] = _now_iso()
            state["history_complete_from_turn"] = 1
            state["history_legacy_imported"] = False
            state["history_legacy_gap"] = False
            return True

        # 历史存在但快照为空，通常是“历史已先写、快照元数据尚未落盘”的断电缝隙。
        # 直接认领现有历史；若磁盘 turn_count 明显高于历史尾，则如实标记旧史缺口。
        if history_records and not has_snapshot:
            max_turn = _as_nonnegative_int(stats.get("max_turn"))
            legacy_gap = bool(legacy_turn > max_turn)
            state["history_initialized"] = True
            state["history_capture_started_at"] = stats.get("first_at") or _now_iso()
            state["history_complete_from_turn"] = (legacy_turn + 1) if legacy_gap else 1
            state["history_legacy_gap"] = legacy_gap
            return True

        # 有旧快照：即使上一次迁移在中途崩溃，也按 fingerprint 跳过已写项、继续补齐。
        existing = list(_iter_history_records(history_dir)) if history_records else []
        existing_fingerprints = {
            str(rec.get("h") or "") for rec in existing if str(rec.get("h") or "")
        }
        existing_legacy = any(str(rec.get("k") or "") == "legacy_snapshot" for rec in existing)
        imported = 0
        legacy_at = str(state.get("updated_at") or "").strip() or _file_timestamp(
            self._json_path(internal_workspace)
        )
        seen: set[str] = set()
        snapshots: list[tuple[str, str]] = []
        state_text = _clip_text(
            str(state.get("state_text") or "").strip(), _STATE_TEXT_CHARS,
        )
        if state_text:
            snapshots.append((
                msg("memory_manager.py.032") + _fallback_line(state_text),
                state_text,
            ))
        for item in state.get("recent") or []:
            body = _clip_text(str(item), 420)
            if body:
                snapshots.append((body, body))

        next_seq = _as_nonnegative_int(stats.get("last_seq")) + 1
        for summary, body in snapshots:
            key = " ".join(body.split()).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            fingerprint = _history_fingerprint("legacy", "legacy_snapshot", "", body)
            if fingerprint in existing_fingerprints:
                continue
            record = _make_history_record(
                seq=next_seq,
                turn=0,               # 老 turn_count 已被重启污染，不能伪装成精确回合号。
                at=legacy_at,
                actor_id="legacy",
                actor_name=msg("memory_manager.py.033"),
                kind="legacy_snapshot",
                summary=summary,
                input_text="",
                output_text=body,     # 把升级时仍可见的旧快照正文完整保留下来。
                fingerprint=fingerprint,
                provenance=unknown_legacy_provenance().to_dict(),
                lineage={},
            )
            record["legacy_truncated"] = True
            _append_history_record(history_dir, record)
            existing_fingerprints.add(fingerprint)
            next_seq += 1
            imported += 1

        state["history_initialized"] = True
        state["history_capture_started_at"] = (
            str(state.get("history_capture_started_at") or "").strip() or _now_iso()
        )
        state["history_complete_from_turn"] = max(1, legacy_turn + 1)
        state["history_legacy_imported"] = bool(existing_legacy or imported)
        state["history_legacy_gap"] = bool(legacy_turn or has_snapshot)
        return True

    # ═══════════════════════════════════════════════════════════
    # 三、v0.44.6 静默预检索：辅助模型只做 query expansion
    # ═══════════════════════════════════════════════════════════
    async def extract_retrieval_keywords(
        self,
        message: str,
        *,
        timeout_s: float = _PRE_RETRIEVAL_TIMEOUT_S,
    ) -> list[str]:
        """从用户最新消息提取 2~5 个长期记忆检索词；任何失败都返回 ``[]``。

        这一步只做 query expansion，不读写 Project Memory。输入过短、只是寒暄/确认、
        auxiliary 未绑定、输出不合法或调用超时，都会完全静默地跳过。调用方据此决定
        是否继续本地检索；主对话绝不依赖它成功。
        """
        clean = _clip_text(
            " ".join(str(message or "").split()), _PRE_RETRIEVAL_MESSAGE_CHARS,
        )
        if not _retrieval_message_has_signal(clean):
            return []

        system = (
            msg("memory_manager.py.034") +
            msg("memory_manager.py.035") +
            msg("memory_manager.py.036") +
            msg("memory_manager.py.037") +
            msg("memory_manager.py.038") +
            msg("memory_manager.py.039") +
            msg("memory_manager.py.078", **{"mm040": msg("memory_manager.py.040")})
        )
        try:
            timeout = min(10.0, max(0.05, float(timeout_s)))
        except (TypeError, ValueError, OverflowError):
            timeout = _PRE_RETRIEVAL_TIMEOUT_S

        try:
            raw = await asyncio.wait_for(self._call_llm(system, clean), timeout=timeout)
        except asyncio.TimeoutError:
            log.debug(msg("memory_manager.py.041"), timeout)
            return []
        except asyncio.CancelledError:
            raise
        except Exception:
            # 用户侧必须完全无感；debug 日志只供排障，不把辅助层失败抬成主流程告警。
            log.debug(msg("memory_manager.py.042"), exc_info=True)
            return []

        keywords = _parse_retrieval_keywords(raw)
        if len(keywords) < _PRE_RETRIEVAL_MIN_KEYWORDS:
            return []
        return keywords[:_PRE_RETRIEVAL_MAX_KEYWORDS]

    # ═══════════════════════════════════════════════════════════
    # 四、auxiliary LLM —— 小调用做摘要，失败返回空串（绝不抛）
    # ═══════════════════════════════════════════════════════════
    async def _auxiliary_summary(self, context: str, mode: str) -> str:
        """
        调当前生效的辅助模型（max_tokens=200, temperature=0）做摘要。
        **失败返回空串，不抛异常。**
        """
        if mode == "full":
            system = (
                msg("memory_manager.py.043") +
                msg("memory_manager.py.044") +
                msg("memory_manager.py.045")
            )
        else:
            system = (
                msg("memory_manager.py.046") +
                msg("memory_manager.py.047") +
                msg("memory_manager.py.048")
            )
        try:
            text = await self._call_llm(system, context)
            return (text or "").strip()
        except Exception:
            log.warning(msg("memory_manager.py.049"), mode)
            return ""

    async def _call_llm(self, system: str, user: str) -> str:
        """真正打 API 的地方。注入了 aux_call 就用它（测试）；否则 httpx 直连。"""
        if self._aux_call is not None:
            return await self._aux_call(system, user)

        # [v0.44.5] 摘要属于 auxiliary 通道，必须跟随设置面板的 aux_model。
        # 只有用户从未配置过可用的辅助绑定时，才兼容老部署的 DEEPSEEK_* 环境变量。
        aux = runtime_settings.aux_effective()
        aux_ok = bool(
            aux
            and aux.get("api_key")
            and aux.get("base_url")
            and aux.get("model")
        )
        api_key = str(
            aux["api_key"] if aux_ok else getattr(CONFIG, "deepseek_api_key", "")
        )
        base = str(
            aux["base_url"] if aux_ok else getattr(CONFIG, "deepseek_base_url", "")
        ).rstrip("/")
        model = str(
            aux["model"] if aux_ok else getattr(CONFIG, "deepseek_model", "")
        )
        if not (api_key and base and model):
            return ""                         # 绑定不完整 → 视作不可用（降级）
        try:
            import httpx
        except ImportError:
            return ""

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        async with httpx.AsyncClient(timeout=_AUX_TIMEOUT_S) as cli:
            r = await cli.post(
                base + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            return (data["choices"][0]["message"]["content"] or "").strip()


# ═══════════════════════════════════════════════════════════════
# 模块级小工具
# ═══════════════════════════════════════════════════════════════
def _cfg_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(getattr(CONFIG, name, default))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


def _recent_keep() -> int:
    return _cfg_int("memory_recent_keep", _DEFAULT_RECENT_KEEP, 1, 200)


def _full_every() -> int:
    return _cfg_int("memory_full_every", _DEFAULT_FULL_EVERY, 1, 1000)


def _segment_records() -> int:
    return _cfg_int("memory_segment_records", _DEFAULT_SEGMENT_RECORDS, 1, 10000)


def _segment_bytes() -> int:
    return _cfg_int("memory_segment_bytes", _DEFAULT_SEGMENT_BYTES, 1024, 64 * 1024 * 1024)


def _search_max() -> int:
    return _cfg_int("memory_search_max", _DEFAULT_SEARCH_MAX, 1, 200)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _file_timestamp(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return _now_iso()


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + ``os.replace``；读者永远只会看到旧版或完整新版。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{id(text):x}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        _fsync_directory(path.parent)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _remove_identity_lines(text: str, agent_id: str, name: str) -> str:
    """从滚动摘要中删除含精确身份标记的整行，避免留下半截个人资料。"""
    id_pattern = None
    if agent_id:
        # id 的边界字符与工具/花名册中的技术 id 约定一致。这样 ``pm_1`` 不会
        # 命中 ``pm_10``，也不会因摘要里出现相似 id 而误删另一位成员的状态。
        id_pattern = re.compile(
            rf"(?<![A-Za-z0-9_.-]){re.escape(agent_id)}(?![A-Za-z0-9_.-])"
        )
    clean_name = str(name or "").strip()
    if id_pattern is None and not clean_name:
        return _clip_text(text, _STATE_TEXT_CHARS)
    lines = [
        line for line in str(text or "").splitlines()
        if not (
            (id_pattern is not None and id_pattern.search(line))
            or (clean_name and clean_name in line)
        )
    ]
    return _clip_text("\n".join(lines).strip(), _STATE_TEXT_CHARS)


def _rewrite_history_records(history_dir: Path, records: list[dict[str, Any]]) -> None:
    """把 Project Memory 历史重写为给定记录集，清除旧活动文件与压缩段。"""
    history_dir.mkdir(parents=True, exist_ok=True)
    active = history_dir / _HISTORY_ACTIVE
    for path in [active, *_segment_paths(history_dir)]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    if records:
        for record in sorted(records, key=lambda row: _as_nonnegative_int(row.get("i"))):
            _append_history_record(history_dir, record)
    _fsync_directory(history_dir)


def _fsync_directory(path: Path) -> None:
    """Best-effort directory fsync so a completed replace/create survives power loss."""
    fd: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(path, flags)
        os.fsync(fd)
    except OSError:
        # Windows and some filesystems do not allow opening/fsyncing directories.
        pass
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _as_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _clean_inline(value: Any, limit: int) -> str:
    return _clip_text(" ".join(str(value or "").split()), limit)


# 关键词输出的防御性清洗。只挡“任何记录都会命中”的空泛词；
# “长期记忆”“Project Memory”这类具体功能名不会因为包含“记忆/项目”而被误杀。
_RETRIEVAL_STOPWORDS = frozenset({
    "项目", "当前项目", "这个项目", "问题", "这个问题", "需求", "这个需求",
    "系统", "功能", "代码", "文件", "内容", "事情", "任务", "方案", "处理",
    "实现", "修改", "改动", "修复", "优化", "对话", "用户", "助手", "记忆",
    "之前", "以前", "上次", "刚才", "相关", "关于", "需要", "可以", "是否",
    "什么", "怎么", "为什么", "帮我", "看看", "一下",
    "project", "issue", "problem", "requirement", "feature", "system", "code", "file",
    "content", "task", "solution", "change", "fix", "previous", "before", "memory",
})
_RETRIEVAL_LOW_SIGNAL_RE = re.compile(
    r"^(?:好|好的|好呀|行|可以|知道了|收到|明白|嗯|哦|谢谢|多谢|辛苦了|继续|开始|" +
    r"ok|okay|thanks|thank\s+you|go\s+ahead|continue)[。.!！?？~～\s]*$",
    re.I,
)


def _retrieval_message_has_signal(message: str) -> bool:
    """便宜的前置闸：明显没有主题时，连 auxiliary 调用都不发。"""
    clean = str(message or "").strip()
    if not clean or _RETRIEVAL_LOW_SIGNAL_RE.fullmatch(clean):
        return False
    compact = re.sub(r"\s+", "", clean)
    if len(compact) < 4:
        return False
    return bool(re.search(r"[A-Za-z0-9\u3400-\u9fff]", compact))


def _normalize_retrieval_keyword(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    # 兼容模型偶尔带回的 markdown 序号、标签和引号。
    raw = re.sub(r"^\s*(?:[-*•]+|\d+[.)、])\s*", "", raw)
    raw = re.sub(r"^(?:关键词|keyword|term)\s*[:：]\s*", "", raw, flags=re.I)
    raw = raw.strip(" \t\r\n`'\"“”‘’[]()（）{}<>《》,，;；|/。.!！?？:：")
    clean = " ".join(raw.split())
    if not clean or len(clean) > _PRE_RETRIEVAL_KEYWORD_CHARS:
        return ""
    folded = clean.casefold()
    if folded in _RETRIEVAL_STOPWORDS:
        return ""
    if re.fullmatch(r"[\W_]+", clean, re.UNICODE) or re.fullmatch(r"\d+", clean):
        return ""
    # 单个汉字/拉丁字母通常过宽；带数字的错误码、版本号则保留。
    semantic = re.sub(r"[^A-Za-z0-9\u3400-\u9fff]+", "", clean)
    if len(semantic) < 2:
        return ""
    return clean


def _parse_retrieval_keywords(raw: Any) -> list[str]:
    """兼容严格 JSON、代码围栏、对象包裹和朴素逐行列表，最终只返回清洗后的短语。"""
    text = str(raw or "").strip()
    if not text:
        return []
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.I | re.S)
    if fenced:
        text = fenced.group(1).strip()

    parsed: Any = None
    candidates = [text]
    array_match = re.search(r"\[[\s\S]*?\]", text)
    if array_match and array_match.group(0) != text:
        candidates.append(array_match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            break
        except (TypeError, json.JSONDecodeError):
            continue

    values: list[Any]
    if isinstance(parsed, dict):
        picked = next(
            (parsed.get(key) for key in ("keywords", "terms", "queries", "query") if key in parsed),
            [],
        )
        values = picked if isinstance(picked, list) else [picked]
    elif isinstance(parsed, list):
        values = parsed
    elif isinstance(parsed, str):
        values = [parsed]
    else:
        # 最后兜底只按明确分隔符拆，不按普通空格拆，避免把一个短语拆碎。
        values = re.split(r"[\r\n,，;；|]+", text)

    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = _normalize_retrieval_keyword(value)
        key = keyword.casefold()
        if not keyword or key in seen:
            continue
        seen.add(key)
        out.append(keyword)
        if len(out) >= _PRE_RETRIEVAL_MAX_KEYWORDS:
            break
    return out


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": _STATE_SCHEMA,
        "state_text": "",
        "recent": [],
        "turn_count": 0,
        "members": None,
        "last_full_turn": 0,
        "last_full_attempt_turn": 0,
        "history_initialized": False,
        "history_records": 0,
        "history_segments": 0,
        "history_first_seq": 0,
        "history_last_seq": 0,
        "history_complete_from_turn": 1,
        "history_legacy_imported": False,
        "history_legacy_gap": False,
        "snapshot_authoritative": False,
        "snapshot_completeness": "bounded_non_exhaustive",
        "history_source_ref": f"{_PROJECT_MEMORY_DIR}/{_HISTORY_DIR}/",
        "provenance": unknown_legacy_provenance().to_dict(),
        "lineage": {},
        "updated_at": "",
    }


def _normalize_state(data: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(data or {})
    defaults = _empty_state()
    for key, value in defaults.items():
        state.setdefault(key, value)

    state["schema_version"] = _STATE_SCHEMA
    state["state_text"] = _clip_text(state.get("state_text"), _STATE_TEXT_CHARS)
    recent: list[str] = []
    raw_recent = state.get("recent")
    if isinstance(raw_recent, list):
        for item in raw_recent:
            if isinstance(item, dict):
                item = item.get("summary") or item.get("text") or ""
            line = _clean_inline(item, 420)
            if line:
                recent.append(line)
    state["recent"] = recent[-_recent_keep():]
    state["turn_count"] = _as_nonnegative_int(state.get("turn_count"))
    state["last_full_turn"] = _as_nonnegative_int(state.get("last_full_turn"))
    state["last_full_attempt_turn"] = _as_nonnegative_int(
        state.get("last_full_attempt_turn")
    )
    state["history_records"] = _as_nonnegative_int(state.get("history_records"))
    state["history_segments"] = _as_nonnegative_int(state.get("history_segments"))
    state["history_first_seq"] = _as_nonnegative_int(state.get("history_first_seq"))
    state["history_last_seq"] = _as_nonnegative_int(state.get("history_last_seq"))
    state["history_complete_from_turn"] = max(
        1, _as_nonnegative_int(state.get("history_complete_from_turn")) or 1,
    )
    state["history_initialized"] = bool(state.get("history_initialized"))
    state["history_legacy_imported"] = bool(state.get("history_legacy_imported"))
    state["history_legacy_gap"] = bool(
        state.get("history_legacy_gap")
        or state.get("history_legacy_imported")
        or state["history_complete_from_turn"] > 1
    )
    state["snapshot_authoritative"] = False
    state["snapshot_completeness"] = "bounded_non_exhaustive"
    state["history_source_ref"] = str(
        state.get("history_source_ref") or f"{_PROJECT_MEMORY_DIR}/{_HISTORY_DIR}/"
    )
    if state.get("members") is not None:
        try:
            state["members"] = max(0, int(state["members"]))
        except (TypeError, ValueError):
            state["members"] = None
    state["provenance"] = normalize_provenance(
        state.get("provenance") if isinstance(state.get("provenance"), Mapping)
        else unknown_legacy_provenance()
    ).to_dict()
    raw_lineage = state.get("lineage")
    state["lineage"] = dict(raw_lineage) if isinstance(raw_lineage, Mapping) else {}
    state["updated_at"] = str(state.get("updated_at") or "").strip()
    return state


def _apply_history_stats(state: dict[str, Any], stats: dict[str, Any]) -> None:
    state["history_records"] = _as_nonnegative_int(stats.get("records"))
    state["history_segments"] = _as_nonnegative_int(stats.get("segments"))
    state["history_first_seq"] = _as_nonnegative_int(stats.get("first_seq"))
    state["history_last_seq"] = _as_nonnegative_int(stats.get("last_seq"))
    if stats.get("first_at") and not state.get("history_capture_started_at"):
        state["history_capture_started_at"] = stats["first_at"]


# ── 一轮对话 → 权威原始材料 ──
def _turn_input(turn_result: dict[str, Any] | None) -> str:
    """Return the complete input captured by Engine, without snapshot clipping."""

    if not isinstance(turn_result, dict):
        return ""
    value = turn_result.get("_memory_input")
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _tool_args(call: dict[str, Any]) -> dict[str, Any]:
    raw = call.get("args")
    if raw is None:
        raw = call.get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _turn_excerpt(turn_result: dict[str, Any] | None) -> str:
    """Return the complete authoritative turn output without rewriting it.

    ``final_response`` is the exact text surfaced by the owning Runtime/Coordinator.
    Typed completion metadata is only a fallback for records that have no public final.
    Snapshot summaries may remain bounded elsewhere, but they never feed back into this
    append-only history value.
    """

    if not isinstance(turn_result, dict):
        return ""

    final = turn_result.get("final_response")
    if isinstance(final, str):
        if final != "":
            return final
    elif final is not None:
        text = str(final)
        if text:
            return text

    completion = completion_from_mapping(turn_result, fallback_text="")
    if completion is not None:
        return completion.text

    speeches: list[str] = []
    for row in turn_result.get("direct_speech") or []:
        if not isinstance(row, dict):
            continue
        content = row.get("content")
        if isinstance(content, str):
            speeches.append(content)
        elif content is not None:
            speeches.append(str(content))
    return "\n\n".join(speeches)


def _turn_material(input_text: str, output_text: str, internal_turn: bool) -> str:
    parts: list[str] = []
    if input_text:
        label = msg("memory_manager.py.050")
        parts.append(f"【{label}】\n{input_text}")
    if output_text:
        parts.append(msg("memory_manager.py.051", output_text=output_text))
    return "\n\n".join(parts)


def _fallback_line(excerpt: str) -> str:
    first = next((s.strip() for s in str(excerpt or "").splitlines() if s.strip()), "")
    first = " ".join(first.split())
    return _clip_text(first or msg("memory_manager.py.052"), 100)


def _fallback_memory_line(input_text: str, output_text: str, internal_turn: bool) -> str:
    parts: list[str] = []
    if input_text:
        parts.append(
            (msg("mm.053a") if internal_turn else msg("mm.053b"))
            + _fallback_line(input_text)
        )
    if output_text:
        parts.append(msg("memory_manager.py.054") + _fallback_line(output_text))
    return _clean_inline("；".join(parts) or msg("memory_manager.py.055"), _SUMMARY_CHARS)


def _reconcile_recent_from_history(
    state: dict[str, Any], history_dir: Path,
) -> None:
    """用历史尾记录修复/裁剪快照 recent，并保留同 memory_id 的精炼摘要。"""
    keep = _recent_keep()
    tail: list[dict[str, Any]] = []
    for record in _iter_history_records(history_dir, reverse=True):
        tail.append(record)
        if len(tail) >= keep:
            break
    if not tail:
        return
    tail.reverse()

    existing: dict[str, str] = {}
    for raw in state.get("recent") or []:
        line = _clean_inline(raw, 420)
        match = re.search(r"\[(m\d{12})\b", line, re.I)
        if match:
            existing[match.group(1).lower()] = line

    rebuilt: list[str] = []
    for record in tail:
        memory_id = _memory_id(_as_nonnegative_int(record.get("i")))
        rebuilt.append(existing.get(memory_id.lower()) or _recent_line(record))
    state["recent"] = rebuilt


# ── 紧凑历史记录编码 ──
def _history_fingerprint(
    actor_id: str, kind: str, input_text: str, output_text: str,
) -> str:
    raw = json.dumps(
        [actor_id, kind, input_text, output_text],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def _make_history_record(
    *,
    seq: int,
    turn: int,
    at: str,
    actor_id: str,
    actor_name: str,
    kind: str,
    summary: str,
    input_text: str,
    output_text: str,
    fingerprint: str,
    provenance: Mapping[str, Any] | None,
    lineage: Mapping[str, Any] | None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "v": _HISTORY_SCHEMA,
        "i": max(1, int(seq)),
        "t": at,
        "a": actor_id or "unknown",
        "k": kind,
        "s": _clean_inline(summary, _SUMMARY_CHARS),
        "h": fingerprint,
        "provenance": normalize_provenance(
            provenance if isinstance(provenance, Mapping) else unknown_legacy_provenance()
        ).to_dict(),
        "lineage": dict(lineage) if isinstance(lineage, Mapping) else {},
    }
    if turn > 0:
        record["n"] = int(turn)
    if actor_name and actor_name != actor_id:
        record["x"] = actor_name
    # q/r are the authoritative raw turn texts.  Snapshot limits never feed back into
    # these append-only records.  Hashes make later reference/repair checks explicit.
    if input_text:
        record["q"] = input_text
        record["q_sha256"] = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
    if output_text:
        record["r"] = output_text
        record["r_sha256"] = hashlib.sha256(output_text.encode("utf-8")).hexdigest()
    record["legacy_truncated"] = False
    return record


def _memory_id(seq: int) -> str:
    return f"m{max(0, int(seq)):012d}"


def _recent_line(record: dict[str, Any]) -> str:
    seq = _as_nonnegative_int(record.get("i"))
    turn = _as_nonnegative_int(record.get("n"))
    at = str(record.get("t") or "")
    stamp = at[:16].replace("T", " ") if at else msg("memory_manager.py.069")
    turn_text = msg("memory_manager.py.070", turn=turn) if turn else msg("memory_manager.py.071")
    return _clip_text(
        f"[{_memory_id(seq)} · {turn_text} · {stamp}] {record.get('s') or msg('memory_manager.py.072')}",
        420,
    )


def _segment_info(path: Path) -> tuple[int, int] | None:
    match = _HISTORY_SEGMENT_RE.match(path.name)
    if not match:
        return None
    return int(match.group("first")), int(match.group("last"))


def _valid_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    seq = _as_nonnegative_int(value.get("i"))
    if seq <= 0:
        return None
    record = dict(value)
    original_schema = _as_nonnegative_int(record.get("v"))
    record["i"] = seq
    record["v"] = original_schema or _HISTORY_SCHEMA
    # v1/v2 wrote q/r only after character clipping.  The exact missing suffix is not
    # recoverable, so expose that limitation instead of presenting the row as complete.
    record["legacy_truncated"] = bool(
        record.get("legacy_truncated")
        or (original_schema and original_schema < _HISTORY_SCHEMA and (record.get("q") or record.get("r")))
        or str(record.get("k") or "") == "legacy_snapshot"
    )
    if record.get("n") is not None:
        turn = _as_nonnegative_int(record.get("n"))
        if turn:
            record["n"] = turn
        else:
            record.pop("n", None)
    for key in ("t", "a", "x", "k", "s", "q", "r", "h", "q_sha256", "r_sha256"):
        if key in record:
            record[key] = str(record.get(key) or "")
    record["provenance"] = normalize_provenance(
        record.get("provenance") if isinstance(record.get("provenance"), Mapping)
        else unknown_legacy_provenance()
    ).to_dict()
    raw_lineage = record.get("lineage")
    record["lineage"] = dict(raw_lineage) if isinstance(raw_lineage, Mapping) else {}
    return record


def _read_history_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    # append-only 活动文件若在断电时留下半行，只丢这一行；更早记录都还在。
                    continue
                record = _valid_record(value)
                if record is not None:
                    records.append(record)
    except (OSError, EOFError, gzip.BadGzipFile):
        log.warning("[memory] 历史段读取失败，已跳过：%s", path, exc_info=True)
    # 同一 seq 若因崩溃恢复重复出现，以文件内最后一条为准。
    dedup = {int(rec["i"]): rec for rec in records}
    return [dedup[key] for key in sorted(dedup)]


def _repair_active_tail(path: Path) -> None:
    """删除 append-only 活动文件末尾可能存在的半行。"""
    if not path.is_file():
        return
    try:
        with path.open("rb+") as handle:
            data = handle.read()
            if not data or data.endswith(b"\n"):
                return
            cut = data.rfind(b"\n")
            handle.truncate(0 if cut < 0 else cut + 1)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        log.warning(msg("memory_manager.py.056"), path, exc_info=True)


def _append_history_record(history_dir: Path, record: dict[str, Any]) -> None:
    history_dir.mkdir(parents=True, exist_ok=True)
    active = history_dir / _HISTORY_ACTIVE
    active_was_missing = not active.exists()
    _repair_active_tail(active)
    line = (
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    with active.open("ab") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    if active_was_missing:
        _fsync_directory(history_dir)

    should_rotate = active.stat().st_size >= _segment_bytes()
    if not should_rotate:
        should_rotate = len(_read_history_file(active)) >= _segment_records()
    if should_rotate:
        _rotate_active_history(history_dir)


def _rotate_active_history(history_dir: Path) -> None:
    active = history_dir / _HISTORY_ACTIVE
    records = _read_history_file(active)
    if not records:
        try:
            active.unlink()
        except OSError:
            pass
        return

    by_seq = {int(rec["i"]): rec for rec in records}
    first, last = min(by_seq), max(by_seq)
    overlapping: list[Path] = []
    for path in history_dir.glob("segment-*.jsonl.gz"):
        info = _segment_info(path)
        if info is None:
            continue
        seg_first, seg_last = info
        if seg_last < first or seg_first > last:
            continue
        overlapping.append(path)
        for rec in _read_history_file(path):
            by_seq.setdefault(int(rec["i"]), rec)
        first, last = min(by_seq), max(by_seq)

    ordered = [by_seq[key] for key in sorted(by_seq)]
    target = history_dir / f"segment-{first:012d}-{last:012d}.jsonl.gz"
    payload = "".join(
        json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
        for rec in ordered
    ).encode("utf-8")
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}-{id(payload):x}")
    try:
        with tmp.open("wb") as raw:
            with gzip.GzipFile(
                fileobj=raw, mode="wb", compresslevel=9, mtime=0,
            ) as zipped:
                zipped.write(payload)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, target)
        _fsync_directory(history_dir)
        try:
            active.unlink()
        except FileNotFoundError:
            pass
        for old in overlapping:
            if old != target:
                try:
                    old.unlink()
                except OSError:
                    log.warning(msg("memory_manager.py.057"), old, exc_info=True)
        _fsync_directory(history_dir)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _segment_paths(history_dir: Path) -> list[Path]:
    rows: list[tuple[int, int, Path]] = []
    if not history_dir.is_dir():
        return []
    for path in history_dir.glob("segment-*.jsonl.gz"):
        info = _segment_info(path)
        if info is not None:
            rows.append((info[0], info[1], path))
    rows.sort(key=lambda row: (row[0], row[1], row[2].name))
    return [row[2] for row in rows]


def _iter_history_records(history_dir: Path, *, reverse: bool = False) -> Iterator[dict[str, Any]]:
    segments = _segment_paths(history_dir)
    active = history_dir / _HISTORY_ACTIVE
    paths = ([active] if active.is_file() else []) + list(reversed(segments)) if reverse else (
        segments + ([active] if active.is_file() else [])
    )
    seen: set[int] = set()
    for path in paths:
        records = _read_history_file(path)
        iterable = reversed(records) if reverse else records
        for record in iterable:
            seq = int(record["i"])
            if seq in seen:
                continue
            seen.add(seq)
            yield record


def _last_history_record(history_dir: Path) -> dict[str, Any] | None:
    return next(_iter_history_records(history_dir, reverse=True), None)


def _record_by_seq(history_dir: Path, seq: int) -> dict[str, Any] | None:
    if seq <= 0:
        return None
    active = history_dir / _HISTORY_ACTIVE
    for rec in reversed(_read_history_file(active)):
        if int(rec["i"]) == seq:
            return rec
    for path in reversed(_segment_paths(history_dir)):
        info = _segment_info(path)
        if info is None or not (info[0] <= seq <= info[1]):
            continue
        for rec in _read_history_file(path):
            if int(rec["i"]) == seq:
                return rec
    return None


def _history_stats(history_dir: Path) -> dict[str, Any]:
    intervals: list[tuple[int, int]] = []
    segments = _segment_paths(history_dir)
    for path in segments:
        info = _segment_info(path)
        if info is not None:
            intervals.append(info)
    active_records = _read_history_file(history_dir / _HISTORY_ACTIVE)
    intervals.extend((int(rec["i"]), int(rec["i"])) for rec in active_records)

    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    records = sum(end - start + 1 for start, end in merged)
    first_seq = merged[0][0] if merged else 0
    last_seq = merged[-1][1] if merged else 0
    first_record = next(_iter_history_records(history_dir), None)
    last_record = next(_iter_history_records(history_dir, reverse=True), None)
    return {
        "records": records,
        "segments": len(segments),
        "active_records": len(active_records),
        "first_seq": first_seq,
        "last_seq": last_seq,
        "first_at": str((first_record or {}).get("t") or ""),
        "last_at": str((last_record or {}).get("t") or ""),
        "max_turn": _as_nonnegative_int((last_record or {}).get("n")),
    }


# ── 检索 / 展开 ──
def _parse_memory_cursor(value: str | None) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.fullmatch(r"[mM](\d{1,12})", raw)
    if not match:
        raise ValueError(msg("memory_manager.py.058"))
    seq = int(match.group(1))
    if seq <= 0:
        raise ValueError(msg("memory_manager.py.059"))
    return seq


def _parse_time_bound(value: str | None, *, is_end: bool) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            base = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if is_end:
                base = base.replace(hour=23, minute=59, second=59, microsecond=999999)
            return base
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError(
            msg("memory_manager.py.060", raw=raw)
        ) from exc


def _record_datetime(record: dict[str, Any]) -> datetime | None:
    raw = str(record.get("t") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _record_in_time(
    record: dict[str, Any], start: datetime | None, end: datetime | None,
) -> bool:
    if start is None and end is None:
        return True
    timestamp = _record_datetime(record)
    if timestamp is None:
        return False
    if start is not None and timestamp < start:
        return False
    if end is not None and timestamp > end:
        return False
    return True


def _record_search_text(record: dict[str, Any]) -> str:
    return "\n".join(
        str(record.get(key) or "") for key in ("s", "q", "r", "a", "x", "k")
    )


def _record_matches_query(record: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    haystack = _record_search_text(record).casefold()
    needle = query.casefold()
    if needle in haystack:
        return True
    terms = [term for term in needle.split() if term]
    return bool(terms) and all(term in haystack for term in terms)


def _match_snippet(record: dict[str, Any], query: str, limit: int = 240) -> str:
    fields = [str(record.get(key) or "") for key in ("s", "q", "r")]
    if not query:
        return _clip_text(fields[0] or fields[1] or fields[2], limit)
    needles = [query.casefold()] + [term.casefold() for term in query.split() if term]
    for field in fields:
        folded = field.casefold()
        positions = [folded.find(needle) for needle in needles if needle]
        positions = [pos for pos in positions if pos >= 0]
        if not positions:
            continue
        pos = min(positions)
        start = max(0, pos - 80)
        end = min(len(field), start + limit)
        snippet = field[start:end].strip()
        if start:
            snippet = "…" + snippet
        if end < len(field):
            snippet += "…"
        return snippet
    return _clip_text(fields[0] or fields[1] or fields[2], limit)


def _kind_label(kind: str) -> str:
    return {
        "conversation": msg("memory_manager.py.061"),
        "internal": msg("memory_manager.py.062"),
        "legacy_snapshot": msg("memory_manager.py.033"),
    }.get(kind, kind or msg("memory_manager.py.063"))


def _record_preview(record: dict[str, Any], query: str) -> dict[str, Any]:
    turn = _as_nonnegative_int(record.get("n"))
    return {
        "memory_id": _memory_id(_as_nonnegative_int(record.get("i"))),
        "turn": turn or None,
        "timestamp": str(record.get("t") or ""),
        "agent_id": str(record.get("a") or "unknown"),
        "agent_name": str(record.get("x") or record.get("a") or "unknown"),
        "source": _kind_label(str(record.get("k") or "")),
        "summary": str(record.get("s") or ""),
        "provenance": normalize_provenance(
            record.get("provenance") if isinstance(record.get("provenance"), Mapping)
            else unknown_legacy_provenance()
        ).to_dict(),
        "lineage": dict(record.get("lineage") or {}) if isinstance(record.get("lineage"), Mapping) else {},
        "match": _match_snippet(record, query),
    }


def _record_detail(record: dict[str, Any]) -> dict[str, Any]:
    detail = _record_preview(record, "")
    detail.pop("match", None)
    detail["input"] = str(record.get("q") or "")
    detail["output"] = str(record.get("r") or "")
    detail["input_sha256"] = str(record.get("q_sha256") or "")
    detail["output_sha256"] = str(record.get("r_sha256") or "")
    detail["legacy_truncated"] = bool(record.get("legacy_truncated"))
    detail["source_ref"] = f"memory://{detail['memory_id']}"
    return detail


def _coverage_note(state: dict[str, Any]) -> str:
    if state.get("history_legacy_gap"):
        complete_from = max(
            1, _as_nonnegative_int(state.get("history_complete_from_turn")) or 1,
        )
        imported = (
            msg("memory_manager.py.064")
            if state.get("history_legacy_imported") else ""
        )
        return (
            msg("memory_manager.py.065") + imported
            + msg("memory_manager.py.066", complete_from=complete_from)
        )
    if _as_nonnegative_int(state.get("history_records")):
        return msg("memory_manager.py.067")
    return msg("memory_manager.py.068")


def _render_project(state: dict[str, Any]) -> str:
    """把有界快照渲染成人读的 ``context.md``；完整历史不重复展开。"""
    turn = _as_nonnegative_int(state.get("turn_count"))
    members = state.get("members")
    members_txt = msg("memory_manager.py.079") if members is None else str(members)
    updated = str(state.get("updated_at") or "").strip() or _now_iso()
    history_records = _as_nonnegative_int(state.get("history_records"))
    state_text = str(state.get("state_text") or "").strip() or msg("memory_manager.py.080")
    recent = state.get("recent") or []
    provenance = normalize_provenance(
        state.get("provenance") if isinstance(state.get("provenance"), Mapping)
        else unknown_legacy_provenance()
    ).to_dict()
    provenance_line = (
        msg("memory_manager.py.081") +
        f"{provenance['status']} | build={provenance['build_id'] or 'unknown'} | "
        f"git={provenance['git_commit'] or 'unknown'} | "
        f"runtime_schema={provenance['runtime_schema_version'] or 'unknown'} | "
        f"harness_schema={provenance['harness_schema_version'] or 'unknown'} | "
        f"prompt_bundle={provenance['prompt_bundle_version'] or 'unknown'}"
    )

    out = [
        msg("memory_manager.py.083"),
        (
            msg("memory_manager.py.082", updated=updated, turn=turn,
                history_records=history_records, members_txt=members_txt)
        ),
        provenance_line,
        "",
        msg("memory_manager.py.084"),
        msg("memory_manager.py.085", **{"history_ref": state.get('history_source_ref') or f'{_PROJECT_MEMORY_DIR}/{_HISTORY_DIR}/'}),
        msg("memory_manager.py.086"),
        msg("memory_manager.py.087"),
        "",
        msg("memory_manager.py.088"),
        state_text,
        "",
        msg("memory_manager.py.089"),
    ]
    if recent:
        out.extend(f"- {line}" for line in recent)
    else:
        out.append(msg("memory_manager.py.090"))
    return "\n".join(out) + "\n"


__all__ = ["MemoryManager", "AuxCall"]