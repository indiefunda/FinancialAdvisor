"""DefeatBeta-powered fundamentals, peers, DCF valuation, calendar, and SEC coverage layer.

Produces deterministic, currency-labelled Markdown & JSON artifacts under
`data/<TICKER>/` so LLMs (DeepSeek, Cherry Studio, Claude) can ground their
reasoning in exact numbers instead of scraping messy filing tables:
  - `data/<TICKER>/CompanyBrief.md`
  - `data/<TICKER>/Peers.md`
  - `data/<TICKER>/Coverage.md`
  - `data/<TICKER>/Valuation/dcf_data.json`
  - `data/<TICKER>/Valuation/DCF_Summary.md`
  - `data/<TICKER>/Valuation/<TICKER>_DCF.xlsx`
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import math
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from sec_downloader import DEFAULT_DATA_ROOT, sanitize_ticker
from transcript_providers import expected_quarters, scan_existing_quarters

_FRONTMATTER_ACC_RE = re.compile(r"^accession_number:\s*(\S+)", re.MULTILINE)
_FRONTMATTER_DATE_RE = re.compile(r"^filing_date:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)


# ============================================================================
# HELPERS: Silent Ticker Construction & Safe Numeric Formatting
# ============================================================================

def create_quiet_ticker(ticker: str) -> Any:
    """Instantiate `defeatbeta_api.data.ticker.Ticker` without stdout banner noise."""
    try:
        from defeatbeta_api.data.ticker import Ticker as DBTicker
    except ImportError as exc:
        raise RuntimeError(
            "Missing required package 'defeatbeta-api'. Install with: pip install -r requirements.txt"
        ) from exc

    clean = sanitize_ticker(ticker)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return DBTicker(clean, log_level=logging.ERROR)


def _is_nan(val: Any) -> bool:
    if val is None:
        return True
    try:
        return bool(math.isnan(float(val)))
    except (TypeError, ValueError):
        return False


def _fmt_num(val: Any, decimals: int = 2, suffix: str = "") -> str:
    if _is_nan(val):
        return "n/a"
    try:
        num = float(val)
    except (TypeError, ValueError):
        return "n/a"
    return f"{num:,.{decimals}f}{suffix}"


def _fmt_pct(val: Any, decimals: int = 2) -> str:
    """Format a decimal ratio (e.g., 0.185 -> '18.50%') safely."""
    if _is_nan(val):
        return "n/a"
    try:
        num = float(val)
    except (TypeError, ValueError):
        return "n/a"
    return f"{num * 100:,.{decimals}f}%"


def _fmt_money(val: Any, currency: str = "") -> str:
    """Format large financial figures compactly (B / M / K) with explicit currency."""
    if _is_nan(val):
        return "n/a"
    try:
        num = float(val)
    except (TypeError, ValueError):
        return "n/a"
    prefix = f"{currency} " if currency else ""
    abs_n = abs(num)
    if abs_n >= 1e12:
        return f"{prefix}{num / 1e12:,.2f}T"
    if abs_n >= 1e9:
        return f"{prefix}{num / 1e9:,.2f}B"
    if abs_n >= 1e6:
        return f"{prefix}{num / 1e6:,.2f}M"
    if abs_n >= 1e3:
        return f"{prefix}{num / 1e3:,.2f}K"
    return f"{prefix}{num:,.2f}"


def _safe_call(obj: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    """Call `getattr(obj, method_name)` safely, returning None on any error."""
    fn = getattr(obj, method_name, None)
    if fn is None:
        return None
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return fn(*args, **kwargs)
    except Exception:
        return None


def _df_is_empty(df: Any) -> bool:
    return df is None or getattr(df, "empty", True)


def _latest_value(
    df: Any,
    preferred_cols: Sequence[str],
    date_cols: Sequence[str] = ("report_date", "price_report_date", "date"),
) -> Tuple[Optional[float], str]:
    """Return `(latest_non_null_value, date_str)` for an explicitly named column.

    Never falls back to arbitrary trailing columns (such as `*_usd` currency
    conversions) so reporting-currency values are never silently mixed with USD.
    """
    if _df_is_empty(df):
        return None, ""
    cols = list(df.columns)
    val_col = next((c for c in preferred_cols if c in cols), None)
    if val_col is None:
        return None, ""

    date_col = next((c for c in date_cols if c in cols), None)
    valid = df.dropna(subset=[val_col])
    if _df_is_empty(valid):
        return None, ""

    if date_col:
        try:
            valid = valid.sort_values(date_col)
        except Exception:
            pass

    last_row = valid.iloc[-1]
    raw_val = last_row[val_col]
    if _is_nan(raw_val):
        return None, ""
    try:
        fval = float(raw_val)
    except (TypeError, ValueError):
        return None, ""

    d_str = ""
    if date_col and not _is_nan(last_row.get(date_col)):
        d_str = str(last_row.get(date_col))[:10]
    return fval, d_str


def _recent_series(
    df: Any,
    preferred_cols: Sequence[str],
    n: int = 4,
    date_cols: Sequence[str] = ("report_date", "price_report_date", "date"),
) -> List[Tuple[str, float]]:
    """Return up to `n` most recent `(date_str, value)` points."""
    if _df_is_empty(df):
        return []
    cols = list(df.columns)
    val_col = next((c for c in preferred_cols if c in cols), None)
    date_col = next((c for c in date_cols if c in cols), None)
    if not val_col or not date_col:
        return []

    valid = df.dropna(subset=[val_col])
    if _df_is_empty(valid):
        return []
    try:
        valid = valid.sort_values(date_col)
    except Exception:
        pass

    out: List[Tuple[str, float]] = []
    for _, row in valid.tail(n).iterrows():
        raw = row[val_col]
        if _is_nan(raw):
            continue
        try:
            out.append((str(row[date_col])[:10], float(raw)))
        except (TypeError, ValueError):
            continue
    return out


def _detect_currency(ticker_obj: Any, dcf_payload: Optional[Dict[str, Any]] = None) -> str:
    """Detect the company's authoritative financial reporting currency.

    Checks DefeatBeta's `company_meta.get_company_info()` first (works even when
    DCF fails), then `dcf_data`, then `info()`, defaulting to `USD`.
    """
    try:
        cm = getattr(ticker_obj, "company_meta", None)
        sym = getattr(ticker_obj, "ticker", None)
        if cm is not None and sym:
            meta_info = cm.get_company_info(sym)
            if isinstance(meta_info, dict) and meta_info.get("financial_currency"):
                return str(meta_info["financial_currency"]).upper()
    except Exception:
        pass

    if isinstance(dcf_payload, dict):
        ge = dcf_payload.get("growth_estimates")
        if isinstance(ge, dict) and ge.get("currency"):
            return str(ge["currency"]).upper()

    info_df = _safe_call(ticker_obj, "info")
    if not _df_is_empty(info_df):
        row = info_df.iloc[0]
        for col in ("financial_currency", "currency", "reporting_currency"):
            if col in info_df.columns and row.get(col) and not _is_nan(row.get(col)):
                return str(row.get(col)).upper()
    return "USD"


def _json_default(obj: Any) -> Any:
    """Serialize numpy/pandas/Decimal/date objects cleanly to JSON."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    try:
        return float(obj)
    except (TypeError, ValueError):
        return str(obj)


