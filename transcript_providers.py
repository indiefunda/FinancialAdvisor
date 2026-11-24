"""Multi-provider, quota-aware earnings call transcript downloader.

Implements a redundancy waterfall designed to maximize free-tier API allowances:
  0. Local disk cache     — 0 API calls for valid transcripts already downloaded
  1. DefeatBeta (HF)      — Unlimited, 0 credits, 10+ yrs, speaker-labeled
  2. ROIC.ai API v3       — 5 req/min, latest 2 calls (~1h after call)
  3. Alpha Vantage        — 25 req/day, any quarter since 2010Q1, speaker + sentiment
  4. Firecrawl (v2)       — 1,000 credits/mo, last-resort web fallback
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import requests

from config_manager import (
    DEFAULT_PROVIDER_ORDER,
    PROVIDER_SPECS,
    QuotaManager,
    load_api_keys,
)
from sec_downloader import DEFAULT_DATA_ROOT, sanitize_ticker

_EXISTING_FILE_RE = re.compile(r"Q([1-4])\s+(\d{4})\.md$", re.IGNORECASE)
_MIN_CACHED_BYTES = 200


@dataclass
class TranscriptRecord:
    ticker: str
    fiscal_year: int
    fiscal_quarter: int
    provider: str
    markdown_body: str
    report_date: str = ""
    source_url: str = ""
    has_speakers: bool = False


@dataclass
class TranscriptRunResult:
    ticker: str
    output_dir: Path
    saved_files: List[Tuple[str, Path]] = field(default_factory=list)  # (provider, path)
    cached_quarters: List[Tuple[int, int]] = field(default_factory=list)
    missing_quarters: List[Tuple[int, int]] = field(default_factory=list)
    unavailable_upstream: List[Tuple[int, int]] = field(default_factory=list)
    provider_notes: Dict[str, str] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)


def _safe_json(resp: requests.Response) -> Any:
    """Parse JSON safely, returning None on HTML/non-JSON error pages."""
    try:
        return resp.json()
    except (ValueError, TypeError):
        return None


def expected_quarters(num_years: int, today: Optional[date] = None) -> List[Tuple[int, int]]:
    """Return completed `(year, quarter)` pairs for the last `num_years`, newest first.

    Excludes the in-progress calendar quarter so quota-limited providers (Alpha Vantage,
    Firecrawl) never burn calls on a quarter that has not ended yet. Listing-based
    providers (DefeatBeta, ROIC.ai) still discover any already-reported fiscal quarters.
    """
    ref = today or date.today()
    cutoff_year = ref.year - max(1, int(num_years)) + 1
    current_q = (ref.month - 1) // 3 + 1

    # Latest completed calendar quarter
    if current_q == 1:
        latest_yr, latest_q = ref.year - 1, 4
    else:
        latest_yr, latest_q = ref.year, current_q - 1

    start_yr = min(cutoff_year, latest_yr)
    quarters: List[Tuple[int, int]] = []
    for yr in range(latest_yr, start_yr - 1, -1):
        max_q = latest_q if yr == latest_yr else 4
        for q in range(max_q, 0, -1):
            quarters.append((yr, q))
    return quarters


def scan_existing_quarters(transcripts_dir: Path) -> Dict[Tuple[int, int], Path]:
    """Return `(year, quarter) -> Path` for valid (non-empty) transcripts on disk."""
    found: Dict[Tuple[int, int], Path] = {}
    if not transcripts_dir.exists():
        return found
    for path in transcripts_dir.glob("*.md"):
        try:
            if path.stat().st_size < _MIN_CACHED_BYTES:
                continue
        except OSError:
            continue
        m = _EXISTING_FILE_RE.search(path.name)
        if m:
            q, yr = int(m.group(1)), int(m.group(2))
            found[(yr, q)] = path
    return found


def clean_transcript_text(text: str) -> str:
    """Normalize whitespace to keep Markdown compact for LLM context windows."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def format_transcript_markdown(record: TranscriptRecord) -> str:
    """Render a `TranscriptRecord` into standardized LLM-ready Markdown."""
    q_label = f"Q{record.fiscal_quarter} {record.fiscal_year}"
    lines = [
        f"# {record.ticker} - {q_label} Earnings Call Transcript",
        "",
        f"- **Ticker:** {record.ticker}",
        f"- **Fiscal Period:** {q_label}",
    ]
    if record.report_date:
        lines.append(f"- **Call / Report Date:** {record.report_date}")
    lines.append(f"- **Source Provider:** {record.provider}")
    if record.source_url:
        lines.append(f"- **Source URL:** {record.source_url}")
    lines.extend(["", "---", "", clean_transcript_text(record.markdown_body), ""])
    return "\n".join(lines)


