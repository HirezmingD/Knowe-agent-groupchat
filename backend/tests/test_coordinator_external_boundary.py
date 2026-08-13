from __future__ import annotations

from pathlib import Path

from backend.engine import ProjectEngine
from backend.hub import Hub
from backend.tools_knowe import build_coordinator_registry


def test_coordinator_cannot_read_user_or_peer_project_paths(tmp_path: Path) -> None:
    current = tmp_path / "projects" / "current"
    peer = tmp_path / "projects" / "peer"
    private = tmp_path / "profile" / ".ssh"
    for directory in (current, peer, private):
        directory.mkdir(parents=True)
    (peer / "secret.txt").write_text("peer-secret", encoding="utf-8")
    (private / "id_ed25519").write_text("host-secret", encoding="utf-8")

    data_root = tmp_path / "knowe-data"
    engine = ProjectEngine(
        Hub(),
        "project_20260813000001",
        workspace_root=current,
        internal_workspace_root=data_root / "project_20260813000001",
        backend_data_root=data_root,
    )
    registry = build_coordinator_registry(engine)

    assert "read_external_file" not in registry.names()
    assert "list_external_dir" not in registry.names()
    rendered = str(registry.get_schemas())
    assert str(peer) not in rendered
    assert str(private) not in rendered