# ============================================================================
# 1. COMPANY BRIEF (`data/<TICKER>/CompanyBrief.md`)
# ============================================================================

@dataclass
class CompanyBriefResult:
    ticker: str
    path: Path
    currency: str
    next_earnings_date: str = "n/a"
    metrics: Dict[str, str] = field(default_factory=dict)


def generate_company_brief(
    ticker: str,
    data_root: Path = DEFAULT_DATA_ROOT,
    ticker_obj: Optional[Any] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> CompanyBriefResult:
    """Generate a deterministic, currency-labelled `CompanyBrief.md` from DefeatBeta."""
    clean = sanitize_ticker(ticker)
    t = ticker_obj if ticker_obj is not None else create_quiet_ticker(clean)

    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    _emit(f"Fetching company profile & calendar for {clean}...")
    info_df = _safe_call(t, "info")
    cal_df = _safe_call(t, "calendar")
    dcf_payload = _safe_call(t, "dcf_data")
    currency = _detect_currency(t, dcf_payload)

    # Profile fields
    profile: Dict[str, str] = {}
    if not _df_is_empty(info_df):
        row = info_df.iloc[0]
        for col in ("sector", "industry", "country", "city", "web_site", "full_time_employees", "long_business_summary"):
            if col in info_df.columns and row.get(col) is not None and not _is_nan(row.get(col)):
                profile[col] = str(row.get(col)).strip()

    # Next earnings date
    next_earnings = "n/a"
    if not _df_is_empty(cal_df):
        date_col = next((c for c in ("report_date", "earnings_date", "date") if c in cal_df.columns), None)
        if date_col:
            today_str = date.today().isoformat()
            dates = sorted(str(v)[:10] for v in cal_df[date_col].dropna().tolist())
            upcoming = [d for d in dates if d >= today_str]
            next_earnings = upcoming[0] if upcoming else (f"{dates[-1]} (last reported)" if dates else "n/a")

    _emit(f"Fetching valuation & balance-sheet multiples for {clean}...")
    pe_val, pe_dt = _latest_value(_safe_call(t, "ttm_pe"), ["ttm_pe", "pe"])
    ps_val, _ = _latest_value(_safe_call(t, "ps_ratio"), ["ps_ratio", "ps"])
    pb_val, _ = _latest_value(_safe_call(t, "pb_ratio"), ["pb_ratio", "pb"])
    peg_val, _ = _latest_value(_safe_call(t, "peg_ratio"), ["peg_ratio", "peg"])
    mcap_val, mcap_dt = _latest_value(_safe_call(t, "market_capitalization"), ["market_capitalization", "market_cap"])
    ev_val, _ = _latest_value(_safe_call(t, "enterprise_value"), ["enterprise_value", "ev"])
    net_debt_val, _ = _latest_value(_safe_call(t, "net_debt_ttm"), ["net_debt", "net_debt_ttm"])
    d2e_val, _ = _latest_value(_safe_call(t, "debt_to_equity"), ["debt_to_equity"])
    wacc_val, _ = _latest_value(_safe_call(t, "wacc"), ["wacc"])
    eps_val, _ = _latest_value(_safe_call(t, "ttm_eps"), ["tailing_eps", "ttm_eps", "eps"])

    rev_df = _safe_call(t, "ttm_revenue")
    fcf_df = _safe_call(t, "ttm_fcf")
    # Explicitly read local-currency columns (never accidentally fall back to *_usd!)
    rev_val, rev_dt = _latest_value(rev_df, ["ttm_total_revenue", "ttm_revenue"])
    rev_usd_val, _ = _latest_value(rev_df, ["ttm_total_revenue_usd"])
    fcf_val, _ = _latest_value(fcf_df, ["ttm_free_cash_flow", "ttm_fcf"])
    fcf_usd_val, _ = _latest_value(fcf_df, ["ttm_free_cash_flow_usd"])

    _emit(f"Fetching profitability, returns & growth trends for {clean}...")
    roic_df = _safe_call(t, "roic")
    roe_df = _safe_call(t, "roe")
    roa_df = _safe_call(t, "roa")
    gm_df = _safe_call(t, "quarterly_gross_margin")
    om_df = _safe_call(t, "quarterly_operating_margin")
    nm_df = _safe_call(t, "quarterly_net_margin")
    fm_df = _safe_call(t, "quarterly_fcf_margin")
    yoy_rev_df = _safe_call(t, "quarterly_revenue_yoy_growth")
    breakdown_df = _safe_call(t, "trailing_revenue_by_breakdown")

    roic_val, _ = _latest_value(roic_df, ["roic"])
    roe_val, _ = _latest_value(roe_df, ["roe"])
    roa_val, _ = _latest_value(roa_df, ["roa"])
    gm_val, _ = _latest_value(gm_df, ["gross_margin", "quarterly_gross_margin"])
    om_val, _ = _latest_value(om_df, ["operating_margin", "quarterly_operating_margin"])
    nm_val, _ = _latest_value(nm_df, ["net_margin", "quarterly_net_margin"])
    fm_val, _ = _latest_value(fm_df, ["fcf_margin", "quarterly_fcf_margin"])
    yoy_val, _ = _latest_value(yoy_rev_df, ["yoy_growth", "revenue_yoy_growth", "total_revenue_yoy_growth"])

    market_metrics: Dict[str, str] = {
        "Market Cap (USD)": _fmt_money(mcap_val, "USD"),
        "Enterprise Value (USD)": _fmt_money(ev_val, "USD"),
        "TTM P/E": _fmt_num(pe_val, 2, "x"),
        "P/S Ratio": _fmt_num(ps_val, 2, "x"),
        "P/B Ratio": _fmt_num(pb_val, 2, "x"),
        "PEG Ratio": _fmt_num(peg_val, 2),
        "TTM EPS (Listing Currency)": _fmt_num(eps_val, 2),
    }

    statement_metrics: Dict[str, str] = {
        f"TTM Revenue ({currency})": _fmt_money(rev_val, currency),
        f"TTM Free Cash Flow ({currency})": _fmt_money(fcf_val, currency),
        f"Net Debt TTM ({currency})": _fmt_money(net_debt_val, currency),
        "Debt / Equity": _fmt_num(d2e_val, 2),
        "WACC": _fmt_pct(wacc_val),
        "ROIC": _fmt_pct(roic_val),
        "ROE": _fmt_pct(roe_val),
        "ROA": _fmt_pct(roa_val),
        "Gross Margin (Latest Q)": _fmt_pct(gm_val),
        "Operating Margin (Latest Q)": _fmt_pct(om_val),
        "Net Margin (Latest Q)": _fmt_pct(nm_val),
        "FCF Margin (Latest Q)": _fmt_pct(fm_val),
        "Revenue YoY Growth (Latest Q)": _fmt_pct(yoy_val),
    }
    if currency != "USD":
        if rev_usd_val is not None:
            statement_metrics["TTM Revenue (USD equiv.)"] = _fmt_money(rev_usd_val, "USD")
        if fcf_usd_val is not None:
            statement_metrics["TTM Free Cash Flow (USD equiv.)"] = _fmt_money(fcf_usd_val, "USD")

    metrics_summary = {**market_metrics, **statement_metrics}

    lines = [
        f"# {clean} — Company Fundamentals Brief",
        "",
        f"- **Generated:** {date.today().isoformat()}",
        f"- **Source:** DefeatBeta Open Financial Dataset (Hugging Face / DuckDB)",
        f"- **Financial Reporting Currency:** `{currency}` *(market multiples are in USD / listing currency — kept in separate tables below)*",
        f"- **Sector / Industry:** {profile.get('sector', 'n/a')} / {profile.get('industry', 'n/a')}",
        f"- **Headquarters:** {profile.get('city', 'n/a')}, {profile.get('country', 'n/a')}",
        f"- **Employees:** {profile.get('full_time_employees', 'n/a')}",
        f"- **Website:** {profile.get('web_site', 'n/a')}",
        f"- **Next / Latest Earnings Calendar:** {next_earnings}",
        f"- **As-Of Dates:** Valuation `{mcap_dt or pe_dt or 'n/a'}` | Financials `{rev_dt or 'n/a'}`",
        "",
        "---",
        "",
        "## Business Overview",
        "",
        profile.get("long_business_summary", "_No profile summary available._"),
        "",
        "## Market Valuation (USD / Listing Currency)",
        "",
        "| Metric | Value |",
        "| :--- | ---: |",
    ]
    for k, v in market_metrics.items():
        lines.append(f"| {k} | {v} |")

    lines.extend([
        "",
        f"## Financial Statements, Returns & Margins (Reporting Currency: `{currency}`)",
        "",
        "| Metric | Value |",
        "| :--- | ---: |",
    ])
    for k, v in statement_metrics.items():
        lines.append(f"| {k} | {v} |")

    # Multi-period trends table for ROIC / ROE / Revenue Growth / Margins
    trend_sections = [
        ("ROIC Trend (Recent Periods)", _recent_series(roic_df, ["roic"], n=6), True),
        ("ROE Trend (Recent Periods)", _recent_series(roe_df, ["roe"], n=6), True),
        (
            "Quarterly Revenue YoY Growth",
            _recent_series(yoy_rev_df, ["yoy_growth", "revenue_yoy_growth", "total_revenue_yoy_growth"], n=6),
            True,
        ),
        ("Quarterly Operating Margin", _recent_series(om_df, ["operating_margin", "quarterly_operating_margin"], n=6), True),
    ]
    has_trends = any(pts for _, pts, _ in trend_sections)
    if has_trends:
        lines.extend(["", "## Historical Trends", ""])
        for title, pts, is_pct in trend_sections:
            if not pts:
                continue
            lines.append(f"### {title}")
            lines.append("")
            lines.append("| Period End | Value |")
            lines.append("| :--- | ---: |")
            for dt_s, v in pts:
                lines.append(f"| {dt_s} | {_fmt_pct(v) if is_pct else _fmt_num(v)} |")
            lines.append("")

    # Segment & breakdown metrics — keep value & unit, latest period per breakdown!
    if not _df_is_empty(breakdown_df):
        seg_rows = _format_breakdown_rows(breakdown_df, default_currency=currency)
        if seg_rows:
            lines.extend([
                "## Trailing Segment & Breakdown Metrics (Latest Period)",
                "",
                "| Period End | Breakdown | Segment / Series | Value |",
                "| :--- | :--- | :--- | ---: |",
            ])
            for dt_s, bname, sname, val_s in seg_rows:
                lines.append(f"| {dt_s} | {bname} | {sname} | {val_s} |")
            lines.append("")

    out_dir = data_root / clean
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "CompanyBrief.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")

    return CompanyBriefResult(
        ticker=clean,
        path=out_path,
        currency=currency,
        next_earnings_date=next_earnings,
        metrics=metrics_summary,
    )


