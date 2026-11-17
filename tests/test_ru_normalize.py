"""Run: uv run python tests/test_ru_normalize.py (or pytest)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ru_normalize as r  # noqa: E402
import stress  # noqa: E402

WORDS = {"commit": "коммит", "tmux": "ти-макс", "claude code": "клод код", "claude": "клод"}


def test_numbers():
    assert r.normalize("Тестов 21, задержка 1.7 с.") == "Тестов двадцать один, задержка одна целая семь десятых с."
    assert r.normalize("Версия 3.12.1") == "Версия три точка двенадцать точка один"
    assert r.normalize("В 22:07") == "В двадцать два ноль семь"
    assert r.normalize("1%, 3%, 11%, 25 %") == "один процент, три процента, одиннадцать процентов, двадцать пять процентов"
    assert r.normalize("стоит $10") == "стоит десять долларов"


def test_latin():
    assert r.normalize("Сделал commit в tmux.", WORDS) == "Сделал коммит в ти-макс."
    assert r.normalize("Claude Code и Claude", WORDS) == "клод код и клод"
    assert r.normalize("API и FPGA") == "эй пи ай и эф пи джи эй"
    assert r.normalize("make site code") == "мейк сайт коуд"
    assert r.normalize("файл MOBILE.md") == "файл мобайл точка эм ди"
    assert r.normalize("1D.3 и 1E") == "один ди точка три и один и"


def test_symbols():
    assert r.normalize("**жирный** `код` — дальше → итог") == "жирный код – дальше – итог"
    assert r.normalize("что-то и/или") == "что-то и или"


def test_silero_stress():
    assert stress.stress("Сейчас перезапущу.", {"перезапущу": 9}, plus=True) == "Сейчас перезапущ+у."


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
