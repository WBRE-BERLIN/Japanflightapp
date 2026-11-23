# ✈ Japan Flight Watch

A small PWA that watches **one-stop round trips Berlin (BER) → Tokyo / Osaka for 2 people**
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
| Route | BER → Tokyo (HND, NRT) and Osaka (KIX), round trip, 2 adults, economy |
| **May 2027** | departures 7–20 May (Golden Week ends 5 May), 10/11/12 nights, back by 1 June |
| **October 2027** | departures 10–21 Oct ("last three weeks"), 10/11/12 nights, back by 1 Nov |
| **Wildcards** | rotating sample across 2027 (holiday peaks excluded); shown only when ≥ 15 % cheaper than the best window price |
| Limits while scraping | max. 1 stop, layover ≤ 6 h, ≤ 24 h per direction, no "self-transfer" tickets |
| Default app filters | layover ≤ 4 h, travel time ≤ 19 h (both adjustable in the app) |

> **Travel time:** Russian airspace is closed to EU airlines, so a one-stop Berlin → Japan
> trip takes about **16–19 h**, and the way back is usually longer. A 14–16 h cap removes nearly
> every option. That is why the app's default is 19 h; the filter goes down to 16 h.

Every night (~03:17 Berlin) all ~160 date/destination combinations are searched. At midday
the cheapest ~24 are checked again. With all three windows in your config, a full run takes
about 15–25 minutes.

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
airlines include one. The app marks every offer (🧳 included / extra / check) and, by default,
ranks by **fare + bag estimate** (75 € per bag and direction, set in `config.toml`). You can
switch to "Fare only" and "Bag included only" in the filters.

## Setup (one time, ~30 minutes)

### 1. GitHub account and repository

This repo lives in the separate account **`cladewig`**, so nothing here touches
`wbre-berlin`. All secrets, Actions minutes and the `data` branch belong to this repository
only.

If you work with both accounts on one computer:

* **GitHub Desktop / browser:** sign in with `cladewig` only for this repository.
* **git with SSH:** add a second key and a host alias in `~/.ssh/config`:
  ```
  Host github-cladewig
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_cladewig
  ```
  then `git clone git@github-cladewig:cladewig/Japanflightapp.git` and inside the clone
  `git config user.name "…"` / `git config user.email "…"`, so commits here use the
  `cladewig` identity. Your other projects keep using `wbre-berlin` unchanged.
* **gh CLI:** `gh auth login` with both accounts, then `gh auth switch` when needed.

The repository is private. Private repos include **2,000 free Actions minutes per month**.
This scraper uses about 600–900, so it fits. If you make the repo public, minutes are unlimited.

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
October 2027 €1,505 +bags · Finnair · 18–28 Oct · Osaka
```

## Using the app

* **Cards** – best price per window, Tokyo vs. Osaka, 7-day trend.
* **Price calendar** – departure day × nights, darker = cheaper. Tap a day to list its flights.
* **Flight cards** – times, stop + layover, total duration, bag status, price trend for these
  exact dates ("↓ €56 since last check", "Lowest seen"), and links: **Book** (Google's booking
  page for exactly these flights), Google Flights search, the airline's site, Skyscanner, KAYAK.
* **Filters** – window, Tokyo/Osaka, max. layover, max. travel time, bag included only,
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
* **Rate limiting** → the scraper stops as soon as Google shows a captcha. It retries at the
  next scheduled run. Keep `concurrency` at 2–3.
* **October 2027** fares appear only once airlines open their schedules, roughly 11 months
  ahead. Until then those calendar cells stay empty.
* Prices are snapshots. Always confirm the fare, the baggage allowance and the conditions
  before booking.