_PCT_BREAKDOWN_KEYWORDS = ("margin", "percent", "%", "share of", "mix", "growth", "rate", "ratio")


def _is_percentage_breakdown(breakdown_name: str, series_name: str, value_type: str = "") -> bool:
    """Detect percentage/margin breakdowns even when upstream mislabels value_type as CURRENCY."""
    if value_type.strip().upper() in ("PERCENT", "PERCENTAGE", "RATIO"):
        return True
    combined = f"{breakdown_name} {series_name}".lower()
    return any(kw in combined for kw in _PCT_BREAKDOWN_KEYWORDS)


def _format_breakdown_rows(
    breakdown_df: Any,
    default_currency: str = "USD",
) -> List[Tuple[str, str, str, str]]:
    """Extract latest-period `(report_date, breakdown_name, series_name, formatted_value)` rows."""
    cols = set(breakdown_df.columns)
    if "value" not in cols or "series_name" not in cols:
        return []

    date_col = "report_date" if "report_date" in cols else None
    bname_col = "breakdown_name" if "breakdown_name" in cols else ("breakdown" if "breakdown" in cols else None)
    cur_col = "currency" if "currency" in cols else None
    vtype_col = "value_type" if "value_type" in cols else None

    # Find latest report_date per breakdown table so we don't repeat stale quarters
    latest_date_by_bname: Dict[str, str] = {}
    parsed: List[Tuple[str, str, str, float, str, str]] = []
    for _, row in breakdown_df.iterrows():
        raw_val = row.get("value")
        if _is_nan(raw_val):
            continue
        try:
            fval = float(raw_val)
        except (TypeError, ValueError):
            continue
        dt_s = str(row.get(date_col, "") or "")[:10] if date_col else ""
        bname = str(row.get(bname_col, "") or "Revenue Breakdown").strip() if bname_col else "Revenue Breakdown"
        sname = str(row.get("series_name", "") or "").strip()
        if not sname:
            continue
        cur = str(row.get(cur_col) or default_currency).upper() if cur_col else default_currency
        vtype = str(row.get(vtype_col) or "") if vtype_col else ""
        if bname not in latest_date_by_bname or dt_s > latest_date_by_bname[bname]:
            latest_date_by_bname[bname] = dt_s
        parsed.append((dt_s, bname, sname, fval, cur, vtype))

    rows: List[Tuple[str, str, str, str]] = []
    seen: Set[Tuple[str, str, str]] = set()
    for dt_s, bname, sname, fval, cur, vtype in parsed:
        if dt_s != latest_date_by_bname.get(bname, dt_s):
            continue
        key = (dt_s, bname, sname)
        if key in seen:
            continue
        seen.add(key)
        if _is_percentage_breakdown(bname, sname, vtype):
            val_str = f"{fval:.2f}%"
        else:
            val_str = _fmt_money(fval, cur)
        rows.append((dt_s or "n/a", bname.replace("|", "/"), sname.replace("|", "/"), val_str))
    return rows


# ============================================================================
# 2. PEERS & INDUSTRY COMPARISON (`data/<TICKER>/Peers.md`)
# ============================================================================

