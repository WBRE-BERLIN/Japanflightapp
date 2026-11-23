"""Google Flights source (headless Chromium via Playwright).

Google Flights aggregates the published fares of practically every airline
flying Europe ⇄ Japan (Finnair, Lufthansa group, Turkish, ANA, JAL, LOT, Gulf
and Chinese carriers …) plus the online travel agencies selling them, so one
search here covers the airline websites the app is interested in.

The scraper behaves like a single, slow visitor: a few tabs, pauses between
searches, and it stops immediately when Google asks for a captcha.
"""
from __future__ import annotations

import asyncio
import json
import datetime as dt
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config, Limits
from ..filters import leg_flags, leg_ok
from ..links import build_links
from ..models import Offer, SearchTask, utcnow_iso
from .gf_parse import ParsedOption, booking_details, parse_option, price_level
from .tfs import google_search_url

log = logging.getLogger(__name__)

NO_RESULTS = re.compile(r"no (?:results|flights) (?:returned|found|match)|we couldn.t find any flights", re.I)
GOOGLE_ERROR = re.compile(r"something went wrong", re.I)


class TransientError(Exception):
    """Google showed 'Oops, something went wrong'."""
# the main row element; each result also has shorter "Leaves …" labels (times, details button)
ROW_SELECTOR = '[aria-label*="Leaves "][aria-label*="Total duration"]'


class Blocked(Exception):
    """Google answered with a captcha / unusual traffic page."""


@dataclass
class SearchOutcome:
    task: SearchTask
    status: str                     # ok | no_results | error | blocked | skipped
    offers: list[Offer] = field(default_factory=list)
    message: str = ""
    seconds: float = 0.0
    counts: dict = field(default_factory=dict)


def choose(options: list[ParsedOption], broad: Limits, strict: Limits, k: int) -> list[ParsedOption]:
    """Cheapest option within the broad limits, the cheapest within the strict
    limits and – while k allows – cheaper alternatives on other airlines."""
    valid = sorted(
        (o for o in options if o.price_total and leg_ok(o.leg, broad)),
        key=lambda o: (o.price_total, o.leg.duration_min or 9999),
    )
    picks: list[ParsedOption] = []
    if valid:
        picks.append(valid[0])
    strict_ok = [o for o in valid if leg_ok(o.leg, strict)]
    if strict_ok and strict_ok[0] is not valid[0]:
        picks.append(strict_ok[0])
    for o in valid:
        if len(picks) >= k:
            break
        seen = {a for p in picks for a in p.leg.airlines}
        if o not in picks and set(o.leg.airlines) - seen:
            picks.append(o)
    return picks[:k]


