"""Render each downloaded Publix receipt (raw JSON) into a PDF archive.

We build a clean, printable HTML receipt from each raw JSON record and convert it
to PDF with the headless Chromium already installed for Playwright. This runs
fully locally: no login, no bot-detection. Publix supplies a ready-made barcode
image (BarcodeSrc data-URI) and the full printed receipt text (ReceiptText), so
we embed both directly — no barcode generation needed.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import config
from .parse import (STORE_KINDS, order_type, product_description, line_quantity,
                    receipt_totals, store_name, tax_code_label, item_tax_codes,
                    _num, _receipt_date, _strip_upc, _product_index)

# Chromium stamps each PDF with a wall-clock /CreationDate and /ModDate, so two
# renders of identical content differ only in those bytes. Blank them out before
# comparing, so we detect *real* changes (content or template) and skip only
# when the receipt is truly unchanged.
_PDF_DATE_RE = re.compile(rb"/(CreationDate|ModDate)\s*\(D:[^)]*\)")


def _normalized_pdf(data: bytes) -> bytes:
    return _PDF_DATE_RE.sub(rb"/\1 (D:00000000000000)", data)


def _write_if_changed(out: Path, data: bytes, force: bool = False) -> bool:
    """Write `data` to `out` unless an identical PDF already exists there.

    Returns True if the file was written, False if skipped as unchanged.
    "Identical" ignores only Chromium's volatile date stamps, so any content or
    template change is picked up and overwrites the old file.
    """
    if not force and out.exists():
        try:
            if _normalized_pdf(out.read_bytes()) == _normalized_pdf(data):
                return False
        except OSError:
            pass
    out.write_bytes(data)
    return True


def _fmt_money(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v or "")


def _decode_receipt_text(text: str) -> str:
    """Publix's ReceiptText escapes newlines as literal \\n and/or &#10; entities."""
    if not text:
        return ""
    return (text.replace("&#10;", "\n").replace("\\n", "\n"))


def _receipt_html(r: dict) -> str:
    date = str(r.get("TransactionDate") or "")[:19] or _receipt_date(r)
    store = store_name(r)
    store_no = str(r.get("FacilityId") or "").strip()
    kind = STORE_KINDS.get(r.get("ReceiptLogoId"), "Publix")
    otype = order_type(r)
    prods = _product_index(r)
    totals = receipt_totals(r)

    rows = []
    tax_codes: dict[str, str] = {}
    line_tax = item_tax_codes(r)  # per-item tax/benefit letter from ReceiptText
    for i, li in enumerate(r.get("ReceiptLineItems") or []):
        upc = _strip_upc(li.get("ItemCode"))
        prod = prods.get(upc)
        desc = product_description(prod, fallback=str(li.get("ItemTypeDescription") or "").strip())
        qty = line_quantity(li)
        qty_s = f"{qty:g}"
        amount = _num(li.get("ItemAmount"))
        code = line_tax[i] if i < len(line_tax) else ""
        if code:
            tax_codes[code] = tax_code_label(code)
        code_cell = (f"<abbr title='{html.escape(tax_code_label(code), quote=True)}'>"
                     f"{html.escape(code)}</abbr>" if code else "")
        rows.append(
            "<tr>"
            f"<td>{html.escape(desc or upc or 'Item')}</td>"
            f"<td class='r'>{html.escape(qty_s)}</td>"
            f"<td class='r'>{_fmt_money(li.get('ItemPrice'))}</td>"
            f"<td class='r'>{_fmt_money(amount)}</td>"
            f"<td class='c'>{code_cell}</td>"
            "</tr>"
        )
        saving = _num(li.get("SavingAmount"))
        if saving:
            rows.append(
                "<tr class='disc'>"
                f"<td>↳ Savings</td>"
                f"<td class='r'></td><td class='r'></td>"
                f"<td class='r'>-{_fmt_money(saving)}</td>"
                f"<td class='c'></td>"
                "</tr>"
            )

    body_rows = "\n".join(rows) or "<tr><td colspan=5>No line items</td></tr>"

    # Tenders (how it was paid).
    tender_rows = []
    for t in r.get("ReceiptTenderLineItems") or []:
        label = html.escape(str(t.get("TenderNumberDescription") or "Payment"))
        tender_rows.append(
            f"<tr><td colspan=3 class='r'>{label}</td>"
            f"<td class='r'>{_fmt_money(t.get('TenderAmount'))}</td><td class='c'></td></tr>"
        )
    tender_block = ("<tr class='sec'><td colspan=5>Payment</td></tr>" + "".join(tender_rows)
                    if tender_rows else "")

    # Ready-made barcode image (data-URI) — embed directly.
    barcode = str(r.get("BarcodeSrc") or "").strip()
    barcode_block = (f"<div class='barcode'><img src='{html.escape(barcode, quote=True)}'"
                     f" alt='receipt barcode'></div>" if barcode.startswith("data:") else "")
    rid = str(r.get("ReceiptId") or "")
    rid_block = f"<div class='rid'>Receipt ID: {html.escape(rid)}</div>" if rid else ""

    # Authentic printed-receipt facsimile.
    rtext = _decode_receipt_text(str(r.get("ReceiptText") or ""))
    facsimile = (f"<h2>Printed receipt</h2><pre class='facsimile'>{html.escape(rtext)}</pre>"
                 if rtext.strip() else "")

    # Tax-letter legend — spell out only the single-letter codes that appear on
    # this receipt's items (tax_codes was filled while building the rows above).
    present = set("".join(tax_codes.keys()))
    legend_items = [f"<b>{c}</b> = {html.escape(tax_code_label(c))}"
                    for c in ("t", "T", "M", "L", "F", "P", "H") if c in present]
    tax_legend = (f"<div class='legend'>What these codes mean: {' · '.join(legend_items)}</div>"
                  if legend_items else "")

    return f"""<!doctype html><html><head><meta charset='utf-8'><style>
      body {{ font-family: -apple-system, Arial, sans-serif; margin: 24px; color:#111; }}
      h1 {{ font-size: 18px; margin:0 0 2px; }}
      h2 {{ font-size: 13px; margin:20px 0 4px; }}
      .meta {{ color:#555; font-size:12px; margin-bottom:12px; }}
      table {{ width:100%; border-collapse:collapse; font-size:12px; }}
      th,td {{ border-bottom:1px solid #ddd; padding:4px 6px; text-align:left; }}
      td.r, th.r {{ text-align:right; }}
      td.c, th.c {{ text-align:center; }}
      abbr {{ text-decoration:none; border-bottom:1px dotted #999; cursor:help; }}
      tr.disc td {{ color:#666; font-style:italic; border-bottom:1px solid #f0f0f0; }}
      tr.sec td {{ font-weight:bold; border-top:2px solid #333; padding-top:8px; }}
      tfoot td {{ font-weight:bold; border-top:2px solid #333; }}
      .barcode {{ margin:10px 0; }}
      .barcode img {{ max-width:280px; height:auto; }}
      .rid {{ font-size:14px; font-weight:700; letter-spacing:.4px; margin-top:8px; }}
      .legend {{ color:#555; font-size:11px; margin-top:8px; }}
      pre.facsimile {{ font-family:monospace; font-size:10px; color:#333;
        white-space:pre-wrap; border:1px solid #eee; padding:8px; }}
    </style></head><body>
      <h1>{html.escape(kind)} Receipt — {html.escape(store)}</h1>
      <div class='meta'>{html.escape(date)}
        &nbsp;•&nbsp; Store #{html.escape(store_no)}
        &nbsp;•&nbsp; {html.escape(otype)}</div>
      {rid_block}
      {barcode_block}
      <table>
        <thead><tr><th>Item</th><th class='r'>Qty</th><th class='r'>Unit price</th><th class='r'>Amount</th><th class='c'>Code</th></tr></thead>
        <tbody>{body_rows}</tbody>
        <tfoot>
          <tr><td colspan=3 class='r'>Subtotal</td><td class='r'>{_fmt_money(totals['subtotal'])}</td><td class='c'></td></tr>
          <tr><td colspan=3 class='r'>Tax</td><td class='r'>{_fmt_money(totals['taxes'])}</td><td class='c'></td></tr>
          <tr><td colspan=3 class='r'>Total</td><td class='r'>{_fmt_money(totals['total'])}</td><td class='c'></td></tr>
          <tr><td colspan=3 class='r'>Savings</td><td class='r'>{_fmt_money(totals['instant_savings'])}</td><td class='c'></td></tr>
          {tender_block}
        </tfoot>
      </table>
      {tax_legend}
      {facsimile}
    </body></html>"""


