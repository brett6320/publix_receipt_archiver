"""strip_email_cruft: keep receipt lines, drop leaked email headers / DKIM /
encoded-words / base64 blobs and the trailing marketing footer."""
import json
import tempfile
from pathlib import Path

from publix_archiver.email_ingest import strip_email_cruft, repair_receipt_text


def test_strips_leaked_email_headers_and_blobs():
    raw = (
        "Publix Super Markets, Inc.\n"
        "Authentication-Results: mail.protonmail.ch;\n"
        "dkim=pass (2048-bit key) header.d=exact.publix.com header.i=@exact.publix.com\n"
        "X-Sfmc-Stack: 4\n"
        "X-Pm-Spam:\n"
        "0yezJI6YSpyJec91ztFGcjIwoJyLCvXBZcQniisnOERJt9TTVOUdSRQUiwslOjLdFJEL\n"
        "=?utf-8?q?CN0lSXZ1BElETjIbp?= =?utf-8?q?\n"
        "Gandy Shopping Center\n"
        "YELLOW TAIL CHARDO 5.75 T\n"
        "Total 753.45\n")
    out = strip_email_cruft(raw)
    for bad in ("Authentication-Results", "dkim=pass", "X-Sfmc", "X-Pm-Spam",
                "=?utf-8?q?", "0yezJI6YSpyJec91"):
        assert bad not in out
    for good in ("Publix Super Markets, Inc.", "Gandy Shopping Center",
                 "YELLOW TAIL CHARDO 5.75 T", "Total 753.45"):
        assert good in out


def test_cuts_marketing_footer_both_variants():
    a = "STORE TOTAL 5.00\nThis email was sent to: me@x. Please do not reply\nUnsubscribe\nCorporate Office"
    assert strip_email_cruft(a) == "STORE TOTAL 5.00"
    # 2021 variant: footer wraps mid-line, starts with "Please do not reply"
    b = ("Remember your reusable bags.\nPublix Super Markets, Inc.\n"
         "Please do not reply to this email as we are not able to respond. "
         "Unsubscribe from all marketing emails.\nContact us")
    out = strip_email_cruft(b)
    assert "Remember your reusable bags." in out and "Publix Super Markets, Inc." in out
    assert "Unsubscribe" not in out and "Please do not reply" not in out


def test_noop_on_clean_receipt():
    clean = ("Gandy Shopping Center\n3617 W. Gandy Blvd.\nGROUND SIRLOIN 5.28 F\n"
             "Total 12.34\nYour cashier was Vanessa J.")
    assert strip_email_cruft(clean) == clean


def test_length_backstop():
    huge = "GROUND SIRLOIN 5.28 F\n" * 2000   # ~44k chars, no footer/headers
    out = strip_email_cruft(huge)
    assert len(out) <= 12100 and out.endswith("…(truncated)")


def test_repair_rewrites_crufty_records_only():
    tmp = Path(tempfile.mkdtemp())
    crufty = {"ReceiptId": "C1", "ReceiptText":
              "Gandy Shopping Center\nTotal 5.00\nThis email was sent to: me@x\nUnsubscribe"}
    clean = {"ReceiptId": "C2", "ReceiptText": "Gandy Shopping Center\nTotal 9.99"}
    (tmp / "c1.json").write_text(json.dumps(crufty))
    (tmp / "c2.json").write_text(json.dumps(clean))

    res = repair_receipt_text(tmp)
    assert res["repaired"] == 1                     # only the crufty one rewritten
    r1 = json.loads((tmp / "c1.json").read_text())
    assert "This email was sent" not in r1["ReceiptText"] and "Total 5.00" in r1["ReceiptText"]
    r2 = json.loads((tmp / "c2.json").read_text())
    assert r2["ReceiptText"] == "Gandy Shopping Center\nTotal 9.99"   # untouched

    assert repair_receipt_text(tmp)["repaired"] == 0   # idempotent


if __name__ == "__main__":
    for fn in list(globals()):
        if fn.startswith("test_"):
            globals()[fn]()
    print("receipt-text tests OK")
