"""Airline reference data: names, IATA codes, websites, checked-bag policy.

Baggage policy describes the *cheapest economy fare* on Europe ⇄ Japan
long-haul as of 2025/26. It is a heuristic – always verify on the booking page.

  "included"  – every economy fare includes ≥ 1 checked bag
  "extra"     – cheapest "Light/Basic" fare is hand-luggage only
"""
from __future__ import annotations

import re

# code: (display name, policy, website, [aliases as Google Flights writes them])
AIRLINES: dict[str, tuple[str, str, str, list[str]]] = {
    "NH": ("ANA", "included", "https://www.ana.co.jp/en/de/", ["All Nippon Airways"]),
    "JL": ("Japan Airlines", "included", "https://www.jal.co.jp/de/en/", ["JAL"]),
    "TK": ("Turkish Airlines", "included", "https://www.turkishairlines.com/en-de/", []),
    "EK": ("Emirates", "included", "https://www.emirates.com/de/english/", []),
    "QR": ("Qatar Airways", "included", "https://www.qatarairways.com/en-de/homepage.html", []),
    "EY": ("Etihad", "included", "https://www.etihad.com/en-de/", ["Etihad Airways"]),
    "LO": ("LOT", "included", "https://www.lot.com/de/en", ["LOT Polish Airlines"]),
    "CA": ("Air China", "included", "https://www.airchina.de/", []),
    "MU": ("China Eastern", "included", "https://de.ceair.com/", ["China Eastern Airlines"]),
    "CZ": ("China Southern", "included", "https://www.csair.com/de/en/", ["China Southern Airlines"]),
    "HU": ("Hainan Airlines", "included", "https://www.hainanairlines.com/", []),
    "CX": ("Cathay Pacific", "included", "https://www.cathaypacific.com/cx/en_DE.html", []),
    "KE": ("Korean Air", "included", "https://www.koreanair.com/de/en", []),
    "OZ": ("Asiana", "included", "https://flyasiana.com/C/DE/EN/index", ["Asiana Airlines"]),
    "SQ": ("Singapore Airlines", "included", "https://www.singaporeair.com/en_UK/de/home", []),
    "TG": ("Thai Airways", "included", "https://www.thaiairways.com/", ["Thai"]),
    "VN": ("Vietnam Airlines", "included", "https://www.vietnamairlines.com/de/en/", []),
    "AI": ("Air India", "included", "https://www.airindia.com/", []),
    "MS": ("EgyptAir", "included", "https://www.egyptair.com/", []),
    "LH": ("Lufthansa", "extra", "https://www.lufthansa.com/de/en/homepage", []),
    "LX": ("SWISS", "extra", "https://www.swiss.com/de/en/homepage", ["Swiss"]),
    "OS": ("Austrian", "extra", "https://www.austrian.com/de/en/homepage", ["Austrian Airlines"]),
    "SN": ("Brussels Airlines", "extra", "https://www.brusselsairlines.com/de/en/homepage", []),
    "EN": ("Air Dolomiti", "extra", "https://www.airdolomiti.eu/", []),
    "AY": ("Finnair", "extra", "https://www.finnair.com/de-en", []),
    "KL": ("KLM", "extra", "https://www.klm.de/en", []),
    "AF": ("Air France", "extra", "https://wwws.airfrance.de/en", []),
    "BA": ("British Airways", "extra", "https://www.britishairways.com/", []),
    "IB": ("Iberia", "extra", "https://www.iberia.com/de/en/", []),
    "SK": ("SAS", "extra", "https://www.flysas.com/de-en/", ["Scandinavian Airlines"]),
    "AZ": ("ITA Airways", "extra", "https://www.ita-airways.com/en_de", ["ITA"]),
    "DE": ("Condor", "extra", "https://www.condor.com/de/", []),
    "EW": ("Eurowings", "extra", "https://www.eurowings.com/en.html", []),
    "VS": ("Virgin Atlantic", "extra", "https://www.virginatlantic.com/", []),
    "ZG": ("ZIPAIR", "extra", "https://www.zipair.net/en", ["Zipair"]),
}

_BY_NAME: dict[str, str] = {}
for _code, (_name, _p, _w, _aliases) in AIRLINES.items():
    for n in [_name, *_aliases]:
        _BY_NAME[n.lower()] = _code


def code_for(name_or_code: str) -> str | None:
    s = name_or_code.strip()
    if s.upper() in AIRLINES:
        return s.upper()
    return _BY_NAME.get(s.lower())


def display_name(name_or_code: str) -> str:
    c = code_for(name_or_code)
    return AIRLINES[c][0] if c else name_or_code.strip()


def website(name_or_code: str) -> str | None:
    c = code_for(name_or_code)
    return AIRLINES[c][2] if c else None


def split_airlines(text: str) -> list[str]:
    """'Lufthansa and ANA' / 'Air China, Lufthansa' → ['Lufthansa', 'ANA']."""
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    parts = re.split(r",\s*|\s+and\s+|\s*·\s*", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if p and p not in out:
            out.append(p)
    return out


def bag_policy(airlines: list[str]) -> tuple[str, str]:
    """Return (status, note) for an itinerary flown by these airlines.

    status: "included" | "extra" | "unknown"
    """
    if not airlines:
        return "unknown", "Airline unknown – check the fare's baggage allowance."
    policies = []
    for a in airlines:
        c = code_for(a)
        policies.append(AIRLINES[c][1] if c else "unknown")
    if all(p == "included" for p in policies):
        return "included", "Economy fares of this airline normally include a checked bag."
    if all(p == "extra" for p in policies):
        return "extra", (
            f"{display_name(airlines[0])}'s cheapest long-haul fare is usually hand-luggage only "
            "– pick a fare with a checked bag."
        )
    if "extra" in policies:
        return "unknown", "Mixed carriers – baggage depends on the ticketing airline's fare."
    return "unknown", "Baggage policy unknown – check before booking."


def bag_extra_total(status: str, adults: int, fee_per_bag_per_direction: float) -> float:
    """Estimated extra cost to add one checked bag per person, both directions."""
    if status == "included":
        return 0.0
    return round(fee_per_bag_per_direction * 2 * adults, 2)
