from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import websockets
from websockets.exceptions import InvalidStatus

from backend import server as server_module
from backend.config import CONFIG
from backend.delete_ops import (
    DELETE_RENAME_DELAYS,
    DeletePathBusyError,
    purge_staged_path,
    restore_staged_path,
    stage_project_root,
)
from backend.engine import ProjectEngine
from backend.hub import Hub
from backend.tools_knowe import build_coordinator_registry
from backend.server import KnoweServer, ProjectClosingError, ProjectDeleteError
from backend.workspace_layout import ensure_internal_workspace, internal_workspace_for


PID = "project_20260730203701"
TOKEN_HEADER = "X-Knowe-Runtime-Token"


def _request(path: str, *, method: str = "GET", token: str | None = None,
             headers: list[tuple[str, str]] | None = None, body: bytes = b"") -> bytes:
    fields = [("Host", "127.0.0.1")]
    if token is not None:
        fields.append((TOKEN_HEADER, token))
    fields.extend(headers or [])
    if method == "POST":
        fields.extend([
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ])
    head = f"{method} {path} HTTP/1.1\r\n" + "".join(
        f"{name}: {value}\r\n" for name, value in fields
    ) + "\r\n"
    return head.encode("latin-1") + body


async def _raw_http(port: int, payload: bytes, *, timeout: float = 3.0) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout=timeout)
    writer.close()
    await writer.wait_closed()
    return raw


def _status_and_body(raw: bytes) -> tuple[int, bytes, dict[str, str]]:
    head, body = raw.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {
        name.strip().lower(): value.strip()
        for line in lines[1:]
        if ":" in line
        for name, value in [line.split(":", 1)]
    }
    return status, body, headers


def test_direct_internal_layout_and_strict_project_component(tmp_path: Path) -> None:
    root = tmp_path / "data" / "backend"
    assert internal_workspace_for(root, PID) == root.resolve() / PID
    assert internal_workspace_for(root, "__platform__") == root.resolve() / "__platform__"
    for invalid in ("", ".", "..", "p1", "project_123", "/absolute", "a/b", r"a\b"):
        with pytest.raises(ValueError):
            internal_workspace_for(root, invalid)


