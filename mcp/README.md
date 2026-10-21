# clousd-mcp: real Android phones for your AI agent

**Clousd MCP server** gives any MCP-capable agent real Android phones in the cloud. Each device is built as a specific
handset (Pixel, Galaxy, Xiaomi, Motorola, OnePlus and 150+ more, Android 12 to 17) with Google Play inside and its own
network exit, so apps behave the way they do on the phone in a user's pocket.

Your agent does not have to tap every button itself. It gives a phone the goal in plain words - "open the app, sign in
with the saved work login and tell me what the order page shows" - and Clousd's own phone agent works the screen and
comes back with the answer, a step-by-step replay and, if asked, JSON in your schema. One call can give the same task to
up to 32 phones at once. When your agent wants to drive the screen itself, `observe` and `act` are there too.

Docs: [clousd.com/docs/mcp](https://clousd.com/docs/mcp/). Source: [github.com/Clousd-Android/clousd-sdk](https://github.com/Clousd-Android/clousd-sdk).

## Tools

| Tool | What it does |
|---|---|
| `account` | Plan, phones, money left and warnings to tell the user |
| `devices` | The phones this key can use (name, label, number, state, model, Android, exit); start, stop, rename |
| `run` | Give one or many phones a task in plain words; waits up to 50 s and returns the status, the answer or what it waits on, and a replay link |
| `run_status` | Follow a run (status, last steps, per-phone results), list recent runs, cancel, or tell the running agent something |
| `answer` | Reply to a run that asks a question or asks permission for a step |
| `observe` | A fresh screenshot with a number on every element, the app in front, the elements as text |
| `act` | Tap an element number or a point, long press, swipe, scroll, type in any language, keys, open apps and links, intents, settings pages; read notifications, clipboard, app info, crashes, health |
| `credentials` | Saved logins: package, name and which fields are set - never the values. Pass one to `run` and it is typed without anyone seeing it |
| `schedules` | Tasks that run by themselves at a time on chosen days: list, add, pause, delete |

## Connect

### Hosted server, no install

`https://api.clousd.com/mcp` (streamable HTTP).

- **Claude.ai, ChatGPT and other apps with connectors**: add the URL; the app opens Clousd's sign-in page (OAuth 2.1)
  and you allow access. Disconnect any time in the dashboard: Settings → Connections → Connected apps.
- **Coding agents and other clients**: send your key in the header.

```json
{
  "mcpServers": {
    "clousd": { "url": "https://api.clousd.com/mcp", "headers": { "Authorization": "Bearer cl_live_..." } }
  }
}
```

```bash
claude mcp add --transport http clousd https://api.clousd.com/mcp --header "Authorization: Bearer cl_live_..."
```

The key goes only in the `Authorization` header: a key in the address is refused (addresses end up in logs and
histories). Without a header the server answers `401` with the OAuth metadata, which is how connector apps start the
sign-in.

### Local (stdio)

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

Keys: dashboard → Settings → Connections → API keys. A `read` key can look but never acts; a `read & write` key can
act and run tasks.

## A session

```
account                                         -> plan, 4 phones, no warnings
devices                                         -> "Pixel 7 #1" running, "Galaxy A35 #2" running
credentials package=com.example.shop            -> use: com.example.shop/work (username, password)
run task="Open the Example Shop app, sign in and tell me how many orders are on the Orders page"
    devices="1,2" credentials="com.example.shop/work"
                                                -> status running, next: run_status in 20-60 s
run_status run=r_8f2c wait_seconds=40           -> completed; Pixel 7 #1: "3 orders", Galaxy A35 #2: "3 orders";
                                                   replay https://clousd.com/app/?run=r_8f2c
```

## Settings

| Variable | Meaning |
|---|---|
| `CLOUSD_API_KEY` | the key (stdio server) |
| `CLOUSD_SHOT_WIDTH` | screenshot width for `observe`, 240 to 1080, default 540 |
| `CLOUSD_MCP_HTTP` | `host:port` - run the remote (streamable HTTP) server at `/mcp` instead of stdio |
| `CLOUSD_MCP_HOSTS` | public host names allowed in the Host header behind a reverse proxy, e.g. `api.clousd.com` |
| `CLOUSD_MCP_RESOURCE_META` | OAuth protected-resource metadata URL sent with `401` (default: api.clousd.com's) |

## Upgrading from 0.3

1.0 gives phones goals instead of scripts: `run` / `run_status` / `answer` replace `fleet` and `recipe`; saved logins
(`credentials`) replace `resources` accounts; reading the phone moved into `act`; `network`, `snapshots` and `inspect`
are no longer MCP tools (the REST API and the Python client still have proxies, snapshots and device reads). Keys in
the URL (`?api_key=`, `?config=`) are no longer accepted - send the `Authorization` header or connect with OAuth.

mcp-name: io.github.Clousd-Android/clousd-mcp

## License

MIT
