# clousd

Python client for [CLOUSD](https://clousd.com/agents/): cloud Android phones modelled on real devices, for scripts
and AI agents. Look at the screen, act on it, reset the phone to a saved state between runs, clone as many copies as
the job needs.

Early access: ask for a key at [clousd.com/agents](https://clousd.com/agents/#access).

```bash
pip install clousd
export CLOUSD_API_KEY=cl_live_...
```

## One run

```python
from clousd import Clousd

c = Clousd()                                   # key from CLOUSD_API_KEY
phone = c.device("dev_04f2")

snap = next(s for s in phone.snapshots() if s["restorable"])
phone.restore(snap["id"])                      # reset: every run starts from the same state

png = phone.screenshot(width=540)              # look (JPEG when width is given, PNG otherwise)
print(phone.screen_text())                     # every text on the screen

phone.open_app("com.android.chrome")           # act
phone.tap_text("Search")
phone.type("weather in Berlin")
phone.key("enter")

phone.wait_text("Berlin", timeout=30)          # check
```

## What a device can do

| Look | Act | State |
|---|---|---|
| `screenshot(width=None)` | `tap(x, y)`, `swipe(x, y, x2, y2, ms)`, `scroll("down")` | `snapshots()` |
| `screen_text()` | `type(text)`, `key("back")` | `save_snapshot()` |
| `find_text(text)` | `open_app(pkg)`, `close_app(pkg)`, `open_url(url)` | `restore(id)` |
| `size()` | `tap_text(text)`, `wait_text(text, timeout)` | `clone(id, name)` |
| `installed()`, `logs()` | `start()`, `stop()`, `restart()` | `c.job(id).wait()` |

Coordinates are device pixels (`size()` gives the screen). Long operations return a `Job`; they wait by default,
pass `wait=False` to poll yourself. Errors raise `ClousdError` with the HTTP status, a short code and a message.
The API allows 60 requests a minute per key; the client waits and retries on 429.

For checks from inside the phone (app databases, files, `dumpsys`) open an ADB session to the device from the
dashboard or `POST /v1/devices/{name}/adb`.

## Plug a phone into an AI agent

[`clousd-mcp`](../mcp) exposes the same operations as MCP tools for any MCP-capable agent.

MIT license.
