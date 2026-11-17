"""Stress marks for words the synthesizer gets wrong ("перезапущу" with the stress in the middle).

The list is a text file, one word form per line, with the stressed vowel in capitals: "перезапущУ".
Before synthesis each listed word gets a combining acute accent after that vowel ("перезапущу́"):
Google Chirp 3 HD follows it, Azure ignores it. Silero wants a "+" before the vowel instead ("перезапущ+у").
"""

import re
from pathlib import Path

ACUTE = "́"


def load(path: str | Path) -> dict[str, int]:
    """{word in lower case: index of the stressed letter}; a missing file is an empty list."""
    words = {}
    p = Path(path)
    if not p.exists():
        return words
    for line in p.read_text(encoding="utf-8").splitlines():
        w = line.split("#", 1)[0].strip()
        caps = [i for i, c in enumerate(w) if c.isupper()]
        if w and len(caps) == 1:
            words[w.lower()] = caps[0]
    return words


def stress(text: str, words: dict[str, int], plus: bool = False) -> str:
    """The text with stress marks in the listed words, the case of the text kept; plus=True for Silero."""
    if not words:
        return text

    def mark(m: re.Match) -> str:
        w = m.group(0)
        i = words.get(w.lower())
        if i is None:
            return w
        return w[:i] + "+" + w[i:] if plus else w[: i + 1] + ACUTE + w[i + 1 :]

    return re.sub(r"(?<!\+)[А-Яа-яЁё]+(?![+́])", mark, text)
