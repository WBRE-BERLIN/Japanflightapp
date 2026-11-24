import datetime as dt
import json
from pathlib import Path

from flightscan import airlines as al
from flightscan.config import Limits, load_config
from flightscan.dates import classify, wildcard_sample, window_pairs, window_tasks
from flightscan.filters import leg_ok, offer_ok
from flightscan.links import build_links
from flightscan.models import Layover, Leg, Offer, SearchTask
from flightscan.report import build_latest, history_points
from flightscan.sources.google_flights import choose
from flightscan.sources.gf_parse import ParsedOption
from flightscan.sources.tfs import build_tfs, decode, google_search_url
from flightscan.sources.travelpayouts import to_offer
from flightscan.store import Store

CFG = load_config()
TODAY = dt.date(2026, 11, 23)


def leg(dur=1000, lay=120, stops=1, airlines=("Finnair",), self_transfer=False):
    return Leg("BER", "HND", "2027-05-10T07:05", None, dur, stops,
               [Layover("HEL", lay)] if stops else [], list(airlines), self_transfer)


def offer(price=1800, out=None, inn=None, window="may", depart="2027-05-10", ret="2027-05-21", airlines=("Finnair",)):
    return Offer("google_flights", "TYO", window, depart, ret, price, 2, "EUR", out or leg(), inn or leg(),
                 list(airlines), found_at="2026-11-23T02:00:00Z")


def test_booking_mode():
    assert CFG.scrape.fetch_booking_link in ("always", "refresh", "never")
    from flightscan.config import _booking_mode
    assert _booking_mode(True) == "always" and _booking_mode(False) == "never" and _booking_mode("refresh") == "refresh"


def test_config_windows():
    may = CFG.window("may")
    assert may.depart_from == dt.date(2027, 5, 7)
    pairs = window_pairs(may)
    assert len(pairs) == 14 * 3
    assert all(r <= may.latest_return for _, r in pairs)
    oct_pairs = window_pairs(CFG.window("oct"))
    assert (dt.date(2027, 10, 21), dt.date(2027, 11, 2)) not in oct_pairs
    limit = TODAY + dt.timedelta(days=CFG.scrape.max_days_ahead)
    reachable = sum(r <= limit for _, r in pairs + oct_pairs)
    assert len(window_tasks(CFG, TODAY)) == reachable * len(CFG.destinations)
    assert all(t.ret <= limit for t in window_tasks(CFG, TODAY))


def test_classify():
    assert classify(CFG, dt.date(2027, 5, 10), dt.date(2027, 5, 21)) == "may"
    assert classify(CFG, dt.date(2027, 3, 2), dt.date(2027, 3, 13)) == "wild"
    assert classify(CFG, dt.date(2027, 4, 28), dt.date(2027, 5, 9)) is None     # golden week
    assert classify(CFG, dt.date(2027, 3, 2), dt.date(2027, 3, 9)) is None      # too short
    assert classify(CFG, dt.date(2027, 5, 1), dt.date(2027, 5, 12)) is None     # overlaps window + GW


def test_wildcard_sample_spreads_months():
    s = wildcard_sample(CFG, TODAY, 6, seed=1)
    assert len(s) == 6
    assert len({t.depart.month for t in s}) >= 4
    assert all(classify(CFG, t.depart, t.ret) == "wild" for t in s)


def test_filters():
    strict = CFG.filters
    assert leg_ok(leg(dur=17 * 60, lay=180), strict)
    assert not leg_ok(leg(dur=20 * 60), strict)
    assert not leg_ok(leg(lay=5 * 60), strict)
    assert not leg_ok(leg(stops=2), strict)
    assert not leg_ok(leg(self_transfer=True), strict)
    assert leg_ok(None, strict)
    assert offer_ok(offer(), Limits(1, 240, 19 * 60))


def test_bag_policy():
    assert al.bag_policy(["ANA"])[0] == "included"
    assert al.bag_policy(["Turkish Airlines"])[0] == "included"
    assert al.bag_policy(["Finnair"])[0] == "extra"
    assert al.bag_policy(["Lufthansa", "ANA"])[0] == "unknown"
    assert al.bag_policy([])[0] == "unknown"
    assert al.bag_extra_total("extra", 2, 75) == 300
    assert al.bag_extra_total("included", 2, 75) == 0
    assert al.split_airlines("Lufthansa and All Nippon Airways") == ["Lufthansa", "All Nippon Airways"]
    assert al.display_name("All Nippon Airways") == "ANA"


def test_tfs_roundtrip():
    tfs = build_tfs([(dt.date(2027, 5, 10), ["BER"], ["HND", "NRT"]), (dt.date(2027, 5, 21), ["HND", "NRT"], ["BER"])],
                    adults=2, max_stops=1)
    fields = decode(tfs)
    legs = [v for f, v in fields if f == 3]
    assert len(legs) == 2
    assert (2, "2027-05-10") in legs[0]
    assert [v for f, v in fields if f == 8] == [1, 1]          # two adults
    assert (19, 1) in fields                                   # round trip
    url = google_search_url(origin=["BER"], dest=["KIX"], depart=dt.date(2027, 5, 10), ret=dt.date(2027, 5, 21),
                            adults=2)
    assert url.startswith("https://www.google.com/travel/flights/search?tfs=") and "curr=EUR" in url


