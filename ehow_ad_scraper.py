"""
ehow.com Ad Scraper — core Playwright logic
--------------------------------------------
Exposes scrape_ehow_for_ads() for use by app.py.
Can also be run standalone from the CLI.

HOW IT WORKS (revised approach):
  ehow uses Google AdSense for Search (AFS/CSA) to show text-based search ads
  in #adcontainer1 and #adcontainer2 on every search results page.

  Previous approach (scraping GPT display banner iframes on article pages) failed
  because Google detects headless browsers and serves empty creatives.

  New approach:
    1. Load ehow's SEARCH PAGE for the given term (not individual articles).
    2. Inject a fake TCF consent response to unblock the CMP gating that was
       preventing ads from loading.
    3. Manually trigger _googCsa() — the Google CSA function ehow already loads —
       with ehow's own pubId/styleId and the search query.
    4. Wait for the AFS ad iframes to render inside #adcontainer1/#adcontainer2.
    5. Access those iframes via Playwright's frame list (cross-origin access works
       in Playwright even though it wouldn't in a normal browser script).
    6. Extract advertiser headline, visible URL, and click-through URL from each ad.

Usage (CLI):
    python ehow_ad_scraper.py makeup 8 ehow_ads.csv
"""

import asyncio
import csv
import os
import re
import sys
from urllib.parse import urlparse, parse_qs, unquote
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Constants — from live inspection of ehow.com
# ---------------------------------------------------------------------------

EHOW_BASE  = "https://www.ehow.com"
AFS_PUB_ID = "partner-pub-0316265116163263"
AFS_STYLE  = "9939500133"

SKIP_DOMAINS = [
    "google.com", "googleapis.com", "googleadservices.com",
    "googlesyndication.com", "doubleclick.net", "googletagmanager.com",
    "gstatic.com", "youtube.com", "facebook.com", "twitter.com",
    "instagram.com", "ehow.com", "about:blank", "javascript:", "chrome-error:",
    "syndicatedsearch.goog",
]

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "browser_profile")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_skip_url(url: str) -> bool:
    return not url or any(d in url for d in SKIP_DOMAINS)


def company_from_url(url: str) -> str:
    """Return a clean, human-readable company name derived from the URL domain."""
    try:
        netloc = urlparse(url).netloc
        # Strip common subdomains
        netloc = re.sub(
            r"^(www\d?|m|mobile|shop|store|go|click|ad|ads|health|get|try|my|use|join)\.",
            "", netloc
        )
        # Strip TLD(s) — handles .com, .co.uk, .net, .io, etc.
        name = re.sub(
            r"\.(com|net|org|io|co|us|info|biz|shop|store|health|app)(\.[a-z]{2})?$",
            "", netloc, flags=re.I
        )
        # Hyphens → spaces, then title-case
        return name.replace("-", " ").replace("_", " ").title()
    except Exception:
        return ""


def unwrap_google_redirect(url: str) -> str:
    """
    Google ad click URLs look like:
      https://www.googleadservices.com/pagead/aclk?...&adurl=https%3A%2F%2Fadvertiser.com%2F...
    Pull out the real destination.
    """
    try:
        params = parse_qs(urlparse(url).query)
        for key in ("adurl", "dest", "url", "u"):
            if key in params:
                return unquote(params[key][0])
    except Exception:
        pass
    return url


# ---------------------------------------------------------------------------
# Consent bypass — injected into the page before _googCsa is triggered
# ---------------------------------------------------------------------------

CONSENT_BYPASS_JS = """
// Fake a fully-consented TCF 2.x CMP so Google AFS stops blocking
window.__tcfapi = function(cmd, version, callback) {
    if (typeof callback === 'function') {
        callback({
            tcString: 'CPAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
            gdprApplies: false,
            cmpStatus: 'loaded',
            eventStatus: 'tcloaded',
            isServiceSpecific: true,
            useNonStandardStacks: false,
            publisherCC: 'US',
            purposeOneTreatment: false,
            purpose: { consents: {}, legitimateInterests: {} },
            vendor:  { consents: {}, legitimateInterests: {} }
        }, true);
    }
};
// Also set __uspapi for CCPA (US privacy)
window.__uspapi = function(cmd, version, callback) {
    if (typeof callback === 'function') {
        callback({ version: 1, uspString: '1YNY' }, true);
    }
};
"""

# ---------------------------------------------------------------------------
# Core: trigger AFS and collect ads from a single search query
# ---------------------------------------------------------------------------

