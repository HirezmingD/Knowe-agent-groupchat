"""
migrations.py — 数据版本迁移框架（阶段一 · 任务 1.6）。

目标（来自计划书）：
  · 数据版本 < 软件版本时按序迁移，用户无感知；
  · 首装直接初始化到最新；
  · 迁移失败 → 不启动 + 记日志 + 显式提示（防数据损坏）。

设计（刻意轻量，只做三件事：版本检查、迁移执行、失败拦截）：

  1. 版本标记：data 根下一个 `schema_version` 文件（JSON，可读、带 sha256 校验可验证）。
     没有该文件 = 首装或老数据，数据版本视为 0，启动时自动补齐到最新（写标记文件）。
     文件损坏 / 校验不过 = 无法确认数据状态 → 抛 DataMigrationError 阻止启动。

  2. 迁移注册表：``MIGRATIONS = {目标版本: 迁移函数}``，有序字典，按 key 升序执行。
     函数签名统一 ``fn(data_root: Path) -> None``；某版本没有要搬的东西就不注册
     （执行器自动跳过，版本号照常前进）。★ 迁移函数必须**幂等**：万一写标记失败、
     下次启动重跑同一段迁移，结果必须一致。

  3. 失败拦截：任何迁移函数抛异常 → 记 ERROR 日志（含 traceback）→ 抛
     ``DataMigrationError``（中文、含数据目录路径）→ server.run() 不捕获，启动直接中断。

  版本号语义：数据版本是**整数**（v1, v2, …），由软件发版演进决定；软件版本是字符串
  （CONFIG.version），只写进标记文件做溯源，不参与大小比较。目标版本 = 当前软件
  认识的数据格式版本（``CURRENT_DATA_SCHEMA_VERSION``，必须等于注册表最大 key）。

★ 首版（v1.0.25.x）说明：JSON/JSONL 类数据（projects.json、events/*.jsonl、*.seq、
  *_tokens.jsonl、project_dirs.json 等）当前没有结构差异迁移，注册表为空——框架完整可用，
  只是没有要搬的东西。将来有结构变更时：写一个 ``_migrate_to_vN`` 函数注册进来、
  把 ``CURRENT_DATA_SCHEMA_VERSION`` 加一即可。

  参考：SQLite 侧的成熟机制（knowe_storage/_sqlite.py 的 schema_registry 表 +
  knowe_provenance/schema_registry.py）。本模块只面向 data 根的 JSON/JSONL 数据，
  不触碰 SQLite，也不引入数据库。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("knowe.migrations")

#: data 根下的版本标记文件名（计划书：「如 data 根 schema_version 文件」）。
SCHEMA_VERSION_FILENAME = "schema_version"

#: 当前软件对应的数据格式版本。发版时如有数据格式变更：注册迁移函数并把这里 +1。
#: 必须 >= 1，且必须等于 MIGRATIONS 的最大 key（没有迁移的中间版本允许缺失）。
CURRENT_DATA_SCHEMA_VERSION = 2

#: 迁移注册表：{目标版本: 迁移函数}，**有序字典**，按 key 升序执行。
#: 函数签名：fn(data_root: Path) -> None。必须幂等。
MIGRATIONS: dict[int, Callable[[Path], None]] = {
    # [v1.0.31] 本地存储优化：JSON/JSONL 侧无需强制搬移——老事件文件由
    # 按天轮转机制惰性归档（mtime 判断自动切分压缩），SQLite 侧由
    # storage_migrator 在启动时执行。此处仅让数据版本前进，标记优化已启用。
    2: lambda root: None,
}


class DataMigrationError(RuntimeError):
    """数据迁移失败（或数据状态无法确认）。抛出 = 阻止启动，防数据损坏。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_body(schema_version: int, software_version: str) -> dict[str, Any]:
    """标记文件里参与校验的主体字段（顺序无关，按 key 排序序列化）。"""
    return {
        "schema_version": int(schema_version),
        "software_version": str(software_version or ""),
    }


def _checksum(body: dict[str, Any]) -> str:
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def schema_version_path(data_root: Path) -> Path:
    return Path(data_root) / SCHEMA_VERSION_FILENAME


