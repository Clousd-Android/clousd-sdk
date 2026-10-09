"""MCP server for CLOUSD cloud phones: gives any MCP-capable agent a real Android phone to look at and act on.

Four tools, on purpose (agents choose better from a few large tools than from a dozen small ones):
  devices   - list the phones this key can use, start or stop one
  observe   - the screen as an image plus every text on it
  act       - tap, swipe, scroll, tap_text, wait_text, type, key, open_app, close_app, open_url, installed
  snapshots - list, save, restore (reset between runs) or clone a phone

Screenshots come back `SHOT_WIDTH` px wide (540: ~1200 px long side on a 1080x2400 phone). tap/swipe take coordinates
in that screenshot and are scaled to the device here, so the model never converts pixels.

    CLOUSD_API_KEY=cl_live_... clousd-mcp          # stdio server
"""
from __future__ import annotations

import os
from typing import Dict, Optional

from clousd import Clousd, ClousdError

try:   # mcp 2.x: FastMCP became MCPServer
    from mcp.server.mcpserver import Image, MCPServer as _Server
except ImportError:   # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server, Image

SHOT_WIDTH = int(os.environ.get("CLOUSD_SHOT_WIDTH", "540"))

mcp = _Server("clousd", instructions="Real Android phones in the cloud. Call observe before acting; prefer act "
              "tap_text over coordinates; use snapshots restore to start each run from the same state.")
_client: Optional[Clousd] = None
_scale: Dict[str, float] = {}   # device name → device pixels per screenshot pixel


def client() -> Clousd:
    global _client
    if _client is None:
        _client = Clousd()
    return _client


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


@mcp.tool()
def devices(op: str = "list", device: str = "") -> object:
    """Phones this API key can use.
    op=list (default): name, state (running / stopped / starting / error), model, Android, network of each phone.
    op=start / op=stop with `device`: start a stopped phone (waits until it has booted, one to three minutes) or stop
    one (a stopped phone keeps its state and costs nothing)."""
    try:
        if op == "list":
            return [d.data for d in client().devices()]
        if op in ("start", "stop") and device:
            d = client().device(device)
            return (d.start() if op == "start" else d.stop()).output or "ok"
        return "error: op is list, start or stop (start/stop need device)"
    except ClousdError as e:
        return f"error: {e}"


def _elements(d: dict, scale: float, limit: int = 80) -> str:
    """Interactive and labelled elements, centres in screenshot pixels: what the model can tap."""
    rows = []
    for n in d.get("ui", []):
        label = n.get("text") or n.get("desc") or n.get("id", "").split("/")[-1]
        if not label and not n.get("click"):
            continue
        b = n.get("b", [0, 0, 0, 0])
        cx, cy = int((b[0] + b[2]) / 2 / scale), int((b[1] + b[3]) / 2 / scale)
        flags = "".join(f for f, k in ((" tap", "click"), (" scroll", "scroll"), (" focused", "focused"), (" off", "disabled")) if n.get(k))
        rows.append(f"- {label[:60]!r} {n.get('class', '')} at ({cx}, {cy}){flags}")
        if len(rows) >= limit:
            break
    return "\n".join(rows)


@mcp.tool()
def observe(device: str, with_text: bool = True) -> list:
    """Look at the phone: a fresh screenshot plus (with_text) the app on screen and its elements with centre
    coordinates. Give tap/swipe coordinates in this image's pixels."""
    d = client().device(device)
    try:
        o = d.observe(width=SHOT_WIDTH, ui=with_text)
    except ClousdError as e:
        if e.status != 404:
            return [f"error: {e}"]
        o = None   # an older gateway without /observe: screenshot + texts the old way
    if o is None:
        try:
            jpeg = d.screenshot(width=SHOT_WIDTH)
            w, h = d.size()
        except ClousdError as e:
            return [f"error: {e}"]
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
    note = f"Screen image {img.get('w')}x{img.get('h')} px (phone {scr.get('w')}x{scr.get('h')}), observation #{o.get('seq')}."
    if o.get("package"):
        note += f"\nOn screen: {o.get('activity') or o.get('package')}"
    if with_text:
        if o.get("ui_error"):
            note += f"\nElements: unavailable ({o['ui_error']})"
        else:
            note += "\nElements (centre in image pixels):\n" + _elements(o, scale)
    return [Image(data=o["image_bytes"], format="jpeg"), note]


@mcp.tool()
def act(device: str, action: str, x: Optional[float] = None, y: Optional[float] = None,
        x2: Optional[float] = None, y2: Optional[float] = None, text: str = "", ms: int = 300,
        timeout: int = 30) -> str:
    """Do one thing on the phone, then wait until the screen settles. action is one of:
    tap (x, y) · swipe (x, y, x2, y2, ms) · scroll (text=down|up) · tap_text (text: tap the element whose text
    contains it - usually more reliable than coordinates) · wait_text (text, timeout up to 120 s) · type (text into the
    focused field, any language) · key (text=home|back|recents|enter|tab|del|menu|power|volume_up|volume_down) ·
    open_app (text=package, e.g. com.android.chrome) · close_app (text=package) · open_url (text=https://…) ·
    installed (list apps you can open). Coordinates are in the pixels of the last observe image."""
    d = client().device(device)
    try:
        if action == "tap":
            body = {"op": "tap", "x": _px(device, x), "y": _px(device, y)}
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
        else:
            return "error: unknown action - see the tool description"
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
        return f"error: {e}"


@mcp.tool()
def snapshots(device: str, op: str = "list", snapshot_id: str = "", new_name: str = "") -> object:
    """Saved states of a phone, for starting every run from the same point.
    op=list (default): id, created, android, current, restorable.
    op=save: save the current state. op=restore with snapshot_id: reset the phone to that state.
    op=clone with snapshot_id and new_name (letters and digits, up to 16): a new phone from that state."""
    d = client().device(device)
    try:
        if op == "list":
            return d.snapshots()
        if op == "save":
            return d.save_snapshot().output or "saved"
        if op == "restore" and snapshot_id:
            return d.restore(snapshot_id).output or "restored"
        if op == "clone" and snapshot_id and new_name:
            return "created " + d.clone(snapshot_id, new_name).name
        return "error: op is list, save, restore (snapshot_id) or clone (snapshot_id, new_name)"
    except ClousdError as e:
        return f"error: {e}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
