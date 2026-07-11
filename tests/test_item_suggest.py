"""Item-number suggestions: search known items by shared-word overlap."""
from publix_archiver import web


def _rows():
    def row(num, desc, ot="store"):
        return {"item_number": num, "description": desc, "order_type": ot}
    return [
        row("4011", "Bananas"),
        row("94011", "Organic Bananas"),
        row("123", "Whole Milk Gallon"),
        row("", "BANANAS"),                # no number — a source with nothing to offer
        row("9", "Savings", "discount"),   # discount line — ignored
    ]


def test_suggestions_rank_and_filter():
    s = web._item_suggestions(_rows(), "BANANAS")
    nums = [x["item_number"] for x in s]
    assert "4011" in nums and "94011" in nums    # both banana items suggested
    assert "123" not in nums                      # milk doesn't share a word
    assert s[0]["item_number"] == "4011"          # exact single-word match ranks first


def test_no_query_or_no_match():
    assert web._item_suggestions(_rows(), "") == []
    assert web._item_suggestions(_rows(), "Xyzzy Nonexistent") == []


if __name__ == "__main__":
    test_suggestions_rank_and_filter()
    test_no_query_or_no_match()
    print("suggest tests OK")
