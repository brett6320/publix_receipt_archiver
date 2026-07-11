// Cloudflare Email Worker for the Publix Receipt Archiver.
//
// Receives receipt emails *forwarded* to a Cloudflare Email Routing address,
// keeps only genuine Publix receipts (content-based, since forwarding rewrites
// the From/Subject), stores the raw .eml in R2, and enqueues a reference on a
// Cloudflare Queue. The self-hosted archiver is the queue's pull consumer.

const PUBLIX_RE = /publix/i;
// A Publix register receipt id: store(4) + 2-3 alnum + 3 digits + 3 digits.
const RECEIPT_ID_RE = /(Receipt ID:|\b\d{4}\s+[0-9A-Za-z]{2,3}\s+\d{3}\s+\d{3}\b)/;
const TOTAL_RE = /(Grand Total|(^|\n)\s*Total\s+\$?\d+\.\d{2})/i;

// Basic filter: discard anything that isn't a Publix receipt. The archiver
// re-validates and parses server-side, so this only needs to be a cheap gate.
function looksLikePublixReceipt(subject, from, rawText) {
  if (!PUBLIX_RE.test(`${from}\n${subject}\n${rawText}`)) return false;
  if (/publix receipt/i.test(subject)) return true; // "Fwd: Your Publix receipt."
  return RECEIPT_ID_RE.test(rawText) && TOTAL_RE.test(rawText);
}

export default {
  async email(message, env, ctx) {
    const raw = new Uint8Array(await new Response(message.raw).arrayBuffer());
    const rawText = new TextDecoder("utf-8", { fatal: false }).decode(raw);
    const subject = message.headers.get("subject") || "";
    const from = message.from || "";

    if (!looksLikePublixReceipt(subject, from, rawText)) {
      // Not a Publix receipt — drop silently (no store, no forward, no bounce).
      return;
    }

    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const rand = (crypto.randomUUID && crypto.randomUUID().slice(0, 8)) || `${Date.now()}`;
    const key = `${env.R2_PREFIX || "receipts/"}${stamp}-${rand}.eml`;

    await env.RECEIPTS_BUCKET.put(key, raw, {
      httpMetadata: { contentType: "message/rfc822" },
    });
    await env.RECEIPTS_QUEUE.send({ key });
  },
};
