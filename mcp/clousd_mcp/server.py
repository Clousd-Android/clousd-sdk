"""MCP server for CLOUSD cloud phones: gives any MCP-capable agent real Android phones in the cloud.

Nine tools (a few clear tools are easier for a model to choose from than dozens of small ones):
  account     - the plan, phones, money and warnings to tell the user
  devices     - the phones this key can use; start, stop or rename one
  run         - give one or many phones a task in plain words; Clousd's agent does it and answers
  run_status  - follow a run: status, the last steps, the answer, a question it waits on
  answer      - answer a run's question or allow / decline the step it asks about
  observe     - the screen as an image with a number on every element, plus the elements as text
  act         - tap, type, swipe, open apps... on the screen yourself; also read notifications, clipboard, app info
  credentials - saved logins (names and which fields are set, never values) to pass to run
  schedules   - tasks that run by themselves at a time on days of the week

Screenshots come back `SHOT_WIDTH` px wide (540: ~1200 px long side on a 1080x2400 phone). act takes an element
number from the last observe, or coordinates in that screenshot; both are converted to the device here.

    CLOUSD_API_KEY=cl_live_... clousd-mcp          # stdio server
    CLOUSD_MCP_HTTP=127.0.0.1:8790 clousd-mcp      # remote server: every request carries Authorization: Bearer …
"""
from __future__ import annotations

import contextvars
import json
import os
import time
from typing import Annotated, Any, Dict, List, Optional

from pydantic import Field

from clousd import Clousd, ClousdError

try:   # mcp 2.x: FastMCP became MCPServer
    from mcp.server.mcpserver import Context, Image, MCPServer as _Server
except ImportError:   # mcp 1.x
    from mcp.server.fastmcp import Context, FastMCP as _Server, Image
from mcp.types import ToolAnnotations

try:
    from clousd.agent import annotate as _annotate, elements_of as _elements_of
except ImportError:   # clousd < 0.2: plain screenshot, elements numbered here
    _annotate = None
    _elements_of = None

SHOT_WIDTH = int(os.environ.get("CLOUSD_SHOT_WIDTH", "540"))
SITE = os.environ.get("CLOUSD_SITE_URL", "https://clousd.com").rstrip("/")
RESOURCE_META = os.environ.get("CLOUSD_MCP_RESOURCE_META", "https://api.clousd.com/.well-known/oauth-protected-resource")
WAIT_MAX = 50   # seconds a tool call may wait for a run (remote clients time out around a minute)


def shot_width() -> int:
    """Screenshot width: CLOUSD_SHOT_WIDTH, else 540. Clamped to 240..1080."""
    return max(240, min(int(SHOT_WIDTH), 1080))


mcp = _Server("clousd", instructions="Real Android phones in the cloud. Start with account (tell the user its warnings with "
              "the link) and devices. To get something done on a phone, give it the goal in plain words with run - Clousd's "
              "agent works the screen and answers; follow it with run_status and reply to its questions with answer. For "
              "several phones put them all in one run (they work in parallel) - never loop over phones. Never ask the user "
              "for passwords: pass a saved login from credentials to run; its values are typed without anyone seeing them. "
              "Use observe and act only when you want to drive the screen yourself step by step. Repeating work goes to "
              "schedules.")
_clients: Dict[str, Clousd] = {}   # API key → client (one per key; the remote server serves many keys)
_key: contextvars.ContextVar = contextvars.ContextVar("clousd_key", default="")
_scale: Dict[str, float] = {}      # device → device pixels per screenshot pixel
_els: Dict[str, Dict[str, Any]] = {}   # key+device → {seq, pts:[(cx, cy, label)]} of the last observe

Device_ = Annotated[str, Field(description="Device from devices: its name, its label from the dashboard (e.g. \"Pixel 7 #3\") or its number")]


