# SEC Data Downloader

A Streamlit-powered tool to download **SEC filings** and **earnings call transcripts** for any public company, convert them to clean Markdown, and feed them into AI analysis tools like Cherry Studio.

---

## Features

### 📄 SEC Financial Documents
- Download annual reports (**10-K**, **20-F**), quarterly reports (**10-Q**), current/earnings reports (**6-K**, **8-K**), IPO registration statements (**F-1**, **S-1**), shelf registrations (**F-3**, **S-3**, **F-4**, **S-4**), and prospectuses (**424B1–424B4**).
- **Smart filtering** — automatically skips junk documents (XBRL taxonomies, SOX certifications, legal opinions, voting agreements, subsidiary lists, employment contracts, etc.).
- **IPO deduplication** — for amended registration statements (F-1/A, S-1/A), only the latest version is kept.
- **Descriptive filenames** — e.g. `AAPL - 10-K FY 2024.md`, `YB - Q2 2025.md`, `HUIZ - Earnings Release 2025-03-15.md`.
- **Markdown conversion** — every document is converted to clean, readable Markdown via the [datamule](https://pypi.org/project/datamule/) library.
- **Year range control** — choose how many recent years to include (default: 2, max: 30).

### 🎙️ Earnings Call Transcripts
- Scrapes transcripts from [discountingcashflows.com](https://discountingcashflows.com) (free, no API key needed).
- Uses **Playwright** to automate the browser — handles WAF challenges automatically.
- **Cookie persistence** — only need to solve CAPTCHA once; cookies are saved for future runs.
- **Year range control** — same year selector applies to transcripts as well.
- Output is cleaned (excessive whitespace removed) and saved as Markdown files.

### 📁 Output Structure
All data is saved under the script's directory, organized by ticker:

```
<repo-root>/
├── <TICKER>/
│   ├── DataFromDataMule/              # Raw SEC filing data
│   ├── DataFromDataMule_markdown/     # Converted SEC filings (.md)
│   └── EarningsTranscripts/           # Earnings call transcripts (.md)
```

---

## Quick Start

### Prerequisites
- **Python 3.10+** installed on your system ([python.org](https://www.python.org/downloads/))
- Windows (the `start.bat` is Windows-native; Linux/Mac users can run the Python commands manually)

### One-Click Launch (Windows)
1. Clone or download this repository.
2. Double-click **`start.bat`**.
3. The script will:
   - Install all Python dependencies (`pip install -r requirements.txt`)
   - Download the Playwright Chromium browser engine
   - Launch the Streamlit app in your browser at `http://localhost:8501`

### Manual Launch (Any OS)
```bash
pip install -r requirements.txt
playwright install chromium
streamlit run app.py
```

---

## Usage

### Download SEC Filings
1. Select the **form types** you want (default: 20-F, 10-K, 10-Q, 6-K, 8-K).
2. Optionally add custom form types in the text field.
3. Enter a **stock ticker** (e.g., `AAPL`, `YB`, `HUIZ`).
4. Set the **years of history** (1 = current year only, 2 = current + last year, etc.).
5. Click **"Download & Convert to Markdown"**.
6. Wait for the status to complete — files appear in `DataFromDataMule_markdown/`.

### Download Earnings Transcripts
1. Enter or confirm the **ticker** in the transcripts section.
2. Click **"Download Earnings Transcripts"**.
3. A browser window will open. On first run, you may see a WAF security challenge — wait for it to resolve automatically, or click "Verify" if needed.
4. Cookies are saved after the first run; subsequent runs will be silent.
5. Transcripts appear in `EarningsTranscripts/`.

### Standalone Scripts
- **`download_data.py`** — CLI version of the SEC downloader. Edit the `TICKER` and `SUBMISSION_TYPES` at the top of the file, then run:
  ```bash
  python download_data.py
  ```
- **`scrape_transcripts.py`** — CLI transcript scraper:
  ```bash
  python scrape_transcripts.py AAPL 2
  ```
  Second argument is the number of years (optional, defaults to all).

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Main Streamlit UI — SEC downloader + transcript downloader |
| `download_data.py` | Standalone CLI script for SEC filings (edit ticker at top) |
| `scrape_transcripts.py` | Standalone CLI script for earnings transcripts |
| `markdown_cleanup.py` | Table cleanup utility used by the SEC converter |
| `requirements.txt` | Python package dependencies |
| `start.bat` | One-click launcher for Windows (installs deps + runs app) |
| `.gitignore` | Ignores `__pycache__`, `.cookies.json`, build artifacts, etc. |

---

## Integration with Cherry Studio / AI Tools

This tool pairs well with the [local_rag.py / api_server.py](https://github.com/kamathhrishi/finance-agent) ReAct agent setup:

1. Download filings/transcripts for a ticker using this app.
2. Point the ReAct agent to the ticker's folder.
3. The LLM can then `ls`, `grep`, and `read_file` to answer questions — no chunking or vector DB needed.

---

## Notes
- **SEC API rate limits**: The datamule library handles this gracefully. If a form type doesn't exist for a company, it's silently skipped.
- **Transcript source**: discountingcashflows.com is free for personal use. Respect their terms.
- **No API keys required**: Everything works with free, public data sources.
