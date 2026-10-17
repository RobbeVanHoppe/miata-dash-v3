"""Wires state, settings, data sources and the web server together."""

from __future__ import annotations

import functools
import logging
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from .fusion import SpeedEstimator
from .knob import KnobController
from .logger import DriveLogger
from .node_link import PiNodeLink, RelayRequests
from .radio import RadioController
from .settings import SettingsStore
from .simulator import FakeEcu, FakeI2cBus, FakeMainNode, FakeRadioRunner, FakeSerialGps
from .sources import CanSource, DataSource, EcuSpeedSender, GpsSource
from .sources.imu import ImuSource, Mounting
from .state import VehicleState
from .ui import UiState
from .web import create_app

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DBC = REPO_ROOT / "can" / "ME1_4.dbc"
NET_DBC = REPO_ROOT / "can" / "miata.dbc"  # our own nodes: Pi, main node, knob
SIM_CHANNEL = "miata-sim"
GPS_LOG_FIELDS = ("utc", "fix", "satellites", "latitude", "longitude", "altitude", "speed_kph", "course")
EXTRA_LOG_FIELDS = ("ins.speed_kph", "ins.mode", "ins.sigma_kph", "imu.a_fwd", "imu.a_left", "imu.yaw_rate")


@dataclass
class AppConfig:
    """Hardware and server options (command line), as opposed to user settings."""

    settings_path: Path = Path.home() / ".config" / "miata-dash" / "settings.json"
    log_dir: Path = Path.home() / "miata-logs"
    host: str = "127.0.0.1"
    port: int = 5000
    dbc_path: Path = DEFAULT_DBC
    can_interface: str = "socketcan"
    can_channel: str = "can0"
    gps_port: str | None = "/dev/serial0"  # None disables the GPS
    gps_baudrate: int = 9600
    imu: bool = True  # MPU6050 on I²C bus 1
    simulate: bool = False


class Application:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = VehicleState()
        self.store = SettingsStore(config.settings_path)  # before sources: EcuSpeedSender reads it
        self.fake_ecu: FakeEcu | None = None
        self.fake_main: FakeMainNode | None = None
        self.ui = UiState()
        self.relays = RelayRequests()
        self.radio = self._build_radio()
        self.knob = KnobController(self.ui, self.store, self.radio)
        self.sources: list[DataSource] = self._build_sources()
        self.web = create_app(
            self.state, self.store, radio=self.radio, log_dir=config.log_dir, ui=self.ui, relays=self.relays
        )

    def _build_radio(self) -> RadioController:
        s = self.store.settings
        if self.config.simulate:
            return RadioController(FakeRadioRunner(), tools_present=True)
        return RadioController(audio_device=s.radio_audio_device, gain=s.radio_gain, ppm=s.radio_ppm)

    def _build_sources(self) -> list[DataSource]:
        cfg = self.config
        settings = self.store.settings
        mounting = Mounting(settings.imu_forward_axis, settings.imu_up_axis)
        self.estimator = SpeedEstimator(self.state, mounting)
        if cfg.simulate:
            sim_start = time.monotonic()
            self.fake_ecu = FakeEcu(cfg.dbc_path, SIM_CHANNEL)
            self.fake_main = FakeMainNode(NET_DBC, SIM_CHANNEL)
            can_source = CanSource(
                self.state, cfg.dbc_path, "virtual", SIM_CHANNEL, extra_dbcs=((NET_DBC, "net."),)
            )
            gps: GpsSource | None = GpsSource(
                self.state,
                serial_factory=functools.partial(FakeSerialGps, start=sim_start),
                rate_hz=settings.gps_rate_hz,
            )
            imu: ImuSource | None = ImuSource(
                self.state, mounting, lambda: FakeI2cBus(sim_start), self.estimator.on_imu
            )
        else:
            can_source = CanSource(
                self.state, cfg.dbc_path, cfg.can_interface, cfg.can_channel, extra_dbcs=((NET_DBC, "net."),)
            )
            gps = (
                GpsSource(self.state, cfg.gps_port, cfg.gps_baudrate, rate_hz=settings.gps_rate_hz)
                if cfg.gps_port
                else None
            )
            imu = ImuSource(self.state, mounting, on_sample=self.estimator.on_imu) if cfg.imu else None
        can_source.add_listener("KNOB_STATE", self.knob.on_frame)
        self.node_link = PiNodeLink(self.state, can_source, self.store, self.relays, self.knob.profile)
        sources: list[DataSource] = [can_source, self.node_link]
        if gps is not None:
            sources += [gps, EcuSpeedSender(self.state, can_source, self.store)]
        if imu is not None:
            sources.append(imu)
        sources.append(DriveLogger(self.state, self.store, cfg.log_dir, self._log_columns(can_source)))
        return sources

    @staticmethod
    def _log_columns(can_source: CanSource) -> list[str]:
        ecu = {
            can_source.prefix + s.name
            for m in can_source.db.messages
            if m.name != "ME1_In_1"  # sent to the ECU, not by it
            for s in m.signals
        }
        return sorted(ecu) + [f"gps.{n}" for n in GPS_LOG_FIELDS] + list(EXTRA_LOG_FIELDS)

    def start(self) -> None:
        for source in self.sources:
            source.start()
        for fake in (self.fake_ecu, self.fake_main):
            if fake is not None:
                fake.start()

    def stop(self) -> None:
        """Orderly shutdown: tell the other nodes, then close devices and the drive log.

        Runs on SIGTERM too (systemd stops the service when the supervisor asks
        the Pi to shut down), so the CSV is flushed before power goes.
        """
        self.node_link.pi_state = "shutting_down"
        self.node_link.tick(force_status=True)  # announce on CAN before the bus closes
        self.radio.stop()
        for fake in (self.fake_ecu, self.fake_main):
            if fake is not None:
                fake.stop()
        for source in self.sources:
            source.stop()

    def run(self) -> None:
        signal.signal(signal.SIGTERM, _raise_exit)
        self.start()
        try:
            log.info("dashboard on http://%s:%d", self.config.host, self.config.port)
            self.web.run(host=self.config.host, port=self.config.port, threaded=True)
        finally:
            self.stop()
            log.info("stopped cleanly")


def _raise_exit(signum, frame) -> None:
    # Python's default SIGTERM kills without running finally-blocks; turn it into SystemExit.
    raise SystemExit(0)
