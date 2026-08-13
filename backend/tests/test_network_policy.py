from __future__ import annotations

import asyncio
import socket
from urllib.parse import urlsplit

import pytest

from backend import browser_tools, web_tools
from backend import network_policy
from backend.agent_runtime import ToolError
from backend.network_policy import (
    NetworkPolicyError,
    PublicEgressProxy,
    assert_public_http_url,
)


@pytest.mark.parametrize(
    "url",
    (
        "http://localhost:8080/health",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fec0::1]/",
        "http://[64:ff9b::7f00:1]/",
        "http://[64:ff9b:1::7f00:1]/",
        "http://[2002:7f00:1::]/",
        "http://[2001:0000:4136:e378:8000:63bf:3fff:fdd2]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.2/",
        "http://100.64.0.1/",
        "http://192.0.2.1/",
        "http://224.0.0.1/",
        "http://0.0.0.0/",
        "http://user:password@example.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/plain,hello",
        "http://[2001:4860:4860::8888%25eth0]/",
    ),
)
def test_model_controlled_urls_reject_local_and_credentialed_targets(url: str) -> None:
    with pytest.raises(ToolError):
        browser_tools.check_url(url)
    with pytest.raises(ToolError):
        web_tools.normalize_urls(url)


@pytest.mark.asyncio
async def test_dns_results_are_all_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(NetworkPolicyError):
        await assert_public_http_url("https://example.test/")


def test_public_https_url_is_normalized() -> None:
    assert browser_tools.check_url("example.com/a#fragment") == "https://example.com/a"


class _FakeUpstreamWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


async def _proxy_exchange(proxy: PublicEgressProxy, request: bytes) -> bytes:
    endpoint = urlsplit(proxy.proxy_url)
    reader, writer = await asyncio.open_connection(endpoint.hostname, endpoint.port)
    writer.write(request)
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_proxy_pins_validated_ip_against_dns_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolutions = 0
    dialed: list[tuple[str, int]] = []

    def rebinding_resolver(_hostname: str, port: int, **_kwargs):
        nonlocal resolutions
        resolutions += 1
        address = "93.184.216.34" if resolutions == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    async def fake_dial(address: str, port: int, *, timeout_s: float):
        del timeout_s
        dialed.append((address, port))
        upstream_reader = asyncio.StreamReader()
        upstream_reader.feed_eof()
        return upstream_reader, _FakeUpstreamWriter()

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_resolver)
    monkeypatch.setattr(network_policy, "_open_connection_to_ip", fake_dial)

    async with PublicEgressProxy() as proxy:
        response = await _proxy_exchange(
            proxy,
            b"CONNECT rebind.example:443 HTTP/1.1\r\nHost: rebind.example:443\r\n\r\n",
        )

    assert response.startswith(b"HTTP/1.1 200")
    assert resolutions == 1, "the validated hostname was unexpectedly resolved again"
    assert dialed == [("93.184.216.34", 443)]


@pytest.mark.asyncio
async def test_proxy_rejects_private_connect_without_dialing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_dial(*_args, **_kwargs):
        raise AssertionError("private CONNECT target reached the socket dialer")

    monkeypatch.setattr(network_policy, "_open_connection_to_ip", forbidden_dial)
    async with PublicEgressProxy() as proxy:
        response = await _proxy_exchange(
            proxy,
            b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443\r\n\r\n",
        )
    assert response.startswith(b"HTTP/1.1 403")


@pytest.mark.asyncio
async def test_proxy_rejects_private_forward_request_without_dialing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_dial(*_args, **_kwargs):
        raise AssertionError("private forward target reached the socket dialer")

    monkeypatch.setattr(network_policy, "_open_connection_to_ip", forbidden_dial)
    async with PublicEgressProxy() as proxy:
        response = await _proxy_exchange(
            proxy,
            b"GET http://10.0.0.8/admin HTTP/1.1\r\nHost: 10.0.0.8\r\n\r\n",
        )
    assert response.startswith(b"HTTP/1.1 403")
