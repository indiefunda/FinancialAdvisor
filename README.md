# FinancialAdvisor — Equity Research & DeepSeek AI Workbench

A Streamlit + CLI equity research toolkit for personal portfolios (~10 stocks) that combines **SEC EDGAR filings**, **redundant earnings call transcripts (Prepared Remarks + Q&A)**, **free DefeatBeta fundamentals, peers & valuation packs**, and the editable **V6 USD intrinsic-valuation protocol** run by **DeepSeek AI (1M-token context + prompt caching)** — all assembled into a local Markdown dossier under `data/<TICKER>/`.

---

## Features

### 📄 SEC EDGAR Filings + Coverage Audit
- Downloads annual reports (**10-K**, **20-F**, amendments), quarterly reports (**10-Q**), current/earnings releases (**6-K**, **8-K**), proxy statements (**DEF 14A**), IPO/shelf registrations, prospectuses (**424B1–424B4**), and custom forms with date-range filtering.
- Automatically cleans SEC Markdown tables (`markdown_cleanup.py`) without dropping legitimate columns or sparse notes.
- **SEC Coverage Audit (`Coverage.md`)**: Diffs DefeatBeta's upstream EDGAR index against your local Markdown files, surfaces EDGAR links for gaps, and highlights insider/ownership filings (`SC 13D/G`, `Form 4`, `40-F`).

### 🎙️ Redundant Earnings Call Transcripts (Catalog Oracle)
Uses a **5-stage quota-preserving waterfall** built for free personal keys:

