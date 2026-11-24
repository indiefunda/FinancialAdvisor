"""Standalone CLI for downloading and converting SEC filings to clean Markdown.

Examples:
    python download_data.py AAPL
    python download_data.py YB 2
    python download_data.py AAPL 3 --forms 10-K,10-Q,8-K,DEF 14A
"""

from __future__ import annotations

import argparse
import sys

from sec_downloader import (
    DEFAULT_FORM_CODES,
    download_and_convert_sec_filings,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download SEC EDGAR filings and convert them to clean Markdown."
    )
    parser.add_argument("ticker", help="Stock ticker symbol (e.g., AAPL, YB, HUIZ)")
    parser.add_argument(
        "num_years",
        nargs="?",
        type=int,
        default=2,
        help="Years of history to include (default: 2)",
    )
    parser.add_argument(
        "--forms",
        default=",".join(DEFAULT_FORM_CODES),
        help=f"Comma-separated SEC form types (default: {','.join(DEFAULT_FORM_CODES)})",
    )
    args = parser.parse_args(argv)

    form_codes = [f.strip().upper() for f in args.forms.split(",") if f.strip()]

    print("=" * 60)
    print(f"  Downloading SEC filings for {args.ticker.upper()} (last {args.num_years} yr(s))")
    print("=" * 60)

    result = download_and_convert_sec_filings(
        ticker=args.ticker,
        form_codes=form_codes,
        num_years=args.num_years,
        on_status=lambda msg: print(f"  {msg}", flush=True),
    )

    print()
    for path in result.converted_files:
        print(f"  [OK] {path.name}")

    if result.form_errors:
        print("\nForm warnings/errors:")
        for form, err in result.form_errors.items():
            print(f"  - {form}: {err}")

    print("\n" + "=" * 60)
    print(
        f"  DONE! Forms: {result.forms_succeeded}/{result.forms_requested} | "
        f"Converted: {len(result.converted_files)} | "
        f"Skipped: {result.skipped_unparseable}"
    )
    print(f"  Markdown: {result.markdown_dir}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
