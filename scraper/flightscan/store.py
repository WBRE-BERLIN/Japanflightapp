"""Persistent state (current offers per date pair) and price history.

Files in the data directory (the ``data`` branch in GitHub Actions):

  state.json    – offers per date pair + last check per source (internal)
  history.json  – daily cheapest price per window/destination + per pair
  latest.json   – what the web app and the notifications read
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .models import Offer

MAX_GOOGLE_PER_PAIR = 4
MAX_TP_PER_PAIR = 2
OFFER_MAX_AGE_DAYS = 5
PAIR_HISTORY_DAYS = 180
RUNS_KEPT = 60


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data, *, pretty: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if pretty:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def parse_ts(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


class Store:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.state = _read(self.dir / "state.json", {"offers": {}, "checks": {}, "runs": []})
        self.history = _read(self.dir / "history.json", {"version": 1, "daily": {}, "pairs": {}})
        self.state.setdefault("offers", {})
        self.state.setdefault("checks", {})
        self.state.setdefault("runs", [])

    # ── offers ──────────────────────────────────────────────────────────────
    def offers(self) -> list[Offer]:
        return [Offer.from_dict(o) for lst in self.state["offers"].values() for o in lst]

    def pair_offers(self, pair: str) -> list[Offer]:
        return [Offer.from_dict(o) for o in self.state["offers"].get(pair, [])]

    def record_search(self, pair: str, source: str, status: str, offers: list[Offer], at: str, window: str):
        chk = self.state["checks"].setdefault(pair, {"window": window})
        chk["window"] = window
        prev = chk.get(source, {})
        entry = {"at": at, "status": status, "last_ok": prev.get("last_ok")}
        if status in ("ok", "no_results"):
            entry["last_ok"] = at
            old = self.state["offers"].get(pair, [])
            others = [o for o in old if o["source"] != source]
            mine = sorted(offers, key=lambda o: o.price_total)[:MAX_GOOGLE_PER_PAIR]
            _carry_booking(mine, [o for o in old if o["source"] == source])
            self.state["offers"][pair] = others + [o.to_dict() for o in mine]
            if not self.state["offers"][pair]:
                self.state["offers"].pop(pair, None)
        chk[source] = entry

    def replace_source(self, source: str, offers: list[Offer], at: str):
        """Replace all offers of a bulk source (Travelpayouts)."""
        for pair in list(self.state["offers"]):
            self.state["offers"][pair] = [o for o in self.state["offers"][pair] if o["source"] != source]
        by_pair: dict[str, list[Offer]] = {}
        for o in offers:
            by_pair.setdefault(o.pair, []).append(o)
        for pair, lst in by_pair.items():
            lst.sort(key=lambda o: o.price_total)
            self.state["offers"].setdefault(pair, []).extend(o.to_dict() for o in lst[:MAX_TP_PER_PAIR])
            chk = self.state["checks"].setdefault(pair, {"window": lst[0].window})
            chk[source] = {"at": at, "status": "ok", "last_ok": at}
        for pair in [p for p, v in self.state["offers"].items() if not v]:
            del self.state["offers"][pair]

    def prune(self, today: dt.date, now: dt.datetime):
        cutoff = now - dt.timedelta(days=OFFER_MAX_AGE_DAYS)
        for pair in list(self.state["offers"]):
            depart = dt.date.fromisoformat(pair.split("|")[1])
            keep = []
            if depart > today:
                for o in self.state["offers"][pair]:
                    ts = parse_ts(o.get("found_at", ""))
                    if ts and ts >= cutoff:
                        keep.append(o)
            if keep:
                self.state["offers"][pair] = keep
            else:
                del self.state["offers"][pair]
        for pair in list(self.state["checks"]):
            if dt.date.fromisoformat(pair.split("|")[1]) <= today:
                del self.state["checks"][pair]

    def keep_destinations(self, dests) -> int:
        """Drop offers, checks and pair history of destinations no longer in the config."""
        keep = set(dests)
        gone = [p for p in {*self.state["offers"], *self.state["checks"]} if p.split("|")[0] not in keep]
        for p in gone:
            self.state["offers"].pop(p, None)
            self.state["checks"].pop(p, None)
        for p in [p for p in self.history["pairs"] if p.split("|")[0] not in keep]:
            del self.history["pairs"][p]
        for day in self.history["daily"].values():
            for k in [k for k in day if k.split("|")[-1] not in keep | {"*"}]:
                del day[k]
        return len(gone)

    def add_run(self, run: dict):
        self.state["runs"] = (self.state["runs"] + [run])[-RUNS_KEPT:]

    # ── history ─────────────────────────────────────────────────────────────
    def update_history(self, today: dt.date, best: dict[str, dict], pair_best: dict[str, tuple]):
        """best: 'window|dest' → compact offer; pair_best: pair → (strict_total, broad_total)."""
        day = today.isoformat()
        daily = self.history["daily"].setdefault(day, {})
        for key, comp in best.items():
            cur = daily.get(key)
            if cur is None or comp["eff_total"] < cur["eff_total"]:
                daily[key] = comp
        for pair, (strict, broad) in pair_best.items():
            series = self.history["pairs"].setdefault(pair, [])
            if series and series[-1][0] == day:
                s0, b0 = series[-1][1], series[-1][2]
                series[-1] = [day, _min(s0, strict), _min(b0, broad)]
            else:
                series.append([day, strict, broad])
        # trim
        cutoff = (today - dt.timedelta(days=PAIR_HISTORY_DAYS)).isoformat()
        for pair in list(self.history["pairs"]):
            depart = pair.split("|")[1]
            series = [e for e in self.history["pairs"][pair] if e[0] >= cutoff]
            if depart <= day or not series:
                del self.history["pairs"][pair]
            else:
                self.history["pairs"][pair] = series

    def pair_stats(self, pair: str, today: dt.date) -> dict:
        series = self.history["pairs"].get(pair) or []
        vals = [(e[0], e[1] if e[1] is not None else e[2]) for e in series if (e[1] or e[2])]
        if not vals:
            return {}
        low_day, low = min(vals, key=lambda x: x[1])
        prev = next((v for d, v in reversed(vals) if d < today.isoformat()), None)
        return {"low": low, "low_day": low_day, "prev": prev, "first": vals[0][0], "n": len(vals)}

    def save(self, latest: dict | None = None):
        write_json(self.dir / "state.json", self.state)
        write_json(self.dir / "history.json", self.history)
        if latest is not None:
            write_json(self.dir / "latest.json", latest)


def _carry_booking(new: list[Offer], old: list[dict]):
    """Keep Google's booking page data (Book link, sellers, bag fee) for the same flights.

    Full runs skip the booking page to save time; without this, a full run would drop the
    "Book" link and the real bag price that the midday run found for the same itinerary.
    """
    prev = {o.get("id"): o for o in old if any(l.get("kind") == "google_booking" for l in o.get("links", []))}
    for o in new:
        p = prev.get(o.id)
        if not p or any(l.get("kind") == "google_booking" for l in o.links):
            continue
        o.links = [l for l in p["links"] if l.get("kind") == "google_booking"] + o.links
        o.booking_vendors = o.booking_vendors or list(p.get("booking_vendors") or [])
        o.bag_google = o.bag_google or p.get("bag_google")
        o.typical_range = o.typical_range or p.get("typical_range")


def _min(a, b):
    vals = [v for v in (a, b) if v is not None]
    return min(vals) if vals else None
