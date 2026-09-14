# Source access and compliance record

Verified **2026-09-13** with single read-only requests: robots.txt, the landing page, and whether a disclaimer, login or CAPTCHA appears. The only exceptions were one parcel search and detail fetch on the Montgomery Tax Claim Bureau, and GIS queries for fixture parcels. Each source's descriptor in `backend/app/connectors/**` holds the same information; the app surfaces it under Settings → Sources & compliance and in the exported **Source Catalog** sheet.

Rules applied to every source:

- Official or documented APIs come first.
- Automated web access happens only where robots.txt allows it and no interactive gate exists.
- Terms that restrict use require a recorded administrator acknowledgement.
- Anything behind a disclaimer, login or human verification becomes a **user-assisted capture task**.
- CAPTCHAs, logins and paywalls are never bypassed. Credentials are never stored or automated.
- **Real-browser automation** (Playwright + Chromium) is used only for public portals whose sole "gate" is a plain "continue as public user" / disclaimer click with **no CAPTCHA and no anti-bot challenge** — the app opens the page the way a person's browser does and reads the public results. It is never pointed at a source protected by a CAPTCHA, Cloudflare Turnstile or "verify you are human" step. It is **off by default** (`USI_BROWSER_AUTOMATION=false`) and only runs where Chromium is installed; otherwise those sources fall back to user-assisted capture. See [DEPLOYMENT_BROWSER.md](DEPLOYMENT_BROWSER.md).

## Montgomery County, Pennsylvania

| Source | URL | Finding | Access in app |
|---|---|---|---|
| Board of Assessment land/building table | `https://gis.montcopa.org/arcgis/rest/services/Parcels/GIS_BOA_LAND/FeatureServer/0` | Public ArcGIS REST service (county open-data program); no robots.txt. Query by `PARCEL_NUMBER` (12 digits, space-padded). Parcel 02-00-01016-00-8 matched the reference workbook: SFLA 1,724, 9 rooms, 4 bed, 2 bath, built 1880, BRICK, ROW. | **Automated** (`montco.assessment_gis`). 30/min, ≥1 s apart, 7-day cache. |
| Property Records portal | `https://propertyrecords.montcopa.org/pt/search/commonsearch.aspx?mode=parid` | robots.txt `User-agent: * Disallow: /`; redirects to `Disclaimer.aspx`. Terms: information for the user's own use, not for sale; not a substitute for title search or appraisal. | **User-assisted** (`montco.assessment_portal`). A task is created only when the GIS lookup fails. |
| Tax Claim Bureau parcel search | `https://webapp02.montcopa.org/taxclaim/` | No robots.txt, login or CAPTCHA. `POST ParcelList.aspx` returns `showDetails(GUID)` links; `POST ParcelInfo.aspx` returns the parcel table and per-claim-year Face/Penalty/Interest/Total/Paid/Balance. The montcopa.org Terms of Use allow personal, non-commercial downloads; commercial use of county technology resources needs prior written CIO approval. The page says it is not a certified search. | **Automated after admin terms acknowledgement** (`montco.tax_claim`). 12/min, ≥5 s apart, 12-hour cache, daily refresh. Before acknowledgement, a capture task is created instead. |
| Recorder of Deeds | `https://rodviewer.montcopa.org/countyweb/` | robots.txt `Disallow: /`; sign-in required. | **User-assisted with your own account** (`montco.recorder`). Paste the results table or import CSV/XLSX; bulk import is supported for workbooks with a Parcel column. |
| Prothonotary case search | `http://webapp.montcopa.org/PSI/Viewer/Search.aspx?c=CaseSearch&panel=CaseNumber` | Redirects to `courtsapp.montcopa.org/psi/auth/init` with a "Validating" human-verification page. | **User-assisted** (`montco.civil`). Search by parcel with dashes removed. Only Status "1 - Open" cases whose type includes "Lien" are kept. |

## Delaware County, Pennsylvania

