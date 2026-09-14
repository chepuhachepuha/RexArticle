"""Cooperative cancellation primitives shared by background operations."""
from __future__ import annotations

import threading


class OperationCancelled(RuntimeError):
    """Raised when a background operation is cancelled cooperatively."""


class CancellationToken:
    """Small thread-safe cancellation token suitable for QRunnable workers."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise OperationCancelled("Операция отменена")