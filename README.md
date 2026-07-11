# Publix Receipt Archiver

[![ci](https://github.com/brett6320/publix_receipt_archiver/actions/workflows/ci.yml/badge.svg)](https://github.com/brett6320/publix_receipt_archiver/actions/workflows/ci.yml)
[![docker-publish](https://github.com/brett6320/publix_receipt_archiver/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/brett6320/publix_receipt_archiver/actions/workflows/docker-publish.yml)
[![secret-scan](https://github.com/brett6320/publix_receipt_archiver/actions/workflows/secret-scan.yml/badge.svg)](https://github.com/brett6320/publix_receipt_archiver/actions/workflows/secret-scan.yml)

Logs into Publix.com (or reuses a captured session), downloads **all available
in-store receipts**, and compiles every purchased item into **deduplicated CSVs**
— by date, price, and item number. It also renders a clean **PDF** and a
browsable **Markdown** archive of every receipt.

Each receipt is saved once (keyed by its `ReceiptId`), so re-running is
**idempotent**: it only picks up what's new and never double-counts.

> ⚠️ This uses Publix's own **undocumented** internal API — the same
> `services.publix.com/api/v1/PurchaseHistory` calls the website makes. It only
> touches **your own** account data. Publix can change the endpoints at any time.

> ⏳ **~180-day retention.** Publix keeps roughly **180 days** of in-store
> purchase history; there's no way to reach further back. So an archive only
> *grows* if you run the tool regularly — capture now, and keep capturing, to
> outlive the retention window.

> 🕒 **24-hour import delay.** Publix publishes a receipt's itemized detail
> 24–48h after purchase; before then it returns a placeholder (all "Normal
> Sale", no product names). So `fetch` **defers** purchases younger than 24h
> (`PUBLIX_IMPORT_DELAY_HOURS`), and any placeholder already saved is **dropped**
> on the next fetch/refresh so it re-imports once the real receipt publishes.

## How it works

1. **Auth** — the API wants a Bearer access token (the `AccessTokenJwt` cookie,
   an Azure AD B2C token) plus an `EcmsId` header (from the `User` cookie). You
   provide these by `import-curl` (recommended), `login`, or `paste-token`.
2. **Fetch** — pages through your purchase history newest-first, fetching each
   transaction's full detail (items, tenders, barcode, printed text) and saving
   the merged record as `data/raw/<ReceiptId>.json`.
3. **Parse** — reads all raw data, dedupes, and writes CSVs to `data/output/`.
4. **PDF / Markdown** — render each receipt to a PDF and a Markdown page.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Or just launch the web app (creates the venv on first run):

```bash
python -m publix_archiver auth adduser <name>   # first: create a login (see Web login)
./run_web.sh              # http://127.0.0.1:8000  (PORT=9000 ./run_web.sh to change)
```

The web UI requires a login — create an account first, or the sign-in page will
reject everyone. See [Web login](#web-login-authentication--mfa).

### Docker

```bash
docker compose up --build             # http://localhost:8000
PORT=9000 docker compose up --build   # or any port
```

Create a login before you sign in (the container serves the same auth-gated UI):

```bash
docker compose run --rm web python -m publix_archiver auth adduser <name>
```

The web port is configurable everywhere: `web --port 9000`, or the
`PUBLIX_WEB_PORT` (or generic `PORT`) env var, honored by the CLI, `run_web.sh`,
and Docker/compose. `--port` overrides the env var. The bind host defaults to
`127.0.0.1` (`PUBLIX_WEB_HOST`, or `web --host`); the Docker image binds
`0.0.0.0` so the container is reachable from the host.

The image bundles headless Chromium (for PDF rendering). Your data (receipts,
CSVs, PDFs, Markdown, credentials, and the `web_users.json` account store)
persists in `./data` via a mounted volume.

## 🔑 Getting credentials — `import-curl` (recommended)

> 📖 **New here? Follow the [Quickstart guide](docs/QUICKSTART.md)** — a
> step-by-step walkthrough of copying the request from your browser and pasting
> it into the **Collect** page.

The most reliable way to authorize the API is to copy one real request from your
own logged-in browser:

1. Log into **publix.com** in your normal browser and open **Account → Purchases**.
2. Open **DevTools → Network** and let the page load.
3. Find the request to `services.publix.com/api/v1/PurchaseHistory`,
   **right-click → Copy → Copy as cURL**.
4. Run:
   ```bash
   python -m publix_archiver import-curl        # reads the cURL from your clipboard
   python -m publix_archiver fetch && python -m publix_archiver parse
   ```

`import-curl` captures the **exact** Bearer token + `EcmsId` the browser used, so
the API accepts them. The token lives ~1 hour, so do steps 3–4 promptly.

### Alternatives

- **`login`** — opens a visible Chromium with a persistent profile; you sign in
  once and it reads the `AccessTokenJwt` / `EcmsId` cookies afterward. Later runs
  reuse the session.
  ```bash
  python -m publix_archiver login && python -m publix_archiver fetch
  ```
- **`paste-token`** — paste the `AccessTokenJwt` + `EcmsId` (as JSON or a cookie
  blob) from your browser; the command reads it off your clipboard (`pbpaste`) or
  a `--file`. Handy when you can't run the automated browser.
  ```bash
  python -m publix_archiver paste-token
  ```
- **Environment variables** — `PUBLIX_ACCESS_TOKEN` and `PUBLIX_ECMS_ID` override
  everything if set.

## ⭐ Easiest bulk path: browser-console export

Pull your **entire** available history in one shot, using your own logged-in
browser (no token to copy):

1. Log into **publix.com** and open **Account → Purchases**.
2. DevTools → **Console**, paste the contents of
   [`browser/publix_fetch_receipts.js`](browser/publix_fetch_receipts.js), press Enter.
   It reads your session cookies, pages your purchase history, fetches each
   receipt's detail, and downloads `publix_receipts.json`.
3. Ingest and process the whole file:
   ```bash
   python -m publix_archiver import ~/Downloads/publix_receipts.json
   python -m publix_archiver parse && python -m publix_archiver pdf && python -m publix_archiver markdown
   python -m publix_archiver web
   ```

`import` reads the `{"receipts": [...]}` file (or a single record, a list, or a
`{"CurrentTransactions": [...]}` envelope) and saves each receipt to `data/raw/`,
deduped by `ReceiptId`.

### Backups

Compressed snapshots of your imported receipts (`data/raw`) as `.tar.gz` in
`data/backups`. In the web UI, **admins** get a Backups panel (Collect tab) to
create, download, restore, and delete them. Restore is **additive** — receipts
already on disk are skipped by identity, so it never creates duplicates. From the
CLI:

```bash
python -m publix_archiver backup create
python -m publix_archiver backup list
python -m publix_archiver backup restore receipts-YYYYMMDD-HHMMSS.tar.gz
python -m publix_archiver backup delete  receipts-YYYYMMDD-HHMMSS.tar.gz
```

## Usage

```bash
# Recommended: fetch + parse + pdf + markdown in one shot
python -m publix_archiver all

# …or step by step:
python -m publix_archiver import-curl    # capture credentials (see above)
python -m publix_archiver fetch          # download all in-store receipts
python -m publix_archiver parse          # (re)build the CSVs from raw data
python -m publix_archiver pdf            # render a clean PDF per receipt
python -m publix_archiver markdown       # index + per-receipt Markdown pages
```

Useful `fetch` flags:

- `--page-size N`   purchase-list page size (default 25).
- `--from YYYY-MM-DD` / `--to YYYY-MM-DD`   restrict the date range (optional).
- `--all`          re-fetch every receipt, even those already on disk.
- `--timeout SEC`  how long to wait for interactive login (default 300).

## Web login (authentication + MFA)

The web UI is **private**: every page and API route requires a signed-in session.
Accounts are **local**, and login is **password + a TOTP one-time code** (the
6-digit codes from Google Authenticator, 1Password, Authy, etc.). If no account
exists, the login page refuses everyone.

Manage accounts from the CLI (secrets are shown in your terminal, not the browser):

```bash
python -m publix_archiver auth adduser <name>   # prompts for a password, prints the TOTP secret
python -m publix_archiver auth users            # list accounts
python -m publix_archiver auth passwd <name>    # change password
python -m publix_archiver auth reset-mfa <name> # regenerate the TOTP secret
python -m publix_archiver auth deluser <name>   # remove an account
```

`adduser` prints an `otpauth://` URI and a base32 secret — scan/paste it into your
authenticator, then sign in with your password + the current code. Accounts live in
`data/web_users.json` (git-ignored, `0600`; passwords are salted **PBKDF2-HMAC-SHA256**,
never stored in plaintext).

Relevant environment variables:

- `PUBLIX_SESSION_TTL` — session lifetime in seconds (default `43200` = 12h).
- `PUBLIX_WEB_HTTPS=1` — mark the session cookie `Secure` when serving over TLS.
- `PUBLIX_AUTH_ISSUER` — label shown in authenticator apps (default
  "Publix Receipt Archiver").

> Sessions are held in server memory, so restarting `web` signs everyone out.

## Outputs (`data/output/`)

| File | Contents |
|------|----------|
| `line_items.csv` | Every purchased line item, one row each, **newest first**: date, item number (UPC), description, unit qty (weight for weighed items), unit price, amount, department, tax flag, `tax_exempt`, `store` (name) and `store_number`, receipt id, doc type, `order_type` (store/pharmacy/greenwise/**discount**), `discount_ref` (for savings lines, the item they apply to), source. |
| `items_deduped.csv` | **One row per item**, aggregated across all purchases: times purchased, total qty, total spent, last price, first/last purchase date. |
| `receipts.csv` | One row per receipt: date, `store`, totals, taxes, savings. |

A browsable **Markdown archive** is written to `data/output/markdown/`:
`index.md` lists every receipt newest-first and links to `receipts/<id>.md`, a
page per receipt with each line item, a **publix.com** product-detail link, the
embedded barcode, totals, tenders, the printed receipt text, and a link to the
rendered PDF.

Raw archives are kept in `data/raw/` (per-receipt JSON) so you can re-parse
without re-downloading. Rendered per-receipt PDFs live in `data/pdfs/`. Re-running
`pdf` always re-renders from the current template and **overwrites a PDF only when
the result differs**; unchanged files are left untouched. Pass `pdf --force` to
rewrite every PDF regardless.

## Privacy

- `data/` and `.publix_profile/` are git-ignored. The profile holds your logged-in
  session and the cached `data/credentials.json` holds bearer tokens — **keep them
  private** and don't commit them.
- The web UI requires login (password + TOTP MFA); accounts live in the git-ignored
  `data/web_users.json` (`0600`, salted PBKDF2 password hashes).

## Troubleshooting

- **`401`/`403` from the API.** Your access token expired (~1h life). Re-capture
  it with `import-curl`, `paste-token`, or `login`, then re-run `fetch`.
- **`login` didn't produce credentials.** Complete sign-in fully and open
  Account → Purchases so the token/EcmsId cookies are set. If it still fails, use
  `import-curl` with a *Copy as cURL* of the PurchaseHistory request, or set
  `PUBLIX_ACCESS_TOKEN` / `PUBLIX_ECMS_ID` directly.
- **Base URL drift.** Override the API base with `PUBLIX_API_BASE` if Publix moves
  the service.

## Tests

```bash
python -m pytest -q
```

- `test_pipeline` merges the real fixtures (`tests/fixtures/publix_detail.json` +
  `publix_list.json`), writes a raw record, runs `parse_all`, and asserts the line
  items, store name, and that totals reconcile to `GrandTotal`.
- `test_dedup` verifies the same `ReceiptId` ingested twice collapses to one receipt.
- `test_ingest` verifies `ingest_paths` saves the right number of raw files from a
  single record, an envelope, a list, and a directory.
