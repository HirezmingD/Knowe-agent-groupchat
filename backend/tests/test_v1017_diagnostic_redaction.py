"""v1.0.17 · 变更集 G · AC-SEC-1 — diagnostic/audit bundle secret redaction.

``redact_secrets`` is the single canonical scrubber that every diagnostic, audit, or
evidence bundle must run its captured configuration through. This test loads only that
function (and its helpers) from ``runtime_settings.py`` so it runs offline, without the
provider/network dependencies the full settings module would pull in.

Property under test: for any nested config shape, no field whose name denotes a
credential (api_key, authorization, token, cookie, …) survives with a real value; the
non-secret structure is preserved verbatim; and an unset secret stays visibly unset.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND / "backend" / "runtime_settings.py"


def _load_redactor():
    text = SRC.read_text(encoding="utf-8")
    start = text.index("_SECRET_FIELD_NAMES")
    end = text.index("def redacted_snapshot")
    namespace: dict[str, Any] = {"Mapping": Mapping, "Any": Any}
    exec(text[start:end], namespace)  # noqa: S102 — trusted first-party source slice
    return namespace["redact_secrets"], namespace["_REDACTED_PLACEHOLDER"]


def test_secret_fields_are_redacted_everywhere() -> None:
    redact, placeholder = _load_redactor()
    sample = {
        "main_model": {
            "provider": "openai",
            "model": "gpt-x",
            "api_key": "sk-REAL-SECRET-0001",
            "base_url": "http://127.0.0.1:9",
        },
        "bindings": [
            {"authorization": "Bearer TOKEN-XYZ"},
            {"cookie": "session=abc123"},
            {"note": "keep me"},
        ],
        "fingerprint_salt": "salt-value",
    }
    out = redact(sample)
    blob = json.dumps(out, ensure_ascii=False)

    assert out["main_model"]["api_key"] == placeholder
    assert out["bindings"][0]["authorization"] == placeholder
    assert out["bindings"][1]["cookie"] == placeholder
    assert out["fingerprint_salt"] == placeholder
    # Non-secret structure is preserved verbatim.
    assert out["main_model"]["provider"] == "openai"
    assert out["main_model"]["base_url"] == "http://127.0.0.1:9"
    assert out["bindings"][2]["note"] == "keep me"
    # No real credential survives anywhere in the serialized bundle.
    for leaked in ("sk-REAL-SECRET-0001", "Bearer TOKEN-XYZ", "session=abc123", "salt-value"):
        assert leaked not in blob, f"real secret leaked into diagnostic bundle: {leaked}"


def test_unset_secret_stays_unset() -> None:
    redact, placeholder = _load_redactor()
    out = redact({"main_model": {"api_key": "", "provider": "x"}})
    # An empty credential is visibly empty, not masked (so 'unset' remains diagnosable).
    assert out["main_model"]["api_key"] == ""
    assert out["main_model"]["provider"] == "x"


if __name__ == "__main__":
    import sys

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"FAIL  {fn.__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(fns)} total")
    sys.exit(1 if failed else 0)
