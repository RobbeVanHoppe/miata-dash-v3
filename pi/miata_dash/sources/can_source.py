from __future__ import annotations

import logging
from pathlib import Path

import can
import cantools

from ..state import VehicleState
from .base import DataSource

log = logging.getLogger(__name__)


class CanSource(DataSource):
    """Decodes CAN frames with a DBC file and stores every signal.

    The bit rate is not set here: on the Pi it belongs to the ``can0``
    interface (see README). Signals are stored as ``<prefix><DBC signal name>``.
    """

    name = "can"

    def __init__(
        self,
        state: VehicleState,
        dbc_path: str | Path,
        interface: str = "socketcan",
        channel: str = "can0",
        prefix: str = "ecu.",
    ) -> None:
        super().__init__(state)
        self.db = cantools.database.load_file(str(dbc_path))
        self.interface = interface
        self.channel = channel
        self.prefix = prefix
        self._bus: can.BusABC | None = None

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

    def handle(self, msg: can.Message) -> None:
        if msg.is_error_frame or msg.is_remote_frame:
            return
        try:
            definition = self.db.get_message_by_frame_id(msg.arbitration_id)
        except KeyError:
            return  # not in the DBC
        try:
            decoded = definition.decode(msg.data, decode_choices=False)
        except Exception as exc:  # noqa: BLE001 - malformed frame, skip it
            log.debug("can: cannot decode 0x%X: %s", msg.arbitration_id, exc)
            return
        for signal in definition.signals:
            if signal.name in decoded:
                self.state.update(self.prefix + signal.name, decoded[signal.name], signal.unit or "")
