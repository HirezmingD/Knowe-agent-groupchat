"""
persist.py — 落盘（关了再开，东西还在）。

三样东西存在磁盘上：
  projects.json               项目注册表 [{project_id, name, created_at}]
  events/{pid}.jsonl          每项目一份事件流水，一行一条事件（JSON）
  events/{pid}_roster.jsonl   [v0.8a A-1] 每项目一份花名册，一行一个成员
                              [v0.9b] 带 status（active/removed/deleted）
                              [v0.9c] 带 name（「前端 1」）—— **名字的权威来源就在这儿**

[v0.8a A-1] 花名册为什么也用 JSONL：
  它和事件流水是同一类东西——**一件一件发生的事**，不是一份需要整体一致的配置。
  「fe_1 是前端」这条记录写下去就不会再改，追加一行就完事，不需要原子重写。
  同一个 agent 万一写了两遍，加载时后来的盖掉先来的（就是这么定义的）。
  最坏情况：最后一行写到一半断电 → 加载时跳过那一行，丢一个成员，
  不是灾难（下次组队他还会被建出来）。

三条规矩：

1. **原子写。** 先写 `.tmp`，再 `os.replace()` 一把换过去。
   os.replace 在同一文件系统上是原子的——要么是旧文件，要么是新文件，
   **绝不会出现「写到一半断电，留下半个坏 JSON」**。这是注册表唯一能接受的写法。

2. **事件用 JSONL 追加，不整file重写。** 一条事件一行，append 就完事。
   整份重写既慢又危险（写到一半崩就全没了）；追加最坏情况是最后一行不完整，
   加载时跳过那一行即可（load 里真的这么做，有测试）。

3. **加载时顺手压实。** ring 只保留每项目最近 N 条，那磁盘上留一百万行也没用。
   加载时把超出的旧行丢掉、把文件重写成"正好 N 条"——不然跑一年这文件能涨到几个 G。

坏文件不能拖垮启动：projects.json 烂了 → 记一条日志，当空的用；
某个项目的 jsonl 烂了 → 那个项目从零开始，别的项目不受牵连。
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .contract import (
    PERSISTABLE_EVENT_TYPES, STRUCTURAL_EVENT_TYPES,
)
from .namegen import gen_name   # [v0.9d] 掷名字的地方只有一处
from .i18n_backend import msg
from .token_usage import normalize_token_usage_record

log = logging.getLogger("knowe.persist")

REGISTRY_NAME = "projects.json"
EVENTS_DIR = "events"
ROSTER_SUFFIX = "_roster.jsonl"        # [v0.8a A-1] events/{pid}_roster.jsonl
SEQ_SUFFIX = ".seq"                       # v0.13: 每项目 seq 高水位（瞬时事件不落 JSONL）

# ═══════════════════════════════════════════════════════════════
# [v0.12 D · 问题四] 什么该落盘、什么不该
#
#   老代码把**带 seq 的一切**都写进 jsonl，包括 stream_delta（逐字增量，一句话
#   几十条）、agent_thinking、tool_start/complete、state_snapshot（整段会话的快照）。
#   于是早上一次很短的测试，jsonl 就涨到 1~2 MB——**里面 95% 是逐字增量的垃圾**。
#
#   真正是「聊天记录」的，只有**结构事件**（message / approval_card / user_echo /
#   agents_created / …）——也就是会进 UI 时间线、需要跨重启活下来的那些。
#   逐字增量是**播放动画**用的瞬时事件：它实时发给前端、也进内存 ring（供同一次
#   会话里快速重连回放），但**没有任何理由留在磁盘上**——重启之后你要看的是
#   「最终那句话」，不是昨天打字的过程。
#
#   所以落盘的白名单 = 结构事件 + user_echo。state_snapshot 也不落：它是会话的
#   一份即时重建，内容和结构事件重复，落它等于把整段历史又抄一遍。
#
#   ★ 这一条和「不丢聊天记录」不冲突，恰恰相反：结构事件一条不少地留下来（那就是
#     全部聊天记录），被丢掉的只有逐字增量。文件因此又小又全。
# ═══════════════════════════════════════════════════════════════
PERSISTABLE_TYPES: frozenset[str] = PERSISTABLE_EVENT_TYPES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_name(project_id: str) -> str:
    """project_id 直接当文件名是不行的——`../../etc/passwd` 也是一个合法字符串。"""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in project_id)[:120] or "_"


# ═══════════════════════════════════════════════════════════════
# [v0.9c → v0.9d] 名字
#
# v0.9c 的账：名字 = 确定性公式（`fe_1` + `前端` → 「前端 1」）。
#   它治好了「每次开 App 名字都变」，**但把人也治没了**——
#   一屋子「前端 1」「后端 1」在群里说话，读起来像仓库清单，不像一支队伍。
#
# v0.9d 的账：**随机和持久化从来不矛盾。矛盾的是「每次都重新掷」。**
#   规矩一句话：**掷一次，写进花名册，此后只读不掷。**
#
#     建人时：花名册里有他（**哪怕是归档的**）→ 用旧名（他回来了，还是他）
#             没有                            → 掷一次 → 立刻落盘
#     温载时：从花名册读回来 —— 绝不重新生成
#
#   掷骰子的地方只有一处（namegen.gen_name），记账的地方也只有一处（花名册）。
#   两处合一，就是 upsert_agent 这个函数——所以名字的生成被**焊死在落盘的那一刻**：
#   想掷一个名字而不落盘，在这套代码里做不到。这是故意的。
# ═══════════════════════════════════════════════════════════════

def legacy_display_name(agent_id: str, role: str) -> str:
    """
    [v0.9c 的老公式] `fe_1` + `前端` → 「前端 1」。

    ★ 只用于**兜底**，而且只在「读」的路径上：老花名册里那些没有 name 字段的行，
      得有个称呼，而这个称呼必须**稳定**（每次加载都一样）——
      在这儿掷随机名，就等于把 v0.9c 修掉的那个 bug 原样请回来。
      真正的升级发生在写的路径上：server 温载时会给这些老行补一个随机名并落盘（一次性）。
    """
    if not isinstance(agent_id, str) or not agent_id:
        return "成员"
    role = (role or "").strip()
    if not role:
        return agent_id
    seq = agent_id.rsplit("_", 1)[-1] if "_" in agent_id else ""
    return f"{role} {seq}".strip()


#: 老名字（v0.9c）。留个别名，免得哪个 import 漏改了就炸。
agent_display_name = legacy_display_name


def agent_name_for(
    agent_id: str,
    role: str,
    stored_name: str | None = None,
    taken: set[str] | None = None,
) -> str:
    """
    这个人该叫什么。

      · 花名册里已经有名字（stored_name）→ **直接用**，一个字都不改。
        归档的人被重新加回来，走的也是这条路——他回来了，他还是他。
      · 没有 → 掷一个（中英各半，同项目内不重名）。
        ★ 调用方**必须把它写回花名册**。掷了不写 = v0.9c 之前那个 bug。
          （所以这个函数的唯一正经调用点是 Store.upsert_agent。）

    [v1.0.23.3-R2] 掷名语言随系统：英文模式只掷英文名（中文名会把模型
    输出带成中文），中文模式保持历史行为（中英各半）。模块级延迟导入
    runtime_settings 避免循环依赖。
    """
    if stored_name:
        return stored_name
    if agent_id == "coordinator":
        return (role or msg("engine.007")).strip()
    from . import runtime_settings
    lang = runtime_settings.language()
    # 只有 en 强制英文名；zh/None 一律保持历史中英各半行为
    return gen_name(taken, lang="en" if lang == "en" else None)


class Store:
    """磁盘上的一份 Knowe 数据。目录不存在就建。"""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.events_dir = self.root / EVENTS_DIR
        self.registry_path = self.root / REGISTRY_NAME
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self._seq_cache: dict[str, int] = {}
        # Token telemetry is appended from multiple concurrent project/agent tasks.
        # A process-local lock keeps each JSON line contiguous without putting the chat
        # event log or the LLM control path behind the same lock.
        self._token_usage_lock = threading.Lock()
        # [v1.0.31 R2] 按天轮转：缓存 pid → 上次检查日期（YYYYMMDD），
        # 避免每次 append 都 stat 文件；跨天懒检查在单 writer 队列内串行执行。
        self._rotation_dates: dict[str, str] = {}
        # [v1.0.24.4] 磁盘写入单队列（见下方「写入不占主循环」一节）。
        # 单 worker = FIFO 串行：落盘顺序永远等于提交顺序。
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="knowe-persist")
        self._writer_closed = False
        # [v1.0.24.4] 磁盘写失败计数（线程安全）。写失败不再阻断主循环，
        # 失败可见性由计数兜底：server 在握手/快照构造处读取并记入日志。
        self._disk_failures = 0
        self._disk_failures_lock = threading.Lock()

    # ═══════════════════════════════════════════════════════════
    # [v1.0.24.4] 磁盘写入走单队列，不占主循环
    #
    # 老代码把每一次落盘（事件 append、seq 高水位的原子替换 + fsync）都同步做在
    # asyncio 主循环上——Agent 一句话能带出几十条结构事件，每条都要等一次磁盘，
    # 主循环被钉在原地，前端就卡。
    #
    # 修法：单 worker 执行器 = 单队列。
    #   · defer()/defer_bg() 提交即返回，主循环只花一次入队的时间。
    #   · 单线程串行消费 → **落盘顺序 = 提交顺序**。事件在 seq 锁内提交，
    #     盘上顺序因此和 seq 顺序一致，不会乱。
    #   · 「读-改-写」类操作（rename 重写、upsert_project…）整体作为一个 job
    #     提交，读到的永远是此前全部写完的状态。
    #   · 极少数必须留在调用线程同步执行的路径（成员删除的读回校验协议），
    #     动手前先 flush() 排空队列；同步段占着主循环期间不会有新提交，
    #     同样不会和队列里的写打架。
    #
    # ★ Store 自身的所有方法保持同步、语义不变：单测、启动温载照旧直接调用，
    #   只有站在主循环上的调用点改走队列。
    # ═══════════════════════════════════════════════════════════
    def defer(self, job: Callable[[], Any]) -> Future:
        """提交一个磁盘写作业，立即返回。

        返回的 Future 供需要等结果、捕异常的调用方 await（配合
        ``asyncio.wrap_future``）；不关心的调用方用 defer_bg。
        队列已关闭（关机后）→ 退化为同步执行：宁可慢，不能丢。
        """
        if self._writer_closed:
            fut: Future = Future()
            try:
                fut.set_result(job())
            except BaseException as exc:
                fut.set_exception(exc)
            return fut
        return self._writer.submit(job)

    def defer_bg(self, job: Callable[[], Any], *, description: str = "") -> Future:
        """提交一个不需要回值的作业；失败在 worker 线程记日志，不打扰提交方。"""
        fut = self.defer(job)
        fut.add_done_callback(lambda f: self._log_job_failure(f, description))
        return fut

    def _log_job_failure(self, fut: Future, description: str) -> None:
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            with self._disk_failures_lock:
                self._disk_failures += 1
            log.error(
                "磁盘写作业失败%s：%s",
                f"（{description}）" if description else "", exc, exc_info=exc,
            )

    def disk_write_failures(self) -> int:
        """[v1.0.24.4] 累计磁盘写失败次数（线程安全读取，供诊断可见性）。"""
        with self._disk_failures_lock:
            return self._disk_failures

    def flush(self, timeout: float | None = 30.0) -> bool:
        """阻塞到已提交的作业全部落盘。供罕见的同步路径动手前排空队列、关机收尾。"""
        try:
            self.defer(lambda: None).result(timeout)
            return True
        except Exception:  # noqa: BLE001 — 超时/已关闭都按「没排空」报告
            log.warning("persist flush 未在 %.1fs 内排空写入队列", timeout or -1)
            return False

    def close(self, timeout: float | None = 10.0) -> None:
        """关机：排空队列，停掉写入线程。之后的 defer 退化为同步执行。"""
        self.flush(timeout)
        self._writer.shutdown(wait=True)
        self._writer_closed = True

    # ═══════════════════════════════════════════════════════════
    # 原子写
    # ═══════════════════════════════════════════════════════════
    def _atomic_write(self, path: Path, text: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())        # 落到盘上，不只是落到页缓存
            os.replace(tmp, path)           # ★ 原子替换
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ═══════════════════════════════════════════════════════════
    # 项目注册表
    # ═══════════════════════════════════════════════════════════
    def load_projects(self) -> list[dict[str, Any]]:
        if not self.registry_path.exists():
            return []
        try:
            data = json.loads(self.registry_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # 坏文件不许拖垮启动——记一笔，当空的用
            log.error("projects.json 读不了（%s），当作空注册表启动", exc)
            return []
        if not isinstance(data, list):
            log.error("projects.json 不是数组，当作空注册表启动")
            return []
        return [
            p for p in data
            if isinstance(p, dict) and isinstance(p.get("project_id"), str)
        ]

    def save_projects(self, projects: list[dict[str, Any]]) -> None:
        self._atomic_write(
            self.registry_path,
            json.dumps(projects, ensure_ascii=False, indent=2),
        )

    def upsert_project(self, project_id: str, name: str) -> list[dict[str, Any]]:
        """新项目追加；已有的只更新名字（created_at 不动——它是历史，不是状态）。"""
        projects = self.load_projects()
        for p in projects:
            if p["project_id"] == project_id:
                p["name"] = name
                self.save_projects(projects)
                return projects
        projects.append({
            "project_id": project_id,
            "name": name,
            "created_at": _now(),
        })
        self.save_projects(projects)
        return projects

    def delete_project(self, project_id: str) -> bool:
        """彻底删除一个项目的注册表、群聊流水、花名册与全部私聊频道。

        Harness 层的“已被删除”记录不属于 Store，由 ``MemoryManager`` 单独保留。
        这里清的是可恢复项目本体；删除后 ``load_projects`` / ``load_roster`` / 回放
        都不会再把它复活。
        """
        if not isinstance(project_id, str) or not project_id:
            return False

        projects = self.load_projects()
        kept = [p for p in projects if p.get("project_id") != project_id]
        existed = len(kept) != len(projects)
        if existed:
            self.save_projects(kept)

        # 先按花名册枚举精确 DM；再从事件正文识别历史版本遗留的 canonical DM。
        #
        # 不能按 ``_safe_name(f"dm:{project_id}:")`` 做文件名前缀删除：safe_name
        # 会把分隔符压成下划线，项目 ``p`` 的前缀 ``dm_p_`` 会误命中项目
        # ``p_x`` 的 DM。事件正文里的 ``project_id`` 没有这种歧义，必须以它为准。
        roster_ids = set(self.load_roster_full(project_id))
        roster_ids.add("coordinator")
        for agent_id in roster_ids:
            self.delete_conversation(f"dm:{project_id}:{agent_id}")

        legacy_dm_ids: set[str] = set()
        for path in list(self.events_dir.glob("*.jsonl")):
            if not path.is_file() or path.name.endswith(ROSTER_SUFFIX):
                continue
            try:
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        conversation_id = event.get("project_id")
                        if (isinstance(conversation_id, str)
                                and conversation_id.startswith(f"dm:{project_id}:")):
                            legacy_dm_ids.add(conversation_id)
                            break
            except OSError:
                log.warning("[%s] 扫描历史 DM 失败：%s", project_id, path, exc_info=True)
        for conversation_id in legacy_dm_ids:
            existed = self.delete_conversation(conversation_id) or existed

        existed = self.delete_conversation(project_id) or existed
        try:
            # Deletion and append share the same tiny lock: unlinking midway through an append
            # must not leave a detached descriptor that recreates/loses the final JSON line.
            with self._token_usage_lock:
                self.token_usage_path(project_id).unlink()
            existed = True
        except FileNotFoundError:
            pass
        except OSError:
            log.debug("[%s] Token 统计文件删除失败", project_id, exc_info=True)
        for key in list(self._seq_cache):
            if key == project_id or key.startswith(f"dm:{project_id}:"):
                self._seq_cache.pop(key, None)
        return existed

    # ═══════════════════════════════════════════════════════════
    # 事件流水
    # ═══════════════════════════════════════════════════════════
    def events_path(self, project_id: str) -> Path:
        return self.events_dir / f"{_safe_name(project_id)}.jsonl"

    def token_usage_path(self, project_id: str) -> Path:
        """Project-level daily token ledger: ``data/{project_id}_tokens.jsonl``."""
        return self.root / f"{_safe_name(project_id)}_tokens.jsonl"

    def append_token_usage(self, project_id: str, record: dict[str, Any]) -> bool:
        """Append one normalized provider-call record; telemetry errors never escape."""
        row = normalize_token_usage_record(record)
        if row is None:
            log.debug("[%s] 跳过无效 Token 统计记录：%r", project_id, record)
            return False
        encoded = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with self._token_usage_lock:
                path = self.token_usage_path(project_id)
                with path.open("ab+") as handle:
                    # A power loss may leave a partial final JSON object without a newline.  Start
                    # the next record on a fresh line so one old bad tail cannot consume the first
                    # successful call after restart.
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() > 0:
                        handle.seek(-1, os.SEEK_END)
                        if handle.read(1) != b"\n":
                            handle.seek(0, os.SEEK_END)
                            handle.write(b"\n")
                    handle.seek(0, os.SEEK_END)
                    handle.write(encoded)
                    handle.flush()
            return True
        except Exception:  # telemetry must not interrupt an agent turn
            log.debug("[%s] Token 统计追加失败", project_id, exc_info=True)
            return False

    def load_token_usage(self, project_id: str) -> list[dict[str, Any]]:
        """Load the full project ledger, skipping malformed or half-written lines."""
        path = self.token_usage_path(project_id)
        rows: list[dict[str, Any]] = []
        bad = 0
        try:
            # Take a coherent in-process snapshot.  Parsing happens after releasing the lock so a
            # large historical query never blocks new provider records longer than the disk read.
            # A crash-truncated final line can still exist on the next launch and is skipped below.
            with self._token_usage_lock:
                if not path.exists():
                    return []
                raw_lines = path.read_bytes().splitlines()
            for raw_line in raw_lines:
                try:
                    line = raw_line.decode("utf-8").strip()
                except UnicodeDecodeError:
                    bad += 1
                    continue
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
                    continue
                row = normalize_token_usage_record(raw)
                if row is None:
                    bad += 1
                    continue
                rows.append(row)
        except Exception:
            log.debug("[%s] Token 统计读取失败", project_id, exc_info=True)
            return []
        if bad:
            log.debug("[%s] Token 统计有 %d 行坏数据，已跳过", project_id, bad)
        return rows

    def seq_path(self, project_id: str) -> Path:
        return self.events_dir / f"{_safe_name(project_id)}{SEQ_SUFFIX}"

    def save_seq_watermark(self, project_id: str, seq: int) -> None:
        """
        保存项目已分配的最大 seq。聊天流水只存结构事件，逐字/思考事件不落 JSONL；
        没有这本小账，进程重启后 seq 可能回退，前端会把新消息当旧消息丢掉。
        """
        if not isinstance(seq, int) or seq < 0:
            return
        if seq <= self._seq_cache.get(project_id, -1):
            return
        try:
            # 即使只有一个整数也做原子替换：断电时不能留下空文件导致 seq 回退。
            self._atomic_write(self.seq_path(project_id), str(seq) + "\n")
            self._seq_cache[project_id] = seq
        except OSError as exc:
            log.warning("[%s] seq 高水位写入失败：%s", project_id, exc)

    def load_seq_watermark(self, project_id: str) -> int:
        path = self.seq_path(project_id)
        loaded = False
        try:
            if path.is_file():
                value = int(path.read_text("ascii").strip())
                loaded = True
            else:
                value = 0
        except (OSError, ValueError):
            value = 0

        if not loaded:
            # 升级兼容：旧版 JSONL 可能还留着不再持久化的 stream/tool 事件，
            # 它们的 seq 往往高于最后一条聊天记录。压实前先从原流水取最大值，
            # 否则升级后的第一轮会发生 seq 回退，前端把新事件当重复项丢掉。
            value = max(
                value,
                max((int(e["seq"]) for e in self.load_all_events(project_id)), default=0),
            )
            value = max(0, value)
            if value:
                try:
                    self._atomic_write(path, str(value) + "\n")
                except OSError as exc:
                    log.warning("[%s] 迁移 seq 高水位写入失败：%s", project_id, exc)
        value = max(0, value)
        self._seq_cache[project_id] = value
        return value

    def append_event(self, project_id: str, event: dict[str, Any]) -> None:
        """
        一行一条，追加。

        两道门，缺一不可：
          1. **必须带 seq**（无 seq 的旁路事件不进 ring，也不该进盘）。
          2. [v0.12 D · 问题四] **必须是结构事件**（PERSISTABLE_TYPES）。
             stream_delta / agent_thinking / tool_* / state_snapshot 这些瞬时事件
             实时发给前端、也进内存 ring，但**不落盘**——它们不是聊天记录，
             是播放动画用的过程数据，留在磁盘上只会把文件撑爆（问题四的病根）。
        """
        if "seq" not in event:
            return
        if event.get("type") not in PERSISTABLE_TYPES:   # [v0.12 D] 只有聊天记录落盘
            return
        try:
            self._rotate_if_needed(project_id)   # [v1.0.31 R2] 跨天切分（懒检查）
        except OSError as exc:
            log.error("[%s] 事件日志轮转失败（不影响追加）：%s", project_id, exc)
        line = json.dumps(event, ensure_ascii=False) + "\n"
        try:
            with open(self.events_path(project_id), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as exc:
            # 写不进去也不能把服务搞停——用户还在聊天
            log.error("[%s] 事件落盘失败：%s", project_id, exc)

    # ═══════════════════════════════════════════════════════════
    # [v1.0.31 R2] 按天轮转 + gz 压缩（聊天记录只压缩、永不删除）
    #
    # 当天文件固定名 {pid}.jsonl（兼容现有读取）；跨天时把旧文件改名为
    # {pid}.YYYYMMDD.jsonl 并 gz 压缩为 {pid}.YYYYMMDD.jsonl.gz。
    # 读取端合并当天明文 + 全部历史 gz，按 seq 排序——聊天记录一条不丢。
    # ═══════════════════════════════════════════════════════════
    DAY_ARCHIVE_RE = re.compile(r"^(?P<pid>.+?)\.(?P<day>\d{8})\.jsonl(?:\.gz)?$")

    def day_archive_paths(self, project_id: str) -> list[Path]:
        """该项目全部历史日文件（.jsonl 未压缩 或 .jsonl.gz 已压缩），按日期升序。"""
        prefix = _safe_name(project_id)
        paths: list[Path] = []
        for p in self.events_dir.glob(f"{prefix}.*.jsonl*"):
            if p.name == self.events_path(project_id).name:
                continue
            m = self.DAY_ARCHIVE_RE.match(p.name)
            if m and m.group("pid") == prefix:
                paths.append(p)
        return sorted(paths, key=lambda p: p.name)

    def _rotate_if_needed(self, project_id: str) -> None:
        """跨天懒检查：当天日期与文件日期不同 → 切分 + 压缩旧日文件。幂等。"""
        today = date.today().strftime("%Y%m%d")
        if self._rotation_dates.get(project_id) == today:
            return
        path = self.events_path(project_id)
        if not path.exists() or path.stat().st_size == 0:
            # 空文件/不存在：无需轮转，但仍记录检查结果，避免反复 stat
            self._rotation_dates[project_id] = today
            return
        day = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y%m%d")
        if day == today:
            # 当天文件，无需轮转
            self._rotation_dates[project_id] = today
            return
        # 切分：当前文件 → 日归档文件
        archived = self.events_dir / f"{_safe_name(project_id)}.{day}.jsonl"
        os.replace(path, archived)
        self._compress_day_file(archived)
        self._rotation_dates[project_id] = today

    def _compress_day_file(self, path: Path) -> None:
        """把 {pid}.YYYYMMDD.jsonl 压成 .gz（原子：先写 .tmp.gz 再替换）。幂等。"""
        gz_path = path.with_suffix(".jsonl.gz")
        if gz_path.exists() and gz_path.stat().st_size > 0:
            path.unlink(missing_ok=True)  # 已压缩过，清掉未压缩残留
            return
        tmp = path.with_name(path.name + ".tmp.gz")
        try:
            with open(path, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
                while True:
                    chunk = src.read(1 << 16)
                    if not chunk:
                        break
                    dst.write(chunk)
            os.replace(tmp, gz_path)
            path.unlink(missing_ok=True)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def rotate_and_compress(self, project_id: str) -> None:
        """后台维护入口：强制检查轮转 + 压缩所有遗留未压缩日文件。幂等。"""
        self._rotation_dates.pop(project_id, None)
        self._rotate_if_needed(project_id)
        for p in self.day_archive_paths(project_id):
            if p.name.endswith(".jsonl") and not p.name.endswith(".gz"):
                try:
                    self._compress_day_file(p)
                except OSError as exc:
                    log.error("[%s] 日文件压缩失败：%s", project_id, exc)

    def load_events(self, project_id: str, limit: int) -> list[dict[str, Any]]:
        """
        读回最近 limit 条。**坏行跳过，不抛异常**——
        最后一行写到一半就断电，是完全可能发生的事，不该让整个项目开不了。

        [v1.0.31 R2] 合并读：当天明文文件 + 全部历史日文件（gz 自动解压），
        按 seq 排序——聊天记录一条不丢（只压缩、永不删除）。
        """
        paths: list[Path] = [self.events_path(project_id)]
        paths.extend(self.day_archive_paths(project_id))

        events: list[dict[str, Any]] = []
        bad = 0
        for path in paths:
            if not path.exists():
                continue
            try:
                if path.name.endswith(".gz"):
                    opener = gzip.open(path, "rt", encoding="utf-8")
                else:
                    opener = open(path, encoding="utf-8")
                with opener as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            bad += 1
                            continue
                        if isinstance(ev, dict) and isinstance(ev.get("seq"), int):
                            events.append(ev)
            except OSError as exc:
                log.error("[%s] 事件日志读不了（%s）：%s", project_id, path.name, exc)
                continue

        if bad:
            log.warning("[%s] 事件日志有 %d 行坏的，已跳过", project_id, bad)

        events.sort(key=lambda e: e["seq"])
        return events[-limit:] if limit > 0 else events

    def load_all_events(self, project_id: str) -> list[dict[str, Any]]:
        """
        [v0.12 D · 问题二] **读回全部历史，一条都不截。**

        这就是问题二的正解：启动温载**绝不能**只读最近 N 条。聊天记录是用户的项目
        历史，不是可淘汰的缓存——磁盘上有多少，就全读回来多少。
        （`load_events(pid, 0)` 本来就返回全部，这里包一个名字明确的入口，
          免得哪天有人又手滑传了个 1000 进去，把问题二原样请回来。）
        """
        return self.load_events(project_id, 0)

    def compact(self, project_id: str, events: list[dict[str, Any]]) -> None:
        """
        把日志重写成正好这些事件（原子）。

        ★ [v0.12 D · 问题二] **调用方传进来的 events 必须是「要保留的全部历史」，
          绝不能是截断过的切片。**

          老代码的病根就在这儿被误用了：启动时 `load_events(pid, 1000)` 只读回
          最近 1000 条，转手 `compact(pid, 那 1000 条)` → 磁盘被**永久**改写成只剩
          1000 条，1500 条聊天记录当场丢 500 条，反复重启也回不来。

          compact 本身没错——它是「去重/去瞬时事件」的原子重写工具。错的是喂给它
          一份残缺的历史。现在的用法：`compact(pid, 全部结构事件)`——既把老文件里的
          逐字增量垃圾清掉（问题四），又**一条聊天记录都不丢**（问题二）。
        """
        text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
        self._atomic_write(self.events_path(project_id), text)
        # [v1.0.31 R2] 重写后历史日文件已并入当天文件 → 清掉 gz/日归档，防重复读取
        for p in self.day_archive_paths(project_id):
            try:
                p.unlink(missing_ok=True)
            except OSError as exc:
                log.error("[%s] compact 清理历史日文件失败（%s）：%s", project_id, p.name, exc)

    def delete_conversation(self, conversation_id: str) -> bool:
        """删除一个精确会话（群或 DM）的流水、seq 水位与可选花名册。幂等。"""
        removed = False
        for path in (
            self.events_path(conversation_id),
            self.seq_path(conversation_id),
            self.roster_path(conversation_id),
        ):
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                pass
        self._seq_cache.pop(conversation_id, None)
        return removed

    @staticmethod
    def _event_has_agent_reference(
        value: Any, agent_id: str, container: str = "",
    ) -> bool:
        """审批结构里出现精确成员 id 时，整条结构事件都属于该身份。"""
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"agent_id", "target_id", "member_id", "source_agent_id"} \
                        and child == agent_id:
                    return True
                # propose_agents 的卡体使用 ``proposed: [{id, role, name}]``；其他
                # 身份数组也常用短字段 ``id``。只在已知身份容器里解释它，避免把
                # approval/card 自己的普通 id 当成成员 id。
                if (key == "id" and child == agent_id
                        and container in {
                            "members", "agents", "proposed", "proposed_agents",
                            "provisional_members", "targets",
                        }):
                    return True
                if Store._event_has_agent_reference(child, agent_id, key):
                    return True
        elif isinstance(value, list):
            return any(
                Store._event_has_agent_reference(child, agent_id, container)
                for child in value
            )
        return False

    def purge_agent_events(self, project_id: str, agent_id: str) -> int:
        """从项目群流水中精确清除某 Agent 的消息、审批与身份事件。

        不做子串匹配：``pm_1`` 永远不会命中 ``pm_10``。普通用户文本里偶然写到
        id 不作为结构身份引用，避免误删无关聊天；结构字段和审批卡体则按精确值清理。
        """
        if not isinstance(agent_id, str) or not agent_id:
            return 0
        events = self.load_all_events(project_id)
        kept: list[dict[str, Any]] = []
        removed = 0
        for event in events:
            etype = str(event.get("type") or "")
            if event.get("agent_id") == agent_id or event.get("target_id") == agent_id:
                removed += 1
                continue
            if etype in {"approval_card", "approval_resolved", "instruction_injected", "report_submitted"} \
                    and self._event_has_agent_reference(event, agent_id):
                removed += 1
                continue

            current = dict(event)
            members = current.get("members")
            if isinstance(members, list):
                filtered = [
                    item for item in members
                    if not (isinstance(item, dict)
                            and (item.get("id") == agent_id or item.get("agent_id") == agent_id))
                ]
                if len(filtered) != len(members):
                    removed += 1
                    if etype in {"agents_created", "agents_rejected"} and not filtered:
                        continue
                    current["members"] = filtered
                    if isinstance(current.get("count"), int):
                        current["count"] = len(filtered)
            kept.append(current)

        if removed:
            self.compact(project_id, kept)
        return removed

    # ═══════════════════════════════════════════════════════════
    # [v0.8a A-1] 花名册
    #
    # 进程重启之后，队伍不该散。历史消息一直是落盘的（events.jsonl），
    # 于是重启后用户看到的是：**满屏的聊天记录，一个成员都没有**——
    # 群还在，人没了。花名册得跟着一起活下来。
    #
    # 存法和事件流水同款：一行一个成员，追加，不重写。
    # ═══════════════════════════════════════════════════════════
    def stored_agent(self, project_id: str, agent_id: str) -> dict[str, str] | None:
        """
        [v0.9d] 花名册里这个人的那一行（**含已归档的**）。没有 → None。

        「归档的人被加回来还是同一个名字」就靠它。
        """
        return self.load_roster_full(project_id).get(agent_id)

    def roster_path(self, project_id: str) -> Path:
        return self.events_dir / f"{_safe_name(project_id)}{ROSTER_SUFFIX}"

    def upsert_agent(self, project_id: str, agent_id: str, role: str,
                     status: str = "active", name: str | None = None) -> None:
        """
        项目花名册：写入/更新一个成员。

        ★ 幂等：这个人已经在册、角色和状态都没变 → **一个字节都不写**。
          不这么挡的话，每次重启都会把整份花名册重新 upsert 一遍
          （温载时要把 Worker 实例重建出来，那条路会经过这里），
          文件每重启一次就长一截——跑一年，一个五人小队能攒出几千行。

        [v0.9b] status: "active" | "removed" | "deleted"。
          归档（removed）**不删行**——追加一行新的、status=removed 的记录，
          加载时后写的盖掉先写的（JSONL 的老规矩）。
          为什么不删：一个人来过、干过活、交过报告，这件事发生过。
          花名册是流水账，不是名单。
        """
        if not isinstance(agent_id, str) or not agent_id:
            return
        if status not in {"active", "removed", "deleted"}:
            status = "active"

        # ═══ [v0.9d] 名字在**落盘的这一刻**定下来，此后只读不掷 ═══
        #
        #   优先级：调用方给的 > 花名册里已有的（含**归档**的那一行）> 现掷一个
        #
        #   ★ 「含归档的那一行」是这次的关键：fe_1 被归档、又被加回来时，
        #     load_roster_full 里他还在（status=removed，name 也还在）→ 用旧名。
        #     他回来了，他还是他 —— 不该顶着一张新面孔。
        full = self.load_roster_full(project_id)
        cur = full.get(agent_id)
        if cur and cur.get("status") == "deleted" and status != "deleted":
            raise ValueError(f"成员 id {agent_id!r} 已被彻底删除，不能复用；请创建新的 id")
        taken = {row["name"] for aid, row in full.items()
                 if aid != agent_id and row.get("name")}

        final_name = "" if status == "deleted" else agent_name_for(
            agent_id, role, stored_name=name or (cur or {}).get("name"), taken=taken,
        )
        if (cur and cur.get("role") == role
                and cur.get("status", "active") == status
                and cur.get("name") == final_name):
            return                                   # 一个字节都不用写

        line = json.dumps({"agent_id": agent_id, "role": role,
                           "name": final_name,
                           "status": status},
                          ensure_ascii=False) + "\n"
        try:
            with open(self.roster_path(project_id), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as exc:
            # 写不进去也不能把服务搞停——人已经在内存里的花名册上了，活照干，
            # 只是重启之后会丢。出声，别装死。
            log.error("[%s] 花名册落盘失败（%s → %s）：%s", project_id, agent_id, role, exc)

    def load_roster(self, project_id: str) -> dict[str, str]:
        """
        读回**在册的活人** → {agent_id: role}。没文件 / 文件坏了 → 返回 {}。

        [v0.9b] ★ 已归档（status=removed）的人**不在这里面**。

          这一条是有意为之，而且是最省心的一处：所有靠 load_roster 判断
          「这个群里有谁」的地方——引擎温载、server 发给前端的 members、
          propose_next 的 target 校验——**一次全部对上**，不用挨个去加 filter。
          想看完整流水（含已归档的）→ load_roster_full()。
        """
        return {
            aid: row["role"]
            for aid, row in self.load_roster_full(project_id).items()
            if row.get("status", "active") == "active"
        }

    def load_roster_full(self, project_id: str) -> dict[str, dict[str, str]]:
        """
        读回**整本流水账** → {agent_id: {"role", "name", "status"}}。

        [v0.9d] ★ **`name` 只在盘上真的有的时候才出现在返回值里。**

          v0.9c 这里会拿公式「补」一个（「前端 1」），于是调用方**分不清**
          「这个人有名字」和「这个人的名字是我刚编的」——而这两件事的处理方式相反：
            · 真有名字 → 原样用
            · 没有名字 → 该**掷一个随机名并落盘**（一次性升级），而不是每次都编同一个假名
          所以现在：盘上没有就**不给**，让调用方自己决定（server 温载时会补掷 + 落盘；
          纯展示的路径用 legacy_display_name 兜一个稳定的假名）。

        同一个 agent_id 出现多次 → **后来的盖掉先来的**（顺序读，直接覆盖 dict 里的值，
        天然就是「最后一条为准」）。坏行跳过——和事件流水同一条规矩。
        """
        path = self.roster_path(project_id)
        if not path.exists():
            return {}

        roster: dict[str, dict[str, str]] = {}
        bad = 0
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        bad += 1
                        continue
                    if not isinstance(row, dict):
                        bad += 1
                        continue
                    aid, role = row.get("agent_id"), row.get("role")
                    status = row.get("status")
                    name = row.get("name")
                    if isinstance(aid, str) and aid and isinstance(role, str):
                        row_out: dict[str, str] = {           # ★ 后来的盖掉先来的
                            "role": role,
                            "status": status if status in ("active", "removed", "deleted") else "active",
                        }
                        if isinstance(name, str) and name:    # [v0.9d] 盘上有才给
                            row_out["name"] = name
                        roster[aid] = row_out
                    else:
                        bad += 1
        except OSError as exc:
            log.error("[%s] 花名册读不了：%s", project_id, exc)
            return {}

        if bad:
            log.warning("[%s] 花名册有 %d 行坏的，已跳过", project_id, bad)
        return roster

    def delete_agent(
        self, project_id: str, agent_id: str, *, reserve_id: bool = True,
    ) -> dict[str, str] | None:
        """删除花名册中的身份资料；普通成员保留无个人信息的技术墓碑防 id 复用。

        ``reserve_id=False`` 只给系统固定角色（当前是 coordinator）使用：个人身份被清空，
        但该技术角色未来仍可由项目引擎重新实例化。
        """
        full = self.load_roster_full(project_id)
        previous = full.pop(agent_id, None)
        if previous is None:
            return None
        if reserve_id:
            full[agent_id] = {"role": "", "status": "deleted"}
        self._write_roster(project_id, full)
        return previous

    def _write_roster(self, project_id: str, roster: dict[str, dict[str, str]]) -> None:
        def line(aid: str, row: dict[str, str]) -> str:
            out: dict[str, str] = {
                "agent_id": aid,
                "role": str(row.get("role") or ""),
                "status": str(row.get("status") or "active"),
            }
            if row.get("name"):
                out["name"] = str(row["name"])
            return json.dumps(out, ensure_ascii=False) + "\n"

        self._atomic_write(
            self.roster_path(project_id),
            "".join(line(aid, row) for aid, row in roster.items()),
        )

    def compact_roster(self, project_id: str) -> None:
        """把花名册重写成「一人一行」（原子）。不是必须的——upsert 已经幂等了。

        ★ 压实时**保留归档记录**（status=removed 的人也留一行）——
          压实是去重，不是遗忘。
        """
        roster = self.load_roster_full(project_id)
        def line(aid: str, row: dict[str, str]) -> str:
            out: dict[str, str] = {"agent_id": aid, "role": row["role"]}
            if row.get("name"):
                out["name"] = row["name"]          # [v0.9d] 没有就别编 —— 压实是去重，不是改写
            out["status"] = row.get("status", "active")
            return json.dumps(out, ensure_ascii=False) + "\n"

        self._write_roster(project_id, roster)

    # ═══════════════════════════════════════════════════════════
    def iter_project_ids(self) -> Iterator[str]:
        for p in self.load_projects():
            yield p["project_id"]