def test_links():
    links = build_links(origin="BER", dest_city="TYO", dest_airport="HND", depart="2027-05-10", ret="2027-05-21",
                        adults=2, currency="EUR", dest_airports=["HND", "NRT"], airline_names=["Finnair", "JAL"])
    kinds = [l["kind"] for l in links]
    assert kinds[:3] == ["google", "skyscanner", "kayak"]
    assert "airline" in kinds
    sky = next(l for l in links if l["kind"] == "skyscanner")["url"]
    assert "/ber/hnd/270510/270521/" in sky and "adultsv2=2" in sky


def _opt(price, dur=1000, lay=120, airline="Finnair"):
    return ParsedOption(price, leg(dur=dur, lay=lay, airlines=(airline,)), aria=f"{price}{airline}", text="")


def test_choose():
    opts = [_opt(1500, dur=21 * 60), _opt(1700), _opt(1650, lay=7 * 60), _opt(1800, airline="LOT")]
    picks = choose(opts, CFG.constraints, CFG.filters, 3)
    assert [p.price_total for p in picks] == [1700, 1500, 1800]   # default-filter option first
    assert choose(opts, CFG.constraints, CFG.filters, 1)[0].price_total == 1700
    only_long = [_opt(1500, dur=21 * 60)]
    assert choose(only_long, CFG.constraints, CFG.filters, 1)[0].price_total == 1500
    assert choose([], CFG.constraints, CFG.filters, 2) == []


def test_travelpayouts_row():
    row = {"origin": "BER", "destination": "TYO", "origin_airport": "BER", "destination_airport": "NRT",
           "price": 712, "airline": "AY", "flight_number": "1432", "departure_at": "2027-05-10T07:05:00+02:00",
           "return_at": "2027-05-21T10:20:00+09:00", "transfers": 1, "return_transfers": 1,
           "duration": 2230, "duration_to": 1010, "duration_back": 1100, "link": "/search/BER1005TYO21051?t=x"}
    o = to_offer(CFG, "TYO", row, "12345")
    assert o.window == "may" and o.price_total == 1424 and o.price_is_estimate
    assert o.airlines == ["Finnair"]
    assert o.links[0]["url"].startswith("https://www.aviasales.com/search/") and "marker=12345" in o.links[0]["url"]
    assert to_offer(CFG, "TYO", {**row, "transfers": 2}, None) is None
    assert to_offer(CFG, "TYO", {**row, "return_at": "2027-05-14T10:00:00+09:00"}, None) is None


def test_store_and_report(tmp_path: Path):
    store = Store(tmp_path)
    now = dt.datetime(2026, 11, 23, 2, 30, tzinfo=dt.timezone.utc)
    t = SearchTask("TYO", dt.date(2027, 5, 10), dt.date(2027, 5, 21), "may")
    cheap_long = offer(1500, out=leg(dur=21 * 60))
    good = offer(1700, airlines=("ANA",), out=leg(airlines=("ANA",)), inn=leg(airlines=("ANA",)))
    store.record_search(t.key, "google_flights", "ok", [cheap_long, good], "2026-11-23T02:10:00Z", "may")
    wild = offer(1000, window="wild", depart="2027-03-02", ret="2027-03-13", airlines=("ANA",),
                 out=leg(airlines=("ANA",)), inn=leg(airlines=("ANA",)))
    store.record_search(wild.pair, "google_flights", "ok", [wild], "2026-11-23T02:10:00Z", "wild")
    latest = build_latest(CFG, store, {"mode": "full"}, now.date(), now)
    s = latest["summary"]
    assert s["windows"]["may"]["best"]["price_total"] == 1700          # 1500 violates 19 h default filter
    assert s["windows"]["may"]["best"]["bag"] == "included"
    assert s["wildcards"][0]["price_total"] == 1000
    assert s["overall"]["price_total"] == 1000
    o1500 = next(o for o in latest["offers"] if o["price_total"] == 1500)
    assert o1500["strict_ok"] is False and o1500["eff_total"] == 1800   # Finnair: +2×2×75
    best, pairs = history_points(CFG, latest["offers"])
    store.update_history(now.date(), best, pairs)
    assert store.history["pairs"][t.key][-1] == ["2026-11-23", 1700, 1500]
    store.save(latest)
    assert json.loads((tmp_path / "latest.json").read_text())["summary"]["overall"]["price_total"] == 1000
    # a later no_results search removes the offers of that pair
    store.record_search(t.key, "google_flights", "no_results", [], "2026-11-24T02:10:00Z", "may")
    assert t.key not in store.state["offers"]


