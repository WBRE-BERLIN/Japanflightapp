"""Build latest.json (offers + summary) from the store."""
from __future__ import annotations

import datetime as dt

from . import airlines as al
from .config import Config
from .filters import offer_ok
from .models import Offer
from .store import Store, parse_ts

MAX_OFFERS = 600


def enrich(cfg: Config, o: Offer, store: Store, today: dt.date, now: dt.datetime) -> dict:
    d = o.to_dict()
    status, _note = al.bag_policy(o.airlines)
    extra = al.bag_extra_total(status, o.adults, cfg.bag_fee)
    d["bag"] = {"status": status, "extra_est": extra}
    d["eff_total"] = round(o.price_total + extra, 2)
    d["strict_ok"] = offer_ok(o, cfg.filters)
    ts = parse_ts(o.found_at)
    age_h = (now - ts).total_seconds() / 3600 if ts else None
    d["age_h"] = round(age_h, 1) if age_h is not None else None
    d["stale"] = age_h is not None and age_h > cfg.scrape.stale_after_hours
    d["pair_stats"] = store.pair_stats(o.pair, today)
    return d


def compact(d: dict) -> dict:
    out, inn = d.get("outbound") or {}, d.get("inbound") or {}

    def via(leg):
        return [l["airport"] for l in (leg.get("layovers") or [])]

    return {
        "id": d["id"], "pair": d["pair"], "window": d["window"], "dest": d["dest"],
        "depart": d["depart"], "ret": d["ret"], "nights": d["nights"],
        "price_total": d["price_total"], "price_pp": d["price_pp"], "eff_total": d["eff_total"],
        "bag": d["bag"]["status"], "airlines": d["airlines"], "source": d["source"],
        "arr_airport": out.get("arr_airport"),
        "out_duration": out.get("duration_min"), "in_duration": inn.get("duration_min"),
        "out_via": via(out), "in_via": via(inn),
        "url": next((l["url"] for l in d.get("links", []) if l["kind"] in ("google_booking", "google", "aviasales")), None),
    }


def summarize(cfg: Config, offers: list[dict]) -> dict:
    """Best offers per window/destination using the default filters."""
    good = [o for o in offers if o["strict_ok"] and not o["stale"]]
    summary: dict = {"windows": {}, "overall": None, "wildcards": []}
    best_window_eff = None
    for w in [*cfg.windows]:
        ws = [o for o in good if o["window"] == w.id]
        entry = {"label": w.label, "best": None, "by_dest": {}}
        if ws:
            b = min(ws, key=lambda o: o["eff_total"])
            entry["best"] = compact(b)
            best_window_eff = b["eff_total"] if best_window_eff is None else min(best_window_eff, b["eff_total"])
        for dest in cfg.destinations:
            ds = [o for o in ws if o["dest"] == dest]
            entry["by_dest"][dest] = compact(min(ds, key=lambda o: o["eff_total"])) if ds else None
        summary["windows"][w.id] = entry
    wild = sorted((o for o in good if o["window"] == "wild"), key=lambda o: o["eff_total"])
    if best_window_eff is not None:
        limit = best_window_eff * (1 - cfg.wildcards.min_saving_pct / 100)
        summary["wildcards"] = [compact(o) for o in wild if o["eff_total"] <= limit][:5]
    else:
        summary["wildcards"] = [compact(o) for o in wild[:3]]
    cands = [e["best"] for e in summary["windows"].values() if e["best"]] + summary["wildcards"][:1]
    if cands:
        summary["overall"] = min(cands, key=lambda c: c["eff_total"])
    return summary


def history_points(cfg: Config, offers: list[dict]) -> tuple[dict, dict]:
    best: dict[str, dict] = {}
    for o in offers:
        if not o["strict_ok"] or o["stale"]:
            continue
        for key in (f"{o['window']}|{o['dest']}", f"{o['window']}|*"):
            if key not in best or o["eff_total"] < best[key]["eff_total"]:
                best[key] = compact(o)
    pair_best: dict[str, list] = {}
    for o in offers:
        if o["stale"]:
            continue
        s, b = pair_best.get(o["pair"], [None, None])
        if o["strict_ok"]:
            s = o["price_total"] if s is None else min(s, o["price_total"])
        b = o["price_total"] if b is None else min(b, o["price_total"])
        pair_best[o["pair"]] = [s, b]
    return best, {k: tuple(v) for k, v in pair_best.items()}


def build_latest(cfg: Config, store: Store, run: dict, today: dt.date, now: dt.datetime,
                 *, demo: bool = False) -> dict:
    offers = [enrich(cfg, o, store, today, now) for o in store.offers()]
    offers.sort(key=lambda o: (o["eff_total"], o["price_total"]))
    summary = summarize(cfg, offers)
    coverage = {}
    for pair, chk in store.state["checks"].items():
        srcs = {k: v for k, v in chk.items() if isinstance(v, dict)}
        last = max((v.get("at") or "" for v in srcs.values()), default=None)
        status = "ok" if any(v.get("status") == "ok" for v in srcs.values()) else (
            next(iter(srcs.values()), {}).get("status") if srcs else None)
        coverage[pair] = {"window": chk.get("window"), "at": last, "status": status}
    return {
        "version": 1,
        "generated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "demo": demo,
        "run": run,
        "runs": store.state["runs"][-14:],
        "config": cfg.public(),
        "summary": summary,
        "offers": offers[:MAX_OFFERS],
        "coverage": coverage,
    }
