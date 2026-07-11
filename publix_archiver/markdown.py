"""Post-process receipts into a browsable Markdown archive.

Produces, under data/output/markdown/:
  index.md            - all receipts, newest first: date, store, total, item
                        count, each linking to its own page.
  receipts/<id>.md    - one page per receipt: metadata, an item table (each line
                        linking to the product on publix.com), totals, tenders,
                        the embedded barcode, and the printed receipt text.

Publix gives us a real product-detail URL (ItemDetailUrl) and a ready-made
barcode image (BarcodeSrc), so links point at the actual catalog and no barcode
generation is needed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config
from .parse import (STORE_KINDS, _load_receipts, _receipt_date, _num, order_type,
                    product_description, line_quantity, receipt_totals, store_name,
                    tax_code_label, item_tax_codes, line_amounts, _strip_upc,
                    _product_index)

_TYPE_ICON = {"store": "🛒 Store", "pharmacy": "💊 Pharmacy",
              "greenwise": "🌿 GreenWise", "discount": "🏷 Discount"}

_PUBLIX = "https://www.publix.com"


def _money(v) -> str:
    return f"${_num(v):,.2f}"


def _safe(receipt: dict) -> str:
    key = (receipt.get("ReceiptId") or receipt.get("TransactionKey")
           or "-".join(str(receipt.get(k, "")) for k in ("TransactionDate", "FacilityId")))
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(key)) or "receipt"


def _decode_receipt_text(text: str) -> str:
    if not text:
        return ""
    return text.replace("&#10;", "\n").replace("\\n", "\n")


def _product_link(prod: dict | None, desc: str) -> str:
    """A markdown link pointing at the product's publix.com detail page."""
    url = str((prod or {}).get("ItemDetailUrl") or "").strip()
    if url:
        if url.startswith("/"):
            url = _PUBLIX + url
        return f"[detail]({url})"
    return ""


def _receipt_page(r: dict) -> str:
    date = _receipt_date(r)
    store = store_name(r)
    store_no = str(r.get("FacilityId") or "").strip()
    kind = STORE_KINDS.get(r.get("ReceiptLogoId"), "Publix")
    otype = order_type(r)
    totals = receipt_totals(r)
    prods = _product_index(r)
    rid = str(r.get("ReceiptId") or "")

    lines = [
        f"# {kind} Receipt — {store}",
        "",
        "[← Back to index](../index.md)",
        "",
    ]
    from .barcode_util import barcode_for
    barcode = barcode_for(r)   # API image, or generated from ReceiptId (email)
    if barcode.startswith("data:"):
        lines += [f'<img src="{barcode}" alt="receipt barcode" height="56">', ""]

    lines += [
        f"- **Type:** {_TYPE_ICON.get(otype, otype)}",
        f"- **Date:** {r.get('TransactionDate') or date}",
        f"- **Store:** {store}" + (f" (#{store_no})" if store_no else ""),
    ]
    if rid:
        lines.append(f"- **Receipt ID:** `{rid}`")
    lines += [
        f"- **Items:** {totals['items']}",
        "",
        "| Item | Qty | Unit price | Amount | Code | Detail |",
        "|---|--:|--:|--:|:-:|---|",
    ]

    line_tax = item_tax_codes(r)  # per-item tax/benefit letter from ReceiptText
    for i, li in enumerate(r.get("ReceiptLineItems") or []):
        upc = _strip_upc(li.get("ItemCode"))
        prod = prods.get(upc)
        desc = product_description(prod, fallback=str(li.get("ItemTypeDescription") or "").strip())
        qty = line_quantity(li)
        qty_s = f"{qty:g}"
        amount = _money(li.get("ItemAmount"))
        detail = _product_link(prod, desc)
        code = line_tax[i] if i < len(line_tax) else ""
        lines.append(f"| {desc or upc or 'Item'} | {qty_s} | {_money(li.get('ItemPrice'))} | {amount} | {code} | {detail} |")
        _printed, inline_discount, _paid = line_amounts(li)
        if inline_discount > 0:
            lines.append(f"| ↳ Savings | | | -{_money(inline_discount)} | | |")

    lines += [
        "",
        "| | | |",
        "|---|---|--:|",
        f"| | **Subtotal** | {_money(totals['subtotal'])} |",
        f"| | **Tax** | {_money(totals['taxes'])} |",
        f"| | **Total** | {_money(totals['total'])} |",
    ]
    if _num(totals["instant_savings"]):
        lines.append(f"| | **Savings** | {_money(totals['instant_savings'])} |")

    tenders = r.get("ReceiptTenderLineItems") or []
    if tenders:
        lines += ["", "**Payment**", ""]
        for t in tenders:
            label = str(t.get("TenderNumberDescription") or "Payment")
            lines.append(f"- {label}: {_money(t.get('TenderAmount'))}")

    # Legend for any per-line tax/benefit letters that appear in the printed text.
    from .email_ingest import strip_email_cruft
    rtext = strip_email_cruft(_decode_receipt_text(str(r.get("ReceiptText") or "")))
    codes = {}
    for c in ("t", "T", "M", "L", "F", "P", "H"):
        if re.search(rf"(?m)\s{c}\s*$", rtext):
            codes[c] = tax_code_label(c)
    legend = [f"**{c}** = {lbl}" for c, lbl in codes.items() if lbl]
    if legend:
        lines += ["", "*What these codes mean: " + " · ".join(legend) + "*"]

    if rtext.strip():
        lines += ["", "## Printed receipt", "", "```", rtext.rstrip(), "```"]

    # Link to the rendered PDF if it exists.
    if (config.PDF_DIR / f"{_safe(r)}.pdf").exists():
        lines += ["", f"[📄 PDF](../../pdfs/{_safe(r)}.pdf)"]

    lines.append("")
    return "\n".join(lines)


