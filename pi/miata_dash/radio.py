"""RTL-SDR radio: rtl_fm demodulates, aplay plays the audio.

Pipeline:  rtl_fm -M <mode> -f <Hz> -s <rate> -r 48000 ... - | aplay -r 48000 -f S16_LE ...
Only one program can own the dongle, so scanning (rtl_power) stops playback first.

Bands: broadcast FM plus a few bands that are commonly receivable. Whether
listening to non-broadcast bands is allowed differs per country; the UI says
so. Police and emergency services in most of Europe use encrypted TETRA, which
cannot be received with this, so no such band is offered.
"""

from __future__ import annotations

import csv
import io
import logging
import shutil
import statistics
import subprocess
import threading
from dataclasses import asdict, dataclass
from typing import Protocol

log = logging.getLogger(__name__)

AUDIO_RATE = 48000


@dataclass(frozen=True)
class Band:
    id: str
    name: str
    mode: str  # rtl_fm -M: wbfm | fm (narrow) | am
    lo_mhz: float
    hi_mhz: float
    step_khz: float
    default_mhz: float
    sample_rate: int  # rtl_fm -s

    def contains(self, mhz: float) -> bool:
        return self.lo_mhz - 1e-6 <= mhz <= self.hi_mhz + 1e-6

    def snap(self, mhz: float) -> float:
        step = self.step_khz / 1000
        n = round((mhz - self.lo_mhz) / step)
        return round(min(max(self.lo_mhz + n * step, self.lo_mhz), self.hi_mhz), 5)


BANDS: tuple[Band, ...] = (
    Band("fm", "FM radio", "wbfm", 87.5, 108.0, 100, 100.0, 200_000),
    Band("air", "Airband (AM)", "am", 118.0, 136.975, 25, 118.0, 24_000),
    Band("marine", "Marine VHF", "fm", 156.0, 162.025, 25, 156.8, 24_000),
    Band("pmr", "PMR446", "fm", 446.00625, 446.19375, 12.5, 446.00625, 24_000),
    Band("ham2m", "Amateur 2 m", "fm", 144.0, 146.0, 12.5, 145.5, 24_000),
    Band("ham70", "Amateur 70 cm", "fm", 430.0, 440.0, 12.5, 433.5, 24_000),
)
BANDS_BY_ID = {b.id: b for b in BANDS}


class Pipeline(Protocol):
    def poll(self) -> int | None: ...
    def stop(self) -> None: ...


class ProcessRunner(Protocol):
    def start_pipeline(self, producer: list[str], consumer: list[str]) -> Pipeline: ...
    def run(self, cmd: list[str], timeout: float) -> str: ...


class _SubprocessPipeline:
    def __init__(self, producer: list[str], consumer: list[str]) -> None:
        self.p1 = subprocess.Popen(producer, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.p2 = subprocess.Popen(consumer, stdin=self.p1.stdout, stderr=subprocess.DEVNULL)
        self.p1.stdout.close()  # aplay owns the read end now

    def poll(self) -> int | None:
        code = self.p1.poll()
        return code if code is not None else self.p2.poll()

    def stop(self) -> None:
        for p in (self.p1, self.p2):
            if p.poll() is None:
                p.terminate()
        for p in (self.p1, self.p2):
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()


class SubprocessRunner:
    def start_pipeline(self, producer: list[str], consumer: list[str]) -> Pipeline:
        return _SubprocessPipeline(producer, consumer)

    def run(self, cmd: list[str], timeout: float) -> str:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True).stdout


def find_stations(rtl_power_csv: str, threshold_db: float = 8.0, merge_khz: float = 250) -> list[float]:
    """Strong carriers in rtl_power output, strongest first, in MHz.

    rtl_power rows: date, time, Hz low, Hz high, Hz step, samples, dB, dB, ...
    A station is a local maximum at least ``threshold_db`` above the median
    (noise floor); peaks closer than ``merge_khz`` count as one station.
    """
    bins: dict[float, float] = {}
    for row in csv.reader(io.StringIO(rtl_power_csv)):
        if len(row) < 7:
            continue
        lo, step = float(row[2]), float(row[4])
        for i, db in enumerate(row[6:]):
            bins[lo + i * step] = max(bins.get(lo + i * step, -999.0), float(db))
    if not bins:
        return []
    freqs = sorted(bins)
    levels = [bins[f] for f in freqs]
    floor = statistics.median(levels)
    peaks = [
        (levels[i], freqs[i])
        for i in range(1, len(freqs) - 1)
        if levels[i] >= floor + threshold_db and levels[i] >= levels[i - 1] and levels[i] >= levels[i + 1]
    ]
    chosen: list[tuple[float, float]] = []
    for level, f in sorted(peaks, reverse=True):
        if all(abs(f - g) > merge_khz * 1e3 for _, g in chosen):
            chosen.append((level, f))
    return [round(f / 1e6, 1) for _, f in chosen]


