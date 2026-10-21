# Clousd: real Android phones in the cloud, for AI agents

[![tests](https://github.com/Clousd-Android/clousd-sdk/actions/workflows/tests.yml/badge.svg)](https://github.com/Clousd-Android/clousd-sdk/actions/workflows/tests.yml)
[![PyPI clousd](https://img.shields.io/pypi/v/clousd?label=clousd)](https://pypi.org/project/clousd/)
[![PyPI clousd-mcp](https://img.shields.io/pypi/v/clousd-mcp?label=clousd-mcp)](https://pypi.org/project/clousd-mcp/)
[![MCP registry](https://img.shields.io/badge/MCP%20registry-io.github.Clousd--Android%2Fclousd--mcp-6366f1)](https://registry.modelcontextprotocol.io)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Every agent framework can tap a phone. Almost none has a phone worth tapping.

**Clousd is the phone.** Each device is a full Android system built as a specific real model, Pixel to Galaxy to
Xiaomi, with Google Play inside and its own network exit. Give it a task in plain words and Clousd's phone agent does
the steps and answers; or drive the screen yourself, step by step. Apps see a phone, not an emulator, so your agent
sees the screens a person sees.

This repository is the public side of it: the OpenAPI description, the Python client, the MCP server and the skill for
coding agents.

## Two ways to use a phone

**Give it the goal.** `POST /v1/runs {"task": "...", "device": "..."}`, `c.run(...)` in Python, the `run` tool over MCP.
Clousd's agent looks at the screen, taps and types until the goal is reached, and finishes with an answer - plus a
replay of every step, and JSON in your schema when you ask for it. One call gives the same task to up to 32 phones.
Saved logins are typed on the phone without your agent or the model seeing them.

**Drive it yourself.** `observe` returns a fresh screenshot with a number on every element; `act` taps an element, types,
swipes, opens apps and links, and waits until the screen settles. Snapshots reset a phone to a saved state in seconds.

## Quick start

### MCP

Hosted, nothing to install: `https://api.clousd.com/mcp`. Apps with connectors (Claude.ai, ChatGPT…) sign in with
OAuth - just add the URL. Other clients send the key in the header:

```json
{
  "mcpServers": {
    "clousd": { "url": "https://api.clousd.com/mcp", "headers": { "Authorization": "Bearer cl_live_..." } }
  }
}
```

Local: `pip install clousd-mcp`, then `{"command": "clousd-mcp", "env": {"CLOUSD_API_KEY": "cl_live_..."}}`.

Tools: `account`, `devices`, `run`, `run_status`, `answer`, `observe`, `act`, `credentials`, `schedules` ([mcp/](mcp)).

Coding agents learn the workflow from the skill:

```bash
npx skills add https://github.com/Clousd-Android/clousd-sdk --skill clousd
```

### Python

```bash
pip install clousd
export CLOUSD_API_KEY=cl_live_...
```

```python
from clousd import Clousd

c = Clousd()
run = c.run("Open Settings and tell me the Android version", device="dev_04f2", wait=True)
print(run.status, run.answer)                     # completed  Android 15

phone = c.device("dev_04f2")                      # or step by step
phone.open_app("com.android.chrome")
phone.tap_text("Search")
phone.type("weather in Berlin")
phone.key("enter")
phone.wait_text("Berlin", timeout=30)
```

More in [python/](python).

### Plain HTTPS

```bash
curl -H "Authorization: Bearer cl_live_..." https://api.clousd.com/v1/devices
curl -H "Authorization: Bearer cl_live_..." -H "Content-Type: application/json" \
     -d '{"task":"Open Settings and tell me the Android version","device":"dev_04f2"}' https://api.clousd.com/v1/runs
curl -N -H "Authorization: Bearer cl_live_..." https://api.clousd.com/v1/runs/r_8f2c/events      # live steps (SSE)
curl -H "Authorization: Bearer cl_live_..." "https://api.clousd.com/v1/devices/dev_04f2/observe?w=540"
```

## API in one table

| Route | Purpose |
|---|---|
| `POST /runs` | a task in plain words on `device` or `devices[]` (up to 32): `{task, device, credentials:[{package,name}], options:{output_schema, mode, max_steps, timeout}}`; `Idempotency-Key` header |
| `GET /runs`, `GET /runs/{id}` | runs of the key; status, answer, structured_output, the card it waits on |
| `GET /runs/{id}/events` | live events (Server-Sent Events): status, step, plan, card, answer, end |
| `GET /runs/{id}/trajectory`, `/steps/{n}.jpg` | every step with its screen |
| `POST /runs/{id}/answer`, `/message`, `/cancel` | answer a question or permission, talk to the running agent, stop it |
| `GET`/`POST /schedules`, `POST /schedules/{id}/toggle`, `/delete` | tasks at a time on days of the week |
| `GET`/`POST /credentials`, `POST /credentials/{id}/fields`, `DELETE …` | saved logins; values are never returned |
| `GET`/`POST /webhooks`, `…/{id}/rotate`, `/toggle`, `/test`, `GET …/deliveries` | events to your server, signed with your secret |
| `GET /devices`, `GET /devices/{name}`, `POST /devices`, `start`/`stop`/`restart` | phones of the key and their lifecycle |
| `GET /devices/{name}/observe`, `POST /devices/{name}/act`, `/screenshot`, `/screenrecord` | the screen, step by step |
| `GET`/`POST /devices/{name}/snapshots`, `…/restore`, `…/clone` | saved states |
| `GET`/`POST /devices/{name}/network`, `GET`/`POST /proxies` | the phone's network exit |
| `GET`/`POST`/`DELETE /devices/{name}/adb` | ADB over a relay: one-time code, IP allow-list, expiry |
| `GET /balance` | plan, phones, money left and warnings to show the user |

Keys have a scope (`read` or `read & write`) and an explicit list of phones; keys travel only in the `Authorization`
header. Limits: 120 requests a minute per key and device, 600 a minute per key across devices, 60 a minute for
everything else; `429` comes with `Retry-After`. Errors are JSON: `{"error": "short_code", "message": "what happened"}`.
Full description: [`openapi.yaml`](openapi.yaml); reference: [clousd.com/docs/reference](https://clousd.com/docs/reference/).

## What is in this repository

| Part | What it is |
|---|---|
| [`openapi.yaml`](openapi.yaml) | OpenAPI 3.1 description of the public API v1 (`https://api.clousd.com/v1`) |
| [`python/`](python) | `clousd`: Python client (runs, schedules, logins, webhooks, devices, observe/act, snapshots), `clousd.agent` (a step-by-step loop on your own model), `clousd.bench`, the `clousd` CLI |
| [`mcp/`](mcp) | `clousd-mcp`: the MCP server, local or hosted |
| [`skills/clousd`](skills/clousd/SKILL.md) | skill for coding agents: how to work with Clousd phones |
| [`docs/agent-loop.md`](docs/agent-loop.md) | driving a phone yourself: the observe → act → verify loop |
| [`server.json`](server.json), [`smithery.yaml`](smithery.yaml) | registry metadata |

## Upgrading from 0.3

1.0 gives phones goals instead of scripts. `/v1/recipes`, `/v1/groups`, `/v1/accounts`, `/v1/texts` and the
device `recipe` / `record` routes answer `410` with where to go instead: tasks are `POST /v1/runs`, logins are
`/v1/credentials`. MCP: `run` / `run_status` / `answer` / `credentials` / `schedules` replace `fleet`, `recipe`,
`resources`, `network`, `snapshots` and `inspect`. Keys in the URL are no longer accepted.

## Building from this repository

```bash
pip install -e python
pip install -e mcp
python -m unittest discover -s python/tests     # offline: a fake server and a scripted model, no key needed
```

## Links

- Phones for AI agents: https://clousd.com/agents/
- Docs: https://clousd.com/docs/ · API reference: https://clousd.com/docs/reference/
- Device catalog (150+ real models, Android 12 to 17): https://clousd.com/devices/
- Pricing: https://clousd.com/pricing/
- Packages: https://pypi.org/project/clousd/ · https://pypi.org/project/clousd-mcp/
- MCP registry entry: `io.github.Clousd-Android/clousd-mcp` · hosted endpoint: `https://api.clousd.com/mcp`

## License

MIT. See [LICENSE](LICENSE).
