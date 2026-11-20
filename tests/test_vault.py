import os
import zipfile

import pytest

from second_brain.vault import atomic
from second_brain.vault.atomic import atomic_write_text
from second_brain.vault.note import Note
from second_brain.vault.vault import ConflictError, Vault


def test_open_creates_layout(tmp_path):
    vault = Vault.open(tmp_path / "v")
    for folder in (vault.meta_dir, vault.notes_dir, vault.attachments_dir, vault.import_dir):
        assert folder.is_dir()
    assert (vault.meta_dir / "vault.json").exists()
    assert Vault.open(tmp_path / "v").root == vault.root  # reopen is idempotent


def test_save_and_read(vault):
    note = vault.create_note("My Note", "hello")
    assert note.path.parent == vault.notes_dir
    assert note.path.name == "my-note.md"
    loaded = vault.read_note(note.path)
    assert loaded.id == note.id and loaded.body == "hello\n"
    second = vault.create_note("My Note", "again")
    assert second.path != note.path


def test_iter_note_paths_skips_hidden_import_and_temp(vault):
    vault.create_note("Visible")
    (vault.meta_dir / "x.md").write_text("hidden")
    (vault.import_dir / "dropped.md").write_text("not yet a note")
    (vault.notes_dir / ".a.md.123.tmp").write_text("temp")
    (vault.root / ".obsidian").mkdir()
    (vault.root / ".obsidian" / "y.md").write_text("hidden")
    names = [p.name for p in vault.iter_note_paths()]
    assert names == ["visible.md"]


def test_conflict_detection(vault):
    note = vault.create_note("Conflict")
    _, digest = vault.read_note_with_hash(note.path)
    note.path.write_text(note.path.read_text() + "external edit\n", encoding="utf-8")
    with pytest.raises(ConflictError):
        vault.save_note(note.with_changes(body="mine"), expected_hash=digest)
    copy = vault.save_conflict_copy(note.with_changes(body="mine"))
    assert copy.exists() and "external edit" in note.path.read_text()


def test_atomic_write_keeps_original_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "note.md"
    atomic_write_text(target, "original")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(atomic.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "new content")
    assert target.read_text() == "original"
    assert os.listdir(tmp_path) == ["note.md"]  # temp file cleaned up


def test_atomic_write_retries_permission_errors(tmp_path, monkeypatch):
    target = tmp_path / "note.md"
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked by antivirus")
        real_replace(src, dst)

    monkeypatch.setattr(atomic.os, "replace", flaky)
    monkeypatch.setattr(atomic.time, "sleep", lambda _: None)
    atomic_write_text(target, "ok")
    assert target.read_text() == "ok" and calls["n"] == 3


def test_delete_moves_to_trash(vault):
    note = vault.create_note("Bye")
    trashed = vault.delete_note(note.path)
    assert not note.path.exists() and trashed.exists() and trashed.parent == vault.trash_dir


def test_export_zip_excludes_index(vault, tmp_path):
    vault.create_note("Exported", "content")
    (vault.attachments_dir / "a.pdf").write_bytes(b"%PDF")
    vault.index_path.write_bytes(b"db")
    archive = vault.export_zip(tmp_path / "out" / "vault.zip")
    names = zipfile.ZipFile(archive).namelist()
    assert "vault/notes/exported.md" in names
    assert "vault/attachments/a.pdf" in names
    assert not any(".secondbrain" in n for n in names)


def test_export_zip_inside_vault_skips_itself(vault):
    vault.create_note("N")
    archive = vault.export_zip(vault.root / "backup.zip")
    assert "vault/backup.zip" not in zipfile.ZipFile(archive).namelist()


def test_store_attachment_avoids_overwrite(vault, tmp_path):
    src = tmp_path / "file.pdf"
    src.write_bytes(b"1")
    assert vault.store_attachment(src) == "attachments/file.pdf"
    assert vault.store_attachment(src) == "attachments/file-2.pdf"
    assert src.exists()


def test_note_path_is_assigned_once(vault):
    note = Note.new("Same")
    first = vault.save_note(note)
    assert vault.save_note(note) == first
