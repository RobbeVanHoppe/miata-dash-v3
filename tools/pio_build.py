"""Build a PlatformIO env and, on failure, show the real compiler errors.

On the dev machine PlatformIO swallows compiler stderr ("Error 1" only), so this
re-runs the first failing compile command by hand. PlatformIO data lives on D:
(C: is nearly full). Usage:

    python tools/pio_build.py esp32/main-node [-e main-node-bench]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("PLATFORMIO_CORE_DIR", "D:/platformio"))
PIO = ROOT / ".pio-venv" / "Scripts" / "pio.exe"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("-e", "--env")
    a = ap.parse_args()
    project = (ROOT / a.project).resolve()
    env = dict(os.environ)
    env.update(
        PLATFORMIO_CORE_DIR=str(CORE),
        TMP=str(CORE / "tmp"),
        TEMP=str(CORE / "tmp"),
        PLATFORMIO_BUILD_DIR=str(CORE / "build" / project.name),
    )
    base = [str(PIO), "run"] + (["-e", a.env] if a.env else [])
    out = subprocess.run(base, cwd=project, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    log = out.stdout + out.stderr
    for line in log.splitlines():
        if re.search(r"RAM:|Flash:|SUCCESS|FAILED", line) and "Took" not in line[:5]:
            print(line.strip())
    if out.returncode == 0:
        return 0
    failed = re.findall(r"\*\*\* \[(.+?\.o)\] Error", log)
    if failed:  # verbose re-run only to recover the compile command lines
        v = subprocess.run(base + ["-v"], cwd=project, env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        log = v.stdout + v.stderr
    if not failed:
        print("\n".join(l for l in log.splitlines()[-25:] if "Writing at" not in l))
    for obj in failed[:3]:
        obj_fwd = obj.replace("\\", "/")
        compile_line = next((l for l in log.splitlines() if " -c " in l and obj_fwd in l.replace("\\", "/")), None)
        if not compile_line:
            continue
        env["PATH"] = str(CORE / "packages" / "toolchain-xtensa-esp32" / "bin") + os.pathsep + env["PATH"]
        r = subprocess.run(compile_line, cwd=project, env=env, shell=True, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        errors = [l for l in (r.stdout + r.stderr).splitlines() if "error" in l or "note:" in l]
        print(f"--- {Path(obj).name}")
        print("\n".join(errors[:25]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
