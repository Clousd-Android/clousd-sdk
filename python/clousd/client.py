"""CLOUSD API v1 client: cloud Android phones for people, scripts and AI agents.

    from clousd import Clousd
    c = Clousd()                          # CLOUSD_API_KEY from the environment
    run = c.run("Open Settings and tell me the Android version", device="dev_04f2")
    print(run.status, run.answer)         # completed  "Android 15"

    phone = c.device("dev_04f2")          # or drive the screen yourself
    png = phone.screenshot()
    phone.tap_text("Wi-Fi")

Every call is a plain HTTPS request to https://api.clousd.com/v1 with `Authorization: Bearer cl_live_…`.
A task (`run`) is done on the phone by Clousd's own agent; follow it with `run.events()` or `run.wait()`.
Long device operations (start, snapshots, restore, clone) return a Job; `wait=True` or `job.wait()` polls it.
"""
from __future__ import annotations

import base64
import os
import struct
import time
import json as _json
from typing import Any, Dict, Iterator, List, Optional

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
        self._http.headers.update({"Authorization": "Bearer " + self.api_key, "User-Agent": "clousd-python/1.0"})

    # -- transport ---------------------------------------------------------------------------------------------
    def request(self, method: str, path: str, json: Any = None, params: Any = None,
                timeout: Optional[float] = None, raw: bool = False, headers: Optional[Dict[str, str]] = None) -> Any:
        """One API call. Retries politely on 429 (60 requests a minute per key) and on a dropped connection."""
        url = self.base_url + "/" + path.lstrip("/")
        for attempt in range(5):
            try:
                r = self._http.request(method, url, json=json, params=params, timeout=timeout or self.timeout, headers=headers)
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

    def find(self, label: str) -> "Device":
        """A device by the name people see in the dashboard ("Anna IG", "Pixel 7 #3", case-insensitive), its number or
        its API name. Raises ClousdError 404 when nothing matches."""
        want = str(label).strip().lower().lstrip("#")
        for d in self.devices():
            if want in (d.name.lower(), str(d.data.get("label") or "").lower(), str(d.data.get("number") or "")):
                return d
        raise ClousdError(404, "no_such_device", f"no device named {label!r}")

    def create_device(self, profile: str, network: Optional[Dict[str, Any]] = None, plan: str = "metered",
                      idempotency_key: Optional[str] = None, region: Optional[str] = None, burner: bool = False,
                      label: str = "", wait: bool = True, timeout: float = 600) -> "Device":
        """Create one device on this account (charged like the dashboard: creation fee, first month for plan monthly,
        paid exits metered per GB). `profile` from models(); `network` like {"type": "residential", "country": "US"} or
        {"type": "byo", "url": "socks5://…"}; `idempotency_key` - a retry with the same key within 10 minutes is not
        charged twice (one is generated if not given). With wait=True returns once the device is running."""
        import uuid
        body: Dict[str, Any] = {"profile": profile, "network": network or {"type": "off"}, "plan": plan, "burner": burner,
                                "idempotency_key": idempotency_key or uuid.uuid4().hex}
        if region:
            body["region"] = region
        if label:
            body["label"] = label
        r = self.request("POST", "devices", json=body, timeout=90)
        d = Device(self, r["name"], {"name": r["name"], "state": r.get("state", "starting")})
        if wait:
            end = time.time() + timeout
            while d.refresh().get("state") in ("starting", "") and time.time() < end:
                time.sleep(5)
        return d

    def job(self, job_id: str) -> "Job":
        return Job(self, self.request("GET", f"jobs/{job_id}").get("job", {"id": job_id}))

    def balance(self) -> Dict[str, Any]:
        """The account's money: balance, spend_per_day (running per-minute phones + proxy traffic of the last 24 hours),
        runs_out_in_hours, monthly renewals within 7 days, prices, top_up link and ready warnings to show the user.
        Check it before starting phones or long runs."""
        return self.request("GET", "balance")

    def usage(self) -> Dict[str, Any]:
        return self.request("GET", "usage")

    def network_options(self) -> Dict[str, Any]:
        """What can be chosen when creating a device right now: {types, countries:[{code,name}], plans}."""
        return self.request("GET", "network/options")

    def models(self) -> List[Dict[str, Any]]:
        """Phone models available to create devices from."""
        return self.request("GET", "models").get("models", [])

    # -- tasks: a goal in plain words, done on the phone by Clousd's agent ------------------------------------------
    def run(self, task: str, device: Any = None, devices: Optional[List[Any]] = None,
            credentials: Optional[List[Any]] = None, output_schema: Optional[Dict[str, Any]] = None,
            mode: str = "auto", max_steps: Optional[int] = None, timeout: Optional[int] = None,
            options: Optional[Dict[str, Any]] = None, idempotency_key: Optional[str] = None,
            wait: bool = False, wait_timeout: float = 1800) -> "Run":
        """Give one phone (`device`) or several (`devices`, up to 32 - one run each, all started at once) a task in plain
        words: "open Settings and tell me the Android version", "sign in to the app with the saved login". Clousd's agent
        looks at the screen, acts, and finishes with an answer.

        credentials: saved logins to use, as {"package": ..., "name": ...} dicts or credential dicts from credentials();
            their values are typed on the phone without the model or you seeing them.
        output_schema: a JSON Schema; the answer also comes back as `run.structured_output` in that shape.
        mode: "auto" (default) - the agent does not stop to ask; "ask" - it asks before risky steps (`run.card`, answer it).
        max_steps / timeout (seconds) / options: limits and the rest of the options (reasoning, vision, recording...).
        idempotency_key: a retry with the same key and body returns the same run instead of starting a new one.
        Returns a Run right away (status queued / running); `wait=True` returns when it has finished."""
        body: Dict[str, Any] = {"task": task, "model": {"provider": "clousd"}, "source": "sdk"}
        if devices:
            body["devices"] = _names(devices)
        if device is not None:
            body["device"] = device.name if isinstance(device, Device) else str(device)
        opts: Dict[str, Any] = dict(options or {})
        opts["mode"] = mode
        if max_steps:
            opts["max_steps"] = int(max_steps)
        if timeout:
            opts["timeout"] = int(timeout)
        if output_schema:
            opts["output_schema"] = output_schema
        body["options"] = opts
        if credentials:
            body["credentials"] = [{"package": str(x.get("package", "")), "name": str(x.get("name", ""))} for x in credentials]
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        r = self.request("POST", "runs", json=body, timeout=90, headers=headers)
        run = Run(self, r.get("run", {}), r.get("runs"))
        return run.wait(wait_timeout) if wait else run

    def runs(self, status: str = "", device: Any = None, since: str = "", limit: int = 50) -> List["Run"]:
        """Recent runs of this key, newest first. status: queued | running | paused | completed | failed | cancelled;
        since: RFC 3339 time."""
        params: Dict[str, Any] = {"limit": int(limit)}
        if status:
            params["status"] = status
        if device is not None:
            params["device"] = device.name if isinstance(device, Device) else str(device)
        if since:
            params["since"] = since
        return [Run(self, x) for x in self.request("GET", "runs", params=params).get("runs", [])]

    def get_run(self, run_id: str) -> "Run":
        return Run(self, self.request("GET", f"runs/{run_id}").get("run", {"id": run_id}))

    # -- schedules: a task that runs by itself on days of the week ---------------------------------------------------
    def schedules(self) -> List[Dict[str, Any]]:
        """Schedules of this key: id, name, phones, task, at (HH:MM), days (1 = Monday … 7; empty = every day), tz,
        enabled, last_run and last_runs (ids of the runs it started last time)."""
        return self.request("GET", "schedules").get("schedules", [])

    def add_schedule(self, task: str, devices: List[Any], at: str = "", days: Optional[List[int]] = None, tz: str = "UTC",
                     name: str = "", credentials: Optional[List[Any]] = None,
                     options: Optional[Dict[str, Any]] = None, every: str = "day", trigger: str = "",
                     more: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Run `task` on `devices` at `at` (HH:MM in time zone `tz`, e.g. "Europe/Berlin") on `days` (1-7, empty = every
        day). every="hour" runs it every hour at the minute of `at`. trigger="api" never runs by time - start it with
        run_schedule(). `more` adds other times to the same task: [{"at": "18:00", "days": [6, 7]}, {"at": "00:30", "every":
        "hour"}]. Each firing starts runs like run(); find them with runs() or the schedule's last_runs."""
        body: Dict[str, Any] = {"name": name or task[:60], "phones": _names(devices), "task": task, "at": at,
                                "days": list(days or []), "tz": tz, "model": {"provider": "clousd"},
                                "options": dict(options or {}), "every": every, "trigger": trigger,
                                "more": list(more or [])}
        if credentials:
            body["credentials"] = [{"package": str(x.get("package", "")), "name": str(x.get("name", ""))} for x in credentials]
        return self.request("POST", "schedules", json=body).get("schedule", {})

    def toggle_schedule(self, schedule_id: str) -> Dict[str, Any]:
        """Pause a schedule or turn it back on."""
        return self.request("POST", f"schedules/{schedule_id}/toggle", json={})

    def delete_schedule(self, schedule_id: str) -> Dict[str, Any]:
        return self.request("POST", f"schedules/{schedule_id}/delete", json={})

    def run_schedule(self, schedule_id: str) -> List[str]:
        """Start a schedule now (the only way for trigger="api"); returns the ids of the runs it started."""
        return self.request("POST", f"schedules/{schedule_id}/run", json={}).get("runs", [])

    # -- network exits: proxies of the account ------------------------------------------------------------------
    def proxies(self) -> List[Dict[str, Any]]:
        """Your own proxies and Clousd exits of the account: id, source (own | clousd), type, country, address (no
        password), check, rotatable, devices (which phones use it). Same list as the dashboard's Network page."""
        return self.request("GET", "proxies").get("proxies", [])

    def add_proxy(self, proxy: str = "", type: str = "", country: str = "", ip_type: str = "static",
                  rotate_url: str = "", name: str = "", group: str = "") -> Dict[str, Any]:
        """Add your own proxy (`proxy` as host:port:user:pass, user:pass@host:port or socks5://...; it is checked at once)
        or a Clousd exit (`type` residential | mobile | isp | datacenter + `country`, paid per GB). Returns {proxy, check}."""
        body: Dict[str, Any] = {"name": name, "group": group}
        if proxy:
            body.update(proxy=proxy, ip_type=ip_type, rotate_url=rotate_url)
        else:
            body.update(type=type, country=country)
        return self.request("POST", "proxies", json=body, timeout=120)

    def check_proxy(self, proxy_id: int) -> Dict[str, Any]:
        return self.request("POST", f"proxies/{int(proxy_id)}/check", json={}, timeout=60)

    def bind_proxy(self, proxy_id: int, devices: List[Any]) -> Dict[str, Any]:
        """Put devices on this proxy (time zone, locale and SIM follow the exit; a minute or two). Stopped devices get it
        when they start."""
        return self.request("POST", f"proxies/{int(proxy_id)}/bind", json={"devices": _names(devices)}, timeout=90)

    def unbind_proxy(self, devices: List[Any]) -> Dict[str, Any]:
        """Take devices off their proxy. A device without a proxy has no internet until you bind one."""
        return self.request("POST", "proxies/unbind", json={"devices": _names(devices)}, timeout=90)

    def rotate_proxy(self, proxy_id: int) -> Dict[str, Any]:
        """New IP: your proxy's rotation link, or a new session of a Clousd exit, for every running device on it."""
        return self.request("POST", f"proxies/{int(proxy_id)}/rotate", json={}, timeout=120)

    def delete_proxy(self, proxy_id: int) -> Dict[str, Any]:
        return self.request("DELETE", f"proxies/{int(proxy_id)}")

    # -- saved logins: typed on the phone during a run, never shown --------------------------------------------------
    def credentials(self, package: str = "") -> List[Dict[str, Any]]:
        """Saved logins (Tasks → Sign-in in the dashboard): id, package, name and which fields are set (username, email,
        password, phone, totp, token, backup). Values never leave Clousd - pass a login to run(credentials=[...])."""
        return self.request("GET", "credentials", params={"package": package} if package else None).get("credentials", [])

    def add_credential(self, package: str, name: str, fields: Dict[str, str]) -> Dict[str, Any]:
        """Save a login for an app: fields like {"username": "anna", "password": "...", "totp": "otpauth://... or the secret"}."""
        return self.request("POST", "credentials", json={"package": package, "name": name, "fields": fields}).get("credential", {})

    def set_credential_field(self, credential_id: int, type: str, value: str) -> Dict[str, Any]:
        return self.request("POST", f"credentials/{int(credential_id)}/fields", json={"type": type, "value": value}).get("credential", {})

    def delete_credential_field(self, credential_id: int, type: str) -> Dict[str, Any]:
        return self.request("DELETE", f"credentials/{int(credential_id)}/fields/{type}")

    def delete_credential(self, credential_id: int) -> Dict[str, Any]:
        return self.request("DELETE", f"credentials/{int(credential_id)}")

    # -- webhooks: Clousd calls your server when a task ends or a phone has a problem -------------------------------
    def webhooks(self) -> Dict[str, Any]:
        """{webhooks: [id, url, events, description, enabled, last…], events: the event names you can subscribe to}."""
        return self.request("GET", "webhooks")

    def add_webhook(self, url: str, events: Optional[List[str]] = None, description: str = "") -> Dict[str, Any]:
        """Subscribe an https URL (empty events = all). Returns {webhook, secret}: keep the secret - it is shown once and
        signs every delivery (header X-Clousd-Signature: t=<time>,v1=HMAC-SHA256(secret, "<time>.<body>"))."""
        return self.request("POST", "webhooks", json={"url": url, "events": list(events or []), "description": description})

    def rotate_webhook_secret(self, webhook_id: str) -> Dict[str, Any]:
        return self.request("POST", f"webhooks/{webhook_id}/rotate", json={})

    def toggle_webhook(self, webhook_id: str) -> Dict[str, Any]:
        return self.request("POST", f"webhooks/{webhook_id}/toggle", json={})

    def test_webhook(self, webhook_id: str) -> Dict[str, Any]:
        """Send a test event now: {ok, status, ms, error}."""
        return self.request("POST", f"webhooks/{webhook_id}/test", json={}, timeout=30)

    def webhook_deliveries(self, webhook_id: str) -> List[Dict[str, Any]]:
        return self.request("GET", f"webhooks/{webhook_id}/deliveries").get("deliveries", [])

    def delete_webhook(self, webhook_id: str) -> Dict[str, Any]:
        return self.request("DELETE", f"webhooks/{webhook_id}")

    # -- media library ------------------------------------------------------------------------------------------
    def media(self, kind: str = "") -> List[Dict[str, Any]]:
        """The media library (Settings → Storage): id, kind (image | video), title, size."""
        return self.request("GET", "media", params={"kind": kind} if kind else None).get("media", [])

    def upload_media(self, data: Any, name: str = "", folder: Optional[int] = None) -> Dict[str, Any]:
        """Add a photo or video to the library (up to 25 MB): `data` is bytes or a file path; `name` keeps the extension
        (.jpg .png .webp .mp4 .mov)."""
        import base64
        import os
        if isinstance(data, (str, os.PathLike)):
            name = name or os.path.basename(str(data))
            with open(data, "rb") as f:
                data = f.read()
        body: Dict[str, Any] = {"name": name, "data_b64": base64.b64encode(data).decode()}
        if folder:
            body["folder"] = int(folder)
        return self.request("POST", "media", json=body, timeout=180).get("media", {})

    def send_media(self, ids: List[int], devices: List[Any]) -> Dict[str, Any]:
        """Copy library files into the phones' galleries, as if taken by that phone (EXIF of the model, local time, place)."""
        return self.request("POST", "media/send", json={"ids": [int(i) for i in ids], "devices": _names(devices)}, timeout=120)

    def _job(self, resp: Dict[str, Any], wait: bool, timeout: float) -> "Job":
        job = Job(self, resp.get("job") or {"state": "done", "output": resp.get("output", "")})
        return job.wait(timeout) if wait else job


def _names(devices: List[Any]) -> List[str]:
    return [d.name if isinstance(d, Device) else str(d) for d in devices]


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

    def wait(self, timeout: float = 900, every: float = 3.0, raise_on_fail: bool = True) -> "Job":
        """Poll until the job finishes; raises ClousdError if it ran out of time, or failed (unless raise_on_fail=False)."""
        end = time.time() + timeout
        while self.state == "running" and self.id:
            if time.time() > end:
                raise ClousdError(408, "timeout", f"job {self.id} still running after {int(timeout)} s")
            time.sleep(every)
            self.refresh()
        if self.state == "failed" and raise_on_fail:
            raise ClousdError(500, "job_failed", self.output.strip()[-500:])
        return self

    def __repr__(self) -> str:
        return f"<Job {self.id} {self.state}>"


class Run:
    """A task on a phone. `status`: queued, running, paused (waiting for an answer, see `card`), completed, failed,
    cancelled. A run on several phones is a parent: `children` are the runs of each phone."""

    DONE = ("completed", "failed", "cancelled")

    def __init__(self, client: Clousd, data: Dict[str, Any], children: Optional[List[Dict[str, Any]]] = None):
        self.client, self.data = client, data or {}
        self._kids = [Run(client, k) for k in children if k.get("id")] if children else None

    id = property(lambda self: self.data.get("id", ""))
    status = property(lambda self: self.data.get("status", ""))
    device = property(lambda self: self.data.get("device", ""))
    answer = property(lambda self: self.data.get("answer", ""))
    error = property(lambda self: self.data.get("error", ""))
    card = property(lambda self: self.data.get("card"))
    structured_output = property(lambda self: self.data.get("structured_output"))
    done = property(lambda self: self.status in Run.DONE)

    @property
    def children(self) -> List["Run"]:
        if self._kids is None:
            self._kids = [Run(self.client, {"id": i}) for i in self.data.get("children") or []]
        return self._kids

    def refresh(self) -> "Run":
        if self.id:
            self.data = self.client.request("GET", f"runs/{self.id}").get("run", self.data)
        return self

    def wait(self, timeout: float = 1800, every: float = 3.0) -> "Run":
        """Poll until the run has finished (a parent - until every phone has). A paused run waits for answer(); this
        returns as soon as it pauses so you can answer the card, then call wait() again."""
        end = time.time() + timeout
        self.refresh()
        while not self.done and self.status != "paused":
            if time.time() > end:
                raise ClousdError(408, "timeout", f"run {self.id} still {self.status} after {int(timeout)} s")
            time.sleep(every)
            self.refresh()
        if self.data.get("children"):
            self._kids = [Run(self.client, {"id": i}).refresh() for i in self.data["children"]]
        return self

    def events(self, since: int = 0) -> Iterator[Dict[str, Any]]:
        """Live events of a one-phone run, as they happen: {n, event, data} with event status | step | plan | card |
        answer | message | log | end. Ends after `end` (the run has finished). Start from `since` to resume."""
        url = self.client.base_url + f"/runs/{self.id}/events"
        last = since
        for attempt in range(20):
            try:
                with self.client._http.get(url, params={"since": last}, stream=True, timeout=(15, 60)) as r:
                    if r.status_code >= 400:
                        raise ClousdError(r.status_code, "error", r.text.strip()[:300])
                    ev: Dict[str, Any] = {}
                    for line in r.iter_lines(decode_unicode=True):
                        if line is None:
                            continue
                        if line == "":
                            if "event" in ev:
                                try:
                                    ev["data"] = _json.loads(ev.get("data") or "null")
                                except ValueError:
                                    pass
                                last = max(last, int(ev.get("n") or last))
                                yield ev
                                if ev["event"] == "end":
                                    return
                            ev = {}
                            continue
                        if line.startswith(":"):
                            continue
                        k, _, v = line.partition(":")
                        v = v[1:] if v.startswith(" ") else v
                        if k == "id":
                            ev["n"] = int(v) if v.isdigit() else v
                        elif k == "event":
                            ev["event"] = v
                        elif k == "data":
                            ev["data"] = (ev.get("data", "") + "\n" + v) if "data" in ev else v
                if self.refresh().done:
                    return
            except requests.RequestException:
                if attempt == 19:
                    raise
                time.sleep(min(2 + attempt, 10))

    def trajectory(self) -> List[Dict[str, Any]]:
        """Every step: goal, action, result and whether a screenshot was kept (step_image(n))."""
        return self.client.request("GET", f"runs/{self.id}/trajectory").get("steps", [])

    def step_image(self, n: int) -> bytes:
        return self.client.request("GET", f"runs/{self.id}/steps/{int(n)}.jpg", raw=True)

    def reply(self, answer: Optional[str] = None, allow: Optional[bool] = None) -> "Run":
        """Answer the run's card: a question (`answer` - text or one of card["options"]) or a permission (`allow`)."""
        card = self.card or self.refresh().card
        if not card:
            raise ClousdError(409, "no_card", "the run is not waiting for an answer")
        body: Dict[str, Any] = {"card": card.get("id", "")}
        if allow is not None:
            body["allow" if allow else "decline"] = True
        else:
            body["answer"] = answer or ""
        self.client.request("POST", f"runs/{self.id}/answer", json=body)
        return self.refresh()

    def message(self, text: str) -> "Run":
        """Tell the running agent something; it reads it before its next step."""
        self.client.request("POST", f"runs/{self.id}/message", json={"text": text})
        return self

    def cancel(self) -> "Run":
        self.client.request("POST", f"runs/{self.id}/cancel", json={})
        return self.refresh()

    def __repr__(self) -> str:
        return f"<Run {self.id} {self.status}>"


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

    @property
    def label(self) -> str:
        """The name people see in the dashboard: their own, or "Model #N" by the phone's number in the account."""
        return str((self.data or self.refresh()).get("label") or self.name)

    def rename(self, label: str) -> Dict[str, Any]:
        """Give the phone a name (up to 40 characters); an empty one goes back to "Model #N"."""
        r = self.client.request("POST", self._p("label"), json={"label": label})
        if isinstance(self.data, dict):
            self.data["label"] = r.get("label", label)
        return r

    # -- network exit ------------------------------------------------------------------------------------------
    def network(self) -> Dict[str, Any]:
        """The exit this phone uses now: type, country, city, carrier, isp, ip, timezone, locale, ok."""
        return self.client.request("GET", self._p("network")).get("network", {})

    def set_network(self, type: str, country: str = "", url: str = "", rotate_url: str = "") -> Dict[str, Any]:
        """Switch the exit: type residential | mobile | isp | datacenter (+ country), byo (your proxy `url`, optional
        `rotate_url`) or off. On an account key this saves the proxy in Network and binds it, like the dashboard."""
        body: Dict[str, Any] = {"type": type}
        if country:
            body["country"] = country
        if url:
            body["url"] = url
        if rotate_url:
            body["rotate_url"] = rotate_url
        return self.client.request("POST", self._p("network"), json=body, timeout=120)

    def rotate_ip(self) -> Dict[str, Any]:
        """New IP on the same exit."""
        return self.client.request("POST", self._p("network/rotate"), json={}, timeout=120)

    # -- tasks -----------------------------------------------------------------------------------------------------
    def run(self, task: str, **kw: Any) -> Run:
        """A task on this phone in plain words (same options as Clousd.run)."""
        return self.client.run(task, device=self, **kw)

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