def generate_peers_report(
    ticker: str,
    data_root: Path = DEFAULT_DATA_ROOT,
    ticker_obj: Optional[Any] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> Path:
    """Compare company valuation & return multiples against industry aggregates."""
    clean = sanitize_ticker(ticker)
    t = ticker_obj if ticker_obj is not None else create_quiet_ticker(clean)

    if on_status:
        on_status(f"Comparing {clean} against industry peers...")

    comparisons = [
        ("TTM P/E", "ttm_pe", ["ttm_pe", "pe"], "industry_ttm_pe", ["industry_pe", "ttm_pe", "industry_ttm_pe"], False),
        ("Price / Sales (P/S)", "ps_ratio", ["ps_ratio", "ps"], "industry_ps_ratio", ["industry_ps", "ps_ratio", "industry_ps_ratio"], False),
        ("Price / Book (P/B)", "pb_ratio", ["pb_ratio", "pb"], "industry_pb_ratio", ["industry_pb", "pb_ratio", "industry_pb_ratio"], False),
        ("ROE", "roe", ["roe"], "industry_roe", ["industry_roe", "roe"], True),
        ("ROIC", "roic", ["roic"], "industry_roic", ["industry_roic", "roic"], True),
    ]

    rows: List[Tuple[str, str, str, str]] = []
    for label, co_method, co_cols, ind_method, ind_cols, is_pct in comparisons:
        co_val, _ = _latest_value(_safe_call(t, co_method), co_cols)
        ind_val, _ = _latest_value(_safe_call(t, ind_method), ind_cols)

        co_str = _fmt_pct(co_val) if is_pct else _fmt_num(co_val, 2, "x")
        ind_str = _fmt_pct(ind_val) if is_pct else _fmt_num(ind_val, 2, "x")

        spread = "n/a"
        if co_val is not None and ind_val is not None:
            diff = co_val - ind_val
            spread = f"{diff * 100:+.2f} pp" if is_pct else f"{diff:+.2f}x"
        rows.append((label, co_str, ind_str, spread))

    info_df = _safe_call(t, "info")
    industry_name = "n/a"
    if not _df_is_empty(info_df) and "industry" in info_df.columns:
        raw_ind = info_df.iloc[0].get("industry")
        if raw_ind and not _is_nan(raw_ind):
            industry_name = str(raw_ind)

    lines = [
        f"# {clean} — Industry & Peer Multiples Comparison",
        "",
        f"- **Generated:** {date.today().isoformat()}",
        f"- **Industry:** {industry_name}",
        "- **Note:** Industry benchmarks may show `n/a` for thin micro-cap industries or negative earnings.",
        "",
        "| Metric | Company | Industry Benchmark | Spread (Co − Ind) |",
        "| :--- | ---: | ---: | ---: |",
    ]
    for label, co_str, ind_str, spread in rows:
        lines.append(f"| {label} | {co_str} | {ind_str} | {spread} |")

    out_dir = data_root / clean
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "Peers.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


# ============================================================================
# 3. DCF VALUATION BUNDLE (`data/<TICKER>/Valuation/`)
# ============================================================================

@dataclass
class ValuationBundleResult:
    ticker: str
    valuation_dir: Path
    summary_md_path: Path
    json_path: Optional[Path] = None
    excel_path: Optional[Path] = None
    dcf_data: Optional[Dict[str, Any]] = None
    model_applicable: bool = False
    warnings: List[str] = field(default_factory=list)


def _is_non_positive(val: Any) -> bool:
    if _is_nan(val):
        return True
    try:
        return float(val) <= 0.0
    except (TypeError, ValueError):
        return True


def generate_valuation_bundle(
    ticker: str,
    data_root: Path = DEFAULT_DATA_ROOT,
    ticker_obj: Optional[Any] = None,
    generate_excel: bool = True,
    on_status: Optional[Callable[[str], None]] = None,
) -> ValuationBundleResult:
    """Compute DefeatBeta DCF valuation JSON, Markdown summary, and optional Excel workbook."""
    clean = sanitize_ticker(ticker)
    t = ticker_obj if ticker_obj is not None else create_quiet_ticker(clean)

    val_dir = data_root / clean / "Valuation"
    val_dir.mkdir(parents=True, exist_ok=True)

    if on_status:
        on_status(f"Computing DCF assumptions & fair value for {clean}...")

    data = _safe_call(t, "dcf_data")
    warnings: List[str] = []
    json_path: Optional[Path] = None
    excel_path: Optional[Path] = None
    model_applicable = False

    if isinstance(data, dict):
        json_path = val_dir / "dcf_data.json"
        json_path.write_text(json.dumps(data, indent=2, default=_json_default), encoding="utf-8")

        dr = data.get("discount_rate") or {}
        ge = data.get("growth_estimates") or {}
        tpl = data.get("dcf_template") or {}
        dv = data.get("dcf_value") or {}
        currency = str(ge.get("currency") or _detect_currency(t, data)).upper()

        base_fcf = tpl.get("base_fcf")
        fair_price = dv.get("fair_price")
        ev = dv.get("enterprise_value")

        invalid_reasons: List[str] = []
        if _is_non_positive(base_fcf):
            invalid_reasons.append(f"Base FCF is non-positive ({_fmt_money(base_fcf, currency)})")
        if _is_non_positive(ev):
            invalid_reasons.append("Enterprise Value is non-positive")
        if _is_non_positive(fair_price):
            invalid_reasons.append("Estimated Fair Price is non-positive")

        if invalid_reasons:
            warnings.append(
                "DCF model not applicable: " + "; ".join(invalid_reasons)
                + ". Standard unlevered FCF DCF is invalid here — use P/S, P/B, ROE, or earnings multiples instead."
            )
        else:
            model_applicable = True

        info_df = _safe_call(t, "info")
        sector = ""
        if not _df_is_empty(info_df) and "sector" in info_df.columns:
            sector = str(info_df.iloc[0].get("sector") or "")
        if "financial" in sector.lower() or "bank" in sector.lower() or "insurance" in sector.lower():
            warnings.append(
                f"Sector is '{sector}'. Traditional unlevered FCF DCF can misstate banks, insurers, and lenders — cross-check with P/B and ROE."
            )

        lines = [
            f"# {clean} — Automated DCF Valuation Summary (Reference Model)",
            "",
            f"- **Generated:** {date.today().isoformat()}",
            f"- **Reporting Currency:** `{currency}`",
            f"- **Model Status:** {'Applicable (reference estimate)' if model_applicable else 'Model not applicable — see caveats'}",
            "- **Disclaimer:** Automated reference DCF model from `defeatbeta-api` — not investment advice. Always cross-examine assumptions against management guidance in earnings transcripts.",
            "",
        ]
        if warnings:
            lines.append("> [!WARNING]")
            for w in warnings:
                lines.append(f"> - {w}")
            lines.append("")

        if model_applicable:
            lines.extend([
                "## Valuation Output",
                "",
                "| Field | Value |",
                "| :--- | ---: |",
                f"| Current Share Price | {_fmt_num(dv.get('current_price'), 2)} |",
                f"| Estimated Fair Price | {_fmt_num(fair_price, 2)} |",
                f"| Margin of Safety | {_fmt_pct(dv.get('margin_of_safety'))} |",
                f"| Model Label | `{dv.get('recommendation', 'n/a')}` |",
                f"| Enterprise Value | {_fmt_money(ev, currency)} |",
                f"| Cash | {_fmt_money(dv.get('cash'), currency)} |",
                f"| Total Debt | {_fmt_money(dv.get('total_debt'), currency)} |",
                f"| Implied Equity Value | {_fmt_money(dv.get('equity_value'), currency)} |",
                f"| Shares Outstanding | {_fmt_money(dv.get('shares_outstanding'))} |",
                "",
            ])
        else:
            lines.extend([
                "## Valuation Output",
                "",
                "_Valuation output table omitted because the mechanical FCF DCF is not applicable (non-positive base FCF, EV, or fair price). Use the assumptions below together with `CompanyBrief.md` and `Peers.md`._",
                "",
            ])

        lines.extend([
            "## Key Model Assumptions (For Context & Transcript Cross-Examination)",
            "",
            "| Assumption | Value |",
            "| :--- | ---: |",
            f"| WACC / Discount Rate | {_fmt_pct(tpl.get('discount_rate') or dr.get('wacc'))} |",
            f"| 5Y Beta | {_fmt_num(dr.get('beta_5y'), 2)} |",
            f"| Risk-Free Rate | {_fmt_pct(dr.get('risk_free_rate'))} |",
            f"| Cost of Equity | {_fmt_pct(dr.get('cost_of_equity'))} |",
            f"| Cost of Debt | {_fmt_pct(dr.get('cost_of_debt'))} |",
            f"| Effective Tax Rate | {_fmt_pct(dr.get('tax_rate'))} |",
            f"| Base FCF ({tpl.get('ttm_period') or 'TTM'}) | {_fmt_money(base_fcf, currency)} |",
            f"| Historical FCF Margin | {_fmt_pct(tpl.get('historical_fcf_margin'))} |",
            f"| Assumed FCF Growth (Years 1–5) | {_fmt_pct(tpl.get('growth_rate_1_5y'))} |",
            f"| Assumed FCF Growth (Years 6–10) | {_fmt_pct(tpl.get('growth_rate_6_10y'))} |",
            f"| Assumed Revenue Growth (Years 1–5) | {_fmt_pct(tpl.get('revenue_growth_1_5y'))} |",
            f"| Assumed Revenue Growth (Years 6–10) | {_fmt_pct(tpl.get('revenue_growth_6_10y'))} |",
            f"| Terminal Growth Rate (5Y Treasury Avg) | {_fmt_pct(tpl.get('growth_rate_terminal'))} |",
            "",
        ])
    else:
        warnings.append("DefeatBeta could not compute a DCF model for this ticker (insufficient cash-flow or market data).")
        lines = [
            f"# {clean} — DCF Valuation Summary",
            "",
            f"- **Generated:** {date.today().isoformat()}",
            "- **Model Status:** Unavailable — DefeatBeta could not compute DCF for this ticker (common for newly IPO'd, negative-FCF, or financial-sector firms).",
            "",
        ]

    summary_path = val_dir / "DCF_Summary.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")

    if generate_excel and isinstance(data, dict) and model_applicable:
        if on_status:
            on_status(f"Building editable Excel DCF workbook for {clean}...")
        dcf_out = _safe_call(t, "dcf")
        if isinstance(dcf_out, dict) and dcf_out.get("file_path"):
            src_xlsx = Path(str(dcf_out["file_path"]))
            if src_xlsx.exists():
                dst_xlsx = val_dir / f"{clean}_DCF.xlsx"
                shutil.copy2(src_xlsx, dst_xlsx)
                excel_path = dst_xlsx

    return ValuationBundleResult(
        ticker=clean,
        valuation_dir=val_dir,
        summary_md_path=summary_path,
        json_path=json_path,
        excel_path=excel_path,
        dcf_data=data if isinstance(data, dict) else None,
        model_applicable=model_applicable,
        warnings=warnings,
    )


