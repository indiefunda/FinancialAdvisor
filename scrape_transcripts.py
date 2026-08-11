# ============================================================
# Earnings Call Transcript Scraper
# ============================================================
# This script downloads earnings call transcripts from
# discountingcashflows.com — a free website for personal use.
#
# It uses Playwright, which is a tool that controls a real
# web browser (Chrome) automatically, like a robot.
#
# USAGE:
#   python scrape_transcripts.py AAPL     (all years)
#   python scrape_transcripts.py AAPL 2   (last 2 years only)
#
# FIRST TIME:
#   A browser window will pop up. You might see a security
#   check (WAF challenge). Just wait — it resolves automatically.
#   After that, cookies are saved so future runs work silently.
#
# LIBRARIES USED:
#   - playwright   → controls a real Chrome browser to scrape websites
#   - pathlib      → modern Python way to handle file paths
#   - re           → regular expressions (pattern matching in text)
#   - json         → read/write cookies in a text file
#   - random, time → add delays so we don't get rate-limited
# ============================================================

import sys
import re
import time
import random
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

# ── Where to save data ─────────────────────────────────────────────
# We put everything in the same folder as this script.
# That way it works on ANY computer.
DATA_ROOT = Path(__file__).parent.resolve()

# ── Cookie file ────────────────────────────────────────────────────
# Cookies are like little ID cards the website gives your browser.
# We save them so next time the website remembers us and skips
# the security check.
COOKIE_FILE = DATA_ROOT / ".cookies.json"


def log(msg):
    """Print a message immediately (flush=True forces it to show up right away)."""
    print(msg, flush=True)


def save_cookies(context):
    """
    Save the browser's cookies to a JSON file.
    'context' is the Playwright browser context (like a browser session).
    """
    cookies = context.cookies()
    COOKIE_FILE.write_text(json.dumps(cookies, indent=2))
    log(">> Cookies saved.")


def load_cookies(context):
    """
    Load previously saved cookies into the browser.
    Returns True if cookies were loaded, False if no cookie file exists.
    """
    if COOKIE_FILE.exists():
        cookies = json.loads(COOKIE_FILE.read_text())
        context.add_cookies(cookies)
        log(">> Cookies loaded (" + str(len(cookies)) + " cookies)")
        return True
    return False


