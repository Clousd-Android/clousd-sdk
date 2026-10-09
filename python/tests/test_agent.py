"""Offline tests of the task loop (clousd.agent): a fake phone and a scripted model, no network.

    python -m unittest discover -s python/tests
"""
import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from clousd import ClousdError  # noqa: E402
from clousd.agent import Agent, elements_of  # noqa: E402

JPEG = base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")


def screen(nodes, seq=1, pkg="com.android.settings"):
    return {"seq": seq, "screen": {"w": 1080, "h": 2400}, "image": {"w": 540, "h": 1200, "format": "jpeg",
            "data": base64.b64encode(JPEG).decode()}, "image_bytes": JPEG, "package": pkg, "activity": pkg + ".Main", "ui": nodes}


SETTINGS = [
    {"text": "Settings", "class": "TextView", "b": [40, 100, 400, 160]},
    {"text": "Network & internet", "class": "TextView", "click": True, "b": [40, 300, 1040, 400]},
    {"text": "About phone", "class": "TextView", "click": True, "b": [40, 500, 1040, 600]},
    {"class": "FrameLayout", "b": [0, 0, 1080, 2400]},                       # layout without meaning: dropped
    {"id": "com.android.settings:id/search", "class": "EditText", "focus": True, "click": True, "b": [40, 200, 1040, 260]},
]


class FakePhone:
    def __init__(self, screens):
        self.screens, self.i, self.acts, self.stale_once = screens, 0, [], False

    def observe(self, width=540, ui=True):
        s = self.screens[min(self.i, len(self.screens) - 1)]
        return dict(s)

    def act(self, op, settle=True, seq=None, **kw):
        if self.stale_once:
            self.stale_once = False
            raise ClousdError(409, "stale", "the screen changed")
        self.acts.append((op, kw))
        self.i += 1
        return {"ok": True, "output": "ok", "changed": True}

    def installed(self):
        return ["com.android.settings", "com.android.chrome"]


class ScriptedModel:
    def __init__(self, calls):
        self.calls, self.seen = list(calls), []

    def chat(self, messages, tools=None):
        self.seen.append(messages)
        name, args = self.calls.pop(0)
        if name == "@text":   # a model without tool calling answers with JSON in the text
            return {"choices": [{"message": {"content": json.dumps(args)}}]}
        return {"choices": [{"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": json.dumps(args)}}]}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


class TestAgent(unittest.TestCase):
    def test_elements_numbered_and_filtered(self):
        els = elements_of(screen(SETTINGS))
        labels = [e.label for e in els]
        self.assertEqual(labels, ["Settings", "search", "Network & internet", "About phone"])   # top to bottom, layout dropped
        self.assertIn("input", els[1].flags)
        self.assertEqual(els[3].center, (540, 550))

    def test_tap_element_then_done(self):
        phone = FakePhone([screen(SETTINGS), screen([{"text": "Android version", "b": [0, 0, 500, 50]}, {"text": "14", "b": [0, 60, 100, 100]}])])
        model = ScriptedModel([("tap", {"element": 3}), ("done", {"answer": "Android 14"})])
        res = Agent(phone, model, log=None).run("Android version?")
        self.assertEqual(res.status, "done")
        self.assertEqual(res.answer, "Android 14")
        self.assertEqual(phone.acts[0], ("tap", {"x": 540, "y": 550}))
        # the model got the image and the numbered list
        content = model.seen[0][1]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertIn("[3] 'About phone'", content[1]["text"])

    def test_json_in_text(self):
        phone = FakePhone([screen(SETTINGS)])
        model = ScriptedModel([("@text", {"tool": "key", "args": {"name": "back"}}), ("done", {"answer": "ok"})])
        res = Agent(phone, model, log=None).run("go back")
        self.assertEqual(res.status, "done")
        self.assertEqual(phone.acts[0], ("key", {"key": "back"}))

    def test_type_needs_focused_field(self):
        phone = FakePhone([screen(SETTINGS)])
        model = ScriptedModel([("type", {"text": "wifi"}), ("fail", {"reason": "x"})])
        res = Agent(phone, model, log=None).run("search wifi")
        self.assertIn("no input field is focused", res.steps[0]["result"])
        self.assertEqual(phone.acts, [])

    def test_waits_limited_and_repeats_flagged(self):
        phone = FakePhone([screen(SETTINGS)])
        model = ScriptedModel([("wait", {"seconds": 1}), ("wait", {"seconds": 1}), ("wait", {"seconds": 1}), ("done", {"answer": "x"})])
        res = Agent(phone, model, log=None).run("wait")
        self.assertIn("waited twice already", res.steps[2]["result"])

    def test_stale_is_not_an_error(self):
        phone = FakePhone([screen(SETTINGS), screen(SETTINGS)])
        phone.stale_once = True
        model = ScriptedModel([("tap", {"element": 2}), ("tap", {"element": 2}), ("done", {"answer": "ok"})])
        res = Agent(phone, model, log=None).run("tap")
        self.assertIn("looking again", res.steps[0]["result"])
        self.assertEqual(res.status, "done")

    def test_bad_element_number(self):
        phone = FakePhone([screen(SETTINGS)])
        model = ScriptedModel([("tap", {"element": 40}), ("done", {"answer": "ok"})])
        res = Agent(phone, model, log=None).run("tap")
        self.assertIn("no element 40", res.steps[0]["result"])


if __name__ == "__main__":
    unittest.main()
