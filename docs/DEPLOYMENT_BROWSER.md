# Deploying the browser automation (Delaware liens) for free

The app can read **Delaware County civil lien cases** automatically by driving the county's public C-Track portal
(`delcopublicaccess.co.delaware.pa.us`) with a real browser (Playwright + Chromium). The portal's only barrier is a
plain **"CONTINUE AS PUBLIC USER"** click — no login and no CAPTCHA — so the app opens it the way a person's browser
does and runs a Party Search for each owner name, keeping only cases whose classification contains "Lien".

> **What this is not.** Browser automation is never pointed at a CAPTCHA, a Cloudflare "verify you are human"
> challenge, or a login you don't own. It is off by default and only used for public, no-challenge portals. See
> [SOURCE_ACCESS.md](SOURCE_ACCESS.md).

Chromium needs roughly **1 GB of RAM and ~700 MB of disk** — more than a Render free instance (512 MB) can give it.
So the pattern is: **host the site anywhere (including Render free), and run the browser work on a box that can fit
Chromium.** Because the worker runs *in the same process as the API*, "a box that can fit Chromium" just means running
the app there with `USI_BROWSER_AUTOMATION=true`. Three free ways to do that, easiest first.

---

## The switch

Everywhere below, browser automation turns on with one environment variable and the Chromium install:

```bash
pip install -r backend/requirements.txt -r backend/requirements-browser.txt
python -m playwright install --with-deps chromium
export USI_BROWSER_AUTOMATION=true      # Windows PowerShell: $env:USI_BROWSER_AUTOMATION="true"
```

When it is **off** (the default, and on Render free), Delaware liens simply fall back to a user-assisted capture task
— nothing breaks, the boss just sees a "search C-Track" to-do instead of the liens filled in automatically.

Optional tuning: `USI_BROWSER_HEADLESS=true` (default), `USI_BROWSER_TIMEOUT_SECONDS=45`.

---

## Option 1 — Your own PC (quickest; on-demand batches)

Best when you want to process a list of 900+ properties on demand and don't need the browser running 24/7. Turn your
PC on, run a batch, done.

```bash
# from the repo root, one time:
cd backend
python -m venv .venv && .venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-browser.txt
python -m playwright install --with-deps chromium

# each time you want to run the app with liens automated:
set USI_BROWSER_AUTOMATION=true                       # PowerShell: $env:USI_BROWSER_AUTOMATION="true"
set USI_AUTO_ACCEPT_TERMS=true
python scripts/serve.py
```

Then open `http://localhost:8000`, upload a Delaware sale list, and click **Autopilot** (or upload with autopilot).
The in-process worker commits the import and enriches every property, driving C-Track for the Delaware liens as it
goes. You can watch progress on the project's **Jobs** page.

- Truly free, nothing to sign up for.
- Only runs while your PC is on — fine for batch runs, not for the daily auto-refresh.
- Uses the local SQLite database under `backend`/`USI_DATA_DIR`.

## Option 2 — Oracle Cloud Always Free VM (recommended for 24/7)

Oracle Cloud's **Always Free** tier includes small VMs (an AMD `VM.Standard.E2.1.Micro`, or an Arm Ampere shape with
up to 4 OCPU / 24 GB) that run indefinitely at no cost — enough to host the whole app *and* Chromium and keep the
daily refresh running. This gives the boss one always-on URL with Delaware liens automated.

1. Create an Always Free VM (Ubuntu 22.04) and open port 8000 (or 80/443 behind a reverse proxy) in its security list.
2. Install Docker, clone the repo, and build the **browser image**:
   ```bash
   sudo apt-get update && sudo apt-get install -y docker.io git
   git clone https://github.com/Aradevski170802/ReaLTOR.git && cd ReaLTOR
   sudo docker build -f Dockerfile.browser -t usi-browser .
   sudo docker run -d --restart unless-stopped -p 8000:8000 \
     -e USI_MASTER_KEY="$(openssl rand -base64 48)" \
     -e USI_SITE_PASSWORD="choose-a-strong-password" \
     -e USI_AUTO_ACCEPT_TERMS=true \
     -e USI_SEED_DEMO=true \
     -v usi-data:/app/data \
     usi-browser
   ```
   `Dockerfile.browser` is based on Microsoft's official Playwright image, so Chromium and its OS libraries are already
   inside and `USI_BROWSER_AUTOMATION=true` is set for you. The `-v usi-data:/app/data` volume keeps the database
   across restarts.
3. Point a free hostname at the VM (e.g. a DuckDNS subdomain) and, ideally, put Caddy or nginx in front for HTTPS.

- Always on, free, runs the daily refresh — the closest thing to a "real" deployment at no cost.
- You manage the VM (updates, backups of the `usi-data` volume).

## Option 3 — GitHub Actions scheduled batch (advanced; no server)

If you'd rather not run a VM, a scheduled GitHub Actions job can install Playwright and enrich pending properties on a
cron, writing to a shared managed database (e.g. a free Neon or Supabase Postgres) that the Render-hosted site also
reads. Sketch of `.github/workflows/enrich.yml`:

```yaml
on:
  schedule: [{ cron: "0 7 * * *" }]   # daily; also runnable on demand
  workflow_dispatch:
jobs:
  enrich:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r backend/requirements.txt -r backend/requirements-browser.txt
      - run: python -m playwright install --with-deps chromium
      - env:
          USI_DATABASE_URL: ${{ secrets.USI_DATABASE_URL }}   # shared Postgres, same DB the site uses
          USI_MASTER_KEY: ${{ secrets.USI_MASTER_KEY }}
          USI_BROWSER_AUTOMATION: "true"
        run: python scripts/enrich_pending.py                 # a small batch script (not included yet)
```

- Free minutes: unlimited for public repos, 2,000 min/month for private ones.
- Not continuous (max 6 h per job) and requires a **shared** database so the site and the job see the same data —
  Render's free SQLite is per-container and can't be shared, so this route needs managed Postgres.
- Needs a small `enrich_pending.py` entrypoint; ask and it can be added.

---

## Verifying it works

After enabling browser automation, enrich a Delaware property whose owner you expect to have municipal liens and check
the **Jobs** log / the property's Liens section. A quick standalone smoke test:

```bash
cd backend
USI_BROWSER_AUTOMATION=true .venv/bin/python -c "from app.connectors.browser import browser_available; print('browser available:', browser_available())"
```

It should print `browser available: True`. If it prints `False`, either the env var isn't set or Chromium isn't
installed (`python -m playwright install --with-deps chromium`).

---

## Honest status of automation

| Data | Delaware County | Montgomery County |
|---|---|---|
| Parcel / assessment / GIS | Automated (ArcGIS API + iasWorld portal) | Automated (ArcGIS API) |
| Delinquent taxes | Automated (iasWorld portal) | Automated (Tax Claim Bureau, after terms ack) |
| Market valuation | Automated (keyless local estimate; AVM APIs if keys added) | Automated (same) |
| **Civil liens** | **Automated** via C-Track browser automation (this doc) | **No automated path** — the court portal (PSI) is behind a Cloudflare "verify you are human" challenge with no bulk/API data, which is never bypassed. Stays a user-assisted capture task. |
| Mortgages (recorder) | User-assisted — the recorder dropped guest access and now requires an account; automation would need your own credentials. | User-assisted — Recorder of Deeds requires your own account. |

So: **Delaware liens are the piece this browser automation makes hands-free.** Montgomery liens and both counties'
recorder mortgages remain user-assisted, honestly, because the only routes to them are gated by a login you own or by a
human-verification challenge the app will not defeat.
