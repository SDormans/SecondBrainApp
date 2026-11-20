"""Markdown note model with YAML frontmatter."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


class NoteStatus(StrEnum):
    INBOX = "inbox"
    PROCESSED = "processed"


class SourceType(StrEnum):
    NOTE = "note"
    TEXT = "text"
    URL = "url"
    PDF = "pdf"
    AUDIO = "audio"
    KINDLE = "kindle"


FRONTMATTER_FIELDS = (
    "id",
    "title",
    "source_type",
    "source_url",
    "author",
    "captured_at",
    "tags",
    "status",
)

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE
)
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)
_WINDOWS_RESERVED = (
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def new_note_id() -> str:
    """Time-sortable, collision-resistant id, e.g. ``20261120013300-a1b2c3``."""
    return f"{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(3)}"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(title: str, max_length: int = 80) -> str:
    """Filesystem-safe (Windows included) slug for a note title."""
    value = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    value = re.sub(r"[-\s_]+", "-", value).strip("-.")[:max_length].strip("-.")
    if not value:
        return "untitled"
    if value in _WINDOWS_RESERVED:
        value += "-note"
    return value


def normalize_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    items = re.split(r"[,\n]", raw) if isinstance(raw, str) else list(raw)
    tags: list[str] = []
    for item in items:
        tag = str(item).strip().lstrip("#").strip()
        tag = re.sub(r"\s+", "-", tag)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


@dataclass
class Note:
    id: str
    title: str
    body: str = ""
    source_type: str = SourceType.NOTE.value
    source_url: str | None = None
    author: str | None = None
    captured_at: str = field(default_factory=now_iso)
    tags: list[str] = field(default_factory=list)
    status: str = NoteStatus.INBOX.value
    extra: dict[str, Any] = field(default_factory=dict)
    """Unknown frontmatter keys, preserved verbatim so later features and other tools keep data."""
    path: Path | None = field(default=None, compare=False)

    @classmethod
    def new(cls, title: str, body: str = "", **fields: Any) -> Note:
        return cls(id=new_note_id(), title=title.strip() or "Untitled", body=body, **fields)

    @property
    def is_inbox(self) -> bool:
        return self.status != NoteStatus.PROCESSED

    def with_changes(self, **changes: Any) -> Note:
        return replace(self, **changes)

    def frontmatter(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "author": self.author,
            "captured_at": self.captured_at,
            "tags": list(self.tags),
            "status": self.status,
        }
        for key, value in self.extra.items():
            if key not in data:
                data[key] = value
        return data

    def to_markdown(self) -> str:
        header = yaml.safe_dump(
            self.frontmatter(),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=None,
            width=10_000,
        )
        body = self.body if self.body.endswith("\n") or not self.body else self.body + "\n"
        return f"---\n{header}---\n\n{body}"


def parse_note(text: str, *, path: Path | None = None) -> Note:
    """Parse markdown with optional YAML frontmatter.

    Files without (or with unreadable) frontmatter are still loaded: the id is derived
    from the path and the title from the first ``# heading`` or the file name. Invalid
    frontmatter is kept in the body so saving the note never discards it.
    """
    text = text.lstrip("\ufeff")
    meta: dict[str, Any] = {}
    body = text
    match = _FRONTMATTER_RE.match(text)
    if match:
        try:
            loaded = yaml.safe_load(match.group(1)) or {}
            if not isinstance(loaded, dict):
                raise ValueError("frontmatter is not a mapping")
            meta = loaded
            body = text[match.end() :]
            if body.startswith("\r\n"):
                body = body[2:]
            elif body.startswith("\n"):
                body = body[1:]
        except (yaml.YAMLError, ValueError) as exc:
            log.warning("Invalid frontmatter in %s: %s", path or "<text>", exc)
            meta = {}
            body = text

    note_id = _as_text(meta.get("id")) or _fallback_id(path, text)
    title = _as_text(meta.get("title")) or _fallback_title(body, path)
    status = (_as_text(meta.get("status")) or NoteStatus.INBOX.value).lower()
    if status not in {s.value for s in NoteStatus}:
        log.warning("Unknown status %r in %s; treating as inbox", status, path or "<text>")
        status = NoteStatus.INBOX.value

    return Note(
        id=note_id,
        title=title,
        body=body,
        source_type=(_as_text(meta.get("source_type")) or SourceType.NOTE.value).lower(),
        source_url=_as_text(meta.get("source_url")),
        author=_as_text(meta.get("author")),
        captured_at=_as_text(meta.get("captured_at")) or now_iso(),
        tags=normalize_tags(meta.get("tags")),
        status=status,
        extra={k: v for k, v in meta.items() if k not in FRONTMATTER_FIELDS},
        path=path,
    )


def _fallback_id(path: Path | None, text: str) -> str:
    seed = path.as_posix() if path is not None else text
    return "file-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _fallback_title(body: str, path: Path | None) -> str:
    heading = _HEADING_RE.search(body)
    if heading:
        return heading.group(1)
    if path is not None:
        return path.stem
    return "Untitled"