# ============================================================================
# 4. SEC COVERAGE AUDIT (`data/<TICKER>/Coverage.md`)
# ============================================================================

@dataclass
class CoverageAuditResult:
    ticker: str
    path: Path
    total_indexed: int = 0
    recent_indexed: int = 0
    local_markdown_count: int = 0
    notable_extra_forms: Dict[str, int] = field(default_factory=dict)


def _normalize_accession(acc: str) -> str:
    return re.sub(r"[^0-9]", "", acc or "")


def _index_local_sec_files(md_dir: Path) -> Tuple[Set[str], Set[str], List[Path]]:
    """Index local SEC Markdown files by accession_number (from HTML metadata comment
    or legacy YAML front-matter) and exact filing_date (from header or filename YYYY-MM-DD).
    """
    if not md_dir.exists():
        return set(), set(), []

    accessions: Set[str] = set()
    exact_dates: Set[str] = set()
    files = sorted(md_dir.glob("*.md"))

    for path in files:
        # Read only the first 512 bytes for metadata header
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:512]
        except OSError:
            head = ""

        if head.startswith("<!--") or head.startswith("---"):
            m_acc = _FRONTMATTER_ACC_RE.search(head)
            if m_acc:
                norm = _normalize_accession(m_acc.group(1))
                if norm:
                    accessions.add(norm)
            m_dt = _FRONTMATTER_DATE_RE.search(head)
            if m_dt:
                exact_dates.add(m_dt.group(1))

        # Legacy filename fallback: exact YYYY-MM-DD in filename (never 4-digit year!)
        for m_file_dt in re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", path.name):
            exact_dates.add(m_file_dt)

    return accessions, exact_dates, files


