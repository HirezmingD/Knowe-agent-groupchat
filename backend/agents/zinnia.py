# [v1.0.13][R1][R2] Readiness-safe binding resolution and shared Zinnia identity contract.
"""
zinnia.py — 知知（Zinnia），Knowe 的接待。

她是**平台级**的：全局只有一位，不属于任何项目，住在一个特殊的会话
`__platform__` 里。用户打开软件，第一个看见的不是一片空白，是她。

她只做一件事：**陪你把「想做什么」聊清楚，然后把项目开出来。**
不组队、不派活、不碰项目里的任何事务——那是项目经理的活。项目一开，她就退场。

除建项目外，她还能只读地搜索互联网、提取网页正文，并用独立的无头浏览器会话
打开/查看/点击/滚动公开网页。联网能力只服务于平台接待和外部事实核对，不能拿来
代替项目团队执行代码、改文件或处理项目内事务。

[v0.5] 建群现在**走审批卡**了（v0.4 那次因为契约里没有第三种卡形状，只能在对话里口头确认）。
   契约已经补上：contract.py 的 _check_card 和 envelope.ts 都认识「建群卡」了
   （card 里带 project_name）。所以知知调 create_project → gate.propose 弹卡 →
   你在卡上**还能把项目名改掉** → 点确认，项目才建出来。

   知知只管提议，建不建、叫什么名字，屏幕前的人说了算。

[v0.7 A0] 建群卡上多了一样东西：**项目目录**。

   一个项目得有个落脚的地方——团队读的、写的、交的报告，全都在那个目录里。
   知知**不填这个字段**，一个字也不填：她是个模型，没资格替用户在人家的磁盘上
   指一个地方。目录由屏幕前的人在卡上按「选择目录」挑（前端 DirectoryPicker），
   点确认时随 create_project 指令一起发给后端，后端拿它当 workspace_root。

   所以这里的 card_body 仍然只有 project_name —— 卡是知知提的，目录是人选的。
   （用户没在卡上选目录也不会卡住：后端会退回默认的 data/workspaces/{project_id}/。）

铁律照旧：**任何异常都变 error 事件，引擎不倒。**
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from knowe_core.provider_client import ProviderClient, build_http_timeout
from knowe_core.provider_identity import provider_target
from knowe_core.stream_assembler import StreamAssembler
from knowe_core.tool_protocol import decode_text_tool_protocol

from ..context_compressor import project_messages

from ..config import CONFIG
from ..attachments import inject_into_last_user
from .. import browser_tools, web_tools
from .. import runtime_settings   # [v0.44.3 场景1] 知知的模型绑定改从设置面板现取
from ..agent_identity import zinnia_identity
from ..feature_flags import FeatureFlag, enabled as feature_enabled
from ..agent_runtime import ToolError, runtime_for
from ..gate import ApprovalCancelled, Gate
from .base import Emit, Turn
from .deepseek import ToolArgError
from ..i18n_backend import msg

log = logging.getLogger("knowe.zinnia")

ZINNIA = "zinnia"

#: 知知住的那个特殊会话——它不是项目，前端把它当作左栏顶上那个固定入口
PLATFORM_PROJECT_ID = "__platform__"

PUBLIC_REPLY_TOOL = "reply_to_user"

# SYSTEM_PROMPT 已外置 → prompts/<lang>/zinnia_system_prompt.md（见 _read_prompt_file）
#: [v0.21 问题三] 工具轮数用完时，塞给模型的最后一句话。
#:
#: 老代码在这里直接 emit 一条 error：「知知的工具调用超过 4 轮，已中止（防止无限循环）」。
#: 那句话有三个毛病，每一个都够呛：
#:   ① 它是**开发者的内部日志**，却端到了用户脸上。用户不知道什么是「工具调用轮次」，
#:      他只知道自己问了个问题，软件报了个错。
#:   ② 它把知知**已经查到的东西全扔了**——她可能第 3 轮就摸到答案了，只是还想再确认一下。
#:   ③ 它让一次「用错了工具」变成了一次**故障**。而这本来只是「她该早点说人话」。
#:
#: 所以现在不中止了：再跑一轮，但只保留 reply_to_user 这一条公开出口；搜索、浏览、建群
#: 等动作工具全部收走。她只能把现有信息整理成人话，且正文仍走类型化通道。
# WRAP_UP_NUDGE 已外置 → prompts/<lang>/zinnia_protocol_nudges.md（见 _read_nudge）
# PROTOCOL_RETRY_NUDGE 已外置 → prompts/<lang>/zinnia_protocol_nudges.md（见 _read_nudge）
#: 类型化收口没有得到 ``reply_to_user`` 时，最后再给模型一次纯自然语言出口。
#: 这是收口专用例外；正常四轮仍由 ``require_typed_output`` 严格把关。
PLAIN_WRAP_UP_NUDGE = """(System) Please answer the user briefly in natural language, without calling any tools.
Output only the final answer meant to be shown to the user; do not output tool-call JSON, XML, or any protocol markers."""

#: 即使协议解析器把残缺控制帧判断成普通文本，下面这些已知边界标记也不得公开。
#: 大小写、空白和闭合标签都一并拦截；命中后继续使用本地兜底。
WRAP_UP_PROTOCOL_MARKER_RE = re.compile(
    r"<\s*/?\s*(?:(?:tool|function)[\s_-]*calls?|invoke)\b|"
    r"<\|[^|>\r\n]{0,120}(?:tool|function|invoke)[^|>\r\n]{0,120}(?:\|>|$)|"
    r"<｜[^｜\r\n]{0,120}(?:tool|function|invoke)[^｜\r\n]{0,120}(?:｜>|$)|"
    r"\[\s*(?:(?:tool|function)[\s_-]*calls?|invoke)\s*\]",
    re.IGNORECASE,
)

#: 连兜底那一轮都没吐出字来时说的话。仍然是**知知在说话**，不是一个错误弹窗。
WRAP_UP_FALLBACK = (
    msg("zinnia.001")
+     msg("zinnia.002")
+     msg("zinnia.003")
)

#: [v0.12 D 5a/5e] 每轮拼给知知的动态上下文——平台动态（公告栏极简版）+ 平台信息。
#:   这就是「基础上下文默认注入」的落点：她因此天生知道现状，而不是等用户催她去查。
CONTEXT_TEMPLATE = """\
──────── Here is the current platform situation (auto-updated by the system, for your reference at any time; mention it only when the user asks, don't show off) ────────

〈Platform Updates〉Current projects and progress:
{harness}

〈Platform Info〉About Knowe itself:
{platform}
{capabilities}
────────────────────────────────────────────────────────────"""

def _build_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "create_project",
                "description": (
                    msg("zinnia.004")
    +                 msg("zinnia.005")
    +                 msg("zinnia.006")
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": msg("zinnia.007"),
                        },
                    },
                    "required": ["project_name"],
                },
            },
        },
        {
            # [v0.12 D 5a] 手动刷新一下平台动态（一般不用——〈平台动态〉每轮已在上下文里）。
            #   保留它只是为了「用户明确要求重新核对」时有个显式动作，日常不该主动调。
            "type": "function",
            "function": {
                "name": "read_harness_memory",
                "description": (
                    msg("zinnia.008")
    +                 msg("zinnia.009")
    +                 msg("zinnia.010")
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            # [v0.12 D 5b] 只读：读某个文件的内容。绝不写、不删、不改。
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    msg("zinnia.011")
    +                 msg("zinnia.012")
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": msg("zinnia.013")},
                        "start_line": {"type": "integer", "minimum": 1, "description": msg("zinnia.014")},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "description": msg("zinnia.015")},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
        {
            # [v0.12 D 5b] 只读：列出某个目录里有什么。
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": (
                    msg("zinnia.016")
    +                 msg("zinnia.017")
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": msg("zinnia.018")},
                        "offset": {"type": "integer", "minimum": 0, "description": msg("zinnia.019")},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "description": msg("zinnia.020")},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    msg("zinnia.021")
    +                 msg("zinnia.022")
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": msg("zinnia.023")},
                        "limit": {"type": "integer", "description": msg("zinnia.024")},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_extract",
                "description": (
                    msg("zinnia.025")
    +                 msg("zinnia.026")
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "urls": {
                            "description": msg("zinnia.027"),
                            "anyOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ],
                        },
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "text", "html"],
                            "description": msg("zinnia.028"),
                        },
                        "offset": {"type": "integer", "minimum": 0, "description": msg("zinnia.029")},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20000, "description": msg("zinnia.030")},
                    },
                    "required": ["urls"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_navigate",
                "description": (
                    msg("zinnia.031")
    +                 msg("zinnia.032")
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": msg("zinnia.033")},
                        "wait_until": {
                            "type": "string",
                            "enum": ["domcontentloaded", "load", "networkidle"],
                            "description": msg("zinnia.034"),
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_snapshot",
                "description": (
                    msg("zinnia.035")
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "offset": {"type": "integer", "minimum": 0, "description": msg("zinnia.036")},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 250, "description": msg("zinnia.037")},
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_click",
                "description": msg("zinnia.038"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "description": msg("zinnia.039")},
                    },
                    "required": ["ref"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_scroll",
                "description": msg("zinnia.040"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {"type": "string", "enum": ["up", "down"], "description": msg("zinnia.041")},
                        "amount": {"type": "integer", "description": msg("zinnia.042")},
                    },
                    "required": ["direction"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_close",
                "description": msg("zinnia.043"),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": PUBLIC_REPLY_TOOL,
                "description": (
                    msg("zinnia.044")
    +                 msg("zinnia.045")
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": msg("zinnia.046"),
                        },
                    },
                    "required": ["content"],
                    "additionalProperties": False,
                },
            },
        },
    ]

# ``delta.content`` 不再在这里做关键词擦除。工具回合统一交给
# knowe_core.StreamAssembler 的完整回合协议闸门；知知的公开回答则只能从
# reply_to_user 的类型化参数进入聊天通道。




# [v1.0.21.3] 工具 schema 按语言构建（msg 在构建时求值 → 语言切换后重建）
_tools_cache: dict[str, list[dict[str, Any]]] = {}


def _schemas(*, allow_tools: bool = True) -> list[dict[str, Any]]:
    """按当前语言返回工具 schema；收口轮只开放 reply_to_user。"""
    lang = runtime_settings.language() or "zh"
    lang = lang if lang in ("zh", "en") else "zh"
    if lang not in _tools_cache:
        _tools_cache[lang] = _build_tools()
    tools = _tools_cache[lang]
    if allow_tools:
        return tools
    return [
        item for item in tools
        if str((item.get("function") or {}).get("name") or "") == PUBLIC_REPLY_TOOL
    ]


class ZinniaProtocolError(RuntimeError):
    """Provider output could not be classified without risking a protocol leak."""


#: server 注入：真正把项目建出来（建 hub 里的项目 + 起引擎 + 广播 project_created）
#: [v0.7 A0] 第二参是可选的项目目录（知知不填，留给测试/兜底路径用）
CreateProject = Callable[..., Awaitable[tuple[str, str]]]



# ── [v1.0.21.3] 提示词外置：代码零中文，按当前语言读 prompts/<lang>/ ──
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _read_prompt_file(name: str) -> str:
    """按当前语言读 prompts/<lang>/<name>；缺失回退 prompts/zh/<name>；再缺失返回空串。"""
    lang = runtime_settings.language() or "zh"
    lang = lang if lang in ("zh", "en") else "zh"
    for cand in (_PROMPTS_DIR / lang / name, _PROMPTS_DIR / "zh" / name):
        try:
            return cand.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def _read_nudge(name: str) -> str:
    """从 zinnia_protocol_nudges.md 按 <!-- NAME --> 标记取对应段落。"""
    text = _read_prompt_file("zinnia_protocol_nudges.md")
    m = re.search(rf"<!-- {name} -->\s*(.*?)(?=<!-- |\Z)", text, re.S)
    return m.group(1).strip() if m else ""


class ZinniaAgent:
    """接待。驱动是真 LLM；没配 key 时会说清楚，而不是装死。"""

    def __init__(
        self,
        create_project: CreateProject | None = None,
        client_factory: Callable[[], Any] | None = None,
        read_harness: Callable[[], str] | None = None,
        harness_brief: Callable[[], str] | None = None,
        platform_brief: Callable[[], str] | None = None,
        read_file: Callable[..., str] | None = None,
        list_dir: Callable[..., str] | None = None,
        usage_sink: Callable[[dict[str, Any]], None] | None = None,
        memory_sink: Callable[[str], None] | None = None,
        memory_brief: Callable[[], str] | None = None,
    ) -> None:
        # [v0.5] 建群改由 server 在审批通过时执行（用户可能在卡上改了名字）。
        #        这个回调留着只为向后兼容，知知自己不再调它。
        self.create_project = create_project
        self._client_factory = client_factory
        # [v0.11 C-1] 读全局公告栏全文（read_harness_memory 工具用）。
        self._read_harness = read_harness
        # [v0.12 D 5a/5e] 默认注入用的极简上下文提供者。
        self._harness_brief = harness_brief
        self._platform_brief = platform_brief
        # [v0.12 D 5b] 只读文件能力（server 注入的只读函数）。
        self._read_file = read_file
        self._list_dir = list_dir
        # [M1 采集点 C] 知知 token 用量回调（server 注入，落盘到平台项目 ledger）。
        self._usage_sink = usage_sink
        # [v1.0.22.1-对齐 B] 平台级对话记忆回调（server 注入，沉淀知知频道对话）。
        self._memory_sink = memory_sink
        # [v1.0.22.1-对齐 B] 最近平台记忆的读通道（server 注入 read_platform_memory_brief）。
        self._memory_brief = memory_brief
        # [v0.44.3 场景1] ★ 这三个不再是「构造期定死」的值，而是**每回合现取**的缓存位：
        #   老写法在 __init__ 里读 CONFIG.deepseek_*，v0.44.1 把硬编码默认清空后，
        #   知知拿到的 base_url 就是空串 → 发出去的是相对 URL → 「missing http://」。
        #   而且构造期缓存意味着用户在设置面板配好模型也传不进来（知知是 server 启动时
        #   建的常驻实例，不像项目 Agent 会被退休重建）。现在 _run 每回合开头调
        #   _resolve_binding() 现取生效绑定，设置一改、下一句话就用新模型，无需重启。
        self.api_key = CONFIG.deepseek_api_key
        self.model = CONFIG.deepseek_model
        self.base_url = CONFIG.deepseek_base_url.rstrip("/")
        self.provider = "deepseek" if (self.api_key and self.base_url) else ""
        #: 绑定解析失败时给用户的人话（None = 可用）。
        self._binding_issue: str | None = None

    # ── 平台级联网资源 ──

    @staticmethod
    def _tool_args(raw: Any) -> dict[str, Any]:
        """把 function-call 参数收敛成对象；错误直接回给模型自行修正。"""
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            raise ToolArgError(msg("zinnia.047", exc=exc)) from None
        if not isinstance(args, dict):
            raise ToolArgError(msg("zinnia.048"))
        return args

    @staticmethod
    def _json_result(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)

    def _browser_pool(self) -> browser_tools.BrowserPool:
        """
        知知不属于任何业务项目，但仍挂在 ``__platform__`` 的 ProjectRuntime 上。

        BrowserPool 内部全进程共享一个 Chromium；这里的 ``zinnia`` session 会拿到
        自己独立的 BrowserContext（cookie/登录态不与项目 Agent 串），平台引擎停止时
        runtime 会统一关门，空闲回收则负责长期无人使用的场景。
        """
        return runtime_for(PLATFORM_PROJECT_ID).slot(
            "browser",
            lambda: browser_tools.BrowserPool(
                PLATFORM_PROJECT_ID,
                headless=CONFIG.browser_headless,
                timeout_s=CONFIG.browser_timeout_s,
                idle_s=CONFIG.browser_idle_s,
                max_sessions=CONFIG.browser_max_sessions,
                snapshot_max=CONFIG.browser_snapshot_max,
            ),
        )

    async def _browser_session(self) -> browser_tools.Session:
        return await self._browser_pool().session(ZINNIA)

    async def _browser_snapshot(
        self,
        session: browser_tools.Session,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> dict[str, Any]:
        page_offset = max(0, int(offset))
        page_limit = max(1, min(250, int(limit or CONFIG.browser_snapshot_max)))
        raw = await browser_tools.snapshot(
            session,
            offset=page_offset,
            limit=page_limit,
            text_offset=page_offset,
        )
        element_end = int(raw.get("element_end") or page_offset)
        body_end = int(raw.get("body_text_end") or page_offset)
        element_has_more = bool(raw.get("element_has_more"))
        body_has_more = bool(raw.get("body_text_has_more"))
        source_ref = {
            "type": "browser_page",
            "url": str(raw.get("url") or ""),
            "session": ZINNIA,
            "element_range": [page_offset, element_end],
            "body_text_range": [int(raw.get("body_text_offset") or 0), body_end],
        }
        payload = {
            **raw,
            "offset": page_offset,
            "limit": page_limit,
            "source_ref": source_ref,
            "truncated": element_has_more or body_has_more,
        }
        continuations: dict[str, str] = {}
        if element_has_more:
            continuations["elements"] = (
                f"browser_snapshot(offset={element_end}, limit={page_limit})"
            )
        if body_has_more:
            continuations["body_text"] = (
                f"browser_snapshot(offset={body_end}, limit={page_limit})"
            )
        if continuations:
            payload["continuations"] = continuations
            payload["continuation"] = (
                continuations.get("elements") or continuations["body_text"]
            )
            payload["next_offset"] = element_end if element_has_more else body_end
        return payload

    # ── [v0.44.3 场景1] 模型绑定：每回合现取（设置面板是唯一权威） ──
    # [v1.0.38 B] 知知开放 anthropic——用户配什么模型就能用什么（共进退，不因协议绕开）。
    # ProviderClient 已支持 anthropic_messages 传输，知知主模型为 anthropic 时直接用。
    _OPENAI_COMPAT = ("openai_chat", "codex_responses", "anthropic_messages")

    def _resolve_binding(self) -> bool:
        """
        解析知知这一回合该用的模型，写入 self.api_key/model/base_url。

        解析顺序（知知通过共享 ProviderClient 使用统一的 transport 传输，含 anthropic）：
         ① 知知的个性化绑定（"__platform__::zinnia"，通常没有） > 全局主模型
            ——transport 只要受支持（openai_chat / codex_responses / anthropic_messages）即可；
         ② 都不行 → 遗留 .env（DEEPSEEK_*，齐全才算）；
         ③ 还不行 → False + self._binding_issue 说明该怎么配。

        返回 True = 绑定可用。
        """
        self._binding_issue = None

        # An explicitly injected client factory is an authoritative test/embedding
        # boundary: it must not be disabled merely because the host has no persisted
        # provider binding.  Use a synthetic absolute origin so MockTransport/custom
        # clients still receive valid URLs without creating a hidden production
        # provider fallback.
        if self._client_factory is not None and self.api_key:
            self.base_url = (self.base_url or "http://provider.invalid").rstrip("/")
            self.model = self.model or "injected-model"
            self.provider = self.provider or "injected"
            return True

        def _usable(b: dict[str, str] | None) -> bool:
            return bool(b and b.get("api_key") and b.get("base_url")
                        and b.get("transport", "openai_chat") in self._OPENAI_COMPAT)

        if feature_enabled(FeatureFlag.MODEL_READINESS_GATE_V1):
            ready, source, effective = runtime_settings.zinnia_binding_status()
            if ready and effective is not None:
                self.api_key = effective["api_key"]
                self.model = effective["model"]
                self.base_url = effective["base_url"].rstrip("/")
                self.provider = effective["provider"]
                return True
            self._binding_issue = {
                "unverified": msg("zinnia.049"),
                "incompatible": msg("zinnia.050"),
                "missing": msg("zinnia.051"),
            }.get(source, msg("zinnia.052"))
            return False

        main = runtime_settings.model_binding_for(PLATFORM_PROJECT_ID, ZINNIA)
        if _usable(main):
            assert main is not None
            self.api_key = main["api_key"]
            self.model = main["model"]
            self.base_url = main["base_url"].rstrip("/")
            self.provider = main["provider"]
            return True

        aux = runtime_settings.aux_effective()
        if main and not _usable(main) and _usable(aux):
            # 主模型是 Anthropic 协议（知知说不了）→ 辅助模型是 OpenAI 兼容 → 用它。
            assert aux is not None
            self.api_key = aux["api_key"]
            self.model = aux["model"]
            self.base_url = aux["base_url"].rstrip("/")
            self.provider = aux["provider"]
            return True

        # 只有用户**从未建立显式主模型绑定**时，才兼容遗留 DEEPSEEK_*。
        # 一旦设置面板里已有 provider/model，哪怕绑定残缺，也不能偷偷打回旧厂商。
        if main is None and CONFIG.deepseek_api_key and CONFIG.deepseek_base_url:
            # 遗留 .env 档：key 和接入点都齐才算可用（v0.44.2 Bug1 同款判据）。
            self.api_key = CONFIG.deepseek_api_key
            self.model = CONFIG.deepseek_model
            self.base_url = CONFIG.deepseek_base_url.rstrip("/")
            self.provider = "deepseek"
            return True

        if main and not _usable(main):
            if not main.get("api_key") or not main.get("base_url"):
                self._binding_issue = (
                    f"{provider_target(main.get('provider'), main.get('base_url'), main.get('model'))} "
                    + msg("zinnia.053")
+                     msg("zinnia.054")
                )
            else:
                self._binding_issue = (
                    msg("zinnia.055")
+                     msg("zinnia.056")
+                     msg("zinnia.057")
                )
        else:
            self._binding_issue = (
                msg("zinnia.058")
+                 msg("zinnia.059")
            )
        return False

    def _context_block(self) -> str:
        """[v0.12 D 5a/5e] 把平台动态 + 平台信息拼成一段，注进每轮的 system 上下文。"""
        harness = msg("zinnia.060")
        platform = msg("zinnia.061")
        if self._harness_brief is not None:
            try:
                harness = (self._harness_brief() or "").strip() or harness
            except Exception:
                log.warning("读平台动态失败（忽略）")
        if self._platform_brief is not None:
            try:
                platform = (self._platform_brief() or "").strip() or platform
            except Exception:
                log.warning("读平台信息失败（忽略）")
        parts = [self._user_block(), CONTEXT_TEMPLATE.format(
            harness=harness, platform=platform, capabilities=self._capabilities_block(),
        )]
        # [v1.0.22.1-对齐 B] 最近几条平台记忆（知知频道沉淀）附在末尾，她每轮天然知情。
        try:
            recent = self._platform_memory_brief()
            if recent:
                parts.append(recent)
        except Exception:
            log.warning("读平台记忆失败（忽略）")
        return "\n\n".join(part for part in parts if part)

    def _user_block(self) -> str:
        """[v1.0.22.1-对齐 A] 当前用户称呼，与项目经理/Worker 同源（runtime_settings.user_name）。

        未设置时返回空串，不产生 prompt 行（与 engine._user_address_line 空设置一致）。
        """
        try:
            uname = runtime_settings.user_name(default="").strip()
        except Exception:
            log.warning("读取用户称呼失败（忽略）")
            return ""
        if not uname:
            return ""
        return msg("zinnia.align.user_block", uname=uname)

    def _platform_memory_brief(self) -> str:
        """[v1.0.22.1-对齐 B] 最近几条平台记忆（复用 harness brief 的读通道）。"""
        if self._memory_brief is None:
            return ""
        return (self._memory_brief() or "").strip()

    @staticmethod
    def _capabilities_block() -> str:
        """
        [v0.21 问题三] 〈项目里能做什么〉。

        知知要把用户往项目里送，就得说得出**为什么那边更好**——
        「那边能直接搜代码、跑命令」比「这不归我管」有用一百倍。
        而这份清单跟项目经理看的是**同一个真源**（capabilities 从注册表现生成），
        所以她不会承诺一个项目里其实没有的能力。

        延迟导入 + 兜空串：接待是用户见到的第一个人，她绝不能因为一个
        上下文增强块出岔子而开不了口。
        """
        try:
            from ..capabilities import zinnia_block
            block = zinnia_block()
        except Exception:
            log.warning("生成〈项目里能做什么〉失败（忽略）", exc_info=True)
            return ""
        return ("\n" + block + "\n") if block else ""

    @staticmethod
    async def _emit_idle(emit: Emit) -> None:
        """UI 生命周期收尾不是业务结果；发送失败只能记日志，不能反吞上一条消息。"""
        try:
            await emit({"type": "agent_idle", "agent_id": ZINNIA})
        except Exception:
            log.warning("知知的 agent_idle 发送失败（忽略）", exc_info=True)

    # ── 引擎唯一入口 ──
    async def run_turn(self, turn: Turn, emit: Emit, gate: Gate) -> None:
        idle_sent = False

        async def tracked_emit(event: dict[str, Any]) -> None:
            nonlocal idle_sent
            if event.get("type") == "agent_idle" and event.get("agent_id") == ZINNIA:
                idle_sent = True
            await emit(event)

        try:
            await self._run(turn, tracked_emit, gate)
        except ApprovalCancelled:
            await self._emit_idle(tracked_emit)
            raise                      # 用户发了新消息 → 引擎收摊
        except Exception as exc:       # ★ 铁律：引擎不许因为模型抽风倒下
            log.exception("知知回合异常")
            try:
                await tracked_emit({
                    "type": "error",
                    "agent_id": ZINNIA,
                    "message": msg("event.zinnia.error.generic", exc=exc),
                })
            except Exception:
                log.warning("知知的 error 事件发送失败（忽略）", exc_info=True)
            await self._emit_idle(tracked_emit)
        finally:
            # CancelledError / SystemExit 也会走这里；已在正常出口发过的不会重复。
            if not idle_sent:
                await self._emit_idle(emit)

    async def _run(self, turn: Turn, emit: Emit, gate: Gate) -> None:
        # [v0.44.3 场景1] 每回合先现取生效绑定（设置面板 > 辅助模型兜底 > 遗留 .env）。
        if not self._resolve_binding():
            # [v1.0.13 R1] Missing/unverified configuration is a readiness state, not chat
            # content.  The Engine normally holds the turn at its barrier; this branch is a
            # defensive direct-call fallback and deliberately emits no red error row.
            if not feature_enabled(FeatureFlag.MODEL_READINESS_GATE_V1):
                await emit({
                    "type": "error",
                    "agent_id": ZINNIA,
                    "message": msg("event.zinnia.binding.issue", issue=self._binding_issue),
                })
            await self._emit_idle(emit)
            return

        client = self._make_client()
        if client is None:
            await emit({
                "type": "error",
                "agent_id": ZINNIA,
                "message": msg("event.zinnia.no.model"),
            })
            await self._emit_idle(emit)
            return

        identity_prompt = (
            zinnia_identity().system_block()
            if feature_enabled(FeatureFlag.IDENTITY_CONTRACT_V1)
            else ""
        )
        system_prompt = "\n\n".join(
            part for part in (identity_prompt, _read_prompt_file("zinnia_system_prompt.md"), self._context_block()) if part
        )
        authoritative: list[dict[str, Any]] = [
            dict(message) for message in turn.history if isinstance(message, dict)
        ]
        authoritative.append({"role": "user", "content": turn.content})
        # [v1.0.34] M3 查询感知投影：开关开时传当前用户消息做 BM25 优先保留；
        # 关时不传 query，行为与 v1.0.33 完全一致。
        projected, _ = project_messages(
            authoritative,
            query=turn.content if CONFIG.query_aware else None,
        )
        # [v1.0.19.4] 附件注入：投影后当前回合是尾部 verbatim 的最后一条 user；
        #   把文本+附件块合成多模态数组替换它。历史/权威副本保持纯文本。
        inject_into_last_user(projected, turn.attachments)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *projected,
        ]

        round_index = 0
        while True:
            round_index += 1
            await emit({"type": "agent_thinking", "agent_id": ZINNIA})

            # [v1.0.22.1] _stream 不再抛协议错误：返回 (text, tool_calls) 二元组。
            # 终局语义与 AgentLoop 对齐——循环任何路径必须有终局，绝不无限重试。
            text, tool_calls = await self._stream(client, messages, emit)

            # ① 纯文本 → 直接回复，终局（AgentLoop "plain answer is authoritative"）
            if text and not tool_calls:
                await self._publish_reply(text, emit)
                self._sink_platform_memory(turn, text)
                await self._emit_idle(emit)
                return

            # ② 协议错误（_stream 已降级为空文本）→ 辅助模型人话收尾，终局，不重试
            if not text and not tool_calls:
                wrap_up = await self._wrap_up_line(turn, emit)
                await self._publish_reply(wrap_up, emit)
                self._sink_platform_memory(turn, wrap_up)
                await self._emit_idle(emit)
                return

            # ③ reply_to_user 显式出口（保留兼容；模型想调就调，但不是唯一出口）
            public_reply = self._public_reply(tool_calls)
            if public_reply is not None:
                await self._publish_reply(public_reply, emit)
                self._sink_platform_memory(turn, public_reply)
                await self._emit_idle(emit)
                return

            # ④ 工具调用 → 执行，回填，继续。
            #    [v1.0.22.1] 非法 reply_to_user（校验失败返回 None）按普通工具失败
            #    回填错误信息，与 AgentLoop 的 ToolExecutionError 路径一致，
            #    由模型自行修正措辞，不当故障反复重试。
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls,
            })
            for call in tool_calls:
                if self._call_name(call) == PUBLIC_REPLY_TOOL:
                    result = msg("zinnia.invalid_reply")
                else:
                    result = await self._execute(call, emit, gate)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result,
                })


    @staticmethod
    def _call_name(call: dict[str, Any]) -> str:
        fn = call.get("function") if isinstance(call, dict) else None
        return str(fn.get("name") or "") if isinstance(fn, dict) else ""

    def _public_reply(self, tool_calls: list[dict[str, Any]]) -> str | None:
        reply_calls = [call for call in tool_calls if self._call_name(call) == PUBLIC_REPLY_TOOL]
        if not reply_calls:
            return None
        if len(reply_calls) != 1 or len(tool_calls) != 1:
            # [v1.0.22.1] 校验失败返回 None：由调用方按普通工具失败回填错误信息，
            # 模型自行修正措辞（与 AgentLoop 的 ToolExecutionError 路径一致）。
            return None

        fn = reply_calls[0].get("function") or {}
        try:
            args = self._tool_args(fn.get("arguments") or "{}")
        except ToolArgError as exc:
            return None
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        if len(content) > 200_000:
            return None

        # ``reply_to_user`` 是公开数据出口，不是另一条协议旁路。若模型把一个可执行的
        # 控制帧塞进 content，仍按同一套 schema/grammar 判定并拒绝。
        nested = decode_text_tool_protocol(content, _schemas())
        if nested.kind != "plain":
            return None
        return content.strip()

    @staticmethod
    async def _publish_reply(content: str, emit: Emit) -> None:
        # 这里的 content 已经来自显式的 public-reply 参数，而不是 provider 的
        # delta.content。保留现有事件顺序，但一次性提交，不再让未分类分片触达 UI。
        await emit({"type": "stream_delta", "agent_id": ZINNIA, "content": content})
        await emit({"type": "message", "agent_id": ZINNIA, "content": content})

    async def _wrap_up_line(self, turn: Turn, emit: Emit) -> str:
        """[v1.0.22.1] 协议错误收尾人话。

        优先用辅助模型（便宜档）生成一句自然收尾话；未配置 / 调用失败 / 输出为空
        时用本地文案兜底——保证任何路径都有终局。
        """
        del emit
        aux = runtime_settings.aux_effective()
        if aux and aux.get("api_key") and aux.get("base_url"):
            try:
                client = ProviderClient(
                    base_url=str(aux["base_url"]).rstrip("/"),
                    api_key=aux["api_key"],
                    model=aux.get("model") or "",
                    timeout=build_http_timeout(
                        connect=CONFIG.provider_connect_timeout_s,
                        read=15,
                        write=CONFIG.provider_write_timeout_s,
                        pool=CONFIG.provider_pool_timeout_s,
                    ),
                    max_retries=0,
                    provider=str(aux.get("provider") or ""),
                )
                text = ""
                async for event in client.chat_stream(
                    messages=[{"role": "system", "content": self._wrap_up_prompt(turn)}],
                    tools=None,
                    temperature=None,
                ):
                    if event.get("type") == "delta" and isinstance(event.get("content"), str):
                        text += event["content"]
                if text.strip():
                    return text.strip()[:500]
            except Exception:
                log.warning("知知收尾辅助模型调用失败，使用本地兜底文案", exc_info=True)
        return msg("zinnia.wrapup")

    def _sink_platform_memory(self, turn: Turn, reply: str) -> None:
        """[v1.0.22.1-对齐 B] 回合收尾：把「用户请求 → 知知回应」沉淀到平台记忆。

        尽力而为：sink 内部异常不冒泡，绝不反吞已发布的消息。
        """
        if self._memory_sink is None:
            return
        user_text = (turn.content or "").strip()
        reply_text = (reply or "").strip()
        if not user_text or not reply_text:
            return
        try:
            line = "[{ts}] 用户说「{user}」→ 知知答「{reply}」".format(
                ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                user=user_text[:80],
                reply=reply_text[:80],
            )
            self._memory_sink(line[:400])
        except Exception:
            log.warning("平台记忆沉淀失败（忽略）")

    @staticmethod
    def _wrap_up_prompt(turn: Turn) -> str:
        """收尾助手的提示词：把用户原话带进去，让收尾话接得上上下文。"""
        lang = runtime_settings.language() or "zh"
        lang = lang if lang in ("zh", "en") else "zh"
        user_line = (turn.content or "").strip()[:500]
        if lang == "zh":
            return (
                "你是知知的收尾助手。用户刚才的请求没能处理成功。"
                "请用一句话自然、友好地向用户说明，语气像知知本人。"
                "不要暴露内部错误，不要提协议、模型、工具等技术词。\n"
                f"用户原话：{user_line}\n"
                "只输出这一句收尾话，不要任何多余内容。"
            )
        return (
            "You are Zinnia's wrap-up assistant. The user's request just now could "
            "not be handled successfully. Say one short, natural, friendly closing "
            "line in Zinnia's own voice. Do not expose internal errors; do not "
            "mention protocols, models, or tools.\n"
            f"User said: {user_line}\n"
            "Output only that closing line, nothing else."
        )


    # ── 工具执行：**第一步永远是问人**（v0.5 起走审批卡） ──
    async def _execute(self, call: dict[str, Any], emit: Emit, gate: Gate) -> str:
        del emit
        name = (call.get("function") or {}).get("name", "")
        raw = (call.get("function") or {}).get("arguments") or "{}"

        # [v0.11 C-1] read_harness_memory：只读，不弹卡，直接把公告栏全文回给模型。
        if name == "read_harness_memory":
            if self._read_harness is None:
                return msg("zinnia.068")
            try:
                return self._read_harness() or msg("zinnia.069")
            except Exception:
                return msg("zinnia.070")

        # [v0.12 D 5b] read_file / list_dir：只读文件能力，不弹卡，直接回内容。
        if name in ("read_file", "list_dir"):
            fn = self._read_file if name == "read_file" else self._list_dir
            if fn is None:
                return msg("zinnia.071")
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
                p = args.get("path") if isinstance(args, dict) else None
                if not isinstance(p, str) or not p.strip():
                    return msg("zinnia.072")
                if name == "read_file":
                    try:
                        start_line = max(1, int(args.get("start_line", 1)))
                        limit = max(1, min(5_000, int(args.get("limit", 400))))
                    except (TypeError, ValueError):
                        return msg("zinnia.073")
                    return fn(p, start_line=start_line, limit=limit)
                try:
                    offset = max(0, int(args.get("offset", 0)))
                    limit = max(1, min(2_000, int(args.get("limit", 300))))
                except (TypeError, ValueError):
                    return msg("zinnia.074")
                return fn(p, offset=offset, limit=limit)
            except json.JSONDecodeError as exc:
                return msg("zinnia.075", exc=exc)
            except Exception as exc:
                return msg("zinnia.076", exc=exc)

        # [v0.45] 知知联网：直接复用项目工具的安全实现，不复制/放宽任何 URL 边界。
        if name in {
            "web_search", "web_extract", "browser_navigate", "browser_snapshot",
            "browser_click", "browser_scroll", "browser_close",
        }:
            try:
                args = self._tool_args(raw)

                if name == "web_search":
                    query = args.get("query")
                    if not isinstance(query, str):
                        raise ToolArgError(msg("zinnia.077"))
                    try:
                        limit = int(args.get("limit", 5))
                    except (TypeError, ValueError):
                        raise ToolArgError(msg("zinnia.078")) from None
                    rows = await web_tools.search(
                        query,
                        limit=max(1, min(20, limit)),
                        backend=CONFIG.web_search_backend,
                        searxng_url=CONFIG.searxng_url,
                    )
                    payload: dict[str, Any] = {"results": rows, "count": len(rows)}
                    if not rows:
                        payload["message"] = msg("event.zinnia.search.empty"),
                    else:
                        payload["message"] = msg("event.zinnia.search.summary"),
                    return self._json_result(payload)

                if name == "web_extract":
                    urls = web_tools.normalize_urls(args.get("urls"))
                    fmt = str(args.get("format") or "markdown").lower()
                    if fmt not in ("markdown", "text", "html"):
                        raise ToolArgError(msg("zinnia.079"))
                    try:
                        offset = max(0, int(args.get("offset", 0)))
                        limit = max(1, min(20_000, int(args.get("limit", 12_000))))
                    except (TypeError, ValueError):
                        raise ToolArgError(msg("zinnia.080")) from None
                    fetched = await web_tools.fetch_many(urls, timeout_s=CONFIG.web_timeout_s)
                    pages = [web_tools.render(page, fmt) for page in fetched]
                    rows: list[dict[str, Any]] = []
                    sections: list[str] = []
                    for page in pages:
                        source_ref = {
                            "type": "web_url",
                            "url": page.url,
                            "bytes_downloaded": page.bytes_downloaded,
                        }
                        row: dict[str, Any] = {
                            "url": page.url,
                            "ok": page.ok,
                            "title": page.title,
                            "chars": len(page.content),
                            "bytes_downloaded": page.bytes_downloaded,
                            "source_complete": not page.truncated,
                            "source_ref": source_ref,
                        }
                        if not page.ok:
                            row["error"] = page.error
                            sections.append(f"URL: {page.url}\nERROR: {page.error}")
                        else:
                            sections.append(f"URL: {page.url}\nTitle: {page.title}\n\n{page.content}")
                        if page.truncated:
                            row["truncated"] = True
                            row["truncation_reason"] = "network_download_byte_boundary"
                        rows.append(row)
                    combined = "\n\n---\n\n".join(sections)
                    chunk = combined[offset : offset + limit]
                    end = offset + len(chunk)
                    more = end < len(combined)
                    ok_count = sum(1 for row in rows if row.get("ok"))
                    import hashlib
                    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
                    payload = {
                        "pages": rows,
                        "count": len(rows),
                        "ok_count": ok_count,
                        "content": chunk,
                        "offset": offset,
                        "limit": limit,
                        "returned_characters": len(chunk),
                        "total_characters": len(combined),
                        "content_sha256": digest,
                        "source_ref": {
                            "type": "web_extract_projection",
                            "urls": urls,
                            "format": fmt,
                            "sha256": digest,
                            "range": [offset, end],
                            "total_characters": len(combined),
                        },
                        "truncated": more,
                    }
                    if more:
                        payload["next_offset"] = end
                        payload["continuation"] = (
                            f"web_extract(urls={urls!r}, format={fmt!r}, offset={end}, limit={limit})"
                        )
                    if ok_count == 0:
                        payload["message"] = (
                            msg("zinnia.081")
                        )
                    elif ok_count < len(rows):
                        payload["message"] = msg("event.zinnia.extract.partial"),
                    return self._json_result(payload)

                if name == "browser_navigate":
                    url = browser_tools.check_url(args.get("url"))
                    wait_until = str(args.get("wait_until") or "domcontentloaded")
                    if wait_until not in ("load", "domcontentloaded", "networkidle", "commit"):
                        wait_until = "domcontentloaded"
                    session = await self._browser_session()
                    await browser_tools.act(
                        session,
                        session.page.goto(
                            url,
                            wait_until=wait_until,
                            timeout=int(CONFIG.browser_timeout_s * 1000),
                        ),
                        what=msg("zinnia.082"),
                    )
                    return self._json_result(await self._browser_snapshot(session))

                if name == "browser_snapshot":
                    try:
                        offset = max(0, int(args.get("offset", 0)))
                        limit = max(1, min(250, int(args.get("limit", 120))))
                    except (TypeError, ValueError):
                        raise ToolArgError(msg("zinnia.080")) from None
                    return self._json_result(
                        await self._browser_snapshot(
                            await self._browser_session(), offset=offset, limit=limit
                        )
                    )

                if name == "browser_click":
                    session = await self._browser_session()
                    loc = browser_tools.locator(session, args.get("ref"))
                    await browser_tools.act(
                        session,
                        loc.click(timeout=int(CONFIG.browser_timeout_s * 1000)),
                        what=msg("zinnia.083"),
                    )
                    await session.page.wait_for_timeout(400)
                    return self._json_result(await self._browser_snapshot(session))

                if name == "browser_scroll":
                    direction = str(args.get("direction") or "down").lower()
                    if direction not in ("up", "down"):
                        raise ToolArgError(msg("zinnia.084"))
                    try:
                        amount = int(args.get("amount", 800))
                    except (TypeError, ValueError):
                        raise ToolArgError(msg("zinnia.085")) from None
                    amount = max(0, min(20000, amount)) or 800
                    session = await self._browser_session()
                    await browser_tools.act(
                        session,
                        session.page.mouse.wheel(0, -amount if direction == "up" else amount),
                        what=msg("zinnia.086"),
                    )
                    await session.page.wait_for_timeout(300)
                    return self._json_result(await self._browser_snapshot(session))

                closed = await self._browser_pool().close_session(ZINNIA)
                return self._json_result({
                    "closed": closed,
                    "message": msg("event.zinnia.browser.closed") if closed else msg("event.zinnia.browser.none"),
                })
            except (ToolArgError, ToolError) as exc:
                return f"error: {exc}"
            except Exception as exc:
                log.exception("知知联网工具执行失败：%s", name)
                return f"error: {type(exc).__name__}: {str(exc).splitlines()[0][:300]}"

        if name != "create_project":
            return msg("zinnia.087", name=name)

        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(args, dict):
                raise ToolArgError(msg("zinnia.048"))
            project_name = _clean_name(args.get("project_name"))
        except (json.JSONDecodeError, ToolArgError) as exc:
            return msg("zinnia.075", exc=exc)

        # ★ 弹卡，等人点头。
        #   项目名用户还能在卡上改，目录也由他在卡上选（[v0.7 A0]）——
        #   所以这里不预先建任何东西，card_body 里也不塞目录：知知不猜路径。
        decision = await gate.propose(
            tool="create_project",
            agent_id=ZINNIA,
            card_body={"project_name": project_name},
        )
        if decision != "approved":
            return decision      # rejected / timeout —— 让知知自己圆场

        # 项目由 server 在 approve 的那一刻建出来（用的是用户最终确认的名字，
        # 可能和知知提的不一样）。知知不需要知道 id，她只要知道成了。
        log.info("知知的建群提议获批：%s", project_name)
        return (
            msg("zinnia.088")
+             msg("zinnia.089")
+             msg("zinnia.090")
        )

    async def _stream(
        self, client: ProviderClient, messages: list[dict[str, Any]], emit: Emit,
        *, allow_tools: bool = True,
    ) -> tuple[str, list[dict[str, Any]]]:
        del emit  # 未分类的 provider 分片绝不直接触达事件总线。

        # 正常轮开放动作工具 + reply_to_user；收口轮只开放 reply_to_user。
        #
        # 不向 provider 发送 ``tool_choice``。DeepSeek v4-pro 的 Thinking Mode
        # 会直接拒绝该字段（包括 ``required``）；而 ``tool_choice`` 本来也只能是
        # provider 请求偏好，不能承担安全边界。控制/正文隔离由下面的
        # ``tool_protocol_mode=\"normalize\"`` 本地强制执行：完整回合缓冲后分类，
        # 只有被证明是纯净正文/合法工具调用的内容才允许发布。
        #
        # [v1.0.22.1] 不再使用 ``require_typed_output``：普通文本是模型最自然的
        # 回话方式，应与项目经理/Worker 的 AgentLoop 同语义（文本即回复，工具即动作）。
        # 协议杂讯仍会被 StreamAssembler 判为 protocol_error，由调用方人话收尾。
        schemas = _schemas(allow_tools=allow_tools)
        assembler = StreamAssembler(
            tool_schemas=schemas,
            tool_protocol_mode="normalize",
        )

        async for event in client.chat_stream(
            messages=messages,
            tools=schemas,
            temperature=None,
        ):
            # [M1 采集点 C] 终端 usage 帧不喂给 assembler，直接交给落盘回调。
            if event.get("type") == "usage" and isinstance(event.get("usage"), dict):
                if self._usage_sink is not None:
                    try:
                        self._usage_sink(event["usage"])
                    except Exception:
                        log.debug("知知 usage 落盘失败（忽略）", exc_info=True)
                continue
            assembler.feed(event)

        turn = assembler.finalize_turn()
        if turn.kind in {"protocol_error", "stream_error"}:
            # [v1.0.22.1] 协议错误是终局，不再 raise 重试。normalize 模式下被缓冲的
            # 正文不会进入 UI（safe fail），这里返回空文本，由调用方人话收尾。
            log.error(
                msg("zinnia.091"),
                turn.kind, turn.protocol_encoding, turn.finish_reason, turn.error,
            )
            return "", []

        # [v1.0.22.1] 纯文本回合 = 模型直接回话（与 AgentLoop "plain answer is
        # authoritative" 同语义），直接作为回复发布；工具回合继续走执行循环。
        if not turn.tool_calls:
            return (turn.content or "").strip(), []

        if turn.protocol_encoding != "native":
            log.info(
                msg("zinnia.094"),
                turn.protocol_encoding,
            )
        return "", list(turn.tool_calls)

    def _make_client(self) -> ProviderClient | None:
        if self._client_factory is None and not self.api_key:
            return None
        return ProviderClient(
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
            provider=self.provider,
        )


# ═══════════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════════

def _clean_name(raw: Any) -> str:
    """项目名不能是空的，也不能是一整段话——模型偶尔会把整个需求塞进来。"""
    if not isinstance(raw, str):
        raise ToolArgError(msg("zinnia.095"))
    name = re.sub(r"\s+", " ", raw).strip()
    if not name:
        raise ToolArgError(msg("zinnia.096"))
    if len(name) > 40:
        raise ToolArgError(msg("zinnia.097"))
    return name


_PROJECT_ID_LOCK = threading.Lock()
_LAST_PROJECT_SECOND: datetime | None = None


def new_project_id() -> str:
    """Generate a readable, monotonic second-resolution project id.

    Normal creation QPS is far below one per second.  The monotonic guard still closes the
    collision edge case without appending random noise or changing the public format.
    """
    global _LAST_PROJECT_SECOND
    now = datetime.now().replace(microsecond=0)
    with _PROJECT_ID_LOCK:
        if _LAST_PROJECT_SECOND is not None and now <= _LAST_PROJECT_SECOND:
            now = _LAST_PROJECT_SECOND + timedelta(seconds=1)
        _LAST_PROJECT_SECOND = now
    return "project_" + now.strftime("%Y%m%d%H%M%S")


__all__ = ["ZinniaAgent", "PLATFORM_PROJECT_ID", "ZINNIA", "new_project_id"]