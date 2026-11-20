"""Run blocking work off the UI thread and deliver results back onto it."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot

log = logging.getLogger(__name__)


class UiInvoker(QObject):
    """Thread-safe ``call(fn)``: ``fn`` runs on the thread this object lives in (the UI thread)."""

    _invoke = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._invoke.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def call(self, fn: Callable[[], Any]) -> None:
        self._invoke.emit(fn)

    @Slot(object)
    def _run(self, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception:
            log.exception("UI callback failed")


class TaskRunner:
    def __init__(self, invoker: UiInvoker, workers: int = 3):
        self.invoker = invoker
        self._pool = ThreadPoolExecutor(workers, thread_name_prefix="task")

    def run(
        self,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def _work() -> None:
            try:
                result = fn()
            except Exception as exc:
                log.exception("Background task failed")
                if on_error:
                    self.invoker.call(partial(on_error, exc))
                return
            if on_done:
                self.invoker.call(lambda: on_done(result))

        self._pool.submit(_work)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
