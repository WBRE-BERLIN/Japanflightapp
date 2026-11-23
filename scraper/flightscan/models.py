"""Plain data structures shared by sources, filters and output."""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import asdict, dataclass, field


@dataclass
class Layover:
    airport: str                 # IATA code if known, otherwise airport name
    duration_min: int
    city: str = ""
    overnight: bool = False
    airport_change: bool = False


@dataclass
class Leg:
    dep_airport: str
    arr_airport: str
    dep_time: str | None         # local time, ISO "2027-05-10T07:05"
    arr_time: str | None
    duration_min: int | None
    stops: int
    layovers: list[Layover] | None = None   # None → unknown (e.g. cached API data)
    airlines: list[str] = field(default_factory=list)
    self_transfer: bool = False

    @property
    def max_layover_min(self) -> int | None:
        if self.layovers is None:
            return None
        return max((l.duration_min for l in self.layovers), default=0)


@dataclass
class SearchTask:
    """One round-trip search: destination city + dates."""
    dest: str                    # destination city code from config (TYO, OSA …)
    depart: dt.date
    ret: dt.date
    window: str                  # window id or "wild"
    priority: float = 0.0

    @property
    def key(self) -> str:
        return pair_key(self.dest, self.depart, self.ret)

    @property
    def nights(self) -> int:
        return (self.ret - self.depart).days


@dataclass
class Offer:
    source: str                  # google_flights | travelpayouts | demo
    dest: str                    # city code
    window: str
    depart: str                  # ISO date
    ret: str
    price_total: float           # all travellers, round trip
    adults: int
    currency: str
    outbound: Leg | None
    inbound: Leg | None
    airlines: list[str] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    found_at: str = ""
    price_level: str | None = None          # Google's "low / typical / high"
    booking_vendors: list[str] = field(default_factory=list)
    price_is_estimate: bool = False          # e.g. cached per-person price × adults

    @property
    def pair(self) -> str:
        return f"{self.dest}|{self.depart}|{self.ret}"

    @property
    def price_pp(self) -> float:
        return round(self.price_total / max(1, self.adults), 2)

    @property
    def nights(self) -> int:
        return (dt.date.fromisoformat(self.ret) - dt.date.fromisoformat(self.depart)).days

    @property
    def id(self) -> str:
        parts = [self.source, self.pair]
        for leg in (self.outbound, self.inbound):
            if leg:
                parts += [leg.dep_airport, leg.arr_airport, leg.dep_time or "", ",".join(leg.airlines)]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        d["pair"] = self.pair
        d["price_pp"] = self.price_pp
        d["nights"] = self.nights
        return d

    @staticmethod
    def from_dict(d: dict) -> "Offer":
        def leg(x):
            if not x:
                return None
            lays = x.get("layovers")
            return Leg(
                dep_airport=x["dep_airport"],
                arr_airport=x["arr_airport"],
                dep_time=x.get("dep_time"),
                arr_time=x.get("arr_time"),
                duration_min=x.get("duration_min"),
                stops=x.get("stops", 0),
                layovers=None if lays is None else [Layover(**l) for l in lays],
                airlines=list(x.get("airlines", [])),
                self_transfer=bool(x.get("self_transfer", False)),
            )

        return Offer(
            source=d["source"],
            dest=d["dest"],
            window=d["window"],
            depart=d["depart"],
            ret=d["ret"],
            price_total=float(d["price_total"]),
            adults=int(d["adults"]),
            currency=d.get("currency", "EUR"),
            outbound=leg(d.get("outbound")),
            inbound=leg(d.get("inbound")),
            airlines=list(d.get("airlines", [])),
            links=list(d.get("links", [])),
            flags=list(d.get("flags", [])),
            found_at=d.get("found_at", ""),
            price_level=d.get("price_level"),
            booking_vendors=list(d.get("booking_vendors", [])),
            price_is_estimate=bool(d.get("price_is_estimate", False)),
        )


def pair_key(dest: str, depart: dt.date | str, ret: dt.date | str) -> str:
    d = depart.isoformat() if isinstance(depart, dt.date) else depart
    r = ret.isoformat() if isinstance(ret, dt.date) else ret
    return f"{dest}|{d}|{r}"


def utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