class GoogleFlightsSource:
    name = "google_flights"

    def __init__(self, cfg: Config, *, debug_dir: Path | None = None, headless: bool = True,
                 tfs_max_stops: int | None = 1, diagnose: bool = False):
        self.cfg = cfg
        self.diagnose = diagnose
        self.debug_dir = debug_dir
        self.headless = headless
        self.tfs_max_stops = tfs_max_stops
        self._debug_saved = 0
        self._blocked = False

    # ── public ──────────────────────────────────────────────────────────────
    async def run(self, tasks: list[SearchTask], deadline: float) -> list[SearchOutcome]:
        from playwright.async_api import async_playwright

        results: list[SearchOutcome] = []
        queue: asyncio.Queue[SearchTask] = asyncio.Queue()
        for t in tasks:
            queue.put_nowait(t)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            probe = await browser.new_page()
            ua = (await probe.evaluate("navigator.userAgent")).replace("HeadlessChrome", "Chrome")
            await probe.close()
            context = await browser.new_context(
                locale="en-US", timezone_id="Europe/Berlin", user_agent=ua,
                viewport={"width": 1400, "height": 1000},
            )
            context.set_default_timeout(30_000)
            n = min(self.cfg.scrape.concurrency, max(1, len(tasks)))
            workers = [asyncio.create_task(self._worker(i, context, queue, results, deadline)) for i in range(n)]
            await asyncio.gather(*workers)
            await context.close()
            await browser.close()

        while not queue.empty():
            t = queue.get_nowait()
            results.append(SearchOutcome(t, "blocked" if self._blocked else "skipped",
                                         message="not searched (time budget or block)"))
        return results

    # ── worker ──────────────────────────────────────────────────────────────
    async def _worker(self, wid, context, queue, results, deadline):
        page = await context.new_page()
        await asyncio.sleep(wid * 2.5)  # stagger start
        while not queue.empty():
            if self._blocked or time.monotonic() > deadline:
                break
            task = queue.get_nowait()
            t0 = time.monotonic()
            try:
                for attempt in range(3):
                    try:
                        out = await self._search(page, task)
                        break
                    except TransientError:
                        if attempt == 2:
                            raise
                        log.info("%s: Google error page, retrying", task.key)
                        await asyncio.sleep(5 + attempt * 10)
            except Blocked as e:
                self._blocked = True
                out = SearchOutcome(task, "blocked", message=str(e))
                log.error("Google blocked the scraper: %s", e)
            except Exception as e:  # noqa: BLE001 – one failed search must not stop the run
                log.warning("search %s failed: %s", task.key, e)
                out = SearchOutcome(task, "error", message=f"{type(e).__name__}: {e}"[:300])
                await self._save_debug(page, task, "error")
            out.seconds = round(time.monotonic() - t0, 1)
            results.append(out)
            log.info("%-26s %-10s %5.1fs offers=%d %s", task.key, out.status, out.seconds,
                     len(out.offers), out.counts)
            await asyncio.sleep(random.uniform(1.5, 4.0))
        await page.close()

    # ── one search ──────────────────────────────────────────────────────────
    async def _search(self, page, task: SearchTask) -> SearchOutcome:
        cfg = self.cfg
        dest_airports = list(cfg.destinations[task.dest].airports)
        url = google_search_url(
            origin=list(cfg.origin), dest=dest_airports, depart=task.depart, ret=task.ret,
            adults=cfg.adults, seat=cfg.cabin, max_stops=self.tfs_max_stops, currency=cfg.currency,
            hl=cfg.scrape.google_hl, gl=cfg.scrape.google_gl,
        )
        await self._open(page, url)
        outs = await self._wait_options(page, task.depart, set(cfg.origin), expand=True)
        counts = {"out": len(outs or [])}
        if not outs:
            await self._save_debug(page, task, "noresults")
            return SearchOutcome(task, "no_results", counts=counts)

        if self.diagnose:
            await self._dump_rows(page, "outbound")
        level = price_level(await page.inner_text("body"))
        cands = choose(outs, cfg.constraints, cfg.filters, cfg.scrape.outbound_candidates)
        counts["out_ok"] = len(cands)
        if not cands:
            return SearchOutcome(task, "no_results", message="no outbound within limits", counts=counts)

        offers: list[Offer] = []
        for i, cand in enumerate(cands):
            if i > 0:
                await self._open(page, url)
                await self._wait_options(page, task.depart, set(cfg.origin), expand=True)
            rets = await self._select(page, cand.aria, task.ret, set(dest_airports))
            counts[f"ret{i}"] = len(rets or [])
            if self.diagnose and i == 0:
                await self._dump_rows(page, "return")
            chosen = choose(rets or [], cfg.constraints, cfg.filters, 2)
            if not chosen:
                if not rets:
                    await self._save_debug(page, task, "noreturns")
                offers.append(self._offer(task, cand, None, cand.price_total, level, url))
                continue
            built = [self._offer(task, cand, r, r.price_total, level, url) for r in chosen]
            if i == 0 and cfg.scrape.fetch_booking_link:
                booking, details = await self._open_booking(page, chosen[0].aria)
                if booking:
                    built[0].links.insert(0, {"kind": "google_booking", "label": "Book (Google Flights)",
                                              "url": booking})
                    built[0].booking_vendors = details.get("vendors", [])
                    built[0].bag_google = details.get("bag")
                    for b in built:
                        b.typical_range = details.get("typical")
            offers.extend(built)
        return SearchOutcome(task, "ok", offers=offers, counts=counts)

    def _offer(self, task, out: ParsedOption, ret: ParsedOption | None, price, level, search_url) -> Offer:
        cfg = self.cfg
        names = list(dict.fromkeys(out.leg.airlines + (ret.leg.airlines if ret else [])))
        flags = leg_flags(out.leg, "out") + leg_flags(ret.leg if ret else None, "in")
        links = build_links(
            origin=cfg.origin[0], dest_city=task.dest, dest_airport=out.leg.arr_airport
            if re.fullmatch(r"[A-Z]{3}", out.leg.arr_airport or "") else None,
            depart=task.depart.isoformat(), ret=task.ret.isoformat(), adults=cfg.adults,
            currency=cfg.currency, dest_airports=list(cfg.destinations[task.dest].airports),
            airline_names=names, hl=cfg.scrape.google_hl, gl=cfg.scrape.google_gl,
        )
        return Offer(
            source=self.name, dest=task.dest, window=task.window,
            depart=task.depart.isoformat(), ret=task.ret.isoformat(),
            price_total=float(price), adults=cfg.adults, currency=cfg.currency,
            outbound=out.leg, inbound=ret.leg if ret else None, airlines=names,
            links=links, flags=flags, found_at=utcnow_iso(), price_level=level,
        )

    # ── page helpers ────────────────────────────────────────────────────────
    async def _open(self, page, url: str):
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        if "consent.google" in page.url:
            await self._consent(page)
        self._check_blocked(page.url, await page.title())

    async def _consent(self, page):
        for label in (r"Reject all", r"Alle ablehnen", r"Accept all", r"Alle akzeptieren"):
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            if await btn.count():
                await btn.first.click()
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1.5)
                return
        raise RuntimeError("consent page without known buttons")

    def _check_blocked(self, url: str, title: str):
        if "/sorry/" in url or "unusual traffic" in (title or "").lower():
            raise Blocked(url)

    async def _rows(self, page) -> list[dict]:
        return await page.eval_on_selector_all(
            ROW_SELECTOR,
            """els => els.map((el, i) => {
                const r = el.getBoundingClientRect();
                const vis = r.width > 0 && r.height > 0 && (el.checkVisibility ? el.checkVisibility() : !!el.offsetParent);
                const li = el.closest('li') || el.parentElement || el;
                return {i, vis, aria: el.getAttribute('aria-label') || '', text: vis ? (li.innerText || '') : ''};
            }).filter(r => r.vis)""",
        )

    async def _wait_options(self, page, ref_date: dt.date, dep_codes: set[str], *, expand=False,
                            timeout: float = 30) -> list[ParsedOption] | None:
        """Wait until the result list has settled; return rows departing from dep_codes."""
        end = time.monotonic() + timeout
        last, stable, expanded = -1, 0, not expand
        parsed: list[ParsedOption] = []
        while time.monotonic() < end:
            await asyncio.sleep(0.7)
            rows = await self._rows(page)
            parsed = []
            for r in rows:
                p = parse_option(r["aria"], r["text"], ref_date)
                if p and p.leg.dep_airport in dep_codes:
                    parsed.append(p)
            n = len(parsed)
            if n == 0:
                if await self._visible(page, GOOGLE_ERROR):
                    raise TransientError("Google: something went wrong")
                if time.monotonic() > end - timeout + 6 and await self._visible(page, NO_RESULTS):
                    return []
                self._check_blocked(page.url, await page.title())
            stable = stable + 1 if n == last and n > 0 else 0
            last = n
            if stable >= 2:
                if not expanded:
                    expanded = True
                    if await self._expand(page):
                        stable = 0
                        continue
                return parsed
        return parsed or None

    async def _visible(self, page, pattern: re.Pattern) -> bool:
        loc = page.get_by_text(pattern)
        try:
            for i in range(min(await loc.count(), 4)):
                if await loc.nth(i).is_visible():
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    async def _expand(self, page) -> bool:
        btn = page.get_by_role("button", name=re.compile(r"more flights", re.I))
        try:
            if await btn.count():
                await btn.first.click(timeout=5_000)
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    _VISIBLE_JS = """(e) => { const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && (e.checkVisibility ? e.checkVisibility() : !!e.offsetParent); }"""

    async def _click(self, page, aria: str, method: str = "dom") -> bool:
        """Click the visible result row with this aria-label."""
        idx = await page.eval_on_selector_all(
            ROW_SELECTOR,
            f"(els, a) => {{ const vis = {self._VISIBLE_JS}; "
            "return els.findIndex(e => e.getAttribute('aria-label') === a && vis(e)); }",
            aria,
        )
        if idx is None or idx < 0:
            if self.diagnose:
                print(f"--- DIAG click: aria not found ({aria[:120]!r})")
            return False
        loc = page.locator(ROW_SELECTOR).nth(idx)
        try:
            if method == "dom":
                await loc.evaluate("e => { e.scrollIntoView({block: 'center'}); e.click(); }")
            elif method == "force":
                await loc.scroll_into_view_if_needed(timeout=5_000)
                await loc.click(force=True, timeout=5_000)
            else:
                await loc.focus(timeout=5_000)
                await page.keyboard.press("Enter")
            return True
        except Exception as e:  # noqa: BLE001
            if self.diagnose:
                print(f"--- DIAG click ({method}) failed: {e}"[:900])
            return False

    async def _select(self, page, aria: str, ref_date: dt.date, dep_codes: set[str]) -> list[ParsedOption] | None:
        """Select an outbound flight and return the parsed return options (None if it did not work)."""
        for method in ("dom", "force", "enter"):
            before = page.url
            if not await self._click(page, aria, method):
                continue
            rets = await self._wait_options(page, ref_date, dep_codes, expand=True, timeout=15)
            if rets:
                return rets
            if self.diagnose:
                print(f"--- DIAG select via {method}: no return rows (url changed: {page.url != before})")
        return None

    async def _open_booking(self, page, aria: str) -> tuple[str | None, dict]:
        for method in ("dom", "force"):
            if not await self._click(page, aria, method):
                continue
            end = time.monotonic() + 12  # wait for navigation
            while time.monotonic() < end:
                await asyncio.sleep(0.7)
                if "/booking" in page.url:
                    body, details = "", {"vendors": []}
                    wait_end = time.monotonic() + 12
                    while time.monotonic() < wait_end and not details["vendors"]:
                        await asyncio.sleep(1.0)
                        try:
                            body = await page.inner_text("body")
                            details = booking_details(body)
                        except Exception:  # noqa: BLE001
                            pass
                    if self.diagnose:
                        print(f"--- DIAG booking url={page.url[:200]}")
                        print("    BOOKING", json.dumps(details, ensure_ascii=False))
                    return page.url, details
        if self.diagnose:
            print(f"--- DIAG booking: no /booking url, now {page.url}")
            await self._dump(page, "booking")
        return None, {}

    async def _dump(self, page, kind: str):
        """Print what the page looks like (used by the smoke test, artifacts may be unreachable)."""
        try:
            body = await page.inner_text("body")
            m = NO_RESULTS.search(body)
            info = await page.evaluate("""() => {
                const all = [...document.querySelectorAll('[aria-label]')].map(e => e.tagName + ': ' + e.getAttribute('aria-label'));
                return {lis: document.querySelectorAll('li').length, aria: all.length,
                        sample: all.filter(a => a.length > 40).slice(0, 25).map(a => a.slice(0, 400))};
            }""")
            print(f"--- DIAG {kind}: url={page.url}\n    title={await page.title()!r}\n"
                  f"    no_results_match={m.group(0) if m else None!r} li={info['lis']} aria={info['aria']}")
            for a in info["sample"]:
                print("    ARIA", a)
            txt = re.sub(r"\n\s*\n+", "\n", body)[:3500]
            print("    BODY", txt.replace("\n", " | "))
        except Exception as e:  # noqa: BLE001
            print("--- DIAG failed", e)

    async def _dump_rows(self, page, kind: str):
        rows = await self._rows(page)
        uniq = len({r["aria"] for r in rows})
        print(f"--- DIAG rows {kind}: {len(rows)} rows, {uniq} unique aria labels, url={page.url[:160]}")
        shown = 0
        for r in rows:
            if shown >= 4:
                break
            shown += 1
            print("    ROW ARIA", r["aria"][:700])
            print("    ROW TEXT", r["text"][:400].replace("\n", " | "))
        odd = [r for r in rows if "Total duration" not in r["aria"]][:4]
        for r in odd:
            print("    ODD ARIA", r["aria"][:700])
            print("    ODD TEXT", r["text"][:300].replace("\n", " | "))
        html = await page.evaluate("""() => {
            const el = document.querySelector('[aria-label*="Leaves "]');
            if (!el) return '';
            const li = el.closest('li') || el;
            return li.outerHTML.slice(0, 2500);
        }""")
        print("    FIRST ROW HTML", re.sub(r"\s+", " ", html))

    async def _save_debug(self, page, task: SearchTask, kind: str):
        if self.diagnose:
            await self._dump(page, kind)
        if not self.debug_dir or self._debug_saved >= 6:
            return
        self._debug_saved += 1
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            stem = self.debug_dir / f"{kind}-{task.dest}-{task.depart}-{task.ret}"
            await page.screenshot(path=f"{stem}.png", full_page=True)
            Path(f"{stem}.html").write_text(await page.content(), encoding="utf-8")
            Path(f"{stem}.url.txt").write_text(page.url, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.debug("debug save failed: %s", e)
