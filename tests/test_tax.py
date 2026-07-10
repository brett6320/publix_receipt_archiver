"""Per-item tax/benefit codes parsed from ReceiptText.

Publix returns no per-line tax field — the letter (t/T/M/L/F/P/H) is printed
only in ReceiptText — so we read it back out by matching each item's amount.
"""
from publix_archiver import parse as p


def _record():
    return {
        "ReceiptText": (
            "  TOMATO BEEFSTEAK\n"
            "  1.40 lb @     2.99/ lb        4.19   F \n"
            "  ALMOND MILK                     3.49   T \n"
            "  PLASTIC BAG                     0.10     \n"
            "    Order Total                  7.78     \n"
        ),
        "ReceiptLineItems": [
            {"ItemCode": "1", "ItemAmount": 4.19, "ItemPrice": 2.99,
             "ItemQty": 0, "ItemWeight": 1.4, "NetAmount": 4.19, "SavingAmount": 0.0},
            {"ItemCode": "2", "ItemAmount": 3.49, "ItemPrice": 3.49,
             "ItemQty": 1, "NetAmount": 3.49, "SavingAmount": 0.0},
            {"ItemCode": "3", "ItemAmount": 0.10, "ItemPrice": 0.10,
             "ItemQty": 1, "NetAmount": 0.10, "SavingAmount": 0.0},
        ],
        "Products": [],
    }


def test_item_tax_codes_from_receipt_text():
    # F (SNAP eligible), T (taxable), and a bare line with no code.
    assert p.item_tax_codes(_record()) == ["F", "T", ""]


def test_tax_flag_flows_into_line_items():
    rows = [r for r in p._iter_line_items(_record()) if r["order_type"] != "discount"]
    assert [r["tax_flag"] for r in rows] == ["F", "T", ""]


def test_tax_code_label():
    assert p.tax_code_label("F") == "SNAP eligible"
    assert p.tax_code_label("t") == "Food tax rate"
    assert p.tax_code_label("") == ""
    # Multi-letter code joins each meaning.
    assert p.tax_code_label("MT") == "Multiple tax plans, Taxable item"


if __name__ == "__main__":
    test_item_tax_codes_from_receipt_text()
    test_tax_flag_flows_into_line_items()
    test_tax_code_label()
    print("tax tests OK")
