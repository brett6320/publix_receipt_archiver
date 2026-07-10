"""Interactive login + credential extraction via a real (headed) browser.

Publix's sign-in runs through Azure AD B2C (account.publix.com) with 2FA, so we
drive a visible Chromium with a *persistent* profile. You log in once; the
session is reused on later runs. After login we read two things the site stores
in cookies:

  - AccessTokenJwt : the B2C access token (Bearer) the purchase-history API wants.
  - EcmsId         : the customer id the API wants in an `EcmsId` header.

The access token lives ~1 hour, so re-capture is cheap and occasional.
"""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from . import config


@dataclass
class Credentials:
    id_token: str            # B2C access token (AccessTokenJwt cookie)
    ecms_id: str             # customer id (EcmsId)

    def headers(self) -> dict:
        """Headers Publix's web app sends on every purchase-history API call."""
        return {
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {self.id_token}",
            "EcmsId": self.ecms_id,
            "User-Agent": config.USER_AGENT,
            "Origin": "https://www.publix.com",
            "Referer": "https://www.publix.com/",
        }


def _decode_user_cookie(value: str) -> dict:
    """The `User` cookie is URL-encoded JSON holding EcmsId, name, etc."""
    try:
        return json.loads(urllib.parse.unquote(value))
    except Exception:
        return {}


def creds_from_cookies(cookies: dict[str, str]) -> Optional[Credentials]:
    """Build Credentials from a name→value cookie map (from browser or cURL).

    Token: the `AccessTokenJwt` cookie. EcmsId: the `EcmsId` field of the `User`
    cookie, falling back to the `UserID` in `CartMicroserviceInfo`.
    """
    token = (cookies.get("AccessTokenJwt") or "").strip()
    if not token:
        return None
    ecms = ""
    user = _decode_user_cookie(cookies.get("User", ""))
    ecms = (user.get("EcmsId") or "").strip()
    if not ecms and cookies.get("CartMicroserviceInfo"):
        cart = _decode_user_cookie(cookies["CartMicroserviceInfo"])
        ecms = (cart.get("UserID") or "").strip()
    if not ecms:
        return None
    return Credentials(id_token=token, ecms_id=ecms)


def login_and_get_credentials(
    timeout_seconds: int = 300,
    cred_cache: Optional[Path] = config.CRED_CACHE_FILE,
    browser_channel: Optional[str] = None,
) -> Credentials:
    """Open a real browser, let the user sign in, then read the token + EcmsId
    from the resulting cookies. Reuses a persistent profile across runs."""
    from playwright.sync_api import sync_playwright

    channels = [browser_channel] if browser_channel else ["chrome", "msedge", None]
    creds: Optional[Credentials] = None

    with sync_playwright() as p:
        ctx = None
        for ch in channels:
            try:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=str(config.PROFILE_DIR),
                    headless=False,
                    channel=ch,
                    args=["--disable-blink-features=AutomationControlled"],
                    user_agent=config.USER_AGENT,
                )
                break
            except Exception:
                continue
        if ctx is None:
            raise RuntimeError("Could not launch a browser (Chrome/Edge/Chromium).")

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(config.SIGNIN_URL, wait_until="domcontentloaded")
        except Exception:
            pass

        print(">>> Sign in to Publix in the browser window, then open "
              "Account → Purchases. Waiting up to "
              f"{timeout_seconds}s for your session...")

        # Poll cookies until the access token appears (or we time out).
        import time
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            cookies = {c["name"]: c["value"] for c in ctx.cookies()}
            creds = creds_from_cookies(cookies)
            if creds and not token_is_expired(creds.id_token):
                # Make sure the account page has been visited so the token is
                # fresh, then re-read once.
                break
            page.wait_for_timeout(2000)

        # Persist exact headers for API replay while we still hold the context.
        if creds:
            _save_api_headers(creds.headers())
        ctx.close()

    if creds is None:
        raise RuntimeError(
            "Could not obtain credentials. Make sure you completed sign-in and "
            "opened Account → Purchases. Alternatively use `import-curl` with a "
            "'Copy as cURL' of the purchase-history request.")

    if cred_cache is not None:
        config.ensure_dirs()
        cred_cache.write_text(json.dumps(asdict(creds), indent=2))
    exp = token_expiry(creds.id_token)
    if exp:
        import datetime
        secs = int((exp - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
        print(f">>> Token valid ~{max(0, secs)//60} more min — fetch now.")
    print(">>> Credentials acquired.\n")
    return creds


def _save_api_headers(headers: dict) -> None:
    """Persist the exact request headers used for the purchase-history API."""
    config.ensure_dirs()
    config.API_HEADERS_FILE.write_text(json.dumps(headers, indent=2))


def token_expiry(id_token: str):
    """Return the token's exp as an aware UTC datetime, or None if undecodable."""
    import base64
    import datetime
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
        if exp:
            return datetime.datetime.fromtimestamp(exp, datetime.timezone.utc)
    except Exception:
        pass
    return None


def token_is_expired(id_token: str, skew_seconds: int = 30) -> bool:
    """True if the token is missing/expired (with a small safety skew)."""
    import datetime
    exp = token_expiry(id_token)
    if exp is None:
        return False  # can't tell; let the API be the judge
    now = datetime.datetime.now(datetime.timezone.utc)
    return now >= exp - datetime.timedelta(seconds=skew_seconds)
