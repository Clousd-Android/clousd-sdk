# Clousd: real Android phones in the cloud, for AI agents and automation

[![tests](https://github.com/Clousd-Android/clousd-sdk/actions/workflows/tests.yml/badge.svg)](https://github.com/Clousd-Android/clousd-sdk/actions/workflows/tests.yml)
[![PyPI clousd](https://img.shields.io/pypi/v/clousd?label=clousd)](https://pypi.org/project/clousd/)
[![PyPI clousd-mcp](https://img.shields.io/pypi/v/clousd-mcp?label=clousd-mcp)](https://pypi.org/project/clousd-mcp/)
[![MCP registry](https://img.shields.io/badge/MCP%20registry-io.github.Clousd--Android%2Fclousd--mcp-6366f1)](https://registry.modelcontextprotocol.io)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## The pitch, in one breath

Every agent framework can tap a phone. Almost none has a phone worth tapping.

**Clousd is the phone.** Each device is a full Android system built as a specific real model, Pixel to Galaxy to
Xiaomi, with the hardware identity, Google Play, cameras, sensors and network of the handset in a user's pocket. Your
agent, your script or your test suite drives it over HTTPS: look at the screen, act on it, check the result, reset to
a saved state, clone it a hundred times. Apps see a phone, not an emulator, so the agent sees the screens a person
sees.

This repository is the public side of it: the OpenAPI description, the Python client, the MCP server and the agent
loop. Early access: keys on request at [clousd.com/agents](https://clousd.com/agents/#access).

## Mix and match

Pick one from each column. They all compose.

| Pick a phone | Pick a brain | Pick a driver |
|---|---|---|
| **Pixel** 6 to 11, Android 12 to 17 | Any **MCP client**: Cursor, Windsurf, Zed, desktop assistants, agent frameworks | **MCP server** `clousd-mcp`, local (stdio) or hosted at `https://api.clousd.com/mcp` |
| **Galaxy** S, A, M, Z series | Any **OpenAI-compatible model**: hosted APIs, OpenRouter, Ollama, vLLM on your GPU | **Python client** `clousd` + `clousd.agent` task loop |
| **Xiaomi, Redmi, POCO, Motorola, OnePlus, realme, OPPO, vivo, Sony, TECNO, Infinix** and more, 150+ models | No model at all: **recipes** replay a recorded flow in seconds | **CLI** `clousd run "book a table" --device dev_04f2` |
| Network exit in the country you choose: residential, mobile, ISP, datacenter | A person in the browser: the dashboard streams the screen live | **REST API** (`openapi.yaml`, [reference](https://clousd.com/docs/reference/)) |

The phone stays the same whatever you plug in. A new model release is a free upgrade to your phone agent.

## What it does

**The phone taps what you'd tap**
- `observe`: a fresh screenshot with every element numbered, the app in front, and the UI tree with text, ids and bounds
- `act`: tap by element number or point, long press, swipe, scroll, type in any language, keys, open apps and links,
  intents, settings pages, clipboard, notification shade; the server waits until the screen settles and refuses an
  action taken on a stale screen (`409 stale`)
- `inspect`: notifications, clipboard, app versions, crashes and ANRs from the log, health, without touching the screen
- ADB over a relay when a check has to go deeper than the screen: one-time code, IP allow-list, expiry

**A phone that apps believe**
- Built from a snapshot of the real handset: build fingerprint, properties, sensors, cameras, codecs, GPU strings,
  audio, screen; Google Play and Play services inside
- Its own network exit, SIM profile, time zone and locale that follow the exit; a kill-switch below Android so
  nothing leaves the phone outside that exit
- Snapshots: save a prepared state, restore it before every run in seconds, clone it into ten or a hundred phones with
  their own identities

**Teach it once, replay for free**
- Recipes: scripted flows per app (login, warm-up, publish, delete) that wait for the right screen at each step and
  deal with popups; built-in recipes for the big social apps, yours in JSON
- Record a recipe from what is done on the phone (through the API or by a person in the live view), then replay it on
  any number of phones without a model
- App packs: named elements with a selector chain per app version, popups handled once per app

**Scale the fleet**
- Start with one phone, run hundreds; stopped phones cost nothing
- Groups and schedules in the dashboard, jobs and webhooks in the API
- Benchmark harness: run AndroidWorld-style task sets on real device models with any model behind the loop

## Why it is built this way

**Emulators are the first thing an app checks for.** Goldfish hardware, emulator builds, no SIM, a datacenter IP: the
"unusual activity" path starts before your agent has tapped anything. A phone built as a real model with a real-looking
network takes the same code path a user takes.

**Every episode should start from the same state.** Evaluation, training and testing are only comparable when run N
starts exactly where run 1 did. Snapshots restore in seconds; clones give parallel episodes without copying a phone
by hand.

**Thinking costs tokens, replaying costs nothing.** Use a model for the unknown task; once a flow works, record it
and replay it as a recipe. The same phone runs both.

**Your keys, your phones.** API keys carry a scope (`read` or `control`) and an explicit list of phones. A read key
can observe and inspect but never acts. Over HTTP the key travels with every request; the hosted MCP server keeps
nothing between requests.

## Quick start

### MCP (any client)

```bash
pip install clousd-mcp
```

```json
{
  "mcpServers": {
    "clousd": { "command": "clousd-mcp", "env": { "CLOUSD_API_KEY": "cl_live_..." } }
  }
}
```

Or no install at all, the hosted server:

```json
{
  "mcpServers": {
    "clousd": { "url": "https://api.clousd.com/mcp", "headers": { "Authorization": "Bearer cl_live_..." } }
  }
}
```

### Python

```bash
pip install clousd
export CLOUSD_API_KEY=cl_live_...
```

```python
from clousd import Clousd

c = Clousd()
phone = c.device("dev_04f2")

snap = next(s for s in phone.snapshots() if s["restorable"])
phone.restore(snap["id"])            # every run starts from the same state

phone.open_app("com.android.chrome")
phone.tap_text("Search")
phone.type("weather in Berlin")
phone.key("enter")
phone.wait_text("Berlin", timeout=30)

obs = phone.observe(width=540)       # screenshot + UI tree + seq
phone.act("tap", seq=obs["seq"], x=540, y=1200, settle=True)
```

### A task with a model

```bash
clousd run "Open Settings and tell me the Android version" --device dev_04f2 \
  --model-url http://127.0.0.1:8080/v1 --model qwen        # any OpenAI-compatible endpoint
clousd run "Find the cheapest flight to Lisbon next Friday" --device dev_04f2 --save-recipe flights.json
clousd recipe flights.json --device dev_04f2                # replay without a model
clousd bench --device dev_04f2 --model-url ...              # the built-in task set, scored
clousd explore com.android.settings --device dev_04f2       # map the screens of an app, draft an app pack
```

### Plain HTTPS

```bash
curl -H "Authorization: Bearer cl_live_..." https://api.clousd.com/v1/devices
curl -H "Authorization: Bearer cl_live_..." "https://api.clousd.com/v1/devices/dev_04f2/observe?w=540"
curl -H "Authorization: Bearer cl_live_..." -H "Content-Type: application/json" \
     -d '{"op":"tap_text","text":"Search","settle":true}' https://api.clousd.com/v1/devices/dev_04f2/act
```

## What a session looks like

```
observe   dev_04f2                               -> screenshot, "On screen: com.android.chrome", elements 1..34
act       dev_04f2 tap element=12                -> "ok" (settled in 0.6 s)
act       dev_04f2 type text="weather in Berlin"
act       dev_04f2 key text=enter
act       dev_04f2 wait_text text=Berlin         -> "found after 1.9 s"
inspect   dev_04f2 crashes                       -> []
snapshots dev_04f2 restore snapshot_id=snap_7f3  -> "restored"   (next episode starts clean)
```

`observe` with the UI tree takes about 1 to 3 seconds, `act` with settle 0.3 to 1 second; a full agent step with the
model is typically 5 to 10 seconds.

## What is in this repository

| Part | What it is |
|---|---|
| [`openapi.yaml`](openapi.yaml) | OpenAPI 3.1 description of the public API v1 (`https://api.clousd.com/v1`) |
| [`python/`](python) | `clousd`: the Python client (devices, observe/act, snapshots, recipes, recording, ADB, jobs), `clousd.agent` (task loop on any OpenAI-compatible model), `clousd.bench`, `clousd.explore`, the `clousd` CLI |
| [`mcp/`](mcp) | `clousd-mcp`: MCP server with six tools (`devices`, `observe`, `act`, `inspect`, `snapshots`, `recipe`), descriptions, result schemas and hints on every tool |
| [`docs/agent-loop.md`](docs/agent-loop.md) | How an agent drives a phone: the observe → act → verify loop, and the mistakes that cost the most time |
| [`server.json`](server.json), [`smithery.yaml`](smithery.yaml) | Registry metadata: MCP registry entry `io.github.Clousd-Android/clousd-mcp` with the hosted endpoint |

## API in one table

| Route | Purpose |
|---|---|
| `GET /devices`, `GET /devices/{name}` | phones the key can use, their state and network |
| `POST /devices` | create a phone: model, Android version, network type and country, plan (`Idempotency-Key` required) |
| `POST /devices/{name}/start`, `/stop`, `/restart`, `DELETE /devices/{name}` | lifecycle; long operations answer `202` with a job |
| `GET /devices/{name}/screenshot?w=540` | PNG, or JPEG when a width is given |
| `GET /devices/{name}/observe?w=540&ui=1` | screenshot plus the UI tree (texts, ids, bounds, clickable), with a sequence number |
| `POST /devices/{name}/act` | `tap`, `long_press`, `swipe`, `scroll`, `text`, `key`, `open_app`, `close_app`, `url`, `intent`, `settings`, `screen_text`, `find_text`, `tap_text`, `wait_text`, `clipboard_set`, `notifications_open` / `_close` / `_clear`; read-only: `notifications`, `clipboard_get`, `app_info`, `crashes`, `health`, `installed`; `settle` waits for the screen to stop changing, `seq` rejects an action taken on a stale observation |
| `GET /devices/{name}/screenrecord?seconds=N` | mp4 of the screen, 1 to 60 seconds (control keys) |
| `POST /devices/{name}/recipe` | run an app recipe: built-in (`package`, `action`) or your own steps (`recipe`), `async` for a job, `cancel` to stop |
| `GET`/`POST /devices/{name}/record` | record a recipe from what is done on the phone: `start`, `status`, `stop` (returns the draft) |
| `GET`/`POST /devices/{name}/network`, `POST …/network/rotate` | which exit the phone uses; switch country or type; new IP |
| `GET`/`POST`/`DELETE /devices/{name}/adb` | ADB over a relay: one-time code, IP allow-list, expiry |
| `GET`/`POST /devices/{name}/snapshots`, `POST …/snapshots/{id}/restore`, `…/clone` | saved states: reset between runs, or clone a phone from a state |
| `GET /jobs/{id}` | progress of long operations |

Keys have a scope (`read` or `control`). Limits: 120 requests a minute per key and device, 600 a minute per key across
devices, 60 a minute for everything else; the server answers `429` with `Retry-After` and the Python client waits and
retries. Errors are JSON: `{"error": "short_code", "message": "what happened"}`. Full description with every field:
[`openapi.yaml`](openapi.yaml); human-readable reference: [clousd.com/docs/reference](https://clousd.com/docs/reference/).

## What teams use it for

- **Agent evaluation and training**: restore a snapshot, run the agent for N steps, check the screen, repeat. One
  phone, hundreds of identical episodes; `clousd bench` scores a task set, `python/examples/episode.py` is the
  minimal loop.
- **App testing on real models**: the same test on a Galaxy A35, a Pixel 9 and a moto g05, Android 14 to 16, without
  a device farm; crashes, ANRs and the log one call away.
- **Account and content operations**: each phone keeps its own identity, network and app state; clone a prepared
  phone instead of setting up the next one by hand; recipes replay a flow on many phones on a schedule.
- **Research and demos**: a real phone in a specific country, reset to a known state for every run.

## Platform

| | |
|---|---|
| Android versions | 12, 13, 14, 15, 16, 17 |
| Models | 150+ real models: Google, Samsung, Xiaomi, Redmi, POCO, Motorola, OnePlus, realme, OPPO, vivo, Sony, TECNO, Infinix, Nothing and more ([catalog](https://clousd.com/devices/)) |
| Google Play | Play Store and Play services on every phone |
| Network | residential, mobile, ISP or datacenter exit by country; or your own proxy; SIM, time zone and locale follow the exit ([networks](https://clousd.com/network/)) |
| State | snapshots and clones in seconds ([snapshots](https://clousd.com/snapshots/)) |
| Screen | live WebRTC stream in the dashboard; screenshots, UI tree and mp4 recordings in the API |
| Access | REST API, MCP (local or hosted), Python client and CLI, ADB relay, dashboard |
| Billing | per minute while a phone runs or monthly; traffic by the gigabyte; stopped phones cost nothing ([pricing](https://clousd.com/pricing/)) |

## Requirements

- An API key from the dashboard (early access: [clousd.com/agents](https://clousd.com/agents/#access))
- Python 3.9+ for the client and CLI, 3.10+ for the MCP server; or nothing at all with the hosted MCP endpoint
- A model for `clousd run` and `clousd bench`: any OpenAI-compatible endpoint; recipes and the API need none

## FAQ

**Is this an emulator?** No. Each phone is a full Android system built as a specific real model; apps read the same
hardware identity the real handset reports. Emulator detection does not trigger.

**Which MCP clients work?** Anything that speaks MCP over stdio or streamable HTTP. Six tools, each with parameter
descriptions, a result schema and read-only/destructive hints.

**Which models work?** For MCP, whatever your client runs. For `clousd run`, any OpenAI-compatible chat endpoint with
tool calling; vision is used when the model has it and the loop falls back to the numbered element list when it does
not.

**Can I run flows without a model?** Yes. Record once (`clousd record`, `clousd run --save-recipe`, or the dashboard)
and replay with `clousd recipe` or the `recipe` tool on any number of phones.

**Can several agents share a phone?** One recipe at a time per phone; `seq` in observe/act makes concurrent actors
safe: an action taken on a screen that has since changed is refused, not guessed.

**Where does my data go?** The phone runs on Clousd servers; its traffic leaves only through the exit you chose. Keys
are per account, per scope, per phone list, and revocable at any time.

## Building from this repository

```bash
pip install -e python
pip install -e mcp
python -m unittest discover -s python/tests     # offline: a fake server and a scripted model, no key needed
```

Releases go to PyPI as [`clousd`](https://pypi.org/project/clousd/) and [`clousd-mcp`](https://pypi.org/project/clousd-mcp/)
and to the [MCP registry](https://registry.modelcontextprotocol.io) automatically.

## Links

- Phones for AI agents, early access: https://clousd.com/agents/
- Docs: https://clousd.com/docs/ · API reference: https://clousd.com/docs/reference/
- Device catalog (150+ real models, Android 12 to 17): https://clousd.com/devices/
- Networks (residential, mobile, ISP, datacenter exits): https://clousd.com/network/
- Snapshots and clones: https://clousd.com/snapshots/
- Pricing: https://clousd.com/pricing/
- Packages: https://pypi.org/project/clousd/ · https://pypi.org/project/clousd-mcp/
- MCP registry entry: `io.github.Clousd-Android/clousd-mcp` · hosted MCP endpoint: `https://api.clousd.com/mcp`

## License

MIT. See [LICENSE](LICENSE).
