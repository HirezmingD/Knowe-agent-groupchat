"""
storage_migrator.py — SQLite 存量数据迁移（v1.0.31 R4）

升级后首次启动执行一次：
  · task_runs：明文 payload → 压缩 + 指纹列；超龄完成任务 → R3 摘要裁剪。
  · completion_outbox_v2：delivered 记录删除（R5）。

设计要点：
  · 全部操作幂等：已压缩（z1: 前缀）跳过、已裁剪（is_pruned）跳过、
    delivered 删除天然幂等——中断后续跑自动续转，无需显式进度标记。
  · 分批事务（每批 200 行），中断损失最多一批。
  · 失败不阻止启动：任何异常记日志并继续/重试，绝不丢用户数据。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from knowe_storage._sqlite import (
    SQLiteDatabase,
    compress_json_dumps,
    is_compressed_payload,
    json_loads,
    payload_sha256,
)
from storage_maintenance import _is_finished, is_pruned, summarize_run_dict

log = logging.getLogger("knowe.storage.migrator")

_BATCH_SIZE = 200


def _project_db_paths(data_root: Path) -> list[Path]:
    """data_root 下全部 runtime.sqlite3（项目级 + 平台级）。

    注意：data_root 已是项目目录的父级（即 data/backend 本身），
    项目库在 <data_root>/<project_id>/runtime/runtime.sqlite3 ——
    不要再拼一层 backend（v1.0.31 实测教训：双 backend 目录 glob 空）。
    """
    root = Path(data_root)
    if not root.exists():
        return []
    return sorted(root.glob("*/runtime/runtime.sqlite3"))


def _open(path: Path) -> SQLiteDatabase:
    db = SQLiteDatabase(path)
    try:
        # 迁移只需读已有表；确保指纹列存在（旧库可能没有）。无 task_runs 表的库跳过。
        db.ensure_columns("task_runs", {"payload_sha256": "TEXT"})
    except Exception:  # noqa: BLE001 — 表不存在等，迁移主体会再容错
        pass
    return db


def migrate_task_runs(db: SQLiteDatabase, *, keep: int = 50) -> dict[str, int]:
    """存量 task_runs：压缩 + 超龄裁剪。返回 {compressed, pruned}。幂等。"""
    with db.transaction(immediate=False) as conn:
        rows = conn.execute(
            "SELECT task_id, payload_json FROM task_runs ORDER BY updated_at DESC"
        ).fetchall()

    # 收集：所有行 → (task_id, payload_dict, 已压缩?)
    updates: list[tuple[str, dict, bool]] = []
    compressed = 0
    for row in rows:
        task_id = str(row["task_id"])
        raw = row["payload_json"]
        payload = json_loads(raw, {})
        if not isinstance(payload, dict) or not payload:
            continue
        was_compressed = is_compressed_payload(raw)
        updates.append((task_id, payload, was_compressed))
        if not was_compressed:
            compressed += 1

    # 超龄裁剪：已完成任务里超出最近 keep 个的（updated_at 已降序）
    finished = [(tid, payload) for tid, payload, _ in updates if _is_finished(payload)]
    retain_ids = {tid for tid, _ in finished[:keep]}
    to_prune: dict[str, dict] = {
        tid: summarize_run_dict(payload)
        for tid, payload in finished[keep:]
        if not is_pruned(payload)
    }
    pruned = len(to_prune)

    with db.transaction() as conn:
        for i in range(0, len(updates), _BATCH_SIZE):
            for task_id, payload, was_compressed in updates[i:i + _BATCH_SIZE]:
                if task_id in to_prune:
                    summary = to_prune[task_id]
                    conn.execute(
                        "UPDATE task_runs SET payload_json=?, payload_sha256=? WHERE task_id=?",
                        (compress_json_dumps(summary), payload_sha256(summary), task_id),
                    )
                elif not was_compressed:
                    conn.execute(
                        "UPDATE task_runs SET payload_json=?, payload_sha256=? WHERE task_id=?",
                        (compress_json_dumps(payload), payload_sha256(payload), task_id),
                    )
                # 已压缩且不裁剪：不动
    if compressed or pruned:
        log.info("task_runs 迁移：压缩 %d 行，裁剪 %d 行", compressed, pruned)
    return {"compressed": compressed, "pruned": pruned}


def clean_delivered_outbox(db: SQLiteDatabase) -> int:
    """删除 completion_outbox_v2 中 delivered 记录（R5 存量清理）。返回删除行数。"""
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM completion_outbox_v2 WHERE state='delivered'")
    n = cur.rowcount
    if n:
        log.info("outbox 清理：删除 delivered 记录 %d 行", n)
    return n


def run_sqlite_migrations(data_root: Path, *, keep: int = 50) -> dict[str, int]:
    """升级后首次启动入口：遍历全部项目库执行压缩/裁剪/outbox 清理。"""
    totals = {"compressed": 0, "pruned": 0, "outbox_deleted": 0, "dbs": 0}
    for path in _project_db_paths(data_root):
        try:
            db = _open(path)
            r = migrate_task_runs(db, keep=keep)
            o = clean_delivered_outbox(db)
            db.close()
            totals["compressed"] += r["compressed"]
            totals["pruned"] += r["pruned"]
            totals["outbox_deleted"] += o
            totals["dbs"] += 1
        except Exception:  # noqa: BLE001 — 单库失败不阻止整体
            log.exception("SQLite 迁移失败（%s），留待下次重试", path)
            continue
        try:
            # [v1.0.31 R1] UPDATE 变短后页不回收（freelist）→ VACUUM 收缩文件。
            # 需在无活动事务/连接时执行；失败不阻止（后台维护循环会再 VACUUM）。
            _vacuum(path)
        except Exception:  # noqa: BLE001
            log.warning("VACUUM 失败（%s），留待后台重试", path)
    return totals


def _vacuum(path: Path) -> None:
    """对单个 sqlite 库执行 VACUUM（文件收缩）。独立连接，避免锁冲突。"""
    import sqlite3
    conn = sqlite3.connect(path, timeout=30.0)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


__all__ = ["run_sqlite_migrations", "migrate_task_runs", "clean_delivered_outbox"]
