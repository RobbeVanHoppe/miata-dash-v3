"""Server-side UI state, so the knob can drive the touchscreen pages.

Every page polls ``/api/ui`` with its own path. The server answers with a page
to navigate to (set by the knob) and a reload counter (bumped when something
the page was rendered with, like the layout, changed).
"""

from __future__ import annotations

import threading

PAGES = ("/", "/radio", "/logs", "/settings")


class UiState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.page = "/"
        self._target: str | None = None
        self.reload_seq = 0

    def report(self, page: str) -> dict:
        with self._lock:
            self.page = page
            if self._target == page:
                self._target = None
            return {"navigate": self._target, "reload_seq": self.reload_seq}

    def navigate(self, page: str) -> None:
        with self._lock:
            self._target = page

    def next_page(self, step: int = 1) -> str:
        with self._lock:
            current = self._target or self.page
            i = PAGES.index(current) if current in PAGES else 0
            self._target = PAGES[(i + step) % len(PAGES)]
            return self._target

    def request_reload(self) -> None:
        with self._lock:
            self.reload_seq += 1

    @property
    def current(self) -> str:
        with self._lock:
            return self._target or self.page
