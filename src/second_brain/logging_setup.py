"""Logging to a rotating file inside the vault (``.secondbrain/logs``) plus stderr."""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "secondbrain.log"
_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
_file_handler: RotatingFileHandler | None = None


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    if not any(getattr(h, "_second_brain_console", False) for h in root.handlers):
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(_FORMAT))
        console._second_brain_console = True  # type: ignore[attr-defined]
        root.addHandler(console)
    for noisy in ("httpx", "httpcore", "watchdog", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def attach_vault_log(log_dir: Path) -> Path:
    """(Re)point the file log at ``log_dir``; called whenever a vault is opened."""
    global _file_handler
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE_NAME
    root = logging.getLogger()
    if root.getEffectiveLevel() > logging.INFO:
        root.setLevel(logging.INFO)
    if _file_handler is not None:
        root.removeHandler(_file_handler)
        _file_handler.close()
    _file_handler = RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8", delay=True
    )
    _file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(_file_handler)
    return log_path


def install_exception_hooks() -> None:
    """Make sure crashes on any thread end up in the log file."""

    def _hook(exc_type, exc, tb):
        logging.getLogger("second_brain").critical(
            "Unhandled exception", exc_info=(exc_type, exc, tb)
        )

    def _thread_hook(args: threading.ExceptHookArgs):
        if args.exc_type is not SystemExit:
            _hook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
