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
