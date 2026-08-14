# [v1.0.13][R1][R2][R3][R4] Readiness, identity, atomic completion and Seen Speech.
# knowe v0.50 — Harness 核心引擎
"""
ProjectEngine owns project sessions, Coordinator orchestration, UI compatibility,
knowledge/memory integration. Every Worker task is a shared
TaskEnvelope executed directly by one WorkerRuntime; no translation gateway, legacy
Worker AgentLoop, or model-facing completion protocol remains in this module.
"""

from __future__ import annotations

import asyncio
import copy
import contextvars               # [v0.37.1] 私聊回合的 emit 频道走 task-局部 ContextVar
import gzip                      # [v0.44.8] 重命名时同步 Project Memory 的压缩历史段
import hashlib
import inspect
import json
import logging
import os                        # [v0.36.1] terminal/execute_code 产出扫描用 os.walk 剪枝
import re
import shutil
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Callable, Iterable

from knowe_core.provider_identity import humanize_provider_error, provider_target
from .token_pricing import estimate_cost
from .i18n_backend import msg
from .content_compress import snapshot_compression_stats  # [v1.0.34-M4] 压缩台账快照
from knowe_provenance import current_provenance_dict, normalize_provenance
from knowe_harness import (
    CompletionAwareTaskRunRepository,
    CompletionCommitter,
    CompletionProjector,
    CompletionStatus,
    completion_scope_id,
    CoordinatorAction,
    DecisionEvent,
    DecisionType,
    SQLiteCompletionStore,
    TaskEnvelopeStore,
)
from knowe_harness.contracts import stable_identifier
from knowe_storage._sqlite import close_sqlite_databases_under

from .runtime import (
    BudgetSpec,
    ContextReference,
    DeliveryAudience,
    DeliveryRecord,
    DeliveryTarget,
    RuntimeEvent,
    RuntimeEventType,
    TaskEnvelope,
    TaskRun,
    TaskState,
    utc_now,
)

from . import aux_client, capabilities, knowledge_api, roles, tool_ledger
from .agent_identity import identity_for
from .feature_flags import FeatureFlag, enabled as feature_enabled
from .seen_speech import (
    SeenSpeechLedger, VisibleSpeech, notification_from_unknown,
    render_seen_speech_block,
)
from .worker_completion import (
    CompletionViewV1, build_user_facing_completion,
)
from .agent_runtime import shutdown_project_runtime
from .agents.base import AgentPort, Turn
from .agents.fake import FakeAgent
from .agents.knowe_agent import KnoweAgent, ProviderConfig
from .config import CONFIG
from . import runtime_settings   # [v0.44 设置] 模型绑定 / 审批超时 / 称呼的运行时权威
from . import prompt_resolver    # [v1.0.21.3] 按语言选提示词模板
from .gate import ApprovalCancelled, Gate
from .handoff import HandoffBook, keyword_of, parse_next_dir, strip_next_dir
from .hub import Hub
from .mentions import MentionMember, MentionResolution, resolve_mentions
from .persist import Store, agent_name_for, legacy_display_name
from .privacy import sanitize_event, sanitize_text
from .tools_knowe import (
    build_coordinator_registry,
    resolve_in_sandbox,          # [v0.36] 产出文件元数据 stat 时复用同一套沙箱解析
)
from .worker_gateway_runtime import DEFAULT_WORKER_RUNTIME_FACTORY, WorkerContextError
from .workspace_layout import ensure_internal_workspace, internal_workspace_for

if TYPE_CHECKING:                       # [v0.11 C-1] 只做类型标注，运行时实例由 server 传入
    from .knowledge_graph import KnowledgeGraphManager
    from .memory_manager import MemoryManager

log = logging.getLogger("knowe.engine")

COORDINATOR = "coordinator"


class ProjectResourceCloseError(RuntimeError):
    """A project-local critical resource could not be closed after teardown."""

    def __init__(self, project_id: str, issues: Iterable[str]) -> None:
        self.project_id = project_id
        self.issues = tuple(str(item) for item in issues if str(item).strip())
        detail = "；".join(self.issues) or msg("engine.001")
        super().__init__(f"[{project_id}] {detail}")


def _unique_strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (tuple, list, set, frozenset)):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _auto_external_roots(objective: str) -> tuple[str, ...]:
    r"""[v1.0.23.8-A] 从任务目标文本提取 Windows 绝对路径，返回其根目录。

    coordinator 在 goal 里写「用 copy_external_file 复制 D:/a/b/c.md」时，
    引擎自动把 D:/a/b 加入 authorized_external_roots，让外部复制工具可用。

    规则（最小权限）：
      - 只认盘符绝对路径（如 D:/xxx 或 D:/xxx/file.md）
      - 文件路径取其所在目录；目录路径取其自身
      - 根目录自身（如 D:/）不授权（太宽）
      - 项目工作区内部路径不授权（本来就能用项目工具）
    """
    if not objective:
        return ()
    # 归一化反斜杠（JSON 转义的双反斜杠 → 单反斜杠）
    text = objective.replace("\\\\", "\\")
    pattern = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]:[\\/][^\s\"'`|<>]+)", re.IGNORECASE)
    roots: list[str] = []
    for match in pattern.finditer(text):
        raw = match.group(1).strip().rstrip(".,;:)]}")
        if not raw or len(raw) < 4:
            continue
        path = Path(raw)
        if not path.is_absolute():
            continue
        candidate: Path
        if path.suffix:
            candidate = path.parent  # 文件 → 所在目录
        else:
            candidate = path  # 目录 → 自身
        if candidate == candidate.anchor:
            continue  # 盘符根太宽，不授权
        roots.append(str(candidate))
    return _unique_strings(roots)


def _normalize_project_path(value: Any) -> str:
    raw = str(value or "").strip().strip("`\"'《》").replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if (
        not raw
        or raw.startswith(("/", "~"))
        or re.match(r"^[A-Za-z]:/", raw)
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw)
    ):
        raise ValueError("path must be project-relative")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path traversal is forbidden")
    return "/".join(parts)


def select_provider_fallback(
    binding_or_project: Mapping[str, Any] | str | None,
    unavailable_providers: Any = None,
    project_id: Any = "",
    agent_id: str = "",
) -> dict[str, Any] | None:
    """Select the first explicitly configured provider that is currently available.

    The selector accepts both the direct production form
    ``select_provider_fallback(binding, unavailable_providers={...})`` and the older
    ``(project_id, agent_id, unavailable_providers)`` form retained by recovery code.
    It never invents a hidden model/provider: only the primary binding and its declared
    ``fallbacks`` are considered.
    """

    unavailable_raw: Any = unavailable_providers
    if isinstance(binding_or_project, Mapping):
        binding = copy.deepcopy(dict(binding_or_project))
    else:
        requested_project = str(binding_or_project or "")
        requested_agent = str(agent_id or "")
        # Compatibility positional form: (project_id, agent_id, unavailable_set).
        if isinstance(unavailable_providers, str):
            requested_agent = unavailable_providers
            unavailable_raw = project_id
        binding_value = runtime_settings.model_binding_for(
            requested_project,
            requested_agent,
        )
        if not isinstance(binding_value, Mapping):
            return None
        binding = copy.deepcopy(dict(binding_value))

    if isinstance(unavailable_raw, str):
        unavailable_values = (unavailable_raw,)
    elif isinstance(unavailable_raw, Mapping):
        unavailable_values = tuple(
            key for key, disabled in unavailable_raw.items() if bool(disabled)
        )
    else:
        try:
            unavailable_values = tuple(unavailable_raw or ())
        except TypeError:
            unavailable_values = ()
    unavailable = {
        str(value or "").strip().casefold()
        for value in unavailable_values
        if str(value or "").strip()
    }

    candidates: list[dict[str, Any]] = [binding]
    declared = binding.get("fallbacks")
    if isinstance(declared, Mapping):
        declared = (declared,)
    for item in declared or ():
        if isinstance(item, Mapping):
            candidates.append(copy.deepcopy(dict(item)))
    single = binding.get("fallback")
    if isinstance(single, Mapping):
        candidates.append(copy.deepcopy(dict(single)))

    primary_provider = str(binding.get("provider") or "").strip()
    for index, candidate in enumerate(candidates):
        provider = str(candidate.get("provider") or "").strip()
        if not provider or provider.casefold() in unavailable:
            continue
        # A selected binding is self-contained. Keeping the parent fallback list in the
        # result makes diagnostics ambiguous and can accidentally recurse on retries.
        selected = copy.deepcopy(candidate)
        selected.pop("fallback", None)
        selected.pop("fallbacks", None)
        if index:
            selected.setdefault("fallback_from", primary_provider)
            selected.setdefault("fallback_index", index - 1)
        return selected
    return None

# ── v0.44.6：Project Memory 静默预检索 ──
# 这是 Harness 内部的上下文预算，不是用户开关：默认自动工作，失败时完全不留痕。
_MEMORY_CLUE_LIMIT = 3
_MEMORY_CLUE_SHORT_LIMIT = 2
_MEMORY_CLUE_SEARCH_LIMIT = 8
_MEMORY_CLUE_SUMMARY_CHARS = 58
_MEMORY_CLUE_MAX_CHARS = 420
_MEMORY_ID_RE = re.compile(r"^m\d{12}$", re.I)
_MEMORY_ID_IN_TEXT_RE = re.compile(r"\b(m\d{12})\b", re.I)

# ── v0.44.7：Agent Memory 个人检索与静默预检索 ──
# worklog 的尾部会由 _agent_memory_block() 常驻注入；静默线索只补这段之外的旧记录。
_AGENT_MEMORY_TAIL_CHARS = 1200
_AGENT_MEMORY_CLUE_LIMIT = 2
_AGENT_MEMORY_CLUE_SEARCH_LIMIT = 8
_AGENT_MEMORY_CLUE_MAX_CHARS = 300
_AGENT_WORKLOG_HEADING_RE = re.compile(
    r"(?m)^##[ \t]+(?P<header>\d{4}-\d{2}-\d{2}T[^ \t\n·]+[ \t]*·[^\n]+?)[ \t]*$"
)
_AGENT_WORKLOG_STEP_RE = re.compile(r"^第\s*(\d+)\s*步$")
_AGENT_WORKLOG_LINKED_STEP_RE = re.compile(r"^关联第\s*(\d+)\s*步$")

# ── v0.44.8：项目重命名的 Harness 级迁移 ──
#
# 项目 id 始终稳定；项目名则会出现在 Project Memory、Agent worklog、handoff、权限说明、
# 知识资产元数据等人读文件里。只改 Hub 上的一行 name 会让这些层各说各话，因此重命名
# 必须在 Harness 内把「内存引用 + 内部工作区持久引用」作为一个整体刷新。
_PROJECT_RENAME_TEXT_SUFFIXES = frozenset({
    ".md", ".markdown", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".csv", ".tsv", ".html", ".htm", ".xml", ".log",
})
_PROJECT_RENAME_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".cache", ".pytest_cache",
})
_PROJECT_RENAME_MAX_BYTES = 16 * 1024 * 1024


def _replace_project_name_value(value: Any, old_name: str, new_name: str, depth: int = 0) -> Any:
    """递归替换 Harness 自己持有的 JSON 形数据；有深度闸，绝不追进任意对象图。"""
    if depth > 12:
        return value
    if isinstance(value, str):
        return value.replace(old_name, new_name)
    if isinstance(value, list):
        return [_replace_project_name_value(v, old_name, new_name, depth + 1) for v in value]
    if isinstance(value, tuple):
        return tuple(_replace_project_name_value(v, old_name, new_name, depth + 1) for v in value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, child in value.items():
            new_key = key.replace(old_name, new_name) if isinstance(key, str) else key
            out[new_key] = _replace_project_name_value(child, old_name, new_name, depth + 1)
        return out
    return value


def _rewrite_project_name_file(path: Path, old_name: str, new_name: str) -> bool:
    """原子改写一份 UTF-8 内部文件；压缩 JSONL 段保持 gzip 格式。"""
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size > _PROJECT_RENAME_MAX_BYTES or path.is_symlink():
        return False

    compressed = path.name.lower().endswith(".jsonl.gz")
    try:
        if compressed:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                text = fh.read()
        else:
            if path.suffix.lower() not in _PROJECT_RENAME_TEXT_SUFFIXES:
                return False
            text = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError, gzip.BadGzipFile):
        return False

    if old_name not in text:
        return False
    replaced = text.replace(old_name, new_name)
    tmp = path.with_name(path.name + ".rename.tmp")
    try:
        if compressed:
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                fh.write(replaced)
        else:
            tmp.write_text(replaced, "utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def rewrite_project_name_tree(root: Path | str, old_name: str, new_name: str) -> dict[str, Any]:
    """同步一棵 Knowe 内部目录中的项目名引用；不碰用户业务目录。"""
    base = Path(root).expanduser()
    report: dict[str, Any] = {"root": str(base), "scanned": 0, "updated": 0, "errors": []}
    if not old_name or old_name == new_name or not base.is_dir():
        return report

    for current, dirs, files in os.walk(base, followlinks=False):
        # .project / .context 等隐藏目录正是旧版记忆元数据可能所在；只跳明确的缓存/依赖目录。
        dirs[:] = [d for d in dirs if d not in _PROJECT_RENAME_SKIP_DIRS]
        here = Path(current)
        for filename in files:
            path = here / filename
            lower = filename.lower()
            if not (path.suffix.lower() in _PROJECT_RENAME_TEXT_SUFFIXES or lower.endswith(".jsonl.gz")):
                continue
            report["scanned"] += 1
            try:
                if _rewrite_project_name_file(path, old_name, new_name):
                    report["updated"] += 1
            except Exception as exc:  # noqa: BLE001 — 一份坏文件不能卡死全局改名
                if len(report["errors"]) < 12:
                    report["errors"].append(f"{path}: {exc}")
    return report


# ═══════════════════════════════════════════════════════════════
# [v0.36] 产出文件 → 预览大类
#
#   把扩展名归成前端预览面板认得的几个大类。前端 previewKinds.ts 里有一份**镜像**
#   （字面一致）——两边各判各的，后端这份是为了让 message 事件里就带上现成的 kind，
#   前端不必仅凭扩展名重猜（也留一手：前端拿不到 kind 时按扩展名兜底）。
# ═══════════════════════════════════════════════════════════════
_PREVIEW_KIND_BY_EXT: dict[str, str] = {
    "md": "markdown", "markdown": "markdown", "mdown": "markdown", "mkd": "markdown",
    "html": "html", "htm": "html",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image",
    "webp": "image", "svg": "image", "bmp": "image", "ico": "image",
    "pdf": "pdf",
    "docx": "docx",
    "pptx": "pptx",
    "xlsx": "sheet", "xls": "sheet", "csv": "sheet", "tsv": "sheet",
}


def _preview_kind_for_ext(ext: str) -> str:
    """扩展名（小写、不带点）→ 预览大类；认不出的一律 'file'（走降级卡）。"""
    return _PREVIEW_KIND_BY_EXT.get((ext or "").lower(), "file")


# ═══════════════════════════════════════════════════════════════
# [v0.36.1] terminal / execute_code 产出扫描
#
#   v0.36 只在 safe_write_file / copy_external_file 里记产出文件；Worker 常用
#   terminal 跑脚本（python convert.py、pandoc、截图…）产出的文件全绕过了追踪。
#   这里定义扫描要认的扩展名，以及要跳过的噪音目录。
#
#   扩展名比预览大类更宽：不只办公文件，Markdown 报告、数据 JSON/YAML、截图 PNG
#   都是常见终端产出。认不出预览方式的（txt/json/yaml…）也照样追踪——
#   前端拿到会走「降级卡」（文件信息 + 在外部打开），至少让用户看得见「产出了什么」。
# ═══════════════════════════════════════════════════════════════
_SCAN_TRACK_EXTS: frozenset[str] = frozenset({
    "md",
    "html", "htm", "xhtml", "css", "scss", "sass", "less",
    "jpg", "jpeg", "png", "gif", "webp", "svg", "bmp",
    "pdf",
    "docx", "doc",
    "pptx", "ppt",
    "xlsx", "xls", "csv", "tsv",
    "txt", "json", "jsonc", "json5", "yaml", "yml", "toml", "xml", "xsd", "xsl", "xslt",
    # [v0.45.2 #5] 源码 / 配置也属于可预览产出，不再把它们当“帮手噪音”一刀切掉。
    "js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts",
    "py", "pyw", "rb", "php", "java", "kt", "kts", "swift", "go", "rs",
    "c", "h", "cc", "cpp", "cxx", "hpp", "hxx", "cs", "fs", "fsx", "vb",
    "lua", "r", "scala", "dart", "pl", "pm", "ex", "exs", "erl", "hrl",
    "clj", "cljs", "cljc", "edn", "groovy", "gradle", "vue", "svelte", "astro",
    "sql", "graphql", "gql", "proto",
    "sh", "bash", "zsh", "fish", "ps1", "psm1", "psd1", "bat", "cmd",
    "ini", "cfg", "conf", "env", "properties", "cmake",
})

_SCAN_TRACK_NAMES: frozenset[str] = frozenset({
    "dockerfile", "containerfile", "makefile", "gnumakefile", "cmakelists.txt",
    "jenkinsfile", ".gitignore", ".dockerignore", ".editorconfig",
})



def _should_track_produced(ext: str, name: str = "") -> bool:
    """文件是否进入聊天文件卡台账。

    v0.45.2 的预览器已能安全显示源码和配置，因此这里的真源也同步扩展；依赖树、
    缓存和构建目录仍由 _SCAN_SKIP_DIRS 剪掉，避免把 node_modules 等噪音灌进聊天。
    """
    e = (ext or "").lower()
    n = (name or "").strip().lower()
    return (
        e in _SCAN_TRACK_EXTS
        or n in _SCAN_TRACK_NAMES
        or _preview_kind_for_ext(e) != "file"
    )


class WorkspaceUnavailable(RuntimeError):
    """用户记录的显式项目根目录已经不存在。Harness 必须暂停，绝不能偷偷重建。"""


# _engine_block("PROJECT_ROOT_CONTEXT") 已外置 → prompts/<lang>/engine_blocks.md（见 _engine_block）
# Harness-authored notes remain explicitly labelled so the model can distinguish them
# from user-authored content.  Labels are prompt context only: outbound model text is never
# searched for these words or edited because it resembles an internal note.
INTERNAL_NOTE_HEADER = (
     msg("engine.002")
    + msg("engine.003")
    + msg("engine.004")
    + "────────\n"
)

#: 提案被拒之后塞给项目经理的话
REJECTION_FOLLOWUP = (
    INTERNAL_NOTE_HEADER
    + msg("engine.005")
      + msg("engine.006")
)

#: [v0.30 Bug2/3] v0.25 的「意见走聊天消息」老路（FEEDBACK_MARK / FEEDBACK_TEMPLATE /
#:   _compose_feedback / remember_pending_instruction / instruction_repeat_after_feedback）
#:   在这一版**整体删除**。v0.26 的注释原话：「两套机制干同一件事，正是这个代码库
#:   过去几版反复踩坑的来源」——而 v0.29 的连锁 bug 里恰好有一条疑似它的影子
#:   （旧反馈的卡在新卡之后弹出）。「我有新意见」从此只有一条路：
#:   控制面 feedback_instruction → adjust_instruction（本文件下方，已串行化 + 可取消）。

#: 项目经理的角色名（写进花名册的那个词）
COORDINATOR_ROLE = msg("engine.007")

#: [v0.8c #7] 补给「有去无回」的那个 tool_call 的假回执。
#:   内容是实话：这一步没走完，别让模型以为它成功了。
TOOL_ABORTED_RESULT = (
     '{"ok": false, "error": "aborted", '
    + msg("engine.008")
)

#: agent 的消息历史可能藏在这些名字底下（knowe_core 的实现细节，别硬编码一个）
_HISTORY_ATTRS = ("messages", "history", "_messages", "_history",
                  "conversation", "conversation_history", "message_history")
_HISTORY_HOLDERS = ("loop", "_loop", "state", "_state", "core", "_core", "session")

#: [v0.8a A-2] Worker 交了报告 → 塞给项目经理的那条通知
#: [v0.21 问题一] 项目经理收到报告时的通知。
#:
#: 老版本在这里同时说了两句互相打架的话：
#:     「别把报告原样再说一遍」 + 「用一两句点出**他做到了什么**、够不够、下一步」
#: 对一件简单的活（改一行代码、抓一个网页）来说，「他做到了什么」**就是**报告本身——
#: 于是这两句要求同一段话既复述又不复述。模型只能二选一，而它总是选那句**具体的**，
#: 也就是复述。屏幕上就出现了用户投诉的那一幕：成员刚说完「XX 已改为 YY」，
#: 项目经理紧接着说「XX 已完成，内容正确，验收通过」。
#:
#: **这不是模型不听话，是指令自相矛盾。** 所以这一版把提问方式整个换掉：
#: 不问「他做到了什么」（那必然指向复述），只问「**用户还不知道什么**」——
#: 一个只可能指向新信息的问题。再给它一条明确的退路（NOTHING_TO_ADD），
#: 因为没有退路的模型一定会为了填满回合而说点什么。
REPORT_NOTICE = (
    INTERNAL_NOTE_HEADER
    + msg("engine.009")
    + msg("engine.010")
    + msg("engine.011")
    + "\n"
    + msg("engine.013")
    + msg("engine.014")
    + msg("engine.015")
    + msg("engine.016")
    + msg("engine.017")
    + msg("engine.018")
    + msg("engine.019")
    + msg("engine.020")
    + msg("engine.021")
    + "\n"
    # ★ [v0.28] 这两行原来写的是：
    #       「要派下一件活，**先跟用户说清楚再 propose_next**；……
    #         **也不要在没跟用户交代之前就直接派活。**」
    #
    #   第二句比 v0.27 删掉的那四处都狠 —— 那四处是**允许**先说，这一句是**要求**先说，
    #   否则不许调工具。它和 v0.27 的新规矩（调完 → NOTHING_TO_ADD，一个字都不说）
    #   **正面对撞**。而 REPORT_NOTICE **成员每交一次报告就注入一次**，
    #   而「审完报告 → 派下一件活」是整个产品里最常走的派活路径。
    #   ——最常见的那条路上，代码每次都在教它先说话。
    #
    #   v0.27 我普查了「prompt 住在哪」（人设、工具描述、工具回执），漏了**引擎注入的通知**。
    + msg("engine.022")
    + msg("engine.023")
    + msg("engine.024")
)

REPORT_SUMMARY_CLIP = 200

# ═══════════════════════════════════════════════════════════════
# [v0.29 问题四] Worker 的【工作中】非正常终止 → 自动 report_failed
#
# ## 为什么这条通知必须存在
#
#   Worker 手上有活，然后：API 报错了 / 卡死被推了三次还是不动 / 用户按了「停止」。
#   在 v0.28，这三条路各自 `discard(_workers_with_open_activity)` 就完事了——
#   头像变灰，**项目经理从头到尾不知道发生过什么**。
#   于是用户说「让他继续」，项目经理一头雾水：在他的世界里，那个人从来没停过，
#   因为在他的世界里，那个人也从来没开始过。
#
# ## 为什么它长得像 REPORT_NOTICE，但不是 REPORT_NOTICE
#
#   两条通知都要让项目经理开口，但要他说的**是相反的两件事**：
#     · REPORT_NOTICE  →「活干完了，用户还不知道什么？没有就闭嘴」
#     · 这一条         →「活**没**干完，用户正等着，你必须给他一个交代」
#   拿一条「已完成」的模板去承载一次失败，项目经理会照着模板去问「用户还不知道什么」，
#   然后得出「没什么要说的」——那正是这个 bug 最坏的形态：**失败被静默地归档了**。
#   所以这里**不给 NOTHING_TO_ADD 这条退路**：失败必须有人说出来。
REPORT_FAILED_NOTICE = (
    INTERNAL_NOTE_HEADER
    + msg("engine.025")
    + msg("engine.026")
    + msg("engine.027")
    + msg("engine.028")
    + msg("engine.029")
    + msg("engine.030")
    + msg("engine.031")
    + "\n"
    + msg("engine.032")
    + msg("engine.033")
    + msg("engine.034")
    + msg("engine.035")
    + msg("engine.036")
    + msg("engine.037")
    + msg("engine.038")
)

#: [v0.29 问题四] 兜底：连失败报告都写不进磁盘时，通知里填这个占位。
#:   写不了盘是运维问题，而「项目经理不知道有人失败了」是产品问题——
#:   **绝不能因为前者，就把后者一起丢掉。**
REPORT_FAILED_NO_FILE = msg("engine.039")

#: [v0.29 问题四] 各条失败路径的原因文案。写成常量是为了让「原因」这一格
#:   在日志、报告、项目经理通知三处**逐字一致**——查起来能对得上。
STOP_REASON_USER = msg("engine.040")
#: [v0.31 Bug1] 重建执行器并重试之后**仍然**开机即报被打断——只能如实认账。

# ═══════════════════════════════════════════════════════════════
# [v0.31 Bug2] 项目经理**永不静默**——空回复的分类与兜底
#
# ## 病根（截图逐帧对上了）
#
#   「主管」的发送者行弹出来 → 气泡里什么都没有 → 用户连问三句「你在吗」
#   → 每一轮都一样。机制：agent_thinking 挂上打字气泡 → 回合以**空 final**
#   收场（清单见下）→ 空 message 把气泡落定成不可见 → 屏幕上只剩下那行名字。
#
#   空 final 的来路不止一条，v0.30 把它们一视同仁地静默了：
#     ① 合法的静默：NOTHING_TO_ADD / 派活后复读被摘（卡就是话）
#        / 被更新的用户消息或拒绝打断（接棒的回合马上说话）
#     ② 病理的静默：LLM 调用出错（上下文炸了、网络断了）
#        / 模型真的一个字没吐 / 看门狗把整段回复摘空 / 中断标记残留
#   ①静默是设计；②静默是死亡——用户没有任何线索，模型的历史里却记着
#   「我答过了」，下一轮它理直气壮地重复同一句被拦的话，死循环。
#
# ## 这一版的规矩
#
#   ②类空回复**必须**换成一条用户看得见的红字（原因说人话），同时：
#     · 把模型历史里那条「已发送」的回复改写成拦截标记——它得知道用户没看到；
#     · 下一轮的上下文里塞一条一次性的恢复指引——教它此刻该怎么把话说对。
#   宁可屏幕上多一条红字，不许再有一个只剩名字的空气泡。
# ═══════════════════════════════════════════════════════════════

# ═══ [v0.33 Bug2] LLM 错误 → 人话（单点翻译，三条出口共用） ═══
#
#   provider_client 已经把 402/403 在源头译成人话；这里再兜一层是给
#   **老错误串与未知错误**的：日志/回放里可能还躺着 v0.32 时代的
#   「意外状态码 402：{…json…}」，以及任何我们没料到的报错。
#   翻译成对 = 用户能照着做点什么；键（key）用于连环同错去重。
def _humanize_llm_error(
    err: str,
    binding: dict[str, str] | None = None,
) -> tuple[str, str]:
    """
    ``(去重键, 给人看的话)``，按**当前生效绑定**翻译。

    v0.44.4 的旧实现无论请求实际发往哪里，都把 401/402/429 写成 DeepSeek；
    模型切到 Z.AI 后，底层明明在请求 ``api.z.ai``，界面却反过来让用户检查
    ``DEEPSEEK_API_KEY``。这里不再从错误旧串猜厂商，而是把当前 binding 一并交给
    provider_identity：旧版残留文案也会按当前 provider/model 重新生成。
    """
    b = binding or {}
    return humanize_provider_error(
        err,
        provider=b.get("provider", ""),
        base_url=b.get("base_url", ""),
        model=b.get("model", ""),
    )



# ═══════════════════════════════════════════════════════════════
# [v0.9a B-2] 注入模板 —— Harness 唯一的「智能」就是这几段硬话
#
# 写法上有一条铁律：**注入的格式，必须和 Harness 真正落盘的格式一模一样。**
# 让模型按 A 格式写、Harness 按 B 格式存，那是在教它说一门没人听的语言。
# 所以下面每一段都标出「这一段填进哪个工具参数」——模型填参数，Harness 填模板。
# ═══════════════════════════════════════════════════════════════


# _engine_block("COORDINATOR_HANDOFF_CONTEXT") 已外置 → prompts/<lang>/engine_blocks.md（见 _engine_block）
# _engine_block("TEAM_CONTEXT") 已外置 → prompts/<lang>/engine_blocks.md（见 _engine_block）
#: [v0.22 问题三] 推 Worker 继续干的那句话。
#:
#: 语气是刻意挑的：**不是骂它违规，是提醒它「用户还在等」**。
#: 「你违反了出口规则」对模型没有意义；「屏幕那头有个人在等你的下文」有意义——
#: 后者能让它接着干活，前者只会让它道个歉、然后还是不干活。


#: [v0.22 问题二] **拼在项目经理上下文最末尾**的行动契约。
#:
#: 为什么要有这么一块，而它又为什么必须在**最后**：
#:
#:   量一下就知道了。项目经理的系统提示词现在是 ~10,000 字（人设 7,148 + 团队 + 能力清单
#:   + 项目根 + 交接 + 项目上下文 + 知识图谱 + 用户已看到的 + 报告通知）。
#:   而「不调工具 = 什么都没发生」这条铁律，写在人设的**第 14 行**——
#:   它后面还压着 212 行人设、加上七八个上下文块。模型读完那条规则之后，
#:   还要再读近万字，然后才开口说话。
#:
#:   这就是 PRD 说的「核心规则被稀释」。而 v0.16 / v0.20 / v0.21 每一版都在往里加东西
#:   （项目隔离、能力清单、用户已看到的、沉默暗号）——**我自己就加了后面两块**。
#:   每一块单看都有理由，合起来就把最重要的那条压到了水面以下。
#:
#: 治法不是把新东西删掉（它们各自都在解决真问题），而是**认账**：
#:   注意力对首尾最敏感。开头已经被人设占了，那就把最要命的那条**再钉在末尾一遍**。
#:   这不是「重复写两遍」——是把它放在模型**开口前读到的最后一样东西**上。
#:
#: 刻意写得短。这里每多一句，它自己就少一分力。
#: [v0.22 问题二] **拼在项目经理上下文最末尾**的行动契约。
#:
#: [v0.27] 这一版把它整个重写了。老版本的写法是「**别说** X」——列了一串禁语，
#: 然后我们花了 v0.22~v0.25 四个版本，在纠正器里追着补检测规则，
#: 每次都被换了说法的项目经理绕过去。
#:
#: ★ 老版本还有一个更要命的毛病：**它自己留了口子**。
#:     「决定要派活，就在这一条回复里把 propose_next 调掉。
#:       别只写一句「我让 XX 去做」**然后就结束回合**」
#:   ——「别只写」的言下之意是「写了再调就行」。于是「先说一句」成了合法动作，
#:   而模型做了便宜的那半（说），漏了贵的那半（调）。
#:   （人设里那句「你可以先用一句话说明你要做什么」是同一个口子，一并拆了。）
#:
#: 新版本换了两件事：
#:   ① **从禁令改成处方**：不是「别说 XX 去做」，而是「派活 = 调工具 + 闭嘴」。
#:      模型对「做 Y」的服从度远高于「别做 X」——「别做 X」还得它先想起 X 是什么。
#:   ② **把判断标准挪到句子里面**：老规矩要它判断「我这轮调工具了吗」，
#:      那是关于它自己动作的事实，句子表达不了，它只能猜。
#:      新规矩要它判断「我这句是在**问**还是在**宣布**」——**这个从句子本身就看得出来**。
# _engine_block("ACTION_CONTRACT") 已外置 → prompts/<lang>/engine_blocks.md（见 _engine_block）
#: [v0.22 问题二] 事实块：此刻到底有没有人在干活。
#:
#: 项目经理撒的那个谎（「Shiloh 正在执行这个任务」）之所以能出口，是因为
#: 「谁在干活」对他来说是一片空白 —— 他只能靠自己上一句话往下圆。
#: 引擎恰恰**确切知道**这件事。摊开给他看，谎就得当着事实的面说；
#: 而用户追问「她去了吗」的时候，他也终于有个地方可以照实回答。
#: （这块很短，且大部分时候只有一行——它买到的确定性远超它占的位置。）
# _engine_block("WORK_STATUS_CONTEXT") 已外置 → prompts/<lang>/engine_blocks.md（见 _engine_block）
NO_ONE_WORKING = (
     msg("engine.041")
    + msg("engine.042")
    + msg("engine.043")
)
STATE_HEADER = (
     "═══════════════════════════════════════════\n"
    + msg("engine.044")
    + msg("engine.045")
    + msg("engine.046")
    + "═══════════════════════════════════════════"
)

def _strip_instruction_fence(text: str) -> str:
    """
    [v0.26] 模型很爱把「只输出正文」理解成「用 ``` 包起来的正文」。

    系统提示里已经明说不要围栏了，它照样时不时来一个 —— 与其跟它拉扯，不如在门口拆掉。
    同理拆掉「修改后的指令：」这类抬头。
    **规矩里说了不算数的部分，就在代码里兜住**（这条从 v0.22 起反复验证过）。
    """
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()[1:]                       # 开围栏（可能带语言标注）
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    for head in (msg("engine.047"), msg("engine.048"), msg("engine.049"), msg("engine.050"),
                 msg("engine.051"), msg("engine.052")):
        if t.startswith(head):
            t = t[len(head):].strip()
    return t


# [v1.0.23.3] 辅助 LLM 提取的四方向建议：宽容解析（方案 B，正文零污染）
def _parse_suggestions_json(raw: str) -> list[dict[str, str]]:
    """解析辅助 LLM 的 JSON 响应。0 个/格式错 → [](无按钮)；≥5 → 前 4。绝不抛异常。"""
    if not isinstance(raw, str) or not raw.strip():
        return []
    m = re.search(r"\{.*\}", raw.strip(), re.S)
    if not m:
        return []
    try:
        payload = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("suggestions")
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, str]] = []
    for it in raw_items[:4]:                    # ≥5 → 只取前 4
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        items.append({"title": title, "sub": str(it.get("sub") or "").strip()})
    return items


# [v1.0.23.10] 建议提取的对话上下文：控制 token 消耗——最多 4 条、每条截断 400 字符。
#   只取尾部最近几条（约 2 轮问答），够辅助 LLM 站在用户视角判断下一步，又不撑爆输入。
def _format_suggestion_context(
    history: list[dict[str, str]],
    max_msgs: int = 4,
    max_chars: int = 400,
) -> str:
    parts: list[str] = []
    for entry in history[-max_msgs:]:
        role = "用户" if entry.get("role") == "user" else "助手"
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        if len(content) > max_chars:
            content = content[:max_chars] + "…"
        parts.append(f"{role}：{content}")
    return "\n".join(parts)


#: [v0.9d Issue 1] 队里一个人都没有时说的话。
#:   光说「（还没有成员）」，模型会自己脑补一句「所以我什么也做不了」——把下一步直接给它。
EMPTY_TEAM = (
     msg("engine.053")
    + msg("engine.054")
    + msg("engine.055")
    + msg("engine.056")
)

EMPTY_ARCHIVE = msg("engine.057")

#: A framework control marker is stripped only when it is the complete response.
#: Markdown wrappers and surrounding whitespace are accepted as formatting of the same
#: marker; a sentence merely containing the token is ordinary model text and is preserved.
NOTHING_TO_ADD_MARK = "NOTHING_TO_ADD"
_NOTHING_TO_ADD_RX = re.compile(
    r"\A[\s>*_`\-]*" + NOTHING_TO_ADD_MARK + r"[\s*_`。.!！]*\Z"
)


def _strip_control_markers(text: Any) -> str:
    """Strip only an exact complete framework marker at persistence boundaries."""
    if not isinstance(text, str):
        return ""
    if _NOTHING_TO_ADD_RX.fullmatch(text):
        return ""
    return text


#: [v0.21 问题一] 项目经理开口之前必须先看一眼：**用户已经读过哪些话**。
#:
#: Worker 公开结果不再绕开 Runtime Delivery。旧实现曾走独立出站链，
#: `deliver_worker_speech`——它明确「不写 handoffs、不唤醒项目经理」，于是项目经理
#: **根本不知道成员当着用户的面说过话**（各 agent 的消息历史是各自独立的，
#: 引擎的 self.history 只喂给平台层的知知，不喂给 Harness 里的项目经理）。
#:
#: 所以老版本那句「别复读用户已经看到的」对项目经理来说是**无法执行的**：
#: 他看不见用户看到了什么，拿什么去避免重复？让一个瞎子别踩线，先得给他线。
#: 这个块就是那条线。

#: 每条已读发言在注入时截断多少字——够项目经理认出「这句他说过了」就行，
#: 不是让他重读一遍。
SEEN_SPEECH_CLIP = 400

def _new_report_notice(listed: str) -> str:
    """新报告通知块（发给项目经理 prompt，msg 化）。"""
    return msg("engine.302", listed=listed)

_SOULS = Path(__file__).parent / "souls"






# [v1.0.21.3] 上下文块按语言读取（prompts/<lang>/engine_blocks.md，按 <!-- NAME --> 切分）
_ENGINE_BLOCKS_CACHE: dict[str, dict[str, str]] = {}


def _engine_block(name: str) -> str:
    """按当前语言取上下文块；缺失返回空串。"""
    from . import runtime_settings as _rs
    lang = _rs.language() or "zh"
    lang = lang if lang in ("zh", "en") else "zh"
    if lang not in _ENGINE_BLOCKS_CACHE:
        blocks: dict[str, list[str]] = {}
        cur: str | None = None
        try:
            raw = (Path(__file__).parent / "prompts" / lang / "engine_blocks.md").read_text(encoding="utf-8")
        except OSError:
            raw = ""
        for line in raw.splitlines():
            m = re.match(r"<!-- (\w+) -->", line)
            if m:
                cur = m.group(1)
                blocks[cur] = []
            elif cur is not None:
                blocks[cur].append(line)
        _ENGINE_BLOCKS_CACHE[lang] = {k: "\n".join(v).strip() for k, v in blocks.items()}
    return _ENGINE_BLOCKS_CACHE[lang].get(name, "")


def _read_soul(name: str, *, lang: str | None = None) -> str:
    # [v1.0.21.3] 语言化：按当前语言从 souls/<lang>/ 读；en 未翻译前回退 zh（行为与旧版一致）。
    p = _SOULS / f"{name}.txt"
    try:
        text = prompt_resolver.read_soul(name, lang=lang) if prompt_resolver else ""
        if not text:  # 回退：resolver 不可用或模板缺失 → 直接读默认路径
            text = p.read_text("utf-8")
        return text.strip()
    except OSError:
        log.error("读不到 SOUL 文件：%s —— 这个 agent 会没有人设", p)
        return ""


# [v0.37] 群内私聊的场景说明块。拼在成员人设之后，把「这是一对一私聊」讲清楚：
#   ★ 只对话，不在这里跑工作/交报告/组队/派活——那些是群里的事。私聊里就是直接聊。
#   ★ 私聊 ≠ 隐秘：说清楚这段对话项目经理事后会知道要点（对应三级记忆的 harness 层），
#     免得模型自以为可以背着团队承诺什么。
# ═══════════════════════════════════════════════════════════════
# [v0.37.1] 群内私聊：完整回合（含工具）+ 事件分流
#
#   私聊回合在自己的 asyncio task 里跑；task 创建时会**拷贝**当前 context，所以在回合
#   协程顶端 set 这两个 ContextVar，只影响这一条 task，与并发的群回合天然隔离——
#   绝不会把某条群事件误发进私聊、或把私聊回复漏进群。
# ───────────────────────────────────────────────────────────────
#   · _DM_CHANNEL_VAR：本回合的回复频道。私聊是 dm:{project}:{agent}；群聊 @ 是 project_id。
#   · _DM_FRAMING_VAR：'worker' / 'coordinator' / 'worker_mention'，_run_agent_turn 据它
#     给临时人设追加「用户直达这个 Agent」的场景说明。
# ═══════════════════════════════════════════════════════════════
_DM_CHANNEL_VAR: contextvars.ContextVar["str | None"] = contextvars.ContextVar(
    "knowe_dm_channel", default=None,
)
_DM_FRAMING_VAR: contextvars.ContextVar["str | None"] = contextvars.ContextVar(
    "knowe_dm_framing", default=None,
)
# Completion-triggered coordinator turns can be replayed after a crash.  Attach the
# durable notification id to the final coordinator message so Hub event-id dedupe makes
# a rerun invisible to users.
_COMPLETION_NOTIFICATION_VAR: contextvars.ContextVar["str | None"] = contextvars.ContextVar(
    "knowe_completion_notification", default=None,
)
# [v1.0.13][R1][R4] Turn-local structured input and replay idempotency.
_STRUCTURED_NOTIFICATION_VAR: contextvars.ContextVar["dict[str, Any] | None"] = contextvars.ContextVar(
    "knowe_structured_notification", default=None,
)
_TURN_IDEMPOTENCY_VAR: contextvars.ContextVar["str | None"] = contextvars.ContextVar(
    "knowe_turn_idempotency", default=None,
)
# [v1.0.19.4] 本回合的用户附件（provider 内容块）。与 _DM_CHANNEL_VAR 同一套 task-局部
#   机制：在回合协程顶端 set、finally reset，天然随 await 链传到 _run_agent_turn，
#   不必给 _harness_turn / _process_turn / _process_turn_inner 逐层加参数。
_TURN_ATTACHMENTS_VAR: contextvars.ContextVar["list[dict[str, Any]] | None"] = contextvars.ContextVar(
    "knowe_turn_attachments", default=None,
)
# Actor-local visible execution identity.  Context is copied into stream/tool callback
# tasks, so every emitted phase can inherit the exact actor/scope/channel without a
# global "current Worker" slot.  Child Worker attempts replace this value at their
# execution boundary and restore the parent Coordinator scope on exit.
_ACTIVITY_SCOPE_VAR: contextvars.ContextVar["dict[str, str] | None"] = contextvars.ContextVar(
    "knowe_activity_scope", default=None,
)

_CORRELATED_EVENT_TYPES: frozenset[str] = frozenset({
    "agent_active", "agent_idle", "agent_thinking", "tool_gen", "tool_start",
    "tool_complete", "stream_delta", "stream_reset", "message", "error",
    "completion_status", "completion_view_v1", "report_submitted",
    # [v1.0.23.3] 推理增量/建议卡片必须与 stream_delta 同 scope——
    #   前端按 (agent_id, scope_id, channel_id) 找 streaming item 累积；
    #   不加进白名单 → 无 scope_id → 前端匹配不上 → 推理被静默丢弃。
    "reasoning_delta", "suggestions",
})

#: 私聊回合里**永远发到群聊频道**的事件（管理/花名册动作：验收 4/13）。
#:   项目经理在私聊里 propose_* 弹的审批卡本就由 Gate 直接发到 project_id（绕过 emit），
#:   这里再兜住经 self.emit 出站的花名册事件——它们属于群，不该只在私聊窗口出现。
_DM_GROUP_ALWAYS_EVENTS: frozenset[str] = frozenset({
    "approval_card", "approval_resolved",
    "agents_created", "agent_removed", "instruction_injected",
})

# [v0.37.1] Worker 版私聊场景：**鼓励直接干活**（读/写/跑命令），干完在私聊里直接汇报，
#   不用等项目经理审批。只有需要多人协作/架构级决策才建议回群。

# [v0.44.5] 群聊 @Worker：执行语义复用私聊直通回合，唯一区别是所有可见事件仍落在群时间线。
#   单独写一块而不是复用上面的「私聊」文案，避免模型误以为用户看不见群里的状态/文件卡。

# [v0.37.1] 项目经理版私聊场景：**鼓励调度**。项目经理在私聊里可以直接调 propose_*，
#   卡片会弹到群里，用户切回去就能审批——不用等回群里再提。
# _engine_block("_DM_FRAMING_COORD") 已外置 → prompts/<lang>/engine_blocks.md（见 _engine_block）
def _dm_short(text: str, limit: int = 60) -> str:
    """把一段私聊内容压成给 harness 摘要看的一句话（去多余空白、超长截断）。"""
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[:limit].rstrip() + "…"


def _memory_inline(value: Any, limit: int) -> str:
    """把历史摘要压成一行；历史文本不能借换行伪装成新的提示词块。"""
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _memory_friendly_time(value: Any, *, now: datetime | None = None) -> str:
    """ISO 时间转中文友好时间；解析失败也只给温和的兜底文案。"""
    raw = str(value or "").strip()
    if not raw:
        return msg("engine.058")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if now is not None and now.tzinfo is not None:
            zone = now.tzinfo
        else:
            zone = datetime.now().astimezone().tzinfo or timezone.utc
        local = parsed.astimezone(zone)
        current = (now or datetime.now(timezone.utc)).astimezone(zone)
    except (TypeError, ValueError, OverflowError):
        fallback = raw[:16].replace("T", " ")
        return fallback or msg("engine.058")

    delta = (current.date() - local.date()).days
    if delta == 0:
        day = msg("engine.059")
    elif delta == 1:
        day = msg("engine.060")
    elif delta == 2:
        day = msg("engine.061")
    elif delta == -1:
        day = msg("engine.062")
    elif local.year == current.year:
        day = msg("engine.063")
    else:
        day = msg("engine.064")

    hour = local.hour
    if hour < 5:
        period = msg("engine.065")
    elif hour < 9:
        period = msg("engine.066")
    elif hour < 12:
        period = msg("engine.067")
    elif hour < 14:
        period = msg("engine.068")
    elif hour < 18:
        period = msg("engine.069")
    else:
        period = msg("engine.070")
    shown_hour = hour % 12 or 12
    return f"{day}{period} {shown_hour}:{local.minute:02d}"


def _memory_clue_source(row: dict[str, Any]) -> str:
    name = _memory_inline(row.get("agent_name"), 20)
    agent_id = _memory_inline(row.get("agent_id"), 20)
    if name and name.casefold() not in {"unknown", "none", "null"}:
        return msg("engine.007") if name == COORDINATOR else name
    if agent_id == COORDINATOR:
        return msg("engine.007")
    source = _memory_inline(row.get("source"), 20)
    return source or agent_id or msg("engine.071")


def _memory_clue_summary(row: dict[str, Any], limit: int) -> str:
    """把确定性历史摘要里的机械标签收成一句自然提示，仍只使用已保存事实。"""
    raw = _memory_inline(
        row.get("summary") or row.get("match") or msg("engine.072"),
        280,
    )
    raw = re.sub(r"^(?:用户提出|收到内部任务)\s*[:：]\s*", "", raw)
    parts = re.split(r"\s*[；;]\s*本轮产出\s*[:：]\s*", raw, maxsplit=1)
    if len(parts) == 2:
        topic = parts[0].strip(" ；;，,。")
        outcome = parts[1].strip(" ；;，,")
        if not topic:
            raw = outcome
        elif not outcome or len(topic) >= max(12, limit - 16):
            # 主题本身已经够完整时不硬塞“本轮产出”，避免一句话在结尾被截成半句。
            raw = topic
        else:
            room = max(10, limit - len(topic) - 1)
            raw = f"{topic.rstrip('。')}；{_memory_inline(outcome, room)}"
    raw = re.sub(r"\s*[；;]\s*[；;]+\s*", "；", raw).strip(" ；;，,")
    return _memory_inline(raw or msg("engine.072"), limit)


def _memory_topic_tokens(value: Any) -> set[str]:
    """给多样化排序用的轻量 token；中文用二元片段，不依赖分词库。"""
    text = str(value or "").casefold()
    tokens = set(re.findall(r"[a-z0-9][a-z0-9_.:+/#-]{1,}", text))
    for chunk in re.findall(r"[\u3400-\u9fff]+", text):
        if len(chunk) <= 6:
            tokens.add(chunk)
        tokens.update(chunk[i:i + 2] for i in range(max(0, len(chunk) - 1)))
    return tokens


def _memory_set_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def _select_diverse_memory_clues(
    candidates: list[dict[str, Any]], limit: int,
) -> list[dict[str, Any]]:
    """相关性优先，同时避免同一回合或近乎同一句摘要占满三条。"""
    remaining = list(candidates)
    selected: list[dict[str, Any]] = []
    seen_turns: set[int] = set()
    while remaining and len(selected) < max(1, limit):
        scored: list[tuple[float, float, str, dict[str, Any]]] = []
        for row in remaining:
            turn = row.get("turn")
            if isinstance(turn, int) and turn > 0 and turn in seen_turns:
                continue
            topic_tokens = row.get("_topic_tokens") or set()
            query_tokens = row.get("_keywords") or set()
            similarity = max(
                (max(
                    _memory_set_similarity(
                        topic_tokens, old.get("_topic_tokens") or set(),
                    ),
                    0.75 * _memory_set_similarity(
                        query_tokens, old.get("_keywords") or set(),
                    ),
                ) for old in selected),
                default=0.0,
            )
            # 关键词命中数是主信号；相似度只负责把同主题的近重复往后推。
            score = float(row.get("_score") or 0.0) - 9.0 * similarity
            scored.append((score, similarity, str(row.get("memory_id") or ""), row))
        if not scored:
            break
        scored.sort(key=lambda item: (item[0], -item[1], item[2]), reverse=True)
        _, similarity, _, best = scored[0]
        remaining.remove(best)
        # 已有线索与它几乎同义时宁可少给一条，也不拿三个近重复挤占 prompt。
        if selected and similarity >= 0.84:
            continue
        selected.append(best)
        turn = best.get("turn")
        if isinstance(turn, int) and turn > 0:
            seen_turns.add(turn)
    return selected


def _render_memory_clues(rows: list[dict[str, Any]]) -> str:
    """渲染有界中文线索块；预算不足时先缩摘要、再减条数，不截断 memory_id。"""
    if not rows:
        return ""
    header = msg("engine.ctx.memory_clues")

    def render(subset: list[dict[str, Any]], summary_limit: int) -> str:
        parts = [header]
        for row in subset:
            memory_id = str(row.get("memory_id") or "").lower()
            parts.extend([
                "",
                f"- {_memory_friendly_time(row.get('timestamp'))} · "
                + f"{_memory_clue_source(row)} | {_memory_clue_summary(row, summary_limit)}",
                msg("engine.073", memory_id=memory_id),
            ])
        return "\n".join(parts)

    for count in range(len(rows), 0, -1):
        subset = rows[:count]
        for summary_limit in (_MEMORY_CLUE_SUMMARY_CHARS, 48, 38):
            block = render(subset, summary_limit)
            if len(block) <= _MEMORY_CLUE_MAX_CHARS:
                return "\n\n" + block
    return ""


def _agent_memory_status_label(value: Any) -> str:
    """把 worklog 的机器状态翻成短中文；未知中文状态原样保留。"""
    clean = " ".join(str(value or "").split()).strip()
    if not clean:
        return ""
    key = clean.casefold().replace("-", "_").replace(" ", "_")
    labels = {
        "completed": msg("engine.074"), "complete": msg("engine.074"), "done": msg("engine.074"),
        "success": msg("engine.074"), "succeeded": msg("engine.074"),
        "failed": msg("engine.075"), "failure": msg("engine.075"), "error": msg("engine.075"),
        "partial": msg("engine.076"), "blocked": msg("engine.077"),
        "in_progress": msg("engine.078"), "working": msg("engine.078"),
        "pending": msg("engine.079"), "cancelled": msg("engine.080"), "canceled": msg("engine.080"),
    }
    return labels.get(key, clean if re.search(r"[\u3400-\u9fff]", clean) else "")


def _agent_memory_short_date(value: Any) -> str:
    """worklog 的 ISO 时间只显示成 ``07-17``；解析失败时给温和兜底。"""
    raw = str(value or "").strip()
    match = re.match(r"^\d{4}-(\d{2})-(\d{2})", raw)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return msg("engine.081")


def _agent_memory_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _agent_worklog_fields(lines: list[str]) -> dict[str, str]:
    """解析 ``- 字段：值``，并把直接对话的缩进引用并回对应字段。"""
    fields: dict[str, str] = {}
    current = ""
    for line in lines:
        matched = re.match(r"^-\s*([^：:\n]{1,40})\s*[：:]\s*(.*)$", line)
        if matched:
            current = matched.group(1).strip()
            fields[current] = matched.group(2).strip()
            continue
        if current and line.startswith((" ", "\t")):
            continuation = re.sub(r"^\s*>\s?", "", line).strip()
            if continuation:
                old = fields.get(current, "")
                fields[current] = f"{old} {continuation}".strip()
            continue
        current = ""
    return fields


def _parse_agent_worklog(text: str) -> list[dict[str, Any]]:
    """按 ``## 时间戳 · …`` 整段解析 worklog，并保留字符边界供尾部去重。"""
    body = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        return []
    headings = list(_AGENT_WORKLOG_HEADING_RE.finditer(body))
    rows: list[dict[str, Any]] = []
    for ordinal, heading in enumerate(headings):
        start = heading.start()
        end = headings[ordinal + 1].start() if ordinal + 1 < len(headings) else len(body)
        raw_segment = body[start:end]
        markdown = raw_segment.strip()
        visible_end = start + len(raw_segment.rstrip())
        header = heading.group("header").strip()
        parts = [part.strip() for part in header.split("·")]
        if len(parts) < 2:
            continue

        timestamp = parts[0]
        step: int | None = None
        keyword = ""
        status = ""
        meta = parts[1:]
        step_match = _AGENT_WORKLOG_STEP_RE.fullmatch(meta[0])
        if step_match:
            step = int(step_match.group(1))
            detail = " · ".join(meta[1:]).strip() or msg("engine.082")
            status_match = re.fullmatch(r"(.*?)[（(]([^（）()]{1,32})[）)]", detail)
            if status_match:
                keyword = status_match.group(1).strip()
                status = status_match.group(2).strip()
            else:
                keyword = detail
        elif meta[0] == msg("engine.083"):
            keyword = msg("engine.083")
            for item in meta[1:]:
                linked = _AGENT_WORKLOG_LINKED_STEP_RE.fullmatch(item)
                if linked:
                    step = int(linked.group(1))
                    break
        else:
            # 兼容未来仍以同一标题结构写入的个人记录，不因新标签丢整段。
            keyword = " · ".join(meta).strip()

        fields = _agent_worklog_fields(markdown.splitlines()[1:])
        artifacts_raw = fields.get(msg("engine.084"), "")
        artifacts = [
            item.strip() for item in re.split(r"\s*[,，]\s*", artifacts_raw)
            if item.strip()
        ]
        completed_what = fields.get(msg("engine.085"), "") or fields.get(msg("engine.086"), "")
        step_token = f"{step:02d}" if isinstance(step, int) else "chat"
        compact_time = re.sub(r"[^0-9A-Za-z]", "", timestamp)[:20] or "unknown"
        digest = hashlib.sha1(markdown.encode("utf-8", errors="replace")).hexdigest()[:8]
        rows.append({
            "timestamp": timestamp,
            "step": step,
            "keyword": keyword or msg("engine.082"),
            "status": status,
            "status_zh": _agent_memory_status_label(status),
            "completed_what": completed_what,
            "artifacts": artifacts,
            "task": fields.get(msg("engine.087"), ""),
            "issues": fields.get(msg("engine.088"), ""),
            "self_check": fields.get(msg("engine.089"), ""),
             "memory_id": f"am-{step_token}-{compact_time}-{digest}",
            "markdown": markdown,
            "_start": start,
            "_end": visible_end,
            "_ordinal": ordinal,
            "_search": markdown.casefold(),
        })
    return rows


def _agent_memory_match_score(row: dict[str, Any], query: str) -> float | None:
    """纯文本匹配：完整短语优先，空白/标点拆出的词用于温和召回。"""
    clean = " ".join(str(query or "").split()).casefold()
    if not clean:
        return None
    haystack = str(row.get("_search") or "")
    phrase_hit = clean in haystack
    terms = {
        term for term in re.split(r"[\s,，、;；|/]+", clean)
        if term
    }
    term_hits = sum(1 for term in terms if term in haystack)
    if not phrase_hit and not term_hits:
        return None
    keyword = str(row.get("keyword") or "").casefold()
    artifacts = " ".join(str(item) for item in row.get("artifacts") or ()).casefold()
    return (
        (24.0 if phrase_hit else 0.0)
        + 5.0 * term_hits
        + (4.0 if clean and clean in keyword else 0.0)
        + (3.0 if clean and clean in artifacts else 0.0)
    )


def _agent_memory_public_row(row: dict[str, Any]) -> dict[str, Any]:
    """去掉字符偏移/排序分数，只把完整个人日志事实交给当前 Worker。"""
    return {
        "timestamp": row.get("timestamp") or "",
        "date": _agent_memory_short_date(row.get("timestamp")),
        "step": row.get("step"),
        "keyword": row.get("keyword") or "",
        "status": row.get("status") or "",
        "status_zh": row.get("status_zh") or "",
        "completed_what": row.get("completed_what") or "",
        "artifacts": list(row.get("artifacts") or []),
        "memory_id": row.get("memory_id") or "",
        "markdown": row.get("markdown") or "",
    }


def _agent_memory_duplicate_of_project(
    row: dict[str, Any], project_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> bool:
    """同一时间点且同一主题/产出时，Project Memory 已提醒过就不再重复。"""
    if not project_rows:
        return False
    agent_text = " ".join([
        str(row.get("keyword") or ""),
        str(row.get("completed_what") or ""),
        " ".join(str(item) for item in row.get("artifacts") or ()),
    ]).casefold()
    agent_tokens = _memory_topic_tokens(agent_text)
    agent_time = _agent_memory_datetime(row.get("timestamp"))
    artifact_names = {
        re.split(r"[/\\]", str(item))[-1].casefold()
        for item in row.get("artifacts") or () if str(item).strip()
    }
    keyword = str(row.get("keyword") or "").strip().casefold()
    for project in project_rows:
        project_text = " ".join(
            str(project.get(key) or "") for key in ("summary", "match")
        ).casefold()
        if not project_text:
            continue
        project_time = _agent_memory_datetime(project.get("timestamp"))
        close_time = False
        same_day = False
        if agent_time is not None and project_time is not None:
            close_time = abs((agent_time - project_time).total_seconds()) <= 15 * 60
            same_day = agent_time.date() == project_time.date()
        artifact_hit = any(name and name in project_text for name in artifact_names)
        keyword_hit = len(keyword) >= 2 and keyword != msg("engine.083") and keyword in project_text
        project_tokens = _memory_topic_tokens(project_text)
        overlap = len(agent_tokens & project_tokens)
        similarity = _memory_set_similarity(agent_tokens, project_tokens)
        if close_time and (artifact_hit or keyword_hit or overlap >= 2):
            return True
        if same_day and (artifact_hit or keyword_hit or (overlap >= 4 and similarity >= 0.28)):
            return True
    return False


def _select_agent_memory_clues(
    candidates: list[dict[str, Any]],
    project_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    limit: int = _AGENT_MEMORY_CLUE_LIMIT,
) -> list[dict[str, Any]]:
    """相关性优先，去掉 Project Memory 重复项和个人日志内部近重复项。"""
    duplicate_step_dates = {
        (int(row["step"]), _agent_memory_short_date(row.get("timestamp")))
        for row in candidates
        if isinstance(row.get("step"), int)
        and _agent_memory_duplicate_of_project(row, project_rows)
    }
    ordered = sorted(
        (
            row for row in candidates
            if not _agent_memory_duplicate_of_project(row, project_rows)
            and not (
                isinstance(row.get("step"), int)
                and (int(row["step"]), _agent_memory_short_date(row.get("timestamp")))
                in duplicate_step_dates
            )
        ),
        key=lambda row: (float(row.get("_score") or 0.0), int(row.get("_ordinal") or 0)),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    seen_step_dates: set[tuple[int, str]] = set()
    for row in ordered:
        step = row.get("step")
        step_date = (
            (step, _agent_memory_short_date(row.get("timestamp")))
            if isinstance(step, int) else None
        )
        if step_date is not None and step_date in seen_step_dates:
            continue
        tokens = row.get("_topic_tokens") or set()
        if any(
            _memory_set_similarity(tokens, old.get("_topic_tokens") or set()) >= 0.84
            for old in selected
        ):
            continue
        selected.append(row)
        if step_date is not None:
            seen_step_dates.add(step_date)
        if len(selected) >= max(1, limit):
            break
    return selected


def _agent_memory_artifacts_inline(values: Any, limit: int) -> str:
    names: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        name = re.split(r"[/\\]", str(value).strip())[-1]
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    if not names:
        return ""
    out: list[str] = []
    for name in names:
        candidate = "、".join([*out, name])
        if len(candidate) <= limit:
            out.append(name)
            continue
        if not out:
            return _memory_inline(name, limit)
        suffix = msg("engine.090")
        return _memory_inline("、".join(out), max(1, limit - len(suffix))) + suffix
    return "、".join(out)


def _render_agent_memory_clues(rows: list[dict[str, Any]]) -> str:
    """渲染 300 字符内的「你做过什么」线索块。"""
    if not rows:
        return ""
    header = msg("engine.ctx.history")

    def render(subset: list[dict[str, Any]], done_limit: int, artifact_limit: int) -> str:
        parts = [header]
        for row in subset:
            step = row.get("step")
            keyword = _memory_inline(row.get("keyword") or msg("engine.091"), 24)
            if isinstance(step, int):
                title = msg("engine.092")
                if keyword:
                    title += f" · {keyword}"
            else:
                title = keyword or msg("engine.091")
            status = str(row.get("status_zh") or "")
            if status:
                title += f"（{status}）"
            parts.extend(["", f"- {_agent_memory_short_date(row.get('timestamp'))} · {title}"])
            done = _memory_inline(row.get("completed_what") or "", done_limit)
            if done:
                parts.append(msg("engine.093", done=done))
            artifacts = _agent_memory_artifacts_inline(row.get("artifacts"), artifact_limit)
            if artifacts:
                parts.append(msg("engine.094", artifacts=artifacts))
        return "\n".join(parts)

    for count in range(min(len(rows), _AGENT_MEMORY_CLUE_LIMIT), 0, -1):
        subset = rows[:count]
        for done_limit, artifact_limit in ((72, 72), (52, 52), (36, 36), (24, 28)):
            block = render(subset, done_limit, artifact_limit)
            if len(block) + 2 <= _AGENT_MEMORY_CLUE_MAX_CHARS:
                return "\n\n" + block
    return ""


def build_agent() -> AgentPort:
    """按 KNOWE_AGENT 选档。默认 fake（零 token）。**保留，向后兼容。**"""
    kind = CONFIG.agent.lower()
    if kind in ("deepseek", "real", "llm"):
        from .agents.deepseek import DeepSeekAgent
        return DeepSeekAgent()
    return FakeAgent()


def harness_mode() -> bool:
    return CONFIG.agent.lower() in ("deepseek", "real", "llm", "knowe", "harness")


class ProjectEngine:
    """一个项目的引擎。"""

    def __init__(
        self,
        hub: Hub,
        project_id: str,
        agent: AgentPort | None = None,
        client_factory: Any | None = None,
        workspace_root: Path | str | None = None,
        internal_workspace_root: Path | str | None = None,
        backend_data_root: Path | str | None = None,
        store: Store | None = None,
        memory_manager: "MemoryManager | None" = None,
        knowledge_manager: "KnowledgeGraphManager | None" = None,
        activity_callback: Callable[[str, str], None] | None = None,
        worker_runtime_factory: Any | None = None,
        project_name: str | None = None,
    ) -> None:
        self.hub = hub
        self.project_id = project_id
        # [v1.0.23.3] 项目显示名（群聊名）：建群时 server 传入，welcome_worker
        #   问候语用它；改名时经 rename_project_references 同步刷新。
        self._project_display_name = project_name or ""
        # [v1.0.23.3] 已发过初入群问候的 worker —— 幂等闸，防审批重试/恢复路径重复问候。
        self._welcomed: set[str] = set()
        self._worker_runtime_factory = worker_runtime_factory or DEFAULT_WORKER_RUNTIME_FACTORY
        # [v0.44.8] 群聊列表偏好由 server 持久化、由 Harness 镜像到每个存活引擎。
        # 它们不改变记忆/执行语义，只决定客户端如何呈现通知与排序；保留在引擎上，
        # 让重建、热换绑和诊断都能读到同一份全局状态，而不是各窗口自说自话。
        self._conversation_pinned = False
        self._conversation_muted = False
        self._conversation_folded = False
        self._conversation_pinned_at = 0
        # 捕获引擎初始化时的当前称呼；设置随后变化时，下一轮 Coordinator prompt
        # 能精确识别改名并追加一次性“旧称作废”对冲提示。
        self._last_user_address_name = runtime_settings.user_name(default="")
        # [v0.44 设置 §3.3] 造 Gate 前把项目登进 contextvar：万一 gate 在构造期就读一次
        #   CONFIG.approval_timeout_s（缓存型实现），读到的也是**本群**的裁决值。
        #   with 退出即复位——不污染调用方（server 的 WS 任务会连续构造多个项目的引擎）。
        with runtime_settings.project_context(project_id):
            self.gate = Gate(hub, project_id)
        # [v0.30 Bug2/3] 卡落定 → 它名下的 in-flight 反馈调整立刻作废（见 gate._settle）。
        self.gate.settle_listener = self._cancel_feedback_flight

        # ═══ [v0.30 Bug2/3] 「我有新意见」的**串行化**状态 ═══
        #
        #   v0.29 的现场：用户连点两次「我有意见」→ 两次 aux LLM 调用并发在跑 →
        #   谁后返回谁改卡面，而用户确认时闸门取的是**那一刻**卡上的字——
        #   三个时间点互相赛跑，旧反馈可能压过新反馈。
        #
        #   这一版的规矩只有一条：**一张卡，同一时刻至多一条反馈在飞。**
        #     · 新反馈进来 → 取消旧的 in-flight task（cancel 之后结果作废，不改卡）
        #     · 卡落定（approve/reject/timeout/cancel 四条路）→ 取消它名下的 flight
        #     · 世代号（_feedback_gen）兜底：万一 cancel 没来得及生效，
        #       过期世代的结果也**不许**碰卡面
        self._feedback_flights: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._feedback_gen: dict[str, int] = {}

        # [v0.11 C-1] 三层 Memory 的看门人（server 建一个全局实例传进来）。
        #   不传 → Memory 功能静默关闭（测试 / 纯内存模式），主流程照跑。
        self._memory: "MemoryManager | None" = memory_manager
        #: [v0.11 C-1 / v0.44.5] 本项目累计回合数。
        # 这里只是构造期占位；业务/内部目录绑定完成后立刻从 Project Memory 恢复，
        # 绝不再把“进程启动次数”误当成“项目从零开始”。
        self._turn_count = 0
        #: [v0.11 C-1] 排出去的记忆更新任务（fire-and-forget，别让它阻塞回合）。
        self._memory_tasks: set[asyncio.Task[Any]] = set()
        # 同一项目的摘要必须按回合顺序串行落盘；否则两个 fire-and-forget 任务可能后发先至，
        # 旧回合反过来覆盖新回合，正是“状态偶尔倒退”的隐蔽来源。
        self._memory_tail: asyncio.Task[Any] | None = None

        # [v0.19] 项目知识图谱与 Project Memory 一样是后台增强层：写 handoff 后只排任务，
        # 不等待辅助 LLM；同一项目严格串行，避免旧来源后写覆盖新来源。
        self._knowledge: "KnowledgeGraphManager | None" = knowledge_manager
        self._knowledge_tasks: set[asyncio.Task[Any]] = set()
        self._knowledge_tail: asyncio.Task[Any] | None = None

        # ═══ [v0.42 知识系统重构] 资产层（真知识库）+ 知知蒸馏 ═══
        #
        #   情节层（上面的 _knowledge）降级为证据库；真正被 agent 复用的是这里的
        #   五类资产（knowledge_assets.py）。蒸馏（T1）与合并（T2）走**主模型档位**
        #   （KNOWE_KG_DISTILL_MODEL，缺省回落 DEEPSEEK_MODEL）——报告的判断：
        #   蒸馏质量直接决定整个系统上限，是不计成本条款的第一花钱处。
        #   一切资产任务照旧排在 _enqueue_knowledge_update 的串行尾巴上，
        #   与情节层摄入天然有序、共用同一把 per-root 锁。
        self._assets = None
        if self._knowledge is not None and project_id != "__platform__":
            try:
                from .knowledge_assets import KnowledgeAssetManager
                self._assets = KnowledgeAssetManager(
                    self._knowledge, distill_call=self._knowledge_distill_call,
                )
            except Exception:
                log.exception("[%s] 资产层初始化失败（知识库退回情节层模式）", project_id)
        #: T2 触发计数：每 N 个 approved 任务合并一次（另有兜底定时器）。
        self._approved_since_consolidate = 0
        self._knowledge_consolidate_timer: asyncio.Task[Any] | None = None

        # [v0.15] 项目动态有两条消费链：
        #   · Project Memory 做长期、语义化摘要（可异步、允许稍慢）；
        #   · Harness Memory 要回答“此刻发生了什么”（必须事件驱动、不能等摘要 LLM）。
        # 因此引擎保留一小段确定性的实时动态，并通过回调通知 server 去合并刷新公告栏。
        self._activity_callback = activity_callback
        self._recent_project_activity: list[str] = []
        # 收到 instruction 后尚未形成 Runtime Delivery 的 Worker。用于在异常
        # 收尾时撤销“正在执行”事实，避免 Harness 永远停在“刚开始工作”。
        self._workers_with_open_activity: set[str] = set()

        # [v0.37.4 Bug2] 其中**因私聊而忙**的那部分（子集）。用来在 _work_status_ctx 里
        #   区分「正在群里执行派活」和「正在私聊里直接处理用户请求」——项目经理据此知道
        #   这个人是被用户私聊拉去干活了（不是他派的），也就不会去重复派活。
        self._dm_busy: set[str] = set()
        # [v0.44.5] `_dm_busy` 中由群聊 @直达触发的子集。忙碌判定完全复用 DM，
        #   只为给项目经理更准确的现场文案（「被群里点名」而不是「正在私聊」）。
        self._mention_busy: set[str] = set()

        # [v1.0.24.4] 权威活动账本：每条经 emit 出站的 agent_active 记一笔
        #   (agent_id, scope_id, channel_id) → started_at_ms，agent_idle 对销。
        #   键与前端 activeScopes 完全同构（前端 activityIdentityKey 同款三元组）。
        #   唯一用途：replay_complete / state_snapshot 随附全量条目，前端以账本为
        #   基准整体校准花名册——瞬时事件无论丢多少都能自愈到引擎现场真实状态。
        #   记账/销账只在 emit（唯一出站口）一处发生，禁止第二处维护。
        self._open_activity: dict[tuple[str, str, str], int] = {}

        # Engine、Runtime 与 Completion 共用同一个任务模型。该映射既是待执行队列，
        # 也是当前 Worker 的权威任务底账；WAITING/终态由 Completion 投影销账。
        self._task_envelopes: dict[str, TaskEnvelope] = {}
        # [v0.29 问题二] 用户按了「停止」→ 先在这儿写下原因，再去打断他。
        #   为什么不在 stop_worker 里直接生成失败报告：那样会有**两个**地方
        #   往 handoffs 里写失败报告（这里一个、回合收尾的 finally 一个），
        #   而「同一件事有两个入口」正是这个代码库反复踩坑的形状。
        #   这里只留一张纸条，落盘和通知永远由 Runtime 结果归并链处理。
        self._stop_reasons: dict[str, str] = {}
        # Worker scheduling reads directly from ``_task_envelopes``.
        self._worker_turns: dict[str, asyncio.Task[None]] = {}
        self._worker_runtime_runs: dict[str, TaskRun] = {}
        # Runtime completion → Project Memory is an Engine boundary side effect.  Keep
        # one in-process idempotency ledger so retries/replayed DeliveryRecords do not
        # increment Project Memory twice for the same authoritative outcome.
        self._worker_memory_keys: set[str] = set()
        # Wave 5-7 stores are lazy so pure unit tests and platform projects do not create
        # runtime directories during construction.  Once opened, all terminal state and
        # projection replay uses these exact instances.
        self._completion_store_instance: SQLiteCompletionStore | None = None
        self._completion_projector_instance: CompletionProjector | None = None
        self._completion_recovery_task: asyncio.Task[Any] | None = None
        self._completion_notifications_queued: set[str] = set()
        # Completion UI is a monotonic projection.  Keep the last visible version so
        # outbox replay and crash recovery cannot regress a terminal card or duplicate
        # the authoritative message.
        self._completion_visible_versions: dict[str, int] = {}
        # [v1.0.13][R4] Open lazily after the internal workspace is bound.
        self._seen_speech_ledger_instance: SeenSpeechLedger | None = None
        # Artifact-scoped locks are shared by every Worker in this project.  Locks are
        # acquired in sorted order, so overlapping targets serialize without deadlock
        # while disjoint targets remain fully concurrent.
        self._artifact_locks: dict[str, asyncio.Lock] = {}
        self._artifact_locks_guard = asyncio.Lock()

        # ── [v0.36] 本轮产出文件的暂存台账 ──
        #
        #   agent_id → [{path, name, ext, kind, bytes, mtime}, …]，按产出顺序、path 去重。
        #   谁写文件（safe_write_file / copy_external_file），工具层就往这里记一笔
        #   （见 tools_knowe.note_file_produced 的调用点）。
        #
        #   它的谢幕时机和「气泡落定」严格对齐：这个 Worker 本轮**带正文**的那条
        #   message 发出时，把台账里的文件附到该事件上、清空（见 emit()）。
        #   本轮若始终没说话（只交了报告、或空返回），回合收尾 _settle_worker_turn
        #   会把残留清掉——绝不让上一轮的文件漏进下一轮那条消息里。
        #
        #   为什么记在引擎而不是塞进工具回执：回执是给**模型**读的（让它别重复写），
        #   而文件卡片是给**用户**看的附载，两者走两条道。放引擎里，一处记账，
        #   emit 一处消费，和 v0.35 之前所有「回合级临时态」同构。
        self._files_produced: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

        # [v0.8a A-1] 花名册落盘用的那本账。不传 → 不落盘（测试 / 纯内存模式）。
        self._store: Store | None = store

        # [v0.7 A0] 项目目录 —— Worker 的沙箱就绑在这里。
        #   用户在「新建项目」弹窗里选的那个目录会一路传到这儿；
        #   [任务 1.7] 不再有任何默认目录兜底：没传目录 → workspace_root 直接抛错。
        self._workspace_root: Path | None = Path(workspace_root) if workspace_root else None

        # [v0.16] Agent 内部文件的唯一物理根。它与 workspace_root 生命周期解耦：
        # 用户重新选择业务目录时，交接记录、成员记忆和项目记忆仍留在同一个内部空间。
        self._internal_workspace_root: Path | None = (
            Path(internal_workspace_root) if internal_workspace_root else None
        )
        self._backend_data_root: Path | None = (
            Path(backend_data_root) if backend_data_root else None
        )
        self._internal_layout_ready = False
        self._internal_migration_errors: list[str] = []

        # [v0.44.5 Project Memory v2] 目录已经绑定，可以从 `.context.json` 与历史尾记录
        # 共同恢复累计回合数。历史先写、快照后写，所以两处取最大值才能覆盖断电缝隙。
        self._restore_project_memory_counter()

        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()   # 工作面
        self.history: list[dict[str, str]] = []
        self._task: asyncio.Task[None] | None = None
        self._stop_lock = asyncio.Lock()
        self._stop_task: asyncio.Task[list[str]] | None = None
        self._stopping = False
        self._stopped = False
        self._stop_error: ProjectResourceCloseError | None = None
        self._turns_active = 0                       # 正在跑的回合数
        self._fired: set[asyncio.Task[Any]] = set()  # 回调排出去的广播任务

        # ── 单 agent 老路（知知 / Fake 档） ──
        self.agent: AgentPort | None = agent
        if self.agent is None and not harness_mode():
            self.agent = build_agent()

        # ── Harness（真 LLM 档） ──
        self._client_factory = client_factory
        self._agents: dict[str, KnoweAgent] = {}
        self._roster: dict[str, str] = {}          # agent_id → role（花名册，不含项目经理）
        self._reports: set[str] = set()            # 交过的报告（去重）
        self._restoring = False                    # [v0.8a A-1] 温载中 → 不回写磁盘
        self._history_warned = False               # [v0.8c #7] 「找不到历史」只喊一次
        # 用户刚拒绝审批时，收口回合保持 propose_next 冻结，也不消耗普通用户回答重试。
        self._rejection_pending = False
        #   一次拒绝只排一个收口回合，别让队列攒出好几条互相打架的话
        self._rejection_followup_queued = False
        # ── [v0.30 Bug5] 项目经理回合的互斥锁 ──
        #   今天项目经理的回合只从主循环里起（结构上天然串行），这把锁不改变任何行为；
        #   它是给明天上的保险：v0.29 已经证明「把回合搬出主循环」这种重构会发生，
        #   而项目经理回合一旦并发，两条流的 emit 会交错、上下文会互相覆盖——
        #   气泡闪现消失就是那类竞态的长相。**排队，永远不丢弃。**
        self._coordinator_lock = asyncio.Lock()

        # Hotfix：审批通过并完成副作用的动作证据。它比“messages 里出现过工具名”更可靠：
        # 被拒绝/超时的调用不算完成，审批后的收口轮次即使丢了上一段 tool_call 也不会误判。
        self._committed_actions_this_turn: set[str] = set()
        # 本回合已提交派活的成员 id；这是动作/生命周期证据，不参与正文过滤。
        self._dispatched_this_turn: list[str] = []
        # v0.13：流式文本先在 Harness 内聚合，最终统一脱敏后再出站，避免 id/路径跨 chunk 泄露。
        self._stream_buffers: dict[tuple[str, str, str], list[str]] = {}
        # [v0.37 → v0.44.5] Agent 直达回合（私聊 / 群聊 @）的在飞回复任务。
        #   回复走后台 task（不占 WS 读循环、不进群回合的 inbox/gate），存一份引用防被 GC；跑完自摘。
        self._dm_tasks: set[asyncio.Task[None]] = set()
        # Worker 同一时刻至多一条直达回合（busy 守卫保证）。单独按人登记有两个用途：
        #   ① 成员面板的「停止」能真正 interrupt 私聊 / 群聊 @回合；
        #   ② 被 interrupt 的共享 Agent 实例要等这条回合确实结束后再退休重建。
        # coordinator 不进 stop_worker/busy 这套，所以不登记在这里。
        self._direct_turns: dict[str, asyncio.Task[None]] = {}
        # [v0.37.1 → v0.44.5] 直达时对方正忙 → 暂存，等他忙完自动补发。
        #   agent_id → [(content, reply_channel, group_mention), …]。
        #   暂存消息**不写任何 memory**（还没送到他嘴里，不算对话）。
        self._dm_pending: dict[str, list[tuple[str, str, bool]]] = {}

        # Hub 是最终出站口；即使 Gate 或其他调用方绕过 engine.emit，也会经过这个守卫。
        # Lightweight compatibility/test hubs may intentionally omit the optional
        # outbound-filter registry; production Hub supplies it.
        set_public_text_filter = getattr(self.hub, "set_public_text_filter", None)
        if callable(set_public_text_filter):
            set_public_text_filter(self.project_id, self._sanitize_outbound)

        # ── [v0.9a B] 交接账本 ──
        self._handoff: HandoffBook | None = None   # 懒建（绑定 internal_workspace/handoffs）
        #: agent_id → 他手上那一步的序号（instruction-NN 派下去时记下，交 report-NN 时用）
        self._worker_step: dict[str, int] = {}
        #: agent_id → 他手上那一步的关键词（报告文件名要用同一个词，才认得出是一对）
        self._worker_keyword: dict[str, str] = {}
        #: [v0.9c] agent_id → 名字（「前端 1」）。权威在花名册，这里只是内存里的一份副本。
        self._names: dict[str, str] = {}
        #: [v0.44.12] add_member 在落盘前判定「首次加入 / 从归档恢复」，
        #: `_record_activity_from_event` 在 agents_created 到达时消费。
        #:
        #: 不能等事件发生后再查花名册：那时 status 已经被 upsert 成 active，
        #: 「恢复」和「新建」已经不可区分。这里保存的是这段极短时序缝里的事实。
        self._pending_member_activity: dict[str, str] = {}
        #: 已经跟项目经理说过的报告文件（开机时用磁盘上现有的报告播种 —— 见 _new_reports）
        self._reports_told: set[str] | None = None

        self.coordinator_soul = _read_soul("coordinator")

    # ═══════════════════════════════════════════════════════════
    # [v0.44.8] 群聊列表偏好 + 项目名全局迁移
    # ═══════════════════════════════════════════════════════════
    def apply_conversation_preferences(
        self, *, pinned: bool, muted: bool, folded: bool, pinned_at: int = 0,
    ) -> None:
        """镜像 server 的持久偏好；pinned/folded 在 Harness 层再次强制互斥。"""
        folded = bool(folded)
        pinned = bool(pinned) and not folded
        self._conversation_pinned = pinned
        self._conversation_muted = bool(muted)
        self._conversation_folded = folded
        self._conversation_pinned_at = max(0, int(pinned_at or 0)) if pinned else 0

    def conversation_preferences(self) -> dict[str, Any]:
        """诊断/重建时读取，不把展示状态混进模型提示词。"""
        return {
            "pinned": self._conversation_pinned,
            "muted": self._conversation_muted,
            "folded": self._conversation_folded,
            "pinned_at": self._conversation_pinned_at,
        }

    @staticmethod
    def rewrite_project_name_tree(
        root: Path | str, old_name: str, new_name: str,
    ) -> dict[str, Any]:
        """给未启动/被隔离的项目也能执行同一套内部文件迁移。"""
        return rewrite_project_name_tree(root, old_name, new_name)

    def rename_project_references(self, old_name: str, new_name: str) -> dict[str, Any]:
        """Refresh Harness-owned state and Coordinator history after a project rename."""
        old_name = str(old_name or "")
        new_name = str(new_name or "")
        if not old_name or not new_name or old_name == new_name:
            return {"updated": 0, "scanned": 0, "errors": []}
        for attr in (
             "history",
            "_recent_project_activity",
            "_task_envelopes",
                        "_stop_reasons",
            "_dm_pending",
            "_project_display_name",   # [v1.0.23.3] 群聊名随改名刷新（问候语用）
        ):
            if hasattr(self, attr):
                setattr(self, attr, _replace_project_name_value(getattr(self, attr), old_name, new_name))
        agents: list[Any] = []
        if self.agent is not None:
            agents.append(self.agent)
        coordinator = self._agents.get(COORDINATOR)
        if coordinator is not None:
            agents.append(coordinator)
        for agent in agents:
            try:
                messages = _agent_history(agent)
                if isinstance(messages, list):
                    messages[:] = _replace_project_name_value(messages, old_name, new_name)
            except Exception:
                log.exception("[%s] 改名时刷新 Coordinator 历史失败", self.project_id)
        proj = self.hub.projects.get(self.project_id)
        if proj is not None and isinstance(proj.pending_card, dict):
            proj.pending_card = _replace_project_name_value(proj.pending_card, old_name, new_name)
        report = rewrite_project_name_tree(self.internal_workspace, old_name, new_name)
        if report.get("errors"):
            log.warning("[%s] 项目改名迁移有 %d 个文件失败", self.project_id, len(report["errors"]))
        return report

    # ═══════════════════════════════════════════════════════════
    # Token telemetry (v0.48) — provider control flow is never coupled to accounting
    # ═══════════════════════════════════════════════════════════
    async def _run_conversation_tracked(
        self, agent: Any, *args: Any, **kwargs: Any,
    ) -> dict[str, Any]:
        """Run an agent turn and consume its per-provider-call usage immediately."""
        result = await agent.run_conversation(*args, **kwargs)
        try:
            self._persist_token_usage(agent, result)
        except Exception:
            # This is intentionally the last firewall: no telemetry parser, model name,
            # roster row or filesystem error may alter the LLM turn's return value.
            log.debug(
                 "[%s] %s Token 统计旁路失败（忽略）",
                self.project_id,
                getattr(agent, "agent_id", "unknown"),
                exc_info=True,
            )
        return result

    def _persist_token_usage(self, agent: Any, result: Any) -> None:
        if not isinstance(result, dict):
            return

        # Always consume private telemetry fields.  They are an implementation detail and must
        # not leak into the normal turn state machine even in an ephemeral/no-store engine.
        raw_calls = result.pop("_token_usage_calls", [])
        aggregate = result.pop("_token_usage", None)
        result_model = str(result.pop("_token_usage_model", "") or "").strip()
        if self._store is None:
            return
        calls = (
            [row for row in raw_calls if isinstance(row, dict)]
            if isinstance(raw_calls, list) else []
        )
        # [v1.0.34-实测v2] 落盘诊断：calls 数量与内容唯一性
        try:
            import json as _json
            uniq = {_json.dumps(c, sort_keys=True, ensure_ascii=False) for c in calls}
            log.warning("[persist-debug] agent=%s n_calls=%d n_uniq=%d ts=%s",
                        getattr(agent, "agent_id", "?"), len(calls), len(uniq),
                        datetime.now().astimezone().isoformat(timespec="seconds"))
        except Exception:
            pass

        # Compatibility with a provider adapter that exposes only one turn-level aggregate.
        # Native KnoweAgent emits the detailed list, so this path is never used there.
        if not calls and isinstance(aggregate, dict):
            try:
                if int(aggregate.get("calls") or 0) > 0:
                    calls = [{
                        "input_tokens": aggregate.get("input_tokens"),
                        "output_tokens": aggregate.get("output_tokens"),
                        "total_tokens": aggregate.get("total_tokens"),
                        "cache_hit_input": aggregate.get("cache_hit_input"),
                        "cache_miss_input": aggregate.get("cache_miss_input"),
                        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                    }]
            except (TypeError, ValueError, OverflowError):
                calls = []
        if not calls:
            return

        agent_id = str(getattr(agent, "agent_id", "") or "").strip()
        if not agent_id:
            log.debug("[%s] Token 统计缺 agent_id；跳过 %d 条", self.project_id, len(calls))
            return
        if agent_id == COORDINATOR:
            role = "coordinator"
        else:
            role = str(
                getattr(agent, "role", "") or self._roster.get(agent_id, "") or "Worker"
            ).strip()
        agent_name = str(getattr(agent, "name", "") or "").strip()

        provider_cfg = getattr(agent, "_provider_cfg", None)
        provider = str(getattr(provider_cfg, "provider", "") or "").strip()
        model = (
            result_model
            or str(getattr(provider_cfg, "model", "") or "").strip()
            or "unknown"
        )
        persisted = 0
        for call in calls:
            timestamp = str(call.get("timestamp") or "").strip()
            if not timestamp:
                timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.astimezone()
                ts = int(parsed.timestamp())
            except (TypeError, ValueError, OverflowError):
                ts = int(datetime.now().astimezone().timestamp())

            hit = int(call.get("cache_hit_input") or 0)
            miss = int(call.get("cache_miss_input") or 0)
            if "cache_miss_input" not in call:
                # Legacy collector rows carry only input/output totals.
                input_total = int(call.get("input_tokens") or 0)
                miss = max(0, input_total - hit)
            output = int(call.get("output_tokens") or 0)

            record: dict[str, Any] = {
                "ts": ts,
                "project_id": self.project_id,
                "agent_id": agent_id,
                "agent_role": role,
                "agent_name": agent_name,
                "provider": provider,
                "model": model,
                "usage": {
                    "cache_hit_input": hit,
                    "cache_miss_input": miss,
                    "output": output,
                },
            }
            # [v1.0.34-M4] 上下文占用三数随回合落盘：
            #   compression_count / saved_chars = 本回合自动压缩台账（快照即清零，
            #   落盘顺序=回合结束顺序，聚合按范围求和即「本会话累计」）
            #   context_usage_pct = 投影后估算 token ÷ 模型窗口（agent 回合内计算
            #   并附在 result 私有字段，见 knowe_agent；缺失时省略，前端显示 --）
            compression = snapshot_compression_stats(reset=True)
            if compression["count"] > 0:
                record["compression"] = {
                    "count": compression["count"],
                    "saved_chars": compression["saved_chars"],
                    "by_method": compression["by_method"],
                }
            context_pct = result.pop("_context_usage_pct", None)
            if isinstance(context_pct, (int, float)) and 0 <= context_pct <= 100:
                record["context_usage_pct"] = round(float(context_pct), 2)
            # [v1.0.34-M4-v2] 投影保留条数随回合落盘：agent 已算好投影后保留的消息条数
            #   （projected_message_count 随 result dict 返回，见 knowe_agent），这里随记录
            #   写出供前端「自动精简」行使用。
            projected_count = result.get("projected_message_count", 0)
            if isinstance(projected_count, int) and projected_count > 0:
                record["projected_message_count"] = projected_count
            cny_cost = estimate_cost(
                model, cache_hit_input=hit, cache_miss_input=miss, output=output,
                currency="CNY",
            )
            usd_cost = estimate_cost(
                model, cache_hit_input=hit, cache_miss_input=miss, output=output,
                currency="USD",
            )
            if cny_cost is not None:
                record["price_cny"] = cny_cost
            if usd_cost is not None:
                record["price_usd"] = usd_cost
            # [v1.0.24.4] 遥测落盘走持久化队列，不占主循环；提交即计数。
            # [v1.0.34-实测v2] 闭包捕获修复：lambda 必须绑定本次循环的 _record 快照，
            # 否则后台队列延迟执行时 _record 已被循环覆盖成最后一条 → N 次调用全写同一行。
            _store, _pid, _record = self._store, self.project_id, record
            _store.defer_bg(
                lambda _s=_store, _p=_pid, _r=dict(_record): _s.append_token_usage(_p, _r),
                description="PM token 用量",
            )
            persisted += 1

        if persisted:
            log.debug(
                 "[%s] %s Token 统计已提交落盘：model=%s calls=%d/%d",
                self.project_id, agent_id, model, persisted, len(calls),
            )

    def _persist_worker_token_usage(self, worker_id: str, run: Any) -> None:
        """Persist one Worker runtime's accumulated ModelUsage (M1 采集点 B).

        Telemetry only: any failure here is swallowed and must never disturb the
        Worker turn's result.
        """
        if self._store is None:
            return
        usage = getattr(run, "usage", None)
        if usage is None:
            return
        try:
            input_total = int(getattr(usage, "input_tokens", 0) or 0)
            output = int(getattr(usage, "output_tokens", 0) or 0)
            hit = (
                int(getattr(usage, "cache_read_tokens", 0) or 0)
                + int(getattr(usage, "cache_write_tokens", 0) or 0)
            )
        except (TypeError, ValueError, OverflowError):
            return
        if input_total == 0 and output == 0 and hit == 0:
            return
        miss = max(0, input_total - hit)

        binding = runtime_settings.model_binding_for(self.project_id, worker_id)
        binding = binding or {}
        model = str(binding.get("model") or "").strip() or "unknown"
        provider = str(binding.get("provider") or "").strip()
        role = str(self._roster.get(worker_id, "") or "").strip() or "worker"
        try:
            agent_name = self.member_name(worker_id)
        except Exception:
            agent_name = role

        record: dict[str, Any] = {
            "ts": int(datetime.now().astimezone().timestamp()),
            "project_id": self.project_id,
            "agent_id": worker_id,
            "agent_role": role,
            "agent_name": agent_name,
            "provider": provider,
            "model": model,
            "usage": {
                "cache_hit_input": hit,
                "cache_miss_input": miss,
                "output": output,
            },
        }
        # [v1.0.34-M4] Worker 回合压缩台账同样随落盘快照（与 PM 路径同口径）
        compression = snapshot_compression_stats(reset=True)
        if compression["count"] > 0:
            record["compression"] = {
                "count": compression["count"],
                "saved_chars": compression["saved_chars"],
                "by_method": compression["by_method"],
            }
        cny_cost = estimate_cost(
            model, cache_hit_input=hit, cache_miss_input=miss, output=output,
            currency="CNY",
        )
        usd_cost = estimate_cost(
            model, cache_hit_input=hit, cache_miss_input=miss, output=output,
            currency="USD",
        )
        if cny_cost is not None:
            record["price_cny"] = cny_cost
        if usd_cost is not None:
            record["price_usd"] = usd_cost
        try:
            # [v1.0.24.4] 遥测落盘走持久化队列，不占主循环；提交即计数。
            # [v1.0.34-实测v2] 闭包捕获修复（同 PM 路径）：绑定 _record 快照。
            _store, _pid, _record = self._store, self.project_id, record
            _store.defer_bg(
                lambda _s=_store, _p=_pid, _r=dict(_record): _s.append_token_usage(_p, _r),
                description="Worker token 用量",
            )
            log.debug(
                 "[%s] %s Token 统计已提交落盘：model=%s hit=%d miss=%d out=%d",
                self.project_id, worker_id, model, hit, miss, output,
            )
        except Exception:
            log.debug("[%s] Worker Token 统计提交失败", self.project_id, exc_info=True)

    # ═══════════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════════
    def start(self) -> None:
        if getattr(self, "_stopping", False) or getattr(self, "_stopped", False):
            raise RuntimeError(msg("engine.095"))
        # 在接收第一条消息之前完成旧快照迁移、context.md v2 渲染和计数二次校准。
        # 全是本地确定性 I/O；迁移冲突或真实 I/O 失败必须阻止项目启动，
        # 仅无关的旧内容渲染异常允许在 Memory 子系统内降级。
        self._prepare_project_memory()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name=f"engine:{self.project_id}")

        # Completion Store is authoritative after a crash.  Recovery is deliberately
        # ordered: repair the narrow legacy terminal-row window, replay CompletionEvent
        # projections. No step invokes WorkerRuntime or repeats file/tool side effects.
        if self._completion_recovery_task is None or self._completion_recovery_task.done():
            async def _recover_completion() -> None:
                try:
                    self.completion_store.reconcile_terminal_runs()
                    await self.reconcile_completion_outbox()
                    self._recover_waiting_completions()
                    await self._recover_completion_notifications()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("[%s] Completion/outbox recovery failed", self.project_id)

            self._completion_recovery_task = asyncio.create_task(
                _recover_completion(),
                name=f"completion-recover:{self.project_id}",
            )
            self._fired.add(self._completion_recovery_task)
            self._completion_recovery_task.add_done_callback(self._fired.discard)

        # [v0.9a B-2 ②] ★ 开机这一刻，把磁盘上**已经存在**的报告全部记作「说过了」。
        #
        #   这一步必须在引擎起来的时候做，不能等「第一次要发通知」时才懒惰地做——
        #   那样会有一个致命的时序缝：Worker 在项目经理的第一个回合之前就交了报告
        #   （链式调度、崩溃恢复都可能），那份**真正的新报告**会被当成陈年旧账吞掉，
        #   项目经理永远收不到通知。（这个洞是自测跑出来的，不是想出来的。）
        #
        #   反过来，不播种的话，每次重启项目经理都会被一堆老报告砸一遍——
        #   跟 v0.8e #3 那条「每开机补一条『已加入项目』」是同一种病。
        if self.agent is None and self._reports_told is None:
            self._seed_reports_told()

        # [v0.19] 老项目第一次升级时，把已有 handoff 异步补进图谱。任务排队后立即返回，
        # 不延长引擎启动；后续新来源会接在 bootstrap 尾部，天然保持顺序。
        if self.project_id != "__platform__":
            self._schedule_knowledge_bootstrap()
            # [v0.41 知识库视图] 把本项目的图谱挂上前端数据面（本机 HTTP，只读 + 用户裁决）。
            #   start() 一定在事件循环里跑（上面刚 create_task），loop 现取现存——
            #   数据面的写入要靠它把用户裁决打回这条循环，与 ingest 同锁串行。
            #   注册与开门皆幂等 best-effort：失败只留日志，绝不把引擎带走。
            if self._knowledge is not None:
                try:
                    knowledge_api.register_project(
                        self.project_id,
                        manager=self._knowledge,
                        workspace_provider=lambda: self.internal_workspace,
                        loop=asyncio.get_running_loop(),
                        assets=self._assets,   # [v0.42] 资产层端点（assets/review/profile…）
                    )
                except Exception:
                    log.debug("[%s] 知识库数据面注册失败（忽略）", self.project_id, exc_info=True)
            # [v0.43] 老项目可能早已有 core 知识，但上次关机后没有新写入触发导出。
            # 启动时把它排在 bootstrap 尾链上补成「待审项目经验技能」，不阻塞开机。
            if self._assets is not None:
                self._enqueue_knowledge_update(
                    lambda: self._assets.sync_skillpacks(
                        self.project_id, self.internal_workspace,
                    ),
                    label="skillpacks-bootstrap",
                )
            # [v0.42] T2 兜底定时器（nightly 语义）。approved 满 N 的主触发在 commit_handoff_step。
            self._start_consolidate_timer()

    def _seed_reports_told(self) -> None:
        """把此刻磁盘上的报告全部记作「已通知」。失败也不许把引擎带走。"""
        try:
            self._reports_told = {self.handoff_ref(p) for p in self.handoff_reports()}
            if self._reports_told:
                log.info("[%s] 交接账本：%d 份历史报告（不再重复通知项目经理）",
                         self.project_id, len(self._reports_told))
        except Exception:
            log.exception("[%s] 扫描历史报告失败 —— 这一轮按「没有历史报告」跑",
                          self.project_id)
            self._reports_told = set()

    async def _cancel_and_join(self, tasks: Iterable[asyncio.Task[Any]]) -> None:
        pending = [task for task in dict.fromkeys(tasks) if task is not asyncio.current_task()]
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._fired.difference_update(pending)

    async def _run_stop_once(self, *, immediate: bool) -> list[str]:
        """Own the teardown independently from any one caller's cancellation."""
        try:
            result = await self._stop_once(immediate=immediate)
        except asyncio.CancelledError:
            # Process shutdown may ultimately cancel the whole event loop.  Do not claim that
            # resources are closed when teardown did not reach its explicit close boundary.
            raise
        except ProjectResourceCloseError as exc:
            self._stop_error = exc
            self._stopped = True
            raise
        except Exception as exc:
            detail = " ".join(str(exc).split()) or exc.__class__.__name__
            wrapped = ProjectResourceCloseError(
                self.project_id,
                [msg("engine.096", detail=detail)],
            )
            self._stop_error = wrapped
            self._stopped = True
            raise wrapped from exc
        else:
            self._stopped = True
            return result

    async def stop(self, *, immediate: bool = False) -> list[str]:
        """Close one Engine exactly once; concurrent callers share one shielded barrier.

        A caller timeout must not cancel the actual teardown and then let deletion create a new
        engine beside still-open SQLite/file handles.  The owned task therefore outlives any
        individual waiter; later callers join the same task.
        """
        async with self._stop_lock:
            if self._stop_error is not None:
                raise self._stop_error
            if self._stopped:
                return []
            if self._stop_task is not None and self._stop_task.cancelled():
                self._stop_task = None
            if self._stop_task is None:
                self._stopping = True
                self._stop_task = asyncio.create_task(
                    self._run_stop_once(immediate=immediate),
                    name=f"engine-stop:{self.project_id}",
                )
            task = self._stop_task
        return await asyncio.shield(task)

    async def _stop_once(self, *, immediate: bool = False) -> list[str]:
        self._stopping = True
        project_root = self.internal_workspace
        close_issues: list[str] = []
        # Shutdown is a cleanup barrier, not a business transaction.  One malformed
        # approval callback must not abort the rest of teardown and leave browser/
        # worker/file handles alive (permanent-delete stages the directory immediately
        # after this method returns).  Gate cancellation is therefore best-effort just
        # like the other runtime closers below.
        try:
            await self.gate.cancel_all_settled("cancelled")
        except Exception as exc:
            close_issues.append(
                 msg("engine.098")
            )
            log.warning("[%s] 关闭审批闸门失败（继续收摊）", self.project_id, exc_info=True)

        # [v0.30 Bug2/3] 卡都作废了，替它们改指令的 flight 也一起收摊。
        #   （cancel_all_settled 已经逐卡触发过落定回调；这一段是兜底：
        #     万一有 flight 的卡在此之前就落定过、而 task 因为异常没被摘干净。）
        feedback_tasks = list(self._feedback_flights.values())
        for card_id in list(self._feedback_flights):
            self._cancel_feedback_flight(card_id)
        await self._cancel_and_join(feedback_tasks)

        # Completion recovery and fire-and-forget projections are possible SQLite writers.
        # Stop them before any project-local database is closed.
        await self._cancel_and_join(list(self._fired))
        self._completion_recovery_task = None

        # ── [v0.29 问题一] 后台跑着的 Worker 回合也得收摊 ──
        #
        #   v0.28 不需要这一段：Worker 的回合活在 `self._task` 里面，取消主循环
        #   就把它们一起带走了。现在它们是独立的 task —— 不管的话，关机之后
        #   它们还在那儿烧 token、往一个已经没人听的 hub 里 emit。
        #
        #   ★ 先立 `_stopping`（上面那行）再取消：`_worker_turn_finished` 的
        #     done_callback 会去 `_spawn_pending_workers`，旗没立起来的话，
        #     取消一个、它再起一个，关不掉。
        for worker_task in list(self._worker_turns.values()):
            if not worker_task.done():
                worker_task.cancel()
        if self._worker_turns:
            await asyncio.gather(*list(self._worker_turns.values()), return_exceptions=True)
            self._worker_turns.clear()
        # 不在这儿补失败报告：关机时 hub 正在收摊，那份报告没人收得到，
        # 而 `_task_envelopes` 是内存账，本来就跟着引擎一起走。**关机不是失败。**

        # [v0.44.5] 私聊 / 群聊 @也是独立后台回合，关机时必须一起收摊。
        # 老代码只保存引用却没有 cancel：切目录后它仍可能继续烧 token、向旧频道 emit。
        direct_tasks = list(self._dm_tasks)
        for direct_task in direct_tasks:
            if not direct_task.done():
                direct_task.cancel()
        if direct_tasks:
            await asyncio.gather(*direct_tasks, return_exceptions=True)
        self._dm_tasks.clear()
        self._direct_turns.clear()
        self._dm_pending.clear()
        self._dm_busy.clear()
        self._mention_busy.clear()
        # [v1.0.24.4] 活动账本随全部回合一起收摊——引擎现场已无任何进行中的事。
        self._open_activity.clear()

        # 目录隔离/关机时，不只停调度循环，也主动中断并关闭每个 Agent 可能持有的
        # provider 会话。不同实现的关闭接口不统一，按 aclose/close 做兼容探测。
        agents = ([self.agent] if self.agent is not None else []) + list(self._agents.values())
        for agent in agents:
            interrupt = getattr(agent, "interrupt", None)
            if callable(interrupt):
                try:
                    interrupt()
                except Exception:
                    log.debug("[%s] 中断 Agent 失败（忽略）", self.project_id, exc_info=True)

        # 正常关机尽量排干 Project Memory；目录隔离必须立即断开，不等辅助 LLM。
        if self._memory_tasks:
            if immediate:
                for memory_task in list(self._memory_tasks):
                    memory_task.cancel()
                await asyncio.gather(*list(self._memory_tasks), return_exceptions=True)
            else:
                try:
                    await asyncio.wait(set(self._memory_tasks), timeout=30)
                except Exception:
                    log.exception("[%s] 关机时等待记忆更新出错（忽略）", self.project_id)

        # [v0.42] 先撤 T2 定时器：它只会往下面这条任务链里排新任务，收摊时不许再进新单。
        if self._knowledge_consolidate_timer is not None:
            self._knowledge_consolidate_timer.cancel()
            try:
                await self._knowledge_consolidate_timer
            except (asyncio.CancelledError, Exception):
                pass
            self._knowledge_consolidate_timer = None

        if self._knowledge_tasks:
            if immediate:
                for knowledge_task in list(self._knowledge_tasks):
                    knowledge_task.cancel()
                await asyncio.gather(*list(self._knowledge_tasks), return_exceptions=True)
            else:
                try:
                    _done, pending = await asyncio.wait(
                        set(self._knowledge_tasks),
                        timeout=max(0.0, float(CONFIG.knowledge_shutdown_drain_s)),
                    )
                    # 图谱是可重建增强层；关机超时后取消，下一次启动会从 handoff 自动补录。
                    for knowledge_task in pending:
                        knowledge_task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                except Exception:
                    log.exception("[%s] 关机时等待知识图谱更新出错（忽略）", self.project_id)

        # [v0.41 知识库视图] 引擎收摊 → 从前端数据面摘牌（幂等；平台/未启用图谱时是空操作）。
        try:
            knowledge_api.unregister_project(self.project_id)
        except Exception as exc:
            close_issues.append(
                 msg("engine.099")
            )
            log.debug("[%s] 知识库数据面摘牌失败（忽略）", self.project_id, exc_info=True)

        task = self._task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:      # 停机不挑食
                pass

        # [v0.20 Batch 4] ★ Worker 的浏览器会话和后台进程也得跟着收摊。
        #
        #   在这一批之前，一个回合结束就什么都不剩，stop() 只要管好 agent 会话。
        #   现在不是了：`process` 起的 `npm run dev` 会一直占着 3000 端口，
        #   headless Chromium 会一直吃着 200MB —— 它们**活得比回合长**。
        #   用户切一次项目目录（server.py:1162 走的就是这条路）就漏一份，
        #   切十次之后他会觉得 Knowe 是个内存黑洞，而且他的端口莫名其妙被占着。
        #
        #   项目没用过这些工具 → 注册表里根本没有这一项 → 直接返回，零开销。
        try:
            close_issues.extend(
                await shutdown_project_runtime(self.project_id, immediate=immediate)
            )
        except Exception as exc:
            # 关不掉一个浏览器，不能连累普通关机；永久删除会读取返回的审计项，
            # 在后续文件锁诊断里明确指出 Knowe 自身哪一类资源没有关干净。
            close_issues.append(
                 msg("engine.100")
            )
            log.warning("[%s] 关闭 Agent 运行时资源失败（忽略）", self.project_id,
                        exc_info=True)

        for agent in agents:
            closer = getattr(agent, "aclose", None) or getattr(agent, "close", None)
            if not callable(closer):
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                agent_id = str(getattr(agent, "agent_id", "?") or "?")
                close_issues.append(
                    msg("engine.101", agent_id=agent_id)
                    + (" ".join(str(exc).split()) or exc.__class__.__name__)
                )
                log.debug("[%s] 关闭 Agent 会话失败（忽略）", self.project_id, exc_info=True)

        # Any task category that survived its normal drain is cancelled and joined here.
        await self._cancel_and_join(list(self._memory_tasks))
        await self._cancel_and_join(list(self._knowledge_tasks))
        await self._cancel_and_join(list(self._dm_tasks))
        await self._cancel_and_join(list(self._worker_turns.values()))
        await self._cancel_and_join(list(self._fired))
        self._memory_tasks.clear()
        self._knowledge_tasks.clear()
        self._dm_tasks.clear()
        self._worker_turns.clear()
        self._memory_tail = None
        self._knowledge_tail = None
        self._task = None

        critical_issues: list[str] = []
        completion_projector = self._completion_projector_instance
        if completion_projector is not None:
            try:
                completion_projector.close()
            except Exception as exc:
                detail = " ".join(str(exc).split()) or exc.__class__.__name__
                close_issues.append(msg("engine.102", detail=detail))
                log.warning(
                     "[%s] Completion Projector 引用解绑失败（继续关闭 SQLite）",
                    self.project_id,
                    exc_info=True,
                )
        completion_store = self._completion_store_instance
        if completion_store is not None:
            try:
                completion_store.close()
            except Exception as exc:
                detail = " ".join(str(exc).split()) or exc.__class__.__name__
                critical_issues.append(msg("engine.103", detail=detail))
                log.exception("[%s] Completion Store SQLite 关闭失败", self.project_id)

        # Runtime/repository adapters can own additional SQLiteDatabase wrappers for
        # this same project while remaining unreachable from the Engine fields above.
        # Closing only ``_completion_store_instance`` therefore is not a sufficient
        # Windows rename barrier.  The storage layer keeps a weak registry and this
        # final sweep explicitly closes every registered project-local connection.
        sqlite_report = close_sqlite_databases_under(project_root)
        if sqlite_report.errors:
            critical_issues.extend(
                msg("engine.104", item=item) for item in sqlite_report.errors
            )
        if sqlite_report.remaining:
            critical_issues.append(
                msg("engine.105") + "、".join(sqlite_report.remaining)
            )
        if sqlite_report.closed:
            log.warning(
                 "[%s] Engine 字段之外仍有 %d 个项目 SQLite 连接，已由停止屏障强制关闭",
                self.project_id,
                sqlite_report.closed,
            )
        self._completion_store_instance = None
        self._completion_projector_instance = None
        self._completion_recovery_task = None
        self._seen_speech_ledger_instance = None
        self._handoff = None
        self._assets = None
        self.agent = None
        self._agents.clear()
        self.gate.settle_listener = None
        self._activity_callback = None
        if critical_issues:
            raise ProjectResourceCloseError(self.project_id, critical_issues)
        return close_issues

    # ═══════════════════════════════════════════════════════════
    # 工作面
    # ═══════════════════════════════════════════════════════════
    def _cancel_feedback_flight(self, card_id: str) -> None:
        """
        [v0.30 Bug2/3] 掐掉这张卡名下正在飞的那次反馈调整（如果有）。

        三个调用点，覆盖一条反馈可能变成幽灵的全部方式：
          · 新反馈进来（adjust_instruction 开头）→ 旧的作废，只留最后一次
          · 卡落定（gate._settle 的回调）→ 卡都没了，还在改它的调用必须死
          · 引擎停机（stop）→ 收摊

        cancel 之后那次调用的结果**不会**碰卡面：flight 自己会在写卡前核对世代号，
        双保险（cancel 可能来不及在 await 点生效，世代号一定生效）。
        """
        flight = self._feedback_flights.pop(card_id, None)
        if flight is not None and not flight.done():
            flight.cancel()
            log.info("[%s] 卡 %s 名下的 in-flight 反馈调整已取消", self.project_id, card_id)
        # 世代号推进一格：即使那个 task 已经跑过了 cancel 检查点，
        # 它拿着的旧世代也写不进卡面。
        self._feedback_gen[card_id] = self._feedback_gen.get(card_id, 0) + 1

    async def submit(self, content: str, attachments: list[dict[str, Any]] | None = None) -> None:
        if getattr(self, "_stopping", False) or getattr(self, "_stopped", False):
            raise RuntimeError(msg("engine.095"))
        # 用户发新消息 → 挂起的审批全部作废（§三）。
        # 正在 await 闸门的回合会收到 ApprovalCancelled，优雅收摊，新回合排队进来。
        # ★ [v0.24 问题二] 用户重新开口 = 拒绝那一页翻过去了 → 解冻。
        #
        #   冻结只该盖住「他刚说完不、项目经理收口那一下」。用户接着说
        #   「那让小王来做」——那是**新指令**，propose_next 必须立刻能用。
        #   拿一个不会自己解开的冻结去防重复弹卡，只会换来一个更蠢的 bug。
        self._rejection_pending = False
        self._rejection_followup_queued = False
        cancelled = self.gate.cancel_all("cancelled")
        if cancelled:
            log.info("[%s] 新消息到达，作废 %d 张挂起审批", self.project_id, cancelled)
        # [v0.30 Bug2/3] 被作废的卡的落定回调会在 _settle 里取消各自的反馈 flight——
        #   这里不用重复做。（cancel_all 只 set_result；真正的 _settle 在 propose
        #   协程恢复时跑，那时回调自然触发。）

        await self.inbox.put({"content": content, "target": None, "_attachments": attachments})

    def dispatch_frozen(self) -> bool:
        """
        [v0.24 问题二] 这一刻允不允许提案？

        用户刚按了拒绝、项目经理的收口回合还没跑完 → **不允许**。

        为什么要在代码里冻，而不是在 REJECTION_FOLLOWUP 里写一句「不要重新提案」——
        那句话**本来就写着**，而线上照样弹了第二张一模一样的卡。v0.22 的教训：
        prompt 单独约束不了审批期间的副作用，因此这里保留结构化拒绝态硬门禁。
        **能用代码保证的事，不要用祈使句去求。**
        """
        return self._rejection_pending

    # ═══════════════════════════════════════════════════════════
    # Wave 5-7 authoritative completion boundary
    # ═══════════════════════════════════════════════════════════
    def _completion_components(self) -> tuple[SQLiteCompletionStore, CompletionProjector]:
        """Return the one project-scoped Completion store/projector pair.

        WorkerRuntimeFactory writes ``internal_workspace/runtime/runtime.sqlite3``.  The
        Engine deliberately opens that exact database so recovery never creates a second
        Harness truth beside the Runtime truth.
        """
        if getattr(self, "_stopping", False) or getattr(self, "_stopped", False):
            raise RuntimeError(msg("engine.095"))
        root = self.internal_workspace / "runtime"
        root.mkdir(parents=True, exist_ok=True)
        if self._completion_store_instance is None:
            self._completion_store_instance = SQLiteCompletionStore(root / "runtime.sqlite3")
        if self._completion_projector_instance is None:
            self._completion_projector_instance = CompletionProjector(
                self._completion_store_instance,
                self,
            )
        return self._completion_store_instance, self._completion_projector_instance

    @property
    def completion_store(self) -> SQLiteCompletionStore:
        return self._completion_components()[0]

    @staticmethod
    def _stable_completion_event_id(completion_id: str, version: int, lane: str) -> str:
        raw = f"{completion_id}\0{max(1, int(version or 1))}\0{lane}"
        return f"cmp_{lane}_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]

    def _normalize_outbound_files(self, value: Any) -> list[dict[str, Any]]:
        """Return safe file-card rows without letting one malformed row drop a message."""
        if value is None:
            return []
        if isinstance(value, Mapping):
            rows: Sequence[Any] = (value,)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            rows = value
        else:
            rows = (value,)

        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw in rows:
            try:
                if isinstance(raw, Mapping):
                    item = dict(raw)
                else:
                    item = {
                        key: getattr(raw, key)
                        for key in (
                             "path", "name", "sha256", "size", "bytes", "media_type",
                            "kind", "ext", "mtime", "mtime_ns", "file_id", "identity",
                            "disposition", "preview_url",
                        )
                        if getattr(raw, key, None) is not None
                    }
                path = _normalize_project_path(item.get("path"))
                name = str(item.get("name") or Path(path).name).strip() or Path(path).name
                sha256 = str(item.get("sha256") or "").strip().lower()
                identity = str(item.get("identity") or item.get("file_id") or sha256).strip()
                dedupe_key = (path.casefold(), identity)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                clean = dict(item)
                clean["path"] = path
                clean["name"] = name
                suffix = Path(name).suffix.lstrip(".").lower()
                if suffix and not str(clean.get("ext") or "").strip():
                    clean["ext"] = suffix
                raw_size = clean.get("bytes", clean.get("size"))
                if raw_size not in (None, ""):
                    clean["bytes"] = max(0, int(raw_size))
                clean.pop("size", None)
                if sha256:
                    clean["sha256"] = sha256
                normalized.append(clean)
            except Exception:  # noqa: BLE001 - one bad card must never hide the message
                log.warning(
                     "[%s] 忽略不合法的文件卡片，不影响消息投递：%r",
                    self.project_id,
                    raw,
                    exc_info=True,
                )
        return normalized

    def _completion_message_text(self, event: Any, files: Sequence[Mapping[str, Any]]) -> str:
        """Build a concise, factual and actionable Worker completion sentence."""
        worker_id = str(getattr(event, "worker_id", "") or "")
        who = self.member_name(worker_id) if worker_id else msg("engine.completion.who_default")
        status = str(getattr(getattr(event, "status", ""), "value", getattr(event, "status", ""))).upper()
        delivery = getattr(event, "delivery_record", None)
        delivered_text = str(delivery.get("text") or "").strip() if isinstance(delivery, Mapping) else ""
        mechanical = {
            "task completed successfully.",
            "task completed.",
            "completed successfully.",
            "done.",
            "success.",
        }
        if delivered_text.casefold() in mechanical:
            delivered_text = ""

        details = [dict(item) for item in getattr(event, "gap_details", ()) if isinstance(item, Mapping)]
        detail_message = next(
            (str(item.get("message") or "").strip() for item in details if str(item.get("message") or "").strip()),
            "",
        )
        actions = [str(item).strip() for item in getattr(event, "next_actions", ()) if str(item).strip()]
        reason = detail_message or str(getattr(event, "terminal_reason", "") or "").strip()
        file_names = [str(item.get("name") or Path(str(item.get("path") or "")).name) for item in files]
        file_names = [name for name in file_names if name]
        file_note = ""
        if file_names:
            shown = "、".join(file_names[:3])
            more = msg("engine.completion.more_files", n=len(file_names)) if len(file_names) > 3 else ""
            file_note = msg("engine.completion.files_ready", shown=shown, more=more)

        if status == "SUCCEEDED":
            if delivered_text:
                return delivered_text + (file_note if file_note and not any(name in delivered_text for name in file_names) else "")
            return msg("engine.completion.succeeded", who=who, file_note=file_note)
        if status == "PARTIAL":
            base = delivered_text or msg("engine.completion.partial_base", who=who)
            return base + (msg("engine.completion.partial_missing", reason=reason) if reason else msg("engine.completion.partial_rest")) + (msg("engine.completion.next_step", action=actions[0]) if actions else "")
        if status == "WAITING":
            question = str(getattr(event, "question", "") or "").strip()
            base = question or reason or msg("engine.completion.waiting_base")
            return msg("engine.completion.waiting", who=who, base=base)
        if status == "BLOCKED":
            base = reason or msg("engine.completion.blocked_base")
            return msg("engine.completion.blocked", who=who, base=base) + (msg("engine.completion.next_step", action=actions[0]) if actions else msg("engine.completion.dot"))
        if status == "CANCELLED":
            return msg("engine.completion.cancelled", who=who) + (msg("engine.completion.reason_suffix", reason=reason) if reason else msg("engine.completion.dot"))
        if status == "ROLLED_BACK":
            return msg("engine.completion.rolled_back", who=who) + (msg("engine.completion.reason_suffix", reason=reason) if reason else msg("engine.completion.dot"))
        if status == "SUPERSEDED":
            return msg("engine.completion.superseded", who=who)
        if delivered_text:
            return delivered_text
        return msg("engine.completion.failed", who=who) + (msg("engine.completion.reason_suffix", reason=reason) if reason else msg("engine.completion.dot")) + (msg("engine.completion.next_step", action=actions[0]) if actions else "")

    def _completion_view_from_event(self, event: Any) -> tuple[CompletionViewV1, str | None]:
        """Build the one authoritative user projection from a CompletionEvent boundary."""

        completion_id = str(getattr(event, "completion_id", "") or "").strip()
        if not completion_id:
            raise ValueError("CompletionEvent has no completion_id")
        version = max(1, int(getattr(event, "version", 1) or 1))
        status_obj = getattr(event, "status", "")
        status = str(getattr(status_obj, "value", status_obj) or "FAILED").upper()
        task_id = str(getattr(event, "task_id", "") or "")
        attempt_id = str(getattr(event, "attempt_id", "") or "")
        run_id = str(getattr(event, "run_id", "") or "")
        scope_id = self._scope_for_task(task_id, attempt_id)
        worker_id = str(getattr(event, "worker_id", "") or "")
        delivery = dict(getattr(event, "delivery_intent", {}) or {})
        channel = str(delivery.get("channel") or "").strip() or None

        raw_files: list[Any] = []
        direct_files = getattr(event, "files", ())
        if isinstance(direct_files, Sequence) and not isinstance(direct_files, (str, bytes, bytearray)):
            raw_files.extend(direct_files)
        delivery_record = getattr(event, "delivery_record", None)
        if isinstance(delivery_record, Mapping):
            record_files = delivery_record.get("artifacts") or delivery_record.get("files") or ()
            if isinstance(record_files, Sequence) and not isinstance(record_files, (str, bytes, bytearray)):
                raw_files.extend(record_files)
        for row in getattr(event, "artifact_manifest", ()) or ():
            if not isinstance(row, Mapping) or not bool(row.get("claimable")):
                continue
            disposition = str(row.get("disposition") or "")
            if disposition in {"deleted_in_attempt", "missing"}:
                continue
            current = dict(row.get("current") or {})
            if not bool(current.get("exists", True)):
                continue
            path = str(row.get("path") or current.get("path") or "").strip()
            if not path:
                continue
            raw_files.append({
                "path": path,
                "name": Path(path).name,
                "sha256": current.get("sha256"),
                "size": current.get("size"),
            })
        files = self._normalize_outbound_files(raw_files)

        record = dict(delivery_record) if isinstance(delivery_record, Mapping) else {}
        metadata_raw = getattr(event, "metadata", {})
        event_metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        summary = (
            record.get("text")
            or record.get("summary")
            or event_metadata.get("user_summary")
            or event_metadata.get("summary")
            or getattr(event, "summary", "")
            or getattr(event, "final_candidate", "")
            or getattr(event, "terminal_reason", "")
        )
        verification = (
            record.get("verification")
            or record.get("verifications")
            or record.get("checks")
            or record.get("tests")
            or event_metadata.get("verification")
            or event_metadata.get("checks")
            or ()
        )
        risks = (
            record.get("risks")
            or record.get("warnings")
            or record.get("open_issues")
            or event_metadata.get("risks")
            or event_metadata.get("warnings")
            or ()
        )
        gaps = [str(item) for item in getattr(event, "gaps", ()) if str(item).strip()]
        for detail in getattr(event, "gap_details", ()) or ():
            if isinstance(detail, Mapping):
                message = str(detail.get("message") or "").strip()
                if message and message not in gaps:
                    gaps.append(message)
        next_actions = [str(item) for item in getattr(event, "next_actions", ()) if str(item).strip()]
        user_visible = build_user_facing_completion(
            status=status,
            summary=summary,
            artifacts=files,
            verification=verification,
            risks=risks,
            gaps=gaps,
            next_actions=next_actions,
            fallback_summary=self._completion_message_text(event, files),
        )
        # [I-3] The Worker's final answer reaches the reader unaltered — same
        # passthrough the Coordinator gets. No templating, no flattening, no
        # placeholder sentences. Only a genuine mechanical stop with no Worker text
        # (timeout/cancel/system error) falls back to a plain factual status line;
        # that is not rewriting the LLM's words, because there are none.
        raw_final = str(summary or "").strip()
        rendered_text = raw_final or self._completion_message_text(event, files)
        created_at = str(
            getattr(event, "created_at", "")
            or getattr(event, "updated_at", "")
            or event_metadata.get("created_at")
            or "1970-01-01T00:00:00Z"
        )
        view = CompletionViewV1(
            event_id=self._stable_completion_event_id(completion_id, version, "view_v1"),
            completion_id=completion_id,
            task_id=task_id,
            attempt_id=attempt_id,
            agent_id=worker_id,
            version=version,
            status=status,
            terminal=bool(getattr(event, "terminal", status not in {"WAITING"})),
            user_visible=user_visible,
            rendered_text=rendered_text,
            created_at=created_at,
            run_id=run_id,
            scope_id=scope_id,
            delivery=delivery,
            # [v1.0.23.5] 推理全文随 view_v1 透传（view_v1 是权威终态投影，message
            #   晚到且 authority 更低被前端拒绝时，气泡仍要有完整推理）
            reasoning=str(event_metadata.get("reasoning") or "").strip(),
            # [v1.0.23.6] 推理耗时（秒）——「思考了 Xs」展示
            reasoning_seconds=(
                float(event_metadata["reasoning_seconds"])
                if isinstance(event_metadata.get("reasoning_seconds"), (int, float))
                else None
            ),
            metadata={
                "runtime_state": str(getattr(event, "runtime_state", "") or ""),
                "authoritative": True,
                "schema": "knowe.completion-view.v1",
            },
        )
        return view, channel

    async def _emit_completion_visible(self, event: Any) -> bool:
        """Publish one atomic result, then durably mark exactly that rendered text seen."""

        if not feature_enabled(FeatureFlag.COMPLETION_VIEW_V1):
            return await self._emit_legacy_completion_visible(event)
        view, channel = self._completion_view_from_event(event)
        current_version = self._completion_visible_versions.get(view.completion_id, 0)
        if view.version < current_version:
            return False
        is_new_projection = view.version > current_version

        # Re-emit equal versions through Hub's stable event id. Hub returns the
        # existing event without broadcasting it again, while the remaining
        # side effects (Seen Speech + review notification) can recover after a
        # crash between the authoritative UI commit and those durable writes.
        visible_event = await self.emit(view.to_payload(), channel=channel)
        self._completion_visible_versions[view.completion_id] = max(current_version, view.version)

        if feature_enabled(FeatureFlag.SEEN_SPEECH_V1):
            try:
                self.seen_speech_ledger.record(VisibleSpeech.create(
                    project_id=self.project_id,
                    visible_id=view.event_id,
                    completion_id=view.completion_id,
                    agent_id=view.agent_id,
                    agent_name=self.member_name(view.agent_id) if view.agent_id else msg("engine.106"),
                    text=view.rendered_text,
                    seq=int(visible_event.get("seq") or 0),
                    audience="group" if not channel or channel == self.project_id else "direct",
                ))
            except Exception:  # noqa: BLE001 - visible truth already committed by Hub
                log.exception("[%s] Seen Speech 落盘失败：%s", self.project_id, view.completion_id)

        # CompletionView owns the review trigger independently from Seen Speech. Turning
        # the ledger off is a rollback of prompt injection, not a request to drop reviews.
        review = {
            "kind": "completion_review",
            "completion_id": view.completion_id,
            "report_ref": str((view.delivery or {}).get("report_ref") or ""),
            "decision_required": ["accept", "rework"],
        }
        await self.notify_coordinator(
            msg("engine.120"),
            priority="background",
            notification_id=f"completion-review:{view.completion_id}:v{view.version}",
            structured_notification=review,
        )
        return is_new_projection

    async def _emit_legacy_completion_visible(self, event: Any) -> bool:
        """Feature-off legacy two-event projection retained for rollback clients."""
        completion_id = str(getattr(event, "completion_id", "") or "")
        if not completion_id:
            return False
        version = max(1, int(getattr(event, "version", 1) or 1))
        if version <= self._completion_visible_versions.get(completion_id, 0):
            return False

        status_obj = getattr(event, "status", "")
        status = str(getattr(status_obj, "value", status_obj) or "FAILED").upper()
        task_id = str(getattr(event, "task_id", "") or "")
        attempt_id = str(getattr(event, "attempt_id", "") or "")
        run_id = str(getattr(event, "run_id", "") or "")
        scope_id = self._scope_for_task(task_id, attempt_id)
        worker_id = str(getattr(event, "worker_id", "") or "")
        reason = str(getattr(event, "terminal_reason", "") or "")
        gaps = [str(item) for item in getattr(event, "gaps", ()) if str(item).strip()]
        gap_details = [dict(item) for item in getattr(event, "gap_details", ()) if isinstance(item, Mapping)]
        next_actions = [str(item) for item in getattr(event, "next_actions", ()) if str(item).strip()]
        delivery = dict(getattr(event, "delivery_intent", {}) or {})
        raw_files: list[Any] = []
        direct_files = getattr(event, "files", ())
        if isinstance(direct_files, Sequence) and not isinstance(direct_files, (str, bytes, bytearray)):
            raw_files.extend(direct_files)

        delivery_record = getattr(event, "delivery_record", None)
        if isinstance(delivery_record, Mapping):
            record_files = delivery_record.get("artifacts") or delivery_record.get("files") or ()
            if isinstance(record_files, Sequence) and not isinstance(record_files, (str, bytes, bytearray)):
                raw_files.extend(record_files)

        for row in getattr(event, "artifact_manifest", ()) or ():
            if not isinstance(row, Mapping) or not bool(row.get("claimable")):
                continue
            disposition = str(row.get("disposition") or "")
            if disposition in {"deleted_in_attempt", "missing"}:
                continue
            current = dict(row.get("current") or {})
            if not bool(current.get("exists", True)):
                continue
            path = str(row.get("path") or current.get("path") or "").strip()
            if not path:
                continue
            metadata_row = dict(row.get("metadata") or {})
            raw_files.append({
                "path": path,
                "name": Path(path).name,
                "sha256": current.get("sha256"),
                "size": current.get("size"),
                "media_type": metadata_row.get("media_type"),
                "disposition": disposition,
                "identity": current.get("sha256") or "",
            })
        files = self._normalize_outbound_files(raw_files)
        terminal = bool(getattr(event, "terminal", status not in {"WAITING"}))
        channel = str(delivery.get("channel") or "").strip() or None
        metadata = {
            "runtime_state": str(getattr(event, "runtime_state", "") or ""),
                        "authoritative": True,
        }
        status_payload: dict[str, Any] = {
            "type": "completion_status",
            "event_id": self._stable_completion_event_id(completion_id, version, "status"),
            "completion_id": completion_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "run_id": run_id,
            "scope_id": scope_id,
            "agent_id": worker_id,
            "status": status,
            "terminal": terminal,
            "reason": reason,
            "gaps": gaps,
            "gap_details": gap_details,
            "next_actions": next_actions,
            "version": version,
            "files": files,
            "delivery": delivery,
            "metadata": metadata,
        }
        await self.emit(status_payload, channel=channel)

        message_payload: dict[str, Any] = {
            "type": "message",
            "agent_id": worker_id,
            "content": self._completion_message_text(event, files),
            "event_id": self._stable_completion_event_id(completion_id, version, "message"),
            "completion_id": completion_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "run_id": run_id,
            "scope_id": scope_id,
            "status": status,
            "terminal": terminal,
            "reason": reason,
            "gaps": gaps,
            "gap_details": gap_details,
            "next_actions": next_actions,
            "version": version,
            "files": files,
            "delivery": delivery,
            "metadata": metadata,
        }
        await self.emit(message_payload, channel=channel)
        self._completion_visible_versions[completion_id] = version
        return True

    def _artifact_lock_keys(self, envelope: TaskEnvelope) -> tuple[str, ...]:
        """Return no predeclared path locks.

        Output paths are discovered from actual tool mutations, so the Engine cannot
        safely derive lock keys from task prose before execution.
        """

        del envelope
        return ()

    @asynccontextmanager
    async def _artifact_lock_scope(self, envelope: TaskEnvelope):
        keys = self._artifact_lock_keys(envelope)
        if not keys:
            yield
            return
        async with self._artifact_locks_guard:
            locks = [self._artifact_locks.setdefault(key, asyncio.Lock()) for key in keys]
        acquired: list[asyncio.Lock] = []
        try:
            for lock in locks:
                await lock.acquire()
                acquired.append(lock)
            yield
        finally:
            for lock in reversed(acquired):
                lock.release()

    @contextmanager
    def _activity_scope(
        self,
        *,
        agent_id: str,
        scope_id: str,
        channel: str | None = None,
        task_id: str = "",
        attempt_id: str = "",
        run_id: str = "",
    ):
        """Bind one visible execution identity and restore its parent on exit."""

        resolved_channel = str(channel or _DM_CHANNEL_VAR.get() or self.project_id)
        value = {
            "agent_id": str(agent_id),
            "scope_id": str(scope_id),
            "channel_id": resolved_channel,
            "task_id": str(task_id),
            "attempt_id": str(attempt_id),
            "run_id": str(run_id),
        }
        token = _ACTIVITY_SCOPE_VAR.set(value)
        try:
            yield value
        finally:
            _ACTIVITY_SCOPE_VAR.reset(token)

    @staticmethod
    def _scope_for_task(task_id: str, attempt_id: str) -> str:
        return completion_scope_id(task_id, attempt_id)

    def _correlate_visible_event(
        self,
        payload: Mapping[str, Any],
        *,
        target_channel: str,
    ) -> dict[str, Any]:
        """Attach Actor/scope identity without guessing from tool names or text."""

        out = dict(payload)
        etype = str(out.get("type") or "")
        agent_id = str(out.get("agent_id") or "")
        if not agent_id or etype not in _CORRELATED_EVENT_TYPES:
            return out
        context = _ACTIVITY_SCOPE_VAR.get()
        if context and context.get("agent_id") == agent_id:
            for key in ("scope_id", "task_id", "attempt_id", "run_id"):
                if not str(out.get(key) or "") and str(context.get(key) or ""):
                    out[key] = str(context[key])
        if not str(out.get("scope_id") or ""):
            derived = self._scope_for_task(
                str(out.get("task_id") or ""),
                str(out.get("attempt_id") or ""),
            )
            if derived:
                out["scope_id"] = derived
        out.setdefault("channel_id", target_channel)
        return out

    def _event_bucket_key(
        self,
        *,
        agent_id: str,
        channel: str,
        scope_id: str = "",
    ) -> tuple[str, str, str]:
        context = _ACTIVITY_SCOPE_VAR.get()
        if not scope_id and context and context.get("agent_id") == agent_id:
            scope_id = str(context.get("scope_id") or "")
        return (str(channel), str(agent_id), str(scope_id or "legacy"))

    def _current_bucket_key(self, agent_id: str) -> tuple[str, str, str]:
        context = _ACTIVITY_SCOPE_VAR.get()
        channel = str(
            (context or {}).get("channel_id")
            or _DM_CHANNEL_VAR.get()
            or self.project_id
        )
        scope_id = str((context or {}).get("scope_id") or "") if (context or {}).get("agent_id") == agent_id else ""
        return self._event_bucket_key(agent_id=agent_id, channel=channel, scope_id=scope_id)

    def _clear_stream_buffer(
        self,
        agent_id: str,
        *,
        channel: str | None = None,
        scope_id: str = "",
    ) -> None:
        key = self._event_bucket_key(
            agent_id=agent_id,
            channel=str(channel or _DM_CHANNEL_VAR.get() or self.project_id),
            scope_id=scope_id,
        )
        self._stream_buffers.pop(key, None)

    def _purge_agent_scoped_state(self, agent_id: str) -> None:
        for mapping in (self._stream_buffers, self._files_produced):
            for key in tuple(mapping):
                if isinstance(key, tuple) and len(key) >= 2 and key[1] == agent_id:
                    mapping.pop(key, None)

    def _record_typed_decision(
        self,
        decision_type: DecisionType,
        *,
        actor: str,
        task_id: str = "",
        attempt_id: str = "",
        completion_id: str = "",
        reason: str = "",
        payload: Mapping[str, Any] | None = None,
        provenance: Mapping[str, Any] | None = None,
        idempotency_seed: str = "",
    ) -> DecisionEvent:
        """Persist one typed user/Coordinator decision without inferring intent from prose."""
        body = dict(payload or {})
        canonical = json.dumps(
            {
                "project_id": self.project_id,
                "decision_type": decision_type.value,
                "actor": actor,
                "task_id": task_id,
                "attempt_id": attempt_id,
                "completion_id": completion_id,
                "reason": reason,
                "payload": body,
                "seed": idempotency_seed,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        event = DecisionEvent(
            decision_id="decision_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24],
            decision_type=decision_type,
            project_id=self.project_id,
            actor=actor,
            task_id=task_id,
            attempt_id=attempt_id,
            completion_id=completion_id,
            reason=reason,
            payload=body,
            provenance=dict(provenance or current_provenance_dict()),
        )
        return self.completion_store.record_decision(event)

    def completion_run_for(self, task_id: str) -> TaskRun | None:
        root = self.internal_workspace / "runtime" / "runtime.sqlite3"
        try:
            return CompletionAwareTaskRunRepository(root, self.completion_store).get(task_id)
        except Exception:
            log.debug("[%s] Completion TaskRun lookup failed for %s", self.project_id, task_id, exc_info=True)
            return None

    async def reconcile_completion_outbox(self, completion_id: str = "") -> dict[str, Any]:
        """Publish visible truth first, then replay slow durable projection lanes."""
        store, projector = self._completion_components()
        if completion_id:
            event = store.get(completion_id)
            if event is not None:
                try:
                    await self._emit_completion_visible(event)
                except Exception:  # noqa: BLE001 - durable outbox still owns recovery
                    log.exception(
                         "[%s] completion 即时可见投影失败，转入 outbox 恢复：%s",
                        self.project_id,
                        completion_id,
                    )
        return await projector.drain(completion_id=completion_id, raise_errors=False)

    def _recover_waiting_completions(self) -> int:
        """Validate durable WAITING lineages without occupying their Workers."""

        recoverable = 0
        for event in self.completion_store.list_active(project_id=self.project_id):
            if event.status is not CompletionStatus.WAITING:
                continue
            token = self.completion_store.active_wait_for_completion(event.completion_id)
            if token is None:
                continue
            raw_envelope = event.metadata.get("task_envelope")
            if not isinstance(raw_envelope, Mapping):
                continue
            try:
                envelope = TaskEnvelope.from_dict(raw_envelope)
                if (
                    envelope.task_id != token.task_id
                    or envelope.attempt_id != token.attempt_id
                    or envelope.worker_id != token.worker_id
                ):
                    raise ValueError("WAITING token and TaskEnvelope lineage differ")
            except Exception:
                log.exception(
                     "[%s] WAITING Completion recovery validation failed: %s",
                    self.project_id,
                    event.completion_id,
                )
                continue
            current = self._task_envelopes.get(event.worker_id)
            if current is not None and (
                current.task_id == event.task_id and current.attempt_id == event.attempt_id
            ):
                self._task_envelopes.pop(event.worker_id, None)
                self._workers_with_open_activity.discard(event.worker_id)
            recoverable += 1
        return recoverable

    @property
    def seen_speech_ledger(self) -> SeenSpeechLedger:
        """Project-local exact visible-speech ledger; no Project Memory dependency."""

        ledger = self._seen_speech_ledger_instance
        if ledger is None:
            ledger = SeenSpeechLedger(self.internal_workspace / "runtime" / "seen-speech-v1.jsonl")
            self._seen_speech_ledger_instance = ledger
        return ledger

    @property
    def _completion_notification_dir(self) -> Path:
        path = self.internal_workspace / "runtime" / "completion-notifications"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _atomic_completion_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    def _completion_notification_path(self, notification_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(notification_id))[:180]
        return self._completion_notification_dir / f"{safe}.json"

    def _read_completion_notification(self, notification_id: str) -> dict[str, Any] | None:
        path = self._completion_notification_path(notification_id)
        try:
            value = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return dict(value) if isinstance(value, Mapping) else None

    def _write_completion_notification(
        self,
        notification_id: str,
        *,
        content: str,
        priority: str,
        status: str,
        error: str = "",
        structured_notification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        prior = self._read_completion_notification(notification_id) or {}
        payload = {
            "schema_version": "knowe.harness.coordinator-notification.v1",
            "notification_id": notification_id,
            "project_id": self.project_id,
            "content": content,
            "structured_notification": dict(structured_notification or prior.get("structured_notification") or {}),
            "priority": priority,
            "status": status,
            "attempts": int(prior.get("attempts") or 0) + (1 if status == "processing" else 0),
            "error": error,
            "created_at": prior.get("created_at") or utc_now(),
            "updated_at": utc_now(),
        }
        self._atomic_completion_json(self._completion_notification_path(notification_id), payload)
        return payload

    async def _recover_completion_notifications(self) -> int:
        """Requeue coordinator wakeups that were durable but not fully processed."""
        queued = 0
        for path in sorted(self._completion_notification_dir.glob("*.json")):
            try:
                row = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if not isinstance(row, Mapping) or row.get("status") == "processed":
                continue
            notification_id = str(row.get("notification_id") or "")
            content = str(row.get("content") or "")
            if not notification_id or not content or notification_id in self._completion_notifications_queued:
                continue
            self._completion_notifications_queued.add(notification_id)
            self._write_completion_notification(
                notification_id,
                content=content,
                priority=str(row.get("priority") or "background"),
                status="queued",
            )
            item = {
                "content": content,
                "target": None,
                "internal": True,
                "_completion_notification_id": notification_id,
                "_structured_notification": dict(row.get("structured_notification") or {}),
                "_idempotency_key": f"completion-notification:{notification_id}",
            }
            await self.inbox.put(item)
            queued += 1
        return queued

    async def resume_waiting_task(
        self,
        wait_token_id: str,
        answer: str,
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        """Resume the same TaskEnvelope lineage with a durable user answer."""
        store = self.completion_store
        token = store.get_wait_token(wait_token_id)
        if token is None:
            raise KeyError(wait_token_id)
        if token.project_id != self.project_id:
            raise ValueError("wait token belongs to another project")
        event = store.get(token.completion_id)
        if event is None:
            raise RuntimeError("wait token has no CompletionEvent")
        raw_envelope = event.metadata.get("task_envelope")
        if not isinstance(raw_envelope, Mapping):
            raise RuntimeError("WAITING completion has no TaskEnvelope snapshot")
        envelope = TaskEnvelope.from_dict(raw_envelope)
        if (
            envelope.task_id != token.task_id
            or envelope.attempt_id != token.attempt_id
            or envelope.worker_id != token.worker_id
        ):
            raise RuntimeError("WAITING resume would change task/attempt/worker lineage")
        if self._worker_has_authoritative_activity(token.worker_id):
            raise RuntimeError(
                f"worker {token.worker_id} is not available; resume after it returns to IDLE"
            )

        answer_path = self.internal_workspace / "runtime" / "wait-answers" / f"{wait_token_id}.json"
        answer_payload = {
            "schema_version": "knowe.harness.wait-answer.v1",
            "wait_token_id": wait_token_id,
            "task_id": token.task_id,
            "attempt_id": token.attempt_id,
            "worker_id": token.worker_id,
            "actor": actor,
            "answer": str(answer).strip(),
            "answered_at": utc_now(),
            "provenance": token.provenance,
        }
        self._atomic_completion_json(answer_path, answer_payload)
        resumed = store.resume_wait(
            wait_token_id,
            answer=str(answer),
            answer_ref=str(answer_path),
            actor=actor,
        )
        envelope = replace(
            envelope,
            started_at=utc_now(),
            metadata={
                **envelope.metadata,
                "resume_wait_token_id": wait_token_id,
                "wait_answer": resumed.answer,
                "wait_answer_ref": resumed.answer_ref,
                "resumed_from_completion_id": event.completion_id,
            },
        )
        self._task_envelopes[token.worker_id] = envelope
        self._workers_with_open_activity.add(token.worker_id)
        await self._run_worker(envelope, token.worker_id, internal=True)
        active = store.active_for(token.task_id, token.attempt_id)
        return {
            "wait_token": (store.get_wait_token(wait_token_id) or resumed).to_dict(),
            "completion": active.to_dict() if active else None,
            "same_lineage": bool(
                active
                and active.task_id == token.task_id
                and active.attempt_id == token.attempt_id
            ),
        }

    def _compile_retry_attempt(
        self,
        prior: Any,
        *,
        decision_id: str,
        reason: str,
        dispatch: bool,
    ) -> dict[str, Any]:
        """Create a fresh attempt while preserving task scope and explicit safety inputs."""
        raw_envelope = prior.metadata.get("task_envelope")
        if not isinstance(raw_envelope, Mapping):
            raise RuntimeError("CompletionEvent has no TaskEnvelope snapshot for retry")
        envelope = TaskEnvelope.from_dict(raw_envelope)
        ordinal = int(envelope.metadata.get("attempt_ordinal") or 1) + 1
        attempt_id = "attempt_" + hashlib.sha256(
            f"{prior.completion_id}:{decision_id}:{ordinal}".encode("utf-8")
        ).hexdigest()[:20]
        mutation_seen = any(
            bool(row.get("mutation_ids"))
            or str(row.get("disposition") or "")
            in {"created_in_attempt", "modified_in_attempt", "unauthorized_mutation", "deleted_in_attempt"}
            for row in prior.artifact_manifest
        )
        transient_keys = {
            "resume_wait_token_id", "wait_answer", "wait_answer_ref", "completion_id",
            "required_context_ready", "context_bundle_ref", "context_receipt_ref",
        }
        clean_metadata = {
            key: value
            for key, value in envelope.metadata.items()
            if key not in transient_keys
        }
        envelope_ref = f"runtime/task-envelopes/{envelope.task_id}/{attempt_id}.json"
        retry_envelope = replace(
            envelope,
            attempt_id=attempt_id,
            delivery=replace(envelope.delivery, attempt_id=attempt_id),
            created_at=utc_now(),
            started_at=utc_now(),
            metadata={
                **clean_metadata,
                "attempt_ordinal": ordinal,
                "retry_of_attempt_id": prior.attempt_id,
                "retry_of_completion_id": prior.completion_id,
                "retry_decision_id": decision_id,
                "retry_reason": reason,
                "mutation_seen": mutation_seen,
                "mutation_warning": (
                     "Prior attempt produced verified side effects; inspect its delivered files before retry."
                    if mutation_seen else ""
                ),
                "task_envelope_ref": envelope_ref,
            },
        )
        retry_envelope, envelope_ref = self._task_envelope_store().commit(retry_envelope)
        self.inject_task_envelope(retry_envelope)
        worker_id = retry_envelope.worker_id
        if dispatch:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self._start_worker_turn(worker_id, retry_envelope)
        return {
            "task_id": retry_envelope.task_id,
            "attempt_id": attempt_id,
            "worker_id": worker_id,
            "task_envelope_ref": envelope_ref,
            "mutation_seen": mutation_seen,
            "dispatched": bool(dispatch),
        }

    async def decide_completion(
        self,
        completion_id: str,
        action: CoordinatorAction | str,
        *,
        actor: str = "coordinator",
        reason: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Uniform Coordinator decision API for all non-success Completion statuses."""
        data = dict(payload or {})
        prior = self.completion_store.get(completion_id)
        if prior is None:
            raise KeyError(completion_id)
        next_event, decision = CompletionCommitter(self.completion_store).decide(
            completion_id,
            action,
            actor=actor,
            reason=reason,
            payload=data,
        )
        action_value = action if isinstance(action, CoordinatorAction) else CoordinatorAction(str(action))
        result: dict[str, Any] = {
            "decision": decision.to_dict(),
            "completion": next_event.to_dict() if next_event else prior.to_dict(),
        }
        if next_event is not None:
            await self.reconcile_completion_outbox(next_event.completion_id)
        if action_value is CoordinatorAction.PROVIDE_DEPENDENCY:
            answer = str(data.get("answer") or reason).strip()
            if prior.status is CompletionStatus.WAITING:
                token = self.completion_store.active_wait_for_completion(prior.completion_id)
                if token is None:
                    raise RuntimeError("WAITING completion has no active wait token")
                result["resume"] = await self.resume_waiting_task(
                    token.wait_token_id,
                    answer,
                    actor=actor,
                )
            else:
                result["retry"] = self._compile_retry_attempt(
                    prior,
                    decision_id=decision.decision_id,
                    reason=reason or "dependency_provided",
                    dispatch=bool(data.get("dispatch", True)),
                )
        elif action_value in {CoordinatorAction.RETRY, CoordinatorAction.REJECT}:
            result["retry"] = self._compile_retry_attempt(
                    prior,
                    decision_id=decision.decision_id,
                    reason=reason or action_value.value,
                    dispatch=bool(data.get("dispatch", True)),
                )
        return result

    async def _submit_internal(
        self,
        content: str,
        *,
        priority: str = "background",
        notification_id: str = "",
        structured_notification: Mapping[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> None:
        if getattr(self, "_stopping", False) or getattr(self, "_stopped", False):
            raise RuntimeError(msg("engine.095"))
        # 引擎自己塞给项目经理的话（提案被拒之后 / Worker 交了报告）。**不作废审批**。
        #
        # 和 `submit()` 的唯一区别就是这一点：`submit()` 是**用户**开口，
        # 用户一开口，桌上挂着的提案就都不算数了（他改主意了）；
        # 而引擎自己塞的话不是新指令，凭什么把用户正在看的那张卡撤掉。
        normalized_notification = notification_from_unknown(structured_notification)
        item = {
            "content": content,
            "target": None,
            "internal": True,
            **({"_structured_notification": normalized_notification} if normalized_notification else {}),
            **({"_idempotency_key": idempotency_key} if idempotency_key else {}),
        }
        if notification_id:
            existing = self._read_completion_notification(notification_id)
            if existing and existing.get("status") == "processed":
                return
            if notification_id in self._completion_notifications_queued:
                return
            self._completion_notifications_queued.add(notification_id)
            self._write_completion_notification(
                notification_id,
                content=content,
                priority=priority,
                status="queued",
                structured_notification=normalized_notification,
            )
            item["_completion_notification_id"] = notification_id
            item["_idempotency_key"] = idempotency_key or f"completion-notification:{notification_id}"
        await self.inbox.put(item)

    # ═══════════════════════════════════════════════════════════
    # [v0.8a A-2] 报告推送：Worker 交完差，项目经理得知道
    # ═══════════════════════════════════════════════════════════
    async def notify_coordinator(
        self,
        message: str,
        *,
        priority: str = "background",
        notification_id: str = "",
        structured_notification: Mapping[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> None:
        """
        往项目经理的 inbox 里塞一条系统通知，安排一个自动回合。

        为什么必须有这一步：工作流交付必须确定性地回到编排器——
        报告落了盘、前端也弹了「已提交报告」，**可项目经理根本不知道**。
        他要么傻等用户开口问「他做完了吗」，要么得自己想起来去调 list_handoff_dir。
        一条流水线在最后一米上断掉，跟没有流水线是一样的。

        走 inbox（不是直接跑一个回合）的理由：
          · 这条通知由 **Runtime 的 Delivery 边界**发出。
            在那儿直接起项目经理回合 = 回合套回合，两个 agent 的 emit 会交错，
            前端的气泡会打架。
          · inbox 是唯一的工作面入口，`_loop` 一条一条处理——Worker 那一轮跑完
            （连同链式调度里的其他 Worker），自然轮到这条通知，项目经理才开口。
          · `busy` 属性看的是 `inbox.empty()`，所以「报告交了、项目经理还没审」这段时间，
            引擎如实地是「忙」——不会有人误判成跑完了。

        **不作废审批**：走 `_submit_internal`。

        [完整性] Exactly-once by identity. A completion review is identified by its
        (completion_id, version), not by whichever string a caller happens to pass.
        We canonicalize the notification_id for any ``completion_review`` here, so
        that *any* producer — this one, or any future source — collapses to a single
        coordinator review per completion+version. This is the boundary invariant
        that replaces one-off "delete the duplicate" fixes.
        """
        review = structured_notification if isinstance(structured_notification, Mapping) else None
        if review and str(review.get("kind") or "") == "completion_review":
            cid = str(review.get("completion_id") or "").strip()
            if cid:
                version = str(review.get("version") or "1").strip() or "1"
                notification_id = f"completion-review:{cid}:v{version}"
        await self._submit_internal(
            message,
            priority=priority,
            notification_id=notification_id,
            structured_notification=structured_notification,
            idempotency_key=idempotency_key,
        )

    #: 提示词/老代码里写的是下划线版，留个别名
    _notify_coordinator = notify_coordinator

    @property
    def _worker_id_from_envelope(self, envelope: TaskEnvelope) -> str:
        return envelope.worker_id

    async def _relay_worker_runtime_event(
        self,
        worker_id: str,
        event: RuntimeEvent,
        *,
        completion_managed: bool,
    ) -> None:
        """Project non-terminal Runtime activity to the existing roster UI."""
        if completion_managed and event.type in {
            RuntimeEventType.TASK_WAITING,
            RuntimeEventType.TASK_BLOCKED,
            RuntimeEventType.TASK_FAILED,
            RuntimeEventType.TASK_CANCELLED,
            RuntimeEventType.DELIVERY_COMMITTED,
        }:
            return
        payload = event.payload
        envelope = self._task_envelopes.get(worker_id)
        attempt_id = envelope.attempt_id if envelope is not None else ""
        correlation = {
            "task_id": event.task_id,
            "attempt_id": attempt_id,
            "run_id": event.run_id,
            "scope_id": self._scope_for_task(event.task_id, attempt_id),
        }
        if event.type is RuntimeEventType.MODEL_CALLED:
            await self.emit({
                "type": "agent_thinking", "agent_id": worker_id,
                "phase": "thinking",
                "stage": "plan",
                "stage_detail": msg("engine.131"),
                 "stage_state": "active",
                **correlation,
            })
        elif event.type is RuntimeEventType.TOOL_STARTED:
            tool_name = str(payload.get("name") or "tool")
            await self.emit({
                "type": "tool_gen", "agent_id": worker_id,
                "tool_name": tool_name,
                **tool_ledger.stage_payload(tool_name),
                **correlation,
            })
            await self.emit({"type": "tool_start", "agent_id": worker_id, **correlation})
        elif event.type in {RuntimeEventType.TOOL_COMPLETED, RuntimeEventType.TOOL_REJECTED}:
            await self.emit({"type": "tool_complete", "agent_id": worker_id, **correlation})
        elif event.type is RuntimeEventType.TASK_WAITING:
            await self.emit({
                "type": "agent_thinking",
                "agent_id": worker_id,
                "phase": "waiting",
                "stage": "wait",
                "stage_detail": msg("engine.132"),
                 "stage_state": "waiting",
                **correlation,
            })
            await self.emit({
                "type": "message", "agent_id": worker_id,
                "content": str(payload.get("question") or msg("engine.133")),
                 "status": "WAITING",
                "terminal": False,
                **correlation,
            })
        elif event.type in {RuntimeEventType.TASK_BLOCKED, RuntimeEventType.TASK_FAILED, RuntimeEventType.TASK_CANCELLED}:
            await self.emit({
                "type": "error", "agent_id": worker_id,
                "message": str(payload.get("dependency") or payload.get("reason") or msg("engine.134")),
                "stage_state": (
                     "cancelled" if event.type is RuntimeEventType.TASK_CANCELLED else "error"
                ),
                **correlation,
            })

    def _remember_worker_runtime_outcome(
        self,
        worker_id: str,
        envelope: TaskEnvelope,
        *,
        state: TaskState,
        completion_status: CompletionStatus | str,
        text: str,
        terminal_reason: str = "",
        dependency: str = "",
        run_id: str = "",
        delivery_id: str = "",
        artifacts: list[str] | None = None,
        instruction: str = "",
        provenance: Mapping[str, Any] | None = None,
        memory_key: str,
    ) -> None:
        """Bridge one authoritative Runtime outcome into the existing Memory pipeline.

        The Runtime owns execution and durable submission state; Memory receives a
        compact serialized view and never infers completion from model tool calls or a
        mechanical quality result. This boundary covers direct delivery
        delivery and Completion outcomes such as WAITING/BLOCKED uniformly.
        """
        if not memory_key or memory_key in self._worker_memory_keys:
            return
        status_value = str(
            getattr(completion_status, "value", completion_status) or ""
        ).strip().upper()
        output = str(text or terminal_reason or dependency or status_value or state.value).strip()
        if not output:
            return
        submitted = status_value in {
            CompletionStatus.SUCCEEDED.value,
            CompletionStatus.PARTIAL.value,
        }
        provenance_source = provenance
        if provenance_source is None and isinstance(envelope.metadata.get("provenance"), Mapping):
            provenance_source = envelope.metadata.get("provenance")
        prov = normalize_provenance(
            provenance_source if provenance_source is not None else current_provenance_dict()
        ).to_dict()
        runtime_payload: dict[str, Any] = {
            "state": state.value,
            "completion_status": status_value,
            "text": output,
            "terminal_reason": str(terminal_reason or ""),
            "dependency": str(dependency or ""),
            "submission_committed": submitted,
            "review_status": "pending_coordinator" if submitted else "not_submitted",
            "task_id": envelope.task_id,
            "run_id": run_id,
            "delivery_id": delivery_id,
            "audience": envelope.delivery.audience.value,
            "artifacts": list(artifacts or []),
            "provenance": prov,
        }
        result = self._project_memory_result(
            {
                "final_response": output,
                "tool_calls": [],
                "worker_runtime": runtime_payload,
                "_memory_kind": "worker_runtime",
                "_provenance": prov,
                "_lineage": {
                    "task_id": envelope.task_id,
                    "run_id": run_id,
                    "delivery_id": delivery_id,
                    "project_id": envelope.project_id,
                },
            },
            worker_id,
            [],
            content=instruction or envelope.goal,
            internal=False,
        )
        self._worker_memory_keys.add(memory_key)
        self._schedule_project_memory(result)

    async def _commit_worker_preflight_block(
        self,
        worker_id: str,
        envelope: TaskEnvelope,
        exc: WorkerContextError | RuntimeError,
    ) -> TaskRun:
        """Persist and surface a zero-side-effect preflight block."""
        block_code = str(getattr(exc, "code", "required_context_unavailable") or "required_context_unavailable")
        block_reference = str(getattr(exc, "reference", getattr(exc, "path", "required_context")) or "required_context")
        detail = exc.to_dict() if callable(getattr(exc, "to_dict", None)) else {
            "code": block_code,
            "reference": block_reference,
            "message": str(exc),
            "repair_action": msg("engine.135"),
        }
        repair_action = str(detail.get("repair_action") or msg("engine.135"))
        blocked_factory = getattr(self._worker_runtime_factory, "blocked_run", None)
        if callable(blocked_factory):
            blocked = blocked_factory(self, envelope, exc)
            run = await blocked if inspect.isawaitable(blocked) else blocked
        else:
            run = TaskRun(
                envelope=envelope,
                state=TaskState.IDLE,
                version=1,
                final_candidate=msg("engine.136", **{"detail.get('message') or str(exc)": str(detail.get("message") or exc), "repair_action": repair_action}),
                terminal_reason=block_code,
                dependency=block_reference,
                metadata={
                    "completion_status": CompletionStatus.BLOCKED.value,
                    "blocked_before_model": True,
                    "blocked_before_tool": True,
                    "blocked_before_mutation": True,
                    "gap_details": [dict(detail)],
                    "next_actions": [repair_action],
                },
            )
        if not run.completion_status:
            run.set_completion_status(CompletionStatus.BLOCKED.value)
        self._worker_runtime_runs[worker_id] = run
        self._task_envelopes[worker_id] = run.envelope
        event = self.completion_store.active_for(run.envelope.task_id, run.envelope.attempt_id)
        if event is None:
            event = self.completion_store.commit_run(run)
        await self.reconcile_completion_outbox(event.completion_id)
        # [v1.0.21.1 REQ-6] blocked 终局同样关信封
        self._close_task_envelope(worker_id)
        return run

    async def _run_worker(
        self,
        task: TaskEnvelope | str,
        worker_id: str,
        *,
        internal: bool = False,
    ) -> None:
        """Run one Worker attempt inside a clean Actor and lifecycle boundary."""

        if worker_id == COORDINATOR:
            raise ValueError("Coordinator cannot enter WorkerRuntime")
        envelope = task if isinstance(task, TaskEnvelope) else self._task_envelopes.get(worker_id)
        if envelope is None:
            envelope = self._create_direct_task_envelope(worker_id, str(task))
            self.inject_task_envelope(envelope)
        # [v1.0.19.5] 群聊 @Worker / 私聊直达带附件时，把附件元数据并入 envelope，
        #   WorkerRuntime._initial_messages 会注入最后一条 user 消息（多模态数组）。
        turn_attachments = _TURN_ATTACHMENTS_VAR.get() or []
        if turn_attachments and not envelope.metadata.get("attachments"):
            envelope = replace(envelope, metadata={
                **envelope.metadata,
                "attachments": turn_attachments,
            })
            self._task_envelopes[worker_id] = envelope
        # [v1.0.19.5] Worker 回合注入它自己的工作记忆（worklog 尾部）——与项目经理/知知
        #   同款机制，但只注入它自己的交差/对话记录；runtime 侧注入 system prompt 尾部。
        turn_memory = self._agent_memory_block(worker_id)
        if turn_memory and not envelope.metadata.get("agent_memory"):
            envelope = replace(envelope, metadata={
                **envelope.metadata,
                "agent_memory": turn_memory,
            })
            self._task_envelopes[worker_id] = envelope
        channel = str(_DM_CHANNEL_VAR.get() or envelope.delivery.channel or self.project_id)
        if envelope.delivery.channel != channel:
            envelope = replace(envelope, delivery=replace(envelope.delivery, channel=channel))
            self._task_envelopes[worker_id] = envelope
        # [v1.0.23.8-C] attempt 开始前拍 workspace 文件清单（交付文件快照）。
        #   Worker 交付时（manifest_for_task）对比 baseline，把本次 attempt
        #   新增的文件补进 manifest——即使工具没产生 verified fact（如 shell
        #   cp 复制且保留源 mtime）。只收交付扩展名，避免快照膨胀。
        if not envelope.metadata.get("workspace_baseline"):
            baseline = self._snapshot_workspace_baseline(envelope)
            if baseline is not None:
                envelope = replace(envelope, metadata={
                    **envelope.metadata,
                    "workspace_baseline": baseline,
                })
                self._task_envelopes[worker_id] = envelope
        scope_id = self._scope_for_task(envelope.task_id, envelope.attempt_id)
        with self._activity_scope(
            agent_id=worker_id,
            scope_id=scope_id,
            channel=channel,
            task_id=envelope.task_id,
            attempt_id=envelope.attempt_id,
        ):
            await self.emit(
                {
                    "type": "agent_active",
                    "agent_id": worker_id,
                    "reason": "worker_runtime",
                },
                channel=channel,
            )
            execution_context = getattr(self._worker_runtime_factory, "execution_context", None)
            context = execution_context() if callable(execution_context) else tool_ledger.actor_scope()
            with context:
                await self._run_worker_attempt(
                    envelope,
                    worker_id,
                    internal=internal,
                )

    async def _run_worker_attempt(
        self,
        task: TaskEnvelope | str,
        worker_id: str,
        *,
        internal: bool = False,
    ) -> None:
        """Direct Engine → TaskEnvelope → WorkerRuntime execution path."""
        del internal
        if worker_id == COORDINATOR:
            raise ValueError("Coordinator cannot enter WorkerRuntime")
        envelope = task if isinstance(task, TaskEnvelope) else self._task_envelopes.get(worker_id)
        if envelope is None:
            envelope = self._create_direct_task_envelope(worker_id, str(task))
            self.inject_task_envelope(envelope)
        if envelope.worker_id != worker_id:
            raise ValueError("TaskEnvelope worker does not match execution target")
        channel = _DM_CHANNEL_VAR.get()
        if channel and envelope.delivery.channel != channel:
            envelope = replace(envelope, delivery=replace(envelope.delivery, channel=channel))
        handoff_step = int(envelope.metadata.get("handoff_step") or self._worker_step.get(worker_id) or 0)
        handoff_keyword = str(envelope.metadata.get("handoff_keyword") or self._worker_keyword.get(worker_id) or envelope.title)
        handoff_phase = str(envelope.metadata.get("handoff_phase") or "")
        if not handoff_phase and envelope.instruction_ref and "://" not in envelope.instruction_ref:
            handoff_phase = Path(envelope.instruction_ref).parent.name
        envelope = replace(envelope, metadata={
            **envelope.metadata,
            "handoff_step": handoff_step,
            "handoff_keyword": handoff_keyword,
            "handoff_phase": handoff_phase,
            "task_title": envelope.title or handoff_keyword,
            # 每次真正启动 Runtime 前现取，队列等待期间发生的改名也能立即生效。
            "user_address": self._user_address_line(),
        })
        self._task_envelopes[worker_id] = envelope
        self._workers_with_open_activity.add(worker_id)
        if feature_enabled(FeatureFlag.MODEL_READINESS_GATE_V1):
            await runtime_settings.wait_for_model_ready(self.project_id, worker_id)
        agent = self._get_or_create_worker(worker_id, self._roster.get(worker_id, msg("engine.106")))
        if feature_enabled(FeatureFlag.IDENTITY_CONTRACT_V1):
            # ProviderModelAdapter implementations may consume this high-priority
            # prompt directly; the same contract also travels in TaskEnvelope metadata.
            identity_prompt = self._identity_block(worker_id)
            # The single Worker prompt is owned by Runtime; the Agent carries identity only.
            agent.ephemeral_system_prompt = identity_prompt

        prepare = getattr(self._worker_runtime_factory, "prepare_envelope", None)
        if callable(prepare):
            prepared = prepare(envelope)
            envelope = await prepared if inspect.isawaitable(prepared) else prepared
        self._task_envelopes[worker_id] = envelope

        async with self._artifact_lock_scope(envelope):
            prepare_context = getattr(self._worker_runtime_factory, "prepare_context", None)
            if callable(prepare_context):
                try:
                    prepared_context = prepare_context(self, envelope)
                    envelope = await prepared_context if inspect.isawaitable(prepared_context) else prepared_context
                except WorkerContextError as exc:
                    await self._commit_worker_preflight_block(worker_id, envelope, exc)
                    return
            self._task_envelopes[worker_id] = envelope

            create = getattr(self._worker_runtime_factory, "create", None)
            if not callable(create):
                raise TypeError("worker runtime factory does not expose create()")
            runtime = create(self, worker_id, envelope, agent)
            if inspect.isawaitable(runtime):
                runtime = await runtime

            event_emitter = getattr(runtime, "events", None)
            add_listener = getattr(event_emitter, "add_listener", None)
            if callable(add_listener):
                async def relay(event: RuntimeEvent) -> None:
                    await self._relay_worker_runtime_event(worker_id, event, completion_managed=True)
                add_listener(relay)
            run, record = await runtime.run(envelope)
            self._worker_runtime_runs[worker_id] = run
            # [M1 采集点 B] Worker 回合结束即落盘（run.usage 由 model adapter 累加）。
            try:
                self._persist_worker_token_usage(worker_id, run)
            except Exception:
                log.debug("[%s] Worker Token 统计旁路失败（忽略）", self.project_id, exc_info=True)
            self._task_envelopes[worker_id] = run.envelope
            if record is not None:
                completion, _stored = self.completion_store.commit_success(run, record)
            else:
                completion = self.completion_store.commit_run(run)
            await self.reconcile_completion_outbox(completion.completion_id)
            # [v1.0.21.1 REQ-1] 成功终局：释放信封，杜绝残留被再拉起
            self._close_task_envelope(worker_id)

    async def _reconcile_worker_run(
        self,
        worker_id: str,
        run: TaskRun,
        envelope: TaskEnvelope,
    ) -> None:
        """Translate a non-delivery Runtime stop without inventing model output."""
        instruction = envelope.goal
        status = CompletionStatus.from_run(run)
        if status is CompletionStatus.WAITING:
            self._close_task_envelope(worker_id)
            self._remember_worker_runtime_outcome(
                worker_id, envelope, state=run.state, completion_status=status,
                text=run.waiting_question, terminal_reason=run.terminal_reason,
                dependency=run.dependency, run_id=run.run_id, instruction=instruction,
                provenance=run.provenance, memory_key=f"run:{run.run_id}:{status.value}",
            )
            return
        reason = run.terminal_reason or run.dependency or run.waiting_question or status.value
        self._remember_worker_runtime_outcome(
            worker_id, envelope, state=run.state, completion_status=status,
            text=run.final_candidate or reason, terminal_reason=run.terminal_reason,
            dependency=run.dependency, run_id=run.run_id, instruction=instruction,
            provenance=run.provenance, memory_key=f"run:{run.run_id}:{status.value}",
        )
        active = self.completion_store.active_for(envelope.task_id, envelope.attempt_id)
        event = active or self.completion_store.commit_run(run, status=status)
        await self.reconcile_completion_outbox(event.completion_id)
        # [v1.0.21.1 REQ-6] 非 delivery 停止终局：关信封
        self._close_task_envelope(worker_id)

    async def _accept_worker_delivery(
        self,
        worker_id: str,
        record: DeliveryRecord,
        envelope: TaskEnvelope,
    ) -> None:
        """Commit a Runtime DeliveryRecord to project boundaries exactly once."""
        active_envelope = self._task_envelopes.get(worker_id) or envelope
        instruction = active_envelope.goal
        payload = record.to_dict()
        delivery_memory_key = (
            str(record.idempotency_key or record.delivery_id or "").strip()
            or f"{record.run_id}:{envelope.task_id}:{record.state.value}"
        )
        report_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        artifacts = [artifact.path for artifact in record.artifacts]

        task = self._close_task_envelope(worker_id) or active_envelope
        who = msg("engine.141",
                    member_name=self.member_name(worker_id),
                    roster_name=self._roster.get(worker_id, msg("engine.141.fb")))
        clipped = " ".join(record.text.split())[:REPORT_SUMMARY_CLIP]
        self.record_project_activity(msg("engine.142", who=who, clipped=clipped), reason="runtime_delivery")
        self._remember_worker_runtime_outcome(
            worker_id,
            envelope,
            state=record.state,
            completion_status=str(
                record.metadata.get("completion_status") or CompletionStatus.SUCCEEDED.value
            ),
            text=record.text,
            terminal_reason=record.terminal_reason,
            run_id=record.run_id,
            delivery_id=record.delivery_id,
            artifacts=artifacts,
            instruction=task.goal if task is not None else instruction,
            provenance=record.provenance,
            memory_key=f"delivery:{delivery_memory_key}",
        )

    # ═══════════════════════════════════════════════════════════
    # [v0.29 问题四] 非正常终止 → report_failed
    #
    # ## 为什么是一个「漏斗」，而不是四个补丁
    #
    #   Worker 的【工作中】能以四种方式结束，v0.28 里它们**各修各的**：
    #       delivery  → _accept_worker_delivery 里销账
    #       user delivery      → _accept_worker_delivery 里销账
    #       Runtime failure    → fail_task 里销账
    #       抛异常              → fail_task 里销账
    #   四个 discard，四段不一样的收尾，**没有一个通知项目经理**。
    #   再加两个（用户点停止、API 报错）就是六个——这个形状注定要漏，
    #   而它漏掉的恰恰是最该说的那句话：「这件事失败了」。
    #
    #   所以这一版把出口收成两个，两个都在这一节里：
    #       _close_task_envelope()  → 已有权威 Delivery：销账，什么都不做
    #       fail_task()    → 没结果（其余一切）：落盘 + 通知项目经理，**只此一处**
    #   ★ 谁调它都行，调多少次都行（幂等）——底账里没有他，就直接返回 False。
    # ═══════════════════════════════════════════════════════════
    def _close_task_envelope(self, agent_id: str) -> TaskEnvelope | None:
        """Release Engine ownership after Completion commits an outcome."""
        self._workers_with_open_activity.discard(agent_id)
        self._stop_reasons.pop(agent_id, None)
        return self._task_envelopes.pop(agent_id, None)

    def task_goal_of(self, agent_id: str) -> str:
        """Return the verbatim goal of the Worker's active TaskEnvelope."""
        envelope = self._task_envelopes.get(agent_id)
        return envelope.goal if envelope is not None else ""

    async def fail_task(self, agent_id: str, reason: str) -> bool:
        """Commit one deterministic failure/cancellation CompletionEvent."""
        runtime_run = self._worker_runtime_runs.get(agent_id)
        envelope = self._task_envelopes.get(agent_id)
        if runtime_run is None and envelope is None:
            return False
        if runtime_run is None:
            assert envelope is not None
            runtime_run = TaskRun(envelope=envelope)
        attempt_id = runtime_run.envelope.attempt_id
        active = self.completion_store.active_for(runtime_run.envelope.task_id, attempt_id)
        if active is not None:
            # Projection/replay failures never manufacture a replacement completion.
            # Reconcile the already-authoritative result regardless of terminality.
            await self.reconcile_completion_outbox(active.completion_id)
            # [v1.0.21.1 REQ-2] 已有权威结果也是终局：关信封
            self._close_task_envelope(agent_id)
            return True
        staged = runtime_run.clone()
        cancelled = bool(self._stop_reasons.get(agent_id)) or reason == STOP_REASON_USER
        if staged.completion_status:
            # Runtime may already have stopped with TIMED_OUT/BLOCKED/etc. Preserve that
            # result even when the exception was raised later by a projection boundary.
            selected_status = CompletionStatus.from_run(staged)
        else:
            selected_status = CompletionStatus.CANCELLED if cancelled else CompletionStatus.FAILED
            staged.set_completion_status(selected_status.value)
        staged.terminal_reason = reason
        staged.updated_at = utc_now()
        staged.metadata = {
            **staged.metadata,
            "harness_terminal_boundary": True,
            "harness_failure_reason": reason,
        }
        event = self.completion_store.commit_run(
            staged,
            status=selected_status,
            metadata={"harness_failure": True},
        )
        self._worker_runtime_runs[agent_id] = staged
        await self.reconcile_completion_outbox(event.completion_id)
        # [v1.0.21.1 REQ-2] 确定性失败/取消也是终局：关信封
        self._close_task_envelope(agent_id)
        return True


    # ═══════════════════════════════════════════════════════════
    # [v0.37 / v0.37.1] 群内 Agent 私聊
    #
    #   私聊 = **绕过项目经理审批、直接指挥成员干活**的直通通道（与群聊互补，见 PROMPT）。
    #
    #   · **完整回合（含工具）**：私聊跑的是和群里一样的 _run_agent_turn——Worker 有读/写/
    #     终端/浏览器全套工具，项目经理额外有 propose_*。所以他能真干活、能派活，不是只会说话。
    #   · **事件分流（隔离，验收 5/13）**：回合在自己的 task 里跑，task 顶端 set 了
    #     _DM_CHANNEL_VAR → emit() 把 thinking/文本/工具进度/文件都发到 dm 频道（群里看不到）；
    #     只有管理事件（审批卡经 Gate、花名册事件经 _DM_GROUP_ALWAYS_EVENTS）发到群聊
    #     ——「思考过程私聊可见，管理动作群聊可见」（验收 11/12）。
    #   · **busy 守卫（需求 3）**：对方正在群里忙 → 不调 LLM，暂存消息 + 回一句「稍等」，
    #     他忙完（群回合 settle / 私聊回合结束）自动补发。
    #   · **三级记忆（验收 6）**：完整回合本身会经工具/收尾写各层记忆；这里再补一条 harness
    #     摘要，保证项目经理事后知道私聊里发生了什么。全部按 self.project_id 路由。
    # ═══════════════════════════════════════════════════════════
    async def submit_dm(
        self, agent_id: str, content: str, dm_channel: str, *, group_mention: bool = False,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        """把一条用户直达消息交给某个成员。

        ``group_mention=False`` 是既有私聊；``True`` 是群聊 @Worker。两者共用同一套
        busy 守卫、完整 Agent 回合、工具、记忆与收尾，仅回复频道/场景文案不同。
        """
        if self._stopping:
            return
        if not isinstance(agent_id, str) or not agent_id:
            return
        if not isinstance(content, str) or not content.strip():
            return
        content = content.strip()

        # ── 需求 3：对方正在群里（或另一条私聊里）忙 → 暂存，不调 LLM ──
        if agent_id != COORDINATOR and self.worker_is_busy(agent_id):
            self._dm_pending.setdefault(agent_id, []).append((content, dm_channel, group_mention))
            name = self.member_name(agent_id) or agent_id
            try:
                await self.emit(
                    {"type": "message", "agent_id": agent_id, "content": (
                        (msg("engine.143")
                         if group_mention else
                         msg("engine.144"))
                    )},
                    channel=dm_channel,
                )
            except Exception:                   # noqa: BLE001
                log.debug("[%s] 私聊 busy 提示发送失败（忽略）", self.project_id, exc_info=True)
            log.info("[%s] %s 正忙，私聊消息暂存（队列 %d 条）", self.project_id, agent_id,
                     len(self._dm_pending.get(agent_id, [])))
            log.warning("[%s] DM BUSY BLOCK: %s is busy, queued (pending=%d)",
                        self.project_id, agent_id,
                        len(self._dm_pending.get(agent_id, [])))
            return

        # [v0.38.6 #5] 竞态封缝：走到这里说明对方**此刻空闲**（上面 busy 就暂存返回了）。
        #   原来是 create_task 后立刻返回，Worker 要等 _run_dm_turn 真正跑到
        #   `_workers_with_open_activity.add`（下一次事件循环）才入忙态。若用户一派完活就
        #   转头问项目经理「谁在忙」，项目经理这一轮可能恰好卡在这道缝里 → _work_status_ctx 误报
        #   「都闲着」。所以在**排任务之前、同步**先把忙态入账（_run_dm_turn 里会再 add 一次，
        #   幂等；回合收尾的 finally 负责撤销）。此路径只对空闲成员成立，不会顶掉真任务的忙。
        if agent_id != COORDINATOR:
            self._workers_with_open_activity.add(agent_id)
            self._dm_busy.add(agent_id)
            if group_mention:
                self._mention_busy.add(agent_id)
            log.warning("[%s] DM BUSY PRE-ADD: %s (workers_open=%d, dm_busy=%d)",
                        self.project_id, agent_id,
                        len(self._workers_with_open_activity), len(self._dm_busy))

        task = asyncio.create_task(
            self._run_dm_turn(agent_id, content, dm_channel, group_mention=group_mention,
                              attachments=attachments),
            name=(f"mention:{self.project_id}:{agent_id}" if group_mention else f"dm:{dm_channel}"),
        )
        self._dm_tasks.add(task)
        if agent_id != COORDINATOR:
            self._direct_turns[agent_id] = task
            task.add_done_callback(
                lambda done, aid=agent_id: self._direct_turn_finished(aid, done)
            )
        else:
            task.add_done_callback(self._dm_tasks.discard)

    async def _run_dm_turn(
        self,
        agent_id: str,
        content: str,
        dm_channel: str,
        *,
        group_mention: bool = False,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        """执行一次用户直达回合；Worker 直达仍经过 Runtime。"""
        is_coord = agent_id == COORDINATOR
        name = self.member_name(agent_id) or (msg("engine.007") if is_coord else agent_id)
        mirror_group_status = not group_mention and dm_channel != self.project_id
        mirror_scope = f"mirror:{uuid.uuid4().hex}" if mirror_group_status else ""
        stopped_by_user = False
        if not is_coord and mirror_group_status:
            try:
                await self.emit(
                    {
                        "type": "agent_active",
                        "agent_id": agent_id,
                        "scope_id": mirror_scope,
                        "reason": "direct_turn_mirror",
                    },
                    channel=self.project_id,
                )
            except Exception:
                log.debug("[%s] 直达忙态广播失败", self.project_id, exc_info=True)

        tok_ch = _DM_CHANNEL_VAR.set(dm_channel)
        tok_fr = _DM_FRAMING_VAR.set("coordinator" if is_coord else "worker")
        # [v1.0.19.4] 私聊/@直达也让附件随 task-局部 ContextVar 传进 _run_agent_turn。
        #   协调者 DM 走 run_conversation 时会被注入；知知/Worker 直达的 Agent 实现
        #   同样读取 turn.attachments（知知已支持；Worker Runtime 侧由各自实现决定）。
        tok_att = _TURN_ATTACHMENTS_VAR.set(attachments)
        if not is_coord:
            self._dm_busy.add(agent_id)
            if group_mention:
                self._mention_busy.add(agent_id)
        try:
            try:
                await self._process_turn(
                    content,
                    None if is_coord else agent_id,
                    internal=True,
                )
            except ApprovalCancelled:
                log.info("[%s] %s 的直达回合被审批取消", self.project_id, agent_id)
            except asyncio.CancelledError:
                stopped_by_user = bool(self._stop_reasons.get(agent_id))
                raise
            except Exception:
                log.exception("[%s] %s 的直达回合异常", self.project_id, agent_id)
                try:
                    await self.emit(
                        {
                            "type": "error",
                            "agent_id": agent_id,
                            "message": (
                                 msg("engine.145")
                                if group_mention
                                else msg("engine.146")
                            ),
                        },
                        channel=dm_channel,
                    )
                except Exception:
                    pass
            try:
                self.record_project_activity(
                    (
                        msg("engine.147", name=name, **{"_dm_short(content)": _dm_short(content)})
                        if group_mention and stopped_by_user
                        else msg("engine.148", name=name, **{"_dm_short(content)": _dm_short(content)})
                        if group_mention
                        else msg("engine.149", name=name, **{"_dm_short(content)": _dm_short(content)})
                        if stopped_by_user
                        else msg("engine.150", name=name, **{"_dm_short(content)": _dm_short(content)})
                    ),
                    reason=(
                        "mention_stopped" if group_mention and stopped_by_user
                        else "mention" if group_mention
                        else "dm_stopped" if stopped_by_user
                        else "dm"
                    ),
                )
            except Exception:
                log.debug("[%s] 直达活动记录失败", self.project_id, exc_info=True)
        finally:
            _DM_CHANNEL_VAR.reset(tok_ch)
            _DM_FRAMING_VAR.reset(tok_fr)
            _TURN_ATTACHMENTS_VAR.reset(tok_att)
            if not is_coord:
                self._dm_busy.discard(agent_id)
                self._mention_busy.discard(agent_id)
            # The Runtime completion projection closes the direct channel's task scope.
            # The group mirror is a separate, status-only scope and never carries private
            # thinking/tool/message detail.
            if not is_coord and mirror_group_status and mirror_scope:
                await self.emit(
                    {
                        "type": "agent_idle",
                        "agent_id": agent_id,
                        "scope_id": mirror_scope,
                        "status": "AVAILABLE",
                        "derived": True,
                        "derived_from": "direct_turn_mirror_settled",
                    },
                    channel=self.project_id,
                )
            if not stopped_by_user:
                self._flush_pending_dm(agent_id)

    def _direct_turn_finished(self, agent_id: str, task: asyncio.Task[None]) -> None:
        """Release one direct-turn task reference and continue its FIFO."""
        self._dm_tasks.discard(task)
        if self._direct_turns.get(agent_id) is task:
            self._direct_turns.pop(agent_id, None)
        self._stop_reasons.pop(agent_id, None)
        if not self._stopping:
            self._flush_pending_dm(agent_id)

    def _flush_pending_dm(self, agent_id: str) -> None:
        """[v0.37.1] 需求 3 步骤 3：对方空下来了 → 补发一条暂存的私聊消息（如果有）。

        群回合收尾（_settle_worker_turn）和私聊回合收尾都会调它。再走一遍 submit_dm：
        此刻他若仍忙（又被派了活）会再次暂存，若空了就正常起回合——自然串起来。
        """
        if self._stopping:
            return
        queue = self._dm_pending.get(agent_id)
        if not queue:
            return
        # 他还在忙（又接了群活）→ 先不补，等下一次 idle。
        if agent_id != COORDINATOR and self.worker_is_busy(agent_id):
            return
        content, dm_channel, group_mention = queue.pop(0)
        if not queue:
            self._dm_pending.pop(agent_id, None)
        log.info("[%s] %s 空闲，补发暂存的私聊消息", self.project_id, agent_id)
        task = asyncio.create_task(
            self.submit_dm(agent_id, content, dm_channel, group_mention=group_mention),
            name=f"dm-flush:{dm_channel}",
        )
        self._dm_tasks.add(task)
        task.add_done_callback(self._dm_tasks.discard)

    # ── [v0.9a B-1] 按规范落盘一份报告 ──
    def write_handoff_report(
        self,
        agent_id: str,
        report_hash: str,
        summary: str,
        artifacts: list[str],
        *,
        status: str = "completed",
        keyword: str = "",
        completed_what: str = "",
        matches_instruction: str = "",
        issues: str = "",
        self_check: str = "",
        knowledge_used: list[str] | None = None,
        knowledge_not_helpful: list[str] | None = None,
        knowledge_suggest: str = "",
        task_id: str = "",
        run_id: str = "",
        delivery_id: str = "",
        provenance: Mapping[str, Any] | None = None,
    ) -> Path:
        """
        Worker 交差 → `handoffs/03-后端/report-03-fe_1-用户认证.md`
        （YAML frontmatter + 五段），并回头在 .approval-03 里补一条「交回的报告」链接。

        序号和关键词**跟着那条 instruction 走**（派活时记下的）——
        instruction-03 和 report-03 是一对，用户 `ls` 一眼就看得出谁回应了谁。
        没有对应指令（Worker 自己主动交的）→ 现取一个新序号。
        """
        step = self._worker_step.get(agent_id) or self.handoff.next_step()
        kw = keyword_of(keyword or self._worker_keyword.get(agent_id) or summary)
        d = self.phase_dir()

        ins = [p for p in self.handoff.instructions()
               if p.name.startswith(f"instruction-{step:02d}-")]
        prov = normalize_provenance(
            provenance if provenance is not None else current_provenance_dict()
        ).to_dict()

        path = self.handoff.write_report(
            step=step, agent_id=agent_id, keyword=kw, phase_dir=d,
            status=status, report_hash=report_hash,
            instruction_ref=ins[0].name if ins else "",
            completed_what=completed_what or summary,
            matches_instruction=matches_instruction,
            artifacts=artifacts,
            issues=issues,
            self_check=self_check,
            knowledge_used=knowledge_used,
            knowledge_not_helpful=knowledge_not_helpful,
            knowledge_suggest=knowledge_suggest,
            task_id=task_id,
            run_id=run_id,
            delivery_id=delivery_id,
            provenance=prov,
        )
        self.handoff.link_report_into_approval(step, path.name, phase_dir=d)

        # [v0.19] 文件已经完整落盘、审批回链也已补齐；现在只排后台图谱任务。
        # 顺序是 report → approval：后者是同一步审批记录的内容更新，重新摄入会先撤销
        # 旧信号再幂等重算，使用户的决议也能覆盖本步刚出现的报告知识，而不会重复加分。
        # 此处不 await，因此不会拖住 Runtime Delivery → 报告入链的主链。
        lineage_meta = {
            "task_id": task_id,
            "run_id": run_id,
            "delivery_id": delivery_id,
            "project_id": self.project_id,
            "provenance": prov,
        }
        self._schedule_knowledge_update(
            path, "report",
            {"trigger": "runtime_delivery", "step": step,
             "agent_id": agent_id, "report_hash": report_hash, **lineage_meta},
        )
        approval_path = d / f".approval-{step:02d}.md"
        if approval_path.is_file():
            self._schedule_knowledge_update(
                approval_path, "approval",
                {"trigger": "report_receipt", "step": step,
                 "agent_id": agent_id, "report_hash": report_hash, **lineage_meta},
            )

        # ═══ [v0.42] 使用闭环结算 + T1 蒸馏（一步完整闭环在此刻凑齐）═══
        #
        #   ① 结算「知识引用」：used/not_helpful → 正/负信号；指令附过 L0 却没被
        #      引用 → matched_never_used。决定取该步审批文件的 decision（拿不到按
        #      approved——能派下来的活默认是批过的；rejected 补正走 resolve）。
        #   ② 排 T1 蒸馏：instruction + report + approval 三件套全文进知识策展人，
        #      宁缺毋滥。任务接在同一条串行尾巴上，天然排在上面的摄入之后。
        if self._assets is not None:
            assets = self._assets
            decision = self._approval_decision(approval_path) or "approved"
            used_ids = [str(a) for a in (knowledge_used or [])]
            nh_ids = [str(a) for a in (knowledge_not_helpful or [])]
            self._enqueue_knowledge_update(
                lambda: assets.record_usage(
                    self.project_id, self.internal_workspace,
                    step=step, used=used_ids, not_helpful=nh_ids,
                    suggest=str(knowledge_suggest or ""), decision=decision,
                ),
                label=f"usage:{step}",
            )
            self._schedule_knowledge_distill(
                step=step,
                instruction_path=ins[0] if ins else None,
                report_path=path,
                approval_path=approval_path if approval_path.is_file() else None,
                decision=decision,
                worker_suggest=str(knowledge_suggest or ""),
                metadata={"agent_id": agent_id, **lineage_meta},
            )

        # [v0.13 模块B] 交差 = 一次「我干了活」的确切记录 → 写进这个 Agent 自己的 memory/。
        #   report 的结构化字段现成，直接落成个人工作日志 + 累计快照，不劳 LLM 摘要。
        self._write_agent_memory(
            agent_id, step=step, keyword=kw, status=status,
            completed_what=completed_what or summary, artifacts=artifacts,
            matches_instruction=matches_instruction, issues=issues,
            self_check=self_check,
            instruction_ref=ins[0].name if ins else "",
            report_file=path.name,
            provenance=prov,
            task_id=task_id,
            run_id=run_id,
            delivery_id=delivery_id,
        )

        # 这一步交完了 —— 下次这个 Worker 再交差，就该是新的一步了
        self._worker_step.pop(agent_id, None)
        self._worker_keyword.pop(agent_id, None)

        log.info("[%s] %s 的报告 → %s", self.project_id, agent_id, self.handoff.rel(path))
        return path

    # ═══════════════════════════════════════════════════════════
    # 控制面（不经队列，直达 gate）
    # ═══════════════════════════════════════════════════════════
    def resolve(self, card_id: str, resolution: str) -> bool:
        return self.gate.resolve(card_id, resolution)   # type: ignore[arg-type]

    # ═══════════════════════════════════════════════════════════
    # 崩溃恢复（B-4：复提卡的顶层字段一个不能少）
    # ═══════════════════════════════════════════════════════════
    async def recover(self, pending: list[tuple[str, str, dict[str, Any]]]) -> None:
        if not pending:
            return
        await self.hub.emit(self.project_id, {
            "type": "recovery_notice",
            "message": msg("engine.151"),
            "details": {
                "stale_approvals_count": len(pending),
                "history_messages": len(self.history),
            },
        })
        for tool, agent_id, card in pending:
            self.gate.restore_pending(tool=tool, agent_id=agent_id, card=card)

    # ═══════════════════════════════════════════════════════════
    # [v0.26] 「我有新意见」——卡片原地改，**不惊动项目经理的回合**
    # ═══════════════════════════════════════════════════════════
    #
    # ## v0.24 / v0.25 为什么两次都失败
    #
    # 两版都走「消息管道」：用户意见 → sendMessage → submit() 作废旧卡 →
    # 项目经理重新 propose_next。两次的结果都一样：**项目经理把旧指令原样又发了一遍。**
    #
    # v0.25 我把原因归到「传输方式」上，于是拼了一段更凶的 prompt 塞给它。
    # 还是没用。所以那个归因**是错的**——真正的原因是：
    #
    #   ★ 我们让项目经理**在一个完整的 agent 回合里**做这件事。而那个回合里：
    #       · 有一条刚炸掉的 tool_call，在冲它喊「原样重试」
    #       · 有几万字的上下文，用户的意见只是其中一行
    #       · 它有十几个工具可以调、无数种话可以说
    #     在这种设定下，「把这份指令按这条意见改一版」只是它**众多选项里的一个**。
    #     它选错，不是因为笨，是因为**有得选**。
    #
    # ## 这一版为什么会成
    #
    # `adjust_instruction` 根本不排回合、不进 inbox、不惊动 Harness。
    # 它就是一次**定向的、一次性的**模型调用：
    #
    #     输入：这份指令 + 这条意见
    #     输出：改好的指令正文
    #     没有工具、没有历史、没有第二种可能的动作
    #
    # ★ **它不会分心，因为没有东西可以让它分心。**
    #
    #   ——传输方式从来不是问题，「让它在什么处境下做这件事」才是。
    #     v0.24 换了措辞，v0.25 换了拼装，两次都在改传输；这一版换的是**处境**。
    # ═══════════════════════════════════════════════════════════

    #: 改写指令的系统提示。刻意写得又短又死：它只有一个出口。
    ADJUST_SYSTEM = (
         msg("engine.152")
        + msg("engine.153")
        + "\n"
        + msg("engine.154")
        + msg("engine.155")
        + msg("engine.156")
        + msg("engine.157")
        + msg("engine.158")
        + msg("engine.159")
        + msg("engine.160")
    )


    async def adjust_instruction(self, card_id: str, feedback: str) -> dict[str, Any]:
        """
        用户在卡上提了新意见 → 就地把这张卡的 instruction 换成改好的。

        返回 {"ok": bool, "reason": str, "silent": bool}。**不抛异常**：这是用户
        点出来的交互，出什么岔子都得给他一句人话，不能让卡永远转圈。

        ═══ [v0.30 Bug2/3] 串行化 ═══

        v0.29 的事故现场：用户连点两次「我有意见」→ 两条 aux LLM 调用**并发**在跑。
        第一条返回改一次卡、第二条返回再改一次——完成顺序和点击顺序无关，
        旧反馈可能压过新反馈；而用户确认的那一刻，闸门取的是**当时**卡上的字。
        三个时钟互相赛跑，谁输谁赢全看网络。

        这一版的三条硬规矩（缺一条都堵不死）：

          ① **一张卡至多一条 flight。** 新反馈进来先掐掉旧的
             （_cancel_feedback_flight），只有最后一次的结果有资格生效。
          ② **世代号。** cancel 发生在 await 点，旧 flight 若已越过取消检查点，
             它拿着的世代号也已过期——写卡前核对，过期就丢弃。
          ③ **卡落定 = flight 陪葬。** gate._settle 的回调（四条落定路径的唯一
             出海口）会掐掉这张卡的 flight——被 approve/作废的卡**绝不会**在
             几秒后被一个迟到的 LLM 结果改出一张「幽灵卡」。

        ═══ 前端的转圈怎么收 ═══

        无论成败，这里都保证**恰好一次**卡面重播（同 card_id 的 approval_card）：
          · 成功 → update_card 带新 instruction 重播（原地 morph，老路）
          · 失败 → update_card 空补丁重播（卡面一个字不变，但 rev 会变）
        前端认 rev：变了 + 指令换了 = 成功收起转圈；变了 + 指令没换 = 失败，
        退回输入态让用户改一改再发。转圈从此不靠 55 秒超时兜底——**每一次
        点击都有一个确定的回执**（Bug 3 的「转圈卡死」就是缺这个回执）。
        """
        feedback = (feedback or "").strip()
        if not feedback:
            return {"ok": False, "reason": msg("engine.179"), "silent": False}

        pending = self.gate.pending_of(card_id)
        if pending is None:
            # 卡刚好在这一瞬间被批了/拒了/超时了 —— 用户手比较快，不是错误
            return {"ok": False,
                    "reason": msg("engine.180"),
                    "silent": False}
        # ★ 后端这边 tool 是 "propose_next"（前端 state.ts 才归一成 'task'）。
        #   写成 "task" 的话每一张卡都会被拒 —— 而且拒得很安静。
        if pending.tool != "propose_next":
            return {"ok": False, "reason": msg("engine.181"), "silent": False}

        # ── ① 旧 flight 作废，登记新世代 ──
        #   注意 _cancel_feedback_flight 自己会把世代 +1；我们随后取到的
        #   就是「专属于这一次点击」的号码。
        self._cancel_feedback_flight(card_id)
        gen = self._feedback_gen.get(card_id, 0)

        flight = asyncio.create_task(
            self._run_feedback_flight(card_id, feedback, gen),
            name=f"feedback:{self.project_id}:{card_id}",
        )
        self._feedback_flights[card_id] = flight
        flight.add_done_callback(
            lambda t, cid=card_id: (
                self._feedback_flights.pop(cid, None)
                if self._feedback_flights.get(cid) is t else None
            ),
        )

        try:
            return await flight
        except asyncio.CancelledError:
            if flight.cancelled():
                # 被更新的意见（或卡落定）顶掉了——这不是错误，接棒的那次会给回执。
                # silent=True：server 不为它发 error（发了只会让用户困惑）。
                return {"ok": False, "reason": msg("engine.172"), "silent": True}
            raise                      # 是**我们自己**被取消（关机）→ 照常向上抛

    async def _run_feedback_flight(
        self, card_id: str, feedback: str, gen: int,
    ) -> dict[str, Any]:
        """一次反馈调整的完整生命周期：读卡 → aux LLM → 核对世代 → 写卡/回执。"""
        pending = self.gate.pending_of(card_id)
        if pending is None:
            return {"ok": False, "reason": msg("engine.173"), "silent": True}
        old = str(pending.card.get("instruction") or "").strip()
        if not old:
            return {"ok": False, "reason": msg("engine.182"), "silent": False}

        failure_reason: str | None = None
        new = ""
        try:
            # [v0.44 设置 §2.2] 辅助通道（摘要/翻译/蒸馏/指令调整）走「辅助模型」绑定：
            #   显式辅助 > 主模型便宜档（runtime_settings 派生） > CONFIG.deepseek_*（老默认）。
            aux = runtime_settings.aux_effective()
            aux_ok = bool(aux and aux.get("api_key") and aux.get("base_url"))
            new = await aux_client.chat(
                [
                    {"role": "system", "content": self.ADJUST_SYSTEM},
                    {"role": "user", "content": (
                        msg("engine.183", old=old)
                        + msg("engine.184", feedback=feedback)
                        + msg("engine.185")
                    )},
                ],
                api_key=aux["api_key"] if aux_ok else CONFIG.deepseek_api_key,
                base_url=aux["base_url"] if aux_ok else CONFIG.deepseek_base_url,
                model=aux["model"] if aux_ok else CONFIG.deepseek_model,
                timeout_s=CONFIG.adjust_timeout_s,
                what=msg("engine.186"),
            )
        except asyncio.CancelledError:
            raise                                  # 被新意见顶掉：外层翻译成 silent 结果
        except Exception as exc:
            log.warning("[%s] 调整指令失败：%s", self.project_id, exc)
            failure_reason = str(exc)

        # ── ② 世代核对：LLM 跑着的这段时间里，有没有更新的意见进来？──
        #   有 → 这次的结果**整个作废**（连失败回执都不发：接棒的那次会发）。
        if self._feedback_gen.get(card_id, 0) != gen:
            log.info("[%s] 卡 %s 的反馈结果过期（世代 %d），丢弃", self.project_id, card_id, gen)
            return {"ok": False, "reason": msg("engine.172"), "silent": True}

        if failure_reason is None:
            new = _strip_instruction_fence(new)
            if not new:
                failure_reason = msg("engine.187")
            elif new == old:
                # 它把原文原样吐回来了。**说清楚**，别让用户对着一张没变的卡发呆——
                # 那正是 v0.24/v0.25 里他经历过的事，只不过那时候没人告诉他发生了什么。
                failure_reason = msg("engine.188")

        if failure_reason is not None:
            # ── ③ 失败也要给卡一个回执：空补丁重播 → 前端 rev 变、指令没变 →
            #    转圈立刻收起、退回输入态。没有这一步，spinner 只能等 55 秒超时
            #    （Bug 3 的「转圈卡死」）。卡还挂着才重播；已落定就随它去。
            await self.gate.update_card(card_id, {})
            return {"ok": False, "reason": failure_reason, "silent": False}

        card = await self.gate.update_card(card_id, {
            "instruction": new,
            # [v1.0.24.3] 意见原文留痕：每轮都 append 进卡体。
            #   card_out 回程（gate.propose）会把它带出 → propose_next 回执能拼
            #   「N 轮意见 + 以最终指令为准」；前端也靠它显示「已修改」badge。
            #   卡走事件流持久化，重启恢复（restore_pending）原样带出，零额外改造。
            "feedback_history": [*(pending.card.get("feedback_history") or []), feedback],
        })
        if card is None:
            return {"ok": False, "reason": msg("engine.178"), "silent": False}
        log.info("[%s] 指令已按用户意见调整（%d 字 → %d 字）",
                 self.project_id, len(old), len(new))
        return {"ok": True, "reason": "", "silent": False}

    # Coordinator final text is authoritative; this layer performs no sentence-level filtering.

    def _single_agent_id(self) -> str:
        """返回单 Agent 模式的真实角色 id，并兼容旧测试替身。"""
        raw = str(getattr(self.agent, "agent_id", "") or "").strip()
        if raw:
            return raw
        if self.project_id == "__platform__":
            return "zinnia"
        return COORDINATOR

    # ═══════════════════════════════════════════════════════════
    # 主循环
    # ═══════════════════════════════════════════════════════════
    async def _loop(self) -> None:
        # Completion reconciliation is a startup barrier, not merely a sibling task.
        # ``start()`` is synchronous, so it schedules both coroutines; awaiting the
        # recovery task here guarantees that no inbox message can race an unprojected
        # terminal CompletionEvent or an unrestored WAITING checkpoint.
        recovery = self._completion_recovery_task
        if recovery is not None and recovery is not asyncio.current_task():
            await asyncio.shield(recovery)

        # [v0.44 设置 §3.3] 本引擎的一切回合都从这个任务派生（turn 任务 / _fire /
        #   repropose / DM 任务……）。在任务树的根上把项目 id 登进 contextvar，
        #   asyncio 的上下文继承会把它带进每一个后代任务——gate.propose 里读
        #   CONFIG.approval_timeout_s 时，runtime_settings 便知道「现在是哪个群」，
        #   群级审批超时（README §3.3）不用改 gate.py 一行就能生效。
        runtime_settings.set_current_project(self.project_id)
        proj = self.hub.get_or_create(self.project_id)
        mode = "harness" if self.agent is None else f"single({type(self.agent).__name__})"
        log.info("[%s] engine up (agent=%s, mode=%s, script=%s)",
                 self.project_id, CONFIG.agent, mode, CONFIG.script)

        while not self._stopping:
            envelope = await self.inbox.get()
            content = str(envelope.get("content") or "")
            completion_notification_id = str(envelope.get("_completion_notification_id") or "")
            structured_notification = notification_from_unknown(envelope.get("_structured_notification"))
            turn_idempotency_key = str(envelope.get("_idempotency_key") or "").strip() or None
            turn_attachments = envelope.get("_attachments")
            if not isinstance(turn_attachments, list) or not turn_attachments:
                turn_attachments = None
            notification_token: contextvars.Token[str | None] | None = None
            structured_token: contextvars.Token[dict[str, Any] | None] | None = None
            idempotency_token: contextvars.Token[str | None] | None = None
            notification_ok = True
            notification_priority = "background"
            if completion_notification_id:
                prior = self._read_completion_notification(completion_notification_id) or {}
                notification_priority = str(prior.get("priority") or "background")
                self._write_completion_notification(
                    completion_notification_id,
                    content=content,
                    priority=notification_priority,
                    status="processing",
                    structured_notification=structured_notification,
                )
                notification_token = _COMPLETION_NOTIFICATION_VAR.set(completion_notification_id)
            structured_token = _STRUCTURED_NOTIFICATION_VAR.set(structured_notification)
            idempotency_token = _TURN_IDEMPOTENCY_VAR.set(turn_idempotency_key)
            attachments_token = _TURN_ATTACHMENTS_VAR.set(turn_attachments)

            # [v1.0.13][R1] Automatic turns are not allowed to race an un-applied
            # model binding.  Wait before mutating history or emitting thinking state,
            # so replaying the inbox item after apply is behaviorally identical.
            if envelope.get("internal") and feature_enabled(FeatureFlag.MODEL_READINESS_GATE_V1):
                ready_actor = self._single_agent_id() if self.agent is not None else COORDINATOR
                await runtime_settings.wait_for_model_ready(self.project_id, ready_actor)

            # [v0.8a A-2] internal = 引擎自己塞的话（报告通知 / 提案被拒的 followup）。
            #   作废审批那一步在 `submit()` 里，**内部消息根本不经过 submit()**——
            #   所以这里什么都不用做，挂着的卡安然无恙。这行日志只是让它在日志里看得见。
            if envelope.get("internal"):
                log.info("[%s] 内部通知入队（不作废挂起审批）", self.project_id)

            turn = Turn(self.project_id, proj.name, content, list(self.history), attachments=turn_attachments)
            self.history.append({"role": "user", "content": content})

            self._turns_active += 1        # ★ 从取出消息到链式调度跑完，全程算「忙」
            turn_actor_id = COORDINATOR
            try:
                if self.agent is not None:
                    turn_actor_id = self._single_agent_id()
                    channel = self.project_id
                    scope_id = f"turn:{uuid.uuid4().hex}"

                    async def single_agent_emit(event: dict[str, Any]) -> None:
                        # Legacy single-agent implementations still close their own idle.
                        # The Engine owns the visible lifecycle now, so swallow only that
                        # duplicate boundary event and preserve every business event.
                        if (
                            event.get("type") == "agent_idle"
                            and event.get("agent_id") == turn_actor_id
                        ):
                            return
                        await self.emit(event, channel=channel)

                    with self._activity_scope(
                        agent_id=turn_actor_id,
                        scope_id=scope_id,
                        channel=channel,
                    ):
                        try:
                            await self.emit(
                                {
                                    "type": "agent_active",
                                    "agent_id": turn_actor_id,
                                    "reason": "single_agent_turn",
                                },
                                channel=channel,
                            )
                            await self.emit(
                                {
                                    "type": "agent_thinking",
                                    "agent_id": turn_actor_id,
                                    "phase": "thinking",
                                    "stage": "plan",
                                    "stage_detail": msg("engine.131"),
                                     "stage_state": "active",
                                },
                                channel=channel,
                            )
                            with tool_ledger.actor_scope():
                                await self.agent.run_turn(turn, single_agent_emit, self.gate)
                        finally:
                            await self._settle_actor_idle(
                                turn_actor_id,
                                channel=channel,
                                ignore_task=asyncio.current_task(),
                                derived_from="single_agent_turn_settled",
                            )
                else:
                    await self._harness_turn(
                        content, envelope.get("target"),
                        internal=bool(envelope.get("internal")),   # [v0.31 Bug2] 永不静默策略要认「这话是不是用户说的」
                    )
            except ApprovalCancelled:
                log.info("[%s] 回合被新消息打断，收摊", self.project_id)
            except asyncio.CancelledError:
                notification_ok = False
                raise
            except Exception as exc:              # ★ 引擎不许因为一个回合倒下
                notification_ok = False
                error_type = type(exc).__name__
                log.exception("[%s] 回合异常（%s）", self.project_id, error_type)
                await self.hub.emit(self.project_id, {
                    "type": "error",
                    "agent_id": turn_actor_id,
                    "message": msg("engine.189", error_type=error_type),
                })
            finally:
                self._turns_active -= 1
                if idempotency_token is not None:
                    _TURN_IDEMPOTENCY_VAR.reset(idempotency_token)
                _TURN_ATTACHMENTS_VAR.reset(attachments_token)
                if structured_token is not None:
                    _STRUCTURED_NOTIFICATION_VAR.reset(structured_token)
                if notification_token is not None:
                    _COMPLETION_NOTIFICATION_VAR.reset(notification_token)
                if completion_notification_id:
                    self._completion_notifications_queued.discard(completion_notification_id)
                    self._write_completion_notification(
                        completion_notification_id,
                        content=content,
                        priority=notification_priority,
                        status="processed" if notification_ok else "queued",
                        error="" if notification_ok else "coordinator_turn_failed_or_cancelled",
                        structured_notification=structured_notification,
                    )

    # ═══════════════════════════════════════════════════════════
    # Harness：一个回合 + 链式调度
    # ═══════════════════════════════════════════════════════════
    async def _harness_turn(self, content: str, target: str | None,
                            *, internal: bool = False) -> None:
        """Run a Coordinator turn and always drain already-committed Worker tasks.

        ``propose_next`` commits the TaskEnvelope from inside the model/tool turn.  Any
        later projection, history or post-processing exception must not strand that
        authoritative task in ``_task_envelopes``. The queue is therefore
        drained in ``finally`` rather than only on the all-success path.
        """
        try:
            await self._harness_turn_impl(content, target, internal=internal)
        finally:
            try:
                self._spawn_pending_workers()
            except Exception:
                # The TaskEnvelope remains queued if create_task fails, so a
                # later turn/recovery can retry instead of silently losing the task.
                log.exception("[%s] 拉起已提交 Worker 队列失败；任务保留待重试", self.project_id)

    async def _harness_turn_impl(self, content: str, target: str | None,
                                 *, internal: bool = False) -> None:
        # [v0.6b] ★ 没有可用模型就别往下走。
        #   不拦的话：ProviderClient 会带一个 `Bearer `（空的）去请求，httpx 当场
        #   LocalProtocolError，AgentLoop 把它塞进 result.error，引擎照常发一条
        #   **content 为空的 message** —— 用户看到的是一个白气泡，什么也没有。
        #   v0.5 的 DeepSeekAgent 本来会说「未配置」，Harness 把这句人话弄丢了。补回来。
        #
        # [v0.44.1 Bug3] 判据从「有没有 DEEPSEEK_API_KEY」改成「项目经理有没有生效的模型绑定」。
        #   现在模型的唯一配置入口是「设置 → 模型与提供方」（硬编码的 DeepSeek 默认已移除）：
        #   用户在设置里配了主模型、但机器上没有 DEEPSEEK_API_KEY 环境变量，是**正常且应当能跑**
        #   的情形——老判据会把这种用户误拦。这里改问 runtime_settings 要项目经理的生效绑定
        #   （个性化 > 全局主模型），拿得到 key+base_url 就放行；只有遗留 .env 档保留兜底。
        coord_binding = runtime_settings.model_binding_for(self.project_id, COORDINATOR)
        has_binding = bool(coord_binding
                           and coord_binding.get("api_key")
                           and coord_binding.get("base_url"))
        # [v0.44.2 Bug1] 遗留 .env 兜底也必须**接入点齐全**才算可用：v0.44.1 抹掉了硬编码的
        #   deepseek base_url，只剩 DEEPSEEK_API_KEY、base_url 为空的机器若放行，_new_agent 会
        #   造出空 base_url 的 client → 请求时报「missing http://」。这种残缺配置一律按「还没配
        #   模型」处理，给下面那句人话，而不是把底层 URL 错误抛给用户。
        legacy_env_ok = bool(CONFIG.deepseek_api_key and CONFIG.deepseek_base_url)
        # [v0.44.5 模型切换] 只要设置面板已经存在显式绑定，就绝不能再静默回落到
        # DEEPSEEK_*。否则新绑定一旦缺字段/同步到一半，请求会偷偷打回旧厂商，正好制造
        # “明明选了 GLM，却还在报 DeepSeek”的真路由歧义。显式绑定不完整就当场说明。
        if coord_binding is not None and not has_binding:
            await self.emit({
                "type": "error",
                "agent_id": COORDINATOR,
                "message": (
                    f"{provider_target(coord_binding.get('provider'), coord_binding.get('base_url'), coord_binding.get('model'))} "
                    + msg("engine.190")
                ),
            })
            return
        if coord_binding is None and not legacy_env_ok:
            await self.emit({
                "type": "error",
                "agent_id": COORDINATOR,
                "message": (
                     msg("engine.191")
                    + msg("engine.192")
                    + msg("engine.193")
                ),
            })
            return

        await self._process_turn(content, target, internal=internal)

    # ═══════════════════════════════════════════════════════════════
    # [v0.29 问题一] ★ Worker 的回合，从主循环里搬出来
    #
    # ## 「冻结」是怎么来的
    #
    #   用户报的是「Worker 一开工，整个项目像被冻住了」。这句话是**字面准确**的，
    #   而且根本不在 propose_next 那儿 —— 它在 `_loop` 的形状里：
    #
    #       while True:
    #           msg = await self.inbox.get()
    #           await self._harness_turn(...)     ← 链式调度在这里面 **await** 完
    #                                                每一个 Worker 的回合
    #
    #   Worker 抓个网页跑三分钟 → 这三分钟里 `_loop` 停在那一行 →
    #   用户打的字进了 inbox，**没有任何人在取**。他看到的是：我说话，没人理。
    #   项目经理不是不想回他，是项目经理这一轮压根没被叫起来过。
    #
    #   ★ 所以在 handle_propose_next 里加一道拦截，一行也解决不了这个投诉：
    #     那道拦截管的是「派给忙人」，而冻结在**队列**上。（拦截照样要做，见那边。）
    #
    # ## 这一版：Worker 的回合 = 一个后台 task
    #
    #   链式调度不再 await，只负责**把人叫起来**，然后立刻返回。主循环于是马上
    #   回到 `inbox.get()` —— 用户说话，项目经理接得住；项目经理派活给别人，卡照弹。
    #   这就是验收标准 1、2、4 的全部。
    #
    # ## 三个「本来会炸」的地方，为什么没炸
    #
    #   ① **不会重复起同一个人。** `_worker_turns` 在 create_task 之前同步登记；
    #      `_find_pending_worker` 只挑尚未登记的 Runtime 队列项，中间没有重复启动窗口。
    #   ② **`busy` 不会说谎。** `_turns_active` 记的是「整条消息处理完没有」，
    #      Worker 的回合搬走之后，它必须跟着搬——所以后台 task 自己 ++/--（见下）。
    #      漏了这一笔，引擎会在 Worker 正干活时报告「我闲着」。
    #   ③ **两个 agent 的话不会打架。** 每个 agent 有自己的 KnoweAgent、自己的历史、
    #      自己的 stream_buffer（emit 按 agent_id 分桶），事件出站带 agent_id，
    #      前端本来就按人分气泡。真正共享的那三个「本回合」标记
    #      （_corrected/_committed/_dispatched）是**项目经理专用**的，
    #      见 _process_turn_inner 里那一段——那是这次改动里唯一有牙齿的地方。
    # ═══════════════════════════════════════════════════════════════
    def start_committed_workers(self) -> None:
        """Kick committed Worker tasks without awaiting their execution.

        ``propose_next`` calls this immediately after TaskEnvelope commit so Worker startup
        does not depend on the Coordinator finishing its post-tool narration.  The
        ``_harness_turn`` remains the recovery drain for every other path.
        """
        self._spawn_pending_workers()

    def _spawn_pending_workers(self) -> None:
        """Start every queued TaskEnvelope that has no active Runtime task."""
        if self._stopping:
            return
        for worker_id, envelope in tuple(self._task_envelopes.items()):
            if worker_id not in self._worker_turns and worker_id not in self._direct_turns:
                pending = self._find_pending_worker(worker_id=worker_id)
                if pending is not None:
                    self._start_worker_turn(*pending)

    def _start_worker_turn(self, agent_id: str, envelope: TaskEnvelope | None = None) -> None:
        """Start one TaskEnvelope; registration precedes create_task."""
        if agent_id in self._worker_turns:
            return
        envelope = envelope or self._task_envelopes.get(agent_id)
        if envelope is None:
            return
        async def run() -> None:
            try:
                await self._process_turn(envelope.goal, agent_id, internal=True)
            except ApprovalCancelled:
                log.info("[%s] %s 的 Runtime 回合被审批取消", self.project_id, agent_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("[%s] %s 的 Runtime 回合异常", self.project_id, agent_id)
                # [v1.0.21.1 REQ-3] 回合异常=终局：落盘失败+关信封，杜绝异常静默死循环
                try:
                    await self.fail_task(agent_id, "runtime_turn_exception")
                except Exception:
                    log.debug("[%s] %s 回合异常后的失败落盘旁路失败（忽略）", self.project_id, agent_id, exc_info=True)
                self._close_task_envelope(agent_id)
            finally:
                self._turns_active -= 1

        self._turns_active += 1
        runner = run()
        try:
            task = asyncio.create_task(runner, name=f"worker-runtime:{self.project_id}:{agent_id}")
        except Exception:
            runner.close()
            self._turns_active -= 1
            raise
        self._worker_turns[agent_id] = task
        task.add_done_callback(lambda done, aid=agent_id: self._worker_turn_finished(aid, done))

    def _worker_turn_finished(self, agent_id: str, task: asyncio.Task[None]) -> None:
        if self._worker_turns.get(agent_id) is task:
            self._worker_turns.pop(agent_id, None)
        try:
            task.exception()
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            pass
        if not self._stopping:
            self._flush_pending_dm(agent_id)
            self._spawn_pending_workers()

    def _find_pending_worker(self, *, worker_id: str = "") -> tuple[str, TaskEnvelope] | None:
        """Return one queued TaskEnvelope with no active execution task."""
        candidates = ((worker_id, self._task_envelopes.get(worker_id)),) if worker_id else tuple(self._task_envelopes.items())
        for candidate_id, envelope in candidates:
            if (
                envelope is None
                or candidate_id == COORDINATOR
                or candidate_id in self._worker_turns
                or candidate_id in self._direct_turns
            ):
                continue
            return candidate_id, envelope
        return None

    async def _process_turn(
        self,
        content: str,
        target: str | None,
        *,
        internal: bool = False,
    ) -> None:
        """Route Worker turns directly to WorkerRuntime and retain Coordinator handling."""
        worker_id = target if target and target != COORDINATOR else None
        try:
            if worker_id is not None:
                envelope = self._task_envelopes.get(worker_id)
                await self._run_worker(envelope or content, worker_id, internal=internal)
            else:
                await self._process_turn_inner(content, None, internal=internal)
        except asyncio.CancelledError:
            if worker_id is not None and not self._stopping:
                reason = self._stop_reasons.get(worker_id) or STOP_REASON_USER
                await asyncio.shield(self.fail_task(worker_id, reason))
            raise
        except Exception as exc:
            if worker_id is not None:
                await self.fail_task(worker_id, msg("engine.194", **{"type(exc).__name__": type(exc).__name__, "exc": exc}))
            raise
        finally:
            if content == REJECTION_FOLLOWUP:
                self._rejection_pending = False
                self._rejection_followup_queued = False


    async def _process_turn_inner(
        self,
        content: str,
        target: str | None,
        *,
        internal: bool = False,
    ) -> None:
        """在既有串行锁内执行一次项目经理回合。"""
        if target not in {None, COORDINATOR}:
            raise RuntimeError("Worker turns must enter through WorkerRuntime")
        waited_from = None
        if self._coordinator_lock.locked():
            waited_from = asyncio.get_running_loop().time()
            log.info("[%s] 项目经理回合排队等上一轮结束…", self.project_id)
        async with self._coordinator_lock:
            if waited_from is not None:
                waited = asyncio.get_running_loop().time() - waited_from
                if waited > 30:
                    log.warning("[%s] 项目经理回合等锁 %.1fs 才开始", self.project_id, waited)
            current_task = asyncio.current_task()
            channel = str(_DM_CHANNEL_VAR.get() or self.project_id)
            scope_id = f"turn:{uuid.uuid4().hex}"
            with self._activity_scope(
                agent_id=COORDINATOR,
                scope_id=scope_id,
                channel=channel,
            ):
                try:
                    await self.emit(
                        {"type": "agent_active", "agent_id": COORDINATOR, "reason": "coordinator_turn"},
                        channel=channel,
                    )
                    # The Coordinator owns its visible audit callback. A nested Worker
                    # attempt replaces this mutable context rather than inheriting it.
                    with tool_ledger.actor_scope():
                        await self._run_agent_turn(content, internal=internal)
                finally:
                    await self._settle_actor_idle(
                        COORDINATOR,
                        channel=channel,
                        ignore_task=current_task,
                        derived_from="coordinator_turn_settled",
                    )

    async def _run_agent_turn(self, content: str, *, internal: bool = False) -> None:
        """Run one Coordinator AgentLoop turn; preserve its authoritative final text."""
        self._committed_actions_this_turn.clear()
        self._dispatched_this_turn.clear()
        dm_framing = _DM_FRAMING_VAR.get()
        is_dm = dm_framing is not None
        memory_clues = ""
        if not internal or is_dm:
            memory_clues = await self._memory_clues_block(content, retrieval_context={})

        agent = self._get_or_create_coordinator()
        structured_notification = notification_from_unknown(_STRUCTURED_NOTIFICATION_VAR.get())
        seen_rows: list[VisibleSpeech] = []
        seen_block = ""
        review_block = ""
        if structured_notification:
            completion_id = str(structured_notification.get("completion_id") or "")
            if feature_enabled(FeatureFlag.SEEN_SPEECH_V1):
                seen_rows = self.seen_speech_ledger.by_completion(completion_id, limit=3)
                seen_block = render_seen_speech_block(
                    seen_rows, total_count=self.seen_speech_ledger.count(),
                )
            choices_raw = structured_notification.get("decision_required") or ()
            choices = msg("engine.196.sep").join(choices_raw) if choices_raw else msg("engine.196.fb")
            # [v1.0.24.2] 账本字段不进 LLM 上下文（审计 PRD v1.0.24.2）：
            #   · completion_id —— 引擎幂等/记账/审计专用（notification_id + ledger），
            #     没有任何工具参数消费它，注入只会诱导 LLM 复述 cmp_ 编号 → 不注入
            #   · report_ref —— 读报告需要路径，语义化为「报告文件：…」（engine.199），
            #     不输出裸 `report_ref: -` 占位
            # 空值行一律省略（不输出 `-`），杜绝「本次 completion（cmp_xxx）」类复述。
            review_block = msg("engine.195")
            report_ref = str(structured_notification.get("report_ref") or "").strip()
            if report_ref:
                review_block += msg("engine.review.report_path", path=report_ref) + "\n"
            review_block += (
                msg("engine.196", choices=choices)
                + msg("engine.197")
                + msg("engine.198")
            )
        # Structured Completion notifications supersede the legacy report notice.
        notice = "" if structured_notification else self._report_notice()
        dm_context = ""
        if dm_framing == "coordinator":
            who = self.member_name(COORDINATOR) or msg("engine.007")
            dm_context = _engine_block("_DM_FRAMING_COORD").format(name=who, role=msg("engine.007"))
        identity_context = (
            self._identity_block(COORDINATOR)
            if feature_enabled(FeatureFlag.IDENTITY_CONTRACT_V1)
            else ""
        )
        user_address_block = self._coordinator_user_address_block()
        agent.ephemeral_system_prompt = (
            ((identity_context + "\n\n") if identity_context else "")
            + self.coordinator_soul
            + "\n\n" + STATE_HEADER + "\n\n"
            + self._team_ctx()
            + "\n\n" + self._work_status_ctx()
            + "\n\n" + self._capability_ctx()
            + "\n\n" + self._project_root_block()
            + "\n\n" + self._handoff_ctx(COORDINATOR)
            + self._project_ctx_block()
            + memory_clues
            + self._knowledge_ctx_block()
            + self._skill_ctx_block()
            + (("\n\n" + review_block) if review_block else "")
            + (("\n\n" + seen_block) if seen_block else "")
            + (("\n\n" + notice) if notice else "")
            + (("\n\n" + dm_context) if dm_context else "")
            + (("\n\n" + user_address_block) if user_address_block else "")
            + "\n\n" + _engine_block("ACTION_CONTRACT")
        )

        tool_ledger.bind_activity(
            lambda token: self._fire({
                "type": "tool_gen", "agent_id": COORDINATOR, "tool_name": token,
                **tool_ledger.stage_payload(token),
            })
        )
        # The Agent owns the raw append-only history and applies the shared ContextCompressor
        # projection immediately before each Provider call.  Engine never truncates it.
        self.repair_agent_history(agent)
        self._clear_stale_interrupt(agent)
        await self.emit({
            "type": "agent_thinking",
            "agent_id": COORDINATOR,
            "phase": "thinking",
            "stage": "plan",
            "stage_detail": msg("engine.131"),
             "stage_state": "active",
        })

        _turn_attachments = _TURN_ATTACHMENTS_VAR.get()
        result = await self._run_conversation_tracked(agent, content, attachments=_turn_attachments)
        learn_history_attr(agent, result)
        self.repair_agent_history(agent)

        # A stale interrupt flag is transport state, not a judgment about model prose.
        if (
            result.get("is_interrupted")
            and not str(result.get("final_response") or "")
            and not self._stopping
            and not self._rejection_pending
            and self.inbox.empty()
        ):
            cleared = self._probe_clear_interrupt(agent)
            if cleared:
                log.warning("[%s] 项目经理开机前清理残留中断：%s", self.project_id, ", ".join(cleared))
            await self.emit({"type": "stream_reset", "agent_id": COORDINATOR})
            await self._drain()
            result = await self._run_conversation_tracked(agent, content, attachments=_TURN_ATTACHMENTS_VAR.get())
            learn_history_attr(agent, result)
            self.repair_agent_history(agent)

        err = str(result.get("error") or "")
        if _is_tool_call_400(err):
            log.error("[%s] 项目经理撞上 tool_calls 400：%s", self.project_id, err[:200])
            if self.repair_agent_history(agent) <= 0:
                self._panic_reset(agent)
        elif err:
            log.error("[%s] 项目经理 LLM 调用出错：%s", self.project_id, err[:200])

        await self._drain()
        raw_final_value = result.get("final_response")
        raw_final = raw_final_value if isinstance(raw_final_value, str) else str(raw_final_value or "")
        final = self.absorb_markers(raw_final)

        if result.get("is_interrupted") and self._rejection_pending:
            self._clear_stream_buffer(COORDINATOR)
            await self.emit({"type": "stream_reset", "agent_id": COORDINATOR})
            await self.emit({"type": "message", "agent_id": COORDINATOR, "content": ""})
            return

        if result.get("is_interrupted") and final == "":
            successor_will_speak = internal or not self.inbox.empty()
            if successor_will_speak:
                self._clear_stream_buffer(COORDINATOR)
                await self.emit({"type": "stream_reset", "agent_id": COORDINATOR})
                await self.emit({"type": "message", "agent_id": COORDINATOR, "content": ""})
                return

        # Provider/protocol failures are reported transparently.  An empty or terse model
        # final without an actual error remains authoritative and is not retried or rewritten.
        if err and final == "" and not self._dispatched_this_turn and not self._committed_actions_this_turn:
            self._clear_stream_buffer(COORDINATOR)
            await self.emit({"type": "stream_reset", "agent_id": COORDINATOR})
            await self.emit({"type": "message", "agent_id": COORDINATOR, "content": ""})
            _key, human = _humanize_llm_error(err, self._provider_binding_used_by(agent))
            # [v1.0.19.4] 决策 #8：机器报错经辅助模型译成人话；失败/超时兜底原文。
            human = await self._aux_translate_error(human)
            await self.emit({
                "type": "error",
                "agent_id": COORDINATOR,
                "message": msg("engine.199", human=human),
            })
            return

        # [v1.0.23.3] 推理全文随 message 落定（reasoning 来自 KnoweAgent/AgentLoop）
        coordinator_message: dict[str, Any] = {
            "type": "message", "agent_id": COORDINATOR, "content": final,
        }
        if isinstance(result, dict):
            reasoning_val = result.get("reasoning")
            if reasoning_val:
                coordinator_message["reasoning"] = reasoning_val
                coordinator_message["reasoning_seconds"] = result.get("reasoning_seconds")
        await self.emit(coordinator_message)
        memory_result = self._project_memory_result(
            result,
            COORDINATOR,
            [],
            content=content,
            internal=internal,
        )
        self._schedule_project_memory(memory_result)

    # ── [v0.13] 项目根目录上下文 ──
    def _project_root_block(self) -> str:
        return _engine_block("PROJECT_ROOT_CONTEXT").format(root=str(self.workspace_root))

    # ── [v0.21 问题二] 成员能干什么 ──
    def _capability_ctx(self) -> str:
        """
        每一轮告诉项目经理：**队里的人能做什么**。

        他每轮都能看见【当前团队】（名字 + 角色），却从来没人告诉过他这些人
        **手上有什么家伙**。于是他拿自己的工具箱（只有 propose_* 和 read_report）
        当成了团队能力的天花板，然后一本正经地回用户「我没有浏览器工具，
        项目环境里也没有」——而他的成员有一整套 Chromium。

        这跟 v0.9b Bug1（「团队只能创建一次」）是**同一个病**：根不在代码里，
        在没人告诉过模型。那次的解法是把现场摊在它眼前（_team_ctx），这次一样。

        清单不写在人设里、而是每轮从注册表现生成——理由见 capabilities.py 的模块头：
        写死的清单已经过期过一次了（v0.16 的人设活到 v0.20），再写死一遍，
        v0.22 还会过期第二次。
        """
        return capabilities.coordinator_block()

    # ── [v0.21 问题一] 用户已经看到的 ──

    # ── [v0.22 问题三] Worker 收口：不许静默消失 ──




    # ═══════════════════════════════════════════════════════════
    # [v0.29 问题二] 停止一个正在干活的 Worker
    # ═══════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════
    # [v0.31 Bug1/Bug2] interrupt 卫生 + 实例退休 + 历史修剪
    # ═══════════════════════════════════════════════════════════

    #: interrupt 状态可能藏身的方法名（有就调）与布尔标记名（True 就放平）。
    #: knowe_core 的实现细节对我们不可见，所以按鸭子类型扫——
    #: 和 _HISTORY_ATTRS 找消息史是同一个路数：不硬编码一个，全都试。
    _INTERRUPT_CLEAR_METHODS = (
         "clear_interrupt", "reset_interrupt", "clear_interruption",
        "uninterrupt", "resume",
    )
    _INTERRUPT_FLAG_ATTRS = (
         "_interrupted", "interrupted", "_interrupt", "_interrupt_requested",
        "interrupt_requested", "_interrupt_flag", "_should_interrupt",
        "should_interrupt", "_stop_requested", "_cancelled",
    )

    def _probe_clear_interrupt(self, agent: Any) -> list[str]:
        """
        把这个 agent 身上**立着的**中断状态放平。返回清掉了什么（空 = 本来就干净）。

        先试显式的清除方法（如果 knowe_core 提供了，那是最正确的一扇门）；
        再扫 agent 本体和常见持有者（loop/core/state/session）上的布尔标记，
        只把值为 True 的放成 False——绝不去碰不认识的非布尔属性。
        """
        cleared: list[str] = []
        for name in self._INTERRUPT_CLEAR_METHODS:
            fn = getattr(agent, name, None)
            if callable(fn):
                try:
                    fn()
                    cleared.append(f"{name}()")
                    break                      # 显式方法就是权威出口，调一个够了
                except Exception:
                    log.debug("[%s] 调 %s.%s() 失败（继续扫标记）",
                              self.project_id, getattr(agent, "agent_id", "?"), name)
        holders = [agent] + [
            h for h in (getattr(agent, n, None) for n in _HISTORY_HOLDERS) if h is not None
        ]
        for holder in holders:
            for attr in self._INTERRUPT_FLAG_ATTRS:
                try:
                    if getattr(holder, attr, None) is True:
                        setattr(holder, attr, False)
                        cleared.append(attr)
                except Exception:
                    continue
        return cleared

    def _clear_stale_interrupt(self, agent: Any) -> None:
        """
        [v0.31] **回合开跑前的卫生检查**：一个刚要开始的回合，按定义不可能有
        「合法的、待处理的」中断——interrupt 打断的是**正在跑**的回合，
        开跑前就立着的标记只能是上一轮的残留（Bug 1/Bug 2 的共同病根）。

        唯一的例外用 `_stop_reasons` 挡住：用户刚按了「停止」、纸条已经写下、
        interrupt 已经发出，而这个回合恰好还没跑到这里——这时**不许**替他把刀
        收回去；让回合立刻被打断、走失败漏斗，才是用户要的结果。
        （stop_worker 的顺序保证了纸条先于刀：见那边「纸条先写，刀后落」。）
        """
        aid = getattr(agent, "agent_id", "?")
        if aid in self._stop_reasons:
            return                             # 停止正在路上——别拆用户的刀
        cleared = self._probe_clear_interrupt(agent)
        if cleared:
            log.warning(
                 "[%s] %s 开跑前带着残留的中断标记（%s）—— 已清除。"
                + msg("engine.200"),
                self.project_id, aid, ", ".join(cleared),
            )


    def worker_is_busy(self, agent_id: str) -> bool:
        return bool(
            agent_id in self._worker_turns
            or agent_id in self._direct_turns
            or agent_id in self._workers_with_open_activity
            or agent_id in self._task_envelopes
        )

    def _worker_has_authoritative_activity(
        self,
        agent_id: str,
        *,
        ignore_task: asyncio.Task[Any] | None = None,
    ) -> bool:
        """Return whether an idle projection would contradict current execution."""
        direct = self._direct_turns.get(agent_id)
        worker = self._worker_turns.get(agent_id)
        task_busy = bool(direct is not None and direct is not ignore_task and not direct.done()) or bool(
            worker is not None and worker is not ignore_task and not worker.done()
        )
        return bool(
            task_busy
            or agent_id in self._workers_with_open_activity
            or agent_id in self._task_envelopes
            or agent_id in self._dm_busy
            or agent_id in self._mention_busy
            or bool(self._dm_pending.get(agent_id))
        )

    # ── [v1.0.24.4] 权威活动账本（记账/销账入口在 emit；此处只放原子操作）──
    @staticmethod
    def _open_activity_key(agent_id: str, scope_id: str, channel: str) -> tuple[str, str, str]:
        return (str(agent_id), str(scope_id or ""), str(channel or ""))

    def _record_open_activity(self, agent_id: str, scope_id: str, channel: str) -> None:
        key = self._open_activity_key(agent_id, scope_id, channel)
        self._open_activity[key] = int(
            datetime.now(timezone.utc).timestamp() * 1000,
        )

    def _release_open_activity(self, agent_id: str, scope_id: str, channel: str) -> None:
        self._open_activity.pop(
            self._open_activity_key(agent_id, scope_id, channel), None,
        )

    def _purge_open_activity(self, agent_id: str) -> None:
        for key in [k for k in self._open_activity if k[0] == agent_id]:
            del self._open_activity[key]

    def open_activity_snapshot(self) -> list[dict[str, Any]]:
        """全量权威活动条目，随 replay_complete / state_snapshot 下发。

        字段与前端 activityIdentity 消费的键完全同构；started_at 为毫秒。
        """
        return [
            {
                "agent_id": agent_id,
                "scope_id": scope_id,
                "channel_id": channel_id,
                "started_at": started_at,
            }
            for (agent_id, scope_id, channel_id), started_at
            in sorted(self._open_activity.items())
        ]

    async def _emit_derived_idle(
        self,
        agent_id: str,
        *,
        channel: str | None = None,
        ignore_task: asyncio.Task[Any] | None = None,
        derived_from: str = "no_active_attempt",
    ) -> bool:
        """在没有权威占用冲突时投影角色可用态。"""

        # Harness 模式下非项目经理角色都是 Worker；单 Agent（含知知）跳过 Worker 专用 guard。
        is_worker = self.agent is None and agent_id != COORDINATOR
        if is_worker and self._worker_has_authoritative_activity(
            agent_id, ignore_task=ignore_task,
        ):
            return False
        await self.emit(
            {
                "type": "agent_idle",
                "agent_id": agent_id,
                "status": "AVAILABLE",
                "terminal": False,
                "derived": True,
                "derived_from": derived_from,
            },
            channel=channel,
        )
        return True

    async def _settle_actor_idle(
        self,
        agent_id: str,
        *,
        channel: str | None = None,
        ignore_task: asyncio.Task[Any] | None = None,
        derived_from: str,
    ) -> None:
        """完成极短的 idle 投影，同时保留原回合的取消与异常语义。"""
        idle_task = asyncio.create_task(
            self._emit_derived_idle(
                agent_id,
                channel=channel,
                ignore_task=ignore_task,
                derived_from=derived_from,
            )
        )
        try:
            await asyncio.shield(idle_task)
        except asyncio.CancelledError:
            try:
                await idle_task
            except asyncio.CancelledError:
                log.debug(
                     "[%s] %s 可用态投影任务被取消",
                    self.project_id,
                    agent_id,
                )
            except Exception:
                log.debug(
                     "[%s] %s 可用态投影失败",
                    self.project_id,
                    agent_id,
                    exc_info=True,
                )
            raise
        except Exception:
            log.debug(
                 "[%s] %s 可用态投影失败",
                self.project_id,
                agent_id,
                exc_info=True,
            )

    def busy_workers(self) -> list[str]:
        """此刻手上有活的成员 id（只算在册的活跃成员）。"""
        return [aid for aid in self._roster if self.worker_is_busy(aid)]

    async def stop_worker(self, agent_id: str, *, reason: str = STOP_REASON_USER) -> dict[str, Any]:
        """Cancel one authoritative Runtime task and commit its deterministic failure."""
        if not self.worker_is_busy(agent_id):
            return {"ok": False, "reason": msg("engine.203")}
        self._stop_reasons[agent_id] = reason
        who = self.member_name(agent_id)
        task = self._direct_turns.get(agent_id) or self._worker_turns.get(agent_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # [v1.0.21.1 REQ-4] 取消=终局：关信封，杜绝取消后立即再拉起
            self._close_task_envelope(agent_id)
            return {"ok": True, "name": who, "message": msg("engine.204", who=who)}
        await self.fail_task(agent_id, reason)
        return {"ok": True, "name": who, "message": msg("engine.205", who=who)}

    # ── [v0.22 问题二] 此刻谁在干活（引擎的硬事实）──
    def _work_status_ctx(self) -> str:
        """
        引擎知道谁在干活。**说出来。**

        这是 v0.9b `_team_ctx`、v0.21 `_seen_by_user_ctx` 的同一条路子：
        模型不会自己知道现场，你不摊开，它就自己编。而这一格恰恰是它编得最贵的地方——
        编出来的是「有人在给你干活」，用户会照着这句话干等。
        """
        # [v0.37.5 模块1] 有排队私聊消息（_dm_pending）= **即将忙**：也算进忙碌名单。
        #   补的是 submit_dm 里「create_task → 实际执行 add」之间那道竞态缝——task 还没跑到
        #   _workers_with_open_activity.add 时项目经理若恰好轮询这里，会误报空闲。排队即入账，堵住它。
        dm_pending = set(self._dm_pending.keys())
        mention_pending = {
            aid for aid, queue in self._dm_pending.items()
            if any(len(row) >= 3 and bool(row[2]) for row in queue)
        }
        busy = [aid for aid in self._roster
                if self.worker_is_busy(aid) or aid in dm_pending]
        if not busy:
            return _engine_block("WORK_STATUS_CONTEXT").format(status=NO_ONE_WORKING)
        # [v0.37.4 Bug2] 区分「群里派活忙」和「私聊里被用户直接拉去忙」——后者不是你派的，
        #   但他确实脱不开身；不论哪种，都**不要再给他派活**（propose_next 也会挡）。
        #   [v0.37.5] 排队中的人（dm_pending）同样标「私聊中」——他马上就要开聊了。
        def _status_line(aid: str) -> str:
            prefix = msg("engine.206",
                            member_name=self.member_name(aid),
                            roster_name=self._roster.get(aid, msg("engine.141.fb")))
            if aid in self._mention_busy or aid in mention_pending:
                return prefix + msg("engine.207")
            if aid in self._dm_busy or aid in dm_pending:
                return prefix + msg("engine.208")
            return prefix + msg("engine.211")

        lines = "\n".join(_status_line(aid) for aid in busy)
        others = len(self._roster) - len(busy)
        tail = (msg("engine.212", others=others)
                + msg("engine.213")) if others else ""
        return _engine_block("WORK_STATUS_CONTEXT").format(status=lines + tail)

    # ── [v0.11 C-1 / v0.16] 三层 Memory 集成 ──
    def _restore_project_memory_counter(self) -> None:
        """从持久层恢复累计回合数；构造期、启动期均可安全重复调用。"""
        if self._memory is None or self.project_id == "__platform__":
            return
        try:
            persisted = self._memory.project_turn_count(self.internal_workspace)
            self._turn_count = max(int(self._turn_count), int(persisted))
        except Exception:
            log.exception("[%s] 恢复 Project Memory 回合数失败（按当前值继续）", self.project_id)

    def _prepare_project_memory(self) -> None:
        """启动前创建/迁移长期记忆，并再次校准累计回合数。"""
        if self._memory is None or self.project_id == "__platform__":
            return
        try:
            self.ensure_internal_layout()
            self._memory.ensure_project_context(self.internal_workspace)
            self._restore_project_memory_counter()
        except WorkspaceUnavailable:
            raise
        except Exception:
            log.exception("[%s] 初始化 Project Memory v2 失败（Memory 降级）", self.project_id)

    async def _memory_clues_block(
        self, message: str, *, retrieval_context: dict[str, Any] | None = None,
    ) -> str:
        """为当前用户消息生成短小的深层历史线索块；失败/空结果一律返回空串。

        auxiliary 只做 query expansion；历史检索仍走现有 ``search_project_memory``
        本地逻辑，不经过 Agent 工具调用。快照 ``recent`` 已经常驻 [项目上下文]，因此
        这里从最早一条 recent 之前开始查，只补更深层历史，避免和“最近动态”重复占预算。
        """
        if retrieval_context is not None:
            retrieval_context.clear()
        if self._memory is None or self.project_id == "__platform__":
            return ""
        clean = " ".join(str(message or "").split())
        if not clean:
            return ""
        try:
            self.ensure_internal_layout()
            state = self._memory.read_project_state(self.internal_workspace)
            history_records = int(state.get("history_records") or 0)
            if history_records <= 0:
                return ""

            recent_ids: set[str] = set()
            for line in state.get("recent") or []:
                recent_ids.update(
                    match.group(1).lower()
                    for match in _MEMORY_ID_IN_TEXT_RE.finditer(str(line or ""))
                )
            # 所有历史都还在有界快照里时，没有“深层历史”需要另起一块提醒。
            if recent_ids and history_records <= len(recent_ids):
                return ""
            deep_cursor = (
                min(recent_ids, key=lambda value: int(value[1:]))
                if recent_ids else None
            )

            if retrieval_context is not None:
                retrieval_context["keywords_attempted"] = True
            keywords = await self._memory.extract_retrieval_keywords(clean)
            if retrieval_context is not None:
                retrieval_context["keywords"] = tuple(keywords)
            if len(keywords) < 2:
                return ""

            candidates: dict[str, dict[str, Any]] = {}
            for keyword in keywords:
                # newest + cursor 会直接从 recent 窗口之前开始，不必先读取再丢掉最近 N 条。
                result = self.search_project_memory(
                    keyword,
                    limit=_MEMORY_CLUE_SEARCH_LIMIT,
                    order="newest",
                    cursor=deep_cursor,
                )
                rows = result.get("results") if isinstance(result, dict) else None
                if not isinstance(rows, list):
                    continue
                folded_keyword = keyword.casefold()
                for rank, raw_row in enumerate(rows):
                    if not isinstance(raw_row, dict):
                        continue
                    memory_id = str(raw_row.get("memory_id") or "").strip().lower()
                    if not _MEMORY_ID_RE.fullmatch(memory_id) or memory_id in recent_ids:
                        continue
                    row = candidates.get(memory_id)
                    if row is None:
                        row = dict(raw_row)
                        row["memory_id"] = memory_id
                        row["_keywords"] = set()
                        row["_rank_score"] = 0.0
                        row["_summary_hits"] = 0
                        candidates[memory_id] = row
                    row["_keywords"].add(folded_keyword)
                    row["_rank_score"] += 1.0 / (rank + 1)
                    searchable = " ".join(
                        str(raw_row.get(key) or "") for key in ("summary", "match")
                    ).casefold()
                    if folded_keyword and folded_keyword in searchable:
                        row["_summary_hits"] += 1

            prepared: list[dict[str, Any]] = []
            for row in candidates.values():
                summary = row.get("summary") or row.get("match") or ""
                if not str(summary).strip():
                    continue
                row["_score"] = (
                    12.0 * len(row.get("_keywords") or ())
                    + float(row.get("_rank_score") or 0.0)
                    + 2.0 * int(row.get("_summary_hits") or 0)
                )
                row["_topic_tokens"] = _memory_topic_tokens(
                    f"{row.get('summary') or ''} {row.get('match') or ''}"
                )
                prepared.append(row)
            if not prepared:
                return ""

            compact_len = len(re.sub(r"\s+", "", clean))
            wanted = _MEMORY_CLUE_SHORT_LIMIT if compact_len < 18 else _MEMORY_CLUE_LIMIT
            selected = _select_diverse_memory_clues(prepared, wanted)
            block = _render_memory_clues(selected)
            if retrieval_context is not None:
                # 只对**实际渲染进 prompt** 的项目线索去重；预算收缩掉的候选不能误伤个人线索。
                rendered_ids = {
                    match.group(1).lower()
                    for match in _MEMORY_ID_IN_TEXT_RE.finditer(block)
                }
                retrieval_context["project_rows"] = tuple(
                    row for row in selected
                    if str(row.get("memory_id") or "").lower() in rendered_ids
                )
            return block
        except asyncio.CancelledError:
            raise
        except Exception:
            # 预检索是增强层：任何迁移、读取、解析或检索异常都不能污染主对话。
            log.debug(
                 "[%s] Project Memory 静默预检索失败，本轮跳过",
                self.project_id,
                exc_info=True,
            )
            return ""

    def _project_ctx_block(self) -> str:
        """把内部 Project Memory 注进 prompt；物理路径不暴露给 Agent。

        [v0.37.2 Bug2] 末尾自动附**最近动态**（`_recent_project_activity`，含成员在私聊里干的活）。
          原本这份即时动态只在 read_harness_memory 工具里暴露——项目经理**主动调**才看得到，
          于是「梁峦十在私聊里交了份报告」这种事，项目经理不去查就不知道，回头在群里发懵。
          Project Memory 是**异步摘要**，dm 动作刚发生时还没落盘；即时动态正是拿来填这段时间差的。
          把它每轮自动注进上下文 → 项目经理（以及任何读项目上下文的成员）不必调工具就一直知情。
        """
        ctx = ""
        if self._memory is not None:
            try:
                self.ensure_internal_layout()
                ctx = self._memory.read_project_context(self.internal_workspace).strip()
            except Exception:
                ctx = ""

        recent = self._recent_activity_block()

        if not ctx and not recent:
            return ""
        out = msg("engine.ctx.project_context")
        if ctx:
            out += "\n" + ctx
        out += recent          # 空串则无影响
        return out

    def _recent_activity_block(self, limit: int = 6) -> str:
        """[v0.37.2 Bug2] 最新几条即时动态（latest first），每轮自动注入，无需主动调工具。

        `_recent_project_activity` 最新在末尾 → 反转取前 `limit` 条。空则返回空串。
        它涵盖团队变动、派活、以及**成员在私聊中的响应**——正是项目经理过去看不见的那部分。
        """
        raw_items = [ln.strip() for ln in reversed(self._recent_project_activity) if ln and ln.strip()]
        # [v0.44.12] 防御性合并成员状态：即使列表里混进了旧版本留下的
        # 「已归档」+「已恢复成员」，也按 latest-first 只展示最新状态。
        items: list[str] = []
        seen_member_state: set[str] = set()
        grouped_prefixes = (msg("engine.214"), msg("engine.215"))
        for line in raw_items:
            prefix = next((p for p in grouped_prefixes if line.startswith(p)), None)
            if prefix is not None:
                labels = [part.strip() for part in line[len(prefix):].split("、") if part.strip()]
                kept: list[str] = []
                for label in labels:
                    name = self._member_name_from_activity_label(label)
                    if name and name in seen_member_state:
                        continue
                    if name:
                        seen_member_state.add(name)
                    kept.append(label)
                if kept:
                    items.append(prefix + "、".join(kept))
                continue

            if msg("engine.216") in line:
                name = line.split(msg("engine.216"), 1)[0].strip()
                if name and name in seen_member_state:
                    continue
                if name:
                    seen_member_state.add(name)
            items.append(line)
        if not items:
            return ""
        lines = "\n".join(f"- {ln}" for ln in items[:limit])
        return (
             msg("engine.ctx.recent_activity")
            + lines
        )

    def _knowledge_ctx_block(self) -> str:
        """[v0.42] 常驻知识块：PROFILE.md 全文 + 资产索引 L0 行（渐进披露的第一级）。

        替换掉 v0.19 的「importance 前 6 名硬塞」——每一行都是可用
        read_knowledge_asset(id) 展开的入口，而不是只读结论。资产层还是空的
        （老项目刚升级 / 蒸馏尚未产出）时回落到旧 brief，让冷启动期不失明。
        """
        if self._knowledge is None or self.project_id == "__platform__":
            return ""
        try:
            self.ensure_internal_layout()
            if self._assets is not None:
                block = self._assets.context_block(
                    self.project_id, self.internal_workspace,
                ).strip()
                if block:
                    return "\n\n" + block
            brief = self._knowledge.brief(
                self.project_id, self.internal_workspace, limit=6,
            ).strip()
        except Exception:
            return ""
        return ("\n\n" + brief) if brief else ""

    def _skill_ctx_block(self) -> str:
        """[v0.43] 生效技能 L0 索引；待审/退役项在这里被 Harness 硬过滤。"""
        if self._assets is None or self.project_id == "__platform__":
            return ""
        try:
            self.ensure_internal_layout()
            return self._assets.skill_context_block(
                self.project_id, self.internal_workspace,
            )
        except Exception:
            log.debug("[%s] 技能索引注入失败（忽略）", self.project_id, exc_info=True)
            return ""

    def _project_memory_result(
        self,
        result: dict[str, Any],
        agent_id: str,
        direct_speeches: list[str],
        *,
        content: str = "",
        internal: bool = False,
    ) -> dict[str, Any]:
        """
        让既有 Project Memory 管线稳定看见公开正文和结构化 Runtime 终态。

        Coordinator 正常回合与 Worker Runtime 交付都只通过这一条 enrich 管线，再由
        ``_schedule_project_memory`` 串行落盘；Worker 侧用 idempotency key 防止重放重复记账。
        """
        if not direct_speeches and not content and not internal:
            return result
        enriched = dict(result)
        # Project Memory v2 同时保存“这一轮收到什么”和“这一轮产出什么”。过去只记
        # final_response，主管被问“最开始说了什么”时即使历史没被裁掉也无从回答。
        enriched["_memory_input"] = content
        enriched["_memory_internal"] = bool(internal)
        enriched["_memory_agent_id"] = agent_id
        enriched["_memory_agent_name"] = self.member_name(agent_id)
        if not direct_speeches:
            return enriched
        who = self.member_name(agent_id)
        enriched["direct_speech"] = [
            {"agent_id": agent_id, "name": who, "content": text}
            for text in direct_speeches
        ]
        enriched["final_response"] = "\n\n".join(
            msg("engine.217", who=who, text=text) for text in direct_speeches
        )
        return enriched


    def _schedule_project_memory(self, result: dict[str, Any]) -> None:
        """把一次 Project Memory 更新排成后台任务（不阻塞）。memory 没配就直接跳过。"""
        if self._memory is None:
            return
        self._turn_count += 1
        try:
            members = len(self.roster())          # 活跃成员数（不含项目经理）
        except Exception:
            members = None
        try:
            previous = self._memory_tail
            turn_count = self._turn_count

            async def update_in_order() -> None:
                if previous is not None:
                    try:
                        await previous
                    except (asyncio.CancelledError, Exception):
                        # 前一轮记忆失败不应阻断后一轮；update_project_context 本身也是尽力而为。
                        pass
                await self._memory.update_project_context(
                    self.internal_workspace, result, turn_count, members=members,
                )

            t = asyncio.create_task(update_in_order())
            self._memory_tail = t
            self._memory_tasks.add(t)
            t.add_done_callback(self._project_memory_finished)
        except Exception:
            log.exception("[%s] 排 Project Memory 更新失败（忽略）", self.project_id)

    def _project_memory_finished(self, task: asyncio.Task[Any]) -> None:
        """Project Memory 真正落盘后，再提醒 Harness 刷一遍精炼后的摘要。"""
        self._memory_tasks.discard(task)
        if self._memory_tail is task:
            self._memory_tail = None
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            log.exception("[%s] Project Memory 后台任务异常（忽略）", self.project_id)
            return
        self._notify_harness_activity("project_memory_updated")

    @staticmethod
    def _approval_decision(approval_path: Path) -> str:
        """从 .approval-NN.md 的 frontmatter 里读 decision（读不到返回空串）。"""
        try:
            if not approval_path.is_file():
                return ""
            head = approval_path.read_text("utf-8", errors="replace")[:600]
            m = re.search(r"^decision:\s*\"?([a-z_]+)\"?\s*$", head, re.M)
            return m.group(1) if m else ""
        except OSError:
            return ""

    # ── [v0.42] 知知蒸馏：主模型档位的 aux 调用（T1/T2 共用）──
    async def _knowledge_distill_call(self, system: str, user: str) -> str:
        """
        蒸馏/合并的 LLM 通道。
        [v0.44 设置 §2.2] 模型解析：KNOWE_KG_DISTILL_MODEL 环境变量仍是最高优先
        （运维口径不变）；其后是设置面板的「辅助模型」绑定；都没有 → 老 CONFIG 默认。
        """
        aux = runtime_settings.aux_effective()
        aux_ok = bool(aux and aux.get("api_key") and aux.get("base_url"))
        model = (
            CONFIG.knowledge_distill_model
            or (aux["model"] if aux_ok else "")
            or CONFIG.deepseek_model
        )
        return await aux_client.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            api_key=aux["api_key"] if aux_ok else CONFIG.deepseek_api_key,
            base_url=aux["base_url"] if aux_ok else CONFIG.deepseek_base_url,
            model=model,
            timeout_s=max(5.0, float(CONFIG.knowledge_distill_timeout_s)),
            what=msg("engine.218"),
        )

    def _schedule_knowledge_distill(
        self,
        *,
        step: int | None,
        instruction_path: Path | None,
        report_path: Path | None,
        approval_path: Path | None,
        decision: str,
        worker_suggest: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """T1 任务收尾蒸馏 → 排在知识串行尾巴上（接在同步 approval 摄入之后，天然有序）。"""
        if self._assets is None:
            return
        assets = self._assets
        meta = dict(metadata or {})
        self._enqueue_knowledge_update(
            lambda: assets.distill_task(
                self.project_id, self.internal_workspace,
                step=step,
                instruction_path=instruction_path,
                report_path=report_path,
                approval_path=approval_path,
                decision=decision,
                worker_suggest=worker_suggest,
                metadata=meta,
            ),
            label=f"distill:{step}",
        )

    def _schedule_knowledge_consolidate(self, *, reason: str) -> None:
        """T2 周期性合并（去重 / case→rule / 冲突 / 退役建议）→ 同一条串行尾巴。"""
        if self._assets is None:
            return
        assets = self._assets
        self._approved_since_consolidate = 0
        self._enqueue_knowledge_update(
            lambda: assets.consolidate(self.project_id, self.internal_workspace),
            label=f"consolidate:{reason}",
        )

    def _start_consolidate_timer(self) -> None:
        """兜底定时器：即使 approved 数不满 N，隔段时间也会做一次 T2（nightly 语义）。"""
        if self._assets is None or self._knowledge_consolidate_timer is not None:
            return
        interval = max(600.0, float(CONFIG.knowledge_consolidate_interval_s))

        async def tick() -> None:
            try:
                while True:
                    await asyncio.sleep(interval)
                    self._schedule_knowledge_consolidate(reason="timer")
            except asyncio.CancelledError:
                pass

        try:
            self._knowledge_consolidate_timer = asyncio.create_task(
                tick(), name=f"knowledge-consolidate:{self.project_id}",
            )
        except Exception:
            log.exception("[%s] T2 定时器启动失败（忽略）", self.project_id)

    # ── [v0.19] 项目知识图谱：后台、串行、可重建 ──
    def _schedule_knowledge_bootstrap(self) -> None:
        """启动时补录已有 handoff；重复调用安全，manager 会按内容 hash 跳过。"""
        if self._knowledge is None or self.project_id == "__platform__":
            return
        self._enqueue_knowledge_update(
            lambda: self._knowledge.bootstrap_project(
                self.project_id, self.internal_workspace,
            ),
            label="bootstrap",
        )

    def _schedule_knowledge_update(
        self, path: Path, source_kind: str, metadata: dict[str, Any] | None = None,
    ) -> None:
        """Durable handoff 已写完 → 排图谱更新；此函数本身不做 IO/LLM 等待。"""
        if self._knowledge is None or self.project_id == "__platform__":
            return
        source = Path(path)
        meta = dict(metadata or {})
        self._enqueue_knowledge_update(
            lambda: self._knowledge.ingest_handoff(
                self.project_id, self.internal_workspace, source, source_kind, meta,
            ),
            label=f"{source_kind}:{source.name}",
        )

    def _enqueue_knowledge_update(
        self, factory: Callable[[], Any], *, label: str,
    ) -> None:
        """同一项目的图谱任务排成一条尾链，避免并发覆盖 `.graph.json`。"""
        try:
            previous = self._knowledge_tail

            async def update_in_order() -> None:
                if previous is not None:
                    try:
                        # shield 避免取消当前尾任务时顺带取消前一项；同时区分
                        # “前一项已取消”和“当前任务收到取消”，后者必须继续向外传播。
                        await asyncio.shield(previous)
                    except asyncio.CancelledError:
                        current = asyncio.current_task()
                        if current is not None and current.cancelling():
                            raise
                        # 前一项已取消；handoff 仍是可重放真源，继续处理后一来源。
                    except Exception:
                        # 前一项失败不阻断后续来源。
                        pass
                result = factory()
                if inspect.isawaitable(result):
                    await result

            task = asyncio.create_task(
                update_in_order(), name=f"knowledge:{self.project_id}:{label}",
            )
            self._knowledge_tail = task
            self._knowledge_tasks.add(task)
            task.add_done_callback(self._knowledge_task_finished)
        except Exception:
            log.exception("[%s] 排知识图谱更新失败（忽略）：%s", self.project_id, label)

    def _knowledge_task_finished(self, task: asyncio.Task[Any]) -> None:
        self._knowledge_tasks.discard(task)
        if self._knowledge_tail is task:
            self._knowledge_tail = None
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            log.exception("[%s] 知识图谱后台任务异常（忽略）", self.project_id)

    def _notify_harness_activity(self, reason: str) -> None:
        callback = self._activity_callback
        if callback is None or self.project_id == "__platform__":
            return
        try:
            callback(self.project_id, reason)
        except Exception:
            log.exception("[%s] 通知 Harness Memory 刷新失败（忽略）", self.project_id)

    def record_project_activity(
        self, text: str, *, reason: str = "activity", notify: bool = True,
    ) -> None:
        """记录一条不依赖摘要 LLM 的实时项目动态。

        这不是审计日志，也不替代 Project Memory；它只负责填平“动作刚发生、异步摘要
        尚未落盘”的时效窗口。最多保留 8 条，Harness 摘要只取其中最新几条。
        """
        clean = sanitize_text(" ".join((text or "").split()), self._public_names()).strip()
        clean = _strip_control_markers(clean)  # [I-6] no internal markers into harness memory
        if not clean:
            return
        if len(clean) > 180:
            clean = clean[:180].rstrip() + "…"
        if not self._recent_project_activity or self._recent_project_activity[-1] != clean:
            self._recent_project_activity.append(clean)
            self._recent_project_activity = self._recent_project_activity[-8:]
        if notify:
            self._notify_harness_activity(reason)

    @staticmethod
    def _member_name_from_activity_label(label: str) -> str:
        """`陆上初（产品）` → `陆上初`。只解析系统自己生成的成员动态。"""
        return str(label or "").split("（", 1)[0].strip()

    def _drop_member_lifecycle_activity(self, names: set[str]) -> None:
        """删掉这些成员在即时动态里的旧状态，只保留随后写入的最新状态。

        旧版本把多人加入合在一行（`团队新增成员：A、B`），所以不能粗暴整行删除：
        只摘掉命中的成员标签，未命中的同伴仍要保留。
        """
        targets = {" ".join(str(name or "").split()) for name in names if str(name or "").strip()}
        if not targets:
            return

        kept: list[str] = []
        grouped_prefixes = (msg("engine.214"), msg("engine.215"))
        for raw in self._recent_project_activity:
            line = str(raw or "").strip()
            if not line:
                continue

            prefix = next((p for p in grouped_prefixes if line.startswith(p)), None)
            if prefix is not None:
                labels = [part.strip() for part in line[len(prefix):].split("、") if part.strip()]
                labels = [
                    label for label in labels
                    if self._member_name_from_activity_label(label) not in targets
                ]
                if labels:
                    kept.append(prefix + "、".join(labels))
                continue

            # 同时兼容旧措辞「已归档，不再接新任务」和新措辞「已归档（需要时可拉回团队）」。
            if any(line.startswith(msg("engine.219", name=name)) for name in targets):
                continue
            kept.append(line)

        self._recent_project_activity = kept[-8:]

    def _record_activity_from_event(self, payload: dict[str, Any]) -> None:
        """把团队/派活事件翻成给 Harness Memory 看的确定性短句。"""
        etype = payload.get("type")
        if etype == "agents_created":
            rows = payload.get("members") or []
            created_labels: list[str] = []
            restored_labels: list[str] = []
            names: set[str] = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                aid = str(row.get("id") or "").strip()
                name = str(row.get("name") or "").strip()
                role = str(row.get("role") or msg("engine.106")).strip()
                label = f"{name}（{role}）" if name else role
                if name:
                    names.add(name)
                change = getattr(self, "_pending_member_activity", {}).pop(aid, "") if aid else ""
                # 兼容未来调用方显式携带 restored；当前生产路径以 add_member 的时序账为准。
                if change == "restored" or row.get("restored") is True:
                    restored_labels.append(label)
                else:
                    created_labels.append(label)

            self._drop_member_lifecycle_activity(names)
            if restored_labels:
                self.record_project_activity(
                    msg("engine.215") + "、".join(restored_labels), reason="team_changed",
                )
            if created_labels:
                self.record_project_activity(
                    msg("engine.214") + "、".join(created_labels), reason="team_changed",
                )
        elif etype == "agent_removed":
            target = payload.get("target_id")
            if isinstance(target, str):
                name = self.member_name(target)
                self._drop_member_lifecycle_activity({name})
                self.record_project_activity(
                    msg("engine.220", name=name),
                    reason="team_changed",
                )
        elif etype == "instruction_injected":
            target = payload.get("target_id")
            if isinstance(target, str):
                # [v0.30 Bug6] 这里**不再**维护 `_workers_with_open_activity`——
                #   那笔账在 inject_instruction 里、和 _task_envelopes 一起、在派活的
                #   那一刻原子地记好了。这里只剩它本来的活：写一条实时动态。
                role = self._roster.get(target, msg("engine.106"))
                self.record_project_activity(
                    msg("engine.221", role=role),
                    reason="work_started",
                )

    def read_harness_memory(self) -> str:
        """[v0.11 C-1] 全局公告栏全文（read_harness_memory 工具调它）。没配 memory → 一句占位。"""
        if self._memory is None:
            return msg("engine.222")
        return self._memory.read_harness()

    def search_project_memory(
        self, query: str = "", *, start_time: str | None = None,
        end_time: str | None = None, limit: int = 12, order: str = "newest",
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """按关键词/时间/先后顺序检索逐回合 Project Memory；全程本地只读。"""
        if self._memory is None:
            return {
                "query": query, "results": [], "count": 0,
                "message": msg("engine.223"),
            }
        self.ensure_internal_layout()
        return self._memory.search_project_history(
            self.internal_workspace, query=query, start_time=start_time,
            end_time=end_time, limit=limit, order=order, cursor=cursor,
        )

    def read_project_memory(self, reference: str | int) -> dict[str, Any]:
        """展开一条逐回合 Project Memory，返回有界保存的原始输入、产出与动作。"""
        if self._memory is None:
            return {
                "found": False, "reference": str(reference),
                "message": msg("engine.223"),
            }
        self.ensure_internal_layout()
        return self._memory.read_project_history(
            self.internal_workspace, reference,
        )

    def search_agent_memory(
        self, agent_id: str, query: str, *, limit: int = 12, order: str = "newest",
    ) -> dict[str, Any]:
        """只读检索当前 Worker 自己的 worklog；不搜索别的 Agent 或 Project Memory。"""
        clean = " ".join(str(query or "").split())
        if not clean:
            raise ValueError(msg("engine.224"))
        if agent_id == COORDINATOR:
            return {
                "query": clean, "results": [], "count": 0, "total_matches": 0,
                "message": msg("engine.225"),
            }
        try:
            bounded_limit = min(50, max(1, int(limit)))
        except (TypeError, ValueError, OverflowError):
            bounded_limit = 12
        normalized_order = str(order or "newest").strip().lower()
        if normalized_order not in {"newest", "oldest"}:
            raise ValueError(msg("engine.226"))

        _, rows = self._read_agent_worklog_records(agent_id)
        matches: list[dict[str, Any]] = []
        for row in rows:
            score = _agent_memory_match_score(row, clean)
            if score is None:
                continue
            found = dict(row)
            found["_score"] = score
            matches.append(found)
        matches.sort(
            key=lambda row: (
                float(row.get("_score") or 0.0),
                int(row.get("_ordinal") or 0)
                if normalized_order == "newest"
                else -int(row.get("_ordinal") or 0),
            ),
            reverse=True,
        )
        selected = matches[:bounded_limit]
        results = [_agent_memory_public_row(row) for row in selected]
        message = "" if results else msg("engine.227")
        truncated = len(matches) > len(results)
        payload: dict[str, Any] = {
            "query": clean,
            "scope": "current_agent_worklog",
            "results": results,
            "count": len(results),
            "total_matches": len(matches),
            "truncated": truncated,
            **({"message": message} if message else {}),
        }
        if truncated:
            payload["source_ref"] = f"agent-memory://{agent_id}/worklog"
        return payload

    def search_project_knowledge(
        self, query: str, *, limit: int = 6, include_contested: bool = True,
    ) -> dict[str, Any]:
        """项目经理/Worker 共用的只读图谱检索；只查预计算结果，不在工具回合里调 LLM。"""
        if self._knowledge is None:
            return {"query": query, "revision": 0, "results": [],
                    "message": msg("engine.228")}
        self.ensure_internal_layout()
        return self._knowledge.search(
            self.project_id, self.internal_workspace, query,
            limit=limit, include_contested=include_contested,
        )

    def read_project_knowledge(self, reference: str) -> dict[str, Any]:
        """读取一个知识节点的来源、信号与关联。"""
        if self._knowledge is None:
            return {"found": False, "reference": reference,
                    "message": msg("engine.228")}
        self.ensure_internal_layout()
        return self._knowledge.read_node(
            self.project_id, self.internal_workspace, reference,
        )

    def read_knowledge_asset(self, asset_id: str) -> dict[str, Any]:
        """[v0.42] L1 披露：展开一条知识资产的 ASSET.md 全文（含证据指针）。"""
        if self._assets is None:
            return {"found": False, "asset_id": asset_id,
                    "message": msg("engine.229")}
        self.ensure_internal_layout()
        return self._assets.read_asset(
            self.project_id, self.internal_workspace, asset_id,
        )

    def read_skillpack(self, pack_id: str) -> dict[str, Any]:
        """[v0.43] Agent 技能入口：只允许 active；UI 的详情端点不走这道门。"""
        if self._assets is None:
            return {"found": False, "pack_id": pack_id,
                    "message": msg("engine.230")}
        self.ensure_internal_layout()
        return self._assets.read_skillpack(
            self.project_id, self.internal_workspace, pack_id,
            active_only=True,
        )


    def iter_worker_retrieval_context(
        self,
        worker_id: str,
        task_text: str,
    ) -> Iterable[str]:
        """Yield complete, read-only Worker context items in deterministic priority order.

        The method deliberately reuses the existing Engine/Manager read boundaries.  It
        performs no model call or network access, requests no source-data mutation, and
        never reads another Agent's worklog. The caller may include these items in an
        explicit task context when it is useful.
        """

        query = str(task_text or "").strip()[:16_000]
        current_worker = str(worker_id or "").strip()
        secret_keys = {
            "api_key", "apikey", "access_token", "refresh_token", "authorization",
            "client_secret", "password", "secret_key",
        }
        path_keys = {
            "path", "absolute_path", "local_path", "file_path", "workspace",
            "workspace_root", "internal_workspace",
        }

        local_roots: list[str] = []
        for attribute in ("internal_workspace", "workspace_root"):
            try:
                raw_root = getattr(self, attribute, None)
                if raw_root in (None, ""):
                    continue
                clean_root = str(Path(raw_root).expanduser().resolve(strict=False))
            except Exception:
                # An unavailable user workspace must not suppress otherwise valid
                # internal project context; root discovery is optional redaction data.
                continue
            if len(clean_root) > 3 and clean_root not in local_roots:
                local_roots.append(clean_root)
        local_roots.sort(key=len, reverse=True)

        assignment_secret = re.compile(
            r"(?i)(\b[a-z0-9_-]*(?:api[_ -]?key|access[_ -]?token|"
            r"refresh[_ -]?token|auth[_ -]?token|authorization|"
            r"client[_ -]?secret|password|passwd|secret(?:[_ -]?key)?)\b"
            r"\s*[\"']?\s*[:=]\s*)[\"']?[^\s,;}\]]{4,}"
        )
        secret_field = re.compile(
            r"(?:^|_)(?:api_?key|apikey|access_token|refresh_token|auth_token|"
            r"authorization|client_secret|password|passwd|secret(?:_key)?)(?:$|_)"
        )
        bearer_secret = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
        sk_secret = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b", re.I)
        windows_absolute = re.compile(r"^[A-Za-z]:[\\/]")

        def _clean_text(value: Any) -> str:
            text = str(value or "").replace("\x00", "")
            for root in local_roots:
                text = text.replace(root, "<project-local>")
                text = text.replace(root.replace("/", "\\"), "<project-local>")
            text = bearer_secret.sub("Bearer [REDACTED]", text)
            text = sk_secret.sub("[REDACTED]", text)
            text = assignment_secret.sub(lambda match: match.group(1) + "[REDACTED]", text)

            lines: list[str] = []
            for raw_line in text.splitlines():
                line = raw_line.rstrip()
                if not line and (not lines or not lines[-1]):
                    continue
                lines.append(line)
            while lines and not lines[-1]:
                lines.pop()
            return "\n".join(lines).strip()

        def _looks_absolute_path(value: Any) -> bool:
            raw = str(value or "").strip()
            if not raw:
                return False
            if windows_absolute.match(raw):
                return True
            try:
                return Path(raw).is_absolute()
            except (OSError, TypeError, ValueError):
                return raw.startswith("/")

        def _safe_value(value: Any, *, field: str = "") -> Any:
            folded_field = field.strip().casefold().replace("-", "_").replace(" ", "_")
            compact_field = re.sub(r"[^a-z0-9]", "", folded_field)
            if (
                folded_field in secret_keys
                or secret_field.search(folded_field)
                or compact_field in {
                    "apikey", "accesstoken", "refreshtoken", "authtoken",
                    "authorization", "clientsecret", "password", "passwd",
                    "secret", "secretkey",
                }
            ):
                return "[REDACTED]"
            if folded_field in path_keys and _looks_absolute_path(value):
                return "<project-local>"
            if isinstance(value, Mapping):
                out: dict[str, Any] = {}
                for raw_key, item in value.items():
                    key = str(raw_key)
                    if key.startswith("_"):
                        continue
                    out[key] = _safe_value(item, field=key)
                return out
            if isinstance(value, (set, frozenset)):
                return [
                    _safe_value(item, field=field)
                    for item in sorted(value, key=lambda item: str(item))
                ]
            if isinstance(value, (list, tuple)):
                return [_safe_value(item, field=field) for item in value]
            if isinstance(value, Path):
                return "<project-local>" if value.is_absolute() else _clean_text(value)
            if value is None or isinstance(value, (bool, int, float)):
                return value
            return _clean_text(value)

        def _json_text(value: Any) -> str:
            safe = _safe_value(value)
            if safe in (None, "", {}, []):
                return ""
            return _clean_text(json.dumps(
                safe,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            ))

        def _block(source: str, stable_id: Any, body: Any) -> str:
            content = _clean_text(body)
            if not content:
                return ""
            identifier = _clean_text(stable_id)[:240]
            heading = f"### {source}"
            if identifier:
                heading += f" · {identifier}"
            return f"{heading}\n{content}"

        def _rows(payload: Any, key: str = "results") -> list[Mapping[str, Any]]:
            if not isinstance(payload, Mapping):
                return []
            values = payload.get(key) or []
            if isinstance(values, Mapping):
                values = list(values.values())
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
                return []
            return [row for row in values if isinstance(row, Mapping)]

        def _stable_id(row: Mapping[str, Any], *keys: str) -> str:
            for key in keys:
                value = row.get(key)
                if value not in (None, "") and not isinstance(value, (Mapping, list, tuple, set)):
                    clean = _clean_text(value)
                    if clean:
                        return clean
            return ""

        # 1. Current project state and PROFILE.  Render the structured state directly;
        # the legacy Markdown snapshot contains obsolete search/read instructions.
        try:
            if self._memory is not None:
                self.ensure_internal_layout()
                state = self._memory.read_project_state(self.internal_workspace)
                if isinstance(state, Mapping):
                    state_text = _clean_text(state.get("state_text"))
                    recent: list[str] = []
                    seen_recent: set[str] = set()
                    persisted_recent = state.get("recent") or ()
                    if not isinstance(persisted_recent, Sequence) or isinstance(
                        persisted_recent, (str, bytes, bytearray)
                    ):
                        persisted_recent = ()
                    for value in (
                        *reversed(tuple(getattr(self, "_recent_project_activity", ()) or ())),
                        *persisted_recent,
                    ):
                        clean = _clean_text(value)
                        key = clean.casefold()
                        if clean and key not in seen_recent:
                            seen_recent.add(key)
                            recent.append(clean)
                    if state_text or recent:
                        details: list[str] = []
                        if state.get("updated_at"):
                            details.append(f"Updated: {_clean_text(state.get('updated_at'))}")
                        if state.get("turn_count") not in (None, ""):
                            details.append(f"Turn count: {state.get('turn_count')}")
                        if state_text:
                            details.extend(("", "Current state:", state_text))
                        if recent:
                            details.extend(("", "Recent activity:", *(f"- {item}" for item in recent)))
                        block = _block("Project State", self.project_id, "\n".join(details))
                        if block:
                            yield block
        except Exception:
            log.debug(
                 "[%s] Worker preload skipped Project State after a read failure",
                self.project_id,
                exc_info=True,
            )

        try:
            if self._assets is not None:
                profile = _clean_text(self._assets.profile_text(self.internal_workspace))
                block = _block("Project PROFILE", self.project_id, profile)
                if block:
                    yield block
        except Exception:
            log.debug(
                 "[%s] Worker preload skipped PROFILE after a read failure",
                self.project_id,
                exc_info=True,
            )

        # Project Memory: related records first, then a complete newest-first browse.
        seen_project_memory: set[str] = set()

        def _iter_project_memory_rows(search_query: str) -> Iterable[Mapping[str, Any]]:
            cursor: str | None = None
            seen_cursors: set[str] = set()
            while True:
                page = self.search_project_memory(
                    query=search_query,
                    limit=50,
                    order="newest",
                    cursor=cursor,
                )
                rows = _rows(page)
                for row in rows:
                    yield row
                if not isinstance(page, Mapping) or not bool(page.get("has_more")):
                    break
                next_cursor = _clean_text(page.get("next_cursor"))
                if not next_cursor or next_cursor in seen_cursors or not rows:
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor

        def _render_project_memory(row: Mapping[str, Any]) -> str:
            reference = _stable_id(row, "memory_id", "reference", "id", "turn")
            if not reference or reference in seen_project_memory:
                return ""
            seen_project_memory.add(reference)
            try:
                loaded = self.read_project_memory(reference)
            except Exception:
                log.debug(
                     "[%s] Worker preload skipped Project Memory item %s",
                    self.project_id,
                    reference,
                    exc_info=True,
                )
                return ""
            if not isinstance(loaded, Mapping) or loaded.get("found") is False:
                return ""
            record = loaded.get("record")
            if not isinstance(record, Mapping):
                return ""
            substantive = any(
                _clean_text(record.get(key))
                for key in ("summary", "input", "output", "actions", "artifacts", "tool_calls")
            )
            if not substantive:
                return ""
            return _block("Project Memory", reference, _json_text(record))

        if query:
            try:
                for row in _iter_project_memory_rows(query):
                    block = _render_project_memory(row)
                    if block:
                        yield block
            except Exception:
                log.debug(
                     "[%s] Worker preload skipped related Project Memory pass",
                    self.project_id,
                    exc_info=True,
                )

        # Current Agent Memory only.  Search results are already complete public rows;
        # recent fallback reuses the same parser without changing the public tool API.
        seen_agent_memory: set[str] = set()

        def _render_agent_memory(row: Mapping[str, Any]) -> str:
            markdown = _clean_text(row.get("markdown"))
            public = {
                str(key): value
                for key, value in row.items()
                if not str(key).startswith("_") and str(key) != "markdown"
            }
            identity_basis = markdown or _json_text(public)
            memory_id = _stable_id(row, "memory_id") or (
                "am-" + hashlib.sha1(identity_basis.encode("utf-8", errors="replace")).hexdigest()[:12]
                if identity_basis else ""
            )
            if not memory_id or memory_id in seen_agent_memory:
                return ""
            seen_agent_memory.add(memory_id)
            metadata = _json_text(public)
            body = "\n\n".join(part for part in (metadata, markdown) if part)
            return _block("Current Agent Memory", memory_id, body)

        if current_worker and current_worker != COORDINATOR:
            if query:
                try:
                    related = self.search_agent_memory(
                        current_worker,
                        query,
                        limit=50,
                        order="newest",
                    )
                    for row in _rows(related):
                        block = _render_agent_memory(row)
                        if block:
                            yield block
                except Exception:
                    log.debug(
                         "[%s] Worker preload skipped related Agent Memory for %s",
                        self.project_id,
                        current_worker,
                        exc_info=True,
                    )

        # Project Knowledge: expand related search hits, then browse/search and finally
        # the existing local graph to cover every visible node in a small project.
        seen_knowledge: set[str] = set()

        def _knowledge_reference(row: Mapping[str, Any], fallback: str = "") -> str:
            reference = _stable_id(
                row,
                "node_id", "reference", "ref", "id", "knowledge_id", "key",
            )
            if reference:
                return reference
            node = row.get("node")
            if isinstance(node, Mapping):
                reference = _stable_id(
                    node,
                    "node_id", "reference", "ref", "id", "knowledge_id", "key",
                )
                if reference:
                    return reference
            # For graph mappings, the mapping key is the canonical node identity and
            # must outrank a non-unique title/alias used only as a lookup fallback.
            reference = _clean_text(fallback)
            if reference:
                return reference
            reference = _stable_id(row, "title", "alias", "label", "name")
            if reference:
                return reference
            if isinstance(node, Mapping):
                return _stable_id(node, "title", "alias", "label", "name")
            return ""

        def _render_knowledge(reference: str) -> str:
            clean_ref = _clean_text(reference)
            if not clean_ref or clean_ref in seen_knowledge:
                return ""
            seen_knowledge.add(clean_ref)
            try:
                loaded = self.read_project_knowledge(clean_ref)
            except Exception:
                log.debug(
                     "[%s] Worker preload skipped Project Knowledge node %s",
                    self.project_id,
                    clean_ref,
                    exc_info=True,
                )
                return ""
            if not isinstance(loaded, Mapping) or loaded.get("found") is False:
                return ""
            detail = {
                str(key): value
                for key, value in loaded.items()
                if str(key) not in {"found", "message"}
            }
            meaningful = {
                key: value for key, value in detail.items()
                if key not in {"reference", "node_id", "id"} and value not in (None, "", [], {})
            }
            if not meaningful:
                return ""
            return _block("Project Knowledge", clean_ref, _json_text(detail))

        if query:
            try:
                related_knowledge = self.search_project_knowledge(
                    query,
                    limit=2000,
                    include_contested=True,
                )
                for row in _rows(related_knowledge):
                    block = _render_knowledge(_knowledge_reference(row))
                    if block:
                        yield block
            except Exception:
                log.debug(
                     "[%s] Worker preload skipped related Project Knowledge pass",
                    self.project_id,
                    exc_info=True,
                )

        # Knowledge Assets: task matches first, then the complete validated/core snapshot.
        seen_assets: set[str] = set()

        def _render_asset(asset_id: Any) -> str:
            clean_id = _clean_text(asset_id)
            if not clean_id or clean_id in seen_assets:
                return ""
            seen_assets.add(clean_id)
            try:
                loaded = self.read_knowledge_asset(clean_id)
            except Exception:
                log.debug(
                     "[%s] Worker preload skipped Knowledge Asset %s",
                    self.project_id,
                    clean_id,
                    exc_info=True,
                )
                return ""
            if not isinstance(loaded, Mapping) or loaded.get("found") is False:
                return ""
            asset = loaded.get("asset")
            if not isinstance(asset, Mapping):
                return ""
            if str(asset.get("status") or "").strip().casefold() not in {"validated", "core"}:
                return ""
            body_md = _clean_text(asset.get("body_md"))
            if not body_md:
                return ""
            metadata = {
                str(key): value for key, value in asset.items()
                if str(key) != "body_md" and not str(key).startswith("_")
            }
            body = "\n\n".join(part for part in (_json_text(metadata), body_md) if part)
            return _block("Knowledge Asset", clean_id, body)

        if self._assets is not None:
            try:
                for row in self._assets.match_for_task(
                    self.project_id,
                    self.internal_workspace,
                    query,
                    top=6,
                ) or ():
                    if not isinstance(row, Mapping):
                        continue
                    block = _render_asset(row.get("asset_id") or row.get("id"))
                    if block:
                        yield block
            except Exception:
                log.debug(
                     "[%s] Worker preload skipped related Knowledge Assets",
                    self.project_id,
                    exc_info=True,
                )

        # Skillpacks: rank active list metadata lexically, then yield remaining packs by
        # recency.  ``read_skillpack`` keeps the existing active-only hard gate.
        seen_skills: set[str] = set()

        def _lexical_terms(value: Any) -> set[str]:
            folded = _clean_text(value).casefold()
            terms = set(re.findall(r"[a-z0-9_][a-z0-9_.-]{1,}", folded))
            for run in re.findall(r"[\u3400-\u9fff]{2,}", folded):
                terms.add(run)
                for width in (2, 3):
                    if len(run) >= width:
                        terms.update(run[index:index + width] for index in range(len(run) - width + 1))
            return terms

        def _render_skill(pack_id: Any) -> str:
            clean_id = _clean_text(pack_id)
            if not clean_id or clean_id in seen_skills:
                return ""
            seen_skills.add(clean_id)
            try:
                loaded = self.read_skillpack(clean_id)
            except Exception:
                log.debug(
                     "[%s] Worker preload skipped Skillpack %s",
                    self.project_id,
                    clean_id,
                    exc_info=True,
                )
                return ""
            if not isinstance(loaded, Mapping) or loaded.get("found") is False:
                return ""
            pack = loaded.get("pack")
            if not isinstance(pack, Mapping):
                return ""
            if str(pack.get("status") or "").strip().casefold() != "active":
                return ""
            body_md = _clean_text(loaded.get("body_md"))
            if not body_md:
                return ""
            metadata = {
                str(key): value for key, value in pack.items()
                if not str(key).startswith("_")
            }
            body = "\n\n".join(part for part in (_json_text(metadata), body_md) if part)
            return _block("Active Skillpack", clean_id, body)

        recent_skills: list[tuple[str, str, Mapping[str, Any]]] = []
        if self._assets is not None:
            try:
                listed = self._assets.list_skillpacks(self.project_id, self.internal_workspace)
                packs_by_id: dict[str, Mapping[str, Any]] = {}
                if isinstance(listed, Mapping):
                    for group in ("system_builtin", "project_experience", "third_party"):
                        values = listed.get(group) or ()
                        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
                            continue
                        for row in values:
                            if not isinstance(row, Mapping):
                                continue
                            if str(row.get("status") or "").strip().casefold() != "active":
                                continue
                            pack_id = _stable_id(row, "pack_id", "id")
                            if pack_id and pack_id not in packs_by_id:
                                packs_by_id[pack_id] = row

                query_terms = _lexical_terms(query)
                related_skills: list[tuple[int, str, str, Mapping[str, Any]]] = []
                recent_skills: list[tuple[str, str, Mapping[str, Any]]] = []
                for pack_id, row in packs_by_id.items():
                    haystack = " ".join(
                        str(row.get(key) or "")
                        for key in (
                             "name", "description", "summary", "tags", "keywords",
                            "applies_to", "capabilities", "source", "kind", "source_kind",
                        )
                    )
                    score = len(query_terms & _lexical_terms(haystack)) if query_terms else 0
                    updated = str(row.get("updated_at") or row.get("created_at") or "")
                    if score:
                        related_skills.append((score, updated, pack_id, row))
                    recent_skills.append((updated, pack_id, row))

                related_skills.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
                for _, _, pack_id, _ in related_skills:
                    block = _render_skill(pack_id)
                    if block:
                        yield block

            except Exception:
                log.debug(
                     "[%s] Worker preload skipped Skillpacks",
                    self.project_id,
                    exc_info=True,
                )

        # Recent fallback passes follow every task-related source so unrelated history
        # cannot displace a hit from another source.
        try:
            for row in _iter_project_memory_rows(""):
                block = _render_project_memory(row)
                if block:
                    yield block
        except Exception:
            log.debug(
                 "[%s] Worker preload skipped recent Project Memory pass",
                self.project_id,
                exc_info=True,
            )

        if current_worker and current_worker != COORDINATOR:
            try:
                _, raw_rows = self._read_agent_worklog_records(current_worker)
                for raw_row in reversed(raw_rows):
                    if not isinstance(raw_row, dict):
                        continue
                    block = _render_agent_memory(_agent_memory_public_row(raw_row))
                    if block:
                        yield block
            except Exception:
                log.debug(
                     "[%s] Worker preload skipped recent Agent Memory for %s",
                    self.project_id,
                    current_worker,
                    exc_info=True,
                )

        try:
            browsed = self.search_project_knowledge(
                 "",
                limit=2000,
                include_contested=True,
            )
            for row in _rows(browsed):
                block = _render_knowledge(_knowledge_reference(row))
                if block:
                    yield block
        except Exception:
            log.debug(
                 "[%s] Worker preload could not browse Project Knowledge via search",
                self.project_id,
                exc_info=True,
            )

        try:
            if self._knowledge is not None:
                root = self._knowledge.knowledge_dir(self.internal_workspace)
                graph = self._knowledge.load_graph(self.project_id, root)
                nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
                graph_rows: list[tuple[str, Mapping[str, Any]]] = []
                if isinstance(nodes, Mapping):
                    for raw_id, node in nodes.items():
                        if isinstance(node, Mapping):
                            graph_rows.append((_clean_text(raw_id), node))
                elif isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes, bytearray)):
                    for node in nodes:
                        if isinstance(node, Mapping):
                            graph_rows.append((_knowledge_reference(node), node))

                def _knowledge_order(item: tuple[str, Mapping[str, Any]]) -> tuple[str, str]:
                    node_id, node = item
                    timestamp = ""
                    for key in (
                         "updated_at", "last_seen_at", "last_seen", "observed_at",
                        "created_at", "created", "timestamp",
                    ):
                        raw = node.get(key)
                        if raw not in (None, ""):
                            timestamp = str(raw)
                            break
                    return timestamp, node_id

                graph_rows.sort(key=_knowledge_order, reverse=True)
                for fallback, node in graph_rows:
                    block = _render_knowledge(_knowledge_reference(node, fallback))
                    if block:
                        yield block
        except Exception:
            log.debug(
                 "[%s] Worker preload skipped local Project Knowledge browse",
                self.project_id,
                exc_info=True,
            )

        if self._assets is not None:
            try:
                snapshot = self._assets.snapshot(
                    self.project_id,
                    self.internal_workspace,
                    limit=2000,
                )
                for row in _rows(snapshot, "assets"):
                    if str(row.get("status") or "").strip().casefold() not in {"validated", "core"}:
                        continue
                    block = _render_asset(row.get("asset_id") or row.get("id"))
                    if block:
                        yield block
            except Exception:
                log.debug(
                     "[%s] Worker preload skipped recent Knowledge Assets",
                    self.project_id,
                    exc_info=True,
                )

        recent_skills.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for _, pack_id, _ in recent_skills:
            block = _render_skill(pack_id)
            if block:
                yield block

        # Harness is deliberately last so a large global announcement cannot displace
        # task-related project data.  Known empty/disabled placeholders consume no context.
        try:
            harness = _clean_text(self.read_harness_memory())
            compact = " ".join(harness.split())
            if compact and not compact.startswith((
                msg("engine.231"), msg("engine.232"),
            )):
                block = _block("Harness Memory", "global", harness)
                if block:
                    yield block
        except Exception:
            log.debug(
                 "[%s] Worker preload skipped Harness Memory after a read failure",
                self.project_id,
                exc_info=True,
            )

    async def project_summary(self) -> dict[str, Any]:
        """供 server 汇总 Harness Memory：优先实时动态，再回落结构化 Project Memory。

        不再解析 context.md 标题/第一句。Markdown 是展示层，结构化 `.context.json` 才是
        状态真源；而刚发生的动作先由 `_recent_project_activity` 填平异步摘要的时间差。
        """
        try:
            proj = self.hub.projects.get(self.project_id)
            name = getattr(proj, "name", "") or self.project_id
        except Exception:
            name = self.project_id
        try:
            members = len(self.roster())
        except Exception:
            members = 0

        memory_recent: list[str] = []
        state_text = ""
        try:
            if self._memory is not None:
                self.ensure_internal_layout()
                state = self._memory.read_project_state(self.internal_workspace)
                memory_recent = [
                    str(x).strip() for x in (state.get("recent") or []) if str(x).strip()
                ]
                state_text = str(state.get("state_text") or "").strip()
        except Exception:
            log.exception("[%s] 读取结构化 Project Memory 失败（摘要回落实时动态）",
                          self.project_id)

        candidates = (
            list(reversed(self._recent_project_activity))
            + list(reversed(memory_recent))
            + ([state_text] if state_text and not state_text.startswith(msg("engine.233")) else [])
        )
        picked: list[str] = []
        seen: set[str] = set()
        for raw in candidates:
            line = " ".join(raw.split()).strip(" -")
            if not line or line in seen:
                continue
            seen.add(line)
            picked.append(line[:90] + ("…" if len(line) > 90 else ""))
            if len(picked) >= 3:
                break

        recent = "；".join(picked)
        if not recent:
            recent = msg("engine.234") if members == 0 else msg("engine.235")
        if len(recent) > 260:
            recent = recent[:260].rstrip() + "…"
        return {
            "project_id": self.project_id,
            "name": name,
            "members": members,
            "recent": recent,
        }
    # Structured action evidence used by approval and lifecycle handling.  These facts
    # never inspect or rewrite Coordinator prose.
    _GUARDED_ACTION_TOOLS: frozenset[str] = frozenset({
        "propose_agents", "propose_remove_agent", "propose_next",
    })

    def record_committed_action(self, tool_name: str) -> None:
        """
        记录本次项目经理回合中**已经审批通过且副作用完成**的受控动作。

        tools_knowe 只在 add/archive/instruction 注入成功后调用这里。这个集合每个 agent
        回合开始时清空，因此不会拿昨天的成功掩盖今天的口头承诺。
        """
        if tool_name in self._GUARDED_ACTION_TOOLS:
            self._committed_actions_this_turn.add(tool_name)

    def record_dispatch(self, target_id: str) -> None:
        """[v0.28] 这一轮真的把活派给了谁。propose_next 审批通过之后调。"""
        if target_id and target_id not in self._dispatched_this_turn:
            self._dispatched_this_turn.append(target_id)

    # ── agent 池 ──
    def _get_or_create_coordinator(self) -> KnoweAgent:
        a = self._agents.get(COORDINATOR)
        if a is None:
            a = self._new_agent(COORDINATOR, COORDINATOR_ROLE, CONFIG.coordinator_temperature)
            a._tool_registry = build_coordinator_registry(self)   # 项目经理的工具箱
            self._agents[COORDINATOR] = a
            self._persist_member(COORDINATOR, COORDINATOR_ROLE)   # [v0.8a A-1]
            log.info("[%s] 项目经理上线", self.project_id)
        return a

    def _get_or_create_worker(self, agent_id: str, role: str) -> KnoweAgent:
        """Create the Provider client holder consumed by ProviderModelAdapter."""
        agent = self._agents.get(agent_id)
        if agent is None:
            stored = self.stored_agent_info(agent_id)
            if stored and stored.get("status") == "deleted":
                raise ValueError(msg("engine.236", agent_id=agent_id))
            agent = self._new_agent(agent_id, role, CONFIG.worker_temperature)
            self._agents[agent_id] = agent
            self._roster[agent_id] = role
            self._names.setdefault(agent_id, self.reserve_name(agent_id, role))
            self._persist_member(agent_id, role)
            if not self._restoring:
                self._write_agent_profile(agent_id)
            log.info("[%s] Worker Provider %s（%s）就绪", self.project_id, agent_id, role)
        return agent

    def _persist_member(self, agent_id: str, role: str,
                        status: str = "active", name: str | None = None) -> None:
        """[v0.8a A-1] 一个人进队/离队 → 写进磁盘上的花名册。温载途中不回写。"""
        if self._restoring:
            return
        if self._store is None:
            log.warning("[%s] _persist_member 被调用但 _store 是 None —— 花名册不会落盘。请检查 server 创建引擎时是否传了 store。", self.project_id)
            return
        try:
            # [v1.0.24.4] 花名册落盘走持久化队列，不占主循环。
            # 单线程 FIFO：同一人「进队→归档→再进队」按提交顺序落盘，不会乱序覆盖。
            _store, _pid = self._store, self.project_id
            _name = name or self._names.get(agent_id)
            _store.defer_bg(
                lambda: _store.upsert_agent(_pid, agent_id, role, status=status, name=_name),
                description=f"花名册 upsert {agent_id}",
            )
        except Exception:            # 提交失败不许把引擎带走——人已经在内存的册上了
            log.exception("[%s] 花名册落盘提交失败：%s（%s，%s）",
                          self.project_id, agent_id, role, status)

    # ── [v0.9c → v0.9d] 名字 ──
    def stored_roster_full(self) -> dict[str, dict[str, str]]:
        """完整花名册快照（含归档）；供身份解析和项目经理上下文使用。

        正常模式下磁盘花名册是身份真源。没有 store（纯内存测试 / Fake 档）时，
        至少把当前活跃成员拼成同一形状，让调用方不必各写一套分支；这种模式没有
        可恢复的归档记录，因此只会返回活跃成员。
        """
        if self._store is None:
            return {
                aid: {
                    "role": role,
                    "name": self._names.get(aid) or legacy_display_name(aid, role),
                    "status": "active",
                }
                for aid, role in self._roster.items()
            }
        try:
            full = self._store.load_roster_full(self.project_id)
        except Exception:
            log.exception("[%s] 查完整花名册失败", self.project_id)
            return {}
        return {
            aid: dict(row)
            for aid, row in full.items()
            if isinstance(aid, str) and isinstance(row, dict)
        }

    def stored_agent_info(self, agent_id: str) -> dict[str, str] | None:
        """
        [v0.9d] 花名册里这个人的那一行（**含已归档的**）。没有 / 没 store → None。

        「归档的人被加回来，还是同一个名字」就靠它：
        `roster()` 里没有他（他被 pop 了），可磁盘上那一行还在，名字也还在。
        """
        return self.stored_roster_full().get(agent_id)

    def reserve_name(self, agent_id: str, role: str) -> str:
        """
        [v0.9d] 给这个人定一个名字（还没落盘，先记在内存里）。

        三级：
          ① 花名册里有（**含归档的**）→ 用持久旧名 —— 身份真源优先，他回来了还是他
          ② 内存里已经有 → 用它（同一个新成员提案的卡上和入队后必须是同一个名字）
          ③ 都没有 → 掷一个（中英各一半，同项目内不重名）

        ★ 掷出来只是「预定」，真正的落盘在 _persist_member → upsert_agent 那一刻。
          用户在审批卡上按了拒绝，这个名字就只是内存里一条没人认领的记录，无害；
          下次同一个 id 再被提议，还是它。
        """
        if agent_id == COORDINATOR:
            # [v1.0.22.1-对齐] 项目经理名随语言实时：模块级固化会在启动语言=英文时
            # 把「Coordinator」钉死，切回中文不更新。现取 msg() 让改名/切语言立即生效。
            return msg("engine.007")

        full = self.stored_roster_full()
        # 只有真实持久花名册里的行才算“旧身份”。纯内存模式的
        # stored_roster_full() 会为当前活跃成员拼一份兼容快照；新 Worker 在
        # _get_or_create_worker 里已先写进 _roster，不能把那条临时快照误当成
        # 持久旧成员，否则首次建队会从随机花名退化成「前端 1」一类占位名。
        stored = full.get(agent_id) if self._store is not None else None
        stored_name = (
            (stored or {}).get("name")
            if (stored or {}).get("status", "active") != "deleted"
            else None
        )
        if stored_name:
            # 磁盘身份真源覆盖内存里的临时预定名。这样即使先前一次花名册读取失败
            # 曾给同一 id 掷过临时名，恢复时也不会把旧成员换成新面孔。
            self._names[agent_id] = stored_name
            return stored_name

        cached = self._names.get(agent_id)
        if cached:
            return cached
        # 归档成员的名字也必须占位：否则新建一个不同 id 时，随机名池可能再次掷出
        # 「陆上初」，制造另一个看似同人的替身。内存缓存只是补充，完整花名册才是真源。
        taken = {
            str(row.get("name"))
            for aid, row in full.items()
            if aid != agent_id and isinstance(row, dict) and row.get("name")
        }
        taken.update(self._names.values())
        name = agent_name_for(
            agent_id, role,
            stored_name=None,
            taken=taken,
        )
        self._names[agent_id] = name
        return name

    def member_name(self, agent_id: str) -> str:
        """
        这个人叫什么。**只读**，绝不掷骰子——

        读的路径上掷随机名，就等于把「每次开 App 名字都变」原样请回来。
        内存里没有、盘上也没有（老项目、没落过盘）→ 退回稳定的老公式（「前端 1」）。
        """
        if agent_id == COORDINATOR:
            # [v1.0.22.1-对齐] 项目经理名现取（见 reserve_name 同款注释）：不读缓存、
            # 不读磁盘语言快照，切语言下一轮即生效。
            # [v1.0.23.3] ★ 特判必须在缓存检查之前：花名册恢复会把盘上的语言快照
            #   （如「项目经理」）写进 _names，命中缓存后英文模式照样输出中文——
            #   LLM 就会拿中文名自我介绍。现取 = 语言切换即时生效，旧快照永不过期。
            return msg("engine.007")
        cached = self._names.get(agent_id)
        if cached:
            return cached

        stored = self.stored_agent_info(agent_id)
        if stored and stored.get("name"):
            self._names[agent_id] = stored["name"]
            return stored["name"]
        return legacy_display_name(agent_id, self._roster.get(agent_id, ""))

    def rewrap_group_mention(self, content: str, agent_id: str) -> str:
        """[v1.0.19.5] 群聊 @Worker 直达：剥离 @提及并标注「用户在群里 @你」。

        此前 content 原样透传，worker 会把「@Fossil 你怎么说繁体字」理解成
        「翻译 Fossil 这个词」——@ 提及没有剥离，它不知道是在叫自己。
        这里把命中的 @别名剥掉，再包一层明确的主语/场景，让它知道：
        用户点名找它（用它的名字）说话，原文是什么。
        """
        from .mentions import _mentioned_aliases
        aliases = {
            a for a in (agent_id, self.member_name(agent_id), self._roster.get(agent_id, "")) if a
        }
        stripped = content
        for alias in _mentioned_aliases(content, aliases):
            stripped = re.sub(
                rf"@\s*{re.escape(alias)}(?![A-Za-z0-9_-])", "", stripped, flags=re.IGNORECASE
            )
        stripped = " ".join(stripped.split()).strip()
        name = self.member_name(agent_id) or agent_id
        if not stripped:
            stripped = msg("engine.237")
        return msg("engine.238", name=name, stripped=stripped)

    def _display_role(self, agent_id: str, stored_role: str = "") -> str:
        """
        [语言化] 对外 / LLM 上下文里的角色显示名，按当前语言实时翻译。

        花名册持久化的 role 是创建时的语言快照（可能是中文），
        所有要给人（前端 / 模型）看的出口都走这里，不把快照原文放出去：
          · coordinator  → msg("engine.007")（zh: 项目经理 / en: Leader）
          · 其他成员     → 前缀命中 ROLE_PROFILES → 本地化标签（zh: 前端 / en: Frontend）
          · 兜底         → msg("engine.106")（zh: 成员 / en: Member）
        """
        if agent_id == COORDINATOR:
            return msg("engine.007")
        role = (stored_role or "").strip() or self._roster.get(agent_id, "")
        if role:
            prof = roles.profile_for(role) or roles.profile_for_agent_id(agent_id)
            if prof is not None:
                return roles.localized_label(prof)
        return msg("engine.106")

    def member_info(self, agent_id: str, role: str = "") -> dict[str, str]:
        """给前端的一条成员记录：{id, role, name}。发事件的地方都问它要，别各拼各的。"""
        return {"id": agent_id, "role": self._display_role(agent_id, role), "name": self.member_name(agent_id)}

    # ═══════════════════════════════════════════════════════════
    # [v0.12 D · 问题一] 统一身份层：id ↔ 名字 ↔ 角色，单点真源
    #
    #   问题一的病根：一个 Agent 的身份（id/名字/角色）散在四个地方各读各的——
    #   花名册 JSONL、引擎内存 _names/_roster、announced_members.json、LLM 上下文。
    #   偏偏**最该拿到名字的模型上下文没拿到**：旧 Worker prompt 只注了 role 和 id，
    #   于是 Kit 在报告里坚称「我是 ux_1，不是 Kit」，反过来把项目经理也带偏了。
    #
    #   花名册（roster.jsonl）本来就是身份的持久真源（有 agent_id/role/name/status），
    #   engine._names/_roster 是它的内存缓存。缺的从来不是「存哪」，是「有没有把这三样
    #   拼成一个身份、原原本本注进模型上下文」。identity() 就是那个单点读取入口：
    #   任何要「这个人是谁」的地方都走它，绝不再各拼各的。
    # ═══════════════════════════════════════════════════════════
    def identity(self, agent_id: str) -> dict[str, str]:
        """
        这个 Agent 的规范身份：{id, name, role}。**唯一读取入口。**

        名字来自 member_name（内存缓存 → 花名册 → 稳定兜底，绝不现掷），
        角色来自内存花名册（项目经理是 COORDINATOR_ROLE）。
        """
        if agent_id == COORDINATOR:
            role = msg("engine.007")
        else:
            role = self._display_role(agent_id)
        return {"id": agent_id, "name": self.member_name(agent_id), "role": role}

    def _identity_block(self, agent_id: str) -> str:
        """
        [v0.12 D · 问题一] 注进 Agent 上下文最顶上的「你是谁」——三样绑死在一起。

        为什么要写得这么直白：模型不会自己把「名字 Kit」「角色 UI/UX」「id ux_1」
        联想成同一个人。必须明说：**别人叫你哪一个，指的都是你。** 这一句就是
        Kit 不再否认自己是 Kit 的关键。
        """
        idy = self.identity(agent_id)
        if feature_enabled(FeatureFlag.IDENTITY_CONTRACT_V1):
            contract = identity_for(
                agent_id,
                display_name=idy["name"],
                role_name=idy["role"],
            )
            return (
                contract.system_block()
                + "\n"
                + msg("engine.identity.same_person", name=idy['name'], role=idy['role'], id=idy['id'])
                + msg("engine.239")
                + roles.identity_block(agent_id, idy["role"])
            )
        return (
             msg("engine.240")
            + msg("engine.identity.your_name", name=idy['name'])
            + msg("engine.identity.your_role", role=idy['role'])
            + msg("engine.identity.your_id", id=idy['id'])
            + msg("engine.identity.refers_you", name=idy['name'], role=idy['role'], id=idy['id'])
            + msg("engine.identity.dont_deny", name=idy['name'], id=idy['id'], role=idy['role'])
            + msg("engine.241")
            # [v0.22 问题四] 光有一个角色**标签**，模型不知道该怎么当这个角色。
            #   给它这个角色的看家本领和边界，它才会按这一行的方式想问题——
            #   而不是「技术写作」四个字配上一套通用做法。
            + roles.identity_block(agent_id, idy["role"])
        )

    # ═══════════════════════════════════════════════════════════
    # [v0.9b → v0.45] 成员生命周期：加人 / 归档 / 彻底删除
    #
    # 三件事**必须一起发生**，少一件就会长出一个「半个人」：
    #   内存（_roster / _agents） · 磁盘（花名册 status） · 账（agent_resources.md）
    # v0.6 就漏过一次（人建了、册上没有），所以这两个方法是唯一的入口。
    #
    # 归档 = 不再接活，但**历史全留**：
    #   他交过的 report、写过的产出、instruction、审批记录，一个字都不动——那是用户的资产。
    # 彻底删除只从联系人功能进入：执行器、在飞任务、Profile/Agent Memory 与身份引用一并清理；
    # 上一级 Project Memory 的“已被删除”墓碑由 server/memory_manager 在事务中保留。
    # ═══════════════════════════════════════════════════════════
    def archive_worker(self, agent_id: str, reason: str = "") -> bool:
        """Archive a Worker and cancel any queued/running Runtime task."""
        if agent_id == COORDINATOR:
            return False
        if agent_id not in self._roster:
            return False
        role = self._roster.pop(agent_id)
        self._agents.pop(agent_id, None)
        self._close_task_envelope(agent_id)
        task = self._worker_turns.pop(agent_id, None)
        if task is not None and not task.done():
            task.cancel()
        direct = self._direct_turns.pop(agent_id, None)
        if direct is not None and not direct.done():
            direct.cancel()
        self._worker_step.pop(agent_id, None)
        self._worker_keyword.pop(agent_id, None)
        self._worker_runtime_runs.pop(agent_id, None)
        self._dm_busy.discard(agent_id)
        self._mention_busy.discard(agent_id)
        self._purge_open_activity(agent_id)
        self._persist_member(agent_id, role, status="removed")
        self.log_agent_resource(
            msg("engine.243"),
            agent_id,
            role,
            (reason.strip() or msg("engine.244")) + msg("engine.245"),
            name=self.member_name(agent_id),
        )
        return True

    async def delete_agent_permanently(self, agent_id: str) -> dict[str, Any]:
        """Remove one identity, Runtime task, Provider holder, and Worker profile."""
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError(msg("engine.246"))
        agent_id = agent_id.strip()
        stored = self.stored_agent_info(agent_id) or {}
        is_coordinator = agent_id == COORDINATOR
        role = msg("engine.007") if is_coordinator else (
            self._roster.get(agent_id) or str(stored.get("role") or msg("engine.106"))
        )
        known = is_coordinator or agent_id in self._roster or agent_id in self._agents or bool(stored)
        if not known or stored.get("status") == "deleted":
            raise KeyError(msg("engine.248", agent_id=agent_id))
        name = str(stored.get("name") or self.member_name(agent_id) or role).strip() or role

        self._close_task_envelope(agent_id)
        tasks: list[asyncio.Task[Any]] = []
        for task in (
            self._worker_turns.pop(agent_id, None),
            self._direct_turns.pop(agent_id, None),
        ):
            if task is not None and task not in tasks:
                tasks.append(task)
        for task in tasks:
            self._dm_tasks.discard(task)
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._roster.pop(agent_id, None)
        agent = self._agents.pop(agent_id, None)
        if agent is not None:
            closer = getattr(agent, "aclose", None) or getattr(agent, "close", None)
            if callable(closer):
                try:
                    result = closer()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    log.debug("[%s] 删除成员时关闭 Provider 失败", self.project_id, exc_info=True)
        for mapping in (
            self._worker_step,
            self._worker_keyword,
            self._worker_runtime_runs,
            self._task_envelopes,
            self._stop_reasons,
            self._dm_pending,
            self._pending_member_activity,
            self._names,
        ):
            mapping.pop(agent_id, None)
        self._purge_agent_scoped_state(agent_id)
        for collection in (self._workers_with_open_activity, self._dm_busy, self._mention_busy):
            collection.discard(agent_id)
        self._purge_open_activity(agent_id)
        self._dispatched_this_turn = [target for target in self._dispatched_this_turn if target != agent_id]

        if not is_coordinator:
            profile = self.agents_dir() / _safe(agent_id)
            if profile.is_symlink():
                profile.unlink(missing_ok=True)
            elif profile.exists():
                shutil.rmtree(profile)
        return {"id": agent_id, "name": name, "role": role, "coordinator": is_coordinator}

    # ═══════════════════════════════════════════════════════════
    # [v0.12 D · 问题六 6b] Agent 层：每个成员一份 Profile
    #
    #   用户问：「我拉了这么多 agent，他们自己的 profile 文件夹在哪？」
    #   v0.16 起位于 internal_workspace/agents/{id}/，不再进入用户业务目录。
    #     · IDENTITY.md   身份（名字/角色/id/加入时间）—— 系统维护，和问题一同一个真源
    #     · SOUL.md       人设（指向共享 worker 人设 + 这个人的角色说明）
    #     · memory/       预留：这个成员自己的记忆
    #     · skills/       预留：这个成员的技能
    #   系统写、成员通过 prompt 读取；通用文件工具完全看不到内部工作区。
    #   写不进去绝不许把引擎带走——档案是给人看的，不是流程的一环。
    # ═══════════════════════════════════════════════════════════
    def agents_dir(self) -> Path:
        self.ensure_internal_layout()
        return self.internal_workspace / "agents"

    def _write_agent_profile(self, agent_id: str) -> None:
        """给一个成员建/刷新 Profile 目录。幂等：IDENTITY.md 每次按最新身份覆盖，
        SOUL.md/占位目录只在缺失时建。异常吞掉。"""
        if agent_id == COORDINATOR:
            return
        try:
            idy = self.identity(agent_id)
            base = self.agents_dir() / _safe(agent_id)
            (base / "memory").mkdir(parents=True, exist_ok=True)
            (base / "skills").mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            identity_md = (
                f"---\nid: {idy['id']}\nname: {idy['name']}\nrole: {idy['role']}\n"
                + f"updated: {ts}\n---\n\n"
                + msg("engine.identity.md_title", name=idy['name'], role=idy['role'])
                + msg("engine.identity.summary_name_role", name=idy['name'], role=idy['role'])
                + msg("engine.identity.summary_id", id=idy['id'])
                + msg("engine.identity.summary_same", name=idy['name'])
                + msg("engine.249")
                + msg("engine.250")
            )
            _atomic_text(base / "IDENTITY.md", identity_md)

            soul_path = base / "SOUL.md"
            if not soul_path.exists():
                _atomic_text(soul_path, (
                    msg("engine.identity.profile_title", name=idy['name'])
                    + msg("engine.identity.profile_role", role=idy['role'])
                    + msg("engine.251")
                ))
        except Exception:
            log.warning("[%s] 写 Agent Profile 失败：%s", self.project_id, agent_id)

    def ensure_agent_profile(self, agent_id: str) -> None:
        """
        [v0.13 模块B] 确保这个成员的 `internal_workspace/agents/{id}/` 存在——不在就重建。

        内部目录迁移 / 意外丢失后自愈：`_write_agent_profile` 是幂等且安全的
        （IDENTITY.md 按最新身份覆盖，SOUL.md / memory/ / skills/ 只在缺失时建，绝不覆盖用户改动），
        所以「缺了才补」这件事随便调，代价只是一次 `exists()`。
        """
        if agent_id == COORDINATOR:
            return
        try:
            base = self.agents_dir() / _safe(agent_id)
            if base.exists() and (base / "IDENTITY.md").exists():
                return                       # 已在 → 跳过，省一趟 IO
        except Exception:
            pass                             # 探测都失败，那就往下走，让重建再兜一次
        self._write_agent_profile(agent_id)

    def ensure_all_agent_profiles(self) -> None:
        """
        [v0.13 模块B] 对当前花名册里每个成员做一次 Profile 自愈。

        温载后必须调它：`restore_roster` 走的是 `_restoring=True` 的路径，
        `_get_or_create_worker` 里那句建 Profile 被守卫跳过了——本来是「重启时 agents 已在盘上、
        省 IO」的合理优化；v0.16 后业务目录恢复不再影响内部 Profile。
        这里在温载收尾时补建；业务目录恢复不再搬动或重建内部 Profile。

        只在 Harness 档（有 Worker 池）跑：单 agent 档（Fake / 知知）没有 `.agents` 这层。
        """
        if self.agent is not None:
            return
        for aid in list(self._roster.keys()):
            self.ensure_agent_profile(aid)

    # ═══════════════════════════════════════════════════════════
    # [v0.13 模块B] Agent Memory —— 三层记忆的第三层，终于落地
    #   Harness Memory（全局）→ Project Memory（internal memory/）→ Agent Memory（这里）
    #   每交一次差，把关键事实追加进 internal agents/{id}/memory/：
    #     · worklog.md   —— 人读的个人工作日志（追加）
    #     · state.json   —— 机器读的累计快照（交差次数 / 最近一次 / 产出清单）
    #   **不做 LLM 摘要**（那是 Project Memory 的活）——结构化关键事实就够了。写不进去绝不炸。
    # ═══════════════════════════════════════════════════════════
    def _ensure_agent_worklog(self, agent_id: str) -> tuple[Path, dict[str, str]]:
        """确保个人工作日志存在，并返回路径与当前身份快照。"""
        base = self.agents_dir() / _safe(agent_id)
        mem = base / "memory"
        mem.mkdir(parents=True, exist_ok=True)
        idy = self.identity(agent_id)
        log_path = mem / "worklog.md"

        if not log_path.exists():
            log_path.write_text(
                msg("engine.identity.worklog_title", name=idy['name'], role=idy['role'])
                + msg("engine.252")
                + msg("engine.253"),
                encoding="utf-8",
            )
        return log_path, idy

    def _write_agent_memory(
        self, agent_id: str, *, step: int, keyword: str, status: str,
        completed_what: str, artifacts: list[str], matches_instruction: str,
        issues: str, self_check: str, instruction_ref: str, report_file: str,
        provenance: Mapping[str, Any] | None = None,
        task_id: str = "", run_id: str = "", delivery_id: str = "",
    ) -> None:
        if agent_id == COORDINATOR:
            return
        try:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            prov = normalize_provenance(
                provenance if provenance is not None else current_provenance_dict()
            ).to_dict()
            log_path, idy = self._ensure_agent_worklog(agent_id)
            mem = log_path.parent
            arts = [str(a) for a in (artifacts or []) if str(a).strip()]

            # ① 人读日志（追加）
            lines = [msg("engine.254", ts=ts, step=f"{step:02d}",
                                  keyword=keyword or msg("engine.254.fb"), status=status)]
            lines.append(msg("engine.255", instruction_ref=instruction_ref or msg("engine.255.fb")))
            done = (completed_what or "").strip()
            if done:
                lines.append(msg("engine.256", done=done))
            if arts:
                lines.append(msg("engine.257"))
            if matches_instruction.strip():
                lines.append(msg("engine.258"))
            if issues.strip():
                lines.append(msg("engine.259"))
            if self_check.strip():
                lines.append(msg("engine.260"))
            lines.append(msg("engine.261", report_file=report_file))
            lines.append(
                 msg("engine.262")
                + f"{prov['status']} | build={prov['build_id'] or 'unknown'} | "
                + f"git={prov['git_commit'] or 'unknown'} | "
                + f"runtime_schema={prov['runtime_schema_version'] or 'unknown'} | "
                + f"harness_schema={prov['harness_schema_version'] or 'unknown'} | "
                + f"prompt_bundle={prov['prompt_bundle_version'] or 'unknown'} | "
                + f"task={task_id or '-'} | run={run_id or '-'} | delivery={delivery_id or '-'}\n"
            )
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

            # ② 机器读快照（累计）
            state_path = mem / "state.json"
            state: dict[str, Any] = {}
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text("utf-8"))
                    if not isinstance(state, dict):
                        state = {}
                except (OSError, ValueError):
                    state = {}
            state["schema_version"] = 2
            state["id"] = idy["id"]
            state["name"] = idy["name"]
            state["role"] = idy["role"]
            state["reports_submitted"] = int(state.get("reports_submitted", 0)) + 1
            state["last_updated"] = ts
            state["last_task"] = {
                "step": step, "keyword": keyword, "status": status,
                "instruction": instruction_ref, "report": report_file,
                "completed_what": done, "artifacts": arts,
                "task_id": task_id, "run_id": run_id, "delivery_id": delivery_id,
                "provenance": prov,
            }
            state["provenance"] = prov
            all_arts = list(state.get("all_artifacts", []))
            for a in arts:
                if a not in all_arts:
                    all_arts.append(a)
            state["all_artifacts"] = all_arts[-100:]        # 别无限长
            _atomic_text(state_path, json.dumps(state, ensure_ascii=False, indent=2))
        except Exception:
            log.warning("[%s] 写 Agent Memory 失败：%s", self.project_id, agent_id)


    def _read_agent_worklog_records(
        self, agent_id: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """只读个人 worklog；文件缺失、为空或损坏都按没有记录处理。"""
        if agent_id == COORDINATOR:
            return "", []
        try:
            log_path = self.agents_dir() / _safe(agent_id) / "memory" / "worklog.md"
            if not log_path.is_file():
                return "", []
            text = log_path.read_text("utf-8", errors="replace").strip()
            if not text:
                return "", []
            return text, _parse_agent_worklog(text)
        except Exception:
            return "", []

    def _agent_memory_has_deep_history(self, agent_id: str) -> bool:
        """是否存在完全落在尾部 1200 字符之前、值得静默检索的完整日志段。"""
        text, rows = self._read_agent_worklog_records(agent_id)
        if len(text) <= _AGENT_MEMORY_TAIL_CHARS:
            return False
        cutoff = len(text) - _AGENT_MEMORY_TAIL_CHARS
        return any(int(row.get("_end") or 0) <= cutoff for row in rows)

    def _agent_memory_clues_block(
        self,
        agent_id: str,
        keywords: list[str] | tuple[str, ...],
        *,
        project_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    ) -> str:
        """用共享关键词从当前 Worker 的深层 worklog 生成最多两条个人线索。"""
        if agent_id == COORDINATOR:
            return ""
        clean_keywords: list[str] = []
        seen_keywords: set[str] = set()
        for value in keywords or ():
            clean = " ".join(str(value or "").split()).strip()
            key = clean.casefold()
            if not clean or key in seen_keywords:
                continue
            seen_keywords.add(key)
            clean_keywords.append(clean)
        if not clean_keywords:
            return ""
        try:
            text, rows = self._read_agent_worklog_records(agent_id)
            if len(text) <= _AGENT_MEMORY_TAIL_CHARS or not rows:
                return ""
            cutoff = len(text) - _AGENT_MEMORY_TAIL_CHARS
            deep_rows = [
                row for row in rows
                if int(row.get("_end") or 0) <= cutoff
            ]
            if not deep_rows:
                return ""

            candidates: dict[str, dict[str, Any]] = {}
            for keyword in clean_keywords:
                ranked: list[tuple[float, int, dict[str, Any]]] = []
                for row in deep_rows:
                    score = _agent_memory_match_score(row, keyword)
                    if score is None:
                        continue
                    ranked.append((score, int(row.get("_ordinal") or 0), row))
                ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
                for rank, (score, _, raw_row) in enumerate(
                    ranked[:_AGENT_MEMORY_CLUE_SEARCH_LIMIT]
                ):
                    memory_id = str(raw_row.get("memory_id") or "")
                    if not memory_id:
                        continue
                    row = candidates.get(memory_id)
                    if row is None:
                        row = dict(raw_row)
                        row["_keywords"] = set()
                        row["_rank_score"] = 0.0
                        row["_match_score"] = 0.0
                        candidates[memory_id] = row
                    row["_keywords"].add(keyword.casefold())
                    row["_rank_score"] += 1.0 / (rank + 1)
                    row["_match_score"] += score

            prepared: list[dict[str, Any]] = []
            for row in candidates.values():
                if not (str(row.get("completed_what") or "").strip()
                        or list(row.get("artifacts") or [])):
                    continue
                row["_score"] = (
                    12.0 * len(row.get("_keywords") or ())
                    + float(row.get("_rank_score") or 0.0)
                    + float(row.get("_match_score") or 0.0)
                )
                row["_topic_tokens"] = _memory_topic_tokens(" ".join([
                    str(row.get("keyword") or ""),
                    str(row.get("completed_what") or ""),
                    " ".join(str(item) for item in row.get("artifacts") or ()),
                ]))
                prepared.append(row)
            if not prepared:
                return ""
            selected = _select_agent_memory_clues(
                prepared, project_rows, _AGENT_MEMORY_CLUE_LIMIT,
            )
            return _render_agent_memory_clues(selected)
        except Exception:
            # 个人预检索是增强层：文件/格式异常不能污染主回合，也不留空标题。
            log.debug(
                 "[%s] Agent Memory 静默预检索失败，本轮跳过：%s",
                self.project_id, agent_id, exc_info=True,
            )
            return ""

    def _agent_memory_block(self, agent_id: str) -> str:
        """
        [v0.13 模块B] 把这个 Agent 自己的工作记忆（worklog.md 的尾部）注进它这一轮的 prompt——
        让它记得自己在这个项目干过什么、做过哪些决定，而不是每回合都失忆。

        只注尾部若干字符（有上限，不撑爆上下文）。**明说这是私人记忆、别向用户复述**——
        和模块C 一致：报告/交接这类内部字眼绝不端给用户看。空则不注。
        """
        if agent_id == COORDINATOR:
            return ""
        try:
            log_path = self.agents_dir() / _safe(agent_id) / "memory" / "worklog.md"
            if not log_path.exists():
                return ""
            text = log_path.read_text("utf-8", errors="replace").strip()
        except Exception:
            return ""
        if not text:
            return ""
        clip = _AGENT_MEMORY_TAIL_CHARS
        if len(text) > clip:
            text = msg("engine.263") + text[-clip:]
        return (
             msg("engine.ctx.work_memory")
            + msg("engine.264") + text
        )
    def log_agent_resource(self, action: str, agent_id: str,
                           role: str = "", detail: str = "",
                           name: str = "") -> None:
        """
        往 `handoffs/agent_resources.md` 追加一条成员变更日志。

        为什么不写 `.approval-NN.md`：增删成员**不是一个交接步骤**——
        它不产出报告、也没有下一步。把它塞进按序号排的交接流水里，
        会把「谁在什么时候干了什么」这条线搅乱。它自己有一本账。

        写不进去也不许炸：这本账是给人看的，不是流程的一环。
        """
        try:
            path = self.handoff_dir / "agent_resources.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            if not path.exists():
                name = self.hub.projects.get(self.project_id)
                pname = getattr(name, "name", None) or self.project_id
                path.write_text(
                    f"---\nproject: {pname}\nupdated: {ts}\n---\n\n"
                    + msg("engine.265")
                    + msg("engine.266")
                    + msg("engine.267"),
                     "utf-8",
                )

            # [v0.9c] 日志里写**名字**——三个月后回头看，「前端 1」比 fe_1 好认得多
            label = name or self.member_name(agent_id)
            who = f"{label}（{agent_id}·{role}）" if role else f"{label}（{agent_id}）"
            entry = f"## {ts} · {action}\n- {who} — {detail or action}\n\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry)
        except (OSError, AttributeError) as exc:
            log.warning("[%s] 成员变更日志写不下去：%s", self.project_id, exc)

    # ═══════════════════════════════════════════════════════════
    # [v0.8a A-1] 温载：把磁盘上的队伍重新建起来
    # ═══════════════════════════════════════════════════════════
    def restore_roster(self, roster: dict[str, Any]) -> None:
        """
        server 在引擎起来之后调它，把上次那支队伍重新建出来。

        重建的不只是「名字和角色」这两个字符串——是**真的把 KnoweAgent 实例造出来、
        把工具箱重新绑上**。不然重启之后项目经理一 propose_next，会发现 target 不在册里，
        或者册里有人、却没有实例可以派活。

        `_restoring` 期间不回写磁盘：这些人本来就是从磁盘上读出来的，
        再写一遍只会让文件白白长一截（upsert_agent 那边虽然也幂等，但没必要跑一趟 IO）。
        """
        if not roster:
            return

        # [v0.9c] server 可能给两种形状：
        #     {id: role}                       —— 老的（load_roster）
        #     {id: {"role","name","status"}}   —— 新的（load_roster_full）
        #   两种都认。名字从盘上读回来 —— **这就是「重启后名字不变」的那一环**。
        flat: dict[str, str] = {}
        for aid, val in roster.items():
            if isinstance(val, dict):
                flat[aid] = str(val.get("role") or msg("engine.106"))
                nm = val.get("name")
                if isinstance(nm, str) and nm:
                    self._names[aid] = nm
            else:
                flat[aid] = str(val)
            # ★ [v0.9d] 盘上没名字 → **不在这里掷**（温载是读的路径）。
            #   真正的补名发生在 server._restore_roster：它会掷一个并**立刻落盘**，
            #   所以下一次温载就有名字了。这里只兜一个稳定的老名字，防止显示空白。
            self._names.setdefault(aid, legacy_display_name(aid, flat[aid]))

        self._restoring = True
        try:
            for agent_id, role in flat.items():
                if agent_id == COORDINATOR:
                    if self.agent is None:              # Harness 档才有 agent 池
                        self._get_or_create_coordinator()
                    continue
                if self.agent is not None:
                    # 单 agent 档（Fake / 知知）：没有 Worker 池，建实例是死重量。
                    # 但册要回来 —— 花名册是「这个群里有谁」，跟哪个档跑没关系。
                    self._roster[agent_id] = role
                else:
                    self._get_or_create_worker(agent_id, role)
        finally:
            self._restoring = False

        # [v0.13 模块B] 温载收尾 → 确保每个成员的 .agents/{id}/ 在当前 workspace 下就位。
        #   目录恢复换了新 workspace，这一步把身份文件（IDENTITY/SOUL/memory/skills）在新目录下重建；
        #   普通重启时 .agents 已在盘上，ensure_* 只做一次 exists() 就返回，不白花 IO。
        self.ensure_all_agent_profiles()

        log.info("[%s] 温载花名册 %d 人：%s", self.project_id, len(self._roster),
                 "、".join(f"{a}（{r}）" for a, r in self._roster.items()) or msg("engine.268"))

    @staticmethod
    def _core_base_url(base: str) -> str:
        """
        [v0.44.3 场景2 修复] 目录口径 → knowe_core 口径的**唯一**换算点（片段保护）。

        全链路曾有两套 base_url 约定在打架：
          · **目录口径**（modelCatalog / 连接测试 / aux_client / 知知）：base 是「端点前缀」
            （自带版本段，如 …/v1、…/api/paas/v4、…/v1beta/openai），请求层只追加
            `/chat/completions`。这是 OpenAI SDK / LiteLLM 的行业标准约定，全厂商通用。
          · **knowe_core 口径**（ProviderClient）：请求层曾自补 `/v1/chat/completions` 一段
            ——证据就是项目经理 404 回显的 `/v4/v1/chat/completions`（目录的 …/v4 又被追加了
            /v1/chat/completions）。老的硬编码默认 `https://api.deepseek.com`（主机根）恰好
            也吃这套口径，`.../v1/chat/completions` 能通，所以 v0.44 接入目录厂商前没暴露。

        两处一起修，belt-and-suspenders：
          ① **根源**：ProviderClient 不再注入 /v1，只追 `/chat/completions`、尊重 base 版本段
             （见 knowe_core/provider_client）——直接调 core 的路径也就对了。
          ② **交接缝**（这里 → runtime_settings.core_base_url）：把 base 换算成
             `{目录 base}/chat/completions#`。core 往后无论追什么都落进 URL fragment，HTTP
             层不发 fragment（RFC 3986 §3.5），实际命中 `{目录 base}/chat/completions`——与
             知知/aux/目录同址。即便 core 侧回退到旧拼接口径，Agent 也照样打对地址。

        于是曾经「core 口径下无可表达 base」的厂商（Z.AI …/paas/v4、Gemini …/v1beta/openai、
        Copilot 主机根）现在都能正确接入；以 /v1 结尾的老厂商（OpenAI、OpenRouter、xAI、
        Kimi、DashScope 等）命中的仍是 `{base}/chat/completions`，与修复前**一字不差**。
        连接测试（runtime_settings.test_binding）也改探这条真实地址，不再「测试绿、聊天 404」。

        实现只有一份，在 runtime_settings.core_base_url（连接测试也用它）；这里做委托。
        """
        return runtime_settings.core_base_url(base)

    def _effective_provider_binding(self, agent_id: str) -> dict[str, str]:
        """
        返回 ``_new_agent`` 这一刻真正会使用的完整绑定。

        设置面板绑定齐全时按「Agent 个性化 > 全局主模型」；否则仅为兼容老部署，
        回落到显式配置的 ``DEEPSEEK_*``。provider 也必须跟着返回——它不仅用于展示，
        还是冻结 client 的一部分；否则 URL 已切到 Z.AI，错误层仍会保留旧厂商身份。
        """
        binding = runtime_settings.model_binding_for(self.project_id, agent_id)
        # 显式设置一旦存在，就是权威；字段不完整也原样返回，让上层报当前 provider 的
        # 配置问题。只有“从未配置过”才允许兼容老部署的 DEEPSEEK_*。
        if binding is not None:
            return dict(binding)
        return {
            "provider": "deepseek" if (CONFIG.deepseek_api_key or CONFIG.deepseek_base_url) else "",
            "model": CONFIG.deepseek_model,
            "api_key": CONFIG.deepseek_api_key,
            "base_url": CONFIG.deepseek_base_url,
            "transport": "openai_chat",
        }

    async def _aux_translate_error(self, raw: str) -> str:
        """把机器报错译成一句友好中文（DESIGN §三#8）。best-effort、短超时、
        失败/超时/无 aux 一律兜底原文，绝不卡住消息流。与 server 侧附件打回同款逻辑。"""
        text = (raw or "").strip()
        if not text:
            return msg("engine.269")
        try:
            aux = runtime_settings.aux_effective()
            aux_ok = bool(aux and aux.get("api_key") and aux.get("base_url"))
            if not aux_ok and not CONFIG.deepseek_api_key:
                return text
            out = await asyncio.wait_for(
                aux_client.chat(
                    [
                        {"role": "system", "content": (
                             msg("engine.270")
                            + msg("engine.271")
                        )},
                        {"role": "user", "content": text},
                    ],
                    api_key=aux["api_key"] if aux_ok else CONFIG.deepseek_api_key,
                    base_url=aux["base_url"] if aux_ok else CONFIG.deepseek_base_url,
                    model=aux["model"] if aux_ok else CONFIG.deepseek_model,
                    timeout_s=8.0, what=msg("engine.272"),
                ),
                timeout=9.0,
            )
            out = (out or "").strip()
            return out or text
        except Exception:
            return text

    @staticmethod
    def _provider_binding_used_by(agent: Any) -> dict[str, str]:
        """
        返回**产生本次结果的实例**实际冻结的 provider/model/base。

        设置可能在一个请求途中被热切换；这时 runtime_settings 已经是新厂商，但正在收尾的
        旧实例仍可能返回旧厂商错误。错误归因必须跟随发出请求的实例，而不是读取“此刻最新”
        的设置，否则会把旧请求的失败反标成新厂商。ProviderConfig 不含任何可变引用，可安全
        作为这次请求的事实快照；拿不到时再从 `_bound_sig` 兼容恢复。
        """
        cfg = getattr(agent, "_provider_cfg", None)
        if cfg is not None:
            return {
                "provider": str(getattr(cfg, "provider", "") or ""),
                "model": str(getattr(cfg, "model", "") or ""),
                "base_url": str(getattr(cfg, "base_url", "") or ""),
            }

        sig = getattr(agent, "_bound_sig", ())
        if isinstance(sig, tuple) and len(sig) >= 4:
            return {
                "provider": str(sig[0] or ""),
                "base_url": str(sig[1] or ""),
                "model": str(sig[2] or ""),
            }
        return {}

    def _new_agent(self, agent_id: str, role: str, temperature: float) -> KnoweAgent:
        """Create a Coordinator loop adapter or a Worker Provider holder."""
        binding = self._effective_provider_binding(agent_id)
        pc = ProviderConfig(
            provider=binding["provider"],
            model=binding["model"],
            api_key=binding["api_key"],
            base_url=self._core_base_url(binding["base_url"]),
            temperature=temperature,
            max_retries=CONFIG.provider_max_retries,
            connect_timeout_s=CONFIG.provider_connect_timeout_s,
            read_timeout_s=CONFIG.provider_read_timeout_s,
            write_timeout_s=CONFIG.provider_write_timeout_s,
            pool_timeout_s=CONFIG.provider_pool_timeout_s,
        )
        agent = KnoweAgent(
            agent_id=agent_id,
            role=role,
            provider_config=pc,
            client_factory=self._client_factory,
        )
        agent._bound_sig = (pc.provider, pc.base_url, pc.model, pc.api_key)
        if agent_id == COORDINATOR:
            agent.stream_delta_callback = lambda text: self._fire({
                "type": "stream_delta", "agent_id": agent_id, "content": text,
            })
            # [v1.0.23.3] 推理增量实时广播（reasoning_content 透传）
            agent.reasoning_delta_callback = lambda text: self._fire({
                "type": "reasoning_delta", "agent_id": agent_id, "content": text,
            })
            agent.tool_gen_callback = lambda tool_name: self._fire({
                "type": "tool_gen", "agent_id": agent_id, "tool_name": tool_name,
                **tool_ledger.stage_payload(tool_name),
            })
            agent.tool_start_callback = lambda: self._fire({
                "type": "tool_start", "agent_id": agent_id,
            })
            def complete() -> None:
                self._fire({"type": "tool_complete", "agent_id": agent_id})
            agent.tool_complete_callback = complete
        return agent

    def _provider_sig_for(self, agent_id: str) -> tuple[str, str, str, str]:
        """
        `_new_agent` **实际会用**的 ``(provider, base_url, model, api_key)``。

        provider 也属于冻结 client 的签名：即便两个目录条目碰巧共享同一端点/模型/Key，
        切换厂商后也要重建实例，保证日志与错误身份同步更新。
        """
        binding = self._effective_provider_binding(agent_id)
        return (
            binding["provider"],
            self._core_base_url(binding["base_url"]),
            binding["model"],
            binding["api_key"],
        )

    def apply_model_settings(self) -> int:
        """
        [v0.44 设置 §2.2/§3.2 · v0.44.2 Bug1] 设置更新后由 server 调用：让**已存活**
        Agent 的模型绑定跟上新设置。新建的 Agent 天然走 _new_agent 的新解析，不用这里管。

        ── 为什么不是「改 provider_config」──
        老做法给 agent.provider_config 换一份新 ProviderConfig，指望下次生效。但
        KnoweAgent 在构造期就用那份 config 造好了 client、每回合复用、**再不回看
        provider_config**——于是那次替换是空操作，热更新根本没落到实际请求上。这正是
        「连接测试通过、聊天却报 missing http:// 」的病根：温载在**用户配模型之前**就把
        项目经理建了出来（空 base_url），设置到了也换不动它那把冻结的 client。

        ── 现在的做法：绑定真变了 → 退休旧实例，下次使用时全新重建 ──
        比对 `_bound_sig`（实例实际在用的接入点）与当前应当生效的绑定：
          · **变了**  → 退休该实例（从 _agents 弹出）。下一次 _get_or_create_* 会用新
                        绑定造一个全新的 KnoweAgent（连同工具箱）。代价仅是它内存里这一段
                        对话——身份/工作记忆/交接全在磁盘上；项目经理的历史本就不跨重启保留
                        （温载只新建空实例），退休它与既有语义一致。
          · **没变**  → 一律不动。/settings 每次改动（哪怕只是切个通知开关）都会调到这里，
                        只有模型绑定真的变了才重建，绝不因无关设置吃掉 Agent 的上下文。
        返回本次被退休（将按新绑定重建）的 Agent 数。
        """
        retired = 0
        for aid in list(self._agents.keys()):
            agent = self._agents.get(aid)
            if agent is None:
                continue
            new_sig = self._provider_sig_for(aid)
            old_sig = getattr(agent, "_bound_sig", None)
            if new_sig == old_sig:
                continue                       # 绑定没变——无关设置改动，别动它
            # 绑定真的变了：冻结的 client 改不动，退休 → 下次用新绑定重建。
            self._agents.pop(aid, None)
            self._probe_clear_interrupt(agent)
            retired += 1
            log.info("[%s] %s 的模型绑定已变更 → 退休旧实例，下次使用时按新绑定重建",
                     self.project_id, aid)
        if retired:
            log.info("[%s] 模型设置热更新：%d 个 Agent 将按新绑定重建", self.project_id, retired)
        return retired

    def _fire(self, payload: dict[str, Any]) -> None:
        if getattr(self, "_stopping", False) or getattr(self, "_stopped", False):
            return
        # 同步回调 → 异步广播。
        #
        # ★ 记账是必须的（真跑一遍才发现的）：不记账的话，stream_delta 这些
        #   create_task 出去的事件会**排到 message 后面**——前端先看到完整消息、
        #   再看到一串增量，气泡会闪一下、甚至重复。
        #   所以 _process_turn 在发 message 之前会 await 干净这批任务（_drain）。
        t = asyncio.create_task(self.emit(payload))
        self._fired.add(t)
        t.add_done_callback(self._fired.discard)

    async def _drain(self) -> None:
        """等回调排出去的广播都落地。"""
        while self._fired:
            await asyncio.gather(*list(self._fired), return_exceptions=True)

    # ═══════════════════════════════════════════════════════════
    # tools_knowe 要用的接口
    # ═══════════════════════════════════════════════════════════
    def _public_names(self) -> dict[str, str]:
        names = {COORDINATOR: msg("engine.007"), "zinnia": msg("engine.273"), "__platform__": msg("engine.274")}
        for aid in set(self._roster) | set(self._names):
            try:
                names[aid] = self.member_name(aid)
            except Exception:
                continue
        return names

    def _sanitize_outbound(self, payload: dict[str, Any]) -> dict[str, Any]:
        return sanitize_event(payload, self._public_names())

    # ── [v1.0.23.3] 辅助 LLM 提取四方向建议（方案 B） ──────────────────────
    async def _gen_suggestions(
        self, project_id: str, agent_id: str, text: str, context: str = "",
    ) -> None:
        """正文落定后异步调辅助 LLM 提取 1~4 个方向建议。失败/超时 → 静默无按钮。

        复用现成 aux_client 通道（先例：知知蒸馏）。
        正文永远只给辅助 LLM 当输入，主 LLM 上下文零改动。
        [v1.0.23.10] context = 最近几轮问答（调用点算好传入，最多 4 条×400 字符），
        让辅助 LLM 站在用户视角判断下一步，而不是只看回复反推。
        """
        try:
            system = _engine_block("suggestions_extract")
            if not system:
                return
            aux = runtime_settings.aux_effective()
            aux_ok = bool(aux and aux.get("api_key") and aux.get("base_url"))
            user_content = text
            if context:
                user_content = (
                    "[最近对话]\n" + context + "\n\n[当前助手回复]\n" + text
                )
            raw = await aux_client.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                api_key=aux["api_key"] if aux_ok else CONFIG.deepseek_api_key,
                base_url=aux["base_url"] if aux_ok else CONFIG.deepseek_base_url,
                model=aux["model"] if aux_ok else CONFIG.deepseek_model,
                max_tokens=800,
                timeout_s=CONFIG.adjust_timeout_s,
                what=msg("engine.170"),
            )
            items = _parse_suggestions_json(raw)
            if items:
                await self.emit({
                    "type": "suggestions", "agent_id": agent_id, "items": items,
                })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("[%s] suggestions 提取失败（静默，无按钮）：%s", project_id, exc)

    # ── [v0.36] 产出文件台账：记 / 取 ──────────────────────────────
    def note_file_produced(self, agent_id: str, rel_path: Any) -> None:
        """
        Worker 写成了一个文件 —— 记一笔，等本轮的气泡落定时附到消息上。

        由工具层（safe_write_file / copy_external_file 成功后）调用；`rel_path` 就是
        工具收到的那个项目内相对路径（safe_write_file 的 path / copy_external_file 的
        destination），原样存——它同时是前端向 /preview 取文件用的 key，动一个字符就取不到了。

        · path 去重：一个回合里同一个文件被反复重写（先写后补），只留最后一次的元数据，
          用户看到的是**一张卡**，不是三张一模一样的。
        · **绝不因为记账失败连累写文件**：分类 / stat 出任何岔子都吞掉，
          最坏结果是「这个文件没有卡片」，而不是「文件没写成」。
        """
        if not isinstance(agent_id, str) or not agent_id:
            return
        if not isinstance(rel_path, str) or not rel_path.strip():
            return
        rel = rel_path.strip()
        # v0.45.2：源码/配置已经有安全的只读预览，因此和文档、图片一样进入卡片台账。
        # 依赖树、缓存和构建目录仍在扫描入口剪掉；无扩展名文件只接受明确的常见工程名，
        # 避免把每个终端临时物都灌进聊天。
        _n = rel.replace("\\", "/").rsplit("/", 1)[-1]
        _d = _n.rfind(".")
        _ext = _n[_d + 1:].lower() if 0 <= _d < len(_n) - 1 else ""
        if not _should_track_produced(_ext, _n):
            return
        try:
            meta = self._describe_produced_file(rel)
        except Exception:                       # noqa: BLE001 记账绝不上抛
            log.debug("[%s] 记录产出文件失败（忽略）：%s", self.project_id, rel, exc_info=True)
            meta = {"path": rel, "name": rel.replace("\\", "/").rsplit("/", 1)[-1] or rel}
        bucket = self._files_produced.setdefault(self._current_bucket_key(agent_id), [])
        # 同路径 → 覆盖旧记录（保持在原顺序上更新为最新元数据）
        for i, existing in enumerate(bucket):
            if existing.get("path") == meta["path"]:
                bucket[i] = meta
                return
        bucket.append(meta)

    def _describe_produced_file(self, rel: str) -> dict[str, Any]:
        """相对路径 → 前端要的文件元数据。stat 失败也给得出名字与类型。"""
        norm = rel.replace("\\", "/")
        name = norm.rsplit("/", 1)[-1] or norm
        dot = name.rfind(".")
        ext = name[dot + 1:].lower() if 0 <= dot < len(name) - 1 else ""
        meta: dict[str, Any] = {
            "path": rel,
            "name": name,
            "ext": ext,
            "kind": _preview_kind_for_ext(ext),
        }
        # 大小 / 修改时间是「锦上添花」：取到就带上，取不到（并发删除等）不影响卡片。
        try:
            target = resolve_in_sandbox(
                self.workspace_root, rel, role="worker", operation="read",
            )
            st = target.stat()
            meta["bytes"] = int(st.st_size)
            meta["mtime"] = (
                datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                .isoformat().replace("+00:00", "Z")
            )
            # [v0.45.2 #3] 文件名不是身份。聊天卡片同时携带文件系统身份，
            # 预览端点才能在“同目录重命名”后找到原来的那个文件，而不是按相似名字猜。
            # st_dev/st_ino 在常见桌面文件系统上跨 rename 保持稳定；若平台不给 inode，
            # 仍保留 size + mtime_ns 供旧卡片/弱平台做唯一匹配。绝不保存绝对路径。
            meta["mtime_ns"] = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
            inode = int(getattr(st, "st_ino", 0) or 0)
            if inode > 0:
                meta["file_id"] = f"{int(getattr(st, 'st_dev', 0) or 0):x}:{inode:x}"
        except Exception:                       # noqa: BLE001
            pass
        return meta

    def _drain_files_produced(
        self,
        agent_id: str,
        *,
        channel: str | None = None,
        scope_id: str = "",
    ) -> list[dict[str, Any]]:
        """Drain only the file ledger that belongs to this visible execution scope."""

        key = self._event_bucket_key(
            agent_id=agent_id,
            channel=str(channel or _DM_CHANNEL_VAR.get() or self.project_id),
            scope_id=scope_id,
        )
        return self._files_produced.pop(key, [])

    # Legacy hook retained for callers.  Produced artifacts are sourced from the
    # tool mutation ledger and authoritative Worker Completion records; the Engine does
    # not infer them by scanning an arbitrary prefix of the workspace.
    async def scan_and_track_produced(self, agent_id: str, since_ts: float) -> None:
        del agent_id, since_ts
        return None

    async def emit(self, payload: dict[str, Any], *, channel: str | None = None) -> dict[str, Any]:
        """
        唯一出站口：流式文本先聚合，最终消息统一脱敏；seq/project_id/ts 由 Hub 盖。

        [v0.37] channel：把这条事件广播到**另一个 Hub 频道**，而不是本引擎的 project_id。
          群内私聊（submit_dm）用它把回复发到 `dm:{project}:{agent}` 频道——只在私聊窗口
          出现，群聊时间线看不到（验收 7）。**记忆与历史仍走 self.project_id**：私聊 id 只是
          一条广播频道，不是新项目。channel 逐调用传入（不存实例标志）→ 与并发的群回合 emit
          天然隔离，绝不会把某条群消息误发进私聊、或反之。

        逐 chunk 直接替换并不安全：模型可能把 ``pm_1`` 拆成 ``pm`` 和 ``_1`` 两帧。
        因此 stream_delta 不立即广播，等 message 到来后对完整文本做一次硬过滤；若完整流与
        final 一致，先发一条已脱敏的整段 delta，再发 message 使前端正常落定气泡。
        """
        etype = payload.get("type")
        # [v0.37.1] 频道决策：显式 channel 参数最高；否则若正在私聊回合（ContextVar 设了）
        #   且不是「必须留在群里」的管理事件 → 发私聊频道；其余发本项目（群）。
        dm_ctx = _DM_CHANNEL_VAR.get()
        if channel is not None:
            target_channel = channel
        elif dm_ctx and etype not in _DM_GROUP_ALWAYS_EVENTS:
            target_channel = dm_ctx
        else:
            target_channel = self.project_id

        payload = self._correlate_visible_event(payload, target_channel=target_channel)
        etype = payload.get("type")
        agent_id = payload.get("agent_id")
        scope_id = str(payload.get("scope_id") or "")
        bucket_key = (
            self._event_bucket_key(
                agent_id=agent_id,
                channel=target_channel,
                scope_id=scope_id,
            )
            if isinstance(agent_id, str) and agent_id else None
        )

        if etype == "stream_delta" and bucket_key is not None:
            content = payload.get("content")
            if isinstance(content, str):
                self._stream_buffers.setdefault(bucket_key, []).append(content)
            return dict(payload)

        if etype == "stream_reset" and bucket_key is not None:
            self._stream_buffers.pop(bucket_key, None)

        # File-card payloads are parsed independently from free-form text.  Keep the
        # original relative path (the preview API key) and discard only malformed rows;
        # a bad attachment must never make the whole chat event disappear.
        raw_explicit_files = payload.get("files")
        safe_payload = self._sanitize_outbound(payload)
        if raw_explicit_files is not None:
            explicit_files = self._normalize_outbound_files(raw_explicit_files)
            if explicit_files:
                safe_payload["files"] = explicit_files
            else:
                safe_payload.pop("files", None)
        completion_notification_id = _COMPLETION_NOTIFICATION_VAR.get()
        turn_idempotency_key = _TURN_IDEMPOTENCY_VAR.get() or completion_notification_id
        if (
            turn_idempotency_key
            and etype == "message"
            and isinstance(agent_id, str)
            and not safe_payload.get("event_id")
        ):
            # Any explicitly idempotent automatic turn (Coordinator review or Zinnia's
            # first welcome) receives one stable visible event id.  The Hub therefore
            # returns the first durable response instead of broadcasting a duplicate
            # after recovery/reconnect.  The legacy ``coordmsg_`` prefix is retained
            # because Hub already gives that namespace first-write-wins semantics.
            safe_payload["event_id"] = "coordmsg_" + hashlib.sha256(
                str(turn_idempotency_key).encode("utf-8")
            ).hexdigest()[:32]
            safe_payload["idempotency_key"] = str(turn_idempotency_key)

        # ── [v0.36] 把本轮产出的文件附到「带正文」的那条 message 上 ──
        #
        #   规矩只有一条：**文件卡挂在看得见的气泡下**。所以：
        #     · 有正文 → 这条 message 会渲染成气泡 → 附上文件、清空台账。
        #     · 空正文的 settle message（收光标用的那条）→ 先不附，把文件留给
        #       本轮真正带正文的那条；本轮若始终没正文，_settle_worker_turn 收尾时清账。
        #
        #   attach 在 sanitize **之后**：路径要原样（前端拿它去 /preview 取文件，
        #   脱敏会把路径里的名字改掉 → 取不到），而 files 里没有需要脱敏的自由正文。
        files_produced = getattr(self, "_files_produced", {})
        if etype == "message" and bucket_key is not None and files_produced.get(bucket_key):
            content_val = safe_payload.get("content")
            if isinstance(content_val, str) and content_val.strip():
                files = self._normalize_outbound_files(
                    self._drain_files_produced(
                        agent_id,
                        channel=target_channel,
                        scope_id=scope_id,
                    )
                )
                if files:
                    safe_payload["files"] = self._normalize_outbound_files(
                        [*(safe_payload.get("files") or []), *files]
                    )

        if etype == "message" and bucket_key is not None:
            buffered = "".join(self._stream_buffers.pop(bucket_key, []))
            safe_final = safe_payload.get("content") if isinstance(safe_payload.get("content"), str) else ""
            safe_buffered = sanitize_text(buffered, self._public_names()) if buffered else ""
            if safe_buffered and safe_buffered.strip() == safe_final.strip():
                replay_payload = {
                    "type": "stream_delta",
                    "agent_id": agent_id,
                    "content": safe_buffered,
                }
                for key in ("scope_id", "task_id", "attempt_id", "run_id", "channel_id"):
                    if safe_payload.get(key):
                        replay_payload[key] = safe_payload[key]
                await self.hub.emit(target_channel, replay_payload)

        event = await self.hub.emit(target_channel, safe_payload)
        # [v1.0.24.4] 活动账本记账/销账——只挂在唯一出站口这一处：
        #   scope_id/channel 取 _correlate_visible_event 补全后的身份（与前端
        #   activeScopes 的键同构），广播成功才落账，账本永远等于现场广播事实。
        if etype == "agent_active" and isinstance(agent_id, str):
            self._record_open_activity(agent_id, scope_id, target_channel)
        elif etype == "agent_idle" and isinstance(agent_id, str):
            self._release_open_activity(agent_id, scope_id, target_channel)
        # [v1.0.23.3] 四方向建议：agent 原生回复落定后异步调辅助 LLM 提取。
        #   判定 = message + 非空正文 + (reasoning key = COORDINATOR/DeepSeekAgent 档
        #   || completion_id = worker completion 档)；engine 系统消息两者皆无，不触发。
        if (
            etype == "message"
            and isinstance(agent_id, str)
            and isinstance(safe_payload.get("content"), str)
            and safe_payload["content"].strip()
            and ("reasoning" in safe_payload or "completion_id" in safe_payload)
        ):
            sg_task = asyncio.create_task(
                self._gen_suggestions(
                    self.project_id, agent_id, safe_payload["content"],
                    context=_format_suggestion_context(self.history),
                ),
                name=f"suggestions:{self.project_id}:{agent_id}",
            )
            self._fired.add(sg_task)
            sg_task.add_done_callback(self._fired.discard)
        # [v0.37] 私聊回复（发到非本项目频道）**不进群历史**：那会让项目经理的模型上下文看见私聊、
        #   污染群聊。私聊要让项目经理知道的是「要点」，走 harness memory（record_project_activity），
        #   不是把逐字对话灌进群历史。
        if (etype == "message" and target_channel == self.project_id
                and isinstance(event.get("content"), str) and event["content"]):
            # [I-6] Strip control markers before durable history. If the content was
            # nothing but a marker, it collapses to empty and is not persisted.
            _persisted = _strip_control_markers(event["content"])
            if _persisted:
                self.history.append({"role": "assistant", "content": _persisted})

        if etype in {"agents_created", "agent_removed", "instruction_injected"}:
            # [v0.30 Bug6] 用**原始** payload，不用洗过的：这个函数要拿 id 去查名字，
            #   而 sanitize 的职责恰恰是把 id 换掉。文本出口在 record_project_activity
            #   里自己会过一遍 sanitize_text——内部记账和对外脱敏，各走各的门。
            self._record_activity_from_event(payload)
        return event

    #: 老代码里有人调 _emit（下划线版），留个别名
    _emit = emit

    @property
    def busy(self) -> bool:
        return bool(
            self._turns_active > 0
            or not self.inbox.empty()
            or self.gate.has_pending()
            or self._fired
            or any(not task.done() for task in self._dm_tasks)
            or self._dm_pending
            or self._task_envelopes
            or any(not task.done() for task in self._worker_turns.values())
        )

    def roster(self) -> dict[str, str]:
        """花名册：agent_id → role（不含项目经理）。"""
        return dict(self._roster)

    def resolve_group_mentions(self, content: str) -> MentionResolution:
        """按**当前在册身份**解析一条群聊消息里的 @目标。

        名字与角色都从引擎花名册读，不信任前端传目标 id：这既保证重连/多端一致，
        也避免客户端伪造一个已归档或根本不存在的 Worker。未知/歧义提及由纯解析器忽略，
        server 随后自然回落到普通项目经理群聊。
        """
        members = [
            MentionMember(
                agent_id=COORDINATOR,
                name=self.member_name(COORDINATOR),
                role=msg("engine.007"),
                coordinator=True,
            ),
        ]
        members.extend(
            MentionMember(agent_id=aid, name=self.member_name(aid), role=role)
            for aid, role in self._roster.items()
        )
        return resolve_mentions(content, members)

    def add_member(self, agent_id: str, role: str,
                   name: str | None = None) -> KnoweAgent:
        """
        组队通过 → 进花名册 + 真的把 Worker 实例建出来 + 落盘。

        [v0.8a A-1] 进册和落盘都下沉到 `_get_or_create_worker` 里了——
        进队这件事只有一个入口，不会再出现「人建了、册上没有」。
        这里只补一句：角色可能变（同一个 id 换了角色），以最新的为准。

        [v0.9b Bug1] ★ **加人是增量的。**
          再调一次 propose_agents，是在现有队伍上**添人**，不是把队伍推倒重来。
          同一个 id 调两次 → 第二次什么都不会发生（_get_or_create_worker 认得他）。
          （模型之所以以为「只能一次性建队」，是我们从来没告诉过它可以加人——
            见 tools_knowe 的工具描述和 engine._team_ctx 的每轮注入。）
        """
        fresh = agent_id not in self._roster
        stored_before = self.stored_agent_info(agent_id) if fresh else None
        if stored_before and stored_before.get("status") == "deleted":
            raise ValueError(
                msg("engine.275", agent_id=agent_id)
            )
        was_archived = bool(
            stored_before and stored_before.get("status", "active") == "removed"
        )
        stored_name = (stored_before or {}).get("name")
        if stored_name:
            # 恢复路径必须让持久身份覆盖任何审批阶段留下的临时缓存。
            self._names[agent_id] = stored_name
        elif name:                                  # [v0.9c] 调用方（工具层）算好的名字优先
            self._names.setdefault(agent_id, name)
        agent = self._get_or_create_worker(agent_id, role)
        if self._roster.get(agent_id) != role:
            self._roster[agent_id] = role
            self._persist_member(agent_id, role)
        if fresh:
            self._pending_member_activity[agent_id] = "restored" if was_archived else "created"
            self.log_agent_resource(msg("engine.276") if was_archived else msg("engine.277"), agent_id, role,
                                    msg("engine.278") if was_archived else msg("engine.279"),
                                    name=self.member_name(agent_id))
        return agent

    async def welcome_worker(self, agent_id: str) -> None:
        """
        [v1.0.23.3] 初入群打招呼：新 worker 入群时触发一次**纯对话** LLM 回合，
        让它在群里用一句话打招呼（人性化）。回复经 emit message 进群聊。

        ★ 设计红线：**绝不走任务链路**（v1.0.23.3 幽灵拉起修复）——
          · 不建 TaskEnvelope、不注册 _task_envelopes → engine 的回合分发循环
            （_spawn_pending_workers）永远看不到它 → 杜绝二次拉起/CompletionConflict
          · 不注册工具（tools=[]）→ 杜绝「打招呼前先调工具」（safe_list_dir 教训）
          · 不走 WorkerRuntime → 不产生 completion/report/worklog/metrics 污染
        幂等：同一 worker 只欢迎一次（内存 _welcomed 集合）。
        失败容错：问候失败只记日志，绝不影响建群/拉人主流程。
        语言：提示词经 msg() 按当前系统语言现取——中英文互不混用。
        """
        if not CONFIG.welcome:
            return
        if agent_id == COORDINATOR or agent_id not in self._roster:
            return
        if agent_id in self._welcomed:
            return
        self._welcomed.add(agent_id)
        project_name = self._project_display_name or self.project_id
        uname = runtime_settings.user_name(default="")
        role = self._roster.get(agent_id, "")
        prof = roles.profile_for_agent_id(agent_id)
        role_label = roles.localized_label(prof) if prof is not None else role
        try:
            goal = (
                msg("engine.welcome", user=uname, project=project_name, role=role_label)
                if uname else
                msg("engine.welcome.anon", project=project_name, role=role_label)
            )
        except Exception:
            log.exception("[%s] 初入群打招呼文案构造失败 %s", self.project_id, agent_id)
            return
        # fire-and-forget：welcome_worker 只做构造立即返回，不阻塞建群/拉人
        asyncio.create_task(self._welcome_chat(agent_id, goal))

    async def _welcome_chat(self, agent_id: str, goal: str) -> None:
        """初入群打招呼的纯对话回合：零信封、零工具、零任务痕迹。"""
        try:
            role = self._roster.get(agent_id, "")
            agent = self._get_or_create_worker(agent_id, role)
            # 模型就绪门控（与正式回合同款）
            await runtime_settings.wait_for_model_ready(self.project_id, agent_id)
            system_prompt = self._identity_block(agent_id)
            client = getattr(agent, "_client", None)
            if client is None or not hasattr(client, "chat"):
                log.warning("[%s] %s 初入群打招呼：无可用 provider client", self.project_id, agent_id)
                return
            log.info("[%s] %s 初入群打招呼 lang=%s goal=%s",
                     self.project_id, agent_id,
                     runtime_settings.language(), goal)
            log.info("[%s] %s 初入群打招呼 system_prompt=%s",
                     self.project_id, agent_id, system_prompt[:400])
            response = await client.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": goal},
                ],
                tools=[],      # 纯对话：不给工具，杜绝「打招呼前先调工具」
                temperature=CONFIG.worker_temperature,
            )
            text = _chat_text(response)
            log.info("[%s] %s 初入群打招呼 reply=%s", self.project_id, agent_id, text[:200])
            if not text:
                log.warning("[%s] %s 初入群打招呼：模型无回复", self.project_id, agent_id)
                return
            await self.emit({
                "type": "message",
                "agent_id": agent_id,
                "content": text,
            })
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[%s] 初入群打招呼失败 %s", self.project_id, agent_id)

    def _task_envelope_store(self) -> TaskEnvelopeStore:
        return TaskEnvelopeStore(self.internal_workspace)

    def _task_actor_refs(self, target_id: str) -> tuple[str, str]:
        self.ensure_agent_profile(target_id)
        safe_id = _safe(target_id)
        return (f"agents/{safe_id}/IDENTITY.md", f"agents/{safe_id}/SOUL.md")

    @staticmethod
    def _runtime_report_lineage(content: str) -> tuple[bool, str]:
        """Recognize reports that carry immutable Runtime delivery lineage."""
        text = str(content or "")
        fields: dict[str, str] = {}
        front = re.match(r"\A---\s*\n(?P<body>.*?)\n---(?:\s*\n|\Z)", text, re.S)
        if front:
            for line in front.group("body").splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                fields[key.strip().casefold().replace("-", "_")] = value.strip().strip("\"'")
        status = str(fields.get("status") or "").strip().casefold()
        delivery_id = str(fields.get("delivery_id") or fields.get("delivery") or "").strip()
        task_id = str(fields.get("task_id") or "").strip()
        run_id = str(fields.get("run_id") or "").strip()
        succeeded = status in {"completed", "complete", "succeeded", "success", "finalized"}
        return bool(succeeded and delivery_id and task_id and run_id), delivery_id

    def _resolve_report_refs(
        self, refs: list[str] | tuple[str, ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """Canonicalize explicitly selected predecessor reports without scoring prose."""
        accepted: list[str] = []
        delivery_ids: list[str] = []
        for raw in refs:
            ref = str(raw or "").strip()
            if not ref:
                continue
            path = self.find_handoff_file(ref)
            canonical = self.handoff_ref(path) if path is not None else ref
            if canonical not in accepted:
                accepted.append(canonical)
            if path is None:
                continue
            try:
                content = path.read_text("utf-8", errors="strict")
            except (OSError, UnicodeError):
                continue
            _completed, delivery_id = self._runtime_report_lineage(content)
            if delivery_id and delivery_id not in delivery_ids:
                delivery_ids.append(delivery_id)
        return tuple(accepted), (), tuple(delivery_ids)

    def _create_task_envelope(
        self,
        *,
        target_id: str,
        title: str,
        objective: str,
        origin_kind: str,
        instruction_ref: str,
        source_ref: str = "",
        background: str = "",
        previous: str = "",
        inputs_text: str = "",
        acceptance_text: str = "",
        notes: str = "",
        report_refs: tuple[str, ...] = (),
        decision_refs: tuple[str, ...] = (),
        input_specs: tuple[Mapping[str, Any], ...] = (),
        context_refs: Sequence[ContextReference | Mapping[str, Any] | str] = (),
        attempt_id: str = "",
        authorization_ref: str = "",
        approval_origin: str = "",
        channel: str = "",
        extensions: Mapping[str, Any] | None = None,
    ) -> tuple[TaskEnvelope, str]:
        """Build and persist the one shared task model from verbatim Coordinator text."""
        if target_id not in self._roster:
            raise ValueError(msg("engine.126", target_id=target_id))
        objective_text = str(objective)
        if not objective_text.strip():
            raise ValueError(msg("engine.280"))
        identity_ref, soul_ref = self._task_actor_refs(target_id)
        accepted_refs, observed_refs, accepted_delivery_ids = self._resolve_report_refs(report_refs)
        identity = self.identity(target_id)
        coordinator_turn_id = f"coordinator-turn-{int(getattr(self, '_turn_count', 0))}"
        source = source_ref or instruction_ref
        task_id = stable_identifier("task", {
            "project_id": self.project_id,
            "origin_kind": origin_kind,
            "coordinator_turn_id": coordinator_turn_id,
            "instruction_ref": instruction_ref,
            "source_ref": source,
            "worker_id": target_id,
            "objective": objective_text,
        })
        resolved_attempt_id = attempt_id or stable_identifier("attempt", {"task_id": task_id, "ordinal": 1})
        envelope_ref = f"runtime/task-envelopes/{task_id}/{resolved_attempt_id}.json"
        coordinator_context = {
            key: value for key, value in {
                "background": str(background or ""),
                "previous": str(previous or ""),
                "inputs": str(inputs_text or ""),
                "acceptance": str(acceptance_text or ""),
                "notes": str(notes or ""),
            }.items() if value
        }
        extension_metadata = dict(extensions or {})
        # [v1.0.23.5] 删除意图安全门整体移除：不再用正则猜测任务是否携带删除意图，
        #   删除权限对一切任务放行（用户指令即最高授权；路径安全校验保留在工具层）。
        roots_value = extension_metadata.pop(
             "authorized_external_roots",
            extension_metadata.pop("external_roots", ()),
        )
        authorized_external_roots = _unique_strings(roots_value)
        # [v1.0.23.8-A] 显式授权为空时：从 goal/objective 文本自动提取外部
        #   绝对路径（如 D:\xxx\file.md），取其根目录加入授权——coordinator
        #   派活时无需显式声明 external roots，copy_external_file 不再被
        #   「no external root is authorized」拦截。仅当显式为空时才自动补，
        #   显式授权永远优先（最小权限原则）。
        if not authorized_external_roots:
            auto_roots = _auto_external_roots(objective_text)
            if auto_roots:
                authorized_external_roots = _unique_strings(auto_roots)

        metadata = {
            "engine_boundary": "ProjectEngine",
            "instruction_delivery": "verbatim",
            "review_owner": "coordinator",
            "user_address": self._user_address_line(),
            "identity_ref": identity_ref,
            "soul_ref": soul_ref,
            **({
                "identity_contract_v1": {
                    "platform_name": identity_for(
                        target_id,
                        display_name=str(identity.get("name") or target_id),
                        role_name=str(identity.get("role") or self._roster.get(target_id, msg("engine.106"))),
                    ).platform_name,
                    "agent_id": target_id,
                    "display_name": str(identity.get("name") or target_id),
                    "role_name": str(identity.get("role") or self._roster.get(target_id, msg("engine.106"))),
                    "system_block": self._identity_block(target_id),
                },
            } if feature_enabled(FeatureFlag.IDENTITY_CONTRACT_V1) else {}),
            "coordinator_turn_id": coordinator_turn_id,
            "origin": {
                "kind": origin_kind,
                "project_id": self.project_id,
                "coordinator_turn_id": coordinator_turn_id,
                "instruction_ref": instruction_ref,
                # 用户批准文件是本次任务的稳定、可审计授权引用（propose_next 审批路径注入）。
                "authorization_ref": authorization_ref,
                "approval_origin": approval_origin,
                "source_ref": source,
            },
            "predecessors": {
                "accepted_delivery_ids": accepted_delivery_ids,
                "accepted_report_refs": accepted_refs,
                "observed_report_refs": observed_refs,
                "decision_refs": tuple(str(item) for item in decision_refs if str(item).strip()),
            },
            "inputs": tuple(dict(item) for item in input_specs),
            "context_policy": {},
            "task_envelope_ref": envelope_ref,
            "attempt_ordinal": 1,
            **({"coordinator_context": coordinator_context} if coordinator_context else {}),
            **extension_metadata,
            # Runtime consumes only these narrow, explicit safety inputs.
            # [v1.0.23.5] delete_intent 恒 True：删除安全门已移除（见 _create_task_envelope 注释）。
            "delete_intent": True,
            "authorized_external_roots": list(authorized_external_roots),
        }
        delivery = DeliveryTarget(
            audience=DeliveryAudience.USER,
            attempt_id=resolved_attempt_id,
            channel=channel,
        )
        envelope = TaskEnvelope(
            task_id=task_id,
            project_id=self.project_id,
            goal=objective_text,
            worker_id=target_id,
            attempt_id=resolved_attempt_id,
            title=str(title or keyword_of(objective_text)),
            worker_name=str(identity.get("name") or target_id),
            worker_role=str(identity.get("role") or self._roster.get(target_id, msg("engine.106"))),
            context_refs=tuple(ContextReference.from_value(row) for row in context_refs),
            budget=BudgetSpec(),
            delivery=delivery,
            source=source or "coordinator_instruction",
            scope_root=str(self.workspace_root),
            instruction_ref=instruction_ref,
            metadata=metadata,
            provenance=current_provenance_dict(),
        )
        return self._task_envelope_store().commit(envelope)

    def inject_task_envelope(self, envelope: TaskEnvelope) -> TaskEnvelope:
        """Queue one already-persisted TaskEnvelope for direct Runtime execution."""
        target_id = envelope.worker_id
        supersedes_task_id = str(envelope.metadata.get("supersedes_task_id") or "")
        waiting = self.completion_store.active_wait_for_task(
            supersedes_task_id, worker_id=target_id,
        ) if supersedes_task_id else None
        self.completion_store.assert_worker_available(target_id, supersedes_task_id=supersedes_task_id)
        if waiting is not None and supersedes_task_id == waiting.task_id:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                recovery = loop.create_task(
                    self.reconcile_completion_outbox(),
                    name=f"completion-supersede:{self.project_id}:{target_id}",
                )
                self._fired.add(recovery)
                recovery.add_done_callback(self._fired.discard)
        self._get_or_create_worker(target_id, self._roster.get(target_id, msg("engine.106")))
        committed, _ref = self._task_envelope_store().commit(envelope)
        self._task_envelopes[target_id] = committed
        self._workers_with_open_activity.add(target_id)
        return committed

    def _create_direct_task_envelope(self, worker_id: str, content: str) -> TaskEnvelope:
        channel = str(_DM_CHANNEL_VAR.get() or "")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        source_ref = f"direct://{self.project_id}/{int(getattr(self, '_turn_count', 0))}/{worker_id}/{digest}"
        envelope, _ref = self._create_task_envelope(
            target_id=worker_id,
            title=keyword_of(content),
            objective=content,
            origin_kind="direct",
            instruction_ref=source_ref,
            source_ref=source_ref,
            channel=channel,
            extensions={"direct_dispatch": True},
        )
        return envelope

    def inject_instruction(self, target_id: str, instruction: str) -> TaskEnvelope:
        """Create and queue a direct TaskEnvelope from verbatim instruction text."""
        envelope = self._create_direct_task_envelope(target_id, instruction)
        return self.inject_task_envelope(envelope)

    async def on_proposal_rejected(self, decision: str, what: str) -> None:
        """
        提案没通过（拒绝 / 超时 / 取消）。

        v0.1 的做法：打断项目经理，再塞一条 followup 让他跟用户交代一句。照搬——
        **但只在「拒绝」时这么做**：
          · timeout：用户可能只是走开了，项目经理把话说完更有用，不打断
          · cancelled：是新消息导致的，回合本来就要收摊，再塞消息只会打架
        """
        coord = self._agents.get(COORDINATOR)

        if decision == "rejected":
            # A rejection follow-up is a control turn, not a new dispatch request.
            # Freeze proposal tools for that one turn so the user's explicit decision
            # cannot be immediately re-opened by the model.  The next user turn clears it.
            self._rejection_pending = True

            if coord is not None:
                coord.interrupt()
            # 一次拒绝只排一个收口回合。不去重的话，纠正器再弹一张卡、用户再拒一次，
            # 队列里就攒下两条 followup → 项目经理连发两三条互相打架的话
            # （用户看到的正是这个：「我现在重新提议」+「那先不派」+「那先不动」）。
            if not self._rejection_followup_queued:
                self._rejection_followup_queued = True
                await self._submit_internal(REJECTION_FOLLOWUP, priority="control")

        # [v0.8c #7] ★ 不管是拒绝、超时还是取消：这一轮的工具调用都**没走完**。
        #   现在就把回执补上，别把一段坏历史留到下一轮去炸 API。
        #   （interrupt() 打断的正是「还停在这个工具调用里」的那个循环——
        #     tool 回执永远不会被写进去了。）
        if coord is not None:
            self.repair_agent_history(coord)

        log.info("[%s] %s提案未通过：%s", self.project_id, what, decision)

    # ── [v0.8c #7] 历史自愈 ──
    def repair_agent_history(self, agent: Any) -> int:
        """
        补上悬空的 tool_call 回执。返回补了几条（0 = 本来就是好的）。

        找不到消息历史 → **喊一声**，返回 -1。装作修好了比不修更糟：
        下一轮照样 400，而日志里干干净净，谁也查不到。
        """
        msgs = _agent_messages(agent)
        if msgs is None:
            if not self._history_warned:
                self._history_warned = True
                log.error(
                     "[%s] 找不到 %s 的消息历史 —— tool_calls 自愈失效。"
                    + msg("engine.281")
                    + msg("engine.282"),
                    self.project_id, getattr(agent, "agent_id", "?"),
                    _attr_dump(agent),
                )
            return -1

        fixed = _repair_tool_calls(msgs)
        if fixed:
            log.warning(
                 "[%s] %s 的历史里有 %d 个 tool_call 没有回执（多半是提案被拒/被打断）"
                + msg("engine.283"),
                self.project_id, getattr(agent, "agent_id", "?"), fixed, len(msgs),
            )
        else:
            log.debug("[%s] %s 的历史干净（%d 条，%s）",
                      self.project_id, getattr(agent, "agent_id", "?"), len(msgs),
                      _LEARNED_HISTORY_ATTR or msg("engine.284"))
        return fixed

    def _panic_reset(self, agent: Any) -> None:
        """
        [v0.9b Bug2] 最后一道闸：历史已经**炸到 API 拒收**，而我们又修不动它。

        这时候只剩一条路：把这个 agent 实例扔了，下一轮重建。
        代价是他忘掉这一轮的短期记忆——但**总比从此每一句话都回 400 强**。
        （用户看到的不是「哑火」，而是项目经理接着说话，只是不记得刚才提过的那个方案。）

        这条路平时**永远不该走到**。走到了，日志里那行 ERROR 就是给我们的传票。
        """
        aid = getattr(agent, "agent_id", "?")
        log.error(
             "[%s] %s 的历史修不动、API 又在拒收 —— 重建这个 agent（他会忘掉本轮短期记忆）。"
            + msg("engine.285"),
            self.project_id, aid, _attr_dump(agent),
        )
        self._agents.pop(aid, None)

    # ── 报告 ──
    def seen_report(self, report_hash: str) -> bool:
        return report_hash in self._reports

    def mark_report(self, report_hash: str) -> None:
        self._reports.add(report_hash)

    def write_report(self, agent_id: str, report_hash: str,
                     summary: str, artifacts: list[str]) -> Path:
        """
        [已弃用 · v0.9a] 老的「一份报告一个 hash 文件名」写法（handoffs/{hash}.md）。

        转发到 write_handoff_report —— 老调用方（如果还有）也会写出规范格式的报告，
        而不是继续在 handoffs/ 根目录下撒一堆看不懂名字的文件。
        留着这个函数只是为了不让任何一处 import 断掉。
        """
        return self.write_handoff_report(agent_id, report_hash, summary, artifacts)

    # ── 沙箱 ──
    def _snapshot_workspace_baseline(self, envelope: Any) -> tuple[str, ...] | None:
        """[v1.0.23.8-C] 拍 attempt 开始时的 workspace 交付文件清单。

        Worker 交付时 manifest_for_task 用它做快照对比：本次 attempt 新增的
        文件即使没有 verified fact（如 shell cp 保留源 mtime）也会被补进
        manifest → 前端出文件卡片。只收常见交付扩展名 + 排除系统/内部目录；
        失败返回 None（调用方跳过 baseline，不阻断派发）。
        """
        try:
            raw = envelope.scope_root or self.workspace_root
            root = Path(raw).expanduser().resolve()
            if not root.is_dir():
                return None
            SKIP_DIRS = {
                "node_modules", ".git", "__pycache__", "logs", "backups", "backup",
                "dist", "build", "out", ".venv", "venv", "data", "runtime",
                "agents", "handoffs", "knowledge", "memory", ".idea", ".vscode",
            }
            DELIVERY_EXTS = {
                ".md", ".mdx", ".txt", ".pdf", ".docx", ".xlsx", ".pptx",
                ".csv", ".json", ".yaml", ".yml", ".html", ".htm",
                ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                ".zip", ".tar", ".gz", ".py", ".js", ".ts", ".tsx",
            }
            found: list[str] = []
            for path in root.rglob("*"):
                try:
                    if not path.is_file():
                        continue
                    if any(part.casefold() in SKIP_DIRS for part in path.parts):
                        continue
                    if path.suffix.casefold() not in DELIVERY_EXTS:
                        continue
                    found.append(path.relative_to(root).as_posix())
                except OSError:
                    continue
            return tuple(sorted(found))
        except Exception:
            return None

    @property
    def workspace_root(self) -> Path:
        """
        这个项目的工作目录 —— **Worker 的读写只能在这里面**（tools_knowe.resolve_in_sandbox
        拿它当根）。

        [v0.7 A0] 用户**建项目时选的那个目录**是唯一合法来源。他心里想的「这个项目」，
          就是磁盘上那个文件夹；团队干出来的东西得出现在他自己看得见的地方，
          而不是埋在某个应用数据目录的深处。

        [任务 1.7] 已删除 v0.7 遗留的 managed-workspaces 兜底：建群必须携带目录，
          拿不到目录就是配置错误，直接抛 WorkspaceUnavailable（绝不偷偷建目录）。
        """
        if self._workspace_root is None:
            raise WorkspaceUnavailable(
                "建群缺少项目目录：本版本已移除工作区兜底机制，建群必须携带有效目录（project_dir），"
                "请重新发起建群并在弹窗中选择项目文件夹。"
            )
        root = self._workspace_root.expanduser().resolve()
        # 显式目录属于用户资产：被移动/删除后必须上报失效，绝不能 mkdir 把空壳偷偷建回来。
        if not root.is_dir():
            raise WorkspaceUnavailable(msg("engine.286", root=root))
        return root

    #: 老代码（v0.6 的 tools_knowe）叫它 workspace，留个别名，语义完全一致
    @property
    def workspace(self) -> Path:
        return self.workspace_root

    def set_workspace_root(self, root: Path | str) -> None:
        """切换用户业务目录；内部交接/记忆根保持不变。

        新目录若带有 v0.15 的 reserved folders，会在下一次内部布局检查时执行只读、
        不合并的冲突预检与一次性导入。已有 handoff 缓存与 step 状态不因业务目录恢复而清空。
        """
        path = Path(root).expanduser().resolve()
        if not path.is_dir():
            raise WorkspaceUnavailable(msg("engine.287", path=path))
        self._workspace_root = path
        self._internal_layout_ready = False
        self.ensure_internal_layout()

    @property
    def backend_data_root(self) -> Path:
        """Complete backend-internal deny boundary; never infer it from a business root."""
        root = self._backend_data_root or Path(CONFIG.data_dir or "./data")
        return root.expanduser().resolve()

    @property
    def internal_workspace(self) -> Path:
        """Agent 内部文件的物理根；永不作为 Worker 文件沙箱。"""
        root = self._internal_workspace_root
        if root is None:
            try:
                root = internal_workspace_for(self.backend_data_root, self.project_id)
            except ValueError:
                # Legacy unit callers construct pure in-memory Engines with short ids such as
                # ``p1``.  Production Engines always have a Store and are required to use the
                # canonical helper above; tests may keep an already-safe single component.
                if self._store is not None or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", self.project_id):
                    raise
                root = (self.backend_data_root / self.project_id).resolve()
        else:
            root = root.expanduser().resolve()
        return root

    def _legacy_workspace_for_import(self) -> Path | None:
        """Return only the known old internal root ``data_root/internal/<id>``."""
        legacy = self.backend_data_root / "internal" / self.project_id
        return legacy if os.path.lexists(os.fspath(legacy)) else None

    def ensure_internal_layout(self) -> None:
        """Create/migrate the v0.16 layout once per bound business workspace."""
        if self._internal_layout_ready:
            return
        legacy = self._legacy_workspace_for_import()
        try:
            report = ensure_internal_workspace(
                self.internal_workspace, legacy_workspace=legacy,
            )
        except (ValueError, OSError) as exc:
            raise WorkspaceUnavailable(str(exc)) from exc
        self._internal_migration_errors = list(report.errors)
        if self._internal_migration_errors:
            raise WorkspaceUnavailable(
                msg("engine.288") + "；".join(self._internal_migration_errors)
            )
        self._internal_layout_ready = True

    @property
    def handoff_dir(self) -> Path:
        self.ensure_internal_layout()
        return self.internal_workspace / "handoffs"

    def handoff_ref(self, path: Path) -> str:
        """Stable virtual handoff path, independent of its physical storage root."""
        path = path.resolve()
        try:
            return "handoffs/" + path.relative_to(self.handoff_dir.resolve()).as_posix()
        except ValueError:
            pass
        return path.name

    def handoff_files(self) -> list[Path]:
        """All readable handoff markdown from the authoritative internal workspace."""
        self.ensure_internal_layout()
        found: list[Path] = []
        seen: set[str] = set()
        base = self.handoff_dir
        if not base.is_dir():
            return found
        for path in sorted(base.rglob("*.md")):
            if not path.is_file():
                continue
            ref = self.handoff_ref(path)
            if ref in seen:
                continue
            seen.add(ref)
            found.append(path)
        return found

    def handoff_reports(self) -> list[Path]:
        """Reports from the active internal handoff book."""
        active = list(self.handoff.reports())
        seen = {self.handoff_ref(p) for p in active}
        for path in self.handoff_files():
            ref = self.handoff_ref(path)
            name = path.name
            looks_report = name.startswith("report-")
            if looks_report and ref not in seen:
                active.append(path)
                seen.add(ref)
        return sorted(active, key=lambda p: self.handoff_ref(p))

    def find_handoff_file(self, query: str) -> Path | None:
        """Resolve internal report references without allowing arbitrary paths."""
        found = self.handoff.find(query)
        if found is not None:
            return found
        needle = str(query or "").replace("\\", "/").strip().lstrip("./")
        if needle.startswith("handoffs/"):
            needle = needle[len("handoffs/"):]
        for path in self.handoff_files():
            ref = self.handoff_ref(path)
            virtual = ref[len("handoffs/"):] if ref.startswith("handoffs/") else ref
            if needle in {virtual, path.name, path.stem}:
                return path
            try:
                head = path.read_text("utf-8", errors="replace")[:4096]
            except OSError:
                continue
            if f"report_hash: {needle}" in head or f"hash: {needle}" in head:
                return path
        return None

    # ═══════════════════════════════════════════════════════════
    # [v0.9a B] 交接账本
    # ═══════════════════════════════════════════════════════════
    @property
    def handoff(self) -> HandoffBook:
        """交接账本。所有读写只从 internal_workspace/handoffs 长出来。"""
        if self._handoff is None:
            self._handoff = HandoffBook(self.handoff_dir)
        return self._handoff

    def phase_dir(self) -> Path:
        """当前阶段目录（一个都没有就现开一个 01-起步）。"""
        return self.handoff.current_phase()

    def set_phase(self, name: str) -> Path:
        """换阶段（项目经理说了算：propose_next 的 phase 参数，或 NEXT_HANDOFF_DIR 标记）。"""
        d = self.handoff.new_phase(name)
        log.info("[%s] 交接阶段 → %s", self.project_id, d.name)
        return d

    def _team_ctx(self) -> str:
        """
        [v0.9b Bug1] 每一轮告诉项目经理：**现在队里有谁**，以及加人/减人的规矩。

        Bug 1 的根不在代码里（engine.add_member 一直是增量的、幂等的），
        而在**没人告诉过模型**这件事。模型只看得见 SOUL 和工具描述，
        于是它按最保守的方式理解「组建团队」＝一次性的事，
        然后一本正经地回你「只能一次性创建团队，不能修改已有团队」。

        所以把现场直接摊在它眼前：队里现在这几个人，你可以加，可以减，
        但**加减都要走审批卡**——决定权在屏幕前的人手里。
        """
        full = self._sync_roster_from_store()   # [v0.9d/v0.44.12] 同一次读取既对账，也列归档

        # [v0.10a Issue 1 红线] ★ 名单里**不再带 id**。
        #   老格式是 `名字（id=fe_1，角色：前端）` —— 项目经理每一轮都看见 id，
        #   于是复述给用户时把 fe_1 一起报了出去。id 是内部标识，
        #   项目经理调工具用得着（那是它自己加人时定的），但没有任何理由摆在名单里让它天天念。
        #   拿掉之后，项目经理眼前就只有「名字 + 角色」，跟用户说话时自然也只有这两样。
        # [v0.22 问题四] 名字后面补一句「擅长/不适合」。
        #
        #   老格式只有「Shiloh（技术写作）」—— 项目经理拿这七个字去判断「他适不适合爬网页」，
        #   判断不了，于是挑了名字里带「技术」的那个。24 个标签就是 24 个盲盒。
        #   一人一句，按当前花名册完整呈现；而这正是**派活那一刻**他看的地方。
        #
        # [v0.22 问题二] 同一行再补一个**事实**：他此刻是不是真的在干活。
        #
        #   这一格是引擎知道的硬事实，而项目经理正是在这上面撒了谎
        #   （「Shiloh 正在执行这个任务」——其实没有）。把真相摆在他眼前，
        #   谎就得当着事实的面说，难度大得多；用户追问「她去了吗」时，
        #   他也能照着这里如实回答，而不是顺着自己上一句圆下去。
        lines: list[str] = []
        for aid, role in self._roster.items():
            hint = roles.roster_hint(role)
            busy = msg("engine.289") if aid in self._workers_with_open_activity \
                else msg("engine.290")
            tail = f" —— {hint}" if hint else ""
            lines.append(f"  · {self.member_name(aid)}（{self._display_role(aid, role)}）{busy}{tail}")
        active_listed = "\n".join(lines) if lines else EMPTY_TEAM

        archived_lines: list[str] = []
        for aid, row in sorted(full.items(), key=lambda item: item[0]):
            if aid == COORDINATOR or row.get("status", "active") != "removed":
                continue
            role = str(row.get("role") or msg("engine.106"))
            name = str(row.get("name") or "").strip() or legacy_display_name(aid, role)
            self._names[aid] = name
            archived_lines.append(f"  · {aid} {name}（{self._display_role(aid, role)}）")
        archived_listed = "\n".join(archived_lines) if archived_lines else EMPTY_ARCHIVE

        return _engine_block("TEAM_CONTEXT").format(
            active_roster=active_listed,
            archived_roster=archived_listed,
        )

    def _sync_roster_from_store(self) -> dict[str, dict[str, str]]:
        """
        [v0.9d Issue 1] ★ **内存花名册和磁盘对不上 → 以磁盘为准，当场补回来。**

        这就是「前端显示 9 个成员，项目经理却说『还没有任何成员』」的根：

          前端的成员来自 `project_created.members` —— 那是 server 从**磁盘**读的；
          项目经理看到的成员来自 `engine._roster` —— 那是**内存**里的，靠温载填。
          温载只在引擎第一次建出来时跑一次（server._roster_restored 那个一次性闸），
          而且**跑失败了也照样落闸**（读文件抛异常 → 返回 {} → 闸落下 → 永远不再试）。
          于是两条路各说各话：屏幕上有九个人，项目经理眼里一个都没有。

        修法有两道：
          · server 那边：读失败不落闸（下次再试）
          · **这里**：每一轮项目经理开口之前，跟磁盘对一次账。少了谁，当场建出来。
            磁盘上那份是有人一笔一笔写进去的，它才是真相；内存只是个缓存。
            缓存和真相不一致的时候，永远不要相信缓存。
        """
        if self._store is None or self._restoring:
            return {}
        try:
            full = self._store.load_roster_full(self.project_id)
        except Exception:
            log.exception("[%s] 对账时读不了花名册 —— 这一轮先用内存里的", self.project_id)
            return {}

        # 磁盘花名册是名字/角色/状态的身份真源。每轮对账时顺手刷新名字缓存，
        # 避免一次临时预定名或旧版本错误缓存长期盖住持久身份。
        for aid, row in full.items():
            name = str(row.get("name") or "").strip()
            if name:
                self._names[aid] = name

        missing = {
            aid: row for aid, row in full.items()
            if aid != COORDINATOR
            and row.get("status", "active") == "active"
            and aid not in self._roster
        }
        if not missing:
            return full

        log.warning(
             "[%s] ⚠ 内存花名册少了 %d 人：%s —— 从磁盘补回来"
            + msg("engine.291"),
            self.project_id, len(missing), "、".join(missing),
        )
        for aid, row in missing.items():
            name = row.get("name")
            if name:
                self._names[aid] = name
            self._get_or_create_worker(aid, row.get("role") or msg("engine.106"))
        return full

    def _handoff_ctx(self, agent_id: str) -> str:
        """Return the Coordinator-only handoff context block."""
        if agent_id != COORDINATOR:
            raise ValueError("Worker handoff context belongs to WorkerRuntime")
        d = self.phase_dir()
        rel = self.handoff.rel(d) + "/"
        phase = d.name.split("-", 1)[-1]
        return _engine_block("COORDINATOR_HANDOFF_CONTEXT").format(
            dir=rel,
            phase=phase,
            step=self.handoff.next_step(),
            next_no=int(d.name.split("-", 1)[0]) + 1,
        )

    def _new_reports(self) -> list[Path]:
        """
        [B-2 ②] 磁盘上有没有**还没跟项目经理说过**的报告。

        ★ 第一次调用时，把当时已经存在的报告全部记作「说过了」。
          否则每次重启，项目经理都会被一堆陈年老报告砸一遍——
          这正是 v0.8e #3 那条「每开机补一条『已加入项目』」的同一种错：
          把「有没有通知过」的判据挂在一个每次都会重来的东西上。
        """
        found = self.handoff_reports()
        if self._reports_told is None:
            self._reports_told = {self.handoff_ref(p) for p in found}   # 开机播种：历史报告不再重播
            return []
        fresh = [p for p in found if self.handoff_ref(p) not in self._reports_told]
        self._reports_told.update(self.handoff_ref(p) for p in fresh)
        return fresh

    def _report_notice(self) -> str:
        """有新报告 → 一段贴到项目经理 prompt 上的通知；没有 → 空串。"""
        fresh = self._new_reports()
        if not fresh:
            return ""
        # [v0.12 D · 问题一] 通知里用**名字**称呼交报告的人（文件名里是 id，但名单/对话都用名字）。
        #   这样项目经理读到「林知远交了报告」，跟【当前团队】名单对得上，不会看着 fe_1 犯迷糊，
        #   也不会顺手把 id 报给用户。read_report 仍然用后面那个文件名。
        listed = "\n".join(
             msg("engine.292")
            for p in fresh)
        log.info("[%s] 新报告 %d 份 → 贴进项目经理的 prompt", self.project_id, len(fresh))
        return _new_report_notice(listed)

    def absorb_markers(self, text: str) -> str:
        """Consume only complete framework marker responses/lines; preserve all prose."""
        if not isinstance(text, str):
            return ""
        if _NOTHING_TO_ADD_RX.fullmatch(text):
            return ""

        kept: list[str] = []
        for line in text.splitlines(keepends=True):
            body = line.rstrip("\r\n")
            next_dir = parse_next_dir(body)
            # strip_next_dir(body) is empty only when the whole line is the exact marker.
            if next_dir and strip_next_dir(body) == "":
                self.set_phase(next_dir)
                continue
            # Strip NOTHING_TO_ADD marker lines (model sometimes appends after real content)
            if _NOTHING_TO_ADD_RX.fullmatch(body):
                continue
            kept.append(line)
        return "".join(kept)

    # ── [B-2 ④⑤] 派活：TaskEnvelope → 展示投影 + 审批 + Runtime 入队 ──
    def commit_handoff_step(
        self,
        *,
        target_id: str,
        instruction: str,
        decision: str,
        keyword: str = "",
        phase: str = "",
        background: str = "",
        previous: str = "",
        inputs: str = "",
        acceptance: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Commit one coordinator handoff through the authoritative TaskEnvelope.

        Approved handoffs are content-addressed before the instruction projection is
        written. The Coordinator text is queued unchanged; rejected proposals remain
        approval-only history.
        """
        # WAITING is occupied capacity.  Check it before changing phase or
        # writing any handoff files so a stale approval cannot leave a half-committed
        # instruction/approval pair with no Runtime task behind it.
        if decision == "approved":
            self.completion_store.assert_worker_available(target_id)

        if phase:
            self.set_phase(phase)

        d = self.phase_dir()
        step = self.handoff.next_step()
        kw = keyword_of(keyword or instruction)

        # Canonical predecessor references feed the TaskEnvelope/context pipeline.  The
        # basename is retained only for the legacy human-readable approval hyperlink.
        reports = self.handoff_reports()
        report_path = reports[-1] if reports else None
        report_ref = self.handoff_ref(report_path) if report_path is not None else ""
        report_display_ref = report_path.name if report_path is not None else ""

        ins_path: Path | None = None
        envelope: Any | None = None
        envelope_ref = ""
        rendered = ""

        # Instruction-conditioned knowledge remains a display hint, but matched ids
        # are also committed as auditable extensions rather than existing only in prose.
        matched_ids: list[str] = []
        if decision == "approved" and self._assets is not None:
            try:
                matches = self._assets.match_for_task(
                    self.project_id, self.internal_workspace,
                    f"{instruction}\n{acceptance}\n{notes}",
                )
                matched_ids = [
                    str(item.get("asset_id") or "") for item in matches
                    if item.get("asset_id")
                ]
            except Exception:
                log.debug("[%s] 资产匹配失败（TaskEnvelope 仍可下发）", self.project_id, exc_info=True)

        if decision == "approved":
            ins_path = self.handoff.instruction_path(
                step=step, target=target_id, keyword=kw, phase_dir=d,
            )
            approval_preview = self.handoff.approval_path(step=step, phase_dir=d)
            instruction_ref = self.handoff.rel(ins_path)
            approval_ref = self.handoff.rel(approval_preview)

            # Authoritative order: persist the verbatim TaskEnvelope, then project it.
            envelope, envelope_ref = self._create_task_envelope(
                target_id=target_id,
                title=kw,
                objective=instruction,
                origin_kind="handoff",
                instruction_ref=instruction_ref,
                source_ref=instruction_ref,
                background=background,
                previous=previous,
                inputs_text=inputs,
                acceptance_text=acceptance,
                notes=notes,
                report_refs=((report_ref,) if report_ref else ()),
                decision_refs=(approval_ref,),
                # 用户批准文件是本次任务的稳定、可审计授权引用。
                authorization_ref=approval_ref,
                approval_origin="propose_next:user_approval",
                extensions={
                    "handoff_step": step,
                    "phase_ref": self.handoff.rel(d),
                    "matched_knowledge_asset_ids": matched_ids,
                },
            )
            rendered = instruction
            self.handoff.write_instruction_projection(ins_path, rendered)

        ap_path = self.handoff.write_approval(
            step=step,
            decision=decision,
            target=target_id,
            keyword=kw,
            phase_dir=d,
            instruction_file=ins_path.name if ins_path else "",
            report_ref=report_display_ref,
            instruction_text=instruction,
            task_id=envelope.task_id if envelope else "",
            task_envelope_ref=envelope_ref,
            provenance=(envelope.provenance if envelope else None),
        )

        if decision == "approved" and envelope is not None:
            try:
                self._record_typed_decision(
                    DecisionType.PLAN_APPROVED,
                    actor="user",
                    task_id=envelope.task_id,
                    attempt_id=envelope.attempt_id,
                    reason="handoff plan approved",
                    payload={
                        "approval_ref": self.handoff.rel(ap_path),
                        "step": step,
                        "worker_id": target_id,
                    },
                    provenance=envelope.provenance,
                    idempotency_seed=f"handoff-plan:{self.handoff.rel(ap_path)}",
                )
            except Exception:
                # The approval file and immutable TaskEnvelope are already durable.  This
                # typed decision is an audit projection; letting it abort before
                # TaskEnvelope queueing recreates the exact 'approved but never started' state.
                log.exception(
                     "[%s] PLAN_APPROVED 审计记录失败；继续排入已批准的 TaskEnvelope",
                    self.project_id,
                )

        # Knowledge bookkeeping remains asynchronous and does not become a hidden
        # source of task facts. The TaskEnvelope above is already immutable at this point.
        if self._assets is not None:
            assets = self._assets
            if decision == "approved" and matched_ids:
                ids = list(matched_ids)
                self._enqueue_knowledge_update(
                    lambda: assets.record_matched(
                        self.project_id, self.internal_workspace, step, ids,
                    ),
                    label=f"matched:{step}",
                )
            if decision == "approved":
                self._approved_since_consolidate += 1
                if (
                    self._approved_since_consolidate
                    >= max(1, int(CONFIG.knowledge_consolidate_every_n))
                ):
                    self._schedule_knowledge_consolidate(reason="every_n")
            elif decision == "rejected":
                self._schedule_knowledge_distill(
                    step=step,
                    instruction_path=None,
                    report_path=None,
                    approval_path=ap_path,
                    decision="rejected",
                    metadata={"agent_id": target_id},
                )

        if ins_path is not None:
            self._schedule_knowledge_update(
                ins_path,
                "instruction",
                {
                    "trigger": "propose_next",
                    "step": step,
                    "decision": decision,
                    "agent_id": target_id,
                    "task_id": envelope.task_id if envelope else "",
                    "task_envelope_ref": envelope_ref,
                },
            )
        self._schedule_knowledge_update(
            ap_path,
            "approval",
            {
                "trigger": "propose_next",
                "step": step,
                "decision": decision,
                "agent_id": target_id,
                "task_id": envelope.task_id if envelope else "",
                "task_envelope_ref": envelope_ref,
            },
        )

        if decision != "approved":
            log.info(
                 "[%s] 第 %02d 步未通过（%s）→ 只留审批记录 %s",
                self.project_id, step, decision, ap_path.name,
            )
            return {"step": step, "approval_file": ap_path.name}

        assert ins_path is not None and envelope is not None
        self._worker_step[target_id] = step
        self._worker_keyword[target_id] = kw
        self.inject_task_envelope(envelope)
        try:
            self._record_typed_decision(
                DecisionType.TASK_DISPATCHED,
                actor="coordinator",
                task_id=envelope.task_id,
                attempt_id=envelope.attempt_id,
                reason="handoff task dispatched",
                payload={
                    "worker_id": target_id,
                    "task_envelope_ref": envelope_ref,
                    "step": step,
                },
                provenance=envelope.provenance,
                idempotency_seed=f"handoff-dispatch:{envelope.attempt_id}",
            )
        except Exception:
            # The in-memory TaskEnvelope queue is authoritative at this point.  Keep
            # dispatch live and make the missing audit projection explicit in logs.
            log.exception(
                 "[%s] TASK_DISPATCHED 审计记录失败；Worker 任务已入队，继续启动",
                self.project_id,
            )
        log.info(
             "[%s] 第 %02d 步派给 %s → %s（task=%s）",
            self.project_id, step, target_id, self.handoff.rel(ins_path),
            envelope.task_id,
        )
        return {
            "step": step,
            "task_id": envelope.task_id,
            "attempt_id": envelope.attempt_id,
            "task_envelope_ref": envelope_ref,
            "instruction_file": ins_path.name,
            "instruction_path": self.handoff.rel(ins_path),
            "approval_file": ap_path.name,
            "diagnostics": [],
        }

    # ── 杂 ──
    def _user_address_line(self) -> str:
        """
        屏幕前用户此刻唯一有效的称呼。Coordinator 高注意力块与 Worker
        TaskEnvelope 均从这里现取；空设置不产生占位 prompt 行。
        """
        uname = runtime_settings.user_name(default="")
        if not uname:
            return ""
        return (
             msg("engine.293", uname=uname)
            + msg("engine.294", uname=uname)
        )

    def _coordinator_user_address_block(self) -> str:
        """返回当前称呼，并在改名后的下一轮追加一次性旧称对冲。"""
        current = runtime_settings.user_name(default="")
        previous = self._last_user_address_name
        self._last_user_address_name = current
        line = self._user_address_line()
        if previous is None or previous == current:
            return line
        if current:
            rename = (
                 msg("engine.295",
                            previous=previous or msg("engine.295.fb"), current=current)
                + msg("engine.296", current=current)
            )
        else:
            rename = (
                 msg("engine.297", previous=previous)
                + msg("engine.298")
            )
        return f"{line}\n{rename}" if line else rename







# ═══════════════════════════════════════════════════════════════
# [v0.8c #7] tool_calls 的「有去无回」——以及怎么把它补上
#
# 现场：用户拒绝了项目经理的卡 → 冒出一条没有头像的报错
#   「400: An assistant message with 'tool_calls' must be followed by tool messages」
#   —— 而且从此**每说一句话都是这条报错**，项目经理彻底哑了。
#
# 怎么来的：
#   1. 项目经理说「我要组队」→ 模型吐出一条 assistant 消息，**带着 tool_calls**
#      （propose_agents）。这条消息当场就进了他的历史。
#   2. 工具处理器 await gate.propose(...) → 卡挂在屏幕上等人点头。
#   3. 用户点「拒绝」→ 处理器里调 engine.on_proposal_rejected() →
#      **coord.interrupt()** —— 它打断的正是「此刻还停在这个工具调用里」的那个循环。
#   4. 于是那条 tool 回执**永远没被写进历史**。历史里留下一条孤零零的 tool_calls。
#   5. 下一轮把这段历史原样发给 DeepSeek → 它按 OpenAI 的规矩校验：
#      带 tool_calls 的 assistant 消息后面必须紧跟对应的 tool 消息 → 400。
#      而报错不会修复历史，所以**每一轮都 400**，永远出不来。
#
# 怎么修：把那条回执**补上**。不是删掉 assistant 消息——删了，模型就不知道自己
# 提过这件事，会原地再提一遍；补一条「aborted」的回执，它才知道「我提了，被否了」。
#
# 为什么写得这么防御：`knowe_core` 在禁改清单里，我手上也没有它的源码——
# 消息历史挂在哪个属性上只能试。找不到就**大声说找不到**，不装作修好了。
# ═══════════════════════════════════════════════════════════════

#: [v0.9b Bug2] 学到的属性名（"messages" 或 "loop.messages" 这种）。
#: 一旦从回合结果里认出来，全进程通用——KnoweAgent 只有一个类。
_LEARNED_HISTORY_ATTR: str | None = None

#: 这些绝对不是 Coordinator 消息历史，扫描时跳过。
_NOT_HISTORY = frozenset({"tools", "schemas", "tool_schemas", "artifacts"})


def _is_history(val: Any) -> bool:
    """
    像不像一条消息历史：**至少有一条带 role 的 dict**。

    ★ [v0.9b Bug2] 这一条是这次修复的核心。
      老版本的判据是「是个 list，且第一项是 dict（或者干脆是空 list）」——
      于是只要 agent 上碰巧有一个叫 `messages` 的**空列表**，它就会被当成历史返回。
      repair 在这条空列表上跑，补 0 条，返回 0 —— 一切看起来正常，
      **而那条真正坏掉的历史根本没被碰过**，下一轮照样 400。
      「找不到就大声喊」的那条错误日志，因此**永远不会触发**。
      装作修好了，比不修更糟——这次栽的就是这个跟头。
    """
    return isinstance(val, list) and any(
        isinstance(m, dict) and "role" in m for m in val)


def _dig(agent: Any, dotted: str) -> Any:
    obj: Any = agent
    for part in dotted.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _chat_text(response: Any) -> str:
    """OpenAI 兼容 chat 响应里取文本（choices[0].message.content）。"""
    try:
        choices = response.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            return str(message.get("content") or "").strip()
    except Exception:
        pass
    return ""


def _agent_messages(agent: Any) -> list[dict[str, Any]] | None:
    """
    把 agent 内部那条消息历史（list[dict]）找出来。找不到 → None。

    四道路，从可靠到将就：
      1. **学到的属性名**（回合跑完时用 result["messages"] 的**身份**反查出来的，见 learn_history_attr）
      2. 名字探测（messages / history / …，但必须**像**消息历史）
      3. `__dict__` 全扫：挑出「带 role 的 dict 最多」的那个 list（排除 schema 等列表）
      4. 实在找不到 —— 返回 None，让上面大声喊
    """
    global _LEARNED_HISTORY_ATTR

    # 1) 学到的
    if _LEARNED_HISTORY_ATTR:
        val = _dig(agent, _LEARNED_HISTORY_ATTR)
        if isinstance(val, list):
            return val                          # type: ignore[return-value]

    # 2) 名字探测
    for name in _HISTORY_ATTRS:
        val = getattr(agent, name, None)
        if _is_history(val):
            return val                          # type: ignore[return-value]
    for holder in _HISTORY_HOLDERS:
        inner = getattr(agent, holder, None)
        if inner is None:
            continue
        for name in _HISTORY_ATTRS:
            val = getattr(inner, name, None)
            if _is_history(val):
                return val                      # type: ignore[return-value]

    # 3) __dict__ 全扫（连内层对象也扫一层）
    best: list[dict[str, Any]] | None = None
    best_score = 0
    for holder_obj in (agent, *[getattr(agent, h, None) for h in _HISTORY_HOLDERS]):
        if holder_obj is None or not hasattr(holder_obj, "__dict__"):
            continue
        for name, val in vars(holder_obj).items():
            if name in _NOT_HISTORY or not _is_history(val):
                continue
            score = sum(1 for m in val if isinstance(m, dict) and "role" in m)
            if score > best_score:
                best, best_score = val, score
    if best is not None:
        return best

    # 4) 还剩一种可能：历史是**空的**（agent 刚建出来，还没说过话）。
    #    这时候本来也没什么好修的 —— 返回那个空 list，别报错吓人。
    for name in _HISTORY_ATTRS:
        val = getattr(agent, name, None)
        if isinstance(val, list) and not val:
            return val                          # type: ignore[return-value]
    return None


def learn_history_attr(agent: Any, result: Any) -> None:
    """
    [v0.9b Bug2] ★ 从回合结果里**认出**历史挂在哪个属性上——不再靠猜。

    `run_conversation()` 回来的 dict 里带着 `messages`（AgentLoopResult.messages）。
    如果 agent 身上某个属性**就是那个对象**（is，不是 ==），那它就是历史本体：
    改它 = 改 agent 的记忆。认出来一次，全进程受用。

    认不出来说明 KnoweAgent 存的是一份**拷贝** —— 那也得知道，
    因为那意味着「在引擎这一侧修历史」这条路根本走不通，得换招（见 _panic_reset）。
    """
    global _LEARNED_HISTORY_ATTR
    if _LEARNED_HISTORY_ATTR or not isinstance(result, dict):
        return
    msgs = result.get("messages")
    if not isinstance(msgs, list):
        return

    for holder in (None, *_HISTORY_HOLDERS):
        obj = agent if holder is None else getattr(agent, holder, None)
        if obj is None or not hasattr(obj, "__dict__"):
            continue
        for name, val in vars(obj).items():
            if val is msgs:                     # ★ 身份相同，不是内容相同
                _LEARNED_HISTORY_ATTR = name if holder is None else f"{holder}.{name}"
                log.info("[history] 认出消息历史挂在 agent.%s 上（%d 条）",
                         _LEARNED_HISTORY_ATTR, len(msgs))
                return

    log.warning(
         "[history] run_conversation 返回的 messages 不是 agent 身上任何一个属性 —— "
        + msg("engine.299")
        + msg("engine.300"),
         ", ".join(sorted(vars(agent))) if hasattr(agent, "__dict__") else "?",
    )


def _repair_tool_calls(msgs: list[dict[str, Any]]) -> int:
    """
    给每一条「悬空的 tool_call」补一条回执。返回补了几条。

    规矩（OpenAI / DeepSeek 一样）：assistant.tool_calls 里的每一个 id，
    后面都必须有一条 role=tool、tool_call_id=它 的消息。少一个都不行。
    """
    fixed = 0
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(msgs):
        m = msgs[i]
        out.append(m)
        i += 1

        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        calls = m.get("tool_calls")
        if not calls:
            continue

        ids = [c.get("id") for c in calls
               if isinstance(c, dict) and isinstance(c.get("id"), str)]
        if not ids:
            continue

        # 紧随其后的那一串 tool 消息，先原样搬过去，顺便记下它们回的是哪个 id
        answered: set[str] = set()
        while i < len(msgs) and isinstance(msgs[i], dict) and msgs[i].get("role") == "tool":
            out.append(msgs[i])
            tid = msgs[i].get("tool_call_id")
            if isinstance(tid, str):
                answered.add(tid)
            i += 1

        for tid in ids:
            if tid in answered:
                continue
            out.append({"role": "tool", "tool_call_id": tid,
                        "content": TOOL_ABORTED_RESULT})
            fixed += 1

    if fixed:
        msgs[:] = out                        # 原地改：调用方持有的还是同一个 list
    return fixed


def _attr_dump(agent: Any) -> str:
    """把 agent 身上的属性名 + 类型列出来（诊断用；日志里要看得懂）。"""
    if not hasattr(agent, "__dict__"):
        return msg("engine.301")
    bits = []
    for name, val in sorted(vars(agent).items()):
        if isinstance(val, list):
            bits.append(f"{name}: list[{len(val)}]")
        else:
            bits.append(f"{name}: {type(val).__name__}")
    return "、".join(bits)


def _supported_run_kwargs(agent: Any, **candidates: Any) -> dict[str, Any]:
    """只把适配层声明支持的每回合选项传给 ``run_conversation``。

    v0.47 的 KnoweAgent 支持轻量 prompt、临时历史、每回合 registry 和空转守卫；
    但 Harness 的测试替身 / 第三方 AgentPort 可能仍只有旧签名。过滤未知关键字可保证
    本轮优化不会把原有 fake、mock 或旧适配层直接撞成 ``unexpected keyword``。
    声明了 ``**kwargs`` 的实现视为已选择接收全部能力开关。
    """
    run = getattr(agent, "run_conversation", None)
    if not callable(run):
        return {}
    try:
        signature = inspect.signature(run)
    except (TypeError, ValueError):
        # 无法反射的原生/动态 callable：宁可退回最老的无关键字 AgentPort。
        return {}
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return dict(candidates)
    supported = signature.parameters
    return {name: value for name, value in candidates.items() if name in supported}


def _is_tool_call_400(err: str) -> bool:
    """这条错误是不是「assistant 带 tool_calls 却没跟 tool 消息」那个 400。"""
    low = err.lower()
    return "tool_call" in low and ("400" in low or "must be followed" in low
                                   or "tool messages" in low)


def _owner_of(filename: str) -> str:
    """`report-03-fe_1-用户认证.md` → `fe_1`。抠不出来就返回文件名本身。"""
    parts = filename.split("-")
    return parts[2] if len(parts) >= 4 else filename


def _safe(pid: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in pid)[:120] or "_"


def _atomic_text(path: Path, text: str) -> None:
    """[v0.12 D] 原子写一个文本文件：先写 .tmp 再 replace，半截写坏的不会被读到。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, "utf-8")
    tmp.replace(path)


__all__ = ["ProjectEngine", "build_agent", "harness_mode",
           "COORDINATOR", "COORDINATOR_ROLE", "REPORT_NOTICE", "WorkspaceUnavailable"]
