"""Shared SEC EDGAR filing downloader and Markdown converter.

Used by both `app.py` (Streamlit UI) and `download_data.py` (CLI).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from markdown_cleanup import cleanup_markdown_tables

REPO_ROOT = Path(__file__).parent.resolve()
DEFAULT_DATA_ROOT = REPO_ROOT / "data"

FORM_CATEGORIES: Dict[str, List[Tuple[str, str]]] = {
    "Annual Reports": [
        ("10-K", "Annual report (US companies)"),
        ("20-F", "Annual report (foreign companies)"),
        ("10-K/A", "Annual report amendment (US)"),
        ("20-F/A", "Annual report amendment (foreign)"),
    ],
    "Quarterly / Current": [
        ("10-Q", "Quarterly report (US)"),
        ("6-K", "Current report (foreign cos — earnings & updates)"),
        ("8-K", "Current report (US cos — earnings & material events)"),
    ],
    "Proxy & Governance": [
        ("DEF 14A", "Definitive proxy statement (exec comp & governance)"),
    ],
    "IPO / Registration": [
        ("F-1", "Foreign IPO registration statement"),
        ("F-1/A", "Foreign IPO registration (amended)"),
        ("S-1", "US IPO registration statement"),
        ("S-1/A", "US IPO registration (amended)"),
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

DEFAULT_FORM_CODES: List[str] = ["20-F", "10-K", "10-Q", "6-K", "8-K"]

# Technical junk — non-narrative data formats
TECH_JUNK: Set[str] = {
    "GRAPHIC",
    "XML",
    "ZIP",
    "JSON",
    "EXCEL",
    "EX-101.SCH",
    "EX-101.CAL",
    "EX-101.DEF",
    "EX-101.LAB",
    "EX-101.PRE",
    "EX-FILING FEES",
}

# Legal/administrative boilerplate exhibits with little investment value
VALUE_JUNK: Set[str] = {
    "EX-1.1",
    "EX-2.6",
    "EX-2.9",
    "EX-3.1",
    "EX-4.1",
    "EX-4.9",
    "EX-4.16",
    "EX-4.17",
    "EX-4.18",
    "EX-5.1",
    "EX-8.1",
    "EX-10.1",
    "EX-10.2",
    "EX-10.3",
    "EX-10.4",
    "EX-10.17",
    "EX-10.18",
    "EX-10.19",
    "EX-10.20",
    "EX-10.21",
    "EX-10.22",
    "EX-10.23",
    "EX-12.1",
    "EX-12.2",
    "EX-13.1",
    "EX-13.2",
    "EX-13.3",
    "EX-13.4",
    "EX-15.1",
    "EX-18.1",
    "EX-19.1",
    "EX-21.1",
    "EX-23.1",
    "EX-23.2",
    "EX-23.3",
    "EX-31.1",
    "EX-31.2",
    "EX-32.1",
    "EX-32.2",
    "EX-97.1",
}

USEFUL_EXHIBITS: Set[str] = {"EX-99.1", "EX-99.2", "EX-99.3", "EX-99.4"}
BASE_COVER_FORMS: Set[str] = {
    code for forms in FORM_CATEGORIES.values() for code, _ in forms
}
ANNUAL_FORMS: Set[str] = {"10-K", "10-KT", "10-K/A", "20-F", "20-F/A"}
IPO_BASES: Set[str] = {"F-1", "S-1"}


def sanitize_ticker(ticker: str) -> str:
    """Normalize and validate a stock ticker symbol for safe filesystem use."""
    cleaned = ticker.strip().upper()
    if not cleaned:
        raise ValueError("Ticker cannot be empty.")
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", cleaned):
        raise ValueError(
            f"Invalid ticker '{ticker}'. Use letters, digits, '.', or '-' only."
        )
    return cleaned


def is_useful_document(
    doc_type: str,
    allowed_cover_forms: Optional[Set[str]] = None,
) -> bool:
    """Return True if this SEC document type should be converted to Markdown."""
    dt = (doc_type or "").strip().upper()
    if dt in TECH_JUNK or dt in VALUE_JUNK:
        return False
    covers = BASE_COVER_FORMS | (allowed_cover_forms or set())
    return dt in covers or dt in USEFUL_EXHIBITS


def submission_base(sub_type: str) -> str:
    """Map amended IPO forms (e.g. F-1/A) to their base form (F-1) for dedup."""
    for base in IPO_BASES:
        if sub_type == base or sub_type.startswith(base + "/"):
            return base
    return sub_type


def _format_yyyymmdd(raw: str) -> str:
    """Format '20250315' or '2025-03-15' cleanly as '2025-03-15'."""
    digits = re.sub(r"[^0-9]", "", raw or "")
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return raw or "unknown-date"


def _extract_year(period: str, filing_date: str) -> int:
    """Extract 4-digit year from period (if valid) or fall back to filing_date."""
    for val in (period, filing_date):
        digits = re.sub(r"[^0-9]", "", val or "")
        if len(digits) >= 4:
            return int(digits[:4])
    return 0


def make_sec_filename(
    ticker: str,
    sub_type: str,
    doc_type: str,
    period: str,
    filing_date: str,
    allowed_cover_forms: Optional[Set[str]] = None,
) -> str:
    """Generate a clean, Windows-safe, descriptive Markdown filename.

    Includes exhibit codes (e.g. EX-99.1) so multiple exhibits on the same
    filing date never overwrite each other, and uses period end date for 10-Q
    so non-calendar fiscal years (AAPL, NVDA, MSFT) are never mislabeled.
    """
    ticker_part = sanitize_ticker(ticker)
    covers = BASE_COVER_FORMS | (allowed_cover_forms or set())
    period_digits = re.sub(r"[^0-9]", "", period or "")
    fd_fmt = _format_yyyymmdd(filing_date)

    if sub_type in ANNUAL_FORMS and len(period_digits) == 8:
        period_str = f"FY {period_digits[:4]}"
    elif sub_type == "10-Q" and len(period_digits) == 8:
        period_str = f"Period {_format_yyyymmdd(period_digits)}"
    elif sub_type in ("F-1", "F-1/A", "S-1", "S-1/A"):
        period_str = f"IPO {fd_fmt}"
    elif doc_type in USEFUL_EXHIBITS:
        period_str = fd_fmt
    elif period_digits and period_digits != re.sub(r"[^0-9]", "", filing_date or ""):
        period_str = _format_yyyymmdd(period_digits)
    else:
        period_str = fd_fmt

    if doc_type in covers:
        desc = sub_type
    elif doc_type in USEFUL_EXHIBITS:
        if sub_type in ("6-K", "8-K"):
            desc = f"Earnings Release ({doc_type})"
        else:
            desc = f"Press Release ({doc_type})"
    else:
        desc = doc_type

    fname = f"{ticker_part} - {desc} {period_str}.md"
    fname = re.sub(r'[\\/:*?"<>|]', " ", fname)
    fname = re.sub(r"\s+", " ", fname).strip()
    return fname


def deduplicate_filename(filename: str, used_names: Set[str]) -> str:
    """Ensure a filename is unique within `used_names` by appending (2), (3), etc."""
    if filename not in used_names:
        used_names.add(filename)
        return filename
    stem = filename[:-3] if filename.endswith(".md") else filename
    ext = ".md" if filename.endswith(".md") else ""
    idx = 2
    while True:
        candidate = f"{stem} ({idx}){ext}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        idx += 1


@dataclass
class SecDownloadResult:
    ticker: str
    raw_dir: Path
    markdown_dir: Path
    forms_requested: int = 0
    forms_succeeded: int = 0
    submissions_found: int = 0
    form_errors: Dict[str, str] = field(default_factory=dict)
    converted_files: List[Path] = field(default_factory=list)
    skipped_unparseable: int = 0


def download_and_convert_sec_filings(
    ticker: str,
    form_codes: Sequence[str],
    num_years: int = 2,
    data_root: Path = DEFAULT_DATA_ROOT,
    on_status: Optional[Callable[[str], None]] = None,
) -> SecDownloadResult:
    """Download SEC filings via `datamule`, filter junk, clean tables, and save Markdown."""
    try:
        from datamule import Portfolio
    except ImportError as exc:
        raise RuntimeError(
            "Missing required package 'datamule'. Install dependencies with: "
            "pip install -r requirements.txt"
        ) from exc

    clean_ticker = sanitize_ticker(ticker)
    forms = list(dict.fromkeys(f.strip().upper() for f in form_codes if f.strip()))
    if not forms:
        raise ValueError("Select at least one SEC form type.")

    raw_dir = data_root / clean_ticker / "DataFromDataMule"
    md_dir = data_root / clean_ticker / "DataFromDataMule_markdown"
    raw_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)

    today = date.today()
    cutoff_year = today.year - max(1, int(num_years)) + 1
    start_date_str = f"{cutoff_year}-01-01"
    end_date_str = today.strftime("%Y-%m-%d")

    result = SecDownloadResult(
        ticker=clean_ticker,
        raw_dir=raw_dir,
        markdown_dir=md_dir,
        forms_requested=len(forms),
    )

    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    # ── Step 1: Download per form type with filing_date filter ─────────
    for idx, form in enumerate(forms, start=1):
        _emit(f"[{idx}/{len(forms)}] Downloading {clean_ticker} {form} ({start_date_str} → {end_date_str})...")
        try:
            portfolio = Portfolio(str(raw_dir))
            try:
                portfolio.download_submissions(
                    ticker=clean_ticker,
                    submission_type=[form],
                    filing_date=(start_date_str, end_date_str),
                    provider="sec",
                    skip_existing=True,
                )
            except TypeError:
                # Fallback for older datamule versions without skip_existing
                portfolio.download_submissions(
                    ticker=clean_ticker,
                    submission_type=[form],
                    filing_date=(start_date_str, end_date_str),
                    provider="sec",
                )
            result.forms_succeeded += 1
        except Exception as exc:
            result.form_errors[form] = str(exc)

    # ── Step 2: Filter & deduplicate BEFORE parsing doc.markdown ───────
    _emit("Filtering documents and converting to clean Markdown...")
    portfolio = Portfolio(str(raw_dir))
    allowed_covers = BASE_COVER_FORMS | set(forms)

    candidates = []
    for submission in portfolio:
        meta = submission.metadata.content
        sub_type = (meta.get("type") or "").upper()
        period = meta.get("period", "") or ""
        filing_date = meta.get("filing-date", "") or ""

        # Filter by year BEFORE parsing markdown (huge speed win)
        if _extract_year(period, filing_date) < cutoff_year:
            continue

        result.submissions_found += 1

        for doc in submission:
            doc_type = (doc.type or "").upper()
            if not is_useful_document(doc_type, allowed_cover_forms=allowed_covers):
                continue
            candidates.append((sub_type, filing_date, doc_type, period, doc))

    # Deduplicate amended IPO filings (keep latest per base form + doc_type)
    latest_ipo: Dict[Tuple[str, str], Tuple[str, tuple]] = {}
    filtered = []
    for c in candidates:
        st_, fd, dt, p, doc = c
        base = submission_base(st_)
        if base in IPO_BASES:
            key = (base, dt)
            if key not in latest_ipo or fd > latest_ipo[key][0]:
                latest_ipo[key] = (fd, c)
        else:
            filtered.append(c)

    for _, (_, c) in latest_ipo.items():
        filtered.append(c)
    filtered.sort(key=lambda c: c[1])

    # ── Step 3: Convert to cleaned Markdown with unique filenames ──────
    used_names: Set[str] = set()
    for sub_type, filing_date, doc_type, period, doc in filtered:
        try:
            md_content = doc.markdown
        except Exception:
            result.skipped_unparseable += 1
            continue
        if not md_content:
            result.skipped_unparseable += 1
            continue

        md_content = cleanup_markdown_tables(md_content)
        raw_fname = make_sec_filename(
            clean_ticker,
            sub_type,
            doc_type,
            period,
            filing_date,
            allowed_cover_forms=allowed_covers,
        )
        fname = deduplicate_filename(raw_fname, used_names)
        out_path = md_dir / fname
        out_path.write_text(md_content, encoding="utf-8")
        result.converted_files.append(out_path)

    return result
