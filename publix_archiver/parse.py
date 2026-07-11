"""Parse raw Publix receipts into deduplicated CSVs.

Outputs (in data/output/):
  line_items.csv      - every purchased line item, one row each, newest first.
  items_deduped.csv   - one row per item, aggregated across all purchases.
  receipts.csv        - one row per receipt (header-level totals).

Each raw file is a merged record: the purchase-list summary (date, store,
ReceiptId) plus the detail payload (Products[], ReceiptLineItems[],
ReceiptTenderLineItems[], totals, BarcodeSrc, ReceiptText). The `store`/
`store_number` columns hold the Publix store name and facility number.
"""
from __future__ import annotations

import csv
import html
import json
import re
from pathlib import Path
from typing import Iterable

from . import config


def _num(v) -> float:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _receipt_date(r: dict) -> str:
    raw = r.get("TransactionDate") or ""
    return str(raw)[:10]  # YYYY-MM-DD


# Publix receipt logo ids → the kind of store the receipt came from.
STORE_KINDS = {
    1: "Publix",
    2: "Publix Sabor",
    3: "Publix GreenWise Market",
    4: "Publix Pharmacy",
    5: "GreenWise Market",
}


def order_type(receipt: dict) -> str:
    """Classify a receipt. Publix in-store purchases are all 'store'; the logo id
    distinguishes pharmacy / GreenWise, which the UI can badge differently."""
    logo = receipt.get("ReceiptLogoId")
    if logo == 4:
        return "pharmacy"
    if logo in (3, 5):
        return "greenwise"
    return "store"


def store_name(receipt: dict) -> str:
    """Store name, trimmed. Publix already returns a clean facility name."""
    raw = str(receipt.get("FacilityName") or "").strip()
    return re.sub(r"\s{2,}", " ", raw)


# Back-compat alias so shared render code can stay retailer-neutral.
warehouse_name = store_name


# Publix per-line tax/benefit letters, printed at the right edge of each receipt
# line. Mirrors the legend the site shows on the purchase-details page.
TAX_CODE_LABELS = {
    "t": "Food tax rate",
    "T": "Taxable item",
    "M": "Multiple tax plans",
    "L": "Locally taxed item",
    "F": "SNAP eligible",
    "P": "Prescription",
    "H": "Healthcare product",
}


def tax_code_label(code) -> str:
    """Human-readable meaning of a per-line tax/benefit letter ('' if none).

    Handles multi-letter codes (e.g. 'MT') by joining each letter's label."""
    code = str(code or "").strip()
    if not code:
        return ""
    parts = [TAX_CODE_LABELS.get(ch, f"Publix code {ch}") for ch in code]
    return ", ".join(parts)


def _decode_receipt_text(text) -> str:
    """ReceiptText escapes newlines as literal \\n and/or &#10; entities."""
    return str(text or "").replace("&#10;", "\n").replace("\\n", "\n")


# An item line in ReceiptText ends with the extended amount followed by the tax
# /benefit letter(s), e.g. "1.40 lb @ 2.99/ lb   4.19   F". Totals/savings lines
# end with a bare amount (no letter), so they never match.
_TEXT_AMT_TAX = re.compile(r"([\d,]+\.\d{2})\s+([tTMLFPH]+)\s*$")


def item_tax_codes(record: dict) -> list[str]:
    """Per-item tax/benefit letters, aligned to record['ReceiptLineItems'].

    Publix doesn't return a per-line tax field — the letter is only printed in
    ReceiptText — so we read it back out, matching each line item to its printed
    line by extended amount. Returns '' for items the receipt prints no letter on.
    """
    from collections import defaultdict, deque
    text = _decode_receipt_text(record.get("ReceiptText"))
    by_amt: dict[float, deque] = defaultdict(deque)
    for line in text.splitlines():
        m = _TEXT_AMT_TAX.search(line)
        if m:
            amt = round(float(m.group(1).replace(",", "")), 2)
            by_amt[amt].append(m.group(2))
    codes: list[str] = []
    for li in record.get("ReceiptLineItems") or []:
        dq = by_amt.get(_num(li.get("ItemAmount")))
        codes.append(dq.popleft() if dq else "")
    return codes


