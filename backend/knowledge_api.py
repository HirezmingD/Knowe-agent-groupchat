"""Knowledge HTTP routes dispatched by the existing asyncio control server.

There is deliberately no listener, thread, wildcard CORS policy, or cross-thread coroutine bridge
in this module.  Engines register their project managers; ``dispatch_knowledge_http`` is called by
``backend.server`` after the shared Runtime Secret and ordinary HTTP limits have been verified.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine
from urllib.parse import parse_qs, unquote

log = logging.getLogger("knowe.knowledge.api")

_OVERRIDE_TIMEOUT_S = 15.0
_MAX_BODY_BYTES = 64 * 1024


@dataclass
class _ProjectReg:
    project_id: str
    manager: Any
    workspace_provider: Callable[[], Path]
    loop: asyncio.AbstractEventLoop | None = None
    order: int = field(default=0)
    assets: Any | None = None


@dataclass(frozen=True)
class KnowledgeHttpResponse:
    status: int
    body: dict[str, Any]


_lock = threading.Lock()
_projects: dict[str, _ProjectReg] = {}
_order_seq = 0


def register_project(
    project_id: str,
    *,
    manager: Any,
    workspace_provider: Callable[[], Path],
    loop: asyncio.AbstractEventLoop | None = None,
    assets: Any | None = None,
) -> None:
    """Register or refresh one project's managers.  This operation is idempotent."""
    global _order_seq
    if not project_id or project_id == "__platform__" or manager is None:
        return
    with _lock:
        existing = _projects.get(project_id)
        order = existing.order if existing else _order_seq
        if existing is None:
            _order_seq += 1
        _projects[project_id] = _ProjectReg(
            project_id=project_id,
            manager=manager,
            workspace_provider=workspace_provider,
            loop=loop,
            order=order,
            assets=assets,
        )


def unregister_project(project_id: str) -> None:
    with _lock:
        _projects.pop(project_id, None)


def project_count() -> int:
    with _lock:
        return len(_projects)


def _registrations() -> list[_ProjectReg]:
    with _lock:
        return sorted(_projects.values(), key=lambda row: row.order)


def _registration(project_id: str) -> _ProjectReg | None:
    with _lock:
        return _projects.get(project_id)


def _ok(payload: dict[str, Any], status: int = 200) -> KnowledgeHttpResponse:
    return KnowledgeHttpResponse(status, payload)


def _fail(status: int, error: str, **extra: Any) -> KnowledgeHttpResponse:
    return _ok({"ok": False, "error": error, **extra}, status)


