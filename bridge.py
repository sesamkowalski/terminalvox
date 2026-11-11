"""Bridge between the voice pipeline and a Claude Code session in tmux.

In: the final text of a user phrase -> `tmux send-keys -l` into the pane running claude.
Out: new assistant text blocks from the session's JSONL transcript -> TTS.
The transcript format and the quirks found along the way are described in NOTES.md.
"""

import asyncio
import json
import os
import random
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from loguru import logger

from commands import find, parse, pick
from phrases import EN, Phrases
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndTaskFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMContextUserTurnFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame

CLAUDE_DIR = Path.home() / ".claude"


def is_checkin(text: str, p: Phrases) -> bool:
    """A short phrase made only of "are you there?" / "how is it going?" and hesitations: no request
    inside it gets lost if the bridge answers it instead of Claude."""
    parts = re.split(r"(?<=[.?!…])\s+", text.strip())
    return (
        len(text.split()) <= 10
        and any(p.checkin.match(s) for s in parts)
        and all(p.checkin.match(s) or p.hesitation.match(s) for s in parts)
    )


# ElevenLabs Scribe marks cut-off speech: "…through", "n-n-no-", "Hmm...". A finished phrase ends
# with a period, "?" or "!", but not with an ellipsis.
COMPLETE_RE = re.compile(r"(?<!\.)[.?!]['\"»)]*$")


def looks_complete(text: str) -> bool:
    return bool(COMPLETE_RE.search(text.strip()))


