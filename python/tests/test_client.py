"""Offline tests of the clousd client against a tiny fake /v1 server (no real phones are touched).

    python -m unittest discover -s sdk/python/tests
"""
import json
import os
import struct
import sys
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from clousd import Clousd, ClousdError  # noqa: E402


def tiny_png(w, h):
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class Fake(BaseHTTPRequestHandler):
    calls = []
    job_polls = 0
    run_polls = 0
    rate_limited_once = False

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None, ctype="application/json", headers=None):
        body = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else None
        Fake.calls.append((self.command, self.path, body, self.headers.get("Authorization")))
        return body

    def do_GET(self):
        self._record()
        p = self.path
        if p == "/v1/devices":
            return self._send(200, {"devices": [{"name": "t1", "state": "running"}]})
        if p.startswith("/v1/devices/t1/screenshot"):
            return self._send(200, raw=tiny_png(1080, 2400), ctype="image/png")
        if p.startswith("/v1/devices/t1/observe"):
            import base64
            return self._send(200, {"seq": 7, "screen": {"w": 1080, "h": 2400},
                                    "image": {"w": 540, "h": 1200, "format": "jpeg", "data": base64.b64encode(b"JPEGDATA").decode()},
                                    "package": "com.android.settings", "activity": "com.android.settings.Settings",
                                    "ui": [{"text": "Wi-Fi", "class": "TextView", "b": [0, 100, 540, 200], "click": True}]})
        if p.startswith("/v1/runs/r1/events"):
            ev = ("id: 1\nevent: step\ndata: {\"n\": 1, \"tool\": \"tap\", \"result\": \"ok\"}\n\n"
                  ": ping\n\n"
                  "id: 2\nevent: end\ndata: {\"status\": \"completed\"}\n\n").encode()
            return self._send(200, raw=ev, ctype="text/event-stream")
        if p == "/v1/runs/r1":
            Fake.run_polls += 1
            st = "completed" if Fake.run_polls >= 2 else "running"
            return self._send(200, {"run": {"id": "r1", "device": "t1", "status": st, "answer": "Android 15" if st == "completed" else ""}})
        if p == "/v1/runs/r2":
            return self._send(200, {"run": {"id": "r2", "status": "paused", "card": {"id": "c1", "kind": "question", "question": "Which?"}}})
        if p.startswith("/v1/credentials"):
            return self._send(200, {"credentials": [{"id": 4, "package": "com.example", "name": "work", "fields": ["username", "password"]}]})
        if p == "/v1/jobs/j1":
            Fake.job_polls += 1
            return self._send(200, {"job": {"id": "j1", "state": "done" if Fake.job_polls >= 2 else "running"}})
        return self._send(404, {"error": "not_found", "message": "no such device"})

    def do_POST(self):
        body = self._record()
        p = self.path
        if p == "/v1/devices/t1/action":
            if body["op"] == "screen_text" and not Fake.rate_limited_once:
                Fake.rate_limited_once = True
                return self._send(429, {"error": "rate_limited"}, headers={"Retry-After": "0"})
            if body["op"] == "screen_text":
                return self._send(200, {"ok": True, "texts": ["Settings", "Wi-Fi"]})
            return self._send(200, {"ok": True, "output": "done " + body["op"]})
        if p == "/v1/devices/t1/act":
            return self._send(200, {"ok": True, "output": "", "seq": 7, "stale": body.get("seq", 7) != 7,
                                    "settled": True, "waited_ms": 600})
        if p == "/v1/runs":
            return self._send(201, {"ok": True, "run": {"id": "r1", "device": "t1", "status": "queued"}})
        if p == "/v1/runs/r2/answer":
            return self._send(200, {"ok": True})
        if p == "/v1/devices/t1/reset":
            return self._send(202, {"ok": True, "job": {"id": "j1", "state": "running"}})
        return self._send(400, {"error": "bad_request", "message": "op"})


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.c = Clousd(api_key="cl_live_test", base_url=f"http://127.0.0.1:{cls.srv.server_port}/v1")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_devices_and_auth(self):
        ds = self.c.devices()
        self.assertEqual([d.name for d in ds], ["t1"])
        self.assertEqual(Fake.calls[-1][3], "Bearer cl_live_test")
        self.assertNotIn("cl_live_test", repr(self.c))

    def test_actions_shape(self):
        d = self.c.device("t1")
        d.tap(10, 20)
        self.assertEqual(Fake.calls[-1][2], {"op": "tap", "x": 10, "y": 20})
        d.wait_text("Done", timeout=500)
        self.assertEqual(Fake.calls[-1][2], {"op": "wait_text", "text": "Done", "ms": 120000})
        d.scroll("up")
        self.assertEqual(Fake.calls[-1][2], {"op": "scroll", "text": "up"})
        d.open_url("https://example.com")
        self.assertEqual(Fake.calls[-1][2], {"op": "url", "url": "https://example.com"})

    def test_screen_text_retries_429(self):
        self.assertEqual(self.c.device("t1").screen_text(), ["Settings", "Wi-Fi"])

    def test_size_from_png_and_tap_norm(self):
        d = self.c.device("t1")
        self.assertEqual(d.size(), (1080, 2400))
        d.tap_norm(0.5, 0.5)
        self.assertEqual(Fake.calls[-1][2], {"op": "tap", "x": 540, "y": 1200})

    def test_reset_waits_for_job(self):
        job = self.c.device("t1").reset(timeout=30)
        self.assertEqual(job.state, "done")

    def test_observe_and_act(self):
        d = self.c.device("t1")
        o = d.observe()
        self.assertEqual((o["seq"], o["image_bytes"], d.size()), (7, b"JPEGDATA", (1080, 2400)))
        r = d.act("tap", x=270, y=150, seq=o["seq"])
        self.assertEqual(Fake.calls[-1][2], {"op": "tap", "settle": True, "x": 270, "y": 150, "seq": 7})
        self.assertEqual((r["settled"], r["stale"]), (True, False))

    def test_run_task(self):
        run = self.c.run("Tell me the Android version", device="t1", credentials=[{"package": "com.example", "name": "work", "id": 4}],
                         output_schema={"type": "object"}, idempotency_key="k1")
        call = [c for c in Fake.calls if c[1] == "/v1/runs"][-1]
        self.assertEqual(call[2], {"task": "Tell me the Android version", "model": {"provider": "clousd"}, "source": "sdk",
                                   "device": "t1", "options": {"mode": "auto", "output_schema": {"type": "object"}},
                                   "credentials": [{"package": "com.example", "name": "work"}]})
        self.assertEqual((run.id, run.status), ("r1", "queued"))
        run.wait(timeout=30, every=0.01)
        self.assertEqual((run.status, run.answer), ("completed", "Android 15"))

    def test_run_events(self):
        evs = list(self.c.get_run("r1").events())
        self.assertEqual([e["event"] for e in evs], ["step", "end"])
        self.assertEqual(evs[0]["data"]["tool"], "tap")
        self.assertEqual(evs[1]["n"], 2)

    def test_reply_to_card(self):
        run = self.c.get_run("r2")
        run.reply("the first one")
        self.assertEqual(Fake.calls[-2][2], {"card": "c1", "answer": "the first one"})

    def test_credentials_list(self):
        self.assertEqual(self.c.credentials("com.example")[0]["name"], "work")
        self.assertIn("package=com.example", Fake.calls[-1][1])

    def test_type_any_language(self):
        self.c.device("t1").type("こんにちは")
        self.assertEqual(Fake.calls[-1][2], {"op": "text", "text": "こんにちは"})
        with self.assertRaises(ValueError):
            self.c.device("t1").type("two\nlines")

    def test_errors_are_typed(self):
        with self.assertRaises(ClousdError) as e:
            self.c.device("nope").refresh()
        self.assertEqual((e.exception.status, e.exception.error), (404, "not_found"))


if __name__ == "__main__":
    unittest.main()
