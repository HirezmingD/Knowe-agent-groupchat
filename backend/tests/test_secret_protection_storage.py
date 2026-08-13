from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# Match the backend test harness: knowe_core is a top-level package rooted at backend/.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if "knowe_core" not in sys.modules:
    # runtime_settings needs only provider_identity.  Avoid importing the aggregate
    # knowe_core package (and optional httpx) when this file is run with stdlib unittest.
    core_package = types.ModuleType("knowe_core")
    core_package.__path__ = [str(BACKEND_ROOT / "knowe_core")]  # type: ignore[attr-defined]
    sys.modules["knowe_core"] = core_package

from backend.config import CONFIG
from backend import runtime_settings
from backend.secret_protection import (
    SecretProtectionError,
    SecretProtectionUnavailable,
    WindowsDpapiProtector,
)
from backend.settings_storage import (
    PROTECTED_API_KEY_FIELD,
    PROTECTED_FINGERPRINT_SALT_FIELD,
    STORAGE_METADATA_FIELD,
    SettingsStorageError,
    SettingsStorageUnavailable,
    backup_path,
    decode_settings,
    load_settings,
    save_settings,
)


class FakeUserProtector:
    """Authenticated test protector; never imported by production code."""

    scheme = "test-current-user-v1"

    def __init__(self, identity: str = "alice", *, fail_protect: bool = False) -> None:
        self.identity = identity.encode("utf-8")
        self.fail_protect = fail_protect

    def _key(self, purpose: bytes) -> bytes:
        return hashlib.sha256(self.identity + b"\0" + purpose).digest()

    def protect(self, plaintext: bytes, *, purpose: bytes) -> dict[str, object]:
        if self.fail_protect:
            raise SecretProtectionError("fake_protect_failed")
        key = self._key(purpose)
        ciphertext = bytes(value ^ key[index % len(key)] for index, value in enumerate(plaintext))
        tag = hmac.new(key, ciphertext, hashlib.sha256).digest()
        return {
            "version": 1,
            "scheme": self.scheme,
            "ciphertext": base64.b64encode(tag + ciphertext).decode("ascii"),
        }

    def unprotect(self, envelope, *, purpose: bytes) -> bytes:
        if envelope.get("version") != 1 or envelope.get("scheme") != self.scheme:
            raise SecretProtectionError("fake_envelope_invalid")
        try:
            payload = base64.b64decode(envelope["ciphertext"], validate=True)
        except Exception as exc:  # noqa: BLE001 - deliberately malformed fixture
            raise SecretProtectionError("fake_envelope_invalid") from exc
        tag, ciphertext = payload[:32], payload[32:]
        key = self._key(purpose)
        if not hmac.compare_digest(tag, hmac.new(key, ciphertext, hashlib.sha256).digest()):
            raise SecretProtectionError("fake_unprotect_failed")
        return bytes(value ^ key[index % len(key)] for index, value in enumerate(ciphertext))


def binding(key: str, *, provider: str = "deepseek", model: str = "deepseek-chat") -> dict[str, str]:
    return {
        "provider": provider,
        "model": model,
        "api_key": key,
        "base_url": "https://api.example.test/v1",
        "transport": "openai_chat",
    }


class WindowsDpapiTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration test")
    def test_current_user_roundtrip_and_purpose_isolation(self) -> None:
        protector = WindowsDpapiProtector()
        secret = b"key-with-arbitrary-format_+/="
        envelope = protector.protect(secret, purpose=b"tests/main/api-key")

        self.assertNotIn(secret.decode("ascii"), str(envelope))
        self.assertEqual(
            WindowsDpapiProtector().unprotect(envelope, purpose=b"tests/main/api-key"),
            secret,
        )
        with self.assertRaisesRegex(SecretProtectionError, "dpapi_unprotect_failed"):
            protector.unprotect(envelope, purpose=b"tests/aux/api-key")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration test")
    def test_dpapi_protects_complete_settings_file_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            state = {
                "settings_revision": 1,
                "fingerprint_salt": "salt-SECRET",
                "main_model": binding("api-SECRET"),
                "aux_model": None,
                "agent_models": {},
            }
            save_settings(path, state, WindowsDpapiProtector())
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("api-SECRET", raw)
            self.assertNotIn("salt-SECRET", raw)
            self.assertEqual(
                load_settings(path, WindowsDpapiProtector()).value,
                state,
            )

    @unittest.skipIf(os.name == "nt", "non-Windows fail-closed test")
    def test_non_windows_has_no_plaintext_fallback(self) -> None:
        with self.assertRaises(SecretProtectionUnavailable):
            WindowsDpapiProtector()


class RuntimeSettingsStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "settings.json"
        self.protector = FakeUserProtector()

        self.old_data_dir = CONFIG.data_dir
        self.old_state = json.loads(json.dumps(runtime_settings._state))  # noqa: SLF001
        self.old_loaded = runtime_settings._loaded  # noqa: SLF001
        self.old_load_error = runtime_settings._load_error  # noqa: SLF001
        self.old_override = runtime_settings._secret_protector_override  # noqa: SLF001

        object.__setattr__(CONFIG, "data_dir", str(self.root))
        self._reset_runtime(self.protector)

    def tearDown(self) -> None:
        runtime_settings._state.clear()  # noqa: SLF001
        runtime_settings._state.update(self.old_state)  # noqa: SLF001
        runtime_settings._loaded = self.old_loaded  # noqa: SLF001
        runtime_settings._load_error = self.old_load_error  # noqa: SLF001
        runtime_settings._secret_protector_override = self.old_override  # noqa: SLF001
        object.__setattr__(CONFIG, "data_dir", self.old_data_dir)
        self.temp.cleanup()

    @staticmethod
    def _state_with_keys(main_key: str = "main-SECRET") -> dict[str, object]:
        value = runtime_settings._default_state()  # noqa: SLF001
        value.update({
            "main_model": binding(main_key),
            "aux_model": binding("aux-SECRET", model="cheap-model"),
            "agent_models": {
                "project-1::worker-1": binding("agent-SECRET", model="worker-model"),
            },
        })
        return value

    @staticmethod
    def _assert_disk_has_no_plaintext(path: Path, *secrets_: str) -> dict[str, object]:
        text = path.read_text(encoding="utf-8")
        for secret in secrets_:
            if secret:
                assert secret not in text
        value = json.loads(text)
        assert STORAGE_METADATA_FIELD in value
        assert PROTECTED_FINGERPRINT_SALT_FIELD in value
        for field in ("main_model", "aux_model"):
            if isinstance(value.get(field), dict):
                assert "api_key" not in value[field]
                assert PROTECTED_API_KEY_FIELD in value[field]
        for model in (value.get("agent_models") or {}).values():
            assert "api_key" not in model
            assert PROTECTED_API_KEY_FIELD in model
        return value

    def _reset_runtime(self, protector: FakeUserProtector) -> None:
        runtime_settings._state.clear()  # noqa: SLF001
        runtime_settings._state.update(runtime_settings._default_state())  # noqa: SLF001
        runtime_settings._loaded = False  # noqa: SLF001
        runtime_settings._load_error = None  # noqa: SLF001
        runtime_settings._secret_protector_override = protector  # noqa: SLF001

    def test_all_binding_keys_and_fingerprint_salt_are_encrypted_and_reload(self) -> None:
        expected = self._state_with_keys()
        save_settings(self.path, expected, self.protector)

        self._assert_disk_has_no_plaintext(
            self.path, "main-SECRET", "aux-SECRET", "agent-SECRET",
            str(expected["fingerprint_salt"]),
        )
        loaded = load_settings(self.path, self.protector)
        self.assertFalse(loaded.needs_migration)
        self.assertEqual(loaded.value, expected)

    def test_plaintext_v1_migrates_to_encrypted_primary_and_backup(self) -> None:
        legacy = self._state_with_keys()
        legacy_bytes = json.dumps(legacy, ensure_ascii=False, indent=2).encode("utf-8")
        self.path.write_bytes(legacy_bytes)
        old_fixed_temp = self.path.with_suffix(".json.tmp")
        old_fixed_temp.write_text("main-SECRET", encoding="utf-8")

        loaded = runtime_settings.snapshot()

        self.assertEqual(loaded["main_model"]["api_key"], "main-SECRET")
        self.assertFalse(old_fixed_temp.exists())
        for candidate in (self.path, backup_path(self.path)):
            self.assertTrue(candidate.exists())
            self._assert_disk_has_no_plaintext(
                candidate, "main-SECRET", "aux-SECRET", "agent-SECRET",
                str(legacy["fingerprint_salt"]),
            )

    def test_migration_protection_failure_leaves_plaintext_source_untouched(self) -> None:
        legacy = self._state_with_keys()
        original = json.dumps(legacy, ensure_ascii=False, indent=2).encode("utf-8")
        self.path.write_bytes(original)
        self._reset_runtime(FakeUserProtector(fail_protect=True))

        with self.assertRaisesRegex(SettingsStorageError, "fake_protect_failed"):
            runtime_settings.snapshot()

        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(backup_path(self.path).exists())
        self.assertFalse(runtime_settings._loaded)  # noqa: SLF001

    def test_corrupt_primary_recovers_current_encrypted_backup(self) -> None:
        first = self._state_with_keys("first-SECRET")
        second = self._state_with_keys("second-SECRET")
        save_settings(self.path, first, self.protector)
        save_settings(self.path, second, self.protector)
        expected_backup = backup_path(self.path).read_bytes()
        self.path.write_bytes(b"not json")
        self._reset_runtime(self.protector)

        loaded = runtime_settings.snapshot()

        self.assertEqual(loaded["main_model"]["api_key"], "second-SECRET")
        self.assertEqual(self.path.read_bytes(), expected_backup)
        self._assert_disk_has_no_plaintext(self.path, "second-SECRET")

    def test_missing_primary_recovers_encrypted_backup(self) -> None:
        first = self._state_with_keys("first-SECRET")
        second = self._state_with_keys("second-SECRET")
        save_settings(self.path, first, self.protector)
        save_settings(self.path, second, self.protector)
        expected_backup = backup_path(self.path).read_bytes()
        self.path.unlink()
        self._reset_runtime(self.protector)

        loaded = runtime_settings.snapshot()

        self.assertEqual(loaded["main_model"]["api_key"], "second-SECRET")
        self.assertEqual(self.path.read_bytes(), expected_backup)
        self._assert_disk_has_no_plaintext(self.path, "second-SECRET")

    def test_primary_and_backup_corruption_fail_closed_without_overwrite(self) -> None:
        self.path.write_bytes(b"bad primary")
        backup_path(self.path).write_bytes(b"bad backup")
        primary_before = self.path.read_bytes()
        backup_before = backup_path(self.path).read_bytes()

        with self.assertRaisesRegex(
            SettingsStorageUnavailable, "settings_primary_and_backup_unavailable",
        ):
            runtime_settings.snapshot()
        with self.assertRaises(SettingsStorageUnavailable):
            runtime_settings.snapshot()

        self.assertFalse(runtime_settings._loaded)  # noqa: SLF001
        self.assertIsNone(runtime_settings._state["main_model"])  # noqa: SLF001
        self.assertEqual(self.path.read_bytes(), primary_before)
        self.assertEqual(backup_path(self.path).read_bytes(), backup_before)

    def test_different_user_identity_cannot_decrypt_primary_or_backup(self) -> None:
        save_settings(self.path, self._state_with_keys("first-SECRET"), self.protector)
        save_settings(self.path, self._state_with_keys("second-SECRET"), self.protector)
        primary_before = self.path.read_bytes()
        backup_before = backup_path(self.path).read_bytes()
        self._reset_runtime(FakeUserProtector("bob"))

        with self.assertRaises(SettingsStorageUnavailable):
            runtime_settings.snapshot()

        self.assertEqual(self.path.read_bytes(), primary_before)
        self.assertEqual(backup_path(self.path).read_bytes(), backup_before)

    def test_failed_primary_replacement_keeps_old_primary_and_encrypted_backup(self) -> None:
        first = self._state_with_keys("first-SECRET")
        second = self._state_with_keys("second-SECRET")
        save_settings(self.path, first, self.protector)
        primary_before = self.path.read_bytes()

        from backend import settings_storage

        real_atomic_write = settings_storage._atomic_write  # noqa: SLF001

        def fail_primary(target: Path, payload: bytes) -> None:
            if target == self.path:
                raise SettingsStorageError("injected_primary_write_failure")
            real_atomic_write(target, payload)

        with patch.object(settings_storage, "_atomic_write", side_effect=fail_primary):
            with self.assertRaisesRegex(SettingsStorageError, "injected_primary_write_failure"):
                save_settings(self.path, second, self.protector)

        self.assertEqual(self.path.read_bytes(), primary_before)
        # The new recovery slot may be published before the primary replacement;
        # the old primary remains authoritative because the save reported failure.
        backup_loaded = load_settings(backup_path(self.path), self.protector)
        self.assertEqual(backup_loaded.value, second)
        self._assert_disk_has_no_plaintext(backup_path(self.path), "second-SECRET")

    def test_apply_preserves_same_scope_key_and_explicit_clear_removes_it(self) -> None:
        runtime_settings.apply({"main_model": binding("kept-SECRET")})
        without_key = binding("")
        without_key.pop("api_key")
        runtime_settings.apply({"main_model": without_key})
        self.assertEqual(runtime_settings.snapshot()["main_model"]["api_key"], "kept-SECRET")

        cleared = dict(without_key)
        cleared["clear_api_key"] = True
        runtime_settings.apply({"main_model": cleared})
        self.assertEqual(runtime_settings.snapshot()["main_model"]["api_key"], "")

        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn(PROTECTED_API_KEY_FIELD, document["main_model"])
        self.assertNotIn("kept-SECRET", self.path.read_text(encoding="utf-8"))
        backup_document = json.loads(backup_path(self.path).read_text(encoding="utf-8"))
        self.assertNotIn(PROTECTED_API_KEY_FIELD, backup_document["main_model"])
        recovered = load_settings(backup_path(self.path), self.protector)
        self.assertEqual(recovered.value["main_model"]["api_key"], "")

    def test_malformed_envelope_is_rejected_without_ciphertext_in_error(self) -> None:
        state = self._state_with_keys()
        save_settings(self.path, state, self.protector)
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["main_model"][PROTECTED_API_KEY_FIELD]["ciphertext"] = "TOP-SECRET-NOT-BASE64"
        self.path.write_text(json.dumps(document), encoding="utf-8")
        backup_path(self.path).write_text(json.dumps(document), encoding="utf-8")

        with self.assertRaises(SettingsStorageUnavailable) as caught:
            load_settings(self.path, self.protector)
        self.assertEqual(str(caught.exception), "settings_primary_and_backup_unavailable")
        self.assertNotIn("TOP-SECRET", str(caught.exception))

    def test_base_url_tampering_fails_even_if_attacker_recomputes_bare_sha(self) -> None:
        save_settings(self.path, self._state_with_keys(), self.protector)
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["main_model"]["base_url"] = "https://attacker.invalid/v1"

        # Simulate the old, insufficient design: the attacker can always recompute an
        # unkeyed digest.  The DPAPI-protected state digest must remain authoritative.
        candidate = json.loads(json.dumps(document))
        candidate[STORAGE_METADATA_FIELD].pop("state_digest_protected", None)
        document[STORAGE_METADATA_FIELD]["state_sha256"] = hashlib.sha256(
            json.dumps(
                candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8"),
        ).hexdigest()
        self.path.write_text(json.dumps(document), encoding="utf-8")
        backup_path(self.path).write_text(json.dumps(document), encoding="utf-8")

        with self.assertRaisesRegex(
            SettingsStorageUnavailable, "settings_primary_and_backup_unavailable",
        ):
            load_settings(self.path, self.protector)


if __name__ == "__main__":
    unittest.main()
