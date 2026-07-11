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


# --- Email ingestion via Cloudflare R2 ---------------------------------------
# A Cloudflare Email Worker drops raw Publix receipt .eml objects into an R2
# bucket; the poller pulls them (S3-compatible API), ingests, and deletes them.
R2_ACCOUNT_ID = os.environ.get("PUBLIX_R2_ACCOUNT_ID", "")
R2_ENDPOINT = os.environ.get("PUBLIX_R2_ENDPOINT") or (
    f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else "")
R2_BUCKET = os.environ.get("PUBLIX_R2_BUCKET", "")
R2_ACCESS_KEY_ID = os.environ.get("PUBLIX_R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("PUBLIX_R2_SECRET_ACCESS_KEY", "")
R2_PREFIX = os.environ.get("PUBLIX_R2_PREFIX", "")  # e.g. "receipts/"

# Cloudflare Queue: the Worker enqueues a reference {bucket,key} per new R2
# object; the poller is an HTTP pull consumer that fetches, ingests, deletes the
# object, and acks the message. Needs an API token with Queues read/write.
CF_ACCOUNT_ID = os.environ.get("PUBLIX_CF_ACCOUNT_ID") or R2_ACCOUNT_ID
CF_QUEUE_ID = os.environ.get("PUBLIX_CF_QUEUE_ID", "")
CF_API_TOKEN = os.environ.get("PUBLIX_CF_API_TOKEN", "")

# How often the email poller pulls the queue, in seconds (default 5 minutes).
try:
    EMAIL_POLL_INTERVAL = int(os.environ.get("PUBLIX_EMAIL_POLL_INTERVAL") or 300)
except ValueError:
    EMAIL_POLL_INTERVAL = 300


# Admin-editable email-ingest settings live here (git-ignored, 0600). Values
# saved from the web UI override the env defaults above, so the poller container
# (which shares ./data) picks them up too.
EMAIL_CONFIG_FILE = DATA_DIR / "email_config.json"

# Keys stored in EMAIL_CONFIG_FILE, each paired with its env default.
_EMAIL_KEYS = {
    "r2_account_id": lambda: R2_ACCOUNT_ID,
    "r2_endpoint": lambda: R2_ENDPOINT,
    "r2_bucket": lambda: R2_BUCKET,
    "r2_access_key_id": lambda: R2_ACCESS_KEY_ID,
    "r2_secret_access_key": lambda: R2_SECRET_ACCESS_KEY,
    "r2_prefix": lambda: R2_PREFIX,
    "cf_account_id": lambda: CF_ACCOUNT_ID,
    "cf_queue_id": lambda: CF_QUEUE_ID,
    "cf_api_token": lambda: CF_API_TOKEN,
    "poll_interval": lambda: EMAIL_POLL_INTERVAL,
}
_SECRET_EMAIL_KEYS = {"r2_secret_access_key", "cf_api_token"}


def email_settings() -> dict:
    """Effective email-ingest settings: saved file merged over env defaults."""
    import json
    out = {k: default() for k, default in _EMAIL_KEYS.items()}
    try:
        saved = json.loads(EMAIL_CONFIG_FILE.read_text())
        for k in _EMAIL_KEYS:
            if saved.get(k) not in (None, ""):
                out[k] = saved[k]
    except Exception:
        pass
    # Derive the endpoint from the account id when only that was given.
    if not out["r2_endpoint"] and out["r2_account_id"]:
        out["r2_endpoint"] = f"https://{out['r2_account_id']}.r2.cloudflarestorage.com"
    if not out["cf_account_id"]:
        out["cf_account_id"] = out["r2_account_id"]
    try:
        out["poll_interval"] = int(out["poll_interval"] or 300)
    except (ValueError, TypeError):
        out["poll_interval"] = 300
    return out


def save_email_settings(values: dict) -> None:
    """Persist email-ingest settings (0600). Blank secret fields are preserved."""
    import json
    current = {}
    try:
        current = json.loads(EMAIL_CONFIG_FILE.read_text())
    except Exception:
        pass
    for k in _EMAIL_KEYS:
        if k in values:
            v = values[k]
            # An empty secret field means "leave unchanged" (not "clear").
            if k in _SECRET_EMAIL_KEYS and (v is None or v == ""):
                continue
            current[k] = v
    ensure_dirs()
    EMAIL_CONFIG_FILE.write_text(json.dumps(current, indent=2))
    try:
        EMAIL_CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


def r2_configured() -> bool:
    s = email_settings()
    return bool(s["r2_endpoint"] and s["r2_bucket"]
                and s["r2_access_key_id"] and s["r2_secret_access_key"])


def email_ingest_configured() -> bool:
    s = email_settings()
    return bool(r2_configured() and s["cf_account_id"] and s["cf_queue_id"]
                and s["cf_api_token"])


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, OUTPUT_DIR, PDF_DIR):
        d.mkdir(parents=True, exist_ok=True)