def _use(ctx: Optional[Context]) -> None:
    """Remember which API key this request carries. Over HTTP (api.clousd.com/mcp) only the header counts:
    `Authorization: Bearer <cl_live_… key or OAuth token>` - keys in the address are refused (they end up in logs and
    histories). Over stdio there is no request and the key is CLOUSD_API_KEY from the environment."""
    key = ""
    try:
        req = getattr(ctx.request_context, "request", None) if ctx is not None else None
    except Exception:   # stdio: no request context at all
        req = None
    try:
        h = getattr(ctx, "headers", None) if ctx is not None else None
        if h is None and req is not None:
            h = getattr(req, "headers", None)
        if h:
            auth = h.get("authorization", "") or h.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                key = auth[7:].strip()
    except Exception:
        key = ""
    _key.set(key)


def client() -> Clousd:
    key = _key.get() or os.environ.get("CLOUSD_API_KEY", "")
    if not key:
        raise ClousdError(401, "no_key", "No Clousd API key: send Authorization: Bearer cl_live_… (remote) or set CLOUSD_API_KEY (stdio)")
    c = _clients.get(key)
    if c is None:
        if len(_clients) >= 256:   # remote mode: unknown keys must not grow the cache without bound
            _clients.pop(next(iter(_clients)))
        c = _clients[key] = Clousd(api_key=key)
    return c


_names: Dict[str, Any] = {}   # key → (time, {name}, {label / number → name}) - phones by the name people gave them


def _resolve(device: str) -> str:
    """A device by its API name, its name in the dashboard ("Pixel 7 #3", case-insensitive) or its number ("3", "#3").
    Unknown values pass through and the API answers 404."""
    d = (device or "").strip()
    if not d:
        return d
    k = _key.get() or "env"
    at, names, labels = _names.get(k, (0.0, set(), {}))
    if d in names and time.time() - at < 60:
        return d
    if time.time() - at > 30 or d.lower() not in labels:
        names, labels = set(), {}
        try:
            for x in client().devices():
                names.add(x.name)
                lb = str(x.data.get("label") or "").strip().lower()
                if lb:
                    labels.setdefault(lb, x.name)
                num = x.data.get("number")
                if num:
                    labels.setdefault(str(num), x.name)
                    labels.setdefault("#" + str(num), x.name)
        except ClousdError:
            return d
        if len(_names) > 256:
            _names.clear()
        _names[k] = (time.time(), names, labels)
    if d in names:
        return d
    return labels.get(d.lower(), d)


