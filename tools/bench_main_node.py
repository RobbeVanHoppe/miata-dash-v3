"""Hardware-in-the-loop test of the main node firmware on a bare ESP32.

Build and flash the bench variant first, then run the scenarios:

    python tools/pio_build.py esp32/main-node -e main-node-bench
    .pio-venv/Scripts/python tools/flash_esp32.py D:/platformio/build/main-node/main-node-bench --port COM10 --core D:/platformio
    python tools/bench_main_node.py --port COM10

The script plays the car (key, headlight stalk, a simulated latching relay inside
the firmware's bench layer) and the Pi (heartbeat, relay requests) over the
serial console of the real firmware, and checks outputs and timing against
miata-hw docs/design-main-node.md §13. All timings use the firmware's own
millisecond timestamps. Nothing may be wired to the ESP32.
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import serial

OUT = re.compile(r"T=(\d+) OUT vrly=(\d) set=(\d) reset=(\d) o=(\d)(\d)(\d) fb=(\d)")
EVENT = re.compile(r"T=(\d+) EVENT (\w+)")
ACK = re.compile(r"T=(\d+) ACK (.+)")
KEYS = ("vrly", "set", "reset", "o02", "o03", "o04", "fb")


class Node:
    def __init__(self, port: str) -> None:
        self.s = serial.Serial(port, 115200, timeout=0.05)
        self.events: list[tuple[int, str, object]] = []  # (device ms, kind, data)
        self.out = dict.fromkeys(KEYS, 0)
        self._buf = b""

    def reset(self) -> None:
        self.s.dtr = False
        self.s.rts = True
        time.sleep(0.1)
        self.s.rts = False
        if not self.wait_for(lambda k, d: k == "event" and d == "boot", 6):
            raise SystemExit("no BOOT from the firmware: is the bench build flashed?")

    def send(self, cmd: str) -> int:
        self.s.write((cmd + "\n").encode())
        ev = self.wait_for(lambda k, d: k == "ack" and d == cmd, 2)
        if ev is None:
            raise SystemExit(f"no ACK for {cmd!r}")
        return ev[0]

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._buf += self.s.read(1024)
            while b"\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n", 1)
                self._parse(raw.decode(errors="replace").strip())

    def _parse(self, text: str) -> None:
        if m := OUT.search(text):
            self.out = dict(zip(KEYS, (int(g) for g in m.groups()[1:])))
            self.events.append((int(m.group(1)), "out", dict(self.out)))
        elif m := EVENT.search(text):
            self.events.append((int(m.group(1)), "event", m.group(2)))
        elif m := ACK.search(text):
            self.events.append((int(m.group(1)), "ack", m.group(2)))

    def wait_for(self, pred, timeout: float, since: int | None = None):
        """First event matching `pred`, searching from event index `since` (default: now)."""
        start = len(self.events) if since is None else since
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.pump(0.02)
            for ev in self.events[start:]:
                if pred(ev[1], ev[2]):
                    return ev
        return None

    def mark(self) -> int:
        return len(self.events)

    def outs_since(self, mark: int) -> list[tuple[int, dict]]:
        return [(t, d) for t, k, d in self.events[mark:] if k == "out"]

    def edges(self, mark: int, key: str) -> list[tuple[int, int]]:
        """(rise ms, length ms) of every pulse on `key` since `mark`."""
        pulses, rise, prev = [], None, None
        for t, d in self.outs_since(mark):
            if d[key] and not prev:
                rise = t
            if not d[key] and prev and rise is not None:
                pulses.append((rise, t - rise))
            prev = d[key]
        return pulses


RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def keyoff_cycle(node: Node, head: bool, settle: float = 4.0) -> tuple[int, int]:
    """Key on, then off with the stalk as given. Returns (mark, device ms of key-off)."""
    node.send(f"head {int(head)}")
    node.send("ig1 1")
    node.pump(0.5)
    m = node.mark()
    t_off = node.send("ig1 0")
    node.pump(settle)
    return m, t_off


def scenario(node: Node) -> None:
    node.reset()
    node.pump(0.3)
    check("boot: every output off", not any(node.out.values()), str(node.out))

    # 1. Key off with headlights on -> verified SET pulse 3 s later.
    m, t_off = keyoff_cycle(node, head=True)
    sets = node.edges(m, "set")
    ok = len(sets) == 1 and 2950 <= sets[0][0] - t_off <= 3150 and 95 <= sets[0][1] <= 115
    check("SET pulse 3.0 s after key off, 100 ms long", ok,
          f"{[(r - t_off, w) for r, w in sets]} ms" if sets else "no pulse")
    vr = node.edges(m, "vrly")
    check("pulse supply on only around the pulse", len(vr) == 1 and vr[0][1] <= 200, f"{vr}")
    check("relay feedback reports SET", node.out["fb"] == 1)
    check("SET and RESET never on together", all(not (d["set"] and d["reset"]) for _, d in node.outs_since(m)))

    # 2. Override: stalk off HEAD -> RESET pulse.
    m = node.mark()
    t = node.send("head 0")
    node.pump(0.6)
    resets = node.edges(m, "reset")
    check("stalk off HEAD while latched -> RESET pulse", len(resets) == 1 and node.out["fb"] == 0,
          f"{[(r - t, w) for r, w in resets]} ms")

    # 3. Lights already off -> nothing.
    m, _ = keyoff_cycle(node, head=False)
    check("key off with lights off: no pulse", not node.edges(m, "set"))

    # 4. Key back on within 3 s -> nothing.
    node.send("head 1")
    node.send("ig1 1")
    node.pump(0.5)
    m = node.mark()
    node.send("ig1 0")
    node.pump(1.5)
    node.send("ig1 1")
    node.pump(2.5)
    check("key back on within 3 s: no pulse", not node.edges(m, "set"))

    # 5. Key-on with the relay still SET: hardware self-reset, no firmware pulse.
    m, _ = keyoff_cycle(node, head=True)
    m = node.mark()
    node.send("ig1 1")
    node.pump(0.8)
    check("key on: hardware self-reset, firmware stays quiet",
          node.out["fb"] == 0 and not node.edges(m, "reset"))

    # 6. Same, but the hardware self-reset path is broken -> firmware RESET.
    node.send("selfreset 0")
    m, _ = keyoff_cycle(node, head=True)
    m = node.mark()
    node.send("ig1 1")
    node.pump(0.8)
    check("key on without hardware reset: firmware RESET pulse", node.out["fb"] == 0 and len(node.edges(m, "reset")) == 1)
    node.send("selfreset 1")

    # 7. Stuck relay -> three attempts, then give up.
    node.send("stuck 1")
    m, _ = keyoff_cycle(node, head=True, settle=5.5)
    check("stuck relay: exactly 3 SET attempts", len(node.edges(m, "set")) == 3, f"{len(node.edges(m, 'set'))}")
    node.send("stuck 0")
    node.send("ig1 1")

    # 8. Pi disabled the feature -> nothing.
    node.send("pi 1")
    node.send("auto 0")
    node.pump(0.5)
    m, _ = keyoff_cycle(node, head=True)
    check("auto lights-off disabled by the Pi: no pulse", not node.edges(m, "set"))
    node.send("auto 1")

    # 9. Pi relays: need the heartbeat (3 frames), follow it, drop at key off.
    node.send("ig1 1")
    node.send("pi 0")
    node.pump(1.5)
    node.send("req 0 1")
    m = node.mark()
    t = node.send("pi 1")
    ev = node.wait_for(lambda k, d: k == "out" and d["o02"] == 1, 2, since=m)
    check("O02 on after heartbeat acquired (~0.2-0.4 s)", ev is not None and 150 <= ev[0] - t <= 500,
          f"{ev[0] - t} ms" if ev else "never")
    m = node.mark()
    t = node.send("pi 0")
    ev = node.wait_for(lambda k, d: k == "out" and d["o02"] == 0, 2, since=m)
    check("O02 released 1.0 s after the heartbeat stops", ev is not None and 900 <= ev[0] - t <= 1200,
          f"{ev[0] - t} ms" if ev else "never")
    m = node.mark()
    node.send("pi 1")
    back = node.wait_for(lambda k, d: k == "out" and d["o02"] == 1, 2, since=m)
    check("O02 back on when the heartbeat returns", back is not None)
    node.pump(0.3)
    m = node.mark()
    t = node.send("ig1 0")
    ev = node.wait_for(lambda k, d: k == "out" and d["o02"] == 0, 1, since=m)
    check("O02 released at key off", ev is not None and ev[0] - t <= 150, f"{ev[0] - t} ms" if ev else "never")
    node.send("req 0 0")
    node.send("pi 0")

    # 10. Settled with the key off -> sleep 10 s later.
    node.send("head 0")
    node.send("ig1 1")
    node.pump(0.3)
    m = node.mark()
    t = node.send("ig1 0")
    ev = node.wait_for(lambda k, d: k == "event" and d.startswith("sleep"), 16, since=m)
    check("deep sleep ~13 s after key off (3 s + 10 s awake)", ev is not None and 12800 <= ev[0] - t <= 13500,
          f"{ev[0] - t} ms ({ev[2]})" if ev else "no sleep event")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    a = ap.parse_args()
    node = Node(a.port)
    try:
        scenario(node)
    finally:
        node.s.close()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