def generate_sec_coverage_audit(
    ticker: str,
    num_years: int = 2,
    data_root: Path = DEFAULT_DATA_ROOT,
    ticker_obj: Optional[Any] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> CoverageAuditResult:
    """Compare upstream DefeatBeta `sec_filing()` index against local SEC Markdown files."""
    clean = sanitize_ticker(ticker)
    t = ticker_obj if ticker_obj is not None else create_quiet_ticker(clean)

    if on_status:
        on_status(f"Auditing SEC filing index vs. local Markdown for {clean}...")

    df = _safe_call(t, "sec_filing")
    md_dir = data_root / clean / "DataFromDataMule_markdown"
    local_accessions, local_dates, local_files = _index_local_sec_files(md_dir)

    cutoff_year = date.today().year - max(1, int(num_years)) + 1
    cutoff_prefix = f"{cutoff_year}-01-01"

    total_indexed = 0
    recent_rows: List[Dict[str, str]] = []
    extra_forms: Dict[str, int] = {}
    notable_extra_set = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "4", "4/A", "3", "144", "DEF 14A", "40-F"}

    if not _df_is_empty(df):
        total_indexed = len(df)
        fdate_col = next((c for c in ("filing_date", "report_date", "date") if c in df.columns), None)
        ftype_col = next((c for c in ("form_type", "form", "type") if c in df.columns), None)
        acc_col = next((c for c in ("accession_number", "accession") if c in df.columns), None)
        url_col = next((c for c in ("filing_url", "url") if c in df.columns), None)
        desc_col = next((c for c in ("form_type_description", "description") if c in df.columns), None)

        for _, row in df.iterrows():
            fdate = str(row.get(fdate_col, "") or "")[:10] if fdate_col else ""
            ftype = str(row.get(ftype_col, "") or "").upper() if ftype_col else ""
            facc = _normalize_accession(str(row.get(acc_col, "") or "")) if acc_col else ""
            furl = str(row.get(url_col, "") or "") if url_col else ""
            fdesc = str(row.get(desc_col, "") or "") if desc_col else ""

            if fdate and fdate >= cutoff_prefix:
                # Match by exact accession number first (100% reliable),
                # falling back to exact filing date for legacy files without front-matter.
                matched = bool(
                    (facc and facc in local_accessions)
                    or (not facc and fdate in local_dates)
                    or (facc and not local_accessions and fdate in local_dates)
                )
                recent_rows.append({
                    "date": fdate,
                    "form": ftype,
                    "desc": fdesc,
                    "url": furl,
                    "local": "Yes" if matched else "Missing",
                })
                if ftype in notable_extra_set:
                    extra_forms[ftype] = extra_forms.get(ftype, 0) + 1

    recent_rows.sort(key=lambda r: r["date"], reverse=True)

    lines = [
        f"# {clean} — SEC Filing Coverage Audit",
        "",
        f"- **Generated:** {date.today().isoformat()}",
        f"- **Window Audited:** `{cutoff_prefix}` → present ({num_years} yr)",
        f"- **Total SEC Filings Indexed (All-Time):** {total_indexed}",
        f"- **Filings in Window:** {len(recent_rows)}",
        f"- **Local Markdown Files (`DataFromDataMule_markdown/`):** {len(local_files)}",
        "",
    ]

    if extra_forms:
        lines.append("## Notable Ownership / Insider / Governance Filings in Window")
        lines.append("")
        for ftype, cnt in sorted(extra_forms.items()):
            lines.append(f"- **{ftype}**: {cnt} filing(s)")
        lines.append("")

    lines.extend([
        "## Recent Filings Index vs. Local Disk",
        "",
        "| Filing Date | Form | Local `.md`? | Description | EDGAR Link |",
        "| :--- | :--- | :---: | :--- | :--- |",
    ])
    for r in recent_rows[:60]:
        link = f"[View]({r['url']})" if r["url"] else "n/a"
        desc = r["desc"].replace("|", "/")[:60] if r["desc"] else ""
        lines.append(f"| {r['date']} | `{r['form']}` | {r['local']} | {desc} | {link} |")

    out_dir = data_root / clean
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "Coverage.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return CoverageAuditResult(
        ticker=clean,
        path=out_path,
        total_indexed=total_indexed,
        recent_indexed=len(recent_rows),
        local_markdown_count=len(local_files),
        notable_extra_forms=extra_forms,
    )


# ============================================================================
# 5. BATCH PREFLIGHT MATRIX & EARNINGS CALENDAR (FOR 10-STOCK PORTFOLIOS)
# ============================================================================