def status_phrase(p: Phrases, started_at: float, activity: str, now: float | None = None) -> str:
    """The bridge's answer to "are you there?" during a long task."""
    parts = [p.working]
    if started_at:
        mins = int(((now or time.time()) - started_at) // 60)
        parts.append(p.minutes(mins) if mins else p.just_started)
    parts.append(".")
    if activity:
        parts.append(f" {p.now}{activity[0].lower()}{activity[1:]}")
    return "".join(parts)


def tool_phrase(block: dict, p: Phrases) -> str | None:
    name = block.get("name", "")
    if name == "Bash":
        cmd = (block.get("input") or {}).get("command", "")
        return next((ph for pat, ph in p.bash if re.search(pat, cmd)), p.bash_default)
    if name.startswith("mcp__"):
        return p.mcp
    return p.tools.get(name)  # internal tools (TaskUpdate, ToolSearch…) are not announced


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()


def list_sessions() -> list[dict]:
    """Live Claude Code sessions in tmux, most recently active first."""
    panes = {}
    for line in _run("tmux", "list-panes", "-a", "-F", "#{pane_id}\t#{session_name}:#{window_index} #{window_name}").splitlines():
        pane, where = line.split("\t", 1)
        panes[pane] = where
    out = []
    for f in (CLAUDE_DIR / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text())
            os.kill(d["pid"], 0)
        except (ValueError, KeyError, OSError):
            continue
        pane = (d.get("tmux") or "").rpartition(".")[2]
        if pane not in panes:
            continue  # not in tmux (background or a plain terminal): send-keys can't reach it
        out.append({
            "name": d.get("name") or pane,
            "pane": pane,
            "where": panes[pane],
            "cwd": d["cwd"].replace(str(Path.home()), "~"),
            "status": d.get("status", ""),
            "waitingFor": d.get("waitingFor", ""),
            "updatedAt": d.get("updatedAt", 0),
        })
    return sorted(out, key=lambda x: -x["updatedAt"])


class ClaudeSession:
    """A tmux pane running claude, and its transcript."""

    def __init__(self, target: str):
        # Pin the pane by id (%NN): tmux may match a name like "voice" by prefix against
        # another window, and an id won't move to a different pane if the session dies.
        self.target = _run("tmux", "display", "-p", "-t", target, "#{pane_id}")
        if not self.target.startswith("%"):
            raise RuntimeError(f"tmux pane {target!r} not found")
        where = _run("tmux", "display", "-p", "-t", self.target, "#{session_name}:#{window_index}.#{pane_index}")
        info = self.info()
        self.name = info.get("name") or where
        logger.info(f"Claude session: {target} -> {self.name}, {where} ({self.target}), {info['cwd']}")

    def info(self) -> dict:
        # ~/.claude/sessions/<pid>.json: sessionId, cwd, status (busy|idle|waiting).
        # claude is either the pane process itself or its child (when the pane runs a shell).
        pane_pid = _run("tmux", "display", "-p", "-t", self.target, "#{pane_pid}")
        children = subprocess.run(["pgrep", "-P", pane_pid], capture_output=True, text=True).stdout
        for pid in [pane_pid, *children.split()]:
            f = CLAUDE_DIR / "sessions" / f"{pid}.json"
            if f.exists():
                return json.loads(f.read_text())
        raise RuntimeError(f"no claude process in pane {self.target}")

    def transcript_path(self) -> Path:
        info = self.info()
        slug = re.sub(r"[^A-Za-z0-9]", "-", info["cwd"])
        return CLAUDE_DIR / "projects" / slug / f"{info['sessionId']}.jsonl"

    def busy(self) -> bool:
        return self.info().get("status") == "busy"

    def input_box(self) -> list[str]:
        """Lines of claude's input box: from the last line starting with "❯" to the border below it."""
        lines = _run("tmux", "capture-pane", "-p", "-t", self.target).splitlines()
        start = max((i for i, l in enumerate(lines) if l.lstrip().startswith("❯")), default=None)
        box = []
        for line in lines[start:] if start is not None else []:
            if line.lstrip().startswith("─"):
                break
            box.append(line.strip())
        return box

    def input_line(self) -> str:
        return " ".join(" ".join(self.input_box()).lstrip("❯").split())

    async def interrupt(self, restored: str = "") -> bool:
        """Escape, only while the session is generating: a double Escape would open the rewind menu.

        restored is the request claude puts back into the input box after Escape. It does so with
        a delay; if send() clears the box before that, the old text lands on top of the new one and
        the Enter is lost (a merged phrase once sat in the input box for two minutes).
        """
        if not self.busy():
            return False
        _run("tmux", "send-keys", "-t", self.target, "Escape")
        for _ in range(40):
            await asyncio.sleep(0.05)
            if not self.busy():
                break
        tail = _tail_words(restored)
        for _ in range(30 if tail else 6):
            await asyncio.sleep(0.05)
            if tail and tail in self.input_line():
                break
        return True

    async def send(self, text: str, images: list[str] = ()) -> bool:
        """False if a dialog is open in the session: typing there is unsafe, Enter would confirm it."""
        if self.info().get("status") == "waiting":
            return False
        text = " ".join(text.split())
        # After Escape claude puts the interrupted request back into the input box: clear it.
        # C-u erases one screen line, and a long request wraps over several.
        _run("tmux", "send-keys", "-t", self.target, *["C-u"] * max(1, len(self.input_box())))
        for path in images:
            await self._paste_image(path)
        _run("tmux", "send-keys", "-t", self.target, "-l", text)
        _run("tmux", "send-keys", "-t", self.target, "Enter")
        # An Enter that arrives while claude is still processing the input is lost: the text stays in the box.
        tail = _tail_words(text)
        for _ in range(20 if tail else 0):
            await asyncio.sleep(0.05)
            if tail not in self.input_line():
                return True
        if not tail:
            return True
        logger.warning("The message is still in the input box, pressing Enter again")
        _run("tmux", "send-keys", "-t", self.target, "Enter")
        return True

    async def _paste_image(self, path: str):
        """An image as an attachment: claude replaces a file path pasted as a (bracketed) paste with
        [Image #N] and attaches the file to the message; no file read permission is needed.
        It does this with a delay: text typed earlier would land before the mark and the Enter
        would be lost. So wait until the mark shows up on screen."""
        def marks() -> int:
            return _run("tmux", "capture-pane", "-p", "-t", self.target).count("[Image #")

        before = marks()
        subprocess.run(["tmux", "load-buffer", "-b", "terminalvox-image", "-"], input=path, text=True, check=True)
        _run("tmux", "paste-buffer", "-p", "-d", "-b", "terminalvox-image", "-t", self.target)
        for _ in range(60):
            await asyncio.sleep(0.05)
            if marks() > before:
                return
        logger.warning(f"claude did not show [Image #] for {path}")


def _tail_words(text: str, n: int = 3) -> str:
    """The last words of a text, to find it in the input box where it is wrapped over lines."""
    return " ".join(text.split()[-n:])


class TranscriptTailer:
    """Yields the assistant's text and tool_use blocks appended to the transcript after start.

    Claude Code writes each content block (thinking / text / tool_use) as a separate, complete
    JSONL line, not token by token.
    """

    def __init__(self, session: ClaudeSession, poll: float = 0.05):
        self._session = session
        self._poll = poll
        self._path: Path | None = None
        self._pos = 0
        self.last_assistant_at = 0.0  # time (epoch) of the last assistant record: thinking, text, tool_use

    def _resolve(self):
        # sessionId changes after /clear, so the path is re-checked.
        path = self._session.transcript_path()
        if path != self._path:
            self._path = path
            self._pos = path.stat().st_size if path.exists() else 0
            logger.info(f"Transcript: {path}")

    async def blocks(self):
        last_resolve = 0.0
        buf = b""
        while True:
            if time.monotonic() - last_resolve > 1.0:
                try:
                    self._resolve()
                except Exception as e:
                    logger.warning(f"Transcript not found: {e}")
                last_resolve = time.monotonic()
            if self._path and self._path.exists():
                with open(self._path, "rb") as f:
                    f.seek(self._pos)
                    chunk = f.read()
                self._pos += len(chunk)
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    rec = _assistant_record(line)
                    if rec:
                        ts, blocks = rec
                        self.last_assistant_at = max(self.last_assistant_at, ts)
                        for block in blocks:
                            yield ts, block
            await asyncio.sleep(self._poll)


def _assistant_record(line: bytes) -> tuple[float, list[dict]] | None:
    """(record time, text and tool_use blocks) for an assistant record of the main session, else None."""
    try:
        d = json.loads(line)
        if d.get("type") != "assistant" or d.get("isSidechain"):
            return None
        ts = datetime.fromisoformat(d["timestamp"]).timestamp()
    except (ValueError, KeyError, TypeError):
        return None
    content = (d.get("message") or {}).get("content") or []
    blocks = [
        b for b in content
        if isinstance(b, dict) and (b.get("type") == "tool_use" or b.get("type") == "text" and b.get("text"))
    ]
    return ts, blocks


def speakable(text: str, max_chars: int = 600, p: Phrases = EN) -> str:
    """Strips markdown and whatever shouldn't be read aloud."""
    # Claude sometimes repeats the incoming message tag in its reply: "[voice] Hi…".
    text = re.sub(r"\[voice[\w-]*\]\s*", "", text, flags=re.I)
    text = re.sub(r"```.*?(```|$)", " ", text, flags=re.S)  # code blocks
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links and images
    text = re.sub(r"https?://\S+", p.link, text)
    text = re.sub(r"^\s*\|.*\|\s*$", " ", text, flags=re.M)  # tables
    text = re.sub(r"^\s*(#+|[-*+>]|\d+[.)])\s+", "", text, flags=re.M)  # headings, lists
    text = re.sub(r"(\*\*|__|\*|_|~~)(\S.*?\S|\S)\1", r"\2", text)
    text = re.sub(r"[*#|]", " ", text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # A line without a sentence end is a separate phrase for TTS.
    text = " ".join(l if l[-1] in ".!?:;…" else l + "." for l in lines)
    if len(text) > max_chars:
        cut = max(text.rfind(c, 0, max_chars) for c in ".!?")
        text = text[: cut + 1 if cut > 0 else max_chars] + " " + p.rest_in_terminal
    return text


class TmuxBridge(FrameProcessor):
    """Sits in the pipeline where the LLM would be.

    session=None: echo mode instead of Claude Code, to test the voice loop.
    """

    def __init__(
        self,
        session: ClaudeSession | None,
        timings,
        phrases: Phrases = EN,
        prefix: str = "",
        max_chars: int = 600,
        merge_secs: float = 5.0,
        filler_secs: float = 1.5,
        tool_gap_secs: float = 4.0,
        inject: bool = True,
        hold_secs: float = 2.5,
        hold_max_secs: float = 15.0,
        beep: bool = True,
        image_wait_secs: float = 10.0,
        aliases: dict[str, str] | None = None,
    ):
        super().__init__()
        self._session = session
        self._p = phrases
        self._tailer = TranscriptTailer(session) if session else None
        self._merge_secs = merge_secs
        self._last_sent = ""
        self._last_sent_at = 0.0
        self._sent_queued = False  # the last phrase went out while claude was busy with something else
        self._timings = timings
        self._prefix = prefix
        self._max_chars = max_chars
        self._muted = False  # after an interruption the rest of the old reply is not spoken
        self._bot_active = False  # the bot is speaking or about to
        self._interrupted_bot = False
        self._tail_task = None
        self._filler_secs = filler_secs
        self._filler_task = None
        self._last_filler = ""
        self._spoken_at = 0.0  # when a claude block (text or tool) was last spoken
        self._inject = inject
        self._voice_on = False  # voice_on has been injected into the current session
        self._tool_gap_secs = tool_gap_secs
        self._last_tool_phrase = ""
        self._activity = ""  # what claude is doing now: the phrase of the last tool
        # An unfinished phrase waits hold_secs for its continuation; while the user is speaking
        # again, up to hold_max_secs, so a phrase of pure noise doesn't hang forever.
        self._hold_secs = hold_secs
        self._hold_max_secs = hold_max_secs
        self._pending = ""
        self._pending_interrupted = False
        self._hold_task = None
        self._send_lock = asyncio.Lock()
        self._beep = beep  # a tone when a phrase is sent to the session
        self._beep_audio = b""
        # A photo waits for the next phrase and goes with it; if none comes, it goes alone.
        self._images: list[str] = []
        self._last_images: list[str] = []  # attached to the last phrase (for merging)
        self._image_wait_secs = image_wait_secs
        self._image_task = None
        # False interruption (noise, not a single word): the cut-off reply is repeated.
        self._unsaid: list[str] = []  # claude's text handed to TTS while the bot hasn't gone quiet
        self._cut: list[str] = []  # text cut off, plus text skipped after the cut
        self._cut_at = 0.0
        self._replay_task = None
        # Voice commands: spoken aliases of session names, and the choices offered by "which one?".
        self._aliases = aliases or {}
        self._choices: list[dict] = []
        self._choices_at = 0.0
        self._previous: dict | None = None  # {"pane", "name"} of the session before the last switch

    async def setup(self, setup):
        await super().setup(setup)
        self._sample_rate = setup.audio_out_sample_rate
        self._beep_audio = beep(self._sample_rate)
        if self._session:
            self._tail_task = self.create_task(self._tail())

    async def start_voice(self):
        """A client connected: turn voice mode on in the session, or just greet."""
        if self._session:
            await self._announce_session()
        if self._session and self._inject:
            await self._say(self._p.connecting.format(name=self._session.name))
            await self._send_control(self._p.voice_on)
        else:
            await self._say(self._p.listening)

    async def switch(self, session: ClaudeSession, inject: bool):
        """Move the voice to another session on the fly."""
        old = self._session
        if old and old.target == session.target and inject == self._inject:
            return
        if old and self._voice_on and old.target != session.target:
            await old.send(self._p.voice_off)
        for task in (self._tail_task, self._filler_task, self._hold_task, self._image_task, self._replay_task):
            if task:
                await self.cancel_task(task)
        self._filler_task = self._hold_task = self._image_task = None
        self._pending, self._pending_interrupted = "", False
        self._images, self._last_images = [], []
        self._unsaid, self._cut, self._replay_task = [], [], None
        if old and old.target != session.target:
            self._previous = {"pane": old.target, "name": old.name}
        self._session, self._inject, self._voice_on = session, inject, False
        self._tailer = TranscriptTailer(session)
        self._last_sent, self._last_sent_at, self._spoken_at = "", 0.0, 0.0
        self._muted = self._interrupted_bot = False
        self._tail_task = self.create_task(self._tail())
        await self._announce_session()
        await self._say(self._p.switching.format(name=session.name))
        if inject:
            await self._send_control(self._p.voice_on)

    @property
    def pane(self) -> str | None:
        """The tmux pane of the current session, None in echo mode."""
        return self._session.target if self._session else None

    async def _announce_session(self):
        # Tell the client which session it is talking to: the page updates its picker on a voice switch.
        await self.push_frame(RTVIServerMessageFrame(
            data={"type": "session", "pane": self._session.target, "name": self._session.name}))

    async def _command(self, text: str) -> bool:
        """Voice commands of the bridge ("switch to X", "what sessions are there?"); True if handled."""
        if self._choices:
            choices, self._choices = self._choices, []
            # The answer to "which one?" ("the second", "pipeline"); anything else is a normal phrase.
            if time.time() - self._choices_at < 30 and (chosen := pick(text, choices, self._p)):
                await self._switch_to(chosen)
                return True
        cmd = parse(text, self._p)
        if not cmd:
            return False
        kind, name, strong = cmd
        try:
            sessions = list_sessions()
        except Exception as e:
            logger.warning(f"Session list failed: {e}")
            sessions = []
        if kind == "back":
            # "Go back": to the session before the last switch; again toggles between the two.
            prev = self._previous
            logger.info(f"Command: go back -> {prev}")
            if not prev:
                await self._say(self._p.no_previous)
            elif not (live := [x for x in sessions if x["pane"] == prev["pane"]]):
                await self._say(self._p.previous_closed.format(name=prev["name"]))
            else:
                await self._switch_to(live[0])
            return True
        if kind == "list":
            names = [x["name"] for x in sessions[:8]]
            logger.info(f"Command: list sessions -> {names}")
            await self._say(self._p.sessions.format(names=", ".join(names)) if names else self._p.no_sessions)
            return True
        hits = find(name, sessions, self._aliases)
        logger.info(f"Command: switch to {name!r} -> {[x['name'] for x in hits]}")
        if not hits:
            if not strong:
                return False  # "go back to the docs folder…": a request for claude, not a command
            await self._say(self._p.switch_none.format(name=name))
        elif len(hits) > 1:
            self._choices, self._choices_at = hits[:3], time.time()
            names = [x["name"] for x in self._choices]
            await self._say(self._p.switch_which.format(options=", ".join(names[:-1]) + self._p.or_ + names[-1]))
        else:
            await self._switch_to(hits[0])
        return True

    async def _switch_to(self, info: dict):
        if self._session and self._session.target == info["pane"]:
            await self._say(self._p.switch_already.format(name=self._session.name))
            return
        try:
            session = ClaudeSession(info["pane"])
        except Exception as e:
            logger.warning(f"Cannot switch to {info['name']}: {e}")
            await self._say(self._p.switch_none.format(name=info["name"]))
            return
        await self.switch(session, self._inject)

    async def _send_control(self, text: str):
        # A control message: no filler and no merging with the user's phrases.
        self._last_sent, self._last_sent_at = "", time.time()
        if await self._session.send(text):
            self._voice_on = self._voice_on or text == self._p.voice_on
        else:
            await self._say(self._p.dialog_open)

    async def cleanup(self):
        for task in (self._tail_task, self._filler_task, self._hold_task, self._image_task, self._replay_task):
            if task:
                await self.cancel_task(task)
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # A new user phrase. LLMContextFrame (run the LLM) is swallowed: it also
        # comes without a new phrase, e.g. on a turn timeout.
        if isinstance(frame, LLMContextUserTurnFrame):
            if direction == FrameDirection.DOWNSTREAM:
                await self._on_user_text(frame.text)
            return
        if isinstance(frame, LLMContextFrame):
            return

        if isinstance(frame, InterruptionFrame):
            # The start of any user phrase. Mute only if it cut into the bot's speech.
            if self._bot_active:
                self._interrupted_bot = self._muted = True
                self._bot_active = False
                self._cut, self._unsaid, self._cut_at = self._unsaid, [], time.time()
            # The user is continuing an unfinished phrase: wait until they're done.
            if self._hold_task:
                await self._start_hold(self._hold_max_secs)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_active = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_active = False
            self._unsaid = []
        elif isinstance(frame, UserStoppedSpeakingFrame) and self._cut:
            # The phrase text (LLMContextUserTurnFrame) comes next; if it doesn't, noise
            # interrupted the bot.
            if self._replay_task:
                await self.cancel_task(self._replay_task)
            self._replay_task = self.create_task(self._replay_if_noise())

        await self.push_frame(frame, direction)

    async def _on_user_text(self, text: str):
        text = text.strip()
        if not text:
            return
        self._cut = []  # interrupted with words: the cut is real
        self._timings.mark("turn_end")
        logger.info(f"User: {text}")

        norm = re.sub(r"(\w)\1+", r"\1", text.lower())  # "byeee" -> "bye"
        # A goodbye as the last sentence of the phrase ("Thanks, I'll switch. Bye!"):
        # send the rest as usual and end the call.
        *rest, last = re.split(r"(?<=[.?!…])\s+", text)
        if self._p.bye.match(re.sub(r"(\w)\1+", r"\1", last.lower())):
            if rest:
                self._pending = f"{self._pending} {' '.join(rest)}".strip()
                await self._cancel_hold()
                await self._flush()
            await self._say(self._p.goodbye)
            await self.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
            return

        interrupted, self._interrupted_bot = self._interrupted_bot, False
        if self._p.stop.match(norm):
            await self._cancel_hold()
            self._pending, self._pending_interrupted = "", False
            if self._session:
                await self._session.interrupt()
            self._timings.reset()
            return

        # Smart Turn handles hesitations poorly (especially in Russian) and closes the turn on a
        # pause mid-thought. If the text says the phrase isn't finished, wait and send it all at once.
        self._pending = f"{self._pending} {text}".strip()
        self._pending_interrupted |= interrupted
        await self._cancel_hold()
        if self._hold_secs > 0 and not looks_complete(self._pending):
            logger.info(f"Phrase not finished, waiting for more: {self._pending}")
            await self._start_hold(self._hold_secs)
            return
        # As a separate task: an InterruptionFrame (the user speaks again) cancels processing of
        # the current frame, and sending would break off between Escape and the text.
        self.create_task(self._flush())

    async def add_image(self, path: str, text: str = ""):
        """A photo from the client (POST /api/image). With a caption it goes out right away;
        without one, with the user's next phrase ("what is this part?")."""
        logger.info(f"Photo: {path}" + (f", caption: {text}" if text else ""))
        if self._session is None:
            await self._say(self._p.photo_no_session)
            return
        self._images.append(path)
        if self._image_task:
            await self.cancel_task(self._image_task)
            self._image_task = None
        if text:
            self._pending = f"{self._pending} {text}".strip()
            await self._cancel_hold()
            self.create_task(self._flush())
            return
        await self._say(self._p.photo_received)
        self._image_task = self.create_task(self._image_alone())

    async def _image_alone(self):
        await asyncio.sleep(self._image_wait_secs)
        self._image_task = None
        # The user is finishing a phrase: the photo will go with it.
        if self._images and not self._pending and not self._hold_task:
            logger.info("Nothing said about the photo, sending it alone")
            self._pending = self._p.image_only
            await self._flush()

    async def _replay_if_noise(self, wait: float = 1.0):
        await asyncio.sleep(wait)
        self._replay_task = None
        if not self._cut or self._last_sent_at > self._cut_at:
            return
        text, self._cut = " ".join(self._cut), []
        logger.info(f"Interrupted without words (noise), repeating: {text[:80]}")
        self._muted = self._interrupted_bot = False
        self._unsaid.append(text)
        await self._say(text)

    async def _start_hold(self, secs: float):
        await self._cancel_hold()
        self._hold_task = self.create_task(self._hold(secs))

    async def _cancel_hold(self):
        if self._hold_task:
            task, self._hold_task = self._hold_task, None
            await self.cancel_task(task)

    async def _hold(self, secs: float):
        await asyncio.sleep(secs)
        self._hold_task = None  # not cancellable from here on: sending must not break off halfway
        logger.info("No continuation, sending")
        await self._flush()

    async def _flush(self):
        async with self._send_lock:
            await self._send_pending()

    async def _send_pending(self):
        text, interrupted = self._pending, self._pending_interrupted
        self._pending, self._pending_interrupted = "", False
        if not text:
            return
        if await self._command(text):
            self._timings.reset()
            return
        if self._session and is_checkin(text, self._p) and self._session.busy():
            logger.info(f"Session busy, answering with status: {text}")
            await self._say(status_phrase(self._p, self._last_sent_at, self._activity))
            return
        if self._session is None:
            self._muted = False
            await self._say(self._echo(text))
            return
        images, self._images = self._images, []
        if self._image_task:
            await self.cancel_task(self._image_task)
            self._image_task = None
        # Smart Turn closed the turn on a pause mid-phrase and claude hasn't answered the first
        # part yet: interrupt and send the whole phrase.
        # But if claude was busy with its own work at that moment, the phrase sits in its queue:
        # Escape would pull it back into the input box and everything would hang unsent.
        if (
            self._last_sent
            and not self._sent_queued
            and time.time() - self._last_sent_at < self._merge_secs
            and self._tailer.last_assistant_at < self._last_sent_at
        ):
            await self._session.interrupt(restored=self._last_sent)
            text = f"{self._last_sent} {text}"
            images = self._last_images + images
            logger.info(f"Merged with the previous phrase: {text}")
        # The user cut into the bot's speech: stop generation too. Otherwise (the bot is silent
        # but the session is still working) claude queues the message itself.
        elif interrupted and await self._session.interrupt():
            logger.info("Session interrupted (Escape)")
        self._sent_queued = self._session.busy()
        self._last_sent, self._last_sent_at, self._last_images = text, time.time(), images
        if images:
            logger.info(f"With photos: {len(images)}")
        if not await self._session.send(self._prefix + text, images):
            self._images = images + self._images
            await self._say(self._p.dialog_open)
            return
        self._timings.mark("tmux_sent")
        if self._beep:
            await self.push_frame(OutputAudioRawFrame(self._beep_audio, self._sample_rate, 1))
        self._muted = False
        if self._filler_secs > 0:
            if self._filler_task:
                await self.cancel_task(self._filler_task)
            self._filler_task = self.create_task(self._filler(self._last_sent_at))

    async def _filler(self, sent_at: float):
        await asyncio.sleep(self._filler_secs)
        # Stay quiet if claude has already answered, a new phrase came in, or we were interrupted.
        if self._spoken_at >= sent_at or self._last_sent_at != sent_at or self._muted:
            return
        phrase = random.choice([f for f in self._p.fillers if f != self._last_filler])
        self._last_filler = phrase
        logger.info(f"Filler: {phrase}")
        await self._say(phrase)

    async def _tail(self):
        async for ts, block in self._tailer.blocks():
            if block["type"] == "tool_use":
                self._activity = tool_phrase(block, self._p) or self._activity
            elif block["type"] == "text":
                self._activity = ""  # claude is talking: the previous tool is no longer "now"
            if self._muted and self._cut and ts >= self._last_sent_at and block["type"] == "text":
                self._cut.append(speakable(block["text"], self._max_chars, self._p))  # replayed if noise cut in
            if ts < self._last_sent_at or self._muted:
                logger.debug(f"Skipped block (stale or after an interruption): {str(block)[:80]}")
                continue
            if block["type"] == "tool_use":
                await self._announce_tool(block)
                continue
            self._timings.mark("first_text")
            self._spoken_at = time.time()
            self._last_tool_phrase = ""
            text = speakable(block["text"], self._max_chars, self._p)
            self._unsaid.append(text)
            await self._say(text)

    async def _announce_tool(self, block: dict):
        # At most once per tool_gap_secs and no repeats in a row ("Reading a file. Reading a file."):
        # a series of calls is announced by its first phrase, and claude's text before a tool says the rest.
        phrase = tool_phrase(block, self._p)
        if (
            not phrase
            or self._tool_gap_secs <= 0
            or time.time() - self._spoken_at < self._tool_gap_secs
            or phrase == self._last_tool_phrase
        ):
            return
        logger.info(f"Tool {block.get('name')}: {phrase}")
        self._spoken_at = time.time()
        self._last_tool_phrase = phrase
        await self._say(phrase)

    async def _say(self, text: str):
        if not text:
            return
        self._bot_active = True
        await self.push_frame(LLMFullResponseStartFrame())
        await self.push_frame(LLMTextFrame(text))
        await self.push_frame(LLMFullResponseEndFrame())

    def _echo(self, text: str) -> str:
        reply = self._p.echo.format(text=text)
        if self._p.echo_long_trigger in text.lower():
            reply += self._p.echo_long
        return reply


def beep(sample_rate: int, hz: float = 880.0, secs: float = 0.08) -> bytes:
    """A short tone with soft edges (no click), 16-bit mono."""
    t = np.arange(int(sample_rate * secs)) / sample_rate
    fade = np.minimum(1.0, np.minimum(t, secs - t) / 0.01)
    return (np.sin(2 * np.pi * hz * t) * fade * 0.25 * 32767).astype(np.int16).tobytes()
