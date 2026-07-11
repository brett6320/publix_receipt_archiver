"""Email ingestion: parse both Publix email templates, handle forwards, ignore
non-receipts, and pull from the Cloudflare queue (mocked)."""
import json
import tempfile
from email.message import EmailMessage
from pathlib import Path

from publix_archiver import email_ingest as E
from publix_archiver import parse as P
from publix_archiver import config


def _eml(subject, html, frm="Publix Super Markets <no-reply@exact.publix.com>"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, "me@example.com", subject
    m.set_content("(see HTML)")
    m.add_alternative(html, subtype="html")
    return m.as_bytes()


# Template A: amount then code, "Grand Total", labelled Receipt ID.
_TEMPLATE_A = """<div>Publix Super Markets, Inc.<br>Sample Plaza<br>123 Example Ave<br>
Sampletown, FL 00000<br>Store Manager: Test<br>
BANANAS 1.18 F<br>ALMOND MILK 3.50 F<br>You Saved 0.50<br>
TOMATO BEEFSTEAK<br>0.75 lb @ 2.99/ lb 2.24 F<br>
Order Total 6.92<br>Sales Tax 0.00<br>Grand Total 6.92<br>Credit Payment 6.92<br>
Receipt ID: 9999 A1B 100 200<br>09/09/2020 12:00 S9999 R100 0200 C0700<br></div>"""

# Template B: code then amount, "Subtotal"/"Total", bare Receipt ID, 12h time.
_TEMPLATE_B = """<div>Publix Super Markets, Inc.<br>Sample Plaza<br>123 Example Ave<br>
Sampletown, FL 00000<br>(000) 000-0000<br>Store Manager: Test<br>
Milk Whole T 3.49<br>Wheat Bread F 2.50<br>
Subtotal 5.99<br>Sales Tax 7.5% - T 0.26<br>Total 6.25<br>Credit 6.25<br>
Publix Super Markets, Inc.<br>9999 X9X 111 222<br>09/10/2020 06:04PM<br></div>"""


def test_template_a():
    rec = E.parse_eml(_eml("Your Publix receipt.", _TEMPLATE_A))
    assert rec and rec["ReceiptId"] == "9999A1B100200"
    assert rec["Source"] == "email" and rec["FacilityName"] == "Sample Plaza"
    assert rec["FacilityId"] == 9999
    assert rec["TransactionDate"] == "2020-09-09T12:00:00"
    assert rec["GrandTotal"] == 6.92
    assert round(sum(i["ItemAmount"] for i in rec["ReceiptLineItems"]), 2) == 6.92
    milk = [i for i in rec["ReceiptLineItems"] if "ALMOND" in i["ItemTypeDescription"]][0]
    assert milk["SavingAmount"] == 0.50 and milk["TaxCode"] == "F"
    tom = [i for i in rec["ReceiptLineItems"] if "TOMATO" in i["ItemTypeDescription"]][0]
    assert tom["ItemWeight"] == 0.75 and tom["ItemAmount"] == 2.24


def test_template_b_code_before_amount_and_bare_id():
    rec = E.parse_eml(_eml("Your Publix receipt.", _TEMPLATE_B))
    assert rec and rec["ReceiptId"] == "9999X9X111222"
    assert rec["TransactionDate"] == "2020-09-10T18:04:00"   # PM -> 18h
    assert rec["GrandTotal"] == 6.25 and rec["TaxAmount"] == 0.26
    assert round(sum(i["ItemAmount"] for i in rec["ReceiptLineItems"]), 2) == 5.99
    codes = {i["ItemTypeDescription"].split()[0]: i["TaxCode"] for i in rec["ReceiptLineItems"]}
    assert codes["Milk"] == "T" and codes["Wheat"] == "F"


def test_forwarded_receipt_is_still_parsed():
    # A forward: From/Subject are the forwarder's, content is the receipt.
    raw = _eml("Fwd: Your Publix receipt.", _TEMPLATE_A, frm="Me <me@example.com>")
    rec = E.parse_eml(raw)
    assert rec and rec["ReceiptId"] == "9999A1B100200"


def test_non_receipt_ignored():
    ad = "<div>Publix Super Markets weekly ad — great deals this week! Save big.</div>"
    assert E.parse_eml(_eml("Weekly savings", ad)) is None
    # Even a plain non-Publix email is ignored.
    assert E.parse_eml(_eml("Hello", "<div>just a note, no receipt</div>",
                            frm="A <a@b.com>")) is None


def test_email_record_not_a_placeholder():
    rec = E.parse_eml(_eml("Your Publix receipt.", _TEMPLATE_A))
    assert P.is_placeholder(rec) is False   # email receipts have no catalog but are complete


def test_email_flows_through_parse_all():
    rec = E.parse_eml(_eml("Your Publix receipt.", _TEMPLATE_A))
    tmp = Path(tempfile.mkdtemp()); raw, out = tmp / "raw", tmp / "out"
    raw.mkdir(); out.mkdir()
    (raw / f"{rec['ReceiptId']}.json").write_text(json.dumps(rec))
    P.parse_all(raw_dir=raw, output_dir=out)
    import csv
    rows = list(csv.DictReader(open(out / "line_items.csv")))
    assert any(r["tax_flag"] == "F" and "BANANAS" in r["description"] for r in rows)


# ---- mocked Cloudflare queue pull -----------------------------------------

class _Resp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


class _FakeHttp:
    def __init__(self, messages):
        self._batches = [messages, []]  # first pull returns msgs, then empty
        self.acked = []
    def post(self, url, headers=None, json=None):
        if url.endswith("/pull"):
            batch = self._batches.pop(0) if self._batches else []
            return _Resp({"result": {"messages": batch}})
        if url.endswith("/ack"):
            self.acked += json.get("acks", [])
            return _Resp({"result": {}})
        return _Resp({})


class _FakeR2:
    def __init__(self, objects): self.objects = dict(objects); self.deleted = []
    def get_object(self, Bucket, Key):
        return {"Body": type("B", (), {"read": lambda s, k=Key: self.objects[k]})()}
    def delete_object(self, Bucket, Key): self.deleted.append(Key); self.objects.pop(Key, None)


def test_pull_from_queue_ingests_deletes_and_acks():
    raw = _eml("Your Publix receipt.", _TEMPLATE_A)
    http = _FakeHttp([{"lease_id": "L1", "body": {"key": "receipts/a.eml"}}])
    r2 = _FakeR2({"receipts/a.eml": raw})
    tmp = Path(tempfile.mkdtemp())
    summary = E.pull_from_queue(raw_dir=tmp, http=http, r2=r2)
    assert summary["saved"] == 1 and summary["deleted"] == 1
    assert r2.deleted == ["receipts/a.eml"]                 # object removed
    assert http.acked == [{"lease_id": "L1"}]               # message acked
    assert (tmp / "9999A1B100200.json").exists()


def test_email_settings_merge_and_secret_preserved():
    tmp = Path(tempfile.mkdtemp())
    orig = config.EMAIL_CONFIG_FILE
    config.EMAIL_CONFIG_FILE = tmp / "email_config.json"
    try:
        config.save_email_settings({"r2_bucket": "b", "cf_api_token": "secret", "poll_interval": "120"})
        s = config.email_settings()
        assert s["r2_bucket"] == "b" and s["cf_api_token"] == "secret" and s["poll_interval"] == 120
        # Blank secret leaves the stored value unchanged.
        config.save_email_settings({"r2_bucket": "b2", "cf_api_token": ""})
        s = config.email_settings()
        assert s["r2_bucket"] == "b2" and s["cf_api_token"] == "secret"
    finally:
        config.EMAIL_CONFIG_FILE = orig


if __name__ == "__main__":
    for fn in list(globals()):
        if fn.startswith("test_"):
            globals()[fn]()
    print("email tests OK")
