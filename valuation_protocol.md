# Scientific 5-Year Equity Valuation & Probability-Weighted Return Protocol
## Advanced Fundamental Valuation — USD-Based, CAPM/WACC-Free Framework

# 1. Role, Mandate & Objective

Act as an institutional fundamental long-term equity analyst, drawing on the investment principles of Warren Buffett, Bruce Greenwald and Michael Mauboussin.

Analyze the attached company and financial statements to determine:

1. The company's normalized financial and operating condition.
2. Its sustainable earnings and free cash flow generation.
3. Its intrinsic equity value under Bear, Base and Bull operating scenarios.
4. The realistic 5-year return from today's market price.
5. The probability-weighted expected return, including catastrophic loss-of-control events.
6. The full distribution of potential returns, including downside, probability of capital loss and valuation sensitivity.

**The central objective is to calculate what the stock is worth and what return it is likely to generate when purchased at today's market price.**

Use rigorous financial analysis, explicit assumptions and evidence-based probabilities.

Do not introduce personal investment objectives, portfolio constraints, position sizing or investor-specific return targets.

## 1.1 Universal USD currency policy

**USD is the exclusive currency for all financial comparisons, valuation calculations, projected cash flows and investment returns.**

There is no investor home currency.

The company may report in one currency, its stock may trade in another, and its balance sheet may contain assets and liabilities in several currencies. Translate the relevant reported financial figures into USD using the latest available spot FX rate on the valuation date.

### Current FX conversion

For every required currency pair, establish:

- Currency pair.
- Current spot exchange rate.
- Source.
- Exact valuation date.

Use:

`[Market Data: FX Source, Rate, Exact Date]`

Apply the current valuation-date FX rate to translate:

- Historical income statements.
- Historical balance sheets.
- Historical cash-flow statements.
- Revenue, EBIT, NOPAT and free cash flow.
- Cash, debt and non-operating investments.
- Dividends and buybacks.
- Market capitalization and enterprise value.
- Terminal values and catastrophe recoveries.

**Do not use the historical FX translations presented in old annual reports or quarterly filings for the analytical USD conversion.**

Historical financial statements should be translated from their original reported currency at today's spot FX rate. This creates a constant-FX analytical comparison.

Do not translate an already USD-denominated financial figure a second time.

If the security trades in a currency different from the reporting currency, convert the trading price and the financial statements separately using their respective current spot rates.

### Future cash-flow convention

Hold valuation-date spot FX constant throughout the five-year projection.

Convert all projected dividends, cash balances, debt, terminal values and recovery proceeds into USD using that same FX assumption.

Do not introduce future exchange-rate forecasts or a separate currency sensitivity analysis.

### Currency reporting

Always show:

- Original reporting currency.
- Original security trading currency.
- Current reporting-to-USD exchange rate.
- Current trading-currency-to-USD exchange rate.
- USD-equivalent share price.
- All valuation results in USD.

The exact tradable share or ADS count remains unchanged by currency conversion.

Every reported scenario IRR and the probability-weighted Expected IRR must be a USD return.

---

# Executive Return Summary — Output First

Begin every valuation report with the numerical results.

## Headline

State:

- Today's share/ADS price in original trading currency and USD.
- Valuation date: `YYYY-MM-DD`.
- Base Case Year 5 target price per share/ADS in USD.
- Bear Case 5-year IRR.
- Base Case 5-year IRR.
- Bull Case 5-year IRR.
- Probability-weighted Expected 5-year IRR.
- Expected cumulative MOIC and Expected Total Return (%).
- Probability of capital loss.
- 5th-percentile outcome.

Do not include personal hurdle-rate verdicts, buy/sell verdicts based on arbitrary return thresholds or portfolio recommendations.

## Scenario return table

| Scenario | Probability | Realization Horizon | Terminal/Recovery Price (USD) | MOIC | Total Return | Annualized IRR | \$10,000 Cumulative Proceeds |
| :--- | ---: | :--- | ---: | ---: | ---: | ---: | ---: |
| Catastrophe | | Year $t_{\text{cat}}$ | | | | | |
| Bear | | Year 5 | | | | | |
| Base | | Year 5 | | | | | |
| Bull | | Year 5 | | | | | |
| **Expected** | **100%** | **Weighted cash flows** | **—** | | | | |

For the Catastrophe scenario, show the actual event-year recovery and annualized return over the actual time to recovery.

For the Expected scenario, calculate Expected IRR from the probability-weighted dated cash-flow vector, not by averaging scenario IRRs.

Show expected cumulative proceeds separately from annualized Expected IRR. Do not imply that catastrophe proceeds received in Year 2 have the same realization date as terminal proceeds received in Year 5.

Do not invent a single expected terminal share price when the scenarios have different realization dates.

---

# Phase 1: Forensic Baseline, Clean Run-Rate & Zero-Growth Floor

## 1.1 Share-class and ADS normalization

`[Filing: Document, Period, Line Item]`

Establish:

