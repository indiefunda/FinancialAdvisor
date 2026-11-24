"""FinancialAdvisor — Streamlit UI for SEC Filings, Transcripts, Fundamentals/DCF & DeepSeek AI."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import streamlit as st

from ai_analyst import (
    ANALYSIS_WORKFLOWS,
    DEEPSEEK_MODELS,
    SYSTEM_PROMPT_BASE,
    SYSTEM_PROMPT_VALUATION,
    VALUATION_PROTOCOL_PATH,
    ValuationParameters,
    build_dossier_context,
    call_deepseek_full,
    estimate_cost_usd,
    list_dossier_files,
    load_valuation_protocol,
    run_workflow_analysis,
    save_valuation_protocol,
)
from config_manager import (
    API_KEY_PROVIDERS,
    DEFAULT_PROVIDER_ORDER,
    PROVIDER_SPECS,
    QuotaManager,
    load_api_keys_with_sources,
    load_stored_api_keys,
    mask_key,
    save_api_keys,
)
from fundamentals import (
    build_portfolio_preflight,
    create_quiet_ticker,
    generate_company_brief,
    generate_peers_report,
    generate_sec_coverage_audit,
    generate_valuation_bundle,
    generate_valuation_pack,
)
from sec_downloader import (
    DEFAULT_DATA_ROOT,
    DEFAULT_FORM_CODES,
    FORM_CATEGORIES,
    download_and_convert_sec_filings,
    sanitize_ticker,
    strip_sec_metadata_header,
)
from transcript_providers import download_transcripts

st.set_page_config(
    page_title="FinancialAdvisor — Research & DeepSeek AI",
    page_icon="📊",
    layout="wide",
)

# ── Load local gitignored secrets & quota state ──────────────────────────────
api_keys, key_sources = load_api_keys_with_sources()
quota = QuotaManager()

# ============================================================================
# SIDEBAR — API KEYS, REDUNDANCY ORDER & QUOTA MONITOR
# ============================================================================
with st.sidebar:
    st.header("⚙️ Settings & Redundancy")

    # ── API Key Status Summary ───────────────────────────────────────────────
    st.subheader("API Key Status")
    for p_id in API_KEY_PROVIDERS:
        spec = PROVIDER_SPECS[p_id]
        key_val = api_keys.get(p_id, "")
        src = key_sources.get(p_id, "")
        if key_val:
            src_tag = " *(env)*" if src == "env" else " *(local)*"
            badge = f"✅ `{mask_key(key_val)}`{src_tag}"
        else:
            badge = "⚪ Not configured"
        st.markdown(f"**{spec['label']}**: {badge}")

    # ── Click-to-Configure API Keys Panel ────────────────────────────────────
    with st.expander("🔑 Add / Update API Keys", expanded=False):
        st.caption(
            "Keys are saved locally to `.secrets.json` (gitignored) and never committed. "
            "Environment-variable keys are never written to disk."
        )
        with st.form("api_keys_form", clear_on_submit=False):
            new_deepseek = st.text_input(
                "DeepSeek API Key",
                value="",
                type="password",
                placeholder="Leave blank to keep existing key",
                help="Powers the DeepSeek AI Analyst tab (1M context, V4.1-Flash / V4-Pro)",
            )
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
                stored = load_stored_api_keys()
                if new_deepseek.strip():
                    stored["deepseek"] = new_deepseek.strip()
                if new_roic.strip():
                    stored["roic"] = new_roic.strip()
                if new_av.strip():
                    stored["alphavantage"] = new_av.strip()
                if new_fc.strip():
                    stored["firecrawl"] = new_fc.strip()
                save_api_keys(stored)
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
# MAIN PAGE HEADER & TABS
# ============================================================================
st.title("📊 FinancialAdvisor — Equity Research Workbench")
st.caption(
    "SEC EDGAR filings + redundant earnings transcripts + DefeatBeta fundamentals/DCF + "
    "DeepSeek AI cross-examination in `data/<TICKER>/`."
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

tab_sec, tab_tr, tab_fund, tab_ai, tab_batch, tab_browse = st.tabs(
    [
        "📄 SEC Filings",
        "🎙️ Transcripts (Q&A)",
        "📈 Fundamentals & DCF",
        "🧠 DeepSeek AI Analyst",
        "📦 Portfolio Batch",
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
            placeholder="e.g. AAPL, YB, HUIZ, JFIN, LU, UPST",
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

    b_sec1, b_sec2 = st.columns([2, 1])
    with b_sec1:
        run_sec_btn = st.button("Download & Convert SEC Filings", type="primary")
    with b_sec2:
        run_cov_btn = st.button("🔍 Audit Coverage vs. EDGAR Index")

    if run_sec_btn:
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

    if run_cov_btn:
        if not sec_ticker:
            st.warning("Please enter a stock ticker first.")
        else:
            try:
                clean_t = sanitize_ticker(sec_ticker)
                with st.status(f"Auditing SEC coverage for {clean_t}...", expanded=True) as status:
                    cov = generate_sec_coverage_audit(
                        clean_t,
                        num_years=int(sec_years),
                        on_status=lambda m: status.write(m),
                    )
                    status.update(label="Coverage audit complete", state="complete")
                m1, m2, m3 = st.columns(3)
                m1.metric("All-Time Indexed", cov.total_indexed)
                m2.metric(f"In Last {int(sec_years)} Yr(s)", cov.recent_indexed)
                m3.metric("Local Markdown Files", cov.local_markdown_count)
                if cov.notable_extra_forms:
                    st.info(
                        "Notable insider/ownership filings detected: "
                        + ", ".join(f"`{k}` ({v})" for k, v in sorted(cov.notable_extra_forms.items()))
                    )
                st.markdown(cov.path.read_text(encoding="utf-8"))
            except Exception as exc:
                st.error(f"Coverage audit failed: {exc}")


# ============================================================================
# TAB 2: EARNINGS CALL TRANSCRIPTS (REDUNDANT WATERFALL + CATALOG ORACLE)
# ============================================================================
with tab_tr:
    st.subheader("Download Earnings Call Transcripts (Prepared Remarks + Q&A)")
    st.caption(
        "Automatically uses **Local Cache → DefeatBeta (unlimited + catalog oracle) → ROIC.ai (latest 2 calls) "
        "→ Alpha Vantage (25/day) → Firecrawl (fallback)** to maximize free tiers."
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

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Newly Downloaded", len(res.saved_files))
                m2.metric("Reused from Cache", len(res.cached_quarters))
                m3.metric("Unavailable Upstream", len(res.unavailable_upstream))
                m4.metric("Still Missing", len(res.missing_quarters))

                if res.provider_notes:
                    st.markdown("**Provider breakdown:**")
                    for prov, note in res.provider_notes.items():
                        st.markdown(f"- `{prov}`: {note}")

                if res.saved_files:
                    st.markdown("**Saved files:**")
                    for prov, path in res.saved_files:
                        st.markdown(f"- `{path.name}` *(via {prov})*")

                if res.unavailable_upstream:
                    unav_str = ", ".join(f"Q{q} {yr}" for yr, q in res.unavailable_upstream)
                    st.caption(f"Unavailable upstream (e.g. pre-IPO / no call recorded — skipped to save quota): {unav_str}")

                if res.missing_quarters:
                    missing_str = ", ".join(f"Q{q} {yr}" for yr, q in res.missing_quarters)
                    st.warning(f"Not yet available or outside free quota: {missing_str}")

                st.info(f"Transcripts folder: `{res.output_dir}`")


# ============================================================================
# TAB 3: FUNDAMENTALS, PEERS & DCF VALUATION (100% FREE VIA DEFEATBETA)
# ============================================================================
with tab_fund:
    st.subheader("Fundamentals Brief, Peers & DCF Valuation (Free — DefeatBeta)")
    st.caption(
        "Generates deterministic, currency-labelled `CompanyBrief.md`, `Peers.md`, "
        "`Valuation/DCF_Summary.md`, and an editable Excel DCF workbook (`<TICKER>_DCF.xlsx`) "
        "— ready for the DeepSeek AI Analyst tab."
    )

    f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
    with f1:
        fund_ticker = st.text_input(
            "Stock Ticker",
            value=st.session_state.get("sec_ticker", ""),
            placeholder="e.g. AAPL, YB, HUIZ, JFIN, UPST",
            key="fund_ticker",
        ).strip().upper()
    with f2:
        gen_brief = st.checkbox("Company Brief", value=True)
    with f3:
        gen_peers = st.checkbox("Peers Comparison", value=True)
    with f4:
        gen_dcf = st.checkbox("DCF + Excel", value=True)

    if st.button("Generate Fundamentals & Valuation Pack", type="primary"):
        if not fund_ticker:
            st.warning("Please enter a stock ticker first.")
        elif not (gen_brief or gen_peers or gen_dcf):
            st.warning("Select at least one report to generate.")
        else:
            try:
                clean_t = sanitize_ticker(fund_ticker)
                with st.status(f"Building DefeatBeta intelligence pack for {clean_t}...", expanded=True) as status:
                    t_obj = create_quiet_ticker(clean_t)
                    brief_res = None
                    peers_path = None
                    val_res = None

                    if gen_brief:
                        brief_res = generate_company_brief(
                            clean_t, ticker_obj=t_obj, on_status=lambda m: status.write(m)
                        )
                    if gen_peers:
                        peers_path = generate_peers_report(
                            clean_t, ticker_obj=t_obj, on_status=lambda m: status.write(m)
                        )
                    if gen_dcf:
                        val_res = generate_valuation_bundle(
                            clean_t, ticker_obj=t_obj, generate_excel=True, on_status=lambda m: status.write(m)
                        )
                    status.update(label=f"Intelligence pack ready for {clean_t}", state="complete")

                if brief_res:
                    st.success(
                        f"Saved `CompanyBrief.md` (Reporting Currency: `{brief_res.currency}` | "
                        f"Next/Last Earnings: `{brief_res.next_earnings_date}`)"
                    )
                    with st.expander("📄 Preview CompanyBrief.md", expanded=True):
                        st.markdown(brief_res.path.read_text(encoding="utf-8"))

                if peers_path:
                    with st.expander("⚖️ Preview Peers.md", expanded=False):
                        st.markdown(peers_path.read_text(encoding="utf-8"))

                if val_res:
                    if val_res.warnings:
                        for w in val_res.warnings:
                            st.warning(w)
                    if val_res.excel_path and val_res.excel_path.exists():
                        st.download_button(
                            f"⬇️ Download Editable DCF Excel ({val_res.excel_path.name})",
                            data=val_res.excel_path.read_bytes(),
                            file_name=val_res.excel_path.name,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    with st.expander("💰 Preview DCF_Summary.md", expanded=True):
                        st.markdown(val_res.summary_md_path.read_text(encoding="utf-8"))

            except Exception as exc:
                st.error(f"Failed to generate fundamentals pack: {exc}")


# ============================================================================
# TAB 4: DEEPSEEK AI ANALYST (1M CONTEXT + CACHED GROUNDED RESEARCH)
# ============================================================================
with tab_ai:
    st.subheader("🧠 DeepSeek AI Equity Analyst")
    st.caption(
        "Grounds DeepSeek (`deepseek-flash` or `deepseek-v4-pro`, 1M context) strictly in your "
        "local dossier (`CompanyBrief.md`, `DCF_Summary.md`, `Peers.md`, transcripts, SEC filings)."
    )

    deepseek_key = api_keys.get("deepseek", "").strip()
    if not deepseek_key:
        st.warning(
            "DeepSeek API key not found yet. Add it in the sidebar under **🔑 Add / Update API Keys** "
            "(or add `deepseek API KEY: sk-...` to your local gitignored `Secrets.txt`)."
        )

    available_tickers = (
        sorted([p.name for p in DEFAULT_DATA_ROOT.iterdir() if p.is_dir()])
        if DEFAULT_DATA_ROOT.exists()
        else []
    )

    a1, a2, a3 = st.columns([1, 1, 2])
    with a1:
        ticker_options = available_tickers + ["➕ Enter new ticker..."]
        picked = st.selectbox("Select Ticker Dossier", ticker_options, key="ai_ticker_pick")
    with a2:
        if picked == "➕ Enter new ticker...":
            raw_new_tk = st.text_input("New Ticker Symbol", value="HUIZ", key="ai_new_ticker")
            ai_ticker = sanitize_ticker(raw_new_tk) if raw_new_tk.strip() else ""
        else:
            ai_ticker = picked
            st.text_input("Active Ticker", value=ai_ticker, disabled=True, key="ai_active_ticker_ro")
    with a3:
        ai_model = st.selectbox(
            "DeepSeek Model",
            options=list(DEEPSEEK_MODELS.keys()),
            format_func=lambda m: DEEPSEEK_MODELS[m]["label"],
            index=0,
            key="ai_model",
        )

    if not ai_ticker:
        st.info("Select an existing ticker or enter a new ticker symbol above.")
    else:
        # ── One-Button Valuation Dataset Builder ─────────────────────────────
        t_root_ai = DEFAULT_DATA_ROOT / ai_ticker
        has_vpack = (t_root_ai / "ValuationPack.md").exists()
        has_brief = (t_root_ai / "CompanyBrief.md").exists()
        has_peers = (t_root_ai / "Peers.md").exists()
        sec_md_count = len(list((t_root_ai / "DataFromDataMule_markdown").glob("*.md"))) if (t_root_ai / "DataFromDataMule_markdown").exists() else 0
        tr_md_count = len(list((t_root_ai / "EarningsTranscripts").glob("*.md"))) if (t_root_ai / "EarningsTranscripts").exists() else 0

        prep_c1, prep_c2, prep_c3 = st.columns([2, 1, 2])
        with prep_c1:
            prep_btn = st.button(
                f"📦 Prepare Valuation Dataset for {ai_ticker}",
                key=f"prep_ds_{ai_ticker}",
                help="One-click: builds ValuationPack.md (price, live FX, 8Q+4Y statements, 10Y multiple band), CompanyBrief.md, Peers.md, latest SEC filings & latest 4 transcripts — skipping anything already on disk.",
            )
        with prep_c2:
            force_refresh_ds = st.checkbox("Force refresh", value=False, key=f"force_ds_{ai_ticker}")
        with prep_c3:
            st.caption(
                f"On disk: ValuationPack {'✅' if has_vpack else '—'} | Brief {'✅' if has_brief else '—'} | "
                f"Peers {'✅' if has_peers else '—'} | SEC: {sec_md_count} | Transcripts: {tr_md_count}"
            )

        if prep_btn:
            prep_home_ccy = st.session_state.get("vp_home_ccy", "USD") or "USD"
            with st.status(f"Preparing Valuation Dataset for {ai_ticker}...", expanded=True) as prep_st:
                t_holder: List[object] = [None]

                def _get_ticker_obj():
                    if t_holder[0] is None:
                        t_holder[0] = create_quiet_ticker(ai_ticker)
                    return t_holder[0]

                # 1. ValuationPack.md (Price, Spot FX, 8Q+4Y statements, 10Y multiple band)
                if force_refresh_ds or not has_vpack:
                    try:
                        vp_res = generate_valuation_pack(
                            ai_ticker,
                            home_currency=prep_home_ccy,
                            ticker_obj=_get_ticker_obj(),
                            on_status=lambda m: prep_st.write(m),
                        )
                        prep_st.write(
                            f"✅ Saved `ValuationPack.md` (Price: {vp_res.latest_price or 'n/a'}, "
                            f"FX {vp_res.currency}->{vp_res.home_currency}: {vp_res.fx_rate or 'n/a'})"
                        )
                    except Exception as exc:
                        prep_st.warning(f"ValuationPack error: {exc}")
                else:
                    prep_st.write("⏭️ `ValuationPack.md` already exists (skipped).")

                # 2. CompanyBrief.md & Peers.md
                if force_refresh_ds or not has_brief:
                    try:
                        generate_company_brief(ai_ticker, ticker_obj=_get_ticker_obj(), on_status=lambda m: prep_st.write(m))
                        prep_st.write("✅ Saved `CompanyBrief.md`")
                    except Exception as exc:
                        prep_st.warning(f"CompanyBrief error: {exc}")
                else:
                    prep_st.write("⏭️ `CompanyBrief.md` already exists (skipped).")

                if force_refresh_ds or not has_peers:
                    try:
                        generate_peers_report(ai_ticker, ticker_obj=_get_ticker_obj(), on_status=lambda m: prep_st.write(m))
                        prep_st.write("✅ Saved `Peers.md`")
                    except Exception as exc:
                        prep_st.warning(f"Peers error: {exc}")
                else:
                    prep_st.write("⏭️ `Peers.md` already exists (skipped).")

                # 3. Latest Annual + Quarterly SEC Filings (only if missing or force refresh)
                if force_refresh_ds or sec_md_count == 0:
                    try:
                        sec_res = download_and_convert_sec_filings(
                            ticker=ai_ticker,
                            form_codes=["10-K", "20-F", "10-Q", "6-K", "8-K"],
                            num_years=2,
                            on_status=lambda m: prep_st.write(m),
                        )
                        prep_st.write(f"✅ SEC Filings: {len(sec_res.converted_files)} Markdown file(s) saved.")
                    except Exception as exc:
                        prep_st.warning(f"SEC download error: {exc}")
                else:
                    prep_st.write(f"⏭️ SEC filings already on disk ({sec_md_count} files — skipped).")

                # 4. Latest 4 Earnings Transcripts (only if fewer than 4 on disk or force refresh)
                if force_refresh_ds or tr_md_count < 4:
                    try:
                        tr_res = download_transcripts(
                            ticker=ai_ticker,
                            num_years=2,
                            providers=enabled_providers,
                            api_keys=api_keys,
                            quota_manager=quota,
                            on_log=lambda m: prep_st.write(m),
                        )
                        prep_st.write(
                            f"✅ Transcripts: {len(tr_res.saved_files)} new, {len(tr_res.cached_quarters)} cached, "
                            f"{len(tr_res.unavailable_upstream)} unavailable upstream."
                        )
                    except Exception as exc:
                        prep_st.warning(f"Transcript download error: {exc}")
                else:
                    prep_st.write(f"⏭️ Transcripts already on disk ({tr_md_count} files — skipped).")

                prep_st.update(label=f"Valuation Dataset ready for {ai_ticker}!", state="complete")
            st.rerun()

        dossier_files = list_dossier_files(ai_ticker)
        if not dossier_files:
            st.info(
                f"No Markdown files found in `data/{ai_ticker}/` yet. Click **📦 Prepare Valuation Dataset for {ai_ticker}** above to fetch everything in one click."
            )
        else:
            # Smart default selection: ValuationPack + Brief + Peers + up to 4 transcripts + 2 newest SEC filings
            default_selected = []
            tr_count = 0
            sec_count = 0
            for df in dossier_files:
                if df.category in ("Summary", "Valuation", "Peers"):
                    default_selected.append(df.rel_label)
                elif df.category == "Transcript" and tr_count < 4:
                    default_selected.append(df.rel_label)
                    tr_count += 1
                elif df.category == "SEC Filing" and sec_count < 2:
                    default_selected.append(df.rel_label)
                    sec_count += 1
            if not default_selected:
                default_selected = [dossier_files[0].rel_label]

            file_map = {df.rel_label: df for df in dossier_files}
            chosen_labels = st.multiselect(
                "Attach Local Files to DeepSeek Context (1M-token window)",
                options=list(file_map.keys()),
                default=default_selected,
                help="Attaching ValuationPack.md + CompanyBrief.md + Peers.md + recent SEC filings (10-K/20-F/10-Q) + 4 transcripts gives DeepSeek everything needed for your 5-Year Hurdle-Rate Valuation.",
            )
            chosen_files = [file_map[lbl] for lbl in chosen_labels]

            # Live token & cost preview before calling DeepSeek
            total_bytes = sum(f.size_bytes for f in chosen_files)
            est_in_tokens = max(1, total_bytes // 4) if chosen_files else 0
            est_cost_miss = estimate_cost_usd(ai_model, est_in_tokens, 6000, cache_hit_tokens=0)
            est_cost_hit = estimate_cost_usd(ai_model, est_in_tokens, 6000, cache_hit_tokens=est_in_tokens)
            st.caption(
                f"Attached: **{len(chosen_files)} file(s)** (~{est_in_tokens:,} input tokens) | "
                f"Est. off-peak cost: **${est_cost_miss:.4f}** (1st run) / **${est_cost_hit:.4f}** (cached follow-up)"
            )

            # ── Custom Valuation Parameters & Editable Protocol ──────────────
            has_foreign_filing = any(
                ("20-F" in df.rel_label.upper() or "6-K" in df.rel_label.upper())
                for df in dossier_files
            )
            with st.expander("⚙️ 5-Year Hurdle-Rate Valuation Parameters & Editable Protocol", expanded=False):
                st.caption(
                    f"Active protocol file: `{VALUATION_PROTOCOL_PATH.name}` — edit parameters per run or "
                    "modify the full Markdown protocol below to evolve your methodology over time."
                )
                vp1, vp2, vp3 = st.columns(3)
                with vp1:
                    hurdle_rate = st.number_input("Target Hurdle Rate H (%)", 1.0, 50.0, 15.0, 0.5, key="vp_hurdle")
                    home_ccy = st.text_input("Investor Home Currency", value="USD", key="vp_home_ccy")
                    div_wht = st.number_input("Dividend Withholding Tax (%)", 0.0, 50.0, 10.0, 1.0, key="vp_wht")
                with vp2:
                    spot_price_ov = st.text_input(
                        "Current Price Override (optional)",
                        placeholder="Auto-pulled in ValuationPack.md or override here",
                        key=f"vp_price_{ai_ticker}",
                    )
                    spot_fx_ov = st.text_input(
                        "Spot FX Rate Override (optional)",
                        placeholder="Auto-pulled in ValuationPack.md or override here",
                        key=f"vp_fx_{ai_ticker}",
                    )
                    adr_fee = st.text_input("Annual ADR/Custody Fee (Home CCY/sh)", value="0.00", key=f"vp_adr_{ai_ticker}")
                with vp3:
                    exit_cgt = st.number_input("Exit Capital-Gains Tax (%)", 0.0, 50.0, 0.0, 1.0, key="vp_cgt")
                    max_impair = st.number_input("Worst-State Portfolio Loss Cap (%)", 0.5, 25.0, 2.5, 0.5, key="vp_impair")
                    single_cap = st.number_input("Single-Stock Concentration Cap (%)", 1.0, 50.0, 15.0, 1.0, key="vp_cap")

                cross_border = st.checkbox(
                    "Activate Optional Cross-Border / Emerging Market / VIE Risk Module",
                    value=has_foreign_filing,
                    key=f"vp_cb_{ai_ticker}",
                    help="Auto-checked when 20-F or 6-K filings are detected in the dossier.",
                )
                custom_run_notes = st.text_area(
                    "Per-Run Analyst Notes / Live Peer Multiples (optional)",
                    placeholder="e.g. Peer median EV/NOPAT = 11.5x (Peers: X, Y, Z [Market Data: Koyfin, 2026-11-24]). Use GAAP SBC basis.",
                    height=70,
                    key=f"vp_notes_{ai_ticker}",
                )

                st.markdown("---")
                st.markdown("**Edit Valuation Protocol (`valuation_protocol.md`)**")
                current_protocol = load_valuation_protocol()
                edited_protocol = st.text_area(
                    "Full Protocol Markdown (changes apply to all future valuation runs)",
                    value=current_protocol,
                    height=260,
                    key="vp_protocol_editor",
                )
                if st.button("💾 Save Valuation Protocol", key="save_vp_protocol"):
                    try:
                        save_valuation_protocol(edited_protocol)
                        st.success(f"Saved updated protocol to `{VALUATION_PROTOCOL_PATH.name}`!")
                    except Exception as exc:
                        st.error(f"Could not save protocol: {exc}")

            val_params = ValuationParameters(
                hurdle_rate_pct=float(hurdle_rate),
                home_currency=home_ccy,
                spot_price=spot_price_ov,
                spot_fx_rate=spot_fx_ov,
                dividend_withholding_tax_pct=float(div_wht),
                adr_custody_fee=adr_fee,
                exit_cgt_pct=float(exit_cgt),
                max_impairment_pct=float(max_impair),
                single_stock_cap_pct=float(single_cap),
                activate_cross_border_module=bool(cross_border),
                custom_notes=custom_run_notes,
            )

            st.divider()

            # ── Part A: One-Click Institutional Workflows ────────────────────
            st.markdown("### ⚡ One-Click DeepSeek Workflows")
            wf_cols = st.columns(2)
            wf_items = list(ANALYSIS_WORKFLOWS.items())
            save_memo_opt = st.checkbox("Auto-save memo to `data/<TICKER>/Analysis/`", value=True)

            triggered_wf = None
            for idx, (wf_id, wf_meta) in enumerate(wf_items):
                with wf_cols[idx % 2]:
                    btn_type = "primary" if wf_id == "scientific_valuation" else "secondary"
                    if st.button(wf_meta["label"], key=f"wf_{wf_id}", help=wf_meta["short_desc"], type=btn_type):
                        triggered_wf = wf_id
                    st.caption(wf_meta["short_desc"])

            if triggered_wf:
                if not deepseek_key:
                    st.error("Please configure your DeepSeek API key in the sidebar first.")
                elif not chosen_files:
                    st.warning("Select at least one dossier file above.")
                else:
                    progress_box = st.empty()

                    def _on_ds_progress(phase: str, r_chars: int, c_chars: int) -> None:
                        if phase == "thinking":
                            progress_box.info(
                                f"🧠 DeepSeek (`{ai_model}`) is reasoning through the protocol... "
                                f"({r_chars:,} reasoning chars so far — please wait)"
                            )
                        else:
                            progress_box.info(
                                f"✍️ DeepSeek (`{ai_model}`) is writing the final report... "
                                f"({c_chars:,} output chars | {r_chars:,} reasoning chars)"
                            )

                    try:
                        progress_box.info(
                            f"⏳ Sending {len(chosen_files)} file(s) to DeepSeek (`{ai_model}`, max_tokens=65,536)..."
                        )
                        res = run_workflow_analysis(
                            ticker=ai_ticker,
                            workflow_id=triggered_wf,
                            files=chosen_files,
                            api_key=deepseek_key,
                            model=ai_model,
                            valuation_params=val_params if triggered_wf == "scientific_valuation" else None,
                            protocol_text=edited_protocol if triggered_wf == "scientific_valuation" else None,
                            stream=True,
                            on_progress=_on_ds_progress,
                            save_memo=save_memo_opt,
                        )
                        progress_box.empty()
                        st.session_state["last_ai_result"] = res
                    except Exception as exc:
                        progress_box.empty()
                        st.error(f"DeepSeek analysis failed: {exc}")

            last_res = st.session_state.get("last_ai_result")
            if last_res and last_res.ticker == ai_ticker:
                st.success(
                    f"Completed via `{last_res.model}` | "
                    f"Tokens: {last_res.prompt_tokens:,} in ({last_res.cache_hit_tokens:,} cached) / "
                    f"{last_res.completion_tokens:,} out | Est. cost: **${last_res.estimated_cost_usd:.4f}**"
                )
                if last_res.finish_reason == "length":
                    st.warning("Output hit the token cap (`finish_reason=length`). You can ask DeepSeek in the chat below to continue from where it stopped.")
                if last_res.saved_path:
                    st.caption(f"Saved memo: `{last_res.saved_path}`")
                act1, act2 = st.columns([1, 1])
                with act1:
                    st.download_button(
                        "⬇️ Download Analysis Memo (.md)",
                        data=last_res.content,
                        file_name=f"{last_res.ticker}_{last_res.workflow_id}.md",
                        mime="text/markdown",
                    )
                with act2:
                    if st.button(
                        "💬 Continue & Tweak in Chat Below",
                        key=f"seed_chat_{ai_ticker}_{last_res.workflow_id}",
                        help="Copies this analysis into the prompt-cached chat so you can challenge assumptions or re-run scenarios.",
                    ):
                        chat_key_seed = f"deepseek_chat_{ai_ticker}"
                        st.session_state[chat_key_seed] = [
                            {"role": "assistant", "content": last_res.content},
                        ]
                        st.session_state[f"deepseek_chat_mode_{ai_ticker}"] = last_res.workflow_id
                        st.rerun()
                if last_res.reasoning:
                    with st.expander(f"🧠 View DeepSeek Internal Reasoning ({len(last_res.reasoning):,} chars)", expanded=False):
                        st.markdown(last_res.reasoning)
                st.markdown(last_res.content)

            st.divider()

            # ── Part B: Interactive Grounded Dossier Chat ────────────────────
            st.markdown(f"### 💬 Ask DeepSeek About `{ai_ticker}` (Prompt-Cached Chat)")
            chat_key = f"deepseek_chat_{ai_ticker}"
            if chat_key not in st.session_state:
                st.session_state[chat_key] = []

            if st.button("Clear chat history", key=f"clear_{chat_key}"):
                st.session_state[chat_key] = []
                st.session_state.pop(f"deepseek_chat_mode_{ai_ticker}", None)
                st.rerun()

            for msg in st.session_state[chat_key]:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            user_q = st.chat_input(f"Ask anything about {ai_ticker} or tweak valuation assumptions...")
            if user_q:
                if not deepseek_key:
                    st.error("Please configure your DeepSeek API key in the sidebar first.")
                elif not chosen_files:
                    st.warning("Attach at least one dossier file above.")
                else:
                    with st.chat_message("user"):
                        st.markdown(user_q)

                    dossier_text, _ = build_dossier_context(chosen_files)
                    chat_mode = st.session_state.get(f"deepseek_chat_mode_{ai_ticker}")
                    chat_sys = SYSTEM_PROMPT_VALUATION if chat_mode == "scientific_valuation" else SYSTEM_PROMPT_BASE
                    candidate_history = st.session_state[chat_key] + [{"role": "user", "content": user_q}]
                    messages = [
                        {
                            "role": "system",
                            "content": f"{chat_sys}\n\n# DOSSIER FOR {ai_ticker}\n\n{dossier_text}",
                        }
                    ] + candidate_history

                    with st.chat_message("assistant"):
                        chat_prog = st.empty()

                        def _on_chat_progress(phase: str, r_chars: int, c_chars: int) -> None:
                            if phase == "thinking":
                                chat_prog.caption(f"🧠 Thinking... ({r_chars:,} chars)")
                            else:
                                chat_prog.caption(f"✍️ Writing... ({c_chars:,} chars)")

                        try:
                            answer, chat_reasoning, _fin, usage = call_deepseek_full(
                                api_key=deepseek_key,
                                messages=messages,
                                model=ai_model,
                                max_tokens=65536 if chat_mode == "scientific_valuation" else 16384,
                                stream=True,
                                on_progress=_on_chat_progress,
                            )
                            chat_prog.empty()
                            if chat_reasoning:
                                with st.expander(f"🧠 View Reasoning ({len(chat_reasoning):,} chars)", expanded=False):
                                    st.markdown(chat_reasoning)
                            st.markdown(answer)
                            c_usd = estimate_cost_usd(
                                ai_model,
                                usage["prompt_tokens"],
                                usage["completion_tokens"],
                                usage["cache_hit_tokens"],
                            )
                            st.caption(
                                f"Tokens: {usage['prompt_tokens']:,} in ({usage['cache_hit_tokens']:,} cached) / "
                                f"{usage['completion_tokens']:,} out | Est. cost: ${c_usd:.4f}"
                            )
                            st.session_state[chat_key] = candidate_history + [{"role": "assistant", "content": answer}]
                        except Exception as exc:
                            chat_prog.empty()
                            st.error(f"DeepSeek chat error: {exc}")


# ============================================================================
# TAB 5: PORTFOLIO BATCH MODE + PREFLIGHT MATRIX (BUILT FOR ~10 STOCKS)
# ============================================================================
with tab_batch:
    st.subheader("Portfolio Command Center & Batch Downloader")
    st.caption(
        "Paste your watchlist (e.g. your 10 portfolio tickers). Run **Preflight** first (free, 0 API credits) "
        "to see upcoming earnings dates and transcript availability before downloading."
    )

    watchlist_raw = st.text_area(
        "Tickers (comma or newline separated)",
        placeholder="AAPL, MSFT, YB, HUIZ, JFIN, LU, UPST",
        height=90,
    )
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        batch_years = st.number_input("Years of history", min_value=1, max_value=10, value=2, key="batch_years")
    with b2:
        do_sec = st.checkbox("SEC Filings", value=True)
    with b3:
        do_tr = st.checkbox("Transcripts", value=True)
    with b4:
        do_fund = st.checkbox("Brief + Peers + DCF", value=True)

    raw_tokens = [
        tok.strip().upper()
        for tok in watchlist_raw.replace("\n", ",").split(",")
        if tok.strip()
    ]
    tickers = list(dict.fromkeys(raw_tokens))

    btn_col1, btn_col2 = st.columns([1, 1])
    with btn_col1:
        preflight_btn = st.button("🔎 Run Free Preflight (Calendar + Coverage Matrix)")
    with btn_col2:
        batch_btn = st.button("🚀 Run Portfolio Batch", type="primary")

    if preflight_btn:
        if not tickers:
            st.warning("Enter at least one ticker.")
        else:
            try:
                with st.spinner("Querying DefeatBeta parallel catalog & earnings calendar (free)..."):
                    cal_rows, matrix_rows = build_portfolio_preflight(tickers, num_years=int(batch_years))
                if cal_rows:
                    st.markdown("#### 📅 Portfolio Readiness & Earnings Calendar")
                    st.dataframe(cal_rows, hide_index=True)
                if matrix_rows:
                    st.markdown("#### 🗂️ Transcript Coverage Matrix (Before Spending API Quota)")
                    st.dataframe(matrix_rows, hide_index=True)
            except Exception as exc:
                st.error(f"Preflight failed: {exc}")

    if batch_btn:
        if not tickers:
            st.warning("Enter at least one ticker.")
        elif not (do_sec or do_tr or do_fund):
            st.warning("Select at least one action (SEC, Transcripts, or Fundamentals).")
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
                        try:
                            sec_res = download_and_convert_sec_filings(
                                ticker=clean_tk,
                                form_codes=DEFAULT_FORM_CODES,
                                num_years=int(batch_years),
                                on_status=lambda m: st_box.write(m),
                            )
                            st_box.write(
                                f"SEC: {sec_res.submissions_found} submission(s), "
                                f"{len(sec_res.converted_files)} Markdown files saved."
                            )
                        except Exception as exc:
                            st_box.error(f"SEC error for {clean_tk}: {exc}")
                    if do_tr:
                        try:
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
                                f"{len(tr_res.unavailable_upstream)} unavailable upstream, "
                                f"{len(tr_res.missing_quarters)} missing."
                            )
                        except Exception as exc:
                            st_box.error(f"Transcript error for {clean_tk}: {exc}")
                    if do_fund:
                        try:
                            t_obj = create_quiet_ticker(clean_tk)
                            generate_company_brief(clean_tk, ticker_obj=t_obj, on_status=lambda m: st_box.write(m))
                            generate_peers_report(clean_tk, ticker_obj=t_obj, on_status=lambda m: st_box.write(m))
                            generate_valuation_bundle(
                                clean_tk, ticker_obj=t_obj, generate_excel=True, on_status=lambda m: st_box.write(m)
                            )
                            st_box.write("Fundamentals: CompanyBrief.md, Peers.md & Valuation/ saved.")
                        except Exception as exc:
                            st_box.error(f"Fundamentals error for {clean_tk}: {exc}")
                    st_box.update(label=f"[{idx+1}/{len(tickers)}] {clean_tk} done", state="complete")
                progress.progress((idx + 1) / len(tickers))
            st.success(f"Batch complete for {len(tickers)} ticker(s)!")


# ============================================================================
# TAB 6: BROWSE DOWNLOADED MARKDOWN & AI MEMO FILES
# ============================================================================
with tab_browse:
    st.subheader("Browse Local Research Dossier")
    if not DEFAULT_DATA_ROOT.exists():
        st.info("No data downloaded yet. Use the tabs above to build your first ticker dossier.")
    else:
        ticker_dirs = sorted([p for p in DEFAULT_DATA_ROOT.iterdir() if p.is_dir()])
        if not ticker_dirs:
            st.info("No ticker folders found in `data/`.")
        else:
            chosen_ticker = st.selectbox("Select Ticker", [p.name for p in ticker_dirs], key="browse_ticker")
            t_root = DEFAULT_DATA_ROOT / chosen_ticker

            md_files: List[Path] = []
            for top_name in ("ValuationPack.md", "CompanyBrief.md", "Peers.md", "Coverage.md"):
                tp = t_root / top_name
                if tp.exists():
                    md_files.append(tp)
            for sub in ("Analysis", "Valuation", "EarningsTranscripts", "DataFromDataMule_markdown"):
                sub_dir = t_root / sub
                if sub_dir.exists():
                    md_files.extend(sorted(sub_dir.glob("*.md"), reverse=True))

            # Offer Excel DCF download if present
            xlsx_path = t_root / "Valuation" / f"{chosen_ticker}_DCF.xlsx"
            if xlsx_path.exists():
                st.download_button(
                    f"📊 Download DCF Excel ({xlsx_path.name})",
                    data=xlsx_path.read_bytes(),
                    file_name=xlsx_path.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

            if not md_files:
                st.info(f"No Markdown files found for {chosen_ticker}.")
            else:
                labels = [
                    f"{p.relative_to(t_root).as_posix()} ({max(1, p.stat().st_size // 1024)} KB)"
                    for p in md_files
                ]
                selected_idx = st.selectbox(
                    "Select Document",
                    range(len(labels)),
                    format_func=lambda i: labels[i],
                )
                doc_path = md_files[selected_idx]
                content = doc_path.read_text(encoding="utf-8", errors="replace")
                st.download_button(
                    "⬇️ Download Markdown",
                    data=content,
                    file_name=doc_path.name,
                    mime="text/markdown",
                )
                preview_content = strip_sec_metadata_header(content)
                preview_limit = 30000
                if len(preview_content) > preview_limit:
                    st.caption(f"Showing first {preview_limit:,} of {len(preview_content):,} characters.")
                    st.markdown(preview_content[:preview_limit] + "\n\n*... (truncated for preview)*")
                else:
                    st.markdown(preview_content)
