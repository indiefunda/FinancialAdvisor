"""Unit tests for FinancialAdvisor core modules."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config_manager import (
    QuotaManager,
    load_api_keys,
    mask_key,
    parse_secrets_txt,
    save_api_keys,
)
from markdown_cleanup import cleanup_markdown_tables
from sec_downloader import (
    deduplicate_filename,
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


def test_cleanup_removes_duplicate_numeric_columns():
    raw = "\n".join([
        "| Metric | 2025 | 2025 |",
        "| --- | --- | --- |",
        "| Revenue | $1,234 | 1,234 |",
        "| EBITDA | $567 | 567 |",
    ])
    cleaned = cleanup_markdown_tables(raw)
    lines = cleaned.splitlines()
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


# ============================================================================
# 3. config_manager.py
# ============================================================================

def test_secrets_parsing_saving_loading_and_masking(tmp_path: Path, monkeypatch):
    txt = tmp_path / "Secrets.txt"
    txt.write_text(
        "roic.ai API KEY: test_roic_123456\n"
        "alphavantage.co API KEY: test_av_abcdef\n"
        "firecrawl.dev API KEY: fc-test9999\n",
        encoding="utf-8",
    )
    parsed = parse_secrets_txt(txt)
    assert parsed == {
        "roic": "test_roic_123456",
        "alphavantage": "test_av_abcdef",
        "firecrawl": "fc-test9999",
    }

    secrets_json = tmp_path / ".secrets.json"
    monkeypatch.delenv("ROIC_API_KEY", raising=False)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    loaded = load_api_keys(secrets_file=secrets_json, auto_import_paths=[txt])
    assert loaded == parsed
    assert secrets_json.exists()

    # Env var overrides saved file
    monkeypatch.setenv("ROIC_API_KEY", "env_override_key")
    loaded_env = load_api_keys(secrets_file=secrets_json)
    assert loaded_env["roic"] == "env_override_key"

    assert mask_key("test_roic_123456") == "te••••••3456"
    assert mask_key("") == "Not set"


def test_quota_manager_limits_and_rollover(tmp_path: Path):
    qfile = tmp_path / ".quota_state.json"
    qm = QuotaManager(quota_file=qfile)
    assert qm.alphavantage_remaining() == 25
    qm.record_alphavantage_call(5)
    assert qm.alphavantage_remaining() == 20

    # Simulate previous day -> should reset to 25
    qm.state["alphavantage"]["date"] = "2020-01-01"
    qm.save()
    qm2 = QuotaManager(quota_file=qfile)
    assert qm2.alphavantage_remaining() == 25

    qm2.record_firecrawl_credits(10)
    assert qm2.firecrawl_remaining() == 990


# ============================================================================
# 4. transcript_providers.py
# ============================================================================

def test_expected_quarters_and_local_cache(tmp_path: Path):
    qs = expected_quarters(2, today=date(2026, 11, 24))
    assert qs[0] == (2026, 4)
    assert qs[-1] == (2025, 1)
    assert len(qs) == 8

    rec = TranscriptRecord(
        ticker="AAPL",
        fiscal_year=2026,
        fiscal_quarter=3,
        provider="defeatbeta",
        markdown_body="A" * 300,
    )
    saved = save_transcript_record(rec, tmp_path)
    assert saved.name == "AAPL - Q3 2026.md"
    existing = scan_existing_quarters(tmp_path)
    assert (2026, 3) in existing


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
    assert mock_session.get.call_count == 1  # Stopped immediately on rate limit


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


def test_waterfall_uses_cache_then_providers_in_order(tmp_path: Path):
    qm = QuotaManager(quota_file=tmp_path / ".quota.json")
    # Pre-seed Q3 2026 in local cache
    tr_dir = tmp_path / "AAPL" / "EarningsTranscripts"
    save_transcript_record(
        TranscriptRecord("AAPL", 2026, 3, "cache", "Cached content " * 30),
        tr_dir,
    )

    def fake_db(ticker, cutoff_year, skip_quarters, log):
        assert (2026, 3) in skip_quarters  # Must skip cached quarter
        return [TranscriptRecord(ticker, 2026, 2, "defeatbeta", "DB body " * 40)], "Fetched 1"

    def fake_roic(self, ticker, cutoff_year, skip_quarters, log):
        assert (2026, 3) in skip_quarters
        assert (2026, 2) in skip_quarters
        return [TranscriptRecord(ticker, 2026, 4, "roic.ai", "ROIC body " * 40)], "Fetched 1"

    def fake_av(ticker, target_quarters, api_key, quota, log, **kwargs):
        assert (2026, 3) not in target_quarters
        assert (2026, 2) not in target_quarters
        assert (2026, 4) not in target_quarters
        return [TranscriptRecord(ticker, 2026, 1, "alphavantage", "AV body " * 40)], "Fetched 1"

    with patch("transcript_providers.fetch_from_defeatbeta", side_effect=fake_db), \
         patch.object(RoicClient, "fetch_missing", fake_roic), \
         patch("transcript_providers.fetch_from_alphavantage", side_effect=fake_av):
        res = download_transcripts(
            ticker="AAPL",
            num_years=1,
            providers=["defeatbeta", "roic", "alphavantage"],
            api_keys={"roic": "k1", "alphavantage": "k2"},
            quota_manager=qm,
            data_root=tmp_path,
        )

    assert (2026, 3) in res.cached_quarters
    saved_providers = {p for p, _ in res.saved_files}
    assert saved_providers == {"defeatbeta", "roic.ai", "alphavantage"}
    assert res.missing_quarters == []
