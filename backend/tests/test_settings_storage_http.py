from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from pathlib import Path

import pytest

from backend import runtime_settings
from backend.config import CONFIG
from backend.server import KnoweServer


class _TestProtector:
    scheme = "test-protector-v1"

    def protect(self, plaintext: bytes, *, purpose: bytes):
        return {
            "version": 1,
            "scheme": self.scheme,
            "ciphertext": base64.b64encode(purpose + b"\0" + plaintext).decode("ascii"),
        }

    def unprotect(self, envelope, *, purpose: bytes) -> bytes:
        raw = base64.b64decode(envelope["ciphertext"])
        prefix = purpose + b"\0"
        if not raw.startswith(prefix):
            raise ValueError("wrong purpose")
        return raw[len(prefix):]


async def _raw_http(port: int, request: bytes) -> tuple[int, dict[str, object]]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout=3.0)
    writer.close()
    await writer.wait_closed()
    head, body = raw.split(b"\r\n\r\n", 1)
    status = int(head.split(b"\r\n", 1)[0].split()[1])
    return status, json.loads(body.decode("utf-8"))


def _request(path: str, *, method: str = "GET", body: bytes = b"") -> bytes:
    headers = [
        "Host: 127.0.0.1",
        f"X-Knowe-Runtime-Token: {CONFIG.runtime_token}",
    ]
    if method == "POST":
        headers.extend([
            "Content-Type: application/json",
            f"Content-Length: {len(body)}",
        ])
    return (
        f"{method} {path} HTTP/1.1\r\n"
        + "\r\n".join(headers)
        + "\r\n\r\n"
    ).encode("latin-1") + body


@pytest.mark.asyncio
async def test_settings_endpoints_return_redacted_503_for_unrecoverable_store() -> None:
    with tempfile.TemporaryDirectory() as directory:
        data_root = Path(directory) / "backend-data"
        data_root.mkdir()
        (data_root / "settings.json").write_bytes(b"corrupt primary SECRET-MATERIAL")
        (data_root / "settings.json.bak").write_bytes(b"corrupt backup SECRET-MATERIAL")
        object.__setattr__(CONFIG, "data_dir", str(data_root))
        runtime_settings._state.clear()  # noqa: SLF001
        runtime_settings._state.update(runtime_settings._default_state())  # noqa: SLF001
        runtime_settings._loaded = False  # noqa: SLF001
        runtime_settings._load_error = None  # noqa: SLF001
        runtime_settings._secret_protector_override = _TestProtector()  # noqa: SLF001

        server = KnoweServer(data_dir=str(data_root))
        listener = await asyncio.start_server(server._health_conn, "127.0.0.1", 0)
        port = int(listener.sockets[0].getsockname()[1])
        try:
            requests = [
                _request("/settings"),
                _request("/settings", method="POST", body=b"{}"),
                _request(
                    "/settings/test",
                    method="POST",
                    body=json.dumps({
                        "target": "main",
                        "binding": {
                            "provider": "test",
                            "model": "test-model",
                            "base_url": "https://example.invalid/v1",
                            "transport": "openai_chat",
                        },
                    }).encode("utf-8"),
                ),
            ]
            for request in requests:
                status, response = await _raw_http(port, request)
                rendered = json.dumps(response, ensure_ascii=False)
                assert status == 503
                assert response["error"] == "credential_store_unavailable"
                assert "SECRET-MATERIAL" not in rendered
                assert "ciphertext" not in rendered
        finally:
            listener.close()
            await listener.wait_closed()

        assert (data_root / "settings.json").read_bytes() == b"corrupt primary SECRET-MATERIAL"
        assert (data_root / "settings.json.bak").read_bytes() == b"corrupt backup SECRET-MATERIAL"
