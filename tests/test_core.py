"""Unit tests for FinancialAdvisor core modules (SEC, Transcripts, Fundamentals, DeepSeek AI)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_analyst import (
    build_dossier_context,
    call_deepseek,
    estimate_cost_usd,
    list_dossier_files,
    run_workflow_analysis,
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
    generate_company_brief,
    generate_peers_report,
    generate_sec_coverage_audit,
    generate_valuation_bundle,
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
# 5. fundamentals.py (CompanyBrief, Peers, DCF, SEC Coverage Audit)
# ============================================================================

def _make_mock_ticker(sector="Financial Services", currency="CNY", base_fcf=-15_000_000.0):
    t = MagicMock()
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
    t.net_debt_ttm.return_value = _FakeDF([{"report_date": "2026-09-30", "net_debt_ttm": -70_000_000}])
    t.debt_to_equity.return_value = _FakeDF([{"report_date": "2026-09-30", "debt_to_equity": 0.35}])
    t.wacc.return_value = _FakeDF([{"report_date": "2026-09-30", "wacc": 0.112}])
    t.ttm_eps.return_value = _FakeDF([{"report_date": "2026-09-30", "tailing_eps": 0.45}])
    t.ttm_revenue.return_value = _FakeDF([{"report_date": "2026-09-30", "ttm_revenue": 1_200_000_000}])
    t.ttm_fcf.return_value = _FakeDF([{"report_date": "2026-09-30", "ttm_fcf": base_fcf}])
    # Empty ROIC (just like UPST / LU in real life) -> must degrade to n/a, NOT 0!
    t.roic.return_value = _FakeDF([])
    t.roe.return_value = _FakeDF([{"report_date": "2026-09-30", "roe": 0.14}])
    t.roa.return_value = _FakeDF([{"report_date": "2026-09-30", "roa": 0.06}])
    t.quarterly_gross_margin.return_value = _FakeDF([{"report_date": "2026-09-30", "gross_margin": 0.38}])
    t.quarterly_operating_margin.return_value = _FakeDF([{"report_date": "2026-09-30", "operating_margin": 0.12}])
    t.quarterly_net_margin.return_value = _FakeDF([{"report_date": "2026-09-30", "net_margin": 0.09}])
    t.quarterly_fcf_margin.return_value = _FakeDF([])
    t.quarterly_revenue_yoy_growth.return_value = _FakeDF([{"report_date": "2026-09-30", "yoy_growth": 0.21}])
    t.trailing_revenue_by_breakdown.return_value = _FakeDF([])

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
            "fair_price": 6.20,
            "margin_of_safety": 0.274,
            "recommendation": "UNDERVALUED",
            "enterprise_value": 280_000_000,
            "cash": 100_000_000,
            "total_debt": 30_000_000,
            "equity_value": 350_000_000,
            "shares_outstanding": 56_000_000,
        },
    }
    t.sec_filing.return_value = _FakeDF([
        {"filing_date": "2026-04-20", "form_type": "20-F", "form_type_description": "Annual report", "filing_url": "https://sec.gov/1"},
        {"filing_date": "2026-05-15", "form_type": "SC 13D", "form_type_description": "Active stake", "filing_url": "https://sec.gov/2"},
    ])
    return t


def test_fundamentals_brief_peers_dcf_and_coverage(tmp_path: Path):
    mock_t = _make_mock_ticker(sector="Financial Services", currency="CNY", base_fcf=-15_000_000.0)

    brief = generate_company_brief("HUIZ", data_root=tmp_path, ticker_obj=mock_t)
    assert brief.currency == "CNY"
    assert brief.next_earnings_date == "2026-12-15"
    assert brief.metrics["ROIC"] == "n/a"  # Empty ROIC degrades to n/a, never 0%
    brief_text = brief.path.read_text(encoding="utf-8")
    assert "`CNY`" in brief_text
    assert "12.50x" in brief_text

    peers_path = generate_peers_report("HUIZ", data_root=tmp_path, ticker_obj=mock_t)
    peers_text = peers_path.read_text(encoding="utf-8")
    assert "-2.50x" in peers_text  # 12.5 - 15.0 spread
    assert "| ROIC | n/a | n/a | n/a |" in peers_text

    val = generate_valuation_bundle("HUIZ", data_root=tmp_path, ticker_obj=mock_t, generate_excel=False)
    assert val.json_path is not None and val.json_path.exists()
    assert len(val.warnings) == 2  # Both negative FCF AND Financial Services caveats flagged!
    dcf_text = val.summary_md_path.read_text(encoding="utf-8")
    assert "non-positive" in dcf_text
    assert "Financial Services" in dcf_text

    # Seed one matching local SEC markdown file to verify diff detection
    sec_dir = tmp_path / "HUIZ" / "DataFromDataMule_markdown"
    sec_dir.mkdir(parents=True, exist_ok=True)
    (sec_dir / "HUIZ - 20-F FY 2026-04-20.md").write_text("# 20-F", encoding="utf-8")

    cov = generate_sec_coverage_audit("HUIZ", num_years=2, data_root=tmp_path, ticker_obj=mock_t)
    assert cov.total_indexed == 2
    assert cov.notable_extra_forms == {"SC 13D": 1}
    cov_text = cov.path.read_text(encoding="utf-8")
    assert "| 2026-04-20 | `20-F` | Yes |" in cov_text
    assert "| 2026-05-15 | `SC 13D` | Missing |" in cov_text


# ============================================================================
# 6. ai_analyst.py (DeepSeek Engine)
# ============================================================================

def test_deepseek_cost_dossier_workflow_and_memo_saving(tmp_path: Path):
    # Create sample dossier files
    t_dir = tmp_path / "HUIZ"
    (t_dir / "Valuation").mkdir(parents=True)
    (t_dir / "EarningsTranscripts").mkdir(parents=True)
    (t_dir / "CompanyBrief.md").write_text("# HUIZ Brief\nROIC: 14%\n", encoding="utf-8")
    (t_dir / "Valuation" / "DCF_Summary.md").write_text("# DCF\nAssumed Growth: 18%\n", encoding="utf-8")
    (t_dir / "EarningsTranscripts" / "HUIZ - Q3 2026.md").write_text("**CEO:** We guide 15% growth.\n", encoding="utf-8")

    files = list_dossier_files("HUIZ", data_root=tmp_path)
    assert [f.rel_label for f in files] == [
        "CompanyBrief.md",
        "Valuation/DCF_Summary.md",
        "EarningsTranscripts/HUIZ - Q3 2026.md",
    ]
    ctx, trunc = build_dossier_context(files)
    assert "FILE: CompanyBrief.md" in ctx
    assert "FILE: Valuation/DCF_Summary.md" in ctx
    assert trunc == []

    # Cost math verification (deepseek-flash off-peak)
    c_miss = estimate_cost_usd("deepseek-flash", 100_000, 2_000, cache_hit_tokens=0)
    c_hit = estimate_cost_usd("deepseek-flash", 100_000, 2_000, cache_hit_tokens=100_000)
    assert c_hit < c_miss

    # Mock DeepSeek API response
    mock_session = MagicMock()
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "## Executive Verdict\nModel is slightly aggressive vs 15% guidance."}}],
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "prompt_cache_hit_tokens": 800,
        },
    }
    mock_session.post.return_value = mock_resp

    res = run_workflow_analysis(
        ticker="HUIZ",
        workflow_id="dcf_audit",
        files=files,
        api_key="sk-test-key",
        model="deepseek-flash",
        save_memo=True,
        data_root=tmp_path,
        session=mock_session,
    )
    assert "slightly aggressive" in res.content
    assert res.cache_hit_tokens == 800
    assert res.saved_path is not None and res.saved_path.exists()
    assert "Analysis" in res.saved_path.parts


def test_call_deepseek_handles_non_json_and_missing_key():
    with pytest.raises(ValueError, match="DeepSeek API key"):
        call_deepseek("", [{"role": "user", "content": "hi"}])

    bad_session = MagicMock()
    bad_resp = MagicMock(status_code=502)
    bad_resp.json.side_effect = ValueError("HTML gateway error")
    bad_session.post.return_value = bad_resp

    with pytest.raises(RuntimeError, match="non-JSON"):
        call_deepseek("sk-test", [{"role": "user", "content": "hi"}], session=bad_session)