def _strip_upc(code) -> str:
    """Normalize an ItemCode ('00000000004799') to a bare UPC ('4799')."""
    s = re.sub(r"\D", "", str(code or ""))
    return s.lstrip("0") or s


def _clean_desc(s) -> str:
    """Decode HTML entities and tidy whitespace in a description. Publix's catalog
    names arrive HTML-encoded (e.g. 'Hershey&#39;s' -> \"Hershey's\")."""
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()


def _product_index(receipt: dict) -> dict[str, dict]:
    """Map normalized UPC -> product catalog entry (name, urls, image)."""
    idx: dict[str, dict] = {}
    for p in receipt.get("Products") or []:
        upc = _strip_upc(p.get("UPC"))
        if upc:
            idx.setdefault(upc, p)
    return idx


def product_description(prod: dict | None, fallback: str = "") -> str:
    """Best display name for a product: catalog name (+ size), else fallback."""
    if not prod:
        return _clean_desc(fallback)
    name = _clean_desc(prod.get("ItemName"))
    size = _clean_desc(prod.get("SizeDescription"))
    desc = _clean_desc(prod.get("ItemDescription"))
    out = name or desc or _clean_desc(fallback)
    if size and size.lower() not in out.lower():
        out = f"{out} ({size})" if out else size
    return out.strip()


# Alias for shared render code.
item_description = product_description


def line_quantity(li: dict) -> float:
    """Units purchased for a receipt line. Weighed items report ItemQty=0 with a
    positive ItemWeight (lbs); everything else uses ItemQty (or MSUQty)."""
    qty = _num(li.get("ItemQty"))
    weight = _num(li.get("ItemWeight"))
    if not qty and weight:
        return weight
    return qty or _num(li.get("MSUQty")) or 1


def line_amounts(li: dict) -> tuple[float, float, float]:
    """(printed, discount, paid) for one receipt line.

    - ``printed`` = ItemAmount, the amount the receipt prints on the line.
    - ``paid``    = NetAmount, what the line actually contributed to the total
      (0 for a free BOGO item). Sum of ``paid`` over all lines == GrandTotal.
    - ``discount`` = printed − paid, the money removed on that line at the
      register (e.g. a BOGO promotion). This is NOT the same as ``SavingAmount``
      (savings vs the *regular* price), which for a special-price item is already
      reflected in the printed amount and must not be subtracted again.
    """
    printed = _num(li.get("ItemAmount"))
    net = li.get("NetAmount")
    paid = _num(net) if net is not None else round(printed - _num(li.get("SavingAmount")), 2)
    discount = round(printed - paid, 2)
    if discount < 0:  # printed shouldn't be below paid; guard against odd data
        discount, paid = 0.0, printed
    return printed, discount, paid


def _iter_line_items(receipt: dict, source: str = "publix") -> Iterable[dict]:
    date = _receipt_date(receipt)
    store = store_name(receipt)
    store_no = str(receipt.get("FacilityId") or "").strip()
    receipt_id = receipt.get("ReceiptId") or receipt.get("TransactionKey") or ""
    otype = order_type(receipt)
    prods = _product_index(receipt)
    tax_codes = item_tax_codes(receipt)  # per-item tax/benefit letter from ReceiptText

    for idx, li in enumerate(receipt.get("ReceiptLineItems") or []):
        upc = _strip_upc(li.get("ItemCode"))
        prod = prods.get(upc)
        desc = product_description(prod, fallback=str(li.get("ItemTypeDescription") or "").strip())
        # Show the printed line amount; a register-level deduction (BOGO promo)
        # becomes its own discount row below so the two net to what was paid.
        printed, inline_discount, _paid = line_amounts(li)
        amount = printed
        base = {
            "date": date,
            "item_number": upc,
            "unit_qty": line_quantity(li),
            "unit_price": _num(li.get("ItemPrice")),
            "amount": amount,
            "department": str(prod.get("RetailSubSectionNumber") or "").strip() if prod else "",
            # Prefer a per-line code (email records carry one); else the code
            # read back from ReceiptText by amount (API records).
            "tax_flag": li.get("TaxCode") or (tax_codes[idx] if idx < len(tax_codes) else ""),
            "store": store,
            "store_number": store_no,
            "receipt_id": receipt_id,
            "doc_type": "in-store",
            "source": source,
        }
        yield {**base,
               "description": desc or upc or "Item",
               "tax_exempt": "",
               "order_type": otype,
               "discount_ref": ""}

        # A per-line saving becomes its own nested 'discount' row so the UI can
        # group it directly under the item it applies to.
        if inline_discount > 0:
            yield {**base,
                   "description": f"Savings → {desc}" if desc else "Savings",
                   "unit_qty": 1,
                   "unit_price": -inline_discount,
                   "amount": -inline_discount,
                   "tax_flag": "",  # a savings line carries no tax code
                   "tax_exempt": "",
                   "order_type": "discount",
                   "discount_ref": upc}


