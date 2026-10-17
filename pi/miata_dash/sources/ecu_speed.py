from __future__ import annotations

import can

from ..settings import SettingsStore
from ..state import VehicleState
from .base import DataSource
from .can_source import CanSource

MAX_SPEED_KPH = 524.28  # 16 bit x 0.008 kph, from ME1_In_1 in the DBC


class EcuSpeedSender(DataSource):
    """Sends GPS speed to the ME221 as vehicle speed (ME1_In_1, 0x340).

    Only sends while the setting is on and the GPS has a fresh fix. During a
    dropout (tunnel, garage) it sends nothing rather than a stale or zero
    speed. How the ME221 handles the input going quiet is TBD: check MEITE.
    """

    name = "ecu-speed"
    period = 0.1  # s between frames
    max_age = 1.5  # s; NEO-6M sends RMC at 1 Hz by default

    def __init__(self, state: VehicleState, can_source: CanSource, store: SettingsStore) -> None:
        super().__init__(state)
        self.can_source = can_source
        self.store = store
        self.message = can_source.db.get_message_by_name("ME1_In_1")

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def poll(self) -> None:
        self._stop.wait(self.period)
        frame = self.build_frame()
        if frame is not None:
            self.can_source.send(frame)

    def build_frame(self) -> can.Message | None:
        if not self.store.settings.send_gps_speed_to_ecu:
            return None
        if self.state.value("gps.fix", max_age=self.max_age) is not True:
            return None
        speed = self.state.value("gps.speed_kph", max_age=self.max_age)
        if speed is None:
            return None
        speed = min(max(float(speed), 0.0), MAX_SPEED_KPH)
        data = self.message.encode({"Vehicle_Speed": speed})
        return can.Message(arbitration_id=self.message.frame_id, data=data, is_extended_id=False)
