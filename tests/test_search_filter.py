"""Web search: the Code (tax/benefit letter) filter matches Publix codes,
case-sensitively, including multi-letter codes.
"""
from publix_archiver import web


def _rows():
    common = {"date": "2020-01-15", "store": "Sample Plaza", "store_number": "9999",
              "receipt_id": "R1", "order_type": "store", "amount": 1.0,
              "unit_qty": 1, "unit_price": 1.0, "item_number": "1",
              "tax_exempt": "", "discount_ref": "", "doc_type": "in-store",
              "department": "", "source": "publix"}
    return [
        {**common, "description": "Bananas", "tax_flag": "F"},   # SNAP eligible
        {**common, "description": "Soda", "tax_flag": "T"},      # Taxable
        {**common, "description": "Bread", "tax_flag": "t"},     # Food tax rate
        {**common, "description": "Beer", "tax_flag": "MT"},     # multiple + taxable
        {**common, "description": "Bag", "tax_flag": ""},        # no code
    ]


def _descs(code):
    res = web._search(_rows(), {"tax": [code]})
    return sorted(r["description"] for r in res["rows"])


def test_filter_by_code():
    assert _descs("F") == ["Bananas"]
    assert _descs("t") == ["Bread"]                 # lowercase != uppercase
    assert _descs("T") == ["Beer", "Soda"]          # membership matches "MT" too
    assert _descs("H") == []                         # none healthcare
    assert len(web._search(_rows(), {"tax": [""]})["rows"]) == 5  # All


if __name__ == "__main__":
    test_filter_by_code()
    print("search filter tests OK")
