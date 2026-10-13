"""MCP server for CLOUSD cloud phones: gives any MCP-capable agent a real Android phone to look at and act on.

Nine tools (a few large tools are easier for a model to choose from than dozens of small ones):
  devices   - list the phones this key can use, start or stop one
  observe   - the screen as an image with a number on every element, plus the elements as text
  act       - tap (by element number or point), long_press, swipe, scroll, type, key, open_app, close_app, open_url,
              tap_text, wait_text, intent, settings, clipboard_set, notifications_open / _close / _clear
  inspect   - read without touching the screen: notifications, clipboard, app info, crashes, health, installed apps
  snapshots - list, save, restore (reset between runs) or clone a phone
  recipe    - run an app recipe (built-in or your own steps) and record new ones from what is done on the phone
  fleet     - many phones in one call: list the built-in recipes, run a recipe or steps on up to 32 phones, follow progress
  network   - where phones go online: exit types and countries, the account's proxies, add your own, bind, new IP
  resources - saved accounts (no secrets), texts, the media library; upload and send files to phones

Screenshots come back `SHOT_WIDTH` px wide (540: ~1200 px long side on a 1080x2400 phone). act takes an element
number from the last observe, or coordinates in that screenshot; both are converted to the device here.

    CLOUSD_API_KEY=cl_live_... clousd-mcp          # stdio server
"""
from __future__ import annotations

import base64
import contextvars
import json
import os
import time
from typing import Annotated, Any, Dict, Optional

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
_width: contextvars.ContextVar = contextvars.ContextVar("clousd_shot_width", default=0)   # per request (remote: ?shotWidth=)


def shot_width() -> int:
    """Screenshot width for this request: `shotWidth` from the client's configuration (query or config JSON over
    HTTP), else CLOUSD_SHOT_WIDTH, else 540. Clamped to 240..1080."""
    w = _width.get() or SHOT_WIDTH
    return max(240, min(int(w), 1080))

mcp = _Server("clousd", instructions="Real Android phones in the cloud. For app tasks (sign in, warm up an account, "
              "publish, delete a post) first look at fleet op=recipes and run a recipe - it waits for the right screens and "
              "handles popups; tap step by step with observe/act only when no recipe fits. For several phones use one "
              "fleet op=run for all of them (it runs them in parallel and returns a job), never a loop of act calls. "
              "Exits and proxies: network (bind a proxy to phones, add the user's own proxy, new IP); saved accounts, texts and "
              "media: resources - sign in with an account id, never ask the user for passwords. "
              "Call observe before acting and act on element numbers from it; use inspect to read notifications, app "
              "versions or crashes without touching the screen; use snapshots restore to start each run from the same state.")
_clients: Dict[str, Clousd] = {}   # API key → client (one per key; the remote server serves many keys)
_key: contextvars.ContextVar = contextvars.ContextVar("clousd_key", default="")
_scale: Dict[str, float] = {}      # device → device pixels per screenshot pixel
_els: Dict[str, Dict[str, Any]] = {}   # key+device → {seq, pts:[(cx, cy, label)]} of the last observe

Device_ = Annotated[str, Field(description="Device from devices (op=list): its name, its label from the dashboard (e.g. \"Anna IG\" or \"Pixel 7 #3\") or its number")]


