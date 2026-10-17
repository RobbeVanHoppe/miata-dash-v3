"""Flash a PlatformIO build onto an ESP32 with esptool.

`pio run -t upload` fails without a message on the dev machine (CH340 adapter,
Windows); calling esptool directly at 115200 baud works. Usage:

    python tools/flash_esp32.py <build-dir-of-env> --port COM10 [--core D:/platformio]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("build_dir", type=Path, help="e.g. D:/platformio/build/main-node/main-node-bench")
    ap.add_argument("--port", required=True)
    ap.add_argument("--core", type=Path, default=Path.home() / ".platformio", help="PLATFORMIO_CORE_DIR")
    ap.add_argument("--baud", default="115200")
    a = ap.parse_args()

    esptool = a.core / "packages" / "tool-esptoolpy" / "esptool.py"
    boot_app0 = a.core / "packages" / "framework-arduinoespressif32" / "tools" / "partitions" / "boot_app0.bin"
    b = a.build_dir
    cmd = [
        sys.executable, str(esptool), "--chip", "esp32", "--port", a.port, "--baud", a.baud,
        "--before", "default_reset", "--after", "hard_reset", "write_flash", "-z",
        "0x1000", str(b / "bootloader.bin"), "0x8000", str(b / "partitions.bin"),
        "0xe000", str(boot_app0), "0x10000", str(b / "firmware.bin"),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    verified = out.stdout.count("Hash of data verified")
    print(f"flash {a.port}: {verified}/4 regions verified")
    if out.returncode != 0 or verified != 4:
        print(out.stdout[-2000:], out.stderr[-2000:])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
