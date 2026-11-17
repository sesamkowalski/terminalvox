"""Voice commands and fuzzy session names: uv run python -m unittest discover tests"""

import unittest

from commands import find, parse, parse_aliases, pick, sound
from phrases import EN, RU


def s(name, where="", cwd=""):
    return {"name": name, "pane": "%" + name, "where": where or f"0:1 {name}", "cwd": cwd or f"~/git/{name}"}


SESSIONS = [s("aramco-pbit-cell"), s("day"), s("scheduling-agent"), s("voicesession"), s("culture-trip"),
            s("data-pipeline"), s("data-platform")]


class Parse(unittest.TestCase):
    def test_switch_ru(self):
        self.assertEqual(parse("Переключись на Арамко.", RU), ("switch", "Арамко", True))
        self.assertEqual(parse("Ну, переключи на дэй", RU), ("switch", "дэй", True))
        self.assertEqual(parse("Переключись на сессию шедулинг!", RU), ("switch", "сессию шедулинг", True))
        self.assertEqual(parse("Перейди в календарь.", RU), ("switch", "календарь", False))
        self.assertEqual(parse("Вернись к voicesession", RU), ("switch", "voicesession", False))

    def test_switch_en(self):
        self.assertEqual(parse("Switch to aramco.", EN), ("switch", "aramco", True))
        self.assertEqual(parse("OK, switch back to the day session", EN), ("switch", "the day session", True))
        self.assertEqual(parse("Go back to scheduling.", EN), ("switch", "scheduling", False))

    def test_list(self):
        self.assertEqual(parse("Какие есть сессии?", RU), ("list", "", True))
        self.assertEqual(parse("Список сессий.", RU), ("list", "", True))
        self.assertEqual(parse("What sessions are there?", EN), ("list", "", True))
        self.assertEqual(parse("List sessions.", EN), ("list", "", True))

    def test_back(self):
        for phrase in ("Вернись обратно.", "Ну, назад!", "Обратно", "Давай вернёмся обратно", "Вернись назад"):
            self.assertEqual(parse(phrase, RU), ("back", "", True), phrase)
        for phrase in ("Go back.", "OK, switch back", "Back!", "go back please"):
            self.assertEqual(parse(phrase, EN), ("back", "", True), phrase)
        self.assertIsNone(parse("Вернись назад к прошлому коммиту.", RU))
        self.assertIsNone(parse("Назад в будущее посмотрим?", RU))
        self.assertIsNone(parse("Go back and fix the test.", EN))
        self.assertEqual(parse("Вернись обратно к дэй", RU), ("switch", "дэй", False))

    def test_fillers_and_stretched_vowels(self):
        for phrase in ("Итак, переключись нааа Арамко.", "Так вот, переключись на Арамко", "Слушай, переключись наа Арамко",
                       "Окей, переключись на Арамко", "Хорошо, переключись на Арамко", "Э-э-э, переключись на Арамко",
                       "А-а-а, ну, переключись на Арамко", "М-м, переключись на Арамко", "Э... переключись на Арамко",
                       "Пееереключись на Арамко"):
            self.assertEqual(parse(phrase, RU), ("switch", "Арамко", True), phrase)
        self.assertEqual(parse("Итак, вернись обратнооо.", RU), ("back", "", True))
        self.assertEqual(parse("Э-э, какие есть сессии?", RU), ("list", "", True))
        self.assertEqual(parse("Ну, переключись на сессию дэй", RU), ("switch", "сессию дэй", True))
        self.assertEqual(parse("Um, switch to aramco", EN), ("switch", "aramco", True))
        self.assertEqual(parse("Well, list sessions.", EN), ("list", "", True))
        self.assertIsNone(parse("Итак, давай починим тесты.", RU))
        self.assertIsNone(parse("Мама, переключись на Арамко", RU))

    def test_not_a_command(self):
        # Only at the start of a phrase, and a long tail is a request, not a session name.
        self.assertIsNone(parse("Я хочу, чтобы ты переключился на другую задачу.", RU))
        self.assertIsNone(parse("Перейди в папку src и почини там опечатку в README.", RU))
        self.assertIsNone(parse("Давай на этом остановимся.", RU))
        self.assertIsNone(parse("Can you switch to tabs in this file?", EN))
        self.assertIsNone(parse("Go to the docs folder and fix the typo in the intro.", EN))
        self.assertIsNone(parse("Какие сессии лучше закрыть, как думаешь?", RU))
        self.assertIsNone(parse("Переключись на", RU))


