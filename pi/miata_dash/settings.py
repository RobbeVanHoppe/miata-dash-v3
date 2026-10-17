"""User settings, persisted as JSON and editable from the settings page."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)


def _default_hidden() -> list[str]:
    # No wideband fitted yet; oil sensors only show data if wired to the ME221.
    return ["afr", "oil_p", "oil_t"]


@dataclass
class Settings:
    hidden_gauges: list[str] = field(default_factory=_default_hidden)
    stale_after_s: float = 2.0  # gauges grey out when their data is older than this

    def gauge_visible(self, gauge_id: str) -> bool:
        return gauge_id not in self.hidden_gauges


class SettingsStore:
    """Loads and saves :class:`Settings`.

    Saving writes a temp file and renames it, so cutting power mid-write
    (ignition off) leaves either the old or the new file, never a broken one.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._settings = self._load()

    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._settings

    def save(self, settings: Settings) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(asdict(settings), fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            self._settings = settings

    def _load(self) -> Settings:
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return Settings()
        except (OSError, ValueError) as exc:
            log.warning("settings: cannot read %s (%s), using defaults", self.path, exc)
            return Settings()
        known = {f.name for f in fields(Settings)}
        return Settings(**{k: v for k, v in raw.items() if k in known})
