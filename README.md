# FinancialAdvisor — SEC Filings & Earnings Transcripts Downloader

A Streamlit + CLI toolkit to download **SEC EDGAR filings** and **earnings call transcripts (Prepared Remarks + Q&A)** for your portfolio, convert them into clean, LLM-ready Markdown, and feed them into AI research tools (Cherry Studio, Claude, ReAct agents, etc.).

---

## Features

### 📄 SEC EDGAR Filings
- Downloads annual reports (**10-K**, **20-F**, amendments), quarterly reports (**10-Q**), current/earnings releases (**6-K**, **8-K**), proxy statements (**DEF 14A**), IPO/shelf registrations (**F-1/S-1/F-3/S-3/F-4/S-4**), prospectuses (**424B1–424B4**), and custom form types.
- **Date-range filtering at download time** — passes `filing_date` directly to `datamule` so you only download the years you ask for.
- **Smart exhibit filtering & IPO deduplication** — skips XBRL/SOX/legal boilerplate while preserving all `EX-99.x` earnings releases without filename collisions.
- **Clean Markdown tables** — automatically strips SEC's duplicate dollar-column and spacer-column artifacts without losing data.

### 🎙️ Redundant Earnings Call Transcripts (No Scraping / No Playwright)
Uses a **4-tier, quota-preserving waterfall** built for free personal API keys:

| Priority | Provider | Free Tier | Role |
|---|---|---|---|
| 0 | **Local Disk Cache** | Unlimited | Skips quarters already saved (`0` API calls) |
| 1 | **[`defeatbeta-api`](https://pypi.org/project/defeatbeta-api/)** (Hugging Face) | Unlimited, no key | Bulk historical backfill (10+ years, speaker-labeled) |
| 2 | **[ROIC.ai API v3](https://www.roic.ai/api/docs/earnings-calls)** | 5 req/min, latest 2 calls | Fast coverage (~1h after call) for newest quarters |
| 3 | **[Alpha Vantage](https://www.alphavantage.co/documentation/)** | 25 req/day | Any missing quarter since 2010Q1 (speaker + sentiment) |
| 4 | **[Firecrawl v2](https://docs.firecrawl.dev/features/alexandria)** | 1,000 credits/month | Last-resort fallback if the first 3 miss a quarter |

### 📦 Portfolio Batch Mode & File Browser
- Paste your watchlist (e.g., a 10-stock portfolio) and download SEC filings + transcripts in one click while respecting free-tier rate limits.
- Preview and download generated Markdown files directly in the Streamlit UI.

---

## Quick Start

### Windows (One-Click)
Double-click **`start.bat`**. It creates an isolated `.venv`, installs dependencies, and opens the app at `http://localhost:8501`.

### Manual Launch (Any OS)
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

---

## Configuring API Keys (Local & Gitignored)

API keys are stored locally in `.secrets.json` (which is **gitignored** and never committed):

- **Via UI:** Open the sidebar → **🔑 Add / Update API Keys** → paste keys → **Save API Keys**.
- **Via Environment Variables:** `ROIC_API_KEY`, `ALPHAVANTAGE_API_KEY`, `FIRECRAWL_API_KEY`.
- **Via `Secrets.txt`:** If a local gitignored `Secrets.txt` exists in the repo root, keys are auto-imported into `.secrets.json` on first run.

---

## CLI Usage

```bash
# Download SEC filings (default: last 2 years)
python download_data.py AAPL 2
python download_data.py YB 3 --forms 20-F,6-K,F-1

# Download earnings call transcripts using the redundant waterfall
python scrape_transcripts.py AAPL 2
python scrape_transcripts.py YB 2 --providers defeatbeta,roic,alphavantage
```

---

## Output Structure

All downloads live under `data/` (gitignored):

```text
data/
└── <TICKER>/
    ├── DataFromDataMule/              # Raw SEC EDGAR submissions
    ├── DataFromDataMule_markdown/     # Cleaned SEC filings (.md)
    └── EarningsTranscripts/           # Earnings call transcripts (.md)
```

---

## Running Tests

```bash
pytest -v
```