def is_placeholder(record: dict) -> bool:
    """True if a receipt's detail hasn't fully published yet.

    Publix serves same-day / very-recent receipts with line items but no product
    catalog, so every line falls back to its generic ItemTypeDescription
    ("Normal Sale") with no real product name. We treat "has line items but not a
    single named product" as a not-yet-ready placeholder that should be dropped
    and re-imported once Publix publishes the real itemized receipt (24-48h).
    """
    lines = record.get("ReceiptLineItems") or []
    if not lines:
        return False
    # Email receipts are complete text receipts with no product catalog — the
    # line items carry their own register descriptions, so never a placeholder.
    if record.get("Source") == "email":
        return False
    products = record.get("Products") or []
    has_named_product = any((p.get("ItemName") or "").strip() for p in products)
    return not has_named_product


def _receipt_key(r: dict) -> str:
    """Identity of a receipt, so the same one ingested twice collapses."""
    rid = (r.get("ReceiptId") or r.get("TransactionKey") or "").strip()
    if rid:
        return rid
    return "|".join(str(r.get(k, "")) for k in (
        "TransactionDate", "FacilityId", "SalesTransactionNumber", "Amount"))


def _load_receipts(raw_dir: Path) -> list[dict]:
    """Load raw receipts, deduplicated by receipt identity."""
    by_key: dict[str, dict] = {}
    for f in sorted(raw_dir.glob("*.json")):
        try:
            r = json.loads(f.read_text())
        except Exception:
            continue
        by_key.setdefault(_receipt_key(r), r)  # first wins; dupes ignored
    return list(by_key.values())


def receipt_totals(r: dict) -> dict:
    """Header-level money for one receipt."""
    total = _num(r.get("GrandTotal") or r.get("Amount"))
    tax = _num(r.get("TaxAmount"))
    # Total savings = every line's SavingAmount (vs the regular price) — this is
    # Publix's "Your Savings at Publix" figure, and covers both BOGO promotions
    # and special prices. Fall back to the coupon fields if lines lack it.
    line_savings = round(sum(_num(li.get("SavingAmount"))
                             for li in r.get("ReceiptLineItems") or []), 2)
    savings = line_savings or round(
        _num(r.get("VendorCouponAmount")) + _num(r.get("StoreCouponAmount")), 2)
    subtotal = round(total - tax, 2)
    items = r.get("ItemCount") or len(r.get("ReceiptLineItems") or [])
    return {"subtotal": subtotal, "taxes": tax, "total": total,
            "instant_savings": savings, "items": items}


FIELDS = [
    "date", "item_number", "description", "original_description",
    "unit_qty", "unit_price",
    "amount", "department", "tax_flag", "tax_exempt", "store",
    "store_number", "receipt_id", "doc_type", "order_type",
    "discount_ref", "source",
]


def _desc_score(desc) -> tuple[int, int]:
    """How 'complete' a description is: (word count, length). A catalog name like
    'Jennie-O 99%/1% Fresh Ground Turkey Breast (16 oz (1 lb))' outscores the
    truncated register text 'J/O 99% Gr Turkey Breas'."""
    s = str(desc or "").strip()
    return (len(re.findall(r"[A-Za-z]{2,}", s)), len(s))


