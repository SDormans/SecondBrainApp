"""The vault: a user-selected folder of markdown notes that is the single source of truth."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .atomic import atomic_write_text, is_temp_file
from .note import Note, content_hash, now_iso, parse_note, slugify

log = logging.getLogger(__name__)

VAULT_FORMAT_VERSION = 1


class VaultError(Exception):
    pass


class ConflictError(VaultError):
    """The note changed on disk since it was loaded; saving would overwrite someone else's edit."""

    def __init__(self, path: Path):
        super().__init__(f"{path} was modified outside the editor")
        self.path = path


class Vault:
    META_DIR = ".secondbrain"
    NOTES_DIR = "notes"
    ATTACHMENTS_DIR = "attachments"
    IMPORT_DIR = "_import"

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    # ----------------------------------------------------------------- layout
    @property
    def meta_dir(self) -> Path:
        return self.root / self.META_DIR

    @property
    def index_path(self) -> Path:
        return self.meta_dir / "index.db"

    @property
    def log_dir(self) -> Path:
        return self.meta_dir / "logs"

    @property
    def trash_dir(self) -> Path:
        return self.meta_dir / "trash"

    @property
    def notes_dir(self) -> Path:
        return self.root / self.NOTES_DIR

    @property
    def attachments_dir(self) -> Path:
        return self.root / self.ATTACHMENTS_DIR

    @property
    def import_dir(self) -> Path:
        """Drop folder watched for new PDFs, audio files and Kindle clippings."""
        return self.root / self.IMPORT_DIR

    @classmethod
    def open(cls, root: Path | str, create: bool = True) -> Vault:
        """Open a vault, initialising the folder layout if needed (existing markdown is kept)."""
        vault = cls(Path(root))
        if not vault.root.exists():
            if not create:
                raise VaultError(f"Vault folder does not exist: {vault.root}")
            vault.root.mkdir(parents=True)
        if not vault.root.is_dir():
            raise VaultError(f"Not a folder: {vault.root}")
        for folder in (
            vault.meta_dir,
            vault.log_dir,
            vault.notes_dir,
            vault.attachments_dir,
            vault.import_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)
        marker = vault.meta_dir / "vault.json"
        if not marker.exists():
            atomic_write_text(
                marker,
                json.dumps(
                    {"format_version": VAULT_FORMAT_VERSION, "created_at": now_iso()}, indent=2
                ),
            )
        return vault

    # ------------------------------------------------------------------ paths
    def is_note_path(self, path: Path) -> bool:
        path = Path(path)
        if path.suffix.lower() != ".md" or is_temp_file(path):
            return False
        try:
            rel = path.resolve().relative_to(self.root)
        except ValueError:
            return False
        # Hidden folders (.secondbrain, .git, .obsidian, ...) and the drop folder are not notes.
        return (
            not any(part.startswith(".") for part in rel.parts) and rel.parts[0] != self.IMPORT_DIR
        )

    def iter_note_paths(self) -> Iterator[Path]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            current = Path(dirpath)
            dirnames[:] = sorted(
                d
                for d in dirnames
                if not d.startswith(".") and not (current == self.root and d == self.IMPORT_DIR)
            )
            for name in sorted(filenames):
                path = current / name
                if self.is_note_path(path):
                    yield path

    def relpath(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    def abspath(self, rel: str) -> Path:
        return self.root / Path(rel)

    # ------------------------------------------------------------------ notes
    def read_note(self, path: Path) -> Note:
        return self.read_note_with_hash(path)[0]

    def read_note_with_hash(self, path: Path) -> tuple[Note, str]:
        path = Path(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        return parse_note(text, path=path), content_hash(text)

    def file_hash(self, path: Path) -> str | None:
        try:
            return content_hash(Path(path).read_text(encoding="utf-8", errors="replace"))
        except FileNotFoundError:
            return None

    def path_for_new_note(self, note: Note, folder: Path | None = None) -> Path:
        folder = folder or self.notes_dir
        slug = slugify(note.title)
        candidate = folder / f"{slug}.md"
        if candidate.exists():
            candidate = folder / f"{slug}-{note.id[-6:]}.md"
        counter = 2
        while candidate.exists():
            candidate = folder / f"{slug}-{note.id[-6:]}-{counter}.md"
            counter += 1
        return candidate

    def save_note(self, note: Note, expected_hash: str | None = None) -> Path:
        """Atomically write ``note``. If ``expected_hash`` is given and the file on disk no
        longer matches it, :class:`ConflictError` is raised instead of overwriting."""
        if note.path is None:
            note.path = self.path_for_new_note(note)
        elif expected_hash is not None:
            current = self.file_hash(note.path)
            if current is not None and current != expected_hash:
                raise ConflictError(note.path)
        atomic_write_text(note.path, note.to_markdown())
        log.info("Saved note %s", self.relpath(note.path))
        return note.path

    def create_note(self, title: str, body: str = "", **fields) -> Note:
        note = Note.new(title, body, **fields)
        self.save_note(note)
        return note

    def save_conflict_copy(self, note: Note) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        original = note.path or self.path_for_new_note(note)
        copy = note.with_changes(
            id=f"{note.id}-conflict-{stamp}",
            title=f"{note.title} (conflict {stamp})",
            path=original.with_name(f"{original.stem}.conflict-{stamp}.md"),
        )
        atomic_write_text(copy.path, copy.to_markdown())
        log.warning("Saved conflict copy %s", self.relpath(copy.path))
        return copy.path

    def delete_note(self, path: Path) -> Path:
        """Move a note to ``.secondbrain/trash`` (never hard-delete user data)."""
        path = Path(path)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        target = self.trash_dir / f"{stamp}-{path.name}"
        shutil.move(str(path), target)
        log.info("Moved %s to trash", path.name)
        return target

    # ------------------------------------------------------------ attachments
    def store_attachment(self, source: Path, move: bool = False) -> str:
        """Copy (or move) a file into ``attachments/`` and return its vault-relative path."""
        source = Path(source)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        target = self.attachments_dir / source.name
        counter = 2
        while target.exists():
            target = self.attachments_dir / f"{source.stem}-{counter}{source.suffix}"
            counter += 1
        if move:
            shutil.move(str(source), target)
        else:
            shutil.copy2(source, target)
        return self.relpath(target)

    # ----------------------------------------------------------------- export
    def export_zip(self, destination: Path, include_meta: bool = False) -> Path:
        """Write the vault to a zip archive (the rebuildable index is skipped by default)."""
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=destination.parent, prefix=".export-", suffix=".tmp")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for dirpath, dirnames, filenames in os.walk(self.root):
                    current = Path(dirpath)
                    if not include_meta and current == self.root and self.META_DIR in dirnames:
                        dirnames.remove(self.META_DIR)
                    for name in filenames:
                        path = current / name
                        if path.resolve() in (destination, tmp.resolve()) or is_temp_file(path):
                            continue
                        archive.write(path, Path(self.root.name) / path.relative_to(self.root))
            os.replace(tmp, destination)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        log.info("Exported vault to %s", destination)
        return destination
