"""Deterministic Python financial math engine for Protocol V6 (USD-Based, CAPM/WACC-Free).

Exposed to DeepSeek via OpenAI-compatible function calling (`VALUATION_TOOLS`) so the model
offloads all iterative dated-cash-flow IRR solves, 1-pass non-circular buybacks, 3×3
cross-grids, ±10 pp probability sensitivities, break-even growth solves, and asymmetry ratios
to exact Python math instead of burning reasoning tokens on manual trial-and-error loops.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ============================================================================
# 1. EXACT DATED CASH-FLOW IRR & RETURN PRIMITIVES
# ============================================================================

def solve_irr(cash_flows: Sequence[float]) -> Optional[float]:
    """Solve exact annualized IRR for `[CF_0, CF_1, ..., CF_N]` with `CF_0 < 0`.

    Protocol V6 Section 4.4 rule: if all post-entry cash flows are <= 0 (zero recovery
    and zero dividends), returns `-1.0` (-100% IRR) rather than an undefined error.
    """
    cfs = [float(x) for x in cash_flows]
    if len(cfs) < 2 or cfs[0] >= 0:
        return None

    # Trim trailing exact zeros only after the last positive flow? Keep index timing intact!
    if all(c <= 0.0 for c in cfs[1:]):
        return -1.0

    def _npv(rate: float) -> float:
        base = 1.0 + rate
        if base <= 0.0:
            return float("inf")
        total = 0.0
        for t, cf in enumerate(cfs):
            total += cf / (base ** t)
        return total

    # Bracket root on (-0.999999, hi)
    lo = -0.999999
    hi = 2.0
    while _npv(hi) > 0.0 and hi < 1000.0:
        hi *= 2.0

    if _npv(lo) < 0.0 or _npv(hi) > 0.0:
        return None

    for _ in range(120):
        mid = 0.5 * (lo + hi)
        val = _npv(mid)
        if abs(val) < 1e-11:
            return mid
        if val > 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def compute_irr_and_returns(cash_flows: Sequence[float]) -> Dict[str, Any]:
    """Compute exact MOIC, Total Return %, Annualized IRR %, and $10,000 proceeds for a CF vector."""
    cfs = [float(x) for x in cash_flows]
    if len(cfs) < 2 or cfs[0] >= 0:
        return {"error": "cash_flows must start with negative entry price CF_0 < 0 and have >= 2 entries."}

    entry = abs(cfs[0])
    total_inflows = sum(cfs[1:])
    moic = max(0.0, total_inflows / entry)
    tot_ret_pct = (moic - 1.0) * 100.0
    irr = solve_irr(cfs)
    return {
        "entry_usd": round(entry, 4),
        "total_inflows_usd": round(total_inflows, 4),
        "moic": round(moic, 4),
        "total_return_pct": round(tot_ret_pct, 2),
        "irr_pct": round(irr * 100.0, 2) if irr is not None else None,
        "ten_k_proceeds_usd": round(10_000.0 * moic, 2),
    }


# ============================================================================
# 2. STANDARD OPERATING COMPANY 5-YEAR MODEL + 3×3 GRID + BREAK-EVEN SOLVE
# ============================================================================

def _expand_5(val: Any, default: float = 0.0) -> List[float]:
    if val is None:
        return [float(default)] * 5
    if isinstance(val, (int, float)):
        return [float(val)] * 5
    seq = [float(x) for x in val]
    if not seq:
        return [float(default)] * 5
    if len(seq) < 5:
        seq = seq + [seq[-1]] * (5 - len(seq))
    return seq[:5]


def _simulate_operating_path(
    *,
    entry_price_usd: float,
    starting_diluted_shares_m: float,
    starting_excess_cash_m: float,
    starting_non_op_investments_m: float,
    starting_debt_m: float,
    nci_m: float,
    preferred_m: float,
    cash_haircut: float,
    investment_haircut: float,
    tax_rate: float,
    sales_to_capital: float,
    after_tax_cash_yield: float,
    after_tax_debt_rate: float,
    dividend_wht: float,
    annual_adr_fee_usd: float,
    year0_revenue_m: float,
    rev_growths: Sequence[float],
    ebit_margins: Sequence[float],
    terminal_g: float,
    exit_multiple: float,
    gross_div_per_share: Optional[Sequence[float]],
    div_payout_of_nopat: Optional[float],
    annual_buyback_m: float,
    annual_debt_change_m: float,
) -> Dict[str, Any]:
    """Run exact 5-year operating, cash/debt roll-forward, 1-pass buyback, and dated IRR calculation."""
    revs: List[float] = []
    cur_rev = float(year0_revenue_m)
    for g in rev_growths:
        cur_rev = cur_rev * (1.0 + g)
        revs.append(cur_rev)
    rev6 = revs[-1] * (1.0 + terminal_g)

    ebits: List[float] = []
    nopats: List[float] = []
    reinvests: List[float] = []
    fcfs: List[float] = []
    for idx in range(5):
        ebit = revs[idx] * ebit_margins[idx]
        nopat = ebit * (1.0 - tax_rate)
        next_rev = revs[idx + 1] if idx < 4 else rev6
        delta_rev = max(next_rev - revs[idx], 0.0)
        reinv = delta_rev / sales_to_capital if sales_to_capital > 0 else 0.0
        fcf = nopat - reinv
        ebits.append(ebit)
        nopats.append(nopat)
        reinvests.append(reinv)
        fcfs.append(fcf)

    def _roll_cash(buyback_per_yr: float, shares_for_div: float) -> Tuple[float, float, float, float, List[float]]:
        cash = float(starting_excess_cash_m)
        debt = float(starting_debt_m)
        sum_interest = 0.0
        sum_gross_div = 0.0
        net_divs_per_share: List[float] = []

        for idx in range(5):
            # Beginning-of-year cash earns interest; debt pays interest
            net_int = max(cash, 0.0) * after_tax_cash_yield - max(debt, 0.0) * after_tax_debt_rate
            if gross_div_per_share is not None:
                gross_div_ps = max(0.0, float(gross_div_per_share[idx]))
                gross_div_total = gross_div_ps * shares_for_div
            elif div_payout_of_nopat is not None and div_payout_of_nopat > 0:
                gross_div_total = max(0.0, nopats[idx] * div_payout_of_nopat)
                gross_div_ps = gross_div_total / shares_for_div
            else:
                gross_div_total = 0.0
                gross_div_ps = 0.0

            net_div_ps = max(0.0, gross_div_ps * (1.0 - dividend_wht) - annual_adr_fee_usd)
            net_divs_per_share.append(net_div_ps)

            cash = cash + fcfs[idx] + net_int - gross_div_total - buyback_per_yr + annual_debt_change_m
            debt = max(0.0, debt + annual_debt_change_m)
            sum_interest += net_int
            sum_gross_div += gross_div_total

        return cash, debt, sum_interest, sum_gross_div, net_divs_per_share

    def _terminal_equity_and_tv(y5_cash: float, y5_debt: float, shares_m: float) -> Tuple[float, float, float, float]:
        ev_y5 = nopats[-1] * exit_multiple
        credited_cash = max(y5_cash, 0.0) * cash_haircut + min(y5_cash, 0.0)
        eq_bridge = (
            credited_cash
            + (starting_non_op_investments_m * investment_haircut)
            - y5_debt
            - nci_m
            - preferred_m
        )
        equity_val = max(0.0, ev_y5 + eq_bridge)
        tv_ps = equity_val / shares_m if shares_m > 0 else 0.0
        return ev_y5, credited_cash, equity_val, tv_ps

    # Pass 1: Zero buybacks -> initial Year-5 TV/share for 1-pass midpoint buyback price
    cash_zb, debt_zb, _, _, _ = _roll_cash(0.0, starting_diluted_shares_m)
    _, _, _, tv_zb = _terminal_equity_and_tv(cash_zb, debt_zb, starting_diluted_shares_m)

    # Pass 2 (1-pass non-circular buyback rule): midpoint repurchase price
    total_buybacks = max(0.0, annual_buyback_m) * 5.0
    avg_buyback_price = 0.5 * (entry_price_usd + tv_zb)
    retired_shares_m = (total_buybacks / avg_buyback_price) if (total_buybacks > 0 and avg_buyback_price > 0) else 0.0
    final_shares_m = max(starting_diluted_shares_m * 0.25, starting_diluted_shares_m - retired_shares_m)

    y5_cash, y5_debt, sum_int, sum_gdiv, net_divs_ps = _roll_cash(max(0.0, annual_buyback_m), starting_diluted_shares_m)
    ev_y5, credited_cash, eq_y5, tv_ps = _terminal_equity_and_tv(y5_cash, y5_debt, final_shares_m)

    cfs = [-entry_price_usd] + net_divs_ps[:4] + [net_divs_ps[4] + tv_ps]
    ret = compute_irr_and_returns(cfs)

    yearly = []
    for idx in range(5):
        yearly.append({
            "year": idx + 1,
            "revenue_m": round(revs[idx], 2),
            "ebit_m": round(ebits[idx], 2),
            "nopat_m": round(nopats[idx], 2),
            "reinvestment_m": round(reinvests[idx], 2),
            "unlevered_fcf_m": round(fcfs[idx], 2),
            "net_div_per_share_usd": round(net_divs_ps[idx], 4),
        })

    return {
        "yearly": yearly,
        "year5_revenue_m": round(revs[-1], 2),
        "year5_nopat_m": round(nopats[-1], 2),
        "sum_unlevered_fcf_m": round(sum(fcfs), 2),
        "sum_after_tax_interest_m": round(sum_int, 2),
        "sum_gross_dividends_m": round(sum_gdiv, 2),
        "sum_buybacks_m": round(total_buybacks, 2),
        "year5_excess_cash_m": round(y5_cash, 2),
        "credited_cash_m": round(credited_cash, 2),
        "year5_debt_m": round(y5_debt, 2),
        "year5_ev_m": round(ev_y5, 2),
        "year5_equity_m": round(eq_y5, 2),
        "zero_buyback_tv_usd": round(tv_zb, 4),
        "avg_buyback_price_usd": round(avg_buyback_price, 4),
        "retired_shares_m": round(retired_shares_m, 4),
        "year5_diluted_shares_m": round(final_shares_m, 4),
        "terminal_price_usd": round(tv_ps, 4),
        "net_dividends_y1_to_y5_usd": [round(x, 4) for x in net_divs_ps],
        "cash_flows_usd": [round(x, 4) for x in cfs],
        "moic": ret["moic"],
        "total_return_pct": ret["total_return_pct"],
        "irr_pct": ret["irr_pct"],
        "ten_k_proceeds_usd": ret["ten_k_proceeds_usd"],
    }


def compute_operating_valuation_model(
    *,
    entry_price_usd: float,
    starting_diluted_shares_m: float,
    starting_excess_cash_m: float,
    year0_revenue_m: float,
    tax_rate: float,
    sales_to_capital: float,
    cash_haircut: float,
    scenarios: Dict[str, Dict[str, Any]],
    starting_non_op_investments_m: float = 0.0,
    starting_debt_m: float = 0.0,
    nci_m: float = 0.0,
    preferred_m: float = 0.0,
    investment_haircut: Optional[float] = None,
    after_tax_cash_yield: float = 0.015,
    after_tax_debt_rate: float = 0.04,
    dividend_wht: float = 0.10,
    annual_adr_fee_usd: float = 0.0,
    ronic: Optional[float] = None,
    required_return_r: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute Bear/Base/Bull 5Y tables, exact 9-cell 3×3 cross-grid, trailing formula check & break-even growth."""
    inv_hc = cash_haircut if investment_haircut is None else investment_haircut
    common = dict(
        entry_price_usd=float(entry_price_usd),
        starting_diluted_shares_m=float(starting_diluted_shares_m),
        starting_excess_cash_m=float(starting_excess_cash_m),
        starting_non_op_investments_m=float(starting_non_op_investments_m),
        starting_debt_m=float(starting_debt_m),
        nci_m=float(nci_m),
        preferred_m=float(preferred_m),
        cash_haircut=float(cash_haircut),
        investment_haircut=float(inv_hc),
        tax_rate=float(tax_rate),
        sales_to_capital=float(sales_to_capital),
        after_tax_cash_yield=float(after_tax_cash_yield),
        after_tax_debt_rate=float(after_tax_debt_rate),
        dividend_wht=float(dividend_wht),
        annual_adr_fee_usd=float(annual_adr_fee_usd),
        year0_revenue_m=float(year0_revenue_m),
    )

    parsed_scens: Dict[str, Dict[str, Any]] = {}
    scen_results: Dict[str, Dict[str, Any]] = {}
    for name in ("bear", "base", "bull"):
        sc = scenarios.get(name) or {}
        g_list = _expand_5(sc.get("revenue_growth_y1_to_y5"), 0.05)
        m_list = _expand_5(sc.get("ebit_margins_y1_to_y5"), 0.15)
        term_g = float(sc.get("terminal_g", 0.03))
        exit_m = float(sc.get("exit_multiple", 10.0))
        gdiv_raw = sc.get("annual_gross_dividends_per_share_usd")
        gdiv_list = _expand_5(gdiv_raw, 0.0) if gdiv_raw is not None else None
        payout = float(sc["dividend_payout_pct_of_nopat"]) if sc.get("dividend_payout_pct_of_nopat") is not None else None
        bb = float(sc.get("annual_buyback_usd_m", 0.0))
        d_debt = float(sc.get("annual_debt_change_m", 0.0))

        parsed_scens[name] = dict(
            rev_growths=g_list,
            ebit_margins=m_list,
            terminal_g=term_g,
            exit_multiple=exit_m,
            gross_div_per_share=gdiv_list,
            div_payout_of_nopat=payout,
            annual_buyback_m=bb,
            annual_debt_change_m=d_debt,
        )
        scen_results[name] = _simulate_operating_path(**common, **parsed_scens[name])

    # Exact 3×3 Cross-Grid (all 9 cells recalculated with row scenario + col multiple + 1-pass buybacks!)
    col_multiples = {c: parsed_scens[c]["exit_multiple"] for c in ("bear", "base", "bull")}
    grid_irr_pct: Dict[str, Dict[str, Optional[float]]] = {}
    grid_tv_usd: Dict[str, Dict[str, float]] = {}
    for r_name in ("bear", "base", "bull"):
        grid_irr_pct[r_name] = {}
        grid_tv_usd[r_name] = {}
        for c_name in ("bear", "base", "bull"):
            cell_cfg = dict(parsed_scens[r_name])
            cell_cfg["exit_multiple"] = col_multiples[c_name]
            cell_res = _simulate_operating_path(**common, **cell_cfg)
            grid_irr_pct[r_name][f"{c_name}_mult_{col_multiples[c_name]:.2f}x"] = cell_res["irr_pct"]
            grid_tv_usd[r_name][f"{c_name}_mult_{col_multiples[c_name]:.2f}x"] = cell_res["terminal_price_usd"]

    # Phase 1.8 Break-even 5-year revenue/NOPAT CAGR solve for 0% IRR at Base exit multiple
    base_cfg = parsed_scens["base"]
    base_margin_const = [base_cfg["ebit_margins"][-1]] * 5

    def _irr_at_cagr(cagr: float) -> float:
        cfg = dict(base_cfg)
        cfg["rev_growths"] = [cagr] * 5
        cfg["ebit_margins"] = base_margin_const
        res_c = _simulate_operating_path(**common, **cfg)
        return float(res_c["irr_pct"] if res_c["irr_pct"] is not None else -100.0)

    breakeven_note = ""
    breakeven_cagr_pct: Optional[float] = None
    irr_low = _irr_at_cagr(-0.60)
    irr_high = _irr_at_cagr(0.80)
    if irr_low > 0.0:
        breakeven_note = (
            f"Even at -60%/yr contraction, IRR remains {irr_low:.1f}% > 0% due to net cash / dividends "
            "(break-even growth is negative / below -60% CAGR)."
        )
    elif irr_high < 0.0:
        breakeven_note = f"Break-even 0% IRR requires >80% CAGR (at +80% CAGR, IRR is {irr_high:.1f}%)."
    else:
        lo_g, hi_g = -0.60, 0.80
        for _ in range(45):
            mid_g = 0.5 * (lo_g + hi_g)
            if _irr_at_cagr(mid_g) > 0.0:
                hi_g = mid_g
            else:
                lo_g = mid_g
        breakeven_cagr_pct = round(0.5 * (lo_g + hi_g) * 100.0, 2)

    # Trailing Gordon cross-check at r ± 200 bps (if ronic & r supplied)
    formula_check: Optional[Dict[str, Any]] = None
    if ronic is not None and required_return_r is not None and ronic > 0:
        g_b = base_cfg["terminal_g"]
        r_mid = float(required_return_r)
        band = {}
        for lbl, r_val in (("r_minus_200bps", r_mid - 0.02), ("r_base", r_mid), ("r_plus_200bps", r_mid + 0.02)):
            if r_val > g_b:
                mult = ((1.0 + g_b) * (1.0 - g_b / float(ronic))) / (r_val - g_b)
                band[lbl] = {"r_pct": round(r_val * 100.0, 2), "implied_ev_nopat": round(mult, 2)}
            else:
                band[lbl] = {"r_pct": round(r_val * 100.0, 2), "implied_ev_nopat": None}
        formula_check = band

    return {
        "scenarios": scen_results,
        "grid_3x3_irr_pct": grid_irr_pct,
        "grid_3x3_terminal_price_usd": grid_tv_usd,
        "breakeven_0pct_irr_cagr_pct": breakeven_cagr_pct,
        "breakeven_note": breakeven_note,
        "trailing_formula_crosscheck": formula_check,
    }