def render_all_pdfs(
    raw_dir: Path = config.RAW_DIR, pdf_dir: Path = config.PDF_DIR, force: bool = False
) -> dict:
    """Render every raw receipt JSON to data/pdfs/<receipt>.pdf.

    Always re-renders from the current template and overwrites when the result
    differs from the existing file (content or template change). A file is left
    untouched only when the freshly-rendered PDF is identical to it. Pass
    force=True to rewrite every file regardless.
    """
    config.ensure_dirs()
    files = sorted(raw_dir.glob("*.json"))
    if not files:
        print("  No raw receipts found — run `fetch` first.")
        return {"rendered": 0, "pdf_dir": str(pdf_dir)}

    written, unchanged = 0, 0
    with sync_playwright() as p:
        # --no-sandbox lets headless Chromium run as root inside Docker.
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()
        for f in files:
            out = pdf_dir / f"{f.stem}.pdf"
            try:
                r = json.loads(f.read_text())
            except Exception:
                continue
            page.set_content(_receipt_html(r), wait_until="load")
            data = page.pdf(format="Letter",
                            margin={"top": "0.4in", "bottom": "0.4in",
                                    "left": "0.4in", "right": "0.4in"})
            if _write_if_changed(out, data, force=force):
                written += 1
            else:
                unchanged += 1
        browser.close()

    print(f"  Wrote {written} PDF(s), {unchanged} unchanged → {pdf_dir}")
    return {"rendered": written, "unchanged": unchanged,
            "total_pdfs": len(list(pdf_dir.glob('*.pdf'))), "pdf_dir": str(pdf_dir)}


def render_one_pdf(receipt_key: str, raw_dir: Path = config.RAW_DIR,
                   pdf_dir: Path = config.PDF_DIR) -> bool:
    """Render a single receipt's PDF from its raw JSON. Returns True on success.

    Overwrites the existing PDF only if the re-render differs from it.
    """
    config.ensure_dirs()
    src = raw_dir / f"{receipt_key}.json"
    if not src.exists():
        return False
    try:
        r = json.loads(src.read_text())
    except Exception:
        return False
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()
        page.set_content(_receipt_html(r), wait_until="load")
        data = page.pdf(format="Letter",
                        margin={"top": "0.4in", "bottom": "0.4in",
                                "left": "0.4in", "right": "0.4in"})
        browser.close()
    _write_if_changed(pdf_dir / f"{receipt_key}.pdf", data)
    return True
