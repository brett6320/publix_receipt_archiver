"""Publix product-catalog suggestions: pull UPCs off arbitrary product JSON,
rank by overlap, and degrade to [] when no session is available."""
from publix_archiver import catalog


def test_strip_upc_matches_archive_format():
    assert catalog._strip_upc("00000000004799") == "4799"
    assert catalog._strip_upc("4011") == "4011"
    assert catalog._strip_upc(None) == ""


def test_find_products_walks_nested_shapes():
    # A made-up envelope with products nested under a facet, mixed field names.
    payload = {
        "Results": {
            "Products": [
                {"ItemName": "Bananas", "Upc": "4011", "Price": "0.59"},
                {"displayName": "Organic Bananas", "retailerItemId": "0000094011"},
            ]
        },
        "unrelated": [{"foo": "bar"}, {"name": "no number here"}],
    }
    out = []
    catalog._find_products(payload, out)
    pairs = {(p["item_number"], p["description"]) for p in out}
    assert ("4011", "Bananas") in pairs
    assert ("94011", "Organic Bananas") in pairs
    # An object with a name but no UPC-like field is not a product.
    assert all(p["description"] != "no number here" for p in out)


def test_rank_dedups_and_orders_by_overlap():
    items = [
        {"item_number": "4011", "description": "Bananas"},
        {"item_number": "4011", "description": "Bananas Yellow"},   # dup number
        {"item_number": "123", "description": "Whole Milk"},
    ]
    ranked = catalog._rank("bananas", items, limit=5)
    nums = [r["item_number"] for r in ranked]
    assert nums[0] == "4011"                       # shares the query word
    assert nums.count("4011") == 1                 # deduped
    assert ranked[0]["description"] == "Bananas"   # shorter of the two dup names
    assert all(r["source"] == "catalog" for r in ranked)


def test_search_returns_empty_without_session(monkeypatch):
    monkeypatch.setattr(catalog, "_load_headers", lambda: None)
    assert catalog.search_products("bananas") == []
    assert catalog.available() is False


def test_search_parses_live_response(monkeypatch):
    monkeypatch.setattr(catalog, "_load_headers", lambda: {"Authorization": "Bearer x"})

    class _Resp:
        status_code = 200
        def json(self):
            return {"Products": [{"ItemName": "Peanut Butter", "UPC": "51500700"}]}

    class _Client:
        def __init__(self, *a, **k): pass
        def get(self, url, params=None): return _Resp()
        def close(self): pass

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)
    out = catalog.search_products("peanut butter")
    assert out and out[0]["item_number"] == "51500700"
    assert out[0]["description"] == "Peanut Butter"


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v"]))
