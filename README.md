# FinancialAdvisor — Equity Research & DeepSeek AI Workbench

A Streamlit + CLI equity research toolkit for personal portfolios (~10 stocks) that combines **SEC EDGAR filings**, **redundant earnings call transcripts (Prepared Remarks + Q&A)**, **free DefeatBeta fundamentals & DCF valuation**, and a **DeepSeek AI Analyst (1M-token context + prompt caching)** into a local Markdown dossier under `data/<TICKER>/`.

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

### 📈 Fundamentals, Peers & DCF Valuation (100% Free via DefeatBeta)
- **`CompanyBrief.md`** — deterministic, currency-labelled snapshot (P/E, P/S, P/B, PEG, ROIC/ROE/ROA, WACC, margins, YoY growth, net debt, segment breakdown, next earnings date).
- **`Peers.md`** — company multiples & returns vs. industry benchmarks.
- **`Valuation/`** — `dcf_data.json`, `DCF_Summary.md` (with negative-FCF / financial-sector caveats), and an editable live-formula Excel DCF workbook (`<TICKER>_DCF.xlsx`).

### 🧠 DeepSeek AI Equity Analyst (`deepseek-flash` & `deepseek-v4-pro`)
- Leverages DeepSeek's **1M-token context window** and **prompt caching** (~$0.003/1M cached input tokens off-peak) — no vector database or chunking required.
- **4 One-Click Institutional Workflows** (saved to `data/<TICKER>/Analysis/`):
  1. **🎯 DCF vs. Guidance Reality Check** — cross-examines DCF assumptions against verbatim management guidance in transcripts.
  2. **🕵️ Management Credibility & Promise Tracker** — diffs consecutive quarters to spot delivered promises vs. disappearing KPIs & dodged Q&A.
  3. **🚩 Red-Flag & Accounting Risk Scanner** — checks GAAP/Non-GAAP gaps, dilution, VIE/governance risks, and peer anomalies.
  4. **📑 Full Investment Thesis Memo** — synthesizes the entire dossier into a Bull/Base/Bear IC memo.
- **💬 Prompt-Cached Dossier Chat** — ask follow-up questions grounded in attached files with live token & USD cost estimates.

### 📦 Portfolio Command Center
- **Free Preflight** (`Tickers` parallel batch): view upcoming earnings dates and a ticker × quarter coverage matrix *before* spending any API quota.
- One-click batch downloads for SEC filings, transcripts, and Fundamentals/DCF packs across your watchlist.

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
    ├── CompanyBrief.md                # Currency-labelled fundamentals & trends
    ├── Peers.md                       # Company vs. industry multiples
    ├── Coverage.md                    # SEC EDGAR index vs. local coverage audit
    ├── Valuation/
    │   ├── dcf_data.json              # Raw DCF assumptions & fair price
    │   ├── DCF_Summary.md             # LLM-ready DCF summary + caveats
    │   └── <TICKER>_DCF.xlsx          # Editable Excel DCF model
    ├── EarningsTranscripts/           # Speaker-labeled call transcripts (.md)
    ├── DataFromDataMule_markdown/     # Cleaned SEC filings (.md)
    └── Analysis/                      # Saved DeepSeek AI memos (.md)
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
