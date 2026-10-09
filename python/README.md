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
| `observe(width, ui)`, `screenrecord(seconds)` | `act(op, settle, seq, ...)`, `long_press(x, y, ms)` | `recipe(...)` |
| `notifications()`, `clipboard_get()` | `intent(action, data, ...)`, `settings("wifi")` | `record_start()` / `record_stop()` |
| `app_info(pkg)`, `crashes(pkg)`, `health()` | `clipboard_set(text)`, `notifications_open()` / `_clear()` | |

Coordinates are device pixels (`size()` gives the screen). Long operations return a `Job`; they wait by default,
pass `wait=False` to poll yourself. Errors raise `ClousdError` with the HTTP status, a short code and a message.
The API allows 60 requests a minute per key; the client waits and retries on 429.

For checks from inside the phone (app databases, files, `dumpsys`) open an ADB session to the device from the
dashboard or `POST /v1/devices/{name}/adb`.

## Give a phone a task

`clousd.agent` runs the loop "look at the screen, decide, act, check" with any OpenAI-compatible chat model (a local
llama.cpp or vLLM server, Ollama, a hosted API): each step the model gets the screenshot with a number on every element
and the elements as text, and calls one tool (tap element 12, type, scroll, open_app, done).

```python
from clousd import Clousd
from clousd.agent import Agent, Model

phone = Clousd().device("dev_04f2")
agent = Agent(phone, Model("http://127.0.0.1:8080/v1"), trace_path="run.jsonl")
res = agent.run("Open Settings and tell me which Android version this phone runs")
print(res.status, res.answer)          # done Android 14
```

Or from the command line (`pip install "clousd[image]"` adds Pillow for the numbered screenshots):

```bash
clousd run "Set an alarm for 7:30 AM" --device dev_04f2 --model-url http://127.0.0.1:8080/v1
clousd bench --device dev_04f2 --model-url http://127.0.0.1:8080/v1      # 12 everyday tasks with automatic checks
clousd explore com.android.settings --device dev_04f2                   # map of the app's screens + draft app pack
clousd record start --device dev_04f2   # ... do the flow on the phone ...   clousd record stop --device dev_04f2 --out flow.json
clousd recipe flow.json --device dev_04f2
```

Every step of a run is kept: what the model saw, what it called, what happened and how long each part took. A screen
that changed between the look and the action is never acted on (the API answers 409 and the loop looks again).

## Plug a phone into an AI agent

[`clousd-mcp`](../mcp) exposes the same operations as MCP tools for any MCP-capable agent.

MIT license.