def save_transcript_record(record: TranscriptRecord, transcripts_dir: Path) -> Path:
    """Write a `TranscriptRecord` to `transcripts_dir` and return its path."""
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    safe_ticker = re.sub(r'[\\/:*?"<>|]', "", record.ticker)
    filename = f"{safe_ticker} - Q{record.fiscal_quarter} {record.fiscal_year}.md"
    out_path = transcripts_dir / filename
    out_path.write_text(format_transcript_markdown(record), encoding="utf-8")
    return out_path


# ============================================================================
# PROVIDER 1: DefeatBeta (Hugging Face Parquet + DuckDB — Unlimited, Free)
# ============================================================================

def fetch_from_defeatbeta(
    ticker: str,
    cutoff_year: int,
    skip_quarters: Set[Tuple[int, int]],
    log: Callable[[str], None],
    catalog_out: Optional[Set[Tuple[int, int]]] = None,
) -> Tuple[List[TranscriptRecord], str]:
    """Fetch available transcripts from `defeatbeta-api` (Hugging Face dataset)."""
    try:
        from defeatbeta_api.data.ticker import Ticker as DBTicker
    except ImportError:
        return [], "defeatbeta-api not installed (pip install defeatbeta-api)"

    records: List[TranscriptRecord] = []
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            t = DBTicker(ticker, log_level=logging.ERROR)
            tr = t.earning_call_transcripts()
            df_list = tr.get_transcripts_list()
    except Exception as exc:
        return [], f"DefeatBeta lookup failed: {exc}"

    if df_list is None or getattr(df_list, "empty", True):
        return [], "No transcripts found in DefeatBeta dataset"

    fetched_count = 0
    for _, row in df_list.iterrows():
        try:
            yr = int(row["fiscal_year"])
            q = int(row["fiscal_quarter"])
        except (KeyError, TypeError, ValueError):
            continue
        if catalog_out is not None:
            catalog_out.add((yr, q))
        if yr < cutoff_year or (yr, q) in skip_quarters:
            continue

        report_date = str(row.get("report_date", "") or "")
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                df_call = tr.get_transcript(yr, q)
        except Exception as exc:
            log(f"  [DefeatBeta] Q{q} {yr} error: {exc}")
            continue

        if df_call is None or getattr(df_call, "empty", True):
            continue

        parts: List[str] = []
        for _, seg in df_call.iterrows():
            speaker = str(seg.get("speaker", "") or "").strip()
            content = str(seg.get("content", "") or "").strip()
            if not content:
                continue
            if speaker:
                parts.append(f"**{speaker}:** {content}")
            else:
                parts.append(content)

        body = "\n\n".join(parts)
        if len(body) > 200:
            records.append(
                TranscriptRecord(
                    ticker=ticker,
                    fiscal_year=yr,
                    fiscal_quarter=q,
                    report_date=report_date,
                    provider="defeatbeta",
                    markdown_body=body,
                    has_speakers=True,
                )
            )
            fetched_count += 1
            log(f"  [DefeatBeta] Q{q} {yr} fetched ({len(body):,} chars)")

    return records, f"Fetched {fetched_count} quarter(s)"


# ============================================================================
# PROVIDER 2: ROIC.ai API v3 (5 req/min, Latest 2 Calls on Free Tier)
# ============================================================================

