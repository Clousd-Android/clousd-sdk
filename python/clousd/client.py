"""CLOUSD API v1 client: cloud Android phones for people, scripts and AI agents.

    from clousd import Clousd
    c = Clousd()                          # CLOUSD_API_KEY from the environment
    phone = c.device("dev_04f2")
    phone.restore("v3", wait=True)        # reset to a saved state
    png = phone.screenshot()              # look
    phone.tap_text("Add to cart")         # act
    phone.wait_text("Added")              # check

Every call is a plain HTTPS request to https://api.clousd.com/v1 with `Authorization: Bearer cl_live_…`.
Long operations (start, snapshots, restore, clone) return a Job; `wait=True` or `job.wait()` polls it.
"""
from __future__ import annotations

import base64
import os
import struct
import time
from typing import Any, Dict, List, Optional

import requests

DEFAULT_URL = "https://api.clousd.com/v1"


class ClousdError(Exception):
    """An API error: HTTP status, the short error code and the human message from the server."""

    def __init__(self, status: int, error: str, message: str = ""):
        super().__init__(f"{status} {error}: {message}" if message else f"{status} {error}")
        self.status, self.error, self.message = status, error, message


class Clousd:
    """Account-level client. `api_key` defaults to $CLOUSD_API_KEY, `base_url` to $CLOUSD_API_URL."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, timeout: float = 60.0):
        self.api_key = api_key or os.environ.get("CLOUSD_API_KEY", "")
        if not self.api_key:
            raise ValueError("no API key: pass api_key= or set CLOUSD_API_KEY (Settings → API keys in the dashboard)")
        self.base_url = (base_url or os.environ.get("CLOUSD_API_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self._http = requests.Session()
        self._http.headers.update({"Authorization": "Bearer " + self.api_key, "User-Agent": "clousd-python/0.2"})

    # -- transport ---------------------------------------------------------------------------------------------
    def request(self, method: str, path: str, json: Any = None, params: Any = None,
                timeout: Optional[float] = None, raw: bool = False) -> Any:
        """One API call. Retries politely on 429 (60 requests a minute per key) and on a dropped connection."""
        url = self.base_url + "/" + path.lstrip("/")
        for attempt in range(5):
            try:
                r = self._http.request(method, url, json=json, params=params, timeout=timeout or self.timeout)
            except requests.ConnectionError:
                if attempt == 4:
                    raise
                time.sleep(1 + attempt)
                continue
            if r.status_code == 429 and attempt < 4:
                ra = r.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.replace(".", "", 1).isdigit() else min(2 ** (attempt + 1), 30))
                continue
            if r.status_code >= 400:
                try:
                    body = r.json()
                    raise ClousdError(r.status_code, str(body.get("error", "error")), str(body.get("message", "")))
                except ValueError:
                    raise ClousdError(r.status_code, "error", r.text.strip()[:300]) from None
            if raw:
                return r.content
            return r.json() if r.content else {}
        raise ClousdError(429, "rate_limited", "too many requests")

    def __repr__(self) -> str:   # never shows the key
        return f"<Clousd {self.base_url}>"

    # -- account -----------------------------------------------------------------------------------------------
    def devices(self) -> List["Device"]:
        """Devices this key can see."""
        return [Device(self, d["name"], d) for d in self.request("GET", "devices").get("devices", [])]

    def device(self, name: str) -> "Device":
        """A handle to one device (no request is made until you use it)."""
        return Device(self, name)

    def create_device(self, profile: str, network: Optional[Dict[str, Any]] = None, plan: str = "metered",
                      idempotency_key: Optional[str] = None, region: Optional[str] = None, burner: bool = False,
                      wait: bool = True, timeout: float = 600) -> "Device":
        """Create one device on this account (charged like the dashboard: creation fee, first month for plan monthly,
        paid exits metered per GB). `profile` from models(); `network` like {"type": "residential", "country": "US"} or
        {"type": "byo", "url": "socks5://…"}; `idempotency_key` - a retry with the same key within 10 minutes is not
        charged twice (one is generated if not given). With wait=True returns once the device is running."""
        import uuid
        body: Dict[str, Any] = {"profile": profile, "network": network or {"type": "off"}, "plan": plan, "burner": burner,
                                "idempotency_key": idempotency_key or uuid.uuid4().hex}
        if region:
            body["region"] = region
        r = self.request("POST", "devices", json=body, timeout=90)
        d = Device(self, r["name"], {"name": r["name"], "state": r.get("state", "starting")})
        if wait:
            end = time.time() + timeout
            while d.refresh().get("state") in ("starting", "") and time.time() < end:
                time.sleep(5)
        return d

    def job(self, job_id: str) -> "Job":
        return Job(self, self.request("GET", f"jobs/{job_id}").get("job", {"id": job_id}))

    def usage(self) -> Dict[str, Any]:
        return self.request("GET", "usage")

    def network_options(self) -> Dict[str, Any]:
        """What can be chosen when creating a device right now: {types, countries:[{code,name}], plans}."""
        return self.request("GET", "network/options")

    def models(self) -> List[Dict[str, Any]]:
        """Phone models available to create devices from."""
        return self.request("GET", "models").get("models", [])

    def _job(self, resp: Dict[str, Any], wait: bool, timeout: float) -> "Job":
        job = Job(self, resp.get("job") or {"state": "done", "output": resp.get("output", "")})
        return job.wait(timeout) if wait else job


class Job:
    """A long operation. `state` is running, done or failed."""

    def __init__(self, client: Clousd, data: Dict[str, Any]):
        self.client, self.data = client, data

    id = property(lambda self: self.data.get("id", ""))
    state = property(lambda self: self.data.get("state", ""))
    output = property(lambda self: self.data.get("output", ""))

    def refresh(self) -> "Job":
        if self.id:
            self.data = self.client.request("GET", f"jobs/{self.id}").get("job", self.data)
        return self

    def wait(self, timeout: float = 900, every: float = 3.0) -> "Job":
        """Poll until the job finishes; raises ClousdError if it failed or ran out of time."""
        end = time.time() + timeout
        while self.state == "running" and self.id:
            if time.time() > end:
                raise ClousdError(408, "timeout", f"job {self.id} still running after {int(timeout)} s")
            time.sleep(every)
            self.refresh()
        if self.state == "failed":
            raise ClousdError(500, "job_failed", self.output.strip()[-500:])
        return self

    def __repr__(self) -> str:
        return f"<Job {self.id} {self.state}>"


class Device:
    """One cloud phone. Coordinates are device pixels (see `size()`)."""

    def __init__(self, client: Clousd, name: str, data: Optional[Dict[str, Any]] = None):
        self.client, self.name, self.data = client, name, data or {}
        self._size: Optional[tuple] = None

    def _p(self, sub: str = "") -> str:
        return f"devices/{self.name}" + ("/" + sub if sub else "")

    # -- state -------------------------------------------------------------------------------------------------
    def refresh(self) -> Dict[str, Any]:
        self.data = self.client.request("GET", self._p()).get("device", {})
        return self.data

    @property
    def state(self) -> str:
        return (self.data or self.refresh()).get("state", "")

    def start(self, wait: bool = True, timeout: float = 900) -> Job:
        return self.client._job(self.client.request("POST", self._p("start")), wait, timeout)

    def stop(self, wait: bool = True, timeout: float = 300) -> Job:
        return self.client._job(self.client.request("POST", self._p("stop")), wait, timeout)

    def restart(self, wait: bool = True, timeout: float = 900) -> Job:
        return self.client._job(self.client.request("POST", self._p("restart")), wait, timeout)

    def logs(self) -> Any:
        """Recent logcat lines."""
        return self.client.request("GET", self._p("logs"))

    # -- look --------------------------------------------------------------------------------------------------
    def screenshot(self, width: Optional[int] = None) -> bytes:
        """The screen as PNG (full size) or, with `width`, a smaller JPEG."""
        return self.client.request("GET", self._p("screenshot"), params={"w": width} if width else None, raw=True)

    def size(self) -> tuple:
        """Screen size in device pixels, read from a full-size screenshot once."""
        if not self._size:
            png = self.screenshot()
            if png[:8] == b"\x89PNG\r\n\x1a\n":
                self._size = struct.unpack(">II", png[16:24])
            else:
                self._size = (1080, 2400)
        return self._size

    def observe(self, width: int = 540, ui: bool = True) -> Dict[str, Any]:
        """One fresh look at the phone: {seq, screen:{w,h}, image:{w,h,format,data}, image_bytes, package, activity,
        ui:[{text, desc, id, class, b:[x1,y1,x2,y2], click, scroll, focused, ...}], ui_error}.
        Coordinates in `ui` are phone pixels. `width` is a maximum: the image is shrunk by a whole factor, its real
        width is image.w (scale to the phone = screen.w / image.w). Pass `seq` to act() so an action on a screen that
        has changed since is refused (409 stale) instead of landing in the wrong place."""
        d = self.client.request("GET", self._p("observe"), params={"w": width, "ui": 1 if ui else 0}, timeout=90)
        d["image_bytes"] = base64.b64decode(d.get("image", {}).get("data", "") or b"")
        if d.get("screen"):
            self._size = (d["screen"]["w"], d["screen"]["h"])
        return d

    def act(self, op: str, settle: bool = True, seq: Optional[int] = None, **kw: Any) -> Dict[str, Any]:
        """Do one action (same ops as `action`) and, with settle, wait until the screen stops changing (up to 5 s).
        Returns {ok, output, seq, settled, changed, waited_ms}: changed=False means the screen did not change at all
        (the action probably missed). With `seq` from observe() raises ClousdError 409 "stale" and does nothing if the
        screen changed since (another observe or act happened)."""
        body = {"op": op, "settle": settle, **kw}
        if seq is not None:
            body["seq"] = seq
        return self.client.request("POST", self._p("act"), json=body, timeout=(kw.get("ms", 0) / 1000 + 90))

    def screen_text(self) -> List[str]:
        """All texts on the screen (buttons, labels, fields) from the UI hierarchy."""
        return self.action("screen_text").get("texts", [])

    def find_text(self, text: str) -> str:
        """Where `text` is on the screen ("found … at x,y"); raises if it is not there."""
        return self.action("find_text", text=text).get("output", "")

    def installed(self) -> List[str]:
        return self.action("installed").get("packages", [])

    # -- act ---------------------------------------------------------------------------------------------------
    def action(self, op: str, timeout: Optional[float] = None, **kw: Any) -> Dict[str, Any]:
        """Low-level: POST /devices/{name}/action {op, …}."""
        return self.client.request("POST", self._p("action"), json={"op": op, **kw}, timeout=timeout)

    def tap(self, x: int, y: int) -> str:
        return self.action("tap", x=int(x), y=int(y)).get("output", "")

    def tap_norm(self, x: float, y: float) -> str:
        """Tap at a point given as fractions of the screen (0..1), independent of the screenshot size."""
        w, h = self.size()
        return self.tap(round(x * (w - 1)), round(y * (h - 1)))

    def swipe(self, x: int, y: int, x2: int, y2: int, ms: int = 300) -> str:
        return self.action("swipe", x=int(x), y=int(y), x2=int(x2), y2=int(y2), ms=int(ms)).get("output", "")

    def scroll(self, direction: str = "down") -> str:
        """One human-like swipe through a feed: down or up."""
        return self.action("scroll", text=direction).get("output", "")

    def type(self, text: str) -> str:
        """Type into the focused field, any language: ASCII goes through the keyboard, everything else is pasted from
        the phone's clipboard by the agent (the clipboard is cleared afterwards). One line, no control characters."""
        if "\n" in text or "\r" in text or "\x00" in text:
            raise ValueError("type(): one line, no control characters")
        return self.action("text", text=text).get("output", "")

    def key(self, key: str) -> str:
        """home, back, recents, enter, power, tab, del, menu, volume_up, volume_down, wake, sleep or a keycode."""
        return self.action("key", key=str(key)).get("output", "")

    def open_app(self, package: str) -> str:
        return self.action("open_app", package=package).get("output", "")

    def close_app(self, package: str) -> str:
        return self.action("close_app", package=package).get("output", "")

    def open_url(self, url: str) -> str:
        return self.action("url", url=url).get("output", "")

    def tap_text(self, text: str) -> str:
        """Tap the first element whose text contains `text` (case-insensitive)."""
        return self.action("tap_text", text=text).get("output", "")

    def wait_text(self, text: str, timeout: int = 30) -> str:
        """Wait until `text` appears on the screen (up to 120 s); raises if it does not."""
        t = max(1, min(int(timeout), 120))
        return self.action("wait_text", text=text, ms=t * 1000, timeout=t + 60).get("output", "")

    # -- core primitives (same ops and result shapes as the Lusiesta core protocol) --------------------------------
    def op(self, op: str, timeout: Optional[float] = None, **args: Any) -> Dict[str, Any]:
        """One primitive: shell {argv, timeout} → {rc, out_b64, err_b64}; dump → {xml_z}; launch_app {package,
        activity}, start_activity {component}, force_stop {package} → {rc}; foreground → {package}; screenshot →
        {png_b64}; screen → {w, h}; tap {x, y}, long_press {x, y, ms}, swipe {x1, y1, x2, y2, ms}, key {code, meta},
        text {value}, clear_field → {}; ip_check → {ok, ip, country, …}. shell needs ADB access enabled for the device
        (adb_enable) and your IP in its allow list - the same gate as adb shell."""
        r = self.client.request("POST", self._p("op"), json={"op": op, "args": args}, timeout=timeout)
        return r.get("data", {})

    def shell(self, *argv: str, timeout: float = 30) -> tuple:
        """Run a command on the phone as the shell user (like `adb shell`): returns (rc, stdout, stderr) as str."""
        d = self.client.request("POST", self._p("op"), json={"op": "shell", "args": {"argv": list(argv), "timeout": timeout}},
                                timeout=timeout + 30).get("data", {})
        dec = lambda k: base64.b64decode(d.get(k, "") or b"").decode("utf-8", "replace")
        return d.get("rc", -1), dec("out_b64"), dec("err_b64")

    def push(self, data: bytes, remote: str, timeout: float = 900) -> Dict[str, Any]:
        """Put a file on the phone under /sdcard/ or /data/local/tmp/ (like `adb push`; same gate as shell)."""
        url = self.client.base_url + "/" + self._p("files")
        r = self.client._http.put(url, params={"path": remote}, data=data, timeout=timeout)
        if r.status_code >= 400:
            body = r.json() if r.content else {}
            raise ClousdError(r.status_code, str(body.get("error", "error")), str(body.get("message", "")))
        return r.json().get("data", {})

    def adb_enable(self, allow_ips: List[str], ttl_hours: int = 24) -> Dict[str, Any]:
        """Enable ADB access to the device for these IPs/CIDRs (also unlocks shell/push); returns host, port, code."""
        return self.client.request("POST", self._p("adb"), json={"ttl_hours": ttl_hours, "allow_ips": allow_ips})

    # -- small tools: long press, clipboard, notifications, intents, app info, crashes, health ------------------
    def _data(self, op: str, **kw: Any) -> Any:
        r = self.action(op, **kw)
        return r.get("data", r.get("output"))

    def long_press(self, x: int, y: int, ms: int = 800) -> str:
        """Press and hold at a point (device pixels), 400-5000 ms: context menus, text selection, drag handles."""
        return self.action("long_press", x=int(x), y=int(y), ms=int(ms)).get("output", "")

    def clipboard_get(self) -> str:
        """Text on the phone's clipboard."""
        d = self._data("clipboard_get")
        return d.get("text", "") if isinstance(d, dict) else str(d or "")

    def clipboard_set(self, text: str) -> str:
        """Put text on the phone's clipboard (without pasting it)."""
        return self.action("clipboard_set", text=text).get("output", "")

    def notifications(self) -> List[Dict[str, Any]]:
        """Notifications in the shade: pkg, title, text, key, when. Does not open the shade."""
        d = self._data("notifications")
        return d if isinstance(d, list) else []

    def notifications_open(self) -> str:
        return self.action("notifications_open").get("output", "")

    def notifications_close(self) -> str:
        return self.action("notifications_close").get("output", "")

    def notifications_clear(self) -> str:
        """Open the shade and press "Clear all"."""
        return self.action("notifications_clear").get("output", "")

    def intent(self, action: str = "", data: str = "", package: str = "", component: str = "", type: str = "",
               extras: Optional[Dict[str, str]] = None) -> str:
        """Start an activity with an intent, like `am start`: intent("android.intent.action.VIEW", "geo:0,0?q=coffee"),
        or component="com.android.settings/.Settings". `extras` are string extras."""
        it: Dict[str, Any] = {k: v for k, v in dict(action=action, data=data, package=package, component=component, type=type).items() if v}
        if extras:
            it["extras"] = extras
        return self.action("intent", intent=it).get("output", "")

    def settings(self, page: str) -> str:
        """Open a settings page: wifi, bluetooth, display, sound, apps, location, security, date, language, about,
        battery, storage, accessibility, notifications, network, airplane, nfc, developer, accounts, privacy, main -
        or any android.settings.* action."""
        return self.action("settings", text=page).get("output", "")

    def app_info(self, package: str) -> Dict[str, Any]:
        """Installed version, version code, install/update time, installer, running, foreground."""
        d = self._data("app_info", package=package)
        return d if isinstance(d, dict) else {}

    def crashes(self, package: str = "") -> List[Dict[str, Any]]:
        """Recent app crashes, native crashes and ANRs (optionally of one package): kind, process, when, summary, trace."""
        d = self._data("crashes", package=package) if package else self._data("crashes")
        return d if isinstance(d, list) else []

    def screenrecord(self, seconds: int = 10) -> bytes:
        """Record the screen for 1-60 seconds and return the MP4 (what happened during a run, for a person to watch)."""
        return self.client.request("GET", self._p("screenrecord"), params={"seconds": int(seconds)}, timeout=seconds + 120, raw=True)

    def health(self) -> Dict[str, Any]:
        """running, booted, network (type, country, ip, ok) and the latest automatic check (verdict, findings)."""
        d = self._data("health")
        return d if isinstance(d, dict) else {}

    # -- recipes: app scenarios as data, and recording them from the live screen --------------------------------
    def recipe(self, package: str = "", action: str = "", recipe: Optional[Dict[str, Any]] = None,
               vars: Optional[Dict[str, str]] = None, wait: bool = True, timeout: float = 1800) -> Any:
        """Run a recipe: a built-in one (package + action, e.g. "warm") or your own `recipe` dict {app, steps}
        (a recorded draft). The run is started as a job and polled, so a dropped connection never starts it twice.
        Returns {ok, output, job}; with wait=False the Job itself. Raises ClousdError when the recipe fails."""
        body: Dict[str, Any] = {"vars": vars or {}, "async": True}
        if recipe is not None:
            body["recipe"] = recipe
            if package:
                body["package"] = package
        else:
            body.update(package=package, action=action)
        job = self.client._job(self.client.request("POST", self._p("recipe"), json=body), wait, timeout)
        if not wait:
            return job
        out = str(job.output or "")
        ok = job.state != "failed" and not out.startswith("step ") and "failed" not in out.lower()[:60]
        if job.state == "failed":
            raise ClousdError(500, "recipe_failed", out[:300])
        return {"ok": ok, "output": out, "job": job.id}

    def record_start(self) -> Dict[str, Any]:
        """Start recording: what is done on this phone (live view, API) becomes recipe steps."""
        return self.client.request("POST", self._p("record"), json={"op": "start"})

    def record_status(self) -> Dict[str, Any]:
        """{active, count, events, tree_ready}: wait for tree_ready before the next tap to get an exact selector."""
        return self.client.request("GET", self._p("record"))

    def record_stop(self, save: bool = True) -> Dict[str, Any]:
        """Stop recording: {recipe: the draft, file}. Run the draft with recipe(recipe=...)."""
        return self.client.request("POST", self._p("record"), json={"op": "stop", "save": save})

    # -- state between runs ------------------------------------------------------------------------------------
    def snapshots(self) -> List[Dict[str, Any]]:
        """Saved states of this device: id, created, android, current, restorable."""
        return self.client.request("GET", self._p("snapshots")).get("snapshots", [])

    def save_snapshot(self, wait: bool = True, timeout: float = 1800) -> Job:
        return self.client._job(self.client.request("POST", self._p("snapshots")), wait, timeout)

    def restore(self, snapshot_id: str, wait: bool = True, timeout: float = 1800) -> Job:
        """Reset the device to a saved state."""
        return self.client._job(self.client.request("POST", self._p(f"snapshots/{snapshot_id}/restore")), wait, timeout)

    def clone(self, snapshot_id: str, name: str, wait: bool = True, timeout: float = 1800) -> "Device":
        """A new device (`name`: letters and digits, up to 16) from a saved state of this one."""
        self.client._job(self.client.request("POST", self._p(f"snapshots/{snapshot_id}/clone"), json={"name": name}), wait, timeout)
        return Device(self.client, name)

    def __repr__(self) -> str:
        return f"<Device {self.name} {self.data.get('state', '')}>".replace(" >", ">")
