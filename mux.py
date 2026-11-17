"""Terminal multiplexers that host Claude Code sessions: tmux and herdr (https://herdr.dev).

Both are driven through their CLIs (a call takes about 3 ms either way). A pane id says which one owns
it: herdr ids look like "w2:p1" or "wD:p1", everything else ("%64", "=voice:0.0") is a tmux target. Sessions of
both show up in one list; a multiplexer that isn't installed or running just contributes nothing.

Keys are herdr's names ("esc", "ctrl+u", "enter"); the tmux backend translates them.
"""

import json
import re
import shutil
import subprocess


def _run(*args: str, input: str | None = None) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True, input=input).stdout.strip()


class Tmux:
    KEYS = {"esc": "Escape", "ctrl+u": "C-u", "enter": "Enter"}

    def available(self) -> bool:
        return shutil.which("tmux") is not None

    def resolve(self, target: str) -> str:
        """The pane id (%NN) of a target: tmux may match a name like "voice" by prefix against another
        window, and an id won't move to a different pane if the session dies."""
        pane = _run("tmux", "display", "-p", "-t", target, "#{pane_id}")
        if not pane.startswith("%"):
            raise RuntimeError(f"tmux pane {target!r} not found")
        return pane

    def where(self, pane: str) -> str:
        return _run("tmux", "display", "-p", "-t", pane, "#{session_name}:#{window_index}.#{pane_index}")

    def claude_panes(self, live: dict[int, dict]) -> list[tuple[str, str, dict]]:
        """(pane, "session:window window_name", session json) of the live claude processes in tmux:
        claude records its pane in the "tmux" field."""
        panes = dict(line.split("\t", 1) for line in _run(
            "tmux", "list-panes", "-a", "-F", "#{pane_id}\t#{session_name}:#{window_index} #{window_name}"
        ).splitlines())
        out = []
        for d in live.values():
            pane = (d.get("tmux") or "").rpartition(".")[2]
            if pane in panes:
                out.append((pane, panes[pane], d))
        return out

    def pids(self, pane: str) -> list[str]:
        """The pane's process and its children: claude is either of them (a pane may run a shell)."""
        pid = _run("tmux", "display", "-p", "-t", pane, "#{pane_pid}")
        children = subprocess.run(["pgrep", "-P", pid], capture_output=True, text=True).stdout
        return [pid, *children.split()]

    def screen(self, pane: str) -> str:
        return _run("tmux", "capture-pane", "-p", "-t", pane)

    def keys(self, pane: str, *keys: str):
        _run("tmux", "send-keys", "-t", pane, *[self.KEYS[k] for k in keys])

    def text(self, pane: str, text: str):
        _run("tmux", "send-keys", "-t", pane, "-l", text)

    def paste(self, pane: str, text: str):
        """As a bracketed paste."""
        _run("tmux", "load-buffer", "-b", "terminalvox-paste", "-", input=text)
        _run("tmux", "paste-buffer", "-p", "-d", "-b", "terminalvox-paste", "-t", pane)


class Herdr:
    """The default herdr session, or the one HERDR_SOCKET_PATH points to."""

    # Numbers in herdr ids go on in letters: w9, wA, …, wZ, w11 (herdr's workspace.rs).
    ID = re.compile(r"^w[0-9A-Za-z]+:p[0-9A-Za-z]+$")

    def available(self) -> bool:
        return shutil.which("herdr") is not None

    def _api(self, *args: str) -> dict:
        return json.loads(_run("herdr", *args))["result"]

    def resolve(self, target: str) -> str:
        return self._api("pane", "get", target)["pane"]["pane_id"]

    def where(self, pane: str) -> str:
        return self._names().get(pane, pane)

    def _names(self) -> dict[str, str]:
        """pane -> "workspace:tab name", like tmux's "session:window window_name". The name is the tab's
        label, or the workspace's when the tab has only its default number for a label."""
        ws = {w["workspace_id"]: w["label"] for w in self._api("workspace", "list")["workspaces"]}
        tabs = {t["tab_id"]: t for t in self._api("tab", "list")["tabs"]}
        out = {}
        for p in self._api("pane", "list")["panes"]:
            t, label = tabs.get(p["tab_id"], {}), ws.get(p["workspace_id"], p["workspace_id"])
            tab = t.get("label") or ""
            out[p["pane_id"]] = f"{label}:{t.get('number', '')} {label if tab.isdigit() or not tab else tab}"
        return out

    def claude_panes(self, live: dict[int, dict]) -> list[tuple[str, str, dict]]:
        """Panes where herdr sees claude, matched to the session json by the claude process id
        (no "tmux"-like field there; this works without herdr's Claude integration too)."""
        names = self._names()
        out = []
        for p in self._api("pane", "list")["panes"]:
            if p.get("agent") != "claude":
                continue
            d = next((live[int(pid)] for pid in self.pids(p["pane_id"]) if int(pid) in live), None)
            if d:
                out.append((p["pane_id"], names.get(p["pane_id"], p["pane_id"]), d))
        return out

    def pids(self, pane: str) -> list[str]:
        info = self._api("pane", "process-info", "--pane", pane)["process_info"]
        pids = [x["pid"] for x in info.get("foreground_processes", [])] + [info.get("shell_pid")]
        return [str(x) for x in pids if x]

    def screen(self, pane: str) -> str:
        return _run("herdr", "pane", "read", pane, "--source", "visible")

    def keys(self, pane: str, *keys: str):
        _run("herdr", "pane", "send-keys", pane, *keys)

    def text(self, pane: str, text: str):
        _run("herdr", "pane", "send-text", pane, text)

    def paste(self, pane: str, text: str):
        """send-text writes raw bytes; claude always turns bracketed paste on."""
        self.text(pane, f"\x1b[200~{text}\x1b[201~")


TMUX, HERDR = Tmux(), Herdr()
ALL = [TMUX, HERDR]


def of(target: str) -> Tmux | Herdr:
    return HERDR if Herdr.ID.match(target) else TMUX


def current(pane: str) -> str:
    """The pane's id now: herdr gives a pane moved to another workspace a new id (the old one still
    resolves to it). As is if the pane is gone."""
    try:
        return of(pane).resolve(pane)
    except (subprocess.CalledProcessError, ValueError, KeyError, RuntimeError):
        return pane
