# knowe v0.20 — Batch 4 测试：终端 / 后台进程 / 代码执行
"""
异步的地方一律用 asyncio.run 包一层，**不引入 pytest-asyncio**——
这一批的硬约束是「不引入新的必选依赖」，测试依赖也算依赖。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agent_runtime import ToolError                     # noqa: E402
from backend.terminal_tools import (                            # noqa: E402
    ProcessPool, _DrainCapture, _RingLog, _mxc_reported_timeout,
    _windows_cmd_compatibility_note,
    guard_command, python_for, run_command, run_python,
)

_WIN = sys.platform.startswith("win")


# ═══════════════════════════════ terminal ═══════════════════════════

def test_runs_a_command_and_returns_output(tmp_path: Path) -> None:
    r = asyncio.run(run_command("echo hello-knowe", cwd=tmp_path,
                                timeout_s=30, max_output=10_000))
    assert "hello-knowe" in r.output
    assert r.exit_code == 0
    assert r.timed_out is False


def test_exit_code_comes_back(tmp_path: Path) -> None:
    r = asyncio.run(run_command("exit 3", cwd=tmp_path, timeout_s=30, max_output=10_000))
    assert r.exit_code == 3


def test_stderr_is_merged_into_output(tmp_path: Path) -> None:
    """合流 = 跟用户在自己终端里看到的一样；分开会让模型漏看报错。"""
    r = asyncio.run(run_command("echo oops 1>&2", cwd=tmp_path,
                                timeout_s=30, max_output=10_000))
    assert "oops" in r.output


def test_cwd_is_the_project_dir(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("x", "utf-8")
    # Full Windows UI lockdown disables Win32k calls; cmd's ``dir`` formatter
    # uses that surface and legitimately fails. ``type`` proves the same cwd
    # and project read boundary without relaxing ui.disable.
    cmd = "type marker.txt" if _WIN else "ls"
    r = asyncio.run(run_command(cmd, cwd=tmp_path, timeout_s=30, max_output=10_000))
    assert ("x" if _WIN else "marker.txt") in r.output


@pytest.mark.skipif(_WIN, reason="sleep 语义不同")
def test_timeout_kills_and_reports(tmp_path: Path) -> None:
    r = asyncio.run(run_command("sleep 30", cwd=tmp_path, timeout_s=1, max_output=10_000))
    assert r.timed_out is True
    assert r.duration_s < 15


@pytest.mark.skipif(_WIN, reason="进程组是 POSIX 语义")
def test_timeout_takes_the_children_too(tmp_path: Path) -> None:
    """
    ★ 只 kill 那个 shell，孩子会活下来变成孤儿，继续占 CPU 和端口。
      这里起一个孙子进程，超时之后它必须也没了。
    """
    marker = tmp_path / "alive.txt"
    cmd = f"(sleep 8; echo yes > {marker}) & sleep 8"
    r = asyncio.run(run_command(cmd, cwd=tmp_path, timeout_s=1, max_output=10_000))
    assert r.timed_out is True
    asyncio.run(asyncio.sleep(0))
    import time
    time.sleep(9)
    assert not marker.exists(), "子进程在超时后活了下来 —— 进程组没被杀干净"


def test_large_output_does_not_hang_and_is_fully_logged(tmp_path: Path) -> None:
    """The receipt stays bounded while the complete merged byte stream is durable."""
    if _WIN:
        pytest.skip("用 POSIX shell 造大输出")
    import hashlib

    cmd = "for i in $(seq 1 20000); do echo 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; done"
    log_path = tmp_path / "attempt.log"
    r = asyncio.run(
        run_command(
            cmd,
            cwd=tmp_path,
            timeout_s=60,
            max_output=5_000,
            log_path=log_path,
        )
    )
    logged = log_path.read_bytes()
    assert r.exit_code == 0
    assert r.timed_out is False
    assert r.truncated is True
    assert r.output == ""
    assert 0 < len(r.output_head) <= 2_500
    assert 0 < len(r.output_tail) <= 2_500
    assert r.bytes_total == len(logged)
    assert r.output_sha256 == hashlib.sha256(logged).hexdigest()


def test_utf8_output(tmp_path: Path) -> None:
    r = asyncio.run(run_command("echo 你好世界", cwd=tmp_path, timeout_s=30, max_output=10_000))
    assert "你好世界" in r.output


def test_provider_key_is_not_handed_to_children(tmp_path: Path, monkeypatch) -> None:
    """
    Worker 调 `env` 然后把结果贴进报告，是很现实的一幕。
    平台自己的 key 对它干活没有任何用处，那就别给。
    """
    if _WIN:
        pytest.skip("用 POSIX shell 读环境变量")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-user-own-token")
    r = asyncio.run(run_command("echo [$DEEPSEEK_API_KEY] [$GITHUB_TOKEN]", cwd=tmp_path,
                                timeout_s=30, max_output=10_000))
    assert "sk-should-not-leak" not in r.output
    assert "ghp-user-own-token" in r.output      # 用户自己的 token 是他干活要用的，保留


# ═══════════════════════════════ 事故拦截 ═══════════════════════════

@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -rf ~",
    "rm -rf $HOME",
    "rm -rf ~/",
    "sudo rm -rf /*",
    "rm -fr /",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    ":(){ :|:& };:",
    "shutdown -h now",
    "rm -rf /etc/passwd",
])
def test_catastrophic_commands_are_stopped(cmd: str) -> None:
    with pytest.raises(ToolError) as e:
        guard_command(cmd)
    assert "拦下" in str(e.value)


@pytest.mark.parametrize("cmd", [
    "rm -rf build",
    "rm -rf node_modules && npm install",
    "pip install -r requirements.txt",
    "npm test",
    "git status",
    "python -m pytest tests/ -v",
    "rm -f dist/app.js",
    "docker compose up -d",
    "curl https://api.example.com/health",
    "grep -rn 'rm -rf /' docs/",     # 提到它 ≠ 执行它…
])
def test_normal_dev_commands_pass(cmd: str) -> None:
    """★ 零误伤是这张表能存在的前提。挡住一条正常命令，它就该被删掉。"""
    guard_command(cmd)


# ═══════════════════════════════ execute_code ═══════════════════════

def test_execute_code_runs_and_prints(tmp_path: Path) -> None:
    r, why = asyncio.run(run_python("print(2 ** 10)", cwd=tmp_path,
                                    timeout_s=30, max_output=10_000))
    assert "1024" in r.output
    assert r.exit_code == 0
    assert why


def test_execute_code_leaves_no_junk_in_the_project(tmp_path: Path) -> None:
    """用户的仓库是用户的——不能给他留一地 tmp_script_3.py。"""
    asyncio.run(run_python("open('made.txt','w').write('ok')", cwd=tmp_path,
                           timeout_s=30, max_output=10_000))
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["made.txt"]          # 只有脚本自己写的那个，没有脚本本身


def test_execute_code_preserves_preexisting_runtime_parents(tmp_path: Path) -> None:
    """Cleanup owns its directories, never a pre-existing user directory."""

    user_knowe = tmp_path / ".knowe"
    user_sandbox = tmp_path / ".knowe-sandbox"
    user_knowe.mkdir()
    user_sandbox.mkdir()
    (user_knowe / "user-note.txt").write_text("keep", "utf-8")

    asyncio.run(
        run_python(
            "open('made.txt','w').write('ok')",
            cwd=tmp_path,
            timeout_s=30,
            max_output=10_000,
        )
    )

    assert (user_knowe / "user-note.txt").read_text("utf-8") == "keep"
    assert user_sandbox.is_dir()
    assert not (user_knowe / "sandbox-home").exists()
    assert not (user_knowe / "sandbox-temp").exists()
    assert not (user_sandbox / "commands").exists()
    assert not (user_sandbox / "execute").exists()


def test_execute_code_can_import_project_modules(tmp_path: Path) -> None:
    (tmp_path / "mymod.py").write_text("VALUE = 'from-project'\n", "utf-8")
    r, _ = asyncio.run(run_python("import mymod; print(mymod.VALUE)", cwd=tmp_path,
                                  timeout_s=30, max_output=10_000))
    assert "from-project" in r.output


def test_execute_code_reports_traceback_without_temp_path(tmp_path: Path) -> None:
    r, _ = asyncio.run(run_python("raise ValueError('boom')", cwd=tmp_path,
                                  timeout_s=30, max_output=10_000))
    assert r.exit_code != 0
    assert "boom" in r.output
    assert "knowe-exec" not in r.output      # 临时目录路径对模型是噪音


def test_execute_code_timeout(tmp_path: Path) -> None:
    r, _ = asyncio.run(run_python("import time; time.sleep(30)", cwd=tmp_path,
                                  timeout_s=1, max_output=10_000))
    assert r.timed_out is True


def test_mxc_empty_ffffffff_is_normalized_to_timeout() -> None:
    empty = _DrainCapture(prefix=b"", tail=b"", bytes_total=0, sha256="")
    assert _mxc_reported_timeout(empty, returncode=0xFFFFFFFF, timeout_s=1)
    assert _mxc_reported_timeout(empty, returncode=-1, timeout_s=1)
    assert _mxc_reported_timeout(empty, returncode=124, timeout_s=1)
    assert not _mxc_reported_timeout(empty, returncode=0xFFFFFFFF, timeout_s=0)


def test_windows_dir_denial_has_actionable_compatibility_note() -> None:
    noted = _windows_cmd_compatibility_note("dir /b", "Access is denied.\r\n", 1)
    if _WIN:
        assert "Python os.listdir" in noted
    else:
        assert noted == "Access is denied.\r\n"


def test_prefers_project_venv(tmp_path: Path) -> None:
    """
    ★ Worker `pip install pandas` 装进项目 venv，转头 execute_code 里
      import pandas 报 ModuleNotFoundError —— 它会以为自己装错了，然后再装一遍。
    """
    exe, why = python_for(tmp_path)
    expected = (
        sys.executable
        if getattr(sys, "frozen", False)
        else str(getattr(sys, "_base_executable", sys.executable))
    )
    assert exe == expected and "没找到虚拟环境" in why

    if _WIN:
        pytest.skip("POSIX 布局")
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    fake = venv / "python3"
    fake.write_text("#!/bin/sh\nexec true\n", "utf-8")
    fake.chmod(0o755)
    exe2, why2 = python_for(tmp_path)
    assert exe2 == str(fake) and "虚拟环境" in why2


def test_packaged_backend_execute_mode_routes_project_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Smoke the narrow entry PyInstaller exposes to execute_code."""

    from backend import run_backend

    script = tmp_path / "packaged-entry-smoke.py"
    script.write_text("print('packaged-entry-ok')\n", "utf-8")
    monkeypatch.setenv("KNOWE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "argv",
        ["KnoweBackend.exe", "--knowe-sandbox-execute", str(script)],
    )
    assert run_backend._sandbox_execute_mode() is True
    assert "packaged-entry-ok" in capsys.readouterr().out


