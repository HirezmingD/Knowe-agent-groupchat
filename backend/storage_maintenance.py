"""
storage_maintenance.py — 本地存储维护（v1.0.31 R2/R3 后台执行器）

职责：
  · R2 轮转压缩：对每个项目的 events 流水执行跨天切分 + gz 压缩（幂等）。
  · R3 快照裁剪：完成任务快照只保留最近 N 个完整（含思考全文/事件轨迹），
    更旧的只留结果摘要（白名单字段），运行中任务永不裁剪。

设计要点：
  · 全部操作幂等，可随时重入（后台循环、启动补执行、迁移共用同一入口）。
  · 不依赖任何 asyncio 设施——纯同步函数，由调用方决定在哪个线程跑。
  · 裁剪写回走压缩格式 + 指纹列（与 task_run_repository 一致）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Mapping

from knowe_storage._sqlite import (
    SQLiteDatabase,
    compress_json_dumps,
    is_compressed_payload,
    json_loads,
    payload_sha256,
)

log = logging.getLogger("knowe.storage.maintenance")

#: R3 保留的 metadata 子键（结果摘要；思考全文/验证等大字段裁剪掉）
SUMMARY_METADATA_KEYS: tuple[str, ...] = ("completion_status",)

#: R3 完成后标记：events 空且 metadata 无 reasoning = 已裁剪（幂等判定）
def is_pruned(payload: Mapping[str, Any]) -> bool:
    return not payload.get("events") and "reasoning" not in (payload.get("metadata") or {})


def summarize_run_dict(d: Mapping[str, Any]) -> dict[str, Any]:
    """白名单裁剪：保留恢复/展示/审计所需字段，裁掉 events 与大 metadata。"""
    meta = d.get("metadata") or {}
    return {
        "envelope": d.get("envelope"),
        "run_id": d.get("run_id"),
        "state": d.get("state"),
        "version": d.get("version"),
        "event_sequence": d.get("event_sequence"),
        "final_candidate": d.get("final_candidate"),
        "targeted_feedback": d.get("targeted_feedback"),
        "terminal_reason": d.get("terminal_reason"),
        "correction_signatures": d.get("correction_signatures"),
        "model_calls": d.get("model_calls"),
        "tool_calls": d.get("tool_calls"),
        "waiting_question": d.get("waiting_question"),
        "dependency": d.get("dependency"),
        "started_at": d.get("started_at"),
        "updated_at": d.get("updated_at"),
        "metadata": {k: meta.get(k) for k in SUMMARY_METADATA_KEYS if k in meta},
        "provenance": d.get("provenance"),
        "events": [],
    }


def _is_finished(payload: Mapping[str, Any]) -> bool:
    """完成任务判定：state=IDLE 且 metadata.completion_status 非空（与 runtime.TaskRun.stopped 同义）。"""
    if str(payload.get("state") or "").upper() != "IDLE":
        return False
    return bool((payload.get("metadata") or {}).get("completion_status"))


def prune_task_runs(db: SQLiteDatabase, *, keep: int = 50) -> int:
    """裁剪已完成且超出最近 ``keep`` 个的任务快照。返回裁剪行数。幂等。"""
    if keep < 0:
        raise ValueError("keep must be >= 0")
    with db.transaction(immediate=False) as conn:
        rows = conn.execute(
            "SELECT task_id, payload_json FROM task_runs ORDER BY updated_at DESC"
        ).fetchall()

    pruned = 0
    # 最近 keep 个（按 updated_at 降序）保留完整
    finished_candidates: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        payload = json_loads(row["payload_json"], {})
        if not isinstance(payload, dict) or not payload:
            continue
        if _is_finished(payload):
            finished_candidates.append((str(row["task_id"]), payload))

    # finished_candidates 已按 updated_at DESC 排序 → 前 keep 个保留
    retain_ids = {task_id for task_id, _ in finished_candidates[:keep]}
    with db.transaction() as conn:
        for task_id, payload in finished_candidates[keep:]:
            if is_pruned(payload):
                continue  # 已裁剪，幂等跳过
            summary = summarize_run_dict(payload)
            conn.execute(
                """
                UPDATE task_runs
                   SET payload_json=?, payload_sha256=?
                 WHERE task_id=?
                """,
                (compress_json_dumps(summary), payload_sha256(summary), task_id),
            )
            pruned += 1
    if pruned:
        log.info("任务快照裁剪：%d 个（保留最近 %d 个完整）", pruned, keep)
    return pruned


def run_project_maintenance(store: Any, db: SQLiteDatabase, *, keep: int = 50) -> dict[str, int]:
    """对单个项目执行 R2（流水压缩）+ R3（快照裁剪）。返回各项处理数。"""
    result: dict[str, int] = {}
    for pid in store_events_project_ids(store):
        try:
            store.rotate_and_compress(pid)
        except Exception:  # noqa: BLE001 — 单项目失败不影响其他项目
            log.exception("[%s] 流水轮转压缩失败", pid)
    result["pruned"] = prune_task_runs(db, keep=keep)
    return result


def store_events_project_ids(store: Any) -> list[str]:
    """从 Store 的项目注册表读出全部项目 id（供轮转压缩遍历）。"""
    projects = store.load_projects()
    return [str(p.get("project_id")) for p in projects if p.get("project_id")]


def run_all_maintenance(store: Any, db_provider: Any, *, keep: int = 50) -> dict[str, int]:
    """启动补执行/后台循环的统一入口：
    R2 对每个项目轮转压缩；R3 对每个项目数据库裁剪。
    """
    totals = {"pruned": 0}
    for pid in store_events_project_ids(store):
        try:
            store.rotate_and_compress(pid)
        except Exception:  # noqa: BLE001
            log.exception("[%s] 流水轮转压缩失败", pid)
        try:
            db = db_provider(pid)
            if db is not None:
                totals["pruned"] += prune_task_runs(db, keep=keep)
        except Exception:  # noqa: BLE001
            log.exception("[%s] 任务快照裁剪失败", pid)
    return totals


__all__ = [
    "is_pruned",
    "summarize_run_dict",
    "prune_task_runs",
    "run_all_maintenance",
]
