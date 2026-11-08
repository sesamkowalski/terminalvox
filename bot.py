"""Voice interface to a Claude Code session: browser (WebRTC) -> STT -> tmux -> transcript -> TTS.

Run: ./run.sh or uv run bot.py   (see README.md)
"""

import json
import os

# pipecat.runner.run loads .env with override=True on import: keep the real environment to restore it.
_ENV = dict(os.environ)

import secrets
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from loguru import logger

from fastapi import Body, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.datastructures import Headers

from bridge import ClaudeSession, TmuxBridge, list_sessions
from phrases import LANGS
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    LLMContextUserTurnFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.run import app
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import BaseUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

os.environ.update(_ENV)
load_dotenv()  # environment variables take precedence over .env

ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]
# Language of speech recognition, synthesis and of everything the bridge itself says (phrases.py).
VOICE_LANG = os.getenv("VOICE_LANG", "en")
if VOICE_LANG not in LANGS:
    raise SystemExit(f"VOICE_LANG={VOICE_LANG!r}: supported {", ".join(LANGS)}")
PHRASES = LANGS[VOICE_LANG]
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID") or PHRASES.voice
TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
# Default session when nothing is chosen in the UI. Empty: echo instead of Claude Code.
# A tmux target, e.g. "=voice:0.0" or a pane id "%64".
TMUX_TARGET = os.getenv("TMUX_TARGET", "")
VOICE_PREFIX = os.getenv("VOICE_PREFIX", "[voice] ")
# Seconds of continuous speech (beyond the VAD trigger) needed to interrupt the bot.
BARGE_IN_SECS = float(os.getenv("BARGE_IN_SECS", "0.5"))
MAX_SPOKEN_CHARS = int(os.getenv("MAX_SPOKEN_CHARS", "600"))
# After this many seconds of silence from claude the bot says "one sec" or similar. 0 = off.
FILLER_SECS = float(os.getenv("FILLER_SECS", "1.5"))
# Announce tool calls ("searching the web") at most once per this many seconds. 0 = off.
TOOL_GAP_SECS = float(os.getenv("TOOL_GAP_SECS", "4"))
IDLE_TIMEOUT_SECS = float(os.getenv("IDLE_TIMEOUT_SECS", "600"))
# If the text says the phrase isn't finished ("Hmm...", "one through"), wait this many seconds
# for the rest and send it all as one message. 0 = send as soon as Smart Turn decides.
HOLD_SECS = float(os.getenv("HOLD_SECS", "2.5"))
# Silence after which Smart Turn decides whether the phrase is finished. Lower is faster,
# but Smart Turn then cuts Russian speech at pauses near commas (see NOTES.md).
VAD_STOP_SECS = float(os.getenv("VAD_STOP_SECS", "0.5"))
# If Smart Turn considers the phrase unfinished, the turn still closes after this pause.
TURN_MAX_PAUSE_SECS = float(os.getenv("TURN_MAX_PAUSE_SECS", "1.5"))
# A new phrase within this many seconds of the last one sent (claude still silent) is merged with it.
MERGE_SECS = float(os.getenv("MERGE_SECS", "5"))
# A short tone when a phrase has been sent to the session.
BEEP = os.getenv("BEEP", "1") != "0"

# The client picks the speech pace (request_data.pace): pauses depend on the person, not the server.
# (wait for an unfinished phrase, max Smart Turn pause, merge window)
PACES = {
    "fast": (1.5, 1.0, 4.0),
    "normal": (HOLD_SECS, TURN_MAX_PAUSE_SECS, MERGE_SECS),
    "slow": (4.0, 2.5, 8.0),
}


