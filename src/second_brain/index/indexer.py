"""Keeps the SQLite index in sync with the markdown files in the vault."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..ai.ollama import OllamaError
from ..vault.links import extract_links
from ..vault.note import content_hash, parse_note
from ..vault.vault import Vault
from .embedder import Embedder
from .store import IndexStore

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]
"""``progress(done, total, message)``."""

MAX_EMBED_CHARS = 6000
EMBED_BATCH = 8


@dataclass
class RebuildStats:
    indexed: int = 0
    removed: int = 0
    errors: int = 0
    embedded: int = 0
    embedding_skipped_reason: str | None = None


class Indexer:
    def __init__(self, vault: Vault, store: IndexStore, embedder: Embedder | None = None):
        self.vault = vault
        self.store = store
        self.embedder = embedder

    # ----------------------------------------------------------- single files
    def index_path(self, path: Path, *, force: bool = False) -> bool:
        """Index (or re-index) one file. Returns True if the index changed."""
        path = Path(path)
        if not self.vault.is_note_path(path):
            return False
        rel = self.vault.relpath(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return self.store.delete_path(rel)
        digest = content_hash(text)
        if not force and self.store.get_hash(rel) == digest:
            return False
        note = parse_note(text, path=path)
        self.store.upsert_note(
            path=rel,
            note_id=note.id,
            title=note.title,
            body=note.body,
            status=note.status,
            source_type=note.source_type,
            tags=note.tags,
            captured_at=note.captured_at,
            mtime=mtime,
            content_hash=digest,
            links=extract_links(note.body),
        )
        log.debug("Indexed %s", rel)
        return True

    def remove_path(self, path: Path) -> bool:
        try:
            rel = Path(path).resolve().relative_to(self.vault.root).as_posix()
        except ValueError:
            return False
        return self.store.delete_path(rel)

    # ------------------------------------------------------------ whole vault
    def rebuild_index(
        self, *, embed: bool = True, progress: ProgressFn | None = None
    ) -> RebuildStats:
        """Drop the index and reconstruct it entirely from the vault's markdown files."""
        log.info("Rebuilding index for %s", self.vault.root)
        self.store.reset()
        paths = list(self.vault.iter_note_paths())
        stats = RebuildStats()
        for i, path in enumerate(paths, 1):
            try:
                self.index_path(path, force=True)
                stats.indexed += 1
            except Exception:  # one unreadable file must not abort the rebuild
                stats.errors += 1
                log.exception("Failed to index %s", path)
            if progress:
                progress(i, len(paths), f"Indexing {path.name}")
        if embed:
            self._embed_into(stats, progress)
        log.info("Rebuild done: %s", stats)
        return stats

    def sync(self, *, embed: bool = False, progress: ProgressFn | None = None) -> RebuildStats:
        """Incremental catch-up after the app was closed: index new/changed files, drop missing."""
        if self.store.needs_rebuild:
            return self.rebuild_index(embed=embed, progress=progress)
        stats = RebuildStats()
        known = self.store.file_state()
        seen: set[str] = set()
        paths = list(self.vault.iter_note_paths())
        for i, path in enumerate(paths, 1):
            rel = self.vault.relpath(path)
            seen.add(rel)
            try:
                previous = known.get(rel)
                stale = previous is None or previous[0] != path.stat().st_mtime
                if stale and self.index_path(path):
                    stats.indexed += 1
            except Exception:
                stats.errors += 1
                log.exception("Failed to index %s", path)
            if progress:
                progress(i, len(paths), f"Checking {path.name}")
        for rel in set(known) - seen:
            self.store.delete_path(rel)
            stats.removed += 1
        if embed:
            self._embed_into(stats, progress)
        return stats

    # ------------------------------------------------------------- embeddings
    def embed_pending(self, progress: ProgressFn | None = None) -> int:
        """Compute embeddings for notes that have none (or a stale one). Returns the count.

        Raises :class:`OllamaError` (incl. ``OllamaUnavailable``) if the embedder fails.
        """
        if self.embedder is None or not self.store.vec_available:
            return 0
        if self.store.embedding_model() not in (None, self.embedder.model):
            # Embedding model changed in settings: vectors are not comparable, start over.
            probe = self.embedder.embed(["dimension probe"], kind="document")
            self.store.ensure_vec_table(self.embedder.model, len(probe[0]))
        pending = self.store.needing_embedding()
        done = 0
        for start in range(0, len(pending), EMBED_BATCH):
            batch = pending[start : start + EMBED_BATCH]
            texts = [
                f"{title}\n\n{self.store.body(rowid)}"[:MAX_EMBED_CHARS]
                for rowid, _, title in batch
            ]
            vectors = self.embedder.embed(texts, kind="document")
            if vectors:
                self.store.ensure_vec_table(self.embedder.model, len(vectors[0]))
            for (rowid, digest, _), vector in zip(batch, vectors, strict=True):
                self.store.set_embedding(rowid, vector, digest)
                done += 1
            if progress:
                progress(done, len(pending), "Computing embeddings")
        return done

    def _embed_into(self, stats: RebuildStats, progress: ProgressFn | None) -> None:
        if self.embedder is None:
            stats.embedding_skipped_reason = "semantic search disabled"
            return
        try:
            stats.embedded = self.embed_pending(progress)
        except OllamaError as exc:
            stats.embedding_skipped_reason = str(exc)
            log.warning("Skipping embeddings: %s", exc)


def rebuild_index(
    vault: Vault,
    embedder: Embedder | None = None,
    *,
    embed: bool = True,
    progress: ProgressFn | None = None,
) -> RebuildStats:
    """Convenience wrapper: rebuild ``vault``'s index from scratch."""
    store = IndexStore(vault.index_path)
    try:
        return Indexer(vault, store, embedder).rebuild_index(embed=embed, progress=progress)
    finally:
        store.close()
