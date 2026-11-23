"""Booking / comparison deep links for a round trip."""
from __future__ import annotations

import datetime as dt
from urllib.parse import quote

from . import airlines as al
from .sources.tfs import google_search_url

SKYSCANNER_CITY = {"TYO": "tyoa", "OSA": "osaa"}


def build_links(
    *,
    origin: str,
    dest_city: str,
    dest_airport: str | None,
    depart: str,
    ret: str,
    adults: int,
    currency: str,
    dest_airports: list[str],
    airline_names: list[str],
    google_booking_url: str | None = None,
    hl: str = "en",
    gl: str = "de",
) -> list[dict]:
    d = dt.date.fromisoformat(depart)
    r = dt.date.fromisoformat(ret)
    links: list[dict] = []
    if google_booking_url:
        links.append({"kind": "google_booking", "label": "Book (Google Flights)", "url": google_booking_url})
    gf_dest = [dest_airport] if dest_airport else dest_airports
    links.append(
        {
            "kind": "google",
            "label": "Google Flights",
            "url": google_search_url(
                origin=[origin], dest=gf_dest, depart=d, ret=r, adults=adults,
                max_stops=1, currency=currency, hl=hl, gl=gl,
            ),
        }
    )
    sky_dest = (dest_airport or SKYSCANNER_CITY.get(dest_city, dest_city)).lower()
    links.append(
        {
            "kind": "skyscanner",
            "label": "Skyscanner",
            "url": f"https://www.skyscanner.de/transport/fluge/{origin.lower()}/{sky_dest}/"
            f"{d:%y%m%d}/{r:%y%m%d}/?adultsv2={adults}&cabinclass=economy&rtn=1&preferDirects=false",
        }
    )
    kayak_dest = dest_airport or dest_city
    links.append(
        {
            "kind": "kayak",
            "label": "KAYAK",
            "url": f"https://www.kayak.de/flights/{origin}-{kayak_dest}/{d:%Y-%m-%d}/{r:%Y-%m-%d}/"
            f"{adults}adults?sort=price_a",
        }
    )
    seen = set()
    for name in airline_names:
        url = al.website(name)
        if url and url not in seen:
            seen.add(url)
            links.append({"kind": "airline", "label": al.display_name(name), "url": url})
    return links


def aviasales_link(path: str, marker: str | None = None) -> str:
    url = "https://www.aviasales.com" + (path if path.startswith("/") else "/" + path)
    if marker:
        url += ("&" if "?" in url else "?") + "marker=" + quote(marker)
    return url
