"""Shared 'Copy as cURL' → credentials logic, used by both the CLI and web UI.

Parses a browser DevTools 'Copy as cURL' and saves:
  - api_headers.json  : exact headers (Bearer token + EcmsId) for API replay
  - credentials.json  : decoded token/ecms_id (for the ~1-hour expiry guard)

Two capture sources both work:
  1. The XHR to services.publix.com/api/v1/PurchaseHistory — carries an
     `Authorization: Bearer` header and an `EcmsId` header directly.
  2. Any authenticated www.publix.com request — carries the token in the
     `AccessTokenJwt` cookie and the EcmsId inside the `User` cookie.
"""
from __future__ import annotations

import datetime
import json
import shlex
from urllib.parse import unquote

from . import config
from .auth import (Credentials, creds_from_cookies, token_expiry,
                   token_is_expired)

# Headers that break an httpx replay (auto-managed or HTTP/2 pseudo-headers).
_DROP = {"content-length", "accept-encoding", "host", "connection",
         "transfer-encoding"}
_DATA_FLAGS = ("--data", "--data-raw", "--data-binary", "--data-ascii", "-d")


def parse_curl(text: str) -> tuple[str, dict, str]:
    """Parse a 'Copy as cURL' command into (url, headers, data)."""
    t = text.strip()
    t = t.replace("\\\n", " ").replace("^\n", " ").replace("`\n", " ")
    t = t.replace(" $'", " '")
    try:
        tokens = shlex.split(t)
    except ValueError:
        tokens = shlex.split(t.replace("$'", "'"))

    url, headers, data = "", {}, ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-H", "--header") and i + 1 < len(tokens):
            raw = tokens[i + 1]
            if ":" in raw and not raw.startswith(":"):
                k, v = raw.split(":", 1)
                if k.strip():
                    headers[k.strip()] = v.strip()
            i += 2
            continue
        if tok in ("-b", "--cookie") and i + 1 < len(tokens):
            headers["cookie"] = tokens[i + 1]
            i += 2
            continue
        if tok in _DATA_FLAGS and i + 1 < len(tokens):
            data = tokens[i + 1]
            i += 2
            continue
        if tok.lower().startswith("http"):
            url = tok
        i += 1
    return url, headers, data


def _parse_cookie_header(cookie: str) -> dict[str, str]:
    """Split a raw `Cookie:` header value into a name→value map."""
    out: dict[str, str] = {}
    for part in (cookie or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class CaptureError(ValueError):
    pass


def _credentials_from_curl(headers: dict) -> Credentials:
    """Pull a Bearer token + EcmsId out of parsed cURL headers.

    Prefers the explicit Authorization/EcmsId headers (the API XHR); falls back
    to the AccessTokenJwt / User cookies (any authenticated page request).
    """
    hl = {k.lower(): v for k, v in headers.items()}
    token = (hl.get("authorization") or "").replace("Bearer ", "").strip()
    ecms = (hl.get("ecmsid") or "").strip()
    if token and ecms:
        return Credentials(id_token=token, ecms_id=ecms)

    cookies = _parse_cookie_header(hl.get("cookie", ""))
    creds = creds_from_cookies(cookies)
    if creds:
        return creds

    raise CaptureError(
        "This cURL has no usable Publix credentials. Copy the request to "
        "services.publix.com/api/v1/PurchaseHistory (it carries Authorization + "
        "EcmsId), or any authenticated www.publix.com request whose cookies "
        "include AccessTokenJwt and User.")


def save_from_curl(text: str) -> dict:
    """Parse + persist credentials from a cURL. Returns a status summary.

    Raises CaptureError with a user-facing message on bad input.
    """
    if not text or "curl" not in text.lower():
        raise CaptureError("That doesn't look like a cURL command. Use "
                           "DevTools → Network → right-click the request → "
                           "Copy → Copy as cURL.")
    config.ensure_dirs()
    url, headers, _data = parse_curl(text)
    headers = {k: v for k, v in headers.items()
               if not k.startswith(":") and k.lower() not in _DROP}

    creds = _credentials_from_curl(headers)
    config.API_HEADERS_FILE.write_text(json.dumps(creds.headers(), indent=2))
    config.CRED_CACHE_FILE.write_text(json.dumps(
        {"id_token": creds.id_token, "ecms_id": creds.ecms_id}, indent=2))

    exp = token_expiry(creds.id_token)
    minutes = 0
    if exp:
        minutes = max(0, int((exp - datetime.datetime.now(datetime.timezone.utc))
                             .total_seconds()) // 60)
    return {
        "ok": True,
        "headers": len(creds.headers()),
        "has_query": False,
        "kind": "purchases",
        "token_minutes": minutes,
        "expired": token_is_expired(creds.id_token),
        "url": url,
    }
