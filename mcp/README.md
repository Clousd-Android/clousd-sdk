# clousd-mcp: a real Android phone for your AI agent

**Clousd MCP server** gives any MCP-capable agent a real Android phone in the cloud. Not an emulator: each device is
built as a specific handset (Pixel, Galaxy, Xiaomi, Motorola, OnePlus, realme, OPPO, vivo, Sony, TECNO, Infinix and
150+ more, Android 12 to 17) with the same build, sensors, cameras, codecs and GPU strings as the real hardware,
Google Play inside, and its own network exit in the country you choose. Apps see a phone, so your agent sees the
same screens a person sees: no "unusual activity" walls, no emulator detection, no captcha loops.

Six tools cover the whole loop: **observe** the screen (screenshot with numbered elements plus the UI tree), **act**
on it (tap by element number, swipe, type in any language, open apps and links), **inspect** the phone without
touching the screen (notifications, clipboard, app versions, crashes), **snapshots** to reset every run to the same
state or clone a prepared phone into many, **recipe** to replay a recorded flow without a model, and **devices** to
manage the fleet. Works with Cursor, Windsurf, Zed, desktop assistants, LangChain-style agents and anything else that
speaks MCP, locally or through the hosted endpoint at `https://api.clousd.com/mcp`.

Early access: keys on request at [clousd.com/agents](https://clousd.com/agents/#access). Docs:
[clousd.com/docs](https://clousd.com/docs/). Source: [github.com/Clousd-Android/clousd-sdk](https://github.com/Clousd-Android/clousd-sdk).

## Why a real cloud phone instead of an emulator

| | Emulator on your machine | Clousd phone |
|---|---|---|
| What apps see | Goldfish/ranchu hardware, emulator build, no SIM | A specific real model: build fingerprint, sensors, cameras, codecs, GPU, SIM and carrier |
| Google Play | Needs Google APIs images, often blocked | Play Store and Play services on every device |
| Network | Your IP, your country | Residential, mobile, ISP or datacenter exit in the country you choose; SIM, time zone and locale follow it |
| Reset between runs | Snapshots, slow to restore | Saved state restored in seconds; clone one prepared phone into ten or a hundred |
| Scale | One per CPU core | Start with one, run hundreds, stopped phones cost nothing |
| Setup | SDK, images, ADB, host resources | One API key |

That is why agents built for social apps, marketplaces, banking flows, app testing and research run here: the
phone behaves like the device in a user's pocket, and every episode starts from the same state.

## Install

```bash
pip install clousd-mcp
```

Any MCP client, local (stdio) server, one environment variable:

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

Clients that take a command line instead of JSON: run `clousd-mcp` with `CLOUSD_API_KEY` in the environment.

### Remote server, no install

The same server runs at `https://api.clousd.com/mcp` (streamable HTTP). Point any MCP client at it with your key in
the `Authorization` header; clients that cannot set headers can pass `?api_key=cl_live_...` in the URL.

```json
{
  "mcpServers": {
    "clousd": {
      "url": "https://api.clousd.com/mcp",
      "headers": { "Authorization": "Bearer cl_live_..." }
    }
  }
}
```

Self-hosting the remote mode: `CLOUSD_MCP_HTTP=127.0.0.1:8790 clousd-mcp` serves `/mcp` over HTTP; every request
must carry a key, nothing is read from the environment. The hosted endpoint is also listed in the
[MCP registry](https://registry.modelcontextprotocol.io) as `io.github.Clousd-Android/clousd-mcp`.

## Tools

Six tools: a few large tools are easier for a model to choose from than dozens of small ones. Every tool has
parameter descriptions, a result schema and hints (read-only, destructive, idempotent), so clients and models pick
the right one.

| Tool | What it does |
|---|---|
| `devices` | list the phones this key can use (model, Android, state, network); start or stop one |
| `observe` | a fresh screenshot with a number drawn on every element, the app in front, and the elements as text with their positions |
| `act` | `tap` / `long_press` (by element number or point), `swipe`, `scroll`, `tap_text`, `wait_text`, `type`, `key`, `open_app`, `close_app`, `open_url`, `intent`, `settings`, `clipboard_set`, `notifications_open` / `_close` / `_clear`, `installed`; waits until the screen settles and refuses an action taken on a stale screen |
| `inspect` | read without touching the screen: `notifications`, `clipboard`, `app` (version, install time, running), `crashes` (app crashes, native crashes, ANRs), `health`, `installed` |
| `snapshots` | list, save, restore (reset between runs) or clone a phone with its own identity |
| `recipe` | run an app recipe (built-in flows such as login, warm-up, publish, or your own steps); record a new one from what is done on the phone and replay it without a model |

Screenshots come back 540 px wide (`CLOUSD_SHOT_WIDTH` to change). Act on an element by its number from the last
`observe`: the most reliable way to press a button. Coordinates, when needed, are in that image and the server scales
them to the phone. Typing goes through the input method and works in any language and in password fields.

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

## What teams use it for

- **Agent evaluation and training**: restore a snapshot, run the agent for N steps, check the screen, repeat. One
  phone, hundreds of identical episodes. Run [AndroidWorld](https://github.com/google-research/android_world)-style
  task sets on real device models.
- **App testing on real models**: the same test on a Galaxy A35, a Pixel 9 and a moto g05, Android 14 to 16, without
  a device farm. Crashes, ANRs and the logcat are one `inspect` away.
- **Account and content operations**: each phone keeps its own identity, network and app state; clone a prepared
  phone instead of setting up the next one by hand; recipes replay a flow on many phones.
- **Anything that needs a phone in a specific country**: the exit, SIM, time zone and locale match.

## Security and scope

- Keys are issued per account with a scope (`read` or `control`) and an explicit list of phones; a read key can
  observe and inspect but never acts.
- Over HTTP the key travels in the `Authorization` header of every request; the hosted server keeps nothing between
  requests.
- Phones never leak traffic outside their configured exit: a kill-switch below Android drops anything that is not
  going through the exit.
- ADB is available on request per phone, with an IP allow-list and a one-time code, for checks that need to go deeper
  than the screen.

## FAQ

**Is this an emulator?** No. Each phone is a full Android system built as a specific real model; apps read the same
hardware identity the real handset reports. Emulator detection does not trigger.

**Which MCP clients work?** Anything that speaks MCP over stdio or streamable HTTP: Cursor, Windsurf, Zed, desktop
assistants, agent frameworks with MCP support, and your own code through the Python client
([`clousd`](https://pypi.org/project/clousd/)).

**How fast is a step?** `observe` with the UI tree takes about 1 to 3 seconds; `act` with settle 0.3 to 1 second.
A typical agent step including the model is 5 to 10 seconds.

**What does it cost?** Per minute while the phone runs, or a monthly plan; stopped phones cost nothing. Network traffic
by the gigabyte. See [clousd.com/pricing](https://clousd.com/pricing/).

**Can I run my own flows without a model?** Yes: record a flow once (`recipe` with `record_start` / `record_stop`, or
by hand in the dashboard) and replay it with `recipe run` on any number of phones.

## Links

- Phones for AI agents: https://clousd.com/agents/
- Documentation and API reference: https://clousd.com/docs/ and https://clousd.com/docs/reference/
- Device catalog (150+ models): https://clousd.com/devices/
- Networks: https://clousd.com/network/
- Snapshots and clones: https://clousd.com/snapshots/
- Python client: https://pypi.org/project/clousd/
- Source, OpenAPI description, issues: https://github.com/Clousd-Android/clousd-sdk

Built on the [`clousd`](https://pypi.org/project/clousd/) Python client. MIT license.

mcp-name: io.github.Clousd-Android/clousd-mcp
