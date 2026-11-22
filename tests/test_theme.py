"""Theme tokens, stylesheet generation and the Appearance setting."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QPalette  # noqa: E402

from second_brain.config import Settings  # noqa: E402
from second_brain.ui import theme  # noqa: E402


def test_resolve_explicit_modes(qapp):
    assert theme.resolve_theme("light") is theme.LIGHT
    assert theme.resolve_theme("dark") is theme.DARK
    assert theme.resolve_theme("system") in (theme.LIGHT, theme.DARK)


def test_unknown_mode_falls_back_to_system(qapp):
    assert theme.resolve_theme("neon") is theme.resolve_theme("system")


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK])
def test_stylesheet_uses_tokens(t):
    qss = theme.build_stylesheet(t)
    for colour in (t.background, t.foreground, t.primary, t.border, t.muted_foreground):
        assert colour in qss
    assert f"border-radius: {t.radius}px" in qss
    assert qss.count("{") == qss.count("}")


def test_apply_theme_sets_palette_and_stylesheet(qapp):
    applied = theme.apply_theme(qapp, "dark")
    assert applied is theme.DARK and theme.current_theme() is theme.DARK
    assert qapp.palette().color(QPalette.ColorRole.Window).name() == theme.DARK.background
    assert theme.DARK.background in qapp.styleSheet()
    assert qapp.property("themeMode") == "dark"
    theme.apply_theme(qapp, "light")
    assert qapp.palette().color(QPalette.ColorRole.Window).name() == theme.LIGHT.background


def test_settings_default_and_dialog_round_trip(qtbot):
    from second_brain.ui.dialogs import SettingsDialog

    assert Settings().theme == "system"
    dialog = SettingsDialog(Settings(theme="dark"))
    qtbot.addWidget(dialog)
    assert dialog.result_settings().theme == "dark"
    dialog.theme.setCurrentIndex(dialog.theme.findData("light"))
    assert dialog.result_settings().theme == "light"


def test_wiki_link_color_follows_theme_change(qapp):
    from PySide6.QtGui import QColor

    from second_brain.ui.editor import MarkdownEdit

    theme.apply_theme(qapp, "light")
    edit = MarkdownEdit()
    edit.setPlainText("see [[Other note]]")
    edit.show()  # visible widgets receive the theme's change events synchronously

    def link_color() -> str:
        block = edit.document().firstBlock()
        ranges = block.layout().formats()
        return next(
            r.format.foreground().color().name() for r in ranges if r.format.fontUnderline()
        )

    assert link_color() == QColor(theme.resolve_theme("light").link).name()
    theme.apply_theme(qapp, "dark")
    qapp.processEvents()
    assert link_color() == QColor(theme.resolve_theme("dark").link).name()


def test_theme_change_does_not_mark_note_dirty(qapp):
    from second_brain.ui.editor import NoteEditor
    from second_brain.vault.note import Note

    theme.apply_theme(qapp, "light")
    editor = NoteEditor()
    editor.load(Note(id="a", title="A", body="see [[Other note]]"))
    assert not editor.dirty
    theme.apply_theme(qapp, "dark")
    qapp.processEvents()
    assert not editor.dirty
