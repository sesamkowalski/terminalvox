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
        self.assertEqual(parse("Переключись на сессию шедулинг!", RU), ("switch", "шедулинг", True))
        self.assertEqual(parse("Перейди в календарь.", RU), ("switch", "календарь", False))
        self.assertEqual(parse("Вернись к voicesession", RU), ("switch", "voicesession", False))

    def test_switch_en(self):
        self.assertEqual(parse("Switch to aramco.", EN), ("switch", "aramco", True))
        self.assertEqual(parse("OK, switch back to the day session", EN), ("switch", "day", True))
        self.assertEqual(parse("Go back to scheduling.", EN), ("switch", "scheduling", False))

    def test_list(self):
        self.assertEqual(parse("Какие есть сессии?", RU), ("list", "", True))
        self.assertEqual(parse("Список сессий.", RU), ("list", "", True))
        self.assertEqual(parse("What sessions are there?", EN), ("list", "", True))
        self.assertEqual(parse("List sessions.", EN), ("list", "", True))

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


class Pick(unittest.TestCase):
    def test_pick(self):
        choices = [s("data-pipeline"), s("data-platform")]
        self.assertEqual(pick("Вторую.", choices, RU)["name"], "data-platform")
        self.assertEqual(pick("the first one", choices, EN)["name"], "data-pipeline")
        self.assertEqual(pick("платформ", choices, RU)["name"], "data-platform")
        self.assertIsNone(pick("не знаю", choices, RU))


if __name__ == "__main__":
    unittest.main()


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
        async def switch(session, inject): self.switched.append(session.name)
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
