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
| `c.recipes(app)` | `c.run(devices, steps)`, `c.run_recipe(devices, pkg, action, vars)` | `job.results`, `job.failed` |

Coordinates are device pixels (`size()` gives the screen). Long operations return a `Job`; they wait by default,
pass `wait=False` to poll yourself. Errors raise `ClousdError` with the HTTP status, a short code and a message.
The API allows 60 requests a minute per key; the client waits and retries on 429.

For checks from inside the phone (app databases, files, `dumpsys`) open an ADB session to the device from the
dashboard or `POST /v1/devices/{name}/adb`.

## Many phones at once

A recipe runs an app flow (sign in, warm up, publish, delete the last post) without a model; `run_recipe` and `run`
start it on up to 32 phones in parallel, wherever they live, and return one `Job` with a result per phone.

```python
from clousd import Clousd

c = Clousd()
phones = [d for d in c.devices() if d.state == "running"]       # 10 phones, any servers

for r in c.recipes("instagram"):                               # what can run without a model
    print(r["action"], "-", r["about"], [i["name"] for i in r["inputs"]])

# sign every phone into its own account: one call, the phones work in parallel
accounts = {"dev_01": {"login": "anna.k", "password": "..."}, "dev_02": {"login": "mark_t", "password": "..."}}
job = c.run_recipe(list(accounts), "com.instagram.android", "login", vars_by_device=accounts)
print(job.failed)                                             # [] - or the devices to look at

# warm all ten up for 15 minutes, starts spread over 20-90 s so they do not move in lockstep
job = c.run_recipe(phones, "com.instagram.android", "warm",
                   {"seconds": 900, "comment_chance": 0.1, "comments": "Love this|So good|Wow"}, stagger=[20, 90])
for name, r in job.results.items():
    print(name, r["state"], r["steps"][-1]["output"])
```

`c.run(phones, steps)` takes your own chain of steps instead of a recipe (`open_app`, `url`, `tap_text`, `wait_text`,
`wait`, `browse`, `scroll`, `install_app`, `rotate`, `recipe`, ...). With `wait=False` it returns at once; poll
`c.job(job.id).refresh()` yourself.

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
clousd run "Turn on Dark theme" --device dev_04f2 --model-url ... --save-recipe dark.json   # the run becomes a recipe
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
