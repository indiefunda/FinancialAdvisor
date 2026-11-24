"""Unit tests for FinancialAdvisor core modules (SEC, Transcripts, Fundamentals, DeepSeek AI)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_analyst import (
    ValuationParameters,
    build_dossier_context,
    call_deepseek,
    call_deepseek_full,
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
    deduplicate_filename,
    download_and_convert_sec_filings,
    is_useful_document,
    make_sec_filename,
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
    # 1. Default valuation_protocol.md (v2) loads cleanly with all core guardrails + Executive Return Summary
    proto = load_valuation_protocol()
    assert "Executive Return Summary" in proto
    assert "Total % Return" in proto
    assert "NO CAPM, NO BETA, NO WACC" in proto
    assert "Share-Class & ADS Ratio Normalization" in proto
    assert "RONIC" in proto
    assert "Phase 6: Mandatory Self-Audit" in proto
    assert "Quarter-Kelly" in proto

    sample_md = (
        '<!-- valuation-summary-json {"currency":"USD","current_price":3.67,"target_year":2031,'
        '"expected_price":9.41,"expected_total_return_pct":156.4,"expected_irr_pct":20.9,'
        '"base_price":9.22,"base_total_return_pct":151.2,"bear_total_return_pct":8.3,'
        '"bull_total_return_pct":399.1,"catastrophe_total_return_pct":-29.0,'
        '"max_weight_pct":8.6,"verdict":"CLEARS"} -->\n# Executive Return Summary\nBody'
    )
    parsed = extract_valuation_summary(sample_md)
    assert parsed is not None
    assert parsed["target_year"] == 2031
    assert parsed["expected_total_return_pct"] == 156.4
    stripped = strip_valuation_summary_tag(sample_md)
    assert stripped.startswith("# Executive Return Summary")
    assert "valuation-summary-json" not in stripped

    # 2. User can save edits to a custom protocol file (and empty text is rejected)
    custom_path = tmp_path / "valuation_protocol.md"
    save_valuation_protocol("# My Updated Protocol\nHurdle: 18%\n", path=custom_path)
    assert "Hurdle: 18%" in load_valuation_protocol(path=custom_path)
    with pytest.raises(ValueError, match="empty"):
        save_valuation_protocol("   ", path=custom_path)

    # 3. ValuationParameters + scientific_valuation workflow passes CAPM-free system prompt & max_tokens=65536
    t_dir = tmp_path / "HUIZ"
    t_dir.mkdir(parents=True)
    (t_dir / "CompanyBrief.md").write_text("# HUIZ Brief\nTTM Revenue (CNY): CNY 1.34B\n", encoding="utf-8")
    files = list_dossier_files("HUIZ", data_root=tmp_path)

    params = ValuationParameters(
        hurdle_rate_pct=18.0,
        home_currency="EUR",
        spot_price="USD 4.50 [Market Data: Yahoo, 2026-11-24]",
        spot_fx_rate="0.92 EUR/USD [Market Data: ECB, 2026-11-24]",
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
                "content": "## Phase 1\nClean baseline...\n## Phase 6\nAll PASS.",
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
    assert "NEVER use CAPM, Beta, or WACC" in sent_payload["messages"][0]["content"]
    user_msg = sent_payload["messages"][1]["content"]
    assert "18.00% nominal annual IRR" in user_msg
    assert "`EUR`" in user_msg
    assert "ACTIVE — include the full Cross-Border" in user_msg
    assert "Use GAAP SBC basis." in user_msg
    assert "My Updated Protocol" in user_msg


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
    # Ordinary Shares = 10B vs 100M ADS => 100:1 ADS ratio warning MUST trigger!
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

    vp = generate_valuation_pack("HUIZ", home_currency="USD", data_root=tmp_path, ticker_obj=mock_t, fx_session=fx_session)
    assert vp.latest_price == 3.50
    assert vp.fx_rate == 0.1385
    vp_text = vp.path.read_text(encoding="utf-8")
    assert "`[Market Data: DefeatBeta, 2026-11-23]`" in vp_text
    assert "`[Market Data: ECB/Frankfurter, 2026-11-23]`" in vp_text
    assert "| Implied Shares Outstanding (Tradable / ADS Basis) | 100.00M |" in vp_text
    assert "already in USD (home currency) — do NOT convert them." in vp_text
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


