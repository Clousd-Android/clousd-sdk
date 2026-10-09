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
        if p == "/v1/devices/t1/snapshots":
            return self._send(200, {"snapshots": [{"id": "v3", "restorable": True}]})
        if p.startswith("/v1/devices/t1/observe"):
            import base64
            return self._send(200, {"seq": 7, "screen": {"w": 1080, "h": 2400},
                                    "image": {"w": 540, "h": 1200, "format": "jpeg", "data": base64.b64encode(b"JPEGDATA").decode()},
                                    "package": "com.android.settings", "activity": "com.android.settings.Settings",
                                    "ui": [{"text": "Wi-Fi", "class": "TextView", "b": [0, 100, 540, 200], "click": True}]})
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
        if p == "/v1/devices/t1/snapshots/v3/restore":
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

    def test_restore_waits_for_job(self):
        job = self.c.device("t1").restore("v3", timeout=30)
        self.assertEqual(job.state, "done")

    def test_observe_and_act(self):
        d = self.c.device("t1")
        o = d.observe()
        self.assertEqual((o["seq"], o["image_bytes"], d.size()), (7, b"JPEGDATA", (1080, 2400)))
        r = d.act("tap", x=270, y=150, seq=o["seq"])
        self.assertEqual(Fake.calls[-1][2], {"op": "tap", "settle": True, "x": 270, "y": 150, "seq": 7})
        self.assertEqual((r["settled"], r["stale"]), (True, False))

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
