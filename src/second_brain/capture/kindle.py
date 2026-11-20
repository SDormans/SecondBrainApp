"""Parse Kindle ``My Clippings.txt`` files into one note per book."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..vault.note import Note, NoteStatus, SourceType

SEPARATOR = "=========="
CLIPPINGS_FILE_NAME = "my clippings.txt"
_TITLE_AUTHOR_RE = re.compile(r"^(?P<title>.*?)\s*\((?P<author>[^()]*)\)\s*$")
_KIND_RE = re.compile(r"Your\s+(Highlight|Note|Bookmark|Clip)", re.IGNORECASE)
_LOCATION_RE = re.compile(r"(?:Location|Loc\.)\s+([\d-]+)", re.IGNORECASE)
_PAGE_RE = re.compile(r"page\s+([\w-]+)", re.IGNORECASE)
_ADDED_RE = re.compile(r"Added on\s+(.+)$", re.IGNORECASE)
_MARKER_RE = re.compile(r"<!-- kindle:([0-9a-f]{12}) -->")


@dataclass(frozen=True)
class Clipping:
    book_title: str
    author: str | None
    kind: str  # highlight | note | bookmark | clip
    location: str | None
    page: str | None
    added_on: str | None
    text: str

    @property
    def key(self) -> str:
        """Stable identity used to skip clippings already imported."""
        seed = f"{self.book_title}|{self.kind}|{self.location}|{self.page}|{self.text}"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def is_clippings_file(path: Path) -> bool:
    return Path(path).name.lower() == CLIPPINGS_FILE_NAME


def parse_clippings(text: str) -> list[Clipping]:
    clippings: list[Clipping] = []
    for raw_entry in text.lstrip("\ufeff").replace("\r\n", "\n").split(SEPARATOR):
        lines = [line.strip("\ufeff") for line in raw_entry.strip("\n").split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        if len(lines) < 2:
            continue
        header, meta = lines[0].strip(), lines[1].strip()
        body = "\n".join(lines[2:]).strip()
        match = _TITLE_AUTHOR_RE.match(header)
        title, author = (
            (match["title"].strip(), match["author"].strip() or None) if match else (header, None)
        )
        kind_match = _KIND_RE.search(meta)
        kind = kind_match.group(1).lower() if kind_match else "highlight"
        if kind == "bookmark" or not body:
            continue
        location = _LOCATION_RE.search(meta)
        page = _PAGE_RE.search(meta)
        added = _ADDED_RE.search(meta)
        clippings.append(
            Clipping(
                book_title=title or "Unknown book",
                author=author,
                kind=kind,
                location=location.group(1) if location else None,
                page=page.group(1) if page else None,
                added_on=added.group(1).strip() if added else None,
                text=body,
            )
        )
    return _drop_superseded(clippings)


def _drop_superseded(clippings: list[Clipping]) -> list[Clipping]:
    """Kindle appends a new highlight when one is extended; keep only the longest version."""
    result: list[Clipping] = []
    for clip in clippings:
        if result:
            prev = result[-1]
            if (
                prev.kind == clip.kind == "highlight"
                and prev.book_title == clip.book_title
                and (clip.text.startswith(prev.text) or prev.text.startswith(clip.text))
            ):
                if len(clip.text) > len(prev.text):
                    result[-1] = clip
                continue
        result.append(clip)
    return result


def book_note_id(title: str, author: str | None) -> str:
    return "kindle-" + hashlib.sha1(f"{title}|{author or ''}".encode()).hexdigest()[:12]


def render_clipping(clip: Clipping) -> str:
    where = " · ".join(
        filter(
            None,
            [
                f"page {clip.page}" if clip.page else None,
                f"location {clip.location}" if clip.location else None,
                clip.added_on,
            ],
        )
    )
    if clip.kind == "note":
        block = f"**My note:** {clip.text}"
    else:
        block = "\n".join(f"> {line}" if line else ">" for line in clip.text.split("\n"))
    meta = f"\n\n<small>{where}</small>" if where else ""
    return f"{block}{meta} <!-- kindle:{clip.key} -->\n"


def group_by_book(clippings: list[Clipping]) -> dict[tuple[str, str | None], list[Clipping]]:
    books: dict[tuple[str, str | None], list[Clipping]] = {}
    for clip in clippings:
        books.setdefault((clip.book_title, clip.author), []).append(clip)
    return books


def existing_keys(body: str) -> set[str]:
    return set(_MARKER_RE.findall(body))


def merge_into_note(
    note: Note | None, title: str, author: str | None, clippings: list[Clipping]
) -> tuple[Note, int]:
    """Create a book note or append clippings not yet in it. Returns ``(note, added_count)``."""
    if note is None:
        note = Note(
            id=book_note_id(title, author),
            title=title,
            body=f"# {title}\n\n" + (f"by {author}\n\n" if author else "") + "## Highlights\n\n",
            source_type=SourceType.KINDLE.value,
            author=author,
            tags=["kindle", "book"],
            status=NoteStatus.INBOX.value,
        )
    seen = existing_keys(note.body)
    new = [c for c in clippings if c.key not in seen]
    if new:
        body = note.body if note.body.endswith("\n") else note.body + "\n"
        note.body = body + "\n".join(render_clipping(c) for c in new)
    return note, len(new)