class Find(unittest.TestCase):
    def names(self, query, **kw):
        return [x["name"] for x in find(query, SESSIONS, **kw)]

    def test_transliteration(self):
        self.assertEqual(self.names("Арамко"), ["aramco-pbit-cell"])
        self.assertEqual(self.names("дэй"), ["day"])
        self.assertEqual(self.names("шедулинг"), ["scheduling-agent"])
        self.assertEqual(self.names("войс сешн"), ["voicesession"])
        self.assertEqual(self.names("культур трип"), ["culture-trip"])

    def test_exact_and_prefix(self):
        self.assertEqual(self.names("day"), ["day"])
        self.assertEqual(self.names("voice"), ["voicesession"])
        self.assertEqual(self.names("aramco pbit"), ["aramco-pbit-cell"])

    def test_noise_word_in_the_name(self):
        # "session" is part of "voicesession", not a filler, when there is also a session "voice".
        both = SESSIONS + [s("voice")]
        find_ = lambda q, p: [x["name"] for x in find(q, both, p=p)]
        self.assertEqual(find_("Voice Session", EN), ["voicesession"])
        self.assertEqual(find_("Voice Session", RU), ["voicesession"])
        self.assertEqual(find_("voice", RU), ["voice"])
        self.assertEqual(find_("сессию дэй", RU), ["day"])
        self.assertEqual(find_("the day session", EN), ["day"])
        self.assertEqual(parse("Дааа, переключи на Voice Session.", RU), ("switch", "Voice Session", True))

    def test_ambiguous(self):
        self.assertEqual(sorted(self.names("data")), ["data-pipeline", "data-platform"])
        self.assertEqual(self.names("дата пайплайн"), ["data-pipeline"])

    def test_nothing(self):
        self.assertEqual(self.names("банан"), [])
        self.assertEqual(self.names("kubernetes"), [])

    def test_aliases(self):
        aliases = parse_aliases("календарь=scheduling-agent, шеф = day")
        self.assertEqual(self.names("календарь", aliases=aliases), ["scheduling-agent"])
        self.assertEqual(self.names("Шеф", aliases=aliases), ["day"])
        self.assertEqual(self.names("календарь"), [])

    def test_sound(self):
        self.assertEqual(sound("Арамко"), sound("aramco"))
        self.assertEqual(sound("шедулинг"), sound("scheduling"))
        self.assertEqual(sound("дэй"), sound("day"))


class Complete(unittest.TestCase):
    def test_unfinished_endings(self):
        from bridge import looks_complete
        for t in ["Давай обсудим.", "Посмотри файл, что?", "Мама.", "Сделай так, а потом.", "Fix the bug."]:
            self.assertTrue(looks_complete(t), t)
        for t in ["Давай обсудим...", "Давай обсудим, и.", "Давай обсудим, э-э-э.", "Давай, ээээ.",
                  "Это важно, потому что.", "Ok, and um."]:
            self.assertFalse(looks_complete(t), t)


class ToolPhrase(unittest.TestCase):
    def say(self, cmd):
        from bridge import tool_phrase
        return tool_phrase({"name": "Bash", "input": {"command": cmd}}, RU)

    def test_program_not_path(self):
        self.assertEqual(self.say("grep -rn foo ~/vault/docker-notes/x.md"), "Ищу в файлах.")
        self.assertEqual(self.say("cd ~/git/voicesession && ls -la"), "Смотрю файлы.")
        self.assertEqual(self.say("cd ~/git/voicesession && git log --oneline -3"), "Смотрю гит.")
        self.assertEqual(self.say("docker ps"), "Работаю с докером.")
        self.assertEqual(self.say("sudo docker compose up -d"), "Работаю с докером.")
        self.assertEqual(self.say("PYTHONPATH=. .venv/bin/python -m unittest"), "Запускаю скрипт.")
        self.assertEqual(self.say("timeout 120 uv run pytest -q"), "Запускаю тесты.")
        self.assertEqual(self.say("cat a.txt | grep x"), "Ищу в файлах.")
        self.assertEqual(self.say("mkdir -p ~/git/x"), "Запускаю команду.")


class Filler(unittest.TestCase):
    def kind(self, text, p=RU):
        from bridge import filler_choices
        return (filler_choices(text, p) or ["(silent)"])[0]

    def test_by_kind(self):  # real phrases from the log
        self.assertEqual(self.kind("М-м-м, да вроде ничего."), "(silent)")
        self.assertEqual(self.kind("Да, закрываем."), "(silent)")
        self.assertEqual(self.kind("Приём."), "(silent)")
        self.assertEqual(self.kind("А-а-а, стой, стой, стой!"), "Понял.")
        self.assertEqual(self.kind("Не надо ничего записывать."), "Понял.")
        self.assertEqual(self.kind("А-а-а, м-м-м, ну-с, покажи, какие вопросы остались."), "Сейчас посмотрю.")
        self.assertEqual(self.kind("Виза есть. Э-э-э, запиши, э-э-э, этот рейс."), "Понял, делаю.")
        self.assertEqual(self.kind("Э-э-э, причём мы там договорились?"), "Сейчас подумаю.")
        self.assertEqual(self.kind("Скажи, а где-то сохраняются логи наших разговоров"), "Сейчас подумаю.")
        self.assertEqual(self.kind("Это отдельное испытание."), "Угу.")
        self.assertEqual(self.kind("Yeah, sounds good.", EN), "(silent)")
        self.assertEqual(self.kind("Can you check the logs?", EN), "Let me look.")
        self.assertEqual(self.kind("Why is it slow?", EN), "Let me think.")