def _devs(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [_resolve(str(x)) for x in raw]
    raw = str(raw or "").strip()
    if raw.startswith("["):
        return [_resolve(str(x)) for x in json.loads(raw)]
    return [_resolve(x.strip()) for x in raw.split(",") if x.strip()]


def _slot(device: str) -> str:
    return (_key.get() or "env")[-12:] + "/" + device


def _jpeg_w(b: bytes) -> int:
    """Width of a JPEG from its SOF marker (the gateway shrinks by an integer factor, so it can differ from the
    requested width: a 720 px screen stays 720 at w=540)."""
    i = 2
    while i + 9 < len(b):
        if b[i] != 0xFF:
            i += 1
            continue
        if 0xC0 <= b[i + 1] <= 0xC3:
            return (b[i + 7] << 8) | b[i + 8]
        i += 2 + ((b[i + 2] << 8) | b[i + 3])
    return shot_width()


def _learn_scale(device: str) -> float:
    """Phone pixels per image pixel, from the real image width - never from the requested one."""
    d = client().device(_resolve(device))
    try:
        o = d.observe(width=shot_width(), ui=False)
        _scale[device] = o["screen"]["w"] / float(o["image"]["w"])
    except ClousdError as e:
        if e.status != 404:
            raise
        w, _ = d.size()
        _scale[device] = w / float(_jpeg_w(d.screenshot(width=shot_width())))
    return _scale[device]


def _px(device: str, v: Optional[float]) -> int:
    if v is None:
        raise ValueError("coordinates are required for this action")
    scale = _scale.get(device) or _learn_scale(device)
    return int(round(v * scale))


def _err(e: Exception) -> str:
    if isinstance(e, ClousdError):
        return f"error: {e.message or e.error} ({e.status})"
    return f"error: {e}"


# ---- account and devices ------------------------------------------------------------------------------------------

@mcp.tool(annotations=ToolAnnotations(title="Account", readOnlyHint=True, openWorldHint=False))
def account(ctx: Optional[Context] = None) -> Dict[str, Any]:
    """The account: plan and phones, money left, what is about to renew, and warnings. Check it before starting phones
    or long work, and tell the user every warning in plain words with the top_up link."""
    _use(ctx)
    try:
        return client().balance()
    except ClousdError as e:
        return {"error": _err(e)}


@mcp.tool(annotations=ToolAnnotations(title="Devices", readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def devices(op: Annotated[str, Field(description="list (default) | start | stop | rename")] = "list",
            device: Annotated[str, Field(description="device (name, label or number), for start / stop / rename")] = "",
            label: Annotated[str, Field(description="rename: the new name, up to 40 characters")] = "",
            ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Phones this key can use.
    op=list: name, label (the name people see in the dashboard, e.g. "Pixel 7 #3"), number, state (running / stopped /
    starting), model, Android version and network exit. Every tool takes the name, the label or the number.
    op=start / op=stop with device: start a stopped phone (one to three minutes until it has booted) or stop one (it keeps
    its apps and data). op=rename with device and label: give a phone a name (empty = back to "Model #N")."""
    _use(ctx)
    try:
        if op == "list":
            return {"devices": [d.data for d in client().devices()]}
        if op in ("start", "stop") and device:
            d = client().device(_resolve(device))
            return {"result": (d.start() if op == "start" else d.stop()).output or "ok"}
        if op == "rename" and device:
            r = client().device(_resolve(device)).rename(label)
            _names.pop(_key.get() or "env", None)
            return r
        return {"error": "op is list, start, stop or rename (they need device)"}
    except ClousdError as e:
        return {"error": _err(e)}


# ---- tasks ------------------------------------------------------------------------------------------------------

def _replay(run_id: str) -> str:
    return f"{SITE}/app/?run={run_id}"


def _run_view(run: Any, steps: int = 0) -> Dict[str, Any]:
    """What a model needs from a run: status, the answer or the question it waits on, and where to look."""
    d = run.data
    out: Dict[str, Any] = {"run": run.id, "status": run.status}
    for k in ("device", "answer", "error", "structured_output", "steps", "seconds", "subgoal"):
        if d.get(k) not in (None, "", 0):
            out[k] = d[k]
    card = d.get("card")
    if card:
        out["waiting_for"] = {k: v for k, v in card.items() if k in ("kind", "question", "options", "action", "allow_text")}
        out["next"] = "answer: reply with answer(run, text) or allow / decline"
    if d.get("children"):
        kids: Dict[str, Any] = {}
        try:
            for r in client().runs(since=d.get("created", ""), limit=200):
                if r.data.get("parent") == run.id:
                    k = {"run": r.id, "status": r.status}
                    if r.answer or r.error:
                        k["answer" if r.answer else "error"] = r.answer or r.error
                    if r.data.get("card"):
                        k["waiting_for"] = r.data["card"].get("question") or r.data["card"].get("action")
                    kids[r.data.get("device", r.id)] = k
        except ClousdError:
            pass
        out["devices"] = kids
        counts: Dict[str, int] = {}
        for k in kids.values():
            counts[k["status"]] = counts.get(k["status"], 0) + 1
        out["counts"] = counts
    if steps and not d.get("children"):
        try:
            tr = run.trajectory()[-steps:]
            out["last_steps"] = [f"{s.get('n')}. {s.get('tool', '')} {s.get('target', '')} - {str(s.get('result', ''))[:160]}".strip()
                                 for s in tr]
        except ClousdError:
            pass
    if not run.done and not card:
        out["next"] = "still working - call run_status with this run in 20-60 s"
    out["replay"] = _replay(run.id)
    return out


def _wait(run: Any, seconds: int) -> Any:
    end = time.time() + max(0, min(int(seconds), WAIT_MAX))
    while time.time() < end:
        run.refresh()
        if run.done or run.status == "paused":
            break
        time.sleep(3)
    return run


@mcp.tool(annotations=ToolAnnotations(title="Run a task", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
def run(task: Annotated[str, Field(description="the goal in plain words, e.g. \"Open Settings and tell me the Android version\" or "
                                               "\"Sign in to the app with the saved login and open the profile\"")],
        devices: Annotated[str, Field(description="one device or several: names, labels or numbers, comma-separated or a JSON list (up to 32)")],
        credentials: Annotated[str, Field(description="saved logins to use: \"package/name\" items, comma-separated, from the credentials tool")] = "",
        output_schema: Annotated[str, Field(description="optional JSON Schema: the answer also comes back as structured_output in this shape")] = "",
        mode: Annotated[str, Field(description="auto (default): works without stopping; ask: asks before risky steps (answer them)")] = "auto",
        max_steps: Annotated[int, Field(description="step limit, default 100")] = 0,
        wait_seconds: Annotated[int, Field(description=f"wait up to this long for the result, 0..{WAIT_MAX}")] = 40,
        ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Give phones a task. Clousd's agent looks at the screen, taps and types until the goal is reached, and finishes with
    an answer. Several devices = the same task on each, all at once (one run per phone under one parent). Returns the
    run with its status; if it is still going, follow it with run_status. A run in mode=ask may stop with waiting_for:
    reply with answer. Logins: never ask the user for passwords - pass credentials; their values are typed on the phone
    without you or the model seeing them."""
    _use(ctx)
    try:
        names = _devs(devices)
        if not names:
            return {"error": "devices: at least one device (see the devices tool)"}
        if len(names) > 32:
            return {"error": f"{len(names)} devices: up to 32 per run - split into several runs"}
        creds = []
        for x in [c.strip() for c in credentials.split(",") if c.strip()]:
            if "/" not in x:
                return {"error": f"credentials: \"package/name\" items, got {x!r}"}
            pk, nm = x.split("/", 1)
            creds.append({"package": pk.strip(), "name": nm.strip()})
        schema = json.loads(output_schema) if output_schema.strip() else None
        r = client().run(task, device=names[0] if len(names) == 1 else None, devices=names if len(names) > 1 else None,
                         credentials=creds or None, output_schema=schema, mode=mode if mode in ("auto", "ask") else "auto",
                         max_steps=max_steps or None)
        return _run_view(_wait(r, wait_seconds), steps=5)
    except (ClousdError, ValueError) as e:
        return {"error": _err(e)}


@mcp.tool(annotations=ToolAnnotations(title="Run status", readOnlyHint=True, openWorldHint=False))
def run_status(run: Annotated[str, Field(description="run id from run (or runs listed with op=list)")] = "",
               op: Annotated[str, Field(description="status (default) | list | cancel | message")] = "status",
               text: Annotated[str, Field(description="message: what to tell the running agent")] = "",
               wait_seconds: Annotated[int, Field(description=f"status: wait up to this long for it to finish or ask, 0..{WAIT_MAX}")] = 0,
               steps: Annotated[int, Field(description="status: how many last steps to show, 0..20")] = 5,
               ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Follow a run. op=status: status (queued, running, paused = waits for an answer, completed, failed, cancelled), the
    answer or error, the last steps, per-device results for a run on several phones, and a replay link for the user.
    op=list: recent runs of this key. op=cancel: stop it. op=message: tell the running agent something (it reads it
    before its next step)."""
    _use(ctx)
    try:
        c = client()
        if op == "list":
            return {"runs": [{"run": r.id, "status": r.status, "device": r.data.get("device") or r.data.get("devices"),
                              "task": str(r.data.get("task", ""))[:120], "created": r.data.get("created"),
                              "answer": str(r.answer or r.error)[:200]} for r in c.runs(limit=20)]}
        if not run:
            return {"error": "run is required (op=list shows recent runs)"}
        r = c.get_run(run)
        if op == "cancel":
            return _run_view(r.cancel())
        if op == "message":
            if not text:
                return {"error": "message needs text"}
            r.message(text)
            return {"ok": True, "run": r.id}
        if op != "status":
            return {"error": "op is status, list, cancel or message"}
        if wait_seconds:
            _wait(r, wait_seconds)
        return _run_view(r, steps=max(0, min(int(steps), 20)))
    except ClousdError as e:
        return {"error": _err(e)}


@mcp.tool(annotations=ToolAnnotations(title="Answer a run", readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
def answer(run: Annotated[str, Field(description="run id that waits (status paused, waiting_for)")],
           text: Annotated[str, Field(description="a question: your answer (or one of its options)")] = "",
           allow: Annotated[Optional[bool], Field(description="a permission: true to allow the step, false to decline it")] = None,
           ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Reply to a run that waits for you: answer its question with text, or allow / decline the step it asks
    permission for. If the question is something only the user knows, ask the user first. The run goes on at once."""
    _use(ctx)
    try:
        r = client().get_run(run)
        if allow is None and not text:
            return {"error": "give text (a question) or allow true/false (a permission)"}
        r.reply(answer=text if allow is None else None, allow=allow)
        return _run_view(_wait(r, 10), steps=3)
    except ClousdError as e:
        return {"error": _err(e)}


# ---- the screen, step by step -------------------------------------------------------------------------------------

def _numbered(o: Dict[str, Any], scale: float) -> tuple:
    """(lines for the model, [(cx, cy, label)] in device pixels, elements for the image) - one numbering for both"""
    if _elements_of is not None:
        els = _elements_of(o)
        lines, pts = [], []
        for e in els:
            cx, cy = e.center
            lines.append(f"[{e.n}] {e.label!r} ({e.cls}){' ' + e.flags if e.flags else ''} at ({int(cx / scale)}, {int(cy / scale)})")
            pts.append((cx, cy, e.label))
        return lines, pts, els
    lines, pts = [], []
    for n in o.get("ui", []):
        label = n.get("text") or n.get("desc") or n.get("id", "").split("/")[-1]
        if not label and not n.get("click"):
            continue
        b = n.get("b", [0, 0, 0, 0])
        cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
        lines.append(f"[{len(pts)}] {label[:60]!r} {n.get('class', '')} at ({int(cx / scale)}, {int(cy / scale)})")
        pts.append((cx, cy, label))
        if len(pts) >= 80:
            break
    return lines, pts, None


@mcp.tool(annotations=ToolAnnotations(title="Observe the screen", readOnlyHint=True, openWorldHint=False))
def observe(device: Device_,
            with_text: Annotated[bool, Field(description="also list the elements (numbered, centres in image pixels)")] = True,
            numbers: Annotated[bool, Field(description="draw the element numbers on the screenshot")] = True,
            ctx: Optional[Context] = None) -> list:
    """Look at the phone yourself: a fresh screenshot, the app on screen and (with_text) every labelled or tappable element
    with a number. Act on an element by its number (act element=N); coordinates, when needed, are in this image's pixels."""
    _use(ctx)
    d = client().device(_resolve(device))
    try:
        o = d.observe(width=shot_width(), ui=with_text)
    except ClousdError as e:
        return [_err(e)]
    img, scr = o.get("image", {}), o.get("screen", {})
    scale = scr.get("w", 1080) / float(img.get("w") or shot_width())
    _scale[device] = scale
    jpeg = o["image_bytes"]
    note = f"Screen image {img.get('w')}x{img.get('h')} px (phone {scr.get('w')}x{scr.get('h')}), observation #{o.get('seq')}."
    if o.get("package"):
        note += f"\nOn screen: {o.get('activity') or o.get('package')}"
    if with_text:
        if o.get("ui_error"):
            note += f"\nElements: unavailable ({o['ui_error']}) - use coordinates from the image"
            _els.pop(_slot(device), None)
        else:
            lines, pts, els = _numbered(o, scale)
            _els[_slot(device)] = {"seq": o.get("seq"), "pts": pts}
            note += "\nElements (act element=N):\n" + "\n".join(lines)
            if numbers and els and _annotate is not None:
                jpeg = _annotate(jpeg, els, scale)
    return [Image(data=jpeg, format="jpeg"), note]


ACTIONS = ("tap long_press swipe scroll tap_text wait_text type key open_app close_app open_url intent settings clipboard_set "
           "notifications_open notifications_close notifications_clear | read: installed notifications clipboard app_info crashes health")


@mcp.tool(annotations=ToolAnnotations(title="Act on the phone", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
def act(device: Device_,
        action: Annotated[str, Field(description="one of: " + ACTIONS)],
        element: Annotated[Optional[int], Field(description="element number from the last observe (tap, long_press)")] = None,
        x: Annotated[Optional[float], Field(description="x in screenshot pixels (tap, long_press, swipe start)")] = None,
        y: Annotated[Optional[float], Field(description="y in screenshot pixels")] = None,
        x2: Annotated[Optional[float], Field(description="swipe end x")] = None,
        y2: Annotated[Optional[float], Field(description="swipe end y")] = None,
        text: Annotated[str, Field(description="type: the text; key: home|back|recents|enter|tab|del|menu; scroll: down|up; "
                                                "open_app/close_app/app_info/crashes: package; open_url: https://...; "
                                                "tap_text/wait_text: text on screen; settings: wifi|bluetooth|display|sound|apps|"
                                                "location|about|battery|...; intent: a URI to view (geo:, tel:, https:...) or JSON "
                                                "{action,data,package,component,extras}; clipboard_set: the text")] = "",
        ms: Annotated[int, Field(description="swipe / long_press duration in ms")] = 300,
        timeout: Annotated[int, Field(description="wait_text: seconds to wait, up to 120")] = 30,
        ctx: Optional[Context] = None) -> str:
    """Do one thing on the phone yourself, then wait until the screen settles. Prefer element numbers from observe over
    coordinates; tap_text taps the element whose text contains `text`. Typing goes into the focused field (tap it first)
    and works in any language. "the screen did not change" means the action probably missed - observe again.
    Read without touching the screen: installed (apps), notifications (the shade), clipboard, app_info (version,
    installer, running), crashes (recent app crashes and ANRs), health (running, booted, network exit, last check).
    For a whole goal, prefer run: it does all the steps for you."""
    _use(ctx)
    d = client().device(_resolve(device))
    try:
        if action == "installed":
            return ", ".join(d.installed())
        if action == "notifications":
            return json.dumps(d.notifications(), ensure_ascii=False)[:6000]
        if action == "clipboard":
            return d.clipboard_get()
        if action == "app_info":
            return json.dumps(d.app_info(text), ensure_ascii=False) if text else "error: app_info needs the package in text"
        if action == "crashes":
            return json.dumps(d.crashes(text), ensure_ascii=False)[:6000]
        if action == "health":
            return json.dumps(d.health(), ensure_ascii=False)[:4000]
        if action in ("tap", "long_press") and element is not None:
            last = _els.get(_slot(device))
            if not last or not 0 <= element < len(last["pts"]):
                return "error: no such element - call observe first and use a number from its list"
            cx, cy, _label = last["pts"][element]
            body: Dict[str, Any] = {"op": action, "x": cx, "y": cy}
            if last.get("seq") is not None:
                body["seq"] = last["seq"]   # the number is only meaningful on the screen it was observed on
            if action == "long_press":
                body["ms"] = max(ms, 600)
        elif action == "tap":
            body = {"op": "tap", "x": _px(device, x), "y": _px(device, y)}
        elif action == "long_press":
            body = {"op": "long_press", "x": _px(device, x), "y": _px(device, y), "ms": max(ms, 600)}
        elif action == "swipe":
            body = {"op": "swipe", "x": _px(device, x), "y": _px(device, y), "x2": _px(device, x2), "y2": _px(device, y2), "ms": ms}
        elif action == "scroll":
            body = {"op": "scroll", "text": text or "down"}
        elif action in ("tap_text", "wait_text"):
            body = {"op": action, "text": text}
            if action == "wait_text":
                body["ms"] = max(1, min(int(timeout), 120)) * 1000
        elif action == "type":
            body = {"op": "text", "text": text}
        elif action == "key":
            body = {"op": "key", "key": text}
        elif action in ("open_app", "close_app"):
            body = {"op": action, "package": text}
        elif action == "open_url":
            body = {"op": "url", "url": text}
        elif action == "settings":
            body = {"op": "settings", "text": text}
        elif action == "intent":
            t = text.strip()
            it = json.loads(t) if t.startswith("{") else {"action": "android.intent.action.VIEW", "data": t}
            body = {"op": "intent", "intent": it}
        elif action == "clipboard_set":
            body = {"op": "clipboard_set", "text": text}
        elif action in ("notifications_open", "notifications_close", "notifications_clear"):
            body = {"op": action}
        else:
            return "error: unknown action - one of " + ACTIONS
        op = body.pop("op")
        try:
            r = d.act(op, settle=True, **body)
        except ClousdError as e:
            if e.status == 409 and e.error == "stale":
                _els.pop(_slot(device), None)
                return "error: the screen changed since the last observe - call observe again and use its numbers"
            raise
        out = r.get("output") or "ok"
        if r.get("changed") is False:
            return out + " (the screen did not change - the action probably missed; observe again)"
        return out + ("" if r.get("settled", True) else " (screen still changing)")
    except (ClousdError, ValueError) as e:
        return _err(e)


# ---- logins and schedules -----------------------------------------------------------------------------------------

@mcp.tool(annotations=ToolAnnotations(title="Saved logins", readOnlyHint=True, openWorldHint=False))
def credentials(package: Annotated[str, Field(description="only logins of this app package (empty = all)")] = "",
                ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Saved logins of the account: package, name and which fields are set (username, email, password, phone, totp...).
    Values are never shown. Pass one to run as "package/name"; the agent types it on the phone without seeing it.
    The user adds logins in the dashboard (Tasks → Sign-in) - do not ask them for passwords in the chat."""
    _use(ctx)
    try:
        rows = client().credentials(package)
        return {"credentials": [{"use": f"{x.get('package', '')}/{x.get('name', '')}", **{k: v for k, v in x.items() if k != "id"}} for x in rows]}
    except ClousdError as e:
        return {"error": _err(e)}


@mcp.tool(annotations=ToolAnnotations(title="Schedules", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
def schedules(op: Annotated[str, Field(description="list (default) | add | toggle | delete")] = "list",
              task: Annotated[str, Field(description="add: the task in plain words, like for run")] = "",
              devices: Annotated[str, Field(description="add: devices, comma-separated or a JSON list")] = "",
              at: Annotated[str, Field(description="add: time HH:MM")] = "",
              days: Annotated[str, Field(description="add: days 1-7 (1 = Monday), comma-separated; empty = every day")] = "",
              tz: Annotated[str, Field(description="add: IANA time zone of `at`, e.g. Europe/Berlin (ask the user; default UTC)")] = "UTC",
              credentials: Annotated[str, Field(description="add: saved logins \"package/name\", comma-separated")] = "",
              name: Annotated[str, Field(description="add: a short name")] = "",
              schedule: Annotated[str, Field(description="toggle / delete: schedule id from op=list")] = "",
              ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Tasks that run by themselves. op=list: id, name, task, phones, at, days, tz, enabled and the runs it started last
    time. op=add: run `task` on `devices` at `at` on `days` (each firing starts runs like the run tool). op=toggle: pause
    or resume. op=delete: remove it."""
    _use(ctx)
    try:
        c = client()
        if op == "list":
            return {"schedules": c.schedules()}
        if op == "add":
            if not task or not devices or not at:
                return {"error": "add needs task, devices and at (HH:MM)"}
            creds = []
            for x in [s.strip() for s in credentials.split(",") if s.strip()]:
                pk, _, nm = x.partition("/")
                creds.append({"package": pk, "name": nm})
            dd = [int(x) for x in days.replace(" ", "").split(",") if x]
            return {"schedule": c.add_schedule(task, _devs(devices), at, dd, tz or "UTC", name=name, credentials=creds or None)}
        if op in ("toggle", "delete"):
            if not schedule:
                return {"error": f"{op} needs schedule (its id from op=list)"}
            return c.toggle_schedule(schedule) if op == "toggle" else c.delete_schedule(schedule)
        return {"error": "op is list, add, toggle or delete"}
    except (ClousdError, ValueError) as e:
        return {"error": _err(e)}


# ---- serving ----------------------------------------------------------------------------------------------------

class _BearerGate:
    """Remote server: a request without `Authorization: Bearer …` gets 401 with the OAuth resource metadata (RFC 9728),
    so connectors (Claude.ai, ChatGPT…) start the sign-in; a key in the address gets a plain refusal."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("method") != "OPTIONS":
            hdrs = {k.lower(): v for k, v in scope.get("headers") or []}
            if not hdrs.get(b"authorization", b"").lower().startswith(b"bearer "):
                qs = (scope.get("query_string") or b"").lower()
                in_url = any(p in qs for p in (b"api_key", b"apikey", b"clousdapikey", b"config=", b"key="))
                msg = ("keys in the address are not accepted - send Authorization: Bearer cl_live_…" if in_url
                       else "sign in: send Authorization: Bearer <your Clousd key>, or connect with OAuth")
                body = json.dumps({"error": "unauthorized", "message": msg}).encode()
                await send({"type": "http.response.start", "status": 401, "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", ('Bearer resource_metadata="' + RESOURCE_META + '"').encode()),
                    (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def main() -> None:
    """stdio by default. CLOUSD_MCP_HTTP=host:port runs the remote (streamable HTTP) server at /mcp: every request
    carries the customer's key, sessions are stateless, so any number of clients share one process."""
    http = os.environ.get("CLOUSD_MCP_HTTP", "").strip()
    if not http:
        mcp.run()
        return
    host, _, port = http.rpartition(":")
    host, port_n = host or "127.0.0.1", int(port or 8790)
    kw: Dict[str, Any] = dict(stateless_http=True, json_response=True)
    # DNS-rebinding protection of the SDK allows only localhost Host headers; behind a reverse proxy the public name
    # arrives as Host, so list it in CLOUSD_MCP_HOSTS (comma-separated), e.g. CLOUSD_MCP_HOSTS=api.clousd.com
    try:
        from mcp.server.transport_security import TransportSecuritySettings
        hosts = [h.strip() for h in os.environ.get("CLOUSD_MCP_HOSTS", "").split(",") if h.strip()]
        hosts += [f"{host}:{port_n}", f"localhost:{port_n}", f"127.0.0.1:{port_n}"]
        kw["transport_security"] = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=["https://" + h for h in hosts] + ["http://" + h for h in hosts])
    except ImportError:
        pass
    try:
        app = mcp.streamable_http_app(host=host, **kw)   # mcp 2.x
    except TypeError:   # mcp 1.x: options live in settings
        for k_, v in kw.items():
            setattr(mcp.settings, k_, v)
        app = mcp.streamable_http_app()
    import uvicorn
    uvicorn.run(_BearerGate(app), host=host, port=port_n, log_level="warning")


if __name__ == "__main__":
    main()