def generate_markdown(
    raw_dir: Path = config.RAW_DIR, output_dir: Path = config.OUTPUT_DIR
) -> dict:
    config.ensure_dirs()
    md_dir = output_dir / "markdown"
    pages_dir = md_dir / "receipts"
    pages_dir.mkdir(parents=True, exist_ok=True)

    receipts = _load_receipts(raw_dir)
    receipts.sort(key=lambda r: (_receipt_date(r), _num(r.get("GrandTotal") or r.get("Amount"))),
                  reverse=True)
    if not receipts:
        print("  No receipts found — run `fetch` or `import` first.")
        return {"receipts": 0, "dir": str(md_dir)}

    total_spent = round(sum(receipt_totals(r)["total"] for r in receipts), 2)
    total_items = sum(int(receipt_totals(r)["items"]) for r in receipts)
    dates = [_receipt_date(r) for r in receipts if _receipt_date(r)]

    idx = [
        "# Publix Purchases",
        "",
        f"**{len(receipts)}** receipts · **{total_items}** items · "
        f"**{_money(total_spent)}** total"
        + (f" · {dates[-1]} → {dates[0]}" if dates else ""),
        "",
        "All purchases, most recent first. Click a date to open the receipt.",
        "",
        "| Date | Store | Items | Total | Receipt |",
        "|---|---|--:|--:|---|",
    ]
    for r in receipts:
        name = _safe(r)
        date = _receipt_date(r)
        store = store_name(r)
        t = receipt_totals(r)
        idx.append(
            f"| [{date}](receipts/{name}.md) | {store} | {t['items']} "
            f"| {_money(t['total'])} | `{r.get('ReceiptId') or ''}` |")
    idx.append("")
    (md_dir / "index.md").write_text("\n".join(idx))

    for r in receipts:
        (pages_dir / f"{_safe(r)}.md").write_text(_receipt_page(r))

    print(f"  Wrote index.md + {len(receipts)} receipt pages → {md_dir}")
    return {"receipts": len(receipts), "index": str(md_dir / "index.md"),
            "pages_dir": str(pages_dir)}


def generate_one(receipt_key: str, raw_dir: Path = config.RAW_DIR,
                 output_dir: Path = config.OUTPUT_DIR) -> bool:
    """Regenerate a single receipt's Markdown page from its raw JSON."""
    src = raw_dir / f"{receipt_key}.json"
    if not src.exists():
        return False
    try:
        r = json.loads(src.read_text())
    except Exception:
        return False
    md_dir = output_dir / "markdown"
    pages_dir = md_dir / "receipts"
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / f"{_safe(r)}.md").write_text(_receipt_page(r))
    return True