class RoicClient:
    BASE_URL = "https://api.roic.ai/v3.0.0"

    def __init__(
        self,
        api_key: str,
        quota: QuotaManager,
        session: Optional[requests.Session] = None,
    ):
        self.api_key = api_key
        self.quota = quota
        self.session = session or requests.Session()
        self._symbol_cache: Dict[str, str] = {}

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        self.quota.roic_wait_if_needed()
        query = dict(params or {})
        query["apikey"] = self.api_key
        resp = self.session.get(f"{self.BASE_URL}/{path.lstrip('/')}", params=query, timeout=30)
        self.quota.record_roic_call()
        return resp

    def resolve_identifier(self, ticker: str) -> str:
        """Resolve bare ticker (e.g. AAPL) to exchange-qualified symbol (NASDAQ:AAPL)."""
        if ":" in ticker:
            return ticker
        if ticker in self._symbol_cache:
            return self._symbol_cache[ticker]

        try:
            resp = self._get("tickers/search", {"query": ticker, "limit": 10})
            if resp.status_code == 200:
                payload = _safe_json(resp)
                items = payload.get("data", payload) if isinstance(payload, dict) else payload
                if isinstance(items, list):
                    us_exchanges = {"NASDAQ", "NYSE", "AMEX", "NYSEARCA", "BATS"}
                    exact = []
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        sym = str(item.get("symbol", "") or "")
                        short = sym.split(":")[-1].upper()
                        if short == ticker.upper():
                            exact.append((item.get("exchange", "").upper() in us_exchanges, sym))
                    if exact:
                        exact.sort(key=lambda x: (not x[0], x[1]))
                        self._symbol_cache[ticker] = exact[0][1]
                        return exact[0][1]
        except Exception:
            pass

        self._symbol_cache[ticker] = ticker
        return ticker

    def fetch_missing(
        self,
        ticker: str,
        cutoff_year: int,
        skip_quarters: Set[Tuple[int, int]],
        log: Callable[[str], None],
    ) -> Tuple[List[TranscriptRecord], str]:
        identifier = self.resolve_identifier(ticker)
        try:
            resp = self._get("earnings-calls", {"identifier": identifier, "limit": 20})
        except Exception as exc:
            return [], f"ROIC.ai request failed: {exc}"

        if resp.status_code == 401:
            return [], "ROIC.ai API key rejected (HTTP 401)"
        if resp.status_code == 429:
            return [], "ROIC.ai rate limit hit (HTTP 429)"
        if resp.status_code != 200:
            return [], f"ROIC.ai list returned HTTP {resp.status_code}"

        payload = _safe_json(resp)
        if not isinstance(payload, dict):
            return [], "ROIC.ai returned non-JSON response on call listing"

        calls = payload.get("data", [])
        if not isinstance(calls, list) or not calls:
            return [], f"No earnings calls listed for {identifier}"

        records: List[TranscriptRecord] = []
        restricted_hits = 0

        for call in calls:
            if not isinstance(call, dict):
                continue
            try:
                yr = int(call.get("fiscal_year") or 0)
                q = int(call.get("fiscal_quarter") or 0)
            except (TypeError, ValueError):
                continue
            if yr < cutoff_year or q not in (1, 2, 3, 4):
                continue
            if (yr, q) in skip_quarters:
                continue

            call_date = str(call.get("date") or call.get("period_end_date") or "")
            try:
                tr_resp = self._get(
                    f"earnings-calls/{identifier}",
                    {"fiscal_year": yr, "fiscal_quarter": q},
                )
            except Exception as exc:
                log(f"  [ROIC.ai] Q{q} {yr} request error: {exc}")
                continue

            if tr_resp.status_code == 402:
                restricted_hits += 1
                log(f"  [ROIC.ai] Q{q} {yr} outside free-tier window (latest 2 calls only)")
                continue
            if tr_resp.status_code == 429:
                log("  [ROIC.ai] Rate limited — stopping ROIC.ai for this run")
                break
            if tr_resp.status_code != 200:
                log(f"  [ROIC.ai] Q{q} {yr} HTTP {tr_resp.status_code}")
                continue

            data = _safe_json(tr_resp)
            if data is None:
                log(f"  [ROIC.ai] Q{q} {yr} returned non-JSON response — skipped")
                continue

            body, has_speakers = self._parse_roic_payload(data)
            if len(body) > 200:
                records.append(
                    TranscriptRecord(
                        ticker=ticker,
                        fiscal_year=yr,
                        fiscal_quarter=q,
                        report_date=call_date,
                        provider="roic.ai",
                        markdown_body=body,
                        has_speakers=has_speakers,
                    )
                )
                log(f"  [ROIC.ai] Q{q} {yr} fetched ({len(body):,} chars)")

        note = f"Fetched {len(records)} quarter(s)"
        if restricted_hits:
            note += f" ({restricted_hits} older call(s) skipped by free 2-call limit)"
        return records, note

    @staticmethod
    def _parse_roic_payload(data: Any) -> Tuple[str, bool]:
        if isinstance(data, dict):
            turns = data.get("transcript")
            if isinstance(turns, list) and turns:
                parts: List[str] = []
                has_speakers = False
                for turn in turns:
                    if not isinstance(turn, dict):
                        continue
                    speaker = str(turn.get("speaker") or turn.get("name") or "").strip()
                    text = str(turn.get("content") or turn.get("text") or "").strip()
                    if not text:
                        continue
                    if speaker:
                        has_speakers = True
                        parts.append(f"**{speaker}:** {text}")
                    else:
                        parts.append(text)
                if parts:
                    return "\n\n".join(parts), has_speakers

            for field_name in ("text", "content", "transcript"):
                val = data.get(field_name)
                if isinstance(val, str) and len(val.strip()) > 200:
                    return val.strip(), False

        return "", False


