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

## Tools

Four tools, on purpose - agents choose better from a few large tools than from a dozen small ones:

| Tool | What it does |
|---|---|
| `devices` | list the phones this key can use; start or stop one |
| `observe` | the screen as an image plus every text on it |
| `act` | `tap`, `swipe`, `scroll`, `tap_text`, `wait_text`, `type`, `key`, `open_app`, `close_app`, `open_url`, `installed` |
| `snapshots` | list, save, restore (reset between runs) or clone a phone |

Screenshots come back 540 px wide (`CLOUSD_SHOT_WIDTH` to change); `tap` and `swipe` take coordinates in that image
and the server scales them to the phone. `tap_text` is usually the more reliable way to press a button. Typing works in
any language.

Built on the [`clousd`](../python) Python client. MIT license.
