"""What the bridge says and which spoken commands it recognizes, per language (VOICE_LANG).

To add a language, add a Phrases entry to LANGS: speech recognition and synthesis use `language`,
the rest is text the bridge speaks or matches in the transcribed speech.
"""

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Phrases:
    language: str  # pipecat Language value for STT and TTS
    voice: str  # default ElevenLabs voice id
    # Whole-utterance commands: stop speaking and interrupt Claude; end the call.
    stop: re.Pattern
    bye: re.Pattern
    # "Are you there?" while Claude is busy: the bridge answers with a status itself.
    checkin: re.Pattern
    hesitation: re.Pattern  # "um", "well": allowed around a check-in
    # Spoken when Claude calls a tool: tool name -> phrase; Bash by command regex, first match.
    tools: dict[str, str]
    bash: list[tuple[str, str]]
    bash_default: str
    mcp: str
    fillers: list[str]  # said when Claude stays silent after a phrase
    # Injected into the Claude session (see voice-rules.md).
    voice_on: str
    voice_off: str
    image_only: str  # message text for a photo sent without words
    connecting: str  # {name}
    listening: str
    switching: str  # {name}
    dialog_open: str
    goodbye: str
    photo_received: str
    photo_no_session: str
    rest_in_terminal: str
    link: str  # replaces URLs in speech
    # Status: "<working><for N minutes | just started>. <now><activity>"
    working: str
    minutes: Callable[[int], str]
    just_started: str
    now: str
    echo: str  # {text}; echo mode (no Claude session), for testing the voice loop
    echo_long_trigger: str
    echo_long: str


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


EN = Phrases(
    language="en",
    voice="JBFqnCBsd6RMkjVDRZzb",  # George
    stop=re.compile(r"^(stop|enough|cancel|quiet|shut up|hold on|wait)[\s.!,]*$", re.I),
    # Matched after collapsing repeated letters ("goodbye" -> "godbye").
    # Only as a whole phrase ("bye", "bye-bye", "OK, bye"): a sentence that merely contains "bye" doesn't end the call.
    bye=re.compile(r"^((ok(ay)?,?\s+)?(bye( for now)?|go+d\s?bye|see you|talk (to you )?later|hang up)[-\s.!,]*)+$", re.I),
    checkin=re.compile(
        r"^\W*((um+|uh+|hey|so|well)[-\s,.…]+)*(are\s+you\s+(there|here|alive|still\s+(there|here|working))"
        r"|you\s+(there|still\s+there)|how('s|\s+is)\s+(it|everything)\s+going|how\s+are\s+(you|things)(\s+doing)?"
        r"|what\s+are\s+you\s+doing|what's\s+(up|happening|the\s+status)|any\s+progress"
        r"|(are\s+you|is\s+it)\s+done(\s+yet)?|still\s+working|hello|ping)\b[^.?!]{0,12}[.?!…]*\s*$",
        re.I,
    ),
    hesitation=re.compile(r"^[\W_]*((um+|uh+|so|well|or)[-\s,.…]*)*$", re.I),
    tools={
        "Read": "Reading a file.",
        "Grep": "Searching files.",
        "Glob": "Looking at files.",
        "Edit": "Editing a file.",
        "Write": "Writing a file.",
        "NotebookEdit": "Editing a notebook.",
        "WebSearch": "Searching the web.",
        "WebFetch": "Opening a page.",
        "Agent": "Calling a helper.",
        "Task": "Calling a helper.",
        "Skill": "Using a skill.",
        "AskUserQuestion": "I have a question, check the terminal.",
        "ExitPlanMode": "The plan is ready, check the terminal.",
        "Artifact": "Preparing a page.",
    },
    bash=[
        (r"\bpytest\b|\b(npm|cargo|go) test\b", "Running tests."),
        (r"\bssh\b|\bscp\b|\brsync\b", "Connecting to a server."),
        (r"\bgh\b", "Checking GitHub."),
        (r"\bgit\b", "Checking git."),
        (r"\bcurl\b|\bwget\b", "Making a network request."),
        (r"\bsqlite3\b|\bpsql\b|\bmysql\b", "Querying a database."),
        (r"\bdocker\b", "Working with Docker."),
        (r"\b(grep|rg|find)\b", "Searching files."),
        (r"\b(ls|tree|du)\b", "Looking at files."),
        (r"\b(cat|head|tail|less|wc)\b|\bsed -n\b", "Reading a file."),
        (r"\b(python3?|node|uv run)\b", "Running a script."),
    ],
    bash_default="Running a command.",
    mcp="Calling an external service.",
    fillers=["One sec.", "Let me check.", "Just a moment.", "OK, looking.", "Got it, on it.", "Mm-hm, one moment."],
    voice_on=(
        "[voice] Voice mode is on: reply to [voice] messages briefly, conversationally and without markdown,"
        " put details in files, don't write the [voice] tag in your replies (rules in ~/.claude/CLAUDE.md,"
        " section \"Voice mode\"). Reply with a short greeting."
    ),
    voice_off="[voice-off] Voice mode is off, reply as usual from now on. No need to reply to this message.",
    image_only="Look at the photo.",
    connecting="Connecting to {name}.",
    listening="Listening.",
    switching="Switching to {name}.",
    dialog_open="A dialog is open in the terminal, answer it there.",
    goodbye="Talk to you later.",
    photo_received="Got the photo.",
    photo_no_session="Got the photo, but there's no session.",
    rest_in_terminal="The rest is in the terminal.",
    link="link",
    working="I'm here, working",
    minutes=lambda n: f" for {n} minute{'s' if n != 1 else ''}",
    just_started=", just started",
    now="Now: ",
    echo="You said: {text}",
    echo_long_trigger="long",
    echo_long=(
        " Now I'll talk for a while so you can test interrupting me."
        " The first sentence of a long answer. The second sentence of a long answer."
        " The third sentence of a long answer. The fourth sentence. The fifth sentence."
    ),
)