def build_portfolio_preflight(
    tickers: Sequence[str],
    num_years: int = 2,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return `(calendar_rows, transcript_matrix_rows)` for a portfolio watchlist."""
    clean_tickers = [sanitize_ticker(tk) for tk in tickers if tk.strip()]
    if not clean_tickers:
        return [], []

    targets = expected_quarters(num_years)
    q_labels = [f"Q{q} {yr}" for yr, q in targets]

    catalogs: Dict[str, set] = {tk: set() for tk in clean_tickers}
    cal_dates: Dict[str, str] = {tk: "n/a" for tk in clean_tickers}

    try:
        from defeatbeta_api.data.tickers import Tickers as DBTickers

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            batch = DBTickers(clean_tickers, log_level=logging.ERROR)
            tr_map = batch.earning_call_transcripts()
            cal_df = batch.calendar()

        if isinstance(tr_map, dict):
            for tk, tr_obj in tr_map.items():
                try:
                    df_l = tr_obj.get_transcripts_list()
                    if not _df_is_empty(df_l):
                        for _, row in df_l.iterrows():
                            catalogs[tk.upper()].add(
                                (int(row["fiscal_year"]), int(row["fiscal_quarter"]))
                            )
                except Exception:
                    pass

        if not _df_is_empty(cal_df) and "symbol" in cal_df.columns:
            today_str = date.today().isoformat()
            d_col = next((c for c in ("report_date", "earnings_date", "date") if c in cal_df.columns), None)
            if d_col:
                for tk in clean_tickers:
                    sub = cal_df[cal_df["symbol"].astype(str).str.upper() == tk]
                    if not _df_is_empty(sub):
                        ds = sorted(str(v)[:10] for v in sub[d_col].dropna().tolist())
                        up = [d for d in ds if d >= today_str]
                        cal_dates[tk] = up[0] if up else (f"{ds[-1]} (last)" if ds else "n/a")
    except Exception:
        pass

    calendar_rows: List[Dict[str, Any]] = []
    matrix_rows: List[Dict[str, Any]] = []

    for tk in clean_tickers:
        tr_dir = data_root / tk / "EarningsTranscripts"
        on_disk = set(scan_existing_quarters(tr_dir).keys())
        upstream = catalogs.get(tk, set())

        calendar_rows.append({
            "Ticker": tk,
            "Next / Last Earnings": cal_dates.get(tk, "n/a"),
            "Transcripts on Disk": len(on_disk),
            "Upstream Catalog Total": len(upstream),
            "CompanyBrief.md": "✅" if (data_root / tk / "CompanyBrief.md").exists() else "—",
            "DCF Ready": "✅" if (data_root / tk / "Valuation" / "DCF_Summary.md").exists() else "—",
        })

        row: Dict[str, Any] = {"Ticker": tk}
        for (yr, q), lbl in zip(targets, q_labels):
            if (yr, q) in on_disk:
                row[lbl] = "✅ Have"
            elif (yr, q) in upstream:
                row[lbl] = "⬇️ Free (HF)"
            elif upstream and (yr, q) < min(upstream):
                row[lbl] = "— Pre-IPO/N/A"
            else:
                row[lbl] = "🔍 API Check"
        matrix_rows.append(row)

    return calendar_rows, matrix_rows


# ============================================================================
# 6. PROTOCOL-READY VALUATION PACK (`data/<TICKER>/ValuationPack.md`)
# ============================================================================

@dataclass
class ValuationPackResult:
    ticker: str
    path: Path
    currency: str = "USD"
    home_currency: str = "USD"
    latest_price: Optional[float] = None
    latest_price_date: str = ""
    fx_rate: Optional[float] = None
    fx_date: str = ""
    fx_source: str = ""


def fetch_spot_fx_rate(
    from_ccy: str,
    to_ccy: str,
    timeout: int = 10,
    session: Optional[Any] = None,
) -> Tuple[Optional[float], str, str]:
    """Fetch latest spot FX rate (`1 from_ccy = X to_ccy`) from free ECB/Frankfurter API."""
    src = (from_ccy or "USD").strip().upper()
    dst = (to_ccy or "USD").strip().upper()
    if src == dst:
        return 1.0, date.today().isoformat(), "Identity (same currency)"

    try:
        import requests as _req

        http = session or _req
        resp = http.get(
            f"https://api.frankfurter.app/latest?from={src}&to={dst}",
            timeout=timeout,
        )
        if getattr(resp, "status_code", None) != 200:
            return None, "", ""
        payload = resp.json()
        rates = payload.get("rates") if isinstance(payload, dict) else None
        if isinstance(rates, dict) and dst in rates:
            return float(rates[dst]), str(payload.get("date") or date.today().isoformat()), "ECB/Frankfurter"
    except Exception:
        pass
    return None, "", ""


def _extract_statement_df(obj: Any) -> Any:
    """Unwrap a DefeatBeta Statement object (`.df()`) or return DataFrame as-is."""
    if obj is None:
        return None
    df_fn = getattr(obj, "df", None)
    if callable(df_fn):
        try:
            return df_fn()
        except Exception:
            return None
    return obj


_NON_CURRENCY_LINE_RE = re.compile(r"\b(shares?|eps|ratio)\b", re.IGNORECASE)


def _format_statement_table(
    raw_obj: Any,
    wanted_rows: Sequence[str],
    max_periods: int = 8,
    currency: str = "USD",
) -> List[str]:
    """Render selected line items from a DefeatBeta financial statement into a clean Markdown table."""
    df = _extract_statement_df(raw_obj)
    if _df_is_empty(df):
        return []

    cols = list(df.columns)
    if not cols:
        return []

    # DefeatBeta statements put line item names in the first column ("Breakdown") or index
    label_col = cols[0] if str(cols[0]).lower() in ("breakdown", "item", "metric", "line_item") else None
    period_cols = [c for c in cols if c != label_col][:max_periods]
    if not period_cols:
        return []

    wanted_lower = [w.lower() for w in wanted_rows]
    matched_rows: List[Tuple[str, List[str]]] = []
    seen_labels: Set[str] = set()

    for idx, row in df.iterrows():
        raw_label = str(row[label_col] if label_col is not None else idx).strip()
        if not raw_label:
            continue
        low = raw_label.lower()
        if wanted_lower and not any(w in low for w in wanted_lower):
            continue
        if low in seen_labels:
            continue
        seen_labels.add(low)

        is_non_currency = bool(_NON_CURRENCY_LINE_RE.search(raw_label))
        vals: List[str] = []
        has_any = False
        for pc in period_cols:
            v = row.get(pc)
            if _is_nan(v) or str(v).strip() in ("", "*", "None", "nan"):
                vals.append("n/a")
            else:
                try:
                    fv = float(str(v).replace(",", ""))
                    has_any = True
                    if is_non_currency:
                        vals.append(_fmt_money(fv) if abs(fv) >= 1_000 else _fmt_num(fv, 2))
                    else:
                        vals.append(_fmt_money(fv, currency))
                except (TypeError, ValueError):
                    vals.append(str(v).strip())
                    has_any = True
        if has_any:
            matched_rows.append((raw_label.replace("|", "/"), vals))

    if not matched_rows:
        return []

    header = "| Line Item | " + " | ".join(str(c) for c in period_cols) + " |"
    sep = "| :--- | " + " | ".join("---:" for _ in period_cols) + " |"
    lines = [header, sep]
    for lbl, vals in matched_rows:
        lines.append(f"| {lbl} | " + " | ".join(vals) + " |")
    return lines


def _extract_latest_row_value(raw_obj: Any, row_keyword: str) -> Optional[float]:
    """Extract the most recent numeric value for a specific line item from a DefeatBeta statement."""
    df = _extract_statement_df(raw_obj)
    if _df_is_empty(df):
        return None
    cols = list(df.columns)
    if not cols:
        return None
    label_col = cols[0] if str(cols[0]).lower() in ("breakdown", "item", "metric", "line_item") else None
    period_cols = [c for c in cols if c != label_col]
    kw = row_keyword.lower()
    for idx, row in df.iterrows():
        raw_label = str(row[label_col] if label_col is not None else idx).strip().lower()
        if kw not in raw_label:
            continue
        for pc in period_cols:
            v = row.get(pc)
            if _is_nan(v) or str(v).strip() in ("", "*", "None", "nan"):
                continue
            try:
                return float(str(v).replace(",", ""))
            except (TypeError, ValueError):
                continue
    return None


def _compute_multiple_band(df: Any, preferred_cols: Sequence[str], min_year: int) -> Optional[Tuple[int, float, float, float, float, float]]:
    """Compute (n_points, min, p25, median, p75, max) for a positive historical valuation multiple."""
    if _df_is_empty(df):
        return None
    cols = list(df.columns)
    val_col = next((c for c in preferred_cols if c in cols), None)
    date_col = next((c for c in ("report_date", "date", "period_end_date") if c in cols), None)
    if not val_col:
        return None

    vals: List[float] = []
    for _, row in df.iterrows():
        if date_col:
            dt_s = str(row.get(date_col) or "")[:4]
            if dt_s.isdigit() and int(dt_s) < min_year:
                continue
        raw = row.get(val_col)
        if _is_nan(raw):
            continue
        try:
            fv = float(raw)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            vals.append(fv)

    if not vals:
        return None
    vals.sort()
    n = len(vals)

    def _pct(p: float) -> float:
        idx = min(n - 1, max(0, int(round(p * (n - 1)))))
        return vals[idx]

    return (n, vals[0], _pct(0.25), _pct(0.50), _pct(0.75), vals[-1])


def generate_valuation_pack(
    ticker: str,
    home_currency: str = "USD",
    data_root: Path = DEFAULT_DATA_ROOT,
    ticker_obj: Optional[Any] = None,
    fx_session: Optional[Any] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> ValuationPackResult:
    """Build `data/<TICKER>/ValuationPack.md` with structured tables tailored to the 5-Year Hurdle-Rate Protocol."""
    clean = sanitize_ticker(ticker)
    home_ccy = (home_currency or "USD").strip().upper()
    t = ticker_obj if ticker_obj is not None else create_quiet_ticker(clean)

    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    _emit(f"Extracting latest price, FX & reporting currency for {clean}...")
    currency = _detect_currency(t)
    price_val, price_dt = _latest_value(_safe_call(t, "price"), ["close", "adj_close", "price"])
    fx_rate, fx_dt, fx_src = fetch_spot_fx_rate(currency, home_ccy, session=fx_session)

    mcap_val, mcap_dt = _latest_value(_safe_call(t, "market_capitalization"), ["market_capitalization", "market_cap"])
    ev_val, _ = _latest_value(_safe_call(t, "enterprise_value"), ["enterprise_value", "ev"])
    pe_df = _safe_call(t, "ttm_pe")
    pb_df = _safe_call(t, "pb_ratio")
    ps_df = _safe_call(t, "ps_ratio")
    pe_val, _ = _latest_value(pe_df, ["ttm_pe", "pe"])
    pb_val, _ = _latest_value(pb_df, ["pb_ratio", "pb"])
    ps_val, _ = _latest_value(ps_df, ["ps_ratio", "ps"])

    _emit(f"Extracting quarterly & annual financial statements for {clean}...")
    q_inc = _safe_call(t, "quarterly_income_statement")
    a_inc = _safe_call(t, "annual_income_statement")
    q_bs = _safe_call(t, "quarterly_balance_sheet")
    a_bs = _safe_call(t, "annual_balance_sheet")
    q_cf = _safe_call(t, "quarterly_cash_flow")
    a_cf = _safe_call(t, "annual_cash_flow")

    implied_tradable_shares: Optional[float] = None
    if mcap_val is not None and price_val is not None and mcap_val > 0 and price_val > 0:
        implied_tradable_shares = mcap_val / price_val

    ord_shares = (
        _extract_latest_row_value(q_bs, "ordinary shares number")
        or _extract_latest_row_value(a_bs, "ordinary shares number")
    )
    ads_ratio_warning = ""
    if implied_tradable_shares and ord_shares and ord_shares / implied_tradable_shares >= 1.5:
        ratio_est = round(ord_shares / implied_tradable_shares)
        ads_ratio_warning = (
            f"> [!WARNING]\n"
            f"> **ADS / Share-Class Ratio Alert:** Balance-sheet `Ordinary Shares Number` "
            f"({_fmt_money(ord_shares)}) is **~{ratio_est}:1** vs. implied tradable shares "
            f"(`Market Cap / Price = {_fmt_money(implied_tradable_shares)}`). "
            f"Verify the exact `1 ADS = N Ordinary Shares` conversion ratio in the latest `20-F`/`6-K` "
            f"and ALWAYS divide terminal equity by the **tradable / ADS share count** ({_fmt_money(implied_tradable_shares)}), "
            f"never raw ordinary shares.\n"
        )

    inc_keywords = (
        "total revenue", "gross profit", "operating income", "ebit", "net interest",
        "interest income", "interest expense", "pretax income", "tax provision",
        "net income", "normalized income", "diluted average shares", "basic average shares",
        "reconciled depreciation", "ebitda",
    )
    bs_keywords = (
        "cash and cash equivalents", "cash cash equivalents", "other short term investments",
        "restricted cash", "investments and advances", "long term equity investment",
        "total assets", "total liabilities", "total debt", "long term debt", "current debt",
        "lease", "minority interest", "preferred stock", "stockholders equity",
        "common stock equity", "tangible book value",
        "total equity gross minority interest", "ordinary shares number", "share issued",
    )
    cf_keywords = (
        "operating cash flow", "capital expenditure", "free cash flow",
        "depreciation", "stock based compensation", "repurchase of capital stock",
        "common stock issuance", "cash dividends paid", "change in working capital",
    )

    lines = [
        f"# {clean} — Valuation Protocol Dataset Pack (`ValuationPack.md`)",
        "",
        f"- **Generated:** {date.today().isoformat()}",
        f"- **Reporting Currency:** `{currency}` | **Investor Home Currency:** `{home_ccy}`",
        "- **Purpose:** Pre-extracted structured financial tables & historical valuation bands tailored for the 5-Year Hurdle-Rate Valuation Protocol. Cross-check footnotes (ADS ratio, VIE/offshore cash split, one-time items) against attached SEC filings (`10-K`/`20-F`/`10-Q`/`6-K`) and transcripts.",
        "",
        "## 1. Market Snapshot & Spot FX (`[Market Data]`)",
        "",
        "| Field | Value | Source Tag |",
        "| :--- | ---: | :--- |",
        f"| Latest Close Price (Listing / ADS CCY) | {_fmt_num(price_val, 2) if price_val is not None else 'not provided'} | `[Market Data: DefeatBeta, {price_dt or 'n/a'}]` |",
        f"| Implied Shares Outstanding (Tradable / ADS Basis) | {_fmt_money(implied_tradable_shares) if implied_tradable_shares is not None else 'not provided'} | `[Derived: Market Cap / Close Price]` |",
        f"| Spot FX (`1 {currency} -> {home_ccy}`) | {f'{fx_rate:.6f}' if fx_rate is not None else 'not provided'} | `[Market Data: {fx_src or 'unavailable'}, {fx_dt or 'n/a'}]` |",
        f"| Market Cap (USD) | {_fmt_money(mcap_val, 'USD')} | `[Market Data: DefeatBeta, {mcap_dt or 'n/a'}]` |",
        f"| Enterprise Value (USD) | {_fmt_money(ev_val, 'USD')} | `[Market Data: DefeatBeta, {mcap_dt or 'n/a'}]` |",
        f"| TTM P/E | {_fmt_num(pe_val, 2, 'x')} | `[Market Data: DefeatBeta, {mcap_dt or 'n/a'}]` |",
        f"| Price / Book (P/B) | {_fmt_num(pb_val, 2, 'x')} | `[Market Data: DefeatBeta, {mcap_dt or 'n/a'}]` |",
        f"| Price / Sales (P/S) | {_fmt_num(ps_val, 2, 'x')} | `[Market Data: DefeatBeta, {mcap_dt or 'n/a'}]` |",
        "",
    ]
    if ads_ratio_warning:
        lines.extend([ads_ratio_warning])

    # Section 2: Historical 5-10Y Multiple Band (Anchor 2)
    min_yr = date.today().year - 10
    band_rows = []
    for lbl, bdf, bcols in (
        ("TTM P/E", pe_df, ["ttm_pe", "pe"]),
        ("Price / Book (P/B)", pb_df, ["pb_ratio", "pb"]),
        ("Price / Sales (P/S)", ps_df, ["ps_ratio", "ps"]),
    ):
        band = _compute_multiple_band(bdf, bcols, min_year=min_yr)
        if band:
            n_pts, mn, p25, med, p75, mx = band
            band_rows.append(
                f"| {lbl} | {n_pts} | {mn:.2f}x | {p25:.2f}x | **{med:.2f}x** | {p75:.2f}x | {mx:.2f}x |"
            )

    if band_rows:
        lines.extend([
            "## 2. Historical Valuation Band — Anchor 2 (Up to 10 Years, Positive Observations)",
            "",
            "| Multiple | Obs. | Min | 25th Pct | Median | 75th Pct | Max |",
            "| :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
            *band_rows,
            "",
        ])

    # Sections 3-8: Structured Financial Statements
    stmt_sections = [
        ("3. Quarterly Income Statement (Seasonality & Clean Run-Rate)", q_inc, inc_keywords, 8),
        ("4. Annual Income Statement (Multi-Year Trend)", a_inc, inc_keywords, 5),
        ("5. Quarterly Balance Sheet (Cash, Debt, Leases, NCI & Parent Equity)", q_bs, bs_keywords, 4),
        ("6. Annual Balance Sheet (Historical Invested Capital)", a_bs, bs_keywords, 4),
        ("7. Annual Cash Flow Statement (Cash Conversion, CapEx, SBC & Executed Buybacks)", a_cf, cf_keywords, 5),
        ("8. Quarterly Cash Flow Statement (Recent Cash Flow & Buyback Pace)", q_cf, cf_keywords, 4),
    ]
    for title, raw_stmt, kw, max_p in stmt_sections:
        tbl = _format_statement_table(raw_stmt, kw, max_periods=max_p, currency=currency)
        if tbl:
            lines.extend([f"## {title} (`{currency}`)", "", *tbl, ""])

    out_dir = data_root / clean
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "ValuationPack.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")

    return ValuationPackResult(
        ticker=clean,
        path=out_path,
        currency=currency,
        home_currency=home_ccy,
        latest_price=price_val,
        latest_price_date=price_dt,
        fx_rate=fx_rate,
        fx_date=fx_dt,
        fx_source=fx_src,
    )

