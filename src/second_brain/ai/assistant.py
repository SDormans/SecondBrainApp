"""AI assistance for notes: summaries and tag/link suggestions (local-first)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..vault.links import extract_links
from ..vault.note import Note, normalize_tags
from .providers import TextModel

if TYPE_CHECKING:
    from ..search.search import SearchService

log = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 12_000

SUMMARY_SYSTEM = (
    "You summarize personal research notes. Be faithful to the text, do not invent facts. "
    "Answer in the language of the note."
)
TAGS_SYSTEM = (
    "You suggest short topical tags for a personal knowledge base. "
    "Reply with a JSON array of 3 to 7 lowercase tags (use hyphens instead of spaces), "
    "nothing else."
)


@dataclass(frozen=True)
class LinkSuggestion:
    title: str
    path: str
    score: float


class Assistant:
    def __init__(self, model_factory: Callable[[], TextModel], search: SearchService | None = None):
        self._model_factory = model_factory
        self.search = search

    def _generate(self, prompt: str, system: str) -> str:
        return self._model_factory().generate(prompt, system=system)

    def summarize(self, note: Note, max_bullets: int = 5) -> str:
        prompt = (
            f"Summarize the note below in at most {max_bullets} markdown bullet points, "
            f"followed by one line starting with 'Key takeaway:'.\n\n"
            f"Title: {note.title}\n\n{note.body[:MAX_PROMPT_CHARS]}"
        )
        return self._generate(prompt, SUMMARY_SYSTEM)

    def suggest_tags(self, note: Note, known_tags: list[str] | None = None) -> list[str]:
        known = ", ".join((known_tags or [])[:100])
        prompt = (
            f"Prefer reusing these existing tags when they fit: {known}\n\n" if known else ""
        ) + f"Title: {note.title}\n\n{note.body[:MAX_PROMPT_CHARS]}"
        tags = parse_tag_response(self._generate(prompt, TAGS_SYSTEM))
        return [t for t in tags if t not in note.tags]

    def suggest_links(self, note: Note, limit: int = 5) -> list[LinkSuggestion]:
        """Related notes not yet linked: semantic neighbours, falling back to keyword overlap."""
        from ..search.search import SemanticSearchUnavailable

        if self.search is None:
            return []
        linked = {t.lower() for t in extract_links(note.body)}
        query = f"{note.title}\n{note.body[:2000]}"
        try:
            results = self.search.semantic(query, limit + 10)
        except SemanticSearchUnavailable:
            results = self.search.keyword(
                " ".join([note.title, *note.tags]), limit + 10, any_term=True
            )
        suggestions = []
        for result in results:
            rec = result.record
            if rec.note_id == note.id or rec.title.lower() in linked:
                continue
            suggestions.append(LinkSuggestion(rec.title, rec.path, result.score))
            if len(suggestions) >= limit:
                break
        return suggestions


def parse_tag_response(text: str) -> list[str]:
    """Extract tags from an LLM reply; tolerant of prose around a JSON array."""
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    raw: list[str] | str
    if match:
        try:
            parsed = json.loads(match.group(0))
            raw = [str(x) for x in parsed] if isinstance(parsed, list) else text
        except json.JSONDecodeError:
            raw = match.group(0).strip("[]").replace('"', "")
    else:
        raw = text
    tags = [re.sub(r"[^\w-]", "", t.lower()) for t in normalize_tags(raw)]
    return [t for t in tags if t][:8]
