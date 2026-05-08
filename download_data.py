import json
from datamule import Portfolio, Document
from pathlib import Path
from markdown_cleanup import cleanup_markdown_tables

ticker = "YB"

save_dir = Path(r"C:\Users\Achilles\Desktop\China Consumer Loan Sector\YuanBao\DataFromDataMule")
save_dir.mkdir(parents=True, exist_ok=True)

md_dir = Path(r"C:\Users\Achilles\Desktop\China Consumer Loan Sector\YuanBao\DataFromDataMule_markdown")
md_dir.mkdir(parents=True, exist_ok=True)

print(f"--- Downloading {ticker} ---")

portfolio = Portfolio(str(save_dir))

portfolio.download_submissions(
    ticker=ticker,
    submission_type=["20-F", "6-K"],
    provider="sec",
)

print("--- Converting to Markdown ---")

skip_types = {"GRAPHIC", "XML", "ZIP", "JSON", "EX-101.SCH", "EX-101.CAL",
              "EX-101.DEF", "EX-101.LAB", "EX-101.PRE"}
cover_forms = {"20-F", "6-K"}
annual_forms = {"20-F"}

for submission in portfolio:
    meta = submission.metadata.content
    company = meta["filer"]["company-data"]["conformed-name"]
    doc_type = meta["type"]
    period = meta.get("period", "")
    filing_date = meta["filing-date"]

    if doc_type in annual_forms and len(period) == 8:
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
            sub_label += f" ({doc.type.replace('/', '-')})"
        md_content = doc.markdown
        if md_content is None:
            print(f"Skipped {doc.type} ({doc.filename})")
            continue
        md_content = cleanup_markdown_tables(md_content)
        safe_company = company.replace("/", "-").replace(":", "-").replace("?", "").replace("*", "").replace("<", "").replace(">", "").replace("|", "").replace('"', "'")
        out_path = md_dir / f"{safe_company} - {sub_label}.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"Saved: {out_path.name}")

print(f"DONE! Check {md_dir}")