# ============================================================================
# 3. FINANCIAL / BANK / BROKER P/B × ROE MODEL + 3×3 GRID
# ============================================================================

def compute_pb_grid(
    *,
    entry_price_usd: float,
    starting_book_equity_m: float,
    diluted_shares_m: float,
    scenarios: Dict[str, Dict[str, Any]],
    required_return_r: float = 0.12,
    dividend_wht: float = 0.10,
    annual_adr_fee_usd: float = 0.0,
) -> Dict[str, Any]:
    """Deterministic 5-year P/B + Normalized ROE model & 9-cell 3×3 cross-grid for banks/brokers/insurers."""
    scen_out: Dict[str, Dict[str, Any]] = {}
    parsed: Dict[str, Dict[str, Any]] = {}

    for name in ("bear", "base", "bull"):
        sc = scenarios.get(name) or {}
        roe = float(sc.get("roe", 0.12))
        payout = float(sc.get("payout_ratio", 0.0))
        term_g = float(sc.get("terminal_g", 0.03))
        exit_pb = float(sc.get("exit_pb", max(0.3, (roe - term_g) / max(0.01, required_return_r - term_g))))
        annual_buyback_m = float(sc.get("annual_buyback_usd_m", 0.0))

        # Roll book equity forward 5 years
        book = float(starting_book_equity_m)
        net_divs: List[float] = []
        for _ in range(5):
            ni = book * roe
            gross_div_tot = max(0.0, ni * payout)
            gross_div_ps = gross_div_tot / diluted_shares_m
            net_divs.append(max(0.0, gross_div_ps * (1.0 - dividend_wht) - annual_adr_fee_usd))
            book = book + ni - gross_div_tot - annual_buyback_m

        # 1-pass buyback adjustment
        tv_zb = max(0.0, book * exit_pb) / diluted_shares_m
        avg_bb_p = 0.5 * (entry_price_usd + tv_zb)
        tot_bb = max(0.0, annual_buyback_m) * 5.0
        retired = (tot_bb / avg_bb_p) if (tot_bb > 0 and avg_bb_p > 0) else 0.0
        fin_shares = max(diluted_shares_m * 0.25, diluted_shares_m - retired)
        tv_ps = max(0.0, book * exit_pb) / fin_shares

        cfs = [-entry_price_usd] + net_divs[:4] + [net_divs[4] + tv_ps]
        ret = compute_irr_and_returns(cfs)

        parsed[name] = {
            "year5_book_m": book,
            "exit_pb": exit_pb,
            "net_divs": net_divs,
            "annual_buyback_m": annual_buyback_m,
            "roe": roe,
            "terminal_g": term_g,
        }
        scen_out[name] = {
            "roe_pct": round(roe * 100.0, 2),
            "exit_pb": round(exit_pb, 3),
            "year5_book_m": round(book, 2),
            "year5_diluted_shares_m": round(fin_shares, 4),
            "terminal_price_usd": round(tv_ps, 4),
            "net_dividends_y1_to_y5_usd": [round(x, 4) for x in net_divs],
            "cash_flows_usd": [round(x, 4) for x in cfs],
            "moic": ret["moic"],
            "total_return_pct": ret["total_return_pct"],
            "irr_pct": ret["irr_pct"],
            "ten_k_proceeds_usd": ret["ten_k_proceeds_usd"],
        }

    # Exact 9-cell 3×3 P/B grid
    grid_irr: Dict[str, Dict[str, Optional[float]]] = {}
    grid_tv: Dict[str, Dict[str, float]] = {}
    for r_name in ("bear", "base", "bull"):
        grid_irr[r_name] = {}
        grid_tv[r_name] = {}
        row_b = parsed[r_name]["year5_book_m"]
        row_divs = parsed[r_name]["net_divs"]
        row_bb = parsed[r_name]["annual_buyback_m"]
        for c_name in ("bear", "base", "bull"):
            pb_c = parsed[c_name]["exit_pb"]
            tv_zb = max(0.0, row_b * pb_c) / diluted_shares_m
            avg_p = 0.5 * (entry_price_usd + tv_zb)
            retired = ((row_bb * 5.0) / avg_p) if (row_bb > 0 and avg_p > 0) else 0.0
            fin_sh = max(diluted_shares_m * 0.25, diluted_shares_m - retired)
            tv_c = max(0.0, row_b * pb_c) / fin_sh
            cfs_c = [-entry_price_usd] + row_divs[:4] + [row_divs[4] + tv_c]
            ret_c = compute_irr_and_returns(cfs_c)
            col_key = f"{c_name}_pb_{pb_c:.3f}x"
            grid_irr[r_name][col_key] = ret_c["irr_pct"]
            grid_tv[r_name][col_key] = round(tv_c, 4)

    # Justified P/B sensitivity at r ± 200 bps
    pb_sens = {}
    for lbl, r_val in (("r_minus_200bps", required_return_r - 0.02), ("r_base", required_return_r), ("r_plus_200bps", required_return_r + 0.02)):
        pb_sens[lbl] = {
            "r_pct": round(r_val * 100.0, 2),
            **{
                n: round((parsed[n]["roe"] - parsed[n]["terminal_g"]) / (r_val - parsed[n]["terminal_g"]), 3)
                if r_val > parsed[n]["terminal_g"] else None
                for n in ("bear", "base", "bull")
            },
        }

    return {
        "scenarios": scen_out,
        "grid_3x3_irr_pct": grid_irr,
        "grid_3x3_terminal_price_usd": grid_tv,
        "justified_pb_sensitivity": pb_sens,
    }


