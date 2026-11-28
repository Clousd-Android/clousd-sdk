"""A small task benchmark for phone agents, in the spirit of AndroidWorld: everyday tasks on stock apps, each with an
automatic check. It measures the agent loop (clousd.agent) with a given model on a given phone.

Checks use what any API key can read: the agent's answer, the final screen (app on top, texts), the device record -
and, when ADB access is enabled for the device and this machine's IP, a shell command (settings, content providers),
which makes state checks exact. A task whose check needs the shell is skipped without it.

Between tasks the phone goes home and the apps the task used are closed. Results: one JSON line per task.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .agent import Agent, Model, Result
from .client import ClousdError, Device


@dataclass
class Task:
    id: str
    goal: str
    apps: List[str]                                   # closed before and after
    check: Callable[["Ctx", Result], tuple]           # → (pass: bool, detail: str)
    needs_shell: bool = False
    setup: Optional[Callable[["Ctx"], None]] = None
    max_steps: int = 0
    requires: Optional[List[str]] = None             # any of these packages must be installed, else the task is skipped


class Ctx:
    def __init__(self, d: Device):
        self.d = d
        self.info = {}
        try:
            self.info = d.refresh()
        except ClousdError:
            pass
        self._shell: Optional[bool] = None
        self._installed: Optional[List[str]] = None

    def installed(self) -> List[str]:
        if self._installed is None:
            try:
                self._installed = self.d.installed()
            except ClousdError:
                self._installed = []
        return self._installed

    def shell_ok(self) -> bool:
        if self._shell is None:
            try:
                rc, _, _ = self.d.shell("true", timeout=20)
                self._shell = rc == 0
            except ClousdError:
                self._shell = False
        return self._shell

    def sh(self, *argv: str) -> str:
        rc, out, err = self.d.shell(*argv, timeout=30)
        return (out + err).strip()

    def screen(self) -> Dict[str, Any]:
        try:
            return self.d.observe(width=360, ui=True)
        except ClousdError:
            return {}

    def texts(self, o: Optional[Dict[str, Any]] = None) -> str:
        o = o if o is not None else self.screen()
        return " | ".join((n.get("text") or n.get("desc") or "") for n in o.get("ui", []))


def _ans(res: Result, *pats: str) -> tuple:
    a = res.answer or ""
    for p in pats:
        if re.search(p, a, re.I):
            return True, f"answer {a[:80]!r}"
    return False, f"answer {a[:80]!r} does not match {pats[0]!r}"


def _screen_has(ctx: Ctx, pkg: str, *pats: str) -> tuple:
    o = ctx.screen()
    t = ctx.texts(o)
    if pkg and pkg not in (o.get("package") or ""):
        return False, f"on screen: {o.get('package')}"
    for p in pats:
        if not re.search(p, t, re.I):
            return False, f"{p!r} not on screen"
    return True, "screen ok"


def _android(ctx: Ctx, res: Result) -> tuple:
    v = str(ctx.info.get("android") or "")
    return _ans(res, r"\b" + re.escape(v) + r"\b") if v else (False, "device has no android field")


def _model(ctx: Ctx, res: Result) -> tuple:
    m = str(ctx.info.get("model") or "")
    return _ans(res, re.escape(m)) if m else (False, "device has no model field")


def _night(ctx: Ctx, res: Result) -> tuple:
    v = ctx.sh("settings", "get", "secure", "ui_night_mode")
    return v == "2", f"ui_night_mode={v}"


def _night_off(ctx: Ctx) -> None:
    ctx.sh("cmd", "uimode", "night", "no")


def _contact(ctx: Ctx, res: Result) -> tuple:
    out = ctx.sh("content", "query", "--uri", "content://com.android.contacts/data/phones", "--projection", "display_name:data1")
    ok = "Alex Morgan" in out and re.search(r"555[\s-]*1234", out) is not None
    return ok, "contact found" if ok else "no contact Alex Morgan with 5551234"


def _contact_clean(ctx: Ctx) -> None:
    ctx.sh("content", "delete", "--uri", "content://com.android.contacts/raw_contacts", "--where", "display_name='Alex Morgan'")


def _alarm(ctx: Ctx, res: Result) -> tuple:
    out = ctx.sh("dumpsys", "alarm")
    ok = "deskclock" in out and re.search(r"(07|7):30", out + " " + (res.answer or "")) is not None
    t = ctx.texts()
    ok = ok or re.search(r"7:30", t) is not None
    return ok, "alarm 7:30 found" if ok else "no 7:30 alarm"


def _brightness(ctx: Ctx, res: Result) -> tuple:
    v = ctx.sh("settings", "get", "system", "screen_brightness")
    try:
        return int(v) >= 200, f"screen_brightness={v}"
    except ValueError:
        return False, f"screen_brightness={v!r}"


def _brightness_low(ctx: Ctx) -> None:
    ctx.sh("settings", "put", "system", "screen_brightness_mode", "0")
    ctx.sh("settings", "put", "system", "screen_brightness", "60")


TASKS: List[Task] = [
    Task("android_version", "Open Settings and tell me which Android version this phone runs.", ["com.android.settings"], _android),
    Task("model_name", "What is the model name of this phone? Look it up in Settings.", ["com.android.settings"], _model),
    Task("calculator", "Use the Calculator app to compute 127 times 43 and tell me the result.", ["com.google.android.calculator"],
         lambda c, r: _ans(r, r"5\s*,?461"), requires=["com.google.android.calculator", "com.android.calculator2"]),
    Task("web_heading", "Open https://example.com in the browser and tell me the main heading of the page.", ["com.android.chrome"],
         lambda c, r: _ans(r, r"example domain")),
    Task("chrome_search", "In Chrome, search the web for 'weather in Paris' and leave the results open.", ["com.android.chrome"],
         lambda c, r: _screen_has(c, "com.android.chrome", r"paris")),
    Task("wifi_page", "Open the Wi-Fi settings page.", ["com.android.settings"],
         lambda c, r: _screen_has(c, "com.android.settings", r"wi-?fi|internet")),
    Task("dark_theme", "Turn on Dark theme on this phone.", ["com.android.settings"], _night, needs_shell=True, setup=_night_off),
    Task("contact", "Create a new contact named Alex Morgan with the phone number 555 1234.", ["com.google.android.contacts"],
         _contact, needs_shell=True, setup=_contact_clean, requires=["com.google.android.contacts", "com.android.contacts"]),
    Task("alarm", "Set an alarm for 7:30 AM in the Clock app.", ["com.google.android.deskclock", "com.android.deskclock"], _alarm,
         needs_shell=True, requires=["com.google.android.deskclock", "com.android.deskclock"]),
    Task("brightness", "Set the screen brightness to maximum.", ["com.android.settings"], _brightness, needs_shell=True, setup=_brightness_low),
    Task("battery_level", "Tell me the current battery level in percent.", ["com.android.settings"],
         lambda c, r: _ans(r, r"\d{1,3}\s*%|\b\d{1,3}\b percent")),
    Task("installed_count", "Is the YouTube app installed on this phone? Answer yes or no.", [],
         lambda c, r: _ans(r, r"\byes\b") if "com.google.android.youtube" in c.d.installed() else _ans(r, r"\bno\b")),
]


def _reset(d: Device, apps: List[str]) -> None:
    for p in apps:
        try:
            d.action("close_app", package=p)
        except ClousdError:
            pass
    try:
        d.action("key", key="home")
    except ClousdError:
        pass
    time.sleep(2)


def run_bench(d: Device, model: Model, only: Optional[List[str]] = None, max_steps: int = 20, vision: bool = True,
              out: Optional[str] = None, log: Optional[Callable[[str], None]] = print) -> List[Dict[str, Any]]:
    ctx = Ctx(d)
    rows: List[Dict[str, Any]] = []
    fh = open(out, "a", encoding="utf-8") if out else None
    try:
        for t in TASKS:
            if only and t.id not in only:
                continue
            if t.requires and not any(p in ctx.installed() for p in t.requires):
                row = {"id": t.id, "pass": False, "skipped": "app not installed: " + " or ".join(t.requires)}
                rows.append(row)
                if log:
                    log(f"{t.id}: skipped - {row['skipped']}")
                continue
            if t.needs_shell and not ctx.shell_ok():
                row = {"id": t.id, "pass": False, "skipped": "needs ADB shell (enable ADB access for this device and IP)"}
                rows.append(row)
                if log:
                    log(f"{t.id}: skipped - {row['skipped']}")
                continue
            _reset(d, t.apps)
            if t.setup:
                try:
                    t.setup(ctx)
                except ClousdError:
                    pass
            if log:
                log(f"{t.id}: {t.goal}")
            agent = Agent(d, model, vision=vision, max_steps=t.max_steps or max_steps, log=log)
            res = agent.run(t.goal)
            try:
                ok, detail = t.check(ctx, res)
            except Exception as e:   # a check that cannot read the phone is a failed check, not a crash of the run
                ok, detail = False, f"check error: {e}"
            row = {"id": t.id, "pass": bool(ok), "status": res.status, "answer": res.answer, "detail": detail,
                   "steps": len(res.steps), "seconds": round(res.seconds, 1),
                   "model_s": round(sum(s.get("model_s", 0) for s in res.steps), 1),
                   "observe_s": round(sum(s.get("observe_s", 0) for s in res.steps), 1),
                   "tools": [s.get("tool") for s in res.steps]}
            rows.append(row)
            if log:
                log(f"{t.id}: {'PASS' if ok else 'FAIL'} - {detail} ({res.status}, {len(res.steps)} steps, {res.seconds:.0f} s)")
            if fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
            _reset(d, t.apps)
    finally:
        if fh:
            fh.close()
    return rows