async def _collect_ads_for_query(page, search_term: str) -> list[dict]:
    """
    Load ehow search page, bypass consent, trigger _googCsa, and harvest ads
    from the AFS iframes that render in #adcontainer1 / #adcontainer2.
    """
    url = f"{EHOW_BASE}/search?q={search_term.replace(' ', '+')}"
    print(f"  [load] {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)

    # Inject consent bypass BEFORE ads try to load
    await page.evaluate(CONSENT_BYPASS_JS)

    # Wait for Google's CSA library to be ready
    try:
        await page.wait_for_function("typeof _googCsa === 'function'", timeout=15000)
        print("  [csa]  _googCsa ready")
    except Exception:
        print("  [csa]  WARNING: _googCsa not found — page may not have loaded properly")
        return []

    # Fire _googCsa with both ad containers
    await page.evaluate(f"""
        (function() {{
            var po = {{
                pubId:    '{AFS_PUB_ID}',
                styleId:  '{AFS_STYLE}',
                query:    {repr(search_term)},
                hl:       'en',
                oe:       'utf-8'
            }};
            var b1 = {{
                container: 'adcontainer1',
                maxTop: 4,
                width: '100%',
                adLoadedCallback: function(c, loaded) {{
                    window._afs1Loaded = loaded;
                }}
            }};
            var b2 = {{
                container: 'adcontainer2',
                number: 4,
                width: '100%',
                adLoadedCallback: function(c, loaded) {{
                    window._afs2Loaded = loaded;
                }}
            }};
            _googCsa('ads', po, b1, b2);
        }})();
    """)

    # Wait for the AFS ad iframes to appear
    print("  [wait] waiting for AFS ad iframes...")
    try:
        await page.wait_for_selector(
            "#adcontainer1 iframe, #adcontainer2 iframe",
            timeout=20000
        )
        print("  [wait] ad iframe(s) detected")
    except Exception:
        print("  [wait] no ad iframes appeared — trying frame scan anyway")

    # Extra settle time for all ads to finish rendering
    await page.wait_for_timeout(3000)

    # Check load status
    loaded1 = await page.evaluate("window._afs1Loaded")
    loaded2 = await page.evaluate("window._afs2Loaded")
    print(f"  [csa]  container1 loaded={loaded1}, container2 loaded={loaded2}")

    # ---------------------------------------------------------------------------
    # Harvest ads from all frames that look like AFS/Google ad frames
    # ---------------------------------------------------------------------------
    ads: list[dict] = []
    seen: set[str] = set()

    def add_ad(company: str, ad_url: str, headline: str = "", description: str = ""):
        key = company.lower()
        if not company or key in seen or is_skip_url(ad_url):
            return
        seen.add(key)
        ads.append({
            "company_name": company,
            "ad_url":       ad_url,
            "headline":     headline,
            "description":  description,
        })

    for frame in page.frames:
        frame_url = frame.url or ""
        if not frame_url or "about:blank" in frame_url or "chrome-error" in frame_url:
            continue
        # Target AFS frames and any frame inside our ad containers
        is_afs = any(d in frame_url for d in (
            "syndicatedsearch.goog", "googleadservices.com",
            "pagead", "afs/ads", "google.com/afs"
        ))
        # Also scan any frame whose parent is an adcontainer
        try:
            parent_id = await frame.evaluate(
                "window.frameElement ? window.frameElement.closest('#adcontainer1,#adcontainer2') !== null : false"
            )
        except Exception:
            parent_id = False

        if not (is_afs or parent_id):
            continue

        print(f"  [frame] scanning {frame_url[:100]}")
        try:
            # Get all anchor tags from the frame
            links = await frame.eval_on_selector_all(
                "a[href]",
                """els => els.map(e => ({
                    href:     e.href,
                    text:     e.innerText.trim(),
                    dataUrl:  e.getAttribute('data-redir') || e.getAttribute('data-adurl') || ''
                }))"""
            )

            # Also grab any visible URL spans (AFS shows a clean "visibleUrl" separate from click URL)
            visible_urls = await frame.eval_on_selector_all(
                "[class*='visible'], [class*='url'], cite, [class*='adDomain']",
                "els => els.map(e => e.innerText.trim()).filter(t => t.includes('.'))"
            )

            # Get all text for headline/description extraction
            try:
                full_text = await frame.inner_text("body")
            except Exception:
                full_text = ""

            for link in links:
                href = (link.get("href") or "").strip()
                if not href or "google" in href.lower() and "adurl" not in href:
                    continue

                # Unwrap Google redirect to get real advertiser URL
                dest = unwrap_google_redirect(href)
                if is_skip_url(dest):
                    # Try data attributes
                    dest = unwrap_google_redirect(link.get("dataUrl") or "")
                    if is_skip_url(dest):
                        continue

                company = company_from_url(dest)
                headline = link.get("text", "")[:120]
                add_ad(company, dest, headline)

            # If we got visible URL text but no links resolved, use visible URLs
            for vu in visible_urls:
                vu = vu.strip().lower()
                if "." in vu and not any(skip in vu for skip in ("google", "ehow", "syndi")):
                    full_url = "https://" + vu if not vu.startswith("http") else vu
                    company = company_from_url(full_url)
                    add_ad(company, full_url)

        except Exception as e:
            print(f"  [frame] error: {e}")

    # Fallback: if no ads from frames, try reading adcontainer DOM directly
    if not ads:
        print("  [fallback] trying DOM read of adcontainers...")
        try:
            dom_links = await page.eval_on_selector_all(
                "#adcontainer1 a[href], #adcontainer2 a[href]",
                "els => els.map(e => ({href: e.href, text: e.innerText.trim()}))"
            )
            for link in dom_links:
                href = (link.get("href") or "").strip()
                dest = unwrap_google_redirect(href)
                if not is_skip_url(dest):
                    add_ad(company_from_url(dest), dest, link.get("text", ""))
        except Exception as e:
            print(f"  [fallback] DOM read error: {e}")

    print(f"  [done] {len(ads)} advertiser(s) found for '{search_term}'")
    return ads


# ---------------------------------------------------------------------------
# Async orchestrator
# ---------------------------------------------------------------------------

async def _async_scrape(search_term: str, num_ads: int) -> list[dict]:
    os.makedirs(PROFILE_DIR, exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = context.pages[0] if context.pages else await context.new_page()
        ads = await _collect_ads_for_query(page, search_term)
        await context.close()

    return ads[:num_ads]


# ---------------------------------------------------------------------------
# Public sync API used by app.py
# ---------------------------------------------------------------------------

def scrape_ehow_for_ads(
    search_term: str,
    num_articles: int = 8,   # repurposed as max_ads
    headless: bool = False,
) -> list[dict]:
    """
    Returns list of dicts: {company_name, ad_url, headline, description}
    num_articles is treated as max number of ads to return.
    """
    return asyncio.run(_async_scrape(search_term, num_articles))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import requests as req_lib

    _EMAIL_RE   = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
    _MAILTO_RE  = re.compile(r'href=["\']mailto:([\w.+-]+@[\w-]+\.[\w.]{2,})["\']', re.I)
    _TEL_RE     = re.compile(r'href=["\']tel:([+\d\s().\-]{7,})["\']', re.I)
    _PHONE_RE   = re.compile(r"\+?1?[\s.\-]?\(?(\d{3})\)?[\s.\-](\d{3})[\s.\-](\d{4})")
    _CONTACT_RE = re.compile(r"contact|about[\-_]?us|reach[\-_]?us|support", re.I)
    _SKIP_EXTS  = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".woff", ".ico"}
    _UA         = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0"

    def _fetch(url):
        try:
            r = req_lib.get(url, headers={"User-Agent": _UA}, timeout=10, allow_redirects=True)
            return r.text if r.ok else ""
        except Exception:
            return ""

    def _enrich(ad_url):
        parsed = urlparse(ad_url if ad_url.startswith("http") else "https://" + ad_url)
        base   = f"{parsed.scheme}://{parsed.netloc}"
        emails, phones, contact_page = set(), set(), ""

        html   = _fetch(ad_url)
        links  = re.findall(r'href=["\']([^"\']+)["\']', html)
        clinks = [l for l in links if _CONTACT_RE.search(l)]
        if clinks:
            contact_page = clinks[0] if clinks[0].startswith("http") else base + clinks[0]

        pages = [ad_url] + ([contact_page] if contact_page else []) + [base + p for p in ("/contact", "/contact-us")]
        for url in pages[:4]:
            text = _fetch(url)
            if not text:
                continue
            for e in _MAILTO_RE.findall(text): emails.add(e.lower())
            for t in _TEL_RE.findall(text):
                c = re.sub(r"[^\d+]", "", t)
                if len(c) >= 10: phones.add(t.strip())
            if not emails:
                for e in _EMAIL_RE.findall(text):
                    if not any(e.endswith(x) for x in _SKIP_EXTS): emails.add(e.lower())
            if not phones:
                for m in _PHONE_RE.finditer(text):
                    phones.add(f"({m.group(1)}) {m.group(2)}-{m.group(3)}")
            if emails or phones:
                break
        return {
            "contact_page_url": contact_page,
            "emails":  " | ".join(sorted(emails)[:5]),
            "phones":  " | ".join(sorted(phones)[:5]),
        }

    term        = sys.argv[1] if len(sys.argv) > 1 else "makeup"
    max_ads     = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    output_file = sys.argv[3] if len(sys.argv) > 3 else "ehow_ads.csv"

    print(f"\nScraping ehow AFS ads for '{term}'...")
    ads = scrape_ehow_for_ads(term, max_ads)
    print(f"  {len(ads)} ad(s) found\n")

    enriched_cache: dict[str, dict] = {}
    for ad in ads:
        domain = ad["company_name"]
        if domain not in enriched_cache:
            print(f"  Enriching: {ad['ad_url']}")
            enriched_cache[domain] = _enrich(ad["ad_url"])

    rows = [{**ad, **enriched_cache.get(ad["company_name"], {})} for ad in ads]

    fieldnames = ["company_name", "ad_url", "headline", "emails", "phones"]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n✓ Saved {len(rows)} record(s) to '{output_file}'")
