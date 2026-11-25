# ✈ Japan Flight Watch

> **Paused (flight booked, 25 Nov 2026).** The "Scrape flights" workflow is disabled, and
> notifications are turned off (`"notifications" => false` in `.github/workflows/deploy.yml`).
> To resume: Actions → Scrape flights → *Enable workflow*, and set `notifications` back to
> `true`, then let the deploy run.

A small PWA that watches **one-stop round trips Berlin (BER) → Tokyo for 2 people**
and shows the cheapest options, a price calendar, price history and booking links. You get
**one push notification a day at a time you choose**, plus an instant alert when a price drops
below your limit.

```
GitHub Actions (free)                 all-inkl (your web space)
┌────────────────────────┐   FTPS    ┌──────────────────────────────┐
│ scraper (Python)       │ ───────▶  │ PWA  (index.html, js, css)   │  ◀── your phones
│  · Google Flights      │ data/*.json│ data/latest.json, history   │
│  · Aviasales (opt.)    │           │ api/*.php  → Web Push         │ ──▶ notifications
│ nightly + midday       │ ──curl──▶ │ api/cron.php (every 15 min)   │
│ history → `data` branch│           └──────────────────────────────┘
└────────────────────────┘
```

## What it searches

Configured in [`config.toml`](config.toml), edited directly on GitHub:

| | |
|---|---|
| Route | BER → Tokyo (HND, NRT), round trip, 2 adults, economy. More cities: `[destinations.*]` in `config.toml` |
| **May 2027** | departures 7–20 May (Golden Week ends 5 May), 10/11/12 nights, back by 1 June |
| **Wildcards** | rotating sample across 2027 (holiday peaks excluded); shown only when ≥ 15 % cheaper than the best window price |
| Limits while scraping | max. 1 stop, layover ≤ 6 h, ≤ 24 h per direction, no "self-transfer" tickets |
| Default app filters | layover ≤ 4 h, travel time ≤ 19 h (both adjustable in the app) |

> **Travel time:** Russian airspace is closed to EU airlines, so a one-stop Berlin → Japan
> trip takes about **16–19 h**, and the way back is usually longer. A 14–16 h cap removes nearly
> every option. That is why the app's default is 19 h; the filter goes down to 16 h.

Every night (~03:17 Berlin) all ~160 date/destination combinations are searched. At midday
the cheapest ~24 are checked again, this time also opening Google's booking page (bag fee,
sellers). One search, including the return flights, takes 20–40 s. With 2 parallel tabs a
full run takes about 35–45 minutes. If Google slows down, the run stops after 45 minutes and
the next run continues with the dates that were checked longest ago.

### Where the prices come from

* **Google Flights** (main source, no account needed): the scraper opens Google Flights in a
  headless browser and reads the result list. Google Flights already contains the fares of
  Finnair, Lufthansa/SWISS/Austrian, Turkish, ANA, JAL, LOT, Qatar, Emirates, Air China,
  KLM/Air France and others, plus the travel agencies selling them. Each result has the exact
  flights, layovers, total for 2 people and a link that goes straight to Google's booking page
  for that itinerary ("Book with Finnair …").
* **Aviasales / Travelpayouts** (optional, free token): cached prices other travellers found
  in the last days, for whole months at once. Useful for spotting cheap dates.

Why not scrape finnair.com, lufthansa.com and the other airline sites directly? Their booking
engines use heavy bot protection, their terms forbid automated searching, and a scraper for
each one would break every few weeks. Google Flights shows the same airline fares and links
to the airlines' own booking pages.

### Checked bags

Google shows the **cheapest** fare. For Lufthansa group, Finnair, KLM/Air France and BA that is
usually a "Light" fare **without** a checked bag. ANA, JAL, Turkish, LOT, Gulf and Chinese
airlines include one.

* In the midday run (the cheapest ~24 dates) the scraper opens Google's booking page and takes
  the **real price of the first checked bag** from it (e.g. "€0–90 per passenger", marked
  "Google"). The same page also lists who sells the ticket (airline ✈ or agency, with prices)
  and Google's usual price range for similar trips.
* For all other offers the airline's usual policy is used, plus an estimate of 75 € per bag and
  direction (set in `config.toml`).

The app ranks by **fare + bag cost** by default. You can switch to "Fare only" and
"Bag included only" in the filters.

## Setup (one time, ~30 minutes)

### 1. GitHub repository

The repository is **`WBRE-BERLIN/Japanflightapp`** and is **public**. Clone it with your
normal `wbre-berlin` login; you don't need a separate account, SSH host alias or
`gh auth switch`:

```
git clone git@github.com:WBRE-BERLIN/Japanflightapp.git
```

Because the repository is public, **GitHub Actions minutes on the standard runners are free and
unlimited**. The scraper (≈35 min each night plus a midday re-check) does not count against
any quota, so there is no need to cut `refresh_top_n` or the midday schedule to save minutes.

Public means anyone can read the code and the `data` branch (price history). The
**repository secrets** (FTP login, `CRON_KEY`, tokens) stay hidden: they are never shown in
the code or in Actions logs, and workflows started from forks cannot read them.

### 2. all-inkl: subdomain + FTP user

In KAS (kas.all-inkl.com):

