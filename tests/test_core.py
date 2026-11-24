"""Unit tests for FinancialAdvisor core modules."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config_manager import (
    QuotaManager,
    load_api_keys,
    load_stored_api_keys,
    mask_key,
    parse_secrets_txt,
    save_api_keys,
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
    """Regression test for data-loss bug when data column precedes a ')' spacer."""
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
    """Sparse legitimate columns (e.g. Notes) must NOT be dropped."""
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
# 3. config_manager.py
# ============================================================================

def test_secrets_partial_merge_and_env_isolation(tmp_path: Path, monkeypatch):
    txt = tmp_path / "Secrets.txt"
    txt.write_text(
        "roic.ai API KEY: test_roic_123456\n"
        "alphavantage.co API KEY: test_av_abcdef\n"
        "firecrawl.dev API KEY: fc-test9999\n",
        encoding="utf-8",
    )
    secrets_json = tmp_path / ".secrets.json"
    # Pre-seed .secrets.json with ONLY roic -> Secrets.txt should still merge missing keys!
    save_api_keys({"roic": "custom_roic_9999"}, secrets_file=secrets_json)

    monkeypatch.delenv("ROIC_API_KEY", raising=False)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    stored = load_stored_api_keys(secrets_file=secrets_json, auto_import_paths=[txt])
    assert stored["roic"] == "custom_roic_9999"  # Existing key preserved
    assert stored["alphavantage"] == "test_av_abcdef"  # Missing key merged
    assert stored["firecrawl"] == "fc-test9999"  # Missing key merged

    # Env var overrides load_api_keys() but NOT load_stored_api_keys()
    monkeypatch.setenv("ROIC_API_KEY", "env_only_secret")
    effective = load_api_keys(secrets_file=secrets_json)
    assert effective["roic"] == "env_only_secret"
    assert load_stored_api_keys(secrets_file=secrets_json)["roic"] == "custom_roic_9999"

    assert mask_key("test_roic_123456") == "te••••••3456"
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
# 4. transcript_providers.py
# ============================================================================

def test_expected_quarters_excludes_in_progress_quarter():
    # On 2026-11-24 (during Q4 2026), latest completed quarter is Q3 2026
    qs = expected_quarters(2, today=date(2026, 11, 24))
    assert qs[0] == (2026, 3)
    assert (2026, 4) not in qs
    assert qs[-1] == (2025, 1)
    assert len(qs) == 7

    # On 2026-02-10 (during Q1 2026), latest completed quarter is Q4 2025
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
    """Regression test: HTTP 200 with HTML body must not raise ValueError."""
    qm = QuotaManager(quota_file=tmp_path / ".quota.json")
    html_resp = MagicMock(status_code=200)
    html_resp.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")

    mock_session = MagicMock()
    mock_session.get.return_value = html_resp
    mock_session.post.return_value = html_resp

    # ROIC.ai
    roic = RoicClient("dummy", qm, session=mock_session)
    recs, note = roic.fetch_missing("AAPL", 2025, set(), lambda _: None)
    assert recs == []
    assert "non-JSON" in note

    # Alpha Vantage
    recs_av, _ = fetch_from_alphavantage(
        "AAPL", [(2026, 3)], "dummy", qm, lambda _: None, session=mock_session
    )
    assert recs_av == []

    # Firecrawl
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
    """Passing providers=['roic', 'defeatbeta'] must call ROIC first, then DefeatBeta."""
    qm = QuotaManager(quota_file=tmp_path / ".quota.json")
    call_order = []

    def fake_roic(self, ticker, cutoff_year, skip_quarters, log):
        call_order.append("roic")
        return [TranscriptRecord(ticker, 2026, 3, "roic.ai", "ROIC body " * 40)], "Fetched 1"

    def fake_db(ticker, cutoff_year, skip_quarters, log):
        call_order.append("defeatbeta")
        assert (2026, 3) in skip_quarters  # Already fetched by ROIC first!
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
