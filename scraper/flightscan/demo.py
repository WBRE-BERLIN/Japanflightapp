"""Generate realistic-looking demo data so the web app can be tried before the
first real scrape. Output is clearly marked ``"demo": true``."""
from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

from .config import Config
from .dates import wildcard_candidates, window_tasks
from .links import build_links
from .models import Layover, Leg, Offer
from .report import build_latest, history_points
from .store import Store

# (airline, hub, out duration, in duration, typical layover, base price pp)
ROUTES = [
    (["Finnair"], "HEL", 16 * 60 + 5, 17 * 60 + 40, 95, 905),
    (["Lufthansa"], "FRA", 16 * 60 + 55, 18 * 60 + 5, 120, 980),
    (["Lufthansa", "ANA"], "MUC", 16 * 60 + 20, 17 * 60 + 50, 110, 1040),
    (["Turkish Airlines"], "IST", 16 * 60 + 35, 18 * 60 + 15, 150, 870),
    (["LOT"], "WAW", 15 * 60 + 50, 17 * 60 + 25, 125, 860),
    (["Qatar Airways"], "DOH", 18 * 60 + 10, 19 * 60 + 35, 175, 925),
    (["Air China"], "PEK", 15 * 60 + 35, 16 * 60 + 50, 160, 880),
    (["Japan Airlines", "Finnair"], "HEL", 16 * 60 + 15, 17 * 60 + 30, 100, 1120),
    (["Emirates"], "DXB", 19 * 60 + 25, 20 * 60 + 40, 210, 990),
]


def _leg(dep_ap, arr_ap, date: dt.date, dep_hm: tuple[int, int], dur: int, hub: str, lay: int, names):
    dep = dt.datetime.combine(date, dt.time(*dep_hm))
    # duration includes the time zone difference only for the arrival clock
    tz_shift = 7 if arr_ap in ("HND", "NRT", "KIX") else -7
    arr = dep + dt.timedelta(minutes=dur, hours=tz_shift)
    return Leg(
        dep_airport=dep_ap, arr_airport=arr_ap, dep_time=dep.strftime("%Y-%m-%dT%H:%M"),
        arr_time=arr.strftime("%Y-%m-%dT%H:%M"), duration_min=dur, stops=1,
        layovers=[Layover(airport=hub, duration_min=lay, city="", overnight=False)], airlines=list(names),
    )


def generate(cfg: Config, out_dir: Path, days_history: int = 35, seed: int = 7) -> Path:
    rnd = random.Random(seed)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    today = now.date()
    store = Store(out_dir / ".demo-store")
    store.state = {"offers": {}, "checks": {}, "runs": []}
    store.history = {"version": 1, "daily": {}, "pairs": {}}
    tasks = window_tasks(cfg, today) + wildcard_candidates(cfg, today)[::5]

    def season(d: dt.date) -> float:
        return {1: 0.85, 2: 0.82, 3: 1.05, 4: 1.15, 5: 1.0, 6: 0.95, 7: 1.12, 8: 1.2,
                9: 0.97, 10: 1.08, 11: 0.9, 12: 1.1}[d.month]

    for t in tasks:
        offers = []
        for names, hub, od, idur, lay, base in rnd.sample(ROUTES, 3):
            dest_ap = cfg.destinations[t.dest].airports[0] if rnd.random() < 0.7 else cfg.destinations[t.dest].airports[-1]
            wkday = 1.06 if t.depart.weekday() in (4, 5) else 0.97
            pp = base * season(t.depart) * wkday * rnd.uniform(0.9, 1.18) * (1.04 if t.dest == "OSA" else 1)
            if t.window == "wild" and rnd.random() < 0.04:
                pp *= 0.68
            out = _leg(cfg.origin[0], dest_ap, t.depart, (rnd.choice([7, 10, 13, 17]), rnd.choice([5, 20, 35, 50])),
                       od + rnd.randint(-20, 40), hub, lay + rnd.randint(-30, 90), names)
            inn = _leg(dest_ap, cfg.origin[0], t.ret, (rnd.choice([9, 11, 21, 23]), rnd.choice([0, 15, 40])),
                       idur + rnd.randint(-20, 50), hub, lay + rnd.randint(-30, 120), names)
            o = Offer(
                source="demo", dest=t.dest, window=t.window, depart=t.depart.isoformat(), ret=t.ret.isoformat(),
                price_total=round(pp * cfg.adults), adults=cfg.adults, currency=cfg.currency,
                outbound=out, inbound=inn, airlines=list(names),
                links=build_links(origin=cfg.origin[0], dest_city=t.dest, dest_airport=dest_ap,
                                  depart=t.depart.isoformat(), ret=t.ret.isoformat(), adults=cfg.adults,
                                  currency=cfg.currency, dest_airports=list(cfg.destinations[t.dest].airports),
                                  airline_names=names),
                flags=[], found_at=(now - dt.timedelta(hours=rnd.randint(1, 20))).isoformat().replace("+00:00", "Z"),
                price_level=rnd.choice(["typical", "high", "typical", "low"]),
            )
            if not offers:   # first offer per pair: pretend we opened Google's booking page
                incl = names[0] in ("Turkish Airlines", "LOT", "Qatar Airways", "Air China", "Emirates")
                o.bag_google = {"text": "Free" if incl else "€0–90", "min": 0, "max": 0 if incl else 90}
                o.booking_vendors = [{"name": names[0], "price": o.price_total + 40, "airline": True, "phone": False},
                                     {"name": "Trip.com", "price": o.price_total, "airline": False, "phone": False}]
                o.typical_range = [round(o.price_total * 1.15, -2), round(o.price_total * 2.1, -2)]
            offers.append(o)
        store.record_search(t.key, "demo", "ok", offers, now.isoformat().replace("+00:00", "Z"), t.window)

    run = {"at": now.isoformat().replace("+00:00", "Z"), "mode": "demo", "seconds": 0,
           "sources": {"demo": {"planned": len(tasks), "ok": len(tasks)}}}
    store.add_run(run)

    # fake history: random walk ending at today's values
    latest = build_latest(cfg, store, run, today, now, demo=True)
    best, pair_best = history_points(cfg, latest["offers"])
    for i in range(days_history, 0, -1):
        day = today - dt.timedelta(days=i)
        drift = 1 + 0.18 * (i / days_history) + rnd.uniform(-0.04, 0.05)
        b2 = {k: {**v, "eff_total": round(v["eff_total"] * drift), "price_total": round(v["price_total"] * drift)}
              for k, v in best.items()}
        p2 = {k: tuple(None if x is None else round(x * drift * rnd.uniform(0.97, 1.03)) for x in v)
              for k, v in pair_best.items()}
        store.update_history(day, b2, p2)
    store.update_history(today, best, pair_best)
    latest = build_latest(cfg, store, run, today, now, demo=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    from .store import write_json
    write_json(out_dir / "latest.json", latest)
    write_json(out_dir / "history.json", store.history)
    return out_dir / "latest.json"
