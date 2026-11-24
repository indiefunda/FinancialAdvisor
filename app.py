"""FinancialAdvisor — Streamlit UI for SEC Filings & Redundant Earnings Transcripts."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import streamlit as st

from config_manager import (
    DEFAULT_PROVIDER_ORDER,
    PROVIDER_SPECS,
    QuotaManager,
    load_api_keys,
    mask_key,
    save_api_keys,
)
from sec_downloader import (
    DEFAULT_DATA_ROOT,
    DEFAULT_FORM_CODES,
    FORM_CATEGORIES,
    download_and_convert_sec_filings,
    sanitize_ticker,
)
from transcript_providers import download_transcripts

st.set_page_config(
    page_title="FinancialAdvisor — SEC & Transcripts",
    page_icon="📊",
    layout="wide",
)

# ── Load local gitignored secrets & quota state ──────────────────────────────
api_keys: Dict[str, str] = load_api_keys()
quota = QuotaManager()

# ============================================================================
# SIDEBAR — API KEYS, REDUNDANCY ORDER & QUOTA MONITOR
# ============================================================================
with st.sidebar:
    st.header("⚙️ Settings & Redundancy")

    # ── API Key Status Summary ───────────────────────────────────────────────
    st.subheader("API Key Status")
    for p_id in ("roic", "alphavantage", "firecrawl"):
        spec = PROVIDER_SPECS[p_id]
        key_val = api_keys.get(p_id, "")
        badge = f"✅ `{mask_key(key_val)}`" if key_val else "⚪ Not configured"
        st.markdown(f"**{spec['label']}**: {badge}")

    # ── Click-to-Configure API Keys Panel ────────────────────────────────────
    with st.expander("🔑 Add / Update API Keys", expanded=False):
        st.caption(
            "Keys are saved locally to `.secrets.json` (gitignored) and never committed."
        )
        with st.form("api_keys_form", clear_on_submit=False):
            new_roic = st.text_input(
                "ROIC.ai API Key",
                value="",
                type="password",
                placeholder="Leave blank to keep existing key",
                help="Free tier: 5 req/min, latest 2 calls per company",
            )
            new_av = st.text_input(
                "Alpha Vantage API Key",
                value="",
                type="password",
                placeholder="Leave blank to keep existing key",
                help="Free tier: 25 req/day, full history since 2010Q1",
            )
            new_fc = st.text_input(
                "Firecrawl API Key",
                value="",
                type="password",
                placeholder="Leave blank to keep existing key",
                help="Free tier: 1,000 credits/month (fallback only)",
            )
            save_clicked = st.form_submit_button("💾 Save API Keys", type="primary")
            if save_clicked:
                updated = dict(api_keys)
                if new_roic.strip():
                    updated["roic"] = new_roic.strip()
                if new_av.strip():
                    updated["alphavantage"] = new_av.strip()
                if new_fc.strip():
                    updated["firecrawl"] = new_fc.strip()
                save_api_keys(updated)
                st.success("API keys saved to local `.secrets.json`.")
                st.rerun()

    st.divider()

    # ── Provider Redundancy Toggles ──────────────────────────────────────────
    st.subheader("Transcript Waterfall Order")
    st.caption("Local disk cache is always checked first (0 API calls).")
    enabled_providers: List[str] = []
    for idx, p_id in enumerate(DEFAULT_PROVIDER_ORDER, start=1):
        spec = PROVIDER_SPECS[p_id]
        checked = st.checkbox(
            f"{idx}. {spec['label']}",
            value=True,
            help=spec["limit_desc"],
            key=f"enable_{p_id}",
        )
        if checked:
            enabled_providers.append(p_id)

    st.divider()

    # ── Free-Tier Quota Monitor ──────────────────────────────────────────────
    st.subheader("Free-Tier Quota Tracker")
    q_summary = quota.summary()
    for p_id in ("roic", "alphavantage", "firecrawl"):
        info = q_summary[p_id]
        st.caption(f"**{PROVIDER_SPECS[p_id]['label']}**: {info['remaining']} remaining")
    if st.button("Reset local quota counters"):
        quota.reset()
        st.toast("Local quota counters reset.")
        st.rerun()

# ============================================================================
# MAIN PAGE HEADER & SHARED CONTROLS
# ============================================================================
st.title("📊 FinancialAdvisor — SEC Filings & Earnings Transcripts")
st.caption(
    "Download clean, LLM-ready SEC filings and earnings call transcripts (Prepared Remarks + Q&A) "
    "into `data/<TICKER>/`."
)

# Build form label lookups
form_display_labels: Dict[str, str] = {}
form_code_to_label: Dict[str, str] = {}
for _cat, _forms in FORM_CATEGORIES.items():
    for _code, _desc in _forms:
        _label = f"{_code} — {_desc}"
        form_display_labels[_code] = _label
        form_code_to_label[_label] = _code

all_form_labels = list(form_display_labels.values())
default_labels = [form_display_labels[c] for c in DEFAULT_FORM_CODES if c in form_display_labels]

tab_sec, tab_tr, tab_batch, tab_browse = st.tabs(
    [
        "📄 SEC Filings",
        "🎙️ Earnings Transcripts (Q&A)",
        "📦 Portfolio Batch (10 Stocks)",
        "📁 Browse Files",
    ]
)

# ============================================================================
# TAB 1: SEC FILINGS
# ============================================================================
with tab_sec:
    st.subheader("Download & Convert SEC EDGAR Filings")

    c1, c2 = st.columns([2, 1])
    with c1:
        sec_ticker = st.text_input(
            "Stock Ticker",
            value="",
            placeholder="e.g. AAPL, YB, HUIZ, MSFT",
            key="sec_ticker",
        ).strip().upper()
    with c2:
        sec_years = st.number_input(
            "Years of history",
            min_value=1,
            max_value=30,
            value=2,
            key="sec_years",
            help="Downloads only filings in this date range (fast & rate-limit friendly).",
        )

    selected_labels = st.multiselect(
        "SEC Form Types",
        options=all_form_labels,
        default=default_labels,
    )
    selected_forms = [form_code_to_label[lbl] for lbl in selected_labels]

    custom_forms = st.text_input(
        "Additional custom form types (comma-separated, e.g. 10-K/A, 424B4, DEF 14A)",
        value="",
    )
    if custom_forms.strip():
        extra = [f.strip().upper() for f in custom_forms.split(",") if f.strip()]
        selected_forms = list(dict.fromkeys(selected_forms + extra))

    if st.button("Download & Convert SEC Filings", type="primary"):
        if not sec_ticker:
            st.warning("Please enter a stock ticker first.")
        elif not selected_forms:
            st.warning("Select at least one form type.")
        else:
            try:
                clean_t = sanitize_ticker(sec_ticker)
            except ValueError as err:
                st.error(str(err))
            else:
                try:
                    with st.status(f"Downloading SEC filings for {clean_t}...", expanded=True) as status:
                        res = download_and_convert_sec_filings(
                            ticker=clean_t,
                            form_codes=selected_forms,
                            num_years=int(sec_years),
                            on_status=lambda m: status.write(m),
                        )
                        status.update(label="SEC download & conversion complete", state="complete")
                except Exception as exc:
                    st.error(f"SEC download failed: {exc}")
                else:
                    if res.converted_files:
                        st.success(
                            f"Found **{res.submissions_found}** submission(s) across "
                            f"{res.forms_succeeded}/{res.forms_requested} queried form types. "
                            f"Converted **{len(res.converted_files)}** cleaned Markdown documents "
                            f"(skipped {res.skipped_unparseable} unparseable)."
                        )
                    else:
                        st.warning(
                            f"Queried {res.forms_succeeded}/{res.forms_requested} form types, "
                            f"but 0 convertible filings were found for `{clean_t}` in the last "
                            f"{int(sec_years)} year(s)."
                        )
                    if res.form_errors:
                        with st.expander(f"⚠️ Warnings / Skipped Forms ({len(res.form_errors)})"):
                            for form, msg in res.form_errors.items():
                                st.warning(f"**{form}**: {msg}")
                    st.info(f"Markdown saved to: `{res.markdown_dir}`")


# ============================================================================
# TAB 2: EARNINGS CALL TRANSCRIPTS (REDUNDANT WATERFALL)
# ============================================================================
with tab_tr:
    st.subheader("Download Earnings Call Transcripts (Prepared Remarks + Q&A)")
    st.caption(
        "Automatically uses **Local Cache → DefeatBeta (unlimited) → ROIC.ai (latest 2 calls) "
        "→ Alpha Vantage (25/day) → Firecrawl (fallback)** to maximize your free tiers."
    )

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        tr_ticker = st.text_input(
            "Stock Ticker",
            value=st.session_state.get("sec_ticker", ""),
            placeholder="e.g. AAPL, NVDA, YB, HUIZ",
            key="tr_ticker",
        ).strip().upper()
    with c2:
        tr_years = st.number_input(
            "Years of history",
            min_value=1,
            max_value=15,
            value=2,
            key="tr_years",
        )
    with c3:
        force_refresh = st.checkbox(
            "Force refresh",
            value=False,
            help="Ignore local cache and re-download quarters.",
        )

    if st.button("Download Earnings Transcripts", type="primary"):
        if not tr_ticker:
            st.warning("Please enter a stock ticker first.")
        elif not enabled_providers:
            st.warning("Enable at least one transcript provider in the sidebar.")
        else:
            try:
                clean_t = sanitize_ticker(tr_ticker)
            except ValueError as err:
                st.error(str(err))
            else:
                with st.status(f"Fetching transcripts for {clean_t}...", expanded=True) as status:
                    res = download_transcripts(
                        ticker=clean_t,
                        num_years=int(tr_years),
                        providers=enabled_providers,
                        api_keys=api_keys,
                        quota_manager=quota,
                        force_refresh=force_refresh,
                        on_log=lambda m: status.write(m),
                    )
                    status.update(label="Transcript download complete", state="complete")

                m1, m2, m3 = st.columns(3)
                m1.metric("Newly Downloaded", len(res.saved_files))
                m2.metric("Reused from Cache", len(res.cached_quarters))
                m3.metric("Still Missing", len(res.missing_quarters))

                if res.provider_notes:
                    st.markdown("**Provider breakdown:**")
                    for prov, note in res.provider_notes.items():
                        st.markdown(f"- `{prov}`: {note}")

                if res.saved_files:
                    st.markdown("**Saved files:**")
                    for prov, path in res.saved_files:
                        st.markdown(f"- `{path.name}` *(via {prov})*")

                if res.missing_quarters:
                    missing_str = ", ".join(f"Q{q} {yr}" for yr, q in res.missing_quarters)
                    st.warning(f"Not yet available or outside free quota: {missing_str}")

                st.info(f"Transcripts folder: `{res.output_dir}`")


# ============================================================================
# TAB 3: PORTFOLIO BATCH MODE (BUILT FOR ~10-STOCK PORTFOLIOS)
# ============================================================================
with tab_batch:
    st.subheader("Portfolio Batch Downloader")
    st.caption(
        "Paste your watchlist (e.g. your 10 portfolio tickers). "
        "Already-downloaded quarters are skipped automatically so you never waste free API credits."
    )

    watchlist_raw = st.text_area(
        "Tickers (comma or newline separated)",
        placeholder="AAPL, MSFT, NVDA, YB, HUIZ",
        height=100,
    )
    b1, b2, b3 = st.columns(3)
    with b1:
        batch_years = st.number_input("Years of history", min_value=1, max_value=10, value=2, key="batch_years")
    with b2:
        do_sec = st.checkbox("Download SEC Filings", value=True)
    with b3:
        do_tr = st.checkbox("Download Transcripts", value=True)

    if st.button("Run Portfolio Batch", type="primary"):
        raw_tokens = [
            tok.strip().upper()
            for tok in watchlist_raw.replace("\n", ",").split(",")
            if tok.strip()
        ]
        tickers = list(dict.fromkeys(raw_tokens))
        if not tickers:
            st.warning("Enter at least one ticker.")
        elif not do_sec and not do_tr:
            st.warning("Select SEC Filings, Transcripts, or both.")
        else:
            progress = st.progress(0.0)
            for idx, tk in enumerate(tickers):
                try:
                    clean_tk = sanitize_ticker(tk)
                except ValueError as err:
                    st.error(f"Skipping `{tk}`: {err}")
                    continue

                with st.status(f"[{idx+1}/{len(tickers)}] Processing {clean_tk}...", expanded=False) as st_box:
                    if do_sec:
                        sec_res = download_and_convert_sec_filings(
                            ticker=clean_tk,
                            form_codes=DEFAULT_FORM_CODES,
                            num_years=int(batch_years),
                            on_status=lambda m: st_box.write(m),
                        )
                        st_box.write(f"SEC: {len(sec_res.converted_files)} Markdown files saved.")
                    if do_tr:
                        tr_res = download_transcripts(
                            ticker=clean_tk,
                            num_years=int(batch_years),
                            providers=enabled_providers,
                            api_keys=api_keys,
                            quota_manager=quota,
                            on_log=lambda m: st_box.write(m),
                        )
                        st_box.write(
                            f"Transcripts: {len(tr_res.saved_files)} new, "
                            f"{len(tr_res.cached_quarters)} cached, "
                            f"{len(tr_res.missing_quarters)} missing."
                        )
                    st_box.update(label=f"[{idx+1}/{len(tickers)}] {clean_tk} done", state="complete")
                progress.progress((idx + 1) / len(tickers))
            st.success(f"Batch complete for {len(tickers)} ticker(s)!")


# ============================================================================
# TAB 4: BROWSE DOWNLOADED MARKDOWN FILES
# ============================================================================
with tab_browse:
    st.subheader("Browse Downloaded Markdown Files")
    if not DEFAULT_DATA_ROOT.exists():
        st.info("No data downloaded yet. Use the SEC Filings or Transcripts tab to get started.")
    else:
        ticker_dirs = sorted([p for p in DEFAULT_DATA_ROOT.iterdir() if p.is_dir()])
        if not ticker_dirs:
            st.info("No ticker folders found in `data/`.")
        else:
            chosen_ticker = st.selectbox("Select Ticker", [p.name for p in ticker_dirs])
            t_root = DEFAULT_DATA_ROOT / chosen_ticker
            md_files: List[Path] = sorted(
                list((t_root / "EarningsTranscripts").glob("*.md"))
                + list((t_root / "DataFromDataMule_markdown").glob("*.md")),
                key=lambda p: (p.parent.name, p.name),
                reverse=True,
            )
            if not md_files:
                st.info(f"No Markdown files found for {chosen_ticker}.")
            else:
                labels = [f"{p.parent.name}/{p.name} ({p.stat().st_size // 1024} KB)" for p in md_files]
                selected_idx = st.selectbox("Select Document", range(len(labels)), format_func=lambda i: labels[i])
                doc_path = md_files[selected_idx]
                content = doc_path.read_text(encoding="utf-8", errors="replace")
                st.download_button(
                    "⬇️ Download Markdown",
                    data=content,
                    file_name=doc_path.name,
                    mime="text/markdown",
                )
                preview_limit = 25000
                if len(content) > preview_limit:
                    st.caption(f"Showing first {preview_limit:,} of {len(content):,} characters.")
                    st.markdown(content[:preview_limit] + "\n\n*... (truncated for preview)*")
                else:
                    st.markdown(content)
