from __future__ import annotations

import logging
import threading
from collections import defaultdict
from pathlib import Path
from typing import Callable

import can
import cantools

from ..state import VehicleState
from .base import DataSource

log = logging.getLogger(__name__)

Listener = Callable[[dict], None]


class CanSource(DataSource):
    """Decodes CAN frames with one or more DBC files and stores every signal.

    The first DBC (the ME221's) is ``self.db`` and stores ``<prefix><signal>``;
    extra DBCs (our own nodes) come with their own prefix. The bit rate is not
    set here: on the Pi it belongs to the ``can0`` interface (see README).
    Listeners registered per message name get every decoded frame, so event
    traffic (knob turns) is not lost between state polls.
    """

    name = "can"

    def __init__(
        self,
        state: VehicleState,
        dbc_path: str | Path,
        interface: str = "socketcan",
        channel: str = "can0",
        prefix: str = "ecu.",
        extra_dbcs: tuple[tuple[str | Path, str], ...] = (),
    ) -> None:
        super().__init__(state)
        self.db = cantools.database.load_file(str(dbc_path))
        self.databases = [(self.db, prefix)] + [
            (cantools.database.load_file(str(path)), pfx) for path, pfx in extra_dbcs
        ]
        self.interface = interface
        self.channel = channel
        self.prefix = prefix
        self._bus: can.BusABC | None = None
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._send_lock = threading.Lock()

    def open(self) -> None:
        self._bus = can.Bus(interface=self.interface, channel=self.channel)

    def poll(self) -> None:
        msg = self._bus.recv(timeout=0.5)
        if msg is not None:
            self.handle(msg)

    def close(self) -> None:
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None

    def add_listener(self, message_name: str, fn: Listener) -> None:
        self._listeners[message_name].append(fn)

    def message(self, name: str):
        for db, _ in self.databases:
            try:
                return db.get_message_by_name(name)
            except KeyError:
                continue
        raise KeyError(name)

    def send(self, msg: can.Message) -> bool:
        """Send a frame if the bus is open. Returns False instead of raising."""
        bus = self._bus
        if bus is None:
            return False
        try:
            with self._send_lock:
                bus.send(msg, timeout=0.05)
        except can.CanError as exc:
            log.debug("can: send 0x%X failed: %s", msg.arbitration_id, exc)
            return False
        return True

    def send_named(self, name: str, values: dict) -> bool:
        definition = self.message(name)
        data = definition.encode(values)
        return self.send(can.Message(arbitration_id=definition.frame_id, data=data, is_extended_id=False))

    def handle(self, msg: can.Message) -> None:
        if msg.is_error_frame or msg.is_remote_frame:
            return
        for db, prefix in self.databases:
            try:
                definition = db.get_message_by_frame_id(msg.arbitration_id)
            except KeyError:
                continue
            try:
                decoded = definition.decode(msg.data, decode_choices=False)
            except Exception as exc:  # noqa: BLE001 - malformed frame, skip it
                log.debug("can: cannot decode 0x%X: %s", msg.arbitration_id, exc)
                return
            for signal in definition.signals:
                if signal.name in decoded:
                    self.state.update(prefix + signal.name, decoded[signal.name], signal.unit or "")
            for fn in self._listeners.get(definition.name, ()):
                try:
                    fn(decoded)
                except Exception:  # noqa: BLE001 - a buggy listener must not stop CAN
                    log.exception("can: listener for %s failed", definition.name)
            return
