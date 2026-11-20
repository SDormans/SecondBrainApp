"""Smoke tests for the Qt UI (run headless with QT_QPA_PLATFORM=offscreen)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtWidgets")

from second_brain.config import Settings  # noqa: E402
from second_brain.core import AppCore  # noqa: E402


@pytest.fixture
def window(qtbot, tmp_path):
    from second_brain.ui.main_window import MainWindow

    core = AppCore(Settings(ollama_url="http://127.0.0.1:9", ollama_timeout_s=1.0))
    core.open_vault(tmp_path / "vault")
    core.vault.create_note("Inbox item", "Link to [[Other]]")
    core.vault.create_note("Other", "target", status="processed")
    core.indexer.rebuild_index(embed=False)
    win = MainWindow(core)
    qtbot.addWidget(win)
    win.show()
    yield win
    win.close()
    core.shutdown()


def test_main_window_opens(window):
    assert window.isVisible()
    assert "SecondBrain" in window.windowTitle() or window.windowTitle()


def test_settings_dialog_round_trip(qtbot):
    from second_brain.ui.dialogs import SettingsDialog

    settings = Settings(vault_path="C:/vault", ollama_text_model="qwen2.5:7b")
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    result = dialog.result_settings()
    assert result.ollama_text_model == "qwen2.5:7b"
    assert result.cloud_ai_enabled is False


def test_inbox_lists_only_inbox_notes(window, qtbot):
    window.refresh_lists()
    inbox = [window.inbox_list.item(i).text() for i in range(window.inbox_list.count())]
    assert any("Inbox item" in t for t in inbox) and not any("Other" in t for t in inbox)
    assert window.all_list.count() == 2


def test_edit_save_search_and_follow_link(window, qtbot):
    core = window.core
    rel = core.store.find_by_title("Inbox item").path
    window.open_path(rel)
    assert window.editor.note.title == "Inbox item"
    window.editor.body.setPlainText("Link to [[Other]] and narwhal")
    assert window.editor.dirty
    assert window.save_current()
    assert "narwhal" in core.vault.read_note(core.vault.abspath(rel)).body

    window.search_box.setText("narwhal")
    window.run_search()
    qtbot.waitUntil(lambda: window.results_list.count() == 1, timeout=5000)

    window.follow_link("Other")
    assert window.editor.note.title == "Other"
    window._refresh_backlinks()
    assert window.backlinks.count() == 1


def test_save_conflict_creates_copy(window, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    core = window.core
    rel = core.store.find_by_title("Other").path
    window.open_path(rel)
    path = core.vault.abspath(rel)
    path.write_text(path.read_text(encoding="utf-8") + "external\n", encoding="utf-8")
    window.editor.body.setPlainText("mine")
    window.save_current()
    assert "external" in path.read_text(encoding="utf-8")
    assert any("conflict" in p.name for p in path.parent.iterdir())


def test_export_zip(window, tmp_path, monkeypatch):
    import zipfile

    from PySide6.QtWidgets import QFileDialog, QMessageBox

    target = tmp_path / "export.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    window.export_zip()
    window.tasks._pool.shutdown(wait=True)
    assert target.exists() and len(zipfile.ZipFile(target).namelist()) >= 2
