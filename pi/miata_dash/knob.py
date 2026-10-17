"""Haptic knob: turns KNOB_STATE frames into UI actions and picks the haptic
profile (PI_KNOB_PROFILE) that matches the page on screen.

    long press        next page (Dash → Radio → Logs → Settings)
    Dash   turn/press  cycle layouts
    Radio  turn        tune one channel step per detent; press = play/stop
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace

from .gauges import LAYOUTS
from .radio import RadioController
from .settings import SettingsStore
from .ui import UiState

log = logging.getLogger(__name__)

# PI_KNOB_PROFILE modes (see can/miata.dbc VAL_ table)
FREE, DETENTS, DETENTS_ENDSTOPS, FINE_TUNING, POWER_ON_CLICK = range(5)

PROFILES: dict[str, dict] = {
    "/": {"Prof_Mode": DETENTS, "Prof_DetentsPerRev": 12, "Prof_Strength": 70,
          "Prof_MinPos": 0, "Prof_MaxPos": 0},
    "/radio": {"Prof_Mode": FINE_TUNING, "Prof_DetentsPerRev": 36, "Prof_Strength": 35,
               "Prof_MinPos": 0, "Prof_MaxPos": 0},
}
DEFAULT_PROFILE = {"Prof_Mode": DETENTS, "Prof_DetentsPerRev": 20, "Prof_Strength": 50,
                   "Prof_MinPos": 0, "Prof_MaxPos": 0}
MAX_STEPS_PER_FRAME = 10  # ignore absurd deltas (e.g. a counter reset)


class KnobController:
    def __init__(self, ui: UiState, store: SettingsStore, radio: RadioController | None) -> None:
        self.ui = ui
        self.store = store
        self.radio = radio
        self._lock = threading.Lock()
        self._pressed = False
        self._long_seen = False

    def profile(self) -> dict:
        return PROFILES.get(self.ui.current, DEFAULT_PROFILE)

    def on_frame(self, d: dict) -> None:
        """CAN listener for KNOB_STATE (decoded signal dict)."""
        with self._lock:
            pressed, long_press = bool(d.get("Knob_Pressed")), bool(d.get("Knob_LongPress"))
            if long_press and not self._long_seen:
                self._long_seen = True
                self.ui.next_page()
            if self._pressed and not pressed:  # released
                if not self._long_seen:
                    self._press()
                self._long_seen = False
            self._pressed = pressed
            delta = int(d.get("Knob_Delta", 0))
            if delta and abs(delta) <= MAX_STEPS_PER_FRAME:
                self._turn(delta)

    def _press(self) -> None:
        page = self.ui.current
        if page == "/":
            self._cycle_layout(1)
        elif page == "/radio" and self.radio is not None:
            if self.radio.status()["playing"]:
                self.radio.stop()
            else:
                self.radio.tune()

    def _turn(self, delta: int) -> None:
        page = self.ui.current
        if page == "/":
            self._cycle_layout(1 if delta > 0 else -1)
        elif page == "/radio" and self.radio is not None:
            for _ in range(abs(delta)):
                self.radio.step(1 if delta > 0 else -1)

    def _cycle_layout(self, step: int) -> None:
        s = self.store.settings
        current = s.layout if s.layout in LAYOUTS else LAYOUTS[0]
        layout = LAYOUTS[(LAYOUTS.index(current) + step) % len(LAYOUTS)]
        self.store.save(replace(s, layout=layout))
        self.ui.request_reload()
