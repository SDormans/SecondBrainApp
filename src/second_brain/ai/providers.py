"""Text-generation providers. Local (Ollama) is the default; cloud is opt-in."""

from __future__ import annotations

from typing import Protocol

from ..config import Settings
from .ollama import OllamaClient


class AIDisabled(Exception):
    """AI assistance is turned off in Settings."""


class TextModel(Protocol):
    name: str

    def generate(self, prompt: str, system: str | None = None) -> str: ...


class OllamaTextModel:
    def __init__(self, client: OllamaClient, model: str):
        self.client = client
        self.name = model

    def generate(self, prompt: str, system: str | None = None) -> str:
        return self.client.generate(
            self.name, prompt, system=system, options={"temperature": 0.2, "num_ctx": 8192}
        )


class CloudTextModel:
    """Extension point for an optional cloud provider (none is shipped in v1)."""

    name = "cloud"

    def generate(self, prompt: str, system: str | None = None) -> str:
        raise AIDisabled("Cloud AI is enabled but no cloud provider is configured in this version")


def select_text_model(settings: Settings, client: OllamaClient) -> TextModel:
    if not settings.ai_enabled:
        raise AIDisabled("AI assistance is turned off in Settings")
    if settings.cloud_ai_enabled:
        return CloudTextModel()
    return OllamaTextModel(client, settings.ollama_text_model)
