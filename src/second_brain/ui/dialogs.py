"""Settings and capture dialogs."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..ai.ollama import OllamaClient, OllamaError
from ..config import Settings
from .theme import ThemeMode


class SettingsDialog(QDialog):
    rebuild_requested = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self._settings = settings

        self.vault_path = QLineEdit(settings.vault_path or "")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_vault)
        vault_row = QHBoxLayout()
        vault_row.addWidget(self.vault_path, 1)
        vault_row.addWidget(browse)

        self.ollama_url = QLineEdit(settings.ollama_url)
        self.text_model = QLineEdit(settings.ollama_text_model)
        self.embed_model = QLineEdit(settings.ollama_embed_model)
        self.timeout = QDoubleSpinBox(
            minimum=5, maximum=1800, suffix=" s", value=settings.ollama_timeout_s
        )
        self.test_button = QPushButton("Test connection")
        self.test_result = QLabel()
        self.test_button.clicked.connect(self._test_connection)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)

        self.ai_enabled = QCheckBox("Local AI assistance (summaries, tag and link suggestions)")
        self.ai_enabled.setChecked(settings.ai_enabled)
        self.semantic = QCheckBox("Semantic search (Ollama embeddings)")
        self.semantic.setChecked(settings.semantic_search_enabled)
        self.cloud = QCheckBox(
            "Allow cloud AI — sends note content off this computer (no cloud provider ships in v1)"
        )
        self.cloud.setChecked(settings.cloud_ai_enabled)
        self.fetch_urls = QCheckBox("Fetch article text when a bare URL is captured")
        self.fetch_urls.setChecked(settings.fetch_url_content)

        self.whisper_model = QLineEdit(settings.whisper_model)
        self.whisper_device = QLineEdit(settings.whisper_device)
        self.whisper_compute = QLineEdit(settings.whisper_compute_type)

        self.theme = QComboBox()
        for mode in ThemeMode:
            self.theme.addItem(mode.value.capitalize(), mode.value)
        index = self.theme.findData(settings.theme)
        self.theme.setCurrentIndex(max(index, 0))

        general = QFormLayout()
        general.addRow("Vault folder", vault_row)
        appearance = QFormLayout()
        appearance.addRow("Theme", self.theme)
        ai = QFormLayout()
        ai.addRow("Ollama endpoint", self.ollama_url)
        ai.addRow("Text model", self.text_model)
        ai.addRow("Embedding model", self.embed_model)
        ai.addRow("Request timeout", self.timeout)
        ai.addRow(test_row)
        ai.addRow(self.ai_enabled)
        ai.addRow(self.semantic)
        ai.addRow(self.cloud)
        capture = QFormLayout()
        capture.addRow(self.fetch_urls)
        capture.addRow("Whisper model", self.whisper_model)
        capture.addRow("Whisper device", self.whisper_device)
        capture.addRow("Whisper compute type", self.whisper_compute)

        rebuild = QPushButton("Rebuild index")
        rebuild.setToolTip("Drop the search index and rebuild it from the markdown files")
        rebuild.clicked.connect(self.rebuild_requested.emit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        sections = (
            ("Vault", general),
            ("Appearance", appearance),
            ("AI", ai),
            ("Capture", capture),
        )
        for title, form in sections:
            box = QGroupBox(title)
            box.setLayout(form)
            layout.addWidget(box)
        maintenance = QHBoxLayout()
        maintenance.addWidget(rebuild)
        maintenance.addStretch(1)
        layout.addLayout(maintenance)
        layout.addWidget(buttons)

    def _browse_vault(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose vault folder", self.vault_path.text()
        )
        if folder:
            self.vault_path.setText(folder)

    def _test_connection(self) -> None:
        client = OllamaClient(self.ollama_url.text().strip(), timeout_s=5)
        try:
            models = client.list_models()
        except OllamaError as exc:
            self.test_result.setText(f"Not reachable: {exc}")
            return
        missing = [
            m
            for m in (self.text_model.text(), self.embed_model.text())
            if not any(name.split(":")[0] == m.split(":")[0] for name in models)
        ]
        self.test_result.setText(
            "Connected"
            + (f"; missing models: {', '.join(missing)} (ollama pull …)" if missing else "")
        )

    def result_settings(self) -> Settings:
        return replace(
            self._settings,
            vault_path=self.vault_path.text().strip() or None,
            ollama_url=self.ollama_url.text().strip() or "http://localhost:11434",
            ollama_text_model=self.text_model.text().strip(),
            ollama_embed_model=self.embed_model.text().strip(),
            ollama_timeout_s=self.timeout.value(),
            ai_enabled=self.ai_enabled.isChecked(),
            semantic_search_enabled=self.semantic.isChecked(),
            cloud_ai_enabled=self.cloud.isChecked(),
            fetch_url_content=self.fetch_urls.isChecked(),
            theme=self.theme.currentData(),
            whisper_model=self.whisper_model.text().strip() or "large-v3",
            whisper_device=self.whisper_device.text().strip() or "cuda",
            whisper_compute_type=self.whisper_compute.text().strip() or "float16",
        )


class CaptureDialog(QDialog):
    """Paste a URL and/or article text into the inbox."""

    def __init__(self, parent: QWidget | None = None, initial_text: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Capture to inbox")
        self.resize(640, 480)
        self.url = QLineEdit(placeholderText="https://… (optional)")
        self.title = QLineEdit(placeholderText="Title (optional)")
        self.text = QPlainTextEdit(
            placeholderText="Paste article text here, or leave empty to fetch the URL"
        )
        if (
            initial_text.strip().lower().startswith(("http://", "https://"))
            and "\n" not in initial_text.strip()
        ):
            self.url.setText(initial_text.strip())
        else:
            self.text.setPlainText(initial_text)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("URL", self.url)
        form.addRow("Title", self.title)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.text, 1)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str | None, str | None]:
        return (
            self.text.toPlainText(),
            self.url.text().strip() or None,
            self.title.text().strip() or None,
        )
