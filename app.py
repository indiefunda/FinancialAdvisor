# ============================================================
# SEC Data Downloader — Main App (Streamlit UI)
# ============================================================
# This is the main file you run to open the web app.
# It shows a page in your browser where you can:
#   1) Download SEC filings (10-K, 10-Q, etc.) for any stock ticker
#   2) Download earnings call transcripts for any stock ticker
#
# HOW TO RUN:
#   streamlit run app.py
#   (Or just double-click start.bat — it does this for you.)
#
# LIBRARIES USED:
#   - streamlit  → makes the web UI (buttons, text inputs, progress bars)
#   - datamule   → talks to the SEC's EDGAR API to download filings
#   - pathlib    → a modern Python way to handle file/folder paths
#   - subprocess → lets us run scrape_transcripts.py as a separate process
# ============================================================

import streamlit as st
from datamule import Portfolio
from pathlib import Path
import sys

# ── Page title shown at the top of the browser window ──────────────
st.title("SEC Data Downloader")

# ── Where to save downloaded files ─────────────────────────────────
# We put everything in the same folder as this script.
# That way it works on ANY computer — no hardcoded paths.
base_save_path = Path(__file__).parent.resolve()

# ============================================================
# SECTION 1 — SEC FILING DOWNLOADER
# ============================================================

st.subheader("Step 0: Select SEC form types to download")

# ── List of all SEC form types the user can choose from ────────────
# Grouped by category so it's easier to read.
# Each entry is (form_code, description).
form_categories = {
    "IPO / Registration": [
        ("F-1", "Foreign IPO registration statement"),
        ("F-1/A", "Foreign IPO registration (amended)"),
        ("S-1", "US IPO registration statement"),
        ("S-1/A", "US IPO registration (amended)"),
    ],
    "Annual Reports": [
        ("10-K", "Annual report (US companies)"),
        ("20-F", "Annual report (foreign companies)"),
    ],
    "Quarterly / Current": [
        ("10-Q", "Quarterly report (US)"),
        ("6-K", "Current report (foreign cos — contains earnings)"),
        ("8-K", "Current report (US cos — contains earnings)"),
    ],
    "Shelf / Business Combos": [
        ("F-3", "Foreign shelf registration"),
        ("F-3/A", "Foreign shelf registration (amended)"),
        ("S-3", "US shelf registration"),
        ("S-3/A", "US shelf registration (amended)"),
        ("F-4", "Foreign business combo registration"),
        ("F-4/A", "Foreign business combo (amended)"),
        ("S-4", "US business combo registration"),
        ("S-4/A", "US business combo (amended)"),
    ],
    "Prospectuses": [
        ("424B1", "Prospectus (new issue)"),
        ("424B2", "Prospectus (supplement)"),
        ("424B3", "Prospectus (combined)"),
        ("424B4", "Prospectus (final)"),
    ],
}

# ── Build a lookup dictionary for the dropdown menu ────────────────
# We show "10-K — Annual report (US companies)" in the dropdown,
# but internally we need to know the real code is "10-K".
form_display_labels = {}     # "10-K" -> "10-K — Annual report (US companies)"
form_code_to_label = {}      # "10-K — Annual report (US companies)" -> "10-K"
for category, forms in form_categories.items():
    for code, desc in forms:
        label = f"{code} — {desc}"
        form_display_labels[code] = label
        form_code_to_label[label] = code

# List of all labels for the multiselect dropdown
all_form_labels = list(form_display_labels.values())

# Default selection — the most commonly used forms
default_codes = ["20-F", "10-K", "10-Q", "6-K", "8-K"]
default_labels = [form_display_labels[c] for c in default_codes if c in form_display_labels]

# ── Streamlit multiselect dropdown ─────────────────────────────────
# The user ticks which form types they want to download.
selected_labels = st.multiselect(
    "Choose which submission types to download:",
    options=all_form_labels,
    default=default_labels,
)

# Convert the displayed labels back to short form codes (e.g. "10-K")
selected_form_codes = [form_code_to_label[lbl] for lbl in selected_labels]

