"""Self-test of the voice loop without a human.

Connects to the bot over WebRTC like a browser, "speaks" pre-synthesized phrases
(OpenAI TTS, cached in tests/audio) and listens to the bot's reply. Phrases follow VOICE_LANG.

    uv run tests/selftest.py latency "Hi, how are you?"
    uv run tests/selftest.py bargein
    uv run tests/selftest.py backchannel
    uv run tests/selftest.py photo %42 picture.png "What's in the photo?"   # into a test pane only
    uv run tests/selftest.py noise %42      # interrupted by noise: the reply is repeated (test pane)

Measured on the client side, i.e. including network and buffers:
- latency: end of our phrase -> first sound from the bot;
- bargein: start of the interrupting phrase -> silence on the bot's channel;
- backchannel: whether "mm-hm" cuts off the bot's speech.

Without a pane the bot runs in echo mode (start it with TMUX_TARGET unset); the "long" phrase
makes the echo reply long enough to interrupt.
"""

import asyncio
import fractions
import hashlib
import os
import sys
import time
from pathlib import Path

import aiohttp
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from av import AudioFrame
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

URL = os.getenv("SELFTEST_URL", "http://localhost:7860")
RATE = 48000
SAMPLES = 960  # 20 ms
AUDIO_DIR = Path(__file__).parent / "audio"
SPEECH_RMS = 300  # "the bot is speaking" threshold per 20 ms frame

# What the test says, in the bot's language.
SAY = {
    "en": {
        "hello": "Hi, how are you?",
        "long": "Say something long.",
        "cut": "Wait, I have another question.",
        "photo": "What's in this picture? Answer briefly.",
        "ack": "Mm-hm.",
    },
    "ru": {
        "hello": "Привет, как дела?",
        "long": "Скажи что-нибудь длинное.",
        "cut": "Подожди, у меня другой вопрос.",
        "photo": "Что на этой картинке? Ответь коротко.",
        "ack": "Угу.",
    },
}[os.getenv("VOICE_LANG", "en")]


async def speech(text: str) -> np.ndarray:
    """48 kHz mono int16 PCM for a phrase; cached on disk to save TTS."""
    AUDIO_DIR.mkdir(exist_ok=True)
    f = AUDIO_DIR / (hashlib.md5(text.encode()).hexdigest()[:12] + ".pcm")
    if not f.exists():
        async with aiohttp.ClientSession() as s:
            r = await s.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                json={"model": "gpt-4o-mini-tts", "voice": "alloy", "input": text, "response_format": "pcm"},
            )
            r.raise_for_status()
            f.write_bytes(await r.read())
    pcm24 = np.frombuffer(f.read_bytes(), dtype=np.int16)
    return np.repeat(pcm24, 2)  # 24 kHz -> 48 kHz


