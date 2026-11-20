"""Capture pasted text and URLs (optionally fetching the article text)."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

import httpx

from ..vault.note import Note, NoteStatus, SourceType

_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"}
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "blockquote",
    "pre",
    "tr",
}
USER_AGENT = "SecondBrain/0.1 (+local desktop app)"


def is_url(text: str) -> bool:
    return bool(_URL_RE.match(text.strip()))


def note_from_text(
    text: str, title: str | None = None, url: str | None = None, author: str | None = None
) -> Note:
    text = text.strip()
    if not title:
        first_line = next(
            (line.strip("# ").strip() for line in text.splitlines() if line.strip()), ""
        )
        title = first_line[:80] or (url or "Captured text")
    return Note.new(
        title,
        text + "\n" if text else "",
        source_type=(SourceType.URL if url else SourceType.TEXT).value,
        source_url=url,
        author=author,
        tags=[],
        status=NoteStatus.INBOX.value,
    )


class _ArticleExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.author: str | None = None
        self._in_title = False
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "meta" and attributes.get("name", "").lower() == "author":
            self.author = attributes.get("content") or None
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        joined = "".join(self._chunks)
        paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", joined)]
        return "\n\n".join(p for p in paragraphs if p)


def extract_article(page_html: str) -> tuple[str, str, str | None]:
    """Return ``(title, text, author)`` from an HTML page using a simple readability heuristic."""
    parser = _ArticleExtractor()
    parser.feed(page_html)
    return html.unescape(parser.title).strip(), parser.text(), parser.author


def fetch_article(url: str, timeout_s: float = 20.0) -> tuple[str, str, str | None]:
    response = httpx.get(
        url, timeout=timeout_s, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    response.raise_for_status()
    if "html" not in response.headers.get("content-type", "html"):
        return url, response.text, None
    return extract_article(response.text)
