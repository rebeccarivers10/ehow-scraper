"""
ehow.com Ad Scraper — Web UI
Run:  py app.py
Then open http://localhost:5003
"""

import re
from urllib.parse import urlparse
from flask import Flask, render_template_string, request, jsonify
import requests as req_lib
from ehow_ad_scraper import scrape_ehow_for_ads

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Contact enrichment helpers (fast requests-based, no Playwright needed)
# ---------------------------------------------------------------------------

_EMAIL_RE   = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
_MAILTO_RE  = re.compile(r'href=["\']mailto:([\w.+-]+@[\w-]+\.[\w.]{2,})["\']', re.I)
_TEL_RE     = re.compile(r'href=["\']tel:([+\d\s().\-]{7,})["\']', re.I)
_PHONE_RE   = re.compile(r"\+?1?[\s.\-]?\(?(\d{3})\)?[\s.\-](\d{3})[\s.\-](\d{4})")
_ADDR_RE    = re.compile(r"\d{1,5}\s[\w\s]{3,40},\s[\w\s]{2,30},\s[A-Z]{2}\s\d{5}(?:-\d{4})?", re.I)
_CONTACT_RE = re.compile(r"contact|about[\-_]?us|reach[\-_]?us|support", re.I)
_SKIP_EXTS  = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".woff", ".ttf", ".ico"}
_UA         = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def _fetch_text(url: str) -> str:
    try:
        r = req_lib.get(url, headers={"User-Agent": _UA}, timeout=9, allow_redirects=True)
        return r.text if r.ok else ""
    except Exception:
        return ""


def _extract_contacts(html: str):
    emails: set = set()
    phones: set = set()
    for e in _MAILTO_RE.findall(html):
        emails.add(e.lower())
    for raw in _TEL_RE.findall(html):
        clean = re.sub(r"[^\d+]", "", raw)
        if len(clean) >= 10:
            phones.add(raw.strip())
    if not emails:
        for e in _EMAIL_RE.findall(html):
            if not any(e.endswith(x) for x in _SKIP_EXTS):
                emails.add(e.lower())
    if not phones:
        for m in _PHONE_RE.finditer(html):
            phones.add(f"({m.group(1)}) {m.group(2)}-{m.group(3)}")
    return emails, phones


def _extract_address(text: str) -> str:
    m = _ADDR_RE.search(text)
    return m.group(0).strip() if m else ""


