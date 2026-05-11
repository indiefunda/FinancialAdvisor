import streamlit as st
from datamule import Portfolio
from pathlib import Path
import sys

st.title("SEC Data Downloader")

base_save_path = Path(r"F:\FinancialAdvisor")

# ============================================================
# SEC FILING DOWNLOADER
# ============================================================

st.subheader("Step 0: Select SEC form types to download")
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

# Build display labels: "10-K — Annual report (US companies)"
form_display_labels = {}
form_code_to_label = {}
for category, forms in form_categories.items():
    for code, desc in forms:
        label = f"{code} — {desc}"
        form_display_labels[code] = label
        form_code_to_label[label] = code

all_form_labels = list(form_display_labels.values())

default_codes = ["20-F", "10-K", "10-Q", "6-K", "8-K"]
default_labels = [form_display_labels[c] for c in default_codes if c in form_display_labels]

selected_labels = st.multiselect(
    "Choose which submission types to download:",
    options=all_form_labels,
    default=default_labels,
)

# Convert display labels back to form codes
selected_form_codes = [form_code_to_label[lbl] for lbl in selected_labels]

custom_forms = st.text_input("Additional form types (comma-separated, e.g. F-1, F-1/A, S-1, 424B4):", "")
if custom_forms:
    extra = [f.strip().upper() for f in custom_forms.split(",") if f.strip()]
    selected_form_codes = list(dict.fromkeys(selected_form_codes + extra))

ticker = st.text_input("Enter Stock Ticker (e.g., YB, AAPL):", "").upper()

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
    st.caption("")  # spacer

download_btn = st.button("Download & Convert to Markdown", type="primary", use_container_width=True)

if download_btn and ticker:
    if not selected_form_codes:
        st.warning("Select at least one form type.")
    else:
        save_dir = base_save_path / ticker / "DataFromDataMule"
        md_dir = base_save_path / ticker / "DataFromDataMule_markdown"
        save_dir.mkdir(parents=True, exist_ok=True)
        md_dir.mkdir(parents=True, exist_ok=True)

        form_count = len(selected_form_codes)
        success_count = 0
        fail_count = 0

        with st.status("Downloading and converting...", expanded=True) as status:
            for i, form in enumerate(selected_form_codes):
                status.update(label=f"[{i+1}/{form_count}] Downloading {form}...")
                try:
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

            portfolio = Portfolio(str(save_dir))

            # ── Filtering rules (same as download_data.py) ──────────────
            TECH_JUNK = {"GRAPHIC","XML","ZIP","JSON","EXCEL",
                         "EX-101.SCH","EX-101.CAL","EX-101.DEF","EX-101.LAB","EX-101.PRE",
                         "EX-FILING FEES"}
            VALUE_JUNK = {
                "EX-1.1","EX-2.6","EX-2.9","EX-3.1",
                "EX-4.1","EX-4.9","EX-4.16","EX-4.17","EX-4.18",
                "EX-5.1","EX-8.1",
                "EX-10.1","EX-10.2","EX-10.3","EX-10.4",
                "EX-10.17","EX-10.18","EX-10.19","EX-10.20",
                "EX-10.21","EX-10.22","EX-10.23",
                "EX-12.1","EX-12.2","EX-13.1","EX-13.2","EX-13.3","EX-13.4",
                "EX-15.1","EX-18.1","EX-19.1",
                "EX-21.1","EX-23.1","EX-23.2","EX-23.3",
                "EX-31.1","EX-31.2","EX-32.1","EX-32.2",
                "EX-97.1",
            }
            USEFUL_EXHIBITS = {"EX-99.1","EX-99.2","EX-99.3","EX-99.4"}
            COVER_FORMS = {"10-K","10-Q","20-F","6-K","8-K","F-1","F-1/A","S-1","S-1/A"}
            ANNUAL_FORMS = {"10-K","10-KT","20-F"}
            IPO_BASES = {"F-1","S-1"}

            def is_useful(dt):
                if dt in TECH_JUNK or dt in VALUE_JUNK:
                    return False
                if dt in COVER_FORMS or dt in USEFUL_EXHIBITS:
                    return True
                return False

            def sub_base(st):
                for b in IPO_BASES:
                    if st == b or st.startswith(b + "/"):
                        return b
                return st

            def make_fname(ticker, sub_type, doc_type, period, filing_date):
                if sub_type in ANNUAL_FORMS and period and len(period) == 8:
                    ps = f"FY {period[:4]}"
                elif sub_type == "10-Q" and period and len(period) == 8:
                    m = int(period[4:6])
                    q = {3: "Q1", 6: "Q2", 9: "Q3"}.get(m, f"Period {period[:4]}-{period[4:6]}")
                    ps = f"{q} {period[:4]}"
                elif sub_type in ("F-1","F-1/A","S-1","S-1/A"):
                    ps = f"IPO {filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"
                elif doc_type in USEFUL_EXHIBITS:
                    ps = f"{filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"
                elif period and period != filing_date:
                    ps = f"{period[:4]}-{period[4:6]}-{period[6:]}"
                else:
                    ps = f"{filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"

                if doc_type in COVER_FORMS:
                    desc = sub_type
                elif doc_type in USEFUL_EXHIBITS:
                    desc = "Earnings Release" if sub_type in ("6-K","8-K") else f"Press Release ({doc_type})"
                else:
                    desc = doc_type

                fname = f"{ticker} - {desc} {ps}.md"
                import re
                fname = re.sub(r'[\\/:*?"<>|]', ' ', fname)
                fname = re.sub(r'\s+', ' ', fname).strip()
                return fname

            # Collect candidates
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

            # Dedup IPO — keep only latest
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

            # ── Year-based filtering ───────────────────────────────────
            from datetime import date
            current_year = date.today().year
            cutoff_year = current_year - num_years + 1  # e.g., 2 years → 2025..2026
            def get_year_from_candidate(c):
                st_, fd, dt, p, doc = c
                # Use period year for annual/quarterly, else filing_date year
                if p and len(p) == 8:
                    return int(p[:4])
                return int(fd[:4])
            filtered = [c for c in filtered if get_year_from_candidate(c) >= cutoff_year]

            # Convert to markdown
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
# EARNINGS TRANSCRIPTS DOWNLOADER (free, no API key needed)
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

transcripts_ticker = st.text_input("Ticker for transcripts:", value=ticker or "", key="transcripts_ticker").upper()

transcripts_btn = st.button("Download Earnings Transcripts", type="primary", use_container_width=True)

if transcripts_btn and transcripts_ticker:
    transcripts_dir = base_save_path / transcripts_ticker / "EarningsTranscripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    import subprocess
    import sys as _sys
    import os

    # When running as exe, script is bundled; extract path
    if getattr(sys, 'frozen', False):
        script_path = Path(sys._MEIPASS) / "scrape_transcripts.py"
    else:
        script_path = Path(__file__).parent / "scrape_transcripts.py"
    python_exe = _sys.executable

    with st.status("Running scraper via your browser...", expanded=True) as status:
        try:
            result = subprocess.run(
                [python_exe, str(script_path), transcripts_ticker, str(num_years)],
                capture_output=True, text=True, timeout=300
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
