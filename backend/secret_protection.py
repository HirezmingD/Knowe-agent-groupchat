"""Small, dependency-free secret-protection primitives for Knowe.

The desktop application is Windows-only.  Provider credentials persisted by the
Python backend are therefore protected with Windows DPAPI in *current user* scope.
No machine-scope flag is used: copying ``settings.json`` to another Windows account
must not make its credentials decryptable.

The protocol is intentionally injectable.  Unit tests running on Linux/macOS use a
test protector instead of weakening production with a plaintext fallback.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import os
from ctypes import wintypes
from functools import lru_cache
from typing import Any, Mapping, Protocol, runtime_checkable


DPAPI_SCHEME = "windows-dpapi-current-user"
PROTECTED_SECRET_VERSION = 1
_MAX_CIPHERTEXT_BYTES = 128 * 1024
_DPAPI_DESCRIPTION = "Knowe protected credential"
_DPAPI_ENTROPY_PREFIX = b"Knowe\x00protected-secret\x00v1\x00"


class SecretProtectionError(RuntimeError):
    """A deliberately non-sensitive credential-protection failure."""

    def __init__(self, code: str, *, winerror: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.winerror = winerror


class SecretProtectionUnavailable(SecretProtectionError):
    """Raised when the production protector cannot be used on this platform."""


@runtime_checkable
class SecretProtector(Protocol):
    """Protect and unprotect bytes for one explicit application purpose."""

    scheme: str

    def protect(self, plaintext: bytes, *, purpose: bytes) -> dict[str, Any]: ...

    def unprotect(self, envelope: Mapping[str, Any], *, purpose: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[Any]]:
    # ``create_string_buffer(b"")`` still creates a one-byte buffer, while cbData=0
    # tells DPAPI that the logical input is empty.  Callers currently protect only
    # non-empty values, but keeping this helper total makes the FFI boundary safer.
    buffer = ctypes.create_string_buffer(data, max(1, len(data)))
    value = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return value, buffer


class WindowsDpapiProtector:
    """Windows DPAPI protector bound to the current user's profile."""

    scheme = DPAPI_SCHEME
    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if os.name != "nt":
            raise SecretProtectionUnavailable("dpapi_unavailable")
        try:
            self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise SecretProtectionUnavailable("dpapi_unavailable") from exc

        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    @staticmethod
    def _entropy(purpose: bytes) -> bytes:
        if not isinstance(purpose, bytes) or not purpose:
            raise SecretProtectionError("secret_purpose_invalid")
        return _DPAPI_ENTROPY_PREFIX + purpose

    def _protect_bytes(self, plaintext: bytes, purpose: bytes) -> bytes:
        source, source_buffer = _blob(plaintext)
        entropy, entropy_buffer = _blob(self._entropy(purpose))
        output = _DataBlob()
        try:
            ok = self._crypt32.CryptProtectData(
                ctypes.byref(source),
                _DPAPI_DESCRIPTION,
                ctypes.byref(entropy),
                None,
                None,
                self._CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output),
            )
            if not ok:
                raise SecretProtectionError(
                    "dpapi_protect_failed", winerror=ctypes.get_last_error(),
                )
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.memset(source_buffer, 0, ctypes.sizeof(source_buffer))
            ctypes.memset(entropy_buffer, 0, ctypes.sizeof(entropy_buffer))
            if output.pbData:
                ctypes.memset(output.pbData, 0, output.cbData)
                self._kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))

    def _unprotect_bytes(self, ciphertext: bytes, purpose: bytes) -> bytes:
        source, source_buffer = _blob(ciphertext)
        entropy, entropy_buffer = _blob(self._entropy(purpose))
        output = _DataBlob()
        description = wintypes.LPWSTR()
        try:
            ok = self._crypt32.CryptUnprotectData(
                ctypes.byref(source),
                ctypes.byref(description),
                ctypes.byref(entropy),
                None,
                None,
                self._CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output),
            )
            if not ok:
                raise SecretProtectionError(
                    "dpapi_unprotect_failed", winerror=ctypes.get_last_error(),
                )
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.memset(source_buffer, 0, ctypes.sizeof(source_buffer))
            ctypes.memset(entropy_buffer, 0, ctypes.sizeof(entropy_buffer))
            if output.pbData:
                ctypes.memset(output.pbData, 0, output.cbData)
                self._kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
            if description:
                self._kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))

    def protect(self, plaintext: bytes, *, purpose: bytes) -> dict[str, Any]:
        if not isinstance(plaintext, bytes):
            raise SecretProtectionError("secret_plaintext_invalid")
        ciphertext = self._protect_bytes(plaintext, purpose)
        return {
            "version": PROTECTED_SECRET_VERSION,
            "scheme": self.scheme,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

    def unprotect(self, envelope: Mapping[str, Any], *, purpose: bytes) -> bytes:
        if not isinstance(envelope, Mapping):
            raise SecretProtectionError("secret_envelope_invalid")
        if envelope.get("version") != PROTECTED_SECRET_VERSION:
            raise SecretProtectionError("secret_envelope_version_unsupported")
        if envelope.get("scheme") != self.scheme:
            raise SecretProtectionError("secret_envelope_scheme_mismatch")
        encoded = envelope.get("ciphertext")
        if not isinstance(encoded, str) or not encoded or len(encoded) > _MAX_CIPHERTEXT_BYTES * 2:
            raise SecretProtectionError("secret_ciphertext_invalid")
        try:
            ciphertext = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise SecretProtectionError("secret_ciphertext_invalid") from exc
        if not ciphertext or len(ciphertext) > _MAX_CIPHERTEXT_BYTES:
            raise SecretProtectionError("secret_ciphertext_invalid")
        return self._unprotect_bytes(ciphertext, purpose)


@lru_cache(maxsize=1)
def default_secret_protector() -> SecretProtector:
    """Return the sole production protector; never fall back to plaintext."""

    return WindowsDpapiProtector()


__all__ = [
    "DPAPI_SCHEME",
    "PROTECTED_SECRET_VERSION",
    "SecretProtectionError",
    "SecretProtectionUnavailable",
    "SecretProtector",
    "WindowsDpapiProtector",
    "default_secret_protector",
]
