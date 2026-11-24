"""Detection and serialization helpers for mounted network filesystems."""
from contextlib import nullcontext
import os
import posixpath
import re
import subprocess
import sys
import threading
import time


_NETWORK_OPERATION_LOCK = threading.RLock()
_MOUNT_CACHE_SECONDS = 30.0
_mount_cache = (0.0, ())


def _decode_mount_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\134", "\\")
    )


def _read_macos_network_mounts() -> tuple[str, ...]:
    global _mount_cache
    now = time.monotonic()
    if now - _mount_cache[0] < _MOUNT_CACHE_SECONDS:
        return _mount_cache[1]
    mounts = []
    try:
        result = subprocess.run(
            ["/sbin/mount"], capture_output=True, text=True,
            check=False, timeout=3,
        )
        for line in result.stdout.splitlines():
            match = re.search(r" on (.+?) \((.+)\)$", line)
            if not match:
                continue
            mount_path = _decode_mount_path(match.group(1))
            details = f"{line[:match.start()]} {match.group(2)}".casefold()
            if any(marker in details for marker in (
                "webdav", "davfs", "webdav.yandex", "https://",
            )):
                mounts.append(os.path.normpath(mount_path))
    except (OSError, subprocess.SubprocessError):
        mounts = []
    _mount_cache = (now, tuple(sorted(mounts, key=len, reverse=True)))
    return _mount_cache[1]


def is_network_path(path: str) -> bool:
    """Return True for mounted WebDAV/remote paths without reading contents."""
    if not path:
        return False
    if sys.platform == "darwin":
        # Keep macOS mount paths meaningful even when this function is tested
        # from a non-POSIX host.
        normalised = posixpath.normpath(os.path.expanduser(path))
    else:
        normalised = os.path.normpath(os.path.abspath(os.path.expanduser(path)))
    if normalised.startswith(("//", "\\\\")):
        return True

    if sys.platform == "darwin" and normalised.startswith("/Volumes/"):
        for mount_path in _read_macos_network_mounts():
            if normalised == mount_path or normalised.startswith(mount_path + "/"):
                return True
        volume_name = normalised[len("/Volumes/"):].split("/", 1)[0].casefold()
        return any(marker in volume_name for marker in ("webdav", "yandex", "dav"))

    if os.name == "nt":
        try:
            import ctypes

            drive = os.path.splitdrive(normalised)[0] + "\\"
            return (
                bool(drive.strip("\\"))
                and ctypes.windll.kernel32.GetDriveTypeW(drive) == 4
            )
        except (AttributeError, OSError):
            return False
    return False


def network_operation(path: str):
    """Serialize mounted network operations while leaving local IO concurrent."""
    return _NETWORK_OPERATION_LOCK if is_network_path(path) else nullcontext()

