"""Crash-safe file writes: write to a temp file in the same folder, fsync, then rename."""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path

TEMP_SUFFIX = ".tmp"
_REPLACE_ATTEMPTS = 6


def is_temp_file(path: Path) -> bool:
    return path.name.startswith(".") and path.name.endswith(TEMP_SUFFIX)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` so readers only ever see the old or the new content.

    On Windows ``os.replace`` can fail transiently while another process (antivirus,
    search indexer, sync client) holds the target open, so it is retried with backoff.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=TEMP_SUFFIX)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def _replace_with_retry(src: Path, dst: Path) -> None:
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.05 * 2**attempt)


def _fsync_dir(directory: Path) -> None:
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
