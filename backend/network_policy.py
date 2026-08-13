"""Shared, fail-closed outbound policy for model-controlled HTTP traffic.

URL validation alone cannot prevent DNS rebinding: a hostname can resolve to a
public address during validation and to a loopback/private address when the HTTP
client connects.  :class:`PublicEgressProxy` closes that gap.  It resolves and
validates a destination, then opens the socket to the *numeric IP it validated*.
HTTPS remains end-to-end: CONNECT only carries bytes, so Chromium/httpx still do
TLS and certificate verification using the original hostname.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit


class NetworkPolicyError(ValueError):
    """A URL cannot be reached without crossing the local-network boundary."""


_HTTP_TOKEN = re.compile(rb"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_MAX_HEADER_BYTES = 64 * 1024
_IPV6_TRANSLATION_RANGES = (
    # NAT64 can translate an apparently global IPv6 literal into a loopback or
    # private IPv4 destination after this process has finished validating it.
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


def _parse_ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    # Scoped IPv6 literals are meaningful only relative to a local interface and
    # therefore never belong in model-controlled public egress.
    if "%" in address:
        raise NetworkPolicyError("IPv6 zone identifiers are not allowed")
    try:
        return ipaddress.ip_address(address)
    except ValueError as exc:
        raise NetworkPolicyError("the destination did not resolve to an IP address") from exc


def _forbidden_ip(address: str) -> bool:
    """Return true unless ``address`` is globally routable.

    ``is_private`` is not sufficient (for example, carrier-grade NAT space is
    neither private nor globally reachable on some Python versions).  Requiring
    ``is_global`` also rejects loopback, link-local, multicast, documentation,
    reserved and unspecified ranges.
    """

    ip = _parse_ip(address)
    if isinstance(ip, ipaddress.IPv6Address):
        # Reject address families whose final IPv4 endpoint can be selected by
        # a host tunnel/NAT implementation after our numeric IPv6 dial.  This
        # includes IPv4-mapped, 6to4, Teredo and both standardized NAT64 ranges.
        # The deprecated site-local block is also considered global by some
        # Python versions even though it is explicitly not public Internet.
        if (
            ip.ipv4_mapped is not None
            or ip.sixtofour is not None
            or ip.teredo is not None
            or ip.is_site_local
            or any(ip in network for network in _IPV6_TRANSLATION_RANGES)
        ):
            return True
    return any(
        (
            not ip.is_global,
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def normalize_public_http_url(value: object, *, default_https: bool = True) -> str:
    """Normalize HTTP(S) input and reject credentials/local address literals."""

    if not isinstance(value, str) or not value.strip():
        raise NetworkPolicyError("URL cannot be empty")
    raw = value.strip()
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw) and not re.match(
        r"^[A-Za-z][A-Za-z0-9+.-]*://", raw,
    ):
        raise NetworkPolicyError("only HTTP and HTTPS URLs are allowed")
    if default_https and re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw) is None:
        raw = "https://" + raw
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise NetworkPolicyError("invalid URL") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise NetworkPolicyError("only HTTP and HTTPS URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise NetworkPolicyError("credentials in URLs are not allowed")

    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname:
        raise NetworkPolicyError("URL is missing a hostname")
    if "%" in hostname:
        raise NetworkPolicyError("IPv6 zone identifiers are not allowed")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise NetworkPolicyError("local and private-network addresses are not allowed")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except (UnicodeError, ValueError) as exc:
            raise NetworkPolicyError("invalid hostname") from exc
    else:
        if _forbidden_ip(str(literal)):
            raise NetworkPolicyError("local and private-network addresses are not allowed")
        hostname = str(literal)

    if port is not None and not (1 <= port <= 65535):
        raise NetworkPolicyError("invalid URL port")
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    # Fragments are client-side only and must never influence the proxy target.
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "", parsed.query, ""))


def _resolve(hostname: str, port: int) -> tuple[str, ...]:
    try:
        rows = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetworkPolicyError("DNS resolution failed") from exc
    addresses = tuple(dict.fromkeys(str(row[4][0]) for row in rows if row[4]))
    if not addresses:
        raise NetworkPolicyError("DNS returned no usable addresses")
    return addresses


async def resolve_public_addresses(hostname: str, port: int) -> tuple[str, ...]:
    """Resolve once and require *every* returned address to be globally routable."""

    if "%" in hostname:
        raise NetworkPolicyError("IPv6 zone identifiers are not allowed")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        addresses = await asyncio.to_thread(_resolve, hostname, port)
    else:
        addresses = (str(literal),)
    if any(_forbidden_ip(address) for address in addresses):
        raise NetworkPolicyError(
            "the hostname resolved to a local, private, reserved, or non-global address"
        )
    return addresses


async def assert_public_http_url(value: object) -> str:
    """Normalize a URL and require every current DNS result to be public.

    This remains available for compatibility and useful early errors.  Network
    callers must still use :class:`PublicEgressProxy`, because a preflight DNS
    check by itself is not an authorization to connect later.
    """

    url = normalize_public_http_url(value)
    parsed = urlsplit(url)
    await resolve_public_addresses(
        parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80)
    )
    return url


async def _open_connection_to_ip(
    address: str, port: int, *, timeout_s: float
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Dial a numeric IP without consulting DNS again."""

    ip = _parse_ip(address)
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        # ``sock_connect`` receives an address already accepted by inet_pton;
        # unlike a hostname-based convenience API it has no reason or input with
        # which to perform a second DNS lookup.
        await asyncio.wait_for(
            asyncio.get_running_loop().sock_connect(sock, (str(ip), port)),
            timeout=timeout_s,
        )
        return await asyncio.open_connection(sock=sock)
    except BaseException:
        sock.close()
        raise