def _display_descriptions(line_items) -> dict[str, str]:
    """item_number -> the most complete description seen for it (across every
    receipt line plus the admin map). Used to rewrite abbreviated lines to the
    fuller name so an item reads and searches consistently."""
    best: dict[str, tuple] = {}

    def consider(num, desc) -> None:
        num = str(num or "").strip()
        desc = _clean_desc(desc)
        if not num or not desc:
            return
        score = _desc_score(desc)
        cur = best.get(num)
        if cur is None or score > cur[0]:
            best[num] = (score, desc)

    for it in line_items:
        if it.get("order_type") == "discount":
            continue  # discount rows carry a derived "Savings → …" label
        consider(it.get("item_number"), it.get("description"))
    from . import item_map
    for e in item_map.entries():
        consider(e.get("item_number"), e.get("description"))
    result = {num: v[1] for num, v in best.items()}
    # An admin-set name wins over the automatic pick for that item number.
    from . import item_names
    for num, name in item_names.names().items():
        result[num] = _clean_desc(name)
    return result

# Size/unit tokens ignored when matching descriptions across sources (a register
# name like "AB MILK U ORG 96OZ" vs a catalog name), so quantities don't block a
# match and word order doesn't matter.
_UNIT_TOKENS = {"oz", "lb", "lbs", "ct", "cnt", "count", "ea", "each", "pk", "pack",
                "gal", "ml", "l", "g", "kg", "fl", "qt", "pt", "in", "pc", "pcs"}


def _norm_desc(desc) -> str | None:
    """A normalized token-set key for a description, or None if there's nothing
    meaningful to match on.

    Lowercases, keeps word tokens (drops pure numbers and size/unit tokens like
    "96oz"), and sorts them — so "TOMATO BEEFSTEAK" and "Beefsteak Tomato" share
    a key. Single-word items (BANANAS, MILK) are kept; token-sets are exact, so
    "bananas" and "bananas organic" stay distinct and never collide."""
    toks = re.findall(r"[a-z0-9]+", str(desc or "").lower())
    keep = [t for t in toks
            if not t.isdigit() and t not in _UNIT_TOKENS
            and not re.fullmatch(r"\d+[a-z]{1,3}", t)]
    key = " ".join(sorted(keep))
    return key if len(key) >= 2 else None


def build_number_index(line_items, include_manual: bool = True) -> dict[str, str]:
    """Map normalized description -> item_number.

    Built from lines that HAVE a number (real receipt data) plus the admin's
    central item-number map. Real receipt data wins on conflicts; the manual map
    fills descriptions the receipts don't cover. Ambiguous auto keys (one
    description → multiple different numbers) are dropped so we never guess."""
    auto: dict[str, str] = {}
    ambiguous: set[str] = set()
    for it in line_items:
        num = str(it.get("item_number") or "").strip()
        if not num or it.get("order_type") == "discount":
            continue
        key = _norm_desc(it.get("description"))
        if not key:
            continue
        if key in auto and auto[key] != num:
            ambiguous.add(key)
        else:
            auto[key] = num
    for k in ambiguous:
        auto.pop(k, None)
    if include_manual:
        from . import item_map
        return {**item_map.index(), **auto}  # real receipt data overrides manual
    return auto


def backfill_item_numbers(raw_dir: Path = config.RAW_DIR) -> dict:
    """Persist matched item numbers into records that lack them (email receipts).

    Builds the description→number index from every receipt, then writes a matched
    number into any unnumbered line whose description matches. Idempotent; returns
    how many lines were filled."""
    import json as _json
    records = []
    all_items = []
    for f in sorted(Path(raw_dir).glob("*.json")):
        try:
            rec = _json.loads(f.read_text())
        except Exception:
            continue
        records.append((f, rec))
        all_items.extend(_iter_line_items(rec))
    index = build_number_index(all_items)
    filled = 0
    for f, rec in records:
        prods = _product_index(rec)
        changed = False
        for li in rec.get("ReceiptLineItems") or []:
            if _strip_upc(li.get("ItemCode")):
                continue  # already numbered
            desc = product_description(
                prods.get(_strip_upc(li.get("ItemCode"))),
                fallback=str(li.get("ItemTypeDescription") or "").strip())
            key = _norm_desc(desc)
            if key and key in index:
                li["ItemCode"] = index[key]
                changed = True
                filled += 1
        if changed:
            f.write_text(_json.dumps(rec, indent=2))
    return {"filled": filled}