# Photos from the client: where to store them and how long to wait for a phrase to send them with.
IMAGES_DIR = Path(os.getenv("IMAGES_DIR") or Path.home() / ".cache" / "terminalvox" / "images")
IMAGE_WAIT_SECS = float(os.getenv("IMAGE_WAIT_SECS", "10"))
IMAGE_KEEP_DAYS = 7
MAX_IMAGE_BYTES = 15 * 1024 * 1024
# Type by file signature: claude accepts only these formats.
IMAGE_MAGIC = {b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png", b"GIF8": ".gif", b"RIFF": ".webp"}

# Directory for a rotating log file (the systemd service sets it). Empty: stderr only.
LOG_DIR = os.getenv("LOG_DIR", "")


def _log_to_file():
    # The Pipecat runner resets loguru handlers in main(), so the file sink is added on server startup.
    if LOG_DIR:
        Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
        logger.add(Path(LOG_DIR) / "bot.log", level="DEBUG", rotation="20 MB", retention=5, enqueue=True)


app.router.add_event_handler("startup", _log_to_file)

# Tailscale logins allowed to connect through `tailscale serve` (comma-separated).
# Unset: the owner of this machine (see _tailnet_users).
TAILNET_USERS = {u.strip().lower() for u in os.getenv("TAILNET_USERS", "").split(",") if u.strip()}


def _machine_owner() -> str:
    """The tailnet login of this machine's owner, or "" if tailscaled doesn't know it yet."""
    try:
        d = json.loads(subprocess.run(["tailscale", "status", "--json"], capture_output=True, timeout=5).stdout)
        return ((d.get("User") or {}).get(str((d.get("Self") or {}).get("UserID"))) or {}).get("LoginName", "").lower()
    except Exception:
        return ""


def _tailnet_users() -> set[str]:
    """The owner is looked up on the first tailnet request, not at startup: after a reboot the service
    starts before tailscaled connects and would otherwise deny everyone until restarted."""
    global TAILNET_USERS
    if not TAILNET_USERS and (owner := _machine_owner()):
        TAILNET_USERS = {owner}
        logger.info(f"Tailnet access: {owner} (machine owner)")
    return TAILNET_USERS
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _deny_reason(headers: Headers) -> str | None:
    """Why the request must be denied; None if it is allowed.

    tailscale serve adds a Tailscale-User-Login header with the verified login (and strips one
    forged by the client); requests via Funnel and from tagged devices have no login. Without a
    login only requests to localhost are allowed. Origin must match Host: the Pipecat runner
    enables CORS for all sites, and without this check any page open in a browser could connect
    to localhost and talk to claude with a synthesized voice.
    """
    host = headers.get("host", "")
    origin = headers.get("origin")
    if origin and urlsplit(origin).netloc != host:
        return f"foreign Origin {origin}"
    hostname = urlsplit("//" + host).hostname or ""
    login = headers.get("tailscale-user-login", "").lower()
    if login:
        return None if login in _tailnet_users() else f"login {login} not in TAILNET_USERS"
    if hostname in LOCAL_HOSTS:
        return None
    return f"no Tailscale login (Host {host})"


class AccessGuard:
    """ASGI middleware: guards every route, including the runner's routes and WebSocket."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            reason = _deny_reason(Headers(scope=scope))
            if reason:
                logger.warning(f"Denied {scope['type']} {scope['path']}: {reason}")
                if scope["type"] == "http":
                    await PlainTextResponse("Forbidden", 403)(scope, receive, send)
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


class CamelCaseOffer:
    """Some Pipecat clients (the React Native transport) send `requestData` to /api/offer, while the
    runner parses the body into a dataclass that only knows `request_data`: without the rename the
    chosen session was lost."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].endswith("/api/offer"):
            return await self.app(scope, receive, send)
        body, more = b"", True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
        try:
            data = json.loads(body)
            if isinstance(data, dict) and "requestData" in data and "request_data" not in data:
                data["request_data"] = data.pop("requestData")
                body = json.dumps(data).encode()
        except ValueError:
            pass
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)


app.add_middleware(CamelCaseOffer)
app.add_middleware(AccessGuard)  # added last, runs first

# One call at a time: the bridge of the current connection, so the UI can switch sessions.
ACTIVE: dict[str, TmuxBridge | None] = {"bridge": None}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(Path(__file__).parent / "ui.html")


@app.get("/api/sessions")
async def sessions():
    return {"sessions": list_sessions(), "connected": ACTIVE["bridge"] is not None}


