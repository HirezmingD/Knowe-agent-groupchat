from __future__ import annotations

import pytest

from backend.aux_client import _http_hint, _parse
from backend.knowe_core.errors import ProviderError
from backend.knowe_core.provider_identity import http_status_error_message
from backend.knowe_core.redaction import redact_sensitive_text


@pytest.mark.parametrize(
    "raw",
    (
        "Authorization: Bearer super-secret-token-value",
        '{"api_key":"super-secret-token-value"}',
        "https://example.test/?access_token=super-secret-token-value",
        "provider echoed sk-example123456789",
        "x-api-key=super-secret-token-value",
    ),
)
def test_common_secret_shapes_are_redacted(raw: str) -> None:
    safe = redact_sensitive_text(raw, secrets=("super-secret-token-value",))
    assert "super-secret-token-value" not in safe
    assert "sk-example123456789" not in safe
    assert "***" in safe


def test_provider_error_never_retains_raw_response_secret() -> None:
    err = ProviderError(
        "provider failed: Bearer super-secret-token-value",
        401,
        '{"error":{"message":"api_key=super-secret-token-value"}}',
    )
    assert "super-secret-token-value" not in str(err)
    assert "super-secret-token-value" not in str(err.response_body)


def test_provider_and_aux_hints_redact_exact_custom_key() -> None:
    key = "totally-custom-credential-value"
    body = '{"error":{"message":"credential totally-custom-credential-value invalid"}}'
    main = http_status_error_message(401, response_body=body, secrets=(key,))
    aux = _http_hint(401, body, "辅助模型", "model", key)
    assert key not in main
    assert key not in aux


def test_malformed_success_payload_cannot_echo_key() -> None:
    key = "another-custom-credential-value"
    with pytest.raises(Exception) as caught:
        _parse({"debug": f"Authorization: Bearer {key}"}, "辅助模型", "model", key)
    assert key not in str(caught.value)
