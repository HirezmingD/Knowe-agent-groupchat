"""Encrypted, crash-safe persistence for :mod:`backend.runtime_settings`.

Only the in-process state contains plaintext credentials.  The disk projection
replaces every model binding's ``api_key`` with a purpose-bound protected envelope
and protects the fingerprint HMAC salt as well.  A validated encrypted backup is
maintained beside the primary file.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .secret_protection import SecretProtectionError, SecretProtector


STORAGE_FORMAT_VERSION = 2
STORAGE_METADATA_FIELD = "settings_storage"
PROTECTED_API_KEY_FIELD = "api_key_protected"
PROTECTED_FINGERPRINT_SALT_FIELD = "fingerprint_salt_protected"
_MAX_SETTINGS_BYTES = 8 * 1024 * 1024
_LEGACY_TEMP_SUFFIX = ".json.tmp"
_PROTECTED_STATE_DIGEST_FIELD = "state_digest_protected"
_KNOWN_STATE_FIELDS = frozenset({
    "settings_revision",
    "fingerprint_salt",
    "active_model_fingerprint",
    "user_name",
    "language",
    "main_model",
    "aux_model",
    "agent_models",
    "approval_timeout_s",
    "group_approval_timeouts",
})


class SettingsStorageError(RuntimeError):
    """A storage failure whose string form is safe for logs and HTTP errors."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SettingsStorageUnavailable(SettingsStorageError):
    """Neither the primary settings file nor its encrypted backup was usable."""


@dataclass(frozen=True)
class LoadedSettings:
    value: dict[str, Any] | None
    needs_migration: bool = False
    recovered_from_backup: bool = False


def backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _json_clone(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise SettingsStorageError("settings_state_not_json_serializable") from exc
    if not isinstance(result, dict):
        raise SettingsStorageError("settings_state_invalid")
    return result


def _binding_slots(value: dict[str, Any]):
    for field in ("main_model", "aux_model"):
        binding = value.get(field)
        if isinstance(binding, dict):
            yield field, binding
    agents = value.get("agent_models")
    if isinstance(agents, dict):
        for key, binding in agents.items():
            if isinstance(binding, dict):
                yield f"agent_models/{key}", binding


def _canonical_plain_state(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize optional secret fields exactly as decode_settings returns them."""

    result = _json_clone(value)
    result.pop(STORAGE_METADATA_FIELD, None)
    for _, binding in _binding_slots(result):
        binding.pop(PROTECTED_API_KEY_FIELD, None)
        raw = binding.get("api_key", "")
        binding["api_key"] = "" if raw is None else raw
    result.pop(PROTECTED_FINGERPRINT_SALT_FIELD, None)
    salt = result.get("fingerprint_salt", "")
    result["fingerprint_salt"] = "" if salt is None else salt
    return result


def _looks_like_settings(value: Mapping[str, Any]) -> bool:
    return any(field in value for field in _KNOWN_STATE_FIELDS)


def _state_digest(value: Mapping[str, Any]) -> str:
    candidate = _json_clone(value)
    metadata = candidate.get(STORAGE_METADATA_FIELD)
    if isinstance(metadata, dict):
        metadata.pop(_PROTECTED_STATE_DIGEST_FIELD, None)
    canonical = json.dumps(
        candidate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _purpose(slot: str, field: str) -> bytes:
    return f"runtime-settings/{slot}/{field}/v1".encode("utf-8")


def _protect_text(
    value: str,
    *,
    protector: SecretProtector,
    purpose: bytes,
) -> dict[str, Any]:
    try:
        envelope = protector.protect(value.encode("utf-8"), purpose=purpose)
        restored = protector.unprotect(envelope, purpose=purpose).decode("utf-8")
    except (SecretProtectionError, UnicodeError) as exc:
        code = getattr(exc, "code", "secret_roundtrip_failed")
        raise SettingsStorageError(str(code)) from exc
    if restored != value:
        raise SettingsStorageError("secret_roundtrip_mismatch")
    return dict(envelope)


def _unprotect_text(
    envelope: Any,
    *,
    protector: SecretProtector,
    purpose: bytes,
) -> str:
    if not isinstance(envelope, Mapping):
        raise SettingsStorageError("secret_envelope_invalid")
    try:
        return protector.unprotect(envelope, purpose=purpose).decode("utf-8")
    except (SecretProtectionError, UnicodeError) as exc:
        code = getattr(exc, "code", "secret_decode_failed")
        raise SettingsStorageError(str(code)) from exc


def encode_settings(value: Mapping[str, Any], protector: SecretProtector) -> dict[str, Any]:
    """Create the encrypted on-disk projection of a plaintext in-memory state."""

    result = _json_clone(value)
    result[STORAGE_METADATA_FIELD] = {
        "version": STORAGE_FORMAT_VERSION,
        "credential_protection": protector.scheme,
    }

    for slot, binding in _binding_slots(result):
        # A mixed in-memory representation is a programming error.  Silently retaining
        # an old envelope could persist a credential different from the active state.
        binding.pop(PROTECTED_API_KEY_FIELD, None)
        raw = binding.pop("api_key", "")
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            raise SettingsStorageError("settings_api_key_invalid")
        if raw:
            binding[PROTECTED_API_KEY_FIELD] = _protect_text(
                raw,
                protector=protector,
                purpose=_purpose(slot, "api-key"),
            )

    result.pop(PROTECTED_FINGERPRINT_SALT_FIELD, None)
    salt = result.pop("fingerprint_salt", "")
    if salt is None:
        salt = ""
    if not isinstance(salt, str):
        raise SettingsStorageError("settings_fingerprint_salt_invalid")
    if salt:
        result[PROTECTED_FINGERPRINT_SALT_FIELD] = _protect_text(
            salt,
            protector=protector,
            purpose=_purpose("root", "fingerprint-salt"),
        )
    # Authenticate the complete encrypted projection (provider/model/base URL included),
    # not merely the ciphertext fields.  A bare checksum would let an offline attacker
    # redirect a still-decryptable key to an attacker-controlled endpoint and recompute
    # the checksum.  DPAPI's integrity protection makes the digest non-forgeable outside
    # the current Windows user context.
    result[STORAGE_METADATA_FIELD][_PROTECTED_STATE_DIGEST_FIELD] = _protect_text(
        _state_digest(result),
        protector=protector,
        purpose=_purpose("root", "state-digest"),
    )
    return result


def decode_settings(
    value: Mapping[str, Any], protector: SecretProtector,
) -> tuple[dict[str, Any], bool]:
    """Decode one primary/backup document and report whether it is plaintext v1."""

    result = _json_clone(value)
    if not _looks_like_settings(result):
        raise SettingsStorageError("settings_document_unrecognized")
    metadata = result.pop(STORAGE_METADATA_FIELD, None)
    if metadata is None:
        # Legacy v1 files contain plaintext api_key fields.  They are accepted only long
        # enough for runtime_settings to normalize and atomically migrate them.
        if any(PROTECTED_API_KEY_FIELD in binding for _, binding in _binding_slots(result)):
            raise SettingsStorageError("settings_storage_metadata_missing")
        if PROTECTED_FINGERPRINT_SALT_FIELD in result:
            raise SettingsStorageError("settings_storage_metadata_missing")
        return result, True
    if not isinstance(metadata, Mapping):
        raise SettingsStorageError("settings_storage_metadata_invalid")
    if metadata.get("version") != STORAGE_FORMAT_VERSION:
        raise SettingsStorageError("settings_storage_version_unsupported")
    if metadata.get("credential_protection") != protector.scheme:
        raise SettingsStorageError("settings_storage_scheme_mismatch")
    protected_digest = metadata.get(_PROTECTED_STATE_DIGEST_FIELD)
    expected_digest = _unprotect_text(
        protected_digest,
        protector=protector,
        purpose=_purpose("root", "state-digest"),
    )
    if len(expected_digest) != 64 or not hmac.compare_digest(
        expected_digest, _state_digest(value),
    ):
        raise SettingsStorageError("settings_integrity_check_failed")

    for slot, binding in _binding_slots(result):
        plaintext = binding.pop("api_key", None)
        if plaintext not in (None, ""):
            raise SettingsStorageError("settings_mixed_plaintext_secret")
        envelope = binding.pop(PROTECTED_API_KEY_FIELD, None)
        binding["api_key"] = (
            _unprotect_text(
                envelope,
                protector=protector,
                purpose=_purpose(slot, "api-key"),
            )
            if envelope is not None else ""
        )

    plaintext_salt = result.pop("fingerprint_salt", None)
    if plaintext_salt not in (None, ""):
        raise SettingsStorageError("settings_mixed_plaintext_secret")
    salt_envelope = result.pop(PROTECTED_FINGERPRINT_SALT_FIELD, None)
    if salt_envelope is not None:
        result["fingerprint_salt"] = _unprotect_text(
            salt_envelope,
            protector=protector,
            purpose=_purpose("root", "fingerprint-salt"),
        )
    else:
        result["fingerprint_salt"] = ""
    return result, False


def _serialize(value: Mapping[str, Any]) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SettingsStorageError("settings_serialize_failed") from exc


def _read_document(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SettingsStorageError("settings_read_failed") from exc
    if not raw or len(raw) > _MAX_SETTINGS_BYTES:
        raise SettingsStorageError("settings_file_size_invalid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise SettingsStorageError("settings_json_invalid") from exc
    if not isinstance(value, dict):
        raise SettingsStorageError("settings_document_invalid")
    return value, raw


def _fsync_directory(path: Path) -> None:
    # Windows does not allow opening a directory this way.  The file itself is always
    # fsynced; directory durability is an additional best effort on supporting systems.
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes) -> None:
    if len(payload) > _MAX_SETTINGS_BYTES:
        raise SettingsStorageError("settings_file_size_invalid")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SettingsStorageError("settings_directory_create_failed") from exc
    temp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            stat.S_IRUSR | stat.S_IWUSR,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(temp, path)
        _fsync_directory(path.parent)
    except SettingsStorageError:
        raise
    except OSError as exc:
        raise SettingsStorageError("settings_atomic_write_failed") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _validated_encrypted_bytes(
    value: Mapping[str, Any], protector: SecretProtector,
) -> bytes:
    encoded = encode_settings(value, protector)
    payload = _serialize(encoded)
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:  # pragma: no cover - own serializer invariant
        raise SettingsStorageError("settings_roundtrip_invalid") from exc
    decoded, legacy = decode_settings(parsed, protector)
    if legacy or decoded != _canonical_plain_state(value):
        raise SettingsStorageError("settings_roundtrip_mismatch")
    return payload


def load_settings(path: Path, protector: SecretProtector) -> LoadedSettings:
    """Load primary settings, restoring a valid encrypted backup when necessary."""

    if not path.exists():
        backup = backup_path(path)
        if not backup.exists():
            return LoadedSettings(None)
        try:
            document, raw = _read_document(backup)
            value, legacy = decode_settings(document, protector)
            if legacy:
                raise SettingsStorageError("settings_backup_not_encrypted")
            _atomic_write(path, raw)
            return LoadedSettings(value, recovered_from_backup=True)
        except SettingsStorageError as backup_error:
            raise SettingsStorageUnavailable(
                "settings_primary_and_backup_unavailable",
            ) from backup_error
    try:
        document, _raw = _read_document(path)
        value, legacy = decode_settings(document, protector)
        return LoadedSettings(value, needs_migration=legacy)
    except SettingsStorageError as primary_error:
        backup = backup_path(path)
        if not backup.exists():
            raise SettingsStorageUnavailable("settings_primary_and_backup_unavailable") from primary_error
        try:
            document, raw = _read_document(backup)
            value, legacy = decode_settings(document, protector)
            # A plaintext backup would perpetuate the exact exposure this module is
            # intended to eliminate.  Only a validated v2 backup is recoverable.
            if legacy:
                raise SettingsStorageError("settings_backup_not_encrypted")
            _atomic_write(path, raw)
            return LoadedSettings(value, recovered_from_backup=True)
        except SettingsStorageError as backup_error:
            raise SettingsStorageUnavailable(
                "settings_primary_and_backup_unavailable",
            ) from backup_error


def save_settings(
    path: Path,
    value: Mapping[str, Any],
    protector: SecretProtector,
) -> None:
    """Persist a candidate to two encrypted, independently replaceable slots.

    The backup is a second copy of the *current* candidate, not credential
    history.  Retaining the previous projection made an explicit key clear or
    rotation reversible after primary-file corruption.  Publishing the backup
    first preserves fail-safe recovery: until the primary replacement succeeds,
    the old valid primary remains authoritative; after success both slots contain
    the new state.
    """

    new_payload = _validated_encrypted_bytes(value, protector)
    if path.exists():
        # Refuse to overwrite a primary that cannot first be authenticated.  A
        # caller must recover it through load_settings rather than accidentally
        # replacing evidence of a credential-store failure.
        old_document, _old_raw = _read_document(path)
        decode_settings(old_document, protector)
    # Publish the encrypted recovery slot before the authoritative primary.  If
    # the second replacement fails, the still-valid old primary wins on reload;
    # if the primary was absent, load_settings recovers this new candidate.
    _atomic_write(backup_path(path), new_payload)
    _atomic_write(path, new_payload)

    # v1 used this fixed temporary name and could leave plaintext behind after a crash.
    # Remove only that exact known sibling, and only after the encrypted primary exists.
    legacy_temp = path.with_suffix(_LEGACY_TEMP_SUFFIX)
    try:
        legacy_temp.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "LoadedSettings",
    "PROTECTED_API_KEY_FIELD",
    "PROTECTED_FINGERPRINT_SALT_FIELD",
    "STORAGE_FORMAT_VERSION",
    "STORAGE_METADATA_FIELD",
    "SettingsStorageError",
    "SettingsStorageUnavailable",
    "backup_path",
    "decode_settings",
    "encode_settings",
    "load_settings",
    "save_settings",
]
