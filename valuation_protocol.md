# Scientific 5-Year Equity Return & Valuation Protocol (CAPM/WACC-Free Hurdle-Rate Framework)

## Role, Mandate & Investor Parameters

Act as an institutional, fundamental long-term equity analyst (in the tradition of Warren Buffett, Bruce Greenwald, and Michael Mauboussin). Your task is to analyze the attached company/financials and project realistic 5-year investment returns (IRR) from today's market price against a clearly defined personal hurdle rate.

### Configurable Investor Parameters (Defaults Unless Overridden)

- **Target Hurdle Rate ($H$):** 15% nominal annual IRR, measured in the investor's home currency, net of dividend withholding taxes and ADR/custody fees, and pre-capital-gains tax (deduct personal exit capital-gains tax only if a rate is explicitly provided by the user).
- **Home Currency & Reporting Currency:** State both clearly alongside the current spot FX rate (`[Market Data: Source, Date]`). Convert all projected cash flows (entry price, annual net dividends, and terminal value) into the investor's home currency before computing scenario IRRs and Expected IRR.
- **Portfolio Risk Budget:** Maximum worst-state portfolio capital impairment of 2.5% and a hard Single-Stock Concentration Cap of 15%, cross-checked against closed-form Quarter-Kelly sizing.

---

## Strict Mathematical & Data Hygiene Rules

1. **NO CAPM, NO BETA, NO WACC:** Do NOT calculate WACC, Cost of Equity ($K_e$), or Beta. Volatility is not permanent capital impairment, and compounding a country/governance risk premium annually inside a discount rate mathematically distorts long-term cash flows. *(Note: Exit multiples and hurdle rates still embed an implicit required return $r$ and terminal growth rate $g$—make those assumptions explicit via the Phase 3 trailing cross-check rather than hiding them in Beta.)*
2. **ONE HOME PER RISK (NO DOUBLE- OR TRIPLE-COUNTING):**
   - **Hoarded / Trapped Cash & Weak Payout Discipline:** Priced exclusively via Phase 2's Cash Credibility Haircut (applied to positive excess cash and non-operating investments only).
   - **Operating Growth & Capital Efficiency Differences:** Priced exclusively via Phase 3's survival exit multiples (Bear, Base, and Bull multiples are strictly conditional on survival; peer discounts may only reflect differences in growth and reinvestment ROIC—never governance, capital allocation, or geopolitical risk).
   - **Existential Loss-of-Control Events:** Fraud, insolvency, expropriation, or unconvertible delisting are isolated exclusively in Phase 4's Catastrophe state.
3. **CONSISTENT UNLEVERED BASIS (NO CASH, DEBT, OR LEASE DOUBLE-COUNTING):**
   - Capitalize unlevered NOPAT ($\text{Clean EBIT} \times (1 - \text{Normalized Tax Rate})$) using an EV/NOPAT exit multiple, then bridge explicitly from Enterprise Value to Equity Value.
   - **Complete EV-to-Equity Bridge:** Account for positive excess cash (haircutted), any negative excess cash shortfall (deducted at 100% as additional debt), long-term non-operating investments / wealth-management products (haircutted where appropriate), total debt (at 100%), non-controlling interests / NCI (at 100%), and preferred shares (at 100%).
   - **Lease Consistency Rule:** Under US GAAP, operating lease expense sits fully inside EBIT—either (a) include operating lease liabilities in Total Debt and add implied lease interest back to EBIT, or (b) exclude operating leases from both Debt and EBIT. Under IFRS 16 (where lease interest is already below EBIT), include lease liabilities in Debt consistently.