def test_one_shot_legacy_internal_import_is_direct_and_conflict_safe(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "backend"
    legacy = data_root / "internal" / PID
    (legacy / "runtime").mkdir(parents=True)
    (legacy / "runtime" / "runtime.sqlite3").write_bytes(b"legacy")
    target = internal_workspace_for(data_root, PID)

    report = ensure_internal_workspace(target, legacy_workspace=legacy)
    assert report.errors == ()
    assert (target / "runtime" / "runtime.sqlite3").read_bytes() == b"legacy"
    assert not legacy.exists()

    conflict_source = data_root / "internal" / PID
    conflict_source.mkdir(parents=True)
    (conflict_source / "other.txt").write_text("other", encoding="utf-8")
    conflict = ensure_internal_workspace(target, legacy_workspace=conflict_source)
    assert conflict.errors and "同时存在" in conflict.errors[0]
    assert (target / "runtime" / "runtime.sqlite3").read_bytes() == b"legacy"
    assert (conflict_source / "other.txt").read_text("utf-8") == "other"


@pytest.mark.asyncio
async def test_engine_stop_closes_completion_store_and_is_idempotent(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "backend"
    workspace = tmp_path / "workspaces" / "demo"
    workspace.mkdir(parents=True)
    internal = internal_workspace_for(data_root, PID)
    engine = ProjectEngine(
        Hub(), PID,
        workspace_root=workspace,
        internal_workspace_root=internal,
        backend_data_root=data_root,
    )
    store = engine.completion_store
    database = internal / "runtime" / "runtime.sqlite3"
    assert database.is_file()

    first = await engine.stop(immediate=True)
    second = await engine.stop(immediate=True)
    assert isinstance(first, list) and second == []
    assert store.db._closed is True  # explicit close boundary, not __del__/GC

    moved = database.with_suffix(".moved")
    os.replace(database, moved)
    assert moved.is_file()


@pytest.mark.asyncio
async def test_engine_stop_survives_waiter_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    engine = ProjectEngine(Hub(), PID, workspace_root=workspace)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def controlled_stop(*, immediate: bool = False) -> list[str]:
        del immediate
        entered.set()
        await release.wait()
        return []

    monkeypatch.setattr(engine, "_stop_once", controlled_stop)
    waiter = asyncio.create_task(engine.stop(immediate=True))
    await entered.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert engine._stopping is True and engine._stopped is False

    release.set()
    assert await engine.stop(immediate=True) == []
    assert engine._stopped is True


def test_delete_root_is_one_rename_and_never_touches_business_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    internal = tmp_path / "data" / "backend" / PID
    business = tmp_path / "workspaces" / "demo"
    internal.mkdir(parents=True)
    business.mkdir(parents=True)
    (internal / "runtime.sqlite3").write_bytes(b"internal")
    business_file = business / "answer.txt"
    business_file.write_bytes(b"user-owned")
    before = hashlib.sha256(business_file.read_bytes()).hexdigest()
    staged = internal.with_name(f".{PID}.knowe-delete-test")

    stage_project_root(internal, staged)
    assert not internal.exists() and staged.is_dir()
    assert hashlib.sha256(business_file.read_bytes()).hexdigest() == before
    restore_staged_path(internal, staged)
    assert internal.is_dir() and not staged.exists()
    stage_project_root(internal, staged)
    purge_staged_path(internal, staged)
    assert not internal.exists() and not staged.exists()
    assert hashlib.sha256(business_file.read_bytes()).hexdigest() == before

    attempts = 0

    def locked_rename(_source: Path, _target: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError(errno.EACCES, "locked")

    internal.mkdir()
    monkeypatch.setattr(os, "rename", locked_rename)
    # Patch the actual delete_ops sleep without spending the 0.5s budget in a unit test.
    import backend.delete_ops as delete_ops
    monkeypatch.setattr(delete_ops.time, "sleep", lambda *_: None)
    with pytest.raises(DeletePathBusyError):
        delete_ops.stage_delete_path(internal, staged)
    assert attempts == len(DELETE_RENAME_DELAYS)
    assert internal.exists() and not staged.exists()


@pytest.mark.asyncio
async def test_http_runtime_auth_knowledge_and_shutdown_share_one_boundary(tmp_path: Path) -> None:
    server = KnoweServer(data_dir=str(tmp_path / "data" / "backend"))
    listener = await asyncio.start_server(server._health_conn, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    try:
        for request in (
            _request("/health"),
            _request("/health", token="f" * 64),
            _request("/health", headers=[("Origin", "null")]),
        ):
            status, body, headers = _status_and_body(await _raw_http(port, request))
            assert status == 401 and b"runtime_auth_required" in body
            assert "access-control-allow-origin" not in headers

        status, body, headers = _status_and_body(await _raw_http(
            port,
            _request("/health", token=CONFIG.runtime_token, headers=[("Origin", "null")]),
        ))
        assert status == 200 and json.loads(body)["status"] == "ok"
        assert headers.get("access-control-allow-origin") == "null"

        status, body, _ = _status_and_body(await _raw_http(
            port, _request("/api/knowledge/health", token=CONFIG.runtime_token),
        ))
        assert status == 200 and json.loads(body)["ok"] is True

        status, _, _ = _status_and_body(await _raw_http(
            port, _request("/shutdown", method="POST", body=b"{}"),
        ))
        assert status == 401 and not server._shutdown_event.is_set()
        status, body, _ = _status_and_body(await _raw_http(
            port,
            _request("/shutdown", method="POST", token=CONFIG.runtime_token, body=b"{}"),
        ))
        assert status == 202 and json.loads(body)["shutting_down"] is True
        assert server._shutdown_event.is_set()
    finally:
        listener.close()
        await listener.wait_closed()


@pytest.mark.asyncio
async def test_http_limits_timeout_and_safe_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    server = KnoweServer(data_dir=str(tmp_path / "data" / "backend"))
    listener = await asyncio.start_server(server._health_conn, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    try:
        long_line = b"GET /" + (b"x" * 9000) + b" HTTP/1.1\r\n\r\n"
        assert _status_and_body(await _raw_http(port, long_line))[0] == 414

        many_headers = [(f"X-Test-{index}", "v") for index in range(101)]
        assert _status_and_body(await _raw_http(
            port, _request("/health", token=CONFIG.runtime_token, headers=many_headers),
        ))[0] == 431

        monkeypatch.setattr(server_module, "HTTP_HEADERS_TOTAL_TIMEOUT_S", 0.05)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n")
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=1)
        writer.close()
        await writer.wait_closed()
        assert _status_and_body(raw)[0] == 408

        def fail_health() -> dict[str, object]:
            raise RuntimeError("forced-handler-failure")

        monkeypatch.setattr(server.hub, "health", fail_health)
        caplog.clear()
        status, body, _ = _status_and_body(await _raw_http(
            port, _request("/health", token=CONFIG.runtime_token),
        ))
        assert status == 500
        assert json.loads(body) == {
            "ok": False,
            "code": "internal_error",
            "error": "local runtime request failed",
        }
        assert "forced-handler-failure" in caplog.text
        assert CONFIG.runtime_token not in caplog.text
    finally:
        listener.close()
        await listener.wait_closed()


@pytest.mark.asyncio
async def test_websocket_rejects_before_hub_and_accepts_shared_token(tmp_path: Path) -> None:
    server = KnoweServer(data_dir=str(tmp_path / "data" / "backend"))
    async with websockets.serve(
        server.handle, "127.0.0.1", 0, process_request=server._authenticate_ws,
    ) as listener:
        port = listener.sockets[0].getsockname()[1]
        uri = f"ws://127.0.0.1:{port}"
        with pytest.raises(InvalidStatus) as rejected:
            async with websockets.connect(uri):
                pass
        assert rejected.value.response.status_code == 401
        assert server.hub.client_count == 0

        with pytest.raises(InvalidStatus) as wrong_query:
            async with websockets.connect(f"{uri}/?token={'0' * 64}"):
                pass
        assert wrong_query.value.response.status_code == 401
        assert server.hub.client_count == 0

        async with websockets.connect(
            uri, additional_headers={TOKEN_HEADER: CONFIG.runtime_token},
        ):
            await asyncio.sleep(0)
            assert server.hub.client_count == 1
        for _ in range(20):
            if server.hub.client_count == 0:
                break
            await asyncio.sleep(0.01)
        assert server.hub.client_count == 0

        # Electron's normal path uses a query bearer because Chromium doesn't
        # reliably allow custom WebSocket headers from the renderer.
        async with websockets.connect(f"{uri}/?token={CONFIG.runtime_token}"):
            await asyncio.sleep(0)
            assert server.hub.client_count == 1
        for _ in range(20):
            if server.hub.client_count == 0:
                break
            await asyncio.sleep(0.01)
        assert server.hub.client_count == 0


@pytest.mark.asyncio
async def test_closing_fence_blocks_engine_creation_without_creating_files(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "backend"
    server = KnoweServer(data_dir=str(data_root))
    server._closing_projects.add(PID)
    with pytest.raises(ProjectClosingError):
        server.engine_for(PID, "demo")
    assert PID not in server.engines
    assert not internal_workspace_for(data_root, PID).exists()


@pytest.mark.asyncio
async def test_coordinator_has_no_generic_external_read_capability(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "backend"
    workspace = tmp_path / "workspaces" / "demo"
    workspace.mkdir(parents=True)
    secret = data_root / "__platform__" / "secret.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("internal", encoding="utf-8")
    engine = ProjectEngine(
        Hub(), PID,
        workspace_root=workspace,
        internal_workspace_root=data_root / PID,
        backend_data_root=data_root,
    )
    try:
        registry = build_coordinator_registry(engine)
        assert "read_external_file" not in registry.names()
        assert "list_external_dir" not in registry.names()
        assert str(secret) not in json.dumps(registry.get_schemas(), ensure_ascii=False)
    finally:
        await engine.stop(immediate=True)


class _StubGate:
    def snapshot_pending(self) -> list[object]:
        return []


class _StubEngine:
    def __init__(self, stop_impl):
        self.history: list[dict[str, str]] = []
        self.gate = _StubGate()
        self._stop_impl = stop_impl

    async def stop(self, *, immediate: bool = False) -> list[str]:
        return await self._stop_impl(immediate=immediate)


@pytest.mark.asyncio
async def test_permanent_delete_commits_then_purges_only_internal_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data" / "backend"
    business = tmp_path / "workspaces" / "demo"
    business.mkdir(parents=True)
    business_file = business / "user.txt"
    business_file.write_bytes(b"preserve-me")
    before = hashlib.sha256(business_file.read_bytes()).hexdigest()

    server = KnoweServer(data_dir=str(data_root))
    server.hub.get_or_create(PID, "demo")
    server.project_dirs[PID] = str(business)
    internal = server._internal_workspace_for(PID)
    (internal / "runtime").mkdir(parents=True)
    (internal / "runtime" / "runtime.sqlite3").write_bytes(b"db")

    async def stopped(*, immediate: bool = False) -> list[str]:
        assert immediate is True
        return []

    server.engines[PID] = _StubEngine(stopped)  # type: ignore[assignment]

    async def no_harness_refresh() -> None:
        return None

    monkeypatch.setattr(server, "_update_harness_now", no_harness_refresh)
    result = await server._delete_project_permanently(PID, "del_success123")
    assert result["deleted"] is True and result["cleanup_pending"] is True
    await asyncio.gather(*list(server._purge_tasks))

    assert PID in server.memory.deleted_project_ids()
    assert not internal.exists()
    assert hashlib.sha256(business_file.read_bytes()).hexdigest() == before
    assert server.project_dirs.get(PID) is None


@pytest.mark.asyncio
async def test_delete_stop_timeout_keeps_fence_and_does_not_revive_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data" / "backend"
    server = KnoweServer(data_dir=str(data_root))
    server.hub.get_or_create(PID, "demo")
    internal = server._internal_workspace_for(PID)
    internal.mkdir(parents=True)
    (internal / "runtime.sqlite3").write_bytes(b"still-open")
    release = asyncio.Event()

    async def slow_stop(*, immediate: bool = False) -> list[str]:
        assert immediate is True
        await release.wait()
        return []

    engine = _StubEngine(slow_stop)
    server.engines[PID] = engine  # type: ignore[assignment]
    monkeypatch.setattr(server_module, "DELETE_ENGINE_STOP_TIMEOUT_S", 0.02)
    monkeypatch.setattr(server_module, "DELETE_PRECOMMIT_TIMEOUT_S", 0.2)

    with pytest.raises(ProjectDeleteError) as failed:
        await server._delete_project_permanently(PID, "del_timeout123")
    assert failed.value.stage == "closing"
    assert failed.value.rollback_ok is False
    assert failed.value.self_lock is True
    assert PID in server._closing_projects
    assert server.engines.get(PID) is engine
    assert internal.is_dir()
    assert PID not in server.memory.deleted_project_ids()

    release.set()
    await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_restart_never_constructs_replacement_before_old_engine_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = KnoweServer(data_dir=str(tmp_path / "data" / "backend"))
    server.hub.get_or_create(PID, "demo")

    async def failed_stop(*, immediate: bool = False) -> list[str]:
        assert immediate is True
        raise RuntimeError("sqlite still open")

    old = _StubEngine(failed_stop)
    server.engines[PID] = old  # type: ignore[assignment]
    constructed: list[object] = []

    def forbidden_constructor(*args, **kwargs):
        constructed.append((args, kwargs))
        raise AssertionError("replacement must not be constructed")

    monkeypatch.setattr(server_module, "ProjectEngine", forbidden_constructor)
    await server._restart_engine(PID)
    assert constructed == []
    assert server.engines.get(PID) is old


@pytest.mark.asyncio
async def test_cancel_after_tombstone_commit_never_restores_deleted_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data" / "backend"
    server = KnoweServer(data_dir=str(data_root))
    server.hub.get_or_create(PID, "demo")
    internal = server._internal_workspace_for(PID)
    internal.mkdir(parents=True)
    (internal / "runtime.sqlite3").write_bytes(b"db")

    original_commit = server.memory.record_deleted_project

    async def commit_then_cancel(project_id: str, project_name: str) -> bool:
        assert await original_commit(project_id, project_name) is True
        raise asyncio.CancelledError()

    async def no_harness_refresh() -> None:
        return None

    monkeypatch.setattr(server.memory, "record_deleted_project", commit_then_cancel)
    monkeypatch.setattr(server, "_update_harness_now", no_harness_refresh)

    with pytest.raises(asyncio.CancelledError):
        await server._delete_project_permanently(PID, "del_cancelled1")

    assert PID in server.memory.deleted_project_ids()
    assert not internal.exists(), "committed delete must not restore the original project root"
    await asyncio.gather(*list(server._purge_tasks))
    assert PID not in server.hub.projects


def test_delete_progress_is_registered_as_a_no_seq_wire_event() -> None:
    from backend.contract import NO_SEQ_EVENT_TYPES, validate_outbound

    event = {
        "type": "project_delete_progress",
        "operation_id": "del_0123456789ab",
        "project_id": PID,
        "phase": "closing",
        "message": "正在关闭项目运行资源…",
        "elapsed_ms": 2001,
    }
    validate_outbound(event)
    assert event["type"] in NO_SEQ_EVENT_TYPES
