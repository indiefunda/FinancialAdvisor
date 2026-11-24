"""Standalone CLI for downloading earnings call transcripts with multi-provider redundancy.

Uses local cache -> DefeatBeta (HF, unlimited) -> ROIC.ai (5/min, latest 2 calls)
-> Alpha Vantage (25/day) -> Firecrawl (fallback).

Examples:
    python scrape_transcripts.py AAPL 2
    python scrape_transcripts.py YB 2 --providers defeatbeta,roic,alphavantage
    python scrape_transcripts.py AAPL 1 --force
"""

from __future__ import annotations

import argparse
import sys

from config_manager import DEFAULT_PROVIDER_ORDER, QuotaManager
from transcript_providers import download_transcripts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download earnings call transcripts using free-tier redundant APIs."
    )
    parser.add_argument("ticker", help="Stock ticker symbol (e.g., AAPL, YB, HUIZ)")
    parser.add_argument(
        "num_years",
        nargs="?",
        type=int,
        default=2,
        help="Years of history to fetch (default: 2)",
    )
    parser.add_argument(
        "--providers",
        default=",".join(DEFAULT_PROVIDER_ORDER),
        help=f"Comma-separated provider order (default: {','.join(DEFAULT_PROVIDER_ORDER)})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download quarters even if already cached on disk",
    )
    args = parser.parse_args(argv)

    providers = [p.strip().lower() for p in args.providers.split(",") if p.strip()]
    quota = QuotaManager()

    print("=" * 60)
    print(f"  Downloading transcripts for {args.ticker.upper()} (last {args.num_years} yr(s))")
    print("=" * 60)

    result = download_transcripts(
        ticker=args.ticker,
        num_years=args.num_years,
        providers=providers,
        quota_manager=quota,
        force_refresh=args.force,
        on_log=lambda msg: print(msg, flush=True),
    )

    print("\n" + "=" * 60)
    print(
        f"  DONE! New: {len(result.saved_files)} | "
        f"Cached: {len(result.cached_quarters)} | "
        f"Missing: {len(result.missing_quarters)}"
    )
    for provider, path in result.saved_files:
        print(f"  [{provider}] {path.name}")
    print(f"  Output: {result.output_dir}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