@app.post("/api/switch")
async def switch(pane: str = Body(...), inject: bool = Body(True)):
    bridge = ACTIVE["bridge"]
    if bridge is None:
        raise HTTPException(409, "no active call")
    try:
        session = ClaudeSession(pane)
    except Exception as e:
        raise HTTPException(404, str(e))
    await bridge.switch(session, inject)
    return {"name": session.name}


class Timings:
    """Timings of one turn: end of speech -> end of phrase -> tmux -> first text -> first audio."""

    ORDER = ["vad_stop", "turn_end", "tmux_sent", "first_text", "first_audio"]

    def __init__(self):
        self.reset()

    def reset(self):
        self.t: dict[str, float] = {}

    def mark(self, name: str):
        if name == "vad_stop":
            if "turn_end" not in self.t:
                self.t[name] = time.monotonic()  # the last pause before the end of the phrase
        elif name == "turn_end":
            self.t = {k: v for k, v in self.t.items() if k == "vad_stop"}
            self.t[name] = time.monotonic()
        elif "turn_end" in self.t and name not in self.t:
            self.t[name] = time.monotonic()
            if name == "first_audio":
                self.report()

    def report(self):
        names = [n for n in self.ORDER if n in self.t]
        steps = " ".join(f"{a}→{b}={self.t[b] - self.t[a]:.2f}s" for a, b in zip(names, names[1:]))
        total = self.t["first_audio"] - self.t[names[0]]
        logger.info(f"TIMING total={total:.2f}s {steps}")
        self.reset()


class FreshTranscriptStopStrategy(TurnAnalyzerUserTurnStopStrategy):
    """End of phrase only with a transcription of the latest piece of speech.

    The base strategy remembers the text since the start of the turn and doesn't reset it on a pause
    mid-phrase. Someone says "Uh…", pauses (Smart Turn: unfinished), continues, and on the next
    pause the turn closes right away on the old text without waiting for the transcription of the
    continuation. That arrives outside the turn, opens a new one, and the new one ends empty: the
    phrase is lost (2026-11-02). Reset the text when speech resumes, so the turn waits for it.
    """

    async def _handle_vad_user_started_speaking(self, frame):
        await super()._handle_vad_user_started_speaking(frame)
        self._text = ""


BACKCHANNEL_MAX_WORDS = 2  # "mm-hm", "yeah yeah", "uh-huh"


class BargeInStartStrategy(BaseUserTurnStartStrategy):
    """Start of phrase by VAD; while the bot speaks, only after `min_secs` of continuous speech.

    This way an interruption takes a fraction of a second, and a short "mm-hm" doesn't cut the bot off.
    Interrupting by words (MinWords) waits for interim STT results, which takes seconds.
    """

    def __init__(self, min_secs: float, **kwargs):
        super().__init__(**kwargs)
        self._min_secs = min_secs
        self._bot_speaking = False
        self._in_turn = False
        self._speech_since: float | None = None  # the user has been speaking (by VAD) since then

    async def handle_user_turn_started(self):
        self._in_turn = True

    async def handle_user_turn_stopped(self):
        # Smart Turn may close the turn on a pause mid-phrase while VAD still hears
        # speech; then the continuation opens a new turn via process_frame.
        self._in_turn = False

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._speech_since = time.monotonic()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._speech_since = None
        elif isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)) and not self._in_turn:
            if not self._bot_speaking:
                await self.trigger_user_turn_started()
                return ProcessFrameResult.STOP
            if self._speech_since is None:
                # A short "mm-hm" while the bot speaks must not stick to the next phrase.
                # A longer text is a late transcription of a real phrase (STT lags by seconds
                # and the bot is already saying "one sec"): it is kept.
                if len(frame.text.split()) > BACKCHANNEL_MAX_WORDS:
                    await self.trigger_user_turn_started()
                    return ProcessFrameResult.STOP
                await self.trigger_reset_aggregation()

        if self._in_turn or self._speech_since is None:
            return ProcessFrameResult.CONTINUE
        if not self._bot_speaking or time.monotonic() - self._speech_since >= self._min_secs:
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP
        return ProcessFrameResult.CONTINUE


