# [v1.0.13][R1][R2] Readiness-gated idempotent welcome and explicit coordinator identity.
"""
server.py — WebSocket 入口（127.0.0.1:8080）+ /health（127.0.0.1:8081）。

握手（PROTOCOL.md §b）：
  1. 连接建立 → **立刻加入广播集合**（实时事件可能先于回放到达，这是设计如此；
     前端有握手缓冲负责排序去重）
  2. 等首帧 `replay_request {project_id, since_seq}`，窗口 5 秒
  3. 回放前先补发这个客户端还没收过的 project_created（每客户端一份账）
  4. 回放 → `replay_complete {project_id, last_seq, unread_count}`
     · Ring 覆盖完整区间时走内存增量
     · since_seq=0 / Ring 有缺口时由 JSONL 持久历史兜底，再与 Ring 合并
     · 只有纯内存模式且请求区间确实丢失时才发 `resync_required`
  5. 5 秒没等到首帧 → `replay_complete {last_seq: 0}`（无 project_id 的超时分支）

入站指令（envelope.ts 出站联合类型）：
  user_message / approve / reject / create_project / replay_request /
  request_snapshot / set_project_directory / cancel_project_directory /
  token_usage_req / ping / shutdown  （+ 额外的 mark_read，用于未读水位）

错误分两级（B-3）：
  · 能归因到项目 → 引擎级 error（带 project_id + seq，进会话流）
  · 归因不了（畸形帧、未知指令）→ 服务器级 error（无 seq，进前端全局通知）
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import mimetypes                # [v0.36] /preview 端点按扩展名猜 Content-Type
import os
import re
import shutil
import signal
import subprocess
import sys
import time                     # [v0.44.8] 置顶「后来者居上」的单调排序戳
import uuid
from contextvars import ContextVar
from datetime import datetime            # [v0.38] /history 按日期过滤 & ts 解析
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit   # [v0.36] 解析 /preview?project_id=&path=

import websockets

from knowe_provenance import activate_build
from knowe_core.provider_client import ProviderClient
from knowe_storage._sqlite import (
    quiesce_sqlite_databases_under,
    release_sqlite_quiescence,
)
from websockets.asyncio.server import ServerConnection, serve

from .agents.zinnia import PLATFORM_PROJECT_ID, ZINNIA, ZinniaAgent, new_project_id
from .tools_knowe import resolve_in_sandbox            # [v0.36] /preview 复用沙箱路径解析
from .tools_knowe import KNOWN_ROLES, _next_agent_id   # [主动拉入worker] 职能白名单 + id 生成

#: [20260805] 移除 MAX_TEAM_ROLES 硬上限——一个群聊内 agent 数量原则上无上限，
#: 身份唯一性由建人时的去重保证（同一职能前缀只实例化一个 Worker）。

#: [v0.8c #2b] 软件一打开，知知先开口。**不是用户消息——不发 user_echo。**
#:
#: 走的是和 KICKOFF 一样的路（eng.submit → 正常回合），所以这句话是**她自己说的**，
#: 不是后端伪造的一条 message —— 她的模型历史里因此也留着这一轮，
#: 用户接下来说什么，她接得上。
#: [v1.0.21.3.r4] 函数化：msg 在调用时求值（模块级求值 = 语言固化）。
def _zinnia_welcome_text() -> str:
    """软件一打开，知知先开口（非用户消息）。"""
    return (
        msg("server.py.001") +
        msg("server.py.002") +
        msg("server.py.003") +
        msg("server.py.004")
    )


#: [v0.5 #10] 新群建好时塞给项目经理的开场白。不是用户消息——不发 user_echo。
#: [v1.0.21.3.r4] 修复：原 KICKOFF 模块级引用 name 未定义（NameError），改为函数传参。
def _kickoff_text(proj_name: str) -> str:
    """项目刚建好时给项目经理的开场白。"""
    return msg("server.py.005", name=proj_name) + msg("server.py.006") + msg("server.py.007")


def project_id_for_card(approval_id: str) -> str:
    """Legacy v0.15 mapping retained only for old callers/data migrations."""
    return f"p_{approval_id}"


# ═══════════════════════════════════════════════════════════════
# [v1.0.23.1] 转发 LLM 模板（PRD R3 / 架构 D4）
#   模板只给 LLM 读，绝不进用户界面（前端显示的是配言 + 引用窗）。
#   附言为空时「并配言」后为空串，语义交 LLM 判断。
#   原文里的 @ 统一转全角：模板串会经过 rewrap_group_mention 的 @ 剥离，
#   不转的话原文里的 @别名 会被误剥，原文就残缺了（架构风险 R1）。
# ═══════════════════════════════════════════════════════════════

_FORWARD_LLM_MARKER = "转发了过来，并配言"


def build_forward_template(
    username: str,
    project_name: str | None,
    source_name: str,
    original: str,
    comment: str,
) -> str:
    """「用户{用户名}将{群/项目名}中{来源者身份}的消息{原消息内容}转发了过来，并配言{附言}」"""
    src = f"{project_name}中" if project_name else ""
    original_safe = (original or "").replace("@", "＠")
    return (
        f"用户{username}将{src}{source_name}的消息{original_safe}"
        f"{_FORWARD_LLM_MARKER}{comment}"
    )


# [v0.37] 群内 Agent 私聊会话 id = `dm:{projectId}:{agentId}`（与前端 chat.ts 一处约定）。
_DM_PREFIX = "dm:"


def _parse_dm(session_id: Any) -> tuple[str, str] | None:
    """`dm:{group}:{agent}` → (group, agent)。不是群内私聊 id → None。

    只在**第一个**冒号处切：前缀 `dm:` 之后到第一个冒号是 group，其余全算 agent
    （即便 agent 段里再含冒号也不会切错）。与前端 parseDmId 对称。
    """
    if not isinstance(session_id, str) or not session_id.startswith(_DM_PREFIX):
        return None
    rest = session_id[len(_DM_PREFIX):]
    sep = rest.find(":")
    if sep <= 0 or sep >= len(rest) - 1:
        return None
    return rest[:sep], rest[sep + 1:]


# [v0.36] /preview 的 Content-Type 表。mimetypes 猜不准或漏猜的这几样自己钉死：
#   · md / csv / tsv：mimetypes 常给 application/octet-stream，会触发下载而不是内嵌
#   · svg：必须是 image/svg+xml，<img> 才认
#   · docx/pptx/xlsx：给 Office 官方 MIME，浏览器不会误判成 zip
_PREVIEW_CONTENT_TYPE_BY_EXT: dict[str, str] = {
    "md": "text/markdown; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "tsv": "text/tab-separated-values; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "svg": "image/svg+xml",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp", "ico": "image/x-icon",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
}


_PREVIEW_TEXT_EXTS: frozenset[str] = frozenset({
    "jsonc", "json5", "js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts",
    "py", "pyw", "css", "scss", "sass", "less", "yaml", "yml", "toml",
    "xml", "xsd", "xsl", "xslt", "sql", "graphql", "gql", "proto",
    "sh", "bash", "zsh", "fish", "ps1", "psm1", "psd1", "bat", "cmd",
    "ini", "cfg", "conf", "env", "properties", "java", "kt", "kts",
    "c", "h", "cc", "cpp", "cxx", "hpp", "hxx", "cs", "fs", "fsx",
    "go", "rs", "rb", "php", "swift", "dart", "scala", "lua", "r",
    "pl", "pm", "ex", "exs", "erl", "hrl", "clj", "cljs", "cljc",
    "edn", "groovy", "gradle", "vue", "svelte", "astro", "cmake",
})


def _preview_content_type(filename: str) -> str:
    """文件名 → HTTP Content-Type，认不出时退回二进制流。"""
    dot = filename.rfind(".")
    ext = filename[dot + 1:].lower() if 0 <= dot < len(filename) - 1 else ""
    if ext in _PREVIEW_CONTENT_TYPE_BY_EXT:
        return _PREVIEW_CONTENT_TYPE_BY_EXT[ext]
    if ext in _PREVIEW_TEXT_EXTS or filename.lower() in {
        "dockerfile", "containerfile", "makefile", "gnumakefile",
        "cmakelists.txt", "jenkinsfile", ".gitignore", ".dockerignore", ".editorconfig",
    }:
        return "text/plain; charset=utf-8"
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"



def _reveal_in_file_manager(target: Path) -> None:
    """Ask the desktop file manager to show ``target`` without invoking a shell."""
    target = target.resolve(strict=True)
    devnull = subprocess.DEVNULL
    if sys.platform.startswith("win"):
        subprocess.Popen(  # noqa: S603 — fixed executable, sandbox-resolved path
            ["explorer.exe", f"/select,{target}"],
            stdout=devnull,
            stderr=devnull,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    if sys.platform == "darwin":
        subprocess.Popen(  # noqa: S603 — fixed executable, no shell
            ["open", "-R", str(target)],
            stdout=devnull,
            stderr=devnull,
            start_new_session=True,
        )
        return

    # FreeDesktop ShowItems can select the file in Nautilus/Dolphin and other compliant
    # managers.  It may be unavailable in minimal Linux desktops, so fall back to opening
    # the parent directory with the first installed standard launcher.
    gdbus = shutil.which("gdbus")
    if gdbus:
        uri = target.as_uri().replace("\\", "\\\\").replace("'", "\\'")
        try:
            completed = subprocess.run(  # noqa: S603 — executable resolved by shutil.which
                [
                    gdbus,
                    "call",
                    "--session",
                    "--dest", "org.freedesktop.FileManager1",
                    "--object-path", "/org/freedesktop/FileManager1",
                    "--method", "org.freedesktop.FileManager1.ShowItems",
                    f"['{uri}']",
                    "",
                ],
                stdout=devnull,
                stderr=devnull,
                timeout=3,
                check=False,
            )
            if completed.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            # Minimal/headless desktops often have gdbus installed but no usable session
            # bus.  That is not terminal: continue to Nautilus/Dolphin/gio/xdg-open.
            pass

    candidates = [
        ("nautilus", ["--select", str(target)]),
        ("dolphin", ["--select", str(target)]),
        ("gio", ["open", str(target.parent)]),
        ("xdg-open", [str(target.parent)]),
    ]
    for executable, args in candidates:
        resolved = shutil.which(executable)
        if not resolved:
            continue
        subprocess.Popen(  # noqa: S603 — executable resolved by shutil.which
            [resolved, *args],
            stdout=devnull,
            stderr=devnull,
            start_new_session=True,
        )
        return
    raise RuntimeError(msg("server.py.008"))
from .config import CONFIG
from . import runtime_settings   # [v0.44 设置] /settings 端点 + 引擎热更新的权威状态
from . import aux_client         # [v1.0.19.4] 报错翻译（附件被打回时把机器错误译成人话）
from .attachments import AttachmentError, build_parts, echo_meta
from .feature_flags import FeatureFlag, enabled as feature_enabled, snapshot as feature_flag_snapshot
from .contract import ContractViolation, now_ts
from .delete_ops import (
    DeletePathBusyError,
    is_link_like,
    purge_staged_path,
    restore_staged_path,
    stage_project_root,
    stage_delete_path,
)
from .engine import (
    COORDINATOR,
    ProjectEngine,
    ProjectResourceCloseError,
    WorkspaceUnavailable,
)
from .hub import Client, Hub
from .knowledge_graph import KnowledgeGraphManager
from .knowledge_api import dispatch_knowledge_http
from .memory_manager import MemoryManager
from .persist import Store, legacy_display_name
from .migrations import run_data_migrations   # [任务 1.6] 启动时数据版本检查 + 迁移
from .storage_migrator import run_sqlite_migrations   # [v1.0.31 R4] 存量 SQLite 迁移
from .storage_maintenance import run_all_maintenance  # [v1.0.31 R2/R3] 流水压缩 + 快照裁剪
from .token_usage import aggregate_token_usage
from knowe_core.provider_client import normalize_usage_buckets
from .token_pricing import estimate_cost
from .workspace_layout import internal_workspace_for
from .platform_manifest import PlatformManifest   # [v0.12 D 5e] 平台清单 + 变更日志
from .privacy import sanitize_events
from .i18n_backend import msg


_HTTP_CORS_ORIGIN: ContextVar[str | None] = ContextVar(
    "knowe_http_cors_origin", default=None,
)

log = logging.getLogger("knowe.server")


_CANONICAL_PROJECT_ID_RE = re.compile(r"^project_\d{14}$")
_REQUEST_ALIAS_PREFIX = "request:"
_APPROVAL_ALIAS_PREFIX = "approval:"
DELETE_ENGINE_STOP_TIMEOUT_S = 5.0
DELETE_PRECOMMIT_TIMEOUT_S = 8.0
# [v1.0.31 R3] 完成任务快照保留最近 N 个完整（更旧只留结果摘要）。已拍板 N=50。
STORAGE_KEEP_RECENT = 50
HTTP_REQUEST_LINE_MAX = 8 * 1024
HTTP_HEADERS_MAX_BYTES = 32 * 1024
HTTP_HEADERS_MAX_COUNT = 100
HTTP_HEADERS_TOTAL_TIMEOUT_S = 5.0


class ProjectIdResolutionError(ValueError):
    """An inbound temporary/foreign project id cannot be resolved safely.

    Unknown aliases must never fall through to ``Hub.get_or_create``: doing so creates the
    exact ghost conversation/internal workspace this hotfix is meant to prevent.
    """


class ProjectClosingError(RuntimeError):
    """The project is being permanently deleted and may not be activated."""

    def __init__(self, project_id: str) -> None:
        super().__init__(msg("server.py.321", project_id=project_id))
        self.project_id = project_id


class ProjectDeleteError(RuntimeError):
    """A pre-commit project-delete stage failed and was rolled back.

    Permanent deletion is a local multi-resource transaction.  Keeping the public
    stage alongside the original exception lets the HTTP boundary report *where*
    the operation failed without exposing a Python traceback to the renderer.
    """

    def __init__(
        self,
        stage: str,
        detail: str,
        *,
        rollback_ok: bool,
        blocked_path: str | None = None,
        locking_processes: list[dict[str, Any]] | None = None,
        resource_close_issues: list[str] | None = None,
        self_lock: bool = False,
    ) -> None:
        super().__init__(detail)
        self.stage = stage
        self.detail = detail
        self.rollback_ok = rollback_ok
        self.blocked_path = blocked_path
        self.locking_processes = list(locking_processes or [])
        self.resource_close_issues = list(resource_close_issues or [])
        self.self_lock = bool(self_lock)


# v0.13：项目目录失效时由 Harness 直接说，不经过 LLM。固定文案不能被模型改写，
# 也不会因为供应商不可用而失声。
#: [v1.0.21.3.r4] 函数化（模块级 msg 求值 = 语言固化）。
def _directory_required_text() -> str:
    return msg("server.py.009") + msg("server.py.010") + msg("server.py.011")


def _directory_cancelled_text() -> str:
    return msg("server.py.012") + msg("server.py.013") + msg("server.py.014")


# [v0.13 fix] 用户点过“取消/暂缓”之后，后续发言只用这条温和提醒——不再弹卡片、不催、不拦。
#   和 DIRECTORY_REQUIRED 的区别在于把“什么时候恢复”的主动权交回给用户。
def _directory_paused_text() -> str:
    return msg("server.py.015") + msg("server.py.016")


def _directory_restored_text() -> str:
    return msg("server.py.017") + msg("server.py.018")


class KnoweServer:
    def __init__(self, data_dir: str | None = None) -> None:
        # [v0.4] 落盘。data_dir 为空串 → 纯内存（测试和「我就试试」模式用）
        raw_dir = CONFIG.data_dir if data_dir is None else data_dir
        self.store: Store | None = Store(raw_dir) if raw_dir else None
        self.data_root = Path(raw_dir or "./data").expanduser().resolve()
        self.hub = Hub(store=self.store)
        self.engines: dict[str, ProjectEngine] = {}
        self.platform: ProjectEngine | None = None   # [v0.4] 知知常驻的平台引擎
        # [v1.0.13 R1] Process-local serialization plus durable history/event-id
        # idempotency.  The lock covers startup, reconnect and duplicate POST /settings.
        self._welcome_lock = asyncio.Lock()
        self._welcome_pending = False
        self._welcome_error = ""
        # Process-local truth for SET-02.  It survives renderer reload/reconnect, but a real
        # backend restart naturally resets it because every Engine is rebuilt from persisted
        # settings.  GET /settings exposes the flag so the UI cannot accidentally hide a required
        # restart during ordinary reconciliation.
        self._settings_restart_required = False

        # [v0.11 C-1] 三层 Memory 的全局看门人：harness_memory.md 落在 data_dir 下，
        #   每个引擎共用这一个实例（Project Memory 落到各自 internal_workspace）。
        #   纯内存模式（raw_dir 空）也给它一个默认目录，功能照常，只是文件落在 ./data。
        self.memory = MemoryManager(self.data_root)
        # [v0.19] 项目级知识图谱看门人。和 MemoryManager 一样全局复用，数据仍按
        # internal_workspace 分项目隔离；更新由各项目引擎异步串行调度。
        self.knowledge = KnowledgeGraphManager(self.data_root)
        #: [v0.11 C-1] Harness Memory 定时兜底任务。
        self._harness_task: asyncio.Task[Any] | None = None
        # [v0.15] 关键状态变化走事件驱动刷新；短时间内的多条事件合并成一次全局汇总。
        self._harness_refresh_task: asyncio.Task[Any] | None = None
        # [v1.0.31 R2/R3] 本地存储维护循环（流水压缩 + 快照裁剪）。
        self._storage_task: asyncio.Task[Any] | None = None
        self._harness_dirty = False
        self._harness_update_lock = asyncio.Lock()

        # [v0.12 D · 问题五 5e] 平台清单：Knowe 自身的版本、安装路径、以及从安装起的变更日志。
        #   知知的平台上下文从这里取——她因此「天生就知道」软件本身的情况。
        self.platform_manifest = PlatformManifest(
            Path(raw_dir or "./data"), CONFIG.install_root, CONFIG.version,
        )

        # [v0.7 A0] project_id → 用户选的项目目录（Worker 沙箱的根）。
        #   persist.py 的 upsert_project 只吃 (id, name)，而它属于「不改的文件」——
        #   所以目录另记一本小账，落在 data_dir/project_dirs.json。
        #   不记的话，重启之后所有项目的沙箱都会悄悄退回默认目录，
        #   用户会发现「昨天写在我文件夹里的东西，今天 Agent 找不到了」。
        self.project_dirs: dict[str, str] = {}
        # Isolated HTML preview hosts are capability labels, not control endpoints.
        # The mapping exists only to resolve root-relative resources ("/assets/app.js")
        # back into the already-validated current project tree.
        self._preview_origin_projects: dict[str, str] = {}

        # [v0.44.8] 群聊列表偏好是项目配置的一部分，后端是唯一真源。
        # project_id → {pinned, muted, folded, pinned_at}；pinned/folded 互斥。
        self.conversation_states: dict[str, dict[str, Any]] = {}
        self._conversation_state_lock = asyncio.Lock()
        self._project_metadata_lock = asyncio.Lock()
        # Process-local activation fence.  Durable deletion remains the Harness tombstone;
        # this set only closes the race between "delete requested" and "tombstone committed".
        self._closing_projects: set[str] = set()
        # Project-internal reads dispatched through ``asyncio.to_thread`` outlive a
        # cancelled HTTP request unless the underlying thread is tracked separately.
        # Permanent deletion drains this registry before renaming the project root.
        self._project_internal_io_tasks: dict[str, set[asyncio.Task[Any]]] = {}
        self._restart_tasks: dict[str, asyncio.Task[None]] = {}
        self._purge_tasks: set[asyncio.Task[Any]] = set()
        self._shutdown_event = asyncio.Event()
        self._last_pinned_at = 0

        # [v0.16 hotfix] persistent inbound-id aliases → canonical project_YYYYMMDDHHMMSS.
        #
        # Historical rows used a bare approval_id as key.  New rows are namespaced:
        #   approval:<approval_id>  — create-project approval cards
        #   request:<raw_id>        — optimistic/client-generated ids (p_ap_*, old slugs, ...)
        #
        # The old file/attribute name is deliberately retained so upgrades are in-place and a
        # rollback can still read the approval mappings it understands.
        self.project_card_ids: dict[str, str] = {}

        # Disk-backed project ids are loaded once at startup.  ID normalization sits on every
        # inbound frame, so it must not re-read projects.json for each chat message.
        self._persisted_project_ids: set[str] = set()

        # [v0.13 3b] 目录生命周期状态。失效状态单独落盘，不能靠 Path.exists() 临时猜：
        # 用户取消弹窗后，即使原路径碰巧又出现，也必须等显式“重新选择目录”才解封。
        self.project_dir_status: dict[str, dict[str, Any]] = {}

        # 目录失效时会停掉整台项目引擎；保留其 Harness 对话历史，重新绑定目录后接着用。
        self._paused_histories: dict[str, list[dict[str, str]]] = {}

        # [v0.13 fix] 用户主动取消/关闭“目录选择”弹窗的项目集合 = “暂缓，别再弹”。
        #   只活在内存里：重启后允许再弹一次（用户可再次取消）——这样既治好死循环，
        #   又不会让某个项目永久失声、用户忘了团队为什么一直不动。
        #   目录一旦重新有效（_mark_project_dir_valid）即清除。
        self._directory_popup_paused: set[str] = set()

        # [v0.8a-fix] 花名册灌过一次就不用再灌（engine_for 每次 replay 都会被调）
        self._roster_restored: set[str] = set()

        # [v0.8e #3] 「这个人我已经向前端宣告过了」——**终生一次**的账本。
        #   project_id → {agent_id, …}
        #
        #   为什么不能只看 ring：ring 是会被淘汰的、会被重建的、replay_since(0) 的语义
        #   也未必永远是「给我全部」。把「补没补过」这件事的判据挂在一个会变的东西上，
        #   就是这条 bug 的根 —— 每开一次机就补一条，用户的群里攒了 7 条
        #   「Ochre、赖全、Kerry 已加入项目」。
        #
        #   现在它自己有一本账，落在磁盘上。ring 仍然作为**补充**证据
        #   （谁说过话、谁被宣告过），但不再是唯一依据。
        self.announced: dict[str, set[str]] = {}

    # ═══════════════════════════════════════════════════════════
    # [v0.7 A0 · v0.12 D 6a] 项目目录的小账本
    #   [v0.12 D 6a] Harness 层的账本文件收进 data/harness/，不再裸在 data/ 根。
    #     读的时候两处都看（迁移期兼容），写只写新位置。
    # ═══════════════════════════════════════════════════════════
    @property
    def _harness_dir(self) -> Path | None:
        if self.store is None:
            return None
        return Path(self.store.root) / "harness"

    @property
    def _dirs_path(self) -> Path | None:
        if self._harness_dir is None:
            return None
        return self._harness_dir / "project_dirs.json"

    @property
    def _legacy_dirs_path(self) -> Path | None:
        if self.store is None:
            return None
        return Path(self.store.root) / "project_dirs.json"

    @property
    def _project_card_ids_path(self) -> Path | None:
        if self._harness_dir is None:
            return None
        return self._harness_dir / "project_card_ids.json"

    def _internal_workspace_for(self, project_id: str) -> Path:
        """Stable internal root; independent from the user-selected business directory."""
        return internal_workspace_for(self.data_root, project_id)

    @property
    def _zinnia_welcome_path(self) -> Path | None:
        """[v0.13 模块A] 一个磁盘面包屑：知知曾经欢迎过。**仅供调试/迁移，不再作为是否欢迎的判据**
        （判据只看 `_last_speaker` 的实际对话状态）。"""
        if self._harness_dir is None:
            return None
        return self._harness_dir / ".zinnia_welcomed"

    def _mark_zinnia_welcomed(self) -> None:
        """原子写入欢迎面包屑；失败只记日志，不影响主流程。"""
        path = self._zinnia_welcome_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text("welcomed\n", encoding="utf-8")
            tmp.replace(path)
        except OSError:
            log.exception("写入知知欢迎标记失败：%s", path)

    # ── [v0.8e #3] 宣告账本 ──
    @property
    def _announced_path(self) -> Path | None:
        if self._harness_dir is None:
            return None
        return self._harness_dir / "announced_members.json"

    @property
    def _legacy_announced_path(self) -> Path | None:
        if self.store is None:
            return None
        return Path(self.store.root) / "announced_members.json"

    @property
    def _project_delete_transactions_path(self) -> Path:
        """Durable journal for project deletion transactions.

        Staged project directories may live outside Knowe's data root, so a process
        crash cannot be recovered by scanning ``data/`` alone.  The journal records
        the exact original/staged path pairs *before* the first rename.  On startup a
        missing tombstone means rollback; an existing tombstone means finish cleanup.
        """
        return self.data_root / "harness" / ".project_delete_transactions.json"

    @staticmethod
    def _short_delete_error(exc: BaseException, limit: int = 300) -> str:
        text = re.sub(r"\s+", " ", str(exc or "")).strip()
        if not text:
            text = exc.__class__.__name__
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _load_project_delete_transactions(
        self, *, strict: bool = False,
    ) -> dict[str, dict[str, Any]]:
        path = self._project_delete_transactions_path
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(msg("server.py.019"))
            return {
                str(pid): dict(row)
                for pid, row in raw.items()
                if isinstance(pid, str) and pid and isinstance(row, dict)
            }
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            if strict:
                raise OSError(msg("server.py.020", exc=exc)) from exc
            log.exception(msg("server.py.021"))
            return {}

    def _save_project_delete_transactions(
        self, rows: dict[str, dict[str, Any]],
    ) -> None:
        path = self._project_delete_transactions_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(rows, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            # fsync the directory when the platform exposes a directory fd.  Failure
            # here is non-fatal on Windows, where opening directories this way is not
            # supported; the file replacement itself has already completed.
            try:
                fd = os.open(path.parent, os.O_RDONLY)
            except OSError:
                fd = None
            if fd is not None:
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _put_project_delete_transaction(self, row: dict[str, Any]) -> None:
        project_id = str(row.get("project_id") or "")
        if not project_id:
            raise ValueError(msg("server.py.022"))
        rows = self._load_project_delete_transactions(strict=True)
        saved = dict(row)
        saved["updated_at"] = now_ts()
        rows[project_id] = saved
        self._save_project_delete_transactions(rows)

    def _drop_project_delete_transaction(self, project_id: str) -> None:
        rows = self._load_project_delete_transactions(strict=True)
        if project_id not in rows:
            return
        rows.pop(project_id, None)
        self._save_project_delete_transactions(rows)

    def _load_announced(self) -> None:
        path = self._announced_path
        legacy = self._legacy_announced_path
        if path is not None and not path.is_file() and legacy is not None and legacy.is_file():
            path = legacy                       # [6a] 新位置没有 → 读老位置（迁移期）
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning(msg("server.py.023"))
            return
        if isinstance(data, dict):
            self.announced = {
                pid: {a for a in ids if isinstance(a, str)}
                for pid, ids in data.items() if isinstance(ids, list)
            }
            log.info(msg("server.py.024"), len(self.announced))

    def _save_announced(self) -> None:
        path = self._announced_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({pid: sorted(ids) for pid, ids in self.announced.items()},
                           ensure_ascii=False, indent=2),
                "utf-8",
            )
        except OSError as exc:
            log.warning(msg("server.py.025"), exc)

    def _write_projects_index(self) -> None:
        """
        [v0.12 D · 问题六 6a/6d] 写一份**人读**的项目索引 data/harness/projects.md：
        项目名 ↔ id ↔ 目录，一目了然。opaque 的 p_xxxx 从此有处可查，方便人工溯源。
        纯展示、失败即忽略。
        """
        if self.store is None:
            return
        try:
            rows = self.store.load_projects()
            lines = [
                msg("server.py.026"),
                msg("server.py.027", **{"now_ts()": now_ts()}, **{"len(rows)": len(rows)}),
                "",
                msg("server.py.028"),
                "| --- | --- | --- | --- |",
            ]
            for r in rows:
                pid = r.get("project_id", "")
                name = r.get("name") or pid
                d = self.project_dirs.get(pid, msg("server.py.029"))
                status = self.project_dir_status.get(pid, {}).get("status", "valid")
                label = msg("s.030a") if status == "invalid" else msg("s.030b")
                lines.append(f"| {name} | `{pid}` | {d} | {label} |")
            harness = self._harness_dir
            if harness is not None:
                harness.mkdir(parents=True, exist_ok=True)
                (harness / "projects.md").write_text("\n".join(lines) + "\n", "utf-8")
        except Exception:
            log.exception(msg("server.py.031"))

    def _load_project_card_ids(self) -> None:
        path = self._project_card_ids_path
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict):
                self.project_card_ids = {
                    str(k): str(v) for k, v in data.items()
                    if isinstance(k, str) and isinstance(v, str)
                }
        except (OSError, json.JSONDecodeError):
            log.warning(msg("server.py.032"))

    def _save_project_card_ids(self) -> None:
        path = self._project_card_ids_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self.project_card_ids, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning(msg("server.py.033"), exc)

    @staticmethod
    def _request_alias_key(requested: str) -> str:
        return _REQUEST_ALIAS_PREFIX + requested

    @staticmethod
    def _approval_alias_key(approval_id: str) -> str:
        return _APPROVAL_ALIAS_PREFIX + approval_id

    def _mapped_project_id(self, requested: str) -> str | None:
        """Read both hotfix aliases and v0.16's legacy bare-approval mapping."""
        keys = [self._request_alias_key(requested)]
        if requested.startswith("p_") and len(requested) > 2:
            approval_id = requested[2:]
            keys.extend((self._approval_alias_key(approval_id), approval_id))
        for key in keys:
            mapped = self.project_card_ids.get(key)
            if isinstance(mapped, str) and mapped:
                request_key = self._request_alias_key(requested)
                if request_key not in self.project_card_ids:
                    # Lazily normalize v0.16's bare approval mapping on first use.
                    self.project_card_ids[request_key] = mapped
                    self._save_project_card_ids()
                return mapped
        return None

    def _deleted_project_ids(self) -> set[str]:
        """Return durable deletion tombstones when Harness Memory is initialized.

        Identity resolution is also exercised by lightweight recovery/compatibility
        callers that intentionally construct only the routing state.  Absence of the
        Memory projector must not weaken strict unknown-id rejection or crash those
        read-only paths.
        """
        memory = getattr(self, "memory", None)
        if memory is None:
            return set()
        return set(memory.deleted_project_ids())

    def _assert_project_activatable(self, project_id: str) -> None:
        """Reject every engine-creating path once deletion begins or commits."""
        if project_id in self._closing_projects or project_id in self._deleted_project_ids():
            raise ProjectClosingError(project_id)

    def _known_project_ids(self) -> set[str]:
        """Return exact project ids that already belong to real persisted/runtime projects.

        This distinction is what preserves old ``p_*`` projects without treating every unknown
        ``p_*``/slug sent by a client as permission to create a new engine.
        """
        deleted = self._deleted_project_ids()
        known = (
            set(self.hub.projects)
            | set(self.engines)
            | set(self.project_dirs)
            | set(self.project_dir_status)
            | set(self._persisted_project_ids)
            | deleted
        )
        return known

    def _allocate_canonical_project_id(self) -> str:
        """Allocate a collision-free canonical id against runtime and persisted state."""
        known = self._known_project_ids()
        for _ in range(10_000):
            candidate = new_project_id()
            if not _CANONICAL_PROJECT_ID_RE.fullmatch(candidate):
                raise RuntimeError(msg("server.py.326", candidate=candidate))
            if candidate not in known:
                return candidate
        raise RuntimeError(msg("server.py.253"))

    def _project_id_for_request(self, requested: str) -> str:
        """Assign a stable canonical id to one optimistic/client-generated id."""
        key = self._request_alias_key(requested)
        existing = self.project_card_ids.get(key)
        if isinstance(existing, str) and existing:
            return existing
        candidate = self._allocate_canonical_project_id()
        self.project_card_ids[key] = candidate
        self._save_project_card_ids()
        log.info("project id alias %r → %s", requested, candidate)
        return candidate

    def _bind_request_alias(self, requested: str, canonical: str) -> None:
        """Persist a wire request id as an alias of the final authoritative project id."""
        if not requested or requested == canonical:
            return
        key = self._request_alias_key(requested)
        previous = self.project_card_ids.get(key)
        if previous == canonical:
            return
        if isinstance(previous, str) and previous:
            log.warning("project request alias %r rebound %s → %s", requested, previous, canonical)
        self.project_card_ids[key] = canonical
        self._save_project_card_ids()

    def _load_project_dirs(self) -> None:
        path = self._dirs_path
        legacy = self._legacy_dirs_path
        if path is not None and not path.is_file() and legacy is not None and legacy.is_file():
            path = legacy                       # [6a] 新位置没有 → 读老位置（迁移期）
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("project_dirs.json 读不了或不是合法 JSON —— 这一轮先按默认目录跑")
            return
        if isinstance(data, dict):
            self.project_dirs = {k: str(v) for k, v in data.items() if isinstance(v, str)}
            log.info("载入 %d 个项目目录", len(self.project_dirs))

    def _save_project_dirs(self) -> bool:
        """原子写项目目录账本；老调用方可忽略返回值，改名事务会据此决定是否回滚。"""
        path = self._dirs_path
        if path is None:
            return True
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8", newline="\n") as fh:
                json.dump(self.project_dirs, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            return True
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            log.warning(msg("server.py.034"), exc)
            return False

    # ── [v0.44.8] 群聊列表偏好（项目配置持久态） ──
    @property
    def _conversation_states_path(self) -> Path | None:
        if self._harness_dir is None:
            return None
        return self._harness_dir / "project_conversation_state.json"

    def _normalize_conversation_state(self, value: Any) -> dict[str, Any]:
        row = value if isinstance(value, dict) else {}
        folded = bool(row.get("folded"))
        pinned = bool(row.get("pinned")) and not folded
        try:
            pinned_at = max(0, int(row.get("pinned_at") or 0)) if pinned else 0
        except (TypeError, ValueError):
            pinned_at = 0
        return {
            "pinned": pinned,
            "muted": bool(row.get("muted")),
            "folded": folded,
            "pinned_at": pinned_at,
        }

    def _conversation_state(self, project_id: str) -> dict[str, Any]:
        states = getattr(self, "conversation_states", None)
        if states is None:
            states = {}
            self.conversation_states = states
        row = self._normalize_conversation_state(states.get(project_id))
        if project_id == PLATFORM_PROJECT_ID or _parse_dm(project_id) is not None:
            # 知知和 dm:* 都不是项目群聊：即使 replay 等通用路径来读取，
            # 也绝不把它们登记进置顶 / 免打扰 / 折叠的项目菜单状态。
            return dict(row)
        # 总把规范形状写回内存，后续所有读者拿到的是同一语义。
        states[project_id] = dict(row)
        return dict(row)

    def _load_conversation_states(self) -> None:
        path = self._conversation_states_path
        if path is None or not path.is_file():
            return
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning(msg("server.py.035"))
            return
        rows = raw.get("projects") if isinstance(raw, dict) and isinstance(raw.get("projects"), dict) else raw
        if not isinstance(rows, dict):
            return
        self.conversation_states = {
            str(pid): self._normalize_conversation_state(value)
            for pid, value in rows.items()
            if isinstance(pid, str) and pid and pid != PLATFORM_PROJECT_ID
            and _parse_dm(pid) is None
        }
        self._last_pinned_at = max(
            (int(row.get("pinned_at") or 0) for row in self.conversation_states.values()),
            default=0,
        )
        log.info(msg("server.py.036"), len(self.conversation_states))

    def _save_conversation_states(self) -> bool:
        """原子持久化菜单状态；磁盘模式写失败时必须让调用方回滚，不能假装成功。"""
        path = self._conversation_states_path
        if path is None:
            # 纯内存测试/临时模式本来就没有重启持久化语义。
            return True
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "projects": {
                    pid: self._normalize_conversation_state(row)
                    for pid, row in sorted(self.conversation_states.items())
                    if pid != PLATFORM_PROJECT_ID and _parse_dm(pid) is None
                },
            }
            with tmp.open("w", encoding="utf-8", newline="\n") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            # POSIX 上同步目录项；Windows/部分文件系统不支持时不把已成功的 replace 判失败。
            try:
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                dir_fd = os.open(path.parent, flags)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
            return True
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            log.warning("群聊列表偏好持久化失败（%s）——本次操作回滚", exc)
            return False

    def _next_pinned_at(self) -> int:
        # 微秒时间戳保持在 JavaScript Number 的安全整数范围内；再和高水位取 max+1，
        # 即使同一微秒内连点，也能严格保证「后来者居上」。
        self._last_pinned_at = max(time.time_ns() // 1_000, self._last_pinned_at + 1)
        return self._last_pinned_at

    def _apply_state_to_engine(self, project_id: str) -> None:
        eng = getattr(self, "engines", {}).get(project_id)
        if eng is None:
            return
        row = self._conversation_state(project_id)
        eng.apply_conversation_preferences(**row)

    def _project_created_payload(self, project_id: str) -> dict[str, Any]:
        """兼容刷新帧：旧前端不认识新事件，也能借它触发 HTTP 对账。"""
        proj = self.hub.projects[project_id]
        state = self._conversation_state(project_id)
        payload: dict[str, Any] = {
            "type": "project_created",
            "project_id": project_id,
            "project_name": proj.name,
            # muted / folded 是全局静默：旧客户端只认识 unread_count，也不能被它点亮。
            "unread_count": 0 if state["muted"] or state["folded"] else self.hub.unread_count(project_id),
        }
        known_dir = self.project_dirs.get(project_id)
        if known_dir:
            payload["project_dir"] = known_dir
        members = self._members_of(project_id)
        if members:
            payload["members"] = members
        dir_info = self._directory_required_info(project_id)
        if dir_info is not None:
            payload["directory_required"] = dir_info
        return payload

    async def _broadcast_conversation_state(self, project_id: str) -> None:
        row = self._conversation_state(project_id)
        proj = self.hub.projects[project_id]
        await self.hub.emit_no_seq({
            "type": "project_state_changed",
            "project_id": project_id,
            "project_name": proj.name,
            **row,
        })
        # 全仓给的前端 envelope.ts 不在本次包内；补一条既有事件保证旧构建也会刷新。
        await self.hub.emit_no_seq(self._project_created_payload(project_id))

    async def _set_conversation_state(
        self, project_id: str, field: str, enabled: bool,
    ) -> dict[str, Any]:
        async with self._conversation_state_lock:
            row = self._conversation_state(project_id)
            previous = dict(row)
            was_silent = bool(row["muted"] or row["folded"])
            if field == "pinned":
                row["pinned"] = bool(enabled)
                row["pinned_at"] = self._next_pinned_at() if enabled else 0
                if enabled:
                    row["folded"] = False
            elif field == "muted":
                row["muted"] = bool(enabled)
            elif field == "folded":
                row["folded"] = bool(enabled)
                if enabled:
                    row["pinned"] = False
                    row["pinned_at"] = 0
            else:
                raise ValueError(msg("server.py.037", field=field))
            row = self._normalize_conversation_state(row)
            self.conversation_states[project_id] = row
            if not self._save_conversation_states():
                self.conversation_states[project_id] = previous
                raise OSError(msg("server.py.038"))
            # 静默期间产生的历史不能在“关闭免打扰 / 移出折叠”后突然变成一串旧未读。
            # Hub 的 unread 是 seq - last_read_seq，因此进入、处于、离开静默态时都把
            # 当前水位视为已读；未来真正的新消息才会重新计数。
            if was_silent or row["muted"] or row["folded"]:
                proj = self.hub.projects.get(project_id)
                if proj is not None:
                    proj.last_read_seq = proj.seq
            self._apply_state_to_engine(project_id)
        await self._broadcast_conversation_state(project_id)
        return dict(row)

    def _conversation_state_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for project_id, proj in self.hub.projects.items():
            if project_id == PLATFORM_PROJECT_ID or _parse_dm(project_id) is not None:
                continue
            rows.append({
                "project_id": project_id,
                "project_name": proj.name,
                **self._conversation_state(project_id),
            })
        rows.sort(key=lambda r: str(r["project_id"]))
        return rows

    # ── [v0.44.8] 项目重命名：一个 Harness 事务，所有子系统只认 project_id ──
    @staticmethod
    def _clean_project_name(raw: Any) -> str:
        if not isinstance(raw, str):
            raise ValueError(msg("server.py.039"))
        # 单行展示名：压掉换行/控制空白；保留普通中文、标点与中间空格。
        name = " ".join(raw.strip().split())
        if not name:
            raise ValueError(msg("server.py.040"))
        if len(name) > 80:
            raise ValueError(msg("server.py.041"))
        return name

    @staticmethod
    def _replace_project_metadata(value: Any, old_name: str, new_name: str) -> Any:
        """只改项目元数据字段；聊天正文是审计历史，不因为备注名变化而改写。"""
        if isinstance(value, list):
            return [KnoweServer._replace_project_metadata(v, old_name, new_name) for v in value]
        if not isinstance(value, dict):
            return value
        out: dict[str, Any] = {}
        for key, child in value.items():
            lower = str(key).lower()
            if isinstance(child, str) and (
                "project_name" in lower or "projectname" in lower
                or lower in {"project", "parent_project", "parentproject"}
            ):
                out[key] = child.replace(old_name, new_name)
            elif isinstance(child, (dict, list)):
                # 任意 envelope/payload 深度都继续走，但只有“项目名语义”的键会改字符串；
                # content/text 等正文键即使嵌套也保持原样。
                out[key] = KnoweServer._replace_project_metadata(child, old_name, new_name)
            else:
                out[key] = child
        return out

    def _rewrite_persisted_project_events(
        self, project_id: str, old_name: str, new_name: str,
    ) -> int:
        if self.store is None:
            return 0
        events = self.store.load_all_events(project_id)
        changed = 0
        rewritten: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            updated = self._replace_project_metadata(event, old_name, new_name)
            if updated != event:
                changed += 1
            rewritten.append(updated)
        if changed:
            self.store.compact(project_id, rewritten)
        return changed

    def _rewrite_ring_project_metadata(
        self, project_id: str, old_name: str, new_name: str,
    ) -> None:
        proj = self.hub.projects.get(project_id)
        if proj is None:
            return
        try:
            events, _gap = proj.ring.replay_since(0)
        except Exception:  # noqa: BLE001 — Ring 实现差异只影响旧帧显示，不影响新状态
            return
        for event in events:
            if not isinstance(event, dict):
                continue
            updated = self._replace_project_metadata(event, old_name, new_name)
            if updated is event:
                continue
            event.clear()
            event.update(updated)

    def _rewrite_global_name_files(self, old_name: str, new_name: str) -> None:
        roots: list[Path] = []
        if self._harness_dir is not None:
            roots.append(self._harness_dir)
        roots.append(self._internal_workspace_for(PLATFORM_PROJECT_ID))
        for root in roots:
            try:
                ProjectEngine.rewrite_project_name_tree(root, old_name, new_name)
            except Exception:  # noqa: BLE001
                log.exception(msg("server.py.044"), root)

        # 很老的版本把 Harness Memory 直接放 data 根；只改这些明确文件，不扫描用户工作区。
        for name in ("harness_memory.md", "harness_memory.json", "harness_memory.jsonl"):
            path = self.data_root / name
            try:
                if path.is_file() and path.stat().st_size <= 16 * 1024 * 1024:
                    text = path.read_text("utf-8")
                    if old_name in text:
                        tmp = path.with_name(path.name + ".rename.tmp")
                        tmp.write_text(text.replace(old_name, new_name), "utf-8")
                        tmp.replace(path)
            except (OSError, UnicodeDecodeError):
                log.exception(msg("server.py.045"), path)

    async def _rename_project(
        self, project_id: str, raw_name: Any, *, rename_workspace: bool = True,
    ) -> dict[str, Any]:
        new_name = self._clean_project_name(raw_name)
        if (project_id == PLATFORM_PROJECT_ID or _parse_dm(project_id) is not None
                or project_id not in self.hub.projects):
            raise ValueError(msg("server.py.046"))

        async with self._project_metadata_lock:
            proj = self.hub.projects[project_id]
            old_name = proj.name
            if new_name == old_name:
                return {
                    "project_id": project_id,
                    "project_name": new_name,
                    "old_project_name": old_name,
                    "project_dir": self.project_dirs.get(project_id, ""),
                    "changed": False,
                }

            old_dir = self.project_dirs.get(project_id)
            old_status_raw = self.project_dir_status.get(project_id)
            old_status = dict(old_status_raw) if isinstance(old_status_raw, dict) else None
            try:
                # [任务 1.7] 已删除 managed-workspaces 兜底：改名不再搬目录，
                #   只改项目名（用户自选目录是用户资产，绝不动）。
                proj.name = new_name
                if self.store is not None:
                    # [v1.0.24.4] 落盘走持久化队列；await 保留原语义——
                    # 写失败照样抛进 except，触发下面的回滚。
                    _store, _pid = self.store, project_id
                    await asyncio.wrap_future(
                        _store.defer(lambda: _store.upsert_project(_pid, new_name)),
                    )
                    self._persisted_project_ids.add(project_id)

                if self._save_project_dirs() is False:
                    raise OSError(msg("server.py.047"))
                if self._save_project_dir_status() is False:
                    raise OSError(msg("server.py.048"))
                self._write_projects_index()
            except Exception:
                proj.name = old_name
                if self.store is not None:
                    try:
                        # [v1.0.24.4] 回滚也走队列：必须排在刚才那个正向写之后，
                        # 否则同步直写会抢在队列前面，盘上最终留下的是新名字。
                        _store, _pid = self.store, project_id
                        await asyncio.wrap_future(
                            _store.defer(lambda: _store.upsert_project(_pid, old_name)),
                        )
                    except Exception:  # noqa: BLE001
                        log.exception(msg("server.py.049"), project_id)
                if old_dir is not None:
                    self.project_dirs[project_id] = old_dir
                else:
                    self.project_dirs.pop(project_id, None)
                if old_status is not None:
                    self.project_dir_status[project_id] = old_status
                else:
                    self.project_dir_status.pop(project_id, None)
                self._save_project_dirs()
                self._save_project_dir_status()
                raise

            # 核心 name/目录已经一致落盘；以下是可重试的引用迁移，一处坏文件不能把项目卡死。
            try:
                # [v1.0.24.4] 这是「读-改-写」：整体作为一个 job 进持久化队列，
                # 排在已提交的事件 append 之后执行，不会读到半新不旧的文件；
                # await 保留原语义——坏文件照样记日志、不卡项目。
                _store, _pid = self.store, project_id
                if _store is not None:
                    await asyncio.wrap_future(_store.defer(
                        lambda: self._rewrite_persisted_project_events(_pid, old_name, new_name),
                    ))
                else:
                    self._rewrite_persisted_project_events(project_id, old_name, new_name)
            except Exception:  # noqa: BLE001
                log.exception(msg("server.py.052"), project_id)
            self._rewrite_ring_project_metadata(project_id, old_name, new_name)

            eng = self.engines.get(project_id)
            if eng is not None:
                try:
                    eng.rename_project_references(old_name, new_name)
                except Exception:  # noqa: BLE001
                    log.exception(msg("server.py.053"), project_id)
            else:
                try:
                    ProjectEngine.rewrite_project_name_tree(
                        self._internal_workspace_for(project_id), old_name, new_name,
                    )
                except Exception:  # noqa: BLE001
                    log.exception(msg("server.py.054"), project_id)

            paused = self._paused_histories.get(project_id)
            if isinstance(paused, list):
                self._paused_histories[project_id] = [
                    self._replace_project_metadata(item, old_name, new_name)
                    if isinstance(item, dict) else item
                    for item in paused
                ]
            self._rewrite_global_name_files(old_name, new_name)

        # Harness Memory 是所有项目摘要的可再生全局视图：改名后立即用稳定 project_id
        # 重新汇总一版，既刷新知知所见，也避免只靠文本替换留下同名项目歧义。
        if hasattr(self, "_harness_update_lock") and hasattr(self, "memory"):
            await self._update_harness_now()

        renamed_event: dict[str, Any] = {
            "type": "project_renamed",
            "project_id": project_id,
            "project_name": new_name,
            "old_project_name": old_name,
        }
        effective_dir = self.project_dirs.get(project_id)
        if effective_dir:
            renamed_event["project_dir"] = effective_dir
        await self.hub.emit_no_seq(renamed_event)
        await self.hub.emit_no_seq(self._project_created_payload(project_id))
        log.info(msg("server.py.055"), project_id, old_name, new_name)
        return {
            "project_id": project_id,
            "project_name": new_name,
            "old_project_name": old_name,
            "project_dir": effective_dir or "",
            "changed": True,
        }

    # ── [v0.13 3b] 目录有效性账本 ──
    @property
    def _dir_status_path(self) -> Path | None:
        if self._harness_dir is None:
            return None
        return self._harness_dir / "project_dir_status.json"

    def _load_project_dir_status(self) -> None:
        path = self._dir_status_path
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning(msg("server.py.056"))
            return
        if isinstance(data, dict):
            self.project_dir_status = {
                str(pid): dict(row)
                for pid, row in data.items()
                if isinstance(pid, str) and isinstance(row, dict)
            }

    def _save_project_dir_status(self) -> bool:
        path = self._dir_status_path
        if path is None:
            return True
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8", newline="\n") as fh:
                json.dump(self.project_dir_status, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            return True
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            log.warning(msg("server.py.057"), exc)
            return False

    def _mark_project_dir_invalid(
        self, project_id: str, previous_dir: str, reason: str,
    ) -> dict[str, Any]:
        old = self.project_dir_status.get(project_id, {})
        request_id = old.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            request_id = f"dir_{project_id}_{uuid.uuid4().hex[:10]}"
        row: dict[str, Any] = {
            "status": "invalid",
            "path": previous_dir,
            "reason": reason,
            "request_id": request_id,
            "detected_at": old.get("detected_at") or now_ts(),
            "updated_at": now_ts(),
        }
        self.project_dir_status[project_id] = row
        self._save_project_dir_status()
        self._write_projects_index()
        return row

    def _mark_project_dir_valid(self, project_id: str, project_dir: str) -> dict[str, Any]:
        # [v0.13 fix] 目录恢复 → 解除“暂缓”。下一轮若再失效，才允许重新弹窗。
        self._directory_popup_paused.discard(project_id)
        old = self.project_dir_status.get(project_id, {})
        row: dict[str, Any] = {
            "status": "valid",
            "path": project_dir,
            "updated_at": now_ts(),
        }
        if isinstance(old.get("request_id"), str):
            row["last_request_id"] = old["request_id"]
        self.project_dir_status[project_id] = row
        self._save_project_dir_status()
        self._write_projects_index()
        return row

    def _scan_project_directories(self) -> None:
        """启动时检查所有已记录目录；已进入隔离态的项目必须由用户显式换目录解封。"""
        self._load_project_dir_status()
        for project_id, raw in list(self.project_dirs.items()):
            previous = self.project_dir_status.get(project_id, {})
            if previous.get("status") == "invalid":
                # 用户取消/关闭弹窗后，隔离态必须跨重启保留。即使原路径后来又出现，
                # 也不能擅自恢复 LLM；只有 set_project_directory 才能明确解除。
                log.warning("[%s] 项目仍处于目录隔离态，等待用户重新选择目录", project_id)
                continue
            try:
                path = Path(raw).expanduser().resolve()
                exists = path.is_dir()
            except OSError:
                path = Path(raw).expanduser()
                exists = False
            if exists:
                self._mark_project_dir_valid(project_id, str(path))
            else:
                self._mark_project_dir_invalid(project_id, str(path), "missing")
                log.warning("[%s] 项目目录失效：%s（项目引擎将保持关闭）", project_id, path)

    def _project_dir_is_valid(self, project_id: str, *, recheck: bool = True) -> bool:
        if project_id == PLATFORM_PROJECT_ID:
            return True
        raw = self.project_dirs.get(project_id)
        if raw is None:
            return True                         # 纯内存/老项目仍走引擎默认工作区

        status = self.project_dir_status.get(project_id, {})
        if status.get("status") == "invalid":
            return False                        # 运行期一旦隔离，必须由显式换目录命令解封
        if not recheck:
            return True

        try:
            valid = Path(raw).expanduser().resolve().is_dir()
        except OSError:
            valid = False
        if not valid:
            self._mark_project_dir_invalid(project_id, raw, "missing")
        return valid

    def _directory_popup(self, project_id: str) -> dict[str, Any]:
        proj = self.hub.get_or_create(project_id)
        previous_dir = self.project_dirs.get(project_id, "")
        row = self.project_dir_status.get(project_id)
        if not isinstance(row, dict) or row.get("status") != "invalid":
            row = self._mark_project_dir_invalid(project_id, previous_dir, "missing")
        return {
            "type": "project_directory_required",
            "project_id": project_id,
            "project_name": proj.name,
            "previous_dir": str(row.get("path") or previous_dir),
            "reason": str(row.get("reason") or "missing"),
            "request_id": str(row["request_id"]),
            "message": _directory_required_text(),
            "can_cancel": True,
        }

    def _directory_required_info(self, project_id: str) -> dict[str, Any] | None:
        """
        [v0.13 卡片] 项目当前处于「目录失效隔离」态时，返回随 project_created 捎给前端的
        最小卡片信息（previous_dir / reason / request_id）；否则 None。

        **只读**——绝不在这里把一个有效项目标记成失效（那会把「告知状态」变成「制造状态」）。
        它只是让重连/冷启动的前端在第一帧就知道「这个项目有个待处理的目录事项」，
        据此点亮侧边栏红字并允许重开恢复卡片，无需再等用户发一条消息去触发。
        """
        if project_id == PLATFORM_PROJECT_ID:
            return None
        row = self.project_dir_status.get(project_id)
        if not isinstance(row, dict) or row.get("status") != "invalid":
            return None
        request_id = row.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return None
        return {
            "previous_dir": str(row.get("path") or self.project_dirs.get(project_id, "")),
            "reason": str(row.get("reason") or "missing"),
            "request_id": request_id,
        }

    async def _quarantine_project(self, project_id: str) -> None:
        """关闭项目所有 LLM/Worker 连接。历史留在内存，目录恢复后原样接回。"""
        eng = self.engines.pop(project_id, None)
        if eng is None:
            return
        self._paused_histories[project_id] = list(eng.history)
        await eng.stop(immediate=True)
        self.hub.clear_public_text_filter(project_id)
        log.warning("[%s] 项目目录失效，项目引擎已隔离", project_id)

    async def _respond_directory_required(
        self, project_id: str, *, cancelled: bool = False,
    ) -> None:
        # 目录失效期间引擎必须停着。_quarantine_project 幂等——取消/提醒重复调用都安全。
        await self._quarantine_project(project_id)

        if cancelled:
            # [v0.13 fix] 用户主动关掉/取消弹窗 = “我先不选，暂缓一下”。
            #   记下暂停标记、回一条确认消息，**绝不再调 _directory_popup**。
            #   老实现在这里仍然广播了一次弹窗——于是“取消”按钮等于“再弹一次”，
            #   取消→再弹→再取消……就是那条死循环的根。
            self._directory_popup_paused.add(project_id)
            await self.hub.emit(project_id, {
                "type": "message",
                "agent_id": COORDINATOR,
                "content": _directory_cancelled_text(),
            })
            return

        if project_id in self._directory_popup_paused:
            # [v0.13 fix] 用户已选择暂缓：之后的发言/审批只温和提醒一句，
            #   既不弹系统窗口，也不强制当场重选。真想恢复时，前端自有入口
            #   重新打开目录选择窗口（那条路径会带上原 request_id，仍然对得上）。
            await self.hub.emit(project_id, {
                "type": "message",
                "agent_id": COORDINATOR,
                "content": _directory_paused_text(),
            })
            return

        # 首次失效（或恢复之后又发生的新一轮失效）：正常提示 + 系统弹窗。
        await self.hub.emit(project_id, {
            "type": "message",
            "agent_id": COORDINATOR,
            "content": _directory_required_text(),
        })
        await self.hub.emit_no_seq(self._directory_popup(project_id))

    def _existing_project_dir(self, raw: str | None) -> str | None:
        """目录恢复只能选择已经存在的绝对目录；绝不替用户创建一个同名空壳。"""
        if not isinstance(raw, str) or not raw.strip():
            return None
        path = Path(raw.strip()).expanduser()
        if not path.is_absolute():
            return None
        try:
            resolved = path.resolve()
        except OSError:
            return None
        norm = str(resolved)
        if norm in _FORBIDDEN_ROOTS or resolved == resolved.parent or not resolved.is_dir():
            return None
        return norm

    # ═══════════════════════════════════════════════════════════
    # [v0.4] 启动：把磁盘上的项目和历史灌回内存
    # ═══════════════════════════════════════════════════════════
    def load_from_disk(self) -> None:
        if self.store is None:
            log.warning(msg("server.py.058"))
            return
        self._load_project_dirs()          # [v0.7 A0] 先把目录捡回来，引擎起来时就能绑对沙箱
        self._load_conversation_states()   # [v0.44.8] pin/mute/fold 与项目配置一起恢复
        self._load_project_card_ids()       # [v0.16] 旧前端建群 id → canonical id 映射
        self._load_announced()             # [v0.8e #3] 谁已经宣告过了
        # [v0.45.2] 删除事务必须在任何项目温载/目录扫描之前恢复。未提交的恢复原目录；
        # 已落墓碑的继续清理。随后再用墓碑做最终逻辑围栏，旧版留下的 projects.json
        # 残行也绝不能把已删除项目重新叫醒。
        self._recover_project_delete_transactions()
        deleted_project_ids = self._exclude_tombstoned_projects_from_disk()
        self._scan_project_directories()   # [v0.13 3b] 开机检测；失效项目不允许起引擎

        # [v0.8a-fix] ★ 开机先把「我在哪儿找东西」印出来。
        #
        #   这次排查里最费时间的一个问题是：「花名册文件明明在，为什么读回来是空的」——
        #   而 `Store(root)` 的 root 来自 CONFIG.data_dir，可能是相对路径。
        #   进程的工作目录一变（Electron 拉起来的后端和你手动 `python -m` 跑的后端，
        #   cwd 常常不是同一个），它找的就是另一个 data/ 了。
        #   把绝对路径和磁盘上真实存在的花名册文件都打出来，这个问题一眼就能看穿。
        log.info(msg("server.py.059"), self.store.root.resolve())
        try:
            found = sorted(p.name for p in self.store.events_dir.glob("*_roster.jsonl"))
        except OSError:
            found = []
        log.info(msg("s.060a"), ", ".join(found) if found else msg("s.060b"))

        # [v0.8c #2b] ★ 知知的会话也要温载。
        #
        #   __platform__ 不在 projects.json 里（它不是项目），所以下面那个循环够不着它——
        #   于是每次重启，知知窗口的历史就**没了**：用户昨天跟她聊的东西，今天一片空白。
        #   （她的事件其实一直在落盘：hub.emit → append_event 不挑项目。只是没人读回来。）
        #
        #   顺带，这也是「欢迎语不重复发」的前提：要判断「她窗口里最后一句是谁说的」，
        #   得先有那段历史。
        # [v0.12 D · 问题二/四] ★ 读**全部**历史（不再截到 1000），并把老文件里的
        #   逐字增量垃圾一次性清掉——结构事件（聊天记录）一条不丢。
        platform_events = sanitize_events(
            _history_only(self.store.load_all_events(PLATFORM_PROJECT_ID)),
            {"zinnia": msg("s.061a"), COORDINATOR: msg("s.061b")},
        )
        platform_watermark = self.store.load_seq_watermark(PLATFORM_PROJECT_ID)
        if platform_events or platform_watermark:
            self.hub.restore(
                PLATFORM_PROJECT_ID, msg("server.py.062"), platform_events,
                platform_watermark,
            )
            self.store.compact(PLATFORM_PROJECT_ID, platform_events)   # 全量重写 = 去垃圾 + 不丢史
            log.info(msg("server.py.063"), PLATFORM_PROJECT_ID,
                     len(platform_events), self.hub.projects[PLATFORM_PROJECT_ID].seq)

        project_rows = self.store.load_projects()
        if deleted_project_ids:
            stale_deleted_rows = [
                row for row in project_rows
                if isinstance(row, dict)
                and str(row.get("project_id") or "") in deleted_project_ids
            ]
            if stale_deleted_rows:
                log.warning(
                    msg("server.py.064"),
                    len(stale_deleted_rows),
                )
                project_rows = [
                    row for row in project_rows
                    if not (
                        isinstance(row, dict)
                        and str(row.get("project_id") or "") in deleted_project_ids
                    )
                ]
        # dm:* 是群内私聊事件频道，不是可启动、可列入左栏的项目。旧版本曾把运行期
        # DM 频道混进 Hub 项目集合；若某份旧索引也被污染，重启时必须在入口处截断，
        # 否则它会再次被温载并补发 project_created(raw dm id)。
        stale_dm_rows = [
            row for row in project_rows
            if isinstance(row, dict) and _parse_dm(row.get("project_id")) is not None
        ]
        if stale_dm_rows:
            log.warning(msg("server.py.065"), len(stale_dm_rows))
            project_rows = [
                row for row in project_rows
                if not (isinstance(row, dict) and _parse_dm(row.get("project_id")) is not None)
            ]
        self._persisted_project_ids = {
            str(row.get("project_id"))
            for row in project_rows
            if isinstance(row, dict) and row.get("project_id")
        }

        for row in project_rows:
            pid = row["project_id"]
            name = row.get("name") or pid
            # [v0.12 D · 问题二/四] ★ 读**全部**历史，只保留结构事件（= 全部聊天记录），
            #   顺手把老文件里的逐字增量垃圾清掉。**绝不按 1000 截断**（那正是问题二的病根）。
            full_roster = self.store.load_roster_full(pid)
            public_names = {COORDINATOR: msg("server.py.066")}
            public_names.update({
                aid: row.get("name") or legacy_display_name(aid, row.get("role", msg("server.py.067")))
                for aid, row in full_roster.items()
                if row.get("status", "active") != "deleted"
            })
            events = sanitize_events(
                _history_only(self.store.load_all_events(pid)), public_names,
            )

            # [v0.8e #3] ★ 把历史里那些**重复的** agents_created 就地清掉。
            #   用户的群里已经攒了 7 条「XX 已加入项目」——那是我们自己一次次补出来的。
            #   只留第一条（那才是真的发生过的事），后面的原样删掉。
            #   紧接着 compact 会把清理后的历史重写回磁盘：这些垃圾**永久消失**。
            events, dropped = _dedupe_agents_created(events)
            if dropped:
                log.info(msg("server.py.068"),
                         pid, dropped)

            self.hub.restore(pid, name, events, self.store.load_seq_watermark(pid))
            # [v0.12 D] 全量重写：既去掉逐字增量/重复公告的垃圾，又**一条聊天记录都不丢**。
            self.store.compact(pid, events)

            # 历史里露过面的人 → 记进账本（老项目第一次跑到这儿时的补票）
            seen = self._announced_members(pid)
            if seen:
                self.announced.setdefault(pid, set()).update(seen)

            # [v0.8a A-1] 这儿只报个数；人是跟着引擎一起回来的（见 wake_projects）。
            roster = self.store.load_roster(pid)
            workers = [a for a in roster if a != COORDINATOR]
            log.info(msg("server.py.069"),
                     pid, len(events), len(workers),
                     (msg("s.070a") if COORDINATOR in roster else msg("s.070b")),
                     name, self.hub.projects[pid].seq)

        # ★ 账本落盘。哪怕将来 ring 被淘汰干净了，「谁已经宣告过」这件事也不会丢——
        #   这正是 v0.8a-fix 那版栽跟头的地方：它把判据挂在了一个会变的东西上。
        self._save_announced()
        self._write_projects_index()          # [v0.12 D 6a/6d] 开机就把人读索引刷一版

    # ═══════════════════════════════════════════════════════════
    # [v0.4] 知知：平台引擎常驻
    # ═══════════════════════════════════════════════════════════
    def start_platform(self) -> ProjectEngine:
        """知知住在 __platform__ 这个特殊会话里。它不是项目——不进 projects.json。"""
        if self.platform is not None:
            return self.platform

        self.hub.get_or_create(PLATFORM_PROJECT_ID, msg("s.061a"))
        agent = ZinniaAgent(
            create_project=self._zinnia_create_project,
            # [M1 采集点 C] 知知 token 用量落盘到平台项目 ledger（与群聊同一套 persist 链路）。
            usage_sink=self._zinnia_usage_sink,
            # [v1.0.22.1-对齐 B] 平台级对话记忆：沉淀 + 每轮读回最近几条。
            memory_sink=self._zinnia_memory_sink,
            memory_brief=self.memory.read_platform_memory_brief,
            # [v0.12 D 5a] 全局公告栏（极简版）默认注进知知上下文——不再等用户催她调工具。
            harness_brief=self.memory.read_harness_brief,
            # [v0.12 D 5e] 平台上下文（版本/路径/变更）——她「天生」就知道软件本身的情况。
            platform_brief=self.platform_manifest.read_brief,
            # [v0.12 D 5b] 只读文件能力（读内容/列目录，绝不增删改）。
            read_file=lambda path, **page: _safe_read_any(
                path, self.data_root, **page
            ),
            list_dir=lambda path, **page: _list_dir_any(
                path, self.data_root, **page
            ),
        )
        eng = ProjectEngine(
            self.hub, PLATFORM_PROJECT_ID, agent=agent,
            internal_workspace_root=self._internal_workspace_for(PLATFORM_PROJECT_ID),
            backend_data_root=self.data_root,
            memory_manager=self.memory,
            knowledge_manager=self.knowledge,
        )
        self.engines[PLATFORM_PROJECT_ID] = eng
        self.platform = eng
        eng.start()
        log.info("平台引擎（知知）已就绪")
        return eng

    def _zinnia_usage_sink(self, usage: dict[str, Any]) -> None:
        """落盘知知的 token 用量到平台项目 ledger（M1 采集点 C）。

        Telemetry only: failures are swallowed and never reach the chat event stream.
        """
        if self.store is None:
            return
        try:
            buckets = normalize_usage_buckets(usage)
            if buckets is None:
                return
            binding = runtime_settings.model_binding_for(PLATFORM_PROJECT_ID, ZINNIA) or {}
            model = str(binding.get("model") or "").strip() or "unknown"
            provider = str(binding.get("provider") or "").strip()
            record: dict[str, Any] = {
                "ts": int(time.time()),
                "project_id": PLATFORM_PROJECT_ID,
                "agent_id": ZINNIA,
                "agent_role": "zinnia",
                "agent_name": msg("server.py.062"),
                "provider": provider,
                "model": model,
                "usage": buckets,
            }
            cny_cost = estimate_cost(
                model,
                cache_hit_input=buckets["cache_hit_input"],
                cache_miss_input=buckets["cache_miss_input"],
                output=buckets["output"],
                currency="CNY",
            )
            usd_cost = estimate_cost(
                model,
                cache_hit_input=buckets["cache_hit_input"],
                cache_miss_input=buckets["cache_miss_input"],
                output=buckets["output"],
                currency="USD",
            )
            if cny_cost is not None:
                record["price_cny"] = cny_cost
            if usd_cost is not None:
                record["price_usd"] = usd_cost
            # [v1.0.24.4] 遥测落盘走持久化队列，不占主循环。
            _store, _record = self.store, record
            _store.defer_bg(
                lambda: _store.append_token_usage(PLATFORM_PROJECT_ID, _record),
                description="平台 token 用量",
            )
        except Exception:
            log.debug(msg("server.py.071"), exc_info=True)

    def _zinnia_memory_sink(self, line: str) -> None:
        """[v1.0.22.1-对齐 B] 平台级对话记忆沉淀（知知回合收尾回调）。

        与 usage_sink 同款容错：失败只记日志，绝不进入聊天事件流。
        """
        try:
            self.memory.append_platform_memory(line)
        except Exception:
            log.exception("平台记忆写入失败（忽略）")

    # ═══════════════════════════════════════════════════════════
    # [v0.8c #2b] 知知的欢迎语
    # ═══════════════════════════════════════════════════════════
    def _welcome_state(self) -> str:
        """Return the observable welcome/readiness state without causing a model turn."""

        if self._last_speaker(PLATFORM_PROJECT_ID) is not None:
            return "sent"
        ready, source, _binding = runtime_settings.zinnia_binding_status()
        if not ready:
            return {
                "missing": "model_missing",
                "unverified": "model_unverified",
                "incompatible": "model_incompatible",
            }.get(source, "model_missing")
        if self._welcome_pending:
            if self.platform is not None and self.platform.busy:
                return "pending"
            return "retryable_error"
        if self._welcome_error:
            return "retryable_error"
        return "model_effective"

    async def ensure_zinnia_welcome_after_binding(self) -> str:
        """Queue the first welcome exactly once after an effective binding exists.

        Durable conversation history is the primary truth.  ``event_id`` on the eventual
        message is the second line of defence, so a crash after persistence but before an
        HTTP acknowledgement cannot create a duplicate bubble on restart.
        """

        eng = self.platform
        if eng is None:
            return "engine_missing"
        async with self._welcome_lock:
            if self._last_speaker(PLATFORM_PROJECT_ID) is not None:
                # The breadcrumb is diagnostic only and is written *after* durable visible
                # history exists.  Enqueueing is never treated as successful delivery.
                self._mark_zinnia_welcomed()
                self._welcome_pending = False
                self._welcome_error = ""
                return "sent"

            if not feature_enabled(FeatureFlag.MODEL_READINESS_GATE_V1):
                await eng.submit(_zinnia_welcome_text())
                self._welcome_pending = True
                return "pending"

            ready, source, _binding = runtime_settings.zinnia_binding_status()
            if not ready:
                self._welcome_pending = False
                self._welcome_error = ""
                return {
                    "missing": "model_missing",
                    "unverified": "model_unverified",
                    "incompatible": "model_incompatible",
                }.get(source, "model_missing")

            # A queued/active turn is already the one authoritative attempt.  Once it has
            # settled without a visible message, permit a later POST/reconnect to retry.
            if self._welcome_pending and eng.busy:
                return "pending"
            if self._welcome_pending and not eng.busy:
                self._welcome_pending = False
                self._welcome_error = "previous_welcome_turn_produced_no_visible_message"

            try:
                await eng._submit_internal(  # noqa: SLF001 — startup control-plane entry
                    _zinnia_welcome_text(),
                    priority="foreground",
                    idempotency_key="zinnia:first_welcome:v1",
                )
            except Exception as exc:  # noqa: BLE001 — settings API remains available for retry
                self._welcome_pending = False
                self._welcome_error = type(exc).__name__
                log.exception(msg("server.py.072"))
                return "retryable_error"

            self._welcome_pending = True
            self._welcome_error = ""
            log.info(msg("server.py.073"))
            return "pending"

    async def welcome_if_needed(self) -> None:
        """Backward-compatible startup entry; readiness-aware implementation is authoritative."""

        await self.ensure_zinnia_welcome_after_binding()

    @staticmethod
    def _speaker_from_events(events: list[dict[str, Any]]) -> str | None:
        """从事件序列中找最后一个真正开口的人。"""
        for ev in reversed(events):
            etype = ev.get("type")
            if etype == "user_echo" and (ev.get("content") or "").strip():
                return "user"
            if etype == "message" and (ev.get("content") or "").strip():
                return "agent"
        return None

    def _last_speaker(self, project_id: str) -> str | None:
        """
        这个会话里最后开口的是谁：'user' / 'agent' / None（没有可靠证据）。

        先看 Ring 的实时视图；Ring 不存在、读取失败或没有说话事件时，再查 JSONL 全量
        持久历史。这样重启、Ring 淘汰或恢复时序都不会把“历史暂不可见”误判成首次使用。
        """
        proj = self.hub.projects.get(project_id)
        if proj is not None:
            try:
                events, _gap = proj.ring.replay_since(0)
                speaker = self._speaker_from_events(events)
                if speaker is not None:
                    return speaker
            except Exception:
                log.exception(msg("server.py.074"), project_id)

        if self.store is not None:
            try:
                speaker = self._speaker_from_events(self.store.load_all_events(project_id))
                if speaker is not None:
                    return speaker
            except Exception:
                log.exception(msg("server.py.075"), project_id)
        return None

    async def _zinnia_create_project(
        self, name: str, project_dir: str | None = None,
    ) -> tuple[str, str]:
        """
        [v0.5] 知知不再直接调它——建群走审批卡，项目在 approve 的那一刻才落地。
        这个回调留着是为了向后兼容（以及测试里直接建项目）。

        [v0.7 A0] 多一个可选的 project_dir：知知自己**不会**填它（她是个模型，
        没资格替用户在磁盘上指一个地方）——目录是用户在卡上选的，
        由前端随 create_project 指令发上来。
        """
        pid = new_project_id()
        await self.create_project(pid, name, project_dir)
        return pid, self.hub.projects[pid].name

    # ═══════════════════════════════════════════════════════════
    # 引擎管理（崩了就重启，并把挂起的卡完整复提 —— B-4）
    # ═══════════════════════════════════════════════════════════
    def engine_for(self, project_id: str, project_name: str | None = None) -> ProjectEngine:
        # Project-aware command handlers resolve ids before reaching this layer;
        # internal/direct callers must likewise pass an already-authoritative id.
        self._assert_project_activatable(project_id)
        self.hub.get_or_create(project_id, project_name)
        if not self._project_dir_is_valid(project_id):
            raise WorkspaceUnavailable(
                msg("server.py.076", project_id=project_id)
            )
        eng = self.engines.get(project_id)
        if eng is None:
            # [v0.7 A0] 引擎一出生就把沙箱绑到用户选的目录上。
            #   账本里没有这个项目（老项目 / 兜底建群）→ 传 None → 引擎自己退回默认目录。
            # [v0.8a A-1] store 一并交给引擎：新成员一进队就自己落盘，
            #   不用 server 在旁边盯着（引擎才知道人是什么时候建出来的）。
            eng = ProjectEngine(
                self.hub, project_id,
                workspace_root=self.project_dirs.get(project_id),
                internal_workspace_root=self._internal_workspace_for(project_id),
                backend_data_root=self.data_root,
                store=self.store,
                memory_manager=self.memory,
                knowledge_manager=self.knowledge,
                activity_callback=self._on_project_activity,
                project_name=project_name,   # [v1.0.23.3] 群聊名（初入群问候语用）
            )
            # v0.16 migration runs before the engine starts emitting or reading handoff state.
            eng.ensure_internal_layout()
            # A delete may have started while layout validation touched the filesystem.
            self._assert_project_activatable(project_id)
            self.engines[project_id] = eng
            self._apply_state_to_engine(project_id)
            try:
                self._assert_project_activatable(project_id)
            except Exception:
                if self.engines.get(project_id) is eng:
                    self.engines.pop(project_id, None)
                raise
            eng.start()
            eng._task.add_done_callback(self._on_engine_died)  # type: ignore[union-attr]

            # [v0.8a A-1] 引擎一建出来，就把磁盘上那支队伍灌回去（工具箱一并重绑）。
            self._restore_roster(eng)
        return eng

    def _open_activity_all(self) -> list[dict[str, Any]]:
        """[v1.0.24.4] 全部引擎的权威活动账本合并（含各群私聊频道的条目）。

        引擎不在/取账本出岔子都降级为空——绝不连累握手/快照主流程。
        全量下发：前端校准函数按 channel 过滤，各会话自动只取自己的条目。
        """
        entries: list[dict[str, Any]] = []
        for pid, eng in list(self.engines.items()):
            if pid == PLATFORM_PROJECT_ID:
                continue
            try:
                entries.extend(eng.open_activity_snapshot())
            except Exception:
                log.exception("[%s] 取活动账本失败，降级跳过", pid)
        return entries

    def _restore_roster(self, eng: ProjectEngine) -> dict[str, str]:
        """
        [v0.8a A-1] 从磁盘把这个项目的花名册灌回引擎。返回磁盘上那份（含项目经理）。

        灌过一次就不再灌 —— engine_for 每来一个客户端都会被调一遍，
        没必要每次都去读一趟文件（restore_roster 本身是幂等的，但 IO 是白花的）。
        """
        pid = eng.project_id
        if self.store is None or pid in self._roster_restored:
            return {}

        path = self.store.roster_path(pid)
        try:
            # [v0.9c] 读**整本**（含 name）—— 名字要跟着人一起回来。
            full = self.store.load_roster_full(pid)
        except Exception:
            # ★ [v0.9d Issue 1] **读失败不落闸。**
            #   老代码在这儿已经 `self._roster_restored.add(pid)` 了，所以读文件一抛异常，
            #   这个项目就**永远不再温载**：内存花名册空着，而前端那边（走的是另一条读盘的路）
            #   照样显示九个人。「前端看得见、项目经理看不见」就是这么来的。
            #   现在：失败就是失败，闸不落，下一个客户端进来会再试一次。
            #   （另外还有一道保险：engine._sync_roster_from_store() 每轮跟磁盘对账。）
            log.exception(msg("server.py.077"), pid)
            return {}

        self._roster_restored.add(pid)          # 读成功了才落闸

        active = {aid: row for aid, row in full.items()
                  if row.get("status", "active") == "active"}   # 归档/已删除都不复活

        if not active:
            # 空不一定是 bug：这个项目本来就还没组过队。但**说清楚我去哪儿找的**——
            # 「文件不在」和「文件在但读出来是空的」是两回事，日志里得分得开。
            log.info(msg("server.py.078"),
                     pid, path, path.exists(), len(full) - len(active))
            return {}

        # ★ [v0.9d Issue 3] **老花名册补名（一次性升级）。**
        #
        #   v0.9c 之前的行只有 {agent_id, role}，没有 name。
        #   这些人现在得有个真名字（「林知远」），而且必须**当场落盘**——
        #   不落盘就等于每次开机重掷一次，那正是我们要根治的病。
        #   落一次盘，从此这一行有名字，读的路径再也不用编。
        self._backfill_names(pid, full)

        log.info(msg("server.py.079"), pid, len(active),
                 "、".join(f"{row.get('name') or aid}（{aid}）"
                           for aid, row in active.items()))
        eng.restore_roster(active)          # ★ 名字随之回到引擎内存
        return {aid: row["role"] for aid, row in active.items()}

    def _backfill_names(self, pid: str, full: dict[str, dict[str, str]]) -> None:
        """
        [v0.9d] 给花名册里**没有名字**的老行补一个随机名，并落盘。**只补一次**。

        归档的人也补：将来他被加回来时，得能拿回「他的」名字——
        哪怕那个名字是我们今天才替他补上的，只要从今天起再也不变，就算数。
        """
        if self.store is None:
            return
        missing = [
            aid for aid, row in full.items()
            if aid != COORDINATOR
            and row.get("status", "active") != "deleted"
            and not row.get("name")
        ]
        if not missing:
            return

        for aid in missing:
            row = full[aid]
            try:
                self.store.upsert_agent(          # ← 名字在 upsert 里掷 + 落盘（唯一的口子）
                    pid, aid, row.get("role") or msg("server.py.067"),
                    status=row.get("status", "active"),
                )
            except Exception:
                log.exception(msg("server.py.080"), pid, aid)

        try:
            named = self.store.load_roster_full(pid)
            log.info(msg("server.py.081"), pid, len(missing),
                     "、".join(f"{aid}→{named.get(aid, {}).get('name', '?')}"
                               for aid in missing))
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # [v0.8a-fix] ★ 开机就把每个项目叫醒
    #
    #   这就是「重启之后队伍不见了」的**真根因**：引擎是**懒建**的——
    #   `engine_for()` 只在 `_do_replay` 里被调一次，而 replay_request 只带
    #   **一个** project_id（前端一开机停在知知那儿，带的是 __platform__）。
    #   于是别的项目根本没有引擎，`_restore_roster` 从头到尾没被调过；
    #   用户点进群，后端这边空空如也，直到他说一句话才把引擎（和项目经理）建出来。
    #
    #   花名册的读写一直是对的。断的是「谁去读」这一步。
    # ═══════════════════════════════════════════════════════════
    async def wake_projects(self) -> None:
        """开机把所有落盘项目的引擎起起来，并把花名册灌回去。

        ★ [v0.12 D · 问题三] 开机**不再补发 agents_created**。
          「这个群里有谁」是**持久状态**，不是刚刚发生的一件事——它随
          `project_created.members` 一起送到前端（见 `_do_replay`），那不是一条
          带时间戳、会排到会话最底下的「XX 已加入项目」。
          真正的「刚加入」只在**活动中组队**时发（tools_knowe 里那条 agents_created）。
          这样「他刚来」和「他一直在、只是我重启了」就彻底分开了。
        """
        if self.store is None:
            return
        for pid in list(self.hub.projects):
            if pid == PLATFORM_PROJECT_ID or _parse_dm(pid) is not None:
                continue                       # 知知和 DM 频道都不是项目，也没有独立队伍
            if not self._project_dir_is_valid(pid, recheck=False):
                log.warning(msg("server.py.082"), pid)
                continue
            eng = self.engine_for(pid)         # ← 建引擎（顺手 _restore_roster）
            pending = _pending_approval_snapshots(self.store.load_all_events(pid))
            if pending:
                # A persisted approval card is only half of the control state: its
                # asyncio.Future lived in the process that just died.  Rebuild that
                # gate before health/WS listeners open, preserving the public card id
                # so an approve/reject frame sent against replay can resolve it.
                await eng.recover(pending)
                log.warning(msg("server.py.083"), pid, len(pending))
            roster = self.store.load_roster(pid)
            if not roster:
                continue
            # [v0.12 D · 问题三] 把在册成员记进「已宣告」账本——不是为了补发，
            #   而是为了万无一失：哪怕将来别处想补一条 join，也会被这本账挡住。
            #   花名册的送达只走 project_created.members（持久态），这里一个事件都不 emit。
            self.announced.setdefault(pid, set()).update(
                aid for aid in roster if aid != COORDINATOR)
        self._save_announced()

    async def _announce_members(self, project_id: str, roster: dict[str, str]) -> None:
        """
        [v0.12 D · 问题三] ★ **已停用（故意保留为空实现）。**

        历史包袱：v0.8a-fix 起，这里会在温载时补发 `agents_created`，想让前端知道
        群里有谁。可 `agents_created` 是一条**带 seq、带时间戳**的事件——补发它，就等于
        往会话最底下塞一条「XX 已加入项目」。哪怕加了「终生只补一次」的账本兜底，
        账本一旦丢/损坏/首次升级，这条 join 就又冒出来了，而且永远排在最新
        （用户的原话：「重启后 Paris、Kit、廖中 已加入项目，明明是很早就加入的」）。

        根治不是「补得更小心」，而是**换一条不带时间戳的通道**：花名册随
        `project_created.members` 送达（那是持久状态，前端直接 registerMember，不进时间线）。
        `_members_of()` 读的是完整磁盘花名册——inject_instruction 兜底建出来的人、
        事件被淘汰的老项目，全都在里面，一个不漏。所以这里**什么都不用发**。

        方法体留空、签名保留：万一还有调用点，它也只是安静地什么都不做，
        绝不会再把「已加入项目」刷到用户脸上。
        """
        return

    def _members_of(self, project_id: str) -> list[dict[str, str]]:
        """
        [v0.8d #1] 这个项目的花名册 → [{"id", "role", "name"}]，**项目经理排第一个**。

        以磁盘那份为准（它含项目经理；engine.roster() 是「队员名单」，故意不含）。
        磁盘上没有 → 退回引擎内存里的那份。两边都没有 → 空列表，那就真的还没组队。

        [v0.9c] ★ 带上 **name**（「前端 1」）。这就是「重启之后名字不变」的那一环：
          名字从花名册来 → 随 project_created 一起到前端 → 前端直接用，不再自己掷骰子。
          老花名册没有 name → persist 按公式补（`fe_1` + `前端` → `前端 1`），不崩。
        """
        info: dict[str, dict[str, str]] = {}
        store = getattr(self, "store", None)
        if store is not None:
            try:
                info = {
                    aid: row
                    for aid, row in store.load_roster_full(project_id).items()
                    if row.get("status", "active") == "active"    # 归档/已删除都不发
                }
            except Exception:
                log.exception(msg("server.py.084"), project_id)

        eng = self.engines.get(project_id)
        if not info and eng is not None:
            info = {aid: {"role": role, "name": eng.member_name(aid)}
                    for aid, role in eng.roster().items()}

        if not info:
            return []

        def row(aid: str) -> dict[str, str]:
            r = info[aid].get("role") or msg("server.py.067")
            if aid == COORDINATOR:
                # [v1.0.22.1-对齐] 项目经理名/角色随语言实时：磁盘花名册可能存着
                # 旧语言快照（如英文启动时落盘的 Coordinator），切回中文必须覆盖。
                return {
                    "id": aid,
                    "role": msg("engine.007"),
                    "name": msg("engine.007"),
                }
            return {
                "id": aid,
                "role": r,
                # [v0.9d] 盘上没名字 → 用**稳定**的老公式兜底（绝不在读的路径上掷随机名）。
                #   正常情况下走不到这儿：_restore_roster 开机时已经把没名字的补掉了。
                "name": info[aid].get("name") or legacy_display_name(aid, r),
            }

        rows = [row(aid) for aid in info if aid != COORDINATOR]
        if COORDINATOR in info:
            # 项目经理排最前：左栏的头像宫格左上角那一格是他的位置
            rows.insert(0, row(COORDINATOR))
        return rows

    def _announced_members(self, project_id: str) -> set[str]:
        """事件流里已经露过面的 agent（前端认得他们）。"""
        proj = self.hub.projects.get(project_id)
        if proj is None:
            return set()
        try:
            events, _gap = proj.ring.replay_since(0)
        except Exception:
            log.exception("[%s] 读 ring 失败", project_id)
            return set()

        seen: set[str] = set()
        for ev in events:
            etype = ev.get("type")
            if etype == "agents_created":
                for m in ev.get("members") or []:
                    if isinstance(m, dict) and isinstance(m.get("id"), str):
                        seen.add(m["id"])
            # 说过话的人，前端在 applyEvent 里也会把他登记进花名册
            elif etype in ("message", "stream_delta", "instruction_injected",
                           "report_submitted", "agent_thinking", "agent_idle"):
                for key in ("agent_id", "target_id"):
                    val = ev.get(key)
                    if isinstance(val, str):
                        seen.add(val)
        return seen

    def _on_engine_died(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        pid = (task.get_name() or "engine:?").split(":", 1)[-1]
        if pid in self._closing_projects or pid in self._deleted_project_ids():
            log.info("[%s] 引擎在删除期间退出，不安排重启", pid)
            return
        log.error("[%s] 引擎异常退出，正在重启", pid, exc_info=exc)
        previous = self._restart_tasks.get(pid)
        if previous is not None and not previous.done():
            return
        restart = asyncio.create_task(self._restart_engine(pid), name=f"restart:{pid}")
        self._restart_tasks[pid] = restart
        restart.add_done_callback(
            lambda done, project_id=pid: self._restart_tasks.pop(project_id, None)
            if self._restart_tasks.get(project_id) is done else None
        )

    async def _restart_engine(self, project_id: str) -> None:
        self._assert_project_activatable(project_id)
        old = self.engines.pop(project_id, None)
        pending = old.gate.snapshot_pending() if old else []
        history = old.history if old else []

        if not self._project_dir_is_valid(project_id):
            self._paused_histories[project_id] = list(history)
            if old is not None:
                await old.stop(immediate=True)
            self.hub.clear_public_text_filter(project_id)
            log.warning("[%s] 引擎异常后发现目录失效，不自动重启", project_id)
            return

        # A crashed main loop may still own Completion SQLite, browser sessions and child
        # processes.  Close the old Engine before constructing a replacement; never let two
        # generations share one project directory.
        if old is not None:
            try:
                await old.stop(immediate=True)
            except Exception:
                self.engines[project_id] = old
                log.exception("[%s] 旧引擎资源未能安全关闭，取消自动重启", project_id)
                return

        # [v0.8a A-1] 引擎崩了重启，队伍也不许散：
        #   先拿磁盘上的那份（它是真相），再把崩掉那台内存里的花名册盖上去——
        #   万一有人刚进队、还没来得及落盘，这一步能把他捞回来。
        # [v0.9d] ★ 读**整本**（含 name）—— 老代码这儿读的是 load_roster()（只有 id→role），
        #   于是引擎重启之后名字全丢，restore_roster 只能现编一个。
        #   在 v0.9c 那套确定性公式下这看不出问题（编出来的和原来一样）；
        #   换成随机名之后，它就是一台"每次引擎崩溃就给全队改名"的机器。
        roster: dict[str, Any] = {}
        if self.store is not None:
            try:
                roster.update({
                    aid: row
                    for aid, row in self.store.load_roster_full(project_id).items()
                    if row.get("status", "active") == "active"
                })
            except Exception:
                log.exception("[%s] 重启时花名册读不了", project_id)
        if old is not None:
            for aid, role in old.roster().items():
                if aid not in roster:
                    roster[aid] = {"role": role, "name": old.member_name(aid)}

        eng = ProjectEngine(
            self.hub, project_id,
            workspace_root=self.project_dirs.get(project_id),   # [v0.7 A0] 重启不许把沙箱丢了
            internal_workspace_root=self._internal_workspace_for(project_id),
            backend_data_root=self.data_root,
            store=self.store,                                   # [v0.8a A-1]
            memory_manager=self.memory,
            knowledge_manager=self.knowledge,
            activity_callback=self._on_project_activity,
        )
        # 崩溃恢复必须和首次启动走同一条布局/迁移路径；不能因重启退回旧目录语义。
        eng.ensure_internal_layout()
        eng.history = history
        self._assert_project_activatable(project_id)
        self.engines[project_id] = eng
        self._apply_state_to_engine(project_id)
        try:
            self._assert_project_activatable(project_id)
        except Exception:
            if self.engines.get(project_id) is eng:
                self.engines.pop(project_id, None)
            raise
        eng.start()
        eng._task.add_done_callback(self._on_engine_died)  # type: ignore[union-attr]
        eng.restore_roster(roster)                              # [v0.8a A-1] 人先回来，再复提卡
        await eng.recover(pending)

    # ═══════════════════════════════════════════════════════════
    # 连接
    # ═══════════════════════════════════════════════════════════
    async def handle(self, ws: ServerConnection) -> None:
        client = Client(ws)
        self.hub.add_client(client)     # ★ 立刻进广播集合（§b）
        log.info("client %s connected (total=%d)", client.id, self.hub.client_count)

        try:
            await self._handshake(client)
            async for raw in ws:
                await self._on_frame(client, raw)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            log.exception("client %s crashed", client.id)
        finally:
            self.hub.remove_client(client)
            log.info("client %s gone (total=%d)", client.id, self.hub.client_count)

    async def _authenticate_ws(self, connection: Any, request: Any) -> Any:
        """Reject an unauthenticated upgrade before it can enter Hub or receive replay."""
        supplied = request.headers.get("X-Knowe-Runtime-Token", "")
        # WebSocket 升级请求无法通过 Electron webRequest 可靠注入 header（ws:// 与
        # http:// 的 URL filter 在部分 Electron 版本不触发），前端因此改用 URL query
        # 参数 ?token=… 传递令牌。header 缺失时回退检查 query，与 _health_conn 的
        # HTTP 认证保持同一套回退口径。
        if not supplied:
            try:
                supplied = parse_qs(urlsplit(request.path).query).get("token", [""])[0]
            except Exception:  # noqa: BLE001 — 认证探测失败一律按未授权处理
                supplied = ""
        if CONFIG.runtime_token and hmac.compare_digest(supplied, CONFIG.runtime_token):
            return None
        return connection.respond(HTTPStatus.UNAUTHORIZED, "unauthorized\n")

    async def _handshake(self, client: Client) -> None:
        """等首帧 replay_request，5 秒窗口。"""
        try:
            raw = await asyncio.wait_for(client.ws.recv(), timeout=CONFIG.handshake_timeout_s)
        except asyncio.TimeoutError:
            # 超时分支：无 project_id 的 replay_complete（契约允许）
            await self.hub.send_to(client, {"type": "replay_complete", "last_seq": 0})
            client.handshake_done = True
            log.info("client %s handshake timeout → replay_complete{last_seq:0}", client.id)
            return
        except websockets.ConnectionClosed:
            return

        frame = _parse(raw)
        if frame is None or frame.get("type") != "replay_request":
            # 首帧不是 replay_request：也认，但先把握手关掉，再按普通帧处理
            await self.hub.send_to(client, {"type": "replay_complete", "last_seq": 0})
            client.handshake_done = True
            if frame is not None:
                await self._on_frame(client, raw)
            else:
                await self._server_error(client, msg("server.py.254"))
            return

        await self._do_replay(client, frame)
        client.handshake_done = True

    async def _do_replay(self, client: Client, data: dict[str, Any]) -> None:
        requested = data.get("project_id") or PLATFORM_PROJECT_ID
        if not isinstance(requested, str):
            await self._server_error(client, msg("server.py.255"))
            await self.hub.send_to(client, {"type": "replay_complete", "last_seq": 0})
            return
        dm = _parse_dm(requested)
        dm_group_id: str | None = None
        try:
            if dm is None:
                project_id = self._canonical_project_id_from_request(requested)
            else:
                # 首帧也可能正停在群内私聊。频道 id 保持前端请求值；只把其中的群 id
                # 解析成 canonical 项目，用它恢复引擎、花名册和脱敏过滤器。
                dm_group_id = self._canonical_project_id_from_request(dm[0])
                project_id = requested
        except ProjectIdResolutionError as exc:
            await self._server_error(client, str(exc))
            await self.hub.send_to(client, {
                "type": "replay_complete",
                "project_id": requested,
                "last_seq": 0,
            })
            return
        try:
            since_seq = max(0, int(data.get("since_seq") or 0))
        except (TypeError, ValueError):
            await self._server_error(client, msg("server.py.256"))
            return

        if dm_group_id is None:
            proj = self.hub.get_or_create(project_id)
            # 目录失效的项目只允许回放历史，不允许为了握手偷偷把 LLM 引擎叫起来。
            if self._project_dir_is_valid(project_id, recheck=False):
                with contextlib.suppress(ProjectClosingError):
                    self.engine_for(project_id)
        else:
            if self._project_dir_is_valid(dm_group_id, recheck=False):
                with contextlib.suppress(ProjectClosingError):
                    self.engine_for(dm_group_id)
            self._ensure_dm_channel(project_id, dm_group_id)
            proj = self.hub.projects[project_id]

        # ── 补发这个客户端没收过的 project_created（每客户端独立记账） ──
        for pid, p in self.hub.projects.items():
            # [v0.4] __platform__ 不是项目——它是左栏顶上那个固定的「知知」入口，
            #        不能混进项目列表里（否则会多出来一个叫「知知」的假项目）
            if pid == PLATFORM_PROJECT_ID or _parse_dm(pid) is not None:
                continue
            if pid not in client.sent_projects:
                # 一处组装：花名册、目录状态、静默 unread 语义不能在各入口漂移。
                payload = self._project_created_payload(pid)
                await self.hub.send_to(client, payload)
                client.sent_projects.add(pid)

        # ── 增量回放 ──
        events, gap, source = self.hub.replay(project_id, since_seq)

        if gap:
            # ★ B-5：要的那一段被淘汰了 → 绝不给残缺历史，直接让前端走快照重建
            await self.hub.send_to(client, {
                "type": "resync_required",
                "last_seq": proj.seq,
                "project_id": project_id,
                "message": msg("event.server.01"),
            })
            log.info("[%s] client %s since_seq=%d 已淘汰（无持久兜底）→ resync_required",
                     project_id, client.id, since_seq)
        else:
            for ev in events:
                await client.send(ev)

        complete: dict[str, Any] = {
            "type": "replay_complete",
            "project_id": project_id,
            "last_seq": proj.seq,
            "unread_count": (
                0 if (
                    self._conversation_state(project_id)["muted"]
                    or self._conversation_state(project_id)["folded"]
                ) else self.hub.unread_count(project_id)
            ),
        }
        # [v1.0.24.4] 附权威活动账本（全量）：前端重连回放后以它校准花名册忙碌状态——
        #   断线期间丢多少 busy/idle 瞬时帧都能自愈到引擎现场真实状态；
        #   全量下发治愈「重连只补当前群」——所有群的账本一次到齐，各会话按 channel 自取。
        complete["activity"] = self._open_activity_all()
        # [v1.0.24.4] 磁盘写失败可见性：写失败不再阻断主循环，这里在握手时
        # 把累计计数暴露到日志，运维不再靠翻异常堆栈才发现磁盘异常。
        if self.store is not None:
            failures = self.store.disk_write_failures()
            if failures > 0:
                log.warning("[%s] 磁盘写失败累计 %d 次（持久化队列）", project_id, failures)
        if not events and not gap:
            complete["note"] = "no history"
        await self.hub.send_to(client, complete)

        log.info("[%s] client %s replayed %d events since %d via %s (last_seq=%d)",
                 project_id, client.id, len(events), since_seq, source, proj.seq)

    # ═══════════════════════════════════════════════════════════
    # 入站帧路由
    # ═══════════════════════════════════════════════════════════
    async def _on_frame(self, client: Client, raw: str | bytes) -> None:
        frame = _parse(raw)
        if frame is None:
            await self._server_error(client, msg("server.py.257"))
            return

        mtype = frame.get("type")
        try:
            # v0.18: project-id resolution belongs to the command that knows whether the
            # referenced project must already exist or may be created.  The old entry-layer
            # rewrite blurred that distinction and introduced a private correlation field whose
            # survival depended on every intermediate layer.  Every project-aware handler below
            # already resolves explicitly; pass the wire message through unchanged here.
            handler = getattr(self, f"_cmd_{mtype}", None)
            if handler is None:
                await self._server_error(client, msg("server.py.329", mtype=mtype))
                return
            await handler(client, frame)
        except ProjectIdResolutionError as exc:
            await self._server_error(client, str(exc))
        except ContractViolation:
            raise                       # 契约违规必须炸——不许悄悄发畸形事件
        except Exception as exc:
            log.exception("处理 %s 失败", mtype)
            await self._server_error(client, msg("server.py.330", mtype=mtype, exc=exc))

    # ── ping ──
    async def _cmd_ping(self, client: Client, _msg: dict[str, Any]) -> None:
        await self.hub.send_to(client, {"type": "pong"})

    # ── project token usage ──
    async def _cmd_token_usage_req(self, client: Client, data: dict[str, Any]) -> None:
        requested = data.get("project_id")
        response: dict[str, Any] = {
            "type": "token_usage_res",
            "project_id": requested if isinstance(requested, str) else "",
        }
        request_id = data.get("request_id")
        if isinstance(request_id, int) and not isinstance(request_id, bool) and request_id > 0:
            response["request_id"] = request_id
        try:
            if not isinstance(requested, str) or not requested.strip():
                raise ValueError(msg("server.py.261"))
            # [M1+] 私聊窗口（dm:{group}:{agent}）也要 Token 统计：解析到所属群，
            # 与群共用同一份 ledger（私聊窗口看所属群的整体消耗，语义同 RecordsDrawer）。
            dm = _parse_dm(requested)
            if dm is not None:
                project_id = self._canonical_project_id_from_request(dm[0])
            else:
                project_id = self._canonical_project_id_from_request(requested)
                if _parse_dm(project_id) is not None:
                    raise ValueError(msg("server.py.262"))
            response["project_id"] = project_id

            # [M1] 时间范围过滤（可选）：前端传 epoch 秒，聚合层按 ts 过滤。
            start_ts: int | None = None
            end_ts: int | None = None
            try:
                if data.get("start_ts") is not None:
                    start_ts = int(data["start_ts"])
                if data.get("end_ts") is not None:
                    end_ts = int(data["end_ts"])
            except (TypeError, ValueError, OverflowError):
                start_ts = None
                end_ts = None

            records = (
                await asyncio.to_thread(self.store.load_token_usage, project_id)
                if self.store is not None else []
            )
            names: dict[str, str] = {COORDINATOR: msg("s.061b")}
            if self.store is not None:
                for agent_id, row in self.store.load_roster_full(project_id).items():
                    role = str(row.get("role") or msg("engine.141.fb"))
                    names[agent_id] = str(
                        row.get("name") or legacy_display_name(agent_id, role)
                    )
            eng = self.engines.get(project_id)
            if eng is not None:
                names.setdefault(COORDINATOR, msg("s.061b"))
                for agent_id, role in eng.roster().items():
                    try:
                        names[agent_id] = eng.member_name(agent_id)
                    except Exception:
                        names.setdefault(agent_id, legacy_display_name(agent_id, role))

            current_model = ""
            binding = runtime_settings.model_binding_for(project_id, COORDINATOR)
            if isinstance(binding, dict):
                current_model = str(binding.get("model") or "").strip()
            if not current_model and eng is not None:
                # Harness mode keeps the coordinator in ``_agents``; single-agent mode keeps it
                # on ``engine.agent``.  Read both so the panel reflects the actual live binding.
                coordinator = (
                    getattr(eng, "agent", None)
                    or getattr(eng, "_agents", {}).get(COORDINATOR)
                )
                provider_cfg = getattr(coordinator, "_provider_cfg", None)
                current_model = str(getattr(provider_cfg, "model", "") or "").strip()
            if not current_model and records:
                current_model = str(records[-1].get("model") or "").strip()
            if not current_model:
                current_model = str(CONFIG.deepseek_model or "").strip()

            response.update(aggregate_token_usage(
                records,
                names=names,
                current_model=current_model,
                start_ts=start_ts,
                end_ts=end_ts,
            ))
            log.debug(
                "[%s] Token 统计查询：records=%d calls=%s",
                project_id,
                len(records),
                (response.get("totals") or {}).get("total_calls"),
            )
        except Exception as exc:
            # Query telemetry is isolated just like capture telemetry.  Return a typed response
            # so the drawer can show retry UI without contaminating the chat event stream.
            log.debug("Token 统计查询失败", exc_info=True)
            response.update({
                "daily": [],
                "totals": {
                    "total_input": 0,
                    "total_output": 0,
                    "total_tokens": 0,
                    "total_calls": 0,
                    "estimated_cost_usd": None,
                },
                "by_agent": [],
                "by_model": [],
                "current_model": "",
                "pricing": {
                    "model": "",
                    "known": False,
                    "input_cost_per_1M": None,
                    "output_cost_per_1M": None,
                    "source": "unknown",
                },
                "error": str(exc) or msg("server.py.263"),
            })
        await self.hub.send_to(client, response)

    # ── [v1.0.19.4] 附件：报错翻译 + 护栏读取 ──
    async def _aux_translate_error(self, raw: str) -> str:
        """把机器报错译成一句友好中文（DESIGN §三#8）。best-effort、短超时、
        失败/超时/无 aux 一律兜底原文，绝不卡住消息流。"""
        text = (raw or "").strip()
        if not text:
            return msg("server.py.085")
        try:
            aux = runtime_settings.aux_effective()
            aux_ok = bool(aux and aux.get("api_key") and aux.get("base_url"))
            if not aux_ok and not CONFIG.deepseek_api_key:
                return text
            out = await asyncio.wait_for(
                aux_client.chat(
                    [
                        {"role": "system", "content": (
                            msg("server.py.086") +
                            msg("server.py.087")
                        )},
                        {"role": "user", "content": text},
                    ],
                    api_key=aux["api_key"] if aux_ok else CONFIG.deepseek_api_key,
                    base_url=aux["base_url"] if aux_ok else CONFIG.deepseek_base_url,
                    model=aux["model"] if aux_ok else CONFIG.deepseek_model,
                    timeout_s=8.0, what=msg("server.py.088"),
                ),
                timeout=9.0,
            )
            out = (out or "").strip()
            return out or text
        except Exception:
            return text

    async def _prepare_attachments(
        self, channel: str, error_agent: str, records: list[Any],
    ) -> tuple[bool, list[dict[str, Any]] | None]:
        """发送前把附件读成 provider 内容块。原子性：任一附件不合格 → 友好打回、
        返回 (False, None)，调用方**绝不**再触发 LLM（DESIGN 决策 #7 / 验收 #4）。"""
        if not records:
            return True, None
        try:
            # [v1.0.19.4] 用**持久**附件签名密钥验签（缺省回退 runtime_token）——
            #   这样重启后历史消息里的旧签名仍然有效，回看历史文件卡不会被误判成「校验未通过」。
            parts, _metas = build_parts(records, CONFIG.attachment_key or CONFIG.runtime_token)
            return True, parts
        except AttachmentError as exc:
            friendly = await self._aux_translate_error(str(exc))
            await self.hub.emit(channel, {
                "type": "error", "agent_id": error_agent, "message": friendly,
            })
            return False, None
        except Exception as exc:  # noqa: BLE001
            await self.hub.emit(channel, {
                "type": "error", "agent_id": error_agent,
                "message": await self._aux_translate_error(msg("server.py.089", exc=exc)),
            })
            return False, None

    # ── user_message ──
    async def _cmd_user_message(self, client: Client, data: dict[str, Any]) -> None:
        project_id = data.get("project_id")
        content = data.get("content")
        cmid = data.get("client_msg_id")
        if not isinstance(project_id, str) or not isinstance(content, str):
            await self._server_error(client, msg("server.py.090"))
            return

        # [v1.0.23.1] 转发结构化载荷（PRD R3）：content 是用户配言原文（含 @ 文本），
        # forwarded 携带来源/原文/附言，LLM 模板由后端构造。旧客户端不带该字段 → 走旧逻辑。
        fwd_in = data.get("forwarded")
        fwd = fwd_in if isinstance(fwd_in, dict) else None

        # [v1.0.19.4] 用户附件（本地文件的路径 + 身份 + HMAC 签名；字节此刻还没读）。
        attachments_in = data.get("attachments")
        att_records = [a for a in attachments_in if isinstance(a, dict)] if isinstance(attachments_in, list) else []

        # [v0.37] 群内私聊：dm:{group}:{agent} → 走私聊路由（回复只发私聊频道，不进群）。
        dm = _parse_dm(project_id)
        if dm is not None:
            await self._cmd_user_message_dm(client, project_id, dm[0], dm[1], content, cmid, att_records, fwd)
            return

        project_id = self._canonical_project_id_from_request(project_id)
        self._assert_project_activatable(project_id)

        # ★ user_echo：带 seq、进 ring、广播给所有人（含发送者自己）——回声哨兵的信号源
        #   [v1.0.19.4] 捎上附件**元数据**（路径/名称/大小/签名，绝不带字节）——
        #   气泡据此渲染文件卡片，重进会话/重放历史也能复原（DESIGN 决策 #3 / 验收 #8）。
        # ★ user_echo：带 seq、进 ring、广播给所有人（含发送者自己）——回声哨兵的信号源
        #   [v1.0.19.4] 捎上附件**元数据**（路径/名称/大小/签名，绝不带字节）——
        #   气泡据此渲染文件卡片，重进会话/重放历史也能复原（DESIGN 决策 #3 / 验收 #8）。
        #   [v1.0.23.1] 转发时回传 forwarded 结构——重放历史时前端据此恢复引用窗（修复 B4）。
        echo_event: dict[str, Any] = {
            "type": "user_echo",
            "content": content,
            "client_msg_id": cmid if isinstance(cmid, str) else None,
        }
        if fwd:
            echo_event["forwarded"] = fwd
        if att_records:
            echo_event["attachments"] = [echo_meta(a) for a in att_records]
        await self.hub.emit(project_id, echo_event)

        # [v0.13 3b] 目录每次发言都复核。失效时 Harness 接管回复，绝不触发 LLM。
        if not self._project_dir_is_valid(project_id):
            await self._respond_directory_required(project_id)
            return

        try:
            eng = self.engine_for(project_id)
        except WorkspaceUnavailable:
            await self._respond_directory_required(project_id)
            return

        # [v0.15] 知知每次开口前都先读一版刚汇总的全局状态。
        # 事件驱动刷新已经覆盖常态；这一道同步刷新封住“动作刚发生，用户立刻追问”的竞态。
        if project_id == PLATFORM_PROJECT_ID:
            await self._update_harness_now()

        # [v1.0.19.4] 发送前把附件读成 provider 内容块（护栏在此落地）。
        #   任一附件不合格 → 友好打回、**不触发 LLM**（原子性）。
        ok, att_parts = await self._prepare_attachments(project_id, COORDINATOR, att_records)
        if not ok:
            return

        # [v1.0.23.1] 转发 → LLM 模板后端构造：路由在用户配言（content）上解析
        # （修复 B5：原文里的 @ 绝不触发直达）；发给引擎的 content 换成本文模板。
        llm_content = content
        if fwd:
            llm_content = build_forward_template(
                username=runtime_settings.user_name(default="用户"),
                project_name=fwd.get("sourceProjectName"),
                source_name=fwd.get("sourceName") or "",
                original=fwd.get("originalText") or "",
                comment=content,
            )

        # [v0.44.5] 群聊 @提及：解析权威在后端花名册，协议仍是一条普通 user_message。
        #
        #   · 只 @主管 / 没有有效 Worker 提及 → 完全沿用普通群聊，交给项目经理；
        #   · @一个或多个 Worker → 无条件绕过项目经理，逐个走既有 submit_dm 直达回合，
        #     但 reply channel 传群 project_id，所以状态、工具、文件和回复都落在群时间线；
        #   · 即使同一条里还写了 @主管，只要命中了 Worker，仍遵守 README 的硬规则
        #     「包含 Worker 提及 → 这条消息不走项目经理」，避免项目经理再次转派造成重复执行。
        #
        # 未知/歧义 @ 不报错、不猜人：没有 Worker 命中时自然回落项目经理，和老版本行为一致。
        mentions = eng.resolve_group_mentions(content)
        if mentions.worker_ids:
            calls = [
                eng.submit_dm(
                    agent_id,
                    eng.rewrap_group_mention(llm_content, agent_id),
                    project_id,
                    group_mention=True,
                    attachments=att_parts,
                )
                for agent_id in mentions.worker_ids
            ]
            results = await asyncio.gather(*calls, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    log.exception(msg("server.py.091"), project_id,
                                  exc_info=(type(result), result, result.__traceback__))
            return
        await eng.submit(llm_content, attachments=att_parts)

    # ── [v0.37] 群内 Agent 私聊 ──
    def _dm_channel_name(self, group_id: str, agent_id: str) -> str:
        """DM 频道展示名取所属群花名册；旧 raw-id 频道也能在首次访问时自愈。"""
        if self.store is not None:
            try:
                member = self.store.load_roster_full(group_id).get(agent_id)
                if isinstance(member, dict):
                    name = member.get("name")
                    if isinstance(name, str) and name.strip():
                        return name.strip()
                    role = member.get("role") or (msg("s.061b") if agent_id == COORDINATOR else msg("engine.141.fb"))
                    return legacy_display_name(agent_id, str(role))
            except Exception:
                log.exception("[%s] 读取 %s 的私聊展示名失败", group_id, agent_id)

        eng = self.engines.get(group_id)
        if eng is not None:
            name = eng.member_name(agent_id)
            if isinstance(name, str) and name.strip():
                return name.strip()
        return msg("s.061b") if agent_id == COORDINATOR else agent_id

    def _ensure_dm_channel(self, dm_id: str, group_id: str) -> None:
        """建/取私聊频道，并让它复用群引擎的脱敏过滤器（roster 感知的用户可见文本守卫）。"""
        parsed = _parse_dm(dm_id)
        agent_id = parsed[1] if parsed is not None else ""
        channel_name = self._dm_channel_name(group_id, agent_id) if agent_id else dm_id
        proj = self.hub.projects.get(dm_id)
        if proj is None and self.store is not None:
            # DM 不进 projects.json，但它的结构事件和 seq 一直独立落盘。后端重启后首次
            # 打开该私聊时在这里温载，避免“历史能读到、频道 seq 却从 0 重来”的冲突。
            try:
                events = _history_only(self.store.load_all_events(dm_id))
                watermark = self.store.load_seq_watermark(dm_id)
                if events or watermark:
                    proj = self.hub.restore(dm_id, channel_name, events, watermark)
            except Exception:
                log.exception(msg("server.py.092"), dm_id)
        if proj is None:
            proj = self.hub.get_or_create(dm_id, channel_name)
        elif proj.name in {proj.id, agent_id}:
            # 兼容旧版 raw id / 群未就绪时的 agentId 临时名；花名册一到即可升级。
            proj.name = channel_name
        eng = self.engines.get(group_id)
        if eng is not None:
            # 没有引擎时（隔离期/未就绪）先不注册——Hub 会退回无状态硬过滤，绝不直通原文。
            self.hub.set_public_text_filter(dm_id, eng._sanitize_outbound)

    async def _cmd_user_message_dm(
        self, client: Client, dm_id: str, group_req: str, agent_id: str,
        content: str, cmid: Any, att_records: list[Any] | None = None,
        fwd: dict[str, Any] | None = None,
    ) -> None:
        """一条私聊消息：user_echo + 该成员的回复都只发 dm 频道；记忆按真项目路由。"""
        # 所属群 → 真项目 id（可能是别名/临时 id）。解析不出就报错，绝不新建项目。
        try:
            group_id = self._canonical_project_id_from_request(group_req)
        except ProjectIdResolutionError as exc:
            await self._server_error(client, msg("server.py.331", exc=exc))
            return

        self._ensure_dm_channel(dm_id, group_id)

        # user_echo 发到私聊频道 —— 出现在私聊窗口，群聊时间线看不到。
        # user_echo 发到私聊频道 —— 出现在私聊窗口，群聊时间线看不到。
        # [v1.0.23.1] 转发时回传 forwarded 结构（修复 B4：重放历史恢复引用窗）。
        dm_records = [a for a in att_records if isinstance(a, dict)] if isinstance(att_records, list) else []
        echo_event: dict[str, Any] = {
            "type": "user_echo",
            "content": content,
            "client_msg_id": cmid if isinstance(cmid, str) else None,
        }
        if fwd:
            echo_event["forwarded"] = fwd
        if dm_records:
            echo_event["attachments"] = [echo_meta(a) for a in dm_records]
        await self.hub.emit(dm_id, echo_event)

        # 目录失效 → 私聊里也如实提示，且**绝不触发 LLM**（和群聊同一条铁律）。
        if not self._project_dir_is_valid(group_id):
            await self.hub.emit(dm_id, {
                "type": "error", "agent_id": agent_id,
                "message": msg("event.cmd.user.message.dm.01"),
            })
            return

        try:
            eng = self.engine_for(group_id)
        except WorkspaceUnavailable:
            await self.hub.emit(dm_id, {
                "type": "error", "agent_id": agent_id,
                "message": msg("event.cmd.user.message.dm.02"),
            })
            return

        # 引擎此刻已就绪 → 让私聊频道用上它的脱敏过滤器（名字按群花名册处理）。
        self.hub.set_public_text_filter(dm_id, eng._sanitize_outbound)
        # [v1.0.19.4] 附件护栏读取（原子性：不合格 → 友好打回、不触发 LLM）。
        ok, dm_parts = await self._prepare_attachments(dm_id, agent_id, dm_records)
        if not ok:
            return
        # [v1.0.23.1] 私聊转发同样走后端模板（LLM 需要知道这条消息是转发的、来源哪）。
        dm_content = content
        if fwd:
            dm_content = build_forward_template(
                username=runtime_settings.user_name(default="用户"),
                project_name=fwd.get("sourceProjectName"),
                source_name=fwd.get("sourceName") or "",
                original=fwd.get("originalText") or "",
                comment=content,
            )
        await eng.submit_dm(agent_id, dm_content, dm_id, attachments=dm_parts)

    # ── [v0.13 3b] 项目目录恢复/取消（前端系统弹窗回传） ──
    async def _cmd_set_project_directory(self, client: Client, data: dict[str, Any]) -> None:
        project_id = data.get("project_id")
        raw_dir = data.get("project_dir")
        request_id = data.get("request_id")
        if not isinstance(project_id, str) or not isinstance(raw_dir, str):
            await self._server_error(
                client, msg("server.py.265"),
            )
            return
        project_id = self._canonical_project_id_from_request(project_id)
        if project_id == PLATFORM_PROJECT_ID or not self.hub.has(project_id):
            await self._server_error(client, msg("server.py.266"))
            return

        current = self.project_dir_status.get(project_id, {})
        expected = current.get("request_id")
        if isinstance(expected, str) and request_id != expected:
            await self._server_error(client, msg("server.py.267"))
            return

        resolved = self._existing_project_dir(raw_dir)
        if resolved is None:
            # [v0.13 fix] 用户已重新打开弹窗并尝试选目录 → 不再是“暂缓”状态；
            #   只是这次选的目录无效，就地把弹窗再递一次让他重选。
            self._directory_popup_paused.discard(project_id)
            self._mark_project_dir_invalid(
                project_id, self.project_dirs.get(project_id, raw_dir), "replacement_invalid",
            )
            await self._server_error(client, msg("server.py.268"))
            await self.hub.emit_no_seq(self._directory_popup(project_id))
            return

        # 先停旧引擎，再更新根目录；旧目录不删不改。
        await self._quarantine_project(project_id)
        old_request_id = expected if isinstance(expected, str) else f"dir_{project_id}"
        self.project_dirs[project_id] = resolved
        self._save_project_dirs()
        self._mark_project_dir_valid(project_id, resolved)
        self._roster_restored.discard(project_id)

        # [v0.44.8] 恢复卡里的改名也必须走 Harness 全局事务，不能只换 Hub 显示名。
        # 新目录是用户刚明确选定的，所以这里禁止重命名事务再次搬目录。
        raw_name = data.get("project_name")
        if isinstance(raw_name, str) and raw_name.strip():
            try:
                await self._rename_project(project_id, raw_name, rename_workspace=False)
            except ValueError as exc:
                # 目录恢复已经成功；附带的新名称无效时保留旧名称继续恢复，
                # 不能因为“改名”这个次要动作把项目重新卡回不可用状态。
                await self._server_error(client, str(exc))
            except Exception:  # noqa: BLE001
                log.exception("[%s] 恢复目录时全局改名失败", project_id)
                await self._server_error(client, msg("server.py.269"))

        eng = self.engine_for(project_id)
        paused = self._paused_histories.pop(project_id, None)
        if paused is not None:
            eng.history = paused
        try:
            self.memory.ensure_project_context(eng.internal_workspace)
        except Exception:
            log.exception("[%s] 恢复目录后初始化内部项目上下文失败", project_id)

        await self.hub.emit_no_seq({
            "type": "project_directory_restored",
            "project_id": project_id,
            "project_dir": resolved,
            "request_id": old_request_id,
            "message": _directory_restored_text(),
        })
        await self.hub.emit(project_id, {
            "type": "message",
            "agent_id": COORDINATOR,
            "content": _directory_restored_text(),
        })
        log.info("[%s] 项目目录恢复 → %s", project_id, resolved)

    async def _cmd_update_project_directory(self, client: Client, msg: dict[str, Any]) -> None:
        """兼容前端可能采用的同义命令名。"""
        await self._cmd_set_project_directory(client, msg)

    async def _cmd_cancel_project_directory(self, client: Client, data: dict[str, Any]) -> None:
        project_id = data.get("project_id")
        if not isinstance(project_id, str):
            await self._server_error(client, msg("server.py.093"))
            return
        project_id = self._canonical_project_id_from_request(project_id)
        if project_id == PLATFORM_PROJECT_ID or not self.hub.has(project_id):
            await self._server_error(client, msg("server.py.094"))
            return
        await self._respond_directory_required(project_id, cancelled=True)

    async def _cmd_project_directory_cancelled(
        self, client: Client, msg: dict[str, Any],
    ) -> None:
        """兼容事件式命名。"""
        await self._cmd_cancel_project_directory(client, msg)

    # ── approve / reject（控制面，直达 gate，不经队列 —— BUG-2） ──
    async def _cmd_approve(self, client: Client, msg: dict[str, Any]) -> None:
        await self._resolve(client, msg, "approved")

    async def _cmd_reject(self, client: Client, msg: dict[str, Any]) -> None:
        await self._resolve(client, msg, "rejected")

    # ── [v0.26] 「我有新意见」：**改卡面，不落定** ──
    async def _cmd_feedback_instruction(self, client: Client, data: dict[str, Any]) -> None:
        """
        用户在派活卡上提了修改意见 → 原地把卡上的 instruction 换成改好的。

        ★ 它和 approve / reject **并列**，都是控制面：直达 gate，不经消息队列，
          不作废这张卡，不惊动项目经理的回合。
          approve/reject 是**落定**这张卡；这一条是**改**这张卡——闸门的 future 碰都不碰，
          倒计时接着走，「恰好一次落定」那条硬保证一个字没动。

        v0.24 / v0.25 两版把它做成了「发一条聊天消息」，于是每次都要绕一大圈：
        作废旧卡 → 项目经理重开一个回合 → 重新提案。两次都在半路丢了用户的意见。
        这一版把它放回它本来该在的地方：**控制面**。
        """
        project_id = data.get("project_id")
        approval_id = data.get("approval_id")
        feedback = data.get("feedback")
        if not isinstance(project_id, str) or not isinstance(approval_id, str) \
                or not isinstance(feedback, str) or not feedback.strip():
            await self._server_error(
                client, msg("server.py.095"))
            return

        project_id = self._canonical_project_id_from_request(project_id)

        # 目录失效期间不许动任何卡（和 _resolve 同一条规矩）
        if not self._project_dir_is_valid(project_id):
            await self._respond_directory_required(project_id)
            return

        eng = self.engines.get(project_id)
        if eng is None:
            await self._server_error(client, msg("server.py.096"))
            return

        result = await eng.adjust_instruction(approval_id, feedback)
        if not result.get("ok") and not result.get("silent"):
            # ★ 走**引擎级** error（hub.emit → 带 project_id + seq），和 _resolve 一个路数：
            #   这件事能归因到项目，而用户就在这个项目的窗口里盯着卡转圈。
            #   （contract.py 的注释写着这条规矩：能归因的走引擎级，不能归因的才走 server 级。）
            #   ——出声是必须的：静默失败 = 卡永远转圈，那比报错难受得多。
            #   [v0.30 Bug2] silent=True 的失败**不出声**：那是「这条意见被更新的意见
            #   取代了」——接棒的那一次会给回执，这里再喊一嗓子只会让用户困惑。
            await self.hub.emit(project_id, {
                "type": "error",
                "message": msg("server.py.337", **{"reason": result.get("reason") or msg("server.py.097")}),
            })

    # ── [v0.29 问题二] 「停止」：掐掉**一个人**当前的任务 ──
    async def _cmd_stop_worker(self, client: Client, data: dict[str, Any]) -> None:
        """
        用户在成员面板上点了「停止」（并确认过一次）→ 让这个成员放下手里的活。

        ★ 它和 `_cmd_shutdown` 是**两件完全不同的事**，别混：
            shutdown    → `eng.stop()` → 整个引擎停摆，所有人下线，项目不动了
            stop_worker → 一个人的一件活没了，项目经理照常在，别人照常干
          用户点这个按钮时想的从来是后者 —— 他只是不想再等**这一个人**了。
          在这一版之前，他要达到这个目的只有一条路：把整个项目重启。

        ★ 它和 approve / reject / feedback_instruction 一样是**控制面**：
          直达引擎，不经 inbox，不惊动项目经理的回合。
          （反过来做——包成一条聊天消息让项目经理去「安排停止」——就是 v0.24/v0.25
            在「我有新意见」上栽的那个跟头：让模型在一个有几十个选项的回合里，
            去执行一件用户已经**明确按下**的指令。他按了按钮，那就是终局，不是提议。）

        后端不做二次确认：确认是**界面的事**（RosterPanel 已经拦了一道）。
        一条 stop_worker 帧到这儿，就是用户已经点过两次了。
        """
        project_id = data.get("project_id")
        agent_id = data.get("agent_id")
        if not isinstance(project_id, str) or not isinstance(agent_id, str) or not agent_id:
            await self._server_error(client, msg("server.py.098"))
            return

        project_id = self._canonical_project_id_from_request(project_id)

        # 目录失效期间不许动任何 Agent（和 _resolve / feedback_instruction 同一条规矩）。
        if not self._project_dir_is_valid(project_id):
            await self._respond_directory_required(project_id)
            return

        eng = self.engines.get(project_id)
        if eng is None:
            await self._server_error(client, msg("server.py.096"))
            return

        result = await eng.stop_worker(agent_id)
        if not result.get("ok"):
            # ★ 走**引擎级** error（带 project_id + seq）：这件事能归因到项目，
            #   而用户就在这个项目的窗口里盯着那个按钮。
            #   最常见的命中是良性的竞态：他点确认的那半秒里，那个人自己交差了。
            #   出声是必须的 —— 按钮点下去毫无反应，比一句「他已经做完了」难受得多。
            await self.hub.emit(project_id, {
                "type": "error",
                "message": msg("server.py.338", **{"reason": result.get("reason") or msg("server.py.097")}),
            })


    async def _resolve(self, client: Client, data: dict[str, Any], resolution: str) -> None:
        project_id = data.get("project_id")
        approval_id = data.get("approval_id")
        if not isinstance(project_id, str) or not isinstance(approval_id, str):
            await self._server_error(client, msg("server.py.327", resolution=resolution))
            return

        # A create-project approval belongs to Zinnia's platform gate even if an optimistic
        # frontend sends p_<approval_id> / the newly canonical project id in this field.
        # Route by the approval itself before touching a project engine.
        if self._is_create_project_approval(approval_id):
            project_id = PLATFORM_PROJECT_ID
        else:
            project_id = self._canonical_project_id_from_request(project_id)

        # 目录隔离期间，旧审批卡也不能重新唤醒任何 Agent。用户点任何审批动作，
        # 都按同一固定流程提醒并重新弹目录选择窗口。
        if not self._project_dir_is_valid(project_id):
            await self._respond_directory_required(project_id)
            return

        eng = self.engines.get(project_id)

        # [v0.5] 建群卡获批 → **由后端把项目建出来**，用的是用户最终确认的名字。
        #   前端在卡上可以改名，改完发一条 create_project（id = p_<card_id>，确定性的），
        #   再发 approve。所以这里先看项目在不在：
        #     · 在 → 前端已经用它改过的名字建好了，后端不重复建
        #     · 不在 → 前端没带名字（或别的客户端点的确认）→ 后端用卡上的原名兜底
        #   两条路径落到同一个 project_id，不会建出两个项目。
        if resolution == "approved" and eng is not None:
            await self._maybe_create_from_card(eng, approval_id)

        if eng is None or not eng.resolve(approval_id, resolution):
            # A repeated control frame for an already-settled card is idempotent, not
            # an error.  This matters across OS shutdown semantics: Windows terminate
            # can leave a genuinely pending card (restored above), while POSIX SIGTERM
            # may let the old process persist ``cancelled`` before it exits.  In the
            # latter case, return the durable terminal event point-to-point rather than
            # append a duplicate resolution or tell the client its replayed card never
            # existed.
            settled = self._historical_approval_resolution(project_id, approval_id)
            if settled is not None:
                await self.hub.send_to(client, settled)
                return

            # Unknown id: this is still actionable project-scoped feedback.
            await self.hub.emit(project_id, {
                "type": "error",
                "message": msg("server.py.332", approval_id=approval_id),
            })

    def _historical_approval_resolution(
        self, project_id: str, approval_id: str,
    ) -> dict[str, Any] | None:
        """Return the latest durable terminal event for one approval id, if any."""
        try:
            events = self.hub.durable_conversation(project_id)
        except Exception:
            log.exception(msg("server.py.102"), project_id, approval_id)
            return None
        for event in reversed(events):
            if event.get("type") != "approval_resolved":
                continue
            if str(event.get("card_id") or "") == approval_id:
                return dict(event)
        return None

    # ── [v0.16] 建群卡 canonical project id ──
    def _pending_create_card(self, approval_id: str) -> dict[str, Any] | None:
        platform = self.engines.get(PLATFORM_PROJECT_ID)
        if platform is None:
            return None
        return next(
            (card for tool, _aid, card in platform.gate.snapshot_pending()
             if tool == "create_project" and card.get("approval_id") == approval_id),
            None,
        )

    def _is_create_project_approval(self, approval_id: str) -> bool:
        return (
            self._pending_create_card(approval_id) is not None
            or approval_id in self.project_card_ids
            or self._approval_alias_key(approval_id) in self.project_card_ids
            or self._request_alias_key(project_id_for_card(approval_id))
            in self.project_card_ids
        )

    def _project_id_for_approval(self, approval_id: str) -> str:
        keys = (
            self._approval_alias_key(approval_id),
            approval_id,
            self._request_alias_key(project_id_for_card(approval_id)),
        )
        for key in keys:
            existing = self.project_card_ids.get(key)
            if isinstance(existing, str) and existing:
                # Complete all alias forms lazily when upgrading partial v0.16/v0.17 mappings.
                changed = False
                for alias_key in keys:
                    if self.project_card_ids.get(alias_key) != existing:
                        self.project_card_ids[alias_key] = existing
                        changed = True
                if changed:
                    self._save_project_card_ids()
                return existing

        candidate = self._allocate_canonical_project_id()
        # Keep the bare key for v0.16 rollback compatibility, and write explicit namespaced keys
        # for all hotfix readers.  All three identify the same logical creation request.
        for key in keys:
            self.project_card_ids[key] = candidate
        self._save_project_card_ids()
        return candidate

    def _bind_project_id_to_approval(
        self, approval_id: str, requested_project_id: str,
    ) -> str:
        """Bind a create-project approval to the id chosen by a v0.18 client.

        The binding is the bridge between the two ordered commands emitted by ApprovalCard:
        ``create_project`` carries the editable name/directory, while ``approve`` resolves the
        platform gate.  Without this explicit field, a canonical client id contains no card id and
        the approve fallback would allocate a second project.

        On retry, an existing binding always wins.  The caller can then echo the newly requested id
        as ``request_project_id`` so even a client that lost its local card cache is reconciled to
        the already-created project instead of leaving another optimistic group behind.
        """
        aid = approval_id.strip()
        if not aid or len(aid) > 256:
            raise ProjectIdResolutionError(msg("server.py.103"))

        keys = (
            self._approval_alias_key(aid),
            aid,
            self._request_alias_key(project_id_for_card(aid)),
        )
        existing: str | None = None
        for key in keys:
            value = self.project_card_ids.get(key)
            if isinstance(value, str) and value:
                if existing is not None and value != existing:
                    raise ProjectIdResolutionError(msg("server.py.104", aid=aid))
                existing = value

        if existing is not None:
            changed = False
            for key in keys:
                if self.project_card_ids.get(key) != existing:
                    self.project_card_ids[key] = existing
                    changed = True
            if changed:
                self._save_project_card_ids()
            return existing

        if self._pending_create_card(aid) is None:
            raise ProjectIdResolutionError(msg("server.py.105", aid=aid))

        self.project_card_ids[aid] = requested_project_id
        self.project_card_ids[self._approval_alias_key(aid)] = requested_project_id
        self.project_card_ids[
            self._request_alias_key(project_id_for_card(aid))
        ] = requested_project_id
        self._save_project_card_ids()
        log.info("create-project approval %s → %s", aid, requested_project_id)
        return requested_project_id

    def _canonical_project_id_from_request(
        self, requested: str, *, allocate: bool = False,
    ) -> str:
        """Resolve one wire-level project reference to an authoritative id.

        Resolution is deliberately strict outside a creation boundary:

        * an exact known id (including genuine legacy ``p_*`` projects) is accepted;
        * a persisted request/approval alias is accepted only after its target project exists;
        * an unknown id never falls through to ``Hub.get_or_create``;
        * ``allocate=True`` is reserved for ``create_project`` and internal creation paths.

        This is the invariant that prevents a stale ``p_ap_*`` click/message from materialising a
        second Hub conversation or internal workspace.
        """
        raw = requested.strip()
        if not raw or len(raw) > 256:
            raise ProjectIdResolutionError(msg("server.py.106"))

        if raw == PLATFORM_PROJECT_ID:
            if allocate:
                raise ProjectIdResolutionError(msg("server.py.107"))
            return raw

        known = self._known_project_ids()
        deleted = self._deleted_project_ids()
        mapped = self._mapped_project_id(raw)
        if mapped is not None:
            if mapped in deleted:
                raise ProjectIdResolutionError(
                    msg("server.py.108", raw=raw)
                )
            if allocate or mapped in known:
                return mapped
            raise ProjectIdResolutionError(
                msg("server.py.109", raw=raw)
            )

        # Preserve genuine v0.15-and-earlier ids (and any other persisted noncanonical ids).
        # Alias lookup intentionally happens first: a stale p_ap_* runtime artifact must route to
        # its mapped canonical project once such a mapping exists.
        if raw in deleted:
            raise ProjectIdResolutionError(
                msg("server.py.110", raw=raw)
            )
        if raw in known:
            return raw

        if _CANONICAL_PROJECT_ID_RE.fullmatch(raw):
            if allocate:
                return raw
            raise ProjectIdResolutionError(msg("server.py.111", raw=raw))

        if allocate:
            # Old clients may still send p_<approval_id>.  It is meaningful only while that
            # create-project approval is pending (or when a persisted mapping was found above).
            # Never reinterpret an orphaned p_ap_* as a fresh generic request: that is exactly how
            # a delayed/replayed click could manufacture another project after the card resolved.
            if raw.startswith("p_") and len(raw) > 2:
                approval_id = raw[2:]
                if self._pending_create_card(approval_id) is not None:
                    return self._project_id_for_approval(approval_id)
                raise ProjectIdResolutionError(
                    msg("server.py.112", raw=raw)
                )
            return self._project_id_for_request(raw)

        raise ProjectIdResolutionError(msg("server.py.113", raw=raw))

    # ── [v0.5] 建群卡：审批通过 → 项目落地 ──
    async def _maybe_create_from_card(self, eng: ProjectEngine, approval_id: str) -> None:
        card = next(
            (c for tool, _aid, c in eng.gate.snapshot_pending()
             if tool == "create_project" and c.get("approval_id") == approval_id),
            None,
        )
        if card is None:
            return                                   # 不是建群卡，跟这儿没关系

        pid = self._project_id_for_approval(approval_id)
        if self.hub.has(pid):
            return                                   # 前端已经用它改过的名字（和目录）建好了

        # [任务 1.7] 强校验：建群必须携带目录。卡上没带目录 → 记日志并跳过，
        #   不建项目、不 crash；带了目录才走 create_project（目录无效时由
        #   create_project 抛错拒绝）。
        card_dir = card.get("project_dir") if isinstance(card.get("project_dir"), str) else None
        if not card_dir:
            # [任务 1.7] 强校验：建群必须带目录；卡上没带 → 记日志跳过，不建项目、不 crash。
            log.warning("[%s] 建群卡未携带 project_dir，已跳过自动建群（建群必须选择项目文件夹）", pid)
            return
        await self.create_project(
            pid,
            str(card.get("project_name") or msg("server.py.114")),
            card_dir,
        )

    async def create_project(
        self, project_id: str, project_name: str, project_dir: str | None = None,
        *, request_project_id: str | None = None,
        roles: list[str] | None = None,
    ) -> str:
        """
        建项目的唯一实现：**定目录** + 建 hub + 起引擎 + 落盘 + 广播 + 让项目经理开口。

        [主动拉入worker] roles：建群时用户勾选的职能前缀（白名单 + 身份去重；
        [20260805] 数量不再设限——一个群聊内 agent 数量原则上无上限）。
        建群后逐个实例化 Worker 并拉入（照主管拉人路径），再 kickoff。
        缺省/空 = 不选，行为与旧版一致。
        """
        project_id = self._canonical_project_id_from_request(project_id, allocate=True)
        self._assert_project_activatable(project_id)
        existed = self.hub.has(project_id)

        resolved = _clean_project_dir(project_dir)
        if resolved is not None:
            self.project_dirs[project_id] = resolved
            self._save_project_dirs()
            self._mark_project_dir_valid(project_id, resolved)
            log.info(msg("server.py.115"), project_id, resolved)
        else:
            # [任务 1.7] 强校验：建群必须携带有效目录。v0.7 遗留的 managed-workspaces
            #   默认目录兜底已删除——没带目录 / 目录无效一律拒绝建群，绝不偷偷建目录。
            raise ValueError(
                f"建群被拒绝：必须携带一个有效的项目文件夹（project_dir）。"
                f"收到：{project_dir or '（空）'}"
            )

        proj = self.hub.get_or_create(project_id, project_name)
        eng = self.engine_for(project_id, project_name)

        if self.store is not None:
            # [v1.0.24.4] 注册表落盘走持久化队列，不占主循环；
            # await 保留原语义：写坏了一样能被建群流程感知。
            _store, _pid, _name = self.store, project_id, proj.name
            await asyncio.wrap_future(
                _store.defer(lambda: _store.upsert_project(_pid, _name)),
            )
            self._persisted_project_ids.add(project_id)
            self._write_projects_index()          # [v0.12 D 6a/6d] 刷新人读索引

        # [v0.16] 项目一建好，就把 internal memory/context.md 落出来（哪怕还没内容）。
        try:
            self.memory.ensure_project_context(eng.internal_workspace)
        except Exception:
            log.exception(msg("server.py.118"), project_id)

        # [v0.7b #1] ★ 事件里带上**实际生效的**目录（不是用户发上来的那个字符串）。
        #   前端可以显示它——「我选的目录到底有没有生效」这个问题，
        #   本来就该在屏幕上有答案，而不是让人去翻后端日志。
        effective_dir = str(eng.workspace_root)

        created_event = self._project_created_payload(project_id)
        created_event["project_dir"] = effective_dir
        if request_project_id and request_project_id != project_id:
            # Optional correlation field; existing clients safely ignore it.  ``project_id``
            # remains the sole authoritative id and its existing meaning is unchanged.
            created_event["request_project_id"] = request_project_id
        await self.hub.emit_no_seq(created_event)
        log.info(msg("server.py.119"), project_id, effective_dir)

        # 项目创建是用户马上会向知知追问的关键状态，直接同步落 Harness Memory。
        eng.record_project_activity(
            msg("server.py.120", **{"proj.name": proj.name}),
            reason="project_created", notify=False,
        )
        await self._update_harness_now()

        # [主动拉入worker] 建群时勾选的职能 → 建群后立刻进队（照主管拉人路径：
        #   reserve_name 掷名 → add_member 建人 → emit agents_created）。
        #   位置在 kickoff 之前：项目经理开场白读取团队上下文时，新人已在花名册。
        created_members: list[dict[str, str]] = []
        for role_prefix in roles or []:
            try:
                role_label = KNOWN_ROLES[role_prefix]
                occupied = set(eng.roster()) | set(eng.stored_roster_full())
                aid = _next_agent_id(role_label, occupied)      # fe_1 / be_1 …
                eng.reserve_name(aid, role_label)               # [v0.9c] 掷名 + 落盘，重启不变
                eng.add_member(aid, role_label)                 # 建实例 + 进花名册 + 幂等
                created_members.append({
                    "id": aid, "role": role_label,
                    "name": eng.member_name(aid),
                })
            except Exception:
                # 个别 Worker 建失败不阻塞建群：记日志，用户可后续对话拉人。
                log.exception("[%s] 建群主动拉入 %s 失败", project_id, role_prefix)
        if created_members:
            await eng.emit({
                "type": "agents_created",
                "agent_id": COORDINATOR,
                "count": len(created_members),
                "members": created_members,
            })

        # [v1.0.23.3] 初入群打招呼：每个新 worker 建群那一刻跑一次 LLM，在群里
        #   打招呼（人性化）。welcome_worker 内部 fire-and-forget + 失败吞异常，
        #   不阻塞建群；顺序上放在 agents_created 之后、kickoff 之前。
        for m in created_members:
            try:
                await eng.welcome_worker(m["id"])
            except Exception:
                log.exception("[%s] 初入群打招呼触发失败 %s", project_id, m.get("id"))

        # [v0.5 #10] ★ 新群建好，项目经理**主动说第一句话**——
        #   一个空群里没人吭声，用户会不知道该干嘛。这条 kickoff 不是用户消息
        #   （不发 user_echo），只是往引擎的 inbox 里塞一个开场白，让它跑一个回合。
        #   Fake 档默认关（它会把这段指令原样念出来，见 config.py 的注释）。
        if not existed and CONFIG.kickoff:
            await eng._submit_internal(  # noqa: SLF001 — automatic turn needs readiness/idempotency
                _kickoff_text(proj.name),
                priority="foreground",
                idempotency_key=f"project-kickoff:{project_id}:v1",
            )

        return project_id

    # ── create_project 指令 ──
    async def _cmd_create_project(self, client: Client, data: dict[str, Any]) -> None:
        project_id = data.get("project_id")
        project_name = data.get("project_name")
        # [v0.7 A0] 项目目录。前端 UI 里是必填的；契约里留可选——
        #   老客户端不发它，知知那条兜底路径也不发。收不到 → 默认目录，不报错。
        raw_dir = data.get("project_dir")
        project_dir = raw_dir if isinstance(raw_dir, str) else None

        # [主动拉入worker] 建群时勾选的职能前缀（如 ["gis","da","fe"]）。
        #   校验：白名单（KNOWN_ROLES）+ 身份去重；[20260805] 数量不再设限。
        roles: list[str] = []
        raw_roles = data.get("roles")
        if raw_roles is not None and not isinstance(raw_roles, list):
            await self._server_error(client, msg("server.py.roles_limit"))
            return
        if isinstance(raw_roles, list):
            seen: set[str] = set()
            for r in raw_roles:
                if isinstance(r, str) and r in KNOWN_ROLES and r not in seen:
                    seen.add(r)
                    roles.append(r)

        if not isinstance(project_id, str) or not isinstance(project_name, str):
            await self._server_error(client, msg("server.py.121"))
            return

        # Resolve at the creation boundary while the original wire id is still available.
        # v0.18 clients already send canonical ids, so request_project_id is normally absent;
        # legacy p_ap_*/slug clients still receive the correlation field for safe reconciliation.
        raw_project_id = project_id
        project_id = self._canonical_project_id_from_request(raw_project_id, allocate=True)

        raw_approval_id = data.get("approval_id")
        if raw_approval_id is not None and not isinstance(raw_approval_id, str):
            await self._server_error(client, msg("server.py.122"))
            return
        if isinstance(raw_approval_id, str):
            project_id = self._bind_project_id_to_approval(raw_approval_id, project_id)

        request_project_id = raw_project_id if raw_project_id != project_id else None
        if request_project_id is not None:
            # The approval binding may override a retry's freshly proposed canonical id. Persist
            # that retry id as an alias too, so any already-queued message/snapshot from the stale
            # optimistic conversation is routed to the authoritative project.
            self._bind_request_alias(request_project_id, project_id)

        if self.hub.has(project_id):
            # 已存在 → 只重发 project_created，不再 kickoff（不然项目经理会重复自我介绍）
            again = self._project_created_payload(project_id)
            if request_project_id and request_project_id != project_id:
                again["request_project_id"] = request_project_id
            await self.hub.emit_no_seq(again)
            log.info(msg("server.py.123"), project_id)
            return

        await self.create_project(
            project_id, project_name, project_dir,
            request_project_id=request_project_id,
            roles=roles,
        )

    # ── add_agents（[v1.0.23.4] 群聊中途添加 Agent 员工）──
    async def _cmd_add_agents(self, client: Client, data: dict[str, Any]) -> None:
        """
        [v1.0.23.4] 群聊中途添加 Agent 员工：用户按钮直达，不走审批卡。

        与建群 roles 路径同一套建人块（create_project 内）：_next_agent_id
        自动编号（fe_1 占用 → fe_2/fe_3…，含归档不复用）→ reserve_name 掷名
        → add_member 建人 → emit agents_created → welcome_worker（与初入群
        机制完全相同，方案 B 纯对话）。

        roles：职能前缀数组，**允许重复**（同职能多选，如 ['fe','fe','gis']
        = 加 2 个前端 + 1 个 GIS）。数量编码在数组长度里，无上限；
        单点容错：个别职能建失败不阻塞整单。
        """
        project_id = data.get("project_id")
        if not isinstance(project_id, str):
            await self._server_error(client, msg("server.py.121"))
            return
        try:
            project_id = self._canonical_project_id_from_request(project_id)
        except ProjectIdResolutionError:
            await self._server_error(client, msg("addAgents.err.project"))
            return

        # 私聊 / 知知平台窗不可中途添加
        if _parse_dm(project_id) is not None or project_id == PLATFORM_PROJECT_ID:
            await self._server_error(client, msg("addAgents.err.project"))
            return

        raw_roles = data.get("roles")
        if not isinstance(raw_roles, list) or not raw_roles:
            await self._server_error(client, msg("server.py.roles_limit"))
            return

        # 白名单校验：前缀必须来自标准库（KNOWN_ROLES），允许重复
        role_labels: list[str] = []
        for r in raw_roles:
            if not isinstance(r, str) or r not in KNOWN_ROLES:
                await self._server_error(client, msg("addAgents.err.roles"))
                return
            role_labels.append(KNOWN_ROLES[r])

        eng = self.engine_for(project_id)
        created: list[dict[str, str]] = []
        occupied = set(eng.roster()) | set(eng.stored_roster_full())
        for label in role_labels:
            try:
                aid = _next_agent_id(label, occupied)   # fe_1 占用 → fe_2 …
                occupied.add(aid)                        # ★ 同次多建防重（[v1.0.23.4]）
                eng.reserve_name(aid, label)             # 掷名（旧名优先）
                eng.add_member(aid, label)               # 建实例 + 进花名册 + 落盘
                created.append({
                    "id": aid, "role": label,
                    "name": eng.member_name(aid),
                })
            except Exception:
                # 个别 Worker 建失败不阻塞整单：记日志，其余照常拉入。
                log.exception("[%s] 中途添加 %s 失败", project_id, label)

        if created:
            await eng.emit({
                "type": "agents_created",
                "agent_id": COORDINATOR,
                "count": len(created),
                "members": created,
            })
            # [v1.0.23.3] 与初入群相同的欢迎机制：fire-and-forget + 失败吞异常。
            for m in created:
                try:
                    await eng.welcome_worker(m["id"])
                except Exception:
                    log.exception("[%s] 中途添加打招呼失败 %s", project_id, m.get("id"))

    # ── request_snapshot ──
    async def _cmd_request_snapshot(self, client: Client, data: dict[str, Any]) -> None:
        project_id = data.get("project_id")
        if not isinstance(project_id, str):
            await self._server_error(client, msg("server.py.124"))
            return
        # [v0.37] 私聊频道（dm:{group}:{agent}）直接快照它自己——它是独立的 Hub 频道，
        #   不走项目 id 规范化（那会把 dm: 当未知项目拒掉）。频道不存在就现建一个空的。
        dm = _parse_dm(project_id)
        if dm is not None:
            try:
                group_id = self._canonical_project_id_from_request(dm[0])
                self._ensure_dm_channel(project_id, group_id)
            except ProjectIdResolutionError:
                # 群还没就绪：先给个空频道，但展示名至少用 agent 段，不能把完整 dm:*
                # 原始会话 id 注入前端标题。群就绪后的 _ensure_dm_channel 会继续升级名字。
                self.hub.get_or_create(project_id, dm[1])
                await self.hub.snapshot(project_id)
                return
            # [v1.0.24.4] 快照附带权威活动账本（全量）：前端重建现场时忙碌状态一次对齐。
            await self.hub.snapshot(
                project_id, activity=self._open_activity_all(),
            )
            return
        project_id = self._canonical_project_id_from_request(project_id)
        # [v1.0.24.4] 快照附带权威活动账本（全量）：前端重建现场时忙碌状态一次对齐。
        await self.hub.snapshot(
            project_id, activity=self._open_activity_all(),
        )

    # ── replay_request（非首帧 → 拒绝，前端应改用 request_snapshot） ──
    async def _cmd_replay_request(self, client: Client, data: dict[str, Any]) -> None:
        if not client.handshake_done:
            await self._do_replay(client, data)
            return
        await self._server_error(
            client,
            msg("server.py.125"),
        )

    # ── mark_read（未读水位；前端契约里没有，多出来的能力，不发它也不影响） ──
    async def _cmd_mark_read(self, client: Client, msg: dict[str, Any]) -> None:
        project_id = msg.get("project_id")
        seq = msg.get("seq")
        if isinstance(project_id, str) and isinstance(seq, int):
            # DM 是独立 Hub 频道，不应送进项目 id canonicalizer。
            if _parse_dm(project_id) is None:
                project_id = self._canonical_project_id_from_request(project_id)
            self.hub.mark_read(project_id, seq)

    # ── shutdown（前端永不发；留着给运维/测试） ──
    async def _cmd_shutdown(self, client: Client, data: dict[str, Any]) -> None:
        project_id = data.get("project_id")
        if not isinstance(project_id, str):
            await self._server_error(client, msg("server.py.126"))
            return
        project_id = self._canonical_project_id_from_request(project_id)
        eng = self.engines.pop(project_id, None)
        if eng:
            await eng.stop()
            log.warning("project %s engine shut down by client", project_id)

    # ── 服务器级 error（无 seq，进前端全局通知通道） ──
    async def _server_error(self, client: Client, message: str) -> None:
        await self.hub.send_to(client, {
            "type": "error",
            "message": message,
            "ts": now_ts(),
        })
        log.warning("server error → %s: %s", client.id, message)

    # ═══════════════════════════════════════════════════════════
    # /health + /preview（127.0.0.1:8081）
    #
    # [v0.36] 同一个本地 HTTP 服务多挂一个 /preview 端点：前端文件预览面板
    #   用它按需取项目内文件的原始字节（Markdown/HTML/图片/PDF/docx/pptx/xlsx…）。
    #
    #   为什么走 HTTP 而不是 WebSocket：预览是**大对象、按需拉取**（一个 PDF 几 MB），
    #   塞进事件流会撑爆 ring、和聊天事件抢道；而 WS 契约是逐字段严校的，
    #   往里加二进制通道得动一整条链路。HTTP 端点旁路开一条，不碰任何事件契约，
    #   浏览器/Electron 渲染进程 fetch 一下即可（原生 <img>/<iframe>/pdf 查看器直接吃 URL）。
    #
    #   安全边界与 Worker 写文件**同一把锁**：resolve_in_sandbox 只允许项目目录内、
    #   相对路径、非系统目录、非历史内部目录——越界一律 403，绝不外泄。只读。
    # ═══════════════════════════════════════════════════════════

    #: 单次预览允许的最大字节数。超了返回 413，让前端走「在外部打开」降级——
    #: 把一个 500MB 的文件整个读进内存塞给渲染进程，是拿体验换崩溃。
    _PREVIEW_MAX_BYTES = 64 * 1024 * 1024

    _PREVIEW_HOST_RE = re.compile(
        r"^p-([0-9a-f]{32})\.preview\.localhost(?::\d{1,5})?$",
        re.IGNORECASE,
    )

    @staticmethod
    def _preview_origin_token(project_id: str) -> str:
        return hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:32]

    @classmethod
    def _preview_host_token(cls, host: str) -> str | None:
        match = cls._PREVIEW_HOST_RE.fullmatch(host.strip().lower())
        return match.group(1).lower() if match else None

    @classmethod
    def _is_preview_origin(cls, raw: str) -> bool:
        if not raw:
            return False
        try:
            return cls._preview_host_token(urlsplit(raw).netloc) is not None
        except ValueError:
            return False

    @staticmethod
    def _configured_renderer_origins() -> set[str]:
        """Exact app origins allowed to read the local desktop HTTP surface."""
        origins = {"null"}  # Packaged file:// renderer serializes its CORS origin as null.
        raw = os.environ.get("ELECTRON_RENDERER_URL", "").strip()
        if raw:
            try:
                parsed = urlsplit(raw)
                if parsed.scheme in {"http", "https"} and parsed.netloc:
                    origins.add(f"{parsed.scheme}://{parsed.netloc}")
            except ValueError:
                pass
        return origins

    async def _health_conn(self, reader: asyncio.StreamReader,
                           writer: asyncio.StreamWriter) -> None:
        method = "GET"
        path = "/"
        try:
            async with asyncio.timeout(HTTP_HEADERS_TOTAL_TIMEOUT_S):
                line = await reader.readline()
                if len(line) > HTTP_REQUEST_LINE_MAX:
                    self._write_http(
                        writer, b"414 URI Too Long",
                        b'{"ok":false,"code":"uri_too_long","error":"request target is too long"}',
                        b"application/json", cors=False,
                    )
                    await writer.drain()
                    return
                if not line or not line.endswith(b"\n"):
                    raise asyncio.IncompleteReadError(line, None)
                parts = line.decode("latin-1").split()
                method = parts[0].upper() if parts else "GET"
                raw_target = parts[1] if len(parts) > 1 else "/"
                headers: dict[str, str] = {}
                header_bytes = 0
                header_count = 0
                while True:
                    h = await reader.readline()
                    if h in (b"\r\n", b"\n", b""):
                        break
                    header_bytes += len(h)
                    header_count += 1
                    if header_bytes > HTTP_HEADERS_MAX_BYTES or header_count > HTTP_HEADERS_MAX_COUNT:
                        self._write_http(
                            writer, b"431 Request Header Fields Too Large",
                            b'{"ok":false,"code":"headers_too_large","error":"request headers are too large"}',
                            b"application/json", cors=False,
                        )
                        await writer.drain()
                        return
                    try:
                        name, value = h.decode("latin-1").split(":", 1)
                    except ValueError:
                        continue
                    headers[name.strip().lower()] = value.strip()

            split = urlsplit(raw_target)
            path = split.path
            host = headers.get("host", "")
            preview_token = self._preview_host_token(host)
            # Fallback: extract preview token from query param when Host header lacks it
            # (e.g. Windows where *.preview.localhost DNS doesn't resolve)
            if preview_token is None:
                query_params = parse_qs(split.query)
                kpt = query_params.get('_kpt', [None])[0]
                if kpt and re.fullmatch(r'[0-9a-f]{32}', kpt):
                    preview_token = kpt

            # An isolated preview origin is a static, read-only project resource surface.
            if preview_token is not None:
                if method != "GET":
                    self._write_http(
                        writer,
                        b"405 Method Not Allowed",
                        b'{"error":"preview origin is read-only"}',
                        b"application/json",
                        cors=False,
                    )
                else:
                    await self._serve_preview_tree(
                        writer,
                        path,
                        preview_token=preview_token,
                    )
                await writer.drain()
                return

            supplied_token = headers.get("x-knowe-runtime-token", "")
            # WebSocket 升级请求无法通过 Electron webRequest 注入 header，
            # 允许前端通过 URL query 参数 ?token=… 传递认证令牌。
            if not supplied_token:
                qs_token = parse_qs(split.query).get("token", [""])[0]
                if qs_token:
                    supplied_token = qs_token
            if not CONFIG.runtime_token or not hmac.compare_digest(
                supplied_token, CONFIG.runtime_token,
            ):
                self._write_http(
                    writer,
                    b"401 Unauthorized",
                    b'{"ok":false,"code":"runtime_auth_required","error":"runtime authentication required"}',
                    b"application/json",
                    cors=False,
                )
                await writer.drain()
                return

            raw_length = headers.get("content-length", "0").strip()
            try:
                content_length = int(raw_length)
                if content_length < 0:
                    raise ValueError
            except ValueError:
                self._write_http(
                    writer, b"400 Bad Request",
                    b'{"error":"invalid content-length"}', b"application/json", cors=False,
                )
                await writer.drain()
                return
            if headers.get("transfer-encoding"):
                self._write_http(
                    writer, b"400 Bad Request",
                    b'{"error":"transfer-encoding is unsupported"}', b"application/json", cors=False,
                )
                await writer.drain()
                return

            allowed_origins = self._configured_renderer_origins()
            origin = headers.get("origin", "").strip()
            cors_token = None
            if origin in allowed_origins:
                cors_token = _HTTP_CORS_ORIGIN.set(origin)

            exact_route_methods: dict[str, set[str]] = {
                "/health": {"GET"},
                "/shutdown": {"POST"},
                "/settings": {"GET", "POST"},
                "/settings/test": {"POST"},
                "/projects/menu-state": {"GET"},
                "/projects/pin": {"POST"},
                "/projects/mute": {"POST"},
                "/projects/fold": {"POST"},
                "/projects/rename": {"POST"},
                "/projects/permanent-delete": {"POST"},
                "/agents/permanent-delete": {"POST"},
                "/preview/resolve": {"GET"},
                "/files/reveal": {"POST"},
                "/preview": {"GET"},
                "/history": {"GET"},
                "/history/handoffs": {"GET"},
                "/api/events": {"GET"},   # [v1.0.23.6] 增量读取（HTTP 旁路，不碰 WS 状态机）
            }
            allowed_methods = exact_route_methods.get(path)
            if allowed_methods is None and path.startswith("/preview/tree/"):
                allowed_methods = {"GET"}
            if allowed_methods is None and path.startswith("/api/knowledge"):
                allowed_methods = {"GET", "POST"}

            try:
                if method == "OPTIONS":
                    if allowed_methods is None:
                        self._write_http(
                            writer,
                            b"404 Not Found",
                            b'{"error":"not found"}',
                            b"application/json",
                        )
                    else:
                        self._write_http(
                            writer,
                            b"204 No Content",
                            b"",
                            b"text/plain",
                            extra=(
                                b"Access-Control-Allow-Methods: "
                                + ", ".join(sorted(allowed_methods | {"OPTIONS"})).encode("ascii")
                                + b"\r\n" +
                                b"Access-Control-Allow-Headers: Content-Type, X-Knowe-Runtime-Token\r\n"
                            ),
                        )
                    await writer.drain()
                    return

                if allowed_methods is not None and method not in allowed_methods:
                    allow = ", ".join(sorted(allowed_methods)).encode("ascii")
                    self._write_http(
                        writer,
                        b"405 Method Not Allowed",
                        b'{"error":"method not allowed"}',
                        b"application/json",
                        extra=b"Allow: " + allow + b"\r\n",
                    )
                    await writer.drain()
                    return

                body_bytes = b""
                if method == "POST":
                    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type != "application/json":
                        self._write_http(
                            writer,
                            b"415 Unsupported Media Type",
                            b'{"error":"POST requires application/json"}',
                            b"application/json",
                        )
                        await writer.drain()
                        return
                    if content_length > 1_000_000:
                        self._write_http(
                            writer,
                            b"413 Payload Too Large",
                            b'{"error":"request body exceeds 1000000 bytes"}',
                            b"application/json",
                        )
                        await writer.drain()
                        return
                    if content_length:
                        body_bytes = await asyncio.wait_for(
                            reader.readexactly(content_length), timeout=10,
                        )

                if path == "/health" and method == "GET":
                    body = json.dumps(self.hub.health()).encode()
                    self._write_http(writer, b"200 OK", body, b"application/json")
                    await writer.drain()
                    return

                if path == "/shutdown" and method == "POST":
                    self._write_http(
                        writer,
                        b"202 Accepted",
                        b'{"ok":true,"shutting_down":true}',
                        b"application/json",
                    )
                    await writer.drain()
                    self._shutdown_event.set()
                    return

                if path.startswith("/api/knowledge"):
                    response = await dispatch_knowledge_http(
                        method, path, split.query, body_bytes,
                    )
                    status = {
                        200: b"200 OK", 400: b"400 Bad Request", 404: b"404 Not Found",
                        405: b"405 Method Not Allowed", 413: b"413 Payload Too Large",
                        500: b"500 Internal Server Error", 503: b"503 Service Unavailable",
                        504: b"504 Gateway Timeout",
                    }.get(response.status, f"{response.status} Error".encode("ascii"))
                    body = json.dumps(response.body, ensure_ascii=False).encode("utf-8")
                    self._write_http(writer, status, body, b"application/json; charset=utf-8")
                    await writer.drain()
                    return

                # [v0.44 设置] 设置面板的三个端点（与 /preview 同一个本机只信 127.0.0.1 的小服务）：
                #   GET  /settings       → 当前生效设置快照（前端开机对账用）
                #   POST /settings       → 整包应用 + 落盘 + 通知各引擎热更新
                #   POST /settings/test  → 对一份绑定真发最小请求（连接测试，禁 mock）
                if path == "/settings" and method == "GET":
                    snapshot = runtime_settings.api_snapshot(welcome_state=self._welcome_state())
                    snapshot["restart_required"] = self._settings_restart_required
                    snapshot["feature_flags"] = feature_flag_snapshot()
                    body = json.dumps(snapshot, ensure_ascii=False).encode()
                    self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")
                    await writer.drain()
                    return

                if path == "/settings" and method == "POST":
                    await self._serve_settings_apply(writer, body_bytes)
                    await writer.drain()
                    return

                if path == "/settings/test" and method == "POST":
                    await self._serve_settings_test(writer, body_bytes)
                    await writer.drain()
                    return

                # [v0.44.8] 群聊列表右键菜单。状态以后端为真源，所有窗口经广播/HTTP 对账。
                if path == "/projects/menu-state" and method == "GET":
                    await self._serve_project_menu_state(writer)
                    await writer.drain()
                    return

                state_routes = {
                    "/projects/pin": "pinned",
                    "/projects/mute": "muted",
                    "/projects/fold": "folded",
                }
                if path in state_routes and method == "POST":
                    await self._serve_project_state_change(
                        writer, body_bytes, state_routes[path],
                    )
                    await writer.drain()
                    return

                if path == "/projects/rename" and method == "POST":
                    await self._serve_project_rename(writer, body_bytes)
                    await writer.drain()
                    return

                # 联系人视图的永久删除与群聊列表既有菜单端点隔离，避免互相装配。
                if path == "/projects/permanent-delete" and method == "POST":
                    await self._serve_project_permanent_delete(writer, body_bytes)
                    await writer.drain()
                    return

                if path == "/agents/permanent-delete" and method == "POST":
                    await self._serve_agent_permanent_delete(writer, body_bytes)
                    await writer.drain()
                    return

                if path == "/preview/resolve" and method == "GET":
                    await self._serve_preview_resolve(writer, split.query)
                    await writer.drain()
                    return

                if path == "/files/reveal" and method == "POST":
                    await self._serve_file_reveal(writer, body_bytes)
                    await writer.drain()
                    return

                if method == "GET" and path.startswith("/preview/tree/"):
                    await self._serve_preview_tree(writer, path, preview_token=None)
                    await writer.drain()
                    return

                if path == "/preview" and method == "GET":
                    await self._serve_preview(writer, split.query)
                    await writer.drain()
                    return

                if path == "/history" and method == "GET":
                    # [v0.38] 聊天记录：过滤 + 分页 + 按日期查。与 /preview 同一个只读 HTTP 服务。
                    await self._serve_history(writer, split.query)
                    await writer.drain()
                    return

                if path == "/history/handoffs" and method == "GET":
                    # [v0.38.3 #4] 群聊「报告/交接」：把 instruction_injected + report_submitted 配对。
                    await self._serve_handoffs(writer, split.query)
                    await writer.drain()
                    return

                if path == "/api/events" and method == "GET":
                    # [v1.0.23.6] 增量读取（HTTP 旁路预热通道）：seq > after_seq 的结构事件。
                    await self._serve_events_delta(writer, split.query)
                    await writer.drain()
                    return

                self._write_http(writer, b"404 Not Found",
                                 b'{"error":"not found"}', b"application/json")
                await writer.drain()
            finally:
                if cors_token is not None:
                    _HTTP_CORS_ORIGIN.reset(cors_token)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            # A client that advertises a body and then stops sending used to hit this
            # path after ten seconds and receive a silent EOF.  Return a complete HTTP
            # response so callers can distinguish an application timeout from a broken
            # transport, while keeping the lightweight local server alive.
            if not getattr(writer, "_knowe_response_written", False):
                body = json.dumps({"error": "request timeout"}).encode("utf-8")
                self._write_http(
                    writer,
                    b"408 Request Timeout",
                    body,
                    b"application/json; charset=utf-8",
                )
                with contextlib.suppress(ConnectionError, OSError):
                    await writer.drain()
        except Exception as exc:
            log.exception("local HTTP request failed: method=%s path=%s type=%s", method, path, type(exc).__name__)
            if not getattr(writer, "_knowe_response_written", False):
                self._write_http(
                    writer,
                    b"500 Internal Server Error",
                    b'{"ok":false,"code":"internal_error","error":"local runtime request failed"}',
                    b"application/json",
                    cors=False,
                )
                with contextlib.suppress(ConnectionError, OSError):
                    await writer.drain()
        finally:
            writer.close()

    def _write_http(self, writer: asyncio.StreamWriter, status: bytes,
                    body: bytes, content_type: bytes, *, extra: bytes = b"",
                    cors: bool = True) -> None:
        """一条极简 HTTP/1.1 应答（Connection: close），文本和二进制通用。"""
        setattr(writer, "_knowe_response_written", True)
        allowed_origin = _HTTP_CORS_ORIGIN.get() if cors else None
        cors_header = (
            f"Access-Control-Allow-Origin: {allowed_origin}\r\nVary: Origin\r\n".encode("ascii")
            if allowed_origin else b""
        )
        writer.write(
            b"HTTP/1.1 " + status + b"\r\n" +
            b"Content-Type: " + content_type + b"\r\n" +
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            + cors_header +
            b"Cache-Control: no-store\r\n"
            + extra +
            b"Connection: close\r\n\r\n" + body
        )

    def _preview_error(
        self,
        writer: asyncio.StreamWriter,
        status: bytes,
        message: str,
        cors: bool = True,
        **details: Any,
    ) -> None:
        payload: dict[str, Any] = {"error": message}
        payload.update({key: value for key, value in details.items() if value is not None})
        body = json.dumps(payload, ensure_ascii=False).encode()
        self._write_http(
            writer,
            status,
            body,
            b"application/json; charset=utf-8",
            cors=cors,
        )

    # ── [v0.44.8] 群聊列表菜单 API ──────────────────────────────

    @staticmethod
    def _decode_json_object(body_bytes: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(msg("server.py.127", exc=exc)) from exc
        if not isinstance(payload, dict):
            raise ValueError(msg("server.py.128"))
        return payload

    def _menu_project_id(self, payload: dict[str, Any]) -> str:
        raw = payload.get("project_id")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(msg("server.py.129"))
        try:
            project_id = self._canonical_project_id_from_request(raw.strip())
        except ProjectIdResolutionError as exc:
            raise ValueError(str(exc)) from exc
        if project_id == PLATFORM_PROJECT_ID or _parse_dm(project_id) is not None:
            raise ValueError(msg("server.py.130"))
        if project_id not in self.hub.projects:
            raise ValueError(msg("server.py.094"))
        return project_id

    def _project_delete_target(
        self, payload: dict[str, Any],
    ) -> tuple[str, str, bool]:
        """Resolve a delete request, including retries after the tombstone committed.

        HTTP acknowledgement can be lost after a successful local transaction.  A retry
        must therefore return the same successful semantic result instead of saying
        "project does not exist".  ``requested`` is echoed so the renderer can reconcile
        an old optimistic alias with the authoritative id.
        """
        raw = payload.get("project_id")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(msg("server.py.129"))
        requested = raw.strip()
        if requested == PLATFORM_PROJECT_ID or _parse_dm(requested) is not None:
            raise ValueError(msg("server.py.131"))

        mapped = self._mapped_project_id(requested)
        candidate = mapped or requested
        deleted = self._deleted_project_ids()
        if candidate in deleted:
            return candidate, requested, True

        try:
            project_id = self._canonical_project_id_from_request(requested)
        except ProjectIdResolutionError as exc:
            raise ValueError(str(exc)) from exc
        if project_id == PLATFORM_PROJECT_ID or _parse_dm(project_id) is not None:
            raise ValueError(msg("server.py.131"))
        if project_id not in self.hub.projects:
            raise ValueError(msg("server.py.094"))
        return project_id, requested, False

    def _menu_agent_id(self, payload: dict[str, Any], project_id: str) -> str:
        """联系人菜单中的 Agent 必须是该项目当前可见的精确身份。"""
        raw = payload.get("agent_id")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(msg("server.py.283"))
        agent_id = raw.strip()
        if agent_id in {"zinnia", PLATFORM_PROJECT_ID}:
            raise ValueError(msg("server.py.284"))

        # 项目经理是项目固定技术角色；即使很老的花名册没写过这一行，联系人里的项目经理卡
        # 仍是合法目标。Worker 则必须由活跃花名册/当前引擎精确作证。
        if agent_id == COORDINATOR:
            return agent_id
        if self.store is not None:
            row = self.store.load_roster_full(project_id).get(agent_id)
            if row and row.get("status", "active") == "active":
                return agent_id
        eng = self.engines.get(project_id)
        if eng is not None and agent_id in eng.roster():
            return agent_id
        raise ValueError(msg("server.py.285"))

    def _delete_path_candidate(
        self, raw: Path | str | None, *, internal: bool = False,
    ) -> Path | None:
        """Return an internal-only deletion target; business paths are never candidates."""
        if raw is None or not str(raw).strip():
            return None
        if not internal:
            # Fail before Path.resolve/is_link_like so a business ledger value is never
            # stat'ed, followed, renamed or deleted by the permanent-delete flow.
            raise ValueError(msg("server.py.132"))
        candidate = Path(raw).expanduser().absolute()
        # resolve() follows Windows junctions/reparse points. Permanent deletion must
        # remove the project entry itself, never silently retarget to foreign data.
        link_like = is_link_like(candidate)
        try:
            resolved = candidate if link_like else candidate.resolve(strict=False)
        except OSError as exc:
            raise ValueError(msg("server.py.133", candidate=candidate)) from exc
        if resolved == resolved.parent:
            raise ValueError(msg("server.py.134", resolved=resolved))

        protected: list[Path] = [self.data_root, Path.home(), Path.cwd()]
        install_root = getattr(CONFIG, "install_root", None)
        if install_root:
            protected.append(Path(str(install_root)).expanduser())
        for raw_root in _FORBIDDEN_ROOTS:
            protected.append(Path(raw_root).expanduser())

        for root in protected:
            try:
                guard = root.resolve(strict=False)
                guard_lexical = root.expanduser().absolute()
            except OSError:
                continue
            # resolved == guard：正删共享根；resolved in guard.parents：正删共享根的祖先。
            # Reparse points are compared lexically as well because we intentionally do
            # not follow their target during a delete transaction.
            if (
                resolved == guard
                or resolved in guard.parents
                or candidate == guard_lexical
                or candidate in guard_lexical.parents
            ):
                raise ValueError(msg("server.py.135", resolved=resolved))

        data_root = self.data_root.resolve(strict=False)
        lexical_data_root = self.data_root.expanduser().absolute()
        under_resolved_root = data_root in resolved.parents
        under_lexical_root = lexical_data_root in candidate.parents
        if not (under_resolved_root or (link_like and under_lexical_root)):
            raise ValueError(msg("server.py.136", resolved=resolved))
        return resolved

    def _assert_project_delete_paths_isolated(
        self, project_id: str, paths: list[Path],
    ) -> None:
        """拒绝删除包含其他项目目录的共享父目录，防止级联误伤。"""
        targets = [path.absolute() if is_link_like(path) else path.resolve(strict=False)
                   for path in paths]
        other_ids = (
            set(self._persisted_project_ids)
            | {
                pid for pid in self.hub.projects
                if pid != PLATFORM_PROJECT_ID and _parse_dm(pid) is None
            }
        )
        other_ids.discard(project_id)
        for other_id in other_ids:
            try:
                candidates: list[Path | str | None] = [self._internal_workspace_for(other_id)]
            except Exception:  # noqa: BLE001 — 只做保护性探测
                candidates = []
            for raw in candidates:
                if raw is None or not str(raw).strip():
                    continue
                other = Path(raw).expanduser().absolute()
                if not is_link_like(other):
                    try:
                        other = other.resolve(strict=False)
                    except OSError:
                        continue
                for target in targets:
                    # 删除 target 会带走 target 自身及所有后代；只要另一个项目的根
                    # 位于其中，就必须拒绝。反向包含（target 在共享父目录里面）是安全的。
                    if other == target or target in other.parents:
                        raise ValueError(
                            msg("server.py.286", target=target, other_id=other_id)
                        )

    @staticmethod
    def _stage_delete_paths(paths: list[Path], token: str) -> list[tuple[Path, Path]]:
        """Stage arbitrary delete paths through the Windows-aware filesystem barrier."""
        unique: list[Path] = []
        for path in sorted(paths, key=lambda p: len(p.parts)):
            if any(path == parent or parent in path.parents for parent in unique):
                continue
            unique.append(path)

        staged: list[tuple[Path, Path]] = []
        try:
            for original in unique:
                if not os.path.lexists(os.fspath(original)):
                    continue
                base = original.with_name(f".{original.name}.knowe-delete-{token}")
                target = base
                seq = 1
                while os.path.lexists(os.fspath(target)):
                    seq += 1
                    target = base.with_name(f"{base.name}-{seq}")
                stage_delete_path(original, target)
                staged.append((original, target))
            return staged
        except Exception:
            rollback_errors: list[str] = []
            for original, target in reversed(staged):
                try:
                    restore_staged_path(original, target)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{target} → {original}: {rollback_exc}")
                    log.exception(msg("server.py.137"), target, original)
            if rollback_errors:
                log.error(msg("server.py.138"), len(rollback_errors))
            raise

    @staticmethod
    def _restore_staged_paths(staged: list[tuple[Path, Path]]) -> None:
        errors: list[str] = []
        for original, target in reversed(staged):
            try:
                restore_staged_path(original, target)
            except Exception as exc:  # noqa: BLE001 — try every journaled path
                errors.append(f"{target} → {original}: {exc}")
                log.exception(msg("server.py.139"), target, original)
        if errors:
            raise OSError("；".join(errors))

    @staticmethod
    def _purge_staged_paths(staged: list[tuple[Path, Path]]) -> None:
        errors: list[str] = []
        for original, target in staged:
            try:
                purge_staged_path(original, target)
            except Exception as exc:  # noqa: BLE001 — purge every independent path
                errors.append(f"{target}: {exc}")
                log.exception(msg("server.py.140"), target)
        if errors:
            raise OSError("；".join(errors))

    @staticmethod
    def _plan_project_delete_paths(
        paths: list[Path], token: str,
    ) -> list[tuple[Path, Path]]:
        """Create deterministic same-volume staging pairs without mutating disk.

        The complete plan is persisted before the first rename.  Recovery therefore
        works even if the process dies between ``rename()`` and the next Python line.
        """
        unique: list[Path] = []
        for path in sorted(paths, key=lambda p: len(p.parts)):
            if any(path == parent or parent in path.parents for parent in unique):
                continue
            unique.append(path)

        planned: list[tuple[Path, Path]] = []
        for original in unique:
            base = original.with_name(f".{original.name}.knowe-delete-{token}")
            target = base
            seq = 1
            while os.path.lexists(os.fspath(target)):
                seq += 1
                target = base.with_name(f"{base.name}-{seq}")
            planned.append((original, target))
        return planned

    @staticmethod
    def _stage_planned_project_paths(
        planned: list[tuple[Path, Path]],
        resource_close_issues: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        """Apply the pre-journaled one-root rename and restore it on failure."""
        moved: list[tuple[Path, Path]] = []
        outcomes: list[dict[str, Any]] = []
        try:
            for original, target in planned:
                result = stage_delete_path(
                    original,
                    target,
                    resource_close_issues=resource_close_issues,
                )
                outcomes.append(result.as_dict())
                if result.method != "absent":
                    moved.append((original, target))
            return outcomes
        except Exception:
            rollback_errors: list[str] = []
            for original, target in reversed(moved):
                try:
                    restore_staged_path(original, target)
                except Exception as rollback_exc:  # noqa: BLE001
                    rollback_errors.append(f"{target} → {original}: {rollback_exc}")
                    log.exception("项目目录暂存失败后的即时回滚失败：%s → %s", target, original)
            if rollback_errors:
                log.error("项目目录暂存失败后有 %d 个路径未完全回滚", len(rollback_errors))
            raise

    def _is_internal_delete_ledger_path(self, raw: str) -> bool:
        """Lexical containment check only; deliberately performs no filesystem access."""
        root = os.path.normcase(os.path.abspath(os.path.expanduser(os.fspath(self.data_root))))
        candidate = os.path.normcase(os.path.abspath(os.path.expanduser(raw)))
        if candidate == root:
            return False
        try:
            return os.path.commonpath((root, candidate)) == root
        except (ValueError, OSError):
            return False

    def _transaction_path_pairs(self, row: dict[str, Any]) -> list[tuple[Path, Path]]:
        """Read only internal pairs from a delete journal; legacy external pairs are inert."""
        out: list[tuple[Path, Path]] = []
        raw_paths = row.get("paths")
        if not isinstance(raw_paths, list):
            return out
        for item in raw_paths:
            if not isinstance(item, dict):
                continue
            original = item.get("original")
            staged = item.get("staged")
            if isinstance(original, str) and original and isinstance(staged, str) and staged:
                if (
                    self._is_internal_delete_ledger_path(original)
                    and self._is_internal_delete_ledger_path(staged)
                ):
                    out.append((Path(original), Path(staged)))
                else:
                    log.warning(
                        "[%s] 忽略旧删除事务中的外部路径，不恢复也不清理：%s -> %s",
                        row.get("project_id") or "?", original, staged,
                    )
        return out

    def _cleanup_deleted_project_metadata(self, project_id: str) -> list[str]:
        """Idempotently remove every non-memory projection of a deleted project."""
        warnings: list[str] = []

        def attempt(label: str, action: Callable[[], Any]) -> None:
            try:
                result = action()
                if result is False:
                    raise OSError(msg("server.py.141", label=label))
            except Exception as exc:  # noqa: BLE001 — each projection retries independently
                detail = self._short_delete_error(exc)
                warnings.append(f"{label}：{detail}")
                log.exception("[%s] %s", project_id, label)

        if self.store is not None:
            # [v1.0.24.4] 删项目要扫全量文件，先排空持久化队列再动手，
            # 不与延迟中的事件 append 抢同一份 jsonl。
            self.store.flush(timeout=30)
            attempt(msg("server.py.142"), lambda: self.store.delete_project(project_id))
        attempt(msg("server.py.143"), lambda: self.hub.remove_project_tree(project_id))

        self.engines.pop(project_id, None)
        self.project_dirs.pop(project_id, None)
        self.project_dir_status.pop(project_id, None)
        self.conversation_states.pop(project_id, None)
        self._paused_histories.pop(project_id, None)
        self._directory_popup_paused.discard(project_id)
        self._roster_restored.discard(project_id)
        self._persisted_project_ids.discard(project_id)
        self.announced.pop(project_id, None)
        self.project_card_ids = {
            key: value for key, value in self.project_card_ids.items()
            if key != project_id and value != project_id
        }

        saves: tuple[tuple[str, Callable[[], Any]], ...] = (
            (msg("server.py.144"), self._save_project_dirs),
            (msg("server.py.145"), self._save_project_dir_status),
            (msg("server.py.146"), self._save_conversation_states),
            (msg("server.py.147"), self._save_project_card_ids),
            (msg("server.py.148"), self._save_announced),
            (msg("server.py.149"), self._write_projects_index),
        )
        for label, save in saves:
            attempt(label, save)
        return warnings

    def _remember_project_delete_cleanup(
        self,
        row: dict[str, Any],
        warnings: list[str],
    ) -> None:
        """Keep a committed transaction until every derived resource is gone."""
        pending = dict(row)
        pending["stage"] = "cleanup_pending"
        pending["warnings"] = list(dict.fromkeys(warnings))
        self._put_project_delete_transaction(pending)

    def _finish_committed_project_delete_sync(
        self, project_id: str, row: dict[str, Any],
    ) -> list[str]:
        warnings = self._cleanup_deleted_project_metadata(project_id)
        planned = self._transaction_path_pairs(row)
        try:
            self._purge_staged_paths(planned)
        except Exception as exc:  # noqa: BLE001
            detail = self._short_delete_error(exc)
            warnings.append(msg("server.py.324", detail=detail))
            log.exception("[%s] 暂存项目目录物理清除失败", project_id)

        if warnings:
            try:
                self._remember_project_delete_cleanup(row, warnings)
            except Exception:  # noqa: BLE001
                # Tombstone remains the logical source of truth even if the retry ledger
                # itself cannot be updated. Startup also filters all tombstoned ids.
                log.exception("[%s] 删除清理待重试状态无法保存", project_id)
        else:
            try:
                self._drop_project_delete_transaction(project_id)
            except Exception:  # noqa: BLE001
                log.exception("[%s] 已完成删除事务，但无法移除事务账本行", project_id)
        return warnings

    def _recover_project_delete_transactions(self) -> None:
        """Recover interrupted deletion before any project is restored into Hub."""
        rows = self._load_project_delete_transactions()
        if not rows:
            return
        deleted = self._deleted_project_ids()
        for project_id, row in list(rows.items()):
            planned = self._transaction_path_pairs(row)
            if project_id in deleted:
                warnings = self._finish_committed_project_delete_sync(project_id, row)
                if warnings:
                    log.warning(msg("server.py.150"), project_id, len(warnings))
                else:
                    log.info(msg("server.py.151"), project_id)
                continue

            # No tombstone means the delete never committed. Restore every path from
            # the durable plan and keep the project active.
            try:
                self._restore_staged_paths(planned)
                self._drop_project_delete_transaction(project_id)
                log.warning(msg("server.py.152"), project_id)
            except Exception:  # noqa: BLE001
                log.exception(msg("server.py.153"), project_id)

    def _exclude_tombstoned_projects_from_disk(self) -> set[str]:
        """Never warm-load a project once its Harness tombstone exists.

        This is the final logical fence.  Even if a secondary projects.json rewrite
        failed after commit, the deleted project cannot reappear on restart.
        """
        deleted = self._deleted_project_ids()
        if not deleted:
            return deleted
        for project_id in sorted(deleted):
            # Always try the persisted project index as well; an old partial delete may
            # have removed every side ledger while leaving only projects.json/events.
            warnings = self._cleanup_deleted_project_metadata(project_id)
            if warnings:
                row = self._load_project_delete_transactions().get(project_id, {
                    "version": 1,
                    "project_id": project_id,
                    "project_name": project_id,
                    "transaction_id": f"legacy-{_safe_name(project_id)}",
                    "stage": "cleanup_pending",
                    "paths": [],
                    "created_at": now_ts(),
                })
                try:
                    self._remember_project_delete_cleanup(row, warnings)
                except Exception:  # noqa: BLE001
                    log.exception(msg("server.py.154"), project_id)
        return deleted

    async def _emit_project_delete_progress(
        self,
        operation_id: str,
        project_id: str,
        phase: str,
        started_at: float,
    ) -> None:
        messages = {
            "closing": msg("server.py.155"),
            "staging": msg("server.py.156"),
            "committing": msg("server.py.157"),
            "cleanup": msg("server.py.158"),
        }
        if phase not in messages:
            return
        try:
            await self.hub.emit_no_seq({
                "type": "project_delete_progress",
                "operation_id": operation_id,
                "project_id": project_id,
                "phase": phase,
                "message": messages[phase],
                "elapsed_ms": max(0, int((asyncio.get_running_loop().time() - started_at) * 1000)),
            })
        except Exception:  # progress is never part of delete correctness
            log.debug(msg("server.py.159"), project_id, exc_info=True)

    def _track_purge_task(self, task: asyncio.Task[Any]) -> None:
        self._purge_tasks.add(task)

        def done(completed: asyncio.Task[Any]) -> None:
            self._purge_tasks.discard(completed)
            if completed.cancelled():
                return
            try:
                completed.result()
            except Exception:
                log.exception(msg("server.py.160"))

        task.add_done_callback(done)

    async def _run_project_internal_io(
        self,
        project_id: str,
        operation: Callable[[], Any],
        *,
        label: str,
    ) -> Any:
        """Run one internal-workspace reader and keep its real thread lifetime visible.

        Cancelling a task that directly awaits ``asyncio.to_thread`` does not stop a
        thread that has already entered ``os.scandir``/``open``.  Shield the owned task
        and remove it from the registry only after the thread actually returns.  The
        metadata lock makes registration mutually exclusive with the delete fence.
        """

        async with self._project_metadata_lock:
            if project_id in self._closing_projects or project_id in self._deleted_project_ids():
                raise ProjectClosingError(project_id)
            task = asyncio.create_task(
                asyncio.to_thread(operation),
                name=f"project-io:{_safe_name(project_id)}:{label}",
            )
            self._project_internal_io_tasks.setdefault(project_id, set()).add(task)

        def done(completed: asyncio.Task[Any]) -> None:
            bucket = self._project_internal_io_tasks.get(project_id)
            if bucket is not None:
                bucket.discard(completed)
                if not bucket:
                    self._project_internal_io_tasks.pop(project_id, None)
            # A cancelled HTTP request no longer awaits this task.  Consume any late
            # exception after the real worker thread finishes to avoid an orphan warning.
            if not completed.cancelled():
                with contextlib.suppress(Exception):
                    completed.result()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _drain_project_internal_io(self, project_id: str) -> None:
        """Wait until every already-registered internal reader has left its thread."""

        while True:
            tasks = tuple(
                task
                for task in self._project_internal_io_tasks.get(project_id, ())
                if not task.done()
            )
            if not tasks:
                return
            await asyncio.gather(
                *(asyncio.shield(task) for task in tasks),
                return_exceptions=True,
            )

    async def _purge_committed_project_delete(
        self,
        project_id: str,
        operation_id: str,
        started_at: float,
        tx: dict[str, Any],
        planned: list[tuple[Path, Path]],
        warnings: list[str],
    ) -> None:
        await self._emit_project_delete_progress(
            operation_id, project_id, "cleanup", started_at,
        )
        pending = list(dict.fromkeys(warnings))
        purge_task = asyncio.create_task(
            asyncio.to_thread(self._purge_staged_paths, planned),
            name=f"project-purge-files:{project_id}",
        )
        cancelled: asyncio.CancelledError | None = None
        purge_error: Exception | None = None
        try:
            await asyncio.shield(purge_task)
        except asyncio.CancelledError as exc:
            # A running thread cannot be cancelled.  Keep this coroutine alive until
            # it really leaves the staged tree so the SQLite fence's done-callback is
            # not released early.
            cancelled = exc
            try:
                await purge_task
            except Exception as inner_exc:  # noqa: BLE001
                purge_error = inner_exc
        except Exception as exc:  # committed deletion is never rolled back
            purge_error = exc
        if purge_error is not None:
            detail = self._short_delete_error(purge_error)
            pending.append(msg("server.py.324", detail=detail))
            log.error(
                "[%s] 暂存项目目录物理清除失败",
                project_id,
                exc_info=(
                    purge_error.__class__,
                    purge_error,
                    purge_error.__traceback__,
                ),
            )
        pending = list(dict.fromkeys(pending))
        if pending:
            self._remember_project_delete_cleanup(tx, pending)
        else:
            self._drop_project_delete_transaction(project_id)
        if cancelled is not None:
            raise cancelled

    async def _delete_project_permanently(
        self,
        project_id: str,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """Permanently delete one internal project root with a bounded pre-commit path."""
        if project_id == PLATFORM_PROJECT_ID:
            raise ValueError(msg("server.py.161"))
        operation_id = operation_id or f"del_{uuid.uuid4().hex[:12]}"
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        deadline = started_at + DELETE_PRECOMMIT_TIMEOUT_S
        timings_ms: dict[str, int] = {}
        warnings: list[str] = []
        resource_close_issues: list[str] = []
        planned: list[tuple[Path, Path]] = []
        internal_root: Path | None = None
        sqlite_quiescence_active = False
        tx: dict[str, Any] = {}
        eng: ProjectEngine | None = None
        old_history: list[dict[str, str]] = []
        old_pending: list[tuple[str, str, dict[str, Any]]] = []
        had_engine = False
        engine_stop_safe = False
        project_name = project_id
        committed = False
        cancelled: asyncio.CancelledError | None = None
        stage = "closing"

        def remaining() -> float:
            left = deadline - loop.time()
            if left <= 0:
                raise TimeoutError(msg("server.py.162"))
            return left

        # Short critical section: establish the activation fence and durable plan only.
        try:
            await asyncio.wait_for(self._project_metadata_lock.acquire(), timeout=remaining())
            try:
                if project_id in self._closing_projects:
                    raise ProjectClosingError(project_id)
                proj = self.hub.projects.get(project_id)
                if proj is None:
                    raise ValueError(msg("server.py.094"))
                project_name = proj.name
                self._closing_projects.add(project_id)

                restart = self._restart_tasks.pop(project_id, None)
                if restart is not None:
                    restart.cancel()

                eng = self.engines.pop(project_id, None)
                had_engine = eng is not None
                engine_stop_safe = eng is None
                old_history = list(eng.history) if eng is not None else []
                try:
                    old_pending = eng.gate.snapshot_pending() if eng is not None else []
                except Exception:
                    old_pending = []

                internal = self._delete_path_candidate(
                    self._internal_workspace_for(project_id), internal=True,
                )
                internal_root = internal
                paths = [path for path in (internal,) if path is not None]
                self._assert_project_delete_paths_isolated(project_id, paths)
                transaction_id = f"{_safe_name(project_id)}-{uuid.uuid4().hex[:12]}"
                planned = self._plan_project_delete_paths(paths, transaction_id)
                if len(planned) > 1:
                    raise ValueError(msg("server.py.163"))
                if any(
                    not self._is_internal_delete_ledger_path(str(path))
                    for pair in planned for path in pair
                ):
                    raise ValueError(msg("server.py.164"))
                tx = {
                    "version": 1,
                    "transaction_id": transaction_id,
                    "project_id": project_id,
                    "project_name": project_name,
                    "stage": "prepared",
                    "created_at": now_ts(),
                    "paths": [
                        {"original": str(original), "staged": str(staged)}
                        for original, staged in planned
                    ],
                    "warnings": [],
                }
                self._put_project_delete_transaction(tx)
            except Exception:
                self._closing_projects.discard(project_id)
                if eng is not None:
                    self.engines[project_id] = eng
                raise
            finally:
                self._project_metadata_lock.release()

            await self._emit_project_delete_progress(
                operation_id, project_id, "closing", started_at,
            )
            phase_started = loop.time()
            self.hub.clear_public_text_filter(project_id)
            if eng is not None:
                close_timeout = min(DELETE_ENGINE_STOP_TIMEOUT_S, remaining())
                stop_waiter = asyncio.create_task(
                    eng.stop(immediate=True),
                    name=f"delete-stop:{project_id}",
                )
                try:
                    close_report = await asyncio.wait_for(
                        asyncio.shield(stop_waiter), timeout=close_timeout,
                    )
                except asyncio.TimeoutError:
                    resource_close_issues.append(
                        msg("server.py.165", **{"close_timeout:.1f": f"{close_timeout:.1f}"})
                    )

                    def consume_stop_result(done: asyncio.Task[list[str]]) -> None:
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            done.result()

                    stop_waiter.add_done_callback(consume_stop_result)
                    raise TimeoutError(msg("server.py.166"))
                resource_close_issues.extend(str(item) for item in (close_report or []))
                engine_stop_safe = True

            # HTTP cancellation does not stop an already-running ``to_thread`` scan.
            # Because the closing flag was installed under the same metadata lock used
            # for registration, this drain is a closed set: no new internal reader can
            # enter after it returns.
            try:
                await asyncio.wait_for(
                    self._drain_project_internal_io(project_id),
                    timeout=remaining(),
                )
            except asyncio.TimeoutError as exc:
                resource_close_issues.append(msg("server.py.167"))
                raise TimeoutError(msg("server.py.168")) from exc

            # Fence the original root before staging.  The storage registry closes any
            # project-local SQLite wrapper not reachable from ``ProjectEngine`` and the
            # fence rejects delayed callbacks that try to reopen/recreate the path.
            if internal_root is not None:
                sqlite_quiescence_active = True
                sqlite_report = quiesce_sqlite_databases_under(internal_root)
                if sqlite_report.closed:
                    log.warning(
                        msg("server.py.169"),
                        project_id,
                        sqlite_report.closed,
                    )
                sqlite_issues = [
                    *(msg("server.py.170", item=item) for item in sqlite_report.errors),
                ]
                if sqlite_report.remaining:
                    sqlite_issues.append(
                        msg("server.py.171") + "、".join(sqlite_report.remaining)
                    )
                if sqlite_issues:
                    raise ProjectResourceCloseError(project_id, sqlite_issues)
            timings_ms["closing"] = int((loop.time() - phase_started) * 1000)

            stage = "staging"
            await self._emit_project_delete_progress(
                operation_id, project_id, "staging", started_at,
            )
            phase_started = loop.time()
            outcomes = await asyncio.wait_for(
                asyncio.to_thread(
                    self._stage_planned_project_paths,
                    planned,
                    tuple(resource_close_issues),
                ),
                timeout=remaining(),
            )
            timings_ms["staging"] = int((loop.time() - phase_started) * 1000)
            tx["stage"] = "staged"
            tx["staging"] = outcomes
            if resource_close_issues:
                tx["resource_close_issues"] = list(resource_close_issues)
            self._put_project_delete_transaction(tx)

            stage = "committing"
            await self._emit_project_delete_progress(
                operation_id, project_id, "committing", started_at,
            )
            phase_started = loop.time()
            record_ok = await asyncio.wait_for(
                self.memory.record_deleted_project(project_id, project_name),
                timeout=remaining(),
            )
            tombstone_committed = project_id in self._deleted_project_ids()
            if not record_ok and not tombstone_committed:
                raise OSError(msg("server.py.172"))
            committed = True
            timings_ms["committing"] = int((loop.time() - phase_started) * 1000)
            if not record_ok:
                warnings.append(msg("server.py.173"))
            tx["stage"] = "committed"
            try:
                self._put_project_delete_transaction(tx)
            except Exception as exc:
                warnings.append(msg("server.py.174", **{"self._short_delete_error(exc)": self._short_delete_error(exc)}))
                log.exception(msg("server.py.175"), project_id)

        except (Exception, asyncio.CancelledError) as exc:
            cancelled = exc if isinstance(exc, asyncio.CancelledError) else None
            # The durable tombstone is the only commit truth.  A timeout/cancellation may land
            # after its atomic write but before ``record_deleted_project`` returns; re-read it
            # before deciding to restore the staged root.
            if not committed and project_id in self._deleted_project_ids():
                committed = True
                tx["stage"] = "committed"
                warnings.append(
                    msg("server.py.176")
                )
            if committed:
                warnings.append(msg("server.py.177", stage=stage, **{"self._short_delete_error(exc)": self._short_delete_error(exc)}))
            else:
                rollback_errors: list[str] = []
                paths_restored = True
                try:
                    await asyncio.to_thread(self._restore_staged_paths, planned)
                except Exception as rollback_exc:
                    paths_restored = False
                    rollback_errors.append(msg("server.py.178", **{"self._short_delete_error(rollback_exc)": self._short_delete_error(rollback_exc)}))
                    log.exception(msg("server.py.179"), project_id)

                try:
                    self._drop_project_delete_transaction(project_id)
                except Exception as rollback_exc:
                    rollback_errors.append(msg("server.py.180", **{"self._short_delete_error(rollback_exc)": self._short_delete_error(rollback_exc)}))

                # A restored original root may be opened again by the replacement
                # Engine.  Release the SQLite fence only after the directory rollback
                # has completed, and always before ``engine_for`` below.
                if sqlite_quiescence_active and paths_restored and internal_root is not None:
                    release_sqlite_quiescence(internal_root)
                    sqlite_quiescence_active = False

                # A timed-out or critically failed stop may still own SQLite/file handles.
                # Directory rollback can be complete while engine rollback is not; keep the
                # activation fence in that case and let the existing startup recovery restore
                # normal service on the next process launch.
                rollback_ok = not rollback_errors and engine_stop_safe
                async with self._project_metadata_lock:
                    if rollback_ok:
                        self._closing_projects.discard(project_id)
                    else:
                        self._closing_projects.add(project_id)
                        if eng is not None:
                            self.engines.setdefault(project_id, eng)

                if rollback_ok and had_engine and project_id in self.hub.projects:
                    try:
                        self._roster_restored.discard(project_id)
                        restored = self.engine_for(project_id)
                        restored.history = old_history
                        if old_pending:
                            await restored.recover(old_pending)
                    except Exception as rollback_exc:
                        rollback_ok = False
                        rollback_errors.append(
                            msg("server.py.181", **{"self._short_delete_error(rollback_exc)": self._short_delete_error(rollback_exc)})
                        )
                        self._closing_projects.add(project_id)
                        log.exception(msg("server.py.182"), project_id)

                detail = self._short_delete_error(exc, limit=1200)
                if rollback_errors:
                    detail = f"{detail}；" + "；".join(rollback_errors)
                blocked_path: str | None = None
                locking_processes: list[dict[str, Any]] = []
                close_report = list(resource_close_issues)
                if isinstance(exc, ProjectResourceCloseError):
                    close_report = list(dict.fromkeys([
                        *close_report,
                        *exc.issues,
                    ]))
                self_lock = isinstance(exc, ProjectResourceCloseError) or not engine_stop_safe
                if isinstance(exc, DeletePathBusyError):
                    blocked_path = str(exc.path)
                    locking_processes = [item.as_dict() for item in exc.locking_processes]
                    self_lock = self_lock or any(
                        bool(item.get("current_process")) for item in locking_processes
                    )
                    close_report = list(dict.fromkeys([
                        *close_report,
                        *exc.resource_close_issues,
                    ]))
                if cancelled is not None:
                    # Preserve task cancellation after restoring every pre-commit side effect.
                    raise cancelled
                raise ProjectDeleteError(
                    stage,
                    detail,
                    rollback_ok=rollback_ok,
                    blocked_path=blocked_path,
                    locking_processes=locking_processes,
                    resource_close_issues=close_report,
                    self_lock=self_lock,
                ) from exc

        # The tombstone is the commit point.  All remaining work is idempotent and may retry.
        # Keep the SQLite fence until the staged tree has been purged; otherwise a late
        # callback holding only the old path could recreate the original root between
        # commit and cleanup.
        try:
            cleanup_warnings = self._cleanup_deleted_project_metadata(project_id)
            warnings.extend(cleanup_warnings)
            self._closing_projects.discard(project_id)
            warnings = list(dict.fromkeys(warnings))
            cleanup_quiescence_root = (
                internal_root if sqlite_quiescence_active else None
            )
            cleanup_task = asyncio.create_task(
                self._purge_committed_project_delete(
                    project_id,
                    operation_id,
                    started_at,
                    tx,
                    planned,
                    warnings,
                ),
                name=f"project-purge:{project_id}",
            )
            if cleanup_quiescence_root is not None:
                cleanup_task.add_done_callback(
                    lambda _task, root=cleanup_quiescence_root: release_sqlite_quiescence(root)
                )
                sqlite_quiescence_active = False  # ownership moved to cleanup_task
            self._track_purge_task(cleanup_task)
            asyncio.create_task(self._update_harness_now())
            log.warning(msg("server.py.183"), project_id, project_name)
            if cancelled is not None:
                # The request task may disappear, but the committed delete and tracked cleanup must
                # remain authoritative.  Re-propagate cancellation only after scheduling cleanup.
                raise cancelled
            return {
                "operation_id": operation_id,
                "project_id": project_id,
                "project_name": project_name,
                "deleted": True,
                "cleanup_pending": not cleanup_task.done(),
                "warnings": warnings,
                "timings_ms": timings_ms,
            }
        except BaseException:
            if sqlite_quiescence_active and internal_root is not None:
                release_sqlite_quiescence(internal_root)
                sqlite_quiescence_active = False
            raise

    async def _resume_committed_project_delete(
        self,
        project_id: str,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """Return success for a committed retry and resume idempotent cleanup."""
        operation_id = operation_id or f"del_{uuid.uuid4().hex[:12]}"
        started_at = asyncio.get_running_loop().time()
        rows = self._load_project_delete_transactions()
        tx = rows.get(project_id, {
            "version": 1,
            "transaction_id": f"retry-{_safe_name(project_id)}",
            "project_id": project_id,
            "project_name": project_id,
            "stage": "cleanup_pending",
            "created_at": now_ts(),
            "paths": [],
            "warnings": [],
        })
        proj = self.hub.projects.get(project_id)
        project_name = str(tx.get("project_name") or (proj.name if proj else project_id))
        self._closing_projects.add(project_id)
        restart = self._restart_tasks.pop(project_id, None)
        if restart is not None:
            restart.cancel()
        eng = self.engines.pop(project_id, None)
        warnings: list[str] = []
        self.hub.clear_public_text_filter(project_id)
        warnings.extend(self._cleanup_deleted_project_metadata(project_id))
        self._closing_projects.discard(project_id)
        planned = self._transaction_path_pairs(tx)

        async def finish_committed_cleanup() -> None:
            if eng is not None:
                try:
                    close_report = await eng.stop(immediate=True)
                    warnings.extend(str(item) for item in (close_report or []))
                except Exception as exc:
                    warnings.append(
                        msg("server.py.291", **{"self._short_delete_error(exc)": self._short_delete_error(exc)})
                    )
                    log.exception("[%s] 重试删除时关闭引擎失败", project_id)
            await self._purge_committed_project_delete(
                project_id, operation_id, started_at, tx, planned, warnings,
            )

        task = asyncio.create_task(
            finish_committed_cleanup(),
            name=f"project-purge:{project_id}",
        )
        self._track_purge_task(task)
        asyncio.create_task(self._update_harness_now())
        return {
            "operation_id": operation_id,
            "project_id": project_id,
            "project_name": project_name,
            "deleted": True,
            "already_deleted": True,
            "cleanup_pending": not task.done(),
            "warnings": list(dict.fromkeys(warnings)),
            "timings_ms": {},
        }

    async def _delete_agent_permanently(
        self, project_id: str, agent_id: str,
    ) -> dict[str, Any]:
        """永久删除单个 Agent；项目继续存在，Project Memory 保留身份墓碑。"""
        warnings: list[str] = []
        async with self._project_metadata_lock:
            proj = self.hub.projects.get(project_id)
            if proj is None:
                raise ValueError(msg("server.py.094"))
            eng = self.engines.get(project_id)
            full = self.store.load_roster_full(project_id) if self.store is not None else {}
            stored = full.get(agent_id) or {}
            if stored.get("status") == "deleted":
                raise ValueError(msg("server.py.184"))

            role = (
                msg("server.py.066") if agent_id == COORDINATOR
                else str(stored.get("role") or (eng.roster().get(agent_id) if eng else "") or msg("server.py.067"))
            )
            name = str(
                stored.get("name")
                or (eng.member_name(agent_id) if eng is not None else "")
                or role
            )
            internal = eng.internal_workspace if eng is not None \
                else self._internal_workspace_for(project_id)

            if not await self.memory.record_deleted_agent(internal, agent_id, name, role):
                raise OSError(msg("server.py.186"))

            if agent_id == COORDINATOR:
                # 项目经理共享项目主循环；删除他的身份时停整台引擎，下一次项目活动会按
                # 剩余花名册重建一位全新的固定技术角色，不复用旧会话。
                old = self.engines.pop(project_id, None)
                if old is not None:
                    try:
                        await old.stop(immediate=True)
                    except Exception:  # noqa: BLE001
                        warnings.append(msg("server.py.187"))
                        log.exception(msg("server.py.188"), project_id)
                self.hub.clear_public_text_filter(project_id)
                self._roster_restored.discard(project_id)
                # 隔离项目保存的是整台旧项目经理的短期消息史；身份被彻底删除后，未来
                # 重建的项目经理不能继承这份个人上下文。
                self._paused_histories.pop(project_id, None)
            elif eng is not None:
                try:
                    await eng.delete_agent_permanently(agent_id)
                except Exception:  # noqa: BLE001
                    # Project Memory 墓碑已经提交，继续清持久真源；否则一次 Profile
                    # unlink 失败会把花名册和聊天事件完整留住，形成“已删除但仍可复活”。
                    warnings.append(msg("server.py.189"))
                    log.exception(msg("server.py.190"), project_id, agent_id)
            else:
                profile = self._delete_path_candidate(
                    Path(internal) / "agents" / _safe_name(agent_id), internal=True,
                )
                if profile is not None:
                    try:
                        staged = await asyncio.to_thread(
                            self._stage_delete_paths, [profile],
                            f"{_safe_name(agent_id)}-{uuid.uuid4().hex[:8]}",
                        )
                        await asyncio.to_thread(self._purge_staged_paths, staged)
                    except Exception:  # noqa: BLE001
                        warnings.append(msg("server.py.191"))
                        log.exception(msg("server.py.192"), project_id, agent_id)

            dm_id = f"dm:{project_id}:{agent_id}"
            removed_events = 0
            if self.store is not None:
                # [v1.0.24.4] 下面是「读回校验 + 补写」协议，读到的必须是定稿：
                # 先排空持久化队列，再同步动手。同步段占着主循环期间没有新提交，
                # 不会和队列里的写打架。
                self.store.flush(timeout=30)
                try:
                    removed_events = self.store.purge_agent_events(project_id, agent_id)
                except Exception:  # noqa: BLE001
                    warnings.append(msg("server.py.193"))
                    log.exception(msg("server.py.194"), project_id, agent_id)
                try:
                    self.store.delete_conversation(dm_id)
                except Exception:  # noqa: BLE001
                    warnings.append(msg("server.py.195"))
                    log.exception(msg("server.py.196"), project_id, agent_id)
                identity_error: Exception | None = None
                try:
                    self.store.delete_agent(
                        project_id, agent_id, reserve_id=(agent_id != COORDINATOR),
                    )
                except Exception as exc:  # noqa: BLE001
                    identity_error = exc
                    log.exception(msg("server.py.197"), project_id, agent_id)

                if agent_id != COORDINATOR:
                    # 无论前一步是“旧项目根本没 roster 行”，还是重写途中抛错，都再以
                    # 最终真源核验一次。只要 deleted 技术墓碑没落稳，就独立补写；这样
                    # 单次 I/O 故障不会悄悄放开旧 id，下一次自动分配也不会撞回旧身份。
                    try:
                        row = self.store.load_roster_full(project_id).get(agent_id)
                        if not row or row.get("status") != "deleted":
                            self.store.upsert_agent(
                                project_id, agent_id, "", status="deleted", name=None,
                            )
                        row = self.store.load_roster_full(project_id).get(agent_id)
                        if not row or row.get("status") != "deleted":
                            raise OSError(msg("server.py.198"))
                        identity_error = None
                    except Exception as exc:  # noqa: BLE001
                        identity_error = exc
                        log.exception(msg("server.py.199"), project_id, agent_id)

                if identity_error is not None:
                    warnings.append(msg("server.py.200"))
            try:
                self.hub.remove_project(dm_id)
            except Exception:  # noqa: BLE001
                warnings.append(msg("server.py.201"))
                log.exception(msg("server.py.202"), project_id, agent_id)

            # 运行中 Hub ring 也从持久真源重建，确保刷新前不会再回放已删身份。
            if self.store is not None and project_id in self.hub.projects:
                try:
                    remaining = self.store.load_roster_full(project_id)
                    public_names = {
                        aid: row.get("name") or legacy_display_name(aid, row.get("role", msg("server.py.067")))
                        for aid, row in remaining.items()
                        if row.get("status", "active") == "active"
                    }
                    events = sanitize_events(
                        _history_only(self.store.load_all_events(project_id)), public_names,
                    )
                    previous = self.hub.projects[project_id]
                    last_read = previous.last_read_seq
                    rebuilt = self.hub.restore(
                        project_id, previous.name, events,
                        self.store.load_seq_watermark(project_id),
                    )
                    rebuilt.last_read_seq = min(last_read, rebuilt.seq)
                    rebuilt.members = self._members_of(project_id)
                except Exception:  # noqa: BLE001
                    warnings.append(msg("server.py.203"))
                    log.exception(msg("server.py.204"), project_id)

            seen = self.announced.get(project_id)
            if seen is not None:
                seen.discard(agent_id)
            self._save_announced()

        await self._update_harness_now()
        log.warning(msg("server.py.205"), project_id, name, agent_id)
        return {
            "project_id": project_id,
            "project_name": proj.name,
            "agent_id": agent_id,
            "agent_name": name,
            "role": role,
            "removed_events": removed_events,
            "deleted": True,
            "warnings": warnings,
        }

    async def _serve_project_permanent_delete(
        self, writer: asyncio.StreamWriter, body_bytes: bytes,
    ) -> None:
        try:
            payload = self._decode_json_object(body_bytes)
            raw_operation_id = payload.get("operation_id")
            operation_id = (
                raw_operation_id.strip()
                if isinstance(raw_operation_id, str)
                and re.fullmatch(r"del_[A-Za-z0-9_-]{6,80}", raw_operation_id.strip())
                else f"del_{uuid.uuid4().hex[:12]}"
            )
            project_id, requested_project_id, already_deleted = self._project_delete_target(payload)
            result = (
                await self._resume_committed_project_delete(project_id, operation_id)
                if already_deleted
                else await self._delete_project_permanently(project_id, operation_id)
            )
            result["request_project_id"] = requested_project_id
        except ValueError as exc:
            self._preview_error(writer, b"400 Bad Request", str(exc))
            return
        except ProjectDeleteError as exc:
            rollback = msg("s.206a") if exc.rollback_ok else msg("s.206b")
            message = (
                msg("server.py.207")
                if exc.self_lock
                else msg("server.py.208", **{"exc.stage": exc.stage}, **{"exc.detail": exc.detail}, rollback=rollback)
            )
            log.exception(msg("server.py.209"), exc.stage)
            self._preview_error(
                writer,
                b"500 Internal Server Error",
                message,
                code="project_delete_stage_failed",
                operation_id=operation_id,
                project_id=locals().get("project_id"),
                stage=exc.stage,
                rollback_ok=exc.rollback_ok,
                self_lock=exc.self_lock,
                blocked_path=exc.blocked_path,
                locking_processes=exc.locking_processes or None,
                resource_close_issues=exc.resource_close_issues or None,
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.exception(msg("server.py.210"))
            self._preview_error(
                writer,
                b"500 Internal Server Error",
                msg("server.py.211", **{"self._short_delete_error(exc)": self._short_delete_error(exc)}),
                code="project_delete_unexpected",
                stage=msg("server.py.212"),
            )
            return
        body = json.dumps({"ok": True, **result}, ensure_ascii=False).encode("utf-8")
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")

    async def _serve_agent_permanent_delete(
        self, writer: asyncio.StreamWriter, body_bytes: bytes,
    ) -> None:
        try:
            payload = self._decode_json_object(body_bytes)
            project_id = self._menu_project_id(payload)
            agent_id = self._menu_agent_id(payload, project_id)
            result = await self._delete_agent_permanently(project_id, agent_id)
        except ValueError as exc:
            self._preview_error(writer, b"400 Bad Request", str(exc))
            return
        except Exception:  # noqa: BLE001
            log.exception(msg("server.py.213"))
            self._preview_error(writer, b"500 Internal Server Error", msg("server.py.213"))
            return
        body = json.dumps({"ok": True, **result}, ensure_ascii=False).encode("utf-8")
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")

    async def _serve_project_menu_state(self, writer: asyncio.StreamWriter) -> None:
        body = json.dumps(
            {"ok": True, "version": 1, "projects": self._conversation_state_rows()},
            ensure_ascii=False,
        ).encode("utf-8")
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")

    async def _serve_project_state_change(
        self, writer: asyncio.StreamWriter, body_bytes: bytes, field: str,
    ) -> None:
        try:
            payload = self._decode_json_object(body_bytes)
            project_id = self._menu_project_id(payload)
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError(msg("server.py.214"))
            row = await self._set_conversation_state(project_id, field, enabled)
        except ValueError as exc:
            self._preview_error(writer, b"400 Bad Request", str(exc))
            return
        except Exception:  # noqa: BLE001 — 不把内部异常细节暴露给渲染进程
            log.exception(msg("server.py.215"), field)
            self._preview_error(writer, b"500 Internal Server Error", msg("server.py.216"))
            return

        body = json.dumps(
            {
                "ok": True,
                "project_id": project_id,
                "project_name": self.hub.projects[project_id].name,
                **row,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")

    async def _serve_project_rename(
        self, writer: asyncio.StreamWriter, body_bytes: bytes,
    ) -> None:
        try:
            payload = self._decode_json_object(body_bytes)
            project_id = self._menu_project_id(payload)
            result = await self._rename_project(project_id, payload.get("project_name"))
        except ValueError as exc:
            self._preview_error(writer, b"400 Bad Request", str(exc))
            return
        except Exception:  # noqa: BLE001
            log.exception(msg("server.py.217"))
            self._preview_error(writer, b"500 Internal Server Error", msg("server.py.218"))
            return

        body = json.dumps({"ok": True, **result}, ensure_ascii=False).encode("utf-8")
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")

    # ── [v0.44 设置] POST /settings：整包应用 ──────────────────────

    async def _serve_settings_apply(self, writer: asyncio.StreamWriter,
                                    body_bytes: bytes) -> None:
        try:
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
            if not isinstance(payload, dict):
                raise ValueError(msg("server.py.219"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._preview_error(writer, b"400 Bad Request", msg("server.py.220", exc=exc))
            return

        def _norm_pid(pid: str) -> str:
            """群级键归一：临时 id → 权威 id；解析不了的原样保留（不丢用户数据）。"""
            try:
                return self._canonical_project_id_from_request(str(pid))
            except Exception:  # noqa: BLE001 — 含 ProjectIdResolutionError
                return str(pid)

        try:
            runtime_settings.apply(payload, canonical_pid=_norm_pid)
        except runtime_settings.SettingsApplyConflict as exc:
            body = json.dumps(
                {
                    "ok": False,
                    "error": "settings_conflict",
                    "message": str(exc),
                    **runtime_settings.model_apply_ack(welcome_state=self._welcome_state()),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self._write_http(writer, b"409 Conflict", body, b"application/json; charset=utf-8")
            return
        except Exception as exc:  # noqa: BLE001 — persistence failure is a real failed save
            log.exception("设置持久化失败；权威运行时状态保持不变")
            detail = " ".join(str(exc).split())[:300] or exc.__class__.__name__
            body = json.dumps(
                {
                    "ok": False,
                    "error": "settings_persist_failed",
                    "message": msg("server.py.336", detail=detail),
                    **runtime_settings.model_apply_ack(welcome_state=self._welcome_state()),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self._write_http(
                writer, b"500 Internal Server Error", body,
                b"application/json; charset=utf-8",
            )
            return

        # 通知所有已加载引擎把存活 Agent 热换绑（新引擎/新 Agent 天然走新解析）。
        touched = 0
        restart_required = False
        for eng in list(self.engines.values()):
            try:
                touched += eng.apply_model_settings()
            except Exception:  # noqa: BLE001 — 热更新失败不许拖垮设置保存
                restart_required = True
                log.exception("设置热更新到引擎失败（忽略，重启该项目后生效）")
        self._settings_restart_required = restart_required

        welcome_state = await self.ensure_zinnia_welcome_after_binding()
        body = json.dumps(
            {
                "ok": True,
                "agents_hot_swapped": touched,
                "restart_required": restart_required,
                **runtime_settings.model_apply_ack(welcome_state=welcome_state),
                "feature_flags": feature_flag_snapshot(),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")

    # ── [v0.44 设置] POST /settings/test：连接测试（真发请求）─────────

    async def _serve_settings_test(self, writer: asyncio.StreamWriter,
                                   body_bytes: bytes) -> None:
        try:
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
            binding = payload.get("binding") if isinstance(payload, dict) else None
            target = str(payload.get("target") or "main").strip().lower() if isinstance(payload, dict) else "main"
            if target not in {"main", "aux"}:
                raise ValueError(msg("server.py.294"))
            if not isinstance(binding, dict):
                raise ValueError(msg("server.py.295"))
            binding = runtime_settings.binding_for_test(target, binding)
            if binding is None:
                raise ValueError(msg("server.py.296"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._preview_error(writer, b"400 Bad Request", msg("server.py.328", exc=exc))
            return

        # urllib 是同步阻塞——丢线程池，别把事件循环钉在网络等待上（最多 15s）。
        result = await asyncio.to_thread(runtime_settings.test_binding, binding)
        body = json.dumps(result, ensure_ascii=False).encode()
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")

    @staticmethod
    def _preview_param(params: dict[str, list[str]], key: str) -> str:
        return unquote((params.get(key) or [""])[0]).strip()

    @staticmethod
    def _preview_int(value: str) -> int | None:
        if not value:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _preview_timestamp(value: str) -> float | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _preview_file_id(st: os.stat_result) -> str:
        inode = int(getattr(st, "st_ino", 0) or 0)
        if inode <= 0:
            return ""
        return f"{int(getattr(st, 'st_dev', 0) or 0):x}:{inode:x}"

    @classmethod
    def _preview_identity_matches(
        cls,
        st: os.stat_result,
        *,
        file_id: str,
        expected_size: int | None,
        expected_mtime_ns: int | None,
        expected_mtime: float | None,
        allow_unidentified: bool,
    ) -> bool:
        """Whether ``st`` is the file represented by a historical chat card.

        A stable filesystem identity wins.  Legacy cards did not carry one, so they may
        fall back to the *pair* (size, mtime); size alone is deliberately insufficient.
        This keeps rename recovery deterministic and prevents opening an unrelated file
        which merely happens to have the same name.
        """
        if file_id:
            return cls._preview_file_id(st) == file_id
        if expected_size is None and expected_mtime_ns is None and expected_mtime is None:
            return allow_unidentified
        if expected_size is None or (expected_mtime_ns is None and expected_mtime is None):
            return False
        if int(st.st_size) != expected_size:
            return False
        if expected_mtime_ns is not None:
            actual_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
            return actual_ns == expected_mtime_ns
        assert expected_mtime is not None
        # Old events store an ISO timestamp derived from st_mtime.  A few filesystems
        # round to milliseconds, hence the tiny tolerance; rename does not alter mtime.
        return abs(float(st.st_mtime) - expected_mtime) <= 0.01

    def _preview_request(
        self, query: str,
    ) -> tuple[str, str, str, int | None, int | None, float | None]:
        params = parse_qs(query, keep_blank_values=False)
        project_id = self._preview_param(params, "project_id")
        rel_path = self._preview_param(params, "path")
        if not project_id or not rel_path:
            raise ValueError(msg("server.py.221"))
        return (
            project_id,
            rel_path,
            self._preview_param(params, "file_id"),
            self._preview_int(self._preview_param(params, "bytes")),
            self._preview_int(self._preview_param(params, "mtime_ns")),
            self._preview_timestamp(self._preview_param(params, "mtime")),
        )

    def _resolve_preview_target(
        self,
        project_id: str,
        rel_path: str,
        *,
        file_id: str = "",
        expected_size: int | None = None,
        expected_mtime_ns: int | None = None,
        expected_mtime: float | None = None,
    ) -> tuple[str, Path, dict[str, Any]]:
        """Resolve a file card to a current path, following only a same-directory rename.

        The search is intentionally one directory deep.  We never recurse through the
        project looking for a matching inode: once the file leaves its original parent,
        the UI must report that it is no longer at the original location.
        """
        canonical = self._canonical_project_id_from_request(project_id)
        eng = self.engines.get(canonical) or self.engines.get(project_id)
        if eng is None:
            raise FileNotFoundError(msg("server.py.222"))
        root = Path(eng.workspace_root)

        try:
            exact = resolve_in_sandbox(
                root, rel_path, role="worker", operation="read",
            )
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — workspace missing/unavailable
            raise FileNotFoundError(msg("server.py.223", exc=exc)) from exc

        exact_fallback: Path | None = None
        try:
            if exact.is_file():
                exact_stat = exact.stat()
                if self._preview_identity_matches(
                    exact_stat,
                    file_id=file_id,
                    expected_size=expected_size,
                    expected_mtime_ns=expected_mtime_ns,
                    expected_mtime=expected_mtime,
                    allow_unidentified=True,
                ):
                    return canonical, exact, self._preview_file_meta(
                        root, exact, source_path=rel_path,
                    )
                # An editor may replace a file atomically at the same pathname, changing
                # its inode.  Search for the old identity first (rename + replacement); if
                # it no longer exists anywhere in the parent, the current pathname remains
                # the least-surprising file-card meaning and is accepted below.
                exact_fallback = exact
        except OSError:
            pass

        # The old pathname is absent or now points at a different file.  Search only the
        # original direct parent and accept exactly one identity match.
        norm = rel_path.replace("\\", "/")
        parent_rel = norm.rsplit("/", 1)[0] if "/" in norm else "."
        try:
            parent = resolve_in_sandbox(
                root, parent_rel, role="worker", operation="read",
            )
            if not parent.is_dir():
                raise FileNotFoundError(msg("server.py.224"))
            children = list(parent.iterdir())
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise FileNotFoundError(msg("server.py.224")) from exc

        matches: list[Path] = []
        for child in children:
            try:
                candidate_rel = (
                    f"{parent_rel.rstrip('/')}/{child.name}"
                    if parent_rel not in {"", "."}
                    else child.name
                )
                candidate = resolve_in_sandbox(
                    root, candidate_rel, role="worker", operation="read",
                )
                if not candidate.is_file():
                    continue
                st = candidate.stat()
            except (OSError, ValueError):
                continue
            if self._preview_identity_matches(
                st,
                file_id=file_id,
                expected_size=expected_size,
                expected_mtime_ns=expected_mtime_ns,
                expected_mtime=expected_mtime,
                allow_unidentified=False,
            ):
                matches.append(candidate)

        if len(matches) > 1:
            raise FileNotFoundError(msg("server.py.224"))
        if len(matches) == 1:
            target = matches[0]
        elif exact_fallback is not None and not file_id:
            target = exact_fallback
        else:
            raise FileNotFoundError(msg("server.py.224"))
        return canonical, target, self._preview_file_meta(
            root, target, source_path=rel_path,
        )

    @classmethod
    def _preview_file_meta(
        cls, root: Path, target: Path, *, source_path: str,
    ) -> dict[str, Any]:
        st = target.stat()
        try:
            rel = target.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError) as exc:
            raise ValueError(msg("server.py.225")) from exc
        name = target.name
        dot = name.rfind(".")
        ext = name[dot + 1:].lower() if 0 <= dot < len(name) - 1 else ""
        meta: dict[str, Any] = {
            "source_path": source_path,
            "path": rel,
            "name": name,
            "ext": ext,
            "bytes": int(st.st_size),
            "mtime": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(),
            "mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
            "renamed": rel.replace("\\", "/") != source_path.replace("\\", "/"),
        }
        identity = cls._preview_file_id(st)
        if identity:
            meta["file_id"] = identity
        return meta

    async def _serve_preview_resolve(
        self, writer: asyncio.StreamWriter, query: str,
    ) -> None:
        """Resolve the current filename before a renderer requests the file bytes."""
        try:
            project_id, rel_path, file_id, size, mtime_ns, mtime = self._preview_request(query)
            canonical, _target, meta = self._resolve_preview_target(
                project_id,
                rel_path,
                file_id=file_id,
                expected_size=size,
                expected_mtime_ns=mtime_ns,
                expected_mtime=mtime,
            )
        except ProjectIdResolutionError as exc:
            self._preview_error(writer, b"404 Not Found", str(exc))
            return
        except ValueError as exc:
            self._preview_error(writer, b"403 Forbidden", str(exc))
            return
        except FileNotFoundError as exc:
            self._preview_error(writer, b"404 Not Found", str(exc))
            return
        except OSError as exc:
            self._preview_error(writer, b"404 Not Found", msg("server.py.229", exc=exc))
            return

        body = json.dumps(
            {"ok": True, "project_id": canonical, "file": meta},
            ensure_ascii=False,
        ).encode()
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")

    async def _serve_file_reveal(
        self, writer: asyncio.StreamWriter, body_bytes: bytes,
    ) -> None:
        """POST /files/reveal — reveal a sandboxed file in the native file manager."""
        try:
            payload = self._decode_json_object(body_bytes)
            project_id = str(payload.get("project_id") or "").strip()
            file_payload = payload.get("file")
            if not isinstance(file_payload, dict):
                raise ValueError(msg("server.py.226"))
            rel_path = str(
                file_payload.get("source_path") or file_payload.get("path") or ""
            ).strip()
            if not project_id or not rel_path:
                raise ValueError(msg("server.py.227"))
            file_id = str(file_payload.get("file_id") or "").strip()
            size = self._preview_int(str(file_payload.get("bytes") or ""))
            mtime_ns = self._preview_int(str(file_payload.get("mtime_ns") or ""))
            mtime = self._preview_timestamp(str(file_payload.get("mtime") or ""))
            canonical, target, meta = self._resolve_preview_target(
                project_id,
                rel_path,
                file_id=file_id,
                expected_size=size,
                expected_mtime_ns=mtime_ns,
                expected_mtime=mtime,
            )
            await asyncio.to_thread(_reveal_in_file_manager, target)
        except (ValueError, ProjectIdResolutionError) as exc:
            self._preview_error(writer, b"400 Bad Request", str(exc))
            return
        except FileNotFoundError as exc:
            self._preview_error(writer, b"404 Not Found", str(exc))
            return
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            self._preview_error(
                writer,
                b"500 Internal Server Error",
                msg("server.py.228", exc=exc),
            )
            return

        body = json.dumps(
            {"ok": True, "project_id": canonical, "file": meta},
            ensure_ascii=False,
        ).encode()
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")

    def _preview_tree_request(self, request_path: str) -> tuple[str, str]:
        """解析树形预览地址；只解码一次，路径安全仍交给统一沙箱解析器。"""
        prefix = "/preview/tree/"
        if not request_path.startswith(prefix):
            raise ValueError(msg("server.py.299"))
        encoded = request_path[len(prefix):]
        encoded_project, separator, encoded_path = encoded.partition("/")
        if not separator or not encoded_project or not encoded_path:
            raise ValueError(msg("server.py.221"))

        if (
            re.search(r"%(?![0-9A-Fa-f]{2})", encoded_project)
            or re.search(r"%(?![0-9A-Fa-f]{2})", encoded_path)
        ):
            raise ValueError(msg("server.py.300"))
        try:
            project_id = unquote(encoded_project, errors="strict").strip()
            rel_path = unquote(encoded_path, errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(msg("server.py.301")) from exc
        normalized = rel_path.replace("\\", "/")
        if (
            not project_id
            or not rel_path
            or "\x00" in project_id
            or "\x00" in rel_path
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
        ):
            raise ValueError(msg("server.py.302"))
        parts = normalized.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(msg("server.py.303"))
        return project_id, normalized

    def _preview_root_request(
        self,
        request_path: str,
        project_id: str,
    ) -> tuple[str, str]:
        """Resolve root-relative URLs on an isolated per-project origin."""

        encoded_path = request_path.lstrip("/") or "index.html"
        return self._preview_tree_request(
            f"/preview/tree/{quote(project_id, safe='')}/{encoded_path}",
        )

    async def _serve_preview_tree(
        self,
        writer: asyncio.StreamWriter,
        request_path: str,
        *,
        preview_token: str | None,
    ) -> None:
        """Serve project web resources only from an isolated per-project origin."""
        if preview_token is None:
            self._preview_error(
                writer,
                b"403 Forbidden",
                msg("server.py.304"),
                cors=False,
            )
            return
        try:
            if request_path.startswith("/preview/tree/"):
                project_id, rel_path = self._preview_tree_request(request_path)
            else:
                mapped_project = self._preview_origin_projects.get(preview_token)
                if not mapped_project:
                    raise ProjectIdResolutionError(msg("server.py.305"))
                project_id, rel_path = self._preview_root_request(request_path, mapped_project)

            canonical, target, _meta = self._resolve_preview_target(project_id, rel_path)
            expected_token = self._preview_origin_token(canonical)
            if preview_token is not None and preview_token != expected_token:
                raise ValueError(msg("server.py.306"))
            self._preview_origin_projects[expected_token] = canonical

            if target.is_dir():
                rel_path = f"{rel_path.rstrip('/')}/index.html"
                canonical, target, _meta = self._resolve_preview_target(canonical, rel_path)
            stat = target.stat()
        except ProjectIdResolutionError as exc:
            self._preview_error(writer, b"404 Not Found", str(exc), cors=False)
            return
        except ValueError as exc:
            self._preview_error(writer, b"403 Forbidden", str(exc), cors=False)
            return
        except FileNotFoundError as exc:
            self._preview_error(writer, b"404 Not Found", str(exc), cors=False)
            return
        except OSError as exc:
            self._preview_error(writer, b"404 Not Found", msg("server.py.229", exc=exc), cors=False)
            return

        size_now = int(stat.st_size)
        if size_now > self._PREVIEW_MAX_BYTES:
            self._preview_error(
                writer,
                b"413 Payload Too Large",
                msg("server.py.230", size_now=size_now),
                cors=False,
            )
            return
        try:
            data = await asyncio.to_thread(target.read_bytes)
        except OSError as exc:
            self._preview_error(
                writer,
                b"500 Internal Server Error",
                msg("server.py.231", exc=exc),
                cors=False,
            )
            return
        if len(data) > self._PREVIEW_MAX_BYTES:
            self._preview_error(
                writer,
                b"413 Payload Too Large",
                msg("server.py.325", **{"len(data)": len(data)}),
                cors=False,
            )
            return

        extra = (
            b"Content-Disposition: inline\r\n" +
            b"X-Content-Type-Options: nosniff\r\n" +
            b"Cross-Origin-Resource-Policy: same-origin\r\n" +
            b"Referrer-Policy: no-referrer\r\n" +
            b"Permissions-Policy: camera=(), microphone=(), geolocation=(), usb=(), serial=(), hid=(), payment=()\r\n" +
            b"Service-Worker-Allowed: /\r\n"
        )
        if target.suffix.lower() in {".html", ".htm"}:
            csp = " ".join((
                "default-src 'self' data: blob: http: https:;",
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: http: https:;",
                "style-src 'self' 'unsafe-inline' data: blob: http: https:;",
                "img-src 'self' data: blob: http: https:;",
                "font-src 'self' data: blob: http: https:;",
                "media-src 'self' data: blob: http: https:;",
                "connect-src 'self' data: blob: http: https: ws: wss:;",
                "worker-src 'self' blob:;",
                "child-src 'self' blob: http: https:;",
                "frame-src 'self' blob: http: https:;",
                "object-src 'none';",
                "form-action 'self' http: https:;",
                "base-uri 'self';",
            ))
            extra += f"Content-Security-Policy: {csp}\r\n".encode("ascii")

        self._write_http(
            writer,
            b"200 OK",
            data,
            _preview_content_type(target.name).encode(),
            extra=extra,
            cors=False,
        )

    async def _serve_preview(self, writer: asyncio.StreamWriter, query: str) -> None:
        """GET /preview → current file bytes, with same-directory rename recovery."""
        try:
            project_id, rel_path, file_id, size, mtime_ns, mtime = self._preview_request(query)
            _canonical, target, _meta = self._resolve_preview_target(
                project_id,
                rel_path,
                file_id=file_id,
                expected_size=size,
                expected_mtime_ns=mtime_ns,
                expected_mtime=mtime,
            )
            stat = target.stat()
        except ProjectIdResolutionError as exc:
            self._preview_error(writer, b"404 Not Found", str(exc))
            return
        except ValueError as exc:
            self._preview_error(writer, b"403 Forbidden", str(exc))
            return
        except FileNotFoundError as exc:
            self._preview_error(writer, b"404 Not Found", str(exc))
            return
        except OSError as exc:
            self._preview_error(writer, b"404 Not Found", msg("server.py.229", exc=exc))
            return

        size_now = int(stat.st_size)
        if size_now > self._PREVIEW_MAX_BYTES:
            self._preview_error(
                writer,
                b"413 Payload Too Large",
                msg("server.py.230", size_now=size_now),
            )
            return

        try:
            data = await asyncio.to_thread(target.read_bytes)
        except OSError as exc:
            self._preview_error(writer, b"500 Internal Server Error", msg("server.py.231", exc=exc))
            return

        ctype = _preview_content_type(target.name)
        self._write_http(
            writer,
            b"200 OK",
            data,
            ctype.encode(),
            extra=(
                b"Content-Disposition: inline\r\n" +
                b"X-Content-Type-Options: nosniff\r\n"
            ),
        )

    # ═══════════════════════════════════════════════════════════
    # [v0.38] /history —— 聊天记录（过滤 · 分页 · 按日期查）
    #
    #   数据源与温载同一条：store.load_all_events(pid) 过 _history_only()，
    #   再合并引擎内存里的 eng.history（按 seq 去重，内存优先——它更新）。
    #   只读、脱敏、不外泄沙箱路径；project_id 无效一律 400。
    # ═══════════════════════════════════════════════════════════
    async def _serve_history(self, writer: asyncio.StreamWriter, query: str) -> None:
        params = parse_qs(query, keep_blank_values=False)
        project_id = unquote((params.get("project_id") or [""])[0]).strip()
        category = (params.get("category") or ["all"])[0].strip().lower()
        if category not in ("all", "files", "images", "videos", "links"):
            category = "all"
        date_str = (params.get("date") or [""])[0].strip() or None
        try:
            page = max(1, int((params.get("page") or ["1"])[0]))
        except ValueError:
            page = 1
        try:
            page_size = int((params.get("page_size") or ["25"])[0])
        except ValueError:
            page_size = 25
        page_size = max(5, min(50, page_size))

        if not project_id:
            self._history_error(writer, b"400 Bad Request", msg("server.py.129"))
            return

        # [v0.38.3 修正 #1] 记录源 = 「当前窗口这条会话本身」，绝不拿别的顶替：
        #   · 群聊窗口 → 群项目历史（引擎内存 + 落盘）。
        #   · 私聊 Worker 窗口 dm:{group}:{agent} → **该私聊频道自己的历史**（落盘键就是 dm_id）
        #     + 该频道 Hub Ring 的实时视图；脱敏借群引擎的过滤器。
        #     不再拿群历史顶替，也不再按 agent 过滤——私聊窗口就只显示这条私聊的对话。
        dm = _parse_dm(project_id)
        if dm is not None:
            group_req = dm[0]
            try:
                group_id = self._canonical_project_id_from_request(group_req)
            except Exception:                   # noqa: BLE001
                group_id = group_req
            group_eng = self.engines.get(group_id) or self.engines.get(group_req)
            # 私聊频道的落盘历史（键 = dm_id 本身）
            try:
                raw = await asyncio.to_thread(self.store.load_all_events, project_id)
            except Exception:                   # noqa: BLE001
                raw = []
            events = _history_only(raw)
            # 合并私聊频道 Ring（最新、未淘汰的实时视图），按 seq 去重
            proj = self.hub.projects.get(project_id)
            if proj is not None:
                try:
                    ring_events, _gap = proj.ring.replay_since(0)
                except Exception:               # noqa: BLE001
                    ring_events = []
                if ring_events:
                    by_seq: dict[Any, dict[str, Any]] = {}
                    for e in events:
                        if isinstance(e, dict):
                            by_seq[e.get("seq")] = e
                    for e in _history_only(ring_events):
                        if isinstance(e, dict):
                            by_seq[e.get("seq")] = e
                    events = list(by_seq.values())
            sanitize = getattr(group_eng, "_sanitize_outbound", None)
        else:
            # 归一到规范 id（和 /preview 一样容忍前端临时 id）
            canonical = self._canonical_project_id_from_request(project_id)
            eng = self.engines.get(canonical) or self.engines.get(project_id)
            if eng is None:
                self._history_error(writer, b"404 Not Found", msg("server.py.222"))
                return
            pid = canonical if self.engines.get(canonical) else project_id

            # 读盘可能阻塞 → 丢线程池
            try:
                raw = await asyncio.to_thread(self.store.load_all_events, pid)
            except Exception:                   # noqa: BLE001
                raw = []
            events = _history_only(raw)

            # 合并内存历史（更新），按 seq 去重
            mem = getattr(eng, "history", None)
            if isinstance(mem, list) and mem:
                by_seq = {}
                for e in events:
                    if isinstance(e, dict):
                        by_seq[e.get("seq")] = e
                for e in mem:
                    if isinstance(e, dict):
                        by_seq[e.get("seq")] = e
                events = list(by_seq.values())
            sanitize = getattr(eng, "_sanitize_outbound", None)

        # 只留会出现在时间线上的消息：message（agent）+ user_echo（用户）
        events = [e for e in events if e.get("type") in ("message", "user_echo")]

        # 分类过滤
        if category != "all":
            events = [e for e in events if _history_match_category(e, category)]

        # 日期过滤
        if date_str:
            events = [e for e in events if _history_match_date(e, date_str)]

        # 按 seq 倒序（最新在前）
        events.sort(key=lambda e: (e.get("seq") is None, e.get("seq") or 0), reverse=True)

        total = len(events)
        start = (page - 1) * page_size
        page_events = events[start:start + page_size]

        # sanitize 已在上面按「群/私聊」两支各自取好（群引擎的脱敏过滤器）
        items = [_history_item(e, sanitize) for e in page_events]

        body = json.dumps(
            {"total": total, "page": page, "page_size": page_size, "items": items},
            ensure_ascii=False,
        ).encode("utf-8")
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")

    def _history_error(self, writer: asyncio.StreamWriter,
                       status: bytes, message: str) -> None:
        body = json.dumps(
            {"error": message, "total": 0, "page": 1, "page_size": 25, "items": []},
            ensure_ascii=False,
        ).encode("utf-8")
        self._write_http(writer, status, body, b"application/json; charset=utf-8")

    # ═══════════════════════════════════════════════════════════
    # [v0.38.6 #2] /history/handoffs —— 群聊「报告/交接」
    #
    #   直接列出项目 handoffs/ 目录下**所有** report / instruction 文件，
    #   每个文件独立一条、按文件创建时间倒序（最新在前），不再按序号配对。
    #   点击预览该文件 markdown（前端行为不变）。
    #
    #   ⚠ v0.38.5 扫的是「业务目录/handoff」（单数）——全扫空了。真正的交接文件在
    #   **引擎内部工作区 internal_workspace/handoffs（复数）** 下、按阶段分子目录
    #   （report-03-…​.md / instruction-…​.md）。这里直接问引擎要目录，最准。
    # ═══════════════════════════════════════════════════════════
    def _project_handoff_dirs(self, pid: str, project_id: str) -> list[Any]:
        """Authoritative internal handoff directory only; legacy reads are retired."""
        eng = self.engines.get(pid) or self.engines.get(project_id)
        dirs: list[Any] = []
        if eng is not None:
            try:
                dirs.append(eng.handoff_dir)          # internal_workspace/handoffs（复数）
            except Exception:                         # noqa: BLE001
                pass
        if not dirs:
            # 引擎没加载也兜底：直接拼内部工作区/handoffs
            try:
                dirs.append(self._internal_workspace_for(pid) / "handoffs")
            except Exception:                         # noqa: BLE001
                pass
        seen: set[str] = set()
        out: list[Any] = []
        for d in dirs:
            try:
                rp = str(d.resolve())
                if rp in seen:
                    continue
                seen.add(rp)
                if d.is_dir():
                    out.append(d)
            except Exception:                         # noqa: BLE001
                pass
        return out

    async def _serve_handoffs(self, writer: asyncio.StreamWriter, query: str) -> None:
        params = parse_qs(query, keep_blank_values=False)
        project_id = unquote((params.get("project_id") or [""])[0]).strip()
        if not project_id:
            self._history_error(writer, b"400 Bad Request", msg("server.py.129"))
            return
        try:
            canonical = self._canonical_project_id_from_request(project_id)
        except Exception:                       # noqa: BLE001
            canonical = project_id

        # 脱敏借项目引擎的过滤器；没有引擎也照列文件（只是不脱敏）
        eng = self.engines.get(canonical) or self.engines.get(project_id)
        sanitize = getattr(eng, "_sanitize_outbound", None)

        def collect() -> list[dict[str, Any]]:
            # 优先用引擎自己的 handoff_files()：它只读取权威 internal workspace。
            paths: list[Any] = []
            if eng is not None:
                try:
                    paths = [p for p in eng.handoff_files()]
                except Exception:                 # noqa: BLE001
                    paths = []
            dirs = self._project_handoff_dirs(canonical, project_id)
            if not paths:
                for d in dirs:
                    try:
                        for p in d.rglob("*"):
                            if p.is_file() and p.suffix.lower() in _HANDOFF_TEXT_EXTS:
                                paths.append(p)
                    except Exception:             # noqa: BLE001
                        pass
            base = dirs[0] if dirs else None
            return _build_handoff_items(paths, base, sanitize)

        try:
            items = await self._run_project_internal_io(
                canonical,
                collect,
                label="handoffs",
            )
        except ProjectClosingError as exc:
            self._history_error(writer, b"409 Conflict", str(exc))
            return
        body = json.dumps(
            {"total": len(items), "items": items}, ensure_ascii=False,
        ).encode("utf-8")
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")

    # ═══════════════════════════════════════════════════════════
    # [v1.0.23.6] 增量读取：GET /api/events?project_id=&after_seq=
    # ═══════════════════════════════════════════════════════════
    async def _serve_events_delta(self, writer: asyncio.StreamWriter, query: str) -> None:
        """
        增量读取接口（HTTP 旁路预热通道，不碰 WS 状态机）。

        · after_seq=N → 返回 seq > N 的结构事件（磁盘全量 + ring 未落盘合并，脱敏）；
        · after_seq 缺省/非法 → 0（等价全量，但正常前端总带水位）；
        · 事件与 WS 实时事件同构（同一 emit 管线产物），前端走同一 applyEvent 幂等共存；
        · 空增量返回 events: []，前端据此判断「已同步到最新」。
        """
        params = parse_qs(query, keep_blank_values=False)
        project_id = unquote((params.get("project_id") or [""])[0]).strip()
        if not project_id:
            self._history_error(writer, b"400 Bad Request", msg("server.py.129"))
            return
        try:
            after_seq = max(0, int((params.get("after_seq") or ["0"])[0]))
        except ValueError:
            after_seq = 0

        # 归一到规范 id（和 /history 一样容忍前端临时 id）
        try:
            canonical = self._canonical_project_id_from_request(project_id)
        except Exception:                   # noqa: BLE001
            canonical = project_id
        pid = canonical if self.engines.get(canonical) else project_id

        def collect() -> list[dict[str, Any]]:
            # 增量事件 = 磁盘全量结构历史 + ring 未落盘结构事件（已脱敏，按 seq 升序）
            return self.hub.durable_since(pid, after_seq)

        try:
            events = await self._run_project_internal_io(
                canonical,
                collect,
                label="events_delta",
            )
        except ProjectClosingError as exc:
            self._history_error(writer, b"409 Conflict", str(exc))
            return

        last_seq = events[-1].get("seq", 0) if events else after_seq
        body = json.dumps(
            {
                "project_id": pid,
                "after_seq": after_seq,
                "last_seq": last_seq,
                "events": events,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self._write_http(writer, b"200 OK", body, b"application/json; charset=utf-8")


    # ═══════════════════════════════════════════════════════════
    # 启动
    # ═══════════════════════════════════════════════════════════
    async def run(self) -> None:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", CONFIG.runtime_token):
            raise RuntimeError("KNOWE_RUNTIME_TOKEN must be exactly 64 hexadecimal characters")

        provenance = activate_build(
            self.data_root,
            Path(__file__).resolve().parents[1],
            application_version=CONFIG.version,
        )
        self.build_provenance = provenance.to_dict()
        log.info(
            "build   → %s git=%s runtime_schema=%s harness_schema=%s prompt=%s startup=%s",
            provenance.build_id,
            provenance.git_commit,
            provenance.runtime_schema_version,
            provenance.harness_schema_version,
            provenance.prompt_bundle_version,
            provenance.startup_id,
        )
        # [任务 1.6] 数据版本检查 + 按序迁移，必须在任何数据读取之前（load_from_disk
        # 会读 projects.json / events / 花名册）。失败抛 DataMigrationError → 启动
        # 中断、不监听任何端口（防数据损坏）。纯内存模式（store 为 None）不落盘，跳过。
        if self.store is not None:
            run_data_migrations(self.data_root, software_version=CONFIG.version)
            # [v1.0.31 R4] 存量 SQLite 迁移（压缩/裁剪/outbox 清理）。
            # 幂等 + 分批事务：中断后续跑自动续转；失败记日志不阻止启动。
            try:
                run_sqlite_migrations(self.data_root, keep=STORAGE_KEEP_RECENT)
            except Exception:  # noqa: BLE001 — 迁移失败不阻止启动，后台循环会重试
                log.exception("SQLite 存量迁移失败（后台维护循环将重试）")
        self.load_from_disk()
        self.platform_manifest.refresh()
        self.start_platform()

        health = await asyncio.start_server(
            self._health_conn, CONFIG.health_host, CONFIG.health_port,
        )
        log.info("health  → http://%s:%d/health", CONFIG.health_host, CONFIG.health_port)

        await self.welcome_if_needed()
        await self.wake_projects()
        await self._update_harness_now()
        self._harness_task = asyncio.create_task(self._harness_memory_loop())
        # [v1.0.31 R2/R3] 存储维护循环：启动先补执行一次（轮转压缩 + 快照裁剪），
        # 之后每 6 小时兜底一次。store 为 None（纯内存模式）不启动。
        if self.store is not None:
            self._storage_task = asyncio.create_task(
                self._storage_maintenance_loop(), name="storage-maintenance",
            )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown_event.set)
            except NotImplementedError:
                pass

        async with serve(
            self.handle,
            CONFIG.ws_host,
            CONFIG.ws_port,
            process_request=self._authenticate_ws,
        ) as ws_server:
            log.info(
                "knowe  → ws://%s:%d  (agent=%s script=%s strict=%s data=%s)",
                CONFIG.ws_host,
                CONFIG.ws_port,
                CONFIG.agent,
                CONFIG.script,
                CONFIG.strict_contract,
                self.store.root if self.store else msg("server.py.310"),
            )
            await self._shutdown_event.wait()
            # Stop accepting new work before closing project-owned resources.
            health.close()
            ws_server.close()
            await health.wait_closed()
            await ws_server.wait_closed()

            log.info("shutting down…")
            for task in (self._harness_task, self._harness_refresh_task, self._storage_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (self._harness_task, self._harness_refresh_task, self._storage_task) if task is not None),
                return_exceptions=True,
            )

            engines = list({id(engine): engine for engine in self.engines.values()}.values())
            stop_tasks = [
                asyncio.create_task(engine.stop(immediate=True), name=f"shutdown:{engine.project_id}")
                for engine in engines
            ]
            try:
                await asyncio.wait_for(
                    asyncio.gather(*stop_tasks, return_exceptions=True), timeout=10.0,
                )
            except asyncio.TimeoutError:
                overdue = [
                    task.get_name().split(":", 1)[-1]
                    for task in stop_tasks if not task.done()
                ]
                log.error("server shutdown timed out waiting for projects: %s", overdue)
                for task in stop_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*stop_tasks, return_exceptions=True)

            purge_tasks = list(self._purge_tasks)
            if purge_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*purge_tasks, return_exceptions=True), timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    for task in purge_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*purge_tasks, return_exceptions=True)

            try:
                await asyncio.wait_for(self._update_harness_now(), timeout=2.0)
            except Exception:
                log.exception("关机末次 Harness Memory 刷新失败（忽略）")
            if self.store is not None:
                # [v1.0.24.4] 关机收尾：排空持久化队列，停掉写入线程。
                self.store.close(timeout=10.0)
            await ProviderClient.aclose_shared_clients()

    # ── [v0.15] Harness Memory：事件驱动 + 去抖 + 定时兜底 ──
    def _on_project_activity(self, project_id: str, reason: str) -> None:
        """ProjectEngine 的同步回调：只标脏并排一个轻量去抖任务。"""
        if project_id == PLATFORM_PROJECT_ID:
            return
        self._harness_dirty = True
        task = self._harness_refresh_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 尚未进入事件循环（只可能出现在构造/极早期测试）——启动同步刷新会兜底。
            self._harness_refresh_task = None
            return
        if loop.is_closed():
            self._harness_refresh_task = None
            return
        self._harness_refresh_task = loop.create_task(
            self._flush_harness_refresh(),
            name=f"harness-refresh:{reason}",
        )

    async def _flush_harness_refresh(self) -> None:
        """把一小段时间内的组队、派活、报告与 Runtime 交付合并成一次全局写入。"""
        cancelled = False
        try:
            await asyncio.sleep(0.2)
            while True:
                self._harness_dirty = False
                await self._update_harness_now()
                await asyncio.sleep(0)
                if not self._harness_dirty:
                    break
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:
            log.exception("Harness Memory 事件刷新任务异常（忽略）")
        finally:
            self._harness_refresh_task = None
            # 正常运行时封住“最后一次 dirty 恰好落在退出缝里”的竞态；
            # 任务取消表示事件循环/服务正在关停，绝不能在 finally 里再生一个任务。
            if self._harness_dirty and not cancelled:
                self._on_project_activity("", "late_dirty")

    async def _update_harness_now(self) -> None:
        """汇总所有项目的实时+持久摘要并覆盖写 Harness Memory。不抛。"""
        try:
            async with self._harness_update_lock:
                summaries: list[dict[str, Any]] = []
                for pid, eng in list(self.engines.items()):
                    if pid == PLATFORM_PROJECT_ID:
                        continue
                    try:
                        summaries.append(await eng.project_summary())
                    except Exception:
                        log.exception(msg("server.py.232"), pid)
                await self.memory.update_harness(summaries)
        except Exception:
            log.exception(msg("server.py.233"))

    async def _harness_memory_loop(self) -> None:
        """每 60 秒做一次修复性刷新；正常新鲜度由事件驱动链保证。"""
        try:
            while True:
                await asyncio.sleep(60)
                await self._update_harness_now()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Harness Memory 定时任务异常退出")

    # ── [v1.0.31 R2/R3] 本地存储维护：流水压缩 + 快照裁剪 ──
    async def _storage_maintenance_loop(self) -> None:
        """启动先补执行一次，之后每 6 小时兜底一次。全部操作幂等，可随时重入。"""
        if self.store is None:
            return
        try:
            while True:
                self._run_storage_maintenance_once()   # 同步函数，勿 await（v1.0.31 实测 TypeError）
                await asyncio.sleep(6 * 3600)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("存储维护循环异常退出（下轮重试）")

    def _run_storage_maintenance_once(self) -> None:
        """同步执行一轮维护：R2 每个项目流水轮转压缩 + R3 任务快照裁剪。

        跑在 asyncio 任务里（磁盘 IO 短暂、数据量小，不阻塞主循环可忽略）。
        """
        try:
            stats = run_all_maintenance(
                self.store,
                self._storage_db_provider,
                keep=STORAGE_KEEP_RECENT,
            )
            if stats.get("pruned"):
                log.info("存储维护完成：裁剪 %d 个任务快照", stats["pruned"])
        except Exception:  # noqa: BLE001 — 维护失败不影响服务运行
            log.exception("存储维护执行失败")

    def _storage_db_provider(self, project_id: str):
        """按项目 id 打开 runtime.sqlite3（不存在则 None，跳过该项目）。

        data_root 即项目目录父级：库在 <data_root>/<project_id>/runtime/runtime.sqlite3。
        """
        db_path = Path(self.data_root) / project_id / "runtime" / "runtime.sqlite3"
        if not db_path.exists():
            return None
        from knowe_storage._sqlite import SQLiteDatabase
        return SQLiteDatabase(db_path)


#: 一眼就该拒的目录（把 Agent 的沙箱指到这些地方，等于没有沙箱）
_FORBIDDEN_ROOTS = ("/", "/etc", "/usr", "/bin", "/sbin", "/var", "/root",
                    "/proc", "/sys", "C:\\", "C:\\Windows")


def _pending_approval_snapshots(
    events: list[dict[str, Any]],
) -> list[tuple[str, str, dict[str, Any]]]:
    """Project durable approval cards that have no later terminal resolution.

    ``approval_card`` is deliberately persisted because it is visible history.  The
    gate Future that makes the card actionable is process-local, however.  Replaying
    only the visible event after a hard kill creates a zombie card: the client still
    has its id, but ``approve`` has nothing to resolve.  This projection rebuilds the
    minimum durable control state from the ordered event stream.

    Re-emissions from ``update_card`` replace the card body in-place.  Once a card id
    has an ``approval_resolved`` event it is terminal forever; stale duplicate cards
    after that point are ignored rather than reopening a completed approval.
    """
    pending: dict[str, tuple[str, str, dict[str, Any]]] = {}
    resolved: set[str] = set()
    for event in sorted(
        (row for row in events if isinstance(row, dict)),
        key=lambda row: int(row.get("seq") or 0),
    ):
        etype = event.get("type")
        if etype == "approval_resolved":
            card_id = str(event.get("card_id") or event.get("approval_id") or "").strip()
            if card_id:
                resolved.add(card_id)
                pending.pop(card_id, None)
            continue
        if etype != "approval_card":
            continue

        raw_card = event.get("card")
        card = dict(raw_card) if isinstance(raw_card, dict) else {}
        card_id = str(
            event.get("card_id")
            or event.get("approval_id")
            or card.get("approval_id")
            or ""
        ).strip()
        if not card_id or card_id in resolved:
            continue
        card["approval_id"] = card_id
        tool = str(event.get("tool") or card.get("tool") or "").strip()
        agent_id = str(event.get("agent_id") or card.get("agent_id") or "").strip()
        pending[card_id] = (tool, agent_id, card)
    return list(pending.values())


def _history_only(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    [v0.12 D · 问题四] 从磁盘读回来的事件里，**只留聊天记录（结构事件）**。

    新代码写盘时就已经只写结构事件了（persist.append_event 挡了一道），但**老文件**
    里塞满了 stream_delta / agent_thinking / state_snapshot 这些逐字增量与瞬时垃圾。
    温载时过这一道，把它们滤掉——紧接着的 compact 会把干净的历史重写回磁盘，
    老文件从此瘦身，而聊天记录一条不丢。
    """
    from .persist import PERSISTABLE_TYPES
    return [e for e in events if e.get("type") in PERSISTABLE_TYPES]


# ═══════════════════════════════════════════════════════════════
# [v0.38] /history 的分类 / 日期 / 序列化辅助（模块级，无状态）
# ═══════════════════════════════════════════════════════════════
_HISTORY_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".heic")
_VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".flv", ".wmv")


def _history_files(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """message 事件上随附的产出文件（[v0.36] files 字段）。稳妥取，只认带 path 的项。"""
    raw = ev.get("files")
    if not isinstance(raw, list):
        return []
    return [f for f in raw if isinstance(f, dict) and isinstance(f.get("path"), str)]


def _history_names(ev: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for f in _history_files(ev):
        for k in ("name", "path"):
            v = f.get(k)
            if isinstance(v, str):
                out.append(v.lower())
    return out


def _history_has_files(ev: dict[str, Any]) -> bool:
    return bool(_history_files(ev))


def _history_has_images(ev: dict[str, Any]) -> bool:
    # 预留：目前图片来源只有产出文件的扩展名；将来接入 images 字段 / mime 时在此放开。
    if ev.get("images"):
        return True
    return any(n.endswith(_IMAGE_EXTS) for n in _history_names(ev))


def _history_has_videos(ev: dict[str, Any]) -> bool:
    # 预留：同上，目前只按产出文件扩展名判定。
    if ev.get("videos"):
        return True
    return any(n.endswith(_VIDEO_EXTS) for n in _history_names(ev))


def _history_has_links(ev: dict[str, Any]) -> bool:
    c = ev.get("content")
    return bool(isinstance(c, str) and _HISTORY_URL_RE.search(c))


def _history_match_category(ev: dict[str, Any], category: str) -> bool:
    if category == "files":
        return _history_has_files(ev)
    if category == "links":
        return _history_has_links(ev)
    if category == "images":
        return _history_has_images(ev)
    if category == "videos":
        return _history_has_videos(ev)
    return True


def _history_ts_ms(ev: dict[str, Any]) -> int | None:
    """事件 ts → 毫秒 int。兼容 epoch 秒/毫秒、ISO 字符串。取不到 → None。"""
    raw = ev.get("ts")
    if isinstance(raw, (int, float)):
        return int(raw if raw > 1e12 else raw * 1000)
    if isinstance(raw, str) and raw:
        try:
            iso = raw.replace("Z", "+00:00")
            return int(datetime.fromisoformat(iso).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _history_match_date(ev: dict[str, Any], date_str: str) -> bool:
    ms = _history_ts_ms(ev)
    if ms is None:
        return False
    try:
        d = datetime.fromtimestamp(ms / 1000).astimezone()
    except (OverflowError, OSError, ValueError):
        return False
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}" == date_str


def _history_out_files(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """
    把事件里的产出文件整成前端文件卡片形状，并保留重命名追踪所需身份。

    ``path`` 仍是项目内相对路径；``file_id`` / ``mtime_ns`` / ``bytes`` 只是文件系统
    元数据，不含绝对路径。历史抽屉若把这些字段裁掉，同一张聊天卡在主会话能追踪改名，
    到历史视图却会退化成“文件不存在”，形成两套不一致的预览语义。
    """
    out: list[dict[str, Any]] = []
    allowed = (
        "name", "path", "ext", "kind", "bytes", "mtime", "mtime_ns", "file_id",
        # 兼容旧附件/历史端点的字段名。
        "size", "mime",
    )
    for f in _history_files(ev):
        item: dict[str, Any] = {
            "name": f.get("name") or "",
            "path": f.get("path") or "",
        }
        for key in allowed[2:]:
            value = f.get(key)
            if value is not None and value != "":
                item[key] = value
        out.append(item)
    return out


def _history_item(ev: dict[str, Any], sanitize: Any) -> dict[str, Any]:
    content = ev.get("content") or ""
    if callable(sanitize):
        try:
            content = sanitize(content)
        except Exception:                       # noqa: BLE001 脱敏失败不该让整个端点垮
            pass
    item: dict[str, Any] = {
        "seq": ev.get("seq"),
        "type": ev.get("type", "message"),
        "agent_id": ev.get("agent_id", "") or "",
        "content": content,
        "ts": _history_ts_ms(ev),
        "has_files": _history_has_files(ev),
        "has_images": _history_has_images(ev),
        "has_videos": _history_has_videos(ev),
        "has_links": _history_has_links(ev),
    }
    # [v0.38.1 #9] 含文件时附上 files（供文件筛选的卡片 + 点击预览）。
    files = _history_out_files(ev)
    if files:
        item["files"] = files
    return item


# ═══════════════════════════════════════════════════════════════
# [v0.38.5 #1] 「报告/交接」= 直接列 handoff/ 目录下的文件（模块级）
#
#   不再按序号配对事件——改为把项目 handoff/ 目录里的每个 report/instruction 文件
#   独立列一条，按文件创建时间倒序。点击预览该文件的 markdown。
#   （沿用前端既有的 instruction/report 字段：instruction 文件填 instruction，
#    report 文件填 report，前端预览就各显示对应那一段，无需改前端。）
# ═══════════════════════════════════════════════════════════════
_HANDOFF_TEXT_EXTS = (".md", ".markdown", ".txt", "")


def _handoff_kind_from_name(name: str) -> str:
    low = name.lower()
    if "instruction" in low or "instr" in low or msg("s.234a") in name or msg("s.234b") in name:
        return "instruction"
    if "report" in low or msg("server.py.235") in name:
        return "report"
    return "report"   # 认不出就按报告/通用交接文档处理


def _handoff_file_ctime_ms(st: Any) -> int:
    """尽量取“创建时间”：有 st_birthtime 用它；Linux 没有 → 用 mtime（交接文件多为一次写成）。"""
    bt = getattr(st, "st_birthtime", None)
    if isinstance(bt, (int, float)) and bt > 0:
        return int(bt * 1000)
    mt = getattr(st, "st_mtime", 0) or 0
    return int(mt * 1000)


def _handoff_title_from(name: str, content: str) -> str:
    for line in (content or "").splitlines():
        s = line.strip().lstrip("#>*- ").strip()
        if s:
            return s[:60]
    stem = name.rsplit(".", 1)[0]
    return (stem[:60] or msg("server.py.311"))


def _build_handoff_items(paths: Any, base: Any, sanitize: Any) -> list[dict[str, Any]]:
    """把一组交接文件路径各自读成一条记录，按文件创建时间倒序（最新在前）。

    id 用相对 base 的路径（含阶段子目录，保证唯一）；base 为 None 时退回文件名。
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in (paths or []):
        try:
            key = str(f.resolve())
        except Exception:                       # noqa: BLE001
            key = str(f)
        if key in seen:
            continue
        seen.add(key)
        try:
            if not f.is_file():
                continue
            st = f.stat()
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:                       # noqa: BLE001
            continue
        if callable(sanitize):
            try:
                content = sanitize(content)
            except Exception:                   # noqa: BLE001
                pass
        kind = _handoff_kind_from_name(f.name)
        rel = f.name
        if base is not None:
            try:
                rel = str(f.relative_to(base))
            except Exception:                   # noqa: BLE001
                rel = f.name
        out.append({
            "id": rel,
            "seq": 0,
            "ts": _handoff_file_ctime_ms(st),
            "title": f.stem,
            "agent_id": "",
            "kind": kind,
            "content": content,
            # 沿用前端字段：按类别填其一，前端预览各显示对应段落（无需改前端）
            "instruction": content if kind == "instruction" else "",
            "report": content if kind == "report" else "",
        })
    out.sort(key=lambda h: (h.get("ts") or 0, str(h.get("id") or "")), reverse=True)
    return out


def _dedupe_agents_created(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """
    [v0.8e #3] 清掉历史里**重复的** agents_created。

    规矩：按事件流维护“当时仍在队”的集合。一条 agents_created 里的人如果
    **当时全都已经在队**，它才是旧版本每次开机补出来的垃圾；若某人此前已有
    agent_removed，则这条是合法恢复，必须保留。只要里面有新人或恢复成员就留着。

    ⚠ 删事件会在 seq 上留洞。这是安全的：
      · 前端回放是在**握手窗口**里进行的，握手期间事件绕过空洞检测（socket.ts 的
        buffering 分支在水位判定之前）；水位以 replay_complete 的 last_seq 为准。
      · ring 的 gap 标志说的是「被淘汰」，不是「不连续」。
    """
    # `active` 不是“历史上见过”，而是按事件流推演出的当时在队状态。
    # 旧实现只用 seen：成员被 agent_removed 归档后，再次 agents_created（合法恢复）
    # 仍会被当成“老面孔补发”删掉。随后前端重放只看见归档、看不见恢复，身份又会灰回去。
    active: set[str] = set()
    out: list[dict[str, Any]] = []
    dropped = 0

    for ev in events:
        etype = ev.get("type")
        if etype == "agent_removed":
            target = ev.get("target_id")
            if isinstance(target, str):
                active.discard(target)
            out.append(ev)
            continue
        if etype != "agents_created":
            out.append(ev)
            continue

        ids = [m.get("id") for m in (ev.get("members") or [])
               if isinstance(m, dict) and isinstance(m.get("id"), str)]
        reactivated = [i for i in ids if i not in active]
        if ids and not reactivated:
            dropped += 1                 # 当时已经全在队 → 旧版本开机补发，删
            continue

        active.update(i for i in ids if isinstance(i, str))
        out.append(ev)

    return out, dropped


def _safe_name(name: str) -> str:
    """文件夹名 → 能当目录名用的安全串（和 engine._safe 同一套规矩）。"""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)[:120] or "_"


def _clean_project_dir(raw: str | None) -> str | None:
    """
    [v0.7 A0 · v0.7b #1] 用户选的目录，落地成一个能用的绝对路径。

    两条出路：
      1. **绝对路径**（Electron 正路：dialog.showOpenDialog 给的）→ 必须已经存在，直接用。
      2. 其它任何情况（非绝对路径 / 空 / 指到系统根目录 / 目录不存在）→ None，
         [任务 1.7] 一律返回 None，由 create_project 强校验抛错拒绝建群——
         已删除 v0.7 遗留的自动建目录兜底，绝不偷偷在 workspaces/ 下开目录。

    这里挡的不是攻击（目录是用户自己在系统对话框里点的），是**事故**：
    沙箱的根要是 `/`，`..` 都不用写就已经出界了。
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None

    path = Path(text).expanduser()

    # ── 出路 2：拿不到绝对路径 —— 喊出来并拒绝（不再自动建目录）──
    if not path.is_absolute():
        # [任务 1.7] 已删除自动建目录兜底：非绝对路径一律拒绝，由 create_project 抛错。
        log.warning(
            "建群目录不是绝对路径（%s），已拒绝：本版本要求选择真实存在的文件夹（绝对路径）",
            text,
        )
        return None

    # ── 出路 1：绝对路径 ──
    try:
        resolved = path.resolve()
    except OSError:
        return None

    norm = str(resolved)
    if norm in _FORBIDDEN_ROOTS or resolved == resolved.parent:
        log.warning(msg("server.py.240"), norm)
        return None

    if not resolved.is_dir():
        log.warning(msg("server.py.241"), norm)
        return None

    return norm


def _parse(raw: str | bytes) -> dict[str, Any] | None:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


# ═══════════════════════════════════════════════════════════════
# [v0.12 D · 问题五 5b] 知知的**只读**文件能力
#
#   用户的诉求：平台级 agent 应有全电脑的**读取/扫描**权限——能读文件内容、看路径，
#   这样问什么都答得上；但**绝不能**做增/删/改/剪切/重命名等破坏本地文件的危险操作。
#
#   所以这两个函数**只读**：只 stat / 只 read，物理上没有任何写入路径。
#   再加两道保险：文件太大不整个读（截断），二进制不硬塞（提示而非乱码）。
#   这是用户自己机器上的本地应用、用户亲口要的能力——放行读，卡死写。
# ═══════════════════════════════════════════════════════════════
_READ_DEFAULT_LINES = 400
_LIST_DEFAULT_ENTRIES = 300


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        return path == root or root in path.parents
    except Exception:
        return False


def _safe_read_any(
    path: str,
    forbidden_root: Path | str | None = None,
    *,
    start_line: int = 1,
    limit: int = _READ_DEFAULT_LINES,
) -> str:
    """Read one resumable line page from any user file.

    The platform reader remains physically read-only and keeps the internal-root
    boundary.  File size is metadata, never a reason to make later content
    unreachable: callers continue with ``next_start_line``.
    """
    try:
        p = Path(path).expanduser().resolve(strict=False)
        if forbidden_root is not None:
            denied = Path(forbidden_root).expanduser().resolve(strict=False)
            if _path_is_under(p, denied):
                return msg("server.py.242")
        if not p.exists():
            return msg("server.py.243", p=p)
        if p.is_dir():
            return msg("server.py.244", p=p)
        if not p.is_file():
            return msg("server.py.245", p=p)

        start = max(1, int(start_line))
        page_limit = max(1, min(5_000, int(limit)))
        size = p.stat().st_size
        with p.open("rb") as probe_stream:
            probe = probe_stream.read(8_192)
        if b"\x00" in probe:
            return msg("server.py.246", p=p, size=size)

        rows: list[str] = []
        total_lines = 0
        with p.open("r", encoding="utf-8", errors="replace", newline="") as stream:
            for line_number, line in enumerate(stream, start=1):
                total_lines = line_number
                if line_number < start:
                    continue
                if len(rows) < page_limit:
                    rows.append(line.rstrip("\r\n"))

        end_line = start + len(rows) - 1 if rows else start - 1
        has_more = end_line < total_lines
        header = [
            msg("server.py.247", p=p),
            msg("server.py.248", size=size),
            msg("server.py.249", start=start, end_line=end_line, total_lines=total_lines),
            "",
        ]
        body = "\n".join(rows)
        tail: list[str] = []
        if has_more:
            next_line = end_line + 1
            tail.extend([
                "",
                f"continuation: read_file(path={str(p)!r}, start_line={next_line}, limit={page_limit})",
                f"next_start_line: {next_line}",
            ])
        else:
            tail.extend(["", "completeness: true"])
        return "\n".join(header) + body + "\n".join(tail)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return msg("server.py.250", path=path, exc=exc)


def _list_dir_any(
    path: str,
    forbidden_root: Path | str | None = None,
    *,
    offset: int = 0,
    limit: int = _LIST_DEFAULT_ENTRIES,
) -> str:
    """List one resumable directory page while hiding Knowe internal storage."""
    try:
        p = Path(path).expanduser().resolve(strict=False)
        denied = (
            Path(forbidden_root).expanduser().resolve(strict=False)
            if forbidden_root is not None else None
        )
        if denied is not None and _path_is_under(p, denied):
            return msg("server.py.312")
        if not p.exists():
            return msg("server.py.243", p=p)
        if not p.is_dir():
            return msg("server.py.314", p=p)
        entries = sorted(p.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
        visible: list[Path] = []
        for entry in entries:
            try:
                resolved = entry.resolve(strict=False)
            except (OSError, RuntimeError):
                resolved = entry
            if denied is not None and _path_is_under(resolved, denied):
                continue
            visible.append(entry)

        page_offset = max(0, int(offset))
        page_limit = max(1, min(2_000, int(limit)))
        page = visible[page_offset : page_offset + page_limit]
        out = [
            msg("server.py.322", p=p),
            msg("server.py.323", page_offset=page_offset, **{"page_offset + len(page)": page_offset + len(page)}, **{"len(visible)": len(visible)}),
            "",
        ]
        for entry in page:
            try:
                if entry.is_dir():
                    out.append(f"  📁 {entry.name}/")
                else:
                    out.append(msg("server.py.333", **{"entry.name": entry.name}, **{"entry.stat().st_size": entry.stat().st_size}))
            except OSError:
                out.append(f"  ? {entry.name}")
        next_offset = page_offset + len(page)
        if next_offset < len(visible):
            out.extend([
                "",
                f"continuation: list_dir(path={str(p)!r}, offset={next_offset}, limit={page_limit})",
                f"next_offset: {next_offset}",
            ])
        else:
            out.extend(["", "completeness: true"])
        return "\n".join(out)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return msg("server.py.319", path=path, exc=exc)


def _attach_file_logging() -> None:
    """console=False 打包态下的日志保险丝：stderr 不可见时把日志双写到文件。

    日志目录取 env KNOWE_LOG_DIR；缺省用 KNOWE_INSTALL_ROOT/Logs；都没有则
    跳过文件日志。文件名 backend_YYYYMMDD.log。任何失败只跳过文件日志，绝不
    拖垮服务。
    """
    try:
        log_dir = os.environ.get("KNOWE_LOG_DIR")
        if not log_dir:
            install_root = os.environ.get("KNOWE_INSTALL_ROOT")
            if install_root:
                log_dir = os.path.join(install_root, "Logs")
        if not log_dir:
            return
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(
            os.path.join(log_dir, f"backend_{datetime.now():%Y%m%d}.log"),
            encoding="utf-8",
            errors="replace",
        )
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s %(name)-13s %(message)s",
            datefmt="%H:%M:%S",
        ))
        logging.getLogger().addHandler(file_handler)
    except Exception:
        # 文件日志只是保险丝：写不进去就保持 stderr 单通道，不报错不崩溃。
        pass


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)-13s %(message)s",
        datefmt="%H:%M:%S",
    )
    _attach_file_logging()
    try:
        asyncio.run(KnoweServer().run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()