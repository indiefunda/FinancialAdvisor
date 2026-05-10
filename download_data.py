"""
Download SEC filings and extract ONLY the useful documents:
  - Annual reports: 10-K, 10-KT, 20-F
  - Quarterly reports: 10-Q
  - IPO/registration: F-1, F-1/A, S-1, S-1/A
  - Current reports (earnings): 8-K, 6-K (only EX-99.x press releases)
  - Material events: 8-K, 6-K main form (if they contain useful info)

Skips garbage: voting agreements, employment contracts, audit consents,
certifications (EX-31, EX-32), subsidiary lists, legal opinions, etc.

File naming is descriptive: "Ticker - 10-K 2023", "Ticker - Q1 2024 Earnings"
"""
import json
from datamule import Portfolio, Document
from pathlib import Path
from markdown_cleanup import cleanup_markdown_tables
from collections import defaultdict

# ============================================================
# CONFIG — edit these per ticker
# ============================================================
TICKER = "YB"
COMPANY_NAME = "Pagaya Technologies Ltd."  # optional override; if empty, uses SEC name

# Submission types to download (the wrapper forms)
SUBMISSION_TYPES = ["10-K", "10-Q", "20-F", "6-K", "8-K", "F-1", "F-1/A", "S-1", "S-1/A"]

# Paths
DATA_ROOT = Path(r"F:\FinancialAdvisor")
save_dir = DATA_ROOT / TICKER / "DataFromDataMule"
md_dir   = DATA_ROOT / TICKER / "DataFromDataMule_markdown"
save_dir.mkdir(parents=True, exist_ok=True)
md_dir.mkdir(parents=True, exist_ok=True)

# ============================================================
# DOCUMENT-TYPE FILTERING
# ============================================================
# These are ALWAYS skipped (technical/xbrl junk)
TECH_JUNK = {"GRAPHIC", "XML", "ZIP", "JSON", "EXCEL",
             "EX-101.SCH", "EX-101.CAL", "EX-101.DEF", "EX-101.LAB", "EX-101.PRE",
             "EX-FILING FEES"}

# These document types add NO investment value — skip them
VALUE_JUNK = {
    "EX-1.1",    # underwriting agreement
    "EX-2.6", "EX-2.9",  # merger agreements / plans
    "EX-3.1",    # articles of incorporation
    "EX-4.1", "EX-4.9", "EX-4.16", "EX-4.17", "EX-4.18",  # description of securities, voting agreements
    "EX-5.1",    # legal opinion
    "EX-8.1",    # tax opinion
    "EX-10.1", "EX-10.2", "EX-10.3", "EX-10.4",            # material contracts (employment, voting, etc.)
    "EX-10.17", "EX-10.18", "EX-10.19", "EX-10.20",
    "EX-10.21", "EX-10.22", "EX-10.23",
    "EX-12.1", "EX-12.2",  # certifications
    "EX-13.1", "EX-13.2", "EX-13.3", "EX-13.4",  # certifications
    "EX-15.1",  # auditor consent
    "EX-18.1",  # letter on changes in accounting
    "EX-19.1",  # certifications
    "EX-21.1",  # subsidiary list
    "EX-23.1", "EX-23.2", "EX-23.3",  # auditor consent
    "EX-31.1", "EX-31.2",  # SOX certifications
    "EX-32.1", "EX-32.2",  # SOX certifications
    "EX-97.1",  # clawback policy
}

# Document types that ARE useful on their own (beyond the main form)
# EX-99.x = press releases / earnings releases / shareholder letters — VERY useful
USEFUL_EXHIBITS = {"EX-99.1", "EX-99.2", "EX-99.3", "EX-99.4"}

# Submission types that are the "cover" form — we keep the main doc
COVER_FORMS = {"10-K", "10-Q", "20-F", "6-K", "8-K", "F-1", "F-1/A", "S-1", "S-1/A"}

# Annual report submission types
ANNUAL_FORMS = {"10-K", "10-KT", "20-F"}

# ============================================================
# NAMING LOGIC
# ============================================================
def make_filename(company, ticker, submission_type, doc_type, period, filing_date):
    """Generate a clean, descriptive filename."""
    ticker_part = ticker.upper()

    # Determine period label
    period_str = ""
    if submission_type in ANNUAL_FORMS and period and len(period) == 8:
        # 20-F with period 20221231 → "FY 2022"
        period_str = f"FY {period[:4]}"
    elif submission_type == "10-Q" and period and len(period) == 8:
        # 10-Q with period 20240930 → figure out which quarter
        m = int(period[4:6])
        q = {3: "Q1", 6: "Q2", 9: "Q3"}.get(m, f"Period {period[:4]}-{period[4:6]}")
        period_str = f"{q} {period[:4]}"
    elif submission_type in ("F-1", "F-1/A", "S-1", "S-1/A"):
        period_str = f"IPO {filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"
    elif doc_type in USEFUL_EXHIBITS:
        # EX-99.x inside 6-K or 8-K → earnings release
        period_str = f"{filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"
    elif period and period != filing_date:
        period_str = f"{period[:4]}-{period[4:6]}-{period[6:]}"
    else:
        period_str = f"{filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"

    # Content description
    if doc_type in COVER_FORMS:
        desc = submission_type
    elif doc_type in USEFUL_EXHIBITS:
        # Try to label what kind of EX-99 it is
        if submission_type in ("6-K", "8-K"):
            desc = "Earnings Release"
        else:
            desc = f"Press Release ({doc_type})"
    else:
        desc = doc_type

    # Sanitize: remove chars illegal in Windows filenames, including /
    fname = f"{ticker_part} - {desc} {period_str}.md"
    fname = re.sub(r'[\\/:*?"<>|]', ' ', fname)
    fname = re.sub(r'\s+', ' ', fname).strip()
    return fname


def is_useful_document(doc_type):
    """Return True if this document is worth converting to markdown."""
    if doc_type in TECH_JUNK or doc_type in VALUE_JUNK:
        return False
    if doc_type in COVER_FORMS or doc_type in USEFUL_EXHIBITS:
        return True
    return False


# ============================================================
# MAIN
# ============================================================
import re

print(f"{'='*60}")
print(f"  Downloading SEC filings for {TICKER}")
print(f"{'='*60}")

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

# ── Phase 2: Dedup — for IPO filings (F-1/A, S-1/A), keep ONLY the latest ──
# Group by (submission_base, doc_type) where submission_base = F-1, S-1 etc (strip /A)
IPO_BASES = {"F-1", "S-1"}  # F-1/A and S-1/A map to base F-1, S-1

def submission_base(st):
    for base in IPO_BASES:
        if st == base or st.startswith(base + "/"):
            return base
    return st

# Find the latest filing per (base, doc_type) for IPO forms
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

# Sort by filing_date for consistent output
filtered.sort(key=lambda c: c[1])

# ── Phase 3: Save to markdown ─────────────────────────────────────
saved_count = 0
skipped_count = 0

for submission_type, filing_date, doc_type, period, doc, company in filtered:
    md_content = doc.markdown
    if md_content is None:
        skipped_count += 1
        continue

    md_content = cleanup_markdown_tables(md_content)

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
