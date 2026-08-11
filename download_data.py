# ============================================================
# SEC Filing Downloader — Standalone CLI Version
# ============================================================
# This script downloads SEC filings for a single ticker and
# converts them to clean Markdown files.
#
# Unlike app.py (which has a web UI), this is a command-line
# script. You edit the TICKER at the top and run it.
#
# USAGE:
#   1. Edit TICKER and SUBMISSION_TYPES below
#   2. Run: python download_data.py
#
# WHAT IT DOWNLOADS:
#   - Annual reports: 10-K, 10-KT, 20-F
#   - Quarterly reports: 10-Q
#   - IPO/registration: F-1, F-1/A, S-1, S-1/A
#   - Current reports (earnings): 8-K, 6-K
#
# WHAT IT SKIPS:
#   - XBRL/XML data files (computer-readable but not human-readable)
#   - Legal opinions (EX-5.1, EX-8.1)
#   - SOX certifications (EX-31, EX-32 — boilerplate)
#   - Employment contracts, voting agreements, subsidiary lists, etc.
#
# FILE NAMING:
#   - "AAPL - 10-K FY 2024.md"
#   - "YB - Q2 2025.md"
#   - "HUIZ - Earnings Release 2025-03-15.md"
#
# LIBRARIES USED:
#   - datamule    → connects to SEC EDGAR API to download filings
#   - pathlib     → modern Python way to handle file/folder paths
#   - re          → regular expressions (text pattern matching)
#   - json        → read/write JSON data from SEC metadata
# ============================================================

import json
from datamule import Portfolio, Document
from pathlib import Path
from markdown_cleanup import cleanup_markdown_tables
from collections import defaultdict
import re

# ============================================================
# CONFIG — EDIT THESE BEFORE RUNNING
# ============================================================

# The stock ticker you want to download filings for
TICKER = "YB"

# Optional: override the company name (if empty, uses the name from SEC)
COMPANY_NAME = "Pagaya Technologies Ltd."

# Which SEC form types to download
SUBMISSION_TYPES = ["10-K", "10-Q", "20-F", "6-K", "8-K", "F-1", "F-1/A", "S-1", "S-1/A"]

# ============================================================
# PATHS — where to save files
# ============================================================
# We put everything in the same folder as this script.
# That way it works on ANY computer.
DATA_ROOT = Path(__file__).parent.resolve()
save_dir = DATA_ROOT / TICKER / "DataFromDataMule"           # Raw SEC data
md_dir   = DATA_ROOT / TICKER / "DataFromDataMule_markdown"  # Converted .md files
save_dir.mkdir(parents=True, exist_ok=True)
md_dir.mkdir(parents=True, exist_ok=True)

# ============================================================
# DOCUMENT-TYPE FILTERING
# ============================================================
# SEC filings contain many "exhibits" (extra documents attached to
# the main filing). Most are useless for investment research.
# We define which ones to keep and which to skip.

# ── Technical junk ──────────────────────────────────────────────────
# These are data formats, not readable documents.
TECH_JUNK = {"GRAPHIC", "XML", "ZIP", "JSON", "EXCEL",
             "EX-101.SCH", "EX-101.CAL", "EX-101.DEF", "EX-101.LAB", "EX-101.PRE",
             "EX-FILING FEES"}