async def _relay(
    source: asyncio.StreamReader, destination: asyncio.StreamWriter
) -> None:
    try:
        while True:
            chunk = await source.read(64 * 1024)
            if not chunk:
                break
            destination.write(chunk)
            await destination.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass


class PublicEgressProxy:
    """Loopback-only HTTP CONNECT/forward proxy with IP-pinned dialing."""

    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = max(1.0, float(connect_timeout_s))
        self._server: asyncio.AbstractServer | None = None
        self._start_lock = asyncio.Lock()
        self._client_writers: set[asyncio.StreamWriter] = set()

    @property
    def proxy_url(self) -> str:
        server = self._server
        if server is None or not server.sockets:
            raise RuntimeError("public egress proxy has not been started")
        port = int(server.sockets[0].getsockname()[1])
        return f"http://127.0.0.1:{port}"

    async def start(self) -> "PublicEgressProxy":
        async with self._start_lock:
            if self._server is None:
                self._server = await asyncio.start_server(
                    self._handle_client,
                    host="127.0.0.1",
                    port=0,
                    limit=_MAX_HEADER_BYTES + 1,
                )
        return self

    async def aclose(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        for writer in tuple(self._client_writers):
            writer.close()
        for writer in tuple(self._client_writers):
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        self._client_writers.clear()

    async def __aenter__(self) -> "PublicEgressProxy":
        return await self.start()

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def _send_error(
        self, writer: asyncio.StreamWriter, status: int, reason: str
    ) -> None:
        body = f"{status} {reason}\n".encode("ascii")
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\n".encode("ascii")
            + b"Content-Type: text/plain; charset=us-ascii\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Connection: close\r\n\r\n"
            + body
        )
        with contextlib.suppress(ConnectionError):
            await writer.drain()

    async def _dial(
        self, hostname: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        # Resolution and authorization happen once.  The following socket calls
        # receive only numeric addresses, which is the anti-rebinding invariant.
        addresses = await resolve_public_addresses(hostname, port)
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await _open_connection_to_ip(
                    address, port, timeout_s=self.connect_timeout_s
                )
            except (OSError, asyncio.TimeoutError) as exc:
                last_error = exc
        raise ConnectionError("could not connect to an authorized address") from last_error

    @staticmethod
    def _parse_authority(authority: str) -> tuple[str, int]:
        if not authority or any(ch.isspace() for ch in authority):
            raise NetworkPolicyError("invalid CONNECT authority")
        try:
            parsed = urlsplit("//" + authority)
            port = parsed.port
        except ValueError as exc:
            raise NetworkPolicyError("invalid CONNECT authority") from exc
        if (
            not parsed.hostname
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise NetworkPolicyError("CONNECT requires a hostname and explicit port")
        # Reuse URL hostname/literal checks without doing DNS here.
        checked = normalize_public_http_url(f"https://{authority}/", default_https=False)
        normalized = urlsplit(checked)
        return normalized.hostname or "", port

    @staticmethod
    def _forward_request(
        method: bytes, target: bytes, version: bytes, header_lines: list[bytes]
    ) -> tuple[str, int, bytes]:
        try:
            raw_target = target.decode("ascii")
        except UnicodeDecodeError as exc:
            raise NetworkPolicyError("proxy request target must be ASCII") from exc
        normalized = normalize_public_http_url(raw_target, default_https=False)
        parsed = urlsplit(normalized)
        if parsed.scheme != "http":
            raise NetworkPolicyError("HTTPS proxy requests must use CONNECT")
        hostname = parsed.hostname or ""
        port = parsed.port or 80
        origin_target = parsed.path or "/"
        if parsed.query:
            origin_target += "?" + parsed.query

        kept: list[bytes] = []
        for line in header_lines:
            if b":" not in line:
                raise NetworkPolicyError("malformed HTTP header")
            name, value = line.split(b":", 1)
            if not _HTTP_TOKEN.fullmatch(name):
                raise NetworkPolicyError("malformed HTTP header name")
            lower = name.lower()
            if lower in {b"host", b"proxy-connection", b"proxy-authorization", b"connection"}:
                continue
            kept.append(name + b":" + value)

        host_header = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port is not None:
            host_header += f":{port}"
        outbound = [method + b" " + origin_target.encode("ascii") + b" " + version]
        outbound.append(b"Host: " + host_header.encode("ascii"))
        outbound.extend(kept)
        # One client request maps to one pinned upstream connection.  Closing it
        # prevents a subsequent absolute-form request for another host from being
        # smuggled over an already-authorized connection.
        outbound.append(b"Connection: close")
        return hostname, port, b"\r\n".join(outbound) + b"\r\n\r\n"

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._client_writers.add(writer)
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            try:
                header = await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                await self._send_error(writer, 400, "Bad Request")
                return
            if len(header) > _MAX_HEADER_BYTES:
                await self._send_error(writer, 431, "Request Header Fields Too Large")
                return
            lines = header[:-4].split(b"\r\n")
            if not lines or len(lines[0].split(b" ")) != 3:
                await self._send_error(writer, 400, "Bad Request")
                return
            method, target, version = lines[0].split(b" ")
            if not _HTTP_TOKEN.fullmatch(method) or version not in {b"HTTP/1.0", b"HTTP/1.1"}:
                await self._send_error(writer, 400, "Bad Request")
                return

            try:
                if method.upper() == b"CONNECT":
                    hostname, port = self._parse_authority(target.decode("ascii"))
                    upstream_reader, upstream_writer = await self._dial(hostname, port)
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await writer.drain()
                else:
                    hostname, port, outbound = self._forward_request(
                        method, target, version, lines[1:]
                    )
                    upstream_reader, upstream_writer = await self._dial(hostname, port)
                    upstream_writer.write(outbound)
                    await upstream_writer.drain()
            except (UnicodeDecodeError, NetworkPolicyError):
                await self._send_error(writer, 403, "Forbidden")
                return
            except (ConnectionError, OSError, asyncio.TimeoutError):
                await self._send_error(writer, 502, "Bad Gateway")
                return

            client_to_upstream = asyncio.create_task(_relay(reader, upstream_writer))
            upstream_to_client = asyncio.create_task(_relay(upstream_reader, writer))
            done, pending = await asyncio.wait(
                {client_to_upstream, upstream_to_client},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        except Exception:
            # Never let malformed model-controlled traffic escape the server task.
            with contextlib.suppress(Exception):
                await self._send_error(writer, 500, "Internal Server Error")
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                with contextlib.suppress(Exception):
                    await upstream_writer.wait_closed()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            self._client_writers.discard(writer)


__all__ = [
    "NetworkPolicyError",
    "PublicEgressProxy",
    "assert_public_http_url",
    "normalize_public_http_url",
    "resolve_public_addresses",
]
