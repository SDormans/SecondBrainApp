"""Qt application bootstrap."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from ..config import Settings
from ..core import AppCore
from .main_window import MainWindow
from .theme import apply_theme

log = logging.getLogger(__name__)


def run_gui(vault_path: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Second Brain")
    app.setOrganizationName("SecondBrain")

    settings = Settings.load()
    apply_theme(app, settings.theme)
    core = AppCore(settings)
    window = MainWindow(core)
    window.show()

    previous_hook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        previous_hook(exc_type, exc, tb)
        QMessageBox.critical(
            window,
            "Unexpected error",
            f"{exc_type.__name__}: {exc}\n\nDetails were written to the log file.",
        )

    sys.excepthook = _excepthook

    if vault_path:
        window.open_vault(vault_path)  # explicit --vault: create it if missing
    elif settings.vault_path and Path(settings.vault_path).is_dir():
        window.open_vault(settings.vault_path)
    else:
        QMessageBox.information(
            window,
            "Welcome to Second Brain",
            "Choose (or create) a folder to use as your vault. Notes are plain "
            "markdown files inside it.",
        )
        window.choose_vault()
    return app.exec()
