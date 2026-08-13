"""
test_e2e.py — 端到端：起一个真的 KnoweServer，用真的 WebSocket 客户端连上去走全链路。

这些测试证明的不是「函数返回值对」，而是「一个真客户端连上来能拿到该拿的东西」。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import websockets
from websockets.asyncio.server import serve

# Fake 档跑快一点，别让测试等剧本演完
os.environ.setdefault("KNOWE_FAKE_DELAY", "0.001")
os.environ.setdefault("KNOWE_FAKE_THINK", "0.001")
os.environ.setdefault("KNOWE_FAKE_WORK", "0.001")

from backend.config import CONFIG           # noqa: E402
from backend.server import KnoweServer      # noqa: E402

DEMO_ID = "project_20260723000000"
PROJECT_ONE_ID = "project_20260723000001"
PROJECT_TWO_ID = "project_20260723000002"
PROJECT_A_ID = "project_20260723000003"
PROJECT_B_ID = "project_20260723000004"


# ═══════════════════════════════════════════════════════════════
# 夹具：一个真服务 + 一个真客户端
# ═══════════════════════════════════════════════════════════════

class Conn:
    """测试用客户端：能发指令、能按类型等事件。"""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.events: list[dict] = []

    async def send(self, **msg) -> None:
        await self.ws.send(json.dumps(msg))

    async def recv(self, timeout: float = 3.0) -> dict:
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        ev = json.loads(raw)
        self.events.append(ev)
        return ev

    async def wait_for(self, etype: str, timeout: float = 5.0, **match) -> dict:
        """一直收到出现该类型（且字段匹配）的事件为止。"""
        async def _loop() -> dict:
            while True:
                ev = await self.recv(timeout=timeout)
                if ev.get("type") != etype:
                    continue
                if all(ev.get(k) == v for k, v in match.items()):
                    return ev
        return await asyncio.wait_for(_loop(), timeout=timeout)

    def seen(self, etype: str) -> list[dict]:
        return [e for e in self.events if e.get("type") == etype]

    async def handshake(self, project_id: str = DEMO_ID, since_seq: int = 0) -> dict:
        await self.send(type="replay_request", project_id=project_id, since_seq=since_seq)
        return await self.wait_for("replay_complete")


@pytest.fixture
async def server(tmp_path: Path):
    # Keep the test's install/private-data roots disjoint from the business workspace;
    # production now rejects every parent/child overlap by design.
    install_root = tmp_path / "fake-install"
    install_root.mkdir()
    object.__setattr__(CONFIG, "install_root", str(install_root))
    srv = KnoweServer(data_dir=str(tmp_path / "knowe-data"))
    # Unknown replay ids are intentionally rejected by the current identity contract.
    # Seed one real canonical project so E2E tests exercise transport/replay rather than
    # relying on the retired "handshake materialises a project" side effect.
    project_parent = tmp_path / "business-projects"
    project_parent.mkdir()
    srv.test_project_parent = project_parent  # type: ignore[attr-defined]
    project_dir = project_parent / "demo-project"
    project_dir.mkdir()
    await srv.create_project(DEMO_ID, "端到端测试项目", project_dir=str(project_dir))
    async with serve(srv.handle, "127.0.0.1", 0) as ws_server:
        port = ws_server.sockets[0].getsockname()[1]
        srv.test_port = port  # type: ignore[attr-defined]
        yield srv
        for eng in list(srv.engines.values()):
            await eng.stop()


async def connect(srv) -> Conn:
    ws = await websockets.connect(f"ws://127.0.0.1:{srv.test_port}")
    return Conn(ws)


def new_project_dir(srv, name: str) -> str:
    """Allocate a peer-disjoint business root for create-project E2E frames."""
    path = Path(srv.test_project_parent) / name
    path.mkdir()
    return str(path)


# ═══════════════════════════════════════════════════════════════
# 握手与回放
# ═══════════════════════════════════════════════════════════════

async def test_handshake_returns_replay_complete(server):
    c = await connect(server)
    done = await c.handshake(DEMO_ID)
    assert done["project_id"] == DEMO_ID
    assert done["last_seq"] == 0
    await c.ws.close()


async def test_handshake_timeout_sends_replay_complete_zero(server, monkeypatch):
    """5 秒不发首帧 → replay_complete{last_seq: 0}（无 project_id 的超时分支）。"""
    monkeypatch.setattr(CONFIG.__class__, "handshake_timeout_s", 0.1, raising=False)
    object.__setattr__(CONFIG, "handshake_timeout_s", 0.1)

    c = await connect(server)
    ev = await c.wait_for("replay_complete", timeout=2)
    assert ev["last_seq"] == 0
    assert "project_id" not in ev            # 超时分支没有 project_id
    await c.ws.close()

    object.__setattr__(CONFIG, "handshake_timeout_s", 5.0)


async def test_replay_backfills_project_created_and_history(server):
    # 第一个客户端建项目、发一条消息
    c1 = await connect(server)
    await c1.handshake(DEMO_ID)
    await c1.send(
        type="create_project",
        project_id=PROJECT_ONE_ID,
        project_name="官网改版",
        project_dir=new_project_dir(server, "project-one"),
    )
    await c1.wait_for("project_created", project_id=PROJECT_ONE_ID)
    await c1.send(type="user_message", project_id=PROJECT_ONE_ID, content="你好", client_msg_id="cm_1")
    await c1.wait_for("message", project_id=PROJECT_ONE_ID)

    # 第二个客户端后来才连上：握手时必须补发 project_created + 回放历史
    c2 = await connect(server)
    await c2.send(type="replay_request", project_id=PROJECT_ONE_ID, since_seq=0)
    done = await c2.wait_for("replay_complete", project_id=PROJECT_ONE_ID)

    created = c2.seen("project_created")
    assert any(e["project_id"] == PROJECT_ONE_ID for e in created), "握手必须补发 project_created"
    assert any(e["type"] == "user_echo" for e in c2.events), "历史必须被回放"
    assert done["last_seq"] > 0

    await c1.ws.close()
    await c2.ws.close()


async def test_incremental_replay_since_seq(server):
    c1 = await connect(server)
    await c1.handshake(DEMO_ID)
    await c1.send(type="user_message", project_id=DEMO_ID, content="一", client_msg_id="cm_1")
    await c1.wait_for("message", project_id=DEMO_ID)
    last = server.hub.projects[DEMO_ID].seq

    c2 = await connect(server)
    await c2.send(type="replay_request", project_id=DEMO_ID, since_seq=last)
    done = await c2.wait_for("replay_complete")

    # 从最新水位起回放 → 一条历史都不该有
    assert done["last_seq"] == last
    assert not [e for e in c2.events if e.get("type") == "user_echo"]
    await c1.ws.close()
    await c2.ws.close()


# ═══════════════════════════════════════════════════════════════
# 广播（BUG-1：不能失聪）
# ═══════════════════════════════════════════════════════════════

async def test_BUG1_broadcast_reaches_all_clients_including_sender(server):
    """
    ★ BUG-1 永久回归：广播必须到达**所有**客户端，包括发送者自己（user_echo）。
      旧版用 getattr(c, 'open', False) 判活，在 websockets≥14 上恒为 False → 全员失聪。
    """
    c1 = await connect(server)
    c2 = await connect(server)
    await c1.handshake(DEMO_ID)
    await c2.handshake(DEMO_ID)

    await c1.send(type="user_message", project_id=DEMO_ID, content="喂", client_msg_id="cm_9")

    echo1 = await c1.wait_for("user_echo")     # ★ 发送者自己也要收到
    echo2 = await c2.wait_for("user_echo")     # ★ 别的客户端也要收到
    assert echo1["client_msg_id"] == "cm_9"
    assert echo2["seq"] == echo1["seq"]

    await c1.ws.close()
    await c2.ws.close()


async def test_events_from_other_projects_are_also_broadcast(server):
    """所有事件广播给所有客户端（含别的项目的）——前端按 project_id 自己路由。"""
    c = await connect(server)
    await c.handshake(DEMO_ID)
    await c.send(
        type="create_project",
        project_id=PROJECT_TWO_ID,
        project_name="别的项目",
        project_dir=new_project_dir(server, "project-two"),
    )
    await c.wait_for("project_created", project_id=PROJECT_TWO_ID)
    await c.send(type="user_message", project_id=PROJECT_TWO_ID, content="嗨", client_msg_id="cm_2")

    echo = await c.wait_for("user_echo", project_id=PROJECT_TWO_ID)
    assert echo["project_id"] == PROJECT_TWO_ID
    await c.ws.close()


# ═══════════════════════════════════════════════════════════════
# 消息往返 + 流式
# ═══════════════════════════════════════════════════════════════

async def test_fake_simple_script_message_roundtrip(server, monkeypatch):
    object.__setattr__(CONFIG, "script", "simple")
    try:
        c = await connect(server)
        await c.handshake(DEMO_ID)
        await c.send(type="user_message", project_id=DEMO_ID, content="你好", client_msg_id="cm_1")

        await c.wait_for("agent_thinking")
        msg = await c.wait_for("message")

        assert c.seen("stream_delta"), "必须有流式增量"
        assert msg["content"]
        assert not c.seen("approval_card"), "简化剧本不触发审批"
        # 每条引擎级事件都带 ts + project_name + seq
        for ev in c.seen("stream_delta") + c.seen("message"):
            assert ev["ts"] and ev["project_name"] and isinstance(ev["seq"], int)
        # 流式增量拼起来 == 完整消息
        assert "".join(e["content"] for e in c.seen("stream_delta")) == msg["content"]
        await c.ws.close()
    finally:
        object.__setattr__(CONFIG, "script", "full")


# ═══════════════════════════════════════════════════════════════
# 审批全链路（完整剧本）
# ═══════════════════════════════════════════════════════════════

async def test_full_script_approve_all_the_way(server):
    c = await connect(server)
    await c.handshake(DEMO_ID)
    await c.send(type="user_message", project_id=DEMO_ID, content="做个官网", client_msg_id="cm_1")

    # 1. 组队卡
    card = await c.wait_for("approval_card", tool="propose_agents")
    assert card["card_id"] == card["card"]["approval_id"]
    assert card["card"]["proposed"]

    await c.send(type="approve", project_id=DEMO_ID, approval_id=card["card_id"])
    resolved = await c.wait_for("approval_resolved")
    assert resolved["resolution"] == "approved"

    # 2. 成员入驻
    created = await c.wait_for("agents_created")
    assert created["count"] == len(created["members"]) == 2

    # 3. 派活卡
    card2 = await c.wait_for("approval_card", tool="propose_next")
    assert card2["card"]["target_id"] and card2["card"]["instruction"]

    await c.send(type="approve", project_id=DEMO_ID, approval_id=card2["card_id"])
    await c.wait_for("instruction_injected")

    # 4. 交报告
    report = await c.wait_for("report_submitted")
    assert report["report_hash"]
    await c.ws.close()


async def test_reject_stops_the_chain(server):
    c = await connect(server)
    await c.handshake(DEMO_ID)
    await c.send(type="user_message", project_id=DEMO_ID, content="做个官网", client_msg_id="cm_1")

    card = await c.wait_for("approval_card", tool="propose_agents")
    await c.send(type="reject", project_id=DEMO_ID, approval_id=card["card_id"])

    resolved = await c.wait_for("approval_resolved")
    assert resolved["resolution"] == "rejected"

    msg = await c.wait_for("message")
    assert "拒绝" in msg["content"]
    assert not c.seen("agents_created"), "拒绝之后不许有人入驻"
    await c.ws.close()


async def test_new_user_message_cancels_pending_approval(server):
    """§三：用户发新消息 → 挂起的审批作废（cancelled）。"""
    c = await connect(server)
    await c.handshake(DEMO_ID)
    await c.send(type="user_message", project_id=DEMO_ID, content="第一件事", client_msg_id="cm_1")
    await c.wait_for("approval_card")

    await c.send(type="user_message", project_id=DEMO_ID, content="算了，换个事", client_msg_id="cm_2")
    resolved = await c.wait_for("approval_resolved")
    assert resolved["resolution"] == "cancelled"

    # 新回合照常开始
    await c.wait_for("approval_card", tool="propose_agents")
    await c.ws.close()


async def test_only_one_resolution_per_card(server):
    """后端对一张卡只发一条 approval_resolved（前端的幂等是第二道保险，不是遮羞布）。"""
    c = await connect(server)
    await c.handshake(DEMO_ID)
    await c.send(type="user_message", project_id=DEMO_ID, content="做个官网", client_msg_id="cm_1")
    card = await c.wait_for("approval_card")

    await c.send(type="approve", project_id=DEMO_ID, approval_id=card["card_id"])
    await c.send(type="approve", project_id=DEMO_ID, approval_id=card["card_id"])  # 重复点击
    await c.wait_for("agents_created")
    await asyncio.sleep(0.2)

    same = [e for e in c.seen("approval_resolved") if e["card_id"] == card["card_id"]]
    assert len(same) == 1
    # 重复审批被幂等忽略，但会出一条引擎级 error（出声，不静默）
    assert any(e["type"] == "error" and "已解决" in e["message"] for e in c.events)
    await c.ws.close()


# ═══════════════════════════════════════════════════════════════
# 快照
# ═══════════════════════════════════════════════════════════════

async def test_request_snapshot_returns_state_snapshot(server):
    c = await connect(server)
    await c.handshake(DEMO_ID)
    await c.send(type="user_message", project_id=DEMO_ID, content="你好", client_msg_id="cm_1")
    await c.wait_for("approval_card")

    await c.send(type="request_snapshot", project_id=DEMO_ID)
    snap = await c.wait_for("state_snapshot")

    assert snap["project_id"] == DEMO_ID
    assert snap["seq"] == snap["last_seq"] + 1        # 快照本身消耗一个 seq
    assert isinstance(snap["conversation"], list) and snap["conversation"]
    assert snap["pending_card"] is not None           # 有挂起的卡
    types = {e["type"] for e in snap["conversation"]}
    assert "stream_delta" not in types                # 瞬时事件不进时间线
    await c.ws.close()


# ═══════════════════════════════════════════════════════════════
# 多项目隔离
# ═══════════════════════════════════════════════════════════════

async def test_two_projects_have_independent_seq(server):
    c = await connect(server)
    await c.handshake(DEMO_ID)
    await c.send(
        type="create_project",
        project_id=PROJECT_A_ID,
        project_name="甲",
        project_dir=new_project_dir(server, "project-a"),
    )
    await c.send(
        type="create_project",
        project_id=PROJECT_B_ID,
        project_name="乙",
        project_dir=new_project_dir(server, "project-b"),
    )
    await c.wait_for("project_created", project_id=PROJECT_B_ID)

    await c.send(type="user_message", project_id=PROJECT_A_ID, content="甲的话", client_msg_id="a1")
    await c.send(type="user_message", project_id=PROJECT_B_ID, content="乙的话", client_msg_id="b1")

    ea = await c.wait_for("user_echo", project_id=PROJECT_A_ID)
    eb = await c.wait_for("user_echo", project_id=PROJECT_B_ID)
    # [v0.5] Fake 档默认不 kickoff（见 config.py），所以 user_echo 仍是各自的 seq=1。
    #        真 LLM 档下总管会先说话，这里的断言写成「两边一致」，两种档都成立。
    assert ea["seq"] == eb["seq"] == 1
    await c.ws.close()


# ═══════════════════════════════════════════════════════════════
# 心跳 / 未知指令 / 畸形帧
# ═══════════════════════════════════════════════════════════════

async def test_ping_pong(server):
    c = await connect(server)
    await c.handshake(DEMO_ID)
    await c.send(type="ping")
    pong = await c.wait_for("pong")
    assert "seq" not in pong               # 无 seq 白名单
    await c.ws.close()


async def test_unknown_command_yields_server_level_error(server):
    """B-3：归因不了的错误 → 服务器级 error（无 seq，进前端全局通知）。"""
    c = await connect(server)
    await c.handshake(DEMO_ID)
    await c.send(type="没这个指令")
    err = await c.wait_for("error")
    assert "seq" not in err and "project_id" not in err
    await c.ws.close()


async def test_malformed_frame_does_not_kill_the_connection(server):
    c = await connect(server)
    await c.handshake(DEMO_ID)
    await c.ws.send("{ 这不是 json")
    err = await c.wait_for("error")
    assert "JSON" in err["message"]

    # 连接还活着，还能干活
    await c.send(type="ping")
    await c.wait_for("pong")
    await c.ws.close()


# ═══════════════════════════════════════════════════════════════
# /health
# ═══════════════════════════════════════════════════════════════

async def test_health_endpoint():
    srv = KnoweServer()
    health = await asyncio.start_server(srv._health_conn, "127.0.0.1", 0)
    port = health.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(("GET /health HTTP/1.1\r\nHost: x\r\n" f"X-Knowe-Runtime-Token: {CONFIG.runtime_token}\r\n\r\n").encode())
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(4096), timeout=3)
    writer.close()

    body = raw.split(b"\r\n\r\n", 1)[1]
    data = json.loads(body)
    assert data == {"status": "ok", "project_count": 0, "ws_clients": 0}

    health.close()
    await health.wait_closed()


async def _raw_http(port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout=3)
    writer.close()
    await writer.wait_closed()
    return raw


def _http_parts(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    head, body = raw.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {
        name.strip().lower(): value.strip()
        for line in lines[1:]
        if ":" in line
        for name, value in [line.split(":", 1)]
    }
    return status, headers, body


async def test_legacy_preview_tree_is_retired_and_preview_remains_authenticated(tmp_path: Path):
    """Legacy Host/_kpt capabilities never bypass auth or revive the tree endpoint."""
    srv = KnoweServer(data_dir="")
    project_a = PROJECT_A_ID
    root_a = tmp_path / "a"
    root_a.mkdir()
    preview_body = b"<h1>authenticated preview</h1>"
    (root_a / "index.html").write_bytes(preview_body)
    srv.hub.get_or_create(project_a, "A")
    srv.engines[project_a] = SimpleNamespace(workspace_root=root_a)

    health = await asyncio.start_server(srv._health_conn, "127.0.0.1", 0)
    port = health.sockets[0].getsockname()[1]
    legacy_capability = "a" * 32
    host_a = f"p-{legacy_capability}.preview.localhost:8081"
    tree_path = f"/preview/tree/{project_a}/index.html"

    try:
        # The retired preview Host label is ordinary untrusted request metadata.  It
        # cannot bypass the one runtime authentication boundary shared by every route.
        raw = await _raw_http(
            port,
            f"GET {tree_path} HTTP/1.1\r\nHost: {host_a}\r\n\r\n".encode(),
        )
        status, headers, body = _http_parts(raw)
        assert status == 401 and b"runtime_auth_required" in body
        assert "access-control-allow-origin" not in headers

        # The retired query capability is ignored as well; only the runtime token is
        # authentication material for the local HTTP surface.
        raw = await _raw_http(
            port,
            (
                f"GET {tree_path}?_kpt={legacy_capability} HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n\r\n"
            ).encode(),
        )
        status, _, body = _http_parts(raw)
        assert status == 401 and b"runtime_auth_required" in body

        # Authentication does not restore the removed service surface.
        raw = await _raw_http(
            port,
            (
                f"GET {tree_path} HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                f"X-Knowe-Runtime-Token: {CONFIG.runtime_token}\r\n\r\n"
            ).encode(),
        )
        status, _, body = _http_parts(raw)
        assert status == 404 and b"not found" in body

        # The supported preview endpoint remains available after authentication.
        raw = await _raw_http(
            port,
            (
                f"GET /preview?project_id={project_a}&path=index.html HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                f"X-Knowe-Runtime-Token: {CONFIG.runtime_token}\r\n\r\n"
            ).encode(),
        )
        status, headers, body = _http_parts(raw)
        assert status == 200 and body == preview_body
        assert headers["content-type"].startswith("text/html")
    finally:
        health.close()
        await health.wait_closed()