1. **Domain → Subdomain anlegen**, e.g. `flights.your-domain.de`, with its own folder, e.g.
   `/flights/`. Turn on **SSL (Let's Encrypt)** and **"SSL erzwingen"** (force HTTPS).
   Push notifications only work over HTTPS.
2. **FTP → Neuer FTP-Zugang**: restrict it to the folder `/flights/`. This user cannot
   see or overwrite your other websites.
3. PHP version for the subdomain: **8.1 or newer** (default is fine).
4. Optional: **Verzeichnisschutz** (password protection) on the folder if you don't want the
   page to be public. It contains no personal data. It sends `noindex`, so it won't appear in
   search engines.

### 3. GitHub secrets

Repository → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Example | Required |
|---|---|---|
| `FTP_HOST` | `w0123456.kasserver.com` | yes |
| `FTP_USER` | `f0123456` (the restricted FTP user) | yes |
| `FTP_PASSWORD` | … | yes |
| `FTP_DIR` | `/` (the restricted user's root is already the subdomain folder) | yes |
| `SITE_URL` | `https://flights.your-domain.de` | yes |
| `CRON_KEY` | a long random string, e.g. from `openssl rand -hex 24` | yes |
| `APP_TOKEN` | a short code you both type once when enabling notifications | optional |
| `VAPID_SUBJECT` | `mailto:you@example.com` (contact for push services) | optional |
| `TRAVELPAYOUTS_TOKEN` | free API token from travelpayouts.com → Tools → API | optional |
| `TRAVELPAYOUTS_MARKER` | your Travelpayouts partner ID (affiliate marker for links) | optional |

### 4. Deploy and first scrape

1. **Actions → Deploy web app → Run workflow** uploads the app (it also runs on every push to
   `main` that changes `web/`).
2. **Actions → Scrape flights → Run workflow**, mode `full`, creates the first data.
   Try `limit: 6` first to check that everything works in ~2 minutes.
3. Open `https://flights.your-domain.de` on both phones and **add it to the home screen**.

### 5. Daily notification

1. all-inkl KAS → **Tools → Cronjobs → Neuer Cronjob**: URL
   `https://flights.your-domain.de/api/cron.php?key=YOUR_CRON_KEY`, every **15 minutes**.
   Cron jobs are included from the PrivatPlus plan. Without them, use the free service
   [cron-job.org](https://cron-job.org) with the same URL, or enable the schedule in
   `.github/workflows/notify-fallback.yml`.
2. In the app: 🔔 → choose the time (e.g. 08:00), optionally an alert limit (e.g. 1,800 € for
   two), → **Enable notifications** → **Send test**.
   * **iPhone:** works from iOS 16.4, and only when opened from the home-screen icon.
   * Every device registers separately, so do this on both phones.

The daily message looks like this:

```
✈ Japan for 2: from €1,353 (€677 pp) ↓€64
May 2027 €1,353 · Air China · 11–21 May · Tokyo
Wildcard €1,232 · LOT · 14–25 Feb · Tokyo
```

## Using the app

* **Cards** – best price per window, 7-day trend.
* **Price calendar** – departure day × nights, darker = cheaper. Tap a day to list its flights.
* **Flight cards** – times, stop + layover, total duration, bag status, price trend for these
  exact dates ("↓ €56 since last check", "Lowest seen"), and links: **Book** (Google's booking
  page for exactly these flights: airline direct, booking.com and other agencies, each with the
  flights pre-filled), Google Flights search, Skyscanner, KAYAK. On iPhone the links open in the
  Chrome app (saved cards and logins); switch this off in Settings if you use Safari.
* **Filters** – window, destination, max. layover, max. travel time, bag included only,
  exclude airlines, total vs. per person, fare vs. fare + bag estimate.
* **Price history** – daily cheapest per window (also as a table).
* **Scraper status** – recent runs with success and error counts.

## Repository layout

```
config.toml                 what to search – edit this
scraper/flightscan/         Python scraper (Playwright)
  sources/google_flights.py   browser automation
  sources/gf_parse.py         parses Google's result rows
  sources/tfs.py              builds Google Flights search URLs
  sources/travelpayouts.py    optional Aviasales API
  airlines.py                 names, websites, bag policies
  runner.py / store.py / report.py
web/                        the PWA (static) + api/ (PHP, Web Push without dependencies)
tests/push_e2e.py           end-to-end test of the push backend
.github/workflows/          ci, scrape, deploy, smoke test, notification fallback
```

Price history lives on the **`data` branch** (`state.json`, `history.json`, `latest.json`).
The scraper commits to it after each run, so the history is kept and versioned for free.

### Local development

```bash
cd scraper
pip install -r requirements.txt -r requirements-dev.txt && python -m playwright install chromium
python -m pytest -q                         # unit tests
python -m flightscan demo --out ../web/data # fake data for the UI
python -m flightscan smoke                  # 2 live searches, prints what was parsed
python -m flightscan run --data ../store --mode full --limit 5
cd .. && php -S localhost:8000 -t web       # open http://localhost:8000
```

## Limits and troubleshooting

* **Google changes its page** → searches end up as "error" or "no flights" in the status table.
  The `Scraper smoke test` workflow shows what the parser reads, and failed searches leave
  screenshots as workflow artifacts. The parsing is in `sources/gf_parse.py`.
* **Rate limiting** → the scraper stops as soon as Google shows a captcha. If Google keeps
  answering with "something went wrong" pages (5 failed searches in a row), it pauses 4 minutes
  once, and if that does not help it ends the run. The dates it did not reach come first in the
  next run. Keep `concurrency` at 2.
* **Dates far ahead**: Google Flights can only search trips whose return is at most 330 days
  ahead (later dates fail with "something went wrong"). The scraper skips those dates
  (`max_days_ahead` in `config.toml`) and picks them up as soon as they come within reach.
* Prices are snapshots. Always confirm the fare, the baggage allowance and the conditions
  before booking.
