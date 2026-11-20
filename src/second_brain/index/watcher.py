"""File-system watching: keeps the index current while the app runs."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .indexer import Indexer

log = logging.getLogger(__name__)

DEBOUNCE_S = 0.4


class VaultWatcher(FileSystemEventHandler):
    """Re-indexes markdown files on create/modify/delete/move (debounced per path).

    ``on_change`` is called (from a background thread) with the set of changed paths
    after the index was updated.
    """

    def __init__(self, indexer: Indexer, on_change: Callable[[set[Path]], None] | None = None):
        self.indexer = indexer
        self.on_change = on_change
        self._pending: set[Path] = set()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._observer = Observer()
        self._observer.daemon = True

    def start(self) -> None:
        self._observer.schedule(self, str(self.indexer.vault.root), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        if self._observer.is_alive():
            self._observer.join(timeout=5)
        with self._lock:
            if self._timer:
                self._timer.cancel()
        self.flush()

    # watchdog callbacks --------------------------------------------------
    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type in ("opened", "closed_no_write"):
            return
        paths = [Path(str(event.src_path))]
        dest = getattr(event, "dest_path", None)
        if dest:
            paths.append(Path(str(dest)))
        relevant = [p for p in paths if p.suffix.lower() == ".md"]
        if not relevant:
            return
        with self._lock:
            self._pending.update(relevant)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_S, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, set()
            self._timer = None
        changed: set[Path] = set()
        for path in pending:
            try:
                if path.exists():
                    updated = self.indexer.index_path(path)
                else:
                    updated = self.indexer.remove_path(path)
                if updated:
                    changed.add(path)
            except Exception:
                log.exception("Failed to update index for %s", path)
        if changed and self.on_change:
            self.on_change(changed)
