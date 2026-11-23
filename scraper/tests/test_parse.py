import datetime as dt

from flightscan.sources.gf_parse import (
    airport_code, booking_vendors, parse_duration, parse_option, parse_price, price_level,
)

OUT_ARIA = (
    "From 1,846 euros round trip total. 1 stop flight with Finnair. Leaves Berlin Brandenburg Airport "
    "at 7:05 AM on Monday, May 10 and arrives at Haneda Airport at 8:35 AM on Tuesday, May 11. "
    "Total duration 18 hr 30 min. Layover (1 of 1) is a 3 hr 10 min layover at Helsinki Airport in Helsinki. "
    "Select flight"
)
OUT_TEXT = "7:05 AM – 8:35 AM+1\nFinnair\n18 hr 30 min\nBER–HND\n1 stop\n3 hr 10 min HEL\n€1,846\nround trip"

RET_ARIA = (
    "1846 euros round trip total. 1 stop flight with Lufthansa and ANA. Leaves Narita International Airport "
    "at 11:20 PM on Friday, May 21 and arrives at Berlin Brandenburg Airport at 9:05 AM on "
    "Saturday, May 22. Total duration 16 hr 45 min. Layover (1 of 1) is a 1 hr layover at Munich "
    "International Airport in Munich. Select flight"
)


def test_parse_outbound():
    p = parse_option(OUT_ARIA, OUT_TEXT, dt.date(2027, 5, 10))
    assert p is not None
    assert p.price_total == 1846
    leg = p.leg
    assert (leg.dep_airport, leg.arr_airport) == ("BER", "HND")
    assert leg.dep_time == "2027-05-10T07:05"
    assert leg.arr_time == "2027-05-11T08:35"
    assert leg.duration_min == 18 * 60 + 30
    assert leg.stops == 1
    assert leg.airlines == ["Finnair"]
    assert len(leg.layovers) == 1
    assert leg.layovers[0].airport == "HEL"
    assert leg.layovers[0].duration_min == 190
    assert not leg.self_transfer


def test_parse_return_names_only():
    p = parse_option(RET_ARIA, "", dt.date(2027, 5, 21))
    assert p.price_total == 1846
    assert p.leg.dep_airport == "NRT"
    assert p.leg.arr_airport == "BER"
    assert p.leg.dep_time == "2027-05-21T23:20"
    assert p.leg.airlines == ["Lufthansa", "ANA"]
    assert p.leg.layovers[0].airport == "MUC"
    assert p.leg.layovers[0].duration_min == 60


def test_overnight_and_self_transfer():
    aria = (
        "From 990 euros round trip total. 1 stop flight with Air China. Leaves Berlin Brandenburg Airport at "
        "1:15 PM on Saturday, May 8 and arrives at Kansai International Airport at 6:40 PM on Sunday, May 9. "
        "Total duration 22 hr 25 min. Layover (1 of 1) is a 9 hr 5 min overnight layover at Beijing Capital "
        "International Airport in Beijing. Select flight"
    )
    p = parse_option(aria, "Self transfer\nBER–KIX", dt.date(2027, 5, 8))
    assert p.leg.arr_airport == "KIX"
    assert p.leg.layovers[0].overnight
    assert p.leg.layovers[0].airport == "PEK"
    assert p.leg.self_transfer


def test_nonstop_and_unparseable():
    aria = ("From 2,100 euros round trip total. Nonstop flight with ANA. Leaves Frankfurt Airport at 12:00 PM on "
            "Monday, May 10 and arrives at Haneda Airport at 7:30 AM on Tuesday, May 11. Total duration 12 hr 30 min.")
    p = parse_option(aria, "", dt.date(2027, 5, 10))
    assert p.leg.stops == 0 and p.leg.layovers == [] and p.leg.dep_airport == "FRA"
    assert parse_option("Select a flight", "", dt.date(2027, 5, 10)) is None


def test_year_rollover():
    aria = ("From 900 euros round trip total. 1 stop flight with LOT. Leaves Berlin Brandenburg Airport at "
            "6:00 AM on Friday, December 31 and arrives at Narita International Airport at 9:00 AM on Saturday, "
            "January 1. Total duration 17 hr. Layover (1 of 1) is a 2 hr layover at Warsaw Chopin Airport in Warsaw.")
    p = parse_option(aria, "", dt.date(2027, 12, 31))
    assert p.leg.arr_time == "2028-01-01T09:00"
    assert p.leg.duration_min == 17 * 60
    assert p.leg.layovers[0].airport == "WAW"


def test_helpers():
    assert parse_duration("18 hr 30 min") == 1110
    assert parse_duration("45 min") == 45
    assert parse_duration("junk") is None
    assert parse_price("€1,234 round trip") == 1234
    assert parse_price("1.234 €") == 1234
    assert airport_code("Haneda Airport") == "HND"
    assert airport_code("Narita International Airport") == "NRT"
    assert airport_code("Hamad International Airport") == "DOH"
    assert airport_code("Unknown Field") == "Unknown Field"
    assert price_level("Prices are currently typical for your search") == "typical"
    assert booking_vendors("Book with Finnair\n€1,846\nBook with Trip.com\n€1,790") == ["Finnair", "Trip.com"]
