"""Markdown note editor with ``[[wiki-link]]`` completion and a rendered preview."""

from __future__ import annotations

import re

from PySide6.QtCore import QStringListModel, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QKeyEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..vault.links import WIKILINK_RE, target_from_url, wikilinks_to_markdown
from ..vault.note import Note, NoteStatus, normalize_tags

_OPEN_LINK_RE = re.compile(r"\[\[([^\[\]\n|#]*)$")


class MarkdownHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self._link = QTextCharFormat()
        self._link.setForeground(QColor("#2a6fdb"))
        self._link.setFontUnderline(True)
        self._heading = QTextCharFormat()
        self._heading.setFontWeight(QFont.Weight.Bold)

    def highlightBlock(self, text: str) -> None:
        if text.startswith("#"):
            self.setFormat(0, len(text), self._heading)
        for match in WIKILINK_RE.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._link)


class MarkdownEdit(QPlainTextEdit):
    """Plain-text editor that pops up note titles after typing ``[[``."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setTabChangesFocus(False)
        self._titles = QStringListModel(self)
        self.completer = QCompleter(self._titles, self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.activated.connect(self._insert_completion)
        MarkdownHighlighter(self.document())

    def set_titles(self, titles: list[str]) -> None:
        self._titles.setStringList(titles)

    def _link_prefix(self) -> str | None:
        cursor = self.textCursor()
        line = cursor.block().text()[: cursor.positionInBlock()]
        match = _OPEN_LINK_RE.search(line)
        return match.group(1) if match else None

    def _insert_completion(self, title: str) -> None:
        prefix = self._link_prefix() or ""
        cursor = self.textCursor()
        cursor.movePosition(
            QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(prefix)
        )
        after = cursor.block().text()[cursor.positionInBlock() + len(prefix) :]
        cursor.insertText(title if after.startswith("]]") else f"{title}]]")
        self.setTextCursor(cursor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        popup = self.completer.popup()
        if popup.isVisible() and event.key() in (
            Qt.Key.Key_Enter,
            Qt.Key.Key_Return,
            Qt.Key.Key_Escape,
            Qt.Key.Key_Tab,
        ):
            event.ignore()
            return
        super().keyPressEvent(event)
        prefix = self._link_prefix()
        if prefix is None:
            popup.hide()
            return
        self.completer.setCompletionPrefix(prefix)
        rect = self.cursorRect()
        rect.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width())
        self.completer.complete(rect)


class NoteEditor(QWidget):
    changed = Signal()
    link_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.note: Note | None = None
        self.loaded_hash: str | None = None
        self._dirty = False
        self._loading = False

        self.title = QLineEdit(placeholderText="Title")
        self.title.setFont(QFont(self.title.font().family(), 14, QFont.Weight.Bold))
        self.tags = QLineEdit(placeholderText="tags, comma separated")
        self.status = QComboBox()
        self.status.addItems([s.value for s in NoteStatus])
        self.source = QLabel(
            openExternalLinks=True,
            textInteractionFlags=(Qt.TextInteractionFlag.TextBrowserInteraction),
        )
        self.body = MarkdownEdit()
        self.preview = QTextBrowser(openLinks=False)
        self.preview.anchorClicked.connect(self._on_anchor)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.body, "Edit")
        self.tabs.addTab(self.preview, "Preview")
        self.tabs.currentChanged.connect(lambda i: i == 1 and self.render_preview())

        form = QFormLayout()
        form.addRow("Tags", self.tags)
        form.addRow("Status", self.status)
        form.addRow("Source", self.source)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addLayout(form)
        layout.addWidget(self.tabs, 1)

        for widget_signal in (
            self.title.textEdited,
            self.tags.textEdited,
            self.body.textChanged,
            self.status.currentIndexChanged,
        ):
            widget_signal.connect(self._mark_dirty)
        self.setEnabled(False)

    # -------------------------------------------------------------- state
    @property
    def dirty(self) -> bool:
        return self._dirty

    def _mark_dirty(self, *_args) -> None:
        if not self._loading:
            self._dirty = True
            self.changed.emit()

    def load(self, note: Note | None, file_hash: str | None = None) -> None:
        self._loading = True
        try:
            self.note, self.loaded_hash = note, file_hash
            self.setEnabled(note is not None)
            self.title.setText(note.title if note else "")
            self.tags.setText(", ".join(note.tags) if note else "")
            self.status.setCurrentText(note.status if note else NoteStatus.INBOX.value)
            self.body.setPlainText(note.body if note else "")
            self.source.setText(self._source_html(note) if note else "")
            self._dirty = False
            if self.tabs.currentIndex() == 1:
                self.render_preview()
        finally:
            self._loading = False

    def mark_saved(self, file_hash: str) -> None:
        self.loaded_hash = file_hash
        self._dirty = False

    def current_note(self) -> Note | None:
        if self.note is None:
            return None
        return self.note.with_changes(
            title=self.title.text().strip() or "Untitled",
            tags=normalize_tags(self.tags.text()),
            status=self.status.currentText(),
            body=self.body.toPlainText(),
        )

    def insert_text(self, text: str) -> None:
        self.body.textCursor().insertText(text)

    def append_section(self, heading: str, content: str) -> None:
        body = self.body.toPlainText().rstrip("\n")
        self.body.setPlainText(f"{body}\n\n## {heading}\n\n{content.strip()}\n")

    # ------------------------------------------------------------ preview
    def render_preview(self) -> None:
        self.preview.setMarkdown(wikilinks_to_markdown(self.body.toPlainText()))

    def _on_anchor(self, url: QUrl) -> None:
        target = target_from_url(url.toString())
        if target is not None:
            self.link_activated.emit(target)
        else:
            QDesktopServices.openUrl(url)

    @staticmethod
    def _source_html(note: Note) -> str:
        parts = [note.source_type]
        if note.author:
            parts.append(f"by {note.author}")
        if note.source_url:
            parts.append(f'<a href="{note.source_url}">{note.source_url}</a>')
        parts.append(f"captured {note.captured_at}")
        return " · ".join(parts)