class Mic(MediaStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self._queue = np.zeros(0, dtype=np.int16)
        self._start = None
        self._pts = 0
        self.speech_ended = asyncio.Event()

    def say(self, pcm: np.ndarray):
        self.speech_ended.clear()
        self._queue = np.concatenate([self._queue, pcm])

    async def recv(self):
        if self._start is None:
            self._start = time.monotonic()
        else:
            self._pts += SAMPLES
            delay = self._start + self._pts / RATE - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        chunk, self._queue = self._queue[:SAMPLES], self._queue[SAMPLES:]
        if len(chunk) and not len(self._queue):
            self.speech_end_t = time.monotonic()
            self.speech_ended.set()
        chunk = np.pad(chunk, (0, SAMPLES - len(chunk)))
        frame = AudioFrame(format="s16", layout="mono", samples=SAMPLES)
        frame.planes[0].update(chunk.tobytes())
        frame.pts, frame.sample_rate, frame.time_base = self._pts, RATE, fractions.Fraction(1, RATE)
        return frame


class Speaker:
    """Receives the bot's audio and remembers when it was speaking."""

    def __init__(self):
        self.frames: list[tuple[float, float]] = []  # (time, rms)

    async def run(self, track):
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return  # connection closed
            a = frame.to_ndarray().astype(np.float32)
            self.frames.append((time.monotonic(), float(np.sqrt(np.mean(a * a)))))

    def speaking(self, window=0.1) -> bool:
        now = time.monotonic()
        return any(r > SPEECH_RMS for t, r in self.frames if t > now - window)

    async def wait_speech(self, after: float, timeout=30.0) -> float | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for t, r in self.frames:
                if t > after and r > SPEECH_RMS:
                    return t
            await asyncio.sleep(0.02)
        return None

    async def wait_silence(self, after: float, silence=0.5, timeout=60.0) -> float | None:
        """The moment since which the bot has been silent for at least `silence` seconds."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            last_loud = max([t for t, r in self.frames if r > SPEECH_RMS] + [after])
            if time.monotonic() - last_loud > silence:
                quiet = [t for t, r in self.frames if t > last_loud]
                return quiet[0] if quiet else last_loud
            await asyncio.sleep(0.02)
        return None


async def connect(request_data: dict | None = None):
    pc = RTCPeerConnection()
    mic, speaker = Mic(), Speaker()
    pc.addTrack(mic)
    pc.createDataChannel("chat")

    @pc.on("track")
    def on_track(track):
        if track.kind == "audio":
            asyncio.ensure_future(speaker.run(track))

    await pc.setLocalDescription(await pc.createOffer())
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{URL}/api/offer", json={"sdp": pc.localDescription.sdp, "type": "offer", "request_data": request_data})
        ans = await r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
    # Wait for the greeting ("Listening.") and its end.
    t0 = time.monotonic()
    if await speaker.wait_speech(t0, timeout=20):
        await speaker.wait_silence(t0)
    return pc, mic, speaker


async def latency(text: str = SAY["hello"], timeout=60.0):
    pc, mic, speaker = await connect()
    mic.say(await speech(text))
    await mic.speech_ended.wait()
    t = await speaker.wait_speech(mic.speech_end_t, timeout=timeout)
    if t is None:
        print("RESULT latency: the bot did not answer")
    else:
        print(f"RESULT latency: {t - mic.speech_end_t:.2f}s from the end of the phrase to the first sound")
        end = await speaker.wait_silence(t, silence=1.5, timeout=timeout)
        print(f"RESULT reply_duration: {end - t:.1f}s")
    await pc.close()


async def bargein(question=SAY["long"], cut=SAY["cut"], after=2.0):
    pc, mic, speaker = await connect()
    mic.say(await speech(question))
    await mic.speech_ended.wait()
    t = await speaker.wait_speech(mic.speech_end_t)
    print(f"RESULT latency: {t - mic.speech_end_t:.2f}s")
    await asyncio.sleep(after)
    if not speaker.speaking(0.3):
        print("RESULT bargein: the bot went quiet before the interruption, the reply is too short")
    t_cut = time.monotonic()
    mic.say(await speech(cut))
    stop = await speaker.wait_silence(t_cut, silence=0.4, timeout=15)
    print(f"RESULT bargein: the bot went quiet {stop - t_cut:.2f}s after the interrupting phrase started")
    await mic.speech_ended.wait()
    t2 = await speaker.wait_speech(mic.speech_end_t, timeout=60)
    if t2:
        print(f"RESULT after_bargein_latency: {t2 - mic.speech_end_t:.2f}s")
        await speaker.wait_silence(t2, silence=1.5)
    await pc.close()


async def photo(pane: str, image: str, text=SAY["photo"]):
    """POST /api/image without a caption, then a spoken phrase: the photo must go out with it."""
    pc, mic, speaker = await connect({"pane": pane, "inject": True})
    async with aiohttp.ClientSession() as s:
        form = aiohttp.FormData()
        form.add_field("file", Path(image).read_bytes(), filename=Path(image).name)
        r = await s.post(f"{URL}/api/image", data=form)
        print(f"RESULT upload: {r.status} {await r.text()}")
    await asyncio.sleep(2)  # "Got the photo."
    mic.say(await speech(text))
    await mic.speech_ended.wait()
    t = await speaker.wait_speech(mic.speech_end_t, timeout=60)
    print("RESULT photo: the bot did not answer" if t is None else f"RESULT photo: reply after {t - mic.speech_end_t:.2f}s")
    if t:
        await speaker.wait_silence(t, silence=2.5, timeout=60)
    await pc.close()


async def noise(pane: str, question=SAY["long"], after=1.5):
    """Interrupt the bot with speech-like sound without words (a phrase played backwards): the reply must be repeated."""
    pc, mic, speaker = await connect({"pane": pane, "inject": False})
    mic.say(await speech(question))
    await mic.speech_ended.wait()
    t = await speaker.wait_speech(mic.speech_end_t, timeout=60)
    # The filler ("One sec.") comes first, claude's reply after a pause.
    t = await speaker.wait_speech(await speaker.wait_silence(t, silence=0.5), timeout=60)
    print(f"RESULT reply: {t - mic.speech_end_t:.2f}s" if t else "RESULT reply: the bot did not answer")
    await asyncio.sleep(after)
    t_cut = time.monotonic()
    mic.say((await speech(SAY["cut"]))[::-1].copy())
    stop = await speaker.wait_silence(t_cut, silence=0.4, timeout=15)
    print(f"RESULT cut: the bot went quiet after {stop - t_cut:.2f}s")
    t2 = await speaker.wait_speech(stop, timeout=20)
    print(f"RESULT replay: reply repeated after {t2 - stop:.2f}s" if t2 else "RESULT replay: not repeated")
    if t2:
        await speaker.wait_silence(t2, silence=1.5, timeout=60)
    await pc.close()


async def backchannel(question=SAY["long"], ack=SAY["ack"], after=2.0):
    pc, mic, speaker = await connect()
    mic.say(await speech(question))
    await mic.speech_ended.wait()
    t = await speaker.wait_speech(mic.speech_end_t)
    await asyncio.sleep(after)
    t_ack = time.monotonic()
    mic.say(await speech(ack))
    await mic.speech_ended.wait()
    await asyncio.sleep(1.0)
    # Was speech cut off: a pause >= 0.5 s in the window after "mm-hm" (pauses between sentences are shorter).
    loud = [tt for tt, r in speaker.frames if t_ack <= tt <= time.monotonic() and r > SPEECH_RMS]
    gap = max((b - a for a, b in zip(loud, loud[1:])), default=99)
    verdict = f"speech cut off (pause {gap:.1f}s)" if gap >= 0.5 else "speech continued"
    print(f"RESULT backchannel: \"{ack}\" -> {verdict}")
    await speaker.wait_silence(t_ack, silence=1.5)
    await pc.close()


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["latency"]
    if cmd in ("photo", "noise"):
        asyncio.run({"photo": photo, "noise": noise}[cmd](*rest))
        sys.exit()
    fn = {"latency": latency, "bargein": bargein, "backchannel": backchannel}[cmd]
    asyncio.run(fn(" ".join(rest)) if rest else fn())