def _atomic_write_text(path: Path, text: str) -> None:
    """先写 .tmp 再 os.replace 一把换过去（与 persist.py 同款原子写）。"""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_marker(root: Path, schema_version: int, software_version: str) -> None:
    body = _canonical_body(schema_version, software_version)
    payload = {
        **body,
        "checksum": _checksum(body),
        "migrated_at": _now(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        _atomic_write_text(schema_version_path(root), text)
    except OSError as exc:
        raise DataMigrationError(
            f"数据版本标记写入失败（{exc}）——已阻止启动以防数据损坏"
            f"（数据目录：{root}）"
        ) from exc


def read_data_schema_version(data_root: Path) -> int:
    """读 data 根的版本标记。没有标记 → 0（首装或老数据，需要初始化/迁移）。

    标记存在但读不了 / 不是合法 JSON / 校验和不过 → 抛 DataMigrationError：
    数据状态无法确认时宁可拦住启动，也不能蒙着头去读写数据（防数据损坏）。
    """
    path = schema_version_path(data_root)
    if not path.is_file():
        return 0
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DataMigrationError(
            f"数据版本标记 {path} 无法读取（{exc}）——已阻止启动以防数据损坏，"
            f"请勿手工删除/修改该文件"
        ) from exc
    if not isinstance(raw, dict):
        raise DataMigrationError(
            f"数据版本标记 {path} 不是 JSON 对象——已阻止启动以防数据损坏，"
            f"请勿手工删除/修改该文件"
        )
    version = raw.get("schema_version")
    if not isinstance(version, int) or version < 0:
        raise DataMigrationError(
            f"数据版本标记 {path} 的 schema_version 非法（{version!r}）——"
            f"已阻止启动以防数据损坏，请勿手工删除/修改该文件"
        )
    body = {
        key: raw.get(key)
        for key in ("schema_version", "software_version")
        if key in raw
    }
    if raw.get("checksum") != _checksum(body):
        raise DataMigrationError(
            f"数据版本标记 {path} 校验失败——文件可能被改动，已阻止启动以防数据损坏"
        )
    return int(version)


def run_data_migrations(
    data_root: str | os.PathLike[str],
    *,
    software_version: str = "",
) -> int:
    """启动时统一入口：版本检查 → 按序迁移 → 写版本标记。返回最终数据版本。

    失败（迁移抛异常 / 标记写不进 / 数据版本高于软件）→ 抛 DataMigrationError，
    调用方（server.run）不得吞掉——启动必须中断。

    ``software_version`` 只写入标记文件做溯源（如 CONFIG.version），不参与比较。
    """
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)

    current = read_data_schema_version(root)
    target = CURRENT_DATA_SCHEMA_VERSION

    if current > target:
        # 数据版本比软件认识的新：多半是拿旧版软件开了新版的数据。宁可不启动，
        # 也不能让旧版逻辑去读写新版结构（防数据损坏）。
        raise DataMigrationError(
            f"数据版本 v{current} 高于当前软件支持的数据版本 v{target}"
            f"（数据目录：{root}）——数据可能来自更新的 Knowe 版本，"
            f"请升级软件或恢复原数据，已阻止启动以防数据损坏"
        )

    if current == target:
        return current

    log.info("数据版本 v%s → v%s 迁移开始（数据目录：%s）", current, target, root)
    for version in range(current + 1, target + 1):
        fn = MIGRATIONS.get(version)
        if fn is None:
            log.info("数据版本 v%s 无迁移动作，跳过", version)
            continue
        log.info("执行数据迁移 v%s …", version)
        try:
            fn(root)
        except Exception as exc:
            log.exception("数据迁移 v%s 失败", version)
            raise DataMigrationError(
                f"数据迁移 v{version} 失败（{exc}）——已阻止启动以防数据损坏，"
                f"请恢复备份或联系支持（数据目录：{root}）"
            ) from exc

    _write_marker(root, target, software_version)
    log.info("数据版本已升级到 v%s（用户无感知）", target)
    return target


__all__ = [
    "CURRENT_DATA_SCHEMA_VERSION",
    "DataMigrationError",
    "MIGRATIONS",
    "SCHEMA_VERSION_FILENAME",
    "read_data_schema_version",
    "run_data_migrations",
    "schema_version_path",
]
