from __future__ import annotations

import os
import sys
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

import pytest

# Exercise the real source packages.  Deleted Worker modules are deliberately not
# aliased: stale imports must fail at collection time instead of being hidden.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import CONFIG, Config  # noqa: E402
from backend import runtime_settings  # noqa: E402


def _clean_config_baseline() -> dict[str, object]:
    # Config fields use environment-backed default factories.  Instantiate once with an
    # empty environment so a developer's .env cannot silently steer the test suite.
    with patch.dict(os.environ, {}, clear=True):
        clean = Config()
    baseline = {field.name: getattr(clean, field.name) for field in fields(clean)}
    # Tests use pure in-memory persistence and the deterministic fake provider unless a
    # case explicitly opts into another mode.
    baseline.update({
        "agent": "fake",
        "deepseek_api_key": "",
        "kickoff": False,
        "data_dir": "",
        "runtime_token": "0123456789abcdef" * 4,
        "provider_max_retries": 0,
        "fake_delta_delay_s": 0.0,
        "fake_think_delay_s": 0.0,
        "fake_work_delay_s": 0.0,
    })
    return baseline


TEST_BASELINE = _clean_config_baseline()


def _reset_runtime_settings() -> None:
    # runtime_settings is an intentional process-local authority.  Tests must reset it
    # just like CONFIG, otherwise a model applied by one case can make a later case call
    # the real network with stale credentials.
    for waiter in tuple(runtime_settings._model_waiters):  # noqa: SLF001
        waiter.set()
    runtime_settings._model_waiters.clear()  # noqa: SLF001
    runtime_settings._state.clear()  # noqa: SLF001
    runtime_settings._state.update(runtime_settings._default_state())  # noqa: SLF001
    runtime_settings._loaded = True  # noqa: SLF001


@pytest.fixture(autouse=True)
def _restore_config_baseline():
    for name, value in TEST_BASELINE.items():
        object.__setattr__(CONFIG, name, value)
    _reset_runtime_settings()
    yield
    _reset_runtime_settings()
    for name, value in TEST_BASELINE.items():
        object.__setattr__(CONFIG, name, value)
