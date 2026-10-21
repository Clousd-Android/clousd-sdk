---
name: clousd
description: Drive real Android cloud phones on Clousd - check the account, list and start phones, give one or many phones a task in plain words and follow it to the answer, use saved logins without seeing passwords, schedule repeating tasks, or drive the screen step by step. Use when the user mentions Clousd, cloud phones, testing or automating Android apps on remote phones, or an agent that needs a phone.
---

# Clousd: real Android phones for agents

Clousd phones are real Android devices in the cloud (Google Play inside, their own network exit and time zone). You reach
them through the Clousd MCP server (tools `account`, `devices`, `run`, `run_status`, `answer`, `observe`, `act`,
`credentials`, `schedules`) or the REST API `https://api.clousd.com/v1` with `Authorization: Bearer cl_live_...`.

## Setup (once)

If the Clousd tools are not available, connect the remote server `https://api.clousd.com/mcp`:

- Claude.ai, ChatGPT and other apps with connectors: add the URL; the app asks the user to sign in to Clousd (OAuth).
- Claude Code: `claude mcp add --transport http clousd https://api.clousd.com/mcp --header "Authorization: Bearer <key>"`
- Codex (`~/.codex/config.toml`): `[mcp_servers.clousd]` with `url = "https://api.clousd.com/mcp"` and
  `http_headers = { "Authorization" = "Bearer <key>" }`
- Anything else: an MCP server of type http at that URL with the Authorization header.

The key comes from the dashboard (Settings → Connections → API keys). Keys go only in the Authorization header, never
in the URL. Never print the key back in full, never store it in a project file.

## How to work

1. **Account first.** `account` before starting phones or long work: tell the user every line of `warnings` in plain
   words, with the `top_up` link.
2. **Look at the phones.** `devices` (op=list): every phone has a `label` (the name the user sees, e.g. "Pixel 7 #3")
   and a `number`; every tool accepts the name, the label or the number. A stopped phone starts with `devices op=start`
   (one to three minutes).
3. **Give the phone the goal, not the taps.** `run task="..." devices=...` - Clousd's agent works the screen and
   finishes with an answer. Write the task like you would for a person: what to open, what to do, what to report back.
   Pass `output_schema` when you need the answer as JSON.
4. **Follow it.** If `run` returns before the end, call `run_status run=<id> wait_seconds=40` until `status` is
   `completed`, `failed` or `cancelled`. Show the user the `answer` and the `replay` link (every step with its screen).
5. **Questions.** A run with `waiting_for` stopped to ask: reply with `answer` (text, or allow / decline for a
   permission). If only the user can know, ask the user first.
6. **Many phones = one run.** `devices` takes a list: the same task runs on each phone in parallel (up to 32), the
   result has a line per phone. Never loop over phones yourself.
7. **Logins without passwords.** `credentials` lists saved logins (names only, never values). Pass them to `run` as
   `credentials="package/name"`: the values are typed on the phone without you or the model seeing them. If a login is
   missing, ask the user to add it in the dashboard - never ask for a password in the chat.
8. **Repeat on a schedule.** `schedules op=add task=... devices=... at=09:00 days=1,2,3,4,5 tz=<user's time zone>`.
9. **Drive the screen yourself only when needed.** `observe` (screenshot with numbered elements) and `act` (tap an
   element number, type, swipe, open an app or a link) - for checking a screen, or for a step the user wants exactly.
   `act` also reads without touching: notifications, clipboard, app_info, crashes, health.

## Typical requests

- "What Android version is on my Pixel?" → `run task="Open Settings and tell me the Android version" devices="Pixel 7 #3"`
- "Check that our app opens and the login screen shows on all test phones" →
  `run task="Open the app com.example.shop and tell me what the first screen shows" devices="1,2,3,4"`
- "Sign in with the work account and open the profile" →
  `credentials` (find `com.example.app/work`), then `run task="Sign in and open the profile" devices=... credentials="com.example.app/work"`
- "Every weekday at 9 check the order page" → `schedules op=add task="..." devices=... at=09:00 days=1,2,3,4,5 tz=Europe/Berlin`

## Limits

- Up to 32 phones per run; split bigger sets.
- 60 API requests a minute per key (120 per device route); the tools retry on 429.
- A phone does one task at a time; a second run on it waits in the queue.
- Stopped phones keep their apps and data; starting one takes one to three minutes.
