"""Parse Google Flights result rows.

Every result row carries an accessible description (``aria-label``) such as::

  From 1846 euros round trip total. 1 stop flight with Finnair. Leaves Berlin
  Brandenburg Airport at 7:05 AM on Monday, May 10 and arrives at Haneda
  Airport at 8:35 AM on Tuesday, May 11. Total duration 18 hr 30 min.
  Layover (1 of 1) is a 3 hr 10 min layover at Helsinki Airport in Helsinki.
  Select flight

The visible row text adds IATA codes ("BER–HND", "3 hr 10 min HEL"), which
are used to turn airport names into codes.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from .. import airlines as al
from ..models import Layover, Leg

AIRPORT_NAMES = {
    "berlin brandenburg": "BER",
    "haneda": "HND", "tokyo haneda": "HND", "tokyo international": "HND",
    "narita": "NRT", "narita international": "NRT", "tokyo narita": "NRT",
    "kansai": "KIX", "kansai international": "KIX", "osaka kansai": "KIX",
    "itami": "ITM", "osaka international": "ITM",
    "helsinki": "HEL", "helsinki-vantaa": "HEL",
    "frankfurt": "FRA", "frankfurt am main": "FRA",
    "munich": "MUC", "munich international": "MUC",
    "zurich": "ZRH", "vienna international": "VIE", "vienna": "VIE",
    "brussels": "BRU", "istanbul": "IST", "warsaw chopin": "WAW",
    "amsterdam airport schiphol": "AMS", "amsterdam schiphol": "AMS", "schiphol": "AMS",
    "paris charles de gaulle": "CDG", "charles de gaulle": "CDG",
    "london heathrow": "LHR", "heathrow": "LHR", "copenhagen": "CPH",
    "dubai international": "DXB", "hamad international": "DOH", "doha hamad international": "DOH",
    "zayed international": "AUH", "abu dhabi international": "AUH",
    "beijing capital international": "PEK", "beijing daxing international": "PKX",
    "shanghai pudong international": "PVG", "guangzhou baiyun international": "CAN",
    "hong kong international": "HKG", "incheon international": "ICN", "seoul incheon international": "ICN",
    "singapore changi": "SIN", "suvarnabhumi": "BKK", "rome fiumicino": "FCO",
    "leonardo da vinci–fiumicino": "FCO", "madrid-barajas adolfo suárez": "MAD",
    "stockholm arlanda": "ARN", "oslo": "OSL", "düsseldorf": "DUS", "dusseldorf": "DUS",
    "hamburg": "HAM", "milan malpensa": "MXP", "lisbon": "LIS", "taoyuan international": "TPE",
}

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}

_WS = re.compile(r"[\s   ]+")
_PRICE = re.compile(r"(?:From\s+)?([\d.,]+)\s+(?:euros?|EUR)\b", re.I)
_PRICE_SYM = re.compile(r"€\s?([\d.,]+)|([\d.,]+)\s?€")
_STOPS = re.compile(r"\b(Nonstop|(\d+)\s+stops?)\s+flight\s+with\s+(.+?)\.(?:\s|$)", re.I)
_LEAVES = re.compile(
    r"Leaves\s+(?P<dep>.+?)\s+at\s+(?P<dt>\d{1,2}:\d{2}(?:\s*[AP]M)?)\s+on\s+\w+,\s+(?P<dm>\w+)\s+(?P<dd>\d{1,2})"
    r"\s+and\s+arrives\s+at\s+(?P<arr>.+?)\s+at\s+(?P<at>\d{1,2}:\d{2}(?:\s*[AP]M)?)\s+on\s+\w+,\s+(?P<am>\w+)\s+(?P<ad>\d{1,2})",
    re.I,
)
_DURATION = re.compile(r"Total duration\s+((?:\d+\s*hr\s*)?(?:\d+\s*min)?)", re.I)
_LAYOVER = re.compile(
    r"Layover\s*\(\d+\s+of\s+\d+\)\s+is\s+an?\s+(?P<dur>(?:\d+\s*hr\s*)?(?:\d+\s*min)?)\s+(?P<ovn>overnight\s+)?"
    r"layover\s+at\s+(?P<ap>.+?)\s+in\s+(?P<city>[^.]+)\.(?P<rest>[^.]*\.)?",
    re.I,
)
_ROUTE_CODES = re.compile(r"\b([A-Z]{3})\s*[–—-]\s*([A-Z]{3})\b")
_LAYOVER_CODES = re.compile(r"(?:\d+\s*hr(?:\s*\d+\s*min)?|\d+\s*min)\s+([A-Z]{3})\b")
_OPERATED = re.compile(r"Operated by\s+([^.\n]+)", re.I)


@dataclass
class ParsedOption:
    price_total: float | None
    leg: Leg
    aria: str
    text: str


def norm(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def parse_duration(s: str | None) -> int | None:
    if not s:
        return None
    h = re.search(r"(\d+)\s*hr", s)
    m = re.search(r"(\d+)\s*min", s)
    if not h and not m:
        return None
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def parse_price(s: str) -> float | None:
    m = _PRICE.search(s)
    raw = m.group(1) if m else None
    if raw is None:
        m2 = _PRICE_SYM.search(s)
        raw = (m2.group(1) or m2.group(2)) if m2 else None
    if raw is None:
        return None
    digits = re.sub(r"[.,](?=\d{3}\b)", "", raw)   # strip thousands separators
    digits = digits.replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def airport_code(name: str, hints: list[str] | None = None) -> str:
    n = norm(name)
    if re.fullmatch(r"[A-Z]{3}", n):
        return n
    key = re.sub(r"\s+airport$", "", n.lower().split(" (")[0])
    for k in (key, re.sub(r"\s+international$", "", key)):
        if k in AIRPORT_NAMES:
            return AIRPORT_NAMES[k]
    for k, v in AIRPORT_NAMES.items():
        if k in key:
            return v
    return n


def _time24(t: str) -> str:
    t = norm(t).upper()
    m = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", t)
    if not m:
        return t
    h, mi, ap = int(m.group(1)), m.group(2), m.group(3)
    if ap == "PM" and h != 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{mi}"


def _date(month: str, day: str, ref: dt.date) -> dt.date | None:
    mo = MONTHS.get(month.lower()) or MONTHS.get(_month_from_abbr(month))
    if not mo:
        return None
    d = dt.date(ref.year, mo, int(day))
    if (d - ref).days < -200:   # crossed new year
        d = dt.date(ref.year + 1, mo, int(day))
    return d


def _month_from_abbr(m: str) -> str:
    m = m.lower()[:3]
    return next((full for full in MONTHS if full.startswith(m)), "")


def parse_option(aria: str, text: str, ref_date: dt.date) -> ParsedOption | None:
    """Parse one result row. ``ref_date`` = searched date of this leg."""
    a = norm(aria)
    t = text or ""
    lv = _LEAVES.search(a)
    if not lv:
        return None

    price = parse_price(a)
    if price is None:
        price = parse_price(norm(t))

    stops_m = _STOPS.search(a)
    if stops_m:
        stops = 0 if stops_m.group(1).lower().startswith("nonstop") else int(stops_m.group(2))
        airline_names = al.split_airlines(stops_m.group(3))
    else:
        stops = len(_LAYOVER.findall(a))
        airline_names = []
    op = _OPERATED.search(a)
    if op and not airline_names:
        airline_names = al.split_airlines(op.group(1))

    route = _ROUTE_CODES.search(t)
    dep_code = route.group(1) if route else airport_code(lv.group("dep"))
    arr_code = route.group(2) if route else airport_code(lv.group("arr"))

    dep_date = _date(lv.group("dm"), lv.group("dd"), ref_date)
    arr_date = _date(lv.group("am"), lv.group("ad"), ref_date)
    dep_time = f"{dep_date.isoformat()}T{_time24(lv.group('dt'))}" if dep_date else None
    arr_time = f"{arr_date.isoformat()}T{_time24(lv.group('at'))}" if arr_date else None

    dur = _DURATION.search(a)
    duration = parse_duration(dur.group(1)) if dur else None

    layovers: list[Layover] = []
    lay_codes = _LAYOVER_CODES.findall(norm(t))
    lay_codes = [c for c in lay_codes if c not in (dep_code, arr_code)]
    for i, m in enumerate(_LAYOVER.finditer(a)):
        rest = (m.group("rest") or "").lower()
        code = lay_codes[i] if i < len(lay_codes) else airport_code(m.group("ap"))
        layovers.append(
            Layover(
                airport=code,
                city=norm(m.group("city")),
                duration_min=parse_duration(m.group("dur")) or 0,
                overnight=bool(m.group("ovn")),
                airport_change=("change" in rest and "airport" in rest) or "transfer to" in rest,
            )
        )
    if stops and not layovers:
        layovers_val: list[Layover] | None = None
    else:
        layovers_val = layovers

    tl = (a + " " + norm(t)).lower()
    self_transfer = "self transfer" in tl or "self-transfer" in tl or "separate tickets" in tl

    leg = Leg(
        dep_airport=dep_code,
        arr_airport=arr_code,
        dep_time=dep_time,
        arr_time=arr_time,
        duration_min=duration,
        stops=stops,
        layovers=layovers_val,
        airlines=[al.display_name(n) for n in airline_names],
        self_transfer=self_transfer,
    )
    return ParsedOption(price_total=price, leg=leg, aria=aria, text=t)


_LEVEL = re.compile(r"prices are currently\s+(low|typical|high)", re.I)


def price_level(page_text: str) -> str | None:
    m = _LEVEL.search(norm(page_text))
    return m.group(1).lower() if m else None


_VENDOR = re.compile(r"Book with\s+([^\n€]+?)\s*(?:\n|€|$)", re.I)


def booking_vendors(page_text: str) -> list[str]:
    out: list[str] = []
    for m in _VENDOR.finditer(page_text or ""):
        v = norm(m.group(1))
        if v and v not in out and len(v) < 60:
            out.append(v)
    return out[:8]