# ── Value junk ──────────────────────────────────────────────────────
# These are legal/administrative documents. They contain no useful
# information about the company's financial performance.
VALUE_JUNK = {
    "EX-1.1",    # underwriting agreement — legal boilerplate about stock issuance
    "EX-2.6", "EX-2.9",  # merger agreements — long legal docs, not financial data
    "EX-3.1",    # articles of incorporation — company's legal charter
    "EX-4.1", "EX-4.9", "EX-4.16", "EX-4.17", "EX-4.18",  # security descriptions, voting agreements
    "EX-5.1",    # legal opinion — lawyer says "this filing is legit"
    "EX-8.1",    # tax opinion — tax lawyer's letter
    "EX-10.1", "EX-10.2", "EX-10.3", "EX-10.4",            # material contracts (employment, etc.)
    "EX-10.17", "EX-10.18", "EX-10.19", "EX-10.20",
    "EX-10.21", "EX-10.22", "EX-10.23",
    "EX-12.1", "EX-12.2",  # certifications about fees
    "EX-13.1", "EX-13.2", "EX-13.3", "EX-13.4",  # certifications
    "EX-15.1",  # auditor consent — accountant says "we're ok with this"
    "EX-18.1",  # letter about changes in accounting methods
    "EX-19.1",  # certifications
    "EX-21.1",  # subsidiary list — just a list of company names
    "EX-23.1", "EX-23.2", "EX-23.3",  # auditor consent
    "EX-31.1", "EX-31.2",  # SOX certifications — CEO/CFO signing off (boilerplate)
    "EX-32.1", "EX-32.2",  # SOX certifications
    "EX-97.1",  # clawback policy — rules about recovering executive pay
}

# ── Useful exhibits ─────────────────────────────────────────────────
# EX-99.x documents are press releases and earnings releases.
# These are VERY useful — they contain management's commentary.
USEFUL_EXHIBITS = {"EX-99.1", "EX-99.2", "EX-99.3", "EX-99.4"}

# ── Cover forms ────────────────────────────────────────────────────
# These are the main filing documents themselves (the annual/quarterly report).
COVER_FORMS = {"10-K", "10-Q", "20-F", "6-K", "8-K", "F-1", "F-1/A", "S-1", "S-1/A"}

# ── Annual report forms ────────────────────────────────────────────
ANNUAL_FORMS = {"10-K", "10-KT", "20-F"}

# ============================================================
# NAMING LOGIC
# ============================================================

def make_filename(company, ticker, submission_type, doc_type, period, filing_date):
    """
    Generate a clean, descriptive filename for a document.
    
    Examples:
      - "AAPL - 10-K FY 2024.md"           (annual report)
      - "YB - Q2 2025.md"                  (quarterly report)
      - "HUIZ - Earnings Release 2025-03-15.md"  (press release)
      - "MSTR - IPO 2024-06-15.md"         (IPO filing)
    """
    ticker_part = ticker.upper()

    # ── Determine the period/date label ────────────────────────────
    period_str = ""
    if submission_type in ANNUAL_FORMS and period and len(period) == 8:
        # period is like "20241231" → extract year → "FY 2024"
        period_str = f"FY {period[:4]}"
    elif submission_type == "10-Q" and period and len(period) == 8:
        # Figure out which quarter from the month number
        # Month 3 = Q1, Month 6 = Q2, Month 9 = Q3
        m = int(period[4:6])
        q = {3: "Q1", 6: "Q2", 9: "Q3"}.get(m, f"Period {period[:4]}-{period[4:6]}")
        period_str = f"{q} {period[:4]}"
    elif submission_type in ("F-1", "F-1/A", "S-1", "S-1/A"):
        # IPO filings use the date they were filed
        period_str = f"IPO {filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"
    elif doc_type in USEFUL_EXHIBITS:
        # Press releases use the filing date
        period_str = f"{filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"
    elif period and period != filing_date:
        period_str = f"{period[:4]}-{period[4:6]}-{period[6:]}"
    else:
        period_str = f"{filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"

    # ── Determine the content description ──────────────────────────
    if doc_type in COVER_FORMS:
        desc = submission_type
    elif doc_type in USEFUL_EXHIBITS:
        # Label earnings releases clearly
        if submission_type in ("6-K", "8-K"):
            desc = "Earnings Release"
        else:
            desc = f"Press Release ({doc_type})"
    else:
        desc = doc_type

    # ── Build filename and sanitize ────────────────────────────────
    # Windows doesn't allow these characters in filenames: \ / : * ? " < > |
    fname = f"{ticker_part} - {desc} {period_str}.md"
    fname = re.sub(r'[\\/:*?"<>|]', ' ', fname)
    fname = re.sub(r'\s+', ' ', fname).strip()
    return fname


