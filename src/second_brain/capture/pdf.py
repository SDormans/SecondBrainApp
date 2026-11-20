"""Import PDF files by extracting their text with pypdf."""

from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

from ..vault.note import Note, NoteStatus, SourceType

log = logging.getLogger(__name__)


class PdfImportError(Exception):
    pass


def extract_pdf(path: Path) -> tuple[str | None, str | None, list[str]]:
    """Return ``(title, author, page_texts)``."""
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfImportError(f"{path.name} is password protected")
        meta = reader.metadata
        title = (meta.title if meta else None) or None
        author = (meta.author if meta else None) or None
        pages = []
        for page in reader.pages:
            try:
                pages.append((page.extract_text() or "").strip())
            except Exception as exc:  # pypdf can fail on individual malformed pages
                log.warning("Could not extract a page of %s: %s", path.name, exc)
                pages.append("")
        return title, author, pages
    except PdfImportError:
        raise
    except Exception as exc:
        raise PdfImportError(f"Could not read {path.name}: {exc}") from exc


def note_from_pdf(path: Path, attachment: str | None = None) -> Note:
    path = Path(path)
    title, author, pages = extract_pdf(path)
    parts = [f"<!-- page {i} -->\n{text}" for i, text in enumerate(pages, 1) if text]
    if parts:
        body = "\n\n".join(parts)
    else:
        body = "_No extractable text found (scanned PDF?). OCR is not part of v1._"
    if attachment:
        body = f"Original file: `{attachment}`\n\n{body}"
    return Note.new(
        (title or path.stem).strip(),
        body + "\n",
        source_type=SourceType.PDF.value,
        author=author,
        status=NoteStatus.INBOX.value,
        extra={"attachment": attachment, "pages": len(pages)}
        if attachment
        else {"pages": len(pages)},
    )