# ============================================================================
# 4. PHASE 4 & 5 FOUR-STATE PROBABILITY, EXPECTED IRR, ±10PP & ASYMMETRY
# ============================================================================

def compute_four_state_summary(
    *,
    entry_price_usd: float,
    catastrophe: Dict[str, Any],
    bear: Dict[str, Any],
    base: Dict[str, Any],
    bull: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute Phase 4–5 distribution stats, dated Expected IRR, ±10 pp shift & Asymmetry Ratio."""
    entry = float(entry_price_usd)
    if entry <= 0:
        return {"error": "entry_price_usd must be > 0."}

    states_in = {
        "catastrophe": catastrophe,
        "bear": bear,
        "base": base,
        "bull": bull,
    }
    probs = {k: float(v["probability_pct"]) / 100.0 for k, v in states_in.items()}
    prob_sum = sum(probs.values())
    if abs(prob_sum - 1.0) > 1e-4:
        return {"error": f"Probabilities must sum to 100% (got {prob_sum * 100:.2f}%)."}

    def _build_cf_vector(name: str, cfg: Dict[str, Any]) -> Tuple[List[float], int]:
        vec = [-entry, 0.0, 0.0, 0.0, 0.0, 0.0]
        if name == "catastrophe":
            t_cat = max(1, min(5, int(cfg.get("event_year", 2))))
            prior_divs = [float(x) for x in (cfg.get("prior_net_dividends_usd") or [])]
            for idx, d in enumerate(prior_divs):
                yr = idx + 1
                if yr <= t_cat:
                    vec[yr] += max(0.0, d)
            vec[t_cat] += max(0.0, float(cfg.get("recovery_per_share_usd", 0.0)))
            return vec, t_cat
        else:
            divs = _expand_5(cfg.get("net_dividends_y1_to_y5_usd"), 0.0)
            for idx in range(5):
                vec[idx + 1] += max(0.0, divs[idx])
            vec[5] += max(0.0, float(cfg.get("terminal_price_usd", 0.0)))
            return vec, 5

    cf_vectors: Dict[str, List[float]] = {}
    scen_stats: Dict[str, Dict[str, Any]] = {}
    for name, cfg in states_in.items():
        vec, horizon = _build_cf_vector(name, cfg)
        cf_vectors[name] = vec
        # For catastrophe, trim vector at t_cat for scenario IRR over actual realization horizon
        irr_vec = vec[: horizon + 1] if name == "catastrophe" else vec
        ret = compute_irr_and_returns(irr_vec)
        scen_stats[name] = {
            "probability_pct": round(probs[name] * 100.0, 2),
            "horizon_year": horizon,
            "terminal_or_recovery_usd": round(
                float(cfg.get("recovery_per_share_usd", 0.0) if name == "catastrophe" else cfg.get("terminal_price_usd", 0.0)),
                4,
            ),
            "moic": ret["moic"],
            "total_return_pct": ret["total_return_pct"],
            "irr_pct": ret["irr_pct"],
            "ten_k_proceeds_usd": ret["ten_k_proceeds_usd"],
            "cash_flows_usd": [round(x, 4) for x in vec],
        }

    def _weighted_summary(p_map: Dict[str, float]) -> Dict[str, Any]:
        w_cf = [-entry, 0.0, 0.0, 0.0, 0.0, 0.0]
        for name, p in p_map.items():
            for t in range(1, 6):
                w_cf[t] += p * cf_vectors[name][t]
        w_ret = compute_irr_and_returns(w_cf)
        w_moic = sum(p_map[n] * scen_stats[n]["moic"] for n in p_map)
        return {
            "weighted_cash_flows_usd": [round(x, 4) for x in w_cf],
            "expected_moic": round(w_moic, 4),
            "expected_total_return_pct": round((w_moic - 1.0) * 100.0, 2),
            "expected_irr_pct": w_ret["irr_pct"],
            "expected_ten_k_proceeds_usd": round(10_000.0 * w_moic, 2),
        }

    expected = _weighted_summary(probs)

    # Base ±10 pp sensitivity (proportional reallocation between Bear & Catastrophe, Bull unchanged)
    def _shift_base(delta: float) -> Dict[str, Any]:
        p_base_new = min(1.0 - probs["bull"], max(0.0, probs["base"] + delta))
        actual_delta = p_base_new - probs["base"]
        bc_sum = probs["bear"] + probs["catastrophe"]
        if bc_sum > 0:
            rem = max(0.0, bc_sum - actual_delta)
            p_bear_new = rem * (probs["bear"] / bc_sum)
            p_cat_new = rem * (probs["catastrophe"] / bc_sum)
        else:
            p_bear_new = max(0.0, -actual_delta)
            p_cat_new = 0.0
        p_new = {
            "catastrophe": p_cat_new,
            "bear": p_bear_new,
            "base": p_base_new,
            "bull": probs["bull"],
        }
        out = _weighted_summary(p_new)
        out["probabilities_pct"] = {k: round(v * 100.0, 2) for k, v in p_new.items()}
        return out

    sens_minus10 = _shift_base(-0.10)
    sens_plus10 = _shift_base(+0.10)

    # Distribution stats ordered by MOIC ascending
    ordered = sorted(scen_stats.items(), key=lambda kv: kv[1]["moic"])
    cum = 0.0
    median_state = ordered[-1][0]
    fifth_pct_state = ordered[0][0]
    found_5th = False
    found_med = False
    for name, st in ordered:
        cum += probs[name]
        if not found_5th and cum >= 0.05 - 1e-9:
            fifth_pct_state = name
            found_5th = True
        if not found_med and cum >= 0.50 - 1e-9:
            median_state = name
            found_med = True

    prob_loss_pct = round(sum(probs[n] for n in scen_stats if scen_stats[n]["moic"] < 1.0) * 100.0, 2)

    exp_gain = sum(probs[n] * max(scen_stats[n]["moic"] - 1.0, 0.0) for n in scen_stats)
    exp_loss = sum(probs[n] * max(1.0 - scen_stats[n]["moic"], 0.0) for n in scen_stats)
    asymmetry_ratio: Any = round(exp_gain / exp_loss, 2) if exp_loss > 1e-12 else "No modeled capital-loss state"

    return {
        "scenarios": scen_stats,
        "expected": expected,
        "base_prob_minus10_pp": sens_minus10,
        "base_prob_plus10_pp": sens_plus10,
        "distribution": {
            "prob_capital_loss_pct": prob_loss_pct,
            "median_outcome": median_state.title(),
            "fifth_percentile_outcome": fifth_pct_state.title(),
            "expected_gain": round(exp_gain, 4),
            "expected_loss": round(exp_loss, 4),
            "asymmetry_ratio": asymmetry_ratio,
        },
    }


# ============================================================================
# 5. OPENAI / DEEPSEEK FUNCTION-CALLING TOOL SCHEMAS & DISPATCHER
# ============================================================================

VALUATION_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "compute_operating_valuation_model",
            "description": (
                "Deterministically calculate Protocol V6 Phase 3 Bear/Base/Bull 5-year operating & cash roll-forward tables, "
                "exact 1-pass non-circular buybacks, ALL 9 cells of the 3x3 cross-grid, Phase 1.8 break-even 0% IRR growth solve, "
                "and trailing Gordon formula cross-check (r +/- 200 bps). All monetary inputs MUST be in USD millions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_price_usd": {"type": "number", "description": "Current share/ADS price in USD"},
                    "starting_diluted_shares_m": {"type": "number", "description": "Latest filing diluted shares/ADS in millions"},
                    "starting_excess_cash_m": {"type": "number", "description": "Starting excess cash in USD millions (negative if shortfall)"},
                    "starting_non_op_investments_m": {"type": "number", "default": 0.0},
                    "starting_debt_m": {"type": "number", "default": 0.0},
                    "nci_m": {"type": "number", "default": 0.0},
                    "preferred_m": {"type": "number", "default": 0.0},
                    "year0_revenue_m": {"type": "number", "description": "Year 0 starting revenue base in USD millions"},
                    "tax_rate": {"type": "number", "description": "Normalized tax rate as decimal (e.g. 0.15)"},
                    "sales_to_capital": {"type": "number", "description": "Revenue per unit capital (e.g. 10.0, NEVER inverted)"},
                    "cash_haircut": {"type": "number", "description": "Credit factor on positive excess cash (e.g. 0.50)"},
                    "investment_haircut": {"type": "number"},
                    "after_tax_cash_yield": {"type": "number", "default": 0.015},
                    "after_tax_debt_rate": {"type": "number", "default": 0.04},
                    "dividend_wht": {"type": "number", "default": 0.10},
                    "annual_adr_fee_usd": {"type": "number", "default": 0.0},
                    "ronic": {"type": "number", "description": "Terminal RONIC as decimal (e.g. 0.20) for trailing cross-check"},
                    "required_return_r": {"type": "number", "description": "Market required return r as decimal (e.g. 0.11)"},
                    "scenarios": {
                        "type": "object",
                        "description": "Keys 'bear', 'base', 'bull'; each has revenue_growth_y1_to_y5 (5 decimals), ebit_margins_y1_to_y5 (5 decimals), terminal_g, exit_multiple, annual_buyback_usd_m, and either annual_gross_dividends_per_share_usd (5 numbers) or dividend_payout_pct_of_nopat.",
                        "properties": {
                            "bear": {"type": "object"},
                            "base": {"type": "object"},
                            "bull": {"type": "object"},
                        },
                        "required": ["bear", "base", "bull"],
                    },
                },
                "required": [
                    "entry_price_usd", "starting_diluted_shares_m", "starting_excess_cash_m",
                    "year0_revenue_m", "tax_rate", "sales_to_capital", "cash_haircut", "scenarios",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_pb_grid",
            "description": (
                "Deterministically calculate Protocol V6 5-year P/B + Normalized ROE tables, 1-pass buybacks, "
                "all 9 cells of the 3x3 P/B cross-grid, and justified P/B sensitivity (r +/- 200 bps) for banks/brokers/insurers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_price_usd": {"type": "number"},
                    "starting_book_equity_m": {"type": "number", "description": "Parent book equity in USD millions"},
                    "diluted_shares_m": {"type": "number"},
                    "required_return_r": {"type": "number", "default": 0.12},
                    "dividend_wht": {"type": "number", "default": 0.10},
                    "annual_adr_fee_usd": {"type": "number", "default": 0.0},
                    "scenarios": {
                        "type": "object",
                        "description": "Keys 'bear', 'base', 'bull'; each has roe (decimal), payout_ratio (decimal), terminal_g (decimal), exit_pb, annual_buyback_usd_m.",
                        "properties": {
                            "bear": {"type": "object"},
                            "base": {"type": "object"},
                            "bull": {"type": "object"},
                        },
                        "required": ["bear", "base", "bull"],
                    },
                },
                "required": ["entry_price_usd", "starting_book_equity_m", "diluted_shares_m", "scenarios"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_four_state_summary",
            "description": (
                "Deterministically calculate Protocol V6 Phase 4 & Phase 5 probability-weighted dated Expected IRR, "
                "Expected MOIC, $10,000 cumulative proceeds, Base +/-10 pp sensitivity (proportional Bear/Catastrophe reallocation), "
                "Asymmetry Ratio, Probability of Capital Loss, Median, and 5th-percentile outcome."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_price_usd": {"type": "number"},
                    "catastrophe": {
                        "type": "object",
                        "properties": {
                            "probability_pct": {"type": "number"},
                            "event_year": {"type": "integer", "description": "Event year t_cat (1..5)"},
                            "recovery_per_share_usd": {"type": "number"},
                            "prior_net_dividends_usd": {"type": "array", "items": {"type": "number"}},
                        },
                        "required": ["probability_pct", "event_year", "recovery_per_share_usd"],
                    },
                    "bear": {
                        "type": "object",
                        "properties": {
                            "probability_pct": {"type": "number"},
                            "terminal_price_usd": {"type": "number"},
                            "net_dividends_y1_to_y5_usd": {"type": "array", "items": {"type": "number"}},
                        },
                        "required": ["probability_pct", "terminal_price_usd"],
                    },
                    "base": {
                        "type": "object",
                        "properties": {
                            "probability_pct": {"type": "number"},
                            "terminal_price_usd": {"type": "number"},
                            "net_dividends_y1_to_y5_usd": {"type": "array", "items": {"type": "number"}},
                        },
                        "required": ["probability_pct", "terminal_price_usd"],
                    },
                    "bull": {
                        "type": "object",
                        "properties": {
                            "probability_pct": {"type": "number"},
                            "terminal_price_usd": {"type": "number"},
                            "net_dividends_y1_to_y5_usd": {"type": "array", "items": {"type": "number"}},
                        },
                        "required": ["probability_pct", "terminal_price_usd"],
                    },
                },
                "required": ["entry_price_usd", "catastrophe", "bear", "base", "bull"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_irr_and_returns",
            "description": "Compute exact annualized IRR %, MOIC, Total Return %, and $10,000 proceeds for any USD dated cash-flow vector [CF_0, CF_1, ..., CF_N].",
            "parameters": {
                "type": "object",
                "properties": {
                    "cash_flows": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Dated USD cash flows starting with negative entry price CF_0",
                    },
                },
                "required": ["cash_flows"],
            },
        },
    },
]

_TOOL_DISPATCH = {
    "compute_operating_valuation_model": compute_operating_valuation_model,
    "compute_pb_grid": compute_pb_grid,
    "compute_four_state_summary": compute_four_state_summary,
    "compute_irr_and_returns": compute_irr_and_returns,
}


def execute_valuation_tool(name: str, arguments_json: str) -> str:
    """Execute one valuation math tool call safely and return compact JSON."""
    fn = _TOOL_DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        kwargs = json.loads(arguments_json) if isinstance(arguments_json, str) else dict(arguments_json)
        if not isinstance(kwargs, dict):
            return json.dumps({"error": "Tool arguments must be a JSON object."})
        result = fn(**kwargs)
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
