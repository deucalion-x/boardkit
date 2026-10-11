"""The platform's own credential store, behind four calls.

macOS keeps secrets in the login keychain (`security`), Linux desktops in the
Secret Service (`secret-tool`, from libsecret), and Windows in Credential
Manager, reached through its API since it has no command that reads a secret
back. There is deliberately no fallback to a file: a token on disk in clear is
exactly what a credential store exists to avoid. Without one, `BOARDKIT_TOKEN`
is the way in.
"""
from __future__ import annotations

import shutil
import subprocess
import sys


def backend() -> str:
    """"macos", "linux", "windows", or "" when no store is reachable."""
    if sys.platform == "darwin" and shutil.which("security"):
        return "macos"
    if sys.platform.startswith("linux") and shutil.which("secret-tool"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    return ""


def available() -> bool:
    return bool(backend())


def get(service: str, account: str) -> str:
    """The secret filed under service/account, or "" if there is none."""
    kind = backend()
    if kind == "macos":
        found = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, check=False)
        return found.stdout.strip() if found.returncode == 0 else ""
    if kind == "linux":
        found = subprocess.run(["secret-tool", "lookup", "service", service, "account", account],
                               capture_output=True, text=True, check=False)
        return found.stdout.strip() if found.returncode == 0 else ""
    if kind == "windows":
        return windows_read(f"{service}:{account}")
    return ""


def put(service: str, account: str, secret: str, label: str) -> None:
    """File `secret`, replacing any earlier one under the same names."""
    kind = backend()
    if kind == "macos":
        # `-w` takes the secret on argv, visible to this user's `ps` for the
        # instant the call runs; `security` reads it no other way without a
        # prompt. The credentials kept here are narrow by design.
        subprocess.run(["security", "add-generic-password", "-U", "-s", service,
                        "-a", account, "-l", label, "-w", secret],
                       capture_output=True, text=True, check=True)
    elif kind == "linux":
        # The secret goes on stdin, never on argv.
        subprocess.run(["secret-tool", "store", f"--label={label}",
                        "service", service, "account", account],
                       input=secret, capture_output=True, text=True, check=True)
    elif kind == "windows":
        windows_write(f"{service}:{account}", secret, label)
    else:
        raise RuntimeError("no credential store on this machine")


def delete(service: str, account: str) -> None:
    kind = backend()
    if kind == "macos":
        subprocess.run(["security", "delete-generic-password", "-s", service, "-a", account],
                       capture_output=True, text=True, check=False)
    elif kind == "linux":
        subprocess.run(["secret-tool", "clear", "service", service, "account", account],
                       capture_output=True, text=True, check=False)
    elif kind == "windows":
        windows_delete(f"{service}:{account}")


# Windows Credential Manager, through advapi32. Generic credentials, stored
# for this user, persisted on this machine only.
GENERIC, LOCAL_MACHINE = 1, 2


def _windows():  # pragma: no cover - exercised on Windows only
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    return ctypes, CREDENTIAL, advapi


def windows_read(target: str) -> str:  # pragma: no cover - Windows only
    ctypes, CREDENTIAL, advapi = _windows()
    pointer = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(target, GENERIC, 0, ctypes.byref(pointer)):
        return ""
    try:
        cred = pointer.contents
        return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize).decode("utf-8")
    finally:
        advapi.CredFree(pointer)


def windows_write(target: str, secret: str, label: str) -> None:  # pragma: no cover
    ctypes, CREDENTIAL, advapi = _windows()
    blob = secret.encode("utf-8")
    cred = CREDENTIAL(Type=GENERIC, TargetName=target, Comment=label,
                      CredentialBlobSize=len(blob),
                      CredentialBlob=ctypes.cast(ctypes.create_string_buffer(blob, len(blob)),
                                                 ctypes.POINTER(ctypes.c_char)),
                      Persist=LOCAL_MACHINE, UserName="boardkit")
    if not advapi.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(ctypes.get_last_error(), "CredWriteW failed")


def windows_delete(target: str) -> None:  # pragma: no cover - Windows only
    _, _, advapi = _windows()
    advapi.CredDeleteW(target, GENERIC, 0)
