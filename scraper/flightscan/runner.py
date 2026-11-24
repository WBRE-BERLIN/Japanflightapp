"""Plan and execute a scrape run."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import time
from collections import Counter
from pathlib import Path

from .config import Config
from .dates import wildcard_sample, window_tasks
from .models import SearchTask, utcnow_iso
from .report import build_latest, history_points
from .store import Store, parse_ts

log = logging.getLogger(__name__)


def resolve_mode(cfg: Config, mode: str, now: dt.datetime) -> str:
    if mode != "auto":
        return mode
    return "full" if now.hour in cfg.scrape.full_run_hours_utc else "refresh"


def _last_price(store: Store) -> dict[str, float]:
    best: dict[str, float] = {}
    for o in store.offers():
        if o.source == "google_flights":
            best[o.pair] = min(best.get(o.pair, 1e12), o.price_total)
    return best


def _last_check(store: Store, pair: str) -> str:
    return (store.state["checks"].get(pair, {}).get("google_flights") or {}).get("at") or ""


def plan(cfg: Config, store: Store, mode: str, today: dt.date, limit: int | None = None) -> list[SearchTask]:
    windows = window_tasks(cfg, today)
    prices = _last_price(store)
    if mode == "refresh":
        known = sorted((t for t in windows if t.key in prices), key=lambda t: prices[t.key])
        never = [t for t in windows if not _last_check(store, t.key)]
        tasks = known[: cfg.scrape.refresh_top_n] + never[: max(0, cfg.scrape.refresh_top_n // 3)]
        n_wild = max(1, cfg.wildcards.samples_per_run // 2)
    else:
        cheap = sorted((t for t in windows if t.key in prices), key=lambda t: prices[t.key])[:20]
        cheap_keys = {t.key for t in cheap}
        rest = sorted((t for t in windows if t.key not in cheap_keys), key=lambda t: _last_check(store, t.key))
        tasks = cheap + rest
        n_wild = cfg.wildcards.samples_per_run

    # wildcards: refresh known cheap ones + a rotating sample
    wild_known = sorted(
        ({o.pair: o for o in store.offers() if o.window == "wild" and o.source == "google_flights"}).values(),
        key=lambda o: o.price_total,
    )[:2]
    wild = [SearchTask(o.dest, dt.date.fromisoformat(o.depart), dt.date.fromisoformat(o.ret), "wild")
            for o in wild_known]
    seen = {t.key for t in tasks} | {t.key for t in wild}
    for t in wildcard_sample(cfg, today, n_wild, seed=today.toordinal() * 2 + (mode == "refresh")):
        if t.key not in seen:
            wild.append(t)
            seen.add(t.key)
    # put wildcards early enough that a time-out does not starve them
    head = min(len(tasks), 12)
    tasks = tasks[:head] + wild + tasks[head:]
    # dedupe preserving order
    out, keys = [], set()
    for t in tasks:
        if t.key not in keys:
            keys.add(t.key)
            out.append(t)
    return out[:limit] if limit else out


def run(cfg: Config, data_dir: Path, *, mode: str = "auto", limit: int | None = None,
        use_google: bool = True, use_tp: bool = True, debug_dir: Path | None = None,
        tfs_max_stops: int | None = 1) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    mode = resolve_mode(cfg, mode, now)
    store = Store(data_dir)
    t0 = time.monotonic()
    started = utcnow_iso()
    run_info: dict = {"at": started, "mode": mode, "sources": {}}

    # 1) Travelpayouts (bulk, cheap)
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip()
    if use_tp and cfg.sources.get("travelpayouts", True) and token:
        from .sources import travelpayouts

        offers, stats = travelpayouts.fetch(cfg, token, os.environ.get("TRAVELPAYOUTS_MARKER") or None, today)
        if stats["requests"] and stats["errors"] < stats["requests"]:
            store.replace_source("travelpayouts", offers, started)
        run_info["sources"]["travelpayouts"] = {**stats, "offers": len(offers)}
        log.info("travelpayouts: %s, %d offers kept", stats, len(offers))
    elif use_tp:
        run_info["sources"]["travelpayouts"] = {"skipped": "no TRAVELPAYOUTS_TOKEN"}

    # 2) Google Flights (detailed)
    if use_google and cfg.sources.get("google_flights", True):
        from .sources.google_flights import GoogleFlightsSource

        tasks = plan(cfg, store, mode, today, limit)
        budget = cfg.scrape.max_runtime_minutes * 60 - (time.monotonic() - t0)
        log.info("google flights: %d searches planned (mode=%s, budget %.0fs)", len(tasks), mode, budget)
        booking = cfg.scrape.fetch_booking_link == "always" or (
            cfg.scrape.fetch_booking_link == "refresh" and mode == "refresh")
        src = GoogleFlightsSource(cfg, debug_dir=debug_dir, tfs_max_stops=tfs_max_stops, fetch_booking=booking)
        outcomes = asyncio.run(src.run(tasks, time.monotonic() + budget))
        statuses = Counter(o.status for o in outcomes)
        for o in outcomes:
            if o.status in ("ok", "no_results", "error"):
                store.record_search(o.task.key, "google_flights", o.status, o.offers,
                                    utcnow_iso(), o.task.window)
        run_info["sources"]["google_flights"] = {
            "planned": len(tasks), **statuses,
            "offers": sum(len(o.offers) for o in outcomes),
            "avg_s": round(sum(o.seconds for o in outcomes if o.seconds) / max(1, len(outcomes)), 1),
        }
        run_info["blocked"] = statuses.get("blocked", 0) > 0
        if src._throttled:
            run_info["throttled"] = True   # stopped early: Google kept showing error pages
        errors = [f"{o.task.key}: {o.message}" for o in outcomes if o.status == "error"][:5]
        if errors:
            run_info["errors"] = errors

    store.prune(today, now)
    run_info["seconds"] = round(time.monotonic() - t0)
    store.add_run(run_info)
    latest = build_latest(cfg, store, run_info, today, now)
    best, pair_best = history_points(cfg, latest["offers"])
    store.update_history(today, best, pair_best)
    # re-enrich so pair statistics include today's point
    latest = build_latest(cfg, store, run_info, today, now)
    store.save(latest)
    return run_info


def last_good_run_age_h(store: Store, now: dt.datetime) -> float | None:
    for r in reversed(store.state.get("runs", [])):
        g = r.get("sources", {}).get("google_flights", {})
        if g.get("ok"):
            ts = parse_ts(r["at"])
            return (now - ts).total_seconds() / 3600 if ts else None
    return None
