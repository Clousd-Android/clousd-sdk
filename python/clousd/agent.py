"""A task loop for a phone: goal in, steps on the screen, answer out - with any OpenAI-compatible chat model.

    from clousd import Clousd
    from clousd.agent import Agent, Model
    phone = Clousd().device("dev_04f2")
    agent = Agent(phone, Model("http://127.0.0.1:8080/v1", "qwen"))
    result = agent.run("Open Settings and tell me the Android version")
    print(result.status, result.answer)

Each step: observe (screenshot + numbered elements) → the model calls one tool → the action runs with `settle` and the
observation's `seq` (a screen that changed in between is never acted on: 409 stale → look again) → next step.
The run stops when the model calls done / fail, at `max_steps`, or after repeated errors. Every step is kept in
`result.steps` (and written to `trace_path` as JSON lines when given): what the model saw, said and did, and how long
each part took.

The model gets the screenshot (when `vision`) with a number drawn on every element, and the same elements as text
lines; it acts on an element by its number, which is more reliable than pixel coordinates for most models.
Works with tool-calling models (OpenAI `tools`); a model without tool calling may answer with one JSON object
{"tool": ..., "args": {...}} in the text, which is accepted too.
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests

from .client import ClousdError, Device

SYSTEM = """You operate a real Android phone to complete the user's task.
Every turn you get the current screen: a screenshot with a number on each element, and the list of those elements
as lines `[n] label (class) flags`. Act with exactly ONE tool call per turn.

Rules:
- Prefer tap with an element number. Use x/y only when the target has no number; x/y are pixels of the screenshot.
- To type: tap the input field first (it becomes "focused" in the list), then call type. Typing appends - clear the
  field first if needed.
