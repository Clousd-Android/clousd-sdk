# clousd

Python client for [CLOUSD](https://clousd.com/agents/): real Android phones in the cloud, built as specific real models,
for scripts and AI agents. Give a phone a task in plain words and get the answer, or drive the screen yourself.

```bash
pip install clousd
export CLOUSD_API_KEY=cl_live_...        # dashboard: Settings → Connections → API keys
```

## Give a phone a task

```python
from clousd import Clousd

c = Clousd()                                             # key from CLOUSD_API_KEY

run = c.run("Open Settings and tell me the Android version", device="dev_04f2", wait=True)
print(run.status, run.answer)                            # completed  Android 15

# the same task on several phones at once (up to 32): one run per phone under one parent
run = c.run("Open the Example Shop app and tell me what the first screen shows", devices=["dev_01", "dev_02"], wait=True)
for r in run.children:
    print(r.device, r.status, r.answer)

# a saved login is typed on the phone without you or the model seeing it (add logins in the dashboard or below)
login = c.credentials("com.example.shop")[0]
run = c.run("Sign in and tell me how many orders are on the Orders page", device="dev_01", credentials=[login],
            output_schema={"type": "object", "properties": {"orders": {"type": "integer"}}}, wait=True)
print(run.structured_output)                             # {"orders": 3}
```

Follow a run live and answer it when it asks:

```python
run = c.run("Book the earliest table for two tonight", device="dev_01", mode="ask")
for ev in run.events():                                  # status | step | plan | card | answer | message | end
    if ev["event"] == "step":
        print(ev["data"]["n"], ev["data"]["tool"], ev["data"]["result"])
    if ev["event"] == "card":                            # a question, or permission for a risky step
        run.reply(allow=True)                            # or run.reply("7 pm")
print(run.refresh().answer)
for s in run.trajectory():                               # every step: what it did and what happened
    print(s["n"], s["tool"], s["result"])
```

Repeat a task on a schedule, and get a webhook when it ends:

```python
c.add_schedule("Open the Orders page and report new orders", devices=["dev_01"], at="09:00", days=[1, 2, 3, 4, 5],
               tz="Europe/Berlin")
hook = c.add_webhook("https://example.com/clousd", events=["run.completed", "run.failed"])
print(hook["secret"])                                    # shown once: verify X-Clousd-Signature with it
```

| Tasks | Logins | Schedules and webhooks |
|---|---|---|
| `c.run(task, device=… / devices=[…], credentials, output_schema, mode, max_steps, timeout, wait)` | `c.credentials(package)` | `c.schedules()`, `c.add_schedule(...)` |
| `run.wait()`, `run.events()`, `run.trajectory()`, `run.step_image(n)` | `c.add_credential(package, name, fields)` | `c.toggle_schedule(id)`, `c.delete_schedule(id)` |
| `run.reply(answer / allow=)`, `run.message(text)`, `run.cancel()` | `c.set_credential_field(id, type, value)` | `c.webhooks()`, `c.add_webhook(url, events)` |
| `c.runs(status=, device=)`, `c.get_run(id)`, `device.run(task)` | `c.delete_credential(id)` | `c.test_webhook(id)`, `c.webhook_deliveries(id)` |

## Drive the screen yourself

```python
phone = c.device("dev_04f2")

snap = next(s for s in phone.snapshots() if s["restorable"])
phone.restore(snap["id"])                      # every run starts from the same state

phone.open_app("com.android.chrome")
phone.tap_text("Search")
phone.type("weather in Berlin")
phone.key("enter")
phone.wait_text("Berlin", timeout=30)

obs = phone.observe(width=540)                 # screenshot + UI tree + seq
phone.act("tap", seq=obs["seq"], x=540, y=1200, settle=True)
```

| Look | Act | State |
|---|---|---|
| `screenshot(width=None)`, `observe(width, ui)` | `tap(x, y)`, `swipe(...)`, `scroll("down")`, `long_press(...)` | `snapshots()`, `save_snapshot()` |
| `screen_text()`, `find_text(text)`, `size()` | `type(text)`, `key("back")`, `tap_text(text)`, `wait_text(text)` | `restore(id)`, `clone(id, name)` |
| `installed()`, `notifications()`, `clipboard_get()` | `open_app(pkg)`, `close_app(pkg)`, `open_url(url)`, `intent(...)`, `settings("wifi")` | `start()`, `stop()`, `restart()` |
| `app_info(pkg)`, `crashes(pkg)`, `health()`, `screenrecord(seconds)` | `act(op, settle, seq, ...)`, `clipboard_set(text)` | `c.job(id).wait()` |
| `c.devices()`, `c.find("Pixel 7 #3")`, `c.balance()` | `device.rename(...)`, `c.create_device(...)` | `device.network()`, `c.proxies()`, `c.bind_proxy(id, phones)` |

Coordinates are device pixels (`size()` gives the screen). Long device operations return a `Job`; they wait by default,
pass `wait=False` to poll yourself. Errors raise `ClousdError` with the HTTP status, a short code and a message. The API
allows 60 requests a minute per key; the client waits and retries on 429.

## Your own model, step by step

`clousd.agent` runs the loop "look at the screen, decide, act, check" on your machine with any OpenAI-compatible chat
model (llama.cpp, vLLM, Ollama, a hosted API): each step the model gets the screenshot with a number on every element
and calls one tool.

```python
from clousd import Clousd
from clousd.agent import Agent, Model

phone = Clousd().device("dev_04f2")
res = Agent(phone, Model("http://127.0.0.1:8080/v1"), trace_path="run.jsonl").run("Open Settings and tell me the Android version")
print(res.status, res.answer)
```

## Command line

```bash
clousd devices
clousd run "Open Settings and tell me the Android version" --device dev_04f2        # Clousd's agent, steps live
clousd run "Open the app and report the first screen" --device dev_01 --device dev_02
clousd runs --status failed
clousd agent "Turn on Dark theme" --device dev_04f2 --model-url http://127.0.0.1:8080/v1   # your own model
clousd bench --device dev_04f2 --model-url http://127.0.0.1:8080/v1      # 12 everyday tasks with automatic checks
clousd explore com.android.settings --device dev_04f2                   # map of the app's screens
```

## Upgrading from 0.3

`c.run(task, ...)` now starts a task (a Run). Removed: `recipes()`, `run_recipe()`, the group form of `run(devices,
steps)`, `accounts()` / `add_accounts()` / `link_account()` / `add_via_instagram()`, `texts()` / `add_texts()`,
`device.recipe()` and `record_*()`. Logins are `credentials()`, passed to a run.

[`clousd-mcp`](../mcp) exposes the same as MCP tools for any MCP-capable agent. MIT license.
