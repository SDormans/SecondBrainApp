"""Main window: inbox / notes / search results, note editor, backlinks, menus."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QTabWidget,
    QToolBar,
)

from ..ai.ollama import OllamaError
from ..ai.providers import AIDisabled
from ..core import AppCore
from ..index.store import NoteRecord
from ..search.search import SearchMode, SearchOutcome
from ..vault.note import Note, NoteStatus, content_hash
from ..vault.vault import ConflictError
from .dialogs import CaptureDialog, SettingsDialog
from .editor import NoteEditor
from .tasks import TaskRunner, UiInvoker

log = logging.getLogger(__name__)

PATH_ROLE = Qt.ItemDataRole.UserRole
AUTOSAVE_MS = 2000
IMPORT_FILTER = (
    "Importable files (*.pdf *.mp3 *.m4a *.wav *.flac *.ogg *.opus *.aac *.webm "
    "*.mp4 *.txt *.md);;All files (*)"
)


class MainWindow(QMainWindow):
    def __init__(self, core: AppCore):
        super().__init__()
        self.core = core
        self.invoker = UiInvoker(self)
        self.tasks = TaskRunner(self.invoker)
        self.setWindowTitle("Second Brain")
        self.resize(1280, 820)

        self._build_widgets()
        self._build_actions()

        self._refresh_timer = QTimer(self, singleShot=True, interval=300)
        self._refresh_timer.timeout.connect(self.refresh_lists)
        self._autosave_timer = QTimer(self, singleShot=True, interval=AUTOSAVE_MS)
        self._autosave_timer.timeout.connect(self.save_current)

        core.on_index_changed.append(lambda: self.invoker.call(self._refresh_timer.start))
        core.on_imported.append(lambda notes: self.invoker.call(lambda: self._imported(notes)))
        core.on_error.append(lambda msg: self.invoker.call(lambda: self.status(msg, 10000)))
        self._update_enabled()

    # ================================================================ layout
    def _build_widgets(self) -> None:
        self.search_box = QLineEdit(
            placeholderText="Search notes…  (Ctrl+F)", clearButtonEnabled=True
        )
        self.search_box.returnPressed.connect(self.run_search)
        self.search_mode = QComboBox()
        self.search_mode.addItem("Keyword", SearchMode.KEYWORD)
        self.search_mode.addItem("Semantic", SearchMode.SEMANTIC)
        self.search_mode.setToolTip(
            "Keyword search uses SQLite FTS5; semantic search uses Ollama embeddings"
        )
        self.search_mode.currentIndexChanged.connect(
            lambda _: self.search_box.text() and self.run_search()
        )
        toolbar = QToolBar("Search", movable=False)
        toolbar.addWidget(self.search_box)
        toolbar.addWidget(self.search_mode)
        self.addToolBar(toolbar)

        self.inbox_list = QListWidget()
        self.all_list = QListWidget()
        self.results_list = QListWidget()
        self.results_list.setWordWrap(True)
        self.lists = QTabWidget()
        self.lists.addTab(self.inbox_list, "Inbox")
        self.lists.addTab(self.all_list, "All notes")
        self.lists.addTab(self.results_list, "Search")
        for widget in (self.inbox_list, self.all_list, self.results_list):
            widget.currentItemChanged.connect(self._on_item_selected)

        self.editor = NoteEditor()
        self.editor.changed.connect(self._on_editor_changed)
        self.editor.link_activated.connect(self.follow_link)

        splitter = QSplitter()
        splitter.addWidget(self.lists)
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([340, 940])
        self.setCentralWidget(splitter)

        self.backlinks = QListWidget()
        self.backlinks.itemActivated.connect(lambda item: self.open_path(item.data(PATH_ROLE)))
        dock = QDockWidget("Backlinks", self)
        dock.setWidget(self.backlinks)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.backlinks_dock = dock

        self.progress = QProgressBar(maximumWidth=220, visible=False)
        self.statusBar().addPermanentWidget(self.progress)

    def _action(self, menu, text: str, slot, shortcut=None, needs_vault: bool = True) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: slot())
        action.setProperty("needs_vault", needs_vault)
        menu.addAction(action)
        return action

    def _build_actions(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        self._action(file_menu, "New vault…", self.choose_vault, needs_vault=False)
        self._action(
            file_menu,
            "Open vault…",
            self.choose_vault,
            QKeySequence.StandardKey.Open,
            needs_vault=False,
        )
        file_menu.addSeparator()
        self._action(file_menu, "New note", self.new_note, QKeySequence.StandardKey.New)
        self._action(file_menu, "Capture text / URL…", self.capture_text, "Ctrl+Shift+V")
        self._action(file_menu, "Import files (PDF, audio, text)…", self.import_files, "Ctrl+I")
        self._action(file_menu, "Import Kindle clippings…", self.import_kindle)
        self._action(file_menu, "Open drop folder", self.open_drop_folder)
        file_menu.addSeparator()
        self._action(file_menu, "Export vault as zip…", self.export_zip)
        file_menu.addSeparator()
        self._action(
            file_menu, "Quit", self.close, QKeySequence.StandardKey.Quit, needs_vault=False
        )

        note_menu = bar.addMenu("&Note")
        self._action(note_menu, "Save", self.save_current, QKeySequence.StandardKey.Save)
        self._action(note_menu, "Toggle inbox / processed", self.toggle_processed, "Ctrl+E")
        self._action(note_menu, "Insert link…", self.insert_link, "Ctrl+L")
        self._action(note_menu, "Move to trash", self.delete_current)

        ai_menu = bar.addMenu("&AI")
        self._action(ai_menu, "Summarize note", self.ai_summarize, "Ctrl+Shift+S")
        self._action(ai_menu, "Suggest tags", self.ai_suggest_tags, "Ctrl+Shift+T")
        self._action(ai_menu, "Suggest links", self.ai_suggest_links, "Ctrl+Shift+L")

        tools = bar.addMenu("&Tools")
        self._action(tools, "Settings…", self.open_settings, "Ctrl+,", needs_vault=False)
        self._action(tools, "Rebuild index", self.rebuild_index)
        self._action(tools, "Open log file", self.open_log)

        find = QAction(self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(lambda: (self.search_box.setFocus(), self.search_box.selectAll()))
        self.addAction(find)

    def _update_enabled(self) -> None:
        has_vault = self.core.vault is not None
        for action in self.findChildren(QAction):
            if action.property("needs_vault"):
                action.setEnabled(has_vault)
        self.search_box.setEnabled(has_vault)
        title = f"Second Brain — {self.core.vault.root}" if has_vault else "Second Brain"
        self.setWindowTitle(title)

    def status(self, message: str, timeout_ms: int = 5000) -> None:
        self.statusBar().showMessage(message, timeout_ms)

    def _progress(self, done: int, total: int, message: str) -> None:
        def _update() -> None:
            self.progress.setVisible(done < total)
            self.progress.setMaximum(max(total, 1))
            self.progress.setValue(done)
            self.statusBar().showMessage(message)

        self.invoker.call(_update)

    def _fail(self, title: str):
        def _handler(exc: Exception) -> None:
            self.progress.setVisible(False)
            QMessageBox.warning(self, title, str(exc))

        return _handler

    # ================================================================= vault
    def open_vault(self, path: str) -> None:
        if not self.confirm_leave_note():
            return
        try:
            self.core.open_vault(path)
            self.core.settings.save()
        except Exception as exc:
            log.exception("Could not open vault")
            QMessageBox.critical(self, "Open vault", f"Could not open {path}:\n{exc}")
            return
        self.editor.load(None)
        self._update_enabled()
        self.refresh_lists()
        self.status("Checking index…")
        self.tasks.run(
            lambda: self.core.sync_index(self._progress),
            lambda stats: self.status(f"Index ready ({self.core.store.count()} notes)"),
            self._fail("Index"),
        )

    def choose_vault(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose or create a vault folder",
            self.core.settings.vault_path or str(Path.home()),
        )
        if folder:
            self.open_vault(folder)

    # ================================================================= lists
    def refresh_lists(self) -> None:
        if self.core.store is None:
            for widget in (self.inbox_list, self.all_list, self.results_list):
                widget.clear()
            return
        current = self.editor.note.path if self.editor.note else None
        self._fill(self.inbox_list, self.core.store.list_notes(NoteStatus.INBOX.value), current)
        self._fill(self.all_list, self.core.store.list_notes(), current)
        self.lists.setTabText(0, f"Inbox ({self.inbox_list.count()})")
        self.editor.body.set_titles(self.core.store.titles())
        self._refresh_backlinks()

    def _fill(self, widget: QListWidget, records: list[NoteRecord], current: Path | None) -> None:
        widget.blockSignals(True)
        widget.clear()
        for record in records:
            item = QListWidgetItem(
                f"{record.title}\n{record.source_type} · {record.captured_at[:10]}"
            )
            item.setData(PATH_ROLE, record.path)
            item.setToolTip(record.path)
            widget.addItem(item)
            if current is not None and self.core.vault.abspath(record.path) == current:
                widget.setCurrentItem(item)
        widget.blockSignals(False)

    def _on_item_selected(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is not None:
            self.open_path(item.data(PATH_ROLE))

    def _refresh_backlinks(self) -> None:
        self.backlinks.clear()
        note = self.editor.note
        if note is None or note.path is None or self.core.store is None:
            return
        for record in self.core.store.backlinks(self.core.vault.relpath(note.path)):
            item = QListWidgetItem(record.title)
            item.setData(PATH_ROLE, record.path)
            self.backlinks.addItem(item)

    # ================================================================ search
    def run_search(self) -> None:
        if self.core.search is None:
            return
        query = self.search_box.text().strip()
        if not query:
            self.results_list.clear()
            return
        mode = self.search_mode.currentData()
        self.status("Searching…")
        self.tasks.run(
            lambda: self.core.search.search(query, mode), self._show_results, self._fail("Search")
        )

    def _show_results(self, outcome: SearchOutcome) -> None:
        self.results_list.blockSignals(True)
        self.results_list.clear()
        for result in outcome.results:
            item = QListWidgetItem(f"{result.title}\n{result.snippet}")
            item.setData(PATH_ROLE, result.path)
            item.setToolTip(f"{result.path} (score {result.score:.3f})")
            self.results_list.addItem(item)
        self.results_list.blockSignals(False)
        self.lists.setCurrentWidget(self.results_list)
        self.lists.setTabText(2, f"Search ({len(outcome.results)})")
        self.status(
            outcome.message or f"{len(outcome.results)} {outcome.mode.value} results",
            10000 if outcome.message else 5000,
        )

    # ================================================================= notes
    def confirm_leave_note(self) -> bool:
        """Save pending edits before switching notes; returns False to cancel the switch."""
        if not self.editor.dirty:
            return True
        return self.save_current()

    def open_path(self, rel: str | None) -> None:
        if not rel or self.core.vault is None:
            return
        path = self.core.vault.abspath(rel)
        if self.editor.note is not None and self.editor.note.path == path and not self.editor.dirty:
            return
        if not self.confirm_leave_note():
            return
        try:
            note, digest = self.core.vault.read_note_with_hash(path)
        except FileNotFoundError:
            self.status(f"{rel} no longer exists")
            self._refresh_timer.start()
            return
        self.editor.load(note, digest)
        self._refresh_backlinks()

    def _on_editor_changed(self) -> None:
        self._autosave_timer.start()

    def save_current(self) -> bool:
        self._autosave_timer.stop()
        note = self.editor.current_note()
        if note is None or not self.editor.dirty:
            return True
        try:
            path = self.core.save_note(note, expected_hash=self.editor.loaded_hash)
        except ConflictError:
            copy = self.core.vault.save_conflict_copy(note)
            QMessageBox.warning(
                self,
                "Note changed on disk",
                f"{note.path.name} was changed by another program. Your version was saved as "
                f"{copy.name}; the note now shows the version on disk.",
            )
            fresh, digest = self.core.vault.read_note_with_hash(note.path)
            self.editor.load(fresh, digest)
            return True
        except OSError as exc:
            log.exception("Save failed")
            QMessageBox.critical(self, "Save failed", f"Could not save the note:\n{exc}")
            return False
        self.editor.note = note.with_changes(path=path)
        self.editor.mark_saved(self.core.vault.file_hash(path) or content_hash(note.to_markdown()))
        self.status(f"Saved {path.name}", 2000)
        return True

    def new_note(self, title: str | None = None) -> None:
        if title is None:
            title, ok = QInputDialog.getText(self, "New note", "Title:")
            if not ok:
                return
        if not self.confirm_leave_note():
            return
        note = Note.new(
            title or "Untitled", f"# {title or 'Untitled'}\n\n", status=NoteStatus.PROCESSED.value
        )
        path = self.core.save_note(note)
        self.refresh_lists()
        self.open_path(self.core.vault.relpath(path))
        self.editor.body.setFocus()

    def follow_link(self, target: str) -> None:
        record = self.core.store.find_by_title(target)
        if record is not None:
            self.open_path(record.path)
            return
        answer = QMessageBox.question(self, "Create note", f"No note called “{target}”. Create it?")
        if answer == QMessageBox.StandardButton.Yes:
            self.new_note(target)

    def toggle_processed(self) -> None:
        if self.editor.note is None:
            return
        new = (
            NoteStatus.INBOX
            if self.editor.status.currentText() == NoteStatus.PROCESSED
            else NoteStatus.PROCESSED
        )
        self.editor.status.setCurrentText(new.value)
        self.save_current()

    def insert_link(self) -> None:
        if self.editor.note is None:
            return
        title, ok = QInputDialog.getItem(
            self, "Insert link", "Note:", self.core.store.titles(), editable=True
        )
        if ok and title:
            self.editor.insert_text(f"[[{title}]]")

    def delete_current(self) -> None:
        note = self.editor.note
        if note is None or note.path is None:
            return
        if (
            QMessageBox.question(
                self,
                "Move to trash",
                f"Move “{note.title}” to the vault trash (.secondbrain/trash)?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.core.vault.delete_note(note.path)
        self.core.indexer.remove_path(note.path)
        self.editor.load(None)
        self.refresh_lists()

    # =============================================================== capture
    def capture_text(self) -> None:
        dialog = CaptureDialog(self, QApplication.clipboard().text())
        if not dialog.exec():
            return
        text, url, title = dialog.values()
        if not (text.strip() or url):
            return
        self.status("Capturing…")
        self.tasks.run(
            lambda: self.core.capture.capture_text(text, url=url, title=title),
            lambda note: self._imported([note]),
            self._fail("Capture"),
        )

    def import_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Import files", str(Path.home()), IMPORT_FILTER
        )
        for file in files:
            self._import_in_background(Path(file))

    def import_kindle(self) -> None:
        file, _ = QFileDialog.getOpenFileName(
            self,
            "Import Kindle clippings",
            str(Path.home()),
            "Kindle clippings (My Clippings.txt *.txt)",
        )
        if file:
            self.status("Importing Kindle clippings…")
            self.tasks.run(
                lambda: self.core.capture.import_kindle(Path(file)),
                self._imported,
                self._fail("Kindle import"),
            )

    def _import_in_background(self, path: Path) -> None:
        capture = self.core.capture
        self.status(f"Importing {path.name}…", 0)

        def _work():
            def progress(fraction: float) -> None:
                self._progress(int(fraction * 100), 100, f"Transcribing {path.name}…")

            return capture.import_file(path, move=False, progress=progress)

        self.tasks.run(_work, self._imported, self._fail(f"Import {path.name}"))

    def _imported(self, notes: list[Note]) -> None:
        self.progress.setVisible(False)
        for note in notes:
            if note.path:
                self.core.indexer.index_path(note.path)
        self.core.schedule_embedding()
        self.refresh_lists()
        self.lists.setCurrentWidget(self.inbox_list)
        self.status(
            f"Added/updated {len(notes)} inbox note(s)" if notes else "Nothing new to import"
        )

    def open_drop_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.core.vault.import_dir)))

    def export_zip(self) -> None:
        self.save_current()
        default = Path.home() / f"{self.core.vault.root.name}-{date.today():%Y%m%d}.zip"
        target, _ = QFileDialog.getSaveFileName(
            self, "Export vault as zip", str(default), "Zip archive (*.zip)"
        )
        if not target:
            return
        vault = self.core.vault
        self.status("Exporting…", 0)
        self.tasks.run(
            lambda: vault.export_zip(Path(target)),
            lambda path: self.status(f"Exported to {path}", 10000),
            self._fail("Export"),
        )

    # ==================================================================== AI
    def _ai(self, title: str, fn, on_done) -> None:
        note = self.editor.current_note()
        if note is None or self.core.assistant is None:
            return
        self.status(f"{title}…", 0)

        def _error(exc: Exception) -> None:
            self.status("")
            if isinstance(exc, AIDisabled | OllamaError):
                QMessageBox.information(
                    self,
                    title,
                    f"{exc}\n\nAI features need Ollama running "
                    f"locally (see README) and can be toggled in Settings.",
                )
            else:
                QMessageBox.warning(self, title, str(exc))

        self.tasks.run(lambda: fn(note), on_done, _error)

    def ai_summarize(self) -> None:
        def done(summary: str) -> None:
            self.status("")
            box = QMessageBox(QMessageBox.Icon.NoIcon, "Summary", summary, parent=self)
            insert = box.addButton("Insert into note", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is insert:
                self.editor.append_section("Summary", summary)

        self._ai("Summarize", self.core.assistant.summarize, done)

    def ai_suggest_tags(self) -> None:
        known = [tag for tag, _ in self.core.store.tag_counts().most_common(100)]

        def done(tags: list[str]) -> None:
            self.status("")
            if not tags:
                QMessageBox.information(self, "Suggest tags", "No new tags suggested.")
                return
            text, ok = QInputDialog.getText(
                self, "Suggest tags", "Add these tags (edit as needed):", text=", ".join(tags)
            )
            if ok and text:
                current = self.editor.tags.text().strip()
                self.editor.tags.setText(f"{current}, {text}" if current else text)
                self.editor._mark_dirty()

        self._ai("Suggest tags", lambda note: self.core.assistant.suggest_tags(note, known), done)

    def ai_suggest_links(self) -> None:
        def done(suggestions) -> None:
            self.status("")
            if not suggestions:
                QMessageBox.information(self, "Suggest links", "No related notes found.")
                return
            labels = [f"{s.title}  ({s.score:.2f})" for s in suggestions]
            choice, ok = QInputDialog.getItem(
                self, "Suggest links", "Insert a link to:", labels, editable=False
            )
            if ok:
                self.editor.insert_text(f"[[{suggestions[labels.index(choice)].title}]]")

        self._ai("Suggest links", self.core.assistant.suggest_links, done)

    # ================================================================= tools
    def open_settings(self) -> None:
        dialog = SettingsDialog(self.core.settings, self)
        dialog.rebuild_requested.connect(self.rebuild_index)
        if dialog.exec():
            new = dialog.result_settings()
            vault_changed = new.vault_path and (
                self.core.vault is None or Path(new.vault_path).resolve() != self.core.vault.root
            )
            if vault_changed:
                self.core.settings = new
                self.open_vault(new.vault_path)
            else:
                self.core.apply_settings(new)
                self.status("Settings saved")

    def rebuild_index(self) -> None:
        if self.core.indexer is None:
            return
        self.save_current()
        self.status("Rebuilding index…", 0)

        def done(stats) -> None:
            self.progress.setVisible(False)
            message = f"Index rebuilt: {stats.indexed} notes, {stats.embedded} embeddings"
            if stats.errors:
                message += f", {stats.errors} errors (see log)"
            if stats.embedding_skipped_reason:
                message += f" — embeddings skipped: {stats.embedding_skipped_reason}"
            self.status(message, 15000)

        self.tasks.run(
            lambda: self.core.rebuild_index(self._progress), done, self._fail("Rebuild index")
        )

    def open_log(self) -> None:
        if self.core.log_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.core.log_path)))

    # ================================================================ events
    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.confirm_leave_note():
            event.ignore()
            return
        self.tasks.shutdown()
        self.core.settings.save()
        self.core.shutdown()
        super().closeEvent(event)