def _int_of(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _project_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for reg in _registrations():
        try:
            snap = reg.manager.snapshot(reg.project_id, reg.workspace_provider(), limit=1)
            rows.append({
                "project_id": reg.project_id,
                "revision": snap.get("revision", 0),
                "updated_at": snap.get("updated_at"),
                "node_count": (snap.get("counts") or {}).get("total", 0),
            })
        except Exception:
            log.exception("[%s] 项目行组装失败（跳过）", reg.project_id)
    return rows


def _merged_nodes(only_project: str | None) -> dict[str, Any]:
    projects: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    for reg in _registrations():
        if only_project and reg.project_id != only_project:
            continue
        try:
            snap = reg.manager.snapshot(reg.project_id, reg.workspace_provider())
        except Exception:
            log.exception("[%s] 合并快照失败（跳过该项目）", reg.project_id)
            continue
        projects.append({
            "project_id": reg.project_id,
            "revision": snap.get("revision", 0),
            "updated_at": snap.get("updated_at"),
            "counts": snap.get("counts") or {},
        })
        nodes.extend(snap.get("nodes") or [])
    nodes.sort(key=lambda row: str(row.get("last_seen") or ""), reverse=True)
    return {"ok": True, "projects": projects, "nodes": nodes}


def _merged_assets(only_project: str | None) -> dict[str, Any]:
    projects: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    for reg in _registrations():
        if only_project and reg.project_id != only_project:
            continue
        if reg.assets is None:
            continue
        try:
            snap = reg.assets.snapshot(reg.project_id, reg.workspace_provider())
        except Exception:
            log.exception("[%s] 资产合并快照失败（跳过该项目）", reg.project_id)
            continue
        projects.append({
            "project_id": reg.project_id,
            "revision": snap.get("revision", 0),
            "updated_at": snap.get("updated_at"),
            "counts": snap.get("counts") or {},
            "profile_exists": bool(snap.get("profile_exists")),
        })
        for raw in snap.get("assets") or []:
            if isinstance(raw, dict):
                row = dict(raw)
                row["project_id"] = reg.project_id
                assets.append(row)
    assets.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return {"ok": True, "projects": projects, "assets": assets}


def _merged_skillpacks(only_project: str | None) -> dict[str, Any]:
    system_by_id: dict[str, dict[str, Any]] = {}
    experiences: list[dict[str, Any]] = []
    third_party_by_id: dict[str, dict[str, Any]] = {}
    for reg in _registrations():
        if only_project and reg.project_id != only_project:
            continue
        if reg.assets is None:
            continue
        try:
            packs = reg.assets.list_skillpacks(reg.project_id, reg.workspace_provider())
        except Exception:
            log.exception("[%s] 技能包列举失败（跳过该项目）", reg.project_id)
            continue
        for raw in packs.get("system_builtin") or []:
            if isinstance(raw, dict) and raw.get("pack_id"):
                system_by_id[str(raw["pack_id"])] = raw
        for raw in packs.get("project_experience") or []:
            if isinstance(raw, dict) and raw.get("pack_id"):
                experiences.append(raw)
        for raw in packs.get("third_party") or []:
            if isinstance(raw, dict) and raw.get("pack_id"):
                third_party_by_id[str(raw["pack_id"])] = raw
    system = sorted(system_by_id.values(), key=lambda row: str(row.get("name") or "").casefold())
    experiences.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    third_party = sorted(
        third_party_by_id.values(), key=lambda row: str(row.get("name") or "").casefold(),
    )
    return {
        "ok": True,
        "system_builtin": system,
        "project_experience": experiences,
        "third_party": third_party,
        "system": system + experiences,
    }


def _find_skillpack(pack_id: str) -> tuple[_ProjectReg, dict[str, Any]] | None:
    wanted = str(pack_id or "").strip()
    if not wanted:
        return None
    for reg in _registrations():
        if reg.assets is None:
            continue
        try:
            result = reg.assets.read_skillpack(
                reg.project_id, reg.workspace_provider(), wanted, active_only=False,
            )
        except Exception:
            log.exception("[%s] 技能定位失败（继续找）：%s", reg.project_id, wanted)
            continue
        if result.get("found"):
            return reg, result
    return None


def _dispatch_get_sync(parts: list[str], query: dict[str, list[str]]) -> KnowledgeHttpResponse:
    rest = parts[2:]
    if rest == ["health"]:
        return _ok({"ok": True, "projects": project_count()})
    if rest == ["projects"]:
        return _ok({"ok": True, "projects": _project_rows()})
    if rest == ["nodes"]:
        return _ok(_merged_nodes((query.get("project") or [None])[0]))
    if rest == ["assets"]:
        return _ok(_merged_assets((query.get("project") or [None])[0]))
    if rest == ["skillpacks"]:
        return _ok(_merged_skillpacks((query.get("project") or [None])[0]))
    if len(rest) == 2 and rest[0] == "skillpacks":
        found = _find_skillpack(rest[1])
        return (
            _fail(404, "skillpack_not_found", pack_id=rest[1])
            if found is None else _ok({"ok": True, **found[1]})
        )
    if len(rest) == 2 and rest[1] == "assets":
        reg = _registration(rest[0])
        if reg is None:
            return _fail(404, "unknown_project", project_id=rest[0])
        if reg.assets is None:
            return _fail(503, "assets_unavailable", project_id=rest[0])
        return _ok({"ok": True, **reg.assets.snapshot(reg.project_id, reg.workspace_provider())})
    if len(rest) == 3 and rest[1] == "assets":
        reg = _registration(rest[0])
        if reg is None:
            return _fail(404, "unknown_project", project_id=rest[0])
        if reg.assets is None:
            return _fail(503, "assets_unavailable", project_id=rest[0])
        result = reg.assets.read_asset(reg.project_id, reg.workspace_provider(), rest[2])
        return _ok({"ok": True, **result})
    if len(rest) == 2 and rest[1] == "profile":
        reg = _registration(rest[0])
        if reg is None:
            return _fail(404, "unknown_project", project_id=rest[0])
        if reg.assets is None:
            return _fail(503, "assets_unavailable", project_id=rest[0])
        text = reg.assets.profile_text(reg.workspace_provider())
        return _ok({"ok": True, "project_id": reg.project_id, "text": text})
    if len(rest) == 2 and rest[1] == "nodes":
        reg = _registration(rest[0])
        if reg is None:
            return _fail(404, "unknown_project", project_id=rest[0])
        return _ok({"ok": True, **reg.manager.snapshot(reg.project_id, reg.workspace_provider())})
    if len(rest) == 2 and rest[1] == "search":
        reg = _registration(rest[0])
        if reg is None:
            return _fail(404, "unknown_project", project_id=rest[0])
        q = (query.get("q") or [""])[0]
        limit = _int_of((query.get("limit") or ["6"])[0], 6)
        result = reg.manager.search(reg.project_id, reg.workspace_provider(), q, limit=limit)
        return _ok({"ok": True, **result})
    if len(rest) == 2 and rest[1] == "node":
        reg = _registration(rest[0])
        if reg is None:
            return _fail(404, "unknown_project", project_id=rest[0])
        ref = (query.get("ref") or [""])[0]
        if not ref:
            return _fail(400, "missing_ref")
        result = reg.manager.read_node(reg.project_id, reg.workspace_provider(), ref)
        return _ok({"ok": True, **result})
    return _fail(404, "not_found")


async def _run_override(
    reg: _ProjectReg,
    operation: Coroutine[Any, Any, dict[str, Any]],
    *,
    not_found_reason: str,
) -> KnowledgeHttpResponse:
    current = asyncio.get_running_loop()
    if reg.loop is not None and (reg.loop.is_closed() or reg.loop is not current):
        operation.close()
        return _fail(503, "engine_loop_unavailable", project_id=reg.project_id)
    try:
        result = await asyncio.wait_for(operation, timeout=_OVERRIDE_TIMEOUT_S)
    except asyncio.TimeoutError:
        return _fail(504, "override_timeout")
    code = 200 if result.get("ok") else (404 if result.get("reason") == not_found_reason else 400)
    return _ok(result, code)


async def _dispatch_post(parts: list[str], body: dict[str, Any]) -> KnowledgeHttpResponse:
    if len(parts) < 4:
        return _fail(404, "not_found")
    if parts[2] == "skillpacks":
        pack_id = parts[3]
        found = await asyncio.to_thread(_find_skillpack, pack_id)
        if found is None:
            return _fail(404, "skillpack_not_found", pack_id=pack_id)
        reg, _detail = found
        if reg.assets is None:
            return _fail(503, "assets_unavailable", project_id=reg.project_id)
        if len(parts) == 4:
            status = str(body.get("status") or "")
            if not status:
                return _fail(400, "empty_patch")
            return await _run_override(
                reg,
                reg.assets.set_skillpack_status(
                    reg.project_id, reg.workspace_provider(), pack_id, status,
                ),
                not_found_reason="skillpack_not_found",
            )
        if len(parts) == 5 and parts[4] == "review":
            return await _run_override(
                reg,
                reg.assets.review_skillpack(
                    reg.project_id, reg.workspace_provider(), pack_id,
                    str(body.get("action") or ""),
                ),
                not_found_reason="skillpack_not_found",
            )
        if len(parts) == 5 and parts[4] == "purge":
            return await _run_override(
                reg,
                reg.assets.purge_skillpack(reg.project_id, reg.workspace_provider(), pack_id),
                not_found_reason="skillpack_not_found",
            )
        return _fail(404, "not_found")

    project_id = parts[2]
    reg = _registration(project_id)
    if reg is None:
        return _fail(404, "unknown_project", project_id=project_id)
    if len(parts) == 5 and parts[3] == "nodes":
        patch = {
            key: body.get(key)
            for key in ("status", "scope", "title", "summary")
            if body.get(key) is not None
        }
        if not patch:
            return _fail(400, "empty_patch")
        return await _run_override(
            reg,
            reg.manager.apply_user_override(
                reg.project_id, reg.workspace_provider(), parts[4], **patch,
            ),
            not_found_reason="node_not_found",
        )
    if len(parts) >= 5 and parts[3] == "assets":
        if reg.assets is None:
            return _fail(503, "assets_unavailable", project_id=project_id)
        asset_id = parts[4]
        if len(parts) == 6 and parts[5] == "review":
            return await _run_override(
                reg,
                reg.assets.review(
                    reg.project_id, reg.workspace_provider(), asset_id,
                    str(body.get("action") or ""),
                ),
                not_found_reason="asset_not_found",
            )
        if len(parts) == 6 and parts[5] == "purge":
            return await _run_override(
                reg,
                reg.assets.purge(reg.project_id, reg.workspace_provider(), asset_id),
                not_found_reason="asset_not_found",
            )
        if len(parts) == 5:
            patch = {
                key: body.get(key)
                for key in ("title", "scope", "category", "status")
                if body.get(key) is not None
            }
            if not patch:
                return _fail(400, "empty_patch")
            return await _run_override(
                reg,
                reg.assets.apply_user_override(
                    reg.project_id, reg.workspace_provider(), asset_id, **patch,
                ),
                not_found_reason="asset_not_found",
            )
    if len(parts) == 4 and parts[3] == "profile":
        if reg.assets is None:
            return _fail(503, "assets_unavailable", project_id=project_id)
        return await _run_override(
            reg,
            reg.assets.set_profile(
                reg.project_id, reg.workspace_provider(), str(body.get("text") or ""),
            ),
            not_found_reason="profile_not_found",
        )
    return _fail(404, "not_found")


async def dispatch_knowledge_http(
    method: str,
    path: str,
    query: str,
    body: bytes,
) -> KnowledgeHttpResponse:
    """Dispatch one already-authenticated request without opening another server."""
    try:
        parts = [unquote(part) for part in path.strip("/").split("/") if part]
        if parts[:2] != ["api", "knowledge"]:
            return _fail(404, "not_found")
        verb = method.upper()
        if verb == "GET":
            return await asyncio.to_thread(_dispatch_get_sync, parts, parse_qs(query))
        if verb == "POST":
            if len(body) > _MAX_BODY_BYTES:
                return _fail(413, "body_too_large")
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                return _fail(400, "bad_json")
            if not isinstance(payload, dict):
                return _fail(400, "bad_json")
            return await _dispatch_post(parts, payload)
        return _fail(405, "method_not_allowed")
    except Exception as exc:
        log.exception("知识库数据面处理异常：%s %s", method, path)
        return _fail(500, type(exc).__name__)


__all__ = [
    "KnowledgeHttpResponse",
    "dispatch_knowledge_http",
    "project_count",
    "register_project",
    "unregister_project",
]
