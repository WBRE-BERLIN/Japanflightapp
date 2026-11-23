"""Build Google Flights search URLs.

Google encodes a flight search in the ``tfs`` query parameter as a
base64url-encoded protobuf message. Layout (reverse engineered, matches the
URLs the Google Flights UI produces):

  Info {
    1:  int   (28, constant)
    2:  int   (2,  constant)
    3:  FlightData (repeated, one per leg)
    8:  Passenger enum (repeated, 1 = adult)
    9:  Seat enum (1 economy, 2 premium economy, 3 business, 4 first)
    14: int   (1, constant)
    16: { 1: int64 } (-1 = no max price)
    19: Trip enum (1 round trip, 2 one way, 3 multi city)
  }
  FlightData {
    2:  date "YYYY-MM-DD"
    5:  max stops (optional)
    13: Airport from (repeated)
    14: Airport to   (repeated)
  }
  Airport { 1: type (1 = airport), 2: IATA code }
"""
from __future__ import annotations

import base64
import datetime as dt
from urllib.parse import urlencode

SEAT = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}
TRIP = {"round_trip": 1, "one_way": 2, "multi_city": 3}


def _varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _int(field: int, v: int) -> bytes:
    return _key(field, 0) + _varint(v)


def _bytes(field: int, data: bytes) -> bytes:
    return _key(field, 2) + _varint(len(data)) + data


def _airport(code: str) -> bytes:
    return _int(1, 1) + _bytes(2, code.encode())


def _leg(date: dt.date, frm: list[str], to: list[str], max_stops: int | None) -> bytes:
    b = _bytes(2, date.isoformat().encode())
    if max_stops is not None:
        b += _int(5, max_stops)
    for c in frm:
        b += _bytes(13, _airport(c))
    for c in to:
        b += _bytes(14, _airport(c))
    return b


def build_tfs(
    legs: list[tuple[dt.date, list[str], list[str]]],
    *,
    adults: int = 1,
    seat: str = "economy",
    trip: str = "round_trip",
    max_stops: int | None = None,
) -> str:
    msg = _int(1, 28) + _int(2, 2)
    for date, frm, to in legs:
        msg += _bytes(3, _leg(date, frm, to, max_stops))
    msg += _int(8, 1) * adults
    msg += _int(9, SEAT.get(seat, 1))
    msg += _int(14, 1)
    msg += _bytes(16, _int(1, -1))
    msg += _int(19, TRIP[trip])
    return base64.urlsafe_b64encode(msg).decode().rstrip("=")


def google_search_url(
    *,
    origin: list[str],
    dest: list[str],
    depart: dt.date,
    ret: dt.date,
    adults: int,
    seat: str = "economy",
    max_stops: int | None = 1,
    currency: str = "EUR",
    hl: str = "en",
    gl: str = "de",
) -> str:
    tfs = build_tfs(
        [(depart, origin, dest), (ret, dest, origin)],
        adults=adults, seat=seat, trip="round_trip", max_stops=max_stops,
    )
    return "https://www.google.com/travel/flights/search?" + urlencode(
        {"tfs": tfs, "hl": hl, "gl": gl, "curr": currency}
    )


# ── decoder (used by tests / debugging) ─────────────────────────────────────
def decode(tfs: str) -> list[tuple[int, object]]:
    data = base64.urlsafe_b64decode(tfs + "=" * (-len(tfs) % 4))
    return _decode(data)


def _decode(data: bytes) -> list[tuple[int, object]]:
    i, out = 0, []
    while i < len(data):
        key, i = _read_varint(data, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            v, i = _read_varint(data, i)
            out.append((field, v))
        elif wire == 2:
            ln, i = _read_varint(data, i)
            chunk = data[i: i + ln]
            i += ln
            if chunk and all(32 <= c < 127 for c in chunk):
                out.append((field, chunk.decode()))
                continue
            try:
                inner = _decode(chunk)
                out.append((field, inner if inner and all(isinstance(f, int) and f > 0 for f, _ in inner) else chunk))
            except Exception:
                out.append((field, chunk))
        else:
            raise ValueError(f"unsupported wire type {wire}")
    return out


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    shift = n = 0
    while True:
        b = data[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, i
        shift += 7