# ============================================================================
# PROVIDER 3: Alpha Vantage (25 req/day Free Tier, Full History Since 2010Q1)
# ============================================================================

def fetch_from_alphavantage(
    ticker: str,
    target_quarters: Sequence[Tuple[int, int]],
    api_key: str,
    quota: QuotaManager,
    log: Callable[[str], None],
    session: Optional[requests.Session] = None,
    max_calls_this_run: Optional[int] = None,
) -> Tuple[List[TranscriptRecord], str]:
    """Fetch missing quarters from Alpha Vantage `EARNINGS_CALL_TRANSCRIPT`."""
    http = session or requests.Session()
    min_interval = float(PROVIDER_SPECS["alphavantage"].get("min_interval_sec", 1.2))
    daily_limit = quota.alphavantage_daily_limit

    records: List[TranscriptRecord] = []
    calls_made = 0
    consecutive_empty = 0

    for yr, q in target_quarters:
        if not quota.can_call_alphavantage():
            log(f"  [Alpha Vantage] Daily free quota ({daily_limit}/day) reached — stopping.")
            break
        if max_calls_this_run is not None and calls_made >= max_calls_this_run:
            log(f"  [Alpha Vantage] Per-run cap ({max_calls_this_run}) reached.")
            break

        if calls_made > 0:
            time.sleep(min_interval)

        quarter_param = f"{yr}Q{q}"
        try:
            resp = http.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "EARNINGS_CALL_TRANSCRIPT",
                    "symbol": ticker,
                    "quarter": quarter_param,
                    "apikey": api_key,
                },
                timeout=30,
            )
            quota.record_alphavantage_call(1)
            calls_made += 1
        except Exception as exc:
            log(f"  [Alpha Vantage] {quarter_param} network error: {exc}")
            continue

        if resp.status_code != 200:
            log(f"  [Alpha Vantage] {quarter_param} HTTP {resp.status_code}")
            continue

        data = _safe_json(resp)
        if not isinstance(data, dict):
            log(f"  [Alpha Vantage] {quarter_param} returned non-JSON response — skipped")
            continue

        info_msg = str(data.get("Information") or data.get("Note") or "")
        if info_msg:
            lower = info_msg.lower()
            if "rate limit" in lower or "requests per day" in lower or "spreading out" in lower:
                quota.mark_alphavantage_exhausted()
                log(f"  [Alpha Vantage] Rate limit reached: {info_msg[:120]}")
                break
            log(f"  [Alpha Vantage] {quarter_param}: {info_msg[:120]}")
            continue

        if "Error Message" in data:
            log(f"  [Alpha Vantage] {quarter_param}: {data['Error Message']}")
            break

        turns = data.get("transcript", [])
        if not isinstance(turns, list) or not turns:
            consecutive_empty += 1
            log(f"  [Alpha Vantage] {quarter_param}: no transcript available")
            if consecutive_empty >= 3 and len(records) == 0:
                log("  [Alpha Vantage] 3 consecutive empty quarters — stopping to save daily quota.")
                break
            continue

        consecutive_empty = 0
        parts: List[str] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            speaker = str(turn.get("speaker") or "").strip()
            title = str(turn.get("title") or "").strip()
            content = str(turn.get("content") or "").strip()
            sentiment = turn.get("sentiment")
            if not content:
                continue

            header = speaker
            if title and title.lower() not in ("n/a", "none"):
                header = f"{speaker} ({title})" if speaker else title
            if sentiment not in (None, "", "None"):
                header = f"{header} [sentiment: {sentiment}]" if header else f"[sentiment: {sentiment}]"

            parts.append(f"**{header}:** {content}" if header else content)

        body = "\n\n".join(parts)
        if len(body) > 200:
            records.append(
                TranscriptRecord(
                    ticker=ticker,
                    fiscal_year=yr,
                    fiscal_quarter=q,
                    provider="alphavantage",
                    markdown_body=body,
                    has_speakers=True,
                )
            )
            log(f"  [Alpha Vantage] {quarter_param} fetched ({len(body):,} chars)")

    remaining = quota.alphavantage_remaining()
    return (
        records,
        f"Fetched {len(records)} quarter(s) ({calls_made} calls used, {remaining}/{daily_limit} left today)",
    )


