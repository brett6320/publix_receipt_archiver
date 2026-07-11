# Cloudflare email ingestion — setup

Forward your Publix receipt emails to a Cloudflare-routed address. An **Email
Worker** keeps only genuine Publix receipts, stores each raw `.eml` in **R2**,
and enqueues a reference on a **Queue**. The self-hosted archiver's `email-pull`
consumer pulls the queue, fetches the object from R2, ingests it, deletes the
object, and acks the message.

```
Publix receipt ──(you forward)──▶ you@your-domain (Cloudflare Email Routing)
        │
        ▼  Email Worker: filter → R2.put(raw .eml) → Queue.send({key})
   R2 bucket  ◀──────────────  Queue  ◀───────────────┘
        ▲                        │  (HTTP pull consumer)
        └──── archiver `email-pull` ── get object → ingest → delete object → ack
```

## 1. Prereqs
- A domain on Cloudflare with **Email Routing** enabled
  (Dashboard → your domain → Email → Email Routing).
- `npm i -g wrangler` and `wrangler login`.

## 2. Create the R2 bucket and the Queue
```bash
wrangler r2 bucket create publix-receipts
wrangler queues create publix-receipts

# REQUIRED: enable HTTP pull on the queue so the archiver can pull messages.
# Without this the pull endpoint returns 405 Method Not Allowed.
wrangler queues consumer http add publix-receipts
```
(Match these names in `email-worker/wrangler.toml`, or edit the toml.)

## 3. Deploy the Worker
```bash
cd cloudflare/email-worker
npm install
wrangler deploy
```

## 4. Route mail to the Worker
Dashboard → Email → Email Routing:
- **Option A (catch-all / address):** create an address like
  `receipts@your-domain` and set its action to **Send to a Worker →
  `publix-receipt-email`**.
- Then **forward** your Publix receipts to that address. (Publix sends receipts
  to your Publix account email; set up an auto-forward rule there, or forward
  manually.)

Non-Publix mail sent to that address is dropped by the Worker.

## 5. Create an API token for the archiver (pull consumer)
Dashboard → My Profile → API Tokens → **Create Token** with:
- **Account → Queues → Edit** — this grants `queues#read` + `queues#write`, both
  required to pull and ack messages.

(The queue must have the HTTP pull consumer enabled — see step 2 — or pulling
returns **405 Method Not Allowed**.)

Also create **R2 API credentials** (Access Key ID + Secret) for the S3 API:
Dashboard → R2 → **Manage R2 API Tokens** → create an **S3** token scoped to the
bucket.

## 6. Configure the archiver
Either in the **web UI** (admin → Email ingestion panel) or via env on the
`email-poller` container:

| Setting | Env var | Notes |
|---|---|---|
| R2 account id | `PUBLIX_R2_ACCOUNT_ID` | your Cloudflare account id |
| R2 endpoint | `PUBLIX_R2_ENDPOINT` | derived from account id if blank |
| R2 bucket | `PUBLIX_R2_BUCKET` | `publix-receipts` |
| R2 access key id | `PUBLIX_R2_ACCESS_KEY_ID` | from the R2 S3 token |
| R2 secret | `PUBLIX_R2_SECRET_ACCESS_KEY` | from the R2 S3 token |
| R2 prefix | `PUBLIX_R2_PREFIX` | `receipts/` (match the Worker) |
| Queue id | `PUBLIX_CF_QUEUE_ID` | Queues → your queue → id |
| API token | `PUBLIX_CF_API_TOKEN` | the Queues-scoped token |
| Poll interval | `PUBLIX_EMAIL_POLL_INTERVAL` | seconds (default 300) |

Then run the poller (or the `email-poller` service in `docker-compose.yml`):
```bash
python -m publix_archiver email-pull --loop          # every PUBLIX_EMAIL_POLL_INTERVAL
python -m publix_archiver email-pull --loop --interval 120
```
You can also trigger a one-off pull from the web UI (admin → Email ingestion →
**Poll now**).

## What the Worker filters
It discards anything that isn't a Publix receipt: it keeps a message only if the
content mentions Publix **and** either the subject is a Publix receipt or the
body has a Receipt ID and a total. The archiver re-validates and parses
server-side, so this is just a cheap first gate.
