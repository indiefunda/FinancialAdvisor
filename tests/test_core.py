"""Unit tests for FinancialAdvisor core modules (SEC, Transcripts, Fundamentals, DeepSeek AI)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_analyst import (
    SYSTEM_PROMPT_VALUATION_CHAT,
    ValuationParameters,
    build_dossier_context,
    call_deepseek,
    call_deepseek_full,
    discover_peer_tickers,
    estimate_cost_usd,
    extract_valuation_summary,
    list_dossier_files,
    load_valuation_protocol,
    run_workflow_analysis,
    save_valuation_protocol,
    strip_valuation_summary_tag,
)
from config_manager import (
    QuotaManager,
    load_api_keys,
    load_stored_api_keys,
    mask_key,
    parse_secrets_txt,
    save_api_keys,
)
from fundamentals import (
    fetch_spot_fx_rate,
    generate_company_brief,
    generate_peers_report,
    generate_sec_coverage_audit,
    generate_valuation_bundle,
    generate_valuation_pack,
)
from markdown_cleanup import cleanup_markdown_tables
from sec_downloader import (
    _TICKER_CIK_CACHE,
    deduplicate_filename,
    download_and_convert_sec_filings,
    html_to_markdown,
    is_useful_document,
    is_within_filing_window,
    make_sec_filename,
    resolve_ticker_to_cik,
    sanitize_ticker,
)
from transcript_providers import (
    RoicClient,
    TranscriptRecord,
    download_transcripts,
    expected_quarters,
    fetch_from_alphavantage,
    fetch_from_firecrawl,
    save_transcript_record,
    scan_existing_quarters,
)
from valuation_math import (
    VALUATION_TOOLS,
    compute_four_state_summary,
    compute_irr_and_returns,
    compute_operating_valuation_model,
    compute_pb_grid,
    execute_valuation_tool,
    solve_irr,
)


# ── Minimal pandas-compatible DataFrame double (no heavy dep needed in unit tests) ──
class _FakeSeries:
    def __init__(self, values):
        self._values = list(values)

    def dropna(self):
        return _FakeSeries([v for v in self._values if v is not None])

    def tolist(self):
        return list(self._values)


class _FakeIloc:
    def __init__(self, rows):
        self._rows = rows

    def __getitem__(self, idx):
        return self._rows[idx]


class _FakeDF:
    def __init__(self, rows):
        self._rows = list(rows)
        self.columns = list(rows[0].keys()) if rows else []

    @property
    def empty(self):
        return len(self._rows) == 0

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, col):
        return _FakeSeries([r.get(col) for r in self._rows])

    @property
    def iloc(self):
        return _FakeIloc(self._rows)

    def dropna(self, subset=None):
        if not subset:
            return self
        kept = [r for r in self._rows if all(r.get(c) is not None for c in subset)]
        return _FakeDF(kept)

    def sort_values(self, col):
        return _FakeDF(sorted(self._rows, key=lambda r: str(r.get(col, ""))))

    def tail(self, n):
        return _FakeDF(self._rows[-n:])

    def iterrows(self):
        for i, r in enumerate(self._rows):
            yield i, r


# ============================================================================
# 1. markdown_cleanup.py
# ============================================================================

def test_cleanup_removes_left_dollar_spacer_column():
    raw = "\n".join([
        "| Metric | | 2025 |",
        "| --- | --- | --- |",
        "| Revenue | $ | 1,234 |",
        "| Net Income | $ | 567 |",
    ])
    cleaned = cleanup_markdown_tables(raw)
    assert "| Revenue | 1,234 |" in cleaned
    assert "| Net Income | 567 |" in cleaned
    assert "| $ |" not in cleaned


def test_cleanup_never_deletes_data_column_when_right_column_is_closing_paren():
    raw = "\n".join([
        "| Metric | 2025 | |",
        "| --- | --- | --- |",
        "| Loss | (1,234 | ) |",
        "| CapEx | (567 | ) |",
    ])
    cleaned = cleanup_markdown_tables(raw)
    assert "(1,234" in cleaned
    assert "(567" in cleaned


def test_cleanup_removes_duplicate_and_triple_duplicate_columns():
    raw = "\n".join([
        "| Metric | 2025 | 2025 | 2025 |",
        "| --- | --- | --- | --- |",
        "| Revenue | $1,234 | 1,234 | 1,234 |",
        "| EBITDA | $567 | 567 | 567 |",
    ])
    cleaned = cleanup_markdown_tables(raw)
    lines = cleaned.splitlines()
    assert lines[0] == "| Metric | 2025 |"
    assert lines[2] == "| Revenue | 1,234 |"
    assert lines[3] == "| EBITDA | 567 |"


def test_cleanup_preserves_sparse_notes_column():
    raw = "\n".join([
        "| Metric | 2025 | Notes |",
        "| --- | --- | --- |",
        "| Revenue | 1,234 | Restated |",
        "| COGS | 500 | |",
        "| Gross Profit | 734 | |",
        "| OpEx | 200 | |",
        "| Net Income | 534 | |",
    ])
    cleaned = cleanup_markdown_tables(raw)
    assert "Restated" in cleaned
    assert "Notes" in cleaned


# ============================================================================
# 2. sec_downloader.py
# ============================================================================

def test_sanitize_ticker():
    assert sanitize_ticker("  aapl  ") == "AAPL"
    assert sanitize_ticker("brk.b") == "BRK.B"
    with pytest.raises(ValueError):
        sanitize_ticker("")
    with pytest.raises(ValueError):
        sanitize_ticker("../etc/passwd")


def test_is_useful_document_includes_all_ui_and_custom_forms():
    assert is_useful_document("10-K")
    assert is_useful_document("424B4")
    assert is_useful_document("S-3")
    assert is_useful_document("DEF 14A")
    assert is_useful_document("EX-99.1")
    assert is_useful_document("SC 13D", allowed_cover_forms={"SC 13D"})
    assert not is_useful_document("EX-31.1")
    assert not is_useful_document("XML")


def test_make_sec_filename_no_exhibit_collision_and_non_calendar_10q():
    f1 = make_sec_filename("AAPL", "8-K", "EX-99.1", "", "20260501")
    f2 = make_sec_filename("AAPL", "8-K", "EX-99.2", "", "20260501")
    assert f1 != f2
    assert "EX-99.1" in f1
    assert "EX-99.2" in f2

    q_file = make_sec_filename("AAPL", "10-Q", "10-Q", "20260328", "20260501")
    assert q_file == "AAPL - 10-Q Period 2026-03-28.md"

    used = set()
    assert deduplicate_filename("AAPL - 8-K 2026-05-01.md", used) == "AAPL - 8-K 2026-05-01.md"
    assert deduplicate_filename("AAPL - 8-K 2026-05-01.md", used) == "AAPL - 8-K 2026-05-01 (2).md"


def test_filing_window_uses_filing_date_not_fiscal_period():
    """Regression: a 20-F for FY2025 filed 2026-04-30 must survive a 1-year window.

    The old filter compared the *period* year (2025) against the cutoff (2026) and
    silently dropped the most recent annual report from every window.
    """
    # Annual report: period 2025-12-31, filed 2026-04-30, 1-year window (cutoff 2026)
    assert is_within_filing_window("20260430", "20251231", 2026) is True
    # ...and it still belongs to a 2-year window
    assert is_within_filing_window("20260430", "20251231", 2025) is True
    # Prior-year annual report is genuinely outside a 1-year window
    assert is_within_filing_window("20250430", "20241231", 2026) is False
    # Quarterly/current filings keep working on filing date
    assert is_within_filing_window("20260910", "20260630", 2026) is True
    # Falls back to the period when the filing date is missing/blank
    assert is_within_filing_window("", "20251231", 2025) is True
    assert is_within_filing_window("", "20241231", 2025) is False
    # Nothing parseable -> excluded rather than crashing
    assert is_within_filing_window("", "", 2025) is False


def test_missing_datamule_raises_clean_runtime_error(tmp_path: Path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "datamule":
            raise ImportError("No module named 'datamule'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(RuntimeError, match="Missing required package 'datamule'"):
            download_and_convert_sec_filings("AAPL", ["10-K"], data_root=tmp_path)


# ============================================================================
# 3. config_manager.py (including DeepSeek)
# ============================================================================

def test_secrets_partial_merge_deepseek_and_env_isolation(tmp_path: Path, monkeypatch):
    txt = tmp_path / "Secrets.txt"
    txt.write_text(
        "roic.ai API KEY: test_roic_123456\n"
        "alphavantage.co API KEY: test_av_abcdef\n"
        "firecrawl.dev API KEY: fc-test9999\n"
        "deepseek API KEY: sk-deepseek-test001\n",
        encoding="utf-8",
    )
    secrets_json = tmp_path / ".secrets.json"
    save_api_keys({"roic": "custom_roic_9999"}, secrets_file=secrets_json)

    for var in ("ROIC_API_KEY", "ALPHAVANTAGE_API_KEY", "FIRECRAWL_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    stored = load_stored_api_keys(secrets_file=secrets_json, auto_import_paths=[txt])
    assert stored["roic"] == "custom_roic_9999"
    assert stored["alphavantage"] == "test_av_abcdef"
    assert stored["firecrawl"] == "fc-test9999"
    assert stored["deepseek"] == "sk-deepseek-test001"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-only")
    effective = load_api_keys(secrets_file=secrets_json)
    assert effective["deepseek"] == "sk-env-only"
    assert load_stored_api_keys(secrets_file=secrets_json)["deepseek"] == "sk-deepseek-test001"

    assert mask_key("sk-deepseek-test001") == "sk••••••t001"
    assert mask_key("") == "Not set"


def test_quota_manager_limits_and_rollover(tmp_path: Path):
    qfile = tmp_path / ".quota_state.json"
    qm = QuotaManager(quota_file=qfile)
    assert qm.alphavantage_remaining() == 25
    qm.record_alphavantage_call(5)
    assert qm.alphavantage_remaining() == 20

    qm.state["alphavantage"]["date"] = "2020-01-01"
    qm.save()
    qm2 = QuotaManager(quota_file=qfile)
    assert qm2.alphavantage_remaining() == 25

    qm2.record_firecrawl_credits(10)
    assert qm2.firecrawl_remaining() == 990


# ============================================================================
# 4. transcript_providers.py (incl. Catalog Oracle)
# ============================================================================

def test_expected_quarters_excludes_in_progress_quarter():
    qs = expected_quarters(2, today=date(2026, 11, 24))
    assert qs[0] == (2026, 3)
    assert (2026, 4) not in qs
    assert qs[-1] == (2025, 1)
    assert len(qs) == 7

    qs_jan = expected_quarters(1, today=date(2026, 2, 10))
    assert qs_jan[0] == (2025, 4)
    assert qs_jan[-1] == (2025, 1)


def test_scan_existing_quarters_ignores_zero_byte_stubs(tmp_path: Path):
    (tmp_path / "AAPL - Q2 2026.md").write_text("", encoding="utf-8")
    save_transcript_record(
        TranscriptRecord("AAPL", 2026, 3, "defeatbeta", "A" * 300),
        tmp_path,
    )
    existing = scan_existing_quarters(tmp_path)
    assert (2026, 3) in existing
    assert (2026, 2) not in existing


def test_non_json_html_responses_never_crash_providers(tmp_path: Path):
    qm = QuotaManager(quota_file=tmp_path / ".quota.json")
    html_resp = MagicMock(status_code=200)
    html_resp.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")

    mock_session = MagicMock()
    mock_session.get.return_value = html_resp
    mock_session.post.return_value = html_resp

    roic = RoicClient("dummy", qm, session=mock_session)
    recs, note = roic.fetch_missing("AAPL", 2025, set(), lambda _: None)
    assert recs == []
    assert "non-JSON" in note

    recs_av, _ = fetch_from_alphavantage(
        "AAPL", [(2026, 3)], "dummy", qm, lambda _: None, session=mock_session
    )
    assert recs_av == []

    recs_fc, _ = fetch_from_firecrawl(
        "AAPL", [(2026, 3)], "dummy", qm, lambda _: None, session=mock_session
    )
    assert recs_fc == []


def test_alphavantage_handles_http200_rate_limit(tmp_path: Path):
    qm = QuotaManager(quota_file=tmp_path / ".quota.json")
    mock_session = MagicMock()
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "Information": "We have detected your API key and our standard API rate limit is 25 requests per day."
    }
    mock_session.get.return_value = mock_resp

    recs, _ = fetch_from_alphavantage(
        "AAPL",
        [(2026, 3), (2026, 2)],
        api_key="dummy",
        quota=qm,
        log=lambda _: None,
        session=mock_session,
    )
    assert recs == []
    assert qm.alphavantage_remaining() == 0
    assert mock_session.get.call_count == 1


def test_catalog_oracle_prunes_pre_ipo_quarters_before_calling_alphavantage(tmp_path: Path):
    """If DefeatBeta catalog starts at Q1 2026 (e.g. recent IPO), 2025 quarters are marked
    unavailable_upstream so Alpha Vantage never burns quota on them."""
    qm = QuotaManager(quota_file=tmp_path / ".quota.json")
    av_queried = []

    def fake_db(ticker, cutoff_year, skip_quarters, log, catalog_out=None):
        if catalog_out is not None:
            catalog_out.update({(2026, 1), (2026, 2)})
        return [
            TranscriptRecord(ticker, 2026, 1, "defeatbeta", "Q1 body " * 40),
            TranscriptRecord(ticker, 2026, 2, "defeatbeta", "Q2 body " * 40),
        ], "Fetched 2"

    def fake_av(ticker, target_quarters, api_key, quota, log, **kwargs):
        av_queried.extend(target_quarters)
        return [], "Fetched 0"

    with patch("transcript_providers.fetch_from_defeatbeta", side_effect=fake_db), \
         patch("transcript_providers.fetch_from_alphavantage", side_effect=fake_av):
        res = download_transcripts(
            ticker="YB",
            num_years=2,
            providers=["defeatbeta", "alphavantage"],
            api_keys={"alphavantage": "k2"},
            quota_manager=qm,
            data_root=tmp_path,
        )

    # All 2025 quarters should be pruned into unavailable_upstream; AV should only check latest target
    assert all(yr == 2025 for yr, _ in res.unavailable_upstream)
    assert len(res.unavailable_upstream) == 4
    assert all(yr == 2026 for yr, _ in av_queried)


def test_roic_payload_parsing():
    text, has_sp = RoicClient._parse_roic_payload({
        "transcript": [
            {"speaker": "CEO", "content": "Strong revenue growth this quarter across all regions. " * 10},
            {"speaker": "Analyst", "content": "Can you share guidance for next fiscal year? " * 10},
        ]
    })
    assert has_sp is True
    assert "**CEO:**" in text
    assert "**Analyst:**" in text

    raw_text, has_sp2 = RoicClient._parse_roic_payload({"text": "Hello everyone. " * 30})
    assert has_sp2 is False
    assert "Hello everyone." in raw_text


def test_waterfall_respects_custom_provider_order(tmp_path: Path):
    qm = QuotaManager(quota_file=tmp_path / ".quota.json")
    call_order = []

    def fake_roic(self, ticker, cutoff_year, skip_quarters, log):
        call_order.append("roic")
        return [TranscriptRecord(ticker, 2026, 3, "roic.ai", "ROIC body " * 40)], "Fetched 1"

    def fake_db(ticker, cutoff_year, skip_quarters, log, catalog_out=None):
        call_order.append("defeatbeta")
        assert (2026, 3) in skip_quarters
        return [
            TranscriptRecord(ticker, 2026, 2, "defeatbeta", "DB body " * 40),
            TranscriptRecord(ticker, 2026, 1, "defeatbeta", "DB body " * 40),
        ], "Fetched 2"

    with patch.object(RoicClient, "fetch_missing", fake_roic), \
         patch("transcript_providers.fetch_from_defeatbeta", side_effect=fake_db):
        res = download_transcripts(
            ticker="AAPL",
            num_years=1,
            providers=["roic", "defeatbeta"],
            api_keys={"roic": "k1"},
            quota_manager=qm,
            data_root=tmp_path,
        )

    assert call_order == ["roic", "defeatbeta"]
    assert res.missing_quarters == []


# ============================================================================
# 5. fundamentals.py (Bugs 1-4: Currency, Coverage, Invalid DCF, Segments)
# ============================================================================

def _make_mock_ticker(sector="Financial Services", currency="CNY", base_fcf=-15_000_000.0):
    t = MagicMock()
    t.ticker = "HUIZ"
    t.company_meta.get_company_info.return_value = {"financial_currency": currency}
    t.info.return_value = _FakeDF([{
        "sector": sector,
        "industry": "Credit Services",
        "country": "China",
        "city": "Shenzhen",
        "web_site": "https://example.com",
        "full_time_employees": 1200,
        "long_business_summary": "Online insurance and fintech broker.",
    }])
    t.calendar.return_value = _FakeDF([{"report_date": "2026-12-15"}])
    t.ttm_pe.return_value = _FakeDF([{"price_report_date": "2026-11-20", "ttm_pe": 12.5}])
    t.ps_ratio.return_value = _FakeDF([{"report_date": "2026-11-20", "ps_ratio": 1.8}])
    t.pb_ratio.return_value = _FakeDF([{"report_date": "2026-11-20", "pb_ratio": 1.1}])
    t.peg_ratio.return_value = _FakeDF([])
    t.market_capitalization.return_value = _FakeDF([{"report_date": "2026-11-20", "market_capitalization": 350_000_000}])
    t.enterprise_value.return_value = _FakeDF([{"report_date": "2026-11-20", "enterprise_value": 280_000_000}])
    t.net_debt_ttm.return_value = _FakeDF([{"report_date": "2026-09-30", "net_debt": -70_000_000}])
    t.debt_to_equity.return_value = _FakeDF([{"report_date": "2026-09-30", "debt_to_equity": 0.35}])
    t.wacc.return_value = _FakeDF([{"report_date": "2026-09-30", "wacc": 0.112}])
    t.ttm_eps.return_value = _FakeDF([{"report_date": "2026-09-30", "tailing_eps": 0.45}])
    # Real DefeatBeta schema: local currency column + _usd column (Bug 1 regression!)
    t.ttm_revenue.return_value = _FakeDF([{
        "report_date": "2026-09-30",
        "ttm_total_revenue": 1_336_186_000,
        "ttm_total_revenue_usd": 186_303_314,
    }])
    t.ttm_fcf.return_value = _FakeDF([{
        "report_date": "2026-09-30",
        "ttm_free_cash_flow": base_fcf,
        "ttm_free_cash_flow_usd": base_fcf / 7.17,
    }])
    t.roic.return_value = _FakeDF([])
    t.roe.return_value = _FakeDF([{"report_date": "2026-09-30", "roe": 0.14}])
    t.roa.return_value = _FakeDF([{"report_date": "2026-09-30", "roa": 0.06}])
    t.quarterly_gross_margin.return_value = _FakeDF([{"report_date": "2026-09-30", "gross_margin": 0.38}])
    t.quarterly_operating_margin.return_value = _FakeDF([{"report_date": "2026-09-30", "operating_margin": 0.12}])
    t.quarterly_net_margin.return_value = _FakeDF([{"report_date": "2026-09-30", "net_margin": 0.09}])
    t.quarterly_fcf_margin.return_value = _FakeDF([])
    t.quarterly_revenue_yoy_growth.return_value = _FakeDF([{"report_date": "2026-09-30", "yoy_growth": 0.21}])
    # Real DefeatBeta breakdown schema with value + currency + multiple dates + mislabeled margin row
    t.trailing_revenue_by_breakdown.return_value = _FakeDF([
        {"symbol": "HUIZ", "breakdown": "revenue-by-segment", "breakdown_name": "Revenue by Segment", "period_type": "trailing", "report_date": "2025-09-30", "series_name": "Old Row", "value": 500_000_000, "currency": "CNY", "value_type": "CURRENCY"},
        {"symbol": "HUIZ", "breakdown": "revenue-by-segment", "breakdown_name": "Revenue by Segment", "period_type": "trailing", "report_date": "2026-09-30", "series_name": "Life Insurance", "value": 900_000_000, "currency": "CNY", "value_type": "CURRENCY"},
        {"symbol": "HUIZ", "breakdown": "revenue-by-segment", "breakdown_name": "Revenue by Segment", "period_type": "trailing", "report_date": "2026-09-30", "series_name": "Health Insurance", "value": 436_186_000, "currency": "CNY", "value_type": "CURRENCY"},
        {"symbol": "HUIZ", "breakdown": "gross-margin-by-type", "breakdown_name": "Gross Margin by Type", "period_type": "trailing", "report_date": "2026-09-30", "series_name": "Products", "value": 39.0, "currency": "USD", "value_type": "CURRENCY"},
    ])

    t.industry_ttm_pe.return_value = _FakeDF([{"report_date": "2026-11-20", "industry_pe": 15.0}])
    t.industry_ps_ratio.return_value = _FakeDF([{"report_date": "2026-11-20", "industry_ps": 2.4}])
    t.industry_pb_ratio.return_value = _FakeDF([])
    t.industry_roe.return_value = _FakeDF([{"report_date": "2026-09-30", "industry_roe": 0.12}])
    t.industry_roic.return_value = _FakeDF([])

    t.dcf_data.return_value = {
        "symbol": "HUIZ",
        "discount_rate": {"wacc": 0.112, "beta_5y": 1.25, "risk_free_rate": 0.042, "cost_of_equity": 0.12, "cost_of_debt": 0.05, "tax_rate": 0.2},
        "growth_estimates": {"currency": currency},
        "dcf_template": {
            "discount_rate": 0.112,
            "base_fcf": base_fcf,
            "ttm_period": "2026-09-30",
            "historical_fcf_margin": 0.08,
            "growth_rate_1_5y": 0.15,
            "growth_rate_6_10y": 0.08,
            "revenue_growth_1_5y": 0.18,
            "revenue_growth_6_10y": 0.10,
            "growth_rate_terminal": 0.035,
        },
        "dcf_value": {
            "current_price": 4.50,
            "fair_price": -4.04 if base_fcf <= 0 else 6.20,
            "margin_of_safety": 1.5257 if base_fcf <= 0 else 0.274,
            "recommendation": "Sell" if base_fcf <= 0 else "UNDERVALUED",
            "enterprise_value": 0.0 if base_fcf <= 0 else 280_000_000,
            "cash": 100_000_000,
            "total_debt": 30_000_000,
            "equity_value": -20_000_000 if base_fcf <= 0 else 350_000_000,
            "shares_outstanding": 56_000_000,
        },
    }
    t.sec_filing.return_value = _FakeDF([
        {"filing_date": "2026-04-24", "form_type": "20-F", "accession_number": "0001193125-26-000111", "form_type_description": "FY2025 Annual report", "filing_url": "https://sec.gov/1"},
        {"filing_date": "2025-04-23", "form_type": "20-F", "accession_number": "0001193125-25-000222", "form_type_description": "FY2024 Annual report", "filing_url": "https://sec.gov/2"},
        {"filing_date": "2026-05-15", "form_type": "SC 13D", "accession_number": "0001193125-26-000333", "form_type_description": "Active stake", "filing_url": "https://sec.gov/3"},
    ])
    return t


def test_fundamentals_currency_segments_invalid_dcf_and_accession_coverage(tmp_path: Path):
    mock_t = _make_mock_ticker(sector="Financial Services", currency="CNY", base_fcf=-15_000_000.0)

    # Bug 1: TTM Revenue (CNY) MUST read ttm_total_revenue (CNY 1.34B), NOT ttm_total_revenue_usd (186.30M)!
    brief = generate_company_brief("HUIZ", data_root=tmp_path, ticker_obj=mock_t)
    assert brief.currency == "CNY"
    assert brief.metrics["TTM Revenue (CNY)"] == "CNY 1.34B"
    assert brief.metrics["TTM Revenue (USD equiv.)"] == "USD 186.30M"
    assert brief.metrics["ROIC"] == "n/a"

    # Bug 4 & margin-unit fix: Segment table MUST include values + units, only latest date, and % for margins!
    brief_text = brief.path.read_text(encoding="utf-8")
    assert "Life Insurance | CNY 900.00M" in brief_text
    assert "Health Insurance | CNY 436.19M" in brief_text
    assert "Gross Margin by Type | Products | 39.00%" in brief_text
    assert "USD 39.00" not in brief_text
    assert "Old Row" not in brief_text

    peers_path = generate_peers_report("HUIZ", data_root=tmp_path, ticker_obj=mock_t)
    peers_text = peers_path.read_text(encoding="utf-8")
    assert "-2.50x" in peers_text
    assert "| ROIC | n/a | n/a | n/a |" in peers_text

    # Bug 3: Invalid DCF (base_fcf <= 0, fair_price < 0) MUST omit valuation table & mark not applicable!
    val = generate_valuation_bundle("HUIZ", data_root=tmp_path, ticker_obj=mock_t, generate_excel=False)
    assert val.model_applicable is False
    dcf_text = val.summary_md_path.read_text(encoding="utf-8")
    assert "Model not applicable" in dcf_text
    assert "-4.04" not in dcf_text
    assert "152.57%" not in dcf_text
    assert "WACC / Discount Rate" in dcf_text  # Assumptions still kept for context

    # Bug 2 & HTML-comment metadata: Accession-number matching works with hidden HTML comment header!
    sec_dir = tmp_path / "HUIZ" / "DataFromDataMule_markdown"
    sec_dir.mkdir(parents=True, exist_ok=True)
    (sec_dir / "HUIZ - 20-F FY 2025.md").write_text(
        "<!-- financial-advisor-meta\nticker: HUIZ\nform_type: 20-F\nfiling_date: 2026-04-24\naccession_number: 0001193125-26-000111\n-->\n# 20-F Body",
        encoding="utf-8",
    )

    cov = generate_sec_coverage_audit("HUIZ", num_years=2, data_root=tmp_path, ticker_obj=mock_t)
    assert cov.notable_extra_forms == {"SC 13D": 1}
    cov_text = cov.path.read_text(encoding="utf-8")
    assert "| 2026-04-24 | `20-F` | Yes |" in cov_text       # Exact accession match!
    assert "| 2025-04-23 | `20-F` | Missing |" in cov_text   # No false positive from "2025" in filename!


# ============================================================================
# 6. ai_analyst.py (Bugs 5-6: Transcript Chronological Order & Unique Memos)
# ============================================================================

def test_deepseek_transcript_sorting_and_unique_same_day_memos(tmp_path: Path):
    t_dir = tmp_path / "HUIZ"
    (t_dir / "Valuation").mkdir(parents=True)
    (t_dir / "EarningsTranscripts").mkdir(parents=True)
    (t_dir / "DataFromDataMule_markdown").mkdir(parents=True)
    (t_dir / "CompanyBrief.md").write_text("# HUIZ Brief\nROIC: 14%\n", encoding="utf-8")
    (t_dir / "Valuation" / "DCF_Summary.md").write_text("# DCF\nAssumed Growth: 18%\n", encoding="utf-8")
    (t_dir / "DataFromDataMule_markdown" / "HUIZ - 20-F FY 2025.md").write_text(
        "<!-- financial-advisor-meta\nticker: HUIZ\naccession_number: 0001193125-26-000111\n-->\n\n# Annual Report Body\n",
        encoding="utf-8",
    )
    (t_dir / "DataFromDataMule_markdown" / "HUIZ - Earnings Release 2026-09-10.md").write_text("# ER Q2", encoding="utf-8")
    (t_dir / "DataFromDataMule_markdown" / "HUIZ - 6-K 2026-09-10.md").write_text("# 6-K", encoding="utf-8")

    # Bug 5 regression: string reverse sort would put Q4 2024 before Q2 2025!
    for fname in ("HUIZ - Q4 2024.md", "HUIZ - Q3 2024.md", "HUIZ - Q1 2025.md", "HUIZ - Q2 2025.md"):
        (t_dir / "EarningsTranscripts" / fname).write_text(f"Transcript {fname}", encoding="utf-8")

    files = list_dossier_files("HUIZ", data_root=tmp_path)
    tr_labels = [f.rel_label for f in files if f.category == "Transcript"]
    assert tr_labels == [
        "EarningsTranscripts/HUIZ - Q2 2025.md",
        "EarningsTranscripts/HUIZ - Q1 2025.md",
        "EarningsTranscripts/HUIZ - Q4 2024.md",
        "EarningsTranscripts/HUIZ - Q3 2024.md",
    ]
    sec_labels = [f.rel_label for f in files if f.category == "SEC Filing"]
    assert sec_labels == [
        "DataFromDataMule_markdown/HUIZ - 20-F FY 2025.md",
        "DataFromDataMule_markdown/HUIZ - Earnings Release 2026-09-10.md",
        "DataFromDataMule_markdown/HUIZ - 6-K 2026-09-10.md",
    ]

    ctx, trunc = build_dossier_context(files)
    assert "FILE: CompanyBrief.md" in ctx
    assert "# Annual Report Body" in ctx
    assert "financial-advisor-meta" not in ctx
    assert "accession_number:" not in ctx
    assert trunc == []

    c_miss = estimate_cost_usd("deepseek-flash", 100_000, 2_000, cache_hit_tokens=0)
    c_hit = estimate_cost_usd("deepseek-flash", 100_000, 2_000, cache_hit_tokens=100_000)
    assert c_hit < c_miss

    mock_session = MagicMock()
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "## Executive Verdict\nModel is slightly aggressive."}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "prompt_cache_hit_tokens": 800},
    }
    mock_session.post.return_value = mock_resp

    # Bug 6 regression: two runs of the same workflow on the same day MUST NOT overwrite each other!
    res1 = run_workflow_analysis(
        ticker="HUIZ", workflow_id="dcf_audit", files=files, api_key="sk-test",
        model="deepseek-flash", save_memo=True, data_root=tmp_path, session=mock_session,
    )
    res2 = run_workflow_analysis(
        ticker="HUIZ", workflow_id="dcf_audit", files=files, api_key="sk-test",
        model="deepseek-flash", save_memo=True, data_root=tmp_path, session=mock_session,
    )
    assert res1.saved_path is not None and res2.saved_path is not None
    assert res1.saved_path != res2.saved_path
    assert res1.saved_path.exists() and res2.saved_path.exists()


def test_call_deepseek_handles_non_json_and_missing_key():
    with pytest.raises(ValueError, match="DeepSeek API key"):
        call_deepseek("", [{"role": "user", "content": "hi"}])

    bad_session = MagicMock()
    bad_resp = MagicMock(status_code=502)
    bad_resp.json.side_effect = ValueError("HTML gateway error")
    bad_session.post.return_value = bad_resp

    with pytest.raises(RuntimeError, match="non-JSON"):
        call_deepseek("sk-test", [{"role": "user", "content": "hi"}], session=bad_session)


def test_scientific_valuation_protocol_editable_and_wired(tmp_path: Path):
    # 1. Default valuation_protocol.md (V6) loads cleanly with Universal USD & zero personal hurdle/sizing rules
    proto = load_valuation_protocol()
    assert "Protocol V6" in proto
    assert "Universal USD currency policy" in proto
    assert "Executive Return Summary — Output First" in proto
    assert "Do not introduce personal investment objectives" in proto
    assert "No personal hurdle, portfolio constraints" in proto
    assert "1.1 Share-class and ADS normalization" in proto
    assert "RONIC" in proto
    assert "Phase 6: Mandatory Mathematical & Consistency Audit" in proto
    assert "17. Executive Summary, Phase 3, Phase 4, Phase 5 and `valuation-summary-json` reconcile exactly" in proto
    # Orientation bug guard: the incremental ratio is revenue-per-capital, never "capital intensity"
    assert "Incremental Sales-to-Capital" in proto
    assert "Incremental Capital Intensity" not in proto
    assert "Recourse rule" in proto
    assert "-100%" in proto

    assert "NEVER use CAPM, Beta, or WACC" in SYSTEM_PROMPT_VALUATION_CHAT
    assert "valuation-summary-json" not in SYSTEM_PROMPT_VALUATION_CHAT

    # Trailing JSON summary (V6 placement: after Phase 6) parses & strips cleanly
    sample_md = (
        "# Executive Return Summary\nBody text.\n\n"
        '<!-- valuation-summary-json {"valuation_date":"2026-11-24","ticker":"YB","current_price_usd":10.61,'
        '"target_year":2031,"base_price_usd":48.54,"bear_price_usd":25.81,"bull_price_usd":86.60,'
        '"expected_moic":5.44,"expected_total_return_pct":443.8,"expected_irr_pct":45.8,'
        '"base_moic":5.53,"base_total_return_pct":452.6,"bear_total_return_pct":208.8,'
        '"bull_total_return_pct":837.0,"prob_capital_loss_pct":5.0,"fifth_percentile_outcome":"Catastrophe",'
        '"asymmetry_ratio":214.9} -->\n'
    )
    parsed = extract_valuation_summary(sample_md)
    assert parsed is not None
    assert parsed["target_year"] == 2031
    assert parsed["expected_moic"] == 5.44
    assert parsed["expected_total_return_pct"] == 443.8
    stripped = strip_valuation_summary_tag(sample_md)
    assert stripped == "# Executive Return Summary\nBody text."
    assert "valuation-summary-json" not in stripped

    # Partial JSON must be rejected so UI never renders bogus zeroed KPI cards!
    assert extract_valuation_summary('<!-- valuation-summary-json {"ticker":"YB"} -->') is None
    assert extract_valuation_summary('<!-- valuation-summary-json {"current_price_usd":0,"base_price_usd":10,"expected_moic":1.5,"expected_irr_pct":8,"prob_capital_loss_pct":10} -->') is None

    # 2. User can save edits to a custom protocol file (and empty text is rejected)
    custom_path = tmp_path / "valuation_protocol.md"
    save_valuation_protocol("# My Updated USD Protocol\nCheck footnotes.\n", path=custom_path)
    assert "Check footnotes." in load_valuation_protocol(path=custom_path)
    with pytest.raises(ValueError, match="empty"):
        save_valuation_protocol("   ", path=custom_path)

    # 3. ValuationParameters + scientific_valuation workflow passes CAPM/hurdle-free system prompt, max_tokens=65536, temp=0.0
    t_dir = tmp_path / "HUIZ"
    t_dir.mkdir(parents=True)
    (t_dir / "CompanyBrief.md").write_text("# HUIZ Brief\nTTM Revenue (CNY): CNY 1.34B\n", encoding="utf-8")
    files = list_dossier_files("HUIZ", data_root=tmp_path)

    params = ValuationParameters(
        spot_price="USD 4.50 [Market Data: Yahoo, 2026-11-24]",
        spot_fx_rate="0.15005 USD/CNY [Market Data: ECB, 2026-11-24]",
        dividend_withholding_tax_pct=10.0,
        adr_custody_fee="0.02",
        activate_cross_border_module=True,
        custom_notes="Use GAAP SBC basis.",
    )

    mock_session = MagicMock()
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "choices": [{
            "finish_reason": "stop",
            "message": {
                "reasoning_content": "Checking ADS ratio and Sales-to-Capital...",
                "content": "## Phase 1\nClean baseline...\n## Phase 6\nAll 17 PASS.",
            },
        }],
        "usage": {"prompt_tokens": 2500, "completion_tokens": 1200, "prompt_cache_hit_tokens": 0},
    }
    mock_session.post.return_value = mock_resp

    res = run_workflow_analysis(
        ticker="HUIZ",
        workflow_id="scientific_valuation",
        files=files,
        api_key="sk-test",
        model="deepseek-flash",
        valuation_params=params,
        protocol_text=load_valuation_protocol(path=custom_path),
        save_memo=True,
        data_root=tmp_path,
        session=mock_session,
    )
    assert res.saved_path is not None and res.saved_path.exists()
    assert res.reasoning == "Checking ADS ratio and Sales-to-Capital..."
    assert res.finish_reason == "stop"
    sent_payload = mock_session.post.call_args.kwargs["json"]
    assert sent_payload["max_tokens"] == 65536
    assert sent_payload["temperature"] == 0.0
    sys_msg = sent_payload["messages"][0]["content"]
    assert "NEVER use CAPM, Beta, WACC" in sys_msg
    assert "NEVER include personal hurdle rates" in sys_msg
    user_msg = sent_payload["messages"][1]["content"]
    assert "Universal USD Policy" in user_msg
    assert "10.00%" in user_msg
    assert "0.02" in user_msg
    assert "ACTIVE — include the full Optional Module" in user_msg
    assert "Use GAAP SBC basis." in user_msg
    assert "My Updated USD Protocol" in user_msg


def test_deepseek_never_returns_thoughts_as_final_answer_and_streams_cleanly():
    # 1. Reasoning-only truncated response MUST raise, NEVER silently return thoughts as answer!
    trunc_session = MagicMock()
    trunc_resp = MagicMock(status_code=200)
    trunc_resp.json.return_value = {
        "choices": [{
            "finish_reason": "length",
            "message": {"reasoning_content": "Long internal thinking...", "content": ""},
        }],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 8192},
    }
    trunc_session.post.return_value = trunc_resp
    with pytest.raises(RuntimeError, match="exhausted the max_tokens limit"):
        call_deepseek_full("sk-test", [{"role": "user", "content": "valuate"}], max_tokens=8192, session=trunc_session)

    # 2. SSE streaming separates reasoning_content and content cleanly + parses usage
    sse_lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"Step 1 think..."},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":"## Phase 1\\nFinal answer."},"finish_reason":"stop"}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":500,"completion_tokens":120,"prompt_cache_hit_tokens":400}}',
        "data: [DONE]",
    ]
    stream_session = MagicMock()
    stream_resp = MagicMock(status_code=200)
    stream_resp.iter_lines.return_value = iter(sse_lines)
    stream_session.post.return_value = stream_resp

    content, reasoning, finish, usage = call_deepseek_full(
        "sk-test",
        [{"role": "user", "content": "valuate"}],
        max_tokens=65536,
        stream=True,
        session=stream_session,
    )
    assert content == "## Phase 1\nFinal answer."
    assert reasoning == "Step 1 think..."
    assert finish == "stop"
    assert usage == {"prompt_tokens": 500, "completion_tokens": 120, "cache_hit_tokens": 400}


def test_generate_valuation_pack_and_spot_fx(tmp_path: Path):
    mock_t = _make_mock_ticker(sector="Technology", currency="CNY", base_fcf=50_000_000.0)
    # Market Cap = 350M USD, Close Price = 3.50 USD => Implied Tradable / ADS Shares = 100.00M
    mock_t.price.return_value = _FakeDF([{"report_date": "2026-11-23", "close": 3.50}])

    class _FakeStatement:
        def __init__(self, df):
            self._df = df

        def df(self):
            return self._df

    mock_t.quarterly_income_statement.return_value = _FakeStatement(_FakeDF([
        {"Breakdown": "Total Revenue", "2026-09-30": 350_000_000, "2026-06-30": 320_000_000},
        {"Breakdown": "Operating Income", "2026-09-30": 42_000_000, "2026-06-30": 38_000_000},
        {"Breakdown": "Net Income Continuous Operations", "2026-09-30": 35_400_000, "2026-06-30": 31_000_000},
        {"Breakdown": "Net Income(Attributable to Parent Company Shareholders)", "2026-09-30": 40_400_000, "2026-06-30": 36_000_000},
        {"Breakdown": "Diluted Average Shares", "2026-09-30": 100_000_000, "2026-06-30": 100_000_000},
    ]))
    mock_t.annual_income_statement.return_value = _FakeStatement(_FakeDF([
        {"Breakdown": "Total Revenue", "2025-12-31": 1_200_000_000, "2024-12-31": 1_050_000_000},
    ]))
    # Ordinary Shares = 10B vs 100M ADS => 100.00x ADS ratio warning MUST trigger!
    mock_t.quarterly_balance_sheet.return_value = _FakeStatement(_FakeDF([
        {"Breakdown": "Cash And Cash Equivalents", "2026-09-30": 400_000_000},
        {"Breakdown": "Stockholders Equity", "2026-09-30": 750_000_000},
        {"Breakdown": "Ordinary Shares Number", "2026-09-30": 10_000_000_000},
    ]))
    mock_t.annual_balance_sheet.return_value = _FakeStatement(_FakeDF([]))
    mock_t.annual_cash_flow.return_value = _FakeStatement(_FakeDF([
        {"Breakdown": "Operating Cash Flow", "2025-12-31": 120_000_000},
        {"Breakdown": "Repurchase Of Capital Stock", "2025-12-31": -15_000_000},
    ]))
    mock_t.quarterly_cash_flow.return_value = _FakeStatement(_FakeDF([]))

    fx_session = MagicMock()
    fx_resp = MagicMock(status_code=200)
    fx_resp.json.return_value = {"date": "2026-11-23", "rates": {"USD": 0.1385}}
    fx_session.get.return_value = fx_resp

    # Identity FX check
    id_rate, _, _ = fetch_spot_fx_rate("USD", "USD")
    assert id_rate == 1.0

    vp = generate_valuation_pack("HUIZ", data_root=tmp_path, ticker_obj=mock_t, fx_session=fx_session)
    assert vp.latest_price == 3.50
    assert vp.fx_rate == 0.1385
    vp_text = vp.path.read_text(encoding="utf-8")
    assert "`[Market Data: DefeatBeta, 2026-11-23]`" in vp_text
    assert "`[Market Data: ECB/Frankfurter, 2026-11-23]`" in vp_text
    assert "| Implied Shares Outstanding (Market Cap / Price) | 100.00M |" in vp_text
    assert "Universal USD Constant-FX Rule" in vp_text
    assert "already in USD — do NOT convert them." in vp_text
    assert "`× 0.138500`" in vp_text
    assert "ADS / Share-Class Ratio Alert" in vp_text
    assert "100.00x" in vp_text
    assert "as of `2026-09-30`" in vp_text
    assert "~100:1" not in vp_text
    # Word-boundary regex regressions ("ratio" in "operations", "share" in "shareholders")
    assert "Net Income Continuous Operations | CNY 35.40M | CNY 31.00M" in vp_text
    assert "Net Income(Attributable to Parent Company Shareholders) | CNY 40.40M | CNY 36.00M" in vp_text
    assert "Diluted Average Shares | 100.00M | 100.00M" in vp_text
    assert "Ordinary Shares Number | 10.00B" in vp_text
    assert "Total Revenue | CNY 350.00M | CNY 320.00M" in vp_text
    assert "Cash And Cash Equivalents | CNY 400.00M" in vp_text
    assert "Repurchase Of Capital Stock | CNY -15.00M" in vp_text

    # ValuationPack.md must be Priority #1 in list_dossier_files
    dossier = list_dossier_files("HUIZ", data_root=tmp_path)
    assert dossier[0].rel_label == "ValuationPack.md"


def test_ai_peer_discovery_and_named_peers_report(tmp_path: Path):
    # 1. AI peer discovery parses JSON array, excludes self ("YB"), sanitizes & dedupes
    ds_session = MagicMock()
    ds_resp = MagicMock(status_code=200)
    ds_resp.json.return_value = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": '```json\n["YB", "WDH", "HUIZ", "LMND", "wdh"]\n```'},
        }],
        "usage": {"prompt_tokens": 120, "completion_tokens": 20},
    }
    ds_session.post.return_value = ds_resp

    peers = discover_peer_tickers(
        ticker="YB",
        api_key="sk-test",
        company_name="Yuanbao Inc.",
        business_summary="Online insurance brokerage in China",
        session=ds_session,
    )
    assert peers == ["WDH", "HUIZ", "LMND"]
    assert ds_session.post.call_args.kwargs["json"]["max_tokens"] == 2048
    assert ds_session.post.call_args.kwargs["json"]["temperature"] == 0.0

    # 2. Named Peers report fetches live GAAP multiples + computes positive EV/NOPAT median cleanly
    class _FakeStmt:
        def __init__(self, df):
            self._df = df

        def df(self):
            return self._df

    def _make_peer(sym, name, ccy, mcap, ev, pe, pb, ps, roe, roic, ebit, pretax, tax):
        p = _make_mock_ticker(currency=ccy)
        p.ticker = sym
        p.info.return_value = _FakeDF([{"short_name": name, "industry": "Insurance Brokers"}])
        p.market_capitalization.return_value = _FakeDF([{"report_date": "2026-11-20", "market_capitalization": mcap}])
        p.enterprise_value.return_value = _FakeDF([{"report_date": "2026-11-20", "enterprise_value": ev}])
        p.price.return_value = _FakeDF([{"report_date": "2026-11-20", "close": 2.00}])
        p.ttm_pe.return_value = _FakeDF([{"report_date": "2026-11-20", "ttm_pe": pe}] if pe is not None else [])
        p.pb_ratio.return_value = _FakeDF([{"report_date": "2026-11-20", "pb_ratio": pb}] if pb is not None else [])
        p.ps_ratio.return_value = _FakeDF([{"report_date": "2026-11-20", "ps_ratio": ps}] if ps is not None else [])
        p.roe.return_value = _FakeDF([{"report_date": "2026-06-30", "roe": roe}] if roe is not None else [])
        p.roic.return_value = _FakeDF([{"report_date": "2026-06-30", "roic": roic}] if roic is not None else [])
        p.quarterly_income_statement.return_value = _FakeStmt(_FakeDF([
            {"Breakdown": "Operating Income", "TTM": ebit},
            {"Breakdown": "Pretax Income", "TTM": pretax},
            {"Breakdown": "Tax Provision", "TTM": tax},
        ]))
        return p

    # WDH: EV=300M USD, EBIT=500M CNY * (1 - 20%) * 0.15 = 60M USD NOPAT => EV/NOPAT = 5.00x
    # HUIZ: EV=-10M USD => neg EV (excluded from EV/NOPAT median)
    # LMND: EV=1.5B USD, EBIT=-200M USD => neg NOPAT (excluded from EV/NOPAT median)
    peer_map = {
        "WDH": _make_peer("WDH", "Waterdrop Inc.", "CNY", 500_000_000, 300_000_000, 8.0, 0.90, 1.10, 0.11, 0.10, 500_000_000, 500_000_000, 100_000_000),
        "HUIZ": _make_peer("HUIZ", "Huize Holding", "CNY", 25_000_000, -10_000_000, 6.0, 0.50, 0.20, 0.08, 0.07, 40_000_000, 40_000_000, 6_000_000),
        "LMND": _make_peer("LMND", "Lemonade Inc.", "USD", 2_000_000_000, 1_500_000_000, None, 3.20, 3.80, -0.25, -0.15, -200_000_000, -200_000_000, 0),
    }

    fx_session = MagicMock()
    fx_resp = MagicMock(status_code=200)
    fx_resp.json.return_value = {"date": "2026-11-24", "rates": {"USD": 0.15}}
    fx_session.get.return_value = fx_resp

    target_mock = _make_mock_ticker(currency="CNY")
    peers_path = generate_peers_report(
        "YB",
        data_root=tmp_path,
        ticker_obj=target_mock,
        peer_tickers=["WDH", "HUIZ", "LMND"],
        peer_ticker_factory=lambda s: peer_map[s],
        fx_session=fx_session,
    )
    txt = peers_path.read_text(encoding="utf-8")
    assert "## Anchor 1 — Named Global Peer Group" in txt
    assert "| `WDH` | Waterdrop Inc. | `2026-11-20` | USD 500.00M | USD 300.00M | 5.00x | 8.00x | 0.90x | 1.10x | 11.00% | 10.00% |" in txt
    assert "| `HUIZ` | Huize Holding | `2026-11-20` | USD 25.00M | USD -10.00M | neg EV |" in txt
    assert "| `LMND` | Lemonade Inc. | `2026-11-20` | USD 2.00B | USD 1.50B | neg NOPAT |" in txt
    # Median EV/NOPAT uses only WDH (5.00x); median P/E uses WDH & HUIZ (7.00x); median P/B uses 0.50, 0.90, 3.20 (0.90x)
    assert "| **Peer Median** | *(positive multiples)* | — | — | — | **5.00x** | **7.00x** | **0.90x** | **1.10x** |" in txt


# ============================================================================
# 7. valuation_math.py & DeepSeek Tool-Calling + Auto-Continuation
# ============================================================================

def test_valuation_math_irr_operating_model_pb_grid_and_four_state():
    # 1. Exact IRR + V6 Section 4.4 zero-recovery -100% rule
    assert solve_irr([-10.0, 0.0, 0.0, 0.0]) == -1.0
    zero_ret = compute_irr_and_returns([-10.0, 0.0, 0.0])
    assert zero_ret["moic"] == 0.0
    assert zero_ret["total_return_pct"] == -100.0
    assert zero_ret["irr_pct"] == -100.0
    assert zero_ret["ten_k_proceeds_usd"] == 0.0

    # Zero-dividend 5Y doubling -> 2^(1/5) - 1 = 14.8698%
    no_div = compute_irr_and_returns([-10.0, 0.0, 0.0, 0.0, 0.0, 20.0])
    assert no_div["moic"] == 2.0
    assert abs(no_div["irr_pct"] - 14.87) < 0.02

    # Interim dividends raise IRR strictly above (MOIC^(1/5)-1)
    with_div = compute_irr_and_returns([-10.0, 1.0, 1.0, 1.0, 1.0, 16.0])
    assert with_div["moic"] == 2.0
    assert with_div["irr_pct"] > no_div["irr_pct"]

    # 2. Operating 5Y model + 1-pass buyback + exact 9-cell 3x3 grid + break-even CAGR + trailing check
    op = compute_operating_valuation_model(
        entry_price_usd=10.0,
        starting_diluted_shares_m=50.0,
        starting_excess_cash_m=100.0,
        year0_revenue_m=400.0,
        tax_rate=0.20,
        sales_to_capital=5.0,
        cash_haircut=0.50,
        ronic=0.25,
        required_return_r=0.11,
        scenarios={
            "bear": {
                "revenue_growth_y1_to_y5": [0.02, 0.02, 0.02, 0.02, 0.02],
                "ebit_margins_y1_to_y5": [0.14, 0.14, 0.14, 0.14, 0.14],
                "terminal_g": 0.02,
                "exit_multiple": 7.0,
                "annual_buyback_usd_m": 0.0,
                "dividend_payout_pct_of_nopat": 0.10,
            },
            "base": {
                "revenue_growth_y1_to_y5": [0.10, 0.09, 0.08, 0.07, 0.06],
                "ebit_margins_y1_to_y5": [0.18, 0.18, 0.18, 0.18, 0.18],
                "terminal_g": 0.03,
                "exit_multiple": 10.0,
                "annual_buyback_usd_m": 15.0,
                "dividend_payout_pct_of_nopat": 0.15,
            },
            "bull": {
                "revenue_growth_y1_to_y5": [0.16, 0.15, 0.14, 0.12, 0.10],
                "ebit_margins_y1_to_y5": [0.22, 0.22, 0.22, 0.22, 0.22],
                "terminal_g": 0.035,
                "exit_multiple": 13.0,
                "annual_buyback_usd_m": 25.0,
                "dividend_payout_pct_of_nopat": 0.15,
            },
        },
    )
    # Center cell (base row, base multiple 10.00x) MUST reconcile identically to Base scenario IRR
    assert op["grid_3x3_irr_pct"]["base"]["base_mult_10.00x"] == op["scenarios"]["base"]["irr_pct"]
    assert op["grid_3x3_terminal_price_usd"]["base"]["base_mult_10.00x"] == op["scenarios"]["base"]["terminal_price_usd"]
    assert all(len(op["grid_3x3_irr_pct"][r]) == 3 for r in ("bear", "base", "bull"))
    # Buybacks retired shares strictly using 1-pass midpoint price
    base_s = op["scenarios"]["base"]
    expected_mid = 0.5 * (10.0 + base_s["zero_buyback_tv_usd"])
    assert abs(base_s["avg_buyback_price_usd"] - expected_mid) < 1e-3
    assert base_s["year5_diluted_shares_m"] < 50.0
    # Trailing Gordon formula check at r ± 200 bps
    tf = op["trailing_formula_crosscheck"]
    assert tf["r_minus_200bps"]["implied_ev_nopat"] > tf["r_base"]["implied_ev_nopat"] > tf["r_plus_200bps"]["implied_ev_nopat"]

    # 3. Financial / Broker P/B x ROE 9-cell grid
    pb = compute_pb_grid(
        entry_price_usd=3.67,
        starting_book_equity_m=700.0,
        diluted_shares_m=183.06,
        required_return_r=0.12,
        scenarios={
            "bear": {"roe": 0.09, "payout_ratio": 0.0, "terminal_g": 0.02, "exit_pb": 0.60},
            "base": {"roe": 0.125, "payout_ratio": 0.0, "terminal_g": 0.03, "exit_pb": 1.10},
            "bull": {"roe": 0.16, "payout_ratio": 0.0, "terminal_g": 0.035, "exit_pb": 1.35},
        },
    )
    assert pb["grid_3x3_irr_pct"]["base"]["base_pb_1.100x"] == pb["scenarios"]["base"]["irr_pct"]
    assert pb["grid_3x3_irr_pct"]["base"]["bear_pb_0.600x"] < pb["grid_3x3_irr_pct"]["bull"]["bear_pb_0.600x"]
    assert pb["justified_pb_sensitivity"]["r_base"]["base"] == round((0.125 - 0.03) / (0.12 - 0.03), 3)

    # 4. Phase 4-5 Four-State Summary (dated Expected IRR, +/-10 pp reallocation, asymmetry)
    four = compute_four_state_summary(
        entry_price_usd=10.0,
        catastrophe={"probability_pct": 10, "event_year": 2, "recovery_per_share_usd": 2.0, "prior_net_dividends_usd": [0.2, 0.0]},
        bear={"probability_pct": 20, "terminal_price_usd": 12.0, "net_dividends_y1_to_y5_usd": [0.2, 0.2, 0.2, 0.2, 0.2]},
        base={"probability_pct": 50, "terminal_price_usd": 24.0, "net_dividends_y1_to_y5_usd": [0.4, 0.4, 0.4, 0.4, 0.4]},
        bull={"probability_pct": 20, "terminal_price_usd": 40.0, "net_dividends_y1_to_y5_usd": [0.6, 0.6, 0.6, 0.6, 0.6]},
    )
    # Weighted dated Expected IRR must NOT equal naive weighted sum of individual scenario IRRs
    naive_irr = (
        0.10 * four["scenarios"]["catastrophe"]["irr_pct"]
        + 0.20 * four["scenarios"]["bear"]["irr_pct"]
        + 0.50 * four["scenarios"]["base"]["irr_pct"]
        + 0.20 * four["scenarios"]["bull"]["irr_pct"]
    )
    assert abs(four["expected"]["expected_irr_pct"] - naive_irr) > 1.0
    # +/-10 pp sensitivity keeps Bull=20%, reallocates proportionally between Bear (20/30) and Cat (10/30)
    assert four["base_prob_minus10_pp"]["probabilities_pct"]["bull"] == 20.0
    assert four["base_prob_minus10_pp"]["probabilities_pct"]["base"] == 40.0
    assert abs(sum(four["base_prob_minus10_pp"]["probabilities_pct"].values()) - 100.0) < 0.02
    assert four["base_prob_plus10_pp"]["probabilities_pct"]["base"] == 60.0
    assert four["base_prob_minus10_pp"]["expected_irr_pct"] < four["expected"]["expected_irr_pct"] < four["base_prob_plus10_pp"]["expected_irr_pct"]
    assert four["distribution"]["prob_capital_loss_pct"] == 10.0
    assert four["distribution"]["median_outcome"] == "Base"
    assert four["distribution"]["fifth_percentile_outcome"] == "Catastrophe"
    assert isinstance(four["distribution"]["asymmetry_ratio"], float) and four["distribution"]["asymmetry_ratio"] > 10.0

    # Dispatcher executes JSON string and handles unknown tool gracefully
    out_json = json.loads(execute_valuation_tool("compute_irr_and_returns", json.dumps({"cash_flows": [-10, 0, 0, 0, 0, 20]})))
    assert out_json["moic"] == 2.0
    err_json = json.loads(execute_valuation_tool("nonexistent_tool", "{}"))
    assert "error" in err_json


def test_deepseek_sse_tool_calling_and_seamless_auto_continuation():
    # 1. SSE streaming tool call: round 1 streams chunked tool_calls + reasoning_content,
    #    round 2 receives tool result and returns final answer.
    sse_round1 = [
        'data: {"choices":[{"delta":{"reasoning_content":"Let me run Python IRR."},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"compute_irr_and_returns","arguments":"{\\"cash_flows\\":"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"[-10, 0, 0, 0, 0, 20]}"}}]},"finish_reason":"tool_calls"}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":1000,"completion_tokens":80,"prompt_cache_hit_tokens":0}}',
        "data: [DONE]",
    ]
    sse_round2 = [
        'data: {"choices":[{"delta":{"reasoning_content":"Got exact 14.87% IRR."},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":"Exact IRR is 14.87% (2.00x MOIC)."},"finish_reason":"stop"}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":1200,"completion_tokens":40,"prompt_cache_hit_tokens":1000}}',
        "data: [DONE]",
    ]
    r1 = MagicMock(status_code=200)
    r1.iter_lines.return_value = iter(sse_round1)
    r2 = MagicMock(status_code=200)
    r2.iter_lines.return_value = iter(sse_round2)

    stream_session = MagicMock()
    stream_session.post.side_effect = [r1, r2]
    progress_events = []
    stats = {}

    content, reasoning, finish, usage = call_deepseek_full(
        api_key="sk-test",
        messages=[{"role": "user", "content": "Compute IRR for -10 to 20 in 5y"}],
        max_tokens=65536,
        stream=True,
        on_progress=lambda ph, rc, cc: progress_events.append((ph, rc, cc)),
        tools=VALUATION_TOOLS,
        stats_out=stats,
        session=stream_session,
    )
    assert content == "Exact IRR is 14.87% (2.00x MOIC)."
    assert "Let me run Python IRR." in reasoning and "Got exact 14.87% IRR." in reasoning
    assert finish == "stop"
    assert stats == {"tool_calls_used": 1, "continuations_used": 0}
    assert usage == {"prompt_tokens": 2200, "completion_tokens": 120, "cache_hit_tokens": 1000}
    assert any(ev[0] == "tool:compute_irr_and_returns" for ev in progress_events)

    # Verify round-2 payload preserved reasoning_content on assistant tool_calls msg & passed exact tool result
    round2_msgs = stream_session.post.call_args_list[1].kwargs["json"]["messages"]
    asst_tc_msg = round2_msgs[-2]
    tool_res_msg = round2_msgs[-1]
    assert asst_tc_msg["role"] == "assistant"
    assert asst_tc_msg["reasoning_content"] == "Let me run Python IRR."
    assert tool_res_msg["role"] == "tool" and tool_res_msg["tool_call_id"] == "call_1"
    assert json.loads(tool_res_msg["content"])["irr_pct"] == 14.87

    # 2. Non-streaming tool call + auto_continue on finish_reason == "length"
    m1 = MagicMock(status_code=200)
    m1.json.return_value = {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "reasoning_content": "Need IRR",
                "content": "",
                "tool_calls": [{
                    "id": "tc_a",
                    "type": "function",
                    "function": {"name": "compute_irr_and_returns", "arguments": '{"cash_flows": [-10, 0, 0]}'},
                }],
            },
        }],
        "usage": {"prompt_tokens": 500, "completion_tokens": 30},
    }
    m2 = MagicMock(status_code=200)
    m2.json.return_value = {
        "choices": [{
            "finish_reason": "length",
            "message": {"reasoning_content": "Drafting", "content": "## Phase 1\nPartial report"},
        }],
        "usage": {"prompt_tokens": 600, "completion_tokens": 400, "prompt_cache_hit_tokens": 500},
    }
    m3 = MagicMock(status_code=200)
    m3.json.return_value = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": "## Phase 6\nAll 17 PASS."},
        }],
        "usage": {"prompt_tokens": 1050, "completion_tokens": 100, "prompt_cache_hit_tokens": 950},
    }
    cont_session = MagicMock()
    cont_session.post.side_effect = [m1, m2, m3]
    cont_stats = {}

    c_txt, _c_rsn, c_fin, c_usage = call_deepseek_full(
        api_key="sk-test",
        messages=[{"role": "user", "content": "Run full valuation"}],
        max_tokens=65536,
        stream=False,
        tools=VALUATION_TOOLS,
        auto_continue=True,
        stats_out=cont_stats,
        session=cont_session,
    )
    assert "## Phase 1\nPartial report" in c_txt
    assert "## Phase 6\nAll 17 PASS." in c_txt
    assert c_fin == "stop"
    assert cont_stats == {"tool_calls_used": 1, "continuations_used": 1}
    assert c_usage["cache_hit_tokens"] == 1450
    # Continuation turn MUST strip prior reasoning_content and omit tools
    cont_payload = cont_session.post.call_args_list[2].kwargs["json"]
    assert "tools" not in cont_payload
    assert all("reasoning_content" not in msg for msg in cont_payload["messages"])

    # 3. Graceful HTTP 400 fallback if endpoint rejects tools
    fb1 = MagicMock(status_code=400)
    fb1.json.return_value = {"error": {"message": "tools parameter not supported"}}
    fb2 = MagicMock(status_code=200)
    fb2.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": "Fallback succeeded."}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    }
    fb_session = MagicMock()
    fb_session.post.side_effect = [fb1, fb2]
    fb_txt, _, fb_fin, _ = call_deepseek_full(
        api_key="sk-test",
        messages=[{"role": "user", "content": "Hi"}],
        tools=VALUATION_TOOLS,
        session=fb_session,
    )
    assert fb_txt == "Fallback succeeded."
    assert fb_fin == "stop"
    assert "tools" not in fb_session.post.call_args_list[1].kwargs["json"]


def test_valuation_math_unit_guards_strict_scenarios_pb_zero_buyback_and_edge_cases():
    base_scens = {
        "bear": {"revenue_growth_y1_to_y5": [0.02] * 5, "ebit_margins_y1_to_y5": [0.14] * 5, "terminal_g": 0.02, "exit_multiple": 7.0},
        "base": {"revenue_growth_y1_to_y5": [0.08] * 5, "ebit_margins_y1_to_y5": [0.18] * 5, "terminal_g": 0.03, "exit_multiple": 10.0},
        "bull": {"revenue_growth_y1_to_y5": [0.14] * 5, "ebit_margins_y1_to_y5": [0.22] * 5, "terminal_g": 0.035, "exit_multiple": 13.0},
    }

    # 1. Raw share count (173,340,000 instead of 173.34) MUST be rejected, never silently scaled by 1e6!
    err_shares = json.loads(execute_valuation_tool("compute_operating_valuation_model", json.dumps({
        "entry_price_usd": 3.67,
        "starting_diluted_shares_m": 173_340_000,
        "starting_excess_cash_m": 100.0,
        "year0_revenue_m": 400.0,
        "tax_rate": 0.15,
        "sales_to_capital": 5.0,
        "cash_haircut": 0.5,
        "scenarios": base_scens,
    })))
    assert "error" in err_shares and "MILLIONS" in err_shares["error"]

    # Raw dollar revenue ($400,000,000,000 > $10T in millions) and percent-vs-decimal tax_rate=15 MUST be rejected
    err_rev = json.loads(execute_valuation_tool("compute_operating_valuation_model", json.dumps({
        "entry_price_usd": 10.0,
        "starting_diluted_shares_m": 50.0,
        "starting_excess_cash_m": 100.0,
        "year0_revenue_m": 40_000_000.0,
        "tax_rate": 0.15,
        "sales_to_capital": 5.0,
        "cash_haircut": 0.5,
        "scenarios": base_scens,
    })))
    assert "error" in err_rev and "MILLIONS" in err_rev["error"]

    with pytest.raises(ValueError, match="decimal"):
        compute_operating_valuation_model(
            entry_price_usd=10.0,
            starting_diluted_shares_m=50.0,
            starting_excess_cash_m=100.0,
            year0_revenue_m=400.0,
            tax_rate=15.0,
            sales_to_capital=5.0,
            cash_haircut=0.5,
            scenarios=base_scens,
        )

    # 2. Missing scenario or missing required keys MUST raise (never invent silent defaults)
    with pytest.raises(ValueError, match="missing required key"):
        compute_operating_valuation_model(
            entry_price_usd=10.0,
            starting_diluted_shares_m=50.0,
            starting_excess_cash_m=100.0,
            year0_revenue_m=400.0,
            tax_rate=0.15,
            sales_to_capital=5.0,
            cash_haircut=0.5,
            scenarios={**base_scens, "bear": {"revenue_growth_y1_to_y5": [0.02] * 5}},
        )
    with pytest.raises(ValueError, match="missing required key"):
        compute_pb_grid(
            entry_price_usd=3.67,
            starting_book_equity_m=700.0,
            diluted_shares_m=183.06,
            scenarios={
                "bear": {"roe": 0.09, "payout_ratio": 0.0, "terminal_g": 0.02, "exit_pb": 0.60},
                "base": {"roe": 0.125, "terminal_g": 0.03, "exit_pb": 1.10},  # missing payout_ratio
                "bull": {"roe": 0.16, "payout_ratio": 0.0, "terminal_g": 0.035, "exit_pb": 1.35},
            },
        )

    # 3. P/B true zero-buyback Pass 1: zero_buyback_tv_usd MUST equal the exact 0-buyback TV!
    pb_no_bb = compute_pb_grid(
        entry_price_usd=3.67,
        starting_book_equity_m=700.0,
        diluted_shares_m=183.06,
        scenarios={
            "bear": {"roe": 0.09, "payout_ratio": 0.10, "terminal_g": 0.02, "exit_pb": 0.60, "annual_buyback_usd_m": 0.0},
            "base": {"roe": 0.125, "payout_ratio": 0.10, "terminal_g": 0.03, "exit_pb": 1.10, "annual_buyback_usd_m": 0.0},
            "bull": {"roe": 0.16, "payout_ratio": 0.10, "terminal_g": 0.035, "exit_pb": 1.35, "annual_buyback_usd_m": 0.0},
        },
    )
    pb_with_bb = compute_pb_grid(
        entry_price_usd=3.67,
        starting_book_equity_m=700.0,
        diluted_shares_m=183.06,
        scenarios={
            "bear": {"roe": 0.09, "payout_ratio": 0.10, "terminal_g": 0.02, "exit_pb": 0.60, "annual_buyback_usd_m": 15.0},
            "base": {"roe": 0.125, "payout_ratio": 0.10, "terminal_g": 0.03, "exit_pb": 1.10, "annual_buyback_usd_m": 30.0},
            "bull": {"roe": 0.16, "payout_ratio": 0.10, "terminal_g": 0.035, "exit_pb": 1.35, "annual_buyback_usd_m": 250.0},
        },
    )
    assert pb_with_bb["scenarios"]["base"]["zero_buyback_tv_usd"] == pb_no_bb["scenarios"]["base"]["terminal_price_usd"]
    assert pb_with_bb["scenarios"]["base"]["buyback_cap_binding"] is False
    assert pb_with_bb["scenarios"]["bull"]["buyback_cap_binding"] is True
    assert pb_with_bb["units_sanity"]["implied_market_cap_usd_m"] == round(3.67 * 183.06, 2)
    # Center cell still matches Base exactly when buybacks are active
    assert pb_with_bb["grid_3x3_irr_pct"]["base"]["base_pb_1.100x"] == pb_with_bb["scenarios"]["base"]["irr_pct"]

    # 4. Non-op investment compounding & shrinking-share dividends & units_sanity in operating router
    op_comp = compute_operating_valuation_model(
        entry_price_usd=10.0,
        starting_diluted_shares_m=50.0,
        starting_excess_cash_m=100.0,
        starting_non_op_investments_m=100.0,
        after_tax_investment_yield=0.05,
        year0_revenue_m=400.0,
        tax_rate=0.20,
        sales_to_capital=5.0,
        cash_haircut=0.50,
        scenarios={
            "bear": {**base_scens["bear"], "dividend_payout_pct_of_nopat": 0.20, "annual_buyback_usd_m": 0.0},
            "base": {**base_scens["base"], "dividend_payout_pct_of_nopat": 0.20, "annual_buyback_usd_m": 30.0},
            "bull": {**base_scens["bull"], "dividend_payout_pct_of_nopat": 0.20, "annual_buyback_usd_m": 40.0},
        },
    )
    assert abs(op_comp["scenarios"]["base"]["year5_non_op_investments_m"] - 100.0 * (1.05 ** 5)) < 0.05
    assert op_comp["units_sanity"]["implied_market_cap_usd_m"] == 500.0
    # Per-share dividends grow strictly faster than NOPAT when buybacks shrink the share count
    divs_base = op_comp["scenarios"]["base"]["net_dividends_y1_to_y5_usd"]
    assert divs_base[-1] > divs_base[0]

    # 5. Value-destructive growth (low margin + low STC => IRR falls as growth rises): break-even still solves cleanly!
    op_vdest = compute_operating_valuation_model(
        entry_price_usd=10.0,
        starting_diluted_shares_m=50.0,
        starting_excess_cash_m=450.0,
        year0_revenue_m=500.0,
        tax_rate=0.20,
        sales_to_capital=0.40,
        cash_haircut=1.0,
        scenarios={
            "bear": {"revenue_growth_y1_to_y5": [0.0] * 5, "ebit_margins_y1_to_y5": [0.02] * 5, "terminal_g": 0.0, "exit_multiple": 5.0},
            "base": {"revenue_growth_y1_to_y5": [0.10] * 5, "ebit_margins_y1_to_y5": [0.02] * 5, "terminal_g": 0.0, "exit_multiple": 5.0},
            "bull": {"revenue_growth_y1_to_y5": [0.20] * 5, "ebit_margins_y1_to_y5": [0.02] * 5, "terminal_g": 0.0, "exit_multiple": 5.0},
        },
    )
    assert op_vdest["breakeven_0pct_irr_cagr_pct"] is not None
    assert "value-destructive" in op_vdest["breakeven_note"]


def test_deepseek_repeated_sse_tool_name_reasoning_400_and_multi_continuation():
    # 1. Repeated function.name in later SSE delta MUST NOT double the tool name!
    sse_r1 = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":"compute_irr_and_returns","arguments":"{\\"cash_flows\\":"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"compute_irr_and_returns","arguments":"[-10, 0, 0, 0, 0, 20]}"}}]},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]
    sse_r2 = [
        'data: {"choices":[{"delta":{"content":"Done 14.87%."},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    s1, s2 = MagicMock(status_code=200), MagicMock(status_code=200)
    s1.iter_lines.return_value = iter(sse_r1)
    s2.iter_lines.return_value = iter(sse_r2)
    sess = MagicMock()
    sess.post.side_effect = [s1, s2]
    txt, _, fin, _ = call_deepseek_full("sk-test", [{"role": "user", "content": "hi"}], stream=True, tools=VALUATION_TOOLS, session=sess)
    assert txt == "Done 14.87%." and fin == "stop"
    tool_msg = sess.post.call_args_list[1].kwargs["json"]["messages"][-1]
    assert json.loads(tool_msg["content"])["irr_pct"] == 14.87

    # 2. HTTP 400 rejecting `reasoning_content` on round 2 strips reasoning_content & retries cleanly
    a1 = MagicMock(status_code=200)
    a1.json.return_value = {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "reasoning_content": "Thinking...",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "compute_irr_and_returns", "arguments": '{"cash_flows": [-10, 20]}'}}],
            },
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    }
    a2_err = MagicMock(status_code=400)
    a2_err.json.return_value = {"error": {"message": "Unknown field reasoning_content in message"}}
    a2_ok = MagicMock(status_code=200)
    a2_ok.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": "Recovered after stripping reasoning_content."}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 15},
    }
    rc_sess = MagicMock()
    rc_sess.post.side_effect = [a1, a2_err, a2_ok]
    rc_txt, _, rc_fin, _ = call_deepseek_full("sk-test", [{"role": "user", "content": "hi"}], tools=VALUATION_TOOLS, session=rc_sess)
    assert rc_txt == "Recovered after stripping reasoning_content." and rc_fin == "stop"
    assert all("reasoning_content" not in m for m in rc_sess.post.call_args_list[2].kwargs["json"]["messages"])

    # 3. Multi-shot auto-continuation (2 length caps -> 3rd finishes cleanly)
    c1 = MagicMock(status_code=200)
    c1.json.return_value = {"choices": [{"finish_reason": "length", "message": {"content": "Part 1"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
    c2 = MagicMock(status_code=200)
    c2.json.return_value = {"choices": [{"finish_reason": "length", "message": {"content": "Part 2"}}], "usage": {"prompt_tokens": 150, "completion_tokens": 50}}
    c3 = MagicMock(status_code=200)
    c3.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": "Part 3"}}], "usage": {"prompt_tokens": 200, "completion_tokens": 50}}
    mc_sess = MagicMock()
    mc_sess.post.side_effect = [c1, c2, c3]
    mc_stats = {}
    mc_txt, _, mc_fin, _ = call_deepseek_full(
        "sk-test", [{"role": "user", "content": "long report"}],
        auto_continue=True, max_continuations=2, stats_out=mc_stats, session=mc_sess,
    )
    assert mc_txt == "Part 1\nPart 2\nPart 3"
    assert mc_fin == "stop"
    assert mc_stats["continuations_used"] == 2


def test_peer_quality_gate_filters_stale_nanocaps_and_near_zero_ev(tmp_path: Path):
    # 1. discover_peer_tickers includes scale context & global two-bucket mandate, parses dict-list JSON cleanly
    ds_sess = MagicMock()
    ds_resp = MagicMock(status_code=200)
    ds_resp.json.return_value = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": '[{"ticker": "WDH"}, {"ticker": "HUIZ"}, {"ticker": "LMND"}, {"ticker": "OSCR"}]'},
        }],
        "usage": {"prompt_tokens": 180, "completion_tokens": 35},
    }
    ds_sess.post.return_value = ds_resp

    peers = discover_peer_tickers(
        ticker="YB",
        api_key="sk-test",
        company_name="Yuanbao Inc.",
        country="China",
        business_summary="Online insurance distribution platform.",
        market_cap_usd="USD 343.47M",
        ttm_revenue_usd="USD 742.05M",
        session=ds_sess,
    )
    assert peers == ["WDH", "HUIZ", "LMND", "OSCR"]
    sent_prompt = ds_sess.post.call_args.kwargs["json"]["messages"][1]["content"]
    assert "USD 343.47M" in sent_prompt
    assert "Bucket B (Scaled global / US business-model peers" in sent_prompt

    # 2. generate_peers_report deterministically filters stale quotes (FANH), nano-caps (TIRX, ZBAO),
    #    and guards near-zero-EV distortion (HUIZ EV=$217K on $21.5M mcap -> excluded from EV/NOPAT median!)
    class _FakeStmt:
        def __init__(self, df):
            self._df = df

        def df(self):
            return self._df

    def _mk_peer(sym, name, dt, ccy, mcap, ev, pe, pb, ps, ebit, pretax, tax):
        p = _make_mock_ticker(currency=ccy)
        p.ticker = sym
        p.info.return_value = _FakeDF([{"short_name": name}])
        p.market_capitalization.return_value = _FakeDF([{"report_date": dt, "market_capitalization": mcap}] if mcap is not None else [])
        p.price.return_value = _FakeDF([{"report_date": dt, "close": 5.0}])
        p.enterprise_value.return_value = _FakeDF([{"report_date": dt, "enterprise_value": ev}] if ev is not None else [])
        p.ttm_pe.return_value = _FakeDF([{"report_date": dt, "ttm_pe": pe}] if pe is not None else [])
        p.pb_ratio.return_value = _FakeDF([{"report_date": dt, "pb_ratio": pb}] if pb is not None else [])
        p.ps_ratio.return_value = _FakeDF([{"report_date": dt, "ps_ratio": ps}] if ps is not None else [])
        p.roe.return_value = _FakeDF([{"report_date": dt, "roe": 0.08}])
        p.roic.return_value = _FakeDF([{"report_date": dt, "roic": 0.07}])
        p.quarterly_income_statement.return_value = _FakeStmt(_FakeDF([
            {"Breakdown": "Operating Income", "TTM": ebit},
            {"Breakdown": "Pretax Income", "TTM": pretax},
            {"Breakdown": "Tax Provision", "TTM": tax},
        ]))
        return p

    # Target mcap = $350M -> dynamic floor = 5% = $17.50M; ref_date = 2026-11-20
    peer_map = {
        "WDH": _mk_peer("WDH", "Waterdrop Inc.", "2026-11-20", "USD", 280_000_000, 215_000_000, 4.8, 0.37, 0.38, 70_000_000, 70_000_000, 14_000_000),
        "HUIZ": _mk_peer("HUIZ", "Huize Holding", "2026-11-20", "USD", 21_500_000, 216_000, 8.5, 0.34, 0.12, 2_700_000, 2_700_000, 540_000),  # EV < 5% mcap => near-zero EV!
        "OSCR": _mk_peer("OSCR", "Oscar Health", "2026-11-20", "USD", 3_200_000_000, 2_500_000_000, 18.0, 2.80, 0.35, 250_000_000, 250_000_000, 50_000_000),
        "FANH": _mk_peer("FANH", "Fanhua (Stale)", "2024-12-20", "USD", 120_000_000, 80_000_000, 5.0, 0.40, 0.20, 20_000_000, 20_000_000, 4_000_000),
        "TIRX": _mk_peer("TIRX", "Tian Ruixiang", "2026-03-13", "USD", 5_000_000, 7_000_000, None, 0.11, None, -1_000_000, -1_000_000, 0),
        "ZBAO": _mk_peer("ZBAO", "Zhibao Tech", "2026-11-20", "USD", 3_200_000, 10_900_000, None, 1.07, None, 1_360_000, 1_360_000, 272_000),
    }

    target_mock = _make_mock_ticker(currency="CNY")
    peers_path = generate_peers_report(
        "YB",
        data_root=tmp_path,
        ticker_obj=target_mock,
        peer_tickers=["WDH", "HUIZ", "OSCR", "FANH", "TIRX", "ZBAO"],
        peer_ticker_factory=lambda s: peer_map[s],
    )
    txt = peers_path.read_text(encoding="utf-8")
    # Passing quality peers are in the Anchor 1 table
    assert "| `WDH` |" in txt
    assert "| `HUIZ` |" in txt
    assert "| `OSCR` |" in txt
    assert "0.10x (near-zero EV)" in txt
    # Trash / stale candidates are NOT in the Anchor 1 table, and ARE transparently listed in the filter audit note
    assert "| `FANH` |" not in txt
    assert "| `TIRX` |" not in txt
    assert "| `ZBAO` |" not in txt
    assert "_Filtered out candidates" in txt
    assert "`FANH` (stale quote 2024-12-20)" in txt
    assert "`TIRX` (stale quote 2026-03-13; mcap USD 5.00M" in txt
    assert "`ZBAO` (mcap USD 3.20M" in txt
    # Median EV/NOPAT uses WDH (3.84x) and OSCR (12.50x) -> 8.17x (NOT dragged down by HUIZ 0.10x!)
    assert "**8.17x**" in txt


def test_sec_edgar_api_fallback_when_datamule_returns_zero_submissions(tmp_path: Path, monkeypatch):
    import sys
    import types

    _TICKER_CIK_CACHE.clear()

    # Fake datamule Portfolio that succeeds without error yet downloads 0 files (exact silent-403 bug!)
    seen_dm_kwargs = []

    class _EmptyPortfolio:
        def __init__(self, path):
            self.path = path

        def download_submissions(self, **kwargs):
            seen_dm_kwargs.append(kwargs)

        def __iter__(self):
            return iter([])

    fake_dm = types.ModuleType("datamule")
    fake_dm.Portfolio = _EmptyPortfolio
    monkeypatch.setitem(sys.modules, "datamule", fake_dm)
    monkeypatch.setattr("sec_downloader.time.sleep", lambda _: None)

    def _mk_resp(status=200, json_data=None, text_data=""):
        r = MagicMock(status_code=status, text=text_data)
        if json_data is not None:
            r.json.return_value = json_data
        return r

    index_20f = (
        "<table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>"
        "<tr><td>1</td><td>20-F</td><td>qfin-20251231x20f.htm iXBRL</td><td>20-F</td><td>12345</td></tr>"
        "<tr><td>2</td><td>Cert</td><td>ex31-1.htm</td><td>EX-31.1</td><td>999</td></tr></table>"
    )
    index_6k = (
        "<table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>"
        "<tr><td>1</td><td>6-K</td><td>qfin-6k.htm</td><td>6-K</td><td>2000</td></tr>"
        "<tr><td>2</td><td>Press Release</td><td>qfin-ex99d1.htm</td><td>EX-99.1</td><td>8000</td></tr></table>"
    )
    html_20f = (
        "<html><head><style>.x{color:red}</style></head><body>"
        "<h1>Qfin Holdings FY2025 20-F</h1>"
        "<table><tr><th>Metric</th><th></th><th>2025</th></tr>"
        "<tr><td>Net Revenue</td><td>$</td><td>3,088.5</td></tr></table>"
        "</body></html>"
    )
    html_6k = "<html><body><p>Form 6-K Cover</p></body></html>"
    html_ex99 = "<html><body><h2>Q3 2026 Results</h2><p>Revenue RMB 3,088.5M</p></body></html>"

    today_yr = date.today().year
    d_20f = f"{today_yr}-04-28"
    d_6k = f"{today_yr}-08-25"

    def _fake_get(url, headers=None, timeout=None):
        assert "User-Agent" in (headers or {})
        if url.endswith("company_tickers.json"):
            return _mk_resp(json_data={"0": {"cik_str": 1741530, "ticker": "QFIN", "title": "Qfin Holdings, Inc."}})
        if url.endswith("CIK0001741530.json"):
            return _mk_resp(json_data={
                "filings": {
                    "recent": {
                        "form": ["20-F", "6-K", "4"],
                        "filingDate": [d_20f, d_6k, d_6k],
                        "reportDate": [f"{today_yr - 1}-12-31", f"{today_yr}-06-30", ""],
                        "accessionNumber": ["0001104659-26-049501", "0001104659-26-088888", "0001104659-26-099999"],
                        "primaryDocument": ["qfin-20251231x20f.htm", "qfin-6k.htm", "form4.xml"],
                    },
                    "files": [],
                }
            })
        if url.endswith("0001104659-26-049501-index.htm"):
            return _mk_resp(text_data=index_20f)
        if url.endswith("0001104659-26-088888-index.htm"):
            return _mk_resp(text_data=index_6k)
        if url.endswith("qfin-20251231x20f.htm"):
            return _mk_resp(text_data=html_20f)
        if url.endswith("qfin-6k.htm"):
            return _mk_resp(text_data=html_6k)
        if url.endswith("qfin-ex99d1.htm"):
            return _mk_resp(text_data=html_ex99)
        return _mk_resp(status=404)

    mock_sess = MagicMock()
    mock_sess.get.side_effect = _fake_get

    res = download_and_convert_sec_filings(
        ticker="QFIN",
        form_codes=["20-F", "10-K", "6-K"],
        num_years=1,
        data_root=tmp_path,
        session=mock_sess,
    )
    assert res.resolved_cik == "0001741530"
    assert res.source_used == "sec_edgar_api"
    assert res.submissions_found == 2
    assert len(res.converted_files) == 3  # 20-F + 6-K + EX-99.1 (EX-31.1 & Form 4 filtered out!)
    assert all(kw.get("cik") == "0001741530" for kw in seen_dm_kwargs)

    names = sorted(p.name for p in res.converted_files)
    assert any("20-F" in n for n in names)
    assert any("EX-99.1" in n for n in names)

    f_20f = next(p for p in res.converted_files if "20-F" in p.name)
    body = f_20f.read_text(encoding="utf-8")
    assert "accession_number: 0001104659-26-049501" in body
    assert "| Net Revenue | 3,088.5 |" in body
    assert "color:red" not in body
    assert html_to_markdown("<h2>Title</h2><p>Hello&nbsp;World</p>") == "## Title\n\nHello World"
    assert resolve_ticker_to_cik("QFIN", session=mock_sess) == "0001741530"


def test_datamule_document_without_markdown_attr_converts_via_content(tmp_path: Path, monkeypatch):
    """Regression: older datamule Document objects have `.content` (HTML/bytes) but no `.markdown` property."""
    import sys
    import types

    _TICKER_CIK_CACHE["QFIN"] = "0001741530"
    today_yr = date.today().year

    class _FakeDoc:
        def __init__(self, doc_type, content):
            self.type = doc_type
            self.content = content

    class _FakeSub:
        def __init__(self):
            self.accession = "0001104659-26-049501"
            self.metadata = types.SimpleNamespace(content={
                "type": "20-F",
                "period": f"{today_yr - 1}1231",
                "filing-date": f"{today_yr}0428",
                "accession-number": "0001104659-26-049501",
            })
            self._docs = [
                _FakeDoc("20-F", b"<h1>Qfin 20-F</h1><table><tr><th>Item</th><th>2025</th></tr><tr><td>Rev</td><td>3,088</td></tr></table>"),
            ]

        def __iter__(self):
            return iter(self._docs)

    class _FakePortfolio:
        def __init__(self, path):
            self.path = path

        def download_submissions(self, **kwargs):
            pass

        def __iter__(self):
            return iter([_FakeSub()])

    fake_dm = types.ModuleType("datamule")
    fake_dm.Portfolio = _FakePortfolio
    monkeypatch.setitem(sys.modules, "datamule", fake_dm)

    res = download_and_convert_sec_filings(
        ticker="QFIN",
        form_codes=["20-F"],
        num_years=1,
        data_root=tmp_path,
    )
    assert res.source_used == "datamule"
    assert res.submissions_found == 1
    assert res.skipped_unparseable == 0
    assert res.form_errors == {}
    assert len(res.converted_files) == 1
    body = res.converted_files[0].read_text(encoding="utf-8")
    assert "# Qfin 20-F" in body
    assert "| Rev | 3,088 |" in body








