"""Load and validate config.toml."""
from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config.toml"


@dataclass(frozen=True)
class Limits:
    max_stops: int
    max_layover_min: int
    max_duration_min: int
    allow_self_transfer: bool = False


@dataclass(frozen=True)
class Destination:
    code: str
    name: str
    airports: tuple[str, ...]


@dataclass(frozen=True)
class Window:
    id: str
    label: str
    depart_from: dt.date
    depart_to: dt.date
    latest_return: dt.date | None
    nights: tuple[int, ...]


@dataclass(frozen=True)
class Wildcards:
    enabled: bool
    start: dt.date
    end: dt.date
    nights: tuple[int, ...]
    exclude: tuple[tuple[dt.date, dt.date], ...]
    min_saving_pct: float
    samples_per_run: int


@dataclass(frozen=True)
class ScrapeSettings:
    concurrency: int = 3
    full_run_hours_utc: tuple[int, ...] = (0, 1, 2, 3, 4, 5)
    refresh_top_n: int = 24
    outbound_candidates: int = 2
    fetch_booking_link: str = "refresh"   # always | refresh | never
    max_runtime_minutes: int = 40
    max_days_ahead: int = 330
    stale_after_hours: int = 60
    google_hl: str = "en"
    google_gl: str = "de"


@dataclass(frozen=True)
class Config:
    origin: tuple[str, ...]
    adults: int
    cabin: str
    currency: str
    destinations: dict[str, Destination]
    windows: tuple[Window, ...]
    wildcards: Wildcards
    constraints: Limits
    filters: Limits
    bag_fee: float
    scrape: ScrapeSettings
    sources: dict[str, bool] = field(default_factory=dict)

    def window(self, wid: str) -> Window | None:
        return next((w for w in self.windows if w.id == wid), None)

    def public(self) -> dict:
        """The part of the config the web app needs."""
        return {
            "origin": list(self.origin),
            "adults": self.adults,
            "cabin": self.cabin,
            "currency": self.currency,
            "destinations": {
                k: {"name": d.name, "airports": list(d.airports)} for k, d in self.destinations.items()
            },
            "windows": [
                {
                    "id": w.id,
                    "label": w.label,
                    "depart_from": w.depart_from.isoformat(),
                    "depart_to": w.depart_to.isoformat(),
                    "latest_return": w.latest_return.isoformat() if w.latest_return else None,
                    "nights": list(w.nights),
                }
                for w in self.windows
            ],
            "wildcards": {
                "enabled": self.wildcards.enabled,
                "min_saving_pct": self.wildcards.min_saving_pct,
                "nights": list(self.wildcards.nights),
            },
            "filters": {
                "max_layover_hours": self.filters.max_layover_min / 60,
                "max_duration_hours": self.filters.max_duration_min / 60,
                "max_stops": self.filters.max_stops,
            },
            "constraints": {
                "max_layover_hours": self.constraints.max_layover_min / 60,
                "max_duration_hours": self.constraints.max_duration_min / 60,
                "max_stops": self.constraints.max_stops,
            },
            "baggage": {"fee_per_bag_per_direction": self.bag_fee},
        }


def _hours(v: float) -> int:
    return int(round(float(v) * 60))


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else DEFAULT_CONFIG
    with open(p, "rb") as fh:
        raw = tomllib.load(fh)

    trip = raw.get("trip", {})
    origin = trip.get("origin", ["BER"])
    if isinstance(origin, str):
        origin = [origin]

    dests = {
        code: Destination(code=code, name=d.get("name", code), airports=tuple(d["airports"]))
        for code, d in raw.get("destinations", {}).items()
    }
    if not dests:
        raise ValueError("config: at least one [destinations.X] block is required")

    windows = []
    for w in raw.get("windows", []):
        win = Window(
            id=str(w["id"]),
            label=str(w.get("label", w["id"])),
            depart_from=_as_date(w["depart_from"]),
            depart_to=_as_date(w["depart_to"]),
            latest_return=_as_date(w["latest_return"]) if w.get("latest_return") else None,
            nights=tuple(int(n) for n in w.get("nights", [10, 11, 12])),
        )
        if win.depart_to < win.depart_from:
            raise ValueError(f"config: window {win.id}: depart_to before depart_from")
        if win.id == "wild":
            raise ValueError("config: window id 'wild' is reserved for wildcards")
        windows.append(win)

    wc = raw.get("wildcards", {})
    wildcards = Wildcards(
        enabled=bool(wc.get("enabled", False)),
        start=_as_date(wc.get("from", dt.date.today())),
        end=_as_date(wc.get("to", dt.date.today() + dt.timedelta(days=330))),
        nights=tuple(int(n) for n in wc.get("nights", [10, 11, 12])),
        exclude=tuple((_as_date(a), _as_date(b)) for a, b in wc.get("exclude", [])),
        min_saving_pct=float(wc.get("min_saving_pct", 15)),
        samples_per_run=int(wc.get("samples_per_run", 6)),
    )

    c = raw.get("constraints", {})
    constraints = Limits(
        max_stops=int(c.get("max_stops", 1)),
        max_layover_min=_hours(c.get("max_layover_hours", 6)),
        max_duration_min=_hours(c.get("max_duration_hours", 24)),
        allow_self_transfer=bool(c.get("allow_self_transfer", False)),
    )
    f = raw.get("filters", {})
    filters = Limits(
        max_stops=int(f.get("max_stops", constraints.max_stops)),
        max_layover_min=_hours(f.get("max_layover_hours", 4)),
        max_duration_min=_hours(f.get("max_duration_hours", 19)),
        allow_self_transfer=constraints.allow_self_transfer,
    )

    s = raw.get("scrape", {})
    defaults = ScrapeSettings()
    scrape = ScrapeSettings(
        concurrency=max(1, int(s.get("concurrency", defaults.concurrency))),
        full_run_hours_utc=tuple(int(h) for h in s.get("full_run_hours_utc", defaults.full_run_hours_utc)),
        refresh_top_n=int(s.get("refresh_top_n", defaults.refresh_top_n)),
        outbound_candidates=max(1, int(s.get("outbound_candidates", defaults.outbound_candidates))),
        fetch_booking_link=_booking_mode(s.get("fetch_booking_link", defaults.fetch_booking_link)),
        max_runtime_minutes=int(s.get("max_runtime_minutes", defaults.max_runtime_minutes)),
        max_days_ahead=int(s.get("max_days_ahead", defaults.max_days_ahead)),
        stale_after_hours=int(s.get("stale_after_hours", defaults.stale_after_hours)),
        google_hl=str(s.get("google_hl", defaults.google_hl)),
        google_gl=str(s.get("google_gl", defaults.google_gl)),
    )

    return Config(
        origin=tuple(origin),
        adults=int(trip.get("adults", 2)),
        cabin=str(trip.get("cabin", "economy")),
        currency=str(trip.get("currency", "EUR")).upper(),
        destinations=dests,
        windows=tuple(windows),
        wildcards=wildcards,
        constraints=constraints,
        filters=filters,
        bag_fee=float(raw.get("baggage", {}).get("fee_per_bag_per_direction", 75)),
        scrape=scrape,
        sources={k: bool(v) for k, v in raw.get("sources", {}).items()},
    )


def _booking_mode(v) -> str:
    if v is True:
        return "always"
    if v is False:
        return "never"
    v = str(v).lower()
    if v not in ("always", "refresh", "never"):
        raise ValueError("config: scrape.fetch_booking_link must be true, false or \"refresh\"")
    return v


def _as_date(v) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))
