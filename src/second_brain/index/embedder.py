from __future__ import annotations

from typing import Literal, Protocol


class Embedder(Protocol):
    """Anything that turns text into vectors (Ollama in production, fakes in tests)."""

    model: str

    def available(self) -> bool: ...

    def embed(
        self, texts: list[str], kind: Literal["document", "query"] = "document"
    ) -> list[list[float]]: ...
