from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod

from ..state import VehicleState

log = logging.getLogger(__name__)


class DataSource(ABC):
    """A reader running in its own thread that pushes values into the state.

    Subclasses implement ``open``/``poll``/``close``. If anything raises (cable
    unplugged, CAN interface down), the source closes, waits ``retry_delay``
    and reopens, so a flaky device never takes the dashboard down.
    """

    name = "source"
    retry_delay = 2.0

    def __init__(self, state: VehicleState) -> None:
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def poll(self) -> None:
        """Read and handle one unit of input. Must return within about a second."""

    @abstractmethod
    def close(self) -> None: ...

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        last_error = ""
        while not self._stop.is_set():
            try:
                self.open()
                log.info("%s: opened", self.name)
                while not self._stop.is_set():
                    self.poll()
            except Exception as exc:  # noqa: BLE001 - keep the thread alive on any device error
                # Warn once per distinct error; a missing device would otherwise log every 2 s.
                level = logging.DEBUG if str(exc) == last_error else logging.WARNING
                last_error = str(exc)
                log.log(level, "%s: %s; retrying in %.0f s", self.name, exc, self.retry_delay)
                self._stop.wait(self.retry_delay)
            finally:
                try:
                    self.close()
                except Exception:  # noqa: BLE001
                    log.debug("%s: error while closing", self.name, exc_info=True)
