# Product walkthrough

Start the app with `python dev.py` (add `--seed` for the demo projects) and open http://127.0.0.1:5173.

## 1. Create a project

**Projects → New research project**: enter a name and choose **Montgomery County, Pennsylvania** or **Delaware County, Pennsylvania**. The Delaware option notes that it is the Pennsylvania county, not the State of Delaware.

## 2. Import and review the sale list (tab "1 · Import & review")

1. Choose the county PDF (for example `UpsetSaleList_8-28-2026.PDF`) and click **Upload & extract**. A background job detects each page type (text, scanned, notice, empty) and runs the county parser. The Delco report yields 1,742 records; the Montco list yields 484 rows.
2. The summary shows:
   - parser and page kinds
   - report metadata (report date, list "as of" date, sale date)
   - warnings, such as gaps in the record sequence
3. **Needs review** lists rows flagged with a reason, such as a malformed ZIP or an OCR-read value. Click a row to open it:
   - the rendered PDF page and the original text
   - every extracted field, with its as-printed value and confidence
4. Fix any field and **Save corrections**. Corrected values are marked. Re-extracting or re-committing later asks before overwriting them.
5. **Exclude** rows that should not become properties, then click **Commit rows to project**.

CSV or XLSX lists work the same way. Headers are mapped by synonym ("Folio-NBR", "Parcel", "Owner", "Site Location", …).

## 3. Choose the pilot and enrich (tab "2 · Pilot & enrichment")

1. Click **Auto-select first 8**, or tick 7–8 properties and choose **Use checked**.
2. Review the source list:
   - **Automated** sources run straight away.
   - **User-assisted** sources create tasks.
   - Montgomery's Tax Claim Bureau shows **terms not acknowledged** until an admin acknowledges it under Settings → Sources & compliance.
3. Click **Run pilot**. The jobs panel shows progress per property. Failures such as rate limits are retried automatically with backoff.

## 4. Complete user-assisted tasks (tab "3 · User-assisted tasks")

Each task lists the property, the official URL, the exact search (with a copy button) and instructions.

1. Open the official site. Accept its disclaimer, sign in with your own account, or complete its human check yourself.
2. Run the search, then copy the page (Ctrl+A, Ctrl+C) or save it, and paste or upload it into the task. You can drag the **Copy page for USI** bookmarklet to your bookmarks bar to copy a page in one click.
3. Click **Preview** to see every recognised field. Click **Save capture** to store the page as immutable evidence and update the property: assessment fields, tax-sale status, recorder documents with mortgage matching, and lien cases.
4. If an official recorder or civil search returns nothing, tick **The official search returned no results**, so "searched, none found" is recorded separately from "not checked".

## 5. Review results (tab "4 · Results grid")

- **Columns:** the grid follows the reference workbook: Municipality, Sale Number and Parcel pinned; the assessment, valuation, tax and mortgage groups; then application columns (decision, explanation, pending actions, provenance).
- **Colours:**
  - Entire row red: Montco 2024 balance of $0.00.
  - Orange: valuation below $200,000, or Delco assessment below $100,000.
  - Green/red: Delco Tax Sale Status.
  - Red/green/yellow: mortgage summary.
  - Decision chips.
- **Text styles:** *italic* = estimate; blue = manual or override; brown dashed = stale; grey = unknown.
- **Navigation:** search, filter by decision or pilot, sort, filter and resize columns, show or hide columns, paginate, and save named views.
- **Inspect and edit:**
  - Click a pinned cell to open the **property drawer**. Tabs: Summary, Sale list, Assessment, Valuation, Taxes, Mortgages, Liens, Rules, Evidence, Capture, Images.
  - **Alt+click** any cell for its evidence: source, URL, retrieval time, raw value, quality, notes and snapshot.
  - **Double-click** an editable cell to override it. A reason is required, and the override is audited and re-evaluated immediately.
- **Mortgages tab:** confirm or reject satisfaction candidates, and confirmed candidates turn the mortgage green.
- **Valuation tab:** record a value you viewed on Zillow, Realtor.com, Homes.com, Redfin or Trulia, or re-run the configured APIs.

## 6. Run the full list

Back on **Pilot & enrichment**, click **Run all**. Automated sources respect per-source rate limits, and successful results are cached, so a re-run only repeats what failed.

## 7. Daily refresh and history

The worker refreshes tax-sale status every day (06:00 America/New_York by default). **Refresh log** lists each run: checked, updated, changed, failed and user action needed. Each property's Taxes tab keeps its status history. Delaware County status is behind a disclaimer, so the refresh creates re-capture tasks and older values are marked stale.

## 8. Export

Click **Export XLSX** in the grid, or build one under **Exports**. The workbook contains:

| Sheet | Contents |
|---|---|
| Updated Sale List | Reference layout, conditional formatting, hyperlinks, frozen panes, filters |
| Mortgage Info | Every recorder document, with red/green/yellow mortgage rows and match reasons |
| Lien Cases | Relevant lien cases |
| Sources | Provenance for every field |
| Source Catalog | Access methods and terms |
| Refresh Log, Tax Status History | Refresh runs and per-property status history |
| Errors & Review Queue, User Action Tasks | Outstanding issues and open tasks |
| PDF Extraction | Each row traced to its PDF page, raw text and corrections |
| Configuration | Rule-set version and thresholds |
| Audit Log | Project audit entries |
| Notes & Legend | Colour legend and disclaimer |

## Settings

- **Sources & compliance:** each source's access method, terms, verified findings, rate limits and health. Enable or disable sources and acknowledge terms.
- **Valuation & image providers:** setup steps and documentation links, encrypted key entry (only a hint is shown back), enable, and test.
- **Screening rules:** edit thresholds and policies, such as the valuation threshold, size minimums, and how unsatisfied mortgages affect the decision. Saving creates a new audited version and re-evaluates every property.
- **Workbook templates:** upload a newer reference workbook; only its layout is stored.