class RadioController:
    def __init__(
        self,
        runner: ProcessRunner | None = None,
        audio_device: str = "default",
        gain: str = "auto",
        ppm: int = 0,
        rtl_fm: str = "rtl_fm",
        aplay: str = "aplay",
        rtl_power: str = "rtl_power",
        tools_present: bool | None = None,  # None = look for the programs on PATH
    ) -> None:
        self.runner = runner or SubprocessRunner()
        self.audio_device = audio_device
        self.gain = gain
        self.ppm = ppm
        self.tools = {"rtl_fm": rtl_fm, "aplay": aplay, "rtl_power": rtl_power}
        self._tools_present = tools_present
        self._lock = threading.Lock()
        self._pipeline: Pipeline | None = None
        self.band = BANDS_BY_ID["fm"]
        self.freq_mhz = self.band.default_mhz
        self.squelch = 0
        self.error = ""

    # -- commands -----------------------------------------------------------
    def rtl_fm_cmd(self) -> list[str]:
        cmd = [
            self.tools["rtl_fm"], "-M", self.band.mode, "-f", f"{self.freq_mhz:.5f}M",
            "-s", str(self.band.sample_rate), "-r", str(AUDIO_RATE),
        ]
        if self.gain != "auto":
            cmd += ["-g", str(self.gain)]
        if self.ppm:
            cmd += ["-p", str(self.ppm)]
        if self.band.mode != "wbfm" and self.squelch:
            cmd += ["-l", str(self.squelch)]
        return cmd + ["-"]

    def aplay_cmd(self) -> list[str]:
        return [self.tools["aplay"], "-q", "-r", str(AUDIO_RATE), "-f", "S16_LE", "-t", "raw",
                "-c", "1", "-D", self.audio_device]

    def available(self) -> bool:
        if self._tools_present is not None:
            return self._tools_present
        return all(shutil.which(self.tools[t]) for t in ("rtl_fm", "aplay"))

    # -- control ------------------------------------------------------------
    def tune(self, band_id: str | None = None, freq_mhz: float | None = None) -> None:
        with self._lock:
            if band_id is not None:
                if band_id not in BANDS_BY_ID:
                    raise ValueError(f"unknown band {band_id!r}")
                if band_id != self.band.id:
                    self.band = BANDS_BY_ID[band_id]
                    self.freq_mhz = self.band.default_mhz
            if freq_mhz is not None:
                if not self.band.contains(freq_mhz):
                    raise ValueError(f"{freq_mhz} MHz is outside {self.band.name}")
                self.freq_mhz = self.band.snap(freq_mhz)
            self._restart()

    def step(self, direction: int) -> None:
        """One channel step up (+1) or down (−1), wrapping at the band edges."""
        with self._lock:
            f = round(self.freq_mhz + direction * self.band.step_khz / 1000, 5)
            if f > self.band.hi_mhz + 1e-6:
                f = self.band.lo_mhz
            elif f < self.band.lo_mhz - 1e-6:
                f = self.band.hi_mhz
            self.freq_mhz = self.band.snap(f)
            self._restart()

    def set_squelch(self, level: int) -> None:
        with self._lock:
            self.squelch = max(0, int(level))
            if self._pipeline is not None:
                self._restart()

    def stop(self) -> None:
        with self._lock:
            self._stop_pipeline()

    def scan_fm(self) -> list[float]:
        """Find FM stations with rtl_power. Playback stops while scanning, then resumes."""
        with self._lock:
            was_playing = self._pipeline is not None
            self._stop_pipeline()
            fm = BANDS_BY_ID["fm"]
            cmd = [self.tools["rtl_power"], "-f", f"{fm.lo_mhz}M:{fm.hi_mhz}M:50k", "-i", "2", "-1", "-"]
            try:
                stations = find_stations(self.runner.run(cmd, timeout=30))
            except (OSError, subprocess.SubprocessError) as exc:
                self.error = f"scan failed: {exc}"
                stations = []
            if was_playing:
                self._restart()
            return stations

    def status(self) -> dict:
        with self._lock:
            if self._pipeline is not None and self._pipeline.poll() is not None:
                self._pipeline = None
                self.error = "radio stopped (is the RTL-SDR plugged in?)"
            return {
                "playing": self._pipeline is not None,
                "band": self.band.id,
                "freq_mhz": self.freq_mhz,
                "squelch": self.squelch,
                "error": self.error,
                "available": self.available(),
                "bands": [asdict(b) for b in BANDS],
            }

    # -- internals (lock held) ------------------------------------------------
    def _restart(self) -> None:
        self._stop_pipeline()
        self.error = ""
        try:
            self._pipeline = self.runner.start_pipeline(self.rtl_fm_cmd(), self.aplay_cmd())
        except OSError as exc:
            self.error = f"cannot start radio: {exc}"
            log.warning("radio: %s", self.error)

    def _stop_pipeline(self) -> None:
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None