class Pick(unittest.TestCase):
    def test_pick(self):
        choices = [s("data-pipeline"), s("data-platform")]
        self.assertEqual(pick("Вторую.", choices, RU)["name"], "data-platform")
        self.assertEqual(pick("the first one", choices, EN)["name"], "data-pipeline")
        self.assertEqual(pick("платформ", choices, RU)["name"], "data-platform")
        self.assertIsNone(pick("не знаю", choices, RU))


class Bridge(unittest.IsolatedAsyncioTestCase):
    """TmuxBridge._command with tmux replaced by stubs."""

    async def asyncSetUp(self):
        import bridge

        class Timings:
            def mark(self, name): pass
            def reset(self): pass

        class Session:
            def __init__(self, pane):
                self.target, self.name = pane, pane[1:]

        self.mod = bridge
        self._saved = bridge.list_sessions, bridge.ClaudeSession
        bridge.list_sessions = lambda: SESSIONS
        bridge.ClaudeSession = Session
        self.b = bridge.TmuxBridge(None, Timings(), phrases=RU, aliases=parse_aliases("календарь=scheduling-agent"))
        self.said, self.switched = [], []

        async def say(text): self.said.append(text)
        async def switch(session, inject):  # what the real switch does with the session pair
            if self.b._session:
                self.b._previous = {"pane": self.b._session.target, "name": self.b._session.name}
            self.b._session = session
            self.switched.append(session.name)
        self.b._say, self.b.switch = say, switch

    async def asyncTearDown(self):
        self.mod.list_sessions, self.mod.ClaudeSession = self._saved

    async def test_switch(self):
        self.assertTrue(await self.b._command("Переключись на Арамко."))
        self.assertTrue(await self.b._command("Перейди в календарь."))
        self.assertEqual(self.switched, ["aramco-pbit-cell", "scheduling-agent"])

    async def test_which_one(self):
        self.assertTrue(await self.b._command("Переключись на дата."))
        self.assertEqual(self.said, ["Есть data-pipeline или data-platform. Какую?"])
        self.assertTrue(await self.b._command("Вторую."))
        self.assertEqual(self.switched, ["data-platform"])

    async def test_list_and_none(self):
        self.assertTrue(await self.b._command("Какие есть сессии?"))
        self.assertTrue(self.said[-1].startswith("Сессии: aramco-pbit-cell, day,"))
        self.assertTrue(await self.b._command("Переключись на банан."))
        self.assertEqual(self.said[-1], "Не нашёл сессию банан.")
        self.assertEqual(self.switched, [])

    async def test_not_a_command(self):
        self.assertFalse(await self.b._command("Вернись к началу файла."))
        self.assertFalse(await self.b._command("Почини тесты."))
        self.assertEqual((self.said, self.switched), ([], []))

    async def test_back(self):
        self.assertTrue(await self.b._command("Вернись обратно."))
        self.assertEqual(self.said, ["Некуда возвращаться."])
        await self.b._command("Переключись на дэй.")
        await self.b._command("Переключись на Арамко.")
        self.assertTrue(await self.b._command("Назад."))
        self.assertTrue(await self.b._command("Обратно!"))
        self.assertEqual(self.switched, ["day", "aramco-pbit-cell", "day", "aramco-pbit-cell"])

    async def test_back_closed(self):
        self.b._previous = {"pane": "%gone", "name": "gone"}
        self.assertTrue(await self.b._command("Вернись назад"))
        self.assertEqual(self.said, ["Сессия gone закрыта."])
        self.assertEqual(self.switched, [])

class Checkin(unittest.TestCase):
    def test_first_word_lost(self):
        from bridge import is_checkin
        from phrases import EN, RU
        for t in ["Ты здесь?", "Здесь?", "А-а-а, здесь?", "Тут?", "Жив?"]:
            self.assertTrue(is_checkin(t, RU), t)
        for t in ["Здесь проблема.", "Здесь файл лежит."]:
            self.assertFalse(is_checkin(t, RU), t)
        self.assertTrue(is_checkin("There?", EN))
        self.assertFalse(is_checkin("There is a bug.", EN))



if __name__ == "__main__":
    unittest.main()

