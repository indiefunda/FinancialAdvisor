import streamlit as st
import json
from datamule import Portfolio
from pathlib import Path

st.title("SEC Data Downloader")

base_save_path = Path(r"F:\FinancialAdvisor")
skip_types = {"GRAPHIC", "XML", "ZIP", "JSON", "EX-101.SCH", "EX-101.CAL",
              "EX-101.DEF", "EX-101.LAB", "EX-101.PRE"}

# --- Step 1: Download ---
ticker = st.text_input("Enter Stock Ticker (e.g., YB):", "").upper()
col1, col2 = st.columns(2)

with col1:
    download_btn = st.button("1. Download Submissions")

if download_btn and ticker:
    save_dir = base_save_path / ticker / "DataFromDataMule"
    save_dir.mkdir(parents=True, exist_ok=True)

    st.info(f"Downloading {ticker}...")
    portfolio = Portfolio(str(save_dir))
    portfolio.download_submissions(
        ticker=ticker,
        submission_type=["20-F", "6-K"],
        provider="sec",
    )

    # Collect available document types (excluding technical ones)
    available = set()
    for sub in portfolio:
        for doc in sub:
            if doc.type not in skip_types:
                available.add(doc.type)

    st.session_state["portfolio_path"] = str(save_dir)
    st.session_state["available_types"] = sorted(available)
    st.session_state["ticker"] = ticker
    st.success(f"Downloaded! Found document types: {', '.join(sorted(available))}")

elif download_btn and not ticker:
    st.warning("Please enter a ticker first.")

# --- Step 2: Convert (only if we have downloaded data) ---
if "available_types" in st.session_state:
    st.divider()
    st.subheader("Step 2: Select documents to convert")

    selected_types = st.multiselect(
        "Choose document types to extract:",
        options=st.session_state["available_types"],
        default=[t for t in st.session_state["available_types"] if t in ("20-F", "6-K", "EX-99.1")],
    )

    with col2:
        convert_btn = st.button("2. Convert to Markdown")

    if convert_btn and selected_types:
        ticker = st.session_state["ticker"]
        save_dir = Path(st.session_state["portfolio_path"])
        md_dir = base_save_path / ticker / "DataFromDataMule_markdown"
        md_dir.mkdir(parents=True, exist_ok=True)

        st.info("Converting...")
        portfolio = Portfolio(str(save_dir))

        for submission in portfolio:
            meta = submission.metadata.content
            company = meta["filer"]["company-data"]["conformed-name"]
            doc_type = meta["type"]
            period = meta.get("period", "")

            if doc_type == "20-F":
                label = period[:4]
            else:
                fd = meta["filing-date"]
                label = f"{fd[:4]}-{fd[4:6]}-{fd[6:]}"

            for doc in submission:
                if doc.type not in selected_types:
                    continue

                sub_label = label
                if doc.type not in ("20-F", "6-K"):
                    sub_label += f" ({doc.type})"
                md_content = doc.markdown
                if md_content is None:
                    st.warning(f"Skipped {doc.type} ({doc.filename}) — could not parse")
                    continue

                out_path = md_dir / f"{company} - {sub_label}.md"
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(md_content)

        st.success(f"Done! Files in: {md_dir}")
    elif convert_btn and not selected_types:
        st.warning("Select at least one document type.")