def test_demo(tmp_path: Path):
    from flightscan.demo import generate
    p = generate(CFG, tmp_path)
    data = json.loads(p.read_text())
    assert data["demo"] is True and data["offers"]
    assert data["summary"]["windows"]["may"]["best"]
    hist = json.loads((tmp_path / "history.json").read_text())
    assert len(hist["daily"]) > 20


def test_google_bag_info_overrides_rules():
    from flightscan.report import enrich
    store = Store(Path("/nonexistent-dir-for-test"))
    now = dt.datetime(2026, 11, 23, 2, 30, tzinfo=dt.timezone.utc)
    o = offer(2000)                                 # Finnair → rules say "extra" (+300)
    assert enrich(CFG, o, store, now.date(), now)["eff_total"] == 2300
    o.bag_google = {"text": "€0–90", "min": 0, "max": 90}
    d = enrich(CFG, o, store, now.date(), now)
    assert d["bag"]["status"] == "varies" and d["bag"]["source"] == "Google" and d["eff_total"] == 2180
    o.bag_google = {"text": "Free", "min": 0, "max": 0}
    assert enrich(CFG, o, store, now.date(), now)["bag"]["status"] == "included"
    assert Offer.from_dict(o.to_dict()).bag_google == o.bag_google


def test_circuit_breaker():
    import asyncio
    from flightscan.sources.google_flights import GoogleFlightsSource
    src = GoogleFlightsSource(CFG)

    async def feed(statuses):
        for s in statuses:
            await src._note_outcome(s)

    asyncio.run(feed(["error"] * 4 + ["ok"] + ["error"] * 4))
    assert not src._cooled_down and not src._throttled          # streak was broken by a success
    asyncio.run(feed(["error"]))
    assert src._cooled_down and src._pause_until > 0 and not src._throttled
    asyncio.run(feed(["error"] * 5))
    assert src._throttled


def test_store_keeps_booking_link_for_same_flights(tmp_path: Path):
    store = Store(tmp_path)
    t = SearchTask("TYO", dt.date(2027, 5, 18), dt.date(2027, 5, 30), "may")
    midday = offer(1867)
    midday.links.insert(0, {"kind": "google_booking", "label": "Book (Google Flights)", "url": "https://www.google.com/travel/flights/booking?tfs=x"})
    midday.booking_vendors = [{"name": "JAL", "price": 1867.0, "airline": True, "phone": False}]
    midday.bag_google = {"text": "€0", "min": 0.0, "max": 0.0}
    store.record_search(t.key, "google_flights", "ok", [midday], "2026-11-24T11:00:00Z", "may")
    # the next full run finds the same flights (cheaper) but skips the booking page
    night, other = offer(1850), offer(1900, airlines=("ANA",), out=leg(airlines=("ANA",)), inn=leg(airlines=("ANA",)))
    store.record_search(t.key, "google_flights", "ok", [night, other], "2026-11-25T02:00:00Z", "may")
    kept = {o["id"]: o for o in store.state["offers"][t.key]}
    n = kept[night.id]
    assert n["price_total"] == 1850 and n["links"][0]["kind"] == "google_booking"
    assert n["booking_vendors"][0]["name"] == "JAL" and n["bag_google"]["max"] == 0.0
    assert all(l["kind"] != "google_booking" for l in kept[other.id]["links"])
    assert sum(l["kind"] == "google_booking" for l in n["links"]) == 1


def test_store_drops_removed_destination(tmp_path: Path):
    store = Store(tmp_path)
    tyo = offer(1867)
    osa = offer(1900)
    osa.dest = "OSA"
    store.record_search(tyo.pair, "google_flights", "ok", [tyo], "2026-11-24T11:00:00Z", "may")
    store.record_search(osa.pair, "google_flights", "ok", [osa], "2026-11-24T11:00:00Z", "may")
    store.history["pairs"] = {tyo.pair: [["2026-11-24", 1867, 1867]], osa.pair: [["2026-11-24", 1900, 1900]]}
    store.history["daily"] = {"2026-11-24": {"may|TYO": {}, "may|OSA": {}, "may|*": {}, "wild|OSA": {}}}
    assert store.keep_destinations({"TYO": None}) == 1
    assert list(store.state["offers"]) == [tyo.pair] and list(store.state["checks"]) == [tyo.pair]
    assert list(store.history["pairs"]) == [tyo.pair]
    assert sorted(store.history["daily"]["2026-11-24"]) == ["may|*", "may|TYO"]


def test_plan_single_window(tmp_path: Path):
    from flightscan.runner import plan
    store = Store(tmp_path)
    dec = dt.date(2026, 12, 10)          # every October date is within reach by then
    tasks = plan(CFG, store, "refresh", dec, window="oct")
    assert {t.window for t in tasks} == {"oct"} and len(tasks) == 35 * len(CFG.destinations)
    assert len(plan(CFG, store, "refresh", dec, limit=2, window="oct")) == 2
    assert plan(CFG, store, "refresh", TODAY, window="oct") == []   # Oct 2027 not searchable yet
