# TerminalVox

Talk to your [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) sessions by voice.
You speak in a browser (on your computer or phone), your speech goes into the chosen Claude Code session
in tmux as a regular message, and the reply is read aloud. You can interrupt at any moment and switch
sessions in the middle of a call.

```
browser ─WebRTC─▶ Silero VAD + Smart Turn ─▶ ElevenLabs Scribe (STT) ─▶ tmux send-keys ─▶ claude
browser ◀─WebRTC─ ElevenLabs Flash (TTS) ◀─ markdown cleanup ◀─ tail of the JSONL transcript ◀─┘
```

There is no extra LLM in the loop: Claude Code itself answers, with its tools, files and context.
The bridge only turns speech into keystrokes and the session transcript into speech.

Built on [Pipecat](https://github.com/pipecat-ai/pipecat). English and Russian are supported.

**Use headphones**: without them the bot hears itself and interrupts its own speech.

> Not affiliated with Anthropic or ElevenLabs.

## Requirements

- Linux or macOS with [tmux](https://github.com/tmux/tmux), [uv](https://docs.astral.sh/uv/) and
  [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) (`claude` on the `PATH`).
  The systemd service is Linux only; tested on Ubuntu.
- An [ElevenLabs](https://elevenlabs.io) API key (speech recognition and synthesis).
- Optional: [Tailscale](https://tailscale.com), to talk from your phone or another computer.

## Quick start

```bash
git clone https://github.com/sesamkowalski/terminalvox && cd terminalvox
cp .env.example .env      # set ELEVENLABS_API_KEY; VOICE_LANG=ru for Russian
cat voice-rules.md >> ~/.claude/CLAUDE.md   # voice mode rules for Claude (see below)
./run.sh
```

`run.sh`:
1. starts a dedicated claude session in the tmux session `voice` unless it exists (in `CLAUDE_CWD`,
   your home directory by default);
2. publishes the server in your tailnet with `tailscale serve` and prints the address, if Tailscale
   is connected;
3. starts the voice server on `127.0.0.1:7860`.

Open `http://localhost:7860/` on the same machine, or the printed `https://<machine>.<tailnet>.ts.net/`
from another device in your tailnet. Pick a session, allow the microphone and press Connect.

Restarting the server (Ctrl+C, `./run.sh`) doesn't touch the claude sessions. To look at the dedicated
session: `tmux attach -t voice`. Any other claude session running in tmux shows up in the list too.

### In the background, as a systemd user service (Linux)

```bash
make install    # install the unit into ~/.config/systemd/user and enable it
make start      # make stop | make restart | make status
make logs       # tail logs/bot.log
make journal    # systemd journal: run.sh output, startup errors
make uninstall  # stop and remove the service
```

- The service runs the same `run.sh`; `make run` does the same in the terminal.
- The log goes to `logs/bot.log` (DEBUG, rotated at 20 MB, 5 files kept). Another directory:
  `make install LOG_DIR=/path`.
- The unit is generated from `terminalvox.service.in`; after editing the template `make restart`
  reinstalls it.
- `stop` and `restart` stop only the server; tmux and the claude sessions keep running.
- To start the service at boot without logging in: `loginctl enable-linger $USER`.
- Stop a `run.sh` running in a terminal before `make start`: both use port 7860.

### Tailscale (once)

```bash
sudo tailscale up --operator=$USER   # log in; operator lets `tailscale serve` run without sudo
```

- MagicDNS and HTTPS certificates must be enabled in the tailnet admin console. If Serve isn't enabled
  yet, `run.sh` prints a link to enable it and waits up to two minutes.
- On a phone, install the Tailscale app and log in with the same account.
- HTTPS is required: browsers don't allow the microphone on plain HTTP except on localhost.

## Security

Your voice drives a Claude Code session with whatever permissions it has, so **the server must never be
reachable from the internet**. It listens on `127.0.0.1` only and is exposed only inside your tailnet by
`tailscale serve`. Don't use `tailscale funnel` (public access); Funnel requests are rejected anyway.

Who gets in:
- from the tailnet, only the logins in `TAILNET_USERS`. By default that's the owner of the machine:
  the server asks tailscaled on the first request. The login comes from the `Tailscale-User-Login`
  header set by `tailscale serve`, which a client cannot forge. Other users, tagged devices and Funnel
  get 403;
- from the machine itself, only requests to `localhost`;
- requests from pages of other sites (a foreign `Origin`) are rejected, even on localhost.

Denied requests are logged with `Denied`. Anyone with a shell on the machine can reach the local port,
so on a shared machine keep that in mind.

Photos sent during a call are stored in `~/.cache/terminalvox/images` for 7 days.

## The page

- **Session**: live Claude Code sessions in tmux (from `~/.claude/sessions/*.json`), most recently active
  first, with search by name, window and folder. Sessions outside tmux are not listed. Badges show that a
  session is working or waiting for an answer in the terminal. You can change the choice during a call:
  the bot says "Switching to …" and continues with the new session.
- **Inject voice mode messages** (on by default):
  - on connect the bot says "Connecting to …" and sends "[voice] Voice mode is on…" into the session,
    and claude answers with a short greeting;
  - when you switch away, the old session gets "[voice-off] Voice mode is off".
- **Speech pace**: how long the bot waits for the rest of a phrase after you pause.
- **Send photo**: a photo goes into the conversation together with your next phrase.
- The page remembers the last session and settings.
- Pipecat's built-in client is still available at `/client/`; it connects to the default session
  (`TMUX_TARGET`) without a choice.

### Voice mode

Claude needs to know that a message was spoken and that its reply will be heard. The rules live in
`~/.claude/CLAUDE.md`; copy them from `voice-rules.md` (English) or `voice-rules.ru.md` (Russian).
Messages tagged `[voice]` were dictated: claude answers them briefly, conversationally and without
markdown, puts details into files, and says what it is about to do before using tools. Typed messages
get the usual answers.

Sessions that are already running don't reread `CLAUDE.md`, so the injected message repeats the gist.

## How to talk

- A phrase is a message to the session.
- You may hesitate and pause: if a phrase is cut off ("so, I wanted to…", "hmm…"), the bot waits 2.5 s
  for the rest and sends it all as one message.
- If claude thinks longer than 1.5 s, the bot says "one sec", "let me check" and so on. While claude
  works, the bot announces its tools: "searching the web", "reading a file", "checking git".
- If you ask "are you there?" or "how's it going?" during a long task, the bridge answers itself with
  how long claude has been working and what it is doing, without disturbing the session.
- "Stop", "wait", "enough": stop the reply and the generation.
- Speaking over a reply interrupts it: the sound stops within about a second and claude gets Escape and
  your new phrase. A short "mm-hm" doesn't interrupt. Escape also goes into busy sessions, so
  interrupting during a long task stops it. If noise interrupted the bot, the reply is repeated.
- "Bye", "goodbye", "talk to you later": end the call. It also ends after 10 minutes of silence.
- If a dialog is open in the session (a permission prompt, a question), the bot doesn't type into it and
  asks you to answer in the terminal: Enter would confirm the dialog.
- Long replies are spoken up to 600 characters, then "the rest is in the terminal".

### Switching sessions by voice

- "Switch to aramco", "go back to day": the bridge finds the session by name and moves the call there,
  as if you had picked it on the page (the page follows). The command counts only at the start of a
  phrase, and the phrase itself doesn't go to claude.
- Names are matched fuzzily: recognition spells them the way it hears them, possibly in another script,
  so "Арамко" finds `aramco-pbit-cell` and "шедулинг" finds `scheduling-agent`. The session name, its
  tmux window and its folder all count, as do single words of them.
- If several sessions fit, the bot names up to three and asks which one: answer with a name or
  "the second". If none fits, it says so. For names that sound nothing like the session, set
  `SESSION_ALIASES` ("calendar=scheduling-agent").
- "Go back to …" and "go to …" act only when a session matches, so "go back to the previous version of
  the file" still goes to claude; "switch to …" always acts.
- "What sessions are there?", "list sessions": the bot reads up to eight, most recent first.

In Russian the commands are «стоп», «хватит», «подожди», «пока», «до свидания», «отбой», «ты здесь?»,
«переключись на …», «перейди в …», «вернись к …», «какие есть сессии». Other languages can be added in
`phrases.py`.

## Settings

Everything is set in `.env`; see the comments in `.env.example`. The main ones:
- `VOICE_LANG`: `en` (default) or `ru`, the language of recognition, synthesis and the bot's own phrases.
- `ELEVENLABS_VOICE_ID`: the voice; the default depends on the language. List your voices with
  `GET https://api.elevenlabs.io/v2/voices`; add new ones from the ElevenLabs voice library.
- `CLAUDE_TMUX_SESSION`, `CLAUDE_CWD`: name and working directory of the dedicated session.
  `TMUX_TARGET`: the default session when nothing is chosen in the UI (`=session:window.pane` or a pane
  `%id`; `=` means an exact name, otherwise tmux may match another window by prefix).
- `HOLD_SECS` (2.5): how long to wait for the rest of a cut-off phrase; `0` turns it off.
- `FILLER_SECS` (1.5), `TOOL_GAP_SECS` (4): fillers and tool announcements; `0` turns them off.
- `BARGE_IN_SECS`, `VAD_STOP_SECS`, `TURN_MAX_PAUSE_SECS`: the balance between speed and how often the
  bot cuts you off.
- `SESSION_ALIASES`: extra spoken names for sessions in voice commands, `alias=session-name` separated by
  commas.
- `TAILNET_USERS`: who in the tailnet may connect (comma-separated logins).
- `MAX_SPOKEN_CHARS`, `IDLE_TIMEOUT_SECS`, `PORT`, `LOG_DIR`, `TAILSCALE_SERVE` (`0` leaves the
  `tailscale serve` config alone).

## Files

- `bot.py`: the Pipecat pipeline, interruption strategy, timings, access control and HTTP routes.
- `bridge.py`: the tmux bridge: session list, sending, switching, transcript tailing, fillers, tool
  announcements, markdown cleanup.
- `phrases.py`: everything the bot says and the spoken commands it recognizes, per language.
- `commands.py`: parsing of voice commands and fuzzy matching of spoken session names.
- `ui.html`: the page with the session picker and the WebRTC client.
- `voice-rules.md`, `voice-rules.ru.md`: voice mode rules for `~/.claude/CLAUDE.md`.
- `run.sh`: one-command start; `start-session.sh`: the dedicated claude session.
- `Makefile`, `terminalvox.service.in`: running in the background as a systemd user service.
- `tests/selftest.py`: a test without a human: a WebRTC client "speaks" synthesized phrases.
- `tests/test_commands.py`: unit tests of voice commands: `uv run python -m unittest discover tests`.
- `NOTES.md`: the transcript format, choice of services, measurements, known issues and why things are
  done the way they are.

## HTTP API

Any client can use it, e.g. a native phone app.

- `GET /`: the page.
- `GET /api/sessions`: the session list, whether a call is active, and the pane of the call's session
  (`current`). During a call the bot also sends an RTVI server message
  `{"type": "session", "pane", "name"}` over the data channel whenever the session changes.
- `POST /api/offer`: Pipecat WebRTC signaling; `request_data` carries `{pane, inject, pace}`
  (`pace`: `fast`, `normal` or `slow`).
- `POST /api/switch` with JSON `{"pane": "%56", "inject": true}`: move the ongoing call to another
  session.
- `POST /api/image`, multipart: `file` (JPEG, PNG, GIF or WebP, up to 15 MB) and an optional `text`: a
  photo into the ongoing call. With `text` it goes out right away; without it the bot says "Got the
  photo" and attaches it to your next phrase, and if you say nothing within `IMAGE_WAIT_SECS` it sends
  the photo alone. `409` means there is no call.

## Testing without a human

```bash
uv run bot.py -t webrtc                    # echo mode (no TMUX_TARGET)
uv run tests/selftest.py latency "Hi, how are you?"
uv run tests/selftest.py bargein
uv run tests/selftest.py backchannel
```

Needs `OPENAI_API_KEY`: test phrases are synthesized with OpenAI TTS and cached in `tests/audio`.
The server log's `TIMING` lines break the latency down: end of speech → end of phrase → tmux → first
text in the transcript → first sound.

## License

[Apache License 2.0](LICENSE).
