# How an AI agent drives a real Android phone

Notes from running agents on Clousd phones: what the loop looks like, where it breaks, and what fixed it. The API
calls below are the public ones in [`openapi.yaml`](../openapi.yaml); the Python names are from the
[`clousd`](../python) client.

## The loop

Every step is the same three calls:

1. **Observe**: `GET /devices/{name}/observe?w=540` returns a fresh screenshot, the foreground package and activity,
   the UI tree (every node with text, description, resource id, bounds and whether it is clickable or scrollable)
   and a sequence number `seq`.
2. **Act**: `POST /devices/{name}/act` with one operation and the `seq` from the observation. If anything observed or
   acted on the phone after that `seq`, the server answers `409 stale` and does nothing: the action was decided on a
   screen that no longer exists.
3. **Verify**: the next observation. `settle: true` on the act call makes the server wait until two consecutive
   frames are identical (up to five seconds), so the next observation shows the result of the action, not the
   animation.

```python
phone = Clousd().device("dev_04f2")
obs = phone.observe(width=540)
while not done(obs):
    step = model(obs["image"], obs["ui"])          # your policy
    phone.act(step["op"], seq=obs["seq"], settle=True, **step["args"])
    obs = phone.observe(width=540)
```

## Give the model text, not only pixels

A screenshot alone makes the model guess coordinates. The UI tree gives it the texts and the bounds of every
element, so most actions become `tap_text("Add to cart")` instead of `tap(541, 1702)`. `tap_text` is exact match
by default, because substring matching taps the wrong thing more often than people expect: "Delete" matches the
title "Delete post?", "Profile" matches "Profile picture".

When a screen has no accessibility nodes (games, some video players, custom-drawn editors) the tree comes back
empty and `ui_error` says why. That is the signal to fall back to coordinates from the image, not a transient
error to retry.

## Reset between runs

An evaluation is only comparable when every episode starts from the same state. Save a snapshot once the phone is
set up (apps installed, account signed in, permissions granted) and restore it before each run:

```python
snap = next(s for s in phone.snapshots() if s["restorable"])
phone.restore(snap["id"])      # seconds, the phone keeps its identity and network
```

Restore returns a job; the client waits for it by default. The phone comes back booted, with the same model
identity, SIM, network exit and installed apps, and the app data as it was at the snapshot.

To run many episodes in parallel, clone the snapshot into new phones (`POST /snapshots/{id}/clone`): each clone is
a separate device with its own identity.

## Things that cost us the most time

**Acting on a stale screen.** Before `seq` existed, agents tapped buttons that had already moved. A dialog appears
200 ms after the observation, the tap lands on it, the run is ruined and nobody notices until the end. `seq` turns
that into an explicit `409` the agent can handle by observing again.

**Not waiting for the UI to settle.** Android animates almost every transition. An observation taken 100 ms after a
tap shows a half-drawn screen; the model reads the old texts and acts twice. `settle: true` costs 0.3 to 1 s and
removes most "the agent pressed it twice" failures. Live video and camera previews never settle; the response says
`settled: false` and you decide.

**Pixel coordinates from a scaled image.** The model sees a 540 px wide screenshot; the phone is 1080 px wide. The MCP
server scales taps for you; with the raw API, scale by `screen.w / image.w` or use the bounds from the UI tree,
which are always in device pixels.

**Typing with key events.** Typing character by character through key codes breaks on non-Latin text and on
password fields. `text` sends the whole string through the input method, works in any language and in password
fields.

**Rate limits as errors.** The API allows 120 requests a minute per phone. An agent polling `observe` in a tight loop
hits `429` within a minute. Observe once per step, use `settle` instead of polling, and let the client honour
`Retry-After`.

**Checking the result with another screenshot.** `wait_text("Added", timeout=30)` is cheaper and more reliable than
screenshot-compare-screenshot: it polls the UI tree on the server and returns as soon as the text appears.

## Why the phone model matters

Apps read the device they run on: build fingerprint, hardware properties, sensors, GPU, codecs, installed Google
services, network type and the country of the IP. A phone that reports an emulator, or a model that does not exist,
or a datacenter IP with a mobile SIM, gets the "unusual activity" path: captchas, verification loops, features that
never load. Clousd phones are built as specific models from snapshots of the real handsets, and the network, SIM,
time zone and locale follow the exit the phone uses. The agent sees the same screens a person on that phone would.

## Where the logs are

Everything the phone writes to logcat is kept on the server and available through `GET /devices/{name}/logs` and in
the dashboard. When a run fails for no visible reason, the log usually has the answer: an app that crashed, a
permission dialog that was dismissed, a network timeout.
