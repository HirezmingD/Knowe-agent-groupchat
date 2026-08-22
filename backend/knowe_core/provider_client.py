# knowe v0.6 — Harness 核心引擎
"""OpenAI-compatible provider client with pooled keep-alive and bounded retries.

ProviderClient instances are still stateless with respect to chat history, but their
HTTP transport is deliberately shared per event loop and provider origin.  Coordinator,
Zinnia and Worker calls therefore reuse healthy TCP/TLS connections instead of creating a
new ``httpx.AsyncClient`` for every request.

Retries are limited to transient transport failures, rate limits and selected 5xx status
codes.  Streaming calls are retried only before the first event is emitted; once a stream
has produced output, retrying could duplicate text or tool calls and is therefore unsafe.

The optional ``client_factory`` remains the test/embedding injection boundary.  Its
returned client is cached for the lifetime of this ProviderClient so injected transports
exercise the same connection-reuse path as production.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import weakref
from collections.abc import Mapping
from typing import Any, AsyncGenerator, Callable

import httpx

from knowe_core.provider_identity import http_status_error_message, provider_target
from knowe_core.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from knowe_core.anthropic_codec import (
    AnthropicStreamDecoder,
    build_headers as build_anthropic_headers,
    decode_response as decode_anthropic_response,
    encode_request as encode_anthropic_request,
    resolve_endpoint as resolve_anthropic_endpoint,
)

logger = logging.getLogger(__name__)

# The application configuration is the sole retry-policy source.  Direct embedders
# that omit the argument receive no implicit retries instead of a second hidden policy.
DEFAULT_MAX_RETRIES = 0
DEFAULT_BACKOFF_BASE = 1.0  # seconds: 1, 2, 4, ...


def normalize_usage_buckets(raw: Any) -> dict[str, int] | None:
    """Normalize any provider usage object into cache buckets.

    Returns ``{"cache_hit_input": int, "cache_miss_input": int, "output": int}`` or
    ``None`` when the payload carries neither input nor output counters.

    Dialects handled:
    - OpenAI standard: ``usage.prompt_tokens_details.cached_tokens``
    - DeepSeek: ``usage.prompt_cache_hit_tokens`` / ``usage.prompt_cache_miss_tokens``
    - Anthropic: ``usage.cache_read_input_tokens`` + ``usage.cache_creation_input_tokens``
    - Gemini: ``usageMetadata.cachedContentTokenCount``

    Unknown fields fall back to cache_miss so tokens are never dropped silently.
    """
    if not isinstance(raw, Mapping):
        return None
    usage = raw
    # Unwrap in priority order: dedicated usage keys first, then generic
    # envelope keys (message/data/response/chunk/delta) used by native
    # Anthropic streams, the Responses API and gateway SDKs.  Envelopes may
    # nest several levels deep (e.g. data -> response -> usage), so keep
    # unwrapping until no further container is found (depth guard 4).
    for _ in range(4):
        for key in ("usage", "usageMetadata", "usage_metadata", "token_usage"):
            child = usage.get(key)
            if isinstance(child, Mapping):
                usage = child
                break
        else:
            for key in ("message", "data", "response", "chunk", "delta"):
                child = usage.get(key)
                if isinstance(child, Mapping):
                    usage = child
                    break
            else:
                break
            continue
        break

    def num(value: Any) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(numeric) or numeric < 0:
            return None
        return int(numeric)

    output: int | None = None
    for key in ("completion_tokens", "output_tokens", "candidatesTokenCount", "candidates_token_count"):
        output = num(usage.get(key))
        if output is not None:
            break
    input_total: int | None = None
    for key in ("prompt_tokens", "input_tokens", "promptTokenCount", "prompt_token_count"):
        input_total = num(usage.get(key))
        if input_total is not None:
            break
    if output is None and input_total is None:
        return None
    output = output or 0
    input_total = input_total or 0

    # Cache-hit candidates, by dialect.  Anthropic may split hit input across read
    # and creation, so all matching fields are summed rather than first-wins.
    hit = 0
    for key in (
        "prompt_cache_hit_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "cachedContentTokenCount",
    ):
        value = num(usage.get(key))
        if value is not None:
            hit += value
    if hit == 0:
        details = usage.get("prompt_tokens_details")
        if not isinstance(details, Mapping):
            details = usage.get("input_tokens_details")
        if isinstance(details, Mapping):
            hit = num(details.get("cached_tokens")) or 0

    explicit_miss = num(usage.get("prompt_cache_miss_tokens"))
    if explicit_miss is not None:
        miss = explicit_miss
    else:
        miss = max(0, input_total - hit)
    return {
        "cache_hit_input": hit,
        "cache_miss_input": miss,
        "output": output,
    }


def build_http_timeout(
    *,
    connect: float | None = 10.0,
    read: float | None = 120.0,
    write: float | None = 120.0,
    pool: float | None = 10.0,
) -> httpx.Timeout:
    """Build explicit per-operation HTTP timeouts.

    ``None`` disables only that network-progress boundary.  For a streaming response,
    httpx applies ``read`` to each wait for the next network chunk, so every arriving
    event naturally starts a fresh read window; it is not an overall generation clock.
    """

    return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)


DEFAULT_TIMEOUT = build_http_timeout()
DEFAULT_MAX_CONNECTIONS = 100
DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 20
DEFAULT_KEEPALIVE_EXPIRY = 300.0

#: Test/embedding injection point: return an httpx.AsyncClient (often MockTransport).
ClientFactory = Callable[[], httpx.AsyncClient]


def _should_retry(status_code: int) -> bool:
    """Only retry rate limits and provider/server failures that are normally transient."""

    return status_code in (429, 500, 502, 503, 504)


class ProviderClient:
    """OpenAI-compatible chat completions client.

    The object stores request configuration, not conversation state.  Production clients
    on the same asyncio loop and provider origin share one keep-alive pool.
    """

    _shared_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, httpx.AsyncClient]]" = (
        weakref.WeakKeyDictionary()
    )
    _shared_clients_lock = threading.RLock()

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "",
        timeout: httpx.Timeout | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        extra_headers: dict[str, str] | None = None,
        client_factory: ClientFactory | None = None,
        provider: str = "",
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        transport: str = "openai_chat",
        on_format_rejected=None,
    ):
        normalized_base = base_url.rstrip("/")
        # HTTPX requires an absolute URL even when MockTransport intercepts the request.
        # The synthetic origin is used only for an explicitly injected client factory.
        self.base_url = normalized_base or (
            "http://provider.invalid" if client_factory is not None else ""
        )
        self.api_key = api_key
        self.model = model
        self.provider = provider
        self.transport = transport or "openai_chat"
        self.timeout = timeout or DEFAULT_TIMEOUT
        self.max_retries = max(0, int(max_retries or 0))
        self.backoff_base = max(0.0, float(backoff_base or 0.0))
        self._client_factory = client_factory
        self._injected_client: httpx.AsyncClient | None = None
        # [v1.0.39.2] 格式降级回调：网关 400 点名拒绝某内容块格式
        #   （如 file 块不认）时，用回调把 messages 换格式后**单次重发**。
        #   默认 None = 老行为（不降级，直接抛错）。回调签名 (messages) -> messages。
        self._on_format_rejected = on_format_rejected

        self._headers = {
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }

        if self.transport == "anthropic_messages":
            # Anthropic 走 x-api-key + anthropic-version，不打 /chat/completions。
            # endpoint 由 codec 从 core_base_url 的保护形态还原（剥 /chat/completions#）。
            self._endpoint = resolve_anthropic_endpoint(self.base_url)
            # 剥掉 base_url 自带的 fragment（若有）——resolve 已处理，这里幂等兜底。
            self._headers.update(build_anthropic_headers(api_key))
        else:
            # OpenAI 兼容路径（openai_chat / codex_responses 的 chat/completions 兼容端点）
            if api_key:
                self._headers["Authorization"] = f"Bearer {api_key}"
            # Respect a version segment already present in the configured base URL.  The
            # request layer only appends /chat/completions; it never injects /v1.
            stripped = self.base_url
            if stripped.endswith("/chat/completions"):
                self._endpoint = stripped
            else:
                self._endpoint = f"{stripped}/chat/completions"

    # ── HTTP client lifecycle ──

    @property
    def _pool_key(self) -> str:
        try:
            url = httpx.URL(self._endpoint)
            if not url.is_absolute_url:
                return self._endpoint or "unconfigured-provider"
            default_port = 443 if url.scheme == "https" else 80
            return f"{url.scheme}://{url.host}:{url.port or default_port}"
        except (TypeError, ValueError, httpx.InvalidURL):
            return self._endpoint or "unconfigured-provider"

    @staticmethod
    def _create_shared_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            limits=httpx.Limits(
                max_connections=DEFAULT_MAX_CONNECTIONS,
                max_keepalive_connections=DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
                keepalive_expiry=DEFAULT_KEEPALIVE_EXPIRY,
            ),
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            if self._injected_client is None or self._injected_client.is_closed:
                self._injected_client = self._client_factory()
            return self._injected_client

        loop = asyncio.get_running_loop()
        with self._shared_clients_lock:
            by_origin = self._shared_clients.setdefault(loop, {})
            client = by_origin.get(self._pool_key)
            if client is None or client.is_closed:
                client = self._create_shared_client()
                by_origin[self._pool_key] = client
            return client

    # Backward-compatible private name used by a few transport probes.
    def _new_client(self) -> httpx.AsyncClient:
        return self._get_client()

    async def aclose(self) -> None:
        """Close an injected client owned by this instance.

        Production shared pools live for the event-loop lifetime and must not be closed by
        one Agent while another Agent is still using them.
        """

        client = self._injected_client
        self._injected_client = None
        if client is not None and not client.is_closed:
            await client.aclose()

    @classmethod
    async def aclose_shared_clients(cls) -> None:
        """Close shared clients for the current event loop (mainly shutdown/tests)."""

        loop = asyncio.get_running_loop()
        with cls._shared_clients_lock:
            clients = tuple(cls._shared_clients.pop(loop, {}).values())
        for client in clients:
            if not client.is_closed:
                await client.aclose()

    # ── Non-streaming ──

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.transport == "anthropic_messages":
            body = encode_anthropic_request(
                model=self.model,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return await self._request(body)
        body = self._build_body(
            messages,
            tools,
            temperature,
            max_tokens,
            stream=False,
            extra_body=extra_body,
        )
        return await self._request(body)

    # ── Streaming ──

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield provider-neutral SSE events with safe pre-output retries."""

        body = self._build_stream_body(
            messages, tools, temperature, max_tokens, extra_body,
        )
        # [v1.0.39.2] 循环槽位 = 正常重试次数 + 1 个降级槽。降级 continue 会消耗
        #   一个额外槽位；未触发降级时多出的槽永远不会被走到（正常路径在
        #   attempt == max_retries 那轮要么 return 要么 raise），重试语义逐字节不变。
        total_attempts = self.max_retries + 2
        # 格式降级重发标记：同一请求最多降级重发一次
        #   （换格式后再失败 = 网关真有问题，照常抛错，绝不无限重试）。
        format_retried = False

        for attempt in range(total_attempts):
            emitted_event = False
            try:
                client = self._get_client()
                async with client.stream(
                    "POST",
                    self._endpoint,
                    headers=self._headers,
                    json=body,
                    timeout=self.timeout,
                ) as response:
                    await self._check_response(response)
                    async for event in self._parse_sse_stream(response):
                        emitted_event = True
                        yield event
                return
            except ProviderError as exc:
                # [v1.0.39.2] 网关点名拒绝 file 块且配置了降级回调 → 换 text 块重发一次。
                #   必须发生在任何输出之前（emitted_event=False），否则不降级。
                if (
                    not emitted_event
                    and not format_retried
                    and getattr(exc, "format_rejected", None) == "file"
                    and self._on_format_rejected is not None
                ):
                    new_messages = self._on_format_rejected(messages)
                    if new_messages is not None:
                        format_retried = True
                        messages = new_messages
                        body = self._build_stream_body(
                            messages, tools, temperature, max_tokens, extra_body,
                        )
                        continue
                exc.with_retry_context(
                    attempts=attempt + 1,
                    max_attempts=total_attempts,
                )
                if emitted_event:
                    raise self._stream_started_error(exc, attempt + 1, total_attempts) from exc
                if self._can_retry(exc, attempt):
                    await self._wait_before_retry(exc, attempt)
                    continue
                raise exc.with_retry_context(
                    attempts=attempt + 1,
                    max_attempts=total_attempts,
                    exhausted=True,
                )
            except httpx.TimeoutException as exc:
                mapped = self._transport_error(exc, timeout=True)
                mapped.with_retry_context(attempts=attempt + 1, max_attempts=total_attempts)
                if emitted_event:
                    raise self._stream_started_error(mapped, attempt + 1, total_attempts) from exc
                if self._can_retry(mapped, attempt):
                    await self._wait_before_retry(mapped, attempt)
                    continue
                raise mapped.with_retry_context(
                    attempts=attempt + 1,
                    max_attempts=total_attempts,
                    exhausted=True,
                ) from exc
            except httpx.TransportError as exc:
                mapped = self._transport_error(exc, timeout=False)
                mapped.with_retry_context(attempts=attempt + 1, max_attempts=total_attempts)
                if emitted_event:
                    raise self._stream_started_error(mapped, attempt + 1, total_attempts) from exc
                if self._can_retry(mapped, attempt):
                    await self._wait_before_retry(mapped, attempt)
                    continue
                raise mapped.with_retry_context(
                    attempts=attempt + 1,
                    max_attempts=total_attempts,
                    exhausted=True,
                ) from exc

        raise ProviderError("Provider retry loop ended unexpectedly.")

    # ── Request/retry helpers ──

    def _build_stream_body(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        max_tokens: int | None,
        extra_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """[v1.0.39.2] 流式请求体构建（chat_stream 与格式降级重发共用）。

        降级重发时 messages 已被回调换格式，body 必须重建——抽出这个
        私有方法保证两处走同一条构建路径，不会出现新旧 body 不一致。
        """
        if self.transport == "anthropic_messages":
            return encode_anthropic_request(
                model=self.model,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        return self._build_body(
            messages,
            tools,
            temperature,
            max_tokens,
            stream=True,
            extra_body=extra_body,
        )

    def _build_body(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
        extra_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = tools
        # ``None`` means let the provider/model choose its own output limit.  This is the
        # production Worker path; an explicit positive value remains available to other
        # callers such as one-token connectivity probes.
        if max_tokens is not None and int(max_tokens) > 0:
            body["max_tokens"] = int(max_tokens)
        if stream:
            # [M1 实测] 部分 OpenAI 兼容厂商（Kimi/Moonshot 等）默认不在流式响应里
            # 返回 usage 帧——token 采集层会永远拿不到账单。主动请求 include_usage
            # （OpenAI 标准字段，DeepSeek/GLM/Kimi 均接受）。调用方显式传入的
            # ``extra_body.stream_options`` 仍优先（extra_body 最后合并）。
            body.setdefault("stream_options", {"include_usage": True})
        if extra_body:
            body.update(extra_body)
        return body

    async def _request(self, body: dict[str, Any]) -> dict[str, Any]:
        total_attempts = self.max_retries + 1
        for attempt in range(total_attempts):
            try:
                client = self._get_client()
                response = await client.post(
                    self._endpoint,
                    headers=self._headers,
                    json=body,
                    timeout=self.timeout,
                )
                await self._check_response(response)
                try:
                    parsed = response.json()
                    if not isinstance(parsed, dict):
                        raise TypeError(
                            f"expected a JSON object, got {type(parsed).__name__}"
                        )
                    return dict(parsed)
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise ProviderBadResponseError(
                        f"响应不是合法 JSON：{self._exception_detail(exc)}"
                    ) from exc
            except ProviderError as exc:
                exc.with_retry_context(attempts=attempt + 1, max_attempts=total_attempts)
                if self._can_retry(exc, attempt):
                    await self._wait_before_retry(exc, attempt)
                    continue
                raise exc.with_retry_context(
                    attempts=attempt + 1,
                    max_attempts=total_attempts,
                    exhausted=True,
                )
            except httpx.TimeoutException as exc:
                mapped = self._transport_error(exc, timeout=True)
                mapped.with_retry_context(attempts=attempt + 1, max_attempts=total_attempts)
                if self._can_retry(mapped, attempt):
                    await self._wait_before_retry(mapped, attempt)
                    continue
                raise mapped.with_retry_context(
                    attempts=attempt + 1,
                    max_attempts=total_attempts,
                    exhausted=True,
                ) from exc
            except httpx.TransportError as exc:
                mapped = self._transport_error(exc, timeout=False)
                mapped.with_retry_context(attempts=attempt + 1, max_attempts=total_attempts)
                if self._can_retry(mapped, attempt):
                    await self._wait_before_retry(mapped, attempt)
                    continue
                raise mapped.with_retry_context(
                    attempts=attempt + 1,
                    max_attempts=total_attempts,
                    exhausted=True,
                ) from exc

        raise ProviderError("Provider retry loop ended unexpectedly.")

    def _can_retry(self, error: ProviderError, attempt: int) -> bool:
        if attempt >= self.max_retries or not error.retryable:
            return False
        status = error.status_code
        return status is None or _should_retry(int(status))

    async def _wait_before_retry(self, error: ProviderError, attempt: int) -> None:
        delay = self.backoff_base * (2 ** attempt)
        logger.warning(
            "provider transient failure; retry %d/%d in %.1fs: %s",
            attempt + 1,
            self.max_retries,
            delay,
            error,
        )
        if delay > 0:
            await asyncio.sleep(delay)
        else:
            await asyncio.sleep(0)

    def _transport_error(
        self,
        error: httpx.TransportError,
        *,
        timeout: bool,
    ) -> ProviderError:
        detail = self._exception_detail(error)
        target = provider_target(self.provider, self.base_url, self.model)
        if timeout:
            if isinstance(error, httpx.ConnectTimeout):
                phase = "connect"
                configured = self.timeout.connect
            elif isinstance(error, httpx.ReadTimeout):
                phase = "read"
                configured = self.timeout.read
            elif isinstance(error, httpx.WriteTimeout):
                phase = "write"
                configured = self.timeout.write
            elif isinstance(error, httpx.PoolTimeout):
                phase = "pool"
                configured = self.timeout.pool
            else:
                phase = "network"
                configured = None
            window = f"（{configured:g}s）" if isinstance(configured, (int, float)) else ""
            return ProviderTimeoutError(
                f"{target} 在配置的 {phase} 无网络进展窗口{window}内超时：{detail}",
                retryable=True,
                cause_type=type(error).__name__,
            )

        # Configuration/protocol mistakes are not transient.  Network, proxy and remote
        # protocol errors are safe to retry before any streamed event has been emitted.
        retryable = not isinstance(
            error,
            (httpx.LocalProtocolError, httpx.UnsupportedProtocol),
        )
        return ProviderConnectionError(
            f"连不上 {target}：{detail}",
            retryable=retryable,
            cause_type=type(error).__name__,
        )

    @staticmethod
    def _exception_detail(error: BaseException) -> str:
        """Return a non-empty diagnostic, walking causes when wrappers have no text."""

        seen: set[int] = set()
        current: BaseException | None = error
        chain: list[str] = []
        while current is not None and id(current) not in seen and len(chain) < 4:
            seen.add(id(current))
            text = str(current).strip()
            label = type(current).__name__
            if text:
                chain.append(f"{label}: {text}")
            elif not chain:
                chain.append(f"{label}（异常对象未提供文本）")
            # Explicit causes are useful even when the wrapper has text. Implicit
            # ``__context__`` often contains an implementation detail (for example a
            # JSON decoder's StopIteration), so follow it only when the wrapper itself
            # is empty and would otherwise hide the transport cause.
            current = current.__cause__ or (current.__context__ if not text else None)
        return "；由 ".join(chain) if chain else type(error).__name__

    @staticmethod
    def _stream_started_error(
        error: ProviderError,
        attempts: int,
        max_attempts: int,
    ) -> ProviderError:
        error.with_retry_context(attempts=attempts, max_attempts=max_attempts)
        suffix = "流式响应已开始，为避免重复文本或重复工具调用，未自动重放该请求。"
        if suffix not in error.message:
            error.message = f"{error.message} {suffix}"
            error.args = (error.message,)
        return error

    async def _check_response(self, response: httpx.Response) -> None:
        """Map HTTP status codes to typed, binding-aware provider errors."""

        if response.status_code == 200:
            return

        body = ""
        try:
            body = (await response.aread()).decode("utf-8", "replace")[:500]
        except Exception:
            pass

        code = response.status_code
        message = http_status_error_message(
            code,
            provider=self.provider,
            base_url=self.base_url,
            model=self.model,
            response_body=body,
        )
        # [v1.0.39.2] 网关点名拒绝内容块格式（OpenAI 兼容网关普遍未实现新版 file 块）。
        #   把该信号带在错误上（format_rejected="file"），上层据此单次格式降级重发。
        if code == 400 and "invalid value: file" in body.lower():
            raise ProviderError(
                message, code, body, retryable=False, format_rejected="file",
            )
        if code == 401:
            raise ProviderAuthError(message, 401, body, retryable=False)
        if code in (402, 403):
            raise ProviderError(message, code, body, retryable=False)
        if code == 429:
            raise ProviderRateLimitError(message, 429, body, retryable=True)
        if code >= 500:
            raise ProviderConnectionError(
                message,
                code,
                body,
                retryable=_should_retry(code),
            )
        raise ProviderError(message, code, body, retryable=False)

    async def _parse_sse_stream(
        self, response: httpx.Response,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Parse provider SSE into a provider-neutral typed event stream.

        Chunk boundaries are unrelated to line boundaries, so a small line buffer is
        mandatory.  Choice metadata (especially ``finish_reason``) is retained: it is
        part of the control protocol and is used by the downstream protocol gate.
        """
        if self.transport == "anthropic_messages":
            async for event in self._parse_anthropic_sse_stream(response):
                yield event
            return

        buffer = ""
        saw_finish = False

        async for chunk in response.aiter_text():
            if not chunk:
                continue
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                events, done = self._parse_sse_line(line)
                for event in events:
                    if event.get("type") == "finish":
                        saw_finish = True
                    yield event
                if done:
                    if not saw_finish:
                        yield {"type": "finish", "reason": "stop"}
                    return

        # A few gateways omit the terminal newline.  The final data frame is still a
        # real frame and must not disappear merely because the TCP stream ended.
        if buffer.strip():
            events, done = self._parse_sse_line(buffer)
            for event in events:
                if event.get("type") == "finish":
                    saw_finish = True
                yield event
            if done and not saw_finish:
                yield {"type": "finish", "reason": "stop"}

    async def _parse_anthropic_sse_stream(
        self, response: httpx.Response,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Parse Anthropic Messages API SSE into provider-neutral events.

        Anthropic frames are ``event: <name>`` + ``data: {json}`` line pairs (blank line
        terminates each frame).  ``AnthropicStreamDecoder`` accumulates tool input deltas
        across frames and emits a full ``tool_call`` on the matching ``content_block_stop``.
        """
        decoder = AnthropicStreamDecoder()
        buffer = ""
        frame_data: str | None = None
        emitted_finish = False

        def flush() -> list[dict[str, Any]]:
            """Decode the current ``data:`` frame into events (or [] on parse failure)."""
            nonlocal frame_data, emitted_finish
            raw = (frame_data or "").lstrip()
            frame_data = None
            payload = raw[len("data:"):].lstrip() if raw.startswith("data:") else raw
            if not payload:
                return []
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                logger.warning("Anthropic SSE JSON 解析失败：%s", exc)
                return []
            if not isinstance(data, dict):
                return []
            etype = data.get("type")
            out: list[dict[str, Any]] = []
            for e in decoder.feed(str(etype or ""), data):
                if e.get("type") == "finish":
                    emitted_finish = True
                out.append(e)
            return out

        async for chunk in response.aiter_text():
            if not chunk:
                continue
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip()
                if not line.strip():
                    # blank line closes the frame
                    for e in flush():
                        yield e
                elif line.startswith("data:"):
                    if frame_data is not None:
                        for e in flush():
                            yield e
                    frame_data = line
                else:
                    # event:/comment/keep-alive lines: not needed to decode (type is
                    # carried in the JSON body); ignore them.
                    pass

        # flush any trailing frame (gateway may omit final blank line)
        if frame_data is not None:
            for e in flush():
                yield e
        if not emitted_finish:
            yield {"type": "finish", "reason": "end_turn"}

    @classmethod
    def _parse_sse_line(cls, line: str) -> tuple[list[dict[str, Any]], bool]:
        """Return ``(events, is_done_frame)`` for one physical SSE line."""
        stripped = line.strip()
        if not stripped or stripped.startswith(":"):
            return [], False
        if not stripped.startswith("data:"):
            return [], False
        payload = stripped[5:].lstrip()
        if payload == "[DONE]":
            return [], True
        if not payload:
            return [], False

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            logger.warning("SSE JSON 解析失败：%s", exc)
            return [{"type": "error", "message": str(exc)}], False
        if not isinstance(data, dict):
            return [{"type": "error", "message": "SSE data frame is not an object"}], False

        provider_error = data.get("error")
        if provider_error:
            if isinstance(provider_error, dict):
                message = provider_error.get("message") or json.dumps(
                    provider_error, ensure_ascii=False
                )
            else:
                message = str(provider_error)
            return [{"type": "error", "message": str(message)}], False

        # Usage accounting travels in the frame body, not inside a choice.  Streaming
        # gateways typically emit it in a terminal frame whose ``choices`` list is
        # empty, so it must be surfaced *before* the choices-empty early return.
        usage_event: dict[str, Any] | None = None
        usage_payload = data.get("usage")
        if isinstance(usage_payload, dict) and usage_payload:
            usage_event = {"type": "usage", "usage": usage_payload}

        choices = data.get("choices") or []
        if not isinstance(choices, list) or not choices:
            if usage_event is not None:
                return [usage_event], False
            return [], False
        choice = choices[0]
        if not isinstance(choice, dict):
            return [{"type": "error", "message": "SSE choice is not an object"}], False

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            # A handful of compatible gateways stream a ``message`` object instead of
            # ``delta``. Normalizing it here keeps that dialect out of agent code.
            delta = choice.get("message")
        if not isinstance(delta, dict):
            delta = {}

        events: list[dict[str, Any]] = []
        content = cls._content_text(delta.get("content"))
        if content:
            events.append({"type": "delta", "content": content})

        # [v1.0.23.3] 推理流透传：OpenAI 兼容推理模型的标准字段，此前被静默丢弃
        reasoning = cls._content_text(delta.get("reasoning_content"))
        if reasoning:
            events.append({"type": "reasoning_delta", "content": reasoning})

        raw_calls = delta.get("tool_calls")
        if isinstance(raw_calls, dict):
            raw_calls = [raw_calls]
        has_native_calls = isinstance(raw_calls, list) and bool(raw_calls)
        if isinstance(raw_calls, list):
            for fallback_index, call in enumerate(raw_calls):
                if not isinstance(call, dict):
                    events.append({
                        "type": "error",
                        "message": "tool_calls contains a non-object entry",
                    })
                    continue
                normalized = dict(call)
                if not isinstance(normalized.get("index"), int):
                    normalized["index"] = fallback_index
                events.append({"type": "tool_call", "tool_call": normalized})

        # Legacy OpenAI-compatible dialect: ``function_call`` instead of
        # ``tool_calls``. It is still control-plane data and is normalized at the
        # provider boundary, not allowed to fall through as text. If a gateway sends
        # both fields, the modern field wins so the same call is not executed twice.
        legacy = delta.get("function_call")
        if isinstance(legacy, dict) and not has_native_calls:
            events.append({
                "type": "tool_call",
                "tool_call": {
                    "index": 0,
                    "id": str(delta.get("tool_call_id") or "legacy_call_0"),
                    "type": "function",
                    "function": {
                        "name": legacy.get("name") or "",
                        "arguments": legacy.get("arguments") or "",
                    },
                },
            })

        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            events.append({"type": "finish", "reason": finish_reason})
        if usage_event is not None:
            events.append(usage_event)
        return events, False

    @staticmethod
    def _content_text(content: Any) -> str:
        """Normalize string and content-part dialects to text without stringifying data."""
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, dict):
                text = text.get("value")
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(part.get("content"), str):
                parts.append(part["content"])
        return "".join(parts)


__all__ = ["ProviderClient", "ClientFactory", "build_http_timeout"]
