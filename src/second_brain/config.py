"""Application settings, stored as JSON in the per-user config folder (not in the vault)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .vault.atomic import atomic_write_text

log = logging.getLogger(__name__)

APP_NAME = "SecondBrain"


def config_dir() -> Path:
    override = os.environ.get("SECOND_BRAIN_CONFIG_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_NAME
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME.lower()


def settings_path() -> Path:
    return config_dir() / "settings.json"


@dataclass
class Settings:
    vault_path: str | None = None
    recent_vaults: list[str] = field(default_factory=list)

    # Local AI (Ollama)
    ollama_url: str = "http://localhost:11434"
    ollama_text_model: str = "llama3.1:8b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_timeout_s: float = 120.0
    ai_enabled: bool = True
    """Local AI assistance (summaries, tag/link suggestions) via Ollama."""
    semantic_search_enabled: bool = True
    """Compute embeddings via Ollama and allow semantic search."""
    cloud_ai_enabled: bool = False
    """Cloud AI is opt-in and OFF by default; no cloud provider ships in v1."""

    # Appearance
    theme: str = "system"
    """"system" follows the Windows light/dark setting; or "light" / "dark"."""

    # Capture
    fetch_url_content: bool = True
    copy_imported_files: bool = True
    whisper_model: str = "large-v3"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read settings %s (%s); using defaults", path, exc)
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        atomic_write_text(path or settings_path(), json.dumps(asdict(self), indent=2))

    def remember_vault(self, path: str) -> None:
        self.vault_path = path
        self.recent_vaults = [path] + [p for p in self.recent_vaults if p != path][:9]
