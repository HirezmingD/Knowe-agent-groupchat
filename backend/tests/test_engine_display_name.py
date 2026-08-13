"""项目经理显示名随语言实时（v1.0.22.1-对齐修复）。

背景：COORDINATOR_ROLE = msg("engine.007") 是模块级固化——英文模式启动后
切回中文，项目经理名/角色仍显示旧英文快照。修复：显示路径全部现取 msg()。
本文件守住这条语义：语言切回 zh → 项目经理名=「项目经理」；切 en → Leader。
"""

from __future__ import annotations

from backend import runtime_settings
from backend.engine import ProjectEngine
from backend.server import KnoweServer


def _engine() -> ProjectEngine:
    eng = object.__new__(ProjectEngine)
    eng._names = {}
    eng._roster = {}
    eng._store = None
    return eng


def test_coordinator_member_name_follows_language(monkeypatch) -> None:
    eng = _engine()
    monkeypatch.setattr(runtime_settings, "language", lambda: "zh")
    assert eng.member_name("coordinator") == "项目经理"
    monkeypatch.setattr(runtime_settings, "language", lambda: "en")
    assert eng.member_name("coordinator") == "Leader"
    # 切回中文必须立即回到「项目经理」（不缓存、不读旧语言快照）
    monkeypatch.setattr(runtime_settings, "language", lambda: "zh")
    assert eng.member_name("coordinator") == "项目经理"


def test_coordinator_reserve_name_follows_language(monkeypatch) -> None:
    eng = _engine()
    monkeypatch.setattr(runtime_settings, "language", lambda: "zh")
    assert eng.reserve_name("coordinator", "项目经理") == "项目经理"
    monkeypatch.setattr(runtime_settings, "language", lambda: "en")
    assert eng.reserve_name("coordinator", "Coordinator") == "Leader"


def test_coordinator_identity_follows_language(monkeypatch) -> None:
    eng = _engine()
    monkeypatch.setattr(runtime_settings, "language", lambda: "zh")
    ident = eng.identity("coordinator")
    assert ident["name"] == "项目经理"
    assert ident["role"] == "项目经理"
    monkeypatch.setattr(runtime_settings, "language", lambda: "en")
    ident = eng.identity("coordinator")
    assert ident["name"] == "Leader"
    assert ident["role"] == "Leader"


class _FakeStore:
    """只实现 _members_of 用到的 load_roster_full。"""

    def __init__(self, rows: dict) -> None:
        self.rows = rows

    def load_roster_full(self, _project_id: str) -> dict:
        return self.rows


def test_members_of_overrides_stale_disk_coordinator_name(monkeypatch) -> None:
    """磁盘花名册存着英文快照（Coordinator）时，中文模式下必须覆盖为「项目经理」。"""
    srv = object.__new__(KnoweServer)
    srv.store = _FakeStore({
        "coordinator": {"role": "Coordinator", "name": "Coordinator"},
        "fe_1": {"role": "前端", "name": "前端 1"},
    })
    srv.engines = {}
    monkeypatch.setattr(runtime_settings, "language", lambda: "zh")

    rows = srv._members_of("p1")

    coord = rows[0]
    assert coord["id"] == "coordinator"
    assert coord["name"] == "项目经理"
    assert coord["role"] == "项目经理"
    # 普通成员不受影响，仍读磁盘名字
    assert rows[1] == {"id": "fe_1", "role": "前端", "name": "前端 1"}
