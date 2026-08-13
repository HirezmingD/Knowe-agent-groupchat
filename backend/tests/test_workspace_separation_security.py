from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from websockets.protocol import State

from backend.hub import Client
from backend.server import KnoweServer
from backend.workspace_layout import validate_peer_separation, validate_separation


@pytest.mark.parametrize("business_side", ("parent", "child", "same"))
def test_business_workspace_cannot_overlap_private_data(
    tmp_path: Path, business_side: str,
) -> None:
    data = tmp_path / "knowe-data"
    if business_side == "parent":
        business = tmp_path
    elif business_side == "child":
        business = data / "user-project"
    else:
        business = data
    with pytest.raises(ValueError):
        validate_separation(
            business,
            data / "project_20260813000000",
            protected_roots=(data,),
        )


def test_sibling_business_workspace_is_allowed(tmp_path: Path) -> None:
    validate_separation(
        tmp_path / "projects" / "one",
        tmp_path / "knowe-data" / "project_20260813000000",
        protected_roots=(tmp_path / "knowe-data",),
    )


@pytest.mark.parametrize("candidate_side", ("parent", "child", "same"))
def test_business_workspaces_cannot_contain_each_other(
    tmp_path: Path, candidate_side: str,
) -> None:
    existing = tmp_path / "projects" / "existing"
    if candidate_side == "parent":
        candidate = existing.parent
    elif candidate_side == "child":
        candidate = existing / "nested"
    else:
        candidate = existing
    with pytest.raises(ValueError, match="另一个项目"):
        validate_peer_separation(candidate, (existing,))


def test_sibling_business_workspaces_are_allowed(tmp_path: Path) -> None:
    validate_peer_separation(
        tmp_path / "projects" / "one",
        (tmp_path / "projects" / "two",),
    )


@pytest.mark.asyncio
async def test_directory_recovery_rechecks_peer_boundary_after_await(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent peer commit during engine shutdown must not win a stale check."""

    current_id = "project_20260813000001"
    peer_id = "project_20260813000002"
    old_root = tmp_path / "projects" / "old"
    candidate = tmp_path / "projects" / "candidate"
    for root in (old_root, candidate):
        root.mkdir(parents=True)

    from backend.server import CONFIG as server_config
    object.__setattr__(server_config, "install_root", str(tmp_path / "install"))
    server = KnoweServer(data_dir=str(tmp_path / "knowe-data"))
    server.hub.get_or_create(current_id, "Current")
    server.project_dirs[current_id] = str(old_root)
    server.project_dir_status[current_id] = {
        "status": "invalid",
        "path": str(old_root),
        "reason": "missing",
        "request_id": "dir-current",
    }

    async def quarantine_then_commit_peer(project_id: str) -> None:
        assert project_id == current_id
        server.project_dirs[peer_id] = str(candidate)

    server._quarantine_project = quarantine_then_commit_peer  # type: ignore[method-assign]

    sent: list[str] = []

    async def send(payload: str) -> None:
        sent.append(payload)

    client = Client(SimpleNamespace(state=State.OPEN, send=send))
    await server._cmd_set_project_directory(client, {
        "project_id": current_id,
        "project_dir": str(candidate),
        "request_id": "dir-current",
    })

    assert server.project_dirs[current_id] == str(old_root)
    assert server.project_dirs[peer_id] == str(candidate)
    assert server.project_dir_status[current_id]["reason"] == "replacement_conflict"
    assert any('"type": "error"' in payload for payload in sent)