| Source | URL | Finding | Access in app |
|---|---|---|---|
| Parcels Public Access layer | `https://gis.delcopa.gov/arcgis/rest/services/Parcels/Parcels_Public_Access/FeatureServer/0` | Public ArcGIS REST; no robots.txt. `PARID` = folio digits as a number (01-00-00254-00 → 1000025400). Returns site address, legal description, GIS acreage and the county's `datalet.aspx` link. | **Automated** (`delco.parcels_gis`). Acreage is marked *estimated*. |
| Real Estate & Tax Records | `http://delcorealestate.co.delaware.pa.us/pt/search/commonsearch.aspx?mode=parid` | 2026-09-14: the "Agree" disclaimer is a plain form (POST `action=Agree` sets a `DISCLAIMER=1` cookie); **no active CAPTCHA** and **no robots.txt restriction**. After accepting, `datalet.aspx` tabs (`profileall`, `residential`, `comsummary`, `delinquent`) return the parcel data. The county removed name search and photos for privacy. | **Automated, terms-ack required** (`delco.assessment_portal`). Once an admin acknowledges the disclaimer terms, the app accepts it automatically (audited) and reads Site Location, Property Type, School District, building detail (style, base area, year built, beds, baths, acres) and delinquent taxes per parcel. If the portal adds a human-verification step, the app stops and falls back to user-assisted capture. |
| Treasurer tax payments | `https://www.delcopa.gov/treasurer/paytaxes` | Pay → Continue payment-portal flow. | **User-assisted** (`delco.treasurer`). The app never enters the payment flow. |
| Recorder public search | `https://delaware.pa.publicsearch.us/` | robots.txt `Allow: /$`, `Disallow: /` (home page only). | **User-assisted** (`delco.recorder_publicsearch`). |
| Recorder guest search | `https://delcorodonlineservices.co.delaware.pa.us/countyweb/search/searchMain.do?defaultType=Public` | "Login as Guest" plus disclaimer acceptance. | **User-assisted** (`delco.recorder_countyweb`), the default recorder task. |
| Civil public access (C-Track) | `https://delcopublicaccess.co.delaware.pa.us/` | 2026-09-14: public access is a plain **"CONTINUE AS PUBLIC USER"** click — **no login, no CAPTCHA, no robots.txt restriction**. Party Search returns a grid (Case Number, Case Classification, Case Filed Date, Party Name, Party Role); classifications include "Municipal Lien" etc. | **Automated with a real browser when browser automation is enabled** (`delco.civil`). The app performs the public-user click and runs one Party Search per derived name (LAST, FIRST MI; trusts split into person plus entity; companies by name), keeping only cases whose Case Classification includes "Lien". Name matches are not identity matches, so every retained lien case is flagged for review. When browser automation is off (the default, and on hosts without Chromium) it falls back to a user-assisted capture task. See [DEPLOYMENT_BROWSER.md](DEPLOYMENT_BROWSER.md). |

## Valuation and imagery

| Provider | Access |
|---|---|
| ATTOM Property API (AVM) | Documented API, `apikey` header |
| RentCast `/v1/avm/value` | Documented API, `X-Api-Key` header |
| Zillow via Bridge Interactive | Only with an approved agreement; field mapping must be verified |
| Zillow, Realtor.com, Homes.com, Redfin, Trulia websites | **Never scraped.** Users may record values they looked up themselves as manual observations. |
| Google Street View Static API | Licensed API. Key kept server-side, image proxied and not stored, attribution shown. |

## Error statuses

Every lookup is recorded with one of these statuses:

| Status | Meaning |
|---|---|
| `success` | Data retrieved and parsed |
| `no_match` | The source was searched and holds no record |
| `ambiguous_match` | More than one plausible record |
| `blocked` | robots.txt disallows the request, robots.txt was unreachable, or HTTP 403 |
| `authentication_needed` | HTTP 401 or a login form |
| `user_action_needed` | Disclaimer or CAPTCHA redirect, or a user-assisted source |
| `rate_limited` | HTTP 429 after retries |
| `parse_failure` | The page or response could not be parsed |
| `unexpected_failure` | Timeouts or HTTP 5xx after retries |
| `not_configured` | Required setup (for example an API key) is missing |
| `terms_not_acknowledged` | An admin has not yet acknowledged the source's terms |
| `skipped` | Source disabled or not applicable |

"No results" is always stored separately from "lookup failed".
