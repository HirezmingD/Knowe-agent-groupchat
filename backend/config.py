"""
config.py — 全部可调项集中在此，禁止在别处写死。

环境变量：
  KNOWE_AGENT       fake | deepseek        （默认 fake）
  KNOWE_SCRIPT      simple | full          （fake 档的剧本，默认 full）
  KNOWE_STRICT      1 | 0                  （出站契约强校验，默认 1）
  KNOWE_EMIT_TURN_END  1 | 0               （默认 0，见 README「一处契约冲突」）
  KNOWE_WS_HOST/PORT, KNOWE_HEALTH_PORT
  DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL

  [v2.2] Worker 的终端 / 网络 / 浏览器 / 视觉服务：
  KNOWE_TERMINAL_ENABLED   1 | 0   （默认 1；关掉 = 工具仍可见并返回稳定 error）
  KNOWE_TERMINAL_TIMEOUT   秒      （默认 120）
  KNOWE_TERMINAL_MAX_OUTPUT 字     （默认 30000）
  KNOWE_TERMINAL_MAX_TIMEOUT 秒     （默认 off；安装级可选上限）
  KNOWE_PROC_MAX           个      （默认 8，每项目后台进程上限）
  KNOWE_PROC_LOG_BYTES     字节    （默认 200000）
  KNOWE_WEB_ENABLED        1 | 0   （默认 1）
  KNOWE_WEB_SEARCH_BACKEND ddgs | searxng（默认 ddgs）
  KNOWE_SEARXNG_URL        URL     （backend=searxng 时必填）
  KNOWE_WEB_TIMEOUT        秒      （默认 25）
  KNOWE_WEB_MAX_CHARS      字      （默认 20000，每个 URL 返回上限）
  KNOWE_BROWSER_ENABLED    1 | 0   （默认 1）
  KNOWE_BROWSER_HEADLESS   1 | 0   （默认 1，不弹窗口）
  KNOWE_BROWSER_TIMEOUT    秒      （默认 20）
  KNOWE_BROWSER_IDLE       秒      （默认 600，空闲多久回收 Chromium）
  KNOWE_BROWSER_MAX_SESSIONS 个    （默认 4）
  KNOWE_BROWSER_SNAPSHOT_MAX 个    （默认 200，快照最多列几个元素）
  KNOWE_VISION_ENABLED     1 | 0   （默认 1）
  KNOWE_VISION_MODEL / KNOWE_VISION_BASE_URL / KNOWE_VISION_API_KEY
                                   （默认继承 DeepSeek 那一套；见 vision_tools 模块头）

  [v0.44.5] Project Memory 长期记忆：
  KNOWE_MEMORY_RECENT_KEEP       条      （快照里保留的最近活动，默认 24）
  KNOWE_MEMORY_FULL_EVERY        回合    （滚动状态摘要间隔，默认 5）
  KNOWE_MEMORY_SEGMENT_RECORDS   条      （历史活动段轮转阈值，默认 128）
  KNOWE_MEMORY_SEGMENT_BYTES     字节    （历史活动段轮转阈值，默认 524288）
  KNOWE_MEMORY_INPUT_CHARS       字      （快照中单条输入投影上限，默认 1600）
  KNOWE_MEMORY_OUTPUT_CHARS      字      （快照中单条产出投影上限，默认 3200）
  KNOWE_MEMORY_SEARCH_MAX        条      （一次检索最多返回，默认 50）

  [v0.43] Agent 技能包：
  KNOWE_SYSTEM_SKILLS_DIRS         系统自备 SKILL.md 根目录（多个目录用 os.pathsep 分隔）
  KNOWE_THIRD_PARTY_SKILLS_DIR     第三方技能真实安装目录
  KNOWE_SKILL_INDEX_MAX_LINES      Prompt 常驻的生效技能索引上限（默认 16）

  [v2.2] Worker Runtime / Provider：
  KNOWE_RUNTIME_INLINE_REFERENCE_BYTES
  KNOWE_RUNTIME_WALL_CLOCK_SECONDS     （默认 off）
  KNOWE_PROVIDER_RETRIES
  KNOWE_PROVIDER_CONNECT_TIMEOUT / READ_TIMEOUT / WRITE_TIMEOUT / POOL_TIMEOUT
                                      （none/off 可关闭对应无网络进展边界）

"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# [v0.4] 业主在 v0.3 之后自己加了这两行（key 走 .env）——保留，不要删。
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:      # 没装 python-dotenv 也能跑，只是不读 .env
    pass


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _optional_positive_int(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"", "0", "none", "null", "off", "false", "disabled"}:
        return None
    value = int(normalized)
    return value if value > 0 else None


def _optional_positive_float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"", "0", "none", "null", "off", "false", "disabled"}:
        return None
    value = float(normalized)
    return value if value > 0 else None


def _install_root_default() -> str:
    """PyInstaller 冻结态感知的安装目录兜底。

    优先级：
      1. env KNOWE_INSTALL_ROOT（Electron 流程显式注入，最权威）；
      2. PyInstaller 冻结态（sys._MEIPASS 存在）：取 _MEIPASS 的父目录——
         直接跑 KnoweBackend.exe 时 _MEIPASS = <安装根>/_internal，
         父目录即安装根；
      3. 开发态：从包位置往上推两级 = 项目根（原语义，保持不变）。
    """
    env_root = os.environ.get("KNOWE_INSTALL_ROOT")
    if env_root:
        return env_root
    try:
        import sys  # noqa: PLC0415 仅兜底路径需要，避免头部新增全局 import
        meipass = getattr(sys, "_MEIPASS", None)
    except Exception:
        meipass = None
    if meipass:
        return str(Path(meipass).resolve().parent)
    return str(Path(__file__).resolve().parents[2])


def _data_dir() -> str:
    """Normalize an explicit desktop data root; keep CLI defaults and memory mode intact."""
    raw = os.environ.get("KNOWE_DATA_DIR")
    if raw is None:
        return "./data"
    if not raw.strip():
        return ""
    return str(Path(raw).expanduser().resolve())


@dataclass(frozen=True)
class Config:
    # ── 网络 ──
    ws_host: str = field(default_factory=lambda: os.environ.get("KNOWE_WS_HOST", "127.0.0.1"))
    ws_port: int = field(default_factory=lambda: int(os.environ.get("KNOWE_WS_PORT", "8080")))
    health_host: str = field(default_factory=lambda: os.environ.get("KNOWE_HEALTH_HOST", "127.0.0.1"))
    health_port: int = field(default_factory=lambda: int(os.environ.get("KNOWE_HEALTH_PORT", "8081")))

    # Per-process application identity.  Electron generates it at backend launch; it is never
    # persisted or exposed through preload.  ``server.run`` validates the exact format before
    # opening any control socket.
    runtime_token: str = field(default_factory=lambda: os.environ.get("KNOWE_RUNTIME_TOKEN", "").strip())

    # [v1.0.19.4] 附件路径签名密钥——**持久化**、与一进程一换的 runtime_token 分离。
    #   护栏（只有主进程能签→拒绝正文捏造的路径）不要求密钥每次启动都换；反而要求**跨重启稳定**，
    #   否则历史消息里的旧签名在重启后全部作废，回看历史文件卡会被误判成「校验未通过」。
    #   主进程把它持久化在 userData 下并经 KNOWE_ATTACHMENT_KEY 传入；缺省时回退到 runtime_token
    #   （老客户端 / 未传该变量时不至于所有附件都读不了，只是回看仍受一进程一换的限制）。
    attachment_key: str = field(default_factory=lambda: os.environ.get("KNOWE_ATTACHMENT_KEY", "").strip())

    # ── 引擎 ──
    agent: str = field(default_factory=lambda: os.environ.get("KNOWE_AGENT", "fake"))
    script: str = field(default_factory=lambda: os.environ.get("KNOWE_SCRIPT", "full"))

    # ── 协议参数（PROTOCOL.md 的常数，改这里一处） ──
    ring_capacity: int = 1000          # 每项目 ring buffer 容量
    handshake_timeout_s: float = 5.0   # 首帧 replay_request 等待窗口
    # approval_timeout_s：[v0.44 设置] 不再是装机常量——改成类体下方的动态 property，
    #   每次读取都问 runtime_settings 要「当前群 > 全局 > 出厂 300s」的裁决结果。
    #   gate.py 照旧读 CONFIG.approval_timeout_s，一行不用改；设置面板改完即刻生效。
    #   （代价：Config(approval_timeout_s=…) 这种构造方式不再支持——代码库里本来也没有。）

    # ── [v0.6] Harness 三级温度 ──
    #   项目经理 0.5：他在做判断（要几个人、派给谁），要稳，不要发挥
    #   Worker 0.7：他在干活（写方案、出内容），需要一点发挥空间
    coordinator_temperature: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_COORD_TEMP", "0.5")))
    worker_temperature: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_WORKER_TEMP", "0.7")))

    # ── [v0.44.5] Project Memory 长期记忆 ──
    # `.context.json` / `context.md` 只做有界快照；完整历史写入紧凑 JSONL，达到
    # records 或 bytes 任一阈值就封段并用 gzip 压缩。所有旋钮都只影响**今后的写入/展示**，
    # 不会删除已有历史；读取器始终兼容旧段。
    memory_recent_keep: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_MEMORY_RECENT_KEEP", "24")))
    memory_full_every: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_MEMORY_FULL_EVERY", "5")))
    memory_segment_records: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_MEMORY_SEGMENT_RECORDS", "128")))
    memory_segment_bytes: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_MEMORY_SEGMENT_BYTES", "524288")))
    memory_input_chars: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_MEMORY_INPUT_CHARS", "1600")))
    memory_output_chars: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_MEMORY_OUTPUT_CHARS", "3200")))
    memory_search_max: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_MEMORY_SEARCH_MAX", "50")))

    # ── [v0.19] 项目级知识图谱 ──
    # 辅助 LLM 只在 handoff 落盘后后台抽取；查询阶段不调用 LLM。
    knowledge_aux_max_tokens: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_KG_AUX_MAX_TOKENS", "900")))
    knowledge_aux_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_AUX_TIMEOUT", "25")))
    # 图谱可从 handoff 重建；关机只短暂尽力排空，避免项目切换被辅助 LLM 拖住。
    knowledge_shutdown_drain_s: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_SHUTDOWN_DRAIN", "3")))
    # 审批分采用带先验的有界分数：一次拒绝不会永久打死节点；旧信号按半衰期自然降权。
    knowledge_score_prior: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_SCORE_PRIOR", "2.0")))
    knowledge_score_half_life_days: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_SCORE_HALF_LIFE_DAYS", "120")))
    knowledge_freshness_half_life_days: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_FRESHNESS_HALF_LIFE_DAYS", "240")))
    knowledge_approve_reward: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_APPROVE_REWARD", "1.0")))
    knowledge_reject_penalty: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_REJECT_PENALTY", "-0.65")))
    # Harness 层尚未实现；这些阈值决定项目节点何时进入标准化候选出口。
    knowledge_promotion_min_score: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_PROMOTION_MIN_SCORE", "0.45")))
    knowledge_promotion_min_positive_events: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_KG_PROMOTION_MIN_POSITIVE", "3")))
    knowledge_promotion_min_confidence: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_PROMOTION_MIN_CONFIDENCE", "0.60")))
    knowledge_promotion_min_sources: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_KG_PROMOTION_MIN_SOURCES", "2")))
    knowledge_promotion_max_negative_weight: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_PROMOTION_MAX_NEGATIVE", "0.80")))

    # 知识库 HTTP 路由并入主控制服务，不再拥有独立监听配置。

    # ── [v0.42] 知识系统重构：资产层 / 知知蒸馏 / 使用闭环（knowledge_assets.py）──
    # 蒸馏走**主模型档位**（报告：不计成本条款的第一花钱处——蒸馏质量决定系统上限，
    # 不能省给轻模型）。KNOWE_KG_DISTILL_MODEL 不填时回落 DEEPSEEK_MODEL。
    knowledge_distill_enabled: bool = field(
        default_factory=lambda: _flag("KNOWE_KG_DISTILL_ENABLED", True))
    knowledge_distill_model: str = field(
        default_factory=lambda: os.environ.get("KNOWE_KG_DISTILL_MODEL", ""))
    knowledge_distill_max_tokens: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_KG_DISTILL_MAX_TOKENS", "1600")))
    knowledge_distill_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_DISTILL_TIMEOUT", "60")))
    # T2 周期性合并：每 N 个 approved 任务触发一次，另有兜底定时器。
    knowledge_consolidate_every_n: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_KG_CONSOLIDATE_EVERY_N", "5")))
    knowledge_consolidate_interval_s: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_KG_CONSOLIDATE_INTERVAL", "21600")))
    # 常驻注入预算：索引 ≤20 行、画像 ≤40 行（报告 §4.4 的 token 预算口径）。
    knowledge_index_max_lines: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_KG_INDEX_MAX_LINES", "20")))
    knowledge_profile_max_lines: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_KG_PROFILE_MAX_LINES", "40")))
    # 指令条件化注入：对指令正文匹配 top-N 资产的 L0 行。
    knowledge_task_match_top: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_KG_TASK_MATCH_TOP", "3")))

    # ── [v0.43] Agent 技能包 ──
    # 系统自备技能来自安装包/资源目录中的真实 SKILL.md。可用 os.pathsep（macOS/Linux
    # 为 ":"，Windows 为 ";"）追加安装器注入的目录；为空时仍会扫描 Knowe 包内、
    # KNOWE_DATA_DIR/skills/system 与 sys.prefix/share/knowe/skills 等标准位置。
    skill_system_dirs: str = field(
        default_factory=lambda: os.environ.get("KNOWE_SYSTEM_SKILLS_DIRS", ""))
    # 第三方技能的实际安装目录。安装器下一阶段只需把一个含 SKILL.md 的目录放进这里，
    # 本版扫描器就会自动发现，并在首次发现时以 active 状态登记。
    skill_third_party_dir: str = field(
        default_factory=lambda: os.environ.get(
            "KNOWE_THIRD_PARTY_SKILLS_DIR", ""))
    # 常驻 prompt 只注入生效技能的一行索引；全文必须通过 read_skillpack 按需展开。
    skill_index_max_lines: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_SKILL_INDEX_MAX_LINES", "16")))

    # ── [v0.20 Batch 4] Worker 能力：终端 / 网络 / 浏览器 / 视觉 ──
    #
    # 每一类都有一个 *_ENABLED 开关，而且开关的语义是**不注册这些工具**，
    # 不是「注册了但调用时报错」。理由和 tools_knowe 模块头那句一样：
    #   「模型看不见的工具，它就调不了」——比让它调一次、吃个错、再重试可靠得多，
    #   也省一份 function schema 的 token。
    #
    # ⚠ 依赖缺失（没装 playwright / 没装 ddgs）走的是**另一条路**：工具照常注册，
    #   调用时返回「怎么装」。因为那是可以当场补救的，而开关是用户的明确意图。
    terminal_enabled: bool = field(default_factory=lambda: _flag("KNOWE_TERMINAL_ENABLED", True))
    terminal_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_TERMINAL_TIMEOUT", "120")))
    # Omitted commands still default to terminal_timeout_s.  A maximum is an explicit
    # installation policy only; the factory default does not impose a 30-minute task cap.
    terminal_max_timeout_s: float | None = field(
        default_factory=lambda: _optional_positive_float("KNOWE_TERMINAL_MAX_TIMEOUT", None))
    terminal_max_output: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_TERMINAL_MAX_OUTPUT", "30000")))
    process_max: int = field(default_factory=lambda: int(os.environ.get("KNOWE_PROC_MAX", "8")))
    process_log_bytes: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_PROC_LOG_BYTES", "200000")))

    web_enabled: bool = field(default_factory=lambda: _flag("KNOWE_WEB_ENABLED", True))
    web_search_backend: str = field(
        default_factory=lambda: os.environ.get("KNOWE_WEB_SEARCH_BACKEND", "ddgs"))
    searxng_url: str = field(default_factory=lambda: os.environ.get("KNOWE_SEARXNG_URL", ""))
    web_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_WEB_TIMEOUT", "25")))
    web_max_chars: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_WEB_MAX_CHARS", "20000")))

    browser_enabled: bool = field(default_factory=lambda: _flag("KNOWE_BROWSER_ENABLED", True))
    browser_headless: bool = field(default_factory=lambda: _flag("KNOWE_BROWSER_HEADLESS", True))
    browser_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_BROWSER_TIMEOUT", "20")))
    # 空闲回收：Chromium 常驻 ~200MB。查完一次文档就再没人碰的场景是常态，
    # 十分钟没人用就把它放了 —— 用户不该在活动监视器里看见一个不明所以的浏览器。
    browser_idle_s: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_BROWSER_IDLE", "600")))
    browser_max_sessions: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_BROWSER_MAX_SESSIONS", "4")))
    browser_snapshot_max: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_BROWSER_SNAPSHOT_MAX", "200")))

    vision_enabled: bool = field(default_factory=lambda: _flag("KNOWE_VISION_ENABLED", True))
    # 默认继承 DeepSeek 的三件套。DeepSeek 的对话模型**不支持图片**，
    # 所以默认配置下第一次调用会拿到一句「请配置多模态模型」的中文提示——
    # 这是**设计如此**：与其假装支持，不如告诉用户该配什么。
    # [v0.44.1 Bug3] 去掉写死的 "deepseek-chat" / "https://api.deepseek.com" 兜底——
    #   只从 KNOWE_VISION_* / DEEPSEEK_* 环境变量继承，都没有则为空（vision_tools 会
    #   照常提示「请配置多模态模型」，不再静默指向某个隐藏默认）。
    vision_model: str = field(
        default_factory=lambda: os.environ.get(
            "KNOWE_VISION_MODEL", os.environ.get("DEEPSEEK_MODEL", "")))
    vision_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "KNOWE_VISION_BASE_URL",
            os.environ.get("DEEPSEEK_BASE_URL", "")))
    vision_api_key: str = field(
        default_factory=lambda: os.environ.get(
            "KNOWE_VISION_API_KEY", os.environ.get("DEEPSEEK_API_KEY", "")))
    vision_max_tokens: int = field(
        default_factory=lambda: int(os.environ.get("KNOWE_VISION_MAX_TOKENS", "900")))

    # ── [v0.26] 「我有新意见」→ 调整指令那一次定向 LLM 调用的超时 ──
    #   用户点了发送就在盯着转圈，等太久他会以为卡死了；而这活很小（改一段文字），
    #   40 秒足够。超时就如实告诉他「再试一次」，比让他盯着转圈强。
    adjust_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("KNOWE_ADJUST_TIMEOUT", "40")))

    # ── [v2.2] Worker Runtime：单循环、单 Prompt、固定 19 工具 ──
    # Small UTF-8 project references may be inlined.  Larger references are not sliced:
    # the Gateway sends path/size/SHA-256/required metadata and the Worker reads ranges
    # with its existing safe_read_file tool.  This is a transport choice, not a fact cap.
    runtime_inline_reference_bytes: int = field(
        default_factory=lambda: max(0, int(os.environ.get(
            "KNOWE_RUNTIME_INLINE_REFERENCE_BYTES", "32768"))))
    # 0|off|none disables the optional whole-task clock.  Disabled is the factory default;
    # user cancellation and operation-specific Provider/tool boundaries remain active.
    runtime_wall_clock_seconds: int | None = field(
        default_factory=lambda: _optional_positive_int("KNOWE_RUNTIME_WALL_CLOCK_SECONDS", None))

    # Provider transient retry policy has one application-level source of truth.  The
    # client itself has no hidden retry default; every production constructor passes this.
    provider_max_retries: int = field(
        default_factory=lambda: max(0, int(os.environ.get("KNOWE_PROVIDER_RETRIES", "2"))))
    # Per-operation network-progress windows.  For streaming responses the read timeout
    # is refreshed by every received chunk; it is never an overall model/task duration.
    provider_connect_timeout_s: float | None = field(
        default_factory=lambda: _optional_positive_float("KNOWE_PROVIDER_CONNECT_TIMEOUT", 10.0))
    provider_read_timeout_s: float | None = field(
        default_factory=lambda: _optional_positive_float("KNOWE_PROVIDER_READ_TIMEOUT", 120.0))
    provider_write_timeout_s: float | None = field(
        default_factory=lambda: _optional_positive_float("KNOWE_PROVIDER_WRITE_TIMEOUT", 120.0))
    provider_pool_timeout_s: float | None = field(
        default_factory=lambda: _optional_positive_float("KNOWE_PROVIDER_POOL_TIMEOUT", 10.0))

    # ── [v0.5 #10] 新群建好时，项目经理主动说第一句话 ──
    #
    # ⚠ Fake 档默认**关掉**。原因：FakeAgent 的剧本是「收到：「<你说的话>」…」——
    #   它会把 kickoff 那段系统指令**原样念给用户听**（"收到：「（系统）项目刚建好，
    #   请你先自我介绍…」"）。而 fake.py 在禁改清单里，我不能改它的剧本。
    #   真 LLM 档会照指令自我介绍，一切正常。
    #   要在 Fake 档下也看这个功能：KNOWE_KICKOFF=1（然后你会看见它把指令念出来）。
    kickoff: bool = field(default_factory=lambda: _flag(
        "KNOWE_KICKOFF",
        os.environ.get("KNOWE_AGENT", "fake").lower() not in ("fake",),
    ))

    # ── [v1.0.23.3] 初入群打招呼 ──
    # 新 worker 入群（建群主动拉人 / 主管审批拉人）时跑一次 LLM，在群里说一句
    # 话（人性化）。与 kickoff 同策略：真实 LLM 档默认开，Fake 档默认关
    # （Fake 会原样念指令）。welcome_worker 内部 fire-and-forget + 失败容错。
    welcome: bool = field(default_factory=lambda: _flag(
        "KNOWE_WELCOME",
        os.environ.get("KNOWE_AGENT", "fake").lower() not in ("fake",),
    ))

    # ── 落盘（v0.4）──
    # 数据目录。桌面端始终显式注入 <install>/data/backend 的绝对路径；
    # 纯命令行/浏览器模式仍落在 ./data。设成空串 = 关掉持久化（测试里常用）。
    data_dir: str = field(default_factory=_data_dir)

    # ── [v0.12 D · 问题五 5e] 平台自我认知 ──
    # 版本号（知知的平台上下文里报给用户）。发版时改这里或用环境变量。
    version: str = field(default_factory=lambda: os.environ.get("KNOWE_VERSION", "1.0.25.2"))
    # 安装目录（平台变更日志扫描的根）。Electron 流程靠 KNOWE_INSTALL_ROOT 注入；
    #   PyInstaller 冻结态兜底取 _MEIPASS 父目录（直接跑 exe 也拿得到安装根）；
    #   开发态从包位置往上推两级 = 项目根。见 _install_root_default()。
    install_root: str = field(default_factory=_install_root_default)

    # ── 纪律 ──
    strict_contract: bool = field(default_factory=lambda: _flag("KNOWE_STRICT", True))
    emit_turn_end: bool = field(default_factory=lambda: _flag("KNOWE_EMIT_TURN_END", False))

    # ── DeepSeek（[v0.44.1 Bug3] 遗留 .env 兼容位，**不再内置任何默认值**）──
    #   模型的唯一配置入口是「设置 → 模型与提供方」（runtime_settings 权威）。
    #   开发期写死的 DeepSeek 默认模型 / base_url 已移除：这三项只从环境变量读，
    #   缺省一律为空串（= 没配）。留着这三个字段仅为兼容「显式在 .env 里配好整套
    #   DEEPSEEK_* 」的老部署；空着时引擎不会偷偷回落到某个隐藏默认，而是走
    #   _harness_turn 的「还没有配置模型」提示，把用户引到设置面板。
    deepseek_api_key: str = field(default_factory=lambda: os.environ.get("DEEPSEEK_API_KEY", ""))
    deepseek_model: str = field(default_factory=lambda: os.environ.get("DEEPSEEK_MODEL", ""))
    deepseek_base_url: str = field(
        default_factory=lambda: os.environ.get("DEEPSEEK_BASE_URL", "")
    )

    # ── Fake 档节奏（测试里调小，跑得快） ──
    fake_delta_delay_s: float = field(default_factory=lambda: float(os.environ.get("KNOWE_FAKE_DELAY", "0.06")))
    fake_think_delay_s: float = field(default_factory=lambda: float(os.environ.get("KNOWE_FAKE_THINK", "0.3")))
    fake_work_delay_s: float = field(default_factory=lambda: float(os.environ.get("KNOWE_FAKE_WORK", "0.8")))

    # ── [v0.44 设置] 审批超时：动态属性（不是 dataclass 字段）──
    #   读的人还是老样子：CONFIG.approval_timeout_s。变的是来源：
    #   runtime_settings.effective_approval_timeout() 按「当前群 > 全局设置 > 出厂永不」
    #   裁决；「永不」返回 None，由 Gate 直接等待显式批准/拒绝/取消。
    #   群从 contextvar 取（engine._loop 进场登记，propose 的后代任务自动继承）。
    @property
    def approval_timeout_s(self) -> float | None:
        from . import runtime_settings   # 局部 import：避免 config ⇄ runtime_settings 环
        return runtime_settings.effective_approval_timeout()

    @approval_timeout_s.setter
    def approval_timeout_s(self, value: float | None) -> None:
        # Compatibility boundary for pre-v0.44 callers/tests that use
        # ``object.__setattr__(CONFIG, "approval_timeout_s", value)``.  The value is
        # still stored in runtime_settings, never as a second source of truth on Config.
        from . import runtime_settings
        runtime_settings.set_approval_timeout_compat(value)


CONFIG = Config()