# ============================================================================
# PROVIDER 4: Firecrawl v2 (1,000 credits/month — Last-Resort Fallback)
# ============================================================================

_PREFERRED_TRANSCRIPT_DOMAINS = (
    "fool.com",
    "webull.com",
    "insidermonkey.com",
    "investing.com",
    "finance.yahoo.com",
    "seekingalpha.com",
    "sec.gov",
)


def fetch_from_firecrawl(
    ticker: str,
    target_quarters: Sequence[Tuple[int, int]],
    api_key: str,
    quota: QuotaManager,
    log: Callable[[str], None],
    session: Optional[requests.Session] = None,
    max_quarters: int = 2,
) -> Tuple[List[TranscriptRecord], str]:
    """Last-resort Firecrawl v2 search + scrape fallback for missing quarters."""
    http = session or requests.Session()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    records: List[TranscriptRecord] = []
    credits_spent = 0

    for yr, q in list(target_quarters)[:max_quarters]:
        if not quota.can_call_firecrawl(cost=2):
            log("  [Firecrawl] Monthly credit limit nearly reached — stopping.")
            break

        query = f'"{ticker}" "Q{q} {yr}" earnings call transcript'
        try:
            search_resp = http.post(
                "https://api.firecrawl.dev/v2/search",
                headers=headers,
                json={"query": query, "sources": ["web"], "limit": 5},
                timeout=30,
            )
            quota.record_firecrawl_credits(1)
            credits_spent += 1
        except Exception as exc:
            log(f"  [Firecrawl] Q{q} {yr} search error: {exc}")
            continue

        if search_resp.status_code != 200:
            log(f"  [Firecrawl] Q{q} {yr} search HTTP {search_resp.status_code}")
            if search_resp.status_code in (401, 402):
                break
            continue

        search_json = _safe_json(search_resp)
        if not isinstance(search_json, dict):
            log(f"  [Firecrawl] Q{q} {yr} search returned non-JSON response — skipped")
            continue

        raw_results = search_json.get("data", [])
        if isinstance(raw_results, dict):
            raw_results = raw_results.get("web", [])
        if not isinstance(raw_results, list) or not raw_results:
            log(f"  [Firecrawl] Q{q} {yr}: no web results found")
            continue

        def _rank(item: Dict[str, Any]) -> Tuple[int, int]:
            url = str(item.get("url") or "").lower()
            title = str(item.get("title") or "").lower()
            domain_score = 0
            for idx, dom in enumerate(_PREFERRED_TRANSCRIPT_DOMAINS):
                if dom in url:
                    domain_score = len(_PREFERRED_TRANSCRIPT_DOMAINS) - idx
                    break
            title_bonus = 1 if ("transcript" in title and ticker.lower() in title) else 0
            return (domain_score, title_bonus)

        ranked = sorted(
            [r for r in raw_results if isinstance(r, dict) and r.get("url")],
            key=_rank,
            reverse=True,
        )
        if not ranked:
            continue

        best_url = str(ranked[0]["url"])
        try:
            scrape_resp = http.post(
                "https://api.firecrawl.dev/v2/scrape",
                headers=headers,
                json={"url": best_url, "formats": ["markdown"], "onlyMainContent": True},
                timeout=45,
            )
            quota.record_firecrawl_credits(1)
            credits_spent += 1
        except Exception as exc:
            log(f"  [Firecrawl] Q{q} {yr} scrape error: {exc}")
            continue

        if scrape_resp.status_code != 200:
            log(f"  [Firecrawl] Q{q} {yr} scrape HTTP {scrape_resp.status_code}")
            continue

        scrape_json = _safe_json(scrape_resp)
        scrape_data = scrape_json.get("data", {}) if isinstance(scrape_json, dict) else {}
        if not isinstance(scrape_data, dict):
            log(f"  [Firecrawl] Q{q} {yr} scrape returned unexpected payload — skipped")
            continue

        md_text = str(scrape_data.get("markdown") or "").strip()
        if len(md_text) >= 1500:
            records.append(
                TranscriptRecord(
                    ticker=ticker,
                    fiscal_year=yr,
                    fiscal_quarter=q,
                    provider="firecrawl",
                    source_url=best_url,
                    markdown_body=md_text,
                    has_speakers=False,
                )
            )
            log(f"  [Firecrawl] Q{q} {yr} fetched ({len(md_text):,} chars)")
        else:
            log(f"  [Firecrawl] Q{q} {yr} page too short ({len(md_text)} chars) — skipped")

    return records, f"Fetched {len(records)} quarter(s) ({credits_spent} credit(s) used)"