class TimingObserver(BaseObserver):
    def __init__(self, timings: Timings):
        super().__init__()
        self._timings = timings

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if isinstance(f, VADUserStoppedSpeakingFrame):
            self._timings.mark("vad_stop")
        elif isinstance(f, LLMContextUserTurnFrame) and data.direction == FrameDirection.DOWNSTREAM:
            self._timings.mark("turn_end")
        elif isinstance(f, BotStartedSpeakingFrame):
            self._timings.mark("first_audio")


@app.post("/api/image")
async def image(file: UploadFile, text: str = Form("")):
    """A photo into the current call. Without a caption it goes out with the next phrase."""
    bridge = ACTIVE["bridge"]
    if bridge is None:
        raise HTTPException(409, "no active call")
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "photo larger than 15 MB")
    ext = next((e for m, e in IMAGE_MAGIC.items() if data.startswith(m)), None)
    if ext is None or (ext == ".webp" and data[8:12] != b"WEBP"):
        raise HTTPException(415, "JPEG, PNG, GIF or WebP required")
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    for old in IMAGES_DIR.iterdir():
        if time.time() - old.stat().st_mtime > IMAGE_KEEP_DAYS * 86400:
            old.unlink(missing_ok=True)
    path = IMAGES_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}{ext}"
    path.write_bytes(data)
    await bridge.add_image(str(path), text.strip())
    return {"name": path.name}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    body = runner_args.body or {}  # request_data from /api/offer: the choice made in the UI
    target = body.get("pane") or TMUX_TARGET
    session = ClaudeSession(target) if target else None
    logger.info(f"Mode: {'tmux ' + target if session else 'echo'}, language {VOICE_LANG}")
    pace = body.get("pace") if body.get("pace") in PACES else "normal"
    hold_secs, max_pause_secs, merge_secs = PACES[pace]
    logger.info(f"Pace: {pace} (hold {hold_secs}s, max pause {max_pause_secs}s, merge {merge_secs}s)")

    stt = ElevenLabsRealtimeSTTService(
        api_key=ELEVENLABS_API_KEY,
        settings=ElevenLabsRealtimeSTTService.Settings(language=Language(PHRASES.language)),
    )
    tts = ElevenLabsTTSService(
        api_key=ELEVENLABS_API_KEY,
        settings=ElevenLabsTTSService.Settings(
            voice=VOICE_ID, model=TTS_MODEL, language=Language(PHRASES.language)
        ),
        # The default is 3 s: the first ElevenLabs response after connecting can take longer, and audio was dropped.
        stop_frame_timeout_s=6.0,
    )

    user_aggregator, _ = LLMContextAggregatorPair(
        LLMContext(),
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
            user_turn_strategies=UserTurnStrategies(
                start=[BargeInStartStrategy(BARGE_IN_SECS)],
                stop=[
                    FreshTranscriptStopStrategy(
                        turn_analyzer=LocalSmartTurnAnalyzerV3(
                            params=SmartTurnParams(stop_secs=max_pause_secs)
                        )
                    )
                ],
            ),
        ),
    )

    timings = Timings()
    bridge = TmuxBridge(
        session, timings, phrases=PHRASES, prefix=VOICE_PREFIX, max_chars=MAX_SPOKEN_CHARS, filler_secs=FILLER_SECS,
        tool_gap_secs=TOOL_GAP_SECS, hold_secs=hold_secs, merge_secs=merge_secs, beep=BEEP,
        image_wait_secs=IMAGE_WAIT_SECS,
        inject=body.get("inject", True),
    )

    pipeline = Pipeline([transport.input(), stt, user_aggregator, bridge, tts, transport.output()])

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
        observers=[TimingObserver(timings)],
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        ACTIVE["bridge"] = bridge
        await bridge.start_voice()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        if ACTIVE["bridge"] is bridge:
            ACTIVE["bridge"] = None
        await runner.cancel()

    await runner.run()


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(
        runner_args,
        {"webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True)},
    )
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
