# Engineering notes

Findings, measurements and design decisions behind the code; newest sections are at the end. Started 2026-10-28.

## Versions

- Pipecat 1.14.0 (Python 3.14 in `.venv`); examples taken from the `v1.14.0` tag (`.ref/pipecat/examples`).
- Claude Code 2.1.312.

## Claude Code transcript format

File: `~/.claude/projects/<cwd with every non-alphanumeric character replaced by "-">/<sessionId>.jsonl`.

Finding the session for a tmux pane: `~/.claude/sessions/<pid>.json`, where `pid` is the claude process
(the `pane_pid` itself or one of its descendants). It contains `sessionId`, `cwd` and `status` (`busy` or `idle`).
`sessionId` changes after `/clear`, so the bridge re-checks the path once a second.

JSONL lines (verified on live files):

- `type: "user"`: a user message (`message.content` is a string) or a tool result
  (`content` is a list with `type: "tool_result"`).
- `type: "assistant"`: **one content block per line**: `thinking`, `text` or `tool_use`.
  Several lines of one response share `message.id`. `message.stop_reason`: `tool_use` or `end_turn`.
  Every line has a `timestamp` (ISO, UTC), which is when the block was written.
- `type: "system", subtype: "turn_duration"` is written at the end of a turn.
- Many housekeeping types (`attachment`, `api-request*`, `mode`, `permission-mode`, `queue-operation`…);
  the bridge ignores them.
- `isSidechain: true` marks subagent records; they are not spoken.

Only `type == "assistant"` → `type == "text"` blocks are spoken.

**A text block is written whole once it has been generated, not token by token.** Measurement: an 8-sentence
paragraph appeared in the TUI (`tmux capture-pane`) after 1.6 s, but in the JSONL only after 5.1 s. So
"speak from the first sentence" works at block granularity: an announcement before tool calls is spoken
immediately, as are the intermediate texts between tool calls, but a long final block is spoken only after
it has been fully generated. A possible future optimization is to read text from the pane screen (`capture-pane`).
That is fragile because of line wrapping and UI elements, so the MVP does not do it.

## Driving the session via tmux

- Sending: `send-keys C-u` (clear the input line), `send-keys -l <text>`, `send-keys Enter`.
  Newlines in the text are replaced with spaces.
- **Escape** interrupts generation, and `status` becomes `idle` immediately. If interrupted before
  a response has appeared, claude **puts the prompt text back into the input line**. Hence `C-u` before
  every send.
- Escape is sent **only when `status == busy`**: a double Escape while idle opens the rewind menu.
- While the session is busy, claude queues a new message by itself.

## Choice of services

