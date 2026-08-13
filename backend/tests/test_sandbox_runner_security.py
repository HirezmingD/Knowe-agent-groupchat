from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from backend import sandbox_runner
from backend.terminal_tools import ProcessPool, run_command


def _run(
    command: str,
    workspace: Path,
    timeout_s: float = 15,
    *,
    executable: str | Path | None = None,
) -> tuple[int | None, str]:
    async def go() -> tuple[int | None, str]:
        process = await sandbox_runner.spawn(
            command,
            workspace_root=workspace,
            cwd=workspace,
            timeout_s=timeout_s,
            executable=executable,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_s + 10)
        return process.returncode, stdout.decode("utf-8", "replace")

    return asyncio.run(go())


def test_missing_mxc_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sandbox_runner,
        "probe",
        lambda **_kwargs: sandbox_runner.SandboxSupport(None, False, "missing"),
    )
    with pytest.raises(sandbox_runner.SandboxUnavailable, match="missing"):
        _run("echo must-not-run", tmp_path)


def test_child_environment_is_an_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github-sentinel")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-sentinel")
    monkeypatch.setenv("NPM_TOKEN", "npm-sentinel")
    env = sandbox_runner.minimal_environment(tmp_path)
    assert "GITHUB_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "NPM_TOKEN" not in env
    assert Path(env["HOME"]).is_relative_to(tmp_path)
    assert Path(env["TEMP"]).is_relative_to(tmp_path)


def test_batch_preamble_clears_inherited_environment_and_rebuilds_allowlist(
    tmp_path: Path,
) -> None:
    env = sandbox_runner.minimal_environment(tmp_path, {"KNOWE_TASK_ID": "task%42!"})
    script = sandbox_runner._batch_script("set", env)  # noqa: SLF001
    assert "for /f \"delims==\" %%K in ('set')" in script
    assert 'set "KNOWE_TASK_ID=task%%42!"' in script
    assert "GITHUB_TOKEN" not in script


def test_extra_secret_environment_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        sandbox_runner.minimal_environment(tmp_path, {"GITHUB_TOKEN": "sentinel"})