# ============================================================================
# ORCHESTRATOR: Smart Redundancy Waterfall (Respects Exact Provider Order)
# ============================================================================

def download_transcripts(
    ticker: str,
    num_years: int = 2,
    providers: Optional[Sequence[str]] = None,
    api_keys: Optional[Dict[str, str]] = None,
    quota_manager: Optional[QuotaManager] = None,
    data_root: Path = DEFAULT_DATA_ROOT,
    force_refresh: bool = False,
    on_log: Optional[Callable[[str], None]] = None,
) -> TranscriptRunResult:
    """Download earnings call transcripts using the quota-preserving waterfall."""
    clean_ticker = sanitize_ticker(ticker)
    transcripts_dir = data_root / clean_ticker / "EarningsTranscripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    keys = api_keys if api_keys is not None else load_api_keys()
    quota = quota_manager or QuotaManager()
    enabled = (
        list(dict.fromkeys(p.strip().lower() for p in providers if p.strip()))
        if providers is not None
        else list(DEFAULT_PROVIDER_ORDER)
    )

    result = TranscriptRunResult(ticker=clean_ticker, output_dir=transcripts_dir)

    def _log(msg: str) -> None:
        result.logs.append(msg)
        if on_log:
            on_log(msg)

    targets = expected_quarters(num_years)
    cutoff_year = min(yr for yr, _ in targets)

    # ── Step 0: Local Cache Check (0 API cost) ─────────────────────────
    existing = {} if force_refresh else scan_existing_quarters(transcripts_dir)
    acquired: Set[Tuple[int, int]] = set()
    for q_key in targets:
        if q_key in existing:
            acquired.add(q_key)
            result.cached_quarters.append(q_key)

    if result.cached_quarters:
        _log(
            f"Local cache hit: {len(result.cached_quarters)} quarter(s) already on disk (0 API calls)."
        )

    unavailable: Set[Tuple[int, int]] = set()
    latest_target = targets[0] if targets else None

    def _missing() -> List[Tuple[int, int]]:
        return [q_key for q_key in targets if q_key not in acquired and q_key not in unavailable]

    def _save_records(recs: List[TranscriptRecord]) -> None:
        for rec in recs:
            key = (rec.fiscal_year, rec.fiscal_quarter)
            if key in acquired and not force_refresh:
                continue
            out_path = save_transcript_record(rec, transcripts_dir)
            acquired.add(key)
            unavailable.discard(key)
            result.saved_files.append((rec.provider, out_path))

    # ── Run enabled providers in the exact order requested ─────────────
    for prov in enabled:
        if not _missing():
            break

        if prov == "defeatbeta":
            _log("Checking DefeatBeta (Hugging Face open dataset — unlimited)...")
            catalog: Set[Tuple[int, int]] = set()
            try:
                recs, note = fetch_from_defeatbeta(
                    clean_ticker, cutoff_year, acquired, _log, catalog_out=catalog
                )
            except TypeError:
                recs, note = fetch_from_defeatbeta(clean_ticker, cutoff_year, acquired, _log)
            _save_records(recs)

            if catalog:
                earliest = min(catalog)
                pruned = []
                for q_key in list(_missing()):
                    if q_key < earliest or (q_key != latest_target and q_key not in catalog):
                        unavailable.add(q_key)
                        pruned.append(q_key)
                if pruned:
                    _log(
                        f"  [Catalog Oracle] Marked {len(pruned)} historical quarter(s) as "
                        "unavailable upstream (saves Alpha Vantage / Firecrawl quota)."
                    )

            result.provider_notes["defeatbeta"] = note

        elif prov == "roic":
            roic_key = keys.get("roic", "").strip()
            if roic_key:
                _log(f"Checking ROIC.ai API v3 (latest calls, {quota.roic_rpm_limit} req/min)...")
                client = RoicClient(roic_key, quota)
                recs, note = client.fetch_missing(clean_ticker, cutoff_year, acquired, _log)
                _save_records(recs)
                result.provider_notes["roic"] = note
            else:
                result.provider_notes["roic"] = "Skipped (no API key configured)"

        elif prov == "alphavantage":
            av_key = keys.get("alphavantage", "").strip()
            if av_key:
                missing_now = _missing()
                _log(
                    f"Checking Alpha Vantage for {len(missing_now)} remaining quarter(s) "
                    f"({quota.alphavantage_remaining()}/{quota.alphavantage_daily_limit} daily calls left)..."
                )
                recs, note = fetch_from_alphavantage(clean_ticker, missing_now, av_key, quota, _log)
                _save_records(recs)
                result.provider_notes["alphavantage"] = note
            else:
                result.provider_notes["alphavantage"] = "Skipped (no API key configured)"

        elif prov == "firecrawl":
            fc_key = keys.get("firecrawl", "").strip()
            if fc_key:
                missing_now = _missing()
                _log(
                    f"Trying Firecrawl fallback for up to 2 of {len(missing_now)} missing quarter(s) "
                    f"({quota.firecrawl_remaining():,} credits left this month)..."
                )
                recs, note = fetch_from_firecrawl(clean_ticker, missing_now, fc_key, quota, _log)
                _save_records(recs)
                result.provider_notes["firecrawl"] = note
            else:
                result.provider_notes["firecrawl"] = "Skipped (no API key configured)"

        else:
            result.provider_notes[prov] = f"Unknown provider '{prov}' — skipped"

    result.unavailable_upstream = sorted(unavailable, reverse=True)
    result.missing_quarters = _missing()
    return result
