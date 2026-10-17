"""Wires state, settings, data sources and the web server together."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .settings import SettingsStore
from .simulator import FakeEcu, FakeSerialGps
from .sources import CanSource, DataSource, EcuSpeedSender, GpsSource
from .state import VehicleState
from .web import create_app

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DBC = REPO_ROOT / "can" / "ME1_4.dbc"
SIM_CHANNEL = "miata-sim"


@dataclass
class AppConfig:
    """Hardware and server options (command line), as opposed to user settings."""

    settings_path: Path = Path.home() / ".config" / "miata-dash" / "settings.json"
    host: str = "127.0.0.1"
    port: int = 5000
    dbc_path: Path = DEFAULT_DBC
    can_interface: str = "socketcan"
    can_channel: str = "can0"
    gps_port: str | None = "/dev/serial0"  # None disables the GPS
    gps_baudrate: int = 9600
    simulate: bool = False


class Application:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = VehicleState()
        self.store = SettingsStore(config.settings_path)  # before sources: EcuSpeedSender reads it
        self.fake_ecu: FakeEcu | None = None
        self.sources: list[DataSource] = self._build_sources()
        self.web = create_app(self.state, self.store)

    def _build_sources(self) -> list[DataSource]:
        cfg = self.config
        if cfg.simulate:
            self.fake_ecu = FakeEcu(cfg.dbc_path, SIM_CHANNEL)
            can_source = CanSource(self.state, cfg.dbc_path, interface="virtual", channel=SIM_CHANNEL)
            gps: GpsSource | None = GpsSource(self.state, serial_factory=FakeSerialGps)
        else:
            can_source = CanSource(self.state, cfg.dbc_path, cfg.can_interface, cfg.can_channel)
            gps = GpsSource(self.state, cfg.gps_port, cfg.gps_baudrate) if cfg.gps_port else None
        sources: list[DataSource] = [can_source]
        if gps is not None:
            sources += [gps, EcuSpeedSender(self.state, can_source, self.store)]
        return sources

    def start(self) -> None:
        for source in self.sources:
            source.start()
        if self.fake_ecu is not None:
            self.fake_ecu.start()

    def stop(self) -> None:
        if self.fake_ecu is not None:
            self.fake_ecu.stop()
        for source in self.sources:
            source.stop()

    def run(self) -> None:
        self.start()
        try:
            log.info("dashboard on http://%s:%d", self.config.host, self.config.port)
            self.web.run(host=self.config.host, port=self.config.port, threaded=True)
        finally:
            self.stop()
