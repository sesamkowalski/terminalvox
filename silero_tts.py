"""Client for the local Silero synthesis service (silero/server.py), TTS_PROVIDER=silero.

Pipecat's PiperHttpTTSService speaks the same protocol but sends only text and voice; this one also
sends rate and pitch, which can be changed during a call (tts.rate = "slow"). The text is normalized
first: Silero reads Cyrillic only, see ru_normalize.py.

The audio is paced to real time (LEAD_SECS ahead): a whole sentence arriving at once made the WebRTC
output "stop speaking" right away while the phone was still playing it, so barge-in protection was
off and the bot's own voice from the phone speaker interrupted it.
"""

LEAD_SECS = 0.2

import asyncio
import time
from collections.abc import AsyncGenerator

import aiohttp
from pipecat.frames.frames import ErrorFrame, Frame, TTSStoppedFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

import ru_normalize
import stress


class SileroTTSService(TTSService):
    def __init__(self, *, url: str, voice: str = "", rate: str | None = None, pitch: str | None = None,
                 latin_file: str = "latin.txt", stress_file: str = "stress.txt", **kwargs):
        """rate / pitch None: the service's defaults (SILERO_RATE / SILERO_PITCH of the service)."""
        latin, stressed = ru_normalize.load(latin_file), stress.load(stress_file)

        async def normalize(text: str, text_type: str) -> str:
            return stress.stress(ru_normalize.normalize(text, latin), stressed, plus=True)

        super().__init__(push_start_frame=True, push_stop_frames=True,
                         settings=TTSSettings(model=None, voice=voice or None, language=None),
                         text_transforms=[("*", normalize)], **kwargs)
        self._url = url.rstrip("/") + "/"
        self.voice, self.rate, self.pitch = voice, rate, pitch
        self._http: aiohttp.ClientSession | None = None

    def can_generate_metrics(self) -> bool:
        return True

    async def cleanup(self):
        await super().cleanup()
        if self._http:
            await self._http.close()
            self._http = None

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        req = {"text": text, "voice": self.voice, "rate": self.rate, "pitch": self.pitch}
        try:
            self._http = self._http or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
            async with self._http.post(self._url, json={k: v for k, v in req.items() if v is not None}) as r:
                if r.status != 200:
                    yield ErrorFrame(error=f"Silero {r.status}: {await r.text()}")
                    yield TTSStoppedFrame(context_id=context_id)
                    return
                await self.start_tts_usage_metrics(text)
                start, sent = time.monotonic(), 0.0
                async for frame in self._stream_audio_frames_from_iterator(
                    r.content.iter_chunked(self.chunk_size), strip_wav_header=True, context_id=context_id
                ):
                    await self.stop_ttfb_metrics()
                    yield frame
                    sent += len(frame.audio) / 2 / frame.sample_rate
                    if (ahead := sent - (time.monotonic() - start)) > LEAD_SECS:
                        await asyncio.sleep(ahead - LEAD_SECS)
        except (aiohttp.ClientError, TimeoutError) as e:
            yield ErrorFrame(error=f"Silero at {self._url} is not reachable ({e}): make silero-status")
            yield TTSStoppedFrame(context_id=context_id)
        finally:
            await self.stop_ttfb_metrics()
