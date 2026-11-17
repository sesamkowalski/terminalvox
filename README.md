# TerminalVox

Talk to your [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) sessions by voice.
You speak in a browser (on your computer or phone), your speech goes into the chosen Claude Code session
in tmux or [herdr](https://herdr.dev) as a regular message, and the reply is read aloud. You can interrupt at any moment and switch
sessions in the middle of a call.

```
browser ─WebRTC─▶ Silero VAD + Smart Turn ─▶ speech-to-text ─▶ keystrokes (tmux / herdr) ─▶ claude
browser ◀─WebRTC─ text-to-speech ◀─ markdown cleanup ◀─ tail of the JSONL transcript ◀────────┘
```

Speech-to-text: ElevenLabs Scribe (default) or Google. Text-to-speech: ElevenLabs Flash (default),
Google Chirp 3 HD, or local Silero (Russian only). See [Settings](#settings).

There is no extra LLM in the loop: Claude Code itself answers, with its tools, files and context.
The bridge only turns speech into keystrokes and the session transcript into speech.

Built on [Pipecat](https://github.com/pipecat-ai/pipecat). English and Russian are supported.

**Use headphones**: without them the bot hears itself and interrupts its own speech.

> Not affiliated with Anthropic or ElevenLabs.

## Requirements

- Linux or macOS with [uv](https://docs.astral.sh/uv/) (it installs Python and the packages itself),
  `python3`, and [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) (`claude` on the
  `PATH`, logged in: run `claude` once by hand if you never have). The systemd service is Linux only;
  tested on Ubuntu.
- [tmux](https://github.com/tmux/tmux) or [herdr](https://herdr.dev), or both: claude sessions run there.
- An [ElevenLabs](https://elevenlabs.io) API key: speech recognition and, by default, synthesis. With
  local Silero synthesis it is still needed for recognition, unless you switch that to Google too.
- Optional: [Tailscale](https://tailscale.com), to talk from your phone or another computer.

### What is supported

| Part | Options | Where it runs | Needs |
|---|---|---|---|
| OS | Linux (tested on Ubuntu 24.04), macOS | | systemd service: Linux only |
| Multiplexer | tmux, [herdr](https://herdr.dev), both at once | local | one of them |
| Speech-to-text | ElevenLabs Scribe v2 Realtime (default), Google Speech-to-Text v2 | cloud | ElevenLabs key / Google service account |
| Text-to-speech | ElevenLabs Flash v2.5 (default), Google Chirp 3 HD, [Silero](https://github.com/snakers4/silero-models) | cloud, cloud, local CPU | ElevenLabs key / Google service account / nothing (Russian only, non-commercial license) |
| Turn detection | Silero VAD + Smart Turn v3 | local CPU | nothing |
| Languages | English, Russian (`VOICE_LANG`) | | others: add them to `phrases.py` |
| Clients | the built-in web page (any browser with a microphone), any WebRTC client of the HTTP API | | HTTPS outside localhost (Tailscale) |

## Quick start

```bash
git clone https://github.com/sesamkowalski/terminalvox && cd terminalvox
cp .env.example .env      # set ELEVENLABS_API_KEY; VOICE_LANG=ru for Russian
cat voice-rules.md >> ~/.claude/CLAUDE.md   # voice mode rules for Claude (see below)
./run.sh
```

`run.sh`:
1. starts a dedicated claude session named `voice` unless it exists (in `CLAUDE_CWD`, your home directory
   by default): a tmux session if tmux is installed, otherwise a herdr workspace (`CLAUDE_MUX` chooses:
   `tmux`, `herdr` or `none`). Without a running herdr server it starts a headless one, which, like
   the herdr app, restores your saved workspaces;
2. publishes the server in your tailnet with `tailscale serve` and prints the address, if Tailscale
   is connected;
3. installs the dependencies (`uv sync`; the first run downloads a few hundred MB) and starts the voice
   server on `127.0.0.1:7860`.

**First run in a new folder**: claude first asks whether you trust the folder, and the session can't
be used (and isn't listed) until you answer. `run.sh` says so; answer it once: `tmux attach -t voice`
(detach with Ctrl+B D) or, with herdr, open `herdr` and go to the `voice` workspace.

Check that it works: `curl -s localhost:7860/api/sessions` lists your claude sessions, the dedicated
one among them.

Open `http://localhost:7860/` on the same machine, or the printed `https://<machine>.<tailnet>.ts.net/`
from another device in your tailnet. Pick a session, allow the microphone and press Connect.

Restarting the server (Ctrl+C, `./run.sh`) doesn't touch the claude sessions. To look at the dedicated
session: `tmux attach -t voice`, or `herdr`. Any other claude session running in tmux shows up in the list
too, and so do claude sessions in [herdr](https://herdr.dev) panes (the server `herdr` itself would
talk to: the default session, or the one in `HERDR_SOCKET_PATH`): one list, and you can switch between
the two.

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

### On a server (VPS)

TerminalVox is made to run on the machine where your claude sessions live, and that is usually a server
you reach over SSH. You talk to it from your phone or laptop through Tailscale. This is the setup it is
used and tested in: an Ubuntu 24.04 server, sessions in tmux and herdr, the systemd service, a phone
with the Tailscale app.

1. On the server: install tmux or herdr, uv and Claude Code, then run `claude` once over SSH and log in.
   With no browser on the server, it prints a link: open it on any device and paste the code back.
2. Tailscale on the server: `sudo tailscale up --operator=$USER` (see [Tailscale](#tailscale-once)).
3. Clone, fill `.env` (keys; `CLAUDE_CWD` for the dedicated session; `VOICE_LANG`), then
   `make install && make start`, and `loginctl enable-linger $USER` so the service and the sessions
   run without anyone logged in and come back after a reboot.
4. Answer claude's trust question for `CLAUDE_CWD` once (`tmux attach -t voice` or `herdr` over SSH),
   and check `make status` and `curl -s localhost:7860/api/sessions`.
5. On the phone: the Tailscale app with the same account, then the `https://<server>.<tailnet>.ts.net/`
   address printed by `run.sh` (`make journal` shows it). Headphones help.

Notes:
- No ports need to be open to the internet: the page and its API go through `tailscale serve`, and the
  WebRTC audio flows directly between the phone and the server's tailnet address (UDP inside the
  tailnet, no STUN or TURN). A firewall that blocks all inbound traffic except Tailscale is fine.
- Resources measured here: the bot takes about 1.6 GB of memory and a fraction of one core between
  phrases; Silero, if used, about 1.2 GB more and up to `SILERO_THREADS` cores for a fraction of a second
  per sentence. A 1 GB VPS won't fit them; 2–4 vCPU and 4 GB should be comfortable (an estimate: it runs
  here on a 2011 24-thread Xeon with plenty of memory).
- Latency depends on the round trip between your phone and the server and between the server and the
  speech services: choose a region close to you. From the end of your phrase to the first sound is
  about one second here.
- Your sessions keep running when the call ends or the service restarts (`make restart` doesn't touch
  tmux, herdr or claude); look at them over SSH as usual.

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

Photos and files sent during a call are stored in `~/.cache/terminalvox/images` and `.../files` for 7 days.

## The page

- **Session**: live Claude Code sessions in tmux and herdr (from `~/.claude/sessions/*.json`), most recently
  active first, with search by name, window (herdr: tab or workspace) and folder. Sessions outside a
  multiplexer are not listed. Badges show that a
  session is working or waiting for an answer in the terminal. You can change the choice during a call:
  the bot says "Switching to …" and continues with the new session.
- **Inject voice mode messages** (on by default):
  - on connect the bot says "Connecting to …" and sends "[voice] Voice mode is on…" into the session,
    and claude answers with a short greeting;
  - when you switch away, the old session gets "[voice-off] Voice mode is off".
- **Speech pace**: how long the bot waits for the rest of a phrase after you pause.
- **Send photo or file**: it goes into the conversation together with your next phrase.
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

- A phrase is a message to the session. A short tone confirms it has been sent (`BEEP=0` turns it off).
- You may hesitate and pause: if a phrase is cut off ("so, I wanted to…", "hmm…"), the bot waits 2.5 s
  for the rest and sends it all as one message.
- A phrase that ends on a filler or a conjunction ("…and, um", "…because") counts as unfinished too. A
  follow-up within a few seconds, before claude has answered, is merged into the same message (claude's
  start on the first part is interrupted), and the tone sounds once per merged thought.
- If claude thinks longer than 1.5 s, the bot says a short filler that fits the phrase: "let me think"
  after a question, "let me check" after "look", "find", "check", "on it" after "do", "add", "fix", and
  nothing after a mere "yes", "OK", "thanks". While claude works, the bot announces its tools: "searching
  the web", "reading a file", "running tests", by the program a command actually runs.
- If you ask "are you there?" or "how's it going?" during a long task, the bridge answers itself with
  how long claude has been working and what it is doing, without disturbing the session. A bare
  "there?" works too: the first word is often lost after a long silence.
- "Stop", "wait", "enough": stop the reply and the generation.
- Speaking over a reply interrupts it: the sound stops within about a second and claude gets Escape and
  your new phrase. A short "mm-hm" doesn't interrupt. Escape also goes into busy sessions, so
  interrupting during a long task stops it. If noise interrupted the bot, the reply is repeated.
- "Bye", "goodbye", "talk to you later": end the call. It also ends after 10 minutes of silence.
- If a dialog is open in the session (a permission prompt, a question), the bot doesn't type into it and
  asks you to answer in the terminal: Enter would confirm the dialog.
- Long replies are spoken up to 600 characters, then "the rest is in the terminal".

### Photos and files

During a call you can show claude a picture or hand it a file: the **Send photo or file** button on the
page (or `POST /api/file` from your own client). Pictures (JPEG, PNG, GIF, WebP, up to 15 MB) and any
other files (PDF, Markdown, PPTX…, up to 50 MB).
- The bot says "Got the photo" and attaches it to your next phrase: "what's wrong with this error?". If
  you say nothing within 10 s (`IMAGE_WAIT_SECS`), the photo goes alone.
- claude gets it as a real attachment (`[Image #1]` in the message), the same as an image pasted in the
  terminal: no file-read permission is asked. This works in tmux and herdr sessions alike.
- Other files are saved under their own name, and claude gets the path in the message ("File from the
  phone: …/report.pdf"). It opens what it can with its tools (PDF and text natively, PPTX or DOCX with
  a script) and says aloud when it can't read a format. Reading a file outside the session's folder may
  ask for permission in the terminal; the bot then tells you a dialog is open.
- Photos are kept in `~/.cache/terminalvox/images`, files in `~/.cache/terminalvox/files`, for 7 days, so
  claude can refer to them later.

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
- "Go back", "switch back", "back" (the whole phrase): returns to the session you came from; saying it
  again toggles between the two. If there is none or it has closed, the bot says so.
- "What sessions are there?", "list sessions": the bot reads up to eight, most recent first.

In Russian the commands are «стоп», «хватит», «подожди», «пока», «до свидания», «отбой», «ты здесь?» (or just «здесь?»),
«переключись на …», «перейди в …», «вернись к …», «какие есть сессии». Other languages can be added in
`phrases.py`.

## Settings

Everything is set in `.env`; see the comments in `.env.example`. The main ones:
- `VOICE_LANG`: `en` (default) or `ru`, the language of recognition, synthesis and the bot's own phrases.
- `ELEVENLABS_VOICE_ID`: the voice; the default depends on the language. List your voices with
  `GET https://api.elevenlabs.io/v2/voices`; add new ones from the ElevenLabs voice library.
- `TTS_PROVIDER=google`: synthesis by Google Cloud Chirp 3 HD instead of ElevenLabs, several times cheaper.
  Create a project, enable the Text-to-Speech API, create a service account with a JSON key and put it at
  `GOOGLE_CREDENTIALS` (`google-tts.json`). `GOOGLE_TTS_VOICE`, for example `ru-RU-Chirp3-HD-Charon`;
  `stress.txt`: words Google stresses wrongly, one per line with the stressed vowel in capitals
  (`перезапущУ`); a stress mark is added before synthesis.
- `TTS_PROVIDER=silero`: local Russian synthesis by [Silero](https://github.com/snakers4/silero-models),
  free and offline, on the CPU (about 0.1 s per sentence on a 2011 Xeon). It runs as a separate service
  so the model (about 800 MB of memory) loads once: `make silero-install`, then `make silero-status`
  (Linux, systemd); on macOS run `make silero-run` in a terminal of its own.
  Its environment is a separate uv project, `silero/pyproject.toml` with the exact versions in
  `silero/uv.lock`: torch (CPU-only build, from the PyTorch index), numpy, omegaconf, Python 3.12–3.13.
  `silero/run.sh` installs it with `uv sync` into `silero/.venv` (about 900 MB), and the first start
  downloads the model from GitHub via `torch.hub` (about 140 MB, into `~/.cache/torch/hub`, or
  `$TORCH_HOME`); later starts need no network. Checked from an empty cache: ready in under half a
  minute (with the packages already in uv's cache). Without uv: `pip install torch --index-url
  https://download.pytorch.org/whl/cpu numpy omegaconf`, then `python silero/server.py`.
  `SILERO_VOICE` (`xenia`, also `aidar`, `baya`,
  `kseniya`, `eugene`); the service's defaults `SILERO_RATE` (`x-slow` ... `x-fast`), `SILERO_PITCH`
  (`x-low` ... `x-high`), `SILERO_THREADS`. Silero reads Cyrillic only, so numbers and Latin words are
  spelled out first: add how to say words to `latin.txt` (`commit = коммит`). The models are licensed
  CC BY-NC-SA 4.0: non-commercial use.
- `STT_PROVIDER=google`: recognition by Google Speech-to-Text v2 with the same key (enable the
  Speech-to-Text API and give the service account the Cloud Speech Client role). `GOOGLE_STT_MODEL`
  (`latest_long`), `GOOGLE_STT_LOCATION` (`global`).
- `CLAUDE_MUX` (`auto`: tmux if installed, else herdr; `tmux`, `herdr`, `none`), `CLAUDE_SESSION` (old
  name `CLAUDE_TMUX_SESSION`), `CLAUDE_CWD`: where the dedicated session runs, its name and working
  directory.
  `MUX_TARGET` (old name `TMUX_TARGET`): the default session when nothing is chosen in the UI
  (`=session:window.pane` or a pane `%id`; `=` means an exact name, otherwise tmux may match another
  window by prefix; or a herdr pane id like `w2:p1`).
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
- `mux.py`: tmux and herdr behind one interface: list panes, read the screen, type, press keys, paste.
- `bridge.py`: the bridge to the session: session list, sending, switching, transcript tailing, fillers, tool
  announcements, markdown cleanup.
- `phrases.py`: everything the bot says and the spoken commands it recognizes, per language.
- `commands.py`: parsing of voice commands and fuzzy matching of spoken session names.
- `ui.html`: the page with the session picker and the WebRTC client.
- `voice-rules.md`, `voice-rules.ru.md`: voice mode rules for `~/.claude/CLAUDE.md`.
- `run.sh`: one-command start; `start-session.sh`: the dedicated claude session (tmux or herdr).
- `Makefile`, `terminalvox.service.in`: running in the background as a systemd user service.
- `tests/selftest.py`: a test without a human: a WebRTC client "speaks" synthesized phrases.
- `tests/test_*.py`: unit tests (voice commands, multiplexers, text normalization):
  `uv run python -m unittest discover tests`.
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
- `POST /api/file`, multipart: `file` (any file up to 50 MB) and an optional `text`: like `/api/image`
  below, but anything goes. A picture is handled as a photo; another file is saved under its name and
  its path goes into the message for claude.
- `POST /api/image`, multipart: `file` (JPEG, PNG, GIF or WebP, up to 15 MB) and an optional `text`: a
  photo into the ongoing call. With `text` it goes out right away; without it the bot says "Got the
  photo" and attaches it to your next phrase, and if you say nothing within `IMAGE_WAIT_SECS` it sends
  the photo alone. `409` means there is no call.

## Testing without a human

```bash
uv run bot.py -t webrtc                    # echo mode (no MUX_TARGET)
uv run tests/selftest.py latency "Hi, how are you?"
uv run tests/selftest.py bargein
uv run tests/selftest.py backchannel
```

Needs `OPENAI_API_KEY`: test phrases are synthesized with OpenAI TTS and cached in `tests/audio`.
The server log's `TIMING` lines break the latency down: end of speech → end of phrase → tmux → first
text in the transcript → first sound.

## License

[Apache License 2.0](LICENSE).
