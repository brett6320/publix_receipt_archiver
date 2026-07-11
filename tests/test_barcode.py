"""Barcode generation fallback for receipts that ship no barcode (email)."""
from publix_archiver import barcode_util as B
from publix_archiver import pdf, markdown


def _email_record():
    return {
        "ReceiptId": "1808B5Q710114", "Source": "email",
        "FacilityId": 1808, "FacilityName": "Sample Plaza",
        "TransactionDate": "2025-11-05T19:38:00", "GrandTotal": 3.79,
        "OrderTotal": 3.79, "TaxAmount": 0.0, "ReceiptText": "LETTUCE SHREDS 3.79 F",
        "Products": [], "ReceiptLineItems": [
            {"ItemCode": "", "ItemTypeDescription": "LETTUCE SHREDS", "TaxCode": "F",
             "ItemQty": 1, "ItemWeight": 0.0, "ItemPrice": 3.79, "ItemAmount": 3.79,
             "SavingAmount": 0.0, "NetAmount": 3.79}],
    }


def test_generate_data_uri():
    uri = B.barcode_data_uri("18087AR773135")
    assert uri.startswith("data:image/svg+xml;base64,")
    assert B.barcode_data_uri("") == ""


def test_barcode_for_prefers_existing_then_generates():
    # API record keeps its own image.
    assert B.barcode_for({"BarcodeSrc": "data:image/png;base64,AAAA"}) == "data:image/png;base64,AAAA"
    # Email record (no BarcodeSrc) generates one from the ReceiptId.
    assert B.barcode_for(_email_record()).startswith("data:image/svg+xml")


def test_pdf_and_markdown_embed_generated_barcode():
    rec = _email_record()
    assert "receipt barcode" in pdf._receipt_html(rec)          # <img ... alt='receipt barcode'>
    md = markdown._receipt_page(rec)
    assert "data:image/svg+xml" in md and "receipt barcode" in md


if __name__ == "__main__":
    test_generate_data_uri()
    test_barcode_for_prefers_existing_then_generates()
    test_pdf_and_markdown_embed_generated_barcode()
    print("barcode tests OK")
