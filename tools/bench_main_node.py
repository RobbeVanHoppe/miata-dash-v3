"""Hardware-in-the-loop test of the main node firmware on a bare ESP32.

Flash the bench build first (env main-node-bench), then:

    python tools/bench_main_node.py --port COM10

The script plays the car (key, headlight stalk) and the Pi (heartbeat, relay
requests) over the serial console of the real firmware and checks the relay
outputs and their timing against the owner's rules. Nothing may be wired to
the ESP32: the firmware toggles its output pins.
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import serial

LINE = re.compile(r"T=(\d+) (OUT set=(\d) reset=(\d) o=(\d)(\d)(\d)|EVENT (\w+)|BOOT.*|ACK .*)")


class Node:
    def __init__(self, port: str) -> None:
        self.s = serial.Serial(port, 115200, timeout=0.05)
        self.events: list[tuple[float, str, tuple]] = []  # (host time, kind, data)
        self.out = (0, 0, 0, 0, 0)

    def reset(self) -> None:
        self.s.dtr = False
        self.s.rts = True
        time.sleep(0.1)
        self.s.rts = False
        self.wait_for(lambda k, d: k == "boot", 5)

    def send(self, cmd: str) -> None:
        self.s.write((cmd + "\n").encode())
        self.wait_for(lambda k, d: k == "ack" and d == cmd, 2)

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        buf = b""
        while time.monotonic() < end:
            buf += self.s.read(1024)
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self._parse(raw.decode(errors="replace").strip())

    def _parse(self, text: str) -> None:
        m = LINE.search(text)
        if not m:
            return
        now = time.monotonic()
        if m.group(3) is not None:
            self.out = tuple(int(m.group(i)) for i in range(3, 8))
            self.events.append((now, "out", self.out))
        elif m.group(8):
            self.events.append((now, m.group(8), ()))
        elif text.split(" ", 1)[1].startswith("BOOT"):
            self.events.append((now, "boot", ()))
        else:
            self.events.append((now, "ack", text.split("ACK ", 1)[1]))

    def wait_for(self, pred, timeout: float):
        start = len(self.events)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.pump(0.02)
            for ev in self.events[start:]:
                if pred(ev[1], ev[2]):
                    return ev
        return None

    def mark(self) -> int:
        return len(self.events)

    def outputs_since(self, mark: int) -> list[tuple[float, tuple]]:
        return [(t, d) for t, k, d in self.events[mark:] if k == "out"]


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def scenario(node: Node) -> None:
    node.reset()
    node.pump(0.3)
    check("boot: all outputs off", node.out == (0, 0, 0, 0, 0), str(node.out))

    # 1. Key off with headlights on -> SET pulse after 3 s.
    node.send("head 1")
    node.send("ig1 1")
    node.pump(0.3)
    m = node.mark()
    t0 = time.monotonic()
    node.send("ig1 0")
    ev = node.wait_for(lambda k, d: k == "out" and d[0] == 1, 5)
    delay = ev[0] - t0 if ev else None
    check("lights-off SET pulse ~3 s after key off", ev is not None and 2.9 <= delay <= 3.3,
          f"{delay:.2f} s" if delay else "no pulse")
    end = node.wait_for(lambda k, d: k == "out" and d[0] == 0, 1)
    width = (end[0] - ev[0]) if (ev and end) else None
    check("SET pulse length ~50 ms", width is not None and 0.02 <= width <= 0.15,
          f"{width * 1000:.0f} ms" if width else "-")
    check("RESET coil never on together with SET", all(not (d[0] and d[1]) for _, d in node.outputs_since(m)))

    # 2. Override: move the stalk -> RESET pulse.
    t0 = time.monotonic()
    node.send("head 0")
    ev = node.wait_for(lambda k, d: k == "out" and d[1] == 1, 1)
    check("stalk moved while latched -> RESET pulse", ev is not None,
          f"{(ev[0] - t0) * 1000:.0f} ms" if ev else "none")
    node.pump(0.2)

    # 3. Key off with the lights already off -> nothing.
    node.send("ig1 1")
    node.pump(0.2)
    m = node.mark()
    node.send("ig1 0")
    node.pump(3.6)
    check("key off with lights off: no pulse", not any(d[0] for _, d in node.outputs_since(m)))

    # 4. Key back on within 3 s cancels.
    node.send("head 1")
    node.send("ig1 1")
    node.pump(0.2)
    m = node.mark()
    node.send("ig1 0")
    node.pump(1.5)
    node.send("ig1 1")
    node.pump(2.5)
    check("key back on within 3 s: no pulse", not any(d[0] for _, d in node.outputs_since(m)))

    # 5. Pi disabled the feature -> nothing.
    node.send("pi 1")
    node.send("auto 0")
    node.pump(0.3)
    m = node.mark()
    node.send("ig1 0")
    node.pump(3.6)
    check("auto lights-off disabled by the Pi: no pulse", not any(d[0] for _, d in node.outputs_since(m)))
    node.send("auto 1")
    node.send("ig1 1")

    # 6. Pi relay request follows the heartbeat; released within ~1 s after the Pi stops.
    node.send("req 0 1")
    ev = node.wait_for(lambda k, d: k == "out" and d[2] == 1, 1)
    check("Pi request energises O02", ev is not None)
    t0 = time.monotonic()
    node.send("pi 0")
    ev = node.wait_for(lambda k, d: k == "out" and d[2] == 0, 2)
    gap = ev[0] - t0 if ev else None
    check("O02 released after Pi heartbeat stops (1.0 s timeout)", gap is not None and 0.85 <= gap <= 1.3,
          f"{gap:.2f} s" if gap else "never released")
    node.send("req 0 0")

    # 7. Key off, nothing pending -> the node would deep-sleep after 5 s.
    node.send("head 0")
    t0 = time.monotonic()
    node.send("ig1 0")
    ev = node.wait_for(lambda k, d: k == "sleep", 8)
    gap = ev[0] - t0 if ev else None
    check("deep sleep ~5 s after key off", gap is not None and 4.8 <= gap <= 5.6,
          f"{gap:.2f} s" if gap else "no sleep event")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    a = ap.parse_args()
    node = Node(a.port)
    try:
        scenario(node)
    finally:
        node.s.close()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
