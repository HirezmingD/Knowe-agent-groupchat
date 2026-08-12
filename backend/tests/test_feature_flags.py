# [v1.0.13][R1][R2][R3][R4] Backend feature-flag default/override tests.
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.feature_flags import FeatureFlag, enabled, snapshot


class FeatureFlagTest(unittest.TestCase):
    def test_all_v1013_flags_default_on(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(all(snapshot().values()))

    def test_exact_and_prefixed_overrides_are_strict(self) -> None:
        flag = FeatureFlag.COMPLETION_VIEW_V1
        with patch.dict(os.environ, {flag.value: "off"}, clear=True):
            self.assertFalse(enabled(flag))
        with patch.dict(os.environ, {f"KNOWE_{flag.value}": "0"}, clear=True):
            self.assertFalse(enabled(flag))
        with patch.dict(os.environ, {flag.value: "ambiguous"}, clear=True):
            self.assertTrue(enabled(flag))


if __name__ == "__main__":
    unittest.main()
