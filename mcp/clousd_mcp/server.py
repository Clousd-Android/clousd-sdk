"""MCP server for CLOUSD cloud phones: gives any MCP-capable agent a real Android phone to look at and act on.

Six tools (a few large tools are easier for a model to choose from than dozens of small ones):
  devices   - list the phones this key can use, start or stop one
  observe   - the screen as an image with a number on every element, plus the elements as text
  act       - tap (by element number or point), long_press, swipe, scroll, type, key, open_app, close_app, open_url,
              tap_text, wait_text, intent, settings, clipboard_set, notifications_open / _close / _clear
  inspect   - read without touching the screen: notifications, clipboard, app info, crashes, health, installed apps
  snapshots - list, save, restore (reset between runs) or clone a phone
  recipe    - run an app recipe (built-in or your own steps) and record new ones from what is done on the phone

Screenshots come back `SHOT_WIDTH` px wide (540: ~1200 px long side on a 1080x2400 phone). act takes an element
number from the last observe, or coordinates in that screenshot; both are converted to the device here.

    CLOUSD_API_KEY=cl_live_... clousd-mcp          # stdio server
"""
from __future__ import annotations

import base64
import contextvars
import json
import os
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

mcp = _Server("clousd", instructions="Real Android phones in the cloud. Call observe before acting and act on element "
              "numbers from it; use inspect to read notifications, app versions or crashes without touching the screen; "
              "use snapshots restore to start each run from the same state.")
_clients: Dict[str, Clousd] = {}   # API key → client (one per key; the remote server serves many keys)
_key: contextvars.ContextVar = contextvars.ContextVar("clousd_key", default="")
_scale: Dict[str, float] = {}      # device → device pixels per screenshot pixel
_els: Dict[str, Dict[str, Any]] = {}   # key+device → {seq, pts:[(cx, cy, label)]} of the last observe

Device_ = Annotated[str, Field(description="Device name from devices (op=list), e.g. dev_04f2")]


def _use(ctx: Optional[Context]) -> None:
    """Remember which API key this request carries. Over HTTP (the remote server at api.clousd.com/mcp) the key comes
    with every request: `Authorization: Bearer cl_live_…`, `X-API-Key`, or `?api_key=` / `?clousdApiKey=` /
    `?config=<base64 JSON with clousdApiKey>` for clients that pass configuration in the URL. Over stdio there is no
    request and the key is CLOUSD_API_KEY from the environment."""
    key = ""
    try:
        h = ctx.headers if ctx is not None else None
        if h:
            auth = h.get("authorization", "") or h.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                key = auth[7:].strip()
            key = key or h.get("x-api-key", "") or h.get("X-API-Key", "")
        req = getattr(ctx.request_context, "request", None) if ctx is not None else None
        q = getattr(req, "query_params", None)
        if not key and q:
            key = q.get("api_key", "") or q.get("clousdApiKey", "") or q.get("CLOUSD_API_KEY", "")
            if not key and q.get("config"):
                try:
                    cfg = json.loads(base64.b64decode(q["config"] + "=" * (-len(q["config"]) % 4)))
                    key = cfg.get("clousdApiKey") or cfg.get("CLOUSD_API_KEY") or cfg.get("api_key") or ""
                except Exception:
                    key = ""
    except Exception:
        key = ""
    _key.set(key)


def client() -> Clousd:
    key = _key.get() or os.environ.get("CLOUSD_API_KEY", "")
    if not key:
        raise ClousdError(401, "no_key", "No Clousd API key: send Authorization: Bearer cl_live_… (remote) or set CLOUSD_API_KEY (stdio)")
    c = _clients.get(key)
    if c is None:
        c = _clients[key] = Clousd(api_key=key)
    return c


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
    return SHOT_WIDTH


def _learn_scale(device: str) -> float:
    """Phone pixels per image pixel, from the real image width - never from the requested one."""
    d = client().device(device)
    try:
        o = d.observe(width=SHOT_WIDTH, ui=False)
        _scale[device] = o["screen"]["w"] / float(o["image"]["w"])
    except ClousdError as e:
        if e.status != 404:
            raise
        w, _ = d.size()
        _scale[device] = w / float(_jpeg_w(d.screenshot(width=SHOT_WIDTH)))
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
def devices(op: Annotated[str, Field(description="list (default) | start | stop")] = "list",
            device: Annotated[str, Field(description="device name, for start/stop")] = "",
            ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Phones this API key can use.
    op=list: name, state (running / stopped / starting), model, Android version and network exit of each phone.
    op=start / op=stop with `device`: start a stopped phone (waits until it has booted, one to three minutes) or stop
    one (a stopped phone keeps its state and costs nothing)."""
    _use(ctx)
    try:
        if op == "list":
            return {"devices": [d.data for d in client().devices()]}
        if op in ("start", "stop") and device:
            d = client().device(device)
            return {"result": (d.start() if op == "start" else d.stop()).output or "ok"}
        return {"error": "op is list, start or stop (start/stop need device)"}
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
    d = client().device(device)
    try:
        o = d.observe(width=SHOT_WIDTH, ui=with_text)
    except ClousdError as e:
        if e.status != 404:
            return [_err(e)]
        o = None   # an older gateway without /observe: screenshot + texts the old way
    if o is None:
        try:
            jpeg = d.screenshot(width=SHOT_WIDTH)
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
    scale = scr.get("w", 1080) / float(img.get("w") or SHOT_WIDTH)
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
    d = client().device(device)
    try:
        if action in ("tap", "long_press") and element is not None:
            last = _els.get(_slot(device))
            if not last or not 0 <= element < len(last["pts"]):
                return "error: no such element - call observe first and use a number from its list"
            cx, cy, _label = last["pts"][element]
            body: Dict[str, Any] = {"op": action, "x": cx, "y": cy}
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
            if e.status != 404:
                raise
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
    d = client().device(device)
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
    d = client().device(device)
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
    """App recipes: scripted flows that wait for the right screen at each step, deal with popups and report what they
    did. op=run runs a built-in recipe (package + action) or your own JSON and returns when it is finished.
    op=record_start begins recording what is done on the phone (through act, or by a person in the live view);
    record_status shows progress; record_stop returns the recorded draft recipe, ready for op=run."""
    _use(ctx)
    d = client().device(device)
    try:
        if op == "run":
            v = json.loads(vars) if vars.strip() else {}
            if steps.strip():
                return {"result": d.recipe(recipe=json.loads(steps), vars=v)}
            if package and action:
                return {"result": d.recipe(package, action, vars=v)}
            return {"error": "run needs package + action, or steps"}
        if op == "record_start":
            return d.record_start()
        if op == "record_status":
            return d.record_status()
        if op == "record_stop":
            return d.record_stop()
        return {"error": "op is run, record_start, record_status or record_stop"}
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