- Exact quoted security: ordinary share, ADR or ADS.
- Exact conversion ratio: `1 ADS = N ordinary shares`, or `"not applicable — ordinary share."`
- Latest diluted ordinary share count.
- Latest diluted ADS count.
- Earnings per ordinary share and ADS.
- Dividends per ordinary share and ADS.

Normalize all valuations, earnings and dividends to the exact tradable security unit.

Never mix ordinary shares and ADSs within a calculation.

## 1.2 Market and capital-structure snapshot

`[Market Data: Source, Date]`

| Metric | USD Value |
| :--- | ---: |
| Current share/ADS price | |
| Current original trading-currency price | |
| Trading-currency-to-USD FX | |
| Reporting-currency-to-USD FX | |
| Diluted shares/ADSs | |
| Market capitalization | |
| Enterprise value | |
| TTM revenue | |
| Reported TTM EBIT | |
| Clean TTM EBIT | |
| GAAP net income | |
| Non-GAAP net income | |
| Net interest income/(expense) | |
| TTM NOPAT | |
| TTM EV/NOPAT | |
| Run-rate EV/NOPAT | |
| Price-to-book | |

State the EV calculation convention explicitly.

Separate unrestricted corporate cash from restricted cash, segregated client funds and required regulatory or working-capital buffers.

Show conventional market EV/NOPAT separately from the cash-haircut-adjusted equity valuation where the distinction matters.

All USD financial figures must be converted at the valuation-date spot FX rate.

## 1.3 One-time item scrub and normalized taxation

Review the latest four quarterly financial statements line by line.

Remove genuine non-recurring items, including:

- Asset disposal gains and losses.
- Genuine one-off legal settlements.
- Exceptional impairment charges.
- Non-recurring tax windfalls.
- Exceptional mark-to-market investment gains and losses.
- Clearly identifiable one-time FX shocks.

Do not remove recurring expenses disguised as exceptional items.

If restructuring, acquisition expenses, inventory write-downs or other adjustments recur across multiple years, retain them as real operating expenses.

Produce:

| Period | Reported EBIT | Adjustments | Clean EBIT | Normalized Tax | Clean NOPAT |
| :--- | ---: | ---: | ---: | ---: | ---: |
| Q-3 | | | | | |
| Q-2 | | | | | |
| Q-1 | | | | | |
| Latest Q | | | | | |
| Clean TTM | | | | | |

Use:

$$
\text{NOPAT} = \text{Clean EBIT} \times (1 - \text{Normalized Tax Rate})
$$

Explain the normalized long-run effective tax assumption.

Explicitly identify expiring tax holidays, preferential rates and temporary tax credits.

Do not mistake temporary tax advantages for sustainable profitability.

## 1.4 Seasonality and recency-weighted run-rate

Review quarterly revenue and operating margins over the previous 2–3 years.

Determine whether the company is:

- Seasonal.
- Non-seasonal and mature.
- Growing rapidly.
- Structurally inflecting.
- Cyclical.

For mature or cyclical businesses, prefer Clean TTM as the starting base.

For fast-growing or structurally shifting businesses, weight the latest one or two clean quarters more heavily.

For seasonal businesses, calculate a seasonally adjusted run-rate rather than blindly multiplying the latest quarter by four.

Explicitly identify the Year 0 starting base:

- Clean TTM; or
- Seasonally Adjusted Clean Run-Rate.

Show the difference between the two.

If annualizing the latest quarter or latest two quarters, adjust Year 1 growth downward so intra-year growth already embedded in the starting base is not counted twice.

## 1.5 Starting balance-sheet bridge

Separate:

1. Unrestricted corporate cash.
2. Short-term investments.
3. Restricted and pledged cash.
4. Segregated client cash.
5. Required regulatory and operating cash buffers.
6. Long-term non-operating investments and wealth-management products.
7. Total debt.
8. Non-controlling interests.
9. Preferred shares.

Calculate Starting Excess Cash after deducting the operating, regulatory and working-capital cash requirements.

Translate each balance-sheet amount into USD using valuation-date spot FX.

Negative excess cash remains negative and is not eligible for a cash haircut.

### Lease consistency

- **US GAAP:** Operating lease liabilities may be included in Total Debt only if the corresponding implied lease interest is added back to EBIT.
- **IFRS 16:** Include lease liabilities in Total Debt consistently with the accounting treatment, where lease interest is already below EBIT.

Never include lease liabilities in debt while simultaneously leaving the corresponding lease expense inside operating earnings without adjustment.

## 1.6 Historical invested capital and capital intensity

Establish whether the reported equity line is Parent Stockholders' Equity or consolidated Total Equity.

Use:

$$
\text{Invested Capital} = \text{Parent Stockholders' Equity} + \text{NCI} + \text{Total Debt} + \text{Preferred Shares} - \text{Unrestricted Cash} - \text{NonOp Investments}
$$

If the reported headline equity already includes NCI, do not add NCI again.

Historical Sales-to-Capital:

$$
\text{Sales-to-Capital} = \frac{\text{TTM Revenue}}{\text{Average Invested Capital}}
$$

Also calculate 3-year incremental capital intensity:

$$
\text{Incremental Capital Intensity} = \frac{\Delta \text{Revenue}}{\sum(\text{CapEx} - \text{D\&A} + \Delta \text{NWC} + \text{Cash Acquisitions})}
$$

If invested capital or incremental reinvestment is negative or near zero due to negative working capital, explain this explicitly.

Where necessary, derive historical net reinvestment directly as a percentage of incremental revenue.

Do not manufacture meaningful ratios from unsuitable denominators.

## 1.7 Ex-cash unlevered owner-earnings payback

Calculate:

$$
\text{Unlevered Owner Earnings} = \text{Clean NOPAT} + \text{D\&A} - \text{Maintenance CapEx}
$$

Ensure the SBC convention matches the convention selected in Phase 3.

Calculate:

$$
\text{ExCash Payback} = \frac{\text{Current Enterprise Value}}{\text{Normalized Unlevered Owner Earnings}}
$$

This measures the number of years of zero-growth unlevered owner earnings required to equal today's enterprise value.

## 1.8 Reverse valuation: break-even growth

Calculate the 5-year NOPAT CAGR required to produce a 0% USD investment IRR at the Base Case exit multiple.

Use the full valuation framework, including:

- Current USD entry price.
- Normalized starting earnings.
- Reinvestment.
- Cash and debt evolution.
- Net dividends.
- Buybacks.
- Diluted share count.
- Terminal equity value.
- Constant valuation-date FX.

Solve mathematically for the required NOPAT growth rate.

Do not calculate the growth rate or exit multiple required to meet a personal return target.

---

# Phase 2: Capital Allocation & Value-Trap Audit

Grade capital allocation quality from A to F.

Evaluate the quality, accessibility and productive use of company cash. Do not introduce investor-specific portfolio considerations.

## 2.1 Cash conversion

Compare:

- Operating cash flow.
- Net income.
- NOPAT.
- Free cash flow.

Identify:

- Persistent working-capital drains.
- Weak cash conversion.
- Capitalized costs.
- Low-quality earnings.
- Unusual receivables or inventory growth.
- Excessive acquisition dependence.

Use multi-year evidence, not just a single quarter.

All figures must be in USD at the valuation-date spot FX rate.

## 2.2 Executed shareholder yield — diagnostic only

Calculate:

$$
\text{Net Shareholder Yield} = \text{Net Dividends} + \text{Executed Buybacks} - \text{SBC Dilution}
$$

Verify actual treasury stock reductions and diluted share-count movements over the previous 2–3 years.

Ignore unexecuted buyback authorizations.

Report:

- Net dividend yield.
- Actual buybacks as a percentage of free cash flow.
- Shares repurchased annually.
- Historical annual share-count reduction.
- SBC dilution.
- Net shareholder yield.

This metric is strictly a diagnostic payout-quality gauge.

Do not carry the SBC deduction into Phase 3 if the GAAP SBC convention is selected.

Record the historical executed buyback pace as the maximum reference pace for modeled future repurchases.

## 2.3 Cash credibility haircut

Determine how much \$1.00 of positive excess cash and non-operating investments is realistically worth to minority shareholders at Year 5.

Consider:

- Actual dividend execution.
- Actual buyback execution.
- Cash repatriation.
- Reinvestment quality.
- Management's capital allocation history.
- Restrictions on cash access.

Guidance:

| Cash credibility | Base valuation credit |
| :--- | ---: |
| High trust and proven distribution discipline | 80–100% |
| Hoarded cash or weak payout discipline | 50% |
| Severe restrictions | Explicitly justified lower percentage |

Apply this haircut only to positive excess cash and non-operating investments.

Never apply the haircut to:

- Debt.
- Negative excess cash.
- NCI.
- Preferred shares.

All senior claims and shortfalls are deducted at 100%.

For foreign-listed companies, explicitly distinguish cash held offshore from cash held in jurisdictions where repatriation is restricted.

---

# Phase 3: Five-Year Operating and Valuation Model

## 3.1 Business-type valuation router

Select the valuation engine based on the economics of the business.

- **Standard operating companies with positive NOPAT:** Unlevered EV/NOPAT.
- **Pre-profit or negative-NOPAT companies:** Explicit operating bridge to positive Year 5 normalized NOPAT or EV/Sales based on steady-state margins and unit economics.
- **Banks and insurers:** P/B anchored to normalized ROE and excess capital returns.
- **Deep cyclicals:** Normalize Year 5 margins to mid-cycle levels.
- **Heavy reinvestment compounders:** Use EV/FCF or steady-state unit economics when more representative.

Do not apply an EV/NOPAT multiple to negative earnings.

## 3.2 Three survival operating scenarios

Construct:

1. Bear — Stagnation.
2. Base — Normalized.
3. Bull — Cycle Recovery / Stronger Growth.

These are scenarios conditional on survival.

Existential risks belong exclusively in Phase 4.

For each scenario, project Years 1–5:

- Revenue.
- Revenue growth.
- Clean EBIT margin.
- Clean EBIT.
- Normalized tax.
- NOPAT.
- Reinvestment.
- Unlevered FCF.
- Net interest.
- Dividends.
- Buybacks.
- Debt.
- Excess cash.
- Diluted shares.

Revenue growth must decay naturally as the business approaches its addressable market.

Never extrapolate hyper-growth linearly for five years without strong evidence.

## 3.3 Unlevered NOPAT

$$
\text{NOPAT}_t = \text{Clean EBIT}_t \times (1 - \text{Normalized Tax Rate})
$$

Use Phase 1 normalized tax assumptions unless a clearly justified change is forecast.

## 3.4 Reinvestment tied to revenue expansion

Anchor Sales-to-Capital directly to Phase 1 filing-derived historical and incremental ratios.

$$
\text{Reinvestment}_t = \frac{\max(\text{Revenue}_{t+1} - \text{Revenue}_t,\ 0)}{\text{Sales-to-Capital}}
$$

$$
\text{Unlevered FCF}_t = \text{NOPAT}_t - \text{Reinvestment}_t
$$

Reinvestment in Year $t$ funds the following year's revenue growth.

For Year 5:

$$
\text{Revenue}_6 = \text{Revenue}_5 \times (1 + g)
$$

where $g$ is the terminal revenue growth assumption.

Do not allow negative reinvestment in a shrinking Bear Case unless working-capital liquidation or capital release is explicitly documented.

Calculate:

$$
\text{Incremental ROIC} = \text{Incremental EBIT Margin} \times (1 - \text{Tax Rate}) \times \text{Sales-to-Capital}
$$

Use this to test the economic plausibility of projected reinvestment and growth.

## 3.5 SBC convention

Select one convention and use it consistently.

**Option (a) — GAAP basis (preferred default):**

- Treat SBC as a real compensation expense already deducted in GAAP EBIT and NOPAT.
- Do not add it back.
- Model zero additional gross future SBC dilution.
- Start with the filing's diluted share count.
- Every modeled dollar of buybacks retires shares.
- Use GAAP peer multiples.

**Option (b) — Non-GAAP basis:**

- Add SBC back to operating earnings.
- Model full gross annual share dilution from SBC.
- Use Non-GAAP peer multiples.

Never deduct SBC from earnings while also modeling the same compensation as additional gross share dilution.

Match the selected convention across Phase 1 owner earnings, Phase 2 diagnostic reporting, Phase 3 earnings and share count, and Phase 3 peer multiples.

## 3.6 Diluted share count and executed buyback discipline

Start from the latest filing's diluted ordinary share or ADS count.

Never use a lower basic count unless no dilutive instruments exist and this is explicitly justified.

Cap modeled annual buybacks at the historical executed pace established in Phase 2.

Exceeding that pace requires an explicit and concrete catalyst.

### One-pass repurchase calculation

Avoid circular buyback pricing:

1. Calculate initial Year 5 terminal value per share assuming zero future buybacks.
2. Calculate average repurchase price as the midpoint between today's USD share price and the unadjusted Year 5 USD value.
3. Calculate retired shares from the scenario's modeled buyback amount.
4. Recalculate final diluted Year 5 shares and terminal value per share once.

Do not iterate or optimize the result.

Apply the method consistently to the Base, Bear and Bull scenarios and the $3 \times 3$ cross-grid.

## 3.7 Five-year cash and debt roll-forward

Track excess cash, non-operating investments and debt separately.

$$
\text{Year 5 Excess Cash} = \text{Starting Excess Cash} + \sum_{t=1}^{5} \text{Unlevered FCF}_t + \sum_{t=1}^{5} \text{AfterTax NetInterest}_t - \sum_{t=1}^{5} \text{Gross Dividends}_t - \sum_{t=1}^{5} \text{Buybacks}_t + \Delta \text{Debt}_{1-5}
$$

After-tax net interest is cash interest income less debt interest expense, adjusted for the applicable tax treatment.

A positive change in debt represents net new borrowing. A negative change represents net debt repayment.

Track resulting Year 5 debt separately.

Track non-operating investments separately from cash. Never count the same investment twice.

Gross dividends are deducted from corporate cash. Investor dividends in the IRR calculation are net of applicable withholding taxes and depositary/custody fees.

Do not assume restricted or trapped cash can fund distributions.

A cash funding shortfall must remain negative excess cash and be deducted at 100% in the terminal equity bridge.

Convert the entire roll-forward into USD using the valuation-date FX rates.

## 3.8 Year 5 terminal valuation

For standard operating companies:

$$
\text{Year 5 Enterprise Value} = \text{Year 5 Unlevered NOPAT} \times \text{Exit EV/NOPAT}
$$

Credited excess cash:

$$
\text{Credited Excess Cash} = \max(\text{Excess Cash}, 0) \times \text{Cash Haircut} + \min(\text{Excess Cash}, 0)
$$

Equity bridge:

$$
\text{Equity Bridge} = \text{Credited Excess Cash} + (\text{NonOp Investments} \times \text{Investment Haircut}) - \text{Total Debt} - \text{NCI} - \text{Preferred Shares}
$$

Terminal value per tradable share:

$$
\text{Terminal Value Per Share} = \frac{\text{Enterprise Value} + \text{Equity Bridge}}{\text{Year 5 Diluted Shares}}
$$

All negative excess cash, debt, NCI and preferred shares are deducted at 100%.

Do not apply the positive cash haircut to senior claims.

All amounts are in USD.

## 3.9 Exit multiple benchmarking

Exit multiples are conditional on survival.

Follow this deterministic anchor order.

### Anchor 1 — Named global peers

Provide:

- Specific global peers (use the date-stamped Named Peer Group table in `Peers.md` whenever present).
- Exact market-data date.
- GAAP or Non-GAAP basis.
- Through-cycle EV/NOPAT (or P/B for financials) multiples.
- Median peer multiple.
- Differences in growth and reinvestment ROIC.

Peer discounts may reflect differences in growth and reinvestment economics only.

Never embed governance, capital allocation or geopolitical risk discounts into survival multiples.

Flag peers trading at cyclical peaks or troughs.

### Anchor 2 — Company's own historical multiple band

When peer data is unavailable, use the company's own 5–10-year historical valuation range, where derivable.

Show historical low, median and high multiples and locate the proposed exit multiple within the range.

### Anchor 3 — Trailing theoretical formula cross-check

Use as an economic sanity check.

$$
\text{Implied Trailing EV/NOPAT} = \frac{(1 + g)\left(1 - \frac{g}{\text{RONIC}}\right)}{r - g}
$$

Where:

- $g$ = normalized terminal NOPAT growth rate.
- $\text{RONIC}$ = terminal incremental return on new invested capital.
- $r$ = independently justified institutional/market long-run nominal required-return assumption.

RONIC is an assumption about returns on future incremental investment, not legacy sunk capital.

The $r$ assumption is used exclusively for the theoretical trailing valuation cross-check. It is not a personal required return, investor hurdle or investment verdict.

Do not calculate CAPM, Beta, Cost of Equity or WACC.

Show the formula's implied multiple at:

$$
r - 200\text{ bps}, \quad r, \quad r + 200\text{ bps}
$$

Ensure $r > g$, and explain the economic validity of the RONIC assumption.

If the selected Base exit multiple deviates by more than 20% from the theoretical cross-check, explicitly justify the difference.

### Offline fallback

If live peer data is unavailable and peer multiples have not been supplied:

- Mark Anchor 1 as `"not provided — offline fallback active."`
- Use Anchor 2 when historical data is available.
- Use the trailing formula as an economic sanity check.
- Explicitly flag the absence of peer triangulation.

Never invent peer multiples or historical valuation bands.

## 3.10 Fundamental scenario sanity checks

For each scenario, assess:

**Mauboussin base-rate check**

Compare projected revenue growth and operating margins with historical reference-class distributions for companies of similar scale and maturity.

Justify assumptions in the top decile of historical outcomes.

**Greenwald moat and margin durability**

Assess customer captivity, switching costs, scale economies, intellectual property, network effects and regulatory licenses.

Explain whether projected margins can persist without competitive erosion.

**Physics versus sentiment**

Separate business performance from valuation multiple changes.

The Bear exit multiple must price operating stagnation, not existential catastrophe risk.

## 3.11 Physics-versus-sentiment $3 \times 3$ cross-grid

Build a complete $3 \times 3$ table.

| Year 5 NOPAT | Bear Exit Multiple | Base Exit Multiple | Bull Exit Multiple |
| :--- | ---: | ---: | ---: |
| Bear NOPAT | USD IRR | USD IRR | USD IRR |
| Base NOPAT | USD IRR | USD IRR | USD IRR |
| Bull NOPAT | USD IRR | USD IRR | USD IRR |

For every cell use:

- Stated row NOPAT.
- Stated column exit multiple.
- Corresponding scenario cash and debt balances.
- Consistent NCI and preferred shares.
- Consistent diluted share-count method.
- Corresponding one-pass buyback calculation.
- Identical valuation-date FX assumptions.
- Relevant dividend cash flows.

Recalculate every cell; do not interpolate.

The center cell `(Base NOPAT, Base Multiple)` must match the Phase 3 Base Case USD IRR exactly.

Quote the Base Case valuation and return range directly from this cross-grid.

## 3.12 USD currency treatment

Use the valuation-date spot FX rate consistently across all five years.

No home-currency calculation, future FX scenario or separate FX sensitivity strip is required.

The entire valuation is expressed in USD.

---

# Phase 4: Catastrophe Risk, Scenario Probabilities & Expected Return

## 4.1 Four-state probability model

Model four mutually exclusive states:

1. Catastrophe.
2. Bear.
3. Base.
4. Bull.

Probabilities must sum to 100%.

Bear, Base and Bull operating multiples are conditional on survival. The four probabilities represent the complete unconditional outcome distribution.

Derive probabilities from concrete evidence, including:

- Audit quality.
- Legal ownership and listing structure.
- Financial leverage.
- Interest coverage.
- Regulatory and sanctions exposure.
- Customer concentration.
- Cash accessibility.
- Insolvency risk.
- Governance and control rights.

Never assign unsupported default probability estimates.

### Evidence-gated probability bounds

A Catastrophe probability of 2% or less is permissible only when structural triggers are absent, including:

- Clean audit opinion.
- Developed-market direct equity ownership.
- Interest coverage above 3x.
- No acute regulatory or sanctions exposure.

VIE structure, high leverage, audit qualifications, acute regulatory concentration and other structural risk factors require higher evidence-supported probabilities.

Cyclical, rate-sensitive or leveraged businesses must receive a meaningful Bear probability, normally at least 20–25%.

Explain the evidence behind all four probabilities.

## 4.2 Catastrophe event timing and recovery

State an explicit assumed event year $t_{\text{cat}}$, for example Year 2 or Year 3.

Use the projected balance sheet at that exact event year, not the Year 5 balance sheet.

Calculate:

$$
\text{Catastrophe Equity Value}_{t_{\text{cat}}} = \max\left(0,\ \text{Gross Offshore Unrestricted Cash}_{t_{\text{cat}}} \times \text{Recovery \%} + \text{Recoverable NonDomestic Assets}_{t_{\text{cat}}} - \text{Senior Claims}_{t_{\text{cat}}}\right)
$$

Use gross offshore unrestricted cash.

Do not first deduct debt from cash and then deduct it again as a senior claim.

Recoverable non-domestic assets must exclude cash already included separately.

Senior claims include applicable debt, NCI, preferred shares and other documented senior claims.

Calculate actual recovery from the available evidence and explicit assumptions.

Do not assume automatic 100% equity loss.

Distinguish partial impairment from total loss.

For foreign-listed companies, identify whether offshore cash and non-domestic assets are legally accessible to security holders under the catastrophe scenario.

## 4.3 Catastrophe cash-flow timing

For the Catastrophe scenario:

- Include net dividends actually collected before the event.
- Include terminal recovery at the exact event year.
- Show zero cash flows thereafter.
- Convert every cash flow to USD using valuation-date spot FX.

The catastrophe recovery must be based on the projected balance sheet at the event year.

## 4.4 Unified dated-cash-flow IRR

For each survival scenario:

$$
CF_0 = -\text{USD Entry Price}
$$

$$
CF_{1\text{--}4} = \text{USD Annual Net Dividends}
$$

$$
CF_5 = \text{USD Year 5 Terminal Value} + \text{USD Year 5 Net Dividend}
$$

For Catastrophe, place recovery at $t_{\text{cat}}$ after prior dividends, with zero subsequent cash flows.

Calculate true dated-cash-flow IRR for each scenario.

Net dividends must deduct:

- Dividend withholding tax.
- ADR depositary fees.
- Custody fees.

When dividends are zero, the survival-case calculation simplifies to:

$$
\text{IRR} = \text{MOIC}^{1/5} - 1
$$

Do not use this simplification when intermediate cash flows exist.

All cash flows and returns are USD-denominated.

## 4.5 Scenario return table

| Scenario | Probability | Year 5 NOPAT | Exit Multiple | Terminal/Recovery Value per Share (USD) | MOIC | Total Return | IRR |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Catastrophe | | — | — | Recovery at $t_{\text{cat}}$ | | | |
| Bear | | | | | | | |
| Base | | | | | | | |
| Bull | | | | | | | |

For Catastrophe, total proceeds include prior net dividends and event-year recovery.

Report MOIC as cumulative USD proceeds divided by original USD entry capital.

Report the actual realization horizon. MOIC describes cumulative proceeds, while IRR accounts for the timing of those proceeds.

## 4.6 Probability-weighted Expected IRR

Do not calculate Expected IRR by taking the weighted average of individual scenario IRRs.

Construct the annual probability-weighted USD cash-flow vector:

$$
CF_0 = -\text{USD Entry Price}
$$

$$
CF_t = \sum_i p_i CF_{i,t}, \quad t = 1, \ldots, 5
$$

Then:

$$
\text{Expected IRR} = \text{IRR}(CF_0, CF_1, \ldots, CF_5)
$$

Catastrophe recovery is placed at the actual event year.

Survival terminal values are placed in Year 5.

This is the required probability-weighted Expected IRR calculation.

Also calculate:

$$
\text{Expected MOIC} = \sum_i p_i \text{MOIC}_i
$$

Label this as probability-weighted cumulative nominal proceeds. Since different states may have different realization dates, do not interpret this as a common-horizon annualized return.

## 4.7 Base-probability sensitivity

Recalculate Expected IRR under:

- Base probability reduced by 10 percentage points.
- Original probability distribution.
- Base probability increased by 10 percentage points.

Reallocate the probability difference proportionally between Bear and Catastrophe, keeping Bull unchanged and ensuring that all probabilities remain non-negative and sum to 100%.

Show the revised probabilities and recalculated Expected IRRs.

Do not apply a personal return threshold or verdict.

## 4.8 Full return distribution

Report:

**Base Case valuation and return range**

Use the $3 \times 3$ cross-grid to provide a realistic range around the Base Case point estimate.

**Median outcome**

Order the four scenarios from lowest to highest MOIC.

Identify the exact scenario where cumulative probability crosses 50%.

**Probability of capital loss**

$$
P(\text{Capital Loss}) = \sum_{i: \text{MOIC}_i < 1} p_i
$$

**5th-percentile outcome**

Order scenarios by MOIC and identify the scenario where cumulative probability first crosses 5%.

**Expected value versus median**

Explain the difference between the probability-weighted expected outcome and the median scenario.

Do not confuse expected return with the most likely outcome.

---

# Phase 5: Quantitative Return Assessment & Thesis-Killers

This phase consolidates the numerical investment economics. It does not introduce portfolio-management decisions.

## 5.1 Asymmetry ratio

Calculate across all four states:

$$
\text{Expected Gain} = \sum_i p_i \max(\text{MOIC}_i - 1, 0)
$$

$$
\text{Expected Loss} = \sum_i p_i \max(1 - \text{MOIC}_i, 0)
$$

$$
\text{Asymmetry Ratio} = \frac{\text{Expected Gain}}{\text{Expected Loss}}
$$

If Expected Loss is zero, report `"No modeled capital-loss state."`

Use the same scenario MOIC definitions as Phase 4.

## 5.2 Consolidated numerical results

Report:

- Current security price in original trading currency.
- Current security price in USD.
- Current spot FX rates.
- Base Case intrinsic value per share/ADS in USD.
- Bear, Base and Bull terminal values in USD.
- Catastrophe recovery value in USD.
- All four scenario probabilities.
- All four scenario MOICs.
- All four scenario IRRs.
- Probability-weighted Expected MOIC.
- Probability-weighted Expected IRR.
- Probability of capital loss.
- Median outcome.
- 5th-percentile outcome.
- Asymmetry ratio.
- Base-probability sensitivity.

The final conclusion should describe the numerical return distribution and identify the key assumptions responsible for the valuation.

Do not introduce personal hurdle rates, buy/sell verdicts, passive-index comparisons, opportunity-cost comparisons or portfolio recommendations.

## 5.3 Three concrete thesis-killers

Identify three measurable operating or financial developments that would invalidate the valuation assumptions.

Examples:

- Cash conversion deteriorating below a specified level.
- Clean EBIT margin falling below a specified level.
- Executed buybacks materially diverging from the modeled pace.
- Revenue growth falling below modeled assumptions.
- Debt or interest coverage deteriorating beyond the modeled range.

Use company-specific quarterly KPIs and explicit numerical thresholds.

Do not invent arbitrary thresholds. Anchor them to historical performance, operating economics or the forecast assumptions.

---

# Phase 6: Mandatory Mathematical & Consistency Audit

Before finalizing, verify every calculation.

| Audit Check | Status | Evidence / Line Check |
| :--- | :---: | :--- |
| 1. No CAPM, Beta, WACC or Cost of Equity used; trailing cross-check uses independently justified $r$ and displays $r \pm 200$ bps | PASS/FAIL | |
| 2. No personal hurdle, portfolio constraints, concentration limits, position sizing or Quarter-Kelly calculations included | PASS/FAIL | |
| 3. ADS/share-class ratio explicitly stated; diluted Year 5 shares use one tradable unit | PASS/FAIL | |
| 4. All financial statements and security prices translated into USD using valuation-date spot FX | PASS/FAIL | |
| 5. Historical reported USD translations and old document FX rates are not mixed into the analytical USD conversion | PASS/FAIL | |
| 6. Invested Capital uses Parent Stockholders' Equity without double-counting NCI | PASS/FAIL | |
| 7. Cash haircut applies only to positive excess cash; shortfalls, debt, NCI and preferred shares are deducted at 100% | PASS/FAIL | |
| 8. Lease treatment is consistent across EBIT, Invested Capital and Debt | PASS/FAIL | |
| 9. SBC convention matches Phase 1 owner earnings, Phase 3 diluted shares and peer multiples | PASS/FAIL | |
| 10. Sales-to-Capital is filing-anchored; reinvestment is non-negative unless explicitly documented otherwise | PASS/FAIL | |
| 11. Buybacks are capped at executed historical pace and use the one-pass price calculation | PASS/FAIL | |
| 12. Exit multiple follows Anchor 1 → Anchor 2 → Formula; deviations above 20% are explained | PASS/FAIL | |
| 13. All nine $3 \times 3$ cross-grid cells reconcile; center cell equals the Base Case IRR | PASS/FAIL | |
| 14. Catastrophe uses gross offshore cash at an explicit event year and deducts senior claims exactly once | PASS/FAIL | |
| 15. Probabilities sum to 100%; probability sensitivity and distribution statistics reconcile | PASS/FAIL | |
| 16. Expected IRR uses the weighted dated USD cash-flow vector, not weighted scenario IRRs | PASS/FAIL | |
| 17. Executive Summary, Phase 3, Phase 4, Phase 5 and `valuation-summary-json` reconcile exactly | PASS/FAIL | |

If any check fails, correct the calculation before presenting the valuation conclusion.

## valuation-summary-json

Conclude at the very end of the report with a single-line HTML comment containing a valid JSON object using **these exact keys** (numeric values without `%` or `$` signs), reconciling exactly with the audited tables:

`<!-- valuation-summary-json {"valuation_date":"YYYY-MM-DD","ticker":"TICKER","company_name":"Name","share_class_ads_ratio":"1 ADS = N ordinary shares","reporting_currency":"CNY","trading_currency":"USD","reporting_to_usd_fx":1.0,"trading_to_usd_fx":1.0,"current_price_original":0.0,"current_price_usd":0.0,"diluted_shares_m":0.0,"target_year":2031,"base_price_usd":0.0,"bear_price_usd":0.0,"bull_price_usd":0.0,"catastrophe_recovery_usd":0.0,"catastrophe_year":2029,"prob_catastrophe_pct":0.0,"prob_bear_pct":0.0,"prob_base_pct":0.0,"prob_bull_pct":0.0,"catastrophe_moic":0.0,"bear_moic":0.0,"base_moic":0.0,"bull_moic":0.0,"expected_moic":0.0,"catastrophe_total_return_pct":0.0,"bear_total_return_pct":0.0,"base_total_return_pct":0.0,"bull_total_return_pct":0.0,"expected_total_return_pct":0.0,"catastrophe_irr_pct":0.0,"bear_irr_pct":0.0,"base_irr_pct":0.0,"bull_irr_pct":0.0,"expected_irr_pct":0.0,"prob_capital_loss_pct":0.0,"median_outcome":"Base","fifth_percentile_outcome":"Catastrophe","asymmetry_ratio":0.0,"base_prob_minus10_irr_pct":0.0,"base_prob_plus10_irr_pct":0.0} -->`

---

# Optional Module: Cross-Border, Emerging-Market and VIE Risk

Activate only for foreign-listed companies, ADRs, emerging markets or structures with material cross-border ownership risk.

## Legal structure and cash repatriation

Verify whether investors own direct equity or VIE contractual claims.

Separate onshore restricted cash from offshore unrestricted cash.

Identify cash actually available to foreign shareholders.

Deduct applicable repatriation and withholding taxes when projecting distributable cash or recovery value.

Translate all relevant amounts into USD using valuation-date spot FX.

## Listing, custody and conversion

Document:

- ADR depositary fees.
- Dividend withholding taxes.
- Ordinary-share/ADS conversion ratio.
- Fungibility into Hong Kong or local ordinary shares.
- Custody and conversion restrictions.

Factor applicable fees and withholding taxes directly into investor cash flows.

## Tail-risk isolation

VIE invalidation, forced unconvertible delisting, capital-control expropriation and similar existential risks belong exclusively in Phase 4's Catastrophe state.

Never embed these risks in the survival exit multiples.

---

# Data Quality, Source Rules & Offline Fallback

Every numerical input must be labeled:

- `[Filing: Document, Period, Line Item]`
- `[Market Data: Source, Date]`
- `[Assumption]`

Show derived calculations and the exact underlying source figures.

Use USD throughout all analytical calculations.

Never invent missing figures.

Write `"not provided"` whenever required data is absent.

## Missing current price

Check attached materials for a recent stock price.

If no current price is available in the attachments, obtain the latest available market price where live data access is available.

If no live market access is available and no recent price has been supplied, stop before the return calculations and request the missing price.

## Missing FX

Obtain the latest available spot rate for every currency that needs conversion into USD.

If a required FX rate cannot be obtained, mark it `"not provided"` and stop the affected valuation and return calculations rather than inventing an exchange rate.

## Missing peer multiples

Use the explicit offline fallback in Phase 3.

Do not invent peer multiples, historical valuation multiples or market data.

## Missing financial figures

Write `"not provided"` rather than estimating or manufacturing missing filing data.

Any necessary forward-looking estimate must be identified explicitly as an `[Assumption]`, not presented as a reported historical fact.

## Final governing principle

**Value the operating business using normalized fundamentals. Value the equity using a complete and consistent balance-sheet bridge. Calculate the potential investment return from today's market price using explicit operating scenarios, survival exit multiples, current USD conversion rates and evidence-based probabilities.**

The purpose is to establish the intrinsic value of the stock and the realistic probability-weighted distribution of its USD returns.