def _find_contact_page(base_url: str, html: str) -> str:
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    for href in hrefs:
        if _CONTACT_RE.search(href):
            if href.startswith("http"):
                return href
            return base_url.rstrip("/") + ("/" if not href.startswith("/") else "") + href
    return ""


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>eHow Ad Scraper</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #f5f5f5; color: #222; }
  header { background: #1a1a2e; color: #fff; padding: 16px 32px; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 1.3rem; font-weight: 600; }
  .tag { background: #e94560; color: #fff; font-size: .7rem; padding: 2px 8px; border-radius: 99px; font-weight: 700; letter-spacing: .04em; }
  main { max-width: 1600px; margin: 28px auto; padding: 0 20px; }

  /* Input panel */
  .input-panel { background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; margin-bottom: 20px; }
  .input-panel label { display: block; font-weight: 600; font-size: .9rem; margin-bottom: 8px; color: #1a1a2e; }
  .input-panel .hint { font-size: .78rem; color: #888; margin-bottom: 10px; }
  .row-two { display: flex; gap: 20px; align-items: flex-end; margin-top: 12px; flex-wrap: wrap; }
  .field-group { display: flex; flex-direction: column; gap: 4px; }
  .field-group label { font-size: .8rem; font-weight: 600; color: #555; }
  .field-group select { padding: 7px 10px; border: 1px solid #ccc; border-radius: 6px; font-size: .85rem; }
  textarea#keywords { width: 100%; height: 130px; padding: 10px 14px; border: 1px solid #ccc;
    border-radius: 6px; font-size: .9rem; font-family: inherit; resize: vertical; outline: none; line-height: 1.6; }
  textarea#keywords:focus { border-color: #1a1a2e; }
  .btn-row { display: flex; gap: 10px; align-items: center; margin-top: 0; }
  .btn-run  { padding: 10px 26px; background: #e94560; color: #fff; border: none; border-radius: 6px;
    font-size: 1rem; cursor: pointer; font-weight: 600; white-space: nowrap; }
  .btn-run:disabled { opacity: .45; cursor: not-allowed; }
  .btn-stop { padding: 10px 20px; background: #fff; color: #c0392b; border: 2px solid #c0392b;
    border-radius: 6px; font-size: 1rem; cursor: pointer; font-weight: 600; display: none; white-space: nowrap; }
  .btn-stop:disabled { opacity: .5; cursor: not-allowed; }
  .kw-hint { font-size: .82rem; color: #888; margin-left: 4px; }

  .note-box { background: #fffbea; border: 1px solid #f0d060; border-radius: 6px;
    padding: 10px 14px; font-size: .8rem; color: #7a6000; margin-top: 12px; line-height: 1.6; }

  /* Progress */
  .progress-panel { background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
    padding: 16px 20px; margin-bottom: 16px; display: none; }
  .progress-top { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
  .progress-kw { font-size: .9rem; font-weight: 600; color: #1a1a2e; }
  .progress-fraction { font-size: .82rem; color: #888; }
  .progress-track { height: 6px; background: #eee; border-radius: 99px; overflow: hidden; margin-bottom: 10px; }
  .progress-fill  { height: 100%; background: #e94560; border-radius: 99px; width: 0%; transition: width .3s ease; }
  .progress-sub { font-size: .8rem; color: #777; min-height: 18px; display: flex; align-items: center; gap: 6px; }
  .spinner    { display: inline-block; width: 14px; height: 14px; border: 2px solid #ccc;
    border-top-color: #1a1a2e; border-radius: 50%; animation: spin .7s linear infinite; vertical-align: middle; margin-right: 6px; }
  .spinner-sm { display: inline-block; width: 10px; height: 10px; border: 2px solid #ddd;
    border-top-color: #888; border-radius: 50%; animation: spin .7s linear infinite; flex-shrink: 0; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Status / toolbar */
  #status { font-size: .9rem; color: #666; margin-bottom: 12px; min-height: 20px; }
  #status.error { color: #c0392b; }
  .toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
  .toolbar label { font-size: .85rem; color: #555; }
  .toolbar select { padding: 5px 10px; border: 1px solid #ccc; border-radius: 5px; font-size: .85rem; }
  .toolbar button { padding: 6px 14px; border: 1px solid #bbb; border-radius: 5px; background: #fff; cursor: pointer; font-size: .85rem; }
  .toolbar button:hover { background: #f0f0f0; }
  #count { font-size: .85rem; color: #888; margin-left: auto; }
  .empty { text-align: center; color: #999; padding: 60px 0; font-size: 1rem; }

  /* Table */
  .tbl-wrap { overflow-x: auto; border-radius: 8px; border: 1px solid #e0e0e0; background: #fff; }
  table { width: 100%; border-collapse: collapse; font-size: .875rem; }
  thead th { background: #1a1a2e; color: #fff; text-align: left; padding: 10px 14px; font-weight: 600;
    white-space: nowrap; font-size: .8rem; letter-spacing: .03em; text-transform: uppercase; }
  tbody tr { border-bottom: 1px solid #f0f0f0; }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover { background: #fafafa; }
  td { padding: 10px 14px; vertical-align: top; }

  .col-company { min-width: 160px; }
  .col-adurl   { min-width: 220px; }
  .col-email   { min-width: 220px; }
  .col-phone   { min-width: 160px; }

  .company-link { color: #1565c0; text-decoration: none; font-weight: 600; }
  .company-link:hover { text-decoration: underline; }
  .display-url { font-size: .82rem; color: #2e7d32; text-decoration: none; }
  .display-url:hover { text-decoration: underline; }
  .contact-email { font-size: .78rem; color: #1a56a8; word-break: break-all; margin-bottom: 3px; }
  .contact-email::before { content: "\\2709\\A0"; }
  .contact-phone { font-size: .78rem; color: #2e7d32; margin-bottom: 3px; }
  .contact-phone::before { content: "\\260E\\A0"; }
  .contact-none { font-size: .78rem; color: #ccc; }
</style>
</head>
<body>
<header>
  <h1>eHow Ad Scraper</h1>
  <span class="tag">SPONSORED</span>
</header>
<main>

  <!-- Keyword input -->
  <div class="input-panel">
    <label for="keywords">Search Terms</label>
    <div class="hint">One search term per line. The scraper will visit eHow articles for each term and extract advertiser contact info.</div>
    <textarea id="keywords" placeholder="makeup&#10;skincare&#10;hair dye&#10;nail art"></textarea>

    <div class="row-two">
      <div class="field-group">
        <label for="num-articles">Max ads per term</label>
        <select id="num-articles">
          <option value="4">4</option>
          <option value="8" selected>8</option>
          <option value="12">12</option>
          <option value="16">16</option>
        </select>
      </div>
      <div class="btn-row">
        <button class="btn-run" id="run-btn" onclick="startBatch()">Run Scraper</button>
        <button class="btn-stop" id="stop-btn" onclick="stopBatch()">Stop</button>
        <span class="kw-hint" id="kw-hint"></span>
      </div>
    </div>

    <div class="note-box">
      &#9432;&nbsp; eHow uses Google search ads — the scraper searches eHow directly and captures the advertisers.
      A browser window will open automatically; don't close it while scraping.
    </div>
  </div>

  <!-- Progress -->
  <div class="progress-panel" id="progress-panel">
    <div class="progress-top">
      <span class="progress-kw" id="progress-kw">Starting&hellip;</span>
      <span class="progress-fraction" id="progress-fraction"></span>
    </div>
    <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
    <div class="progress-sub" id="progress-sub"></div>
  </div>

  <div id="status"></div>

  <div id="controls" style="display:none">
    <div class="toolbar">
      <label>Term:</label>
      <select id="kw-filter" onchange="renderTable()">
        <option value="all">All terms</option>
      </select>
      <button onclick="exportCSV()">&#8595; CSV</button>
      <button onclick="exportJSON()">&#8595; JSON</button>
      <span id="count"></span>
    </div>
  </div>

  <div id="results"></div>
</main>

<script>
let currentAds       = [];   // all ads across all keywords
let currentEnrich    = {};   // _uid -> {emails, phones, address, contact_page_url}
let uidCounter       = 0;
let stopRequested    = false;
let batchRunning     = false;
let batchErrors      = [];

document.getElementById('keywords').addEventListener('input', updateKwHint);

function updateKwHint() {
  const kws = parseKeywords();
  const el  = document.getElementById('kw-hint');
  el.textContent = kws.length ? kws.length + ' term' + (kws.length > 1 ? 's' : '') : '';
}

function parseKeywords() {
  return document.getElementById('keywords').value
    .split('\\n').map(k => k.trim()).filter(Boolean);
}

async function startBatch() {
  const keywords = parseKeywords();
  if (!keywords.length) return;

  currentAds    = [];
  currentEnrich = {};
  batchErrors   = [];
  uidCounter    = 0;
  stopRequested = false;
  batchRunning  = true;

  document.getElementById('run-btn').disabled = true;
  document.getElementById('stop-btn').style.display = '';
  document.getElementById('stop-btn').disabled = false;
  document.getElementById('stop-btn').textContent = 'Stop';
  document.getElementById('controls').style.display = 'none';
  document.getElementById('results').innerHTML = '';
  document.getElementById('status').textContent = '';
  document.getElementById('progress-panel').style.display = '';
  resetKwFilter();
  setProgress(0, keywords.length, '');

  const numArts = document.getElementById('num-articles').value;

  let done = 0;
  for (const kw of keywords) {
    if (stopRequested) break;
    setProgress(done, keywords.length, kw);
    await processKeyword(kw, numArts);
    done++;
    setProgress(done, keywords.length, null);
  }

  batchRunning = false;
  document.getElementById('run-btn').disabled = false;
  document.getElementById('stop-btn').style.display = 'none';
  document.getElementById('progress-panel').style.display = 'none';

  const stopped = stopRequested ? ' (stopped early)' : '';
  let statusHtml = '';
  if (currentAds.length) {
    const uniqueKws = [...new Set(currentAds.map(a => a.keyword))].length;
    statusHtml = 'Found <strong>' + currentAds.length + '</strong> ad(s) across <strong>'
      + uniqueKws + '</strong> search term' + (uniqueKws > 1 ? 's' : '') + stopped + '.';
  } else {
    statusHtml = 'No ads found' + stopped + '. Try different search terms or check that the browser window opened.';
  }
  if (batchErrors.length) {
    statusHtml += ' <span style="color:#c0392b">&#9888; '
      + batchErrors.length + ' term' + (batchErrors.length > 1 ? 's' : '') + ' failed.</span>';
  }
  setStatus(statusHtml);
}

function stopBatch() {
  stopRequested = true;
  document.getElementById('stop-btn').disabled = true;
  document.getElementById('stop-btn').textContent = 'Stopping\u2026';
  setProgressSub('<span class="spinner-sm"></span> Finishing current term then stopping\u2026');
}

async function processKeyword(kw, numArts) {
  setProgressSub('<span class="spinner"></span>Opening browser and scraping eHow&hellip; (this may take ~30&ndash;60s)');
  try {
    const resp = await fetch('/scrape', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ query: kw, num_articles: parseInt(numArts) })
    });
    const data = await resp.json();
    if (!resp.ok) {
      batchErrors.push('"' + kw + '": ' + (data.error || 'HTTP ' + resp.status));
      setProgressSub('&#9888; ' + (data.error || 'failed') + ' — skipping');
      return;
    }

    const newAds = (data.ads || []).map(ad => {
      ad._uid    = uidCounter++;
      ad.keyword = kw;
      return ad;
    });
    currentAds.push(...newAds);
    addToKwFilter(kw);
    document.getElementById('controls').style.display = '';
    renderTable();

    // Enrich contact info concurrently for new ads
    if (newAds.length) {
      let enriched = 0;
      // Dedupe by company so we don't hit the same domain multiple times
      const seenDomains = new Set();
      const toEnrich = newAds.filter(a => {
        if (!a.ad_url || seenDomains.has(a.company_name)) return false;
        seenDomains.add(a.company_name);
        return true;
      });

      setProgressSub('<span class="spinner-sm"></span> Fetching contact info&hellip; 0\u202f/\u202f' + toEnrich.length);

      await Promise.all(toEnrich.map(async ad => {
        await enrichOne(ad, newAds);
        enriched++;
        setProgressSub('<span class="spinner-sm"></span> Fetching contact info&hellip; '
          + enriched + '\u202f/\u202f' + toEnrich.length);
      }));
    }
  } catch (err) {
    batchErrors.push('"' + kw + '": ' + err.message);
    setProgressSub('Error: ' + err.message);
  }
}

async function enrichOne(ad, siblingAds) {
  let data = { emails: [], phones: [], address: '', contact_page_url: '' };
  try {
    const resp = await fetch('/enrich', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ ad_url: ad.ad_url })
    });
    if (resp.ok) data = await resp.json();
  } catch (_) {}

  // Apply to all ads with the same company (same domain)
  const sameDomain = siblingAds.filter(a => a.company_name === ad.company_name);
  for (const a of sameDomain) {
    currentEnrich[a._uid] = data;
    updateContactCells(a._uid, data);
  }
}

function updateContactCells(uid, data) {
  const ec = document.getElementById('contact-email-' + uid);
  const pc = document.getElementById('contact-phone-' + uid);
  if (ec) ec.innerHTML = renderEmails(data);
  if (pc) pc.innerHTML = renderPhones(data);
}

// ---- Progress helpers ----
function setProgress(done, total, currentKw) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('progress-fraction').textContent = done + '\u202f/\u202f' + total + ' done';
  if (currentKw !== null) {
    document.getElementById('progress-kw').textContent = '\u201c' + currentKw + '\u201d';
  } else {
    document.getElementById('progress-kw').textContent = done < total ? 'Next term\u2026' : 'Done';
  }
}
function setProgressSub(html) {
  document.getElementById('progress-sub').innerHTML = html;
}

// ---- Keyword filter ----
function resetKwFilter() {
  document.getElementById('kw-filter').innerHTML = '<option value="all">All terms</option>';
}
function addToKwFilter(kw) {
  const sel = document.getElementById('kw-filter');
  if ([...sel.options].some(o => o.value === kw)) return;
  const opt = document.createElement('option');
  opt.value = kw; opt.textContent = kw;
  sel.appendChild(opt);
}

// ---- Table ----
function renderTable() {
  const kwFilter = document.getElementById('kw-filter').value;
  let ads = kwFilter === 'all' ? currentAds : currentAds.filter(a => a.keyword === kwFilter);

  // Deduplicate by company_name
  const seen = new Set();
  ads = ads.filter(ad => {
    const k = (ad.company_name || '').toLowerCase().trim();
    if (k && seen.has(k)) return false;
    if (k) seen.add(k);
    return true;
  });

  document.getElementById('count').textContent = ads.length + ' result' + (ads.length !== 1 ? 's' : '');

  if (!ads.length) {
    document.getElementById('results').innerHTML =
      '<div class="empty">No results yet — results appear as each term finishes.</div>';
    return;
  }

  const spinner = '<span class="spinner-sm"></span>';

  const rows = ads.map(ad => {
    const cached    = currentEnrich[ad._uid];
    const emailCell = cached !== undefined ? renderEmails(cached) : spinner;
    const phoneCell = cached !== undefined ? renderPhones(cached) : spinner;

    const companyCell = ad.ad_url
      ? '<a class="company-link" href="' + escAttr(ad.ad_url) + '" target="_blank" rel="noopener">'
          + escHtml(formatCompanyName(ad)) + '</a>'
      : escHtml(formatCompanyName(ad));

    const adUrlCell = ad.ad_url
      ? '<a class="display-url" href="' + escAttr(ad.ad_url) + '" target="_blank" rel="noopener" title="' + escAttr(ad.ad_url) + '">'
          + escHtml(formatUrl(ad.ad_url)) + '</a>' : '';

    return '<tr>'
      + '<td class="col-company">' + companyCell + '</td>'
      + '<td class="col-adurl">'   + adUrlCell   + '</td>'
      + '<td class="col-email" id="contact-email-' + ad._uid + '">' + emailCell + '</td>'
      + '<td class="col-phone" id="contact-phone-' + ad._uid + '">' + phoneCell + '</td>'
      + '</tr>';
  }).join('');

  document.getElementById('results').innerHTML =
    '<div class="tbl-wrap"><table>'
    + '<thead><tr>'
    + '<th class="col-company">Advertiser</th>'
    + '<th class="col-adurl">URL</th>'
    + '<th class="col-email">Email</th>'
    + '<th class="col-phone">Phone</th>'
    + '</tr></thead>'
    + '<tbody>' + rows + '</tbody>'
    + '</table></div>';
}

function renderEmails(data) {
  return data.emails && data.emails.length
    ? data.emails.map(e => '<div class="contact-email">' + escHtml(e) + '</div>').join('')
    : '<span class="contact-none">—</span>';
}
function renderPhones(data) {
  return data.phones && data.phones.length
    ? data.phones.map(p => '<div class="contact-phone">' + escHtml(p) + '</div>').join('')
    : '<span class="contact-none">—</span>';
}

// ---- Utilities ----
function setStatus(html) { document.getElementById('status').innerHTML = html; }
function escHtml(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(s) { return String(s||'').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }

function formatCompanyName(ad) {
  // AFS ad headlines are usually "Company Name - Tag line" or "Company Name | Something"
  // Extract the part before the first separator as the company name
  const hl = (ad.headline || '').trim();
  if (hl) {
    const match = hl.match(/^(.+?)\s*[-–|]\s*.+$/);
    if (match && match[1].length <= 40) return match[1].trim();
    if (hl.length <= 35) return hl;  // short headline is likely just the brand
  }
  // Fall back to the pre-formatted domain-based name (already title-cased, TLD stripped)
  return ad.company_name || '—';
}

function formatUrl(url) {
  // Show just the hostname, strip www.
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch(e) {
    return url.length > 50 ? url.substring(0, 50) + '…' : url;
  }
}

// ---- Exports ----
function exportCSV() {
  const fields = ['keyword', 'company_name', 'ad_url', 'emails', 'phones'];
  const rows   = [fields.join(',')];
  for (const ad of currentAds) {
    const e = currentEnrich[ad._uid] || {};
    rows.push(fields.map(f => {
      let v;
      if      (f === 'emails') v = (e.emails || []).join(' | ');
      else if (f === 'phones') v = (e.phones || []).join(' | ');
      else                     v = ad[f] || '';
      return '"' + String(v).replace(/"/g, '""') + '"';
    }).join(','));
  }
  download(new Blob([rows.join('\\n')], {type: 'text/csv'}), 'ehow_ads.csv');
}

function exportJSON() {
  const out = currentAds.map(ad => {
    const e = currentEnrich[ad._uid] || {};
    return {
      company_name: ad.company_name || '',
      ad_url:       ad.ad_url       || '',
      emails:       e.emails        || [],
      phones:       e.phones        || [],
    };
  });
  download(new Blob([JSON.stringify({count: out.length, ads: out}, null, 2)], {type:'application/json'}),
    'ehow_ads.json');
}

function download(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
}

updateKwHint();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/scrape", methods=["POST"])
def scrape():
    data        = request.get_json() or {}
    query       = (data.get("query") or "").strip()
    num_articles = int(data.get("num_articles") or 5)

    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        ads = scrape_ehow_for_ads(query, num_articles=num_articles, headless=False)
    except Exception as e:
        return jsonify({"error": f"Scraper error: {e}"}), 500

    if not ads:
        return jsonify({"error": "No ads found — try a different search term."}), 502

    return jsonify({"query": query, "ad_count": len(ads), "ads": ads})


@app.route("/enrich", methods=["POST"])
def enrich():
    data    = request.get_json() or {}
    ad_url  = (data.get("ad_url") or "").strip()

    if not ad_url:
        return jsonify({"emails": [], "phones": [], "address": "", "contact_page_url": ""})

    raw    = ad_url if ad_url.startswith("http") else "https://" + ad_url
    parsed = urlparse(raw)
    base   = f"{parsed.scheme}://{parsed.netloc}"

    # Find a contact page from the landing page HTML
    landing_html = _fetch_text(raw)
    contact_page = _find_contact_page(base, landing_html) if landing_html else ""

    emails:  set = set()
    phones:  set = set()
    address: str = ""

    # Pages to check: landing + contact page + common fallback paths
    candidates = [raw]
    if contact_page:
        candidates.append(contact_page)
    for path in ("/contact", "/contact-us", "/about-us"):
        candidates.append(base + path)

    for url in candidates[:4]:
        html = _fetch_text(url) if url != raw else landing_html
        if not html:
            continue
        e, p = _extract_contacts(html)
        emails |= e
        phones |= p
        if not address:
            address = _extract_address(html)
        if emails or phones:
            break

    return jsonify({
        "emails":           sorted(emails)[:5],
        "phones":           sorted(phones)[:5],
        "address":          address,
        "contact_page_url": contact_page,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5003))
    print(f"\n  eHow Ad Scraper running → http://localhost:{port}\n")
    app.run(debug=False, port=port, threaded=True)
