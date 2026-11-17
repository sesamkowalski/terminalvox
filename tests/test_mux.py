import json
import unittest
from unittest import mock

import mux

# Recorded from herdr 0.9.5 (trimmed).
HERDR = {
    ("workspace", "list"): {"workspaces": [{"workspace_id": "w2", "label": "tts"}, {"workspace_id": "w3", "label": "vault"}]},
    ("tab", "list"): {"tabs": [{"tab_id": "w2:t1", "label": "1", "number": 1}, {"tab_id": "w3:t2", "label": "notes", "number": 2}]},
    ("pane", "list"): {"panes": [
        {"pane_id": "w2:p1", "tab_id": "w2:t1", "workspace_id": "w2", "agent": "claude"},
        {"pane_id": "w3:p2", "tab_id": "w3:t2", "workspace_id": "w3", "agent_status": "unknown"},
    ]},
    ("pane", "process-info", "--pane", "w2:p1"): {"process_info": {
        "foreground_processes": [{"pid": 3644422, "name": "claude"}], "shell_pid": 3481205}},
}


def fake_run(*args, input=None):
    if args[0] == "herdr":
        return json.dumps({"id": "cli", "result": HERDR[args[1:]]})
    if args[:2] == ("tmux", "list-panes"):
        return "%0\tvoice:0 claude\n%36\t0:5 day"
    raise AssertionError(args)


class Mux(unittest.TestCase):
    def test_backend_by_id(self):
        self.assertIs(mux.of("w2:p1"), mux.HERDR)
        self.assertIs(mux.of("wD:p1A"), mux.HERDR)
        self.assertIs(mux.of("%64"), mux.TMUX)
        self.assertIs(mux.of("=voice:0.0"), mux.TMUX)

    @mock.patch("mux._run", fake_run)
    def test_herdr_claude_panes(self):
        live = {3644422: {"pid": 3644422, "name": "tts"}, 1: {"pid": 1, "tmux": "x:@1.%36"}}
        self.assertEqual(mux.HERDR.claude_panes(live), [("w2:p1", "tts:1 tts", live[3644422])])
        self.assertEqual(mux.HERDR.where("w3:p2"), "vault:2 notes")

    @mock.patch("mux._run", fake_run)
    def test_tmux_claude_panes(self):
        live = {7: {"pid": 7, "tmux": "0:@5.%36"}, 8: {"pid": 8}}
        self.assertEqual(mux.TMUX.claude_panes(live), [("%36", "0:5 day", live[7])])

    @mock.patch("mux._run")
    def test_keys(self, run):
        mux.TMUX.keys("%1", "ctrl+u", "ctrl+u", "enter")
        run.assert_called_with("tmux", "send-keys", "-t", "%1", "C-u", "C-u", "Enter")
        mux.HERDR.paste("w1:p1", "/tmp/a.png")
        run.assert_called_with("herdr", "pane", "send-text", "w1:p1", "\x1b[200~/tmp/a.png\x1b[201~")


if __name__ == "__main__":
    unittest.main()
