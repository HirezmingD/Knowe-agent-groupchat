"""Focused regression tests for the v0.18 ghost-group identity fix."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest


def _load_server_module():
    for name in ("backend.backend.server", "backend.server"):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError("cannot import Knowe backend server module")


server_module = _load_server_module()
KnoweServer = server_module.KnoweServer
ProjectIdResolutionError = server_module.ProjectIdResolutionError
PLATFORM_PROJECT_ID = server_module.PLATFORM_PROJECT_ID


class _HubStub:
    def __init__(self) -> None:
        self.projects: dict[str, object] = {}
        self.no_seq_events: list[dict[str, object]] = []

    def has(self, project_id: str) -> bool:
        return project_id in self.projects

    async def emit_no_seq(self, event: dict[str, object]) -> None:
        self.no_seq_events.append(event)


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch):
    """Build only the identity-resolution state; avoid engines, files and network startup."""
    srv = object.__new__(KnoweServer)
    srv.project_card_ids = {}
    srv.hub = _HubStub()
    srv.engines = {}
    srv.project_dirs = {}
    srv.project_dir_status = {}
    srv._persisted_project_ids = set()
    srv._save_project_card_ids = lambda: None
    pending: set[str] = set()
    srv._pending_create_card = lambda approval_id: (
        {"approval_id": approval_id} if approval_id in pending else None
    )
    srv._test_pending_cards = pending

    ids = iter(
        (
            "project_20260715010101",
            "project_20260715010102",
            "project_20260715010103",
        )
    )
    monkeypatch.setattr(server_module, "new_project_id", lambda: next(ids))
    return srv


def test_canonical_card_id_is_bound_before_approve_fallback(server) -> None:
    approval_id = "ap_card_1"
    canonical = "project_20260715120000"
    server._test_pending_cards.add(approval_id)

    assert server._bind_project_id_to_approval(approval_id, canonical) == canonical
    assert server._project_id_for_approval(approval_id) == canonical

    # A renderer that lost its local card cache may retry with another optimistic id. The original
    # binding must win so the caller can reconcile the retry instead of creating another project.
    assert (
        server._bind_project_id_to_approval(approval_id, "project_20260715120001")
        == canonical
    )

    assert server.project_card_ids[approval_id] == canonical
    assert server.project_card_ids[f"approval:{approval_id}"] == canonical
    assert server.project_card_ids[f"request:p_{approval_id}"] == canonical


def test_unknown_aliases_never_fall_through_to_hub_creation(server) -> None:
    with pytest.raises(ProjectIdResolutionError):
        server._canonical_project_id_from_request("p_ap_missing", allocate=False)
    with pytest.raises(ProjectIdResolutionError):
        server._canonical_project_id_from_request("old-client-slug", allocate=False)
    with pytest.raises(ProjectIdResolutionError):
        server._canonical_project_id_from_request("p_ap_missing", allocate=True)
    with pytest.raises(ProjectIdResolutionError):
        server._canonical_project_id_from_request(PLATFORM_PROJECT_ID, allocate=True)

    # Genuine persisted legacy projects remain addressable by their exact old id.
    server._persisted_project_ids.add("p_legacy_real_project")
    assert (
        server._canonical_project_id_from_request("p_legacy_real_project")
        == "p_legacy_real_project"
    )


def test_old_p_ap_client_maps_to_one_canonical_project(server) -> None:
    approval_id = "ap_old_client"
    request_id = f"p_{approval_id}"
    server._test_pending_cards.add(approval_id)

    canonical = server._canonical_project_id_from_request(request_id, allocate=True)
    assert canonical == "project_20260715010101"

    # Once the real project exists, every old alias reference resolves to that same project.
    server.hub.projects[canonical] = SimpleNamespace(name="legacy-compatible")
    assert server._canonical_project_id_from_request(request_id) == canonical


def test_create_project_retry_reconciles_to_existing_approval_binding(
    server, tmp_path,
) -> None:
    approval_id = "ap_retry"
    canonical = "project_20260715123000"
    retry_id = "project_20260715123001"
    workspace = tmp_path / "retry-workspace"
    workspace.mkdir()
    project_dir = str(workspace.resolve())
    server._test_pending_cards.add(approval_id)
    server._directory_required_info = lambda _project_id: None

    created: list[tuple[str, str, str | None, str | None]] = []

    async def fake_create_project(
        project_id: str,
        project_name: str,
        project_dir: str | None = None,
        *,
        request_project_id: str | None = None,
        roles: list[str] | None = None,
    ) -> str:
        assert roles == []
        created.append((project_id, project_name, project_dir, request_project_id))
        server.hub.projects[project_id] = SimpleNamespace(name=project_name, unread_count=0)
        return project_id

    server.create_project = fake_create_project

    asyncio.run(server._cmd_create_project(None, {
        "type": "create_project",
        "project_id": canonical,
        "project_name": "唯一群",
        "project_dir": project_dir,
        "approval_id": approval_id,
    }))
    assert created == [(canonical, "唯一群", project_dir, None)]

    # Simulate a renderer retry that proposes another canonical id for the same card.
    asyncio.run(server._cmd_create_project(None, {
        "type": "create_project",
        "project_id": retry_id,
        "project_name": "唯一群",
        "project_dir": project_dir,
        "approval_id": approval_id,
    }))

    assert len(created) == 1
    assert server.hub.no_seq_events[-1]["project_id"] == canonical
    assert server.hub.no_seq_events[-1]["request_project_id"] == retry_id
    assert server._canonical_project_id_from_request(retry_id) == canonical
