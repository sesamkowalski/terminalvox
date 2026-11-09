"""Voice commands of the bridge itself: switch to another session, list sessions.

A command counts only at the start of a phrase ("switch to aramco"), never in the middle. Session names
are matched fuzzily: speech recognition spells them as it hears them, often in Cyrillic ("Арамко" for
aramco-pbit-cell), so both sides are transliterated and compared by prefix and similarity.
"""

import re
from difflib import SequenceMatcher

from phrases import Phrases

_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


def latin(text: str) -> str:
    """Lowercase Latin letters and digits only: Cyrillic transliterated, separators dropped."""
    return re.sub(r"[^a-z0-9]", "", "".join(_CYR.get(c, c) for c in text.lower()))


def sound(text: str) -> str:
    """A rough phonetic key: spellings that sound alike become equal ("aramco"/"арамко", "scheduling"/"шедулинг")."""
    s = latin(text)
    for a, b in (("sch", "sh"), ("ck", "k"), ("c", "k"), ("ph", "f"), ("w", "v"), ("x", "ks"), ("q", "k"),
                 ("j", "dzh"), ("ey", "ay"), ("ei", "ay"), ("y", "i"), ("e", "i")):
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", s)  # double letters


def parse(text: str, p: Phrases) -> tuple[str, str, bool] | None:
    """(kind, name, strong) if the phrase starts with a command, else None: ("switch", name, strong) or
    ("list", "", True). A weak switch ("go back to …") must not swallow a request when no session matches."""
    t = text.strip()
    for pat, strong in ((p.switch, True), (p.switch_weak, False)):
        if m := pat.match(t):
            name = re.sub(p.name_noise, " ", m.group("name"), flags=re.I)
            name = " ".join(name.strip(" \t.,!?…\"'«»").split())
            # A long "name" is a request ("go to the docs folder and fix the typo"), not a session.
            if name and len(name.split()) <= 4:
                return "switch", name, strong
            return None
    if p.list_sessions.match(t):
        return "list", "", True
    return None


def _keys(session: dict) -> tuple[list[str], list[str]]:
    """Spoken forms a session can be called by: whole names (the session, the tmux window, the folder)
    and the words of those names."""
    window = session.get("where", "").partition(" ")[2]
    folder = session.get("cwd", "").rstrip("/").rpartition("/")[2]
    whole = [session.get("name", ""), window, folder]
    words = [w for v in whole for w in re.split(r"[-_\s.]+", v)]
    keys = lambda vs: [k for k in dict.fromkeys(sound(v) for v in vs) if k]
    return keys(whole), keys(words)


def score(query: str, session: dict) -> float:
    """1.0 for a whole name, 0.95 for a word of it, 0.9 for a prefix, else similarity below that."""
    q = sound(query)
    if not q:
        return 0.0
    whole, words = _keys(session)
    if q in whole:
        return 1.0
    if q in words:
        return 0.95
    if len(q) >= 3 and any(k.startswith(q) for k in whole + words):
        return 0.9
    best = 0.0
    for k in whole + words:
        # Compare with the key's beginning too: "aramco pbit" against "aramcopbitcell".
        best = max(best, SequenceMatcher(None, q, k).ratio(), SequenceMatcher(None, q, k[: len(q)]).ratio() - 0.05)
    return min(best, 0.85)


def find(query: str, sessions: list[dict], aliases: dict[str, str] | None = None,
         threshold: float = 0.65, margin: float = 0.1) -> list[dict]:
    """Sessions matching a spoken name, best first: one means a clear winner, several mean ask again,
    none means nothing similar. aliases maps spoken words to session names ("календарь" -> "scheduling-agent")."""
    alias = (aliases or {}).get(sound(query))
    if alias:
        hits = [s for s in sessions if s.get("name") == alias]
        if hits:
            return hits[:1]
    scored = sorted(((score(query, s), s) for s in sessions), key=lambda x: -x[0])
    scored = [(sc, s) for sc, s in scored if sc >= threshold]
    if not scored:
        return []
    top = scored[0][0]
    # An exact name or word beats anything a tier below it; fuzzy scores need a margin.
    gap = 0.04 if top >= 0.9 else margin
    return [s for sc, s in scored if sc > top - gap]


def pick(text: str, choices: list[dict], p: Phrases) -> dict | None:
    """The answer to "which one?": an ordinal ("the second") or a name among the offered choices."""
    t = text.strip().lower()
    for i, pat in enumerate(p.ordinals[: len(choices)]):
        if re.search(pat, t):
            return choices[i]
    hits = find(t.strip(" .,!?…"), choices)
    return hits[0] if len(hits) == 1 else None


def parse_aliases(spec: str) -> dict[str, str]:
    """SESSION_ALIASES="календарь=scheduling-agent, дэй=day" -> {sound(alias): session name}."""
    out = {}
    for item in spec.split(","):
        alias, _, name = item.partition("=")
        if alias.strip() and name.strip():
            out[sound(alias)] = name.strip()
    return out
