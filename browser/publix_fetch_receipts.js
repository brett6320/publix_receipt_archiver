// Publix receipt bulk export — run this in your NORMAL browser's DevTools
// Console while logged into publix.com (Account → Purchases).
//
// Why this exists: it uses your own logged-in session to page through your
// purchase history and download one JSON file of full receipt records, which
// you then feed to the tool:
//
//     python -m publix_archiver import ~/Downloads/publix_receipts.json
//     python -m publix_archiver parse && python -m publix_archiver pdf && python -m publix_archiver markdown
//
// It reads the Bearer token from the `AccessTokenJwt` cookie and the customer id
// (EcmsId) from the `User` cookie, pages the purchase-history list, fetches each
// transaction's detail, dedupes by ReceiptId, and downloads publix_receipts.json.

(async () => {
  const LIST = 'https://services.publix.com/api/v1/PurchaseHistory';
  const DETAIL = 'https://services.publix.com/api/v1/PurchaseHistory/detail';
  const PAGE_SIZE = 50;      // rows per list page
  const MAX_PAGES = 20;      // safety cap; raise if you have more history
  const LIST_DELAY_MS = 400; // be polite to Akamai between list pages
  const DETAIL_DELAY_MS = 150;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // --- Read credentials from cookies --------------------------------------
  const cookies = Object.fromEntries(
    document.cookie.split(';').map((c) => {
      const i = c.indexOf('=');
      return [c.slice(0, i).trim(), decodeURIComponent(c.slice(i + 1))];
    })
  );
  const token = cookies['AccessTokenJwt'];
  let ecms = '';
  try {
    ecms = (JSON.parse(cookies['User'] || '{}').EcmsId || '').toString();
  } catch (e) { /* ignore */ }

  if (!token || !ecms) {
    console.error('Not logged in? AccessTokenJwt / User(EcmsId) cookies missing. ' +
      'Open Account → Purchases first, then re-run.');
    return;
  }

  const headers = {
    'Accept': 'application/json, text/plain, */*',
    'Authorization': 'Bearer ' + token,
    'EcmsId': ecms,
  };

  const get = async (url) => {
    const resp = await fetch(url, { credentials: 'include', headers });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return resp.json();
  };

  // --- Page through the purchase list -------------------------------------
  const txns = [];
  for (let page = 1; page <= MAX_PAGES; page++) {
    let env;
    try {
      env = await get(`${LIST}?page=${page}&size=${PAGE_SIZE}`);
    } catch (err) {
      console.warn('list page', page, err.message);
      break;
    }
    const rows = env.CurrentTransactions || [];
    txns.push(...rows);
    const totalPages = env.TotalPages || 1;
    console.log(`page ${page}/${totalPages}: ${rows.length} transactions`);
    if (page >= totalPages || rows.length === 0) break;
    await sleep(LIST_DELAY_MS);
  }
  console.log(`Found ${txns.length} transactions; fetching details…`);

  // --- Fetch each transaction's detail, merge with its list summary --------
  const byReceipt = {};
  for (let i = 0; i < txns.length; i++) {
    const t = txns[i];
    const key = t.TransactionKey;
    let detail = {};
    try {
      detail = await get(`${DETAIL}?transactionKey=${encodeURIComponent(key)}`);
    } catch (err) {
      console.warn('detail failed for', t.ReceiptId || key, err.message);
    }
    // Merge: detail wins, but keep the list's clean identifiers/date/store.
    const merged = Object.assign({}, detail);
    for (const k in t) if (!(k in merged)) merged[k] = t[k];
    const id = merged.ReceiptId || key || ('idx-' + i);
    byReceipt[id] = merged; // dedupe by ReceiptId
    if ((i + 1) % 10 === 0) console.log(`  …${i + 1}/${txns.length}`);
    await sleep(DETAIL_DELAY_MS);
  }

  const receipts = Object.values(byReceipt);
  console.log(`Merged receipts: ${receipts.length}`);
  if (!receipts.length) {
    console.error('Nothing collected. Share any error above and we can adjust.');
    return;
  }

  // --- Download a single JSON file ----------------------------------------
  const out = { receipts };
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'publix_receipts.json';
  document.body.appendChild(a); a.click(); a.remove();
  console.log(`Downloaded publix_receipts.json (${receipts.length} receipts).`);
})();
