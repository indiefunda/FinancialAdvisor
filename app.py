import streamlit as st
from datamule import Portfolio
from pathlib import Path

st.title("SEC Data Downloader")

base_save_path = Path(r"F:\FinancialAdvisor")
skip_types = {"GRAPHIC", "XML", "ZIP", "JSON", "EX-101.SCH", "EX-101.CAL",
              "EX-101.DEF", "EX-101.LAB", "EX-101.PRE"}
cover_forms = {"20-F", "6-K", "10-K", "10-Q", "8-K", "F-1", "S-1"}
annual_forms = {"20-F", "10-K", "10-Q"}

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

custom_forms = st.text_input("Additional form types (comma-separated, e.g. F-1, F-1/A, S-1, 424B4):", "")
if custom_forms:
    extra = [f.strip().upper() for f in custom_forms.split(",") if f.strip()]
    selected_forms = list(dict.fromkeys(selected_forms + extra))

# --- Download + Auto-Convert ---
ticker = st.text_input("Enter Stock Ticker (e.g., YB, AAPL):", "").upper()

download_btn = st.button("Download & Convert to Markdown", type="primary", use_container_width=True)

if download_btn and ticker:
    if not selected_forms:
        st.warning("Select at least one form type.")
    else:
        save_dir = base_save_path / ticker / "DataFromDataMule"
        md_dir = base_save_path / ticker / "DataFromDataMule_markdown"
        save_dir.mkdir(parents=True, exist_ok=True)
        md_dir.mkdir(parents=True, exist_ok=True)

        form_count = len(selected_forms)
        success_count = 0
        fail_count = 0

        with st.status("Downloading and converting...", expanded=True) as status:
            for i, form in enumerate(selected_forms):
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

            # Reload all downloaded submissions
            portfolio = Portfolio(str(save_dir))
            converted = 0
            skipped = 0

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
                    if doc.type in skip_types:
                        continue
                    sub_label = label
                    if doc.type not in cover_forms:
                        safe_type = doc.type.replace("/", "-")
                        sub_label += f" ({safe_type})"
                    md_content = doc.markdown
                    if md_content is None:
                        skipped += 1
                        continue
                    safe_company = company.replace("/", "-").replace(":", "-").replace("?", "").replace("*", "").replace("<", "").replace(">", "").replace("|", "").replace('"', "'")
                    out_path = md_dir / f"{safe_company} - {sub_label}.md"
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
