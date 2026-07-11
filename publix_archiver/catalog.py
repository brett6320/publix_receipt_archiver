"""Publix product-catalog search — a second source of item-number suggestions.

When a description has never carried a number in your own archive, the local
suggester (`web._item_suggestions`) has nothing to offer. This module fills that
gap by querying Publix's own product-search API for the keyword and pulling the
UPC off each matching product.

It reuses the same reverse-engineered services.publix.com host and the *same*
saved Bearer + EcmsId session the purchase-history fetch already uses, so it
inherits that session's Akamai clearance. It is strictly best-effort: if the
saved token is stale, the network is unreachable, or Publix changes the response
shape, it returns `[]` and the caller falls back to local suggestions. Every
result is only ever a *suggestion* a human confirms before saving.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from . import config

# Publix's web app calls /v3/product/search; the fallbacks let a version bump on
# their side degrade to "no catalog results" instead of a hard error.
_ENDPOINTS = ("/v3/product/search", "/v4/product/search", "/v3/product")
_QUERY_KEYS = ("query", "keyword", "searchTerm")

# Field-name hints (compared case-insensitively) for the recursive product
# finder — we don't rely on knowing Publix's exact schema.
_NUM_FIELDS = ("upc", "retaileritemid", "itemnumber", "itemcode", "gtin")
_NAME_FIELDS = ("itemname", "displayname", "productname", "name", "title", "description")


def _strip_upc(code) -> str:
    """Normalize a code ('00000000004799') to a bare UPC ('4799'), matching how
    the parser stores item numbers so catalog picks line up with the archive."""
    s = re.sub(r"\D", "", str(code or ""))
    return s.lstrip("0") or s


def _load_headers() -> Optional[dict]:
    """The saved browser headers (Bearer + EcmsId), or None if absent/expired."""
    from .api import PublixAPI
    return PublixAPI._load_saved_headers()


def available() -> bool:
    """True if we hold a non-expired session to query the catalog with."""
    return _load_headers() is not None


def _find_products(obj: Any, out: list, depth: int = 0) -> None:
    """Walk arbitrary JSON and collect {item_number, description} from every
    object that carries both a UPC-like and a name-like field. Robust to Publix
    nesting products under facets/variants or renaming the envelope."""
    if depth > 8:
        return
    if isinstance(obj, list):
        for x in obj:
            _find_products(x, out, depth + 1)
        return
    if not isinstance(obj, dict):
        return
    lk = {k.lower(): k for k in obj.keys()}
    num = ""
    for want in _NUM_FIELDS:
        for lname, orig in lk.items():
            if lname == want or lname.endswith(want):
                v = _strip_upc(obj.get(orig))
                if v and v.isdigit():
                    num = v
                    break
        if num:
            break
    name = ""
    for want in _NAME_FIELDS:
        if want in lk:
            v = str(obj.get(lk[want]) or "").strip()
            if v and len(v) > len(name):
                name = v
    if num and name:
        out.append({"item_number": num, "description": name})
    for v in obj.values():
        if isinstance(v, (list, dict)):
            _find_products(v, out, depth + 1)


def _rank(query: str, items: list[dict], limit: int) -> list[dict]:
    """Dedup by item number, rank by shared-word overlap with the query then by
    shorter (more specific) name."""
    qtoks = set(re.findall(r"[a-z0-9]+", (query or "").lower()))
    best: dict[str, tuple] = {}
    for it in items:
        num, desc = it["item_number"], it["description"]
        toks = set(re.findall(r"[a-z0-9]+", desc.lower()))
        score = len(qtoks & toks)
        cur = best.get(num)
        if not cur or score > cur[1] or (score == cur[1] and len(desc) < len(cur[0])):
            best[num] = (desc, score)
    out = [{"item_number": k, "description": v[0], "score": v[1], "source": "catalog"}
           for k, v in best.items()]
    out.sort(key=lambda x: (-x["score"], len(x["description"])))
    return out[:limit]


def search_products(query: str, limit: int = 8, timeout: float = 12.0) -> list[dict]:
    """Suggest item numbers for a keyword from Publix's product catalog.

    Returns [] (never raises) when no session is available or the lookup fails,
    so the web layer can always fall back to local suggestions."""
    q = (query or "").strip()
    if not q:
        return []
    hdrs = _load_headers()
    if not hdrs:
        return []
    import httpx
    base = config.API_BASE
    found: list[dict] = []
    try:
        client = httpx.Client(headers=hdrs, timeout=timeout, http2=True)
    except Exception:
        return []
    try:
        for ep in _ENDPOINTS:
            for qk in _QUERY_KEYS:
                try:
                    r = client.get(base + ep,
                                   params={qk: q, "rows": max(limit, 10), "page": 1})
                except Exception:
                    continue
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except Exception:
                    continue
                _find_products(data, found)
                if found:
                    break
            if found:
                break
    finally:
        client.close()
    return _rank(q, found, limit)
