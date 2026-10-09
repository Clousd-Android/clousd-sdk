"""clousd - command line for cloud phones.

    clousd devices
    clousd run "Open Settings and tell me the Android version" --device dev_04f2 --model-url http://127.0.0.1:8080/v1
    clousd record start|status|stop --device dev_04f2 [--out draft.json]
    clousd recipe draft.json --device dev_04f2 [--var login=me]
    clousd explore com.android.settings --device dev_04f2 [--depth 2] [--out settings.graph.json]
    clousd bench --device dev_04f2 --model-url ... [--only id,id] [--out results.jsonl]

The API key comes from CLOUSD_API_KEY. The model for `run`/`bench`: --model-url / CLOUSD_MODEL_URL (any OpenAI-compatible
endpoint), --model / CLOUSD_MODEL, key in CLOUSD_MODEL_KEY.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .client import Clousd


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

    r = sub.add_parser("run", help="do a task on a phone with a language model")
    r.add_argument("goal")
    r.add_argument("--device", required=True)
    r.add_argument("--max-steps", type=int, default=25)
    r.add_argument("--trace", default="", help="write every step as JSON lines here")
    model_args(r)

    rec = sub.add_parser("record", help="record a recipe from what is done on the phone")
    rec.add_argument("op", choices=["start", "status", "stop", "cancel"])
    rec.add_argument("--device", required=True)
    rec.add_argument("--out", default="", help="stop: save the draft here")

    rc = sub.add_parser("recipe", help="run a recipe file (a recorded draft) or a built-in recipe")
    rc.add_argument("file", nargs="?", default="")
    rc.add_argument("--device", required=True)
    rc.add_argument("--package", default="")
    rc.add_argument("--action", default="")
    rc.add_argument("--var", action="append", default=[], help="name=value")

    ex = sub.add_parser("explore", help="walk the screens of an app and write a map plus a draft app pack")
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
    d = c.device(a.device)
    if a.cmd == "run":
        from .agent import Agent
        res = Agent(d, _model(a), vision=not a.no_vision, max_steps=a.max_steps, trace_path=a.trace or None).run(a.goal)
        print(f"{res.status}: {res.answer}  ({len(res.steps)} steps, {res.seconds:.0f} s)")
        sys.exit(0 if res.status == "done" else 1)
    if a.cmd == "record":
        if a.op == "start":
            print(json.dumps(d.record_start()))
        elif a.op == "status":
            print(json.dumps(d.record_status(), indent=2))
        elif a.op == "cancel":
            print(json.dumps(c.request("POST", d._p("record"), json={"op": "cancel"})))
        else:
            r = d.record_stop()
            text = json.dumps(r.get("recipe"), indent=2, ensure_ascii=False)
            if a.out:
                open(a.out, "w", encoding="utf-8").write(text + "\n")
                print(f"draft saved to {a.out} ({len(r.get('recipe', {}).get('steps', []))} steps)")
            else:
                print(text)
        return
    if a.cmd == "recipe":
        vars_ = dict(v.split("=", 1) for v in a.var if "=" in v)
        if a.file:
            print(d.recipe(recipe=json.load(open(a.file, encoding="utf-8")), vars=vars_))
        else:
            print(d.recipe(a.package, a.action, vars=vars_))
        return
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
        return


if __name__ == "__main__":
    main()
