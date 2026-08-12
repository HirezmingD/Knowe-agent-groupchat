# knowe v0.43 — 知识资产层 + 独立 Agent 技能包体系
"""
knowledge_assets.py — 设计报告《Knowe 知识系统诊断与重构设计报告》的主机器。

三层架构里的**资产层 + 画像层**在这里落地；情节层（knowledge_graph.py）零改动降级为证据库。

┌─ 一条资产 = 一个目录 ────────────────────────────────────────────┐
│  internal_workspace/knowledge/assets/{asset_id}/ASSET.md          │
│  · front-matter 承担 L0（id/class/one_liner —— 一行索引）          │
│  · 正文承担 L1（条件-行动结构的 markdown，Harness 渲染器保证排版）   │
│  · evidence 指回情节层节点 + attachments 承担 L2（深钻）           │
│  元数据镜像进 `.graph.json` 顶层键 `assets`（沿用同一把 per-root    │
│  锁与事件流 events.jsonl —— 真源哲学不变）。                        │
└──────────────────────────────────────────────────────────────────┘

五类资产（报告 §三）：preference（偏好 P）/ playbook（打法 B）/ pitfall（坑 W）/
fact（事实 F）/ decision（决策 D）。三问门槛（反事实 / 可执行 / 归属）由蒸馏
prompt + 确定性校验双重把关；**evidence 指针为必填**，无指针的输出直接丢弃
（报告风险二的制度性兜底）。

UI 三级标签（用户可改，见 apply_user_override 的 category）：
    preference / decision → 约定       pitfall → 坑
    playbook              → 模式       fact    → 清单
用户改的是**价值判断**（是坑还是约定、全局还是项目内）——注入渲染、
主管/worker 的复用方向、晋升出口都跟着 category/scope 实时走（报告 §4.6 +
本次新增需求 2：harness 层面的 agent 复用机制）。

生命周期：seed（单证据偏好，隐藏）→ candidate →（用户批准）validated →
（使用闭环达标）core；任何一级可退役（用户）或彻底删除（purge，不可逆）。
candidate 不进注入、不进指令匹配——**未经人手的知识不许影响生产**。

使用闭环（报告 §4.5）：
    used_and_approved / used_and_rejected / declared_not_helpful /
    matched_never_used / user_override
    utility = 0.45·引用成功率(带先验) + 0.20·引用频次(对数)
            + 0.15·freshness + 0.20·用户裁决项
「被引用 N 次」= 真实引用次数（usage 事件），与 source_count 从此各说各话。

并发纪律：所有**写**入口与情节层 ingest 共用同一把 per-root asyncio 锁
（KnowledgeGraphManager.root_lock）；读走「磁盘真源 → 内存投影」，原子写
保证永不读到半个文件。全模块 best-effort：失败只记日志，绝不上抛进主链。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import CONFIG
from .i18n_backend import msg
from .knowledge_graph import (
    KnowledgeGraphManager,
    _atomic_write,
    _clip,
    _decay,
    _jaccard,
    _normalize_title,
    _now_iso,
    _parse_json_object,
    _parse_timestamp,
    _tokenize,
    _unique_strings,
)

log = logging.getLogger("knowe.knowledge.assets")

DistillCall = Callable[[str, str], Awaitable[str]]

ASSET_SCHEMA_VERSION = 1

_ASSETS_DIR = "assets"
_ASSET_MD = "ASSET.md"
_PROFILE_MD = "PROFILE.md"
# v0.42 的旧导出目录只作为一次性迁移来源保留；v0.43 的技能包有自己独立的
# 存储、状态机与注册表，不再把知识资产目录冒充技能包目录。
_SKILL_EXPORT_DIR = "export/skills"
_PROJECT_SKILL_DIR = "skills/project_experience"
_SKILL_REGISTRY = ".registry.json"
_SKILL_MD = "SKILL.md"

SKILLPACK_SCHEMA_VERSION = 1
SKILLPACK_KINDS = ("system_builtin", "project_experience", "third_party")
SKILLPACK_STATUSES = ("active", "pending", "retired")

_PROJECT_SKILL_LOCKS_GUARD = threading.Lock()
_PROJECT_SKILL_LOCKS: dict[str, threading.Lock] = {}
_THIRD_PARTY_SKILL_LOCK = threading.Lock()

# ── 五类资产（报告 §三 表格）与 id 前缀 ──
ASSET_CLASSES = ("preference", "playbook", "pitfall", "fact", "decision")
_CLASS_PREFIX = {
    "preference": "P", "playbook": "B", "pitfall": "W", "fact": "F", "decision": "D",
}
# 数据映射：class/category 的中文标签体系与资产文件数据绑定（功能性数据，不 i18n）
_CLASS_ZH = {
    "preference": "用户偏好", "playbook": "打法", "pitfall": "坑与解法",
    "fact": "项目事实", "decision": "决策存档",
}

# ── 三级标签（UI 四类）：类 → 默认 category；用户可用 user_category 覆盖 ──
CATEGORIES = ("约定", "坑", "模式", "清单")
_CLASS_TO_CATEGORY = {
    "preference": "约定", "decision": "约定",
    "pitfall": "坑", "playbook": "模式", "fact": "清单",
}


def _category_marker(cat: str, default: str = "·") -> str:
    """注入渲染时不同价值判断给 agent 的**方向词**（按当前语言）。"""
    return {
        "约定": msg("ka.marker.conv"), "坑": msg("ka.marker.pit"),
        "模式": msg("ka.marker.pat"), "清单": msg("ka.marker.list"),
    }.get(cat, default)


def _category_agent_hint(cat: str) -> str:
    """类别 → 给 agent 的说明（按当前语言）。"""
    return {
        "约定": msg("ka.desc.conv"), "坑": msg("ka.desc.pit"),
        "模式": msg("ka.desc.pat"), "清单": msg("ka.desc.list"),
    }.get(cat, "")

_ASSET_STATUSES = ("seed", "candidate", "validated", "core", "retired")

# ── 使用信号（报告 §4.5 表）──
_SIG_OK = "used_and_approved"
_SIG_BAD = "used_and_rejected"
_SIG_NOT_HELPFUL = "declared_not_helpful"
_SIG_IGNORED = "matched_never_used"

_USAGE_HALF_LIFE_DAYS = 60.0
_UTILITY_PRIOR = 1.0
_CORE_MIN_DISTINCT_OK = 2          # ≥2 个不同任务 used_and_approved
_CORE_NEGATIVE_WINDOW_DAYS = 30.0  # 近 30 天无负信号
_RETIRE_SUGGEST_IGNORED = 4        # 连续 N 次 matched_never_used → 建议退役
_MERGE_SIM = 0.86                  # T2 去重合并阈值
_DEDUP_SIM = 0.82                  # T1 入库判重阈值
_CLUSTER_SIM = 0.50                # case→rule 聚类阈值
_CLUSTER_MIN = 3                   # ≥3 条同型 → 归纳 1 条 playbook

_PROCESS_NARRATION_RX = re.compile(
    msg("knowledge_assets.py.011"))

_ASSET_ID_RX = re.compile(r"^[PBWFD]-\d{2,4}$")


def _bounded(value: Any, lo: int, hi: int) -> int:
    try:
        return min(hi, max(lo, int(value)))
    except (TypeError, ValueError):
        return lo


def _float01(value: Any, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


class KnowledgeAssetManager:
    """资产层管理器。所有公开方法 best-effort；写入口与情节层同锁串行。"""

    def __init__(
        self,
        graph: KnowledgeGraphManager,
        *,
        distill_call: DistillCall | None = None,
    ) -> None:
        self._graph = graph
        self._distill_call = distill_call

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------
    @staticmethod
    def assets_dir(internal_workspace: Path | str) -> Path:
        return KnowledgeGraphManager.knowledge_dir(internal_workspace) / _ASSETS_DIR

    @staticmethod
    def profile_path(internal_workspace: Path | str) -> Path:
        return KnowledgeGraphManager.knowledge_dir(internal_workspace) / _PROFILE_MD

    @staticmethod
    def skill_export_dir(internal_workspace: Path | str) -> Path:
        """v0.42 legacy 导出路径；只供迁移，不再作为技能包真源。"""
        return KnowledgeGraphManager.knowledge_dir(internal_workspace) / _SKILL_EXPORT_DIR

    @staticmethod
    def project_skill_dir(internal_workspace: Path | str) -> Path:
        """项目经验技能的独立真源（与 knowledge/assets 生命周期完全解耦）。"""
        return KnowledgeGraphManager.knowledge_dir(internal_workspace) / _PROJECT_SKILL_DIR

    @staticmethod
    def third_party_skill_dir() -> Path:
        configured = str(CONFIG.skill_third_party_dir or "").strip()
        if configured:
            return Path(configured).expanduser()
        return Path(CONFIG.data_dir).expanduser() / "skills" / "third_party"

    # ------------------------------------------------------------------
    # T1：任务收尾蒸馏（报告 §4.2）
    # ------------------------------------------------------------------
    async def distill_task(
        self,
        project_id: str,
        internal_workspace: Path | str,
        *,
        step: int | None,
        instruction_path: Path | str | None = None,
        report_path: Path | str | None = None,
        approval_path: Path | str | None = None,
        decision: str = "",
        worker_suggest: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """一步完整闭环 → 0–2 条候选资产，宁缺毋滥（多数任务的正确产出是零条）。

        输入除三件套全文外，还包括两个此前被丢弃的高价值信号：审批里的
        **拒绝理由/附言**（正文即是）与 report 六段里 worker 主动写的 suggest。
        """
        if not CONFIG.knowledge_distill_enabled or self._distill_call is None:
            return {"status": "skipped", "reason": "distill_disabled"}
        root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
        try:
            bundle = self._read_bundle(
                Path(internal_workspace),
                instruction_path=instruction_path,
                report_path=report_path,
                approval_path=approval_path,
            )
            if not bundle:
                return {"status": "skipped", "reason": "empty_bundle"}
            raw = await self._call_distiller(bundle, decision, worker_suggest)
            items = _validate_distilled(raw)
            if not items:
                return {"status": "processed", "created": [], "merged": []}

            async with self._graph.root_lock(internal_workspace):
                graph = self._graph.load_graph(project_id, root)
                valid_refs = {
                    str(src.get("ref") or "")
                    for src in (graph.get("sources") or {}).values()
                    if isinstance(src, dict)
                }
                created: list[str] = []
                merged: list[str] = []
                for item in items[:2]:                      # 每步 ≤2 条（报告）
                    ok, keep = self._check_evidence(graph, item, valid_refs)
                    if not ok:
                        log.info(msg("knowledge_assets.py.012"),
                                 project_id, item.get("title"))
                        continue
                    item["evidence"] = keep
                    target = self._find_duplicate(graph, item)
                    if target is not None:
                        self._merge_into(graph, target, item, step)
                        merged.append(str(target.get("id")))
                    else:
                        asset = self._create_asset(graph, item, step, metadata)
                        created.append(str(asset["id"]))
                if not created and not merged:
                    return {"status": "processed", "created": [], "merged": []}
                self._after_write(project_id, root, graph, internal_workspace, {
                    "type": "assets_distilled", "step": step,
                    "created": created, "merged": merged, "decision": decision,
                })
                return {"status": "processed", "created": created, "merged": merged}
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(msg("knowledge_assets.py.013"), project_id)
            return {"status": "failed"}

    async def _call_distiller(
        self, bundle: str, decision: str, worker_suggest: str,
    ) -> str:
        system = (
            msg("knowledge_assets.py.014") +
            msg("knowledge_assets.py.015") +
            msg("knowledge_assets.py.016") +
            msg("knowledge_assets.py.017") +
            msg("knowledge_assets.py.018") +
            msg("knowledge_assets.py.019") +
            msg("knowledge_assets.py.020") +
            msg("knowledge_assets.py.021") +
            msg("knowledge_assets.py.022") +
            msg("knowledge_assets.py.023") +
            '{"class":"preference|playbook|pitfall|fact|decision","title":msg("knowledge_assets.py.024"),'
            '"one_liner":msg("ka.025a"),"applies_when":msg("ka.025b"),'
            '"body_md":msg("knowledge_assets.py.026"),'
            '"anti_pattern":msg("knowledge_assets.py.027"),'
            '"evidence":[{"node_id":msg("ka.028a"),"source_ref":msg("ka.028b"),'
            '"excerpt":msg("knowledge_assets.py.029")}],"confidence":0.0}'
        )
        user = (
            msg("knowledge_assets.py.062", **{"decision": decision or msg("knowledge_assets.py.030")})
            + (msg("knowledge_assets.py.031", **{"_clip(worker_suggest, 200)": _clip(worker_suggest, 200)}) if worker_suggest.strip() else "")
            + "\n" + bundle
        )
        return await self._distill_call(system, user)

    def _read_bundle(
        self,
        internal_workspace: Path,
        *,
        instruction_path: Path | str | None,
        report_path: Path | str | None,
        approval_path: Path | str | None,
    ) -> str:
        """三件套全文（各截 6000 字符），并做路径合法性校验（只读 handoffs/ 之下）。"""
        handoff_root = (internal_workspace / "handoffs").resolve()
        parts: list[str] = []
        for label, raw in (
            ("Instruction", instruction_path),
            ("Report", report_path),
            ("Approval", approval_path),
        ):
            if raw is None:
                continue
            path = Path(raw)
            try:
                resolved = path.resolve()
                resolved.relative_to(handoff_root)
                if not resolved.is_file():
                    continue
                text = resolved.read_text("utf-8", errors="replace")[:6000]
                parts.append(f"── {label}（{path.name}）──\n{text}")
            except (OSError, ValueError):
                continue
        return "\n\n".join(parts)

    def _check_evidence(
        self, graph: dict[str, Any], item: dict[str, Any], valid_refs: set[str],
    ) -> tuple[bool, list[dict[str, Any]]]:
        """evidence 必填且必须指向真实节点/真实来源文件——防蒸馏幻觉（报告风险二）。"""
        keep: list[dict[str, Any]] = []
        for ev in item.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            node_id = str(ev.get("node_id") or "").strip()
            source_ref = str(ev.get("source_ref") or "").strip()
            node_ok = bool(node_id) and node_id in (graph.get("nodes") or {})
            ref_ok = bool(source_ref) and source_ref in valid_refs
            if not node_ok and not ref_ok:
                continue
            keep.append({
                "node_id": node_id if node_ok else "",
                "source_ref": source_ref if ref_ok else "",
                "excerpt": _clip(str(ev.get("excerpt") or ""), 160),
            })
        return (len(keep) > 0, keep[:6])

    def _find_duplicate(
        self, graph: dict[str, Any], item: dict[str, Any],
    ) -> dict[str, Any] | None:
        norm = _normalize_title(str(item.get("title") or ""))
        hay_new = f"{item.get('title')} {item.get('one_liner')} {item.get('applies_when')}"
        best: tuple[float, dict[str, Any] | None] = (0.0, None)
        for asset in (graph.get("assets") or {}).values():
            if not isinstance(asset, dict) or asset.get("class") != item.get("class"):
                continue
            if asset.get("status") == "retired":
                continue
            if norm and _normalize_title(str(asset.get("title") or "")) == norm:
                return asset
            hay_old = f"{asset.get('title')} {asset.get('one_liner')} {asset.get('applies_when')}"
            sim = _jaccard(_tokenize(hay_new), _tokenize(hay_old))
            if sim > best[0]:
                best = (sim, asset)
        return best[1] if best[0] >= _DEDUP_SIM else None

    def _merge_into(
        self, graph: dict[str, Any], asset: dict[str, Any],
        item: dict[str, Any], step: int | None,
    ) -> None:
        """同型再现 → 只并证据、抬置信，不改用户见过的正文（策展权在用户）。"""
        existing = {(e.get("node_id"), e.get("source_ref"))
                    for e in asset.get("evidence") or [] if isinstance(e, dict)}
        for ev in item.get("evidence") or []:
            key = (ev.get("node_id"), ev.get("source_ref"))
            if key in existing:
                continue
            asset.setdefault("evidence", []).append(ev)
            existing.add(key)
        asset["evidence"] = (asset.get("evidence") or [])[:10]
        asset["confidence"] = round(min(
            0.98, max(_float01(asset.get("confidence"), 0.6),
                      _float01(item.get("confidence"), 0.6)) + 0.05), 4)
        asset["updated_at"] = _now_iso()
        if step is not None:
            asset.setdefault("origin", {}).setdefault("steps", [])
            if step not in asset["origin"]["steps"]:
                asset["origin"]["steps"] = (asset["origin"]["steps"] + [step])[-12:]
        # 偏好过拟合对策（报告风险一）：单证据 seed，第二个**独立来源**到齐才转 candidate。
        if asset.get("class") == "preference" and asset.get("status") == "seed":
            distinct = {e.get("source_ref") or e.get("node_id")
                        for e in asset.get("evidence") or []}
            if len(distinct) >= 2:
                asset["status"] = "candidate"

    def _create_asset(
        self, graph: dict[str, Any], item: dict[str, Any],
        step: int | None, metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        cls = str(item["class"])
        seq = dict(graph.get("asset_seq") or {})
        n = int(seq.get(cls) or 0) + 1
        seq[cls] = n
        graph["asset_seq"] = seq
        asset_id = f"{_CLASS_PREFIX[cls]}-{n:02d}"
        status = "seed" if (cls == "preference"
                            and len(item.get("evidence") or []) < 2) else "candidate"
        now = _now_iso()
        asset: dict[str, Any] = {
            "id": asset_id,
            "class": cls,
            "title": _clip(str(item.get("title") or ""), 40),
            "one_liner": _clip(str(item.get("one_liner") or ""), 90),
            "applies_when": _clip(str(item.get("applies_when") or ""), 60),
            "body_md": str(item.get("body_md") or "").strip(),
            "anti_pattern": _clip(str(item.get("anti_pattern") or ""), 400),
            "evidence": item.get("evidence") or [],
            "attachments": [],
            "confidence": _float01(item.get("confidence"), 0.6),
            "status": status,
            "user_status": None,
            "scope": "project",
            "scope_set_by": "system",
            "user_category": None,
            "needs_review": False,
            "retire_suggested": False,
            "conflict_with": [],
            "created_at": now,
            "updated_at": now,
            "reviewed_at": None,
            "reviewed_action": None,
            "usage_events": [],
            "metrics": {},
            "origin": {
                "kind": "distill",
                "steps": [step] if step is not None else [],
                "agent_id": str((metadata or {}).get("agent_id") or ""),
            },
        }
        graph.setdefault("assets", {})[asset_id] = asset
        return asset

    # ------------------------------------------------------------------
    # 使用闭环（报告 §4.5）
    # ------------------------------------------------------------------
    async def record_matched(
        self, project_id: str, internal_workspace: Path | str,
        step: int, asset_ids: list[str],
    ) -> None:
        """指令条件化注入附了哪些 L0 行——matched_never_used 信号的前半。"""
        ids = [i for i in (str(a) for a in asset_ids) if _ASSET_ID_RX.match(i)]
        if not ids:
            return
        root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
        try:
            async with self._graph.root_lock(internal_workspace):
                graph = self._graph.load_graph(project_id, root)
                pend = graph.setdefault("asset_step_matches", {})
                pend[str(step)] = _unique_strings([*(pend.get(str(step)) or []), *ids])[:8]
                self._graph.save_graph(root, graph)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(msg("knowledge_assets.py.032"), project_id)

    async def record_usage(
        self, project_id: str, internal_workspace: Path | str,
        *, step: int, used: list[str], not_helpful: list[str],
        suggest: str = "", decision: str | None = None,
    ) -> dict[str, Any]:
        """report 六段「知识引用」落账：结算 used/not_helpful/matched_never_used。

        decision（该步审批的 approved/rejected）此刻若已知就直接结算；
        若这一步稍后被 rejected，resolve_step_decision 会把同步引用翻成负信号。
        """
        root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
        try:
            async with self._graph.root_lock(internal_workspace):
                graph = self._graph.load_graph(project_id, root)
                assets = graph.get("assets") or {}
                now = _now_iso()
                used_ids = [i for i in (str(a).strip() for a in used) if i in assets]
                nh_ids = [i for i in (str(a).strip() for a in not_helpful) if i in assets]
                ok_kind = _SIG_OK if (decision or "approved") == "approved" else _SIG_BAD
                for aid in used_ids:
                    self._push_usage(assets[aid], ok_kind, step, now)
                for aid in nh_ids:
                    self._push_usage(assets[aid], _SIG_NOT_HELPFUL, step, now)
                matched = (graph.get("asset_step_matches") or {}).pop(str(step), [])
                touched = set(used_ids) | set(nh_ids)
                for aid in matched:
                    if aid in assets and aid not in touched:
                        self._push_usage(assets[aid], _SIG_IGNORED, step, now)
                if suggest.strip():
                    stash = graph.setdefault("asset_suggests", [])
                    stash.append({"step": step, "text": _clip(suggest, 200), "at": now})
                    graph["asset_suggests"] = stash[-40:]
                self._after_write(project_id, root, graph, internal_workspace, {
                    "type": "asset_usage", "step": step,
                    "used": used_ids, "not_helpful": nh_ids,
                    "ignored": [a for a in matched if a not in touched],
                })
                return {"ok": True, "used": used_ids, "not_helpful": nh_ids}
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(msg("knowledge_assets.py.033"), project_id)
            return {"ok": False}

    async def resolve_step_decision(
        self, project_id: str, internal_workspace: Path | str,
        *, step: int, decision: str,
    ) -> None:
        """审批后置落地（先交报告、后出决议的顺序）：把该步引用信号对齐最终决定。"""
        if decision not in {"approved", "rejected"}:
            return
        root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
        try:
            async with self._graph.root_lock(internal_workspace):
                graph = self._graph.load_graph(project_id, root)
                changed = False
                want = _SIG_OK if decision == "approved" else _SIG_BAD
                other = _SIG_BAD if decision == "approved" else _SIG_OK
                for asset in (graph.get("assets") or {}).values():
                    if not isinstance(asset, dict):
                        continue
                    for ev in asset.get("usage_events") or []:
                        if ev.get("step") == step and ev.get("kind") == other:
                            ev["kind"] = want
                            changed = True
                if changed:
                    self._after_write(project_id, root, graph, internal_workspace, {
                        "type": "asset_usage_resolved", "step": step, "decision": decision,
                    })
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[%s] resolve_step_decision 失败（忽略）", project_id)

    @staticmethod
    def _push_usage(asset: dict[str, Any], kind: str, step: int, at: str) -> None:
        events = [e for e in asset.get("usage_events") or [] if isinstance(e, dict)]
        # 同一步同一种信号只记一次（重交报告幂等）。
        events = [e for e in events if not (e.get("step") == step and e.get("kind") == kind)]
        events.append({"kind": kind, "step": step, "at": at})
        asset["usage_events"] = events[-60:]

    # ------------------------------------------------------------------
    # 计分与晋升（utility 替代 importance；情节层各算各的）
    # ------------------------------------------------------------------
    def refresh_assets(self, graph: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        for asset in (graph.get("assets") or {}).values():
            if not isinstance(asset, dict):
                continue
            events = [e for e in asset.get("usage_events") or [] if isinstance(e, dict)]
            ok = [e for e in events if e.get("kind") == _SIG_OK]
            bad = [e for e in events if e.get("kind") == _SIG_BAD]
            nh = [e for e in events if e.get("kind") == _SIG_NOT_HELPFUL]
            ignored = [e for e in events if e.get("kind") == _SIG_IGNORED]
            n_ok, n_bad = len(ok), len(bad) + len(nh)
            success = (n_ok + 0.5 * _UTILITY_PRIOR) / (
                n_ok + n_bad + 0.5 * len(ignored) + _UTILITY_PRIOR)
            freq = math.log1p(n_ok + n_bad) / math.log(6.0)
            last_used = max((str(e.get("at") or "") for e in (ok + bad + nh)), default="")
            fresh_ref = last_used or str(asset.get("updated_at") or asset.get("created_at") or "")
            freshness = _decay(fresh_ref, now, _USAGE_HALF_LIFE_DAYS)
            user_term = 0.5
            if asset.get("user_status") == "active" or asset.get("scope_set_by") == "user":
                user_term = 1.0
            elif asset.get("reviewed_action") == "approve":
                user_term = 0.8
            utility = (0.45 * success + 0.20 * min(1.0, freq)
                       + 0.15 * freshness + 0.20 * user_term)

            # 连续 matched_never_used → 建议退役（T2 也会核，但计分处先立 flag 的事实基础）
            tail = [e.get("kind") for e in events][-_RETIRE_SUGGEST_IGNORED:]
            ignored_streak = (len(tail) == _RETIRE_SUGGEST_IGNORED
                              and all(k == _SIG_IGNORED for k in tail))

            distinct_ok = len({e.get("step") for e in ok})
            neg_recent = any(
                (stamp := _parse_timestamp(e.get("at"))) is not None
                and (now - stamp).days <= _CORE_NEGATIVE_WINDOW_DAYS
                for e in (bad + nh)
            )
            status = str(asset.get("status") or "candidate")
            if status == "validated" and distinct_ok >= _CORE_MIN_DISTINCT_OK and not neg_recent:
                status = "core"                       # 晋升靠真实使用，不靠纸面分（报告 §4.5）
            elif status == "core" and neg_recent:
                status = "validated"                  # 负信号 → 降回，等 T2/用户再裁
            asset["status"] = status

            asset["metrics"] = {
                "utility": round(utility, 4),
                "use_count": n_ok + n_bad,
                "cited_ok": n_ok,
                "cited_bad": n_bad,
                "ignored": len(ignored),
                "distinct_ok_tasks": distinct_ok,
                "freshness": round(freshness, 4),
                "last_used": last_used or None,
                "ignored_streak": ignored_streak,
            }
            # 用户裁决压顶（与情节层同一手法：每次重算后再套用，永不丢失）。
            if asset.get("user_status") == "retired":
                asset["status"] = "retired"
            elif asset.get("user_status") == "active" and asset["status"] == "retired":
                asset["status"] = "validated"

    # ------------------------------------------------------------------
    # T2：周期性合并（去重 / case→rule / 冲突 / 退役建议）
    # ------------------------------------------------------------------
    async def consolidate(
        self, project_id: str, internal_workspace: Path | str,
    ) -> dict[str, Any]:
        root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
        summary = {"merged": 0, "induced": 0, "conflicts": 0, "retire_suggested": 0}
        try:
            async with self._graph.root_lock(internal_workspace):
                graph = self._graph.load_graph(project_id, root)
                assets = {k: v for k, v in (graph.get("assets") or {}).items()
                          if isinstance(v, dict) and v.get("status") != "retired"}
                if not assets:
                    return {"status": "processed", **summary}

                summary["merged"] = self._pass_dedup(graph, assets)
                summary["induced"] = await self._pass_induce(graph, assets)
                summary["conflicts"] = await self._pass_conflicts(graph, assets)
                summary["retire_suggested"] = self._pass_retire_suggest(assets)

                self._after_write(project_id, root, graph, internal_workspace, {
                    "type": "assets_consolidated", **summary,
                })
                return {"status": "processed", **summary}
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[%s] T2 合并失败（忽略）", project_id)
            return {"status": "failed", **summary}

    def _pass_dedup(self, graph: dict[str, Any], assets: dict[str, Any]) -> int:
        merged = 0
        ids = sorted(assets)
        gone: set[str] = set()
        for i, a_id in enumerate(ids):
            if a_id in gone:
                continue
            a = assets[a_id]
            for b_id in ids[i + 1:]:
                if b_id in gone:
                    continue
                b = assets[b_id]
                if a.get("class") != b.get("class"):
                    continue
                sim = _jaccard(
                    _tokenize(f"{a.get('title')} {a.get('one_liner')}"),
                    _tokenize(f"{b.get('title')} {b.get('one_liner')}"),
                )
                if sim < _MERGE_SIM:
                    continue
                self._merge_into(graph, a, {
                    "evidence": b.get("evidence") or [],
                    "confidence": b.get("confidence"),
                }, None)
                a["usage_events"] = (
                    (a.get("usage_events") or []) + (b.get("usage_events") or []))[-60:]
                graph["assets"].pop(b_id, None)
                gone.add(b_id)
                merged += 1
        return merged

    async def _pass_induce(self, graph: dict[str, Any], assets: dict[str, Any]) -> int:
        """case→rule：≥3 条同型 pitfall/fact 情节 → 归纳 1 条 playbook 候选，原条目降为其证据。"""
        if self._distill_call is None:
            return 0
        cases = [a for a in assets.values()
                 if a.get("class") in {"pitfall", "fact"} and not a.get("rolled_into")]
        clusters: list[list[dict[str, Any]]] = []
        for asset in cases:
            tokens = _tokenize(f"{asset.get('title')} {asset.get('one_liner')} {asset.get('applies_when')}")
            for cluster in clusters:
                head = cluster[0]
                head_tokens = _tokenize(f"{head.get('title')} {head.get('one_liner')}")
                if _jaccard(tokens, head_tokens) >= _CLUSTER_SIM:
                    cluster.append(asset)
                    break
            else:
                clusters.append([asset])
        induced = 0
        for cluster in clusters:
            if len(cluster) < _CLUSTER_MIN:
                continue
            digest = "\n".join(
                f"- [{a.get('id')}] {a.get('title')}：{a.get('one_liner')}" for a in cluster)
            try:
                raw = await self._distill_call(
                    msg("knowledge_assets.py.034") +
                    msg("knowledge_assets.py.035") +
                    msg("knowledge_assets.py.036") +
                    msg("knowledge_assets.py.037"),
                    digest,
                )
            except Exception:
                continue
            items = _validate_distilled(raw, require_evidence=False)
            if not items:
                continue
            item = items[0]
            item["class"] = "playbook"
            item["evidence"] = [ev for a in cluster
                                for ev in (a.get("evidence") or [])][:8]
            if not item["evidence"]:
                continue
            asset = self._create_asset(graph, item, None, None)
            asset["origin"] = {"kind": "consolidate",
                               "rolled_from": [a.get("id") for a in cluster]}
            for a in cluster:
                a["rolled_into"] = asset["id"]
            induced += 1
        return induced

    async def _pass_conflicts(self, graph: dict[str, Any], assets: dict[str, Any]) -> int:
        """冲突识别 → 双双送「待审」（needs_review），让用户裁决（复用 v0.41 待审通道）。"""
        if self._distill_call is None:
            return 0
        rows = [a for a in assets.values() if a.get("status") in {"candidate", "validated", "core"}]
        if len(rows) < 2:
            return 0
        digest = "\n".join(f"- [{a.get('id')}] {a.get('one_liner')}" for a in rows[:40])
        try:
            raw = await self._distill_call(
                msg("knowledge_assets.py.063") + msg("knowledge_assets.py.064") + msg("knowledge_assets.py.065"),
                digest,
            )
        except Exception:
            return 0
        data = _parse_json_object(f'{{"pairs": {raw.strip() or "[]"} }}') or {}
        pairs = data.get("pairs") if isinstance(data.get("pairs"), list) else []
        count = 0
        for pair in pairs[:6]:
            if not isinstance(pair, dict):
                continue
            a = (graph.get("assets") or {}).get(str(pair.get("a") or ""))
            b = (graph.get("assets") or {}).get(str(pair.get("b") or ""))
            if not isinstance(a, dict) or not isinstance(b, dict) or a is b:
                continue
            for x, other in ((a, b), (b, a)):
                x["needs_review"] = True
                x["conflict_with"] = _unique_strings(
                    [*(x.get("conflict_with") or []), other.get("id")])[:4]
            count += 1
        return count

    @staticmethod
    def _pass_retire_suggest(assets: dict[str, Any]) -> int:
        count = 0
        for asset in assets.values():
            metrics = asset.get("metrics") or {}
            recent = [e.get("kind") for e in (asset.get("usage_events") or [])[-2:]]
            bad_tail = (len(recent) == 2
                        and all(k in {_SIG_BAD, _SIG_NOT_HELPFUL} for k in recent))
            if (metrics.get("ignored_streak") or bad_tail) and not asset.get("retire_suggested"):
                asset["retire_suggested"] = True
                asset["needs_review"] = True
                count += 1
        return count

    # ------------------------------------------------------------------
    # 策展入口（视图数据面调用；均与 ingest 同锁）
    # ------------------------------------------------------------------
    async def review(
        self, project_id: str, internal_workspace: Path | str,
        asset_id: str, action: str,
    ) -> dict[str, Any]:
        """approve：candidate→validated / 清 needs_review；reject：candidate→retired。"""
        if action not in {"approve", "reject"}:
            return {"ok": False, "reason": "bad_action"}
        root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
        try:
            async with self._graph.root_lock(internal_workspace):
                graph = self._graph.load_graph(project_id, root)
                asset = (graph.get("assets") or {}).get(str(asset_id))
                if not isinstance(asset, dict):
                    return {"ok": False, "reason": "asset_not_found"}
                now = _now_iso()
                asset["reviewed_at"] = now
                asset["reviewed_action"] = action
                asset["needs_review"] = False
                asset["retire_suggested"] = False
                if action == "approve":
                    if asset.get("status") in {"seed", "candidate"}:
                        asset["status"] = "validated"
                    asset["user_status"] = None if asset.get("user_status") == "retired" \
                        else asset.get("user_status")
                else:
                    asset["status"] = "retired"
                    asset["user_status"] = "retired"
                asset["updated_at"] = now
                self._after_write(project_id, root, graph, internal_workspace, {
                    "type": "asset_review", "asset_id": asset_id, "action": action,
                })
                return {"ok": True, "asset": self._public_asset(graph, asset)}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception(msg("knowledge_assets.py.038"), project_id, asset_id)
            return {"ok": False, "reason": type(exc).__name__}

    async def apply_user_override(
        self, project_id: str, internal_workspace: Path | str, asset_id: str,
        *,
        title: str | None = None,
        scope: str | None = None,
        category: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """用户裁决四件事（与视图右键一一对应）：

        · title    —— **重命名**：只改标题，正文一字不动（新增需求 2①）；
        · scope    —— 调整范围二级：global/project，harness 复用方向实时改；
        · category —— 调整范围三级：约定/坑/模式/清单，价值判断实时改；
        · status   —— retired（停用不删除）/ active（恢复启用）。
        """
        root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
        try:
            async with self._graph.root_lock(internal_workspace):
                graph = self._graph.load_graph(project_id, root)
                asset = (graph.get("assets") or {}).get(str(asset_id))
                if not isinstance(asset, dict):
                    return {"ok": False, "reason": "asset_not_found"}
                changed: list[str] = []
                if title is not None:
                    clean = _clip(str(title).strip(), 40)
                    if clean and clean != asset.get("title"):
                        asset["title"] = clean
                        changed.append("title")
                if scope is not None:
                    if scope not in {"global", "project"}:
                        return {"ok": False, "reason": "bad_scope"}
                    if asset.get("scope") != scope:
                        asset["scope"] = scope
                        changed.append("scope")
                    asset["scope_set_by"] = "user"
                if category is not None:
                    if category not in CATEGORIES:
                        return {"ok": False, "reason": "bad_category"}
                    default = _CLASS_TO_CATEGORY.get(str(asset.get("class")), msg("knowledge_assets.py.039"))
                    want = None if category == default else category
                    if asset.get("user_category") != want:
                        asset["user_category"] = want
                        changed.append("category")
                if status is not None:
                    if status not in {"retired", "active"}:
                        return {"ok": False, "reason": "bad_status"}
                    asset["user_status"] = status
                    changed.append("status")
                if not changed:
                    self.refresh_assets(graph)
                    return {"ok": True, "changed": [],
                            "asset": self._public_asset(graph, asset)}
                asset["updated_at"] = _now_iso()
                self._after_write(project_id, root, graph, internal_workspace, {
                    "type": "asset_user_override", "asset_id": asset_id,
                    "changed": changed, "scope": asset.get("scope"),
                    "category": self._effective_category(asset),
                    "user_status": asset.get("user_status"),
                })
                return {"ok": True, "changed": changed,
                        "asset": self._public_asset(graph, asset)}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception(msg("knowledge_assets.py.040"), project_id, asset_id)
            return {"ok": False, "reason": type(exc).__name__}

    async def purge(
        self, project_id: str, internal_workspace: Path | str, asset_id: str,
    ) -> dict[str, Any]:
        """彻底删除（新增需求 2④）：目录 + 元数据 + 导出的 skill 一并移除，**不可逆**。"""
        root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
        try:
            async with self._graph.root_lock(internal_workspace):
                graph = self._graph.load_graph(project_id, root)
                asset = (graph.get("assets") or {}).pop(str(asset_id), None)
                if asset is None:
                    return {"ok": False, "reason": "asset_not_found"}
                for base in (self.assets_dir(internal_workspace),
                             self.skill_export_dir(internal_workspace)):
                    target = (base / str(asset_id)).resolve()
                    try:
                        target.relative_to(base.resolve())   # 越界防御
                        if target.is_dir():
                            shutil.rmtree(target, ignore_errors=True)
                    except (OSError, ValueError):
                        pass
                self._after_write(project_id, root, graph, internal_workspace, {
                    "type": "asset_purged", "asset_id": asset_id,
                    "title": (asset or {}).get("title"),
                })
                return {"ok": True, "asset_id": asset_id}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("[%s] asset purge 失败（忽略）：%s", project_id, asset_id)
            return {"ok": False, "reason": type(exc).__name__}

    # ------------------------------------------------------------------
    # 读投影（HTTP 线程直读安全：只读预计算 + 原子写真源）
    # ------------------------------------------------------------------
    def snapshot(
        self, project_id: str, internal_workspace: Path | str, *, limit: int = 500,
    ) -> dict[str, Any]:
        empty = {"total": 0, "ok": 0, "pending": 0, "retired": 0}
        try:
            root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
            graph = self._graph.load_graph(project_id, root)
            self.refresh_assets(graph)
            sources = graph.get("sources") or {}
            rows: list[dict[str, Any]] = []
            counts = dict(empty)
            assets = [a for a in (graph.get("assets") or {}).values()
                      if isinstance(a, dict) and a.get("status") != "seed"]
            assets.sort(key=lambda a: str(a.get("updated_at") or ""), reverse=True)
            for asset in assets:
                st = self._view_state(asset)
                counts["total"] += 1
                counts[st] += 1
            for asset in assets[:_bounded(limit, 1, 2000)]:
                rows.append(self._public_asset(graph, asset, sources=sources))
            return {
                "project_id": project_id,
                "revision": graph.get("revision", 0),
                "updated_at": graph.get("updated_at"),
                "generated_at": _now_iso(),
                "counts": counts,
                "assets": rows,
                "profile_exists": self.profile_path(internal_workspace).is_file(),
            }
        except Exception:
            log.exception("[%s] assets snapshot 失败（回落为空）", project_id)
            return {"project_id": project_id, "revision": 0, "updated_at": None,
                    "generated_at": _now_iso(), "counts": dict(empty),
                    "assets": [], "profile_exists": False}

    def read_asset(
        self, project_id: str, internal_workspace: Path | str, asset_id: str,
    ) -> dict[str, Any]:
        """L1 全文（工具 read_knowledge_asset 与视图「预览」共用）。"""
        try:
            root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
            graph = self._graph.load_graph(project_id, root)
            self.refresh_assets(graph)
            asset = (graph.get("assets") or {}).get(str(asset_id))
            if not isinstance(asset, dict):
                return {"found": False, "asset_id": str(asset_id)}
            body = self._asset_md_text(internal_workspace, asset)
            row = self._public_asset(graph, asset, sources=graph.get("sources") or {})
            row["body_md"] = body
            row["usage_events"] = list(asset.get("usage_events") or [])[-30:]
            return {"found": True, "asset": row}
        except Exception:
            log.exception(msg("knowledge_assets.py.041"), project_id)
            return {"found": False, "asset_id": str(asset_id)}

    def has_assets(self, project_id: str, internal_workspace: Path | str) -> bool:
        try:
            root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
            graph = self._graph.load_graph(project_id, root)
            return any(
                isinstance(a, dict) and a.get("status") not in {"seed", "retired"}
                for a in (graph.get("assets") or {}).values()
            )
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 注入协议（报告 §4.4：渐进披露的机械实现）
    # ------------------------------------------------------------------
    def context_block(self, project_id: str, internal_workspace: Path | str) -> str:
        """常驻块 = PROFILE 全文 + 资产索引 L0 行（替换 brief 硬塞）。"""
        try:
            parts: list[str] = []
            profile = self.profile_text(internal_workspace).strip()
            if profile:
                parts.append(msg("knowledge_assets.py.066") + profile)
            index = self._index_lines(project_id, internal_workspace)
            if index:
                parts.append(
                    msg("knowledge_assets.py.067") + msg("knowledge_assets.py.068") + "\n".join(index)
                )
            return "\n\n".join(parts)
        except Exception:
            return ""

    def _index_lines(self, project_id: str, internal_workspace: Path | str) -> list[str]:
        root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
        graph = self._graph.load_graph(project_id, root)
        self.refresh_assets(graph)
        rows = [
            a for a in (graph.get("assets") or {}).values()
            if isinstance(a, dict) and a.get("status") in {"validated", "core"}
            # 索引优先 playbook/pitfall/fact/decision；preference 走 PROFILE 不占行（报告风险三）
            and a.get("class") != "preference"
        ]
        rows.sort(key=lambda a: float((a.get("metrics") or {}).get("utility") or 0.0),
                  reverse=True)
        limit = _bounded(CONFIG.knowledge_index_max_lines, 4, 40)
        lines: list[str] = []
        for asset in rows[:limit]:
            lines.append(self._l0_line(asset))
        return lines

    def _l0_line(self, asset: dict[str, Any]) -> str:
        cat = self._effective_category(asset)
        marker = _category_marker(cat)
        applies = str(asset.get("applies_when") or "").strip()
        tail = msg("knowledge_assets.py.069", applies=applies) if applies else ""
        return _clip(
            f"- [{asset.get('id')}] {marker}｜{asset.get('title')}："
            f"{asset.get('one_liner')}{tail}", 140)

    def match_for_task(
        self, project_id: str, internal_workspace: Path | str, task_text: str,
        *, top: int | None = None,
    ) -> list[dict[str, Any]]:
        """指令条件化注入：对指令正文做词法匹配，返回 top-N 资产（L0 行 + 方向提示）。"""
        try:
            root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
            graph = self._graph.load_graph(project_id, root)
            self.refresh_assets(graph)
            q = _tokenize(task_text)
            if not q:
                return []
            ranked: list[tuple[float, dict[str, Any]]] = []
            for asset in (graph.get("assets") or {}).values():
                if not isinstance(asset, dict):
                    continue
                if asset.get("status") not in {"validated", "core"}:
                    continue
                hay = _tokenize(
                    f"{asset.get('title')} {asset.get('one_liner')} "
                    f"{asset.get('applies_when')} {asset.get('body_md')}")
                inter = q & hay
                if not inter:
                    continue
                # 中文 n-gram 让并集膨胀，纯 Jaccard 会漏掉真相关的短查询；
                # 混入重叠系数（|∩|/min）。召回略宽没关系——命中只是附一行 L0，
                # 展开与否由 Worker 判断（渐进披露允许索引宽进）。
                overlap = len(inter) / max(1, min(len(q), len(hay)))
                lexical = 0.5 * _jaccard(q, hay) + 0.5 * overlap
                if lexical < 0.04:
                    continue
                utility = float((asset.get("metrics") or {}).get("utility") or 0.0)
                ranked.append((lexical * 0.75 + utility * 0.25, asset))
            ranked.sort(key=lambda x: x[0], reverse=True)
            n = _bounded(top if top is not None else CONFIG.knowledge_task_match_top, 1, 6)
            out: list[dict[str, Any]] = []
            for score, asset in ranked[:n]:
                cat = self._effective_category(asset)
                out.append({
                    "asset_id": asset.get("id"),
                    "line": self._l0_line(asset),
                    "hint": _category_agent_hint(cat),
                    "category": cat,
                    "score": round(score, 4),
                })
            return out
        except Exception:
            log.exception(msg("knowledge_assets.py.042"), project_id)
            return []

    @staticmethod
    def related_block(matches: list[dict[str, Any]]) -> str:
        """写进 instruction 的「相关知识」区块正文（Harness 填，worker 只管展开）。"""
        if not matches:
            return ""
        lines = [msg("knowledge_assets.py.070")]
        for m in matches:
            lines.append(str(m.get("line") or ""))
            hint = str(m.get("hint") or "")
            if hint:
                lines.append(f"  ↳ {hint}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 画像层 PROFILE.md（报告 §4.1：偏好不是搜出来的，是须臾不可离身的）
    # ------------------------------------------------------------------
    def profile_text(self, internal_workspace: Path | str) -> str:
        try:
            path = self.profile_path(internal_workspace)
            if path.is_file():
                return path.read_text("utf-8", errors="replace")
        except OSError:
            pass
        return ""

    async def set_profile(
        self, project_id: str, internal_workspace: Path | str, text: str,
    ) -> dict[str, Any]:
        """用户直改画像（视图入口）：改完即锁定，编译器不再覆盖（用户话语权最高）。"""
        root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
        try:
            async with self._graph.root_lock(internal_workspace):
                graph = self._graph.load_graph(project_id, root)
                max_lines = _bounded(CONFIG.knowledge_profile_max_lines, 10, 80)
                lines = [ln.rstrip() for ln in str(text or "").splitlines()][:max_lines]
                _atomic_write(self.profile_path(internal_workspace),
                              "\n".join(lines).strip() + "\n")
                graph["profile_locked"] = True
                graph["updated_at"] = _now_iso()
                self._graph.save_graph(root, graph)
                self._graph.append_event(root, {
                    "type": "profile_user_edited", "project_id": project_id,
                    "at": _now_iso(),
                })
                return {"ok": True}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception(msg("knowledge_assets.py.043"), project_id)
            return {"ok": False, "reason": type(exc).__name__}

    def _compile_profile(
        self, graph: dict[str, Any], internal_workspace: Path | str,
    ) -> None:
        """把置信度最高的 preference 编译成 PROFILE.md（用户锁定后不再覆盖）。"""
        if graph.get("profile_locked"):
            return
        prefs = [
            a for a in (graph.get("assets") or {}).values()
            if isinstance(a, dict) and a.get("class") == "preference"
            and a.get("status") in {"validated", "core"}
        ]
        prefs.sort(key=lambda a: (
            0 if a.get("status") == "core" else 1,
            -float(a.get("confidence") or 0.0),
            -float((a.get("metrics") or {}).get("utility") or 0.0),
        ))
        max_lines = _bounded(CONFIG.knowledge_profile_max_lines, 10, 80)
        lines = [
            "# 用户画像（Knowe 自动编译；在知识库视图可直接改，改后系统不再覆盖）",
            "",
        ]
        for asset in prefs:
            if len(lines) >= max_lines:
                break
            lines.append(f"- [{asset.get('id')}] {asset.get('one_liner')}")
        if len(lines) <= 2:
            # 没有已验证偏好就不落文件——空画像不该占常驻 token。
            try:
                path = self.profile_path(internal_workspace)
                if path.is_file() and "自动编译" in path.read_text("utf-8", errors="replace")[:80]:
                    path.unlink()
            except OSError:
                pass
            return
        _atomic_write(self.profile_path(internal_workspace),
                      "\n".join(lines).strip() + "\n")

    # ------------------------------------------------------------------
    # 落盘：ASSET.md（Harness 渲染器 —— 排版由代码保证，不由 LLM 心情决定）
    # ------------------------------------------------------------------
    def _after_write(
        self, project_id: str, root: Path, graph: dict[str, Any],
        internal_workspace: Path | str, event: dict[str, Any],
    ) -> None:
        """一切写路径的统一收尾：重算 → 渲染 md → 编译画像 → 同步技能候选 → 落真源。"""
        self.refresh_assets(graph)
        graph["revision"] = int(graph.get("revision") or 0) + 1
        graph["updated_at"] = _now_iso()
        self._render_all_assets(internal_workspace, graph)
        self._compile_profile(graph, internal_workspace)
        # [v0.43] core 资产只是「产生一个项目经验技能候选」；它不再直接成为生效技能。
        # 技能有独立注册表，首次导出固定 pending，之后由用户单独批准/退役。
        self._sync_project_experience_skills(project_id, internal_workspace, graph)
        self._graph.save_graph(root, graph)
        self._graph.append_event(root, {
            **event, "project_id": project_id,
            "revision": graph["revision"], "at": _now_iso(),
        })

    def _render_all_assets(
        self, internal_workspace: Path | str, graph: dict[str, Any],
    ) -> None:
        base = self.assets_dir(internal_workspace)
        expected: set[str] = set()
        for asset in (graph.get("assets") or {}).values():
            if not isinstance(asset, dict):
                continue
            asset_id = str(asset.get("id") or "")
            if not asset_id:
                continue
            expected.add(asset_id)
            _atomic_write(base / asset_id / _ASSET_MD, self._render_asset_md(asset))
        # 清孤儿目录（只清我们自己管的：目录里有 ASSET.md 才算）。
        try:
            if base.is_dir():
                for child in base.iterdir():
                    if (child.is_dir() and child.name not in expected
                            and (child / _ASSET_MD).is_file()):
                        shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass

    def _render_asset_md(self, asset: dict[str, Any]) -> str:
        """确定性 markdown 渲染器。

        「harness 机制在制作知识卡片时就要保证其格式为 markdown 并排版好看」——
        标题层级、空行、列表、反例、证据脚注全部由这里统一产出；蒸馏 LLM 只提供
        素材（body_md），排版不靠它自觉。重命名只改 title/H1，正文永不被改写。
        """
        cat = self._effective_category(asset)
        front = [
            "---",
            f"id: {asset.get('id')}",
            f"class: {asset.get('class')}",
            f"category: {cat}",
            f"title: {asset.get('title')}",
            f"one_liner: {asset.get('one_liner')}",
            f"applies_when: {asset.get('applies_when')}",
            f"status: {asset.get('status')}",
            f"scope: {asset.get('scope')}",
            f"utility: {float((asset.get('metrics') or {}).get('utility') or 0.0):.2f}",
            "evidence: [" + ", ".join(
                str(e.get("node_id") or e.get("source_ref") or "")
                for e in (asset.get("evidence") or []) if isinstance(e, dict)) + "]",
            "attachments: [" + ", ".join(
                str(a) for a in asset.get("attachments") or []) + "]",
            f"updated: {asset.get('updated_at')}",
            "managed_by: knowe",
            "---",
        ]
        lines: list[str] = ["", f"# {asset.get('title')}", ""]
        lines += [f"> {_CLASS_ZH.get(str(asset.get('class')), msg('knowledge_assets.py.044'))} · {cat}"
                  f" · {asset.get('one_liner')}", ""]
        applies = str(asset.get("applies_when") or "").strip()
        if applies:
            lines += [msg("knowledge_assets.py.045"), "", applies, ""]
        lines += [msg("knowledge_assets.py.046"), ""]
        lines += _normalize_md_body(str(asset.get("body_md") or "")) + [""]
        anti = str(asset.get("anti_pattern") or "").strip()
        if anti:
            lines += [msg("knowledge_assets.py.047"), ""]
            lines += _normalize_md_body(anti) + [""]
        evidence = [e for e in asset.get("evidence") or [] if isinstance(e, dict)]
        if evidence:
            lines += [msg("knowledge_assets.py.048"), ""]
            for ev in evidence:
                ref = str(ev.get("source_ref") or "").strip()
                nid = str(ev.get("node_id") or "").strip()
                head = ref or nid or msg("knowledge_assets.py.049")
                pointer = f"`{nid}`" if nid else ""
                excerpt = str(ev.get("excerpt") or "").strip()
                seg = f"- **{head}** {pointer}".rstrip()
                if excerpt:
                    seg += f" —— {excerpt}"
                lines.append(seg)
            lines.append("")
        return "\n".join(front) + "\n" + "\n".join(lines).rstrip() + "\n"

    def _asset_md_text(
        self, internal_workspace: Path | str, asset: dict[str, Any],
    ) -> str:
        path = self.assets_dir(internal_workspace) / str(asset.get("id") or "") / _ASSET_MD
        try:
            if path.is_file():
                return path.read_text("utf-8", errors="replace")
        except OSError:
            pass
        return self._render_asset_md(asset)          # 文件丢了 → 元数据现渲染（自愈）

    # ------------------------------------------------------------------
    # [v0.43] 技能包：与知识图谱独立的数据 / 生命周期 / Agent 调用体系
    # ------------------------------------------------------------------
    def _sync_project_experience_skills(
        self, project_id: str, internal_workspace: Path | str,
        graph: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """把 core 知识资产同步成**待审的项目经验技能**。

        关键边界：
        · 资产升 core 只负责「创建技能候选」，默认 pending；绝不直接进 Agent。
        · 技能一旦创建就有自己的状态机。知识后来退役/删除，不会暗中删除已导出的
          技能快照；用户必须在技能包体系里单独退役或彻底删除。
        · 用户彻底删除技能后写 tombstone，仍为 core 的源资产不会在下一次同步时复活。
        """
        base = self.project_skill_dir(internal_workspace)
        registry_path = base / _SKILL_REGISTRY
        lock = _project_skill_lock(base)
        with lock:
            registry = _load_skill_registry(registry_path, project_id=project_id)
            items = registry.setdefault("items", {})
            tombstones = registry.setdefault("tombstones", {})
            now = _now_iso()

            assets = graph.get("assets") or {}
            for asset in assets.values():
                if not isinstance(asset, dict) or asset.get("status") != "core":
                    continue
                asset_id = str(asset.get("id") or "").strip()
                if not asset_id or asset_id in tombstones:
                    continue
                pack_id = f"experience:{project_id}:{asset_id}"
                current = items.get(pack_id)
                row = dict(current) if isinstance(current, dict) else {}
                is_new = not row
                status = str(row.get("status") or "pending")
                if status not in SKILLPACK_STATUSES:
                    status = "pending"
                created_at = str(row.get("created_at") or now)
                source_updated = str(asset.get("updated_at") or now)
                metadata_changed = any((
                    str(row.get("name") or "") != str(asset.get("title") or asset_id),
                    str(row.get("description") or "") != str(asset.get("one_liner") or ""),
                    str(row.get("scope") or "") != str(asset.get("scope") or "project"),
                    str(row.get("source_updated_at") or "") != source_updated,
                ))
                row.update({
                    "pack_id": pack_id,
                    "kind": "project_experience",
                    "name": str(asset.get("title") or asset_id),
                    "description": _clip(str(asset.get("one_liner") or ""), 180),
                    "status": status,
                    "scope": "global" if asset.get("scope") == "global" else "project",
                    "project_id": project_id,
                    "asset_id": asset_id,
                    "relative_path": asset_id,
                    "source": "knowledge_asset",
                    "created_at": created_at,
                    "updated_at": now if (is_new or metadata_changed) else str(row.get("updated_at") or now),
                    "source_updated_at": source_updated,
                })
                items[pack_id] = row
                _atomic_write(
                    base / asset_id / _SKILL_MD,
                    self._render_project_experience_skill(project_id, asset, row),
                )

            registry["schema_version"] = SKILLPACK_SCHEMA_VERSION
            registry["project_id"] = project_id
            registry["updated_at"] = now
            _save_skill_registry(registry_path, registry)

            # v0.42 迁移完成后清掉旧 managed export，避免磁盘上出现两份互相矛盾的 skill 真源。
            legacy = self.skill_export_dir(internal_workspace)
            try:
                if legacy.is_dir():
                    for child in legacy.iterdir():
                        if child.is_dir() and (child / _SKILL_MD).is_file():
                            shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass

            return [self._public_skill_row(row) for row in items.values()
                    if isinstance(row, dict)]

    def _render_project_experience_skill(
        self, project_id: str, asset: dict[str, Any], state: dict[str, Any],
    ) -> str:
        """Harness 的确定性 SKILL.md 渲染器；技能正文不靠 LLM 临场排版。"""
        asset_id = str(asset.get("id") or state.get("asset_id") or "")
        pack_id = str(state.get("pack_id") or f"experience:{project_id}:{asset_id}")
        title = str(asset.get("title") or state.get("name") or asset_id)
        one_liner = str(asset.get("one_liner") or state.get("description") or "").strip()
        applies = str(asset.get("applies_when") or "").strip()
        status = str(state.get("status") or "pending")
        scope = "global" if asset.get("scope") == "global" else "project"
        category = self._effective_category(asset)
        description = one_liner + (msg("knowledge_assets.py.069", applies=applies) if applies else "")
        front = [
            "---",
            f"id: {pack_id}",
            f"name: {title}",
            f"description: {_single_line(description)}",
            "source_kind: project_experience",
            f"source_project: {project_id}",
            f"source_asset: {asset_id}",
            f"scope: {scope}",
            f"status: {status}",
            "managed_by: knowe",
            "---",
            "",
            f"# {title}",
            "",
            msg("knowledge_assets.py.071", category=category, asset_id=asset_id),
            "",
            "## 何时调用",
            "",
            applies or msg("knowledge_assets.py.072"),
            "",
            "## 执行说明",
            "",
        ]
        lines = front + _normalize_md_body(str(asset.get("body_md") or "")) + [""]
        anti = str(asset.get("anti_pattern") or "").strip()
        if anti:
            lines += ["## 禁止路径", ""] + _normalize_md_body(anti) + [""]
        lines += [
            "## 调用约束",
            "",
            msg("knowledge_assets.py.073", **{"skill_status": _skill_status_zh(status)}),
            msg("knowledge_assets.py.074", **{"scope_label": msg("knowledge_assets.py.076") if scope == "global" else msg("knowledge_assets.py.077")}),
            msg("knowledge_assets.py.075"),
            "",
        ]
        return "\n".join(lines).rstrip() + "\n"

    async def sync_skillpacks(
        self, project_id: str, internal_workspace: Path | str,
    ) -> dict[str, Any]:
        """升级/启动时补同步已有 core 资产；与图谱写入共用 per-root 锁。"""
        root = KnowledgeGraphManager.knowledge_dir(internal_workspace)
        try:
            async with self._graph.root_lock(internal_workspace):
                graph = self._graph.load_graph(project_id, root)
                rows = self._sync_project_experience_skills(
                    project_id, internal_workspace, graph,
                )
            return {"ok": True, "count": len(rows)}
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(msg("knowledge_assets.py.050"), project_id)
            return {"ok": False, "reason": "sync_failed"}

    def _system_skill_documents(self) -> list[tuple[dict[str, Any], Path]]:
        rows: list[tuple[dict[str, Any], Path]] = []
        seen_ids: set[str] = set()
        for root in _system_skill_roots():
            for md in _discover_skill_files(root):
                try:
                    text = md.read_text("utf-8", errors="replace")
                    row = _skill_row_from_document(
                        md, text, kind="system_builtin", root=root,
                        status="active", immutable=True,
                    )
                    pack_id = str(row.get("pack_id") or "")
                    if not pack_id or pack_id in seen_ids:
                        continue
                    seen_ids.add(pack_id)
                    rows.append((row, md))
                except OSError:
                    continue
        rows.sort(key=lambda pair: (str(pair[0].get("name") or "").casefold(),
                                    str(pair[0].get("pack_id") or "")))
        return rows

    def _third_party_skill_documents(self) -> list[tuple[dict[str, Any], Path]]:
        base = self.third_party_skill_dir()
        registry_path = base / _SKILL_REGISTRY
        rows: list[tuple[dict[str, Any], Path]] = []
        with _THIRD_PARTY_SKILL_LOCK:
            existed = registry_path.is_file()
            registry = _load_skill_registry(registry_path, project_id="")
            items = registry.setdefault("items", {})
            now = _now_iso()
            live: set[str] = set()
            changed = not existed or registry.get("schema_version") != SKILLPACK_SCHEMA_VERSION
            for md in _discover_skill_files(base):
                # 注册表本身不叫 SKILL.md；这里仍防御性排除隐藏目录。
                try:
                    text = md.read_text("utf-8", errors="replace")
                except OSError:
                    continue
                provisional = _skill_row_from_document(
                    md, text, kind="third_party", root=base,
                    status="active", immutable=False,
                )
                pack_id = str(provisional.get("pack_id") or "")
                if not pack_id:
                    continue
                live.add(pack_id)
                current = items.get(pack_id)
                state = dict(current) if isinstance(current, dict) else {}
                status = str(state.get("status") or "active")
                if status not in SKILLPACK_STATUSES:
                    status = "active"
                doc_updated = str(provisional.get("updated_at") or "")
                state_updated = str(state.get("updated_at") or "")
                provisional.update({
                    "status": status,
                    "created_at": str(state.get("created_at") or provisional.get("created_at") or now),
                    # 文件更新与用户状态变更都应反映在“最近更新”；ISO-8601 UTC 可直接比较。
                    "updated_at": max(doc_updated, state_updated) or now,
                    "relative_path": _safe_relative(md.parent, base),
                })
                next_state = {
                    key: provisional.get(key) for key in (
                        "pack_id", "kind", "name", "description", "status", "scope",
                        "relative_path", "source", "created_at", "updated_at",
                    )
                }
                if current != next_state:
                    items[pack_id] = next_state
                    changed = True
                rows.append((self._public_skill_row(provisional), md))

            # 外部卸载/移走目录后，注册表不再展示悬空项。
            for pack_id in list(items):
                if pack_id not in live:
                    items.pop(pack_id, None)
                    changed = True
            registry["schema_version"] = SKILLPACK_SCHEMA_VERSION
            if changed:
                registry["updated_at"] = now
                _save_skill_registry(registry_path, registry)

        rows.sort(key=lambda pair: (str(pair[0].get("name") or "").casefold(),
                                    str(pair[0].get("pack_id") or "")))
        return rows

    def _project_experience_rows(
        self, project_id: str, internal_workspace: Path | str,
    ) -> list[dict[str, Any]]:
        """只读独立注册表。

        core→技能候选的同步只在知识写入与引擎启动尾链执行；GET/Prompt 读取不再反向写盘，
        避免 HTTP 读线程拿旧图谱覆盖刚完成的技能同步，也避免每轮 Agent prompt 重写文件。
        """
        base = self.project_skill_dir(internal_workspace)
        lock = _project_skill_lock(base)
        with lock:
            registry = _load_skill_registry(base / _SKILL_REGISTRY, project_id=project_id)
            rows = [
                self._public_skill_row(row)
                for row in (registry.get("items") or {}).values()
                if isinstance(row, dict) and row.get("kind") == "project_experience"
            ]
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return rows

    def list_skillpacks(
        self, project_id: str, internal_workspace: Path | str,
    ) -> dict[str, Any]:
        """列出三类真实技能包；兼容字段 `system` 只作为旧前端过渡聚合。"""
        try:
            system_builtin = [row for row, _ in self._system_skill_documents()]
            project_experience = self._project_experience_rows(
                project_id, internal_workspace,
            )
            third_party = [row for row, _ in self._third_party_skill_documents()]
            return {
                "system_builtin": system_builtin,
                "project_experience": project_experience,
                "third_party": third_party,
                # v0.42 compatibility：不再作为任何写路由的身份依据。
                "system": system_builtin + project_experience,
            }
        except Exception:
            log.exception("[%s] list_skillpacks 失败（回落为空）", project_id)
            return {
                "system_builtin": [], "project_experience": [],
                "third_party": [], "system": [],
            }

    def read_skillpack(
        self, project_id: str, internal_workspace: Path | str, pack_id: str,
        *, active_only: bool = False,
    ) -> dict[str, Any]:
        """读取 SKILL.md。`active_only=True` 是 Agent 的硬门；UI 详情可看待审/退役。"""
        wanted = str(pack_id or "").strip()
        if not wanted:
            return {"found": False, "pack_id": wanted, "reason": "missing_pack_id"}
        try:
            for row, md in self._system_skill_documents():
                if row.get("pack_id") == wanted:
                    return _read_skill_result(row, md, active_only=active_only)

            base = self.project_skill_dir(internal_workspace)
            lock = _project_skill_lock(base)
            with lock:
                registry = _load_skill_registry(base / _SKILL_REGISTRY, project_id=project_id)
                state = (registry.get("items") or {}).get(wanted)
                if isinstance(state, dict):
                    row = self._public_skill_row(state)
                    rel = str(state.get("relative_path") or state.get("asset_id") or "")
                    md = _safe_child(base, rel) / _SKILL_MD
                    return _read_skill_result(row, md, active_only=active_only)

            for row, md in self._third_party_skill_documents():
                if row.get("pack_id") == wanted:
                    return _read_skill_result(row, md, active_only=active_only)
        except Exception:
            log.exception(msg("knowledge_assets.py.051"), project_id, wanted)
        return {"found": False, "pack_id": wanted, "reason": "skillpack_not_found"}

    async def review_skillpack(
        self, project_id: str, internal_workspace: Path | str,
        pack_id: str, action: str,
    ) -> dict[str, Any]:
        """项目经验技能策展：pending --approve→ active；--reject→ retired。"""
        action = str(action or "").strip().lower()
        if action not in {"approve", "reject"}:
            return {"ok": False, "reason": "invalid_action"}
        base = self.project_skill_dir(internal_workspace)
        lock = _project_skill_lock(base)
        with lock:
            path = base / _SKILL_REGISTRY
            registry = _load_skill_registry(path, project_id=project_id)
            state = (registry.get("items") or {}).get(pack_id)
            if not isinstance(state, dict):
                return {"ok": False, "reason": "skillpack_not_found"}
            if state.get("kind") != "project_experience":
                return {"ok": False, "reason": "bad_skillpack_kind"}
            if state.get("status") != "pending":
                return {"ok": False, "reason": "bad_status"}
            target = "active" if action == "approve" else "retired"
            state["status"] = target
            state["updated_at"] = _now_iso()
            registry["updated_at"] = state["updated_at"]
            _save_skill_registry(path, registry)
            md = _safe_child(base, str(state.get("relative_path") or state.get("asset_id") or "")) / _SKILL_MD
            _rewrite_skill_status(md, target)
            return {"ok": True, "pack": self._public_skill_row(state)}

    async def set_skillpack_status(
        self, project_id: str, internal_workspace: Path | str,
        pack_id: str, status: str,
    ) -> dict[str, Any]:
        """独立技能状态机。系统自备技能永远 immutable；第三方允许三态切换。"""
        status = str(status or "").strip().lower()
        if status not in SKILLPACK_STATUSES:
            return {"ok": False, "reason": "invalid_status"}

        # 系统自备：强制永久 active，任何状态写入都拒绝。
        if any(row.get("pack_id") == pack_id for row, _ in self._system_skill_documents()):
            return {"ok": False, "reason": "immutable_system_skill"}

        base = self.project_skill_dir(internal_workspace)
        lock = _project_skill_lock(base)
        with lock:
            path = base / _SKILL_REGISTRY
            registry = _load_skill_registry(path, project_id=project_id)
            state = (registry.get("items") or {}).get(pack_id)
            if isinstance(state, dict):
                current_status = str(state.get("status") or "pending")
                # pending 是强制人工策展门：只能走 review(approve/reject)，不能借通用状态 API
                # 直接写 active/retired 绕过“点击通过/驳回”。已生效/已退役之间才允许恢复与退役。
                if current_status == "pending" or status == "pending":
                    return {"ok": False, "reason": "project_skill_pending_is_review_only"}
                state["status"] = status
                state["updated_at"] = _now_iso()
                registry["updated_at"] = state["updated_at"]
                _save_skill_registry(path, registry)
                md = _safe_child(base, str(state.get("relative_path") or state.get("asset_id") or "")) / _SKILL_MD
                _rewrite_skill_status(md, status)
                return {"ok": True, "pack": self._public_skill_row(state)}

        third_base = self.third_party_skill_dir()
        with _THIRD_PARTY_SKILL_LOCK:
            path = third_base / _SKILL_REGISTRY
            registry = _load_skill_registry(path, project_id="")
            state = (registry.get("items") or {}).get(pack_id)
            if not isinstance(state, dict):
                # 列举一次会把新安装但尚未登记的真实 SKILL.md 纳入注册表。
                pass
            else:
                state["status"] = status
                state["updated_at"] = _now_iso()
                registry["updated_at"] = state["updated_at"]
                _save_skill_registry(path, registry)
                return {"ok": True, "pack": self._public_skill_row(state)}
        # 注意：不能在持有非可重入锁时调用 _third_party_skill_documents。
        self._third_party_skill_documents()
        with _THIRD_PARTY_SKILL_LOCK:
            path = third_base / _SKILL_REGISTRY
            registry = _load_skill_registry(path, project_id="")
            state = (registry.get("items") or {}).get(pack_id)
            if not isinstance(state, dict):
                return {"ok": False, "reason": "skillpack_not_found"}
            state["status"] = status
            state["updated_at"] = _now_iso()
            registry["updated_at"] = state["updated_at"]
            _save_skill_registry(path, registry)
            return {"ok": True, "pack": self._public_skill_row(state)}

    async def purge_skillpack(
        self, project_id: str, internal_workspace: Path | str, pack_id: str,
    ) -> dict[str, Any]:
        """彻底删除技能包；只允许已退役项。系统自备技能永不可删。"""
        if any(row.get("pack_id") == pack_id for row, _ in self._system_skill_documents()):
            return {"ok": False, "reason": "immutable_system_skill"}

        base = self.project_skill_dir(internal_workspace)
        lock = _project_skill_lock(base)
        with lock:
            path = base / _SKILL_REGISTRY
            registry = _load_skill_registry(path, project_id=project_id)
            items = registry.get("items") or {}
            state = items.get(pack_id)
            if isinstance(state, dict):
                if state.get("status") != "retired":
                    return {"ok": False, "reason": "skillpack_must_be_retired"}
                asset_id = str(state.get("asset_id") or "")
                rel = str(state.get("relative_path") or asset_id)
                items.pop(pack_id, None)
                if asset_id:
                    registry.setdefault("tombstones", {})[asset_id] = _now_iso()
                registry["updated_at"] = _now_iso()
                _save_skill_registry(path, registry)
                shutil.rmtree(_safe_child(base, rel), ignore_errors=True)
                return {"ok": True, "pack_id": pack_id, "purged": True}

        third_base = self.third_party_skill_dir()
        self._third_party_skill_documents()  # 先同步真实安装目录与注册表
        with _THIRD_PARTY_SKILL_LOCK:
            path = third_base / _SKILL_REGISTRY
            registry = _load_skill_registry(path, project_id="")
            items = registry.get("items") or {}
            state = items.get(pack_id)
            if not isinstance(state, dict):
                return {"ok": False, "reason": "skillpack_not_found"}
            if state.get("status") != "retired":
                return {"ok": False, "reason": "skillpack_must_be_retired"}
            rel = str(state.get("relative_path") or "")
            target = _safe_child(third_base, rel)
            items.pop(pack_id, None)
            registry["updated_at"] = _now_iso()
            _save_skill_registry(path, registry)
            # 支持“安装根目录本身就是一个技能包”的真实布局：此时只删根 SKILL.md，
            # 不能把承载其他第三方包和注册表的总目录一起删掉。
            if rel in {"", "."}:
                try:
                    (third_base / _SKILL_MD).unlink(missing_ok=True)
                except OSError:
                    pass
            elif target != third_base:
                shutil.rmtree(target, ignore_errors=True)
            return {"ok": True, "pack_id": pack_id, "purged": True}

    def skill_context_block(
        self, project_id: str, internal_workspace: Path | str,
    ) -> str:
        """L0 技能索引：只列 active。pending/retired 在 Harness 层根本不进工作流。"""
        try:
            packs = self.list_skillpacks(project_id, internal_workspace)
            active: list[dict[str, Any]] = []
            for key in ("system_builtin", "project_experience", "third_party"):
                for row in packs.get(key) or []:
                    if isinstance(row, dict) and row.get("status") == "active":
                        active.append(row)
            if not active:
                return ""
            max_lines = _bounded(CONFIG.skill_index_max_lines, 4, 40)
            lines = [
                msg("knowledge_assets.py.052"),
            ]
            kind_zh = {
                "system_builtin": msg("knowledge_assets.py.053"),
                "project_experience": msg("knowledge_assets.py.054"),
                "third_party": msg("knowledge_assets.py.055"),
            }
            for row in active[:max_lines]:
                lines.append(
                    f"- [{row.get('pack_id')}] {kind_zh.get(str(row.get('kind')), msg('knowledge_assets.py.056'))}｜"
                    f"{row.get('name')}：{_clip(str(row.get('description') or ''), 100)}"
                )
            if len(active) > max_lines:
                lines.append(msg("knowledge_assets.py.057", **{"len(active) - max_lines": len(active) - max_lines}))
            return "\n\n" + "\n".join(lines)
        except Exception:
            log.exception(msg("knowledge_assets.py.058"), project_id)
            return ""

    @staticmethod
    def _public_skill_row(row: dict[str, Any]) -> dict[str, Any]:
        kind = str(row.get("kind") or "")
        status = "active" if kind == "system_builtin" else str(row.get("status") or "pending")
        if status not in SKILLPACK_STATUSES:
            status = "pending"
        return {
            "pack_id": str(row.get("pack_id") or ""),
            "name": str(row.get("name") or msg("knowledge_assets.py.059")),
            "description": _clip(str(row.get("description") or ""), 180),
            "kind": kind,
            "source_kind": kind,
            "source": str(row.get("source") or ""),
            "project_id": str(row.get("project_id") or ""),
            "asset_id": str(row.get("asset_id") or ""),
            "scope": "project" if row.get("scope") == "project" else "global",
            "status": status,
            "view_state": "ok" if status == "active" else status,
            "immutable": kind == "system_builtin" or bool(row.get("immutable")),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    # ------------------------------------------------------------------
    # 投影小工具
    # ------------------------------------------------------------------
    @staticmethod
    def _effective_category(asset: dict[str, Any]) -> str:
        user = str(asset.get("user_category") or "")
        if user in CATEGORIES:
            return user
        return _CLASS_TO_CATEGORY.get(str(asset.get("class")), msg("knowledge_assets.py.039"))

    @staticmethod
    def _view_state(asset: dict[str, Any]) -> str:
        status = str(asset.get("status") or "candidate")
        if status == "retired":
            return "retired"
        if status == "candidate" or asset.get("needs_review") or asset.get("retire_suggested"):
            return "pending"
        return "ok"

    def _public_asset(
        self, graph: dict[str, Any], asset: dict[str, Any],
        sources: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sources = sources if sources is not None else (graph.get("sources") or {})
        metrics = asset.get("metrics") or {}
        ref_to_meta: dict[str, dict[str, Any]] = {}
        for src in sources.values():
            if isinstance(src, dict) and src.get("ref"):
                ref_to_meta[str(src["ref"])] = src
        evidence_rows: list[dict[str, Any]] = []
        for ev in (asset.get("evidence") or [])[:10]:
            if not isinstance(ev, dict):
                continue
            src = ref_to_meta.get(str(ev.get("source_ref") or ""), {})
            meta = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}
            evidence_rows.append({
                "source_ref": ev.get("source_ref") or "",
                "node_id": ev.get("node_id") or "",
                "source_kind": src.get("kind"),
                "step": src.get("step"),
                "observed_at": src.get("created"),
                "excerpt": ev.get("excerpt") or "",
                "agent_id": meta.get("agent_id"),
            })
        return {
            "asset_id": asset.get("id"),
            "class": asset.get("class"),
            "class_zh": _CLASS_ZH.get(str(asset.get("class")), ""),
            "category": self._effective_category(asset),
            "title": asset.get("title"),
            "one_liner": asset.get("one_liner"),
            "applies_when": asset.get("applies_when"),
            "status": asset.get("status"),
            "view_state": self._view_state(asset),
            "needs_review": bool(asset.get("needs_review")),
            "retire_suggested": bool(asset.get("retire_suggested")),
            "conflict_with": list(asset.get("conflict_with") or []),
            "scope": asset.get("scope"),
            "scope_set_by": asset.get("scope_set_by"),
            "confidence": asset.get("confidence"),
            "utility": metrics.get("utility", 0.0),
            "use_count": metrics.get("use_count", 0),
            "cited_ok": metrics.get("cited_ok", 0),
            "cited_bad": metrics.get("cited_bad", 0),
            "source_count": len({
                e.get("source_ref") or e.get("node_id")
                for e in asset.get("evidence") or [] if isinstance(e, dict)
            }),
            "created_at": asset.get("created_at"),
            "updated_at": asset.get("updated_at"),
            "evidence": evidence_rows,
        }


# ======================================================================
# 校验 / 文本小工具
# ======================================================================
def _validate_distilled(
    raw: str, *, require_evidence: bool = True,
) -> list[dict[str, Any]]:
    """蒸馏输出的确定性三道闸：类型合法、反过程转述、（默认）证据必填。"""
    text = str(raw or "").strip()
    if not text:
        return []
    data = _parse_json_object(f'{{"items": {text} }}')
    if data is None:
        data = _parse_json_object(text)
        items_raw = data.get("items") if isinstance(data, dict) else None
    else:
        items_raw = data.get("items")
    if not isinstance(items_raw, list):
        return []
    items: list[dict[str, Any]] = []
    for row in items_raw[:4]:
        if not isinstance(row, dict):
            continue
        cls = str(row.get("class") or "").strip()
        title = str(row.get("title") or "").strip()
        one_liner = str(row.get("one_liner") or "").strip()
        body = str(row.get("body_md") or "").strip()
        if cls not in ASSET_CLASSES or not title or not one_liner or not body:
            continue
        if _PROCESS_NARRATION_RX.match(one_liner) or _PROCESS_NARRATION_RX.match(title):
            continue                                  # 反事实门的确定性兜底
        evidence = [e for e in (row.get("evidence") or []) if isinstance(e, dict)]
        if require_evidence and not evidence:
            continue
        items.append({
            "class": cls,
            "title": title,
            "one_liner": one_liner,
            "applies_when": str(row.get("applies_when") or "").strip(),
            "body_md": body,
            "anti_pattern": str(row.get("anti_pattern") or "").strip(),
            "evidence": evidence,
            "confidence": _float01(row.get("confidence"), 0.6),
        })
    return items


def _normalize_md_body(body: str) -> list[str]:
    """把蒸馏正文规整成干净的 markdown 列表/段落（空行、列表符号、缩进统一）。"""
    lines_in = [ln.rstrip() for ln in str(body or "").splitlines()]
    out: list[str] = []
    for ln in lines_in:
        stripped = ln.strip()
        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue
        if re.match(r"^[-*•·]\s+", stripped):
            out.append("- " + re.sub(r"^[-*•·]\s+", "", stripped))
        elif re.match(r"^\d+[.、)]\s*", stripped):
            out.append(re.sub(r"^(\d+)[.、)]\s*", r"\1. ", stripped))
        elif stripped.startswith("#"):
            out.append("**" + stripped.lstrip("# ").strip() + "**")   # 正文里不再开新标题层级
        else:
            out.append(stripped)
    while out and out[-1] == "":
        out.pop()
    if not out:
        out = [msg("knowledge_assets.py.060")]
    # 单段长句 → 拆成条件-行动 bullet 的观感（句号分句），读起来不糊成一团。
    if len(out) == 1 and len(out[0]) > 120 and not out[0].startswith("- "):
        pieces = [p.strip() for p in re.split(r"[；;。]\s*", out[0]) if p.strip()]
        if len(pieces) >= 2:
            out = [f"- {p}" for p in pieces]
    return out


def _front_value(text: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", text[:2000], re.M)
    return m.group(1).strip() if m else ""


# ======================================================================
# [v0.43] 技能包存储 / 发现小工具
# ======================================================================
def _project_skill_lock(base: Path) -> threading.Lock:
    key = str(base.expanduser().resolve())
    with _PROJECT_SKILL_LOCKS_GUARD:
        lock = _PROJECT_SKILL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROJECT_SKILL_LOCKS[key] = lock
        return lock


def _load_skill_registry(path: Path, *, project_id: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        if path.is_file():
            parsed = json.loads(path.read_text("utf-8", errors="replace"))
            if isinstance(parsed, dict):
                data = parsed
    except (OSError, ValueError, TypeError):
        log.warning(msg("knowledge_assets.py.061"), path)
    if not isinstance(data.get("items"), dict):
        data["items"] = {}
    if not isinstance(data.get("tombstones"), dict):
        data["tombstones"] = {}
    data["schema_version"] = SKILLPACK_SCHEMA_VERSION
    if project_id:
        data["project_id"] = project_id
    return data


def _save_skill_registry(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _system_skill_roots() -> list[Path]:
    """系统自备技能只从真实安装/资源目录发现，绝不造 mock。"""
    module_dir = Path(__file__).resolve().parent
    configured = [
        Path(raw).expanduser()
        for raw in str(CONFIG.skill_system_dirs or "").split(os.pathsep)
        if raw.strip()
    ]
    frozen_raw = str(getattr(sys, "_MEIPASS", "") or "").strip()
    candidates = configured + [
        module_dir / "skills",
        module_dir.parent / "skills",
        Path(CONFIG.data_dir).expanduser() / "skills" / "system",
        Path(sys.prefix) / "share" / "knowe" / "skills",
    ]
    if frozen_raw:
        candidates.append(Path(frozen_raw) / "skills")
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        key = str(resolved)
        if key in seen or not resolved.is_dir():
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def _discover_skill_files(root: Path, *, limit: int = 500) -> list[Path]:
    """发现 root 下真实 SKILL.md；限制深度/数量，避免误扫整个磁盘。"""
    try:
        root = root.expanduser().resolve()
    except OSError:
        root = root.expanduser().absolute()
    if not root.is_dir():
        return []
    found: list[Path] = []
    direct = root / _SKILL_MD
    if direct.is_file():
        found.append(direct)
    try:
        for md in root.rglob(_SKILL_MD):
            if md == direct:
                continue
            try:
                rel = md.relative_to(root)
            except ValueError:
                continue
            # 一个技能包通常 root/{pack}/SKILL.md；允许再嵌套两层给安装器分类。
            if len(rel.parts) > 4 or any(part.startswith(".") for part in rel.parts[:-1]):
                continue
            found.append(md)
            if len(found) >= limit:
                break
    except OSError:
        pass
    return sorted(set(found), key=lambda p: str(p).casefold())


def _front_clean(text: str, key: str) -> str:
    value = _front_value(text, key).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    return value


def _skill_slug(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", str(value or "").strip())
    text = text.strip("-._")
    return text[:72] or "skill"


def _skill_row_from_document(
    md: Path, text: str, *, kind: str, root: Path,
    status: str, immutable: bool,
) -> dict[str, Any]:
    try:
        rel = md.parent.resolve().relative_to(root.resolve())
        rel_text = rel.as_posix() or md.parent.name
    except (OSError, ValueError):
        rel_text = md.parent.name
    explicit_id = _front_clean(text, "id")
    name = (
        _front_clean(text, "name")
        or _front_clean(text, "title")
        or md.parent.name
        or msg("knowledge_assets.py.059")
    )
    description = _front_clean(text, "description") or _skill_body_summary(text)
    seed = explicit_id or rel_text or name
    digest = hashlib.sha1(rel_text.encode("utf-8", errors="ignore")).hexdigest()[:8]
    prefix = "system" if kind == "system_builtin" else "third_party"
    pack_id = f"{prefix}:{_skill_slug(seed)}:{digest}"
    stamp = _mtime_iso(md)
    return {
        "pack_id": pack_id,
        "kind": kind,
        "name": name,
        "description": _clip(description, 180),
        "status": "active" if immutable else status,
        "scope": "global",
        "project_id": "",
        "asset_id": "",
        "source": "knowe_bundle" if kind == "system_builtin" else "user_install",
        "immutable": immutable,
        "created_at": stamp,
        "updated_at": stamp,
    }


def _skill_body_summary(text: str) -> str:
    body = re.sub(r"^---\n[\s\S]*?\n---\n?", "", str(text or ""), count=1)
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ">", "- ", "* ", "```")):
            continue
        return _single_line(line)
    return ""


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _safe_relative(path: Path, base: Path) -> str:
    try:
        rel = path.resolve().relative_to(base.resolve())
        return rel.as_posix()
    except (OSError, ValueError):
        return ""


def _safe_child(base: Path, relative: str) -> Path:
    """返回 base 内的路径；非法相对路径落到不可存在的 sentinel，绝不越界删除。"""
    try:
        resolved_base = base.expanduser().resolve()
        target = (resolved_base / str(relative or "")).resolve()
        if target == resolved_base or resolved_base not in target.parents:
            return resolved_base / ".invalid-skill-path"
        return target
    except OSError:
        return base / ".invalid-skill-path"


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return _now_iso()


def _read_skill_result(
    row: dict[str, Any], md: Path, *, active_only: bool,
) -> dict[str, Any]:
    status = str(row.get("status") or "pending")
    if active_only and status != "active":
        return {
            "found": False,
            "pack_id": row.get("pack_id"),
            "reason": "skillpack_not_active",
            "status": status,
            "message": msg("event.read.skill.result.01"),
        }
    try:
        text = md.read_text("utf-8", errors="replace")
    except OSError:
        return {
            "found": False, "pack_id": row.get("pack_id"),
            "reason": "skillpack_body_missing",
        }
    return {"found": True, "pack": row, "body_md": text}


def _rewrite_skill_status(path: Path, status: str) -> None:
    """注册表是真源；同时修正 managed SKILL.md front-matter，便于人工检查。"""
    try:
        if not path.is_file():
            return
        text = path.read_text("utf-8", errors="replace")
        if re.search(r"^status:\s*.*$", text[:3000], re.M):
            text = re.sub(r"^status:\s*.*$", f"status: {status}", text, count=1, flags=re.M)
        elif text.startswith("---\n"):
            end = text.find("\n---", 4)
            if end >= 0:
                text = text[:end] + f"\nstatus: {status}" + text[end:]
        _atomic_write(path, text)
    except OSError:
        pass


def _skill_status_zh(status: str) -> str:
    return {"active": msg("ka.062a"), "pending": msg("ka.062b"), "retired": msg("ka.062c")}.get(status, status)


__all__ = [
    "KnowledgeAssetManager", "DistillCall", "ASSET_CLASSES", "CATEGORIES",
    "ASSET_SCHEMA_VERSION", "SKILLPACK_SCHEMA_VERSION", "SKILLPACK_KINDS",
    "SKILLPACK_STATUSES",
]