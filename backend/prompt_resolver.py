"""
prompt_resolver.py — [v1.0.21.3] 按语言选择提示词模板。

设计：
  · 语言是全局实时状态（runtime_settings.language()）。
  · 每次组装 prompt 时调用本模块，选对应语言的模板文件。
  · 目录约定：souls/<lang>/<name>.txt、prompts/<lang>/<file>.md
  · 兼容回退链：souls/<lang>/xxx → souls/xxx（现状路径，阶段 0 未迁目录时逐字节一致）→ 空
  · 已产生的历史内容（气泡/文件）不变——本模块只管「下一次组装用什么」。

阶段 0：语言目录尚未建立，全部走现状路径（行为与旧版逐字节一致）。
阶段 2：souls/zh|en/ 与 prompts/zh|en/ 目录建立、模板合入后自动切换。

路径说明：
  souls/   — 角色人设（coordinator.txt / worker.txt）
  prompts/ — 结构化工件（worker_prompt.md / harness_constraints.md / dm_framing.md ...）
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import runtime_settings

log = logging.getLogger(__name__)

# 灵魂/人设目录（与 engine._SOULS 同根，避免两处定义）
_SOULS = Path(__file__).parent / "souls"
# 结构化工件目录
_PROMPTS = Path(__file__).parent / "prompts"

# 白名单：哪些文件按语言解析（防误读非模板文件）
_SOUL_NAMES = ("coordinator", "worker")
_PROMPT_FILES = (
    "worker_prompt.md",
    "harness_constraints.md",
    "dm_framing.md",
)

_SUPPORTED = ("zh", "en")


def current_language() -> str:
    """当前主要语言（'zh' | 'en'），非法值回退 'zh'。"""
    lang = runtime_settings.language()
    return lang if lang in _SUPPORTED else "zh"


def _norm_lang(lang: str | None) -> str:
    lng = (lang or current_language()).strip().lower()
    return lng if lng in _SUPPORTED else "zh"


def read_soul(name: str, *, lang: str | None = None) -> str:
    """角色人设：souls/<lang>/<name>.txt 优先，缺失回退 souls/<name>.txt（现状路径）。

    返回空串仅当两个位置都读不到——调用方应自行兜底（如 engine 直读默认路径）。
    """
    if name not in _SOUL_NAMES:
        log.warning("未知灵魂名 %r（白名单外）", name)
    lng = _norm_lang(lang)
    candidates = (_SOULS / lng / f"{name}.txt", _SOULS / f"{name}.txt")
    for path in candidates:
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError as exc:  # pragma: no cover — 读失败不该拖死组装
                log.warning("读取灵魂模板失败 %s：%s", path, exc)
    log.error("灵魂模板缺失：%s（%s 与默认路径均无）——返回空串", name, lng)
    return ""


def read_prompt(filename: str, *, lang: str | None = None) -> str:
    """结构化工件：prompts/<lang>/<filename> 优先，缺失回退 prompts/<filename>（现状路径）。"""
    if filename not in _PROMPT_FILES:
        log.warning("未知提示词工件 %r（白名单外）", filename)
    lng = _norm_lang(lang)
    candidates = (_PROMPTS / lng / filename, _PROMPTS / filename)
    for path in candidates:
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError as exc:  # pragma: no cover
                log.warning("读取提示词模板失败 %s：%s", path, exc)
    log.error("提示词模板缺失：%s（%s 与默认路径均无）——返回空串", filename, lng)
    return ""


def resolve_prompt_path(filename: str, *, lang: str | None = None) -> Path | None:
    """解析 prompt_path：目标语言文件存在时返回其路径；否则 None（调用方保持默认路径）。

    worker_gateway_runtime 用：阶段 0 prompts/<lang>/ 未建立 → None → 沿用 worker_prompt.md
    现状路径，行为零变化；阶段 2 语言目录建立后自动指向对应模板。
    """
    if filename not in _PROMPT_FILES:
        log.warning("未知提示词工件 %r（白名单外）", filename)
    lng = _norm_lang(lang)
    target = _PROMPTS / lng / filename
    return target if target.is_file() else None
