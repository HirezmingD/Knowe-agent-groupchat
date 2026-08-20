# knowe v1.0.38 — 按项目隔离的成员身份表
"""
identity_store.py — 成员「用户自定义身份」表，**按项目隔离**。

## 为什么从「全局」改成「按项目」

v1.0.38.x 用**全局表**（键 = agent_id，如 fe_1）存用户改的名字/头像，语义是
「同一逻辑成员在所有项目显示同一名字/头像」。这导致一个严重问题：

  在 A 群把某职位（如 fe_1 界面设计助手）改叫「🍌大王」→ 全局表里 agent_id=fe_1
  记为🍌大王 → 全软件**所有** fe_1（含新群的）一起变。

用户要求（2026-08-20）：**某个群的 PM / worker 改名、换头像，只在这个群内保留**，
其他群的同职能成员不受影响。所以身份不能锚在「职位编号」上（同一职位跨项目不唯一），
必须锚在「[群号 + 职位编号]」组合上。本模块改为按项目存储。

## 语义

  · 某 (project_id, agent_id) 表里**没有** → 用回现状（花名册名 / 前端派生头像）。
  · 表里**有**且 `custom_*` 为 true → 用户自定义值**优先**，仅在本项目生效。

## 存储结构

  backend/data/identities.json：
      {
        "<project_id>": {
            "<agent_id>": {
                "name": "...",        # 用户自定义名字
                "custom_name": true,  # 是否覆盖（false=还原为花名册名）
                "avatar": "...",      # 用户自定义头像（dataURL 或引用）
                "custom_avatar": true # 是否覆盖（false=还原为前端派生）
            }
        }
      }

  原子写，参照 persist._atomic_write。

  ★ 迁移：旧版 identities.json 是 {agent_id: {...}} 顶层键为职位编号的**全局表**。
  读盘时检测到这种旧结构（顶层键不是 project_id 分组）→ 直接丢弃旧全局记录、
  按空表起步，避免旧「泄漏名」重新污染新群。旧记录对应的改动本就是缺陷产物，
  不值得迁移。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("knowe.identity_store")

_IDENTITIES_FILENAME = "identities.json"


class IdentityStore:
    """按项目隔离的成员身份读写。进程内单例，写走一次性加载/保存。"""

    def __init__(self, data_dir: str | os.PathLike[str]) -> None:
        self.path = Path(data_dir) / _IDENTITIES_FILENAME
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        self._loaded = False

    # ── 加载/保存 ────────────────────────────────────────────────
    def load(self) -> None:
        """读盘。文件不存在/损坏/旧全局结构 → 空表起步（不炸启动）。"""
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.error("identities.json 读不了（%s），当作空表起步", exc)
            self._data = {}
            return
        if not isinstance(raw, dict):
            self._data = {}
            return
        # 旧全局结构判别（v1.0.38.x）：文件顶层是 {agent_id: {rec}}，rec 的字段值是
        # 标量（name 字符串 / avatar dataURL / custom_* 布尔）。
        # 新结构（v1.0.38）顶层是 {project_id: {agent_id: {rec}}}，project 块的值
        # 全是 dict。判别法：某顶层块值里只要出现非 dict（标量）→ 是旧全局记录 → 丢弃。
        # 旧「泄漏名/头像」本就跨群污染，不值得迁移回任何单项目，按空表起步最干净。
        new_data: dict[str, dict[str, dict[str, Any]]] = {}
        for proj, sub in raw.items():
            if not isinstance(proj, str) or not isinstance(sub, dict):
                continue
            if any(not isinstance(v, dict) for v in sub.values()):
                log.warning("identities.json 检测到旧全局结构，跳过 %r 的全局记录", proj)
                continue
            proj_block: dict[str, dict[str, Any]] = {}
            for agent_id, rec in sub.items():
                if isinstance(agent_id, str) and isinstance(rec, dict):
                    proj_block[agent_id] = rec
            new_data[proj] = proj_block
        self._data = new_data

    def _save(self) -> None:
        """原子写盘。写失败记日志但不抛（和 persist 一样的容错哲学）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── 读 ───────────────────────────────────────────────────────
    def get(self, project_id: str, agent_id: str) -> dict[str, Any] | None:
        """取某成员在**指定项目**的身份记录；没有 → None。"""
        self.load()
        rec = (
            self._data.get(project_id, {}).get(agent_id)
            if isinstance(project_id, str) else None
        )
        return rec if isinstance(rec, dict) else None

    def custom_name(self, project_id: str, agent_id: str) -> str | None:
        """用户自定义名字（本项目内）；没设或未启用 → None（用花名册名）。"""
        rec = self.get(project_id, agent_id)
        if rec and rec.get("custom_name") and rec.get("name"):
            return str(rec["name"])
        return None

    def custom_avatar(self, project_id: str, agent_id: str) -> str | None:
        """用户自定义头像（本项目内）；没设或未启用 → None（用前端派生）。"""
        rec = self.get(project_id, agent_id)
        if rec and rec.get("custom_avatar") and rec.get("avatar"):
            return str(rec["avatar"])
        return None

    # ── 写 ───────────────────────────────────────────────────────
    def set_name(self, project_id: str, agent_id: str, name: str) -> None:
        """改名（仅本项目）。name 空串等价清除。"""
        self.load()
        if not isinstance(project_id, str):
            return
        block = self._data.setdefault(project_id, {})
        rec = block.setdefault(agent_id, {})
        rec["custom_name"] = bool(name)
        if name:
            rec["name"] = name
        else:
            rec.pop("name", None)
        self._save()

    def clear_name(self, project_id: str, agent_id: str) -> None:
        """还原（仅本项目）：不再用自定义名（回花名册名）。"""
        self.load()
        if not isinstance(project_id, str):
            return
        rec = self._data.get(project_id, {}).get(agent_id)
        if rec:
            rec["custom_name"] = False
            rec.pop("name", None)
            self._save()

    def set_avatar(self, project_id: str, agent_id: str, avatar: str) -> None:
        """换头像（仅本项目）。avatar 空串等价清除。"""
        self.load()
        if not isinstance(project_id, str):
            return
        block = self._data.setdefault(project_id, {})
        rec = block.setdefault(agent_id, {})
        rec["custom_avatar"] = bool(avatar)
        if avatar:
            rec["avatar"] = avatar
        else:
            rec.pop("avatar", None)
        self._save()

    def clear_avatar(self, project_id: str, agent_id: str) -> None:
        """还原（仅本项目）：不再用自定义头像（回前端派生）。"""
        self.load()
        if not isinstance(project_id, str):
            return
        rec = self._data.get(project_id, {}).get(agent_id)
        if rec:
            rec["custom_avatar"] = False
            rec.pop("avatar", None)
            self._save()

    # ── 诊断 ─────────────────────────────────────────────────────
    def all(self) -> dict[str, dict[str, dict[str, Any]]]:
        """整本表（只读诊断用）。"""
        self.load()
        return {
            proj: dict(block)
            for proj, block in self._data.items()
        }


# ═══════════════════════════════════════════════════════════════
# 进程级单例（供 engine / server 无参数访问）
#
# 设计：server 启动时调一次 configure(data_dir) 注册，engine 侧用 get() 拿
# 当前实例。engine 是**按项目隔离**的（每个项目一个 engine），读写时把
# 自己的 self.project_id 作为 project_id 传入，天然按项目隔离。
# 未 configure → get() 返回 None（调用方判空兜底，不炸）。
# ═══════════════════════════════════════════════════════════════
_singleton: IdentityStore | None = None
_singleton_dir: str | None = None


def configure(data_dir: str | os.PathLike[str]) -> IdentityStore:
    """server 启动时注册身份表。（幂等：换目录才重建）"""
    global _singleton, _singleton_dir
    data_dir = os.fspath(data_dir)
    if _singleton is not None and _singleton_dir == data_dir:
        return _singleton
    _singleton = IdentityStore(data_dir)
    _singleton_dir = data_dir
    _singleton.load()
    return _singleton


def get() -> IdentityStore | None:
    """当前身份表；未 configure → None。"""
    return _singleton


def reset() -> None:
    """清空单例（测试隔离用）。"""
    global _singleton, _singleton_dir
    _singleton = None
    _singleton_dir = None
