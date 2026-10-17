from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .app import AppConfig, Application


def parse_args(argv: list[str] | None = None) -> AppConfig:
    d = AppConfig()
    p = argparse.ArgumentParser(prog="miata-dash", description="Miata dashboard head unit")
    p.add_argument("--simulate", action="store_true", help="fake ECU and GPS, no hardware needed")
    p.add_argument("--host", default=d.host)
    p.add_argument("--port", type=int, default=d.port)
    p.add_argument("--settings", type=Path, default=d.settings_path)
    p.add_argument("--log-dir", type=Path, default=d.log_dir)
    p.add_argument("--dbc", type=Path, default=d.dbc_path)
    p.add_argument("--can-interface", default=d.can_interface)
    p.add_argument("--can-channel", default=d.can_channel)
    p.add_argument("--gps-port", default=d.gps_port)
    p.add_argument("--gps-baud", type=int, default=d.gps_baudrate)
    p.add_argument("--no-gps", action="store_true")
    p.add_argument("--no-imu", action="store_true", help="no MPU6050 on I2C bus 1")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return AppConfig(
        settings_path=a.settings,
        log_dir=a.log_dir,
        host=a.host,
        port=a.port,
        dbc_path=a.dbc,
        can_interface=a.can_interface,
        can_channel=a.can_channel,
        gps_port=None if a.no_gps else a.gps_port,
        gps_baudrate=a.gps_baud,
        imu=not a.no_imu,
        simulate=a.simulate,
    )


def main(argv: list[str] | None = None) -> None:
    Application(parse_args(argv)).run()


if __name__ == "__main__":
    main()