- **STT: ElevenLabs Scribe v2 Realtime** (`ElevenLabsRealtimeSTTService`, `language=ru`, commit driven by
  Pipecat's VAD). An ElevenLabs key was already available. Russian is recognized well, with occasional small errors
  («Майди» instead of «Найди» ("find"), «Нежат» instead of «лежат» ("lie")); the voice-mode rules (`voice-rules.md`)
  are written with these in mind. Deepgram was not needed.
  (The bridge now supports English and Russian, selected with the `VOICE_LANG` setting.)
- **TTS: ElevenLabs `eleven_flash_v2_5`** over WebSocket, voice Maxim (`HcaxAsrhw4ByUo4CBCBN`, Russian).
  About 0.1 s from text to first audio.
- **VAD: Silero. End of turn: Smart Turn v3** (local, CPU).
- **Transport: SmallWebRTC** and Pipecat's built-in client (`/client/`).

## End of turn and barge-in: what we learned

1. **Smart Turn v3 on Russian cuts phrases at comma pauses** («Сколько будет семь, | умножить на
   восемь» ("What is seven, | times eight")). With the default `VAD stop_secs=0.2` a long phrase fell apart into 3–4 messages.
   What was done:
   - `VAD_STOP_SECS=0.5`: pauses inside a phrase (0.26–0.46 s in tests) no longer split it. Costs about +0.3 s of latency;
   - **merging in the bridge**: if a new utterance arrives while claude has not yet written a single response block
     for the previous one, the bridge sends Escape and sends the combined text. Detected via the `timestamp`
     of transcript records;
   - if Smart Turn has closed the turn but the user keeps talking, the continuation opens a new turn
     (otherwise the tail was lost).
2. **On short words** («Да.» ("Yes.")) Smart Turn often answers "incomplete", and the turn is closed by the fallback
   pause `TURN_MAX_PAUSE_SECS=1.5` (3 s by default in Pipecat).
3. **Word-based barge-in** (`MinWordsUserTurnStartStrategy`) waits for interim STT results:
   audio stopped only after **4.4 s**. Replaced with our own strategy `BargeInStartStrategy`: while the bot
   is speaking, barge-in happens after `0.2 s (VAD) + BARGE_IN_SECS (0.5 s)` of continuous speech.
   Result: **~1.1 s** from the start of the phrase to silence, including network and buffers.
4. **«Угу» ("uh-huh")**: a test «Угу.» (0.56 s of audio) does not cut off speech; its transcription is discarded.
   Longer backchannels («ага, понятно» ("yeah, got it")) will interrupt the bot. This is a deliberate trade-off.
5. The pipeline emits `LLMContextFrame` even without a new utterance (on turn timeout). Because of this, the first version
   re-sent the last utterance. The bridge listens for `LLMContextUserTurnFrame`.

## Barge-in logic in the bridge

- The user starts speaking while the bot is speaking: Pipecat cuts the audio, and the bridge mutes the rest of the current
  response.
- The utterance ends:
  - a stop word («стоп», «хватит», «подожди», «отмена»… ("stop", "enough", "wait", "cancel")) → Escape only (if the session is busy);
  - a goodbye («пока», «до свидания», «отбой»… ("bye", "goodbye", "hang up")) → «До связи.» ("Talk later.") and the pipeline ends;
  - otherwise: if the user interrupted the speaking bot and the session is busy → Escape, then the new message.
    If the bot was silent and the session is busy → the message goes into claude's queue without Escape.
- Escape is sent **at the end** of the interrupting utterance, not at the start. Otherwise any «угу» or cough
  would kill a long-running claude task.
- Transcript blocks written before the last send are not spoken (they are leftovers of the interrupted response).

## Pause fillers

If `FILLER_SECS` (1.5 s) after a send claude has not said anything yet, the bridge says a short
phrase itself («Секунду.» ("One second."), «Сейчас посмотрю.» ("Let me look.")…). This cannot be done via the prompt: even a short first block
takes claude 1.5–3 s to write. Short answers (1.2–1.7 s) usually arrive before the filler. In that case the `TIMING` line
measures up to the filler, not up to claude's answer. The prompt asks claude not to write empty
«секунду» but to name the concrete action.

## Speaking tool calls

The bridge also reads `tool_use` blocks from the transcript and speaks them as a short phrase: `WebSearch` → «Ищу в интернете» ("Searching the web"),
`Read` → «Читаю файл» ("Reading a file"); for `Bash` the phrase is chosen by command (`git` → «Смотрю гит» ("Checking git"), `grep` → «Ищу в файлах» ("Searching files"),
`pytest` → «Запускаю тесты» ("Running tests")…). Housekeeping tools (`TaskUpdate`, `ToolSearch`) stay silent; `AskUserQuestion`
says "look at the terminal". The phrase is spoken only if `TOOL_GAP_SECS` (4 s) have passed since the last thing spoken by claude (its text or
another tool phrase) and it differs from the previous one. The filler does not count. In practice claude announces its work
itself per the voice-mode rules, and the first call after the announcement stays silent. The phrases are heard in long chains: search → read → edits.

## Selecting and switching sessions

- List: `~/.claude/sessions/<pid>.json` of live processes that have `tmux` (`<session>:@window.%pane`).
  The claude session name (`name`) and the tmux window name are different things. The bridge addresses the pane by `%id`.
- The selection at connect time is passed in `request_data` of the `/api/offer` request; Pipecat puts it in `runner_args.body`.
  On-the-fly switching is `POST /api/switch {pane, inject}`: the bridge swaps the session and the transcript tail without
  dropping WebRTC.
- `status: waiting` (`waitingFor: dialog open`) means a dialog is open in the session. The bridge does not type into it.
- The first ElevenLabs synthesis after connecting once took 3.5 s, while Pipecat by default waits 3 s for audio
  (`stop_frame_timeout_s`) and drops late audio. Raised to 6 s. On connect the bot immediately says
  «Подключаюсь к …» ("Connecting to …"), which also warms up the connection.
- Escape on barge-in is sent to working sessions too. Barging in during long work will interrupt it.

## Latency measurements

Client: `tests/selftest.py` (WebRTC over localhost, phrases synthesized with OpenAI TTS). "Client" is
from the end of our phrase to the bot's first audio. "Server" is the `TIMING` line in the log, from `VADUserStoppedSpeaking`.

| Scenario | Client | Server | Of which claude (tmux → first text) |
|---|---|---|---|
| Echo (phase 1, VAD stop 0.2 s) | 0.42–0.92 s | 0.52–0.63 s | — |
| Echo, final settings (VAD stop 0.5 s) | 0.82–1.02 s | 0.55–0.60 s | — |
| Short claude answer ("7×8", "12×13", "2+3") | 2.0–2.9 s | 1.8–2.5 s | 1.2–1.7 s |
| Long answer in one block (a ~800-character story) | ~5 s | ~5 s | ~4.5 s |
| Task with tools | announcement after 3–5 s | | |
| Barge-in (start of phrase → silence) | 1.07–1.13 s | | |

The difference between client and server (about 0.5 s) comes from VAD triggering (`stop_secs`) and the
WebRTC jitter buffer. The main latency is the time claude takes to generate the first text block.
It is reduced by the voice-mode rule "answer briefly; say a phrase before long work".

## Known limitations

- Echo: browser noise suppression is on, but it works reliably only **with headphones**. Without them the bot may
  interrupt itself.
- Long answers start playing only after the whole block is generated (see transcript format).
- All assistant texts in the session are spoken while a browser is connected, including answers to questions
  typed in the terminal.
- If the bot is interrupted by wordless noise, the rest of the answer is muted until the next utterance, while claude
  keeps working.
- Merging utterances costs one Escape and a re-send (about +0.3 s), and the interrupted message stays in the
  transcript.
- ElevenLabs TTS is billed by characters, so long answers cost more.
- No public TURN: WebRTC media goes directly over the Tailscale address. It does not work outside the tailnet (by
  design).

## Pauses mid-phrase

- Smart Turn v3 barely recognizes Russian hesitations: on «э-э-м...» ("uh-um...") and «один — через» ("one — via") after 0.4 s of silence
  it answers COMPLETE (log 2026-10-29), and claude received fragments.
- Scribe, however, marks cut-off speech in the text: an ellipsis, "-", "--", or no final period. The bridge
  (`looks_complete`) holds such an utterance for `HOLD_SECS` (2.5 s). If the user speaks again
  (InterruptionFrame), the wait is extended up to 15 s, and the pieces go out as one message. Cost: a phrase
  that genuinely ends with an ellipsis goes out 2.5 s later.
- On InterruptionFrame Pipecat cancels the current frame processing in a processor. Sending to tmux
  (Escape, waiting for claude to stop, text) ran directly in `process_frame`, and a new utterance cut it off
  between Escape and text: the interrupted prompt was lost and the merged text was never typed. Now sending
  runs as a separate task under a lock.

## Server access

- The Pipecat runner (`_configure_server_app`) installs CORS with `allow_origins=["*"]`. Without our own check, any
  page open in the browser could make a WebRTC offer to `http://localhost:7860/api/offer` and
  talk to claude with a synthesized voice. So `AccessGuard` in `bot.py` requires `Origin` to
  match `Host`. A foreign `Host` (DNS rebinding) is rejected too.
- `tailscale serve` proxies with `Host: <machine>.ts.net` and adds `Tailscale-User-Login`. serve overwrites a header
  forged by the client: verified, a request with someone else's login in the header arrived with the
  real one. Funnel and tagged devices have no login; such requests get 403.
- The middleware is pure ASGI, so it also covers the runner's WebSocket routes (`/ws`, `/ws-client/...`),
  which are registered regardless of transport.

## herdr (herdr.dev) as a possible second backend

Documentation: `docs/next/website/src/content/docs/{socket-api,plugins,agent-automation}.mdx` in
herdrdev/herdr, checked 2026-10-29.
- Sending: `agent.prompt` (with waiting via `wait.until`) and `agent.send_keys`. If the agent is `blocked`,
  `agent.prompt` returns `agent_blocked` and types nothing: the same as our refusal when a dialog is open.
- State: `idle` / `working` / `blocked` inferred from the screen for 22 agents, plus `events.subscribe`.
- The API has no response text, only the screen: `agent.read` returns the rendered terminal. It scrolls a long
  Claude Code history with the mouse wheel and only when `idle`. That is useless for speech.
  But `agent.list` / `pane.get` return `agent_session` (for Claude Code, the session id via the official
  integration `herdr integration install claude`), which locates our JSONL. So for herdr
  only the transport changes (list, send, Escape, status), and answers are still read from the
  transcript. Other agents would need their own reader for their format.
- Plugin: `herdr-plugin.toml` with `[[build]]`, `[[startup]]` (one-shot, "not a daemon supervisor"),
  `[[actions]]`, `[[panes]]`. A plugin keeps a long-lived server in its own pane (`[[panes]]`, as
  herdr-call does: `node dist/server/main.js` in a separate tab) and must kill background processes when that pane
  closes. Plugin config lives in `HERDR_PLUGIN_CONFIG_DIR`; that is where `.env` would go.

## requestData in /api/offer (2026-10-30)

Some Pipecat clients send the session selection in `requestData`. `SmallWebRTCRequest` has `from_dict`,
which renames it to `request_data`, but the runner route receives the body through FastAPI as a
dataclass, and `from_dict` is not called: the field was silently dropped and the bot went to the default session.
Fixed with the ASGI middleware `CamelCaseOffer`. The web page sends `request_data`, so it was not affected.

## ElevenLabs errors are visible to the client

`RTVIProcessor` forwards every `ErrorFrame` to the client as `{"type": "error", "data": {"error"}}`.
Quota: STT answers "You have exceeded your quota.", TTS answers "completed with no audio". The client
shows this as "ElevenLabs quota exhausted".

## «Ты здесь?» ("Are you there?") during a long task

While claude is busy (`status == busy`), short utterances like «ты здесь?», «как дела?» ("how's it going?"), «долго ещё?» ("much longer?")
(`is_checkin`: every phrase in the utterance is such a question or a hesitation, at most 10 words) are not sent to the session:
claude would queue them, and if the user interrupted the bot's speech, the bridge would also press Escape.
The bridge answers itself: «Я тут, работаю уже N минут. Сейчас: ищу в интернете.» ("I'm here, I've been working for N minutes. Right now: searching the web."). The time is counted from the
last sent utterance, the activity from the last tool_use in the transcript (reset when
claude writes text). If the session became busy before connecting, the bridge knows neither.

## Utterance merging and claude's queue

Merging (a new utterance arrives ≤5 s after the one sent) presses Escape and sends both utterances at once.
This is correct only if claude started answering exactly our utterance. If at that moment it was busy with its own work,
the utterance sits in its queue, Escape pulls it back into the input line, and the text hangs there
unsent and duplicated (2026-11-01). So we merge only utterances sent into an idle
session (`_sent_queued`). And after Escape we wait 0.3 s: the interrupted prompt returns to the input line
not immediately, and `C-u` must arrive after it.

## Lost end of phrase after a pause (Smart Turn)

`TurnAnalyzerUserTurnStopStrategy` keeps the transcription text from the start of the turn and does not reset it when
speech resumes. Scenario (2026-11-02): «Ну, кажется, …» ("Well, it seems, …") → pause → «А-а-а…» ("Ah-h-h…") → pause (Smart Turn:
incomplete) → «Всё-таки довольно сильно отличаются» ("They do differ quite a lot after all"). On the last pause Smart Turn says "complete",
the STT wait is collapsed to 0 (`VAD stop_secs 0.5 ≥ p99 STT`), and the turn closes on the old «А-а-а…» without
waiting for the transcription of the continuation. That arrives outside the turn, opens a new turn, which after 5 s
ends empty ("Empty user turn"): the phrase is visible in the client (RTVI) but never reaches the session.
`FreshTranscriptStopStrategy` in `bot.py` resets the text when speech starts, and the turn waits for the transcription of
the last piece (manual commit; a `finalized` transcription closes the turn immediately, with no extra delay).

## Photos into the session: bracketed paste of the path (2026-11-02)

- Claude Code turns an image path inserted as a paste into an `[Image #N]` attachment: the image goes
  with the message, and neither Read nor permission for a file outside the project is needed. `send-keys -l` does not do this; it takes
  bracketed paste: `tmux load-buffer -` + `paste-buffer -p` (verified on 2.1.316).
- It processes the paste asynchronously. If text and Enter are typed right away, the text lands before the label and
  Enter is lost. So `_paste_image` waits until the number of `[Image #` on the pane screen increases.
- The C-u at the start of `send()` would clear the attachment, so the photo is not pasted in advance; it is accumulated in the bridge and
  pasted in `send()` after C-u. When utterances are merged, the previous utterance's photos are attached again.
- Test: `uv run tests/selftest.py photo <test pane> picture.png "a phrase from tests/audio"`.


## Answer cut off by noise (2026-11-03)

- In a noisy room VAD hears "speech" without a single word (9 s, STT sent nothing), and
  BargeInStartStrategy cuts the answer off. Especially easily in the first fractions of a second: the text has already gone to TTS
  but BotStartedSpeakingFrame has not arrived yet, so the strategy thinks the bot is silent.
- The bridge remembers the text handed to TTS (`_unsaid`) and what it skipped after the cut (`_cut`). If the user's
  turn has ended (`UserStoppedSpeakingFrame`, including on turn timeout) and not a single word
  arrived within a second, the answer is spoken again.
- Testing: a false barge-in cannot be reproduced by voice: STT recognizes a reversed phrase as
  words. The logic was tested by feeding frames through `pipecat.tests.utils.run_test`.

## Merged utterance stuck in the input box (2026-11-03)

- After Escape claude returns the interrupted prompt to the input box not immediately (0.2–0.3 s). Previously the bridge
  waited a fixed 0.3 s; sometimes C-u arrived earlier, the text came back on top of the new one, Enter was lost,
  and the utterance sat in the box until the next one. Now `interrupt(restored=...)` waits until the end of the prompt
  appears in the box (`input_line()` via `capture-pane`).
- C-u in claude erases one screen line, but a long prompt wraps over several: we send as many C-u
  as there are lines in the box.
- Enter after the prompt is restored and a long text is sometimes lost. `send()` checks that the end of the text
  has left the box and presses Enter again (verified: the merged utterance goes out clean).
- claude draws queued messages above the box as separate lines with "❯", and in the box itself
  the hint "Press up to edit queued messages"; `input_box()` takes the last line with "❯".
- A late transcription (STT lagging by seconds) arrives when the bot is already saying «секунду» ("one second").
  BargeInStartStrategy discarded it as an «угу»; now it discards only up to two words.

## Tailnet owner after reboot

The service starts before tailscaled has connected to the network.
- At first `tailscale status --json` returns `User: null`.
- Then, in "Starting", it fails outright.

Because of this the owner computed in run.sh ended up empty, and the server answered 403 to everyone in the tailnet until a manual
restart. Waiting in run.sh did not help: once tailscaled took longer to come up.

Now bot.py determines the owner itself:
- on the first request from the tailnet (`_tailnet_users()`);
- if `TAILNET_USERS` is not set.

run.sh only waits up to a minute for tailscaled to connect so it can run `tailscale serve`. The serve configuration
is persisted in tailscaled's state anyway.

## Language and .env (2026-11-08)

- `VOICE_LANG` (`en` by default, or `ru`) selects the STT/TTS language and everything the bridge itself says
  and recognizes: the phrases and command patterns in `phrases.py`.
- `pipecat.runner.run` calls `load_dotenv(override=True)` on import, so values from `.env` overwrote
  environment variables (`VOICE_LANG=en uv run bot.py` had no effect). `bot.py` saves the environment
  before importing Pipecat and restores it afterwards.

## Switching sessions by voice (2026-11-09)

- Bridge commands are in `commands.py`, their patterns in `phrases.py`. A command counts only at the start
  of a phrase, and the phrase itself is not sent to the session. The check runs in `_send_pending`, on the
  whole phrase after waiting for its continuation, so "Switch to…" + pause + "aramco" works too.
- Strong verbs ("switch") always act: no match means "I can't find a session called X". Weak ones ("go back
  to", "go to") act only when a session matches, otherwise the phrase goes to claude: "go back to the start
  of the file" is a request, not a command. A tail longer than four words is not a session name.
- Names: both sides become a rough phonetic key (Cyrillic transliterated, c→k, sch→sh, e/y→i, no double
  letters), so «Арамко» = aramco and «шедулинг» = scheduling. A session's keys are its name, tmux window and
  folder, and their words. Tiers: whole name 1.0, word 0.95, prefix 0.9, otherwise SequenceMatcher (capped at
  0.85, threshold 0.65). A session wins only if it is a tier above the rest; otherwise "I have A or B. Which
  one?", and the answer (a name, "the second") is accepted for 30 s. Names that sound nothing alike:
  `SESSION_ALIASES`.
- The page learns about a switch from an RTVI server message `{"type": "session", pane, name}` over the data
  channel (RTVIObserver sends it without client-ready); `/api/sessions` also returns `current`.
- "Go back", "switch back", "back" count only as the whole phrase ("go back and fix the test" goes to
  claude). `switch()` remembers the previous session on every switch, the page's included, so saying it
  again toggles between the two. Its pane is checked against `list_sessions()`: gone means "Session X is
  closed", no switch yet means "There's nowhere to go back to".

## Stopping the service (2026-11-11)

- `restart` timed out and the old python lived on with the call. Two causes. Snap uv puts its child
  into a scope of its own (`snap.astral-uv.uv-*.scope`), outside the unit's cgroup, so systemd killed only
  uv. And a call is a background task of its `/api/offer` request: on SIGTERM uvicorn stops listening but
  waits for open requests, i.e. for the call to end.
- Now `run.sh` runs `uv sync` and `exec .venv/bin/python`, so python is the unit's main process and
  `KillMode=process` still leaves tmux and claude alone. On SIGTERM bot.py first cancels the call
  pipelines (`RUNNERS`), then calls uvicorn's handler. With a call open it exits in 0.3 s.
