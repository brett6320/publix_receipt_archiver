"""Generate a barcode when the source didn't supply one.

Publix's API returns a ready-made barcode PNG (``BarcodeSrc``), but email
receipts only carry the printed ``Receipt ID``. For those we render a Code 128
barcode of the receipt id as an SVG data-URI (pure Python via ``python-barcode``;
no Pillow), so the PDF/Markdown can embed it exactly like the API's barcode.
"""
from __future__ import annotations

import base64
import io


def barcode_data_uri(text) -> str:
    """A ``data:image/svg+xml;base64,...`` Code 128 barcode for ``text``.

    Returns "" if the text is empty or python-barcode isn't installed — callers
    fall back to no barcode rather than failing.
    """
    text = str(text or "").strip()
    if not text:
        return ""
    try:
        from barcode import Code128
        from barcode.writer import SVGWriter
    except Exception:
        return ""
    try:
        buf = io.BytesIO()
        Code128(text, writer=SVGWriter()).write(
            buf,
            options={"module_height": 12.0, "font_size": 8, "text_distance": 3.0,
                     "quiet_zone": 4.0},
        )
        return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def barcode_for(record: dict) -> str:
    """The record's own barcode if present, else a generated one from ReceiptId."""
    src = str(record.get("BarcodeSrc") or "").strip()
    if src.startswith("data:"):
        return src
    return barcode_data_uri(record.get("ReceiptId"))
