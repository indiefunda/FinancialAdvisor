import streamlit as st
import json
from datamule import Portfolio
from pathlib import Path

st.title("SEC Data Downloader")

base_save_path = Path(r"F:\FinancialAdvisor")
skip_types = {"GRAPHIC", "XML", "ZIP", "JSON", "EX-101.SCH", "EX-101.CAL",
              "EX-101.DEF", "EX-101.LAB", "EX-101.PRE"}

# --- Step 0: Pick form types ---
st.subheader("Step 0: Select SEC form types to download")
form_categories = {
    "IPO / Registration": ["F-1", "F-1/A", "S-1", "S-1/A"],
    "Annual Reports": ["20-F", "10-K"],
    "Quarterly / Current": ["10-Q", "6-K", "8-K"],
    "Shelf / Business Combos": ["F-3", "F-3/A", "S-3", "S-3/A", "F-4", "F-4/A", "S-4", "S-4/A"],
    "Prospectuses": ["424B1", "424B2", "424B3", "424B4"],
}

all_forms = []
for cat_forms in form_categories.values():
    all_forms.extend(cat_forms)

default_forms = ["20-F", "6-K", "10-K", "10-Q", "8-K"]

selected_forms = st.multiselect(
    "Choose which submission types to download:",
    options=all_forms,
    default=[f for f in all_forms if f in default_forms],
)

custom_forms = st.text_input("Additional form types (comma-separated, e.g. F-1, S-1, 424B4):", "")
if custom_forms:
    extra = [f.strip().upper() for f in custom_forms.split(",") if f.strip()]
    selected_forms = list(dict.fromkeys(selected_forms + extra))

st.caption("Keep it to ~5 form types max. The SEC API errors with too many. For IPO docs (F-1, S-1), add them here but remove annual/quarterly forms first.")

# --- Step 1: Download ---
ticker = st.text_input("Enter Stock Ticker (e.g., YB, AAPL):", "").upper()

with st.expander("What's happening?", expanded=False):
    st.markdown("""
**Step 1** downloads raw SEC data (`.tar` files) → saved in `DataFromDataMule/`  
**Step 2** extracts readable Markdown from it → saved in `DataFromDataMule_markdown/`

Both folders appear under `F:\\FinancialAdvisor\\TICKER\\`.
""")

download_btn = st.button("1. Download Submissions", type="primary", use_container_width=True)

if download_btn and ticker:
    if not selected_forms:
        st.warning("Select at least one form type.")
    else:
        save_dir = base_save_path / ticker / "DataFromDataMule"
        save_dir.mkdir(parents=True, exist_ok=True)

        try:
            with st.status(f"Downloading {ticker} — this may take a while...", expanded=True) as status:
                portfolio = Portfolio(str(save_dir))
                portfolio.download_submissions(
                    ticker=ticker,
                    submission_type=selected_forms,
                    provider="sec",
                )
                status.update(label=f"Downloaded {ticker}", state="complete")

            available = set()
            for sub in portfolio:
                for doc in sub:
                    if doc.type not in skip_types:
                        available.add(doc.type)

            st.session_state["portfolio_path"] = str(save_dir)
            st.session_state["ticker"] = ticker

            if available:
                st.session_state["available_types"] = sorted(available)
            else:
                st.session_state.pop("available_types", None)
        except Exception as e:
            st.error(f"SEC API error: {e}")
            st.info("Try selecting fewer form types (3-5 max) and avoid mixing amendment forms with base forms.")

        if available:
            st.success(f"Found document types: {', '.join(sorted(available))}")
        else:
            st.warning("SEC returned 0 results. This usually happens when amendment forms (/A) conflict with base forms in the query. Try selecting fewer form types — e.g., download just `20-F` and `6-K` for foreign stocks, or `10-K`, `10-Q`, `8-K` for US stocks.")

        st.info(f"Raw SEC data saved to: `{save_dir}`")

elif download_btn and not ticker:
    st.warning("Please enter a ticker first.")

# --- Step 2: Convert ---
annual_forms = {"20-F", "10-K", "10-Q"}
cover_forms = {"20-F", "6-K", "10-K", "10-Q", "8-K", "F-1", "S-1"}

if "available_types" in st.session_state:
    st.divider()
    st.subheader("Step 2: Extract Markdown files")

    selected_types = st.multiselect(
        "Choose document types to extract:",
        options=st.session_state["available_types"],
        default=[t for t in st.session_state["available_types"] if t in ("20-F", "6-K", "10-K", "10-Q", "8-K", "EX-99.1")],
    )

    convert_btn = st.button("2. Convert to Markdown", type="primary", use_container_width=True)

    if convert_btn and selected_types:
        ticker = st.session_state["ticker"]
        save_dir = Path(st.session_state["portfolio_path"])
        md_dir = base_save_path / ticker / "DataFromDataMule_markdown"
        md_dir.mkdir(parents=True, exist_ok=True)

        with st.status("Converting...", expanded=True) as status:
            portfolio = Portfolio(str(save_dir))

            for submission in portfolio:
                meta = submission.metadata.content
                company = meta["filer"]["company-data"]["conformed-name"]
                sub_form = meta["type"]
                period = meta.get("period", "")
                filing_date = meta["filing-date"]

                if sub_form in annual_forms and len(period) == 8:
                    label = period[:4]
                elif period and period != filing_date:
                    label = f"{period[:4]}-{period[4:6]}-{period[6:]}"
                else:
                    label = f"{filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:]}"

                for doc in submission:
                    if doc.type not in selected_types:
                        continue

                    sub_label = label
                    if doc.type not in cover_forms:
                        sub_label += f" ({doc.type})"
                    md_content = doc.markdown
                    if md_content is None:
                        st.warning(f"Skipped {doc.type} ({doc.filename}) — could not parse")
                        continue

                    out_path = md_dir / f"{company} - {sub_label}.md"
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(md_content)

            status.update(label="Conversion complete", state="complete")

        st.success(f"Markdown files saved to: `{md_dir}`")
        st.write("Open that folder in File Explorer to see your files.")
    elif convert_btn and not selected_types:
        st.warning("Select at least one document type.")