def parse_all(
    raw_dir: Path = config.RAW_DIR,
    output_dir: Path = config.OUTPUT_DIR,
) -> dict:
    config.ensure_dirs()
    receipts = _load_receipts(raw_dir)

    line_items: list[dict] = []
    for r in receipts:
        line_items.extend(_iter_line_items(r))

    # Fill missing item numbers (email receipts carry none) by matching their
    # description to items with a known number.
    index = build_number_index(line_items)
    for it in line_items:
        if not it["item_number"]:
            key = _norm_desc(it["description"])
            if key and key in index:
                it["item_number"] = index[key]

    # Unify descriptions: once an item number is known, rewrite each line to the
    # most complete description recorded for that number (usually the web-import
    # catalog name), keeping the line's own register text in
    # `original_description` so nothing is lost and it stays searchable.
    display = _display_descriptions(line_items)
    for it in line_items:
        it["original_description"] = it["description"]
        num = str(it.get("item_number") or "").strip()
        disp = display.get(num) if num else None
        if disp and disp != it["description"]:
            if it.get("order_type") == "discount":
                it["description"] = f"Savings → {disp}"
            else:
                it["description"] = disp

    line_items.sort(key=lambda x: (x["date"], x["receipt_id"], x["item_number"]), reverse=True)

    # --- line_items.csv ---
    li_path = output_dir / "line_items.csv"
    with li_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(line_items)

    # --- items_deduped.csv : aggregate by item ---
    # total_spent is the NET spend: a discount row (negative amount) nets against
    # the item it belongs to, so a free BOGO item aggregates to $0 spent — but
    # still counts as one purchase at its regular unit price.
    agg: dict[str, dict] = {}
    for it in line_items:
        num = it["item_number"] or f"NONUM::{it['description']}"
        a = agg.get(num)
        if a is None:
            a = agg[num] = {
                "item_number": it["item_number"],
                "description": "" if it["order_type"] == "discount" else it["description"],
                "times_purchased": 0,
                "total_qty": 0.0,
                "total_spent": 0.0,
                "first_purchase": it["date"],
                "last_purchase": it["date"],
                "last_price": it["unit_price"] or it["amount"],
            }
        a["total_spent"] = round(a["total_spent"] + it["amount"], 2)
        if it["order_type"] == "discount":
            continue  # netted above; not a distinct purchase
        a["times_purchased"] += 1
        a["total_qty"] = round(a["total_qty"] + (it["unit_qty"] or 1), 3)
        if it["date"] and it["date"] < a["first_purchase"]:
            a["first_purchase"] = it["date"]
        if it["date"] and it["date"] >= a["last_purchase"]:
            a["last_purchase"] = it["date"]
            a["last_price"] = it["unit_price"] or it["amount"]
        if it["description"] and not a["description"]:
            a["description"] = it["description"]

    agg_rows = sorted(agg.values(), key=lambda x: x["last_purchase"], reverse=True)
    agg_path = output_dir / "items_deduped.csv"
    with agg_path.open("w", newline="") as fh:
        cols = [
            "item_number", "description", "times_purchased", "total_qty",
            "total_spent", "last_price", "first_purchase", "last_purchase",
        ]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(agg_rows)

    # --- receipts.csv : header-level ---
    rec_rows = []
    for r in receipts:
        t = receipt_totals(r)
        rec_rows.append(
            {
                "date": _receipt_date(r),
                "store": store_name(r),
                "doc_type": "in-store",
                "items": t["items"],
                "subtotal": t["subtotal"],
                "taxes": t["taxes"],
                "total": t["total"],
                "instant_savings": t["instant_savings"],
                "receipt_id": r.get("ReceiptId") or "",
            }
        )
    rec_rows.sort(key=lambda x: x["date"], reverse=True)
    rec_path = output_dir / "receipts.csv"
    with rec_path.open("w", newline="") as fh:
        cols = [
            "date", "store", "doc_type", "items", "subtotal",
            "taxes", "total", "instant_savings", "receipt_id",
        ]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rec_rows)

    summary = {
        "receipts_parsed": len(receipts),
        "line_items": len(line_items),
        "unique_items": len(agg_rows),
        "total_spent": round(sum(x["total_spent"] for x in agg_rows), 2),
        "outputs": {
            "line_items": str(li_path),
            "items_deduped": str(agg_path),
            "receipts": str(rec_path),
        },
    }
    (output_dir / "parse_summary.json").write_text(json.dumps(summary, indent=2))
    return summary
