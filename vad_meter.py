"""Silero VAD that logs loudness, to pick an adaptive volume threshold for noisy places (NOISE.md).

Per VAD frame (32 ms at 16 kHz) pipecat compares the voice confidence with `confidence` and the smoothed
loudness (BS.1770 LUFS mapped to 0..1 over -110..-10 LUFS) with `min_volume`. This logs, when a stretch of
speech ends, its median loudness and the background: the 20th percentile of the loudness over the last
FLOOR_SECS. It also logs voice-like stretches that the volume threshold alone rejected (someone else
talking quietly, or the user too far from the microphone). The threshold itself is not changed yet.
"""

import statistics
import time
from collections import deque

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADState

FLOOR_SECS = 15.0
REPORT_MIN_FRAMES = 8  # ~0.25 s at 16 kHz: shorter blips aren't worth a line


class MeteredSileroVAD(SileroVADAnalyzer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._history: deque[tuple[float, float]] = deque()  # (time, loudness) of every frame
        self._speech: list[float] = []  # loudness of the frames of the current speech stretch
        self._rejected: list[float] = []  # voice-like frames below min_volume, in a row
        self._confidence = 0.0

    def voice_confidence(self, buffer: bytes) -> float:
        self._confidence = super().voice_confidence(buffer)
        return self._confidence

    def _get_smoothed_volume(self, audio: bytes) -> float:
        volume = super()._get_smoothed_volume(audio)
        now = time.monotonic()
        self._history.append((now, volume))
        while self._history and self._history[0][0] < now - FLOOR_SECS:
            self._history.popleft()
        voice = self._confidence >= self._params.confidence
        if voice and volume >= self._params.min_volume:
            self._speech.append(volume)
        elif voice:
            self._rejected.append(volume)
        elif self._rejected:
            self._report("rejected by volume", self._rejected)
            self._rejected = []
        return volume

    def _floor(self) -> float:
        vols = sorted(v for _, v in self._history)
        return vols[len(vols) // 5] if vols else 0.0

    def _report(self, what: str, vols: list[float]):
        if len(vols) >= REPORT_MIN_FRAMES:
            logger.info(
                f"VOLUME {what}: median {statistics.median(vols):.2f}, max {max(vols):.2f}, "
                f"{len(vols) * self._vad_frames / self.sample_rate:.1f}s; floor {self._floor():.2f}, min_volume {self._params.min_volume:.2f}"
            )

    def _run_analyzer(self, buffer: bytes) -> VADState:
        state = super()._run_analyzer(buffer)
        if state == VADState.QUIET and self._speech:
            self._report("speech", self._speech)
            self._speech = []
        return state