# ═══════════════════════════════ 后台进程 ═══════════════════════════

def test_process_lifecycle(tmp_path: Path) -> None:
    async def go() -> None:
        pool = ProcessPool("p1", max_procs=4, log_bytes=100_000)
        try:
            cmd = (
                "for /L %%i in (1,1,9) do @(echo tick-%%i&choice /T 1 /D Y >nul)"
                if _WIN
                else "for i in 1 2 3 4 5 6 7 8 9; do echo tick-$i; sleep 0.4; done"
            )
            bp = await pool.start(cmd, agent_id="be_1", cwd=tmp_path)
            assert bp.session_id == "proc_1"

            # Tier-3 AppContainer setup must establish and journal DACL grants before
            # the contained command starts.  On a cold Windows runner that can take
            # several seconds, so assert streamed output with a bounded poll instead
            # of assuming host-shell startup latency.
            for _ in range(80):
                if "tick-1" in bp.log.read():
                    break
                await asyncio.sleep(0.25)
            assert bp.running is True
            assert "tick-1" in bp.log.read()

            rows = pool.list()
            assert len(rows) == 1 and rows[0]["started_by"] == "be_1"
            assert rows[0]["state"] == "running"

            assert await pool.wait(bp, 0.3) is False        # 还在跑
            assert await pool.kill(bp) is True
            assert bp.running is False
            assert pool.list()[0]["state"] == "exited"
        finally:
            await pool.aclose(immediate=True)

    asyncio.run(go())