| Priority | Provider | Free Tier | Role |
|---|---|---|---|
| 0 | **Local Disk Cache** | Unlimited | Skips valid transcripts already on disk (`0` API calls) |
| 1 | **[`defeatbeta-api`](https://pypi.org/project/defeatbeta-api/)** (Hugging Face) | Unlimited, no key | Bulk multi-year backfill + **catalog oracle** (prevents burning quota on pre-IPO / nonexistent historical calls) |
| 2 | **[ROIC.ai API v3](https://www.roic.ai/api/docs/earnings-calls)** | 5 req/min, latest 2 calls | Fast coverage (~1h after call) for newest quarters |
| 3 | **[Alpha Vantage](https://www.alphavantage.co/documentation/)** | 25 req/day | Missing quarters since 2010Q1 (speaker + sentiment) |
| 4 | **[Firecrawl v2](https://docs.firecrawl.dev/features/alexandria)** | 1,000 credits/month | Last-resort web fallback |

### 📈 Fundamentals, Peers & Valuation Pack (100% Free via DefeatBeta)
- **`ValuationPack.md`** — protocol-ready extract: USD price, ADS/share-class reconciliation alert, 8 quarters + 4–5 years of statements, 10-year multiple bands, and the Universal-USD FX rule.
- **`CompanyBrief.md`** — deterministic snapshot (P/E, P/S, P/B, PEG, ROIC/ROE/ROA, margins, YoY growth, net debt, segment breakdown, next earnings date).
- **`Peers.md`** — **Anchor 1 named peer group**: DeepSeek proposes comparable tickers, then every multiple is fetched deterministically from DefeatBeta (date-stamped, GAAP basis, with peer medians), plus industry-aggregate context and explicit `neg EV` / `neg NOPAT` handling.
- **`Valuation/`** — `dcf_data.json`, `DCF_Summary.md`, and an editable live-formula Excel DCF workbook (`<TICKER>_DCF.xlsx`), kept as a secondary cross-check.

### 🧠 DeepSeek AI Equity Analyst (`deepseek-flash` & `deepseek-v4-pro`)
- Leverages DeepSeek's **1M-token context window** and **prompt caching** (~$0.003/1M cached input tokens off-peak) — no vector database or chunking required.
- **5 One-Click Institutional Workflows** (saved to `data/<TICKER>/Analysis/`):
  1. **🧮 5-Year Intrinsic Valuation & Return Model (USD)** — runs the editable **V6 protocol**: forensic baseline, three survival scenarios, `3×3` physics-vs-sentiment grid, catastrophe state, probability-weighted USD IRR, and the 17-point consistency audit.
  2. **🎯 DCF vs. Guidance Reality Check** — cross-examines DCF assumptions against verbatim management guidance in transcripts.
  3. **🕵️ Management Credibility & Promise Tracker** — diffs consecutive quarters to spot delivered promises vs. disappearing KPIs & dodged Q&A.
  4. **🚩 Red-Flag & Accounting Risk Scanner** — checks GAAP/Non-GAAP gaps, dilution, VIE/governance risks, and peer anomalies.
  5. **📑 Full Investment Thesis Memo** — synthesizes the entire dossier into a Bull/Base/Bear IC memo.
- **🧮 Deterministic Python Math Engine (`valuation_math.py`)** — exposed via OpenAI-compatible function calling so DeepSeek offloads every dated-cash-flow IRR solve, 1-pass non-circular buyback calculation, 9-cell `3×3` cross-grid (both EV/NOPAT and financial P/B × ROE), break-even 0% IRR CAGR solve, `±10 pp` probability sensitivity, and asymmetry ratio to exact Python math (with unit/scale sanity validation and a `🧮 Python math verified` completion badge).
- **🔄 Seamless Auto-Continuation** — if a long report hits `finish_reason == "length"`, the client automatically continues via prompt cache (up to 2 rounds) so memos complete all 6 phases cleanly.
- **💬 Prompt-Cached Dossier Chat** — ask follow-up questions grounded in attached files (with the same Python math tools active in valuation mode) and live token & USD cost estimates.
- **📊 Executive Return Dashboard** — the memo's trailing `valuation-summary-json` block renders into a bordered top dashboard with 4 KPI metric cards (expected return, `$10,000` proceeds, base target, downside/asymmetry), a **Scenario Return Breakdown** table (`Catastrophe` / `Bear` / `Base` / `Bull` / `Weighted Expected`), and a **Median + Base ±10 pp Sensitivity** strip.

### 📐 Protocol V6 — USD Intrinsic Valuation (CAPM/WACC-Free)
`valuation_protocol.md` is versioned and editable from the UI. Its governing rules:

- **Universal USD policy** — one currency for all comparisons, projections and returns at constant valuation-date spot FX. No investor home currency, no FX forecasts, no FX sensitivity strip.
- **No CAPM, Beta, WACC or Cost of Equity** — volatility is not permanent capital impairment.
- **No personal hurdle rate, buy/sell verdict, concentration cap, position sizing or Quarter-Kelly** — the output is a valuation and a return *distribution*, not a recommendation.
- **Deterministic exit-multiple anchors** — named global peers → company's own 5–10-year band → trailing formula as an economic sanity bound (`r` justified independently, shown at `r ± 200 bps`, never used as a hurdle).
- **Probability-weighted expected return from the dated USD cash-flow vector** — never an average of scenario IRRs; catastrophe recovery sits at its own event year.
- **Auditability** — Executive Return Summary first, 17-item self-audit, then a machine-readable JSON block that must reconcile with Phases 3–5.

### 📦 Portfolio Command Center
- **Free Preflight** (`Tickers` parallel batch): view upcoming earnings dates and a ticker × quarter coverage matrix *before* spending any API quota.
- One-click batch downloads for SEC filings, transcripts, and the full ValuationPack + CompanyBrief + Peers + DCF pack across your watchlist.

---

## Quick Start

### Windows (One-Click)
Double-click **`start.bat`**. It creates an isolated `.venv`, installs dependencies, and launches the app at `http://localhost:8501`.

### Manual Launch (Any OS)
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

---

## Configuring API Keys (Local & Gitignored)

API keys are stored locally in `.secrets.json` (gitignored, never committed):

- **Via UI:** Sidebar → **🔑 Add / Update API Keys** → paste keys → **Save API Keys**.
- **Via Environment Variables:** `DEEPSEEK_API_KEY`, `ROIC_API_KEY`, `ALPHAVANTAGE_API_KEY`, `FIRECRAWL_API_KEY` *(env-var keys are never written to disk)*.
- **Via `Secrets.txt`:** Put a gitignored `Secrets.txt` in the repo root (e.g. `deepseek API KEY: sk-...`) — missing keys are auto-imported into `.secrets.json` on startup.

---

## Using with Cherry Studio / Claude (Optional MCP Tip)

If you also want conversational ad-hoc queries inside **Cherry Studio** or **Claude Desktop**, `defeatbeta-api` ships an official **MCP server** and **Skills** (`github.com/defeat-beta/defeatbeta-api/tree/main/mcp`) that pair cleanly alongside this app's persistent `data/<TICKER>/` Markdown dossiers.

---

## Output Structure

All downloads and generated research live under `data/` (gitignored):

```text
data/
└── <TICKER>/
    ├── ValuationPack.md               # Protocol-ready USD extract: price, ADS check, statements, multiple bands
    ├── CompanyBrief.md                # Deterministic fundamentals & trend snapshot
    ├── Peers.md                       # Anchor 1 named peer group + industry aggregate context
    ├── Coverage.md                    # SEC EDGAR index vs. local coverage audit
    ├── Valuation/
    │   ├── dcf_data.json              # Raw DCF assumptions & fair price
    │   ├── DCF_Summary.md             # LLM-ready DCF summary + caveats
    │   └── <TICKER>_DCF.xlsx          # Editable Excel DCF model
    ├── EarningsTranscripts/           # Speaker-labeled call transcripts (.md)
    ├── DataFromDataMule_markdown/     # Cleaned SEC filings (.md)
    └── Analysis/                      # Saved DeepSeek memos (.md, ending with valuation-summary-json)
```

---

## CLI Usage

```bash
# Download & convert SEC filings (default: last 2 years)
python download_data.py AAPL 2
python download_data.py YB 3 --forms 20-F,6-K,F-1

# Download earnings call transcripts using the redundant waterfall
python scrape_transcripts.py AAPL 2
python scrape_transcripts.py YB 2 --providers defeatbeta,roic,alphavantage
```

---

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest -v                 # fast offline unit tests
pytest -m slow -v         # optional live schema contract test against defeatbeta-api
```
