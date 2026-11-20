import os

from second_brain.index.indexer import Indexer, rebuild_index
from second_brain.index.store import IndexStore
from second_brain.vault.note import Note


def _seed(vault):
    vault.create_note(
        "Photosynthesis", "Plants convert sunlight into chemical energy.", tags=["biology"]
    )
    vault.create_note(
        "Mitochondria",
        "The powerhouse of the cell. See [[Photosynthesis]].",
        tags=["biology", "cells"],
    )
    vault.create_note(
        "Rust ownership",
        "Borrow checker rules for memory safety.",
        tags=["programming"],
        status="processed",
    )


def test_rebuild_indexes_every_note(vault, indexer, store):
    _seed(vault)
    stats = indexer.rebuild_index()
    assert (stats.indexed, stats.errors, stats.embedded) == (3, 0, 3)
    assert store.count() == 3
    assert {r.title for r in store.list_notes(status="inbox")} == {"Photosynthesis", "Mitochondria"}
    photo = store.find_by_title("photosynthesis")
    assert [b.title for b in store.backlinks(photo.path)] == ["Mitochondria"]
    assert store.tag_counts()["biology"] == 2


def test_rebuild_from_scratch_after_index_deleted(vault, embedder):
    _seed(vault)
    rebuild_index(vault, embedder)
    os.remove(vault.index_path)
    stats = rebuild_index(vault, embedder)
    assert stats.indexed == 3
    store = IndexStore(vault.index_path)
    try:
        assert store.count() == 3 and store.embedded_count() == 3
    finally:
        store.close()


def test_rebuild_drops_stale_rows_and_survives_bad_files(vault, indexer, store):
    _seed(vault)
    indexer.rebuild_index(embed=False)
    store.upsert_note(
        path="notes/ghost.md",
        note_id="x",
        title="Ghost",
        body="",
        status="inbox",
        source_type="note",
        tags=[],
        captured_at="",
        mtime=0,
        content_hash="h",
        links=[],
    )
    (vault.notes_dir / "binary.md").write_bytes(b"\xff\xfe\x00garbage")
    stats = indexer.rebuild_index(embed=False)
    assert stats.errors == 0 and stats.indexed == 4
    assert store.get_record("notes/ghost.md") is None


def test_incremental_create_modify_delete(vault, indexer, store):
    note = vault.create_note("Draft", "first version")
    assert indexer.index_path(note.path)
    assert not indexer.index_path(note.path)  # unchanged content is skipped
    vault.save_note(note.with_changes(body="second version"))
    assert indexer.index_path(note.path)
    assert "second" in store.body(store.get_record("notes/draft.md").rowid)
    note.path.unlink()
    assert indexer.index_path(note.path)  # missing file -> removed
    assert store.count() == 0


def test_sync_catches_up_offline_changes(vault, indexer, store):
    _seed(vault)
    indexer.rebuild_index(embed=False)
    vault.create_note("New while closed", "added")
    (vault.notes_dir / "rust-ownership.md").unlink()
    stats = indexer.sync()
    assert (stats.indexed, stats.removed) == (1, 1)
    assert store.count() == 3


def test_rebuild_without_embedder_or_ollama(vault, store, embedder):
    _seed(vault)
    stats = Indexer(vault, store, None).rebuild_index()
    assert stats.indexed == 3 and stats.embedding_skipped_reason
    embedder.up = False
    stats = Indexer(vault, store, embedder).rebuild_index()
    assert stats.indexed == 3 and stats.embedded == 0 and "down" in stats.embedding_skipped_reason


def test_embedding_model_change_rebuilds_vectors(vault, store, embedder):
    _seed(vault)
    indexer = Indexer(vault, store, embedder)
    indexer.rebuild_index()
    assert indexer.embed_pending() == 0
    embedder.model = "other-model"
    embedder.dim = 32
    assert indexer.embed_pending() == 3
    assert store.embedding_model() == "other-model"


def test_reindex_after_edit_requires_new_embedding(vault, indexer, store):
    note = vault.create_note("Edited", "v1")
    indexer.rebuild_index()
    vault.save_note(note.with_changes(body="v2"))
    indexer.index_path(note.path)
    assert store.embedded_count() == 0
    assert indexer.embed_pending() == 1


def test_files_outside_notes_dir_are_indexed(vault, indexer, store):
    (vault.root / "projects").mkdir()
    (vault.root / "projects" / "plan.md").write_text(
        Note.new("Plan", "x").to_markdown(), encoding="utf-8"
    )
    indexer.rebuild_index(embed=False)
    assert store.get_record("projects/plan.md").title == "Plan"
