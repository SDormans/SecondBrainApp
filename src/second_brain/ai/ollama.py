"""Minimal Ollama HTTP client (https://github.com/ollama/ollama/blob/main/docs/api.md)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)


class OllamaError(Exception):
    pass


class OllamaUnavailable(OllamaError):
    """Ollama is not running / not reachable at the configured endpoint."""


class OllamaClient:
    #: After a connection failure, fail fast for this long instead of waiting on timeouts again.
    DOWN_BACKOFF_S = 15.0

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout_s: float = 120.0,
        connect_timeout_s: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_s, connect=connect_timeout_s)
        self._transport = transport
        self._down_until = 0.0
        self._lock = threading.Lock()

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url, timeout=self._timeout, transport=self._transport
        )

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        with self._lock:
            if time.monotonic() < self._down_until:
                raise OllamaUnavailable(f"Ollama is not reachable at {self.base_url}")
        try:
            with self._client() as client:
                response = client.request(method, path, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            with self._lock:
                self._down_until = time.monotonic() + self.DOWN_BACKOFF_S
            raise OllamaUnavailable(f"Ollama is not reachable at {self.base_url}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc
        with self._lock:
            self._down_until = 0.0
        if response.status_code >= 400:
            detail = _error_detail(response)
            if response.status_code == 404 and "not found" in detail.lower() and payload:
                model = payload.get("model", "?")
                raise OllamaError(f"Model '{model}' is not installed. Run: ollama pull {model}")
            raise OllamaError(f"Ollama returned HTTP {response.status_code}: {detail}")
        return response.json()

    def is_available(self) -> bool:
        try:
            self._request("GET", "/api/tags")
            return True
        except OllamaError:
            return False

    def list_models(self) -> list[str]:
        data = self._request("GET", "/api/tags")
        return [m.get("name", "") for m in data.get("models", [])]

    def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        data = self._request("POST", "/api/embed", {"model": model, "input": inputs})
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(inputs):
            raise OllamaError("Unexpected embedding response from Ollama")
        return embeddings

    def generate(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        options: dict[str, Any] | None = None,
        format: Literal["json"] | None = None,
    ) -> str:
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        if options:
            payload["options"] = options
        if format:
            payload["format"] = format
        data = self._request("POST", "/api/generate", payload)
        return str(data.get("response", "")).strip()


def _error_detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", response.text))
    except ValueError:
        return response.text


class OllamaEmbedder:
    """Embedder backed by an Ollama embedding model (default ``nomic-embed-text``)."""

    def __init__(self, client: OllamaClient, model: str = "nomic-embed-text"):
        self.client = client
        self.model = model

    def available(self) -> bool:
        return self.client.is_available()

    def embed(self, texts: list[str], kind: Literal["document", "query"] = "document"):
        if "nomic" in self.model:
            # nomic-embed-text expects task prefixes for asymmetric retrieval.
            prefix = "search_query: " if kind == "query" else "search_document: "
            texts = [prefix + t for t in texts]
        return self.client.embed(self.model, texts)