# ── Optional: type in extra form types manually ────────────────────
# In case the dropdown doesn't have what you need.
custom_forms = st.text_input("Additional form types (comma-separated, e.g. F-1, F-1/A, S-1, 424B4):", "")
if custom_forms:
    extra = [f.strip().upper() for f in custom_forms.split(",") if f.strip()]
    # dict.fromkeys() removes duplicates while preserving order
    selected_form_codes = list(dict.fromkeys(selected_form_codes + extra))

# ── Stock ticker input ────────────────────────────────────────────
ticker = st.text_input("Enter Stock Ticker (e.g., YB, AAPL):", "").upper()

# ── Number of years to include ────────────────────────────────────
# A company might have 30 years of filings — you usually only want
# the last 2 years. Default is 2 (current year + last year).
col1, col2 = st.columns([1, 3])
with col1:
    num_years = st.number_input(
        "Years of history:",
        min_value=1,
        max_value=30,
        value=2,
        help="Number of recent years to include (e.g., 2 = current year + last year). Applies to both SEC filings and transcripts."
    )
with col2:
    st.caption("")  # empty spacer so layout looks nice

# ── The big download button ────────────────────────────────────────
download_btn = st.button("Download & Convert to Markdown", type="primary", use_container_width=True)

# ── When user clicks the download button ───────────────────────────
if download_btn and ticker:
    if not selected_form_codes:
        st.warning("Select at least one form type.")
    else:
        # Create output folders for this ticker
        save_dir = base_save_path / ticker / "DataFromDataMule"
        md_dir = base_save_path / ticker / "DataFromDataMule_markdown"
        save_dir.mkdir(parents=True, exist_ok=True)
        md_dir.mkdir(parents=True, exist_ok=True)

        form_count = len(selected_form_codes)
        success_count = 0
        fail_count = 0

        # st.status() shows an expandable progress box
        with st.status("Downloading and converting...", expanded=True) as status:
            # ── Download each selected form type from the SEC ──────────
            for i, form in enumerate(selected_form_codes):
                status.update(label=f"[{i+1}/{form_count}] Downloading {form}...")
                try:
                    # Portfolio is from the 'datamule' library.
                    # It connects to SEC EDGAR and downloads filings.
                    portfolio = Portfolio(str(save_dir))
                    portfolio.download_submissions(
                        ticker=ticker,
                        submission_type=[form],
                        provider="sec",
                    )
                    success_count += 1
                except Exception as e:
                    st.warning(f"{form}: SEC API error (this company may not have this form type)")
                    fail_count += 1
                    continue

            status.update(label="Converting to Markdown...")

            # ── Now load everything we downloaded ─────────────────────
            portfolio = Portfolio(str(save_dir))

            # ── FILTERING RULES ───────────────────────────────────────
            # SEC filings contain many "exhibits" (extra documents).
            # Most are useless for investment research. We only keep
            # the ones that actually contain useful information.

            # Technical junk — files that are just data formats, not readable text
            TECH_JUNK = {"GRAPHIC","XML","ZIP","JSON","EXCEL",
                         "EX-101.SCH","EX-101.CAL","EX-101.DEF","EX-101.LAB","EX-101.PRE",
                         "EX-FILING FEES"}

            # Value junk — legal/administrative documents with no investment insight
            VALUE_JUNK = {
                "EX-1.1",    # underwriting agreement (legal boilerplate)
                "EX-2.6", "EX-2.9",  # merger agreements
                "EX-3.1",    # articles of incorporation
                "EX-4.1", "EX-4.9", "EX-4.16", "EX-4.17", "EX-4.18",  # security descriptions, voting agreements
                "EX-5.1",    # legal opinion from lawyers
                "EX-8.1",    # tax opinion
                "EX-10.1", "EX-10.2", "EX-10.3", "EX-10.4",            # material contracts (employment, etc.)
                "EX-10.17", "EX-10.18", "EX-10.19", "EX-10.20",
                "EX-10.21", "EX-10.22", "EX-10.23",
                "EX-12.1", "EX-12.2",  # certifications
                "EX-13.1", "EX-13.2", "EX-13.3", "EX-13.4",  # certifications
                "EX-15.1",  # auditor consent
                "EX-18.1",  # letter on accounting changes
                "EX-19.1",  # certifications
                "EX-21.1",  # list of subsidiary companies (not useful for analysis)
                "EX-23.1", "EX-23.2", "EX-23.3",  # auditor consent
                "EX-31.1", "EX-31.2",  # SOX certifications (Sarbanes-Oxley — boilerplate)
                "EX-32.1", "EX-32.2",  # SOX certifications
                "EX-97.1",  # clawback policy
            }

            # Exhibits that ARE useful — press releases and earnings releases
            USEFUL_EXHIBITS = {"EX-99.1","EX-99.2","EX-99.3","EX-99.4"}

            # The main "cover" form types — the actual annual/quarterly reports
            COVER_FORMS = {"10-K","10-Q","20-F","6-K","8-K","F-1","F-1/A","S-1","S-1/A"}

            # Annual report forms (used for filename generation)
            ANNUAL_FORMS = {"10-K","10-KT","20-F"}

            # IPO base forms (used for deduplication logic)
            IPO_BASES = {"F-1","S-1"}

            def is_useful(dt):
                """Return True if this document type is worth keeping."""
                if dt in TECH_JUNK or dt in VALUE_JUNK:
                    return False
                if dt in COVER_FORMS or dt in USEFUL_EXHIBITS:
                    return True
                return False

            def sub_base(st):
                """Strip the '/A' suffix from IPO forms for dedup grouping."""
                for b in IPO_BASES:
                    if st == b or st.startswith(b + "/"):
                        return b
                return st

            def make_fname(ticker, sub_type, doc_type, period, filing_date):
                """
                Generate a clean, descriptive filename.
                Examples:
                  - "AAPL - 10-K FY 2024.md"
                  - "YB - Q2 2025.md"
                  - "HUIZ - Earnings Release 2025-03-15.md"
                """
                # Determine the period/date string for the filename
                if sub_type in ANNUAL_FORMS and period and len(period) == 8:
                    # period is like "20241231" → "FY 2024"
                    ps = f"FY {period[:4]}"
                elif sub_type == "10-Q" and period and len(period) == 8:
                    # Figure out which quarter from the month
                    m = int(period[4:6])
                    q = {3: "Q1", 6: "Q2", 9: "Q3"}.get(m, f"Period {period[:4]}-{period[4:6]}")
                    ps = f"{q} {period[:4]}"
                elif sub_type in ("F-1","F-1/A","S-1","S-1/A"):
                    # IPO filings use the filing date
                    ps = f"IPO {filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"
                elif doc_type in USEFUL_EXHIBITS:
                    # Press releases use the filing date
                    ps = f"{filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"
                elif period and period != filing_date:
                    ps = f"{period[:4]}-{period[4:6]}-{period[6:]}"
                else:
                    ps = f"{filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"

                # Determine the document description
                if doc_type in COVER_FORMS:
                    desc = sub_type
                elif doc_type in USEFUL_EXHIBITS:
                    desc = "Earnings Release" if sub_type in ("6-K","8-K") else f"Press Release ({doc_type})"
                else:
                    desc = doc_type

                # Build final filename and remove characters illegal in Windows filenames
                fname = f"{ticker} - {desc} {ps}.md"
                import re
                fname = re.sub(r'[\\/:*?"<>|]', ' ', fname)
                fname = re.sub(r'\s+', ' ', fname).strip()
                return fname

            # ── Collect all candidate documents ───────────────────────
            candidates = []
            for submission in portfolio:
                meta = submission.metadata.content
                sub_type = meta["type"]
                period = meta.get("period", "")
                filing_date = meta["filing-date"]
                for doc in submission:
                    doc_type = doc.type
                    if not is_useful(doc_type):
                        continue
                    if doc.markdown is None:
                        continue
                    candidates.append((sub_type, filing_date, doc_type, period, doc))

            # ── Deduplicate IPO filings ───────────────────────────────
            # Companies often file multiple versions of F-1/A or S-1/A
            # (amended registrations). We only keep the latest one.
            latest_ipo = {}
            filtered = []
            for c in candidates:
                st_, fd, dt, p, doc = c
                base = sub_base(st_)
                if base in IPO_BASES:
                    key = (base, dt)
                    if key not in latest_ipo or fd > latest_ipo[key][0]:
                        latest_ipo[key] = (fd, c)
                else:
                    filtered.append(c)
            for (base, dt), (fd, c) in latest_ipo.items():
                filtered.append(c)
            filtered.sort(key=lambda c: c[1])

            # ── Filter by number of years ─────────────────────────────
            from datetime import date
            current_year = date.today().year
            cutoff_year = current_year - num_years + 1  # e.g. 2 years → 2025..2026
            def get_year_from_candidate(c):
                st_, fd, dt, p, doc = c
                # Use the period year (e.g. "20241231" → 2024) if available,
                # otherwise fall back to filing date year
                if p and len(p) == 8:
                    return int(p[:4])
                return int(fd[:4])
            filtered = [c for c in filtered if get_year_from_candidate(c) >= cutoff_year]

            # ── Convert filtered documents to Markdown files ──────────
            converted = 0
            skipped = 0
            for sub_type, filing_date, doc_type, period, doc in filtered:
                md_content = doc.markdown
                if md_content is None:
                    skipped += 1
                    continue
                fname = make_fname(ticker, sub_type, doc_type, period, filing_date)
                out_path = md_dir / fname
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                converted += 1

            status.update(label="Complete", state="complete")

        # ── Show summary to the user ──────────────────────────────────
        summary = f"Downloaded {success_count}/{form_count} form types"
        if fail_count:
            summary += f" ({fail_count} skipped — not filed by this company)"
        summary += f". Converted {converted} documents"
        if skipped:
            summary += f" ({skipped} unparseable skipped)"
        summary += "."
        st.success(summary)
        st.info(f"Markdown: `{md_dir}`\n\nRaw data: `{save_dir}`")

