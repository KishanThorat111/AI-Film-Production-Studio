"""API keys live in Windows Credential Manager (encrypted by Windows for this account). Nothing else in the program stores a key.

A hash cannot be used to call an API, so the key itself must be stored, encrypted. The database keeps only the entry's name,
a keyed fingerprint (to notice that a key changed) and a masked hint.
Limit: any program running as the same Windows user can ask Windows for the entry. This protects against files, backups,
logs and other users; it does not protect against malware running as you.
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import secrets as pysecrets
from ctypes import wintypes

PREFIX = "AIFilmStudio/"


class _FILETIME(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]


class _CRED(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", _FILETIME), ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


class SecretError(Exception):
    pass


def _api():
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    a.CredWriteW.argtypes = [ctypes.POINTER(_CRED), wintypes.DWORD]
    a.CredWriteW.restype = wintypes.BOOL
    a.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CRED))]
    a.CredReadW.restype = wintypes.BOOL
    a.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    a.CredDeleteW.restype = wintypes.BOOL
    a.CredFree.argtypes = [ctypes.c_void_p]
    return a


class WindowsVault:
    def put(self, name: str, secret: str) -> str:
        blob = secret.encode("utf-8")
        if not 8 <= len(blob) <= 2048:
            raise SecretError("that does not look like an API key")
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        c = _CRED(Type=1, TargetName=PREFIX + name, CredentialBlobSize=len(blob), CredentialBlob=buf, Persist=2, UserName="studio")
        if not _api().CredWriteW(ctypes.byref(c), 0):
            raise SecretError(f"Windows refused to store the key (error {ctypes.get_last_error()})")
        ctypes.memset(buf, 0, len(blob))
        return "wincred:" + PREFIX + name

    def get(self, ref: str) -> str:
        if not ref or not ref.startswith("wincred:" + PREFIX):
            raise SecretError("no key is configured")
        a, p = _api(), ctypes.POINTER(_CRED)()
        if not a.CredReadW(ref[len("wincred:"):], 1, 0, ctypes.byref(p)):
            raise SecretError("the key is missing from Windows Credential Manager; enter it again")
        try:
            return ctypes.string_at(p.contents.CredentialBlob, p.contents.CredentialBlobSize).decode("utf-8")
        finally:
            a.CredFree(p)

    def delete(self, ref: str) -> None:
        if ref and ref.startswith("wincred:" + PREFIX):
            _api().CredDeleteW(ref[len("wincred:"):], 1, 0)


class MemoryVault:
    """For tests only."""

    def __init__(self):
        self._d = {}

    def put(self, name, secret):
        if not 8 <= len(secret) <= 2048:
            raise SecretError("that does not look like an API key")
        self._d[name] = secret
        return "wincred:" + PREFIX + name

    def get(self, ref):
        try:
            return self._d[ref[len("wincred:" + PREFIX):]]
        except (KeyError, TypeError):
            raise SecretError("no key is configured") from None

    def delete(self, ref):
        self._d.pop((ref or "")[len("wincred:" + PREFIX):], None)


def fingerprint(secret: str, salt: bytes | None = None) -> str:
    """Keyed, slow, one-way. Only proves 'same key as before'."""
    salt = salt or pysecrets.token_bytes(16)
    return salt.hex() + ":" + hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, 200_000).hex()[:24]


def same_key(secret: str, fp: str) -> bool:
    return hmac.compare_digest(fingerprint(secret, bytes.fromhex(fp.split(":")[0])), fp)


def hint(secret: str) -> str:
    return "•••• " + secret[-4:] if len(secret) >= 16 else "••••"
