"""clousd - command line for cloud phones.

    clousd devices
    clousd run "Open Settings and tell me the Android version" --device dev_04f2     # Clousd's agent does it
    clousd runs [--status failed]
    clousd agent "Open Settings and tell me the Android version" --device dev_04f2 --model-url http://127.0.0.1:8080/v1
    clousd explore com.android.settings --device dev_04f2 [--depth 2] [--out settings.graph.json]
    clousd bench --device dev_04f2 --model-url ... [--only id,id] [--out results.jsonl]

The API key comes from CLOUSD_API_KEY. `run` needs nothing else. `agent` and `bench` drive the phone step by step with your
own model: --model-url / CLOUSD_MODEL_URL (any OpenAI-compatible endpoint), --model / CLOUSD_MODEL, key in CLOUSD_MODEL_KEY.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .client import Clousd, ClousdError


def _model(a):
    from .agent import Model
    url = a.model_url or os.environ.get("CLOUSD_MODEL_URL", "")
    if not url:
        sys.exit("no model: pass --model-url or set CLOUSD_MODEL_URL (an OpenAI-compatible endpoint, e.g. http://127.0.0.1:8080/v1)")
    return Model(url, a.model or os.environ.get("CLOUSD_MODEL", ""), os.environ.get("CLOUSD_MODEL_KEY", ""))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="clousd", description="Cloud Android phones from the command line")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices", help="list devices")

    def model_args(sp):
        sp.add_argument("--model-url", default="")
        sp.add_argument("--model", default="")
        sp.add_argument("--no-vision", action="store_true", help="text only (models without image input)")

    r = sub.add_parser("run", help="give a phone a task in plain words; Clousd's agent does it and answers")
    r.add_argument("task")
    r.add_argument("--device", action="append", required=True, help="repeat for several phones")
    r.add_argument("--login", action="append", default=[], help="saved login to use: package/name (see credentials)")
    r.add_argument("--max-steps", type=int, default=0)
    r.add_argument("--quiet", action="store_true", help="only the answer")

    rs = sub.add_parser("runs", help="recent runs")
    rs.add_argument("--status", default="")
    rs.add_argument("--limit", type=int, default=20)

    ag = sub.add_parser("agent", help="do a task step by step with your own language model")
    ag.add_argument("goal")
    ag.add_argument("--device", required=True)
    ag.add_argument("--max-steps", type=int, default=25)
    ag.add_argument("--trace", default="", help="write every step as JSON lines here")
    model_args(ag)

    ex = sub.add_parser("explore", help="walk the screens of an app and write a map of them")
    ex.add_argument("package")
    ex.add_argument("--device", required=True)
    ex.add_argument("--depth", type=int, default=2)
    ex.add_argument("--max-screens", type=int, default=25)
    ex.add_argument("--out", default="")

    b = sub.add_parser("bench", help="run the built-in task set and score it")
    b.add_argument("--device", required=True)
    b.add_argument("--only", default="")
    b.add_argument("--out", default="")
    b.add_argument("--max-steps", type=int, default=20)
    model_args(b)

    a = p.parse_args(argv)
    c = Clousd()
    if a.cmd == "devices":
        for d in c.devices():
            x = d.data
            print(f"{x.get('name'):<14} {x.get('state', ''):<9} {x.get('model', '')} Android {x.get('android', '')}")
        return
    if a.cmd == "runs":
        for x in c.runs(status=a.status, limit=a.limit):
            r = x.data
            print(f"{r.get('id', ''):<14} {r.get('status', ''):<10} {r.get('device', '') or ','.join(r.get('devices') or []):<14} "
                  f"{(r.get('answer') or r.get('error') or r.get('task') or '')[:70]}")
        return
    if a.cmd == "run":
        logins = []
        for x in a.login:
            if "/" not in x:
                sys.exit("--login takes package/name, e.g. com.example.app/work")
            pk, nm = x.split("/", 1)
            logins.append({"package": pk, "name": nm})
        devs = a.device
        run = c.run(a.task, device=devs[0] if len(devs) == 1 else None, devices=devs if len(devs) > 1 else None,
                    credentials=logins, max_steps=a.max_steps or None)
        if len(devs) > 1 or a.quiet:
            run.wait()
            for x in (run.children or [run]):
                print(f"{x.device:<14} {x.status:<10} {x.answer or x.error}")
            sys.exit(0 if all(x.status == "completed" for x in (run.children or [run])) else 1)
        for ev in run.events():
            d_ = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if ev["event"] == "step":
                print(f"  {d_.get('n', '')}. {d_.get('tool', '')} {d_.get('target', '')} - {d_.get('result', '')}"[:160])
            elif ev["event"] == "plan":
                print("  plan: " + "; ".join(str(g) for g in (d_.get("subgoals") or [])))
            elif ev["event"] == "card":
                print("  waiting for you: " + str(d_.get("question") or d_.get("action") or "") + "  (answer it in the dashboard or with Run.reply)")
        run.refresh()
        print(f"{run.status}: {run.answer or run.error}")
        sys.exit(0 if run.status == "completed" else 1)
    d = c.device(a.device)
    if a.cmd == "agent":
        from .agent import Agent
        res = Agent(d, _model(a), vision=not a.no_vision, max_steps=a.max_steps, trace_path=a.trace or None).run(a.goal)
        print(f"{res.status}: {res.answer}  ({len(res.steps)} steps, {res.seconds:.0f} s)")
        sys.exit(0 if res.status == "done" else 1)
    if a.cmd == "explore":
        from .explore import explore
        g = explore(d, a.package, depth=a.depth, max_screens=a.max_screens)
        out = a.out or f"{a.package}.graph.json"
        open(out, "w", encoding="utf-8").write(json.dumps(g, indent=2, ensure_ascii=False) + "\n")
        print(f"{len(g['screens'])} screens, {len(g['app_pack']['elements'])} elements -> {out}")
        return
    if a.cmd == "bench":
        from .bench import run_bench
        only = [x for x in a.only.split(",") if x]
        rows = run_bench(d, _model(a), only=only, max_steps=a.max_steps, vision=not a.no_vision, out=a.out or None)
        ok = sum(1 for r in rows if r["pass"])
        print(f"\n{ok}/{len(rows)} passed")
        sys.exit(0 if ok == len(rows) else 1)


def entry() -> None:
    """Console script: API errors as one line and exit code 2, not a traceback."""
    try:
        main()
    except ClousdError as e:
        sys.exit(f"clousd: {e.status} {e.error}: {e.message}".rstrip(": "))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    entry()