def test_policy_has_one_rw_root_and_default_deny_network(tmp_path: Path) -> None:
    command_file = tmp_path / ".knowe-sandbox" / "commands" / "test.cmd"
    config = sandbox_runner.build_config(
        "echo ok",
        workspace_root=tmp_path,
        command_file=command_file,
    )
    filesystem = config["filesystem"]
    network = config["network"]
    process = config["process"]
    assert filesystem["readwritePaths"] == [str(tmp_path.resolve())]
    assert "env" not in process
    assert network == {
        "enforcementMode": "capabilities",
        "defaultPolicy": "block",
        "allowLocalNetwork": False,
        "allowedHosts": [],
        "blockedHosts": [],
    }
    assert config["ui"] == {
        "disable": False,
        "clipboard": "none",
        "injection": False,
    }
    assert config["processContainer"]["ui"] == {
        "isolation": "container",
        "desktopSystemControl": False,
        "systemSettings": "none",
        "ime": False,
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows workspace gate")
def test_workspace_gate_rejects_reparse_points(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "junction"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        if created.returncode != 0:
            pytest.skip(f"reparse-point creation unavailable: {created.stderr!r}")
    with pytest.raises(sandbox_runner.SandboxUnavailable, match="reparse point"):
        sandbox_runner.validate_workspace_security(link)


@pytest.mark.skipif(os.name != "nt", reason="Windows workspace gate")
def test_workspace_gate_rejects_nested_hardlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("sentinel", "utf-8")
    os.link(outside, workspace / "alias.txt")
    with pytest.raises(sandbox_runner.SandboxUnavailable, match="hardlinked"):
        sandbox_runner.validate_workspace_security(workspace)
@pytest.mark.skipif(os.name != "nt", reason="MXC integration requires Windows")
def test_real_mxc_blocks_outside_file_network_and_host_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    support = sandbox_runner.probe()
    if not support.available:
        pytest.skip(support.reason)

    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside-secret.txt"
    workspace.mkdir()
    outside.write_text("outside-sentinel", "utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "host-token-sentinel")

    accepted = threading.Event()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(8)

    def accept_once() -> None:
        try:
            connection, _address = listener.accept()
        except OSError:
            return
        accepted.set()
        connection.close()

    accept_thread = threading.Thread(target=accept_once, daemon=True)
    accept_thread.start()
    local_port = listener.getsockname()[1]

    command = (
        "echo sandbox-ok>inside.txt & "
        f'type "{outside}" 2>outside-read-error.txt & '
        f'echo changed>"{outside}" 2>outside-write-error.txt & '
        "set GITHUB_TOKEN & "
        f"curl.exe --connect-timeout 3 --max-time 5 http://127.0.0.1:{local_port}/ 2>network-error.txt"
    )
    try:
        code, output = _run(command, workspace, timeout_s=15)
    finally:
        listener.close()
        accept_thread.join(timeout=1)

    assert code not in (None, -1), output
    assert (workspace / "inside.txt").read_text("utf-8").strip() == "sandbox-ok"
    assert outside.read_text("utf-8") == "outside-sentinel"
    assert "outside-sentinel" not in output
    assert "host-token-sentinel" not in output
    assert "GITHUB_TOKEN" in output and "not defined" in output
    assert (workspace / "outside-read-error.txt").stat().st_size > 0
    write_error = workspace / "outside-write-error.txt"
    assert (write_error.exists() and write_error.stat().st_size > 0) or "Access is denied" in output
    assert (workspace / "network-error.txt").stat().st_size > 0
    assert not accepted.is_set(), "sandbox connected to a host loopback listener"


@pytest.mark.skipif(os.name != "nt", reason="MXC integration requires Windows")
def test_terminate_reaps_sandbox_process(tmp_path: Path) -> None:
    support = sandbox_runner.probe()
    if not support.available:
        pytest.skip(support.reason)

    async def go() -> int:
        process = await sandbox_runner.spawn(
            'cmd.exe /d /s /c "echo started & ping.exe -n 30 127.0.0.1 >nul"',
            workspace_root=tmp_path,
            cwd=tmp_path,
        )
        await asyncio.sleep(1)
        sandbox_runner.terminate(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            sandbox_runner.terminate(process, force=True)
            await asyncio.wait_for(process.wait(), timeout=5)
        return int(process.returncode or 0)

    assert asyncio.run(go()) != 0


@pytest.mark.skipif(os.name != "nt", reason="MXC integration requires Windows")
def test_real_token_is_appcontainer_at_low_integrity(tmp_path: Path) -> None:
    support = sandbox_runner.probe()
    if not support.available:
        pytest.skip(support.reason)
    assert support.launcher is not None

    code, output = _run(
        f'"{support.launcher}" --test-token-info',
        tmp_path,
        timeout_s=15,
        executable=support.launcher,
    )
    assert code == 0, output
    payload = json.loads(output.strip())
    assert payload["is_app_container"] is True
    assert payload["app_container_sid"].startswith("S-1-15-2-")
    assert payload["integrity_sid"] == "S-1-16-4096"
    assert payload["integrity_rid"] == 4096


@pytest.mark.skipif(os.name != "nt", reason="MXC integration requires Windows")
def test_outer_job_kills_delayed_descendant_if_launcher_is_hard_killed(tmp_path: Path) -> None:
    """A backend/launcher crash must not leave an MXC descendant alive."""

    support = sandbox_runner.probe()
    if not support.available:
        pytest.skip(support.reason)

    marker = tmp_path / "delayed-sentinel.txt"
    assert support.launcher is not None

    async def go() -> int:
        process = await sandbox_runner.spawn(
            f'"{support.launcher}" --test-spawn-delayed-child 6000 "{marker}"',
            workspace_root=tmp_path,
            cwd=tmp_path,
            executable=support.launcher,
        )
        assert process.stdout is not None
        line = await asyncio.wait_for(process.stdout.readline(), timeout=35)
        assert b"descendant-started" in line
        # Simulate an ungraceful launcher/backend failure: no MXC cleanup
        # signal is sent. KILL_ON_JOB_CLOSE is the only reaping mechanism.
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=10)
        return int(process.returncode or 0)

    assert asyncio.run(go()) != 0
    sandbox_runner.recover_after_termination()
    time.sleep(8)
    assert not marker.exists(), "sandbox descendant escaped the outer Windows Job"


@pytest.mark.skipif(os.name != "nt", reason="MXC integration requires Windows")
def test_background_pool_close_reaps_delayed_descendant(tmp_path: Path) -> None:
    """The model-facing background-process path must inherit the same Job."""

    support = sandbox_runner.probe()
    if not support.available:
        pytest.skip(support.reason)
    assert support.launcher is not None
    helper = tmp_path / "sandbox-descendant-test.exe"
    shutil.copy2(support.launcher, helper)
    marker = tmp_path / "background-delayed-sentinel.txt"

    async def go() -> None:
        pool = ProcessPool("sandbox-security", max_procs=1, log_bytes=16_384)
        try:
            process = await pool.start(
                f'"{helper}" --test-spawn-delayed-child 6000 "{marker}"',
                agent_id="security-test",
                cwd=tmp_path,
            )
            for _ in range(70):
                if "descendant-started" in process.log.read():
                    break
                await asyncio.sleep(0.5)
            assert "descendant-started" in process.log.read()
            # Another command finishing must not remove HOME/TEMP while this
            # background workload still owns the workspace runtime.
            assert (tmp_path / ".knowe" / "sandbox-home").is_dir()
            assert (tmp_path / ".knowe" / "sandbox-temp").is_dir()
        finally:
            await pool.aclose(immediate=True)

    asyncio.run(go())
    time.sleep(8)
    assert not marker.exists(), "background process descendant escaped pool shutdown"
    assert not (tmp_path / ".knowe").exists()
    assert not (tmp_path / ".knowe-sandbox").exists()


@pytest.mark.skipif(os.name != "nt", reason="MXC integration requires Windows")
def test_real_safe_bash_timeout_maps_ffffffff_and_reaps_workload(tmp_path: Path) -> None:
    support = sandbox_runner.probe()
    if not support.available:
        pytest.skip(support.reason)
    assert support.launcher is not None
    helper = tmp_path / "sandbox-timeout-test.exe"
    shutil.copy2(support.launcher, helper)
    marker = tmp_path / "timeout-delayed-sentinel.txt"

    result = asyncio.run(
        run_command(
            f'"{helper}" --test-delayed-write 6000 "{marker}"',
            cwd=tmp_path,
            timeout_s=1,
            max_output=16_384,
        )
    )
    assert result.timed_out is True
    assert result.exit_code in (-1, 124, 0xFFFFFFFF)
    time.sleep(8)
    assert not marker.exists(), "timed-out safe_bash workload escaped its Job"
