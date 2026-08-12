"""
i18n_backend.py — [v1.0.21.3] 后端文案资源化。

事件消息 / 工具描述 / 阶段文案全部外置到 locales/zh.json + locales/en.json，
代码零中文文案。msg(key, **vars) 按当前语言（runtime_settings.language()）取文案并插值。

设计：
  · zh 模式返回与改造前逐字节一致的原文案（locales/zh.json 即原文提取）
  · en 模式返回英文资源；缺失 key 时回退 key 本身（不崩）
  · 与前端 i18n 同构：加语言 = 加一个 JSON，代码零改动
"""

from __future__ import annotations

import json
from pathlib import Path

_DIR = Path(__file__).parent / "locales"
_cache: dict[str, dict] = {}


def _table(lang: str) -> dict:
    if lang not in _cache:
        try:
            _cache[lang] = json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _cache[lang] = {}
    return _cache[lang]


def current_lang() -> str:
    # 延迟导入：runtime_settings 模块级依赖本模块的 msg()，
    # 若在模块顶层 import 会形成循环导入（i18n_backend → runtime_settings → i18n_backend）。
    from . import runtime_settings

    lang = runtime_settings.language() or "zh"
    return lang if lang in ("zh", "en") else "zh"


def msg(key: str, **vars_) -> str:
    """按当前语言取文案并插值 {var}。缺失回退 key。

    占位符支持：
      {var}    → str(v)
      {var!r}  → repr(v)（f-string !r 语义）
      {expr}   → 复杂表达式占位符：调用方传 **{"expr": value} 即可
    """
    text = _table(current_lang()).get(key, key)
    if vars_:
        for k, v in vars_.items():
            text = text.replace("{" + k + "}", str(v))
            text = text.replace("{" + k + "!r}", repr(v))
    return text
