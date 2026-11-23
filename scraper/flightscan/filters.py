"""Itinerary constraints (stops, layover, travel time, self-transfer)."""
from __future__ import annotations

from .config import Limits
from .models import Leg, Offer


def leg_ok(leg: Leg | None, limits: Limits) -> bool:
    """True if the leg satisfies the limits. Unknown values are accepted."""
    if leg is None:
        return True
    if leg.stops > limits.max_stops:
        return False
    if leg.duration_min is not None and leg.duration_min > limits.max_duration_min:
        return False
    lay = leg.max_layover_min
    if lay is not None and lay > limits.max_layover_min:
        return False
    if leg.self_transfer and not limits.allow_self_transfer:
        return False
    return True


def offer_ok(offer: Offer, limits: Limits) -> bool:
    return leg_ok(offer.outbound, limits) and leg_ok(offer.inbound, limits)


def leg_flags(leg: Leg | None, prefix: str) -> list[str]:
    if leg is None:
        return [f"{prefix}_unknown"]
    flags = []
    if leg.layovers is None and leg.stops > 0:
        flags.append(f"{prefix}_layover_unknown")
    for l in leg.layovers or []:
        if l.overnight:
            flags.append(f"{prefix}_overnight_layover")
        if l.airport_change:
            flags.append(f"{prefix}_airport_change")
        if l.duration_min and l.duration_min < 60:
            flags.append(f"{prefix}_short_connection")
    if leg.self_transfer:
        flags.append(f"{prefix}_self_transfer")
    return flags