def scrape_transcripts(ticker, num_years=30):
    """
    Main function that scrapes earnings call transcripts.
    
    Args:
        ticker: Stock ticker symbol (e.g. "AAPL", "YB")
        num_years: How many recent years of transcripts to download
                   (default 30 = all available)
    """
    ticker = ticker.upper()

    # Create the folder for this ticker's transcripts
    transcripts_dir = DATA_ROOT / ticker / "EarningsTranscripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    # The URL pattern for transcripts on discountingcashflows.com
    base_url = "https://discountingcashflows.com/company/" + ticker + "/transcripts"
    log("")
    log(">> Scraping transcripts for " + ticker + " ...")

    # ── Start Playwright (controls a real Chrome browser) ──────────
    playwright = sync_playwright().start()

    # Launch Chrome. headless=False means you can SEE the browser window.
    browser = playwright.chromium.launch(
        headless=False,
        args=['--disable-blink-features=AutomationControlled']
    )
    
    # Create a browsing context (like an incognito window with settings)
    context = browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
    )

    # Load saved cookies (if any) so we don't have to solve CAPTCHA again
    cookies_loaded = load_cookies(context)

    # Open a new tab
    page = context.new_page()

    try:
        # ── Load the transcripts page ──────────────────────────────
        log(">> Loading " + base_url + "/ ...")
        page.goto(base_url + "/", timeout=120000, wait_until="domcontentloaded")

        # ── Wait for the page to fully load ────────────────────────
        # Some sites have WAF (Web Application Firewall) challenges
        # that redirect you first. We wait up to 5 minutes for the
        # real page to appear.
        for _ in range(300):  # 300 seconds = 5 minutes
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

        # ── Extract the company name from the page ─────────────────
        company_name = page.evaluate("""() => {
            const h1 = document.querySelector('h1');
            return h1 ? h1.innerText.replace('Earnings Call Transcripts','').trim() : '';
        }""") or ticker
        log(">> Company: " + company_name)

        # ── Discover available quarters ────────────────────────────
        # The website has a dropdown with year/quarter options.
        # We look for JavaScript calls like isSelected('2024','1')
        # which tell us what quarters exist.
        html = page.content()
        matches = re.findall(r"isSelected\('(\d{4})','(\d)'\)", html)
        seen = set()
        available = []
        for year, quarter in matches:
            key = year + "Q" + quarter
            if key not in seen:
                seen.add(key)
                available.append({"year": year, "quarter": quarter})

        # Fallback: if the above didn't work, look for links instead
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

        # ── Filter by number of recent years ───────────────────────
        # If user chose 2 years, we only keep quarters from 2025 and 2026.
        from datetime import date
        current_year = date.today().year
        cutoff_year = current_year - num_years + 1
        available = [q for q in available if int(q["year"]) >= cutoff_year]
        log(">> Keeping " + str(len(available)) + " quarter(s) (last " + str(num_years) + " year(s))")

        downloaded = 0
        errors = 0

        # Remove characters that aren't allowed in Windows filenames
        safe_name = re.sub(r'[\\/:*?"<>|]', '', company_name) or ticker

        # ── Download each quarter's transcript ─────────────────────
        for i, q in enumerate(available):
            year = q["year"]
            quarter = q["quarter"]
            q_url = base_url + "/" + str(year) + "/" + str(quarter) + "/"
            label = "Q" + str(quarter) + " " + str(year)

            log("  [" + str(i+1) + "/" + str(len(available)) + "] " + label + "...")

            try:
                # Slow down between requests to avoid getting blocked
                time.sleep(random.uniform(3, 6))

                # Go to the page for this specific quarter
                page.goto(q_url, timeout=30000)
                page.wait_for_load_state("networkidle")
                time.sleep(random.uniform(2, 4))

                # Check if we got rate-limited (HTTP 403 or "too many requests")
                page_text = page.evaluate("() => document.body.innerText.substring(0, 200)")
                if '403' in page_text or 'rate limit' in page_text.lower() or 'too many' in page_text.lower():
                    log("    -> RATE LIMITED! Waiting 60 seconds...")
                    time.sleep(60)
                    page.goto(q_url, timeout=30000)
                    page.wait_for_load_state("networkidle")
                    time.sleep(5)

                # ── Extract the transcript text from the page ──────
                text = page.evaluate("""() => {
                    // Find the <main> element (where the content is)
                    const main = document.querySelector('main');
                    if (!main) return '';
                    
                    // Clone it so we don't mess up the live page
                    const clone = main.cloneNode(true);
                    
                    // Remove navigation, headers, buttons, scripts — 
                    // we only want the actual transcript text
                    clone.querySelectorAll('nav, header, button, select, script, style, [class*="modal"], [class*="footer"]').forEach(e => e.remove());
                    
                    const raw = clone.innerText;
                    const lines = raw.split('\\n');
                    
                    // Skip the "Download PDF" and "AI Insights" header lines
                    let start = 0;
                    for (let i = 0; i < lines.length; i++) {
                        if (lines[i].includes('Download PDF') || lines[i].includes('AI Insights')) { start = i + 1; break; }
                    }
                    
                    // Clean up: remove empty lines, trim whitespace
                    return lines.slice(start)
                        .map(l => l.trim())
                        .filter(l => l.length > 0)
                        .join('\\n');
                }""")

                # Only save if we got meaningful text (more than 200 characters)
                if text and len(text) > 200:
                    q_label = "Q" + str(quarter) + " " + str(year)
                    safe_q = re.sub(r'[\\/:*?"<>|]', '', q_label)
                    filename = safe_name + " - " + safe_q + ".md"
                    filename = re.sub(r'[\\/:*?"<>|]', '', filename)
                    out_path = transcripts_dir / filename

                    # Clean up excessive whitespace (costs tokens in AI analysis)
                    text = re.sub(r'\n{3,}', '\n\n', text)  # max one blank line
                    text = re.sub(r'[ \t]+', ' ', text)      # collapse multiple spaces

                    # Write the transcript as a Markdown file
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
        # ── Cleanup ────────────────────────────────────────────────
        # Save cookies so next time is faster, then close everything.
        save_cookies(context)
        page.close()
        context.close()
        browser.close()
        log(">> Done. Cookies saved for next time.")
        playwright.stop()

    # ── Print summary ──────────────────────────────────────────────
    log("")
    log("=" * 50)
    log("Done! Downloaded " + str(downloaded) + " transcript(s)")
    log("Saved to: " + str(transcripts_dir))
    if errors:
        log(str(errors) + " quarter(s) skipped")
    log("=" * 50)


# ── Entry point ────────────────────────────────────────────────────
# This runs when you execute the script directly from the command line.
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scrape_transcripts.py <TICKER> [NUM_YEARS]")
        print("Example: python scrape_transcripts.py HUIZ 2")
        sys.exit(1)

    # Second argument is optional — number of years (default: 30 = all)
    num_years = int(sys.argv[2]) if len(sys.argv) >= 3 else 30
    scrape_transcripts(sys.argv[1], num_years)