- To open a web address always use open_url (not the browser's address bar).
- To open an app use open_app with its package name when you know it (Settings com.android.settings, Chrome
  com.android.chrome, Contacts com.google.android.contacts, Clock com.google.android.deskclock, Calculator
  com.google.android.calculator, Play Store com.android.vending, Gmail com.google.android.gm, Maps
  com.google.android.apps.maps, Files com.google.android.documentsui, Messages com.google.android.apps.messaging,
  Phone com.google.android.dialer, Camera com.android.camera2); call installed_apps if unsure.
- scroll down to see more of a list; key back to go back; key home for the home screen.
- wait only while something is visibly loading, at most twice in a row; otherwise act.
- If a dialog or popup is in the way, deal with it first (usually "Not now", "Skip", "Allow" or "OK").
- When the task is complete call done with a short answer (for questions: the answer itself). If the task is
  impossible, call fail with the reason. Do not repeat the same failing action more than twice - try another way.
- Never enter passwords, payment data or personal data that the user did not give you."""

TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {"name": "tap", "description": "Tap an element by its number, or a point (x, y in screenshot pixels).",
     "parameters": {"type": "object", "properties": {"element": {"type": "integer", "description": "element number from the list"},
                                                     "x": {"type": "number"}, "y": {"type": "number"}}}}},
    {"type": "function", "function": {"name": "long_press", "description": "Press and hold an element (context menu, selection).",
     "parameters": {"type": "object", "properties": {"element": {"type": "integer"}, "x": {"type": "number"}, "y": {"type": "number"}}}}},
    {"type": "function", "function": {"name": "type", "description": "Type text into the focused input field (tap the field first). Any language.",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "enter": {"type": "boolean", "description": "press Enter after typing (submit a search)"}},
                    "required": ["text"]}}},
    {"type": "function", "function": {"name": "key", "description": "Press a key: back, home, enter, recents, del, tab.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string", "enum": ["back", "home", "enter", "recents", "del", "tab"]}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "scroll", "description": "Scroll the screen content: down shows what is below, up what is above.",
     "parameters": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["down", "up"]}}, "required": ["direction"]}}},
    {"type": "function", "function": {"name": "swipe", "description": "Swipe from (x, y) to (x2, y2) in screenshot pixels (carousels, sliders).",
     "parameters": {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "x2": {"type": "number"}, "y2": {"type": "number"}},
                    "required": ["x", "y", "x2", "y2"]}}},
    {"type": "function", "function": {"name": "open_app", "description": "Open an installed app by package name.",
     "parameters": {"type": "object", "properties": {"package": {"type": "string"}}, "required": ["package"]}}},
    {"type": "function", "function": {"name": "open_url", "description": "Open a web address in the browser.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "installed_apps", "description": "List package names of apps that can be opened.",
     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "wait", "description": "Wait for the screen to load (1-10 seconds).",
     "parameters": {"type": "object", "properties": {"seconds": {"type": "number"}}}}},
    {"type": "function", "function": {"name": "done", "description": "The task is complete. answer: the result or the answer to the question.",
     "parameters": {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}}},
    {"type": "function", "function": {"name": "fail", "description": "The task cannot be completed.",
     "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}},
]


class Model:
    """An OpenAI-compatible chat completions endpoint (llama.cpp server, vLLM, Ollama, OpenAI, OpenRouter, ...)."""

    def __init__(self, base_url: str, model: str = "", api_key: str = "", timeout: float = 180, temperature: float = 0.2,
                 max_tokens: int = 600, extra: Optional[Dict[str, Any]] = None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key, self.timeout = model, api_key, timeout
        self.temperature, self.max_tokens, self.extra = temperature, max_tokens, extra or {}

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"messages": messages, "temperature": self.temperature, "max_tokens": self.max_tokens, **self.extra}
        if self.model:
            body["model"] = self.model
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = "Bearer " + self.api_key
        r = requests.post(self.url, json=body, headers=h, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"model {r.status_code}: {r.text[:300]}")
        return r.json()


@dataclass
class Element:
    n: int
    label: str
    cls: str
    b: List[int]          # device pixels x1, y1, x2, y2
    flags: str

    @property
    def center(self) -> tuple:
        return ((self.b[0] + self.b[2]) // 2, (self.b[1] + self.b[3]) // 2)


@dataclass
class Result:
    status: str                       # done | fail | max_steps | error
    answer: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0

    def __repr__(self) -> str:
        return f"<Result {self.status} in {len(self.steps)} steps, {self.seconds:.0f} s: {self.answer[:80]!r}>"


def elements_of(obs: Dict[str, Any], limit: int = 70) -> List[Element]:
    """Labelled or interactive elements, top to bottom, without duplicates of the same box and label."""
    out: List[Element] = []
    seen = set()
    nodes = sorted(obs.get("ui", []), key=lambda n: (n.get("b", [0, 0])[1], n.get("b", [0])[0]))
    for nd in nodes:
        b = nd.get("b") or [0, 0, 0, 0]
        label = (nd.get("text") or nd.get("desc") or "").strip()
        rid = (nd.get("id") or "").split("/")[-1]
        interactive = nd.get("click") or nd.get("long") or nd.get("scroll") or nd.get("focus")
        if not label and not interactive:
            continue
        if not label:
            label = rid.replace("_", " ") if rid else ""
        key = (tuple(b), label)
        if key in seen or (b[2] - b[0]) * (b[3] - b[1]) < 16:
            continue
        seen.add(key)
        flags = " ".join(f for f, k in (("tap", "click"), ("scroll", "scroll"), ("input", "focus"), ("focused", "focused"),
                                         ("checked", "checked"), ("selected", "selected"), ("off", "disabled")) if nd.get(k))
        if nd.get("class", "").endswith("EditText") and "input" not in flags:
            flags = (flags + " input").strip()
        out.append(Element(len(out), label[:80], nd.get("class", "").split(".")[-1], list(b), flags))
        if len(out) >= limit:
            break
    return out


def annotate(jpeg: bytes, els: List[Element], scale: float) -> bytes:
    """The screenshot with each element's box and number drawn on it (needs Pillow; without it the plain image)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return jpeg
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    dr = ImageDraw.Draw(im)
    for e in els:
        x1, y1, x2, y2 = (int(v / scale) for v in e.b)
        col = (230, 40, 40) if "tap" in e.flags or "input" in e.flags else (40, 120, 230)
        dr.rectangle([x1, y1, x2, y2], outline=col, width=2)
        tag = str(e.n)
        w = 7 * len(tag) + 4
        dr.rectangle([x1, y1, x1 + w, y1 + 13], fill=col)
        dr.text((x1 + 2, y1), tag, fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


class Agent:
    """Runs one task at a time on one device. `log` gets one line per step (print by default; None = silent)."""

    _waits = 0

    def __init__(self, device: Device, model: Model, vision: bool = True, max_steps: int = 25, width: int = 540,
                 annotate_image: bool = True, trace_path: Optional[str] = None, log: Optional[Callable[[str], None]] = print,
                 history: int = 12):
        self.d, self.model, self.vision, self.max_steps = device, model, vision, max_steps
        self.width, self.annotate_image, self.trace_path, self.log, self.history = width, annotate_image, trace_path, log, history

    def _say(self, s: str) -> None:
        if self.log:
            self.log(s)

    def _observe(self) -> Dict[str, Any]:
        for attempt in range(6):
            try:
                o = self.d.observe(width=self.width, ui=True)
                if o.get("ui_error", "").startswith("not_ready") and attempt < 5:
                    time.sleep(5)
                    continue
                return o
            except ClousdError as e:
                if e.status in (409, 502, 503) and attempt < 5:
                    time.sleep(3)
                    continue
                raise
        return self.d.observe(width=self.width, ui=True)

    def _screen_message(self, goal: str, obs: Dict[str, Any], els: List[Element], scale: float, notes: List[str]) -> Dict[str, Any]:
        img = obs.get("image", {})
        lines = [f"[{e.n}] {e.label!r} ({e.cls}){' ' + e.flags if e.flags else ''}" for e in els]
        txt = (f"Task: {goal}\n"
               + ("Steps so far:\n" + "\n".join(notes[-self.history:]) + "\n" if notes else "Steps so far: none\n")
               + f"Current app: {obs.get('package') or 'unknown'} {obs.get('activity') or ''}\n"
               + f"Screenshot {img.get('w')}x{img.get('h')} px.\n"
               + ("Elements:\n" + "\n".join(lines) if lines else "Elements: none readable (use the screenshot and x/y)")
               + (f"\n(element list unavailable: {obs['ui_error']})" if obs.get("ui_error") else "")
               + "\nWhat is the next single action?")
        if not self.vision:
            return {"role": "user", "content": txt}
        jpeg = obs.get("image_bytes") or base64.b64decode(img.get("data", ""))
        if self.annotate_image and els:
            jpeg = annotate(jpeg, els, scale)
        return {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}},
            {"type": "text", "text": txt}]}

    @staticmethod
    def _parse_call(resp: Dict[str, Any]) -> tuple:
        msg = (resp.get("choices") or [{}])[0].get("message", {}) or {}
        said = (msg.get("content") or "").strip()
        calls = msg.get("tool_calls") or []
        if calls:
            fn = calls[0].get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            return fn.get("name", ""), args if isinstance(args, dict) else {}, said
        # no tool calling: one JSON object in the text, {"tool": ..., "args": {...}}
        m = re.search(r"\{.*\}", said, re.S)
        if m:
            try:
                j = json.loads(m.group(0))
                name = j.get("tool") or j.get("name") or j.get("action") or ""
                args = j.get("args") or j.get("arguments") or {k: v for k, v in j.items() if k not in ("tool", "name", "action")}
                return name, args if isinstance(args, dict) else {}, said
            except ValueError:
                pass
        return "", {}, said

    def _point(self, args: Dict[str, Any], els: List[Element], scale: float) -> tuple:
        if args.get("element") is not None:
            try:
                n = int(args["element"])
            except (TypeError, ValueError):
                raise ValueError(f"element must be a number, got {args['element']!r}")
            if not 0 <= n < len(els):
                raise ValueError(f"no element {n} on this screen (0..{len(els) - 1})")
            return els[n].center, els[n].label
        if args.get("x") is not None and args.get("y") is not None:
            return (int(float(args["x"]) * scale), int(float(args["y"]) * scale)), f"({args['x']}, {args['y']})"
        raise ValueError("give element (a number from the list) or x and y")

    def _do(self, name: str, args: Dict[str, Any], els: List[Element], scale: float, seq: Optional[int]) -> str:
        if name in ("tap", "long_press"):
            (x, y), what = self._point(args, els, scale)
            if name == "tap":
                r = self.d.act("tap", seq=seq, x=x, y=y)
            else:
                try:
                    r = self.d.act("long_press", seq=seq, x=x, y=y, ms=900)
                except ClousdError as e:
                    if e.status != 400:
                        raise
                    r = self.d.act("swipe", x=x, y=y, x2=x, y2=y, ms=900)   # a gateway without long_press
            return f"{name} {what}" + ("" if r.get("changed", True) else " - the screen did not change")
        if name == "type":
            text = str(args.get("text", ""))
            if not text:
                raise ValueError("type needs text")
            if els and not any("focused" in e.flags for e in els):
                raise ValueError("no input field is focused - tap the field first (for web addresses use open_url)")
            r = self.d.act("text", seq=seq, text=text)
            if args.get("enter"):
                self.d.act("key", key="enter")
            return f"typed {text!r}" + (" + enter" if args.get("enter") else "")
        if name == "key":
            k = str(args.get("name") or args.get("key") or "back")
            self.d.act("key", seq=seq, key=k)
            return f"key {k}"
        if name == "scroll":
            dr = str(args.get("direction") or "down")
            r = self.d.act("scroll", seq=seq, text=dr if dr in ("down", "up") else "down")
            return f"scrolled {dr}" + ("" if r.get("changed", True) else " - nothing moved (end of the list?)")
        if name == "swipe":
            p = [int(float(args[k]) * scale) for k in ("x", "y", "x2", "y2")]
            self.d.act("swipe", seq=seq, x=p[0], y=p[1], x2=p[2], y2=p[3], ms=400)
            return "swiped"
        if name == "open_app":
            pkg = str(args.get("package", ""))
            self.d.act("open_app", seq=seq, package=pkg)
            return f"opened {pkg}"
        if name == "open_url":
            url = str(args.get("url", ""))
            if not url.startswith("http"):
                url = "https://" + url
            self.d.act("url", seq=seq, url=url)
            return f"opened {url}"
        if name == "installed_apps":
            return "installed: " + ", ".join(self.d.installed())
        if name == "wait":
            if self._waits >= 2:
                self._waits = 0
                raise ValueError("waited twice already - the screen will not change by itself; act (or call done/fail)")
            self._waits += 1
            s = max(1.0, min(float(args.get("seconds") or 3), 10.0))
            time.sleep(s)
            return f"waited {s:.0f} s"
        raise ValueError(f"unknown tool {name!r}")

    def run(self, goal: str) -> Result:
        t0 = time.time()
        res = Result("max_steps")
        notes: List[str] = []
        errors = 0
        self._waits = 0
        last_calls: List[str] = []
        trace = open(self.trace_path, "a", encoding="utf-8") if self.trace_path else None
        try:
            for step in range(1, self.max_steps + 1):
                ts = time.time()
                obs = self._observe()
                t_obs = time.time() - ts
                img = obs.get("image", {})
                scale = (obs.get("screen", {}).get("w") or 1080) / float(img.get("w") or self.width)
                els = elements_of(obs)
                msgs = [{"role": "system", "content": SYSTEM}, self._screen_message(goal, obs, els, scale, notes)]
                tm = time.time()
                try:
                    resp = self.model.chat(msgs, TOOLS)
                except Exception as e:   # the model endpoint itself failed
                    errors += 1
                    self._say(f"  {step}: model error {e}")
                    if errors >= 3:
                        res.status, res.answer = "error", f"model: {e}"
                        break
                    time.sleep(5)
                    continue
                t_model = time.time() - tm
                name, args, said = self._parse_call(resp)
                usage = resp.get("usage", {})
                rec: Dict[str, Any] = {"step": step, "app": obs.get("package"), "elements": len(els), "tool": name, "args": args,
                                       "said": said[:400], "observe_s": round(t_obs, 2), "model_s": round(t_model, 2),
                                       "tokens": usage.get("prompt_tokens"), "out_tokens": usage.get("completion_tokens")}
                if name == "done":
                    res.status, res.answer = "done", str(args.get("answer", said))
                    rec["result"] = "done"
                    res.steps.append(rec)
                    self._say(f"  {step}: done - {res.answer[:120]}")
                    break
                if name == "fail":
                    res.status, res.answer = "fail", str(args.get("reason", said))
                    rec["result"] = "fail"
                    res.steps.append(rec)
                    self._say(f"  {step}: fail - {res.answer[:120]}")
                    break
                if name != "wait":
                    self._waits = 0
                sig = name + json.dumps(args, sort_keys=True)
                last_calls.append(sig)
                ta = time.time()
                try:
                    if not name:
                        raise ValueError("no tool call - answer with exactly one tool call")
                    out = self._do(name, args, els, scale, obs.get("seq"))
                    errors = 0
                except ClousdError as e:
                    out = "the screen changed before the action - looking again" if e.status == 409 and e.error == "stale" else f"error: {e.message or e.error}"
                    errors += 0 if e.status == 409 else 1
                except (ValueError, KeyError) as e:
                    out = f"error: {e}"
                    errors += 1
                rec["result"], rec["act_s"] = out, round(time.time() - ta, 2)
                res.steps.append(rec)
                if len(last_calls) >= 3 and len(set(last_calls[-3:])) == 1 and name not in ("scroll",):
                    out += " - you did exactly this three times in a row; it is not working, try something different"
                notes.append(f"{step}. {name} {json.dumps(args, ensure_ascii=False)} -> {out}")
                self._say(f"  {step}: {name} {json.dumps(args, ensure_ascii=False)} -> {out}  [{t_obs:.1f}+{t_model:.1f}+{rec['act_s']:.1f} s]")
                if trace:
                    trace.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    trace.flush()
                if errors >= 4:
                    res.status, res.answer = "error", "four errors in a row: " + out
                    break
        finally:
            res.seconds = time.time() - t0
            if trace:
                trace.write(json.dumps({"goal": goal, "status": res.status, "answer": res.answer, "steps": len(res.steps),
                                        "seconds": round(res.seconds, 1)}, ensure_ascii=False) + "\n")
                trace.close()
        return res
