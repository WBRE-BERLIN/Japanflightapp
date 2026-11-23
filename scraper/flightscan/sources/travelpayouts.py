"""Travelpayouts / Aviasales Data API (optional, free token).

Returns the cheapest round trips Aviasales users found in the last days, for
whole months at once – a cheap "radar" for good date pairs and wildcards.
Prices are per person; layover details and baggage are not included.

Docs: https://support.travelpayouts.com/hc/en-us/articles/203956163
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.parse
import urllib.request

from .. import airlines as al
from ..config import Config
from ..dates import classify
from ..filters import leg_flags, leg_ok
from ..links import aviasales_link, build_links
from ..models import Leg, Offer, utcnow_iso

log = logging.getLogger(__name__)
API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def _months(cfg: Config, today: dt.date) -> list[tuple[str, str]]:
    """(departure month, return month) pairs worth asking for."""
    first = dt.date(today.year, today.month, 1)
    out = []
    for i in range(0, 13):
        y, m = divmod(first.month - 1 + i, 12)
        d = dt.date(first.year + y, m + 1, 1)
        if d > today + dt.timedelta(days=cfg.scrape.max_days_ahead):
            break
        ny, nm = divmod(d.month, 12)
        nxt = dt.date(d.year + ny, nm + 1, 1)
        out.append((d.strftime("%Y-%m"), d.strftime("%Y-%m")))
        out.append((d.strftime("%Y-%m"), nxt.strftime("%Y-%m")))
    return out


def fetch(cfg: Config, token: str, marker: str | None = None, today: dt.date | None = None,
          opener=urllib.request.urlopen) -> tuple[list[Offer], dict]:
    today = today or dt.date.today()
    offers: list[Offer] = []
    stats = {"requests": 0, "rows": 0, "errors": 0}
    for dest in cfg.destinations:
        for dep_m, ret_m in _months(cfg, today):
            params = {
                "origin": cfg.origin[0], "destination": dest, "departure_at": dep_m, "return_at": ret_m,
                "one_way": "false", "direct": "false", "unique": "false", "sorting": "price",
                "currency": cfg.currency.lower(), "market": "de", "limit": 1000, "page": 1, "token": token,
            }
            url = API + "?" + urllib.parse.urlencode(params)
            stats["requests"] += 1
            try:
                with opener(urllib.request.Request(url, headers={"Accept-Encoding": "identity"}), timeout=30) as r:
                    payload = json.load(r)
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                log.warning("travelpayouts %s %s→%s: %s", dest, dep_m, ret_m, e)
                continue
            rows = payload.get("data") or []
            stats["rows"] += len(rows)
            for row in rows:
                o = to_offer(cfg, dest, row, marker)
                if o:
                    offers.append(o)
    return offers, stats


def to_offer(cfg: Config, dest: str, row: dict, marker: str | None) -> Offer | None:
    try:
        dep_at = dt.datetime.fromisoformat(row["departure_at"])
        ret_at = dt.datetime.fromisoformat(row["return_at"])
    except (KeyError, TypeError, ValueError):
        return None
    window = classify(cfg, dep_at.date(), ret_at.date())
    if window is None:
        return None
    code = row.get("airline") or ""
    name = al.display_name(code) if code else ""
    dest_airport = row.get("destination_airport") or dest
    origin_airport = row.get("origin_airport") or cfg.origin[0]
    out = Leg(
        dep_airport=origin_airport, arr_airport=dest_airport,
        dep_time=dep_at.strftime("%Y-%m-%dT%H:%M"), arr_time=None,
        duration_min=row.get("duration_to"), stops=int(row.get("transfers", 0)),
        layovers=None if row.get("transfers") else [], airlines=[name] if name else [],
    )
    inn = Leg(
        dep_airport=dest_airport, arr_airport=origin_airport,
        dep_time=ret_at.strftime("%Y-%m-%dT%H:%M"), arr_time=None,
        duration_min=row.get("duration_back"), stops=int(row.get("return_transfers", 0)),
        layovers=None if row.get("return_transfers") else [], airlines=[name] if name else [],
    )
    if not (leg_ok(out, cfg.constraints) and leg_ok(inn, cfg.constraints)):
        return None
    price_pp = float(row.get("price") or 0)
    if price_pp <= 0:
        return None
    links = []
    if row.get("link"):
        links.append({"kind": "aviasales", "label": "Aviasales", "url": aviasales_link(row["link"], marker)})
    links += build_links(
        origin=cfg.origin[0], dest_city=dest, dest_airport=dest_airport if dest_airport != dest else None,
        depart=dep_at.date().isoformat(), ret=ret_at.date().isoformat(), adults=cfg.adults,
        currency=cfg.currency, dest_airports=list(cfg.destinations[dest].airports),
        airline_names=[name] if name else [], hl=cfg.scrape.google_hl, gl=cfg.scrape.google_gl,
    )
    return Offer(
        source="travelpayouts", dest=dest, window=window,
        depart=dep_at.date().isoformat(), ret=ret_at.date().isoformat(),
        price_total=round(price_pp * cfg.adults, 2), adults=cfg.adults, currency=cfg.currency,
        outbound=out, inbound=inn, airlines=[name] if name else [], links=links,
        flags=leg_flags(out, "out") + leg_flags(inn, "in") + ["cached_price"],
        found_at=utcnow_iso(), price_is_estimate=True,
    )
