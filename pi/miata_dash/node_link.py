"""Frames the Pi sends to the other nodes (can/miata.dbc).

    PI_MAIN_CMD      10 Hz  relay requests + auto lights-off enable; doubles as
                            the Pi heartbeat the main node watches
    PI_STATUS         2 Hz  Pi state, GPS fix, logging, INS mode
    PI_KNOB_PROFILE   2 Hz  and immediately when the page (profile) changes
"""

from __future__ import annotations

import threading
from typing import Callable

from .settings import SettingsStore
from .sources.base import DataSource
from .sources.can_source import CanSource
from .state import VehicleState

RELAYS = ("O02", "O03", "O04")
PI_STATE = {"booting": 0, "running": 1, "shutting_down": 2}
INS_MODE = {"lost": 0, "gps": 1, "dr": 2}


class RelayRequests:
    """Relays the Pi asks the main node to energise. Runtime only: after a
    restart everything starts released, which is the factory state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._on = {r: False for r in RELAYS}

    def set(self, relay: str, on: bool) -> None:
        if relay not in self._on:
            raise ValueError(f"unknown relay {relay!r}")
        with self._lock:
            self._on[relay] = bool(on)

    def snapshot(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._on)


class PiNodeLink(DataSource):
    name = "node-link"
    period = 0.1  # s

    def __init__(
        self,
        state: VehicleState,
        can_source: CanSource,
        store: SettingsStore,
        relays: RelayRequests,
        profile: Callable[[], dict],
    ) -> None:
        super().__init__(state)
        self.can = can_source
        self.store = store
        self.relays = relays
        self.profile = profile
        self.pi_state = "running"
        self._tick = 0
        self._last_profile: dict | None = None

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def poll(self) -> None:
        self._stop.wait(self.period)
        self.tick()

    def tick(self) -> None:
        self._tick += 1
        counter = self._tick & 0xFF
        relays = self.relays.snapshot()
        self.can.send_named("PI_MAIN_CMD", {
            **{f"Pi_RelayReq_{r}": int(on) for r, on in relays.items()},
            "Pi_AutoLightsOff_En": int(self.store.settings.auto_lights_off),
            "Pi_Counter": counter,
        })
        profile = self.profile()
        if profile != self._last_profile or self._tick % 5 == 0:
            self.can.send_named("PI_KNOB_PROFILE", profile)
            self._last_profile = profile
        if self._tick % 5 == 0:
            self.can.send_named("PI_STATUS", {
                "PiSt_State": PI_STATE[self.pi_state],
                "PiSt_GpsFix": int(self.state.value("gps.fix", max_age=2.0) is True),
                "PiSt_Logging": int(self.store.settings.logging_enabled),
                "PiSt_InsMode": INS_MODE.get(self.state.value("ins.mode", max_age=2.0) or "lost", 0),
                "PiSt_Counter": counter,
            })