def test_process_wait_returns_when_it_exits(tmp_path: Path) -> None:
    async def go() -> None:
        pool = ProcessPool("p2", max_procs=4, log_bytes=100_000)
        try:
            bp = await pool.start("echo done-now", agent_id="be_1", cwd=tmp_path)
            # Tier-3 cold starts apply and later restore DACL grants before the
            # supervisor exits; allow that security lifecycle to complete.
            assert await pool.wait(bp, 45) is True
            assert bp.exit_code == 0
            assert "done-now" in bp.log.read()
        finally:
            await pool.aclose(immediate=True)

    asyncio.run(go())


def test_process_submit_feeds_stdin(tmp_path: Path) -> None:
    async def go() -> None:
        pool = ProcessPool("p3", max_procs=4, log_bytes=100_000)
        try:
            bp = await pool.start(
                ("findstr /R .*" if _WIN else
                 f"{sys.executable} -c \"import sys;[print('got:'+l.strip(),flush=True) for l in sys.stdin]\""),
                agent_id="qa_1", cwd=tmp_path)
            if _WIN:
                with pytest.raises(ToolError, match="stdin.*fail-closed"):
                    await pool.submit(bp, "hello")
            else:
                await pool.submit(bp, "hello")
                for _ in range(40):
                    if "got:hello" in bp.log.read():
                        break
                    await asyncio.sleep(0.1)
                assert "got:hello" in bp.log.read()
        finally:
            await pool.aclose(immediate=True)

    asyncio.run(go())


