"""Capture entry point: turns pasted text, URLs and files into inbox notes in the vault."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from ..vault.note import Note
from ..vault.vault import Vault
from . import kindle
from .audio import AUDIO_EXTENSIONS, Transcriber, note_from_transcript
from .pdf import note_from_pdf
from .text import fetch_article, is_url, note_from_text

log = logging.getLogger(__name__)

FindNoteById = Callable[[str], Path | None]


class UnsupportedFileType(Exception):
    pass


class CaptureService:
    TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}

    def __init__(
        self,
        vault: Vault,
        transcriber_factory: Callable[[], Transcriber] | None = None,
        find_note_by_id: FindNoteById | None = None,
        fetch_url_content: bool = True,
    ):
        self.vault = vault
        self._transcriber_factory = transcriber_factory
        self._transcriber: Transcriber | None = None
        self._find_note_by_id = find_note_by_id or self._scan_for_id
        self.fetch_url_content = fetch_url_content

    # ----------------------------------------------------------------- text
    def capture_text(self, text: str, url: str | None = None, title: str | None = None) -> Note:
        """Pasted article text and/or URL. A bare URL is fetched if enabled in Settings."""
        text = text.strip()
        if not url and is_url(text):
            url, text = text, ""
        author = None
        if url and not text and self.fetch_url_content:
            try:
                fetched_title, text, author = fetch_article(url)
                title = title or fetched_title or None
            except Exception as exc:
                log.warning("Could not fetch %s: %s", url, exc)
                text = f"_Could not fetch the page ({exc}). Paste the article text here._"
        note = note_from_text(text, title=title, url=url, author=author)
        self.vault.save_note(note)
        return note

    # ---------------------------------------------------------------- files
    def can_import(self, path: Path) -> bool:
        suffix = Path(path).suffix.lower()
        return (
            kindle.is_clippings_file(path)
            or suffix == ".pdf"
            or suffix in AUDIO_EXTENSIONS
            or suffix in self.TEXT_EXTENSIONS
        )

    def import_file(
        self, path: Path, *, move: bool = False, progress: Callable[[float], None] | None = None
    ) -> list[Note]:
        """Import one file. With ``move=True`` the original is moved into ``attachments/``."""
        path = Path(path)
        suffix = path.suffix.lower()
        if kindle.is_clippings_file(path):
            notes = self.import_kindle(path)
            if move:
                self.vault.store_attachment(path, move=True)
            return notes
        if suffix == ".pdf":
            return [self.import_pdf(path, move=move)]
        if suffix in AUDIO_EXTENSIONS:
            return [self.import_audio(path, move=move, progress=progress)]
        if suffix in self.TEXT_EXTENSIONS:
            note = note_from_text(
                path.read_text(encoding="utf-8", errors="replace"), title=path.stem
            )
            self.vault.save_note(note)
            if move:
                path.unlink()
            return [note]
        raise UnsupportedFileType(f"Unsupported file type: {path.name}")

    def import_pdf(self, path: Path, *, move: bool = False, keep_copy: bool = True) -> Note:
        attachment = self.vault.store_attachment(path, move=move) if (keep_copy or move) else None
        source = self.vault.abspath(attachment) if attachment else path
        note = note_from_pdf(source, attachment=attachment)
        self.vault.save_note(note)
        log.info("Imported PDF %s", path.name)
        return note

    def import_audio(
        self,
        path: Path,
        *,
        move: bool = False,
        keep_copy: bool = True,
        progress: Callable[[float], None] | None = None,
    ) -> Note:
        if self._transcriber_factory is None:
            raise RuntimeError("No transcriber configured")
        if self._transcriber is None:
            self._transcriber = self._transcriber_factory()
        transcript = self._transcriber.transcribe(path, progress=progress)
        attachment = self.vault.store_attachment(path, move=move) if (keep_copy or move) else None
        note = note_from_transcript(path, transcript, attachment=attachment)
        self.vault.save_note(note)
        log.info("Transcribed %s (%d segments)", path.name, len(transcript.segments))
        return note

    def import_kindle(self, path: Path) -> list[Note]:
        """Import ``My Clippings.txt``; re-importing only appends new highlights."""
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        changed: list[Note] = []
        for (title, author), clips in kindle.group_by_book(kindle.parse_clippings(text)).items():
            existing_path = self._find_note_by_id(kindle.book_note_id(title, author))
            existing = self.vault.read_note(existing_path) if existing_path else None
            note, added = kindle.merge_into_note(existing, title, author, clips)
            if added:
                self.vault.save_note(note)
                changed.append(note)
        log.info("Kindle import: %d book notes created/updated", len(changed))
        return changed

    def _scan_for_id(self, note_id: str) -> Path | None:
        for path in self.vault.iter_note_paths():
            try:
                if self.vault.read_note(path).id == note_id:
                    return path
            except OSError:
                continue
        return None
