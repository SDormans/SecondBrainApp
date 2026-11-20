"""Entry point: ``second-brain`` (GUI) or ``second-brain-cli rebuild-index --vault PATH``."""

from __future__ import annotations

import argparse
import os
import sys

from .config import Settings
from .logging_setup import configure_logging, install_exception_hooks


def _fix_std_streams() -> None:
    """Make stdout/stderr safe to write to on Windows.

    A GUI launcher (pythonw / ``second-brain.exe``) has no console, so both are ``None`` and
    any library progress bar (e.g. the faster-whisper model download) crashes. Redirected
    output uses the ANSI code page (cp1252), which cannot encode many note titles.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
        elif hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="backslashreplace")
            except (OSError, ValueError):
                pass


def _cli_rebuild(vault_path: str, embed: bool) -> int:
    from .core import AppCore

    settings = Settings.load()
    settings.semantic_search_enabled = settings.semantic_search_enabled and embed
    core = AppCore(settings)
    core.open_vault(vault_path, watch=False)
    stats = core.rebuild_index(
        progress=lambda done, total, msg: print(f"\r{done}/{total} {msg}"[:100], end="", flush=True)
    )
    print()
    print(
        f"Indexed {stats.indexed} notes, {stats.errors} errors, {stats.embedded} embeddings"
        + (
            f" (embeddings skipped: {stats.embedding_skipped_reason})"
            if stats.embedding_skipped_reason
            else ""
        )
    )
    core.shutdown()
    return 1 if stats.errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="second-brain")
    parser.add_argument("--vault", help="vault folder to open")
    sub = parser.add_subparsers(dest="command")
    rebuild = sub.add_parser(
        "rebuild-index", help="rebuild the vault's index from its markdown files"
    )
    rebuild.add_argument("--vault", default=argparse.SUPPRESS, help="vault folder to rebuild")
    rebuild.add_argument("--no-embed", action="store_true", help="skip Ollama embeddings")
    _fix_std_streams()
    args = parser.parse_args(argv)

    configure_logging()
    install_exception_hooks()
    if args.command == "rebuild-index":
        if not args.vault:
            parser.error("rebuild-index requires --vault")
        return _cli_rebuild(args.vault, embed=not args.no_embed)

    from .ui.app import run_gui

    return run_gui(args.vault)


if __name__ == "__main__":
    sys.exit(main())
