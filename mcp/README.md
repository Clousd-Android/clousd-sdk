# clousd-mcp

An MCP server that gives any MCP-capable agent a real Android phone in the cloud: a device modelled on a
real phone, with Google Play, a network in the country you choose, and saved states to reset to between runs.

Early access: ask for a key at [clousd.com/agents](https://clousd.com/agents/#access).

## Install

```bash
pip install clousd-mcp
```

Any MCP client (Cursor, Windsurf, Zed, a desktop assistant or your own agent) - stdio server, one environment variable:

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

Clients that take a command line instead of JSON: `clousd-mcp` with `CLOUSD_API_KEY` in the environment.

## Remote server (no install)

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

Self-hosting the remote mode: `CLOUSD_MCP_HTTP=127.0.0.1:8790 clousd-mcp` serves `/mcp` over HTTP; every request must
carry a key, nothing is read from the environment.

## Tools

Six tools - a few large tools are easier for a model to choose from than dozens of small ones:

| Tool | What it does |
|---|---|
| `devices` | list the phones this key can use; start or stop one |
| `observe` | the screen as an image with a number drawn on every element, plus the elements as text |
| `act` | `tap` / `long_press` (element number or point), `swipe`, `scroll`, `tap_text`, `wait_text`, `type`, `key`, `open_app`, `close_app`, `open_url`, `intent`, `settings`, `clipboard_set`, `notifications_open` / `_close` / `_clear`, `installed` |
| `inspect` | read without touching the screen: `notifications`, `clipboard`, `app` (version, running), `crashes`, `health`, `installed` |
| `snapshots` | list, save, restore (reset between runs) or clone a phone |
| `recipe` | run an app recipe (built-in or your own steps); record a new one from what is done on the phone |

Every tool has parameter descriptions, a result schema and hints (read-only, destructive). Screenshots come back 540 px
wide (`CLOUSD_SHOT_WIDTH` to change); act on an element by its number from the last `observe` - the most reliable way to
press a button - or give coordinates in that image, which the server scales to the phone. Typing works in any language.

Built on the [`clousd`](../python) Python client. MIT license.

mcp-name: io.github.Clousd-Android/clousd-mcp