def _use(ctx: Optional[Context]) -> None:
    """Remember which API key this request carries. Over HTTP (the remote server at api.clousd.com/mcp) the key comes
    with every request: `Authorization: Bearer cl_live_…`, `X-API-Key`, or `?api_key=` / `?clousdApiKey=` /
    `?config=<base64 JSON with clousdApiKey>` for clients that pass configuration in the URL. Over stdio there is no
    request and the key is CLOUSD_API_KEY from the environment."""
    key = ""
    req = None
    try:
        req = getattr(ctx.request_context, "request", None) if ctx is not None else None
    except Exception:   # stdio: no request context at all
        req = None
    try:
        # mcp 2.x: ctx.headers; mcp 1.x: only the transport request carries them
        h = getattr(ctx, "headers", None) if ctx is not None else None
        if h is None and req is not None:
            h = getattr(req, "headers", None)
        if h:
            auth = h.get("authorization", "") or h.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                key = auth[7:].strip()
            key = key or h.get("x-api-key", "") or h.get("X-API-Key", "")
    except Exception:
        key = ""
    width = 0
    try:
        q = getattr(req, "query_params", None)
        if q:
            try:
                width = int(q.get("shotWidth", "") or q.get("shot_width", "") or 0)
            except ValueError:
                width = 0
        if not key and q:
            key = q.get("api_key", "") or q.get("clousdApiKey", "") or q.get("CLOUSD_API_KEY", "")
            if not key and q.get("config"):
                try:
                    cfg = json.loads(base64.b64decode(q["config"] + "=" * (-len(q["config"]) % 4)))
                    key = cfg.get("clousdApiKey") or cfg.get("CLOUSD_API_KEY") or cfg.get("api_key") or ""
                    width = width or int(cfg.get("shotWidth") or 0)
                except Exception:
                    key = ""
    except Exception:
        key = ""
    _key.set(key)
    _width.set(width)


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
    """A device by its API name, its name in the dashboard ("Anna IG", "Pixel 7 #3", case-insensitive) or its number
    ("3", "#3"). Unknown values pass through and the API answers 404."""
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