def test_process_unknown_session_says_what_exists(tmp_path: Path) -> None:
    async def go() -> None:
        pool = ProcessPool("p4", max_procs=4, log_bytes=100_000)
        try:
            with pytest.raises(ToolError) as e:
                pool.get("proc_99")
            assert "没有这个后台进程" in str(e.value)
        finally:
            await pool.aclose(immediate=True)

    asyncio.run(go())


def test_process_cap(tmp_path: Path) -> None:
    async def go() -> None:
        pool = ProcessPool("p5", max_procs=2, log_bytes=10_000)
        long_command = "choice /T 30 /D Y >nul" if _WIN else "sleep 20"
        try:
            await pool.start(long_command, agent_id="a", cwd=tmp_path)
            await pool.start(long_command, agent_id="a", cwd=tmp_path)
            with pytest.raises(ToolError) as e:
                await pool.start(long_command, agent_id="a", cwd=tmp_path)
            assert "上限" in str(e.value)
        finally:
            await pool.aclose(immediate=True)

    asyncio.run(go())


def test_pool_aclose_kills_everything(tmp_path: Path) -> None:
    """engine.stop() 依赖这条：切项目目录不能留下一个占着端口的 dev server。"""
    async def go() -> None:
        pool = ProcessPool("p6", max_procs=4, log_bytes=10_000)
        bp = await pool.start("sleep 60", agent_id="a", cwd=tmp_path)
        proc = bp.proc
        await pool.aclose(immediate=True)
        await asyncio.sleep(0.4)
        assert proc.returncode is not None, "aclose 之后进程还活着"
        assert pool.list() == []

    asyncio.run(go())


def test_background_guard_applies_too(tmp_path: Path) -> None:
    async def go() -> None:
        pool = ProcessPool("p7", max_procs=4, log_bytes=10_000)
        try:
            with pytest.raises(ToolError):
                await pool.start("rm -rf /", agent_id="a", cwd=tmp_path)
        finally:
            await pool.aclose(immediate=True)

    asyncio.run(go())


# ═══════════════════════════════ 日志环 ═════════════════════════════

def test_ring_log_keeps_head_and_tail() -> None:
    """
    ★ 纯尾巴环会把 `Listening on http://localhost:3000` 挤掉——
      而那正是 Worker 起完服务之后唯一想知道的一行。
    """
    ring = _RingLog(1000)
    ring.write(b"STARTUP: listening on port 3000\n")
    for i in range(5000):
        ring.write(f"request {i} handled\n".encode())
    text = ring.read()
    assert "listening on port 3000" in text        # 头还在
    assert "request 4999" in text                  # 尾也在
    assert "中间省略" in text
    assert len(text) < 2000


def test_ring_log_small_input_is_verbatim() -> None:
    ring = _RingLog(1000)
    ring.write(b"hello\n")
    assert ring.read() == "hello\n"
    assert ring.total == 6
