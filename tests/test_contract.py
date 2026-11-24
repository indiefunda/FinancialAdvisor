"""Opt-in live schema contract tests against upstream `defeatbeta-api`.

Run explicitly with:
    pytest -m slow -v

Skipped by default during `pytest -v` (configured in `pyproject.toml`).
Catches upstream DataFrame column renames before they cause silent mismatches.
"""

from __future__ import annotations

import pytest

from fundamentals import create_quiet_ticker


@pytest.mark.slow
def test_defeatbeta_dataframe_schema_contract():
    """Verify real DefeatBeta DataFrames expose the exact columns expected by fundamentals.py."""
    t = create_quiet_ticker("AAPL")

    rev = t.ttm_revenue()
    assert "ttm_total_revenue" in rev.columns
    assert "ttm_total_revenue_usd" in rev.columns

    fcf = t.ttm_fcf()
    assert "ttm_free_cash_flow" in fcf.columns

    nd = t.net_debt_ttm()
    assert "net_debt" in nd.columns or "net_debt_ttm" in nd.columns

    pe = t.ttm_pe()
    assert "ttm_pe" in pe.columns

    bd = t.trailing_revenue_by_breakdown()
    assert {"report_date", "breakdown_name", "series_name", "value", "currency"}.issubset(set(bd.columns))

    sec = t.sec_filing()
    assert {"filing_date", "form_type", "accession_number", "filing_url"}.issubset(set(sec.columns))
