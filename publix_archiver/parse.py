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
    """Human-readable meaning of a per-line tax/benefit letter ('' if none)."""
    code = str(code or "").strip()
    if not code:
        return ""
    return TAX_CODE_LABELS.get(code, f"Publix code {code}")


def _strip_upc(code) -> str:
    """Normalize an ItemCode ('00000000004799') to a bare UPC ('4799')."""
    s = re.sub(r"\D", "", str(code or ""))
    return s.lstrip("0") or s


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
        return fallback
    name = str(prod.get("ItemName") or "").strip()
    size = str(prod.get("SizeDescription") or "").strip()
    desc = str(prod.get("ItemDescription") or "").strip()
    out = name or desc or fallback
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


def _iter_line_items(receipt: dict, source: str = "publix") -> Iterable[dict]:
    date = _receipt_date(receipt)
    store = store_name(receipt)
    store_no = str(receipt.get("FacilityId") or "").strip()
    receipt_id = receipt.get("ReceiptId") or receipt.get("TransactionKey") or ""
    otype = order_type(receipt)
    prods = _product_index(receipt)

    for li in receipt.get("ReceiptLineItems") or []:
        upc = _strip_upc(li.get("ItemCode"))
        prod = prods.get(upc)
        desc = product_description(prod, fallback=str(li.get("ItemTypeDescription") or "").strip())
        amount = _num(li.get("NetAmount"))
        if not amount:
            amount = round(_num(li.get("ItemAmount")) - _num(li.get("SavingAmount")), 2)
        base = {
            "date": date,
            "item_number": upc,
            "unit_qty": line_quantity(li),
            "unit_price": _num(li.get("ItemPrice")),
            "amount": amount,
            "department": str(prod.get("RetailSubSectionNumber") or "").strip() if prod else "",
            "tax_flag": "",  # Publix prints benefit letters in ReceiptText, not per-line JSON
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
        saving = _num(li.get("SavingAmount"))
        if saving:
            yield {**base,
                   "description": f"Savings → {desc}" if desc else "Savings",
                   "unit_qty": 1,
                   "unit_price": -saving,
                   "amount": -saving,
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
    savings = round(_num(r.get("VendorCouponAmount")) + _num(r.get("StoreCouponAmount")), 2)
    subtotal = round(total - tax, 2)
    items = r.get("ItemCount") or len(r.get("ReceiptLineItems") or [])
    return {"subtotal": subtotal, "taxes": tax, "total": total,
            "instant_savings": savings, "items": items}


FIELDS = [
    "date", "item_number", "description", "unit_qty", "unit_price",
    "amount", "department", "tax_flag", "tax_exempt", "store",
    "store_number", "receipt_id", "doc_type", "order_type",
    "discount_ref", "source",
]


def parse_all(
    raw_dir: Path = config.RAW_DIR,
    output_dir: Path = config.OUTPUT_DIR,
) -> dict:
    config.ensure_dirs()
    receipts = _load_receipts(raw_dir)

    line_items: list[dict] = []
    for r in receipts:
        line_items.extend(_iter_line_items(r))

    line_items.sort(key=lambda x: (x["date"], x["receipt_id"], x["item_number"]), reverse=True)

    # --- line_items.csv ---
    li_path = output_dir / "line_items.csv"
    with li_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(line_items)

    # --- items_deduped.csv : aggregate by item ---
    agg: dict[str, dict] = {}
    for it in line_items:
        if it["order_type"] == "discount":
            continue  # don't aggregate savings rows as products
        num = it["item_number"] or f"NONUM::{it['description']}"
        a = agg.setdefault(
            num,
            {
                "item_number": it["item_number"],
                "description": it["description"],
                "times_purchased": 0,
                "total_qty": 0.0,
                "total_spent": 0.0,
                "first_purchase": it["date"],
                "last_purchase": it["date"],
                "last_price": it["unit_price"] or it["amount"],
            },
        )
        a["times_purchased"] += 1
        a["total_qty"] = round(a["total_qty"] + (it["unit_qty"] or 1), 3)
        a["total_spent"] = round(a["total_spent"] + it["amount"], 2)
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