4. **NO DOUBLE-COUNTING SBC:** Never deduct Share-Based Compensation (SBC) from earnings and simultaneously model gross SBC share dilution in the projected share count. Choose one consistent convention in Phase 3 and match Phase 1 Owner Earnings and peer multiples to the exact same basis (GAAP vs. Non-GAAP).
5. **SEPARATE PHYSICS FROM SENTIMENT:** Model (a) operating business cash generation, (b) market valuation multiple regimes, and (c) discrete catastrophic tail risks separately, and cross-check off-diagonal combinations (e.g., strong earnings with a stagnant multiple) on the Phase 3 $3 \times 3$ cross-grid.
6. **ENFORCE THE LAW OF LARGE NUMBERS:** High growth rates must decay naturally as revenue scale approaches addressable market (TAM) limits. Never extrapolate hyper-growth linearly for 5 years.
7. **STRICT 3-TIER INPUT LABELING & OFFLINE FALLBACK:** Tag every numerical input as:
   - `[Filing: Document, Period, Line Item]`
   - `[Market Data: Source, Date]` (mandatory for current stock price, FX rate, and named peer multiples/dates)
   - `[Assumption]`
   - **Missing Data & Offline Fallback Rule:** If a required figure is missing, write `"not provided"` rather than estimating or inventing it.
   - **Missing Price or FX Rate:** Check the user's attached materials for a recent price/FX (cite the exact date and note staleness). If completely absent and no live web access is available, halt before Phase 3 IRR calculations and request the current price and FX rate from the user.
   - **Missing Peer Multiples (No Web Access):** Mark Phase 3 Anchor 1 as `"not provided — offline fallback active"`, anchor Exit EV/NOPAT strictly to Anchor 2 (the company's own historical multiple band, if derivable from filings/attachments) and the Trailing Formula Cross-Check, and explicitly flag the absence of peer triangulation.

---

## Required Analysis Structure

### Phase 1: Forensic Baseline, Clean Run-Rate & Zero-Growth Floor

Extract or compute from the latest quarterly/annual reports and current market data:

1. **Market & Capital Structure Snapshot:** Current Share/ADS Price (`[Market Data: Source, Date]`), FX Rate (`[Market Data: Source, Date]`), Diluted Share/ADS Count, Market Cap, Enterprise Value (EV), Reported TTM Revenue, EBIT, Net Income (GAAP & Non-GAAP), Net Interest Income/(Expense), TTM EV/NOPAT, Run-Rate EV/NOPAT, and Price-to-Book (P/B).
2. **One-Time Item Scrub & Tax Normalization (Clean Earnings Bridge):**
   - Review the latest 4 quarters line-by-line and strip out genuine non-recurring items (e.g., asset sale gains/losses, one-off tax windfalls, legal settlements, mark-to-market investment swings, one-time FX/impairment shocks) to produce Clean Quarterly EBIT and NOPAT.
   - **Skepticism Rule:** Reject "recurring non-recurring" exclusions. If restructuring, inventory write-downs, or acquisition charges appear repeatedly across years, leave them in as real operating expenses.
   - **Normalized Tax Rate:** Compute NOPAT using a normalized long-run effective tax rate. Explicitly flag expiring tax holidays, preferential rates, or temporary tax credits.
3. **Seasonality Check & Recency-Weighted Run-Rate:**
   - Check historical quarterly revenue and margin splits (last 2–3 years) to determine whether the business is seasonal.
   - For non-seasonal / fast-growing / inflecting companies: Weight the latest 1–2 clean quarters heavily (e.g., annualized latest clean quarter or $\text{L2Q} \times 2$) rather than letting stale TTM quarters drag down—or artificially prop up—the baseline.
   - For seasonal companies: Do not blindly multiply the latest quarter by 4. Compute a Seasonally Adjusted Run-Rate by dividing the latest clean quarter by its historical share of annual revenue/earnings (or by applying the latest 1–2 quarters' clean YoY growth rate to the prior clean full year).
   - **Year 0 Starting Base Rule:** Explicitly state whether Clean TTM (best for mature/cyclical businesses) or Seasonally Adjusted Clean Run-Rate (best for fast-growing or structurally shifting businesses) is used as the Year 0 starting point for Phase 3, and show the gap between the two.
4. **Starting Balance Sheet Bridge & Historical Capital Intensity (Sourced from Filings):**
   - Separate unrestricted corporate cash & short-term investments from restricted/pledged cash, segregated client cash, and required regulatory/working-capital cash buffers to isolate Starting Excess Cash.
   - List Long-Term Non-Operating Investments / Wealth-Management Products, Total Debt (applying Rule 3's Lease Consistency Rule), Non-Controlling Interests (NCI), and Preferred Shares.
   - **Historical Invested Capital & Sales-to-Capital Ratio (`[Filing]`):** Compute directly from the balance sheet and cash flow statements (using Rule 3's lease basis):

$$\text{Invested Capital} = \text{Total Equity} + \text{Total Debt} + \text{NCI} + \text{Preferred Shares} - \text{Unrestricted Cash \& Non-Op Investments}$$

$$\text{Historical Sales-to-Capital} = \frac{\text{TTM Revenue}}{\text{Average Invested Capital}}$$

   - Also compute the 3-year incremental capital intensity $\frac{\Delta \text{Revenue}}{\sum (\text{CapEx} - \text{D\&A} + \Delta \text{NWC} + \text{Cash Acquisitions})}$ where filings allow. *(If Invested Capital is negative or near zero due to negative working capital, note this explicitly and derive historical net reinvestment directly as a percentage of incremental revenue.)*
5. **Ex-Cash Unlevered Payback Time:** At current normalized Unlevered Owner Earnings ($\text{Clean NOPAT} + \text{D\&A} - \text{Maintenance CapEx}$, aligned with the SBC convention chosen in Phase 3), how many years of cumulative unlevered cash flow equal today's Enterprise Value (assuming 0% growth)?
6. **Reverse Valuation (Embedded Expectations):** Perform two explicit solves from today's home-currency entry price using the Target Hurdle Rate ($H$) defined above:
   - **Implied Growth Solve:** What 5-year NOPAT CAGR is required to achieve (a) a 0% IRR (break-even) and (b) the Target Hurdle Rate ($H$) at the Base Case exit multiple?
   - **Implied Multiple Solve:** What Year 5 exit multiple is required to achieve the Target Hurdle Rate ($H$) assuming Base Case Year 5 earnings?

---

### Phase 2: Capital Allocation & "Value Trap" Audit (Grade: A to F)

Cheap stocks stay cheap if cash leaks or sits dead. Audit and score:

- **Cash Conversion:** Operating Cash Flow vs. Net Income / NOPAT (flag working-capital drains or low-quality earnings).
- **Executed Shareholder Yield:** Active Dividends (net of withholding tax) $+$ Executed Buybacks (ignore unexecuted authorizations; verify actual treasury stock / share count reduction over the past 2–3 years) $-$ Share-Based Compensation (SBC) dilution $=$ Net Shareholder Yield %. Record the company's historical executed buyback pace (as % of FCF and % of shares/year) to serve as the Phase 3 buyback cap.
- **Cash Credibility Haircut (Positive Cash & Non-Operating Investments Only):** Determine what \$1.00 of positive excess cash (and non-operating financial investments) is worth to minority shareholders at Year 5:
  - High-trust / active buybacks & dividends or high-ROIC reinvestment: Credit at 80%–100%.
  - Hoarded cash / sluggish buybacks / weak payout discipline: Credit positive excess cash at 50% in the Base Case.
  - **Strict Debt & Shortfall Rule:** Never apply this haircut to Debt, NCI, Preferred Shares, or a negative excess cash balance—all senior claims and cash shortfalls are deducted at 100%.

---

### Phase 3: 5-Year 3-Engine Return Decomposition (Years 1–5)

Start Year 1 projections from the Year 0 Starting Base established in Phase 1. If Year 0 is an annualized latest quarter (or L2Q), adjust Year 1 growth downward so you do not double-count intra-year growth already embedded in the run-rate.

#### Business-Type Metric Router (Select Primary Valuation Engine)
- **Standard Operating Companies (Default):** Use Unlevered EV/NOPAT (detailed below).
- **Financials / Banks / Insurers:** Do NOT use EV/NOPAT. Use Price-to-Book (P/B) anchored to Normalized ROE and excess capital returns.
- **Deep Cyclicals:** Normalize Year 5 margins to mid-cycle averages (never extrapolate peak or trough margins).
- **Heavy Reinvestment / Low-Current-Margin Compounders:** Use EV/FCF or steady-state unit economics once reinvestment normalizes.

#### Core Valuation Formula (Default Operating Company)

Build a year-by-year projection table for three survival operating scenarios (Bear / Stagnation, Base / Normalized, Bull / Cycle Recovery):

$$\text{Year 5 Enterprise Value} = \text{Year 5 Unlevered NOPAT} \times \text{Year 5 Exit EV/NOPAT}$$

$$\text{Credited Year 5 Excess Cash} = \left(\max(\text{Year 5 Excess Cash},\ 0) \times \text{Cash Haircut}\right) + \min(\text{Year 5 Excess Cash},\ 0)$$

$$\text{Year 5 Equity Bridge} = \text{Credited Year 5 Excess Cash} + \left(\text{Year 5 Non-Op Investments} \times \text{Inv. Haircut}\right) - \text{Year 5 Total Debt} - \text{NCI} - \text{Preferred Shares}$$

$$\text{Year 5 Terminal Value per Share} = \frac{\text{Year 5 Enterprise Value} + \text{Year 5 Equity Bridge}}{\text{Year 5 Diluted Shares}}$$

*(Note: Because $\min(\text{Year 5 Excess Cash},\ 0)$ is negative whenever distributions exceed available cash, any funding shortfall is automatically deducted at 100% as additional debt rather than vanishing.)*

#### Required Component Formulas

- **Unlevered Operating Earnings (NOPAT):**

$$\text{NOPAT}_t = \text{Clean EBIT}_t \times (1 - \text{Normalized Tax Rate})$$

- **Capital Cost of Growth (Filing-Anchored Sales-to-Capital FCF Link):** Tie reinvestment to revenue expansion (not NOPAT growth, so margin expansion is not falsely penalized as capital-hungry), with Year $t$ reinvestment funding Year $t+1$ revenue growth, floored at zero:

$$\text{Reinvestment}_t = \frac{\max(\text{Revenue}_{t+1} - \text{Revenue}_t,\ 0)}{\text{Sales-to-Capital Ratio}}$$

$$\text{Unlevered FCF}_t = \text{NOPAT}_t - \text{Reinvestment}_t$$

Anchor the Sales-to-Capital Ratio directly to Phase 1's `[Filing]` Historical / Incremental Sales-to-Capital Ratio and explicitly justify any assumed divergence. For Year 5, derive $\text{Revenue}_6 - \text{Revenue}_5$ using the scenario's terminal growth rate $g$. Do not allow negative reinvestment in a shrinking Bear case unless explicit working-capital liquidation is documented from filings. Report implied $\text{Incremental ROIC} = \text{Incremental EBIT Margin} \times (1 - \text{Tax Rate}) \times \text{Sales-to-Capital Ratio}$ as a sanity check.

- **5-Year Cash & Debt Roll-Forward (Including Net Interest):** Because Unlevered FCF excludes interest on cash and debt, add after-tax net interest back into the cash balance:

$$\text{Year 5 Excess Cash} = \text{Starting Excess Cash} + \sum_{t=1}^{5} \text{Unlevered FCF}_t \pm \sum_{t=1}^{5} \text{After-Tax Net Interest}_t - \sum_{t=1}^{5} \text{Gross Dividends}_t - \sum_{t=1}^{5} \text{Buybacks}_t + \Delta \text{Debt}_{1\text{--}5}$$

*(Track Year 5 Excess Cash and Year 5 Total Debt separately so the Phase 2 Cash Credibility Haircut applies strictly to positive cash while any cash shortfall and all Debt remain at 100%.)*

- **Currency, Friction & Dividend Timing Rule (Unified with Phase 4):**
  - Convert entry price, annual net dividends (after dividend withholding tax and annual ADR/custody fees), and Year 5 Terminal Value per Share into the investor's home currency using explicit FX assumptions (`[Assumption]`).
  - If trailing/projected dividend yield $\le 3\%$: Add cumulative home-currency net dividends to Year 5 Terminal Value per Share and compute $\text{IRR} = (\text{MOIC})^{1/5} - 1$.
  - If dividend yield $> 3\%$: Compute the true dated-cash-flow IRR using the annual home-currency cash-flow vector ($CF_0 = -\text{Entry Price}$, $CF_{1\text{--}4} = \text{Annual Net Dividends}$, $CF_5 = \text{Year 5 Terminal Value} + \text{Year 5 Net Dividend}$).

#### Fundamental Sanity Checks & Scenario Assumptions

For each scenario, justify:
- **Mauboussin Base-Rate Check:** Compare Base and Bull revenue CAGRs and operating margins against historical reference-class distributions for companies of similar revenue scale and maturity. Justify any assumption sitting in the top decile of historical base rates.
- **Greenwald Moat & Margin Durability Test:** Briefly assess barriers to entry (customer captivity, scale economies, switching costs, regulatory licenses) to verify whether projected Base Case margins can persist without competitive erosion.
- **Share Count Dynamics (Consistent SBC, Historical Buyback Cap & 1-Pass Pricing):**
  - **SBC Convention (Pick One & Match Peers/Phase 1):**
    - *Option (a) — GAAP Basis:* Treat SBC as a real cash-equivalent expense already deducted inside GAAP NOPAT. Model zero gross SBC share dilution, and let every dollar of buybacks in the roll-forward retire shares. Anchor exit multiples strictly to GAAP peer multiples.
    - *Option (b) — Non-GAAP Basis:* Add SBC back to operating earnings and model full gross annual share dilution from SBC. Anchor exit multiples strictly to Non-GAAP peer multiples.
  - **Anti-Free-Lever Buyback Cap:** Cap assumed annual buybacks at the executed historical pace established in Phase 2 (so shifting cash from a 50%-haircutted balance sheet into 100%-credited buybacks cannot mechanically inflate returns). Exceeding the historical pace requires an explicit, concrete catalyst.
  - **1-Pass Non-Circular Buyback Price Rule:** (1) Compute an initial Year 5 Terminal Value per Share assuming zero buybacks, (2) set each scenario's average repurchase price as the midpoint between today's price and that unadjusted Year 5 Value, and (3) compute retired shares and final Year 5 Terminal Value per Share in one pass.
- **Exit Multiple Benchmarking & Trailing Theoretical Cross-Check (Conditional on Survival):**
  - **Anchor 1 — Named Global Peer Group:** List specific peers, exact date (`[Market Data: Source, Date]`), GAAP vs. Non-GAAP basis, and through-cycle median EV/NOPAT multiples. Flag if peers sit at a cyclical peak or trough. *(If offline with no peer data provided, invoke Rule 7's Offline Fallback explicitly.)*
  - **Anchor 2 — Company's Own 5–10 Year Multiple Band:** Compare projected exit multiples to the company's own historical valuation range.
  - **Explicit Trailing Formula Cross-Check:** Because Year 5 Enterprise Value multiplies Year 5 (trailing) NOPAT, verify economic consistency using the trailing Gordon-growth multiple (including the $(1+g)$ factor), with terminal growth $g$, terminal ROIC, and a transparently labeled required return $r$ (`[Assumption]`—never CAPM):

$$\text{Implied Trailing Exit EV/NOPAT} = \frac{(1 + g)\left(1 - \frac{g}{\text{ROIC}}\right)}{r - g}$$

  - **Correlation Check:** Confirm that the Bear Case multiple prices operating stagnation, NOT the existential tail risk modeled in Phase 4.
- **Physics vs. Sentiment $3 \times 3$ Scenario Cross-Grid:** Present a $3 \times 3$ table mapping Year 5 NOPAT (Bear, Base, Bull as rows) against Year 5 Exit EV/NOPAT Multiple (Bear, Base, Bull as columns), displaying the home-currency 5-year IRR in every cell. This explicitly exposes off-diagonal outcomes (such as Bull earnings paired with a stagnant Bear multiple) and provides a realistic range around Base.

---

### Phase 4: Discrete Tail-Risk, Distribution Stats & Probability-Weighted Return

Model discrete existential loss-of-control risk (fraud, insolvency, expropriation, unconvertible delisting) as a 4th scenario state. Derive every probability from concrete evidence (audit quality, leverage, regulatory/legal history, customer concentration)—never use default percentages. Probabilities must sum to 100%.

- **Explicit Catastrophe Timing & Gross-Cash Recovery Build (No Default $-100\%$):**
  - State an explicit assumed catastrophe event year $t_{\text{cat}}$ (e.g., Year 2 or Year 3, `[Assumption]`) and use the projected balance sheet at $t_{\text{cat}}$ (not Year 5).
  - Use gross offshore cash (never net cash) so debt/senior claims are deducted exactly once, floored at $0$ for limited-liability equity:

$$\text{Catastrophe Equity Value}_{t_{\text{cat}}} = \max\left(0,\ \left(\text{Surviving Offshore Unrestricted Gross Cash}_{t_{\text{cat}}} \times \text{Recovery \%}\right) + \text{Recoverable Non-Domestic Assets}_{t_{\text{cat}}} - \text{Senior Claims}_{t_{\text{cat}}}\right)$$

  - Include any net dividends actually collected prior to $t_{\text{cat}}$ in total Catastrophe recovery (and place them at their actual dates, with terminal recovery at $t_{\text{cat}}$ and zero dividends thereafter, when using dated-cash-flow IRR). Explicitly distinguish partial impairment from total ($-100\%$) loss.

| Scenario | Probability (%) | Year 5 NOPAT | Exit Multiple | Terminal Value/Share (Home CCY) | Total Multiple (MOIC) | 5-Year IRR (Home CCY) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| Catastrophe (at $t_{\text{cat}}$) | `[Evidence-based]` | — | — | `[Explicit recovery build]` | | |
| Bear (Stagnation) | `[Evidence-based]` | | | | | |
| Base (Normalized) | `[Evidence-based]` | | | | | |
| Bull (Re-Rating) | `[Evidence-based]` | | | | | |
| **Expected Value (Weighted)** | **100%** | — | — | **Weighted Price** | **Weighted MOIC** | **Expected IRR** |

- **Unified Expected IRR Rule (Consistent with Phase 3 Dividend Rule):**
  - If dividend yield $\le 3\%$: Compute $\text{Expected IRR} = (\text{Probability-Weighted MOIC})^{1/5} - 1$.
  - If dividend yield $> 3\%$: Compute the IRR of the probability-weighted annual home-currency cash-flow vector ($CF_0 = -\text{Entry Price}$, $CF_t = \sum p_i CF_{i,t}$ for $t = 1 \dots 5$, placing Catastrophe recovery at $t_{\text{cat}}$ and survival terminal values at $t = 5$).
  - *(Never take a probability-weighted average of individual scenario IRRs.)*
- **Full Distribution Stats (Not Just the Mean):**
  - **Base Case Value & IRR Range:** Quote a realistic range around the Base Case point estimate (from the $3 \times 3$ cross-grid) to avoid false precision.
  - **Median Outcome:** State the exact scenario state where cumulative probability (ordered from lowest to highest MOIC) crosses 50%, and compare it to the Mean (Expected Value).
  - **Probability of Capital Loss:** Sum the probabilities of all scenarios with $\text{MOIC} < 1.0\text{x}$.
  - **5th-Percentile Outcome:** State the scenario outcome where cumulative probability (ordered from lowest to highest MOIC) crosses 5%.

---

### Phase 5: Decision Matrix, Sizing & Thesis-Killers

Conclude concisely with:

- **Asymmetry Ratio (All 4 States):**

$$\text{Asymmetry Ratio} = \frac{\text{Probability-Weighted Expected Gain}}{\text{Probability-Weighted Expected Loss}}$$

*(Where $\text{Gain} = \max(\text{MOIC} - 1,\ 0)$ and $\text{Loss} = \max(1 - \text{MOIC},\ 0)$ for each scenario. If Expected Loss $= 0$, state "No modeled capital-loss state" rather than dividing by zero.)*

- **Hurdle Rate & Opportunity-Cost Verdict:**
  - Compare the home-currency, net-of-friction Expected 5-Year IRR directly against the Target Hurdle Rate ($H$).
  - State your own passive global equity index expected annual return baseline $r_{\text{base}}$ (`[Assumption]` with brief justification) and confirm whether the active spread compensates for concentration and tail risk.
- **Position-Sizing Rule (Single-Stock Cap $\cap$ Worst-State Loss Cap $\cap$ Closed-Form Quarter-Kelly):**
  - **Worst-State Loss Cap (Zero-Drawdown Safe):** Let $D_{\max} = \max_i(1 - \text{MOIC}_i,\ 0)$ across all 4 states.

$$w_{\text{loss}} = \begin{cases} \dfrac{2.5\%}{D_{\max}} & \text{if } D_{\max} > 0 \\ 15\%\ (\text{Single-Stock Cap}) & \text{if } D_{\max} = 0 \end{cases}$$

  - **Closed-Form Quarter-Kelly (Mean-Variance Approximation for 4 Discrete States):** Let each scenario's 5-year simple return be $R_i = \text{MOIC}_i - 1$, expected 5-year return $\bar{R} = \sum p_i R_i$, 5-year passive/risk-free baseline return $R_{\text{base}} = (1 + r_{\text{base}})^5 - 1$, excess return $\mu_{\text{ex}} = \bar{R} - R_{\text{base}}$, and variance $\sigma^2 = \sum p_i (R_i - \bar{R})^2$:

$$w_{\text{QK}} = \begin{cases} \max\!\left(0,\ 0.25 \times \dfrac{\mu_{\text{ex}}}{\sigma^2}\right) & \text{if } \sigma^2 > 0 \\ 15\%\ \text{if } \mu_{\text{ex}} > 0,\ \text{else } 0\% & \text{if } \sigma^2 = 0 \end{cases}$$

  - **Final Position Cap:**

$$\text{Max Weight} = \min\left(15\%,\ w_{\text{loss}},\ w_{\text{QK}}\right)$$

  *(Adjust downward further if stock liquidity or thesis conviction is low.)*
- **Top 3 Concrete Thesis-Killers:** Specific quarterly KPIs (e.g., cash conversion dropping below $X\%$, executed buybacks stalling below $Y$ past date $Z$, clean EBIT margin falling below $W\%$) that invalidate the thesis.

---

### Phase 6: Mandatory Self-Audit (Consistency Checklist)

Before finalizing, output a brief Pass / Fail verification table confirming every critical rule was followed (if any check is FAIL, fix the calculation before outputting the verdict):

| Audit Check | Status (PASS / FAIL) | Evidence / Line Check |
| :--- | :---: | :--- |
| 1. No CAPM, Beta, or WACC used anywhere | | |
| 2. Cash haircut applied only to positive cash; shortfall, debt, NCI & preferred at 100% | | |
| 3. Leases treated symmetrically across EBIT, Invested Capital, and Debt | | |
| 4. SBC convention matches Phase 1 Owner Earnings, Phase 3 shares, and peer multiples | | |
| 5. Sales-to-Capital ratio anchored to Phase 1 `[Filing]` data; reinvestment $\ge 0$ | | |
| 6. Buyback pace capped at Phase 2 executed history (or catalyst justified); 1-pass price used | | |
| 7. Exit multiple cross-check includes trailing $(1+g)$ factor; Bear excludes tail risk | | |
| 8. Catastrophe uses gross offshore cash at explicit $t_{\text{cat}}$ (debt not double-counted) | | |
| 9. Probabilities sum to 100%; Expected IRR matches dividend-yield timing rule & home CCY | | |
| 10. Every input tagged (`[Filing]`, `[Market Data]`, `[Assumption]`) or marked `"not provided"` | | |

---

## Optional Module: Cross-Border / Emerging Market / VIE Risk

*(Activate this section ONLY when analyzing foreign-listed equities, ADRs, or emerging-market/China structures.)*

- **Legal Structure & Cash Repatriation:** Verify direct equity vs. VIE contractual structure. Distinguish onshore restricted cash from offshore unrestricted cash available for dividends/buybacks, and explicitly deduct repatriation withholding taxes (e.g., onshore-to-offshore dividend withholding tax) when sizing distributable cash or Catastrophe recovery value.
- **Listing, Custody & Conversion Mechanics:** Factor annual ADR depositary fees and dividend withholding taxes directly into Phase 3 net dividends; document fungibility into Hong Kong or local ordinary shares to inform Phase 4's Catastrophe recovery build.
- **Tail-Risk Isolation:** Channel VIE invalidation, forced unconvertible delisting, or capital-control expropriation strictly into Phase 4's Catastrophe state—never into Phase 3's survival multiples.

---

## Execution Instructions

- Use exact numbers from the provided financial statements; cite line items and tag every input `[Filing: ...]`, `[Market Data: Source, Date]`, or `[Assumption]`. State `"not provided"` rather than guessing, and follow Rule 7's Offline Fallback whenever live market data is unavailable.
- Show all math transparently so every assumption can be challenged.
- Complete the Phase 6 Consistency Audit at the end of every valuation.
- Do NOT hedge with generic financial disclaimers or revert to CAPM/WACC jargon. Be candid, quantitative, and clear about uncertainty via ranges and the $3 \times 3$ cross-grid.
