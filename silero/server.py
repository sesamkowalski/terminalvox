"""Silero speech synthesis as a small HTTP service, so the model loads once and survives bot restarts.

The protocol is that of Piper's HTTP server, so the bot uses Pipecat's PiperHttpTTSService as is:
POST / with JSON {"text": "...", "voice": "xenia"} returns a WAV file. GET /health answers "ok".
Optional "rate" and "pitch" change the speech per request, in the JSON body or in the query string
(POST /?rate=fast&pitch=low, handy as the bot's base_url); the body wins, then the query, then the
SILERO_RATE / SILERO_PITCH defaults. Silero knows five levels of each (and pitch "robo"); a percentage
is snapped by the model to the nearest level: rate 150% sounds as x-fast, 80% as slow.
The text must already be Cyrillic: the model drops Latin letters and digits (the bot normalizes it).
Listens on 127.0.0.1 only.
"""

import io
import json
import logging
import re
import os
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape

import torch

MODEL = os.getenv("SILERO_MODEL", "v5_6_ru")
VOICE = os.getenv("SILERO_VOICE", "xenia")
THREADS = int(os.getenv("SILERO_THREADS", "8"))  # 8 was the fastest on a 12-core Xeon, see TTS-LOCAL.md
PITCH = os.getenv("SILERO_PITCH", "low")  # x-low, low, medium, high, x-high, robo; "" for none
RATE = os.getenv("SILERO_RATE", "fast")  # x-slow, slow, medium, fast, x-fast; "" for none
LEVELS = {"rate": {"x-slow", "slow", "medium", "fast", "x-fast"},
          "pitch": {"x-low", "low", "medium", "high", "x-high", "robo"}}
SAMPLE_RATE = int(os.getenv("SILERO_SAMPLE_RATE", "24000"))  # the model has only 8000, 24000, 48000
PORT = int(os.getenv("SILERO_PORT", "7875"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("silero")

# The cached copy of the hub repository: no network on later starts.
_HUB = Path(torch.hub.get_dir()) / "snakers4_silero-models_master"
t0 = time.perf_counter()
if _HUB.exists():
    model, _ = torch.hub.load(str(_HUB), "silero_tts", source="local", language="ru", speaker=MODEL, verbose=False)
else:
    model, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker=MODEL,
                              trust_repo=True, verbose=False)
model.to("cpu")
torch.set_num_threads(THREADS)  # after loading: Silero resets it to 1
LOCK = threading.Lock()  # one model, one synthesis at a time


ALLOWED = set(model.symbols) | set("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")


def wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def check(name: str, value: str) -> str:
    """A prosody value as Silero takes it: a level name, a percentage or "" for none."""
    value = value.strip().lower()
    if value and value not in LEVELS[name] and not re.fullmatch(r"[+-]?\d{1,3}%", value):
        raise ValueError(f"{name} should be one of {', '.join(sorted(LEVELS[name]))} or a percentage")
    return value


def synth(text: str, voice: str, pitch: str = PITCH, rate: str = RATE) -> bytes:
    # Anything outside the model's alphabet is dropped by it anyway, but in SSML it breaks parsing.
    text = " ".join("".join(c if c in ALLOWED else " " for c in text).split())
    if not any(c.isalpha() for c in text):
        return wav(b"\0\0" * (SAMPLE_RATE // 20))  # nothing to say: 50 ms of silence, not an error
    attrs = "".join(f' {k}="{v}"' for k, v in (("pitch", pitch), ("rate", rate)) if v)
    ssml = f"<speak><prosody{attrs}>{escape(text)}</prosody></speak>" if attrs else None
    with LOCK:
        audio = model.apply_tts(ssml_text=ssml, text=None if ssml else text, speaker=voice, sample_rate=SAMPLE_RATE)
    return wav((audio.clamp(-1, 1) * 32767).to(torch.int16).numpy().tobytes())


synth("Синтез готов.", VOICE)  # warm-up: the first call is several times slower
log.info(f"model {MODEL} loaded in {time.perf_counter() - t0:.1f} s, voice {VOICE}, {THREADS} threads, "
         f"pitch {PITCH or '-'}, rate {RATE or '-'}, {SAMPLE_RATE} Hz")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            self.reply(200, b"ok")
        else:
            self.reply(404, b"not found")

    def do_POST(self):
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            query = {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items()}
            try:
                pitch = check("pitch", str(req.get("pitch", query.get("pitch", PITCH))))
                rate = check("rate", str(req.get("rate", query.get("rate", RATE))))
            except ValueError as e:
                return self.reply(400, str(e).encode())
            text = (req.get("text") or "").strip()
            voice = req.get("voice") or VOICE
            if voice not in model.speakers:
                return self.reply(400, f"unknown voice {voice}, have {', '.join(model.speakers)}".encode())
            if not text:
                return self.reply(400, b"empty text")
            t = time.perf_counter()
            body = synth(text, voice, pitch, rate)
            log.info(f"{len(text)} chars in {time.perf_counter() - t:.2f} s, {voice} pitch {pitch or '-'} "
                     f"rate {rate or '-'}: {text[:60]!r}")
            self.reply(200, body, "audio/wav")
        except Exception as e:  # a text Silero can't parse must not kill the service
            log.exception("synthesis failed")
            self.reply(500, str(e).encode())


if __name__ == "__main__":
    ThreadingHTTPServer.daemon_threads = True
    log.info(f"listening on http://127.0.0.1:{PORT}/")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