elif download_btn and not ticker:
    st.warning("Please enter a ticker first.")

# ============================================================
# SECTION 2 — EARNINGS TRANSCRIPTS DOWNLOADER
# ============================================================
# This downloads earnings call transcripts from
# discountingcashflows.com — a free website for personal use.
# It uses Playwright (a browser automation tool) to scrape the data.
# ============================================================

st.divider()
st.subheader("Earnings Call Transcripts")
st.caption("Source: discountingcashflows.com - free for personal use")

with st.expander("How to use", expanded=False):
    st.markdown("""
    **First time only:** A browser window will pop up. It may show a WAF
    security challenge. Just wait — it resolves automatically, or you can
    click "Verify" if needed.
    
    After that, cookies are saved. Future runs will work silently.
    """)

# ── Ticker input for transcripts (pre-filled from the SEC ticker) ──
transcripts_ticker = st.text_input("Ticker for transcripts:", value=ticker or "", key="transcripts_ticker").upper()

transcripts_btn = st.button("Download Earnings Transcripts", type="primary", use_container_width=True)

# ── When user clicks the transcript download button ─────────────────
if transcripts_btn and transcripts_ticker:
    transcripts_dir = base_save_path / transcripts_ticker / "EarningsTranscripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    import subprocess
    import sys as _sys

    # Path to the transcript scraper script (sits next to this file)
    script_path = Path(__file__).parent / "scrape_transcripts.py"
    # The same Python interpreter that's running this script
    python_exe = _sys.executable

    with st.status("Running scraper via your browser...", expanded=True) as status:
        try:
            # Run scrape_transcripts.py as a separate process.
            # We pass the ticker and number of years as arguments.
            result = subprocess.run(
                [python_exe, str(script_path), transcripts_ticker, str(num_years)],
                capture_output=True, text=True, timeout=300  # 5 minute timeout
            )
            status.update(label="Complete", state="complete")
            if result.returncode == 0:
                st.success(result.stdout)
            else:
                st.error(result.stderr or result.stdout)
        except subprocess.TimeoutExpired:
            st.warning("Timed out after 5 minutes. The ticker may have too many quarters.")
        except Exception as e:
            st.error("Failed: " + str(e))

elif transcripts_btn and not transcripts_ticker:
    st.warning("Please enter a ticker first.")
