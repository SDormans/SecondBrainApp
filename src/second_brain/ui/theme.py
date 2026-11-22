"""App-wide Qt theme derived from the Lovable/shadcn "slate" design tokens.

The design reference (a Lovable React project) defines its look as CSS custom properties
(``--background``, ``--primary``, ``--radius`` ...). The same tokens are mirrored here as
hex colours so the PySide6 UI matches it; new screens should use these tokens instead of
hard-coded colours.
"""

from __future__ import annotations

import atexit
import logging
import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)


class ThemeMode(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class Theme:
    name: str
    background: str
    foreground: str
    card: str
    primary: str
    primary_foreground: str
    secondary: str
    muted: str
    muted_foreground: str
    accent: str
    accent_foreground: str
    destructive: str
    border: str
    input: str
    ring: str
    sidebar: str
    sidebar_primary: str
    sidebar_primary_foreground: str
    link: str
    radius: int = 10  # --radius: 0.625rem

    @property
    def is_dark(self) -> bool:
        return self.name == ThemeMode.DARK


LIGHT = Theme(
    name=ThemeMode.LIGHT,
    background="#ffffff",
    foreground="#020617",
    card="#ffffff",
    primary="#0f172a",
    primary_foreground="#f8fafc",
    secondary="#f1f5f9",
    muted="#f1f5f9",
    muted_foreground="#64748b",
    accent="#f1f5f9",
    accent_foreground="#0f172a",
    destructive="#dc2626",
    border="#e2e8f0",
    input="#e2e8f0",
    ring="#94a3b8",
    sidebar="#f8fafc",
    sidebar_primary="#0f172a",
    sidebar_primary_foreground="#f8fafc",
    link="#2563eb",
)

DARK = Theme(
    name=ThemeMode.DARK,
    background="#020617",
    foreground="#f8fafc",
    card="#0f172a",
    primary="#e2e8f0",
    primary_foreground="#0f172a",
    secondary="#1e293b",
    muted="#1e293b",
    muted_foreground="#94a3b8",
    accent="#1e293b",
    accent_foreground="#f8fafc",
    destructive="#f87171",
    border="#1b202e",  # white at 10% over the background
    input="#26303f",  # white at 15% over the background
    ring="#6b7280",
    sidebar="#0f172a",
    sidebar_primary="#1d4ed8",
    sidebar_primary_foreground="#f8fafc",
    link="#60a5fa",
)


def resolve_theme(mode: str | ThemeMode) -> Theme:
    try:
        mode = ThemeMode(mode)
    except ValueError:
        log.warning("Unknown theme %r; following the system setting", mode)
        mode = ThemeMode.SYSTEM
    if mode == ThemeMode.SYSTEM:
        hints = QGuiApplication.styleHints() if QGuiApplication.instance() else None
        dark = hints is not None and hints.colorScheme() == Qt.ColorScheme.Dark
        return DARK if dark else LIGHT
    return DARK if mode == ThemeMode.DARK else LIGHT


def build_palette(t: Theme) -> QPalette:
    p = QPalette()
    role = QPalette.ColorRole
    for r, colour in (
        (role.Window, t.background),
        (role.WindowText, t.foreground),
        (role.Base, t.background),
        (role.AlternateBase, t.muted),
        (role.Text, t.foreground),
        (role.Button, t.secondary),
        (role.ButtonText, t.foreground),
        (role.ToolTipBase, t.primary),
        (role.ToolTipText, t.primary_foreground),
        (role.PlaceholderText, t.muted_foreground),
        (role.Highlight, t.sidebar_primary),
        (role.HighlightedText, t.sidebar_primary_foreground),
        (role.Link, t.link),
        (role.BrightText, t.destructive),
        (role.Mid, t.border),
        (role.Midlight, t.border),
        (role.Dark, t.ring),
    ):
        p.setColor(r, QColor(colour))
    for r in (role.Text, role.WindowText, role.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, r, QColor(t.muted_foreground))
    return p


_CHECK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" '
    'stroke-width="3" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M20 6 9 17l-5-5"/></svg>'
)
_CHEVRON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="{d}"/></svg>'
)
_icon_root: Path | None = None


