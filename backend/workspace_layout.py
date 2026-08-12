"""Minimal internal-workspace layout helpers.

The backend data root owns one direct child per project::

    <data_root>/<project_id>/

Business workspaces are a different path space and are never inspected, moved, or merged by
this module.  The only supported legacy import is the known previous internal layout
``<data_root>/internal/<project_id>`` supplied explicitly by the caller.
"""
from __future__ import annotations

import errno
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ID_RE = re.compile(r"^project_\d{14}$")
_PLATFORM_PROJECT_ID = "__platform__"
_IMPORT_SUFFIX = ".importing"


@dataclass(frozen=True)
class InternalWorkspaceReport:
    """Result used by the current Engine call site.

    ``errors`` is intentionally the only state carried across the boundary.  Import progress is
    not persisted: either the target root is published, or the old root remains authoritative.
    """

    errors: tuple[str, ...] = ()


# Compatibility name for older imports.  It intentionally has no migration-stage fields.
MigrationReport = InternalWorkspaceReport


@dataclass(frozen=True)
class InternalWorkspacePaths:
    """Small convenience view retained for compatibility with older callers."""

    root: Path

    @property
    def handoffs(self) -> Path:
        return self.root / "handoffs"

    @property
    def agents(self) -> Path:
        return self.root / "agents"

    @property
    def memory(self) -> Path:
        return self.root / "memory"

    @property
    def knowledge(self) -> Path:
        return self.root / "knowledge"


def _resolve(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _validate_project_id(project_id: str) -> str:
    value = str(project_id)
    if (
        not value
        or value in {".", ".."}
        or Path(value).is_absolute()
        or "/" in value
        or "\\" in value
    ):
        raise ValueError(f"非法项目 ID：{project_id!r}")
    if value != _PLATFORM_PROJECT_ID and _PROJECT_ID_RE.fullmatch(value) is None:
        raise ValueError(f"非法项目 ID：{project_id!r}")
    return value


def safe_component(value: str) -> str:
    """Return a valid project path component without silently rewriting it."""

    return _validate_project_id(value)


def internal_workspace_for(data_root: Path | str, project_id: str) -> Path:
    """Return the direct internal root ``<data_root>/<project_id>``.

    The second parent check is deliberate: validation must remain correct even if path parsing
    behavior differs across Windows and POSIX.
    """

    root = _resolve(data_root)
    component = _validate_project_id(project_id)
    target = (root / component).resolve(strict=False)
    if target.parent != root:
        raise ValueError(f"项目内部目录越出数据根：{target}")
    return target


def validate_separation(workspace_root: Path, internal_root: Path) -> None:
    """Reject a business root that contains the backend root, or vice versa."""

    workspace = _resolve(workspace_root)
    internal = _resolve(internal_root)
    if workspace == internal or workspace in internal.parents or internal in workspace.parents:
        raise ValueError("项目业务目录与 Knowe 内部工作区发生包含关系")


def _regular_files(root: Path) -> dict[str, int]:
    """Return a basic file/size inventory without following links."""

    inventory: dict[str, int] = {}
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in list(dirnames):
            child = directory_path / name
            if child.is_symlink():
                raise ValueError(f"旧内部数据包含符号链接：{child}")
        for name in filenames:
            child = directory_path / name
            if child.is_symlink() or not child.is_file():
                raise ValueError(f"旧内部数据包含非普通文件：{child}")
            rel = child.relative_to(root).as_posix()
            inventory[rel] = int(child.stat().st_size)
    return inventory


def _copy_cross_volume(source: Path, target: Path) -> None:
    staging = target.with_name(target.name + _IMPORT_SUFFIX)
    if _lexists(staging):
        # A previous interrupted copy was never authoritative.  Start the one-shot copy again.
        if staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)
        else:
            staging.unlink()
    expected = _regular_files(source)
    shutil.copytree(source, staging, symlinks=False)
    actual = _regular_files(staging)
    if actual != expected:
        shutil.rmtree(staging, ignore_errors=True)
        raise OSError("跨盘导入校验失败")
    os.replace(staging, target)


def _is_cross_volume(exc: OSError) -> bool:
    return exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17


def ensure_internal_workspace(
    root: Path,
    *,
    legacy_workspace: Path | None = None,
) -> InternalWorkspaceReport:
    """Create one direct internal root and import only the known legacy root.

    Rules are intentionally small:
    * old-only: publish by same-volume rename, or copy through ``.importing`` across volumes;
    * new-only: use the new root;
    * old and new together: fail rather than merge;
    * neither: create the new root.
    """

    target = _resolve(root)
    legacy = _resolve(legacy_workspace) if legacy_workspace is not None else None
    errors: list[str] = []

    if legacy is not None and legacy == target:
        legacy = None

    target_exists = _lexists(target)
    legacy_exists = legacy is not None and _lexists(legacy)

    if target_exists and (target.is_symlink() or not target.is_dir()):
        return InternalWorkspaceReport((f"内部工作区不是普通目录：{target}",))
    if legacy_exists and legacy is not None and (legacy.is_symlink() or not legacy.is_dir()):
        return InternalWorkspaceReport((f"旧内部工作区不是普通目录：{legacy}",))
    if target_exists and legacy_exists:
        return InternalWorkspaceReport((
            f"新旧内部工作区同时存在，拒绝自动合并：{legacy}；{target}",
        ))

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if legacy_exists and legacy is not None:
            try:
                os.replace(legacy, target)
            except OSError as exc:
                if not _is_cross_volume(exc):
                    raise
                _copy_cross_volume(legacy, target)
                shutil.rmtree(legacy)
            # Remove the now-empty historical ``internal`` container when safe.
            with os.scandir(legacy.parent) as entries:
                if next(entries, None) is None:
                    legacy.parent.rmdir()
        else:
            target.mkdir(parents=True, exist_ok=True)

        # These are the only current internal subtrees.  Creating them is idempotent and avoids
        # each subsystem inventing a different bootstrap path.
        for name in ("runtime", "handoffs", "agents", "memory", "knowledge"):
            (target / name).mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        errors.append(" ".join(str(exc).split()) or exc.__class__.__name__)

    return InternalWorkspaceReport(tuple(errors))


__all__ = [
    "InternalWorkspacePaths",
    "InternalWorkspaceReport",
    "MigrationReport",
    "ensure_internal_workspace",
    "internal_workspace_for",
    "safe_component",
    "validate_separation",
]
