# Quickstart — capture your Publix token and collect receipts

Publix has no public API. This tool replays the exact request your browser makes
to `services.publix.com/api/v1/PurchaseHistory`, using your logged-in session's
bearer token + customer id. The token lives **~1 hour**, so capture it fresh
right before you collect. Below is the fastest path: copy one request from your
browser and paste it into the **Collect** page.

---

## 1. Sign in to Publix

1. Go to **https://www.publix.com** and sign in with your **Club Publix** account.
2. Open **Account → Purchases** (your in-store purchase history).

## 2. Open DevTools → Network

| Browser | Open DevTools | Then |
|---|---|---|
| Chrome / Edge | `F12` or `Ctrl+Shift+I` (macOS `⌥⌘I`) | click the **Network** tab |
| Firefox | `F12` | click the **Network** tab |
| Safari | enable **Settings → Advanced → Show features for web developers**, then `⌥⌘I` | click the **Network** tab |

In the Network **filter box**, type:

```
PurchaseHistory
```

## 3. Trigger the request

Reload the Purchases page (`Ctrl`/`⌘` + `R`). A request to
`services.publix.com/api/v1/PurchaseHistory` appears in the list. Click it and
check the **Response** tab — you should see your purchases as JSON.

## 4. Copy as cURL

Right-click the **PurchaseHistory** request → **Copy** → **Copy as cURL**.

- **Chrome / Edge:** "Copy as cURL (bash)" on macOS/Linux, "Copy as cURL (cmd)"
  on Windows — either works.
- **Firefox:** **Copy Value → Copy as cURL**.
- **Safari:** **Copy as cURL**.

This puts a big `curl '...' -H '...'` command on your clipboard. It contains your
short-lived token and cookies — treat it like a password (see Privacy below).

## 5. Paste it into the Collect page

1. In the archiver web UI, open the **Collect** tab.
2. Paste the cURL into the text box under **"Capture credentials"**.
3. Click **Capture**. You'll see a confirmation and how many minutes the token
   is still valid.

## 6. Collect

Click **Start Collection**. The app pages through your history (newest first),
downloads each receipt's itemized detail, and builds the CSVs, PDFs, and
Markdown. Switch to the **Search** tab to browse.

---

## Troubleshooting

- **"Token expired" / collection returns 401** — the token is only good for
  ~1 hour. Re-copy a fresh cURL (steps 3–4) and **Capture** again.
- **No `PurchaseHistory` request appears** — make sure you're on **Account →
  Purchases** (not the orders/online page) and reload with the Network tab open.
- **Nothing new gets imported** — Publix publishes a receipt's itemized detail
  **24–48 h** after purchase, so same-day trips are deferred until the real
  receipt is ready. It also keeps only **~180 days** of history — run this
  regularly so your archive outlives that window.
- **A same-day receipt shows only "Normal Sale" rows** — that's an unpublished
  placeholder; it's dropped and re-imported automatically once Publix fills in
  the details.

## Privacy

The cURL contains your session **token and cookies**. It's stored locally under
`data/` and is short-lived — nothing leaves your machine except the calls to
Publix. If you ever paste the cURL somewhere for help, **redact the `Cookie:`
and `Authorization:` header values** first.

## Alternatives

- **Any authenticated publix.com request works** — the app can also read the
  token from the `AccessTokenJwt` cookie and the customer id from the `User`
  cookie — but the `PurchaseHistory` XHR is the most reliable capture.
- **No DevTools?** Run the browser-console snippet in
  [`browser/publix_fetch_receipts.js`](../browser/publix_fetch_receipts.js) on
  the Purchases page; it downloads a JSON file you can load with
  `python -m publix_archiver import <file>`.
