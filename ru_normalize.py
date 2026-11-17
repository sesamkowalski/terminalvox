"""Text for a Cyrillic-only synthesizer (Silero): numbers, Latin words and symbols spelled out in Russian.

Silero's alphabet is Cyrillic plus a few punctuation marks; anything else is dropped without a word,
so "commit в tmux" would be read as "в". Known words come from a list (latin.txt: "commit = коммит"),
short capitals are spelled letter by letter (API -> эй пи ай), the rest is transliterated by rough
English rules. Numbers go through num2words.
"""

import re
from pathlib import Path

from num2words import num2words

LETTERS = dict(zip("abcdefghijklmnopqrstuvwxyz", (
    "эй би си ди и эф джи эйч ай джей кей эл эм эн оу пи кью ар эс ти ю ви дабл-ю экс уай зи".split())))

# Longest first; Ä Ï Ö Ü mark a vowel lengthened by a final silent "e" (make, site, code, use).
_TRANSLIT = sorted({
    "tion": "шн", "sch": "ш", "sh": "ш", "ch": "ч", "th": "т", "ph": "ф", "ck": "к", "qu": "кв",
    "ll": "л", "ss": "с", "tt": "т", "oo": "у", "ee": "и", "ea": "и", "ie": "и", "ou": "ау", "ow": "оу", "oa": "оу", "ai": "ей", "ay": "ей",
    "ey": "ей", "wh": "у", "kn": "н", "gh": "", "ce": "се", "ci": "си", "cy": "си",
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "дж",
    "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т",
    "u": "у", "v": "в", "w": "в", "x": "кс", "y": "и", "z": "з",
    "Ä": "ей", "Ï": "ай", "Ö": "оу", "Ü": "ю",
}.items(), key=lambda kv: -len(kv[0]))

SYMBOLS = {"&": " и ", "+": " плюс ", "=": " равно ", "→": " – ", "—": " – ", "≈": " около ", "№": " номер "}
UNITS = {"%": ("процент", "процента", "процентов"), "$": ("доллар", "доллара", "долларов"),
         "€": ("евро", "евро", "евро"), "ГБ": ("гигабайт", "гигабайта", "гигабайт"),
         "МБ": ("мегабайт", "мегабайта", "мегабайт"), "Гц": ("герц", "герца", "герц"),
         "кГц": ("килогерц", "килогерца", "килогерц")}


def load(path: str | Path) -> dict[str, str]:
    """{latin word or phrase in lower case: how to say it}; a missing file is an empty list."""
    words = {}
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            key, sep, say = line.split("#", 1)[0].partition("=")
            if sep and key.strip() and say.strip():
                words[key.strip().lower()] = say.strip()
    return words


def plural(n: str, forms: tuple[str, str, str]) -> str:
    """The form for the number as written: 1 процент, 2 процента, 5 процентов, 1.5 процента."""
    if not n.isdigit():
        return forms[1]
    k = int(n)
    if k % 10 == 1 and k % 100 != 11:
        return forms[0]
    return forms[1] if 2 <= k % 10 <= 4 and not 12 <= k % 100 <= 14 else forms[2]


def number(n: str) -> str:
    n = n.replace(",", ".")
    if n.count(".") > 1:  # a version: 3.12.1 -> три точка двенадцать точка один
        return " точка ".join(num2words(int(x), lang="ru") for x in n.split("."))
    return num2words(float(n) if "." in n else int(n), lang="ru")


def translit(word: str) -> str:
    w = word.lower()
    if m := re.fullmatch(r"(.*[^aeiou]|)([aiou])([^aeiouy])e", w):  # make, site, code, use
        w = m[1] + {"a": "Ä", "i": "Ï", "o": "Ö", "u": "Ü"}[m[2]] + m[3]
    out, i = [], 0
    while i < len(w):
        for k, v in _TRANSLIT:
            if w.startswith(k, i):
                out.append(v)
                i += len(k)
                break
        else:
            i += 1
    return "".join(out)


def latin(word: str) -> str:
    """An unknown Latin word: spelled if it is short capitals or has no vowels, transliterated otherwise."""
    if (word.isupper() and len(word) <= 5) or len(word) == 1 or not re.search(r"[aeiouy]", word, re.I):
        return " ".join(LETTERS[c] for c in word.lower())
    return translit(word)


def normalize(text: str, words: dict[str, str] | None = None) -> str:
    t = re.sub(r"[`*#_~\[\]{}<>«»\"“”„|\\]", " ", text)
    for key in sorted(words or {}, key=len, reverse=True):
        t = re.sub(rf"(?<![A-Za-z]){re.escape(key)}(?![A-Za-z])", f" {words[key]} ", t, flags=re.I)
    t = re.sub(r"(?<=[A-Za-z0-9])\.(?=[A-Za-z]{1,4}\b)", " точка ", t)  # file names: notes.md
    t = re.sub(r"(?<=[A-Za-z])\.(?=\d)", " точка ", t)  # 1D.3
    t = re.sub(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)", " ", t)  # 1E, v5 -> 1 E, v 5
    t = re.sub(r"(?<=[A-Za-z0-9])[-/](?=[A-Za-z])|(?<=[A-Za-z])[-/](?=[A-Za-z0-9])", " ", t)
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b",
               lambda m: f"{number(m[1])} {'ноль ' if m[2][0] == '0' else ''}{number(m[2])}", t)
    for unit in sorted(UNITS, key=len, reverse=True):
        t = re.sub(rf"(\d+(?:[.,]\d+)?)\s*{re.escape(unit)}(?![А-Яа-яЁё])",
                   lambda m, f=UNITS[unit]: f"{number(m[1])} {plural(m[1], f)}", t)
        t = re.sub(rf"{re.escape(unit)}\s*(\d+(?:[.,]\d+)?)",  # $10
                   lambda m, f=UNITS[unit]: f"{number(m[1])} {plural(m[1], f)}", t)
    t = re.sub(r"\d+(?:[.,]\d+)+|\d+", lambda m: f" {number(m[0])} ", t)
    t = re.sub(r"[A-Za-z]+", lambda m: f" {latin(m[0])} ", t)
    for s, say in SYMBOLS.items():
        t = t.replace(s, say)
    t = re.sub(r"(?<=\s)[-/](?=\s)|(?<=[А-Яа-яЁё])/(?=[А-Яа-яЁё])", " ", t)
    t = re.sub(r"\s+([,.!?:;…])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()