def is_useful_document(doc_type):
    """
    Return True if this document type is worth converting to markdown.
    
    Checks against the junk lists defined above.
    """
    if doc_type in TECH_JUNK or doc_type in VALUE_JUNK:
        return False
    if doc_type in COVER_FORMS or doc_type in USEFUL_EXHIBITS:
        return True
    return False


# ============================================================
# MAIN
# ============================================================

print(f"{'='*60}")
print(f"  Downloading SEC filings for {TICKER}")
print(f"{'='*60}")

# ── Step 1: Download from SEC ──────────────────────────────────────
# Portfolio is from the 'datamule' library.
# It connects to SEC EDGAR and downloads all the filings we asked for.
portfolio = Portfolio(str(save_dir))

portfolio.download_submissions(
    ticker=TICKER,
    submission_type=SUBMISSION_TYPES,
    provider="sec",
)

print(f"\n{'='*60}")
print(f"  Extracting useful documents → Markdown")
print(f"{'='*60}")

# ── Phase 1: Collect all candidate documents ──────────────────────
# Go through every submission and every document inside it,
# and collect the ones we want to keep.
candidates = []  # list of (submission_type, filing_date, doc_type, period, doc_obj, company)

for submission in portfolio:
    meta = submission.metadata.content
    company = meta["filer"]["company-data"]["conformed-name"]
    submission_type = meta["type"]
    period = meta.get("period", "")
    filing_date = meta["filing-date"]

    for doc in submission:
        doc_type = doc.type
        if not is_useful_document(doc_type):
            continue
        if doc.markdown is None:
            continue
        candidates.append((submission_type, filing_date, doc_type, period, doc, company))

# ── Phase 2: Dedup IPO filings ────────────────────────────────────
# Companies often file multiple amended versions of IPO registrations
# (e.g. F-1/A, S-1/A). We only keep the LATEST version of each.
IPO_BASES = {"F-1", "S-1"}  # F-1/A and S-1/A map to base F-1, S-1

def submission_base(st):
    """Strip the '/A' suffix to get the base form type."""
    for base in IPO_BASES:
        if st == base or st.startswith(base + "/"):
            return base
    return st

# Find the latest filing per (base_form, doc_type) for IPO forms
latest_ipo = {}  # (base, doc_type) -> (filing_date, candidate)
filtered = []
for c in candidates:
    st, filing_date, doc_type, period, doc, company = c
    base = submission_base(st)
    if base in IPO_BASES:
        key = (base, doc_type)
        if key not in latest_ipo or filing_date > latest_ipo[key][0]:
            latest_ipo[key] = (filing_date, c)
    else:
        filtered.append(c)

# Add back only the latest IPO versions
for (base, doc_type), (filing_date, c) in latest_ipo.items():
    filtered.append(c)

# Sort everything by filing date so output is chronological
filtered.sort(key=lambda c: c[1])

# ── Phase 3: Save all kept documents as Markdown files ────────────
saved_count = 0
skipped_count = 0

for submission_type, filing_date, doc_type, period, doc, company in filtered:
    md_content = doc.markdown
    if md_content is None:
        skipped_count += 1
        continue

    # Clean up messy tables in the markdown
    md_content = cleanup_markdown_tables(md_content)

    # Generate a descriptive filename
    fname = make_filename(company, TICKER, submission_type, doc_type, period, filing_date)
    out_path = md_dir / fname

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"  ✓ {fname}")
    saved_count += 1

print(f"\n{'='*60}")
print(f"  DONE! Saved: {saved_count}  Skipped: {skipped_count} junk docs")
print(f"  Output: {md_dir}")
print(f"{'='*60}")
