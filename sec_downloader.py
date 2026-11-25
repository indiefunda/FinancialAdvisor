"""Shared SEC EDGAR filing downloader and Markdown converter.

Used by both `app.py` (Streamlit UI) and `download_data.py` (CLI).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import requests

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


def is_within_filing_window(filing_date: str, period: str, cutoff_year: int) -> bool:
    """Return True when a submission belongs in the requested *filing* window.

    The window must be judged on the FILING date — the same basis as the
    `filing_date=(start, end)` filter handed to `datamule` — and never on the
    fiscal period end. Annual reports are structurally affected: a 20-F/10-K for
    fiscal year N is normally filed in year N+1, so a period-year test would drop
    the most recent annual report from every window (and drop it entirely for a
    1-year window). The period year is still used for filename labels.
    """
    return _extract_year(filing_date, period) >= cutoff_year


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


_HTML_META_HEADER_RE = re.compile(
    r"\A<!--\s*financial-advisor-meta\s*\n.*?\n-->\s*\n*",
    re.DOTALL,
)
_YAML_META_HEADER_RE = re.compile(
    r"\A---\s*\n(?:[^\n]*\n){1,12}?---\s*\n*",
)


def strip_sec_metadata_header(text: str) -> str:
    """Remove machine-readable SEC metadata header (HTML comment or legacy YAML front-matter)."""
    if not text:
        return text
    cleaned = _HTML_META_HEADER_RE.sub("", text, count=1)
    if cleaned != text:
        return cleaned
    m = _YAML_META_HEADER_RE.match(text)
    if m and ("accession_number:" in m.group(0) or "form_type:" in m.group(0)):
        return text[m.end():]
    return text


DEFAULT_SEC_USER_AGENT = "FinancialAdvisor ResearchWorkbench (research@financialadvisor.app)"
_TICKER_CIK_CACHE: Dict[str, str] = {}


def _sec_user_agent() -> str:
    return os.environ.get("DATAMULE_SEC_USER_AGENT", "").strip() or DEFAULT_SEC_USER_AGENT


def _sec_headers() -> Dict[str, str]:
    return {
        "User-Agent": _sec_user_agent(),
        "Accept-Encoding": "gzip, deflate",
    }


def resolve_ticker_to_cik(
    ticker: str,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """Resolve a stock ticker to its 10-digit zero-padded SEC CIK via official `company_tickers.json`."""
    clean = sanitize_ticker(ticker)
    if clean in _TICKER_CIK_CACHE:
        return _TICKER_CIK_CACHE[clean]

    http = session or requests.Session()
    try:
        resp = http.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_sec_headers(),
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        sym = str(entry.get("ticker") or "").strip().upper()
        cik_raw = entry.get("cik_str")
        if not sym or cik_raw is None:
            continue
        try:
            cik_10 = str(int(cik_raw)).zfill(10)
        except (TypeError, ValueError):
            continue
        _TICKER_CIK_CACHE[sym] = cik_10
        if "-" in sym:
            _TICKER_CIK_CACHE.setdefault(sym.replace("-", "."), cik_10)
        elif "." in sym:
            _TICKER_CIK_CACHE.setdefault(sym.replace(".", "-"), cik_10)

    return _TICKER_CIK_CACHE.get(clean) or _TICKER_CIK_CACHE.get(clean.replace(".", "-"))


class _SecHtmlToMarkdown(HTMLParser):
    """Lightweight, zero-dependency HTML/iXBRL-to-Markdown converter preserving SEC financial tables."""

    _SKIP_TAGS = {"script", "style", "head", "title", "noscript", "ix:hidden", "ix:header"}
    _BLOCK_TAGS = {"p", "div", "section", "article", "blockquote", "hr", "pre"}
    _HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._out: List[str] = []
        self._in_table = 0
        self._cur_rows: List[List[str]] = []
        self._cur_row: Optional[List[str]] = None
        self._cur_cell: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS or t.endswith(":hidden") or t.endswith(":header"):
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return

        if t == "table":
            if self._in_table == 0:
                self._cur_rows = []
            self._in_table += 1
        elif t == "tr" and self._in_table > 0:
            self._cur_row = []
        elif t in ("td", "th") and self._cur_row is not None:
            self._cur_cell = []
        elif self._cur_cell is not None:
            if t == "br":
                self._cur_cell.append(" ")
        else:
            if t in self._HEADING_TAGS:
                self._out.append(f"\n\n{self._HEADING_TAGS[t]} ")
            elif t == "li":
                self._out.append("\n- ")
            elif t in self._BLOCK_TAGS:
                self._out.append("\n\n")
            elif t == "br":
                self._out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS or t.endswith(":hidden") or t.endswith(":header"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth > 0:
            return

        if t in ("td", "th") and self._cur_cell is not None and self._cur_row is not None:
            cell_txt = re.sub(r"\s+", " ", "".join(self._cur_cell)).strip().replace("|", "\\|")
            self._cur_row.append(cell_txt)
            self._cur_cell = None
        elif t == "tr" and self._cur_row is not None:
            if any(c for c in self._cur_row):
                self._cur_rows.append(self._cur_row)
            self._cur_row = None
        elif t == "table" and self._in_table > 0:
            self._in_table -= 1
            if self._in_table == 0 and self._cur_rows:
                self._flush_table(self._cur_rows)
                self._cur_rows = []
        elif self._cur_cell is None and t in self._HEADING_TAGS:
            self._out.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0 or not data:
            return
        cleaned = data.replace("\xa0", " ")
        if self._cur_cell is not None:
            self._cur_cell.append(cleaned)
        elif self._in_table == 0:
            self._out.append(cleaned)

    def _flush_table(self, rows: List[List[str]]) -> None:
        max_cols = max((len(r) for r in rows), default=0)
        if max_cols == 0:
            return
        norm_rows = [r + [""] * (max_cols - len(r)) for r in rows]
        lines = [
            "| " + " | ".join(norm_rows[0]) + " |",
            "| " + " | ".join("---" for _ in range(max_cols)) + " |",
        ]
        for r in norm_rows[1:]:
            lines.append("| " + " | ".join(r) + " |")
        self._out.append("\n\n" + "\n".join(lines) + "\n\n")

    def get_markdown(self) -> str:
        raw = "".join(self._out)
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
        joined = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", joined).strip()


def html_to_markdown(html_text: str) -> str:
    """Convert SEC EDGAR HTML/iXBRL filing content into clean Markdown."""
    parser = _SecHtmlToMarkdown()
    parser.feed(html_text or "")
    parser.close()
    return parser.get_markdown()


class _SecIndexTableParser(HTMLParser):
    """Parse SEC `{accession}-index.htm` document table to extract `(doc_type, filename)` pairs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_tr = False
        self._in_td = False
        self._row: List[str] = []
        self._cell: List[str] = []
        self.rows: List[List[str]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t == "tr":
            self._in_tr = True
            self._row = []
        elif t in ("td", "th") and self._in_tr:
            self._in_td = True
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("td", "th") and self._in_td:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._in_td = False
        elif t == "tr" and self._in_tr:
            if self._row:
                self.rows.append(self._row)
            self._in_tr = False

    def handle_data(self, data: str) -> None:
        if self._in_td and data:
            self._cell.append(data)


def _parse_sec_index_documents(index_html: str) -> List[Tuple[str, str]]:
    """Extract `(doc_type, filename)` tuples for HTML/text documents from an SEC filing index page."""
    p = _SecIndexTableParser()
    p.feed(index_html or "")
    p.close()
    docs: List[Tuple[str, str]] = []
    for row in p.rows:
        # Standard SEC index table columns: [Seq, Description, Document, Type, Size]
        if len(row) < 4:
            continue
        doc_cell = row[2].split()[0].strip() if row[2] else ""
        doc_type = row[3].strip().upper()
        if not doc_cell or not doc_type or doc_type == "TYPE":
            continue
        if doc_cell.lower().endswith((".htm", ".html", ".txt")):
            docs.append((doc_type, doc_cell))
    return docs


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
    resolved_cik: str = ""
    source_used: str = "datamule"


def _write_sec_markdown_file(
    *,
    md_dir: Path,
    clean_ticker: str,
    sub_type: str,
    doc_type: str,
    period: str,
    filing_date: str,
    accession: str,
    md_content: str,
    allowed_covers: Set[str],
    used_names: Set[str],
) -> Path:
    cleaned = cleanup_markdown_tables(md_content)
    meta_comment = [
        "<!-- financial-advisor-meta",
        f"ticker: {clean_ticker}",
        f"form_type: {sub_type}",
        f"doc_type: {doc_type}",
        f"filing_date: {_format_yyyymmdd(filing_date)}",
        f"period: {_format_yyyymmdd(period) if period else ''}",
        f"accession_number: {accession}",
        "-->",
        "",
    ]
    full_md = "\n".join(meta_comment) + cleaned
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
    out_path.write_text(full_md, encoding="utf-8")
    return out_path


def _download_via_sec_edgar_api(
    *,
    clean_ticker: str,
    cik: str,
    forms: Sequence[str],
    start_date_str: str,
    end_date_str: str,
    md_dir: Path,
    allowed_covers: Set[str],
    result: SecDownloadResult,
    on_status: Optional[Callable[[str], None]] = None,
    session: Optional[requests.Session] = None,
) -> None:
    """Direct official SEC EDGAR REST API fallback (`data.sec.gov/submissions/CIK...json`)."""
    http = session or requests.Session()
    headers = _sec_headers()
    cik_int = int(cik)
    form_set = {f.upper() for f in forms}

    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = http.get(sub_url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        result.form_errors["SEC EDGAR API"] = f"Could not query {sub_url}: {exc}"
        return

    blocks: List[Dict[str, Any]] = []
    filings = data.get("filings") if isinstance(data, dict) else None
    if isinstance(filings, dict):
        recent = filings.get("recent")
        if isinstance(recent, dict):
            blocks.append(recent)
        # Fetch older paginated archive JSONs only if the window extends before recent's earliest filing
        recent_dates = recent.get("filingDate") if isinstance(recent, dict) else None
        oldest_recent = min((d for d in (recent_dates or []) if d), default="")
        if oldest_recent and start_date_str < oldest_recent:
            for extra in filings.get("files") or []:
                if not isinstance(extra, dict) or not extra.get("name"):
                    continue
                if extra.get("filingTo") and str(extra["filingTo"]) < start_date_str:
                    continue
                try:
                    time.sleep(0.12)
                    er = http.get(f"https://data.sec.gov/submissions/{extra['name']}", headers=headers, timeout=30)
                    if er.status_code == 200 and isinstance(er.json(), dict):
                        blocks.append(er.json())
                except Exception:
                    continue

    matched_subs: List[Tuple[str, str, str, str, str]] = []
    seen_acc: Set[str] = set()
    for blk in blocks:
        f_forms = blk.get("form") or []
        f_dates = blk.get("filingDate") or []
        f_periods = blk.get("reportDate") or []
        f_accs = blk.get("accessionNumber") or []
        f_prim = blk.get("primaryDocument") or []
        n = min(len(f_forms), len(f_dates), len(f_accs))
        for i in range(n):
            f_type = str(f_forms[i] or "").strip().upper()
            f_dt = str(f_dates[i] or "").strip()
            acc = str(f_accs[i] or "").strip()
            if f_type not in form_set or not acc or acc in seen_acc:
                continue
            if not (start_date_str <= f_dt <= end_date_str):
                continue
            seen_acc.add(acc)
            per = str(f_periods[i] or "").strip() if i < len(f_periods) else ""
            prim = str(f_prim[i] or "").strip() if i < len(f_prim) else ""
            matched_subs.append((f_type, f_dt, per, acc, prim))

    # Deduplicate IPO amendments (keep latest per base IPO form)
    latest_ipo: Dict[str, Tuple[str, Tuple[str, str, str, str, str]]] = {}
    selected_subs: List[Tuple[str, str, str, str, str]] = []
    for sub in matched_subs:
        base = submission_base(sub[0])
        if base in IPO_BASES:
            if base not in latest_ipo or sub[1] > latest_ipo[base][0]:
                latest_ipo[base] = (sub[1], sub)
        else:
            selected_subs.append(sub)
    for _, sub in latest_ipo.values():
        selected_subs.append(sub)
    selected_subs.sort(key=lambda x: x[1])

    if not selected_subs:
        all_dts = [str(d) for b in blocks for d in (b.get("filingDate") or []) if d]
        latest_dt = max(all_dts) if all_dts else "none"
        _emit(
            f"SEC EDGAR API: 0 filings matched {sorted(form_set)} in {start_date_str} → {end_date_str} "
            f"(checked {len(all_dts)} filings for CIK {cik}; latest filing date: {latest_dt})."
        )
        return

    result.source_used = "sec_edgar_api"
    used_names: Set[str] = set()
    _emit(
        f"SEC EDGAR API found {len(selected_subs)} filing(s) for {clean_ticker} (CIK {cik}) — downloading & converting..."
    )

    first_http_err = ""
    for sub_type, filing_date, period, accession, primary_doc in selected_subs:
        result.submissions_found += 1
        acc_no_dash = accession.replace("-", "")
        base_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_dash}"

        # Inspect `{accession}-index.htm` to discover both primary filing & useful EX-99.* exhibits
        doc_targets: List[Tuple[str, str]] = []
        try:
            time.sleep(0.12)
            idx_resp = http.get(f"{base_url}/{accession}-index.htm", headers=headers, timeout=25)
            if idx_resp.status_code == 200:
                for dt_code, fname in _parse_sec_index_documents(idx_resp.text):
                    if is_useful_document(dt_code, allowed_cover_forms=allowed_covers):
                        doc_targets.append((dt_code, fname))
        except Exception:
            pass

        if not doc_targets and primary_doc:
            doc_targets.append((sub_type, primary_doc.lstrip("/")))

        for doc_type, doc_filename in doc_targets:
            try:
                time.sleep(0.12)
                doc_resp = http.get(f"{base_url}/{doc_filename}", headers=headers, timeout=45)
                if doc_resp.status_code != 200 or not doc_resp.text:
                    if not first_http_err:
                        first_http_err = f"HTTP {doc_resp.status_code} fetching {doc_filename}"
                    result.skipped_unparseable += 1
                    continue
                md_text = (
                    doc_resp.text.strip()
                    if doc_filename.lower().endswith(".txt")
                    else html_to_markdown(doc_resp.text)
                )
                if not md_text:
                    result.skipped_unparseable += 1
                    continue
                out_path = _write_sec_markdown_file(
                    md_dir=md_dir,
                    clean_ticker=clean_ticker,
                    sub_type=sub_type,
                    doc_type=doc_type,
                    period=period,
                    filing_date=filing_date,
                    accession=accession,
                    md_content=md_text,
                    allowed_covers=allowed_covers,
                    used_names=used_names,
                )
                result.converted_files.append(out_path)
            except Exception as exc:
                if not first_http_err:
                    first_http_err = f"{type(exc).__name__}: {exc}"
                result.skipped_unparseable += 1
                continue
    if first_http_err and not result.converted_files:
        result.form_errors["SEC EDGAR document fetch"] = first_http_err


def download_and_convert_sec_filings(
    ticker: str,
    form_codes: Sequence[str],
    num_years: int = 2,
    data_root: Path = DEFAULT_DATA_ROOT,
    on_status: Optional[Callable[[str], None]] = None,
    session: Optional[requests.Session] = None,
) -> SecDownloadResult:
    """Download SEC filings via `datamule` (with automatic official SEC EDGAR REST API fallback),
    filter junk, clean tables, and save Markdown."""
    # Ensure a compliant User-Agent is set BEFORE importing datamule (datamule reads env at import time)
    os.environ.setdefault("DATAMULE_SEC_USER_AGENT", DEFAULT_SEC_USER_AGENT)
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

    # Resolve authoritative SEC CIK directly from SEC EDGAR (avoids stale datamule ticker CSVs)
    resolved_cik = resolve_ticker_to_cik(clean_ticker, session=session)
    if resolved_cik:
        result.resolved_cik = resolved_cik
        _emit(f"Resolved {clean_ticker} → SEC CIK {resolved_cik}")

    # ── Step 1: Download per form type with filing_date filter ─────────
    id_kwargs = {"cik": resolved_cik} if resolved_cik else {"ticker": clean_ticker}
    for idx, form in enumerate(forms, start=1):
        _emit(f"[{idx}/{len(forms)}] Downloading {clean_ticker} {form} ({start_date_str} → {end_date_str})...")
        try:
            portfolio = Portfolio(str(raw_dir))
            try:
                portfolio.download_submissions(
                    **id_kwargs,
                    submission_type=[form],
                    filing_date=(start_date_str, end_date_str),
                    provider="sec",
                    skip_existing=True,
                )
            except TypeError:
                # Fallback for older datamule versions without skip_existing
                portfolio.download_submissions(
                    **id_kwargs,
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

    raw_total = 0
    candidates = []
    for submission in portfolio:
        raw_total += 1
        meta = submission.metadata.content
        sub_type = (meta.get("type") or "").upper()
        period = meta.get("period", "") or ""
        filing_date = meta.get("filing-date", "") or ""
        accession = str(
            meta.get("accession-number")
            or meta.get("accession_number")
            or getattr(submission, "accession", "")
            or ""
        ).strip()

        # Window filter BEFORE parsing markdown (huge speed win). Judged on the
        # filing date, matching the download window: a 20-F for FY2025 filed in
        # 2026 must survive even when `num_years=1` puts the cutoff at 2026.
        if not is_within_filing_window(filing_date, period, cutoff_year):
            continue

        result.submissions_found += 1

        for doc in submission:
            doc_type = (doc.type or "").upper()
            if not is_useful_document(doc_type, allowed_cover_forms=allowed_covers):
                continue
            candidates.append((sub_type, filing_date, doc_type, period, accession, doc))

    # Deduplicate amended IPO filings (keep latest per base form + doc_type)
    latest_ipo: Dict[Tuple[str, str], Tuple[str, tuple]] = {}
    filtered = []
    for c in candidates:
        st_, fd, dt, p, acc, doc = c
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
    first_conv_err = ""
    for sub_type, filing_date, doc_type, period, accession, doc in filtered:
        try:
            md_content = doc.markdown
        except Exception as exc:
            if not first_conv_err:
                first_conv_err = f"{type(exc).__name__}: {exc}"
            result.skipped_unparseable += 1
            continue
        if not md_content:
            result.skipped_unparseable += 1
            continue

        out_path = _write_sec_markdown_file(
            md_dir=md_dir,
            clean_ticker=clean_ticker,
            sub_type=sub_type,
            doc_type=doc_type,
            period=period,
            filing_date=filing_date,
            accession=accession,
            md_content=md_content,
            allowed_covers=allowed_covers,
            used_names=used_names,
        )
        result.converted_files.append(out_path)

    _emit(
        f"datamule pass: {raw_total} raw submission(s) on disk, {result.submissions_found} in window, "
        f"{len(result.converted_files)} converted, {result.skipped_unparseable} skipped."
    )
    if first_conv_err:
        result.form_errors["datamule markdown conversion"] = first_conv_err

    # ── Step 4: Official SEC EDGAR REST API fallback whenever datamule produced 0 Markdown files ──
    if not result.converted_files and resolved_cik:
        _emit(
            f"datamule produced 0 Markdown files — falling back to official SEC EDGAR API for {clean_ticker} (CIK {resolved_cik})..."
        )
        result.submissions_found = 0
        _download_via_sec_edgar_api(
            clean_ticker=clean_ticker,
            cik=resolved_cik,
            forms=forms,
            start_date_str=start_date_str,
            end_date_str=end_date_str,
            md_dir=md_dir,
            allowed_covers=allowed_covers,
            result=result,
            on_status=on_status,
            session=session,
        )

    return result
