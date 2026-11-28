"""Offline test of clousd.explore on a fake two-screen app; destructive buttons must never be tapped."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from clousd.explore import explore  # noqa: E402

PKG = "com.example.app"
HOME = [
    {"text": "Settings", "click": True, "id": PKG + ":id/settings", "b": [0, 100, 1080, 200], "pkg": PKG},
    {"text": "Delete account", "click": True, "b": [0, 300, 1080, 400], "pkg": PKG},
    {"text": "Help", "click": True, "b": [0, 500, 1080, 600], "pkg": PKG},
]
SETTINGS = [
    {"text": "Dark mode", "click": True, "b": [0, 100, 1080, 200], "pkg": PKG},
    {"text": "Sign out", "click": True, "b": [0, 300, 1080, 400], "pkg": PKG},
]
DIALOG = [
    {"text": "Rate us?", "b": [0, 100, 1080, 200], "pkg": PKG},
    {"text": "Not now", "click": True, "b": [0, 300, 500, 400], "pkg": PKG},
]


class FakeApp:
    def __init__(self):
        self.stack = ["home"]
        self.tapped = []

    def _ui(self):
        return {"home": HOME, "settings": SETTINGS, "help": DIALOG, "dark": SETTINGS}[self.stack[-1]]

    def observe(self, width=360, ui=True):
        return {"seq": 1, "package": PKG, "activity": PKG + "." + self.stack[-1], "ui": self._ui(),
                "screen": {"w": 1080, "h": 2400}, "image": {"w": 360}}

    def action(self, op, **kw):
        if op == "close_app":
            self.stack = ["home"]
        return {"ok": True}

    def act(self, op, **kw):
        if op == "open_app":
            self.stack = ["home"]
        elif op == "key":
            if len(self.stack) > 1:
                self.stack.pop()
        elif op == "tap":
            hit = next(n for n in self._ui() if n["b"][1] <= kw["y"] <= n["b"][3])
            self.tapped.append(hit["text"])
            nxt = {"Settings": "settings", "Help": "help", "Dark mode": None, "Not now": None}.get(hit["text"])
            if nxt:
                self.stack.append(nxt)
        return {"ok": True, "changed": True}


class TestExplore(unittest.TestCase):
    def test_map_and_safety(self):
        app = FakeApp()
        g = explore(app, PKG, depth=2, log=None)
        acts = sorted(s["activity"] for s in g["screens"])
        self.assertEqual(acts, [PKG + ".help", PKG + ".home", PKG + ".settings"])
        self.assertNotIn("Delete account", app.tapped)
        self.assertNotIn("Sign out", app.tapped)
        self.assertIn("settings", g["app_pack"]["elements"])           # unique id → id selector
        self.assertEqual(g["app_pack"]["elements"]["settings"], [{"id": "settings"}])
        self.assertTrue(any(p["tap"] == {"text": "Not now"} for p in g["app_pack"]["popups"]))


if __name__ == "__main__":
    unittest.main()
