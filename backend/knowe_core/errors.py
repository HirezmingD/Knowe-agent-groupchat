# knowe v0.6 — Harness 核心引擎
"""
Knowe Agent Core — typed exceptions.

All Knowe-specific exceptions inherit from KnoweError.  Provider exceptions also carry
small, non-secret retry diagnostics so the Worker can surface a useful terminal message
instead of an empty ``Exception.__str__`` value.
"""

from __future__ import annotations

from knowe_core.redaction import redact_sensitive_text


class KnoweError(Exception):
    """Base exception for Knowe Agent Core."""


# ── Provider errors ──

class ProviderError(KnoweError):
    """Provider-level error (network, auth, rate limit, or malformed response)."""

    default_retryable = False

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
        *,
        retryable: bool | None = None,
        attempts: int = 0,
        max_attempts: int = 0,
        cause_type: str = "",
    ) -> None:
        # Some httpx/httpcore exceptions intentionally have an empty ``str(exc)``.  Never
        # let that erase the only diagnostic that reaches a non-technical user.
        normalized = redact_sensitive_text(message).strip() or self.__class__.__name__
        super().__init__(normalized)
        self.message = normalized
        self.status_code = status_code
        self.response_body = (
            redact_sensitive_text(response_body, limit=500) if response_body else response_body
        )
        self.retryable = (
            bool(self.default_retryable) if retryable is None else bool(retryable)
        )
        self.attempts = max(0, int(attempts or 0))
        self.max_attempts = max(0, int(max_attempts or 0))
        self.cause_type = str(cause_type or "")

    def with_retry_context(
        self,
        *,
        attempts: int,
        max_attempts: int,
        exhausted: bool = False,
    ) -> "ProviderError":
        """Attach retry diagnostics in place while preserving the concrete exception type."""

        self.attempts = max(0, int(attempts or 0))
        self.max_attempts = max(0, int(max_attempts or 0))
        if (
            exhausted
            and self.retryable
            and self.max_attempts > 1
            and self.attempts >= self.max_attempts
            and "已尝试" not in self.message
        ):
            self.message = (
                f"{self.message}（已尝试 {self.attempts}/{self.max_attempts} 次，仍未恢复）"
            )
            self.args = (self.message,)
        return self


class ProviderAuthError(ProviderError):
    """Authentication failed (401)."""


class ProviderRateLimitError(ProviderError):
    """Rate limited (429). Retry with backoff."""

    default_retryable = True


class ProviderTimeoutError(ProviderError):
    """Request timed out."""

    default_retryable = True


class ProviderConnectionError(ProviderError):
    """Network connection failed or a retryable provider gateway failed."""

    default_retryable = True


class ProviderBadResponseError(ProviderError):
    """Unparseable response from provider."""


# ── Stream errors ──

class StreamError(KnoweError):
    """Error during SSE stream processing."""


class StreamParseError(StreamError):
    """Unparseable SSE line or JSON."""


# ── Agent loop errors ──

class AgentError(KnoweError):
    """Agent-level error."""


class ToolNotFoundError(AgentError):
    """Agent tried to call a tool not in the registry."""

    def __init__(self, tool_name: str):
        super().__init__(f"Tool not found: {tool_name}")
        self.tool_name = tool_name


class MaxIterationsExceeded(AgentError):
    """Legacy AgentLoop hit max_iterations without completing.

    WorkerRuntime does not use this legacy loop or this hard cap.
    """

    def __init__(self, iterations: int):
        super().__init__(f"Max iterations ({iterations}) exceeded")
        self.iterations = iterations


class AgentInterrupted(AgentError):
    """Agent was interrupted via interrupt()."""


# ── Tool execution errors ──

class ToolExecutionError(KnoweError):
    """Tool handler raised an exception."""

    def __init__(self, tool_name: str, original_error: Exception):
        detail = str(original_error or "").strip() or type(original_error).__name__
        super().__init__(f"Tool '{tool_name}' failed: {detail}")
        self.tool_name = tool_name
        self.original_error = original_error


# ── Messages / context errors ──

class MessageError(KnoweError):
    """Error during message assembly or sanitization."""
