"""Watches the vault's drop folder (``_import/``) and imports files placed there."""

from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..vault.atomic import is_temp_file
from ..vault.note import Note
from .service import CaptureService

log = logging.getLogger(__name__)

FAILED_DIR = "failed"


class DropFolderWatcher(FileSystemEventHandler):
    """Imports files dropped into ``folder`` one at a time on a worker thread.

    Imported originals move into ``attachments/``; files that fail go to ``_import/failed``.
    """

    def __init__(
        self,
        capture: CaptureService,
        folder: Path,
        on_imported: Callable[[list[Note]], None] | None = None,
        on_error: Callable[[Path, Exception], None] | None = None,
        settle_s: float = 1.0,
    ):
        self.capture = capture
        self.folder = Path(folder)
        self.on_imported = on_imported
        self.on_error = on_error
        self.settle_s = settle_s
        self._queue: queue.Queue[Path | None] = queue.Queue()
        self._queued: set[Path] = set()
        self._lock = threading.Lock()
        self._observer = Observer()
        self._observer.daemon = True
        self._worker = threading.Thread(target=self._run, name="drop-folder", daemon=True)

    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self._worker.start()
        for path in sorted(self.folder.iterdir()):
            self._enqueue(path)
        self._observer.schedule(self, str(self.folder), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        if self._observer.is_alive():
            self._observer.join(timeout=5)
        self._queue.put(None)
        self._worker.join(timeout=5)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(Path(str(event.src_path)))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(Path(str(event.dest_path)))

    def _enqueue(self, path: Path) -> None:
        if (
            not path.is_file()
            or path.parent != self.folder
            or path.name.startswith(".")
            or is_temp_file(path)
            or not self.capture.can_import(path)
        ):
            return
        with self._lock:
            if path in self._queued:
                return
            self._queued.add(path)
        self._queue.put(path)

    def _run(self) -> None:
        while (path := self._queue.get()) is not None:
            try:
                if self._wait_until_stable(path):
                    notes = self.capture.import_file(path, move=True)
                    if self.on_imported:
                        self.on_imported(notes)
            except Exception as exc:
                log.exception("Import of %s failed", path.name)
                self._quarantine(path)
                if self.on_error:
                    self.on_error(path, exc)
            finally:
                with self._lock:
                    self._queued.discard(path)

    def _wait_until_stable(self, path: Path, attempts: int = 30) -> bool:
        """Wait until a file being copied in stops growing."""
        last = -1
        for _ in range(attempts):
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                return False
            if size == last:
                return True
            last = size
            time.sleep(self.settle_s)
        return True

    def _quarantine(self, path: Path) -> None:
        if not path.exists():
            return
        failed = self.folder / FAILED_DIR
        failed.mkdir(exist_ok=True)
        try:
            shutil.move(str(path), failed / path.name)
        except OSError:
            log.exception("Could not move %s to %s", path.name, failed)
