# Clousd SDK

[![tests](https://github.com/Clousd-Android/clousd-sdk/actions/workflows/tests.yml/badge.svg)](https://github.com/Clousd-Android/clousd-sdk/actions/workflows/tests.yml)

Cloud Android phones that look and behave like real devices, driven over HTTPS. This repository holds the public
API description, the Python client and an MCP server, so a script or an AI agent can look at a phone's screen,
act on it, check the result and reset the phone to a saved state between runs.

| Part | What it is |
|---|---|
| [`openapi.yaml`](openapi.yaml) | OpenAPI 3.1 description of the public API v1 (`https://api.clousd.com/v1`) |
| [`python/`](python) | `clousd` - Python client: devices, screenshots, screen text, taps, typing, snapshots, ADB, jobs |
| [`mcp/`](mcp) | `clousd-mcp` - MCP server with four tools (`devices`, `observe`, `act`, `snapshots`) for any MCP-capable agent |
| [`docs/agent-loop.md`](docs/agent-loop.md) | How an agent drives a phone: the observe → act → verify loop, with the mistakes that cost the most time |

Early access: keys are issued on request at [clousd.com/agents](https://clousd.com/agents/#access).

## What a Clousd phone is

Each device is a full Android system (12 to 17) built as a specific phone model: the same build fingerprint,
properties, sensors, cameras, codecs, GPU strings, audio ports and screen as the real handset, taken from a snapshot
of that handset. More than 150 models from Google, Samsung, Xiaomi, Motorola, OnePlus, realme, OPPO, vivo, Sony,
TECNO, Infinix and others. Google Play works. Every phone has its own network exit in the country you choose
(residential, mobile, ISP or datacenter), its own SIM profile, time zone and locale that follow the exit, and a
kill-switch so nothing leaves the phone outside that exit.

That matters for automation because apps behave differently on a phone they recognise as real: fewer captchas,
fewer "unusual activity" interruptions, the same code paths a user hits.

## Quick start

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

png = phone.screenshot()             # PNG; pass width=540 for a smaller JPEG
```

### MCP

```bash
pip install clousd-mcp
```

Any MCP client (Cursor, Windsurf, Zed, a desktop assistant or your own agent):

```json
{
  "mcpServers": {
    "clousd": {
      "command": "clousd-mcp",
      "env": { "CLOUSD_API_KEY": "cl_live_..." }
    }
  }
}
```

### Plain HTTPS

```bash
curl -H "Authorization: Bearer cl_live_..." https://api.clousd.com/v1/devices
curl -H "Authorization: Bearer cl_live_..." "https://api.clousd.com/v1/devices/dev_04f2/observe?w=540"
curl -H "Authorization: Bearer cl_live_..." -H "Content-Type: application/json" \
     -d '{"op":"tap_text","text":"Search","settle":true}' https://api.clousd.com/v1/devices/dev_04f2/act
```

## API in one table

| Route | Purpose |
|---|---|
| `GET /devices`, `GET /devices/{name}` | phones the key can use, their state and network |
| `POST /devices` | create a phone: model, Android version, network type and country, plan (`Idempotency-Key` required) |
| `POST /devices/{name}/start`, `/stop`, `/restart`, `DELETE /devices/{name}` | lifecycle; long operations answer `202` with a job |
| `GET /devices/{name}/screenshot?w=540` | PNG, or JPEG when a width is given |
| `GET /devices/{name}/observe?w=540&ui=1` | screenshot plus the UI tree (texts, ids, bounds, clickable), with a sequence number |
| `POST /devices/{name}/act` | `tap`, `swipe`, `scroll`, `text`, `key`, `open_app`, `close_app`, `url`, `screen_text`, `find_text`, `tap_text`, `wait_text`; `settle` waits for the screen to stop changing, `seq` rejects an action taken on a stale observation |
| `GET`/`POST /devices/{name}/network`, `POST …/network/rotate` | which exit the phone uses; switch country or type; new IP |
| `GET`/`POST`/`DELETE /devices/{name}/adb` | ADB over a relay: one-time code, IP allow-list, expiry |
| `GET`/`POST /devices/{name}/snapshots`, `POST …/snapshots/{id}/restore`, `…/clone` | saved states: reset between runs, or clone a phone from a state |
| `GET /jobs/{id}` | progress of long operations |

Keys have a scope (`read` or `control`). Limits: 120 requests a minute per key and device, 600 a minute per key across
devices, 60 a minute for everything else; the server answers `429` with `Retry-After` and the Python client waits and
retries. Errors are JSON: `{"error": "short_code", "message": "what happened"}`.

Full description with every field: [`openapi.yaml`](openapi.yaml). Human-readable reference:
[clousd.com/docs/reference](https://clousd.com/docs/reference/).

## Typical uses

- **Agent evaluation**: restore a snapshot, run the agent for N steps, check the screen text, repeat. One phone, hundreds
  of episodes, identical starting state every time ([`python/examples/episode.py`](python/examples/episode.py)).
- **App testing on real models**: the same test on a Galaxy A35, a Pixel 9 and a moto g05, Android 14 to 16,
  without a device farm.
- **Account and content operations**: each phone keeps its own identity, network and app state; clone a prepared
  phone instead of setting up the next one by hand.
- **Anything that needs a phone in a specific country**: the exit, SIM, time zone and locale match.

## Building from this repository

```bash
pip install -e python
pip install -e mcp
python -m unittest discover -s python/tests
```

Releases are published to PyPI as [`clousd`](https://pypi.org/project/clousd/) and [`clousd-mcp`](https://pypi.org/project/clousd-mcp/).

The tests run against a local fake server; no key needed.

## License

MIT. See [LICENSE](LICENSE).
