"""Generate the date pairs that get searched."""
from __future__ import annotations

import datetime as dt
import random

from .config import Config, Window
from .models import SearchTask


def bookable(ret: dt.date, today: dt.date, max_days_ahead: int) -> bool:
    return ret <= today + dt.timedelta(days=max_days_ahead)


def window_pairs(w: Window) -> list[tuple[dt.date, dt.date]]:
    out = []
    d = w.depart_from
    while d <= w.depart_to:
        for n in w.nights:
            r = d + dt.timedelta(days=n)
            if w.latest_return is None or r <= w.latest_return:
                out.append((d, r))
        d += dt.timedelta(days=1)
    return out


def classify(cfg: Config, depart: dt.date, ret: dt.date) -> str | None:
    """Which window a date pair belongs to: window id, 'wild', or None."""
    nights = (ret - depart).days
    for w in cfg.windows:
        if (
            w.depart_from <= depart <= w.depart_to
            and nights in w.nights
            and (w.latest_return is None or ret <= w.latest_return)
        ):
            return w.id
    wc = cfg.wildcards
    if wc.enabled and nights in wc.nights and wc.start <= depart <= wc.end and not _excluded(cfg, depart, ret):
        return "wild"
    return None


def _excluded(cfg: Config, depart: dt.date, ret: dt.date) -> bool:
    for a, b in cfg.wildcards.exclude:
        if depart <= b and ret >= a:  # overlaps an excluded period
            return True
    # overlapping a main window is not a "wildcard"
    for w in cfg.windows:
        if depart <= (w.latest_return or w.depart_to) and ret >= w.depart_from:
            return True
    return False


def window_tasks(cfg: Config, today: dt.date) -> list[SearchTask]:
    tasks = []
    for w in cfg.windows:
        for d, r in window_pairs(w):
            if d <= today or not bookable(r, today, cfg.scrape.max_days_ahead):
                continue
            for dest in cfg.destinations:
                tasks.append(SearchTask(dest=dest, depart=d, ret=r, window=w.id))
    return tasks


def wildcard_candidates(cfg: Config, today: dt.date) -> list[SearchTask]:
    """All wildcard date pairs (weekly grid: departures on every 7th day)."""
    wc = cfg.wildcards
    if not wc.enabled:
        return []
    out = []
    start = max(wc.start, today + dt.timedelta(days=14))
    d = start
    while d <= wc.end:
        for n in wc.nights[len(wc.nights) // 2: len(wc.nights) // 2 + 1] or wc.nights[:1]:
            r = d + dt.timedelta(days=n)
            if bookable(r, today, cfg.scrape.max_days_ahead) and classify(cfg, d, r) == "wild":
                for dest in cfg.destinations:
                    out.append(SearchTask(dest=dest, depart=d, ret=r, window="wild"))
        d += dt.timedelta(days=3)
    return out


def wildcard_sample(cfg: Config, today: dt.date, n: int, seed: int | None = None) -> list[SearchTask]:
    """A rotating sample of wildcard pairs; deterministic per day so runs spread out."""
    cands = wildcard_candidates(cfg, today)
    if not cands or n <= 0:
        return []
    rnd = random.Random(seed if seed is not None else today.toordinal())
    rnd.shuffle(cands)
    # spread across months: round-robin over month buckets
    buckets: dict[str, list[SearchTask]] = {}
    for t in cands:
        buckets.setdefault(t.depart.strftime("%Y-%m"), []).append(t)
    months = sorted(buckets)
    rnd.shuffle(months)
    picked: list[SearchTask] = []
    i = 0
    while len(picked) < n and any(buckets.values()):
        m = months[i % len(months)]
        if buckets[m]:
            picked.append(buckets[m].pop())
        i += 1
    return picked
