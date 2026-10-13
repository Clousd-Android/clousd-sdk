---
name: clousd
description: Drive real Android cloud phones on Clousd - list and start phones, run app recipes (sign in, warm up, publish) on many phones at once, bind proxies, use saved accounts and media. Use when the user mentions Clousd, cloud phones, phone farms, warming up or posting from many Instagram / TikTok / Facebook / Threads accounts, or automating Android apps on remote phones.
---

# Clousd: real Android phones for agents

Clousd phones are real Android devices in the cloud (Google Play inside, own network exit, SIM and time zone per phone).
You reach them through the Clousd MCP server (tools `devices`, `fleet`, `network`, `resources`, `observe`, `act`,
`inspect`, `snapshots`, `recipe`) or the REST API `https://api.clousd.com/v1` with `Authorization: Bearer cl_live_...`.

## Setup (once)

If the Clousd tools are not available, ask the user for an API key (dashboard: API & MCP → Create a key for your agent)
and connect the remote server:

- Claude Code: `claude mcp add --transport http clousd https://api.clousd.com/mcp --header "Authorization: Bearer <key>"`
- Codex: `codex mcp add clousd --url "https://api.clousd.com/mcp?api_key=<key>"`
- Gemini CLI: `gemini mcp add --transport http --header "Authorization: Bearer <key>" clousd https://api.clousd.com/mcp`
- Anything else: an MCP server of type http at `https://api.clousd.com/mcp` with that Authorization header.

Never print the key back in full, never store it in a project file.

## How to work

1. **Look first.** `devices op=list`: every phone has a `label` (the name the user sees, e.g. "Anna IG" or "Pixel 7 #3"),
   a `number`, its `accounts` and `proxy_id`. Refer to phones by label or number - every tool accepts them.
2. **Recipes before taps.** For app tasks call `fleet op=recipes` and run the matching recipe (`login`, `warm`,
   `publish_post`, `publish_story`, `delete_last_post`, `login_via`). Recipes wait for the right screens and handle
   popups. Tap through `observe` / `act` only when no recipe fits, or to check what is on a screen.
3. **Many phones = one call.** `fleet op=run devices=all ...` (or a list) runs the recipe on up to 32 phones in
   parallel and returns a job. Then `fleet op=status job=...` every 30-60 seconds until `state` is done or failed.
   Never loop `act` over phones. Use `stagger=20,90` so phones do not act in lockstep.
4. **Accounts without passwords.** `resources op=accounts` lists saved accounts (no secrets). Sign in with the step
   `{"op":"login","package":"com.instagram.android","account":"linked"}` (the account linked to each phone) or an
   account id. Never ask the user to type passwords into the chat. Facebook and Threads can sign in through the phone's
   Instagram: `resources op=add_via_instagram`, then the same login step.
5. **Network.** Each phone uses one proxy of the account. `network op=proxies` shows them; `network op=add` saves the
   user's own proxy (checked at once); `network op=bind` puts phones on it; `network op=rotate` gives a new IP.
   Time zone, locale and SIM follow the exit automatically.
6. **Media.** `resources op=media` lists the library; publish with
   `{"op":"publish","package":...,"text":"post","media_id":<id>,"caption":"..."}`.
7. **Clean state.** `snapshots op=save` before risky work, `op=restore` to go back.

## Typical requests

- "Warm up all my Instagram accounts for 15 minutes" →
  `fleet op=run devices=all package=com.instagram.android action=warm vars={"seconds":"900","comment_chance":"0.1"} stagger=20,90`
- "Sign every phone into its Instagram" →
  `fleet op=run devices=all steps=[{"op":"login","package":"com.instagram.android","account":"linked"}]`
- "Put phones 1-5 on my new proxy host:port:user:pass" → `network op=add proxy=...`, then `network op=bind proxy_id=<id> devices=["#1","#2","#3","#4","#5"]`
- "Name the phones after their accounts" → for each phone `devices op=rename device=<label> label=<account login>`

## Limits

- Up to 32 phones per `fleet op=run`; split bigger fleets.
- 60 API requests a minute per key (120 per device route); the tools retry on 429.
- Recipes exist for Instagram, TikTok, Facebook and Threads; other apps run on generic steps.
- Stopped phones cost nothing; starting one takes one to three minutes.
