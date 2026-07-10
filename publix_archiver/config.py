"""Shared configuration and paths."""
from __future__ import annotations

import os
from pathlib import Path

# Root of the repo (parent of this package).
ROOT = Path(__file__).resolve().parent.parent

# Persistent Playwright profile — keeps you logged in between runs.
# Lives outside version control (see .gitignore).
PROFILE_DIR = ROOT / ".publix_profile"

# Where downloaded artifacts land.
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"           # raw JSON responses, one file per receipt
OUTPUT_DIR = DATA_DIR / "output"     # parsed CSV / summaries
PDF_DIR = DATA_DIR / "pdfs"          # per-receipt PDF archive (+ captured PDFs)

# Exact request headers captured from the browser's own purchase-history call.
# Preferred over reconstructed headers because they include everything the API
# expects (Bearer token + EcmsId). The B2C access token lives ~1 hour, so this
# file is short-lived by nature.
API_HEADERS_FILE = DATA_DIR / "api_headers.json"

# Exact request (url + params) captured from the browser's purchase-history call,
# so fetch can replay Publix's own request verbatim if the shape ever drifts.
API_REQUEST_FILE = DATA_DIR / "api_request.json"

# Decoded token / EcmsId cache (short-lived; token ~1 hour).
CRED_CACHE_FILE = DATA_DIR / "credentials.json"

# Publix endpoints (undocumented; reverse-engineered from the site's own calls).
# The purchase-history service is a REST API on services.publix.com; the account
# pages that call it live on www.publix.com.
API_BASE = os.environ.get("PUBLIX_API_BASE", "https://services.publix.com/api")
PURCHASE_HISTORY_URL = f"{API_BASE}/v1/PurchaseHistory"          # list (paged)
PURCHASE_DETAIL_URL = f"{API_BASE}/v1/PurchaseHistory/detail"    # ?transactionKey=
RECEIPT_QUEUE_URL = f"{API_BASE}/v1/receiptqueue"               # POST email-a-receipt

SIGNIN_URL = "https://www.publix.com/login"
ACCOUNT_URL = "https://www.publix.com/account/purchases?nav=account_tab_menu"
RECEIPTS_URL = "https://www.publix.com/account/purchases"

# Azure AD B2C tenant that fronts publix.com sign-in (for reference / login flow).
B2C_TENANT = "372cde5e-efa2-4da5-9b62-9ee9fd9c4bb8"
B2C_CLIENT_ID = "42e3d574-4d38-4d73-88da-1b894afb50ca"

# Publix keeps in-store purchase history for ~180 days. There is no way to reach
# further back, so an archive only grows if you run the tool regularly.
RETENTION_DAYS = 180

# Publix populates a transaction's itemized detail 24–48h after the purchase.
# Fetching detail for a transaction younger than this just fails (the receipt
# isn't ready), so we defer those to a later run. Override via PUBLIX_IMPORT_DELAY_HOURS.
try:
    IMPORT_DELAY_HOURS = int(os.environ.get("PUBLIX_IMPORT_DELAY_HOURS") or 24)
except ValueError:
    IMPORT_DELAY_HOURS = 24

# Web server host/port. Configurable via env (PUBLIX_WEB_HOST / PUBLIX_WEB_PORT,
# or the generic PORT). The `web --port/--host` flags override these.
WEB_HOST = os.environ.get("PUBLIX_WEB_HOST", "127.0.0.1")
try:
    WEB_PORT = int(os.environ.get("PUBLIX_WEB_PORT") or os.environ.get("PORT") or 8000)
except ValueError:
    WEB_PORT = 8000

# --- Web authentication (password + TOTP MFA) --------------------------------
# Account store for the web UI (usernames, password hashes, TOTP secrets, and a
# reserved slot for future passkeys). Local-only; keep it out of version control.
WEB_USERS_FILE = DATA_DIR / "web_users.json"

# Label shown in authenticator apps when enrolling TOTP.
AUTH_ISSUER = os.environ.get("PUBLIX_AUTH_ISSUER", "Publix Receipt Archiver")

# Session lifetime (seconds) and cookie hardening. Set PUBLIX_WEB_HTTPS=1 when
# serving over TLS (directly or behind a proxy) so the session cookie is marked
# Secure. Sessions live in server memory, so a restart signs everyone out.
try:
    SESSION_TTL_SECONDS = int(os.environ.get("PUBLIX_SESSION_TTL") or 43200)  # 12h
except ValueError:
    SESSION_TTL_SECONDS = 43200
COOKIE_SECURE = (os.environ.get("PUBLIX_WEB_HTTPS", "").lower()
                 in ("1", "true", "yes", "on"))

# A modern desktop UA reduces the chance of being served a degraded/blocked page.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, OUTPUT_DIR, PDF_DIR):
        d.mkdir(parents=True, exist_ok=True)
