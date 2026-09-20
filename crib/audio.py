"""Listens to what the PC is playing and turns it into beats and levels.

The analysis (`feed`) is deliberately separate from the sound card (`start`),
so the whole beat detector can be tested with synthetic audio and no hardware.

Capturing *system* audio rather than a microphone needs a loopback device;
see the README for the per-OS one-liner.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

import numpy as np

log = logging.getLogger(__name__)

# Bands in Hz. Bass carries the kick, treble carries hats and snare crack.
BANDS = {"bass": (20, 250), "mid": (250, 4000), "treble": (4000, 16000)}

# ~2s of history at the default block size: long enough to know what "normal"
# loudness is for this track, short enough to follow a build or a drop.
HISTORY = 40


class Audio:
    def __init__(
        self,
        samplerate: int = 44100,
        blocksize: int = 1024,
        device: int | str | None = None,
        sensitivity: float = 1.35,
    ) -> None:
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.device = device
        self.sensitivity = sensitivity

        self.bass = self.mid = self.treble = 0.0  # 0..1, smoothed
        self.level = 0.0
        # `beat`/`spike` are true only for the single block they occur in, and
        # audio blocks arrive faster than the effect loop reads them, so a
        # consumer polling at 20fps would miss most events. The counters are
        # the reliable signal: compare against the last value you saw.
        self.beat = False
        self.spike = False
        self.beats = 0              # monotonic count of kicks
        self.spikes = 0             # monotonic count of treble transients
        self.last_beat = 0.0
        self.bpm = 0.0
        self.running = False
        self.error: str | None = None

        self._hist: deque[float] = deque(maxlen=HISTORY)
        self._treble_hist: deque[float] = deque(maxlen=HISTORY)
        self._beat_times: deque[float] = deque(maxlen=8)
        self._lock = threading.Lock()
        self._stream = None
        self._window = np.hanning(blocksize)

    # -- analysis ----------------------------------------------------------
    def feed(self, samples: np.ndarray, now: float | None = None) -> bool:
        """Analyse one block of mono float samples. Returns True on a beat."""
        now = time.monotonic() if now is None else now
        if samples.ndim > 1:
            samples = samples.mean(axis=1)

        n = len(samples)
        window = self._window if n == self.blocksize else np.hanning(n)
        spectrum = np.abs(np.fft.rfft(samples * window)) / n
        freqs = np.fft.rfftfreq(n, 1 / self.samplerate)

        energy = {}
        for name, (lo, hi) in BANDS.items():
            band = spectrum[(freqs >= lo) & (freqs < hi)]
            # sqrt keeps the response perceptual rather than spiky
            energy[name] = float(np.sqrt(band.mean())) if band.size else 0.0

        with self._lock:
            # Attack fast, release slow: lights should snap on and fade out.
            for name, value in energy.items():
                prev = getattr(self, name)
                setattr(self, name, max(value, prev * 0.82))
            self.level = float(np.sqrt(np.mean(samples**2)))

            bass = energy["bass"]
            avg = sum(self._hist) / len(self._hist) if self._hist else 0.0
            # A beat is bass well above the recent average, with a refractory
            # gap so one kick cannot register as several.
            is_beat = (
                len(self._hist) >= 8
                and bass > avg * self.sensitivity
                and bass > 1e-4
                and now - self.last_beat > 0.18
            )
            self._hist.append(bass)
            if is_beat:
                self.beats += 1
                self.last_beat = now
                self._beat_times.append(now)
                self._update_bpm()
            self.beat = is_beat

            # Treble transients use the same relative test as the kick: an
            # absolute threshold would mean nothing across different volumes.
            treble = energy["treble"]
            t_avg = (sum(self._treble_hist) / len(self._treble_hist)
                     if self._treble_hist else 0.0)
            self.spike = (
                len(self._treble_hist) >= 8
                and treble > t_avg * self.sensitivity
                and treble > 1e-4
            )
            if self.spike:
                self.spikes += 1
            self._treble_hist.append(treble)
        return is_beat

    def _update_bpm(self) -> None:
        if len(self._beat_times) < 4:
            return
        gaps = np.diff(np.array(self._beat_times))
        gaps = gaps[(gaps > 0.25) & (gaps < 2.0)]  # 30-240 BPM
        if gaps.size:
            self.bpm = float(60.0 / np.median(gaps))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "error": self.error,
                "level": round(self.level, 4),
                "bass": round(self.bass, 4),
                "mid": round(self.mid, 4),
                "treble": round(self.treble, 4),
                "beats": self.beats,
                "spikes": self.spikes,
                "bpm": round(self.bpm, 1),
            }

    # -- sound card --------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        try:
            import sounddevice as sd
        except Exception as exc:
            self.error = f"sounddevice not installed: {exc}"
            raise RuntimeError(self.error) from exc

        def callback(indata, frames, time_info, status):
            if status:
                log.debug("audio status: %s", status)
            try:
                self.feed(indata.copy())
            except Exception:
                log.exception("audio analysis failed")

        try:
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                blocksize=self.blocksize,
                channels=1,
                dtype="float32",
                device=self.device,
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:
            self.error = str(exc)
            raise RuntimeError(f"could not open audio input: {exc}") from exc
        self.running = True
        self.error = None

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.exception("closing audio stream")
            self._stream = None
        self.running = False


def list_devices() -> list[dict]:
    """Every input the OS offers, so the user can find their loopback."""
    import sounddevice as sd

    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out.append({"index": i, "name": d["name"],
                        "channels": d["max_input_channels"]})
    return out


if __name__ == "__main__":
    for d in list_devices():
        print(f"{d['index']:>3}  {d['name']}")