def _icons(t: Theme) -> dict[str, str]:
    """Write the theme's indicator SVGs (stylesheets can only load images from files)."""
    global _icon_root
    if _icon_root is None:
        _icon_root = Path(tempfile.mkdtemp(prefix="second-brain-theme-"))
        atexit.register(shutil.rmtree, _icon_root, ignore_errors=True)
    folder = _icon_root / t.name
    folder.mkdir(exist_ok=True)
    shapes = {
        "check": _CHECK_SVG.format(c=t.primary_foreground),
        "down": _CHEVRON_SVG.format(c=t.muted_foreground, d="m6 9 6 6 6-6"),
        "up": _CHEVRON_SVG.format(c=t.muted_foreground, d="m18 15-6-6-6 6"),
    }
    paths = {}
    for name, svg in shapes.items():
        path = folder / f"{name}.svg"
        path.write_text(svg, encoding="utf-8")
        paths[name] = path.as_posix()
    return paths


def build_stylesheet(t: Theme) -> str:
    r, r_md, r_sm = t.radius, t.radius - 2, t.radius - 4
    icon = _icons(t)
    return f"""
QWidget {{
    background-color: {t.background};
    color: {t.foreground};
    font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif;
    font-size: 10pt;
}}
QMainWindow::separator, QSplitter::handle {{ background: {t.border}; width: 1px; height: 1px; }}

QToolBar {{
    background: {t.background};
    border: none;
    border-bottom: 1px solid {t.border};
    padding: 6px 8px;
    spacing: 8px;
}}
QMenuBar {{ background: {t.background}; border-bottom: 1px solid {t.border}; }}
QMenuBar::item {{ padding: 4px 10px; border-radius: {r_sm}px; background: transparent; }}
QMenuBar::item:selected {{ background: {t.accent}; color: {t.accent_foreground}; }}
QMenu {{
    background: {t.card};
    border: 1px solid {t.border};
    border-radius: {r_md}px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: {r_sm}px; }}
QMenu::item:selected {{ background: {t.accent}; color: {t.accent_foreground}; }}
QMenu::item:disabled {{ color: {t.muted_foreground}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 6px; }}

QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t.background};
    border: 1px solid {t.input};
    border-radius: {r_md}px;
    padding: 6px 10px;
    selection-background-color: {t.sidebar_primary};
    selection-color: {t.sidebar_primary_foreground};
}}
QPlainTextEdit, QTextEdit, QTextBrowser {{ padding: 10px 12px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {t.ring};
}}
QLineEdit:disabled, QComboBox:disabled {{ color: {t.muted_foreground}; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{icon["down"]}"); width: 14px; height: 14px; }}
QAbstractSpinBox {{ padding-right: 24px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    border: none; width: 20px; background: transparent;
}}
QAbstractSpinBox::up-arrow {{ image: url("{icon["up"]}"); width: 12px; height: 12px; }}
QAbstractSpinBox::down-arrow {{ image: url("{icon["down"]}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {t.card};
    border: 1px solid {t.border};
    border-radius: {r_md}px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_foreground};
    outline: 0;
}}
QLineEdit#noteTitle {{
    border: none;
    padding: 4px 2px;
    font-size: 18pt;
    font-weight: 600;
}}

QPushButton {{
    background: {t.background};
    border: 1px solid {t.input};
    border-radius: {r_md}px;
    padding: 6px 14px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {t.accent}; color: {t.accent_foreground}; }}
QPushButton:pressed {{ background: {t.secondary}; }}
QPushButton:default, QPushButton[variant="primary"] {{
    background: {t.primary};
    color: {t.primary_foreground};
    border: 1px solid {t.primary};
}}
QPushButton:default:hover, QPushButton[variant="primary"]:hover {{
    background: {t.foreground if t.is_dark else "#1e293b"};
}}
QPushButton[variant="destructive"] {{
    background: {t.destructive};
    color: #f8fafc;
    border: 1px solid {t.destructive};
}}
QPushButton:disabled {{ color: {t.muted_foreground}; background: {t.muted}; }}

QListWidget, QListView, QTreeView {{
    background: {t.sidebar};
    border: none;
    outline: 0;
    padding: 4px;
}}
QListWidget::item, QListView::item {{
    padding: 8px 10px;
    margin: 1px 0;
    border-radius: {r_md}px;
}}
QListWidget::item:hover, QListView::item:hover {{ background: {t.accent}; }}
QListWidget::item:selected, QListView::item:selected {{
    background: {t.accent};
    color: {t.accent_foreground};
    border-left: 3px solid {t.sidebar_primary};
}}

QTabWidget::pane {{ border: none; border-top: 1px solid {t.border}; top: -1px; }}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: {t.muted};
    color: {t.muted_foreground};
    padding: 6px 14px;
    margin: 4px 0 6px 0;
    border: none;
}}
QTabBar::tab:first {{ border-top-left-radius: {r}px; border-bottom-left-radius: {r}px; }}
QTabBar::tab:last {{ border-top-right-radius: {r}px; border-bottom-right-radius: {r}px; }}
QTabBar::tab:selected {{
    background: {t.card if t.is_dark else t.background};
    color: {t.foreground};
    border: 1px solid {t.border};
    border-radius: {r_md}px;
}}
QTabBar::tab:hover:!selected {{ color: {t.foreground}; }}

QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{
    background: {t.sidebar};
    color: {t.muted_foreground};
    padding: 8px 12px;
    border-bottom: 1px solid {t.border};
    text-align: left;
}}

QGroupBox {{
    background: {t.card};
    border: 1px solid {t.border};
    border-radius: {r}px;
    margin-top: 18px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {t.foreground};
    background: transparent;
}}
QGroupBox QWidget {{ background: {t.card}; font-weight: 400; }}
QGroupBox QLineEdit, QGroupBox QComboBox, QGroupBox QDoubleSpinBox {{
    background: {t.background};
}}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {t.primary if t.is_dark else t.ring};
    border-radius: 4px;
    background: {t.background};
}}
QCheckBox::indicator:checked {{
    background: {t.primary};
    border: 1px solid {t.primary};
    image: url("{icon["check"]}");
}}
QCheckBox::indicator:disabled {{ border-color: {t.border}; background: {t.muted}; }}
QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {t.muted_foreground}; }}

QStatusBar {{
    background: {t.sidebar};
    color: {t.muted_foreground};
    border-top: 1px solid {t.border};
}}
QStatusBar QLabel {{ color: {t.muted_foreground}; }}
QProgressBar {{
    background: {t.secondary};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {t.primary}; border-radius: 4px; }}
QToolTip {{
    background: {t.primary};
    color: {t.primary_foreground};
    border: none;
    border-radius: {r_sm}px;
    padding: 4px 8px;
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{
    background: {t.border}; border-radius: 4px; min-height: 24px; min-width: 24px;
}}
QScrollBar::handle:hover {{ background: {t.ring}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; width: 0; height: 0;
}}
"""


_current: Theme | None = None
_follow_system_connected = False


def current_theme() -> Theme:
    return _current or LIGHT


def apply_theme(app: QApplication, mode: str | ThemeMode = ThemeMode.SYSTEM) -> Theme:
    """Apply the palette and stylesheet for ``mode``; "system" also tracks OS changes."""
    global _current, _follow_system_connected
    try:
        mode = ThemeMode(mode)
    except ValueError:
        mode = ThemeMode.SYSTEM
    theme = resolve_theme(mode)
    app.setStyle("Fusion")  # native Windows style ignores parts of the stylesheet
    app.setPalette(build_palette(theme))
    app.setStyleSheet(build_stylesheet(theme))
    app.setProperty("themeMode", mode.value)
    _current = theme

    if not _follow_system_connected:
        hints = QGuiApplication.styleHints()

        def _on_scheme_changed(_scheme) -> None:
            if app.property("themeMode") == ThemeMode.SYSTEM.value:
                apply_theme(app, ThemeMode.SYSTEM)

        hints.colorSchemeChanged.connect(_on_scheme_changed)
        _follow_system_connected = True
    return theme
