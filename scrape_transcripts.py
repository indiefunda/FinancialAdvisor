"""
Scrape earnings call transcripts from discountingcashflows.com.

Usage:
    python scrape_transcripts.py WDH
    python scrape_transcripts.py HUIZ

Saves cookies to .cookies.json so you only solve CAPTCHA once.
"""

import sys
import re
import time
import random
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

DATA_ROOT = Path(r"F:\FinancialAdvisor")
COOKIE_FILE = Path(__file__).parent / ".cookies.json"


def log(msg):
    print(msg, flush=True)


def save_cookies(context):
    cookies = context.cookies()
    COOKIE_FILE.write_text(json.dumps(cookies, indent=2))
    log(">> Cookies saved.")


def load_cookies(context):
    if COOKIE_FILE.exists():
        cookies = json.loads(COOKIE_FILE.read_text())
        context.add_cookies(cookies)
        log(">> Cookies loaded (" + str(len(cookies)) + " cookies)")
        return True
    return False


def scrape_transcripts(ticker):
    ticker = ticker.upper()
    transcripts_dir = DATA_ROOT / ticker / "EarningsTranscripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    base_url = "https://discountingcashflows.com/company/" + ticker + "/transcripts"
    log("")
    log(">> Scraping transcripts for " + ticker + " ...")

    playwright = sync_playwright().start()

    # Launch regular browser (not persistent context)
    browser = playwright.chromium.launch(
        headless=False,
        args=['--disable-blink-features=AutomationControlled']
    )
    context = browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
    )

    # Load saved cookies
    cookies_loaded = load_cookies(context)

    page = context.new_page()

    try:
        log(">> Loading " + base_url + "/ ...")
        page.goto(base_url + "/", timeout=120000, wait_until="domcontentloaded")

        # Wait for the page to settle (handles WAF redirects/challenges)
        for _ in range(300):
            time.sleep(1)
            try:
                title = page.title()
                if ticker.lower() in title.lower() or 'transcript' in title.lower():
                    log(">> Page loaded: " + title)
                    break
            except Exception:
                pass
        else:
            log(">> Page took too long. Continuing anyway...")

        time.sleep(2)

        company_name = page.evaluate("""() => {
            const h1 = document.querySelector('h1');
            return h1 ? h1.innerText.replace('Earnings Call Transcripts','').trim() : '';
        }""") or ticker
        log(">> Company: " + company_name)

        # Discover quarters from isSelected() calls
        html = page.content()
        matches = re.findall(r"isSelected\('(\d{4})','(\d)'\)", html)
        seen = set()
        available = []
        for year, quarter in matches:
            key = year + "Q" + quarter
            if key not in seen:
                seen.add(key)
                available.append({"year": year, "quarter": quarter})

        if not available:
            links = page.evaluate("""() => {
                const qs = [], seen = new Set();
                document.querySelectorAll('a').forEach(a => {
                    const m = a.href.match(/transcripts..(d{4}).(d).$/);
                    if (m) { const k = m[1]+'Q'+m[2]; if (!seen.has(k)) { seen.add(k); qs.push({year:m[1],quarter:m[2]}); } }
                });
                return qs;
            }""")
            available = links

        log(">> Found " + str(len(available)) + " quarter(s)")

        downloaded = 0
        errors = 0
        safe_name = re.sub(r'[\\/:*?"<>|]', '', company_name) or ticker

        for i, q in enumerate(available):
            year = q["year"]
            quarter = q["quarter"]
            q_url = base_url + "/" + str(year) + "/" + str(quarter) + "/"
            label = "Q" + str(quarter) + " " + str(year)

            log("  [" + str(i+1) + "/" + str(len(available)) + "] " + label + "...")

            try:
                # Slow down to avoid rate limiting
                time.sleep(random.uniform(3, 6))

                page.goto(q_url, timeout=30000)
                page.wait_for_load_state("networkidle")
                time.sleep(random.uniform(2, 4))

                # Check if we got rate-limited
                page_text = page.evaluate("() => document.body.innerText.substring(0, 200)")
                if '403' in page_text or 'rate limit' in page_text.lower() or 'too many' in page_text.lower():
                    log("    -> RATE LIMITED! Waiting 60 seconds...")
                    time.sleep(60)
                    page.goto(q_url, timeout=30000)
                    page.wait_for_load_state("networkidle")
                    time.sleep(5)

                text = page.evaluate("""() => {
                    const main = document.querySelector('main');
                    if (!main) return '';
                    const clone = main.cloneNode(true);
                    clone.querySelectorAll('nav, header, button, select, script, style, [class*="modal"], [class*="footer"]').forEach(e => e.remove());
                    const raw = clone.innerText;
                    const lines = raw.split('\\n');
                    let start = 0;
                    for (let i = 0; i < lines.length; i++) {
                        if (lines[i].includes('Download PDF') || lines[i].includes('AI Insights')) { start = i + 1; break; }
                    }
                    // Clean up: remove empty lines, trim each line, remove excessive whitespace
                    return lines.slice(start)
                        .map(l => l.trim())
                        .filter(l => l.length > 0)
                        .join('\\n');
                }""")

                if text and len(text) > 200:
                    q_label = "Q" + str(quarter) + " " + str(year)
                    safe_q = re.sub(r'[\\/:*?"<>|]', '', q_label)
                    filename = safe_name + " - " + safe_q + ".md"
                    filename = re.sub(r'[\\/:*?"<>|]', '', filename)
                    out_path = transcripts_dir / filename

                    # Clean up excessive whitespace (costs tokens in AI)
                    text = re.sub(r'\n{3,}', '\n\n', text)  # max one blank line
                    text = re.sub(r'[ \t]+', ' ', text)      # collapse multiple spaces

                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write("# " + company_name + " - " + q_label + " Earnings Call Transcript\n\n")
                        f.write("Source: " + q_url + "\n\n---\n\n" + text)

                    downloaded += 1
                    log("    -> Saved (" + str(len(text)) + " chars)")
                else:
                    errors += 1
                    log("    -> Empty")

            except Exception as e:
                errors += 1
                log("    -> Error: " + str(e))

    finally:
        save_cookies(context)
        page.close()
        context.close()
        browser.close()
        log(">> Done. Cookies saved for next time.")
        playwright.stop()

    log("")
    log("=" * 50)
    log("Done! Downloaded " + str(downloaded) + " transcript(s)")
    log("Saved to: " + str(transcripts_dir))
    if errors:
        log(str(errors) + " quarter(s) skipped")
    log("=" * 50)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scrape_transcripts.py <TICKER>")
        print("Example: python scrape_transcripts.py HUIZ")
        sys.exit(1)

    scrape_transcripts(sys.argv[1])
