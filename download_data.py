import json
from datamule import Portfolio
from pathlib import Path

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
        if doc.type in skip_types:
            continue
        sub_label = label
        if doc.type not in ("20-F", "6-K"):
            sub_label += f" ({doc.type})"
        out_path = md_dir / f"{company} - {sub_label}.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(doc.markdown)
        print(f"Saved: {out_path.name}")

print(f"DONE! Check {md_dir}")
