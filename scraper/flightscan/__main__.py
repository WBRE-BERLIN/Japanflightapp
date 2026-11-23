"""Command line entry point.

  python -m flightscan run   --data ../store [--mode auto|full|refresh] [--limit N]
  python -m flightscan plan  --data ../store [--mode full]
  python -m flightscan smoke [--debug-dir debug]
  python -m flightscan demo  --out ../web/data
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path

from .config import load_config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flightscan")
    ap.add_argument("--config", default=None, help="path to config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="scrape and update the data directory")
    r.add_argument("--data", required=True, type=Path)
    r.add_argument("--mode", default="auto", choices=["auto", "full", "refresh"])
    r.add_argument("--limit", type=int, default=None, help="max Google searches (testing)")
    r.add_argument("--debug-dir", type=Path, default=None)
    r.add_argument("--no-google", action="store_true")
    r.add_argument("--no-travelpayouts", action="store_true")

    p = sub.add_parser("plan", help="show what a run would search")
    p.add_argument("--data", required=True, type=Path)
    p.add_argument("--mode", default="full", choices=["full", "refresh"])

    s = sub.add_parser("smoke", help="2 live searches, print parsed results")
    s.add_argument("--debug-dir", type=Path, default=Path("debug"))
    s.add_argument("--searches", type=int, default=2)

    d = sub.add_parser("demo", help="write demo latest.json/history.json")
    d.add_argument("--out", required=True, type=Path)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)

    if args.cmd == "run":
        from .runner import run
        info = run(cfg, args.data, mode=args.mode, limit=args.limit, use_google=not args.no_google,
                   use_tp=not args.no_travelpayouts, debug_dir=args.debug_dir)
        print(json.dumps(info, indent=1))
        g = info.get("sources", {}).get("google_flights", {})
        if g and g.get("planned") and not g.get("ok") and not g.get("no_results"):
            print("::error::Google Flights: no successful search in this run", file=sys.stderr)
            return 2
        return 0

    if args.cmd == "plan":
        from .runner import plan
        from .store import Store
        tasks = plan(cfg, Store(args.data), args.mode, dt.date.today())
        by = {}
        for t in tasks:
            by.setdefault(t.window, 0)
            by[t.window] += 1
        print(f"{len(tasks)} searches: {by}")
        for t in tasks[:15]:
            print(" ", t.key, t.window)
        return 0

    if args.cmd == "smoke":
        return _smoke(cfg, args.debug_dir, args.searches)

    if args.cmd == "demo":
        from .demo import generate
        path = generate(cfg, args.out)
        print(f"demo data written to {path}")
        return 0
    return 1


def _smoke(cfg, debug_dir: Path, n: int) -> int:
    from .dates import window_tasks
    from .sources.google_flights import GoogleFlightsSource

    today = dt.date.today()
    tasks = window_tasks(cfg, today)
    picked = []
    for dest in cfg.destinations:   # one per destination, middle of first window
        cand = [t for t in tasks if t.dest == dest]
        if cand:
            picked.append(cand[len(cand) // 3])
    picked = picked[:n]
    ok = False
    for stops in (1, None):
        src = GoogleFlightsSource(cfg, debug_dir=debug_dir, tfs_max_stops=stops)
        res = asyncio.run(src.run(picked, time.monotonic() + 600))
        for o in res:
            print(f"\n=== {o.task.key} [{o.status}] {o.seconds}s counts={o.counts} {o.message}")
            for off in o.offers:
                d = off.to_dict()
                print(json.dumps({k: d[k] for k in ("price_total", "airlines", "flags", "booking_vendors",
                                                    "price_level")}, ensure_ascii=False))
                for leg in ("outbound", "inbound"):
                    print("  ", leg, json.dumps(d[leg], ensure_ascii=False))
                print("   links:", [l["kind"] for l in d["links"]])
        n_ok = sum(1 for o in res if o.status == "ok")
        print(f"\n::notice title=smoke max_stops={stops}::{n_ok}/{len(res)} ok, "
              f"{sum(len(o.offers) for o in res)} offers")
        if n_ok:
            ok = True
            break
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
