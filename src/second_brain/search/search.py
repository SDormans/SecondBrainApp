"""Keyword (FTS5) and semantic (sqlite-vec + Ollama embeddings) search."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from ..ai.ollama import OllamaError
from ..index.embedder import Embedder
from ..index.store import IndexStore, NoteRecord

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r'"([^"]+)"|(\S+)')
_WORD_RE = re.compile(r"\w+", re.UNICODE)


class SearchMode(StrEnum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"


class SemanticSearchUnavailable(Exception):
    pass


@dataclass(frozen=True)
class SearchResult:
    record: NoteRecord
    snippet: str
    score: float

    @property
    def path(self) -> str:
        return self.record.path

    @property
    def title(self) -> str:
        return self.record.title


@dataclass(frozen=True)
class SearchOutcome:
    results: list[SearchResult]
    mode: SearchMode
    """The mode that actually produced the results (semantic may fall back to keyword)."""
    message: str | None = None


def build_fts_query(text: str, *, any_term: bool = False) -> str | None:
    """Turn free text into a safe FTS5 query.

    Every word is quoted so FTS5 operators/punctuation in user input cannot cause syntax
    errors; ``"quoted phrases"`` are kept as phrases and the last bare word is a prefix
    match so results appear while typing.
    """
    terms: list[str] = []
    for phrase, bare in _TOKEN_RE.findall(text):
        if phrase:
            words = _WORD_RE.findall(phrase)
            if words:
                terms.append('"' + " ".join(words) + '"')
        else:
            terms.extend(f'"{w}"' for w in _WORD_RE.findall(bare))
    if not terms:
        return None
    if not text.rstrip().endswith('"'):
        terms[-1] += "*"
    return (" OR " if any_term else " ").join(terms)


class SearchService:
    def __init__(self, store: IndexStore, embedder: Embedder | None = None):
        self.store = store
        self.embedder = embedder

    def keyword(self, query: str, limit: int = 50, *, any_term: bool = False) -> list[SearchResult]:
        fts_query = build_fts_query(query, any_term=any_term)
        if fts_query is None:
            return []
        return [
            SearchResult(rec, snippet, score)
            for rec, snippet, score in self.store.keyword_search(fts_query, limit)
        ]

    def semantic(self, query: str, limit: int = 20) -> list[SearchResult]:
        if not query.strip():
            return []
        if self.embedder is None:
            raise SemanticSearchUnavailable("Semantic search is turned off in Settings")
        if not self.store.vec_available:
            raise SemanticSearchUnavailable("sqlite-vec could not be loaded")
        if self.store.embedded_count() == 0:
            raise SemanticSearchUnavailable("No embeddings yet (is Ollama running?)")
        try:
            [vector] = self.embedder.embed([query], kind="query")
        except OllamaError as exc:
            raise SemanticSearchUnavailable(str(exc)) from exc
        results = []
        for record, distance in self.store.vector_search(vector, limit):
            body = self.store.body(record.rowid)
            results.append(SearchResult(record, _preview(body), 1.0 - distance))
        return results

    def search(
        self, query: str, mode: SearchMode = SearchMode.KEYWORD, limit: int = 50
    ) -> SearchOutcome:
        """Search in ``mode``; semantic search degrades to keyword search when unavailable."""
        if mode == SearchMode.SEMANTIC:
            try:
                return SearchOutcome(self.semantic(query, limit), SearchMode.SEMANTIC)
            except SemanticSearchUnavailable as exc:
                log.info("Semantic search unavailable, using keyword search: %s", exc)
                return SearchOutcome(
                    self.keyword(query, limit),
                    SearchMode.KEYWORD,
                    f"Semantic search unavailable ({exc}); showing keyword results",
                )
        return SearchOutcome(self.keyword(query, limit), SearchMode.KEYWORD)


def _preview(body: str, length: int = 160) -> str:
    text = re.sub(r"\s+", " ", body).strip()
    return text[:length] + ("…" if len(text) > length else "")