RU = Phrases(
    language="ru",
    voice="HcaxAsrhw4ByUo4CBCBN",  # Maxim
    stop=re.compile(r"^(стоп|хватит|замолчи|подожди|отмена|тихо|stop)[\s.!,]*$", re.I),
    # «Пока», «пока-пока», «всё, пока» — целой фразой: «пока не надо» прощанием не считается.
    bye=re.compile(r"^((пока|до свидания|отбой|конец связи|всё,? пока|bye)[-\s.!,]*)+$", re.I),
    checkin=re.compile(
        r"^\W*((ну|эй|алло|а+|э+|м+|или)[-\s,.…]+)*(ты\s+(здесь|тут|где|жив|живой|там)|как\s+(дела|успехи|там|продвигается|оно)"
        r"|что\s+(там|делаешь|с\s+задачей)|ну\s+что|долго\s+ещё|скоро|алло|ау)\b[^.?!]{0,12}[.?!…]*\s*$",
        re.I,
    ),
    hesitation=re.compile(r"^[\W_]*((или|ну|а+|э+|м+)[-\s,.…]*)*$", re.I),
    tools={
        "Read": "Читаю файл.",
        "Grep": "Ищу в файлах.",
        "Glob": "Смотрю файлы.",
        "Edit": "Правлю файл.",
        "Write": "Записываю файл.",
        "NotebookEdit": "Правлю блокнот.",
        "WebSearch": "Ищу в интернете.",
        "WebFetch": "Открываю страницу.",
        "Agent": "Подключаю помощника.",
        "Task": "Подключаю помощника.",
        "Skill": "Беру навык.",
        "AskUserQuestion": "У меня вопрос, посмотрите в терминал.",
        "ExitPlanMode": "План готов, посмотрите в терминал.",
        "Artifact": "Готовлю страницу.",
    },
    bash=[
        (r"\bpytest\b|\b(npm|cargo|go) test\b", "Запускаю тесты."),
        (r"\bssh\b|\bscp\b|\brsync\b", "Подключаюсь к серверу."),
        (r"\bgh\b", "Смотрю гитхаб."),
        (r"\bgit\b", "Смотрю гит."),
        (r"\bcurl\b|\bwget\b", "Обращаюсь к сети."),
        (r"\bsqlite3\b|\bpsql\b|\bmysql\b", "Смотрю базу данных."),
        (r"\bdocker\b", "Работаю с докером."),
        (r"\b(grep|rg|find)\b", "Ищу в файлах."),
        (r"\b(ls|tree|du)\b", "Смотрю файлы."),
        (r"\b(cat|head|tail|less|wc)\b|\bsed -n\b", "Читаю файл."),
        (r"\b(python3?|node|uv run)\b", "Запускаю скрипт."),
    ],
    bash_default="Запускаю команду.",
    mcp="Обращаюсь к внешнему сервису.",
    fillers=["Секунду.", "Сейчас посмотрю.", "Минутку.", "Так, смотрю.", "Понял, сейчас.", "Угу, сейчас."],
    voice_on=(
        "[voice] Голосовой режим включён: на сообщения с [voice] отвечай коротко, разговорно и без markdown,"
        " подробности пиши в файлы, метку [voice] в ответ не ставь (правила в ~/.claude/CLAUDE.md, раздел «Голосовой режим»)."
        " Ответь коротким приветствием."
    ),
    voice_off="[voice-off] Голосовой режим выключен, дальше отвечай как обычно. Отвечать на это сообщение не нужно.",
    image_only="Посмотри на фото.",
    connecting="Подключаюсь к {name}.",
    listening="Слушаю.",
    switching="Переключаюсь на {name}.",
    dialog_open="В терминале открыт диалог, ответьте там.",
    goodbye="До связи.",
    photo_received="Фото получил.",
    photo_no_session="Фото получил, но сессии нет.",
    rest_in_terminal="Остальное в терминале.",
    link="ссылка",
    working="Я тут, работаю",
    minutes=lambda n: f" уже {n} {_ru_plural(n, 'минуту', 'минуты', 'минут')}",
    just_started=", только начал",
    now="Сейчас: ",
    echo="Вы сказали: {text}",
    echo_long_trigger="длинн",
    echo_long=(
        " Сейчас я буду говорить долго, чтобы можно было проверить перебивание."
        " Первое предложение длинного ответа. Второе предложение длинного ответа."
        " Третье предложение длинного ответа. Четвёртое предложение. Пятое предложение."
    ),
)

LANGS = {"en": EN, "ru": RU}
