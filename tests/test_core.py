import time

import pytest

from second_brain.__main__ import main
from second_brain.config import Settings
from second_brain.core import AppCore
from second_brain.search import SearchMode


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def core(tmp_path):
    # Point at a closed port: behaves like "Ollama is not running".
    settings = Settings(ollama_url="http://127.0.0.1:9", ollama_timeout_s=1.0)
    app = AppCore(settings)
    app.open_vault(tmp_path / "vault")
    yield app
    app.shutdown()


def test_watcher_indexes_create_modify_delete(core):
    vault = core.vault
    note = vault.create_note("Watched", "aardvark")
    assert wait_for(lambda: core.search.keyword("aardvark"))
    vault.save_note(note.with_changes(body="platypus"))
    assert wait_for(lambda: core.search.keyword("platypus") and not core.search.keyword("aardvark"))
    note.path.unlink()
    assert wait_for(lambda: core.store.count() == 0)


def test_drop_folder_import(core):
    (core.vault.import_dir / "dropped.txt").write_text("Dropped note\n\nwombat", encoding="utf-8")
    assert wait_for(lambda: core.search.keyword("wombat"), timeout=15)
    assert not (core.vault.import_dir / "dropped.txt").exists()


def test_semantic_search_without_ollama_falls_back(core):
    core.save_note(core.vault.create_note("Fallback", "quokka"))
    outcome = core.search.search("quokka", SearchMode.SEMANTIC)
    assert outcome.mode == SearchMode.KEYWORD and [r.title for r in outcome.results] == ["Fallback"]
    stats = core.rebuild_index()
    assert stats.indexed == 1 and stats.embedding_skipped_reason


def test_vault_log_file_written(core):
    core.rebuild_index()
    log_file = core.vault.log_dir / "secondbrain.log"
    assert log_file.exists() and "Rebuild" in log_file.read_text(encoding="utf-8")


def test_cli_rebuild_index(tmp_path, capsys):
    from second_brain.vault.vault import Vault

    vault = Vault.open(tmp_path / "v")
    vault.create_note("One", "x")
    vault.create_note("Two", "y")
    assert main(["rebuild-index", "--vault", str(vault.root), "--no-embed"]) == 0
    assert "Indexed 2 notes" in capsys.readouterr().out


def test_fix_std_streams_handles_missing_and_narrow_streams(monkeypatch):
    import io
    import sys

    from second_brain.__main__ import _fix_std_streams

    narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", narrow)
    monkeypatch.setattr(sys, "stderr", None)
    _fix_std_streams()
    print("Brain \U0001f9e0 日本語")
    sys.stderr.write("progress bar\n")
    sys.stdout.flush()
    assert rb"Brain \U0001f9e0" in narrow.buffer.getvalue()
    sys.stderr.close()