@mcp.tool(annotations=ToolAnnotations(title="Devices", readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def devices(op: Annotated[str, Field(description="list (default) | start | stop | rename")] = "list",
            device: Annotated[str, Field(description="device (name, label or number), for start / stop / rename")] = "",
            label: Annotated[str, Field(description="rename: the new name, up to 40 characters")] = "",
            ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Phones this API key can use.
    op=list: name, label (the name people see in the dashboard, e.g. "Anna IG" or "Pixel 7 #3"), number, state (running /
    stopped / starting), model, Android version, network exit, the app accounts linked to it and its proxy_id. Every
    tool takes the name, the label or the number. op=rename with device and label: give a phone a name (empty = back to
    "Model #N"); name phones after their accounts to keep a fleet readable.
    op=start / op=stop with `device`: start a stopped phone (waits until it has booted, one to three minutes) or stop
    one (a stopped phone keeps its state and costs nothing)."""
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
    """Look at the phone: a fresh screenshot, the app on screen and (with_text) every labelled or tappable element with
    a number. Act on an element by its number (act element=N); coordinates, when needed, are in this image's pixels."""
    _use(ctx)
    d = client().device(_resolve(device))
    try:
        o = d.observe(width=shot_width(), ui=with_text)
    except ClousdError as e:
        if e.status != 404:
            return [_err(e)]
        o = None   # an older gateway without /observe: screenshot + texts the old way
    if o is None:
        try:
            jpeg = d.screenshot(width=shot_width())
            w, h = d.size()
        except ClousdError as e:
            return [_err(e)]
        iw = _jpeg_w(jpeg)
        _scale[device] = w / float(iw)
        note = f"Screen image {iw}x{int(h / _scale[device])} px (phone {w}x{h})."
        if with_text:
            try:
                note += "\nTexts on screen: " + "; ".join(d.screen_text())
            except ClousdError as e:
                note += f"\nTexts on screen: unavailable ({e.message or e.error})"
        return [Image(data=jpeg, format="jpeg"), note]
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


ACTIONS = ("tap long_press swipe scroll tap_text wait_text type key open_app close_app open_url installed intent settings "
           "clipboard_set notifications_open notifications_close notifications_clear")


@mcp.tool(annotations=ToolAnnotations(title="Act on the phone", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
def act(device: Device_,
        action: Annotated[str, Field(description="one of: " + ACTIONS)],
        element: Annotated[Optional[int], Field(description="element number from the last observe (tap, long_press)")] = None,
        x: Annotated[Optional[float], Field(description="x in screenshot pixels (tap, long_press, swipe start)")] = None,
        y: Annotated[Optional[float], Field(description="y in screenshot pixels")] = None,
        x2: Annotated[Optional[float], Field(description="swipe end x")] = None,
        y2: Annotated[Optional[float], Field(description="swipe end y")] = None,
        text: Annotated[str, Field(description="type: the text; key: home|back|recents|enter|tab|del|menu; scroll: down|up; "
                                                "open_app/close_app: package; open_url: https://...; tap_text/wait_text: text on screen; "
                                                "settings: wifi|bluetooth|display|sound|apps|location|about|battery|...; "
                                                "intent: a URI to view (geo:, tel:, https:...) or JSON {action,data,package,component,extras}; "
                                                "clipboard_set: the text")] = "",
        ms: Annotated[int, Field(description="swipe / long_press duration in ms")] = 300,
        timeout: Annotated[int, Field(description="wait_text: seconds to wait, up to 120")] = 30,
        ctx: Optional[Context] = None) -> str:
    """Do one thing on the phone, then wait until the screen settles. Prefer element numbers from observe over
    coordinates; tap_text taps the element whose text contains `text`. Typing goes into the focused field (tap it
    first) and works in any language. Returns what happened; "the screen did not change" means the action probably
    missed - observe again."""
    _use(ctx)
    d = client().device(_resolve(device))
    try:
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
        elif action == "installed":
            return ", ".join(d.installed())
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
            out = r.get("output") or "ok"
            if r.get("changed") is False:
                return out + " (the screen did not change - the action probably missed; observe again)"
            return out + ("" if r.get("settled", True) else " (screen still changing)")
        except ClousdError as e:
            if e.status == 409 and e.error == "stale":
                _els.pop(_slot(device), None)
                return "error: the screen changed since the last observe - call observe again and use its numbers"
            if e.status != 404:
                raise
            body.pop("seq", None)
            return d.action(op, **body).get("output") or "ok"   # older gateway without /act
    except (ClousdError, ValueError) as e:
        return _err(e)


@mcp.tool(annotations=ToolAnnotations(title="Inspect the phone", readOnlyHint=True, openWorldHint=False))
def inspect(device: Device_,
            what: Annotated[str, Field(description="notifications | clipboard | app | crashes | health | installed")],
            package: Annotated[str, Field(description="app: the package to describe; crashes: only this package")] = "",
            ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Read the phone without touching the screen.
    notifications: the shade as a list (app, title, text, time). clipboard: the clipboard text.
    app: installed version, install/update time, installer, whether it runs or is on screen.
    crashes: recent app crashes, native crashes and ANRs with the first lines of the stack.
    health: running, booted, network exit and the latest automatic check of the phone.
    installed: packages of apps that can be opened."""
    _use(ctx)
    d = client().device(_resolve(device))
    try:
        if what == "notifications":
            return {"notifications": d.notifications()}
        if what == "clipboard":
            return {"text": d.clipboard_get()}
        if what == "app":
            if not package:
                return {"error": "app needs package"}
            return {"app": d.app_info(package)}
        if what == "crashes":
            return {"crashes": d.crashes(package)}
        if what == "health":
            return {"health": d.health()}
        if what == "installed":
            return {"packages": d.installed()}
        return {"error": "what is notifications, clipboard, app, crashes, health or installed"}
    except ClousdError as e:
        return {"error": _err(e)}


@mcp.tool(annotations=ToolAnnotations(title="Snapshots", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False))
def snapshots(device: Device_,
              op: Annotated[str, Field(description="list (default) | save | restore | clone")] = "list",
              snapshot_id: Annotated[str, Field(description="restore / clone: id from op=list")] = "",
              new_name: Annotated[str, Field(description="clone: name of the new phone (letters and digits, up to 16)")] = "",
              ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Saved states of a phone, for starting every run from the same point.
    op=list: id, created, android, current, restorable. op=save: save the current state.
    op=restore with snapshot_id: reset the phone to that state (what happened since is lost).
    op=clone with snapshot_id and new_name: a new phone from that state."""
    _use(ctx)
    d = client().device(_resolve(device))
    try:
        if op == "list":
            return {"snapshots": d.snapshots()}
        if op == "save":
            return {"result": d.save_snapshot().output or "saved"}
        if op == "restore" and snapshot_id:
            return {"result": d.restore(snapshot_id).output or "restored"}
        if op == "clone" and snapshot_id and new_name:
            return {"result": "created " + d.clone(snapshot_id, new_name).name, "device": new_name}
        return {"error": "op is list, save, restore (snapshot_id) or clone (snapshot_id, new_name)"}
    except ClousdError as e:
        return {"error": _err(e)}


@mcp.tool(annotations=ToolAnnotations(title="Recipes", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
def recipe(device: Device_,
           op: Annotated[str, Field(description="run | record_start | record_status | record_stop")],
           package: Annotated[str, Field(description="run: app package of a built-in recipe (e.g. com.instagram.android)")] = "",
           action: Annotated[str, Field(description="run: built-in recipe name (e.g. warm, login, publish_post)")] = "",
           steps: Annotated[str, Field(description="run: your own recipe as JSON {app, steps:[...]} (e.g. a recorded draft)")] = "",
           vars: Annotated[str, Field(description='run: variables as JSON, e.g. {"seconds": "300"}')] = "",
           ctx: Optional[Context] = None) -> Dict[str, Any]:
    """App recipes on one phone: scripted flows that wait for the right screen at each step, deal with popups and report
    what they did. op=run runs a built-in recipe (package + action - the list with inputs is fleet op=recipes) or your own
    JSON and returns when it is finished; for several phones use fleet op=run instead.
    op=record_start begins recording what is done on the phone (through act, or by a person in the live view);
    record_status shows progress; record_stop returns the recorded draft recipe, ready for op=run."""
    _use(ctx)
    d = client().device(_resolve(device))
    try:
        if op == "run":
            v = json.loads(vars) if vars.strip() else {}
            if steps.strip():
                r = d.recipe(recipe=json.loads(steps), vars=v)
            elif package and action:
                r = d.recipe(package, action, vars=v)
            else:
                return {"error": "run needs package + action, or steps"}
            return {"ok": bool(r.get("ok", True)), "result": r.get("output", ""), "job": r.get("job", "")}
        if op == "record_start":
            return d.record_start()
        if op == "record_status":
            return d.record_status()
        if op == "record_stop":
            return d.record_stop()
        return {"error": "op is run, record_start, record_status or record_stop"}
    except (ClousdError, ValueError) as e:
        return {"error": _err(e)}


def _devs(raw: str) -> list:
    raw = (raw or "").strip()
    if raw.startswith("["):
        return [_resolve(str(x)) for x in json.loads(raw)]
    return [_resolve(x.strip()) for x in raw.split(",") if x.strip()]


@mcp.tool(annotations=ToolAnnotations(title="Network exits and proxies", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
def network(op: Annotated[str, Field(description="options | status | proxies | add | check | bind | unbind | rotate | delete")],
            device: Annotated[str, Field(description="status: the device to look at")] = "",
            devices: Annotated[str, Field(description='bind / unbind: device names, JSON list or comma-separated')] = "",
            proxy_id: Annotated[int, Field(description="check / bind / rotate / delete: proxy id from op=proxies")] = 0,
            proxy: Annotated[str, Field(description="add: your own proxy - host:port:user:pass, user:pass@host:port or socks5://user:pass@host:port")] = "",
            type: Annotated[str, Field(description="add: instead of your own proxy, a Clousd exit - residential | mobile | isp | datacenter (see op=options), paid per GB")] = "",
            country: Annotated[str, Field(description="add: two-letter country of a Clousd exit, e.g. US")] = "",
            ip_type: Annotated[str, Field(description="add (own proxy): static | residential | mobile")] = "static",
            rotate_url: Annotated[str, Field(description="add (own residential/mobile proxy): the provider's link that changes the IP")] = "",
            name: Annotated[str, Field(description="add: a name to find it by")] = "",
            ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Where phones go online. Every phone uses one proxy of the account: your own proxy, or a Clousd exit (residential,
    mobile, ISP, datacenter in a country, paid per GB); time zone, locale and SIM follow it. op=options: exit types on
    sale and countries. op=status: the exit a device uses now (IP, country, carrier). op=proxies: the account's proxies
    and which devices use each. op=add: save your proxy (it is checked at once) or a Clousd exit. op=bind: put devices on
    a proxy. op=unbind: take them off (no internet until bound again). op=rotate: new IP for every running device on the
    proxy. op=check: test your proxy. op=delete: remove an unused proxy."""
    _use(ctx)
    try:
        c = client()
        if op == "options":
            return c.network_options()
        if op == "status":
            if not device:
                return {"error": "status needs device"}
            return c.device(device).network()
        if op == "proxies":
            return {"proxies": c.proxies()}
        if op == "add":
            if not proxy and not type:
                return {"error": "add needs proxy (your own) or type + country (a Clousd exit)"}
            return c.add_proxy(proxy=proxy, type=type, country=country, ip_type=ip_type, rotate_url=rotate_url, name=name)
        if op in ("check", "rotate", "delete"):
            if not proxy_id:
                return {"error": f"{op} needs proxy_id (op=proxies)"}
            return {"check": c.check_proxy, "rotate": c.rotate_proxy, "delete": c.delete_proxy}[op](proxy_id)
        if op == "bind":
            if not proxy_id or not devices:
                return {"error": "bind needs proxy_id and devices"}
            return c.bind_proxy(proxy_id, _devs(devices))
        if op == "unbind":
            if not devices:
                return {"error": "unbind needs devices"}
            return c.unbind_proxy(_devs(devices))
        return {"error": "op is options, status, proxies, add, check, bind, unbind, rotate or delete"}
    except (ClousdError, ValueError) as e:
        return {"error": _err(e)}


@mcp.tool(annotations=ToolAnnotations(title="Accounts, texts and media", readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
def resources(op: Annotated[str, Field(description="accounts | add_accounts | link_account | add_via_instagram | texts | add_texts | media | upload_media | send_media")],
              platform: Annotated[str, Field(description="accounts: filter by app package, e.g. com.instagram.android; add_via_instagram: com.facebook.katana or com.instagram.barcelona")] = "",
              accounts: Annotated[str, Field(description='add_accounts: JSON list of {platform, login, password, email?, email_password?, totp?, note?, device?}')] = "",
              account_id: Annotated[int, Field(description="link_account: account id from op=accounts; add_via_instagram: the Instagram account id")] = 0,
              device: Annotated[str, Field(description="link_account: the phone to link it to (empty = unlink)")] = "",
              type: Annotated[str, Field(description="texts / add_texts: caption | comment | bio | name | username")] = "",
              texts: Annotated[str, Field(description="add_texts: JSON list of strings")] = "",
              kind: Annotated[str, Field(description="media: image | video (empty = both)")] = "",
              path: Annotated[str, Field(description="upload_media: a file path on the machine running this server (local MCP only)")] = "",
              data_b64: Annotated[str, Field(description="upload_media: the file as base64 when there is no path (up to 25 MB)")] = "",
              name: Annotated[str, Field(description="upload_media: file name with extension, e.g. beach.jpg")] = "",
              media_ids: Annotated[str, Field(description="send_media: library ids, JSON list or comma-separated")] = "",
              devices: Annotated[str, Field(description="send_media: device names, JSON list or comma-separated")] = "",
              ctx: Optional[Context] = None) -> Dict[str, Any]:
    """The account's resources, the same as Resources in the dashboard. accounts: saved app accounts (login, status, the
    phone it is linked to; parent_id = it signs in through that Instagram) - passwords and 2FA secrets are never shown;
    add_via_instagram (account_id = an Instagram account, platform = com.facebook.katana or com.instagram.barcelona): a
    Facebook / Threads that signs in through that Instagram, on the same phone, no password. To sign a phone in, run fleet with the step
    {"op":"login","package":...,"account":<id> or "linked"} and the server fills the password itself. add_accounts /
    link_account: save accounts and link one to a phone. texts / add_texts: captions, comments, bios, names. media /
    upload_media: the photo and video library; publish one with fleet step {"op":"publish","package":...,"text":"post",
    "media_id":<id>,"caption":...}. send_media: copy files into phones' galleries as if taken by that phone."""
    _use(ctx)
    try:
        c = client()
        if op == "accounts":
            return {"accounts": c.accounts(platform)}
        if op == "add_accounts":
            lst = json.loads(accounts) if accounts.strip() else []
            if not isinstance(lst, list) or not lst:
                return {"error": "add_accounts needs accounts: a JSON list"}
            return {"ids": c.add_accounts(lst)}
        if op == "link_account":
            if not account_id:
                return {"error": "link_account needs account_id"}
            return c.link_account(account_id, device or None)
        if op == "add_via_instagram":
            if not account_id or not platform:
                return {"error": "add_via_instagram needs account_id (the Instagram) and platform"}
            return {"account": c.add_via_instagram(account_id, platform)}
        if op == "texts":
            return {"texts": c.texts(type)}
        if op == "add_texts":
            lst = json.loads(texts) if texts.strip() else []
            if not type or not isinstance(lst, list):
                return {"error": "add_texts needs type and texts (a JSON list)"}
            return {"added": c.add_texts(type, lst)}
        if op == "media":
            return {"media": c.media(kind)}
        if op == "upload_media":
            if path:
                if _key.get():   # remote server: no files of other machines
                    return {"error": "path works with the local server only; send data_b64 and name"}
                return {"media": c.upload_media(path, name)}
            if data_b64 and name:
                return {"media": c.upload_media(base64.b64decode(data_b64), name)}
            return {"error": "upload_media needs path (local server) or data_b64 + name"}
        if op == "send_media":
            ids = [int(x) for x in _devs(media_ids)]
            if not ids or not devices:
                return {"error": "send_media needs media_ids and devices"}
            return c.send_media(ids, _devs(devices))
        return {"error": "op is accounts, add_accounts, link_account, texts, add_texts, media, upload_media or send_media"}
    except (ClousdError, ValueError) as e:
        return {"error": _err(e)}


def _fleet_summary(job: Dict[str, Any]) -> Dict[str, Any]:
    """Job of a fleet run → state plus one short line per device (last step, ok or the error)."""
    try:
        per = json.loads(job.get("output") or "{}").get("per_phone", {}) or {}
    except ValueError:
        per = {}
    devices: Dict[str, Any] = {}
    for name, r in per.items():
        steps = r.get("steps") or []
        last = steps[-1] if steps else {}
        devices[name] = {"state": r.get("state", ""), "steps_done": len(steps),
                         "last": (last.get("op", "") + ": " + str(last.get("output", ""))[-200:]) if last else ""}
    counts: Dict[str, int] = {}
    for d in devices.values():
        counts[d["state"]] = counts.get(d["state"], 0) + 1
    return {"job": job.get("id", ""), "state": job.get("state", ""), "counts": counts, "devices": devices}


@mcp.tool(annotations=ToolAnnotations(title="Many phones at once", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
def fleet(op: Annotated[str, Field(description="recipes | run | status")],
          app: Annotated[str, Field(description="recipes: filter by app name or package, e.g. instagram")] = "",
          devices: Annotated[str, Field(description='run: device names as JSON list or comma-separated, e.g. ["dev_1","dev_2"]; "all" = every running device of this key')] = "",
          package: Annotated[str, Field(description="run: app package of the recipe, e.g. com.instagram.android")] = "",
          action: Annotated[str, Field(description="run: recipe action from op=recipes, e.g. warm, login, publish_post")] = "",
          vars: Annotated[str, Field(description='run: variables for every device as JSON, e.g. {"seconds": "900", "comment_chance": "0.1"}')] = "",
          vars_by_device: Annotated[str, Field(description='run: per-device variables as JSON, e.g. {"dev_1": {"login": "anna", "password": "..."}} (logins differ per phone)')] = "",
          steps: Annotated[str, Field(description='run: instead of a recipe, your own steps as JSON list, e.g. [{"op":"open_app","package":"com.android.chrome"},{"op":"wait","seconds":5}]')] = "",
          stagger: Annotated[str, Field(description="run: spread the starts, min,max seconds, e.g. 20,90 (phones do not act in lockstep)")] = "",
          job: Annotated[str, Field(description="status: job id returned by op=run")] = "",
          ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Work with many phones in one call - the way to automate, instead of tapping each phone step by step.
    op=recipes lists the built-in app recipes (sign in, warm up an account, publish, delete the last post...) with their
    inputs; prefer a recipe over act whenever one fits the task. op=run starts a recipe (package + action) or your own
    steps on up to 32 phones at once (they may live on different servers) and returns a job id right away - the phones
    work in parallel, minutes to an hour for a warm-up. op=status with job shows per-phone progress: call it every
    30-60 s until state is done or failed. Steps for op=run: recipe (package, action, vars), open_app / close_app
    (package), url (url), tap_text / wait_text (text, seconds), wait (seconds, seconds_max), scroll (text=down|up),
    browse (seconds 10-600: scroll like a person), install_app (package from the catalog), key (key), screenshot,
    rotate (new IP), if_text / unless_text (text, skip), login (package, account: an id from resources op=accounts or
    "linked" for the account linked to each phone - the server fills the password), publish (package, text: post|story|reel,
    media_id from resources op=media, caption). vars_by_device gives each phone its own account or media_id too, e.g.
    {"dev_1": {"account": "12"}, "dev_2": {"account": "13"}}."""
    _use(ctx)
    try:
        c = client()
        if op == "recipes":
            return {"recipes": c.recipes(app)}
        if op == "status":
            if not job:
                return {"error": "status needs job (from op=run)"}
            return _fleet_summary(c.job(job).data)
        if op == "run":
            raw = devices.strip()
            if raw == "all":
                names = [d.name for d in c.devices() if d.data.get("state") == "running"]
            else:
                names = _devs(raw)
            if not names:
                return {"error": "run needs devices (names, or \"all\")"}
            if len(names) > 32:
                return {"error": f"{len(names)} devices: at most 32 per run - split into several runs"}
            if steps.strip():
                st = json.loads(steps)
                if not isinstance(st, list):
                    return {"error": "steps must be a JSON list"}
            elif package and action:
                v = json.loads(vars) if vars.strip() else {}
                st = [{"op": "recipe", "package": package, "action": action, "vars": {k: str(x) for k, x in v.items()}}]
            else:
                return {"error": "run needs package + action (see op=recipes), or steps"}
            sg = [int(x) for x in stagger.replace(" ", "").split(",") if x] if stagger.strip() else None
            vb = {_resolve(k): v for k, v in json.loads(vars_by_device).items()} if vars_by_device.strip() else None
            j = c.run(names, st, stagger=sg, vars_by_device=vb, wait=False)
            return {"job": j.id, "state": j.state or "running", "devices": names,
                    "next": "call fleet op=status with this job every 30-60 s"}
        return {"error": "op is recipes, run or status"}
    except (ClousdError, ValueError) as e:
        return {"error": _err(e)}


def main() -> None:
    """stdio by default. CLOUSD_MCP_HTTP=host:port runs the remote (streamable HTTP) server at /mcp: every request
    carries the customer's key (see _use), sessions are stateless, so any number of clients share one process."""
    http = os.environ.get("CLOUSD_MCP_HTTP", "").strip()
    if not http:
        mcp.run()
        return
    host, _, port = http.rpartition(":")
    kw = dict(host=host or "127.0.0.1", port=int(port or 8790), stateless_http=True, json_response=True)
    # DNS-rebinding protection of the SDK allows only localhost Host headers; behind a reverse proxy the public name
    # arrives as Host, so list it in CLOUSD_MCP_HOSTS (comma-separated), e.g. CLOUSD_MCP_HOSTS=api.clousd.com
    try:
        from mcp.server.transport_security import TransportSecuritySettings
        hosts = [h.strip() for h in os.environ.get("CLOUSD_MCP_HOSTS", "").split(",") if h.strip()]
        hosts += [f"{kw['host']}:{kw['port']}", f"localhost:{kw['port']}", f"127.0.0.1:{kw['port']}"]
        kw["transport_security"] = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=["https://" + h for h in hosts] + ["http://" + h for h in hosts])
    except ImportError:
        pass
    try:
        mcp.run(transport="streamable-http", **kw)
    except TypeError:   # mcp 1.x: host/port live in settings
        for k_, v in kw.items():
            setattr(mcp.settings, k_, v)
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
