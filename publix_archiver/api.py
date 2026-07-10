"""REST client for Publix's in-store purchase-history API.

Endpoints are reverse-engineered from the web app's own network calls (the
Account → Purchases page, rendered client-side by pb_PurchaseDetails.js):

  GET  /v1/PurchaseHistory?fromDate=&toDate=&page=&size=   -> paged list
  GET  /v1/PurchaseHistory/detail?transactionKey=<key>     -> one full receipt
  POST /v1/receiptqueue {receiptId, brandId}               -> email a receipt

Auth is a Bearer access token (Azure AD B2C) plus an `EcmsId` header. Publix may
change these; the base url is centralized in config and overridable via the
PUBLIX_API_BASE env var.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from . import config
from .auth import Credentials


class PublixAPI:
    def __init__(
        self,
        creds: Credentials,
        timeout: float = 60.0,
        headers: Optional[dict] = None,
    ):
        # Prefer exact headers captured from the browser; else reconstruct.
        hdrs = headers or self._load_saved_headers() or creds.headers()
        self._client = httpx.Client(headers=hdrs, timeout=timeout, http2=True)

    @staticmethod
    def _load_saved_headers() -> Optional[dict]:
        import json
        from .auth import token_is_expired
        f = config.API_HEADERS_FILE
        if not f.exists():
            return None
        try:
            hdrs = json.loads(f.read_text())
        except Exception:
            return None
        # Ignore captured headers whose Bearer token has expired (~1h), so a
        # fresh env/cached token isn't shadowed by stale headers.
        auth = hdrs.get("Authorization") or hdrs.get("authorization") or ""
        tok = auth.replace("Bearer ", "").strip()
        if tok and token_is_expired(tok):
            return None
        return hdrs

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PublixAPI":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _get(self, url: str, params: dict) -> Any:
        resp = self._client.get(url, params=params)
        if resp.status_code in (401, 403):
            raise PublixAuthError(resp.status_code, resp.text[:300])
        resp.raise_for_status()
        return resp.json()

    def purchases_page(
        self,
        page: int = 1,
        size: int = 10,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> dict:
        """One page of the purchase list.

        Returns the raw envelope: {CurrentPage, TotalPages, TotalCount,
        CurrentTransactions:[...]}. Dates are YYYY-MM-DD (optional).
        """
        params: dict[str, Any] = {"page": page, "size": size}
        if from_date:
            params["fromDate"] = from_date
        if to_date:
            params["toDate"] = to_date
        return self._get(config.PURCHASE_HISTORY_URL, params)

    def iter_transactions(
        self,
        size: int = 25,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        max_pages: Optional[int] = None,
    ):
        """Yield every transaction summary across all pages (newest first)."""
        page = 1
        while True:
            env = self.purchases_page(page, size, from_date, to_date)
            txns = env.get("CurrentTransactions") or []
            for t in txns:
                yield t
            total_pages = int(env.get("TotalPages") or 1)
            if page >= total_pages or (max_pages and page >= max_pages) or not txns:
                break
            page += 1

    def transaction_detail(self, transaction_key: str) -> dict:
        """Full receipt for one transaction (items, tenders, barcode, text)."""
        return self._get(config.PURCHASE_DETAIL_URL,
                         {"transactionKey": transaction_key})


class PublixAuthError(RuntimeError):
    def __init__(self, status: int, body: str = ""):
        self.status = status
        self.body = body
        super().__init__(
            f"Publix API auth failed ({status}). Your access token has likely "
            "expired (~1h life) — re-capture it with `import-curl`, `paste-token` "
            "or `login`.")


def merge_detail(txn: dict, detail: dict) -> dict:
    """Combine a list-summary and its detail into one stored receipt record.

    The list gives clean identifiers/date/store; the detail gives items,
    tenders, totals, barcode and the printed receipt text. We keep both so the
    parser has everything without a second lookup.
    """
    out = dict(detail or {})
    for k, v in (txn or {}).items():
        out.setdefault(k, v)
    return out
