"""Wires vault, index, search, capture and AI together; UI-independent (and testable)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .ai.assistant import Assistant
from .ai.ollama import OllamaClient, OllamaEmbedder, OllamaError
from .ai.providers import select_text_model
from .capture.audio import WhisperTranscriber
from .capture.service import CaptureService
from .capture.watcher import DropFolderWatcher
from .config import Settings
from .index.indexer import Indexer, ProgressFn, RebuildStats
from .index.store import IndexStore
from .index.watcher import VaultWatcher
from .logging_setup import attach_vault_log
from .search.search import SearchService
from .vault.note import Note
from .vault.vault import Vault

log = logging.getLogger(__name__)

ChangeListener = Callable[[], None]
ImportListener = Callable[[list[Note]], None]
ErrorListener = Callable[[str], None]


class AppCore:
    """One open vault plus all services built on top of it.

    Listener callbacks may be invoked from background threads; UI code must marshal them
    onto its own thread.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.vault: Vault | None = None
        self.store: IndexStore | None = None
        self.indexer: Indexer | None = None
        self.search: SearchService | None = None
        self.capture: CaptureService | None = None
        self.assistant: Assistant | None = None
        self.client = OllamaClient(settings.ollama_url, settings.ollama_timeout_s)
        self.log_path: Path | None = None
        self.on_index_changed: list[ChangeListener] = []
        self.on_imported: list[ImportListener] = []
        self.on_error: list[ErrorListener] = []
        self._vault_watcher: VaultWatcher | None = None
        self._drop_watcher: DropFolderWatcher | None = None
        self._embed_executor = ThreadPoolExecutor(1, thread_name_prefix="embed")
        self._embed_scheduled = threading.Event()

    # ---------------------------------------------------------------- vault
    def open_vault(self, path: Path | str, *, watch: bool = True) -> Vault:
        self.close_vault()
        vault = Vault.open(path)
        self.log_path = attach_vault_log(vault.log_dir)
        log.info("Opened vault %s", vault.root)
        self.vault = vault
        self.store = IndexStore(vault.index_path)
        self._build_services()
        if watch:
            self._vault_watcher = VaultWatcher(self.indexer, lambda _paths: self._index_changed())
            self._vault_watcher.start()
            self._drop_watcher = DropFolderWatcher(
                self.capture,
                vault.import_dir,
                on_imported=self._imported,
                on_error=lambda p, e: self._error(f"Import of {p.name} failed: {e}"),
            )
            self._drop_watcher.start()
        self.settings.remember_vault(str(vault.root))
        return vault

    def _build_services(self) -> None:
        assert self.vault and self.store
        self.client = OllamaClient(self.settings.ollama_url, self.settings.ollama_timeout_s)
        embedder = (
            OllamaEmbedder(self.client, self.settings.ollama_embed_model)
            if self.settings.semantic_search_enabled
            else None
        )
        self.indexer = Indexer(self.vault, self.store, embedder)
        self.search = SearchService(self.store, embedder)
        settings = self.settings
        self.capture = CaptureService(
            self.vault,
            transcriber_factory=lambda: WhisperTranscriber(
                settings.whisper_model, settings.whisper_device, settings.whisper_compute_type
            ),
            find_note_by_id=self._path_for_note_id,
            fetch_url_content=settings.fetch_url_content,
        )
        self.assistant = Assistant(
            lambda: select_text_model(self.settings, self.client), self.search
        )

    def apply_settings(self, settings: Settings) -> None:
        vault_changed = settings.vault_path and (
            self.vault is None or Path(settings.vault_path).resolve() != self.vault.root
        )
        self.settings = settings
        if vault_changed:
            self.open_vault(settings.vault_path)
        elif self.vault is not None:
            self._build_services()
            if self._vault_watcher:
                self._vault_watcher.indexer = self.indexer
            if self._drop_watcher:
                self._drop_watcher.capture = self.capture
        settings.save()

    def close_vault(self) -> None:
        for watcher in (self._vault_watcher, self._drop_watcher):
            if watcher:
                try:
                    watcher.stop()
                except Exception:
                    log.exception("Error stopping watcher")
        self._vault_watcher = self._drop_watcher = None
        if self.store:
            self.store.close()
        self.vault = self.store = self.indexer = self.search = self.capture = None
        self.assistant = None

    def shutdown(self) -> None:
        self.close_vault()
        self._embed_executor.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------------- index
    def sync_index(self, progress: ProgressFn | None = None) -> RebuildStats:
        stats = self.indexer.sync(progress=progress)
        self._index_changed()
        self.schedule_embedding()
        return stats

    def rebuild_index(self, progress: ProgressFn | None = None) -> RebuildStats:
        stats = self.indexer.rebuild_index(embed=True, progress=progress)
        self._index_changed()
        return stats

    def save_note(self, note: Note, expected_hash: str | None = None) -> Path:
        path = self.vault.save_note(note, expected_hash)
        self.indexer.index_path(path)
        self._index_changed()
        self.schedule_embedding()
        return path

    def schedule_embedding(self) -> None:
        """Compute missing embeddings in the background (coalesced; never blocks the caller)."""
        if self.indexer is None or self.indexer.embedder is None or self._embed_scheduled.is_set():
            return
        self._embed_scheduled.set()
        indexer = self.indexer

        def _run() -> None:
            self._embed_scheduled.clear()
            try:
                indexer.embed_pending()
            except OllamaError as exc:
                log.info("Embeddings postponed: %s", exc)
            except Exception:
                log.exception("Embedding failed")

        self._embed_executor.submit(_run)

    def _path_for_note_id(self, note_id: str) -> Path | None:
        record = self.store.get_by_note_id(note_id) if self.store else None
        return self.vault.abspath(record.path) if record else None

    # ------------------------------------------------------------ listeners
    def _index_changed(self) -> None:
        for listener in list(self.on_index_changed):
            listener()
        self.schedule_embedding()

    def _imported(self, notes: list[Note]) -> None:
        for note in notes:
            if note.path:
                self.indexer.index_path(note.path)
        for listener in list(self.on_imported):
            listener(notes)
        self._index_changed()

    def _error(self, message: str) -> None:
        log.error(message)
        for listener in list(self.on_error):
            listener(message)
