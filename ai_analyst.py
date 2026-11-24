"""DeepSeek AI Intelligence Engine for FinancialAdvisor.

Connects to DeepSeek's OpenAI-compatible API (`https://api.deepseek.com`) using
`deepseek-flash` (V4.1-Flash, 1M context) or `deepseek-v4-pro` (deep reasoning,
1M context) to run grounded, citation-backed institutional equity research
workflows directly over each ticker's local dossier (`CompanyBrief.md`,
`Valuation/DCF_Summary.md`, `Peers.md`, earnings transcripts, and SEC filings).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import requests

from config_manager import PROVIDER_SPECS
from sec_downloader import DEFAULT_DATA_ROOT, sanitize_ticker, strip_sec_metadata_header

DEEPSEEK_API_URL = f"{PROVIDER_SPECS['deepseek']['base_url'].rstrip('/')}/chat/completions"
_TRANSCRIPT_PERIOD_RE = re.compile(r"Q([1-4])\s+(\d{4})", re.IGNORECASE)

# Off-peak rates per 1M tokens (USD) as of late 2026; peak hours are 2x.
DEEPSEEK_MODELS: Dict[str, Dict[str, Any]] = {
    "deepseek-flash": {
        "label": "DeepSeek Flash (V4.1-Flash — fast, 1M ctx, lowest cost)",
        "context_tokens": 1_000_000,
        "input_miss_per_1m": 0.15,
        "input_hit_per_1m": 0.003,
        "output_per_1m": 0.60,
    },
    "deepseek-v4-pro": {
        "label": "DeepSeek V4 Pro (deep reasoning, 1M ctx)",
        "context_tokens": 1_000_000,
        "input_miss_per_1m": 0.66,
        "input_hit_per_1m": 0.022,
        "output_per_1m": 1.98,
    },
}

DEFAULT_MODEL = "deepseek-flash"

VALUATION_PROTOCOL_PATH = Path(__file__).resolve().parent / "valuation_protocol.md"

SYSTEM_PROMPT_BASE = (
    "You are a senior buy-side equity research analyst. "
    "You MUST ground every number, ratio, quote, and claim strictly in the attached "
    "dossier files. Always cite the exact source filename (e.g., `[CompanyBrief.md]`, "
    "`[AAPL - Q3 2026.md]`, `[DCF_Summary.md]`). "
    "Never mix financial reporting currencies (e.g., CNY vs USD) — explicitly state the currency. "
    "If a metric is missing or `n/a`, state that clearly instead of guessing. "
    "This is analytical research, not personalized financial advice."
)

SYSTEM_PROMPT_VALUATION = (
    "You are an institutional, fundamental long-term equity analyst in the tradition of "
    "Warren Buffett, Bruce Greenwald, and Michael Mauboussin. "
    "Execute the user's attached Scientific 5-Year Equity Return & Valuation Protocol rigorously "
    "using the attached financial dossier (SEC filings, earnings transcripts, CompanyBrief.md, Peers.md). "
    "STRICTLY obey all mathematical and data-hygiene rules in the protocol: "
    "NEVER use CAPM, Beta, or WACC (ignore any WACC/Beta figures if a reference file mentions them). "
    "Tag every numerical input as `[Filing: Document, Period, Line Item]`, `[Market Data: Source, Date]`, "
    "or `[Assumption]`, or write `\"not provided\"` rather than inventing missing figures. "
    "Begin your response with a single-line HTML comment JSON summary (exact keys, numeric values without % signs) "
    "followed immediately by the plain-English Executive Return Summary (At a Glance) table showing Total % Return "
    "by target year and what $10,000 becomes:\n"
    "<!-- valuation-summary-json {\"currency\":\"USD\",\"current_price\":0.0,\"target_year\":2031,"
    "\"expected_price\":0.0,\"expected_total_return_pct\":0.0,\"expected_irr_pct\":0.0,"
    "\"base_price\":0.0,\"base_total_return_pct\":0.0,\"bear_total_return_pct\":0.0,"
    "\"bull_total_return_pct\":0.0,\"catastrophe_total_return_pct\":0.0,"
    "\"max_weight_pct\":0.0,\"verdict\":\"CLEARS\"} -->\n"
    "Show all math transparently, complete the Phase 6 Mandatory Self-Audit table at the end, "
    "and do NOT hedge with generic financial disclaimers."
)

_VALUATION_SUMMARY_RE = re.compile(
    r"<!--\s*valuation-summary-json\s*(\{.*?\})\s*-->",
    re.DOTALL,
)


def extract_valuation_summary(markdown_text: str) -> Optional[Dict[str, Any]]:
    """Parse the machine-readable `valuation-summary-json` comment emitted at the top of a valuation memo."""
    if not markdown_text:
        return None
    m = _VALUATION_SUMMARY_RE.search(markdown_text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def strip_valuation_summary_tag(markdown_text: str) -> str:
    """Remove the hidden `valuation-summary-json` comment before rendering Markdown in Streamlit."""
    if not markdown_text:
        return markdown_text
    return _VALUATION_SUMMARY_RE.sub("", markdown_text, count=1).lstrip()


@dataclass
class ValuationParameters:
    """Configurable investor parameters passed dynamically to the custom valuation protocol."""

    hurdle_rate_pct: float = 15.0
    home_currency: str = "USD"
    spot_price: str = ""
    spot_fx_rate: str = ""
    dividend_withholding_tax_pct: float = 10.0
    adr_custody_fee: str = "0.00"
    exit_cgt_pct: float = 0.0
    max_impairment_pct: float = 2.5
    single_stock_cap_pct: float = 15.0
    activate_cross_border_module: bool = False
    custom_notes: str = ""

    def to_prompt_block(self) -> str:
        """Render active investor parameters as an explicit override block for DeepSeek."""
        cgt_line = (
            f"{self.exit_cgt_pct:.2f}% (deduct personal exit capital-gains tax at Year 5)"
            if self.exit_cgt_pct > 0
            else "0% (pre-capital-gains tax — default)"
        )
        price_line = self.spot_price.strip() if self.spot_price.strip() else "Use latest dated price in attached dossier (or halt per Rule 7 if absent)"
        fx_line = self.spot_fx_rate.strip() if self.spot_fx_rate.strip() else "Use latest dated FX rate in attached dossier (or 1.00 if reporting currency == home currency; halt per Rule 7 if cross-currency FX is absent)"
        cross_border_line = (
            "ACTIVE — include the full Cross-Border / Emerging Market / VIE Risk module."
            if self.activate_cross_border_module
            else "INACTIVE (activate only if filings reveal a foreign/ADR/VIE structure)."
        )
        lines = [
            "## Active Investor Parameters & Run Overrides",
            f"- **Target Hurdle Rate ($H$):** {self.hurdle_rate_pct:.2f}% nominal annual IRR (home currency, net of dividend WHT & ADR/custody fees)",
            f"- **Investor Home Currency:** `{self.home_currency.strip().upper() or 'USD'}`",
            f"- **Current Share/ADS Price Override:** {price_line}",
            f"- **Spot FX Rate Override:** {fx_line}",
            f"- **Dividend Withholding Tax:** {self.dividend_withholding_tax_pct:.2f}%",
            f"- **Annual ADR / Custody Fee (per share, Home CCY):** {self.adr_custody_fee.strip() or '0.00'}",
            f"- **Personal Exit Capital-Gains Tax:** {cgt_line}",
            f"- **Portfolio Risk Budget:** Worst-state portfolio impairment cap = {self.max_impairment_pct:.2f}%; Single-Stock Concentration Cap = {self.single_stock_cap_pct:.2f}%",
            f"- **Optional Cross-Border / EM / VIE Module:** {cross_border_line}",
        ]
        if self.custom_notes.strip():
            lines.extend([
                "",
                "### Additional Analyst Overrides / Live Market Inputs",
                self.custom_notes.strip(),
            ])
        return "\n".join(lines)


def load_valuation_protocol(path: Path = VALUATION_PROTOCOL_PATH) -> str:
    """Load the user's editable valuation protocol Markdown from disk."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "# Custom Valuation Protocol\n\n"
        "valuation_protocol.md was not found. Perform a CAPM/WACC-free 5-year IRR valuation "
        "grounded strictly in the attached financial filings."
    )


def save_valuation_protocol(text: str, path: Path = VALUATION_PROTOCOL_PATH) -> Path:
    """Persist edits to the user's valuation protocol Markdown file."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Valuation protocol cannot be empty.")
    path.write_text(cleaned + "\n", encoding="utf-8")
    return path


def build_scientific_valuation_prompt(
    protocol_text: Optional[str] = None,
    params: Optional[ValuationParameters] = None,
) -> str:
    """Assemble the complete user prompt combining active investor parameters + valuation protocol."""
    proto = protocol_text.strip() if protocol_text and protocol_text.strip() else load_valuation_protocol()
    p = params or ValuationParameters()
    return (
        f"{p.to_prompt_block()}\n\n"
        "---\n\n"
        "Follow the exact Valuation Protocol below step-by-step for the company in the attached dossier:\n\n"
        f"{proto}"
    )


ANALYSIS_WORKFLOWS: Dict[str, Dict[str, Any]] = {
    "scientific_valuation": {
        "label": "🧮 5-Year Hurdle-Rate Valuation (Custom Protocol)",
        "short_desc": "Run your editable CAPM/WACC-free 6-phase IRR & Quarter-Kelly protocol over SEC filings & transcripts.",
        "max_tokens": 65536,
        "system_prompt": SYSTEM_PROMPT_VALUATION,
        "prompt": "",  # Built dynamically from valuation_protocol.md + ValuationParameters
    },
    "dcf_audit": {
        "label": "🎯 DCF vs. Guidance Reality Check",
        "short_desc": "Cross-examine DCF assumptions against management's actual transcript guidance & verbatim quotes.",
        "max_tokens": 4096,
        "prompt": (
            "Cross-examine the automated DCF model assumptions (WACC, Years 1–5 & 6–10 growth, "
            "FCF margin, terminal growth, fair price) against management's actual guidance, "
            "capital allocation, and tone in the earnings transcripts and Company Brief.\n\n"
            "Output these sections in clean Markdown:\n"
            "1. **Executive Verdict** (Is the DCF model Conservative, Consistent, Aggressive, or Unsuitable e.g. negative FCF / financial sector?)\n"
            "2. **Assumption Audit Table** (`Assumption | DCF Model | Management Guidance / Reality | Verbatim Quote & Source | Verdict`)\n"
            "3. **Key Blind Spots** (FX/currency mismatch, cyclical peaks, one-off working capital, capex cycle, or regulatory overhangs the mechanical DCF missed)\n"
            "4. **Qualitative Bull / Base / Bear Valuation Framing** (grounded in multiples from `Peers.md` + `CompanyBrief.md` if FCF DCF is unsuitable)."
        ),
    },
    "promise_tracker": {
        "label": "🕵️ Management Credibility & Promise Tracker",
        "short_desc": "Diff consecutive quarters: what management promised vs. what actually happened + dodged Q&A.",
        "prompt": (
            "Act as a skeptical forensic analyst tracking management credibility across quarters.\n\n"
            "Using all attached transcripts chronologically alongside `CompanyBrief.md` and SEC filings:\n"
            "1. **Promise vs. Delivery Table** (`Quarter Made | Specific Promise / Guidance | Subsequent Actual Outcome | Status (Beat / Met / Missed / Quietly Dropped) | Citation`)\n"
            "2. **Disappearing KPIs & Goalpost Shifts** (metrics emphasized in earlier calls that vanished or changed definition later)\n"
            "3. **Q&A Pressure Points** (toughest analyst questions and where management gave evasive or non-answers, with verbatim quotes)\n"
            "4. **Credibility Score (1–10)** with concrete justification."
        ),
    },
    "red_flags": {
        "label": "🚩 Red-Flag & Accounting Risk Scanner",
        "short_desc": "Scan SEC filings, transcripts, and ratios for dilution, GAAP/Non-GAAP gaps, VIE/governance & margin risks.",
        "prompt": (
            "Perform a rigorous forensic risk & red-flag audit using the attached dossier.\n\n"
            "Check specifically for:\n"
            "1. **Earnings Quality & Accounting** (GAAP vs. Non-GAAP gap, FCF vs. Net Income divergence, SBC/share dilution, receivables/credit-loss trends)\n"
            "2. **Balance Sheet & Capital Allocation** (net debt trajectory, debt/equity, buybacks vs. dilution, WACC vs. ROIC value destruction)\n"
            "3. **Governance, Regulatory & Structural Risks** (VIE/ADR risks, related-party transactions, customer/supplier concentration, insider/13D activity)\n"
            "4. **Peer Anomaly Check** (where multiples or returns diverge sharply from `Peers.md`)\n"
            "5. **Top 3 Kill-Switches** (concrete observable events that would break the bull thesis)."
        ),
    },
    "thesis_memo": {
        "label": "📑 Full Investment Thesis Memo",
        "short_desc": "Synthesize fundamentals, peers, DCF, SEC filings, and transcripts into a structured IC memo.",
        "prompt": (
            "Write a concise, institutional-grade Investment Committee memo grounded strictly in the attached dossier.\n\n"
            "Include:\n"
            "1. **Business Model & Moat** (how it actually makes money, segment drivers, unit economics)\n"
            "2. **Financial Quality Snapshot** (growth, margins, ROIC/ROE vs. WACC, balance sheet — with explicit currency labels)\n"
            "3. **Bull Case vs. Bear Case** (top 3 concrete, evidence-backed arguments each)\n"
            "4. **Valuation & Peer Context** (synthesis of `CompanyBrief.md`, `Peers.md`, and `DCF_Summary.md`)\n"
            "5. **Upcoming Catalysts & What to Watch Next Earnings** (specific numbers/questions to verify next call)."
        ),
    },
}


@dataclass
class DossierFile:
    rel_label: str
    path: Path
    category: str
    size_bytes: int


@dataclass
class AnalysisResult:
    ticker: str
    model: str
    workflow_id: str
    content: str
    reasoning: str = ""
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    estimated_cost_usd: float = 0.0
    saved_path: Optional[Path] = None


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token for English/Markdown financial text)."""
    return max(1, len(text) // 4) if text else 0


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int = 0,
) -> float:
    """Compute approximate off-peak USD cost for a DeepSeek request."""
    spec = DEEPSEEK_MODELS.get(model, DEEPSEEK_MODELS[DEFAULT_MODEL])
    miss_tokens = max(0, prompt_tokens - cache_hit_tokens)
    cost = (
        (miss_tokens / 1_000_000) * spec["input_miss_per_1m"]
        + (cache_hit_tokens / 1_000_000) * spec["input_hit_per_1m"]
        + (completion_tokens / 1_000_000) * spec["output_per_1m"]
    )
    return round(cost, 6)


def _transcript_sort_key(path: Path) -> Tuple[int, int, str]:
    """Sort transcripts chronologically by (year, quarter), newest first when reversed."""
    m = _TRANSCRIPT_PERIOD_RE.search(path.name)
    if m:
        return (int(m.group(2)), int(m.group(1)), path.name)
    return (0, 0, path.name)


def list_dossier_files(ticker: str, data_root: Path = DEFAULT_DATA_ROOT) -> List[DossierFile]:
    """Discover all local Markdown files for a ticker, ordered by analytical value."""
    clean = sanitize_ticker(ticker)
    t_root = data_root / clean
    if not t_root.exists():
        return []

    found: List[DossierFile] = []

    # Priority 1: Structured valuation pack, summaries & peers
    priority_specs = [
        ("ValuationPack.md", "Summary"),
        ("CompanyBrief.md", "Summary"),
        ("Valuation/DCF_Summary.md", "Valuation"),
        ("Peers.md", "Peers"),
        ("Coverage.md", "Coverage"),
    ]
    for rel, cat in priority_specs:
        p = t_root / rel
        if p.exists() and p.is_file():
            found.append(DossierFile(rel, p, cat, p.stat().st_size))

    # Priority 2: Earnings transcripts (newest fiscal period first!)
    tr_dir = t_root / "EarningsTranscripts"
    if tr_dir.exists():
        for p in sorted(tr_dir.glob("*.md"), key=_transcript_sort_key, reverse=True):
            found.append(DossierFile(f"EarningsTranscripts/{p.name}", p, "Transcript", p.stat().st_size))

    # Priority 3: SEC filings (newest first)
    sec_dir = t_root / "DataFromDataMule_markdown"
    if sec_dir.exists():
        for p in sorted(sec_dir.glob("*.md"), reverse=True):
            found.append(DossierFile(f"DataFromDataMule_markdown/{p.name}", p, "SEC Filing", p.stat().st_size))

    return found


def build_dossier_context(
    files: Sequence[DossierFile],
    max_chars_per_file: int = 160_000,
    max_total_chars: int = 1_800_000,
) -> Tuple[str, List[str]]:
    """Read selected dossier files into a single structured context block.

    DeepSeek's 1M-token window fits ~3–4M characters; capping total context at
    1.8M chars (~450k tokens) leaves generous room for conversation & reasoning.
    """
    blocks: List[str] = []
    truncated_notes: List[str] = []
    used_chars = 0

    for df in files:
        if used_chars >= max_total_chars:
            truncated_notes.append(f"Skipped `{df.rel_label}` (context budget reached)")
            continue
        try:
            text = strip_sec_metadata_header(
                df.path.read_text(encoding="utf-8", errors="replace")
            ).strip()
        except OSError:
            continue
        if not text:
            continue

        remaining = max_total_chars - used_chars
        cap = min(max_chars_per_file, remaining)
        if len(text) > cap:
            text = text[:cap] + "\n\n[... truncated to fit context budget ...]"
            truncated_notes.append(f"Truncated `{df.rel_label}` to {cap:,} chars")

        blocks.append(f"===== FILE: {df.rel_label} ({df.category}) =====\n{text}")
        used_chars += len(text)

    return "\n\n".join(blocks), truncated_notes


def save_analysis_memo(
    ticker: str,
    workflow_id: str,
    model: str,
    content: str,
    source_files: Sequence[str],
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """Persist a DeepSeek analysis memo under `data/<TICKER>/Analysis/` without overwriting same-day runs."""
    clean = sanitize_ticker(ticker)
    out_dir = data_root / clean / "Analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_wf = re.sub(r"[^a-zA-Z0-9_-]", "_", workflow_id)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = out_dir / f"{stamp}_{safe_wf}.md"
    idx = 2
    while out_path.exists():
        out_path = out_dir / f"{stamp}_{safe_wf}_{idx}.md"
        idx += 1

    header = [
        f"# {clean} — DeepSeek AI Analysis (`{workflow_id}`)",
        "",
        f"- **Date:** {date.today().isoformat()}",
        f"- **Model:** `{model}`",
        f"- **Sources Attached:** {', '.join(f'`{s}`' for s in source_files) if source_files else 'None'}",
        "",
        "---",
        "",
    ]
    out_path.write_text("\n".join(header) + content.strip() + "\n", encoding="utf-8")
    return out_path


def call_deepseek_full(
    api_key: str,
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout: Any = (30, 600),
    stream: bool = False,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
    session: Optional[requests.Session] = None,
) -> Tuple[str, str, str, Dict[str, int]]:
    """Call DeepSeek's OpenAI-compatible chat completions endpoint.

    Returns `(content, reasoning, finish_reason, usage)` and NEVER silently
    substitutes raw internal `reasoning_content` for the final answer.
    """
    if not api_key or not api_key.strip():
        raise ValueError("DeepSeek API key is not configured. Add it in the sidebar or Secrets.txt.")

    http = session or requests.Session()
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if model == "deepseek-flash":
        payload["temperature"] = temperature
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

    resp = http.post(
        DEEPSEEK_API_URL,
        headers=headers,
        json=payload,
        timeout=timeout,
        stream=stream,
    )

    if resp.status_code != 200:
        err_msg = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    err_msg = str(err.get("message") or err)
                elif err:
                    err_msg = str(err)
        except (ValueError, TypeError):
            raise RuntimeError(
                f"DeepSeek API returned non-JSON response (HTTP {resp.status_code})."
            )
        raise RuntimeError(f"DeepSeek API error (HTTP {resp.status_code}): {err_msg or 'Unknown error'}")

    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    finish_reason = ""
    usage_raw: Dict[str, Any] = {}

    if stream:
        r_chars = 0
        c_chars = 0
        last_report = 0
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except (ValueError, TypeError):
                continue
            if not isinstance(chunk, dict):
                continue
            if isinstance(chunk.get("usage"), dict):
                usage_raw = chunk["usage"]
            choices = chunk.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                c0 = choices[0]
                if c0.get("finish_reason"):
                    finish_reason = str(c0["finish_reason"])
                delta = c0.get("delta") or {}
                if isinstance(delta, dict):
                    rc = delta.get("reasoning_content")
                    if rc:
                        reasoning_parts.append(str(rc))
                        r_chars += len(str(rc))
                    cc = delta.get("content")
                    if cc:
                        content_parts.append(str(cc))
                        c_chars += len(str(cc))
                    if on_progress and (r_chars + c_chars - last_report) >= 250:
                        last_report = r_chars + c_chars
                        phase = "writing" if c_chars > 0 else "thinking"
                        on_progress(phase, r_chars, c_chars)
        content = "".join(content_parts).strip()
        reasoning = "".join(reasoning_parts).strip()
    else:
        try:
            body = resp.json()
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                f"DeepSeek API returned non-JSON response (HTTP {resp.status_code})."
            ) from exc

        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("DeepSeek API returned an empty response with no choices.")

        c0 = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = str(c0.get("finish_reason") or "")
        msg = c0.get("message", {}) if isinstance(c0.get("message"), dict) else {}
        content = str(msg.get("content") or "").strip()
        reasoning = str(msg.get("reasoning_content") or "").strip()
        usage_raw = body.get("usage", {}) if isinstance(body, dict) else {}

    if not content:
        if finish_reason == "length" and reasoning:
            raise RuntimeError(
                f"DeepSeek exhausted the max_tokens limit ({max_tokens:,}) during internal reasoning "
                f"({len(reasoning):,} chars of thoughts) before writing the final valuation. "
                "Try attaching fewer raw SEC filings (rely on ValuationPack.md + CompanyBrief.md) or re-running."
            )
        raise RuntimeError("DeepSeek API returned an empty final answer.")

    prompt_details = usage_raw.get("prompt_tokens_details") or {}
    cache_hit = int(
        usage_raw.get("prompt_cache_hit_tokens")
        or prompt_details.get("cached_tokens")
        or 0
    )
    usage = {
        "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
        "completion_tokens": int(usage_raw.get("completion_tokens") or 0),
        "cache_hit_tokens": cache_hit,
    }
    return content, reasoning, finish_reason, usage


def call_deepseek(
    api_key: str,
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout: Any = (30, 600),
    session: Optional[requests.Session] = None,
) -> Tuple[str, Dict[str, int]]:
    """Backwards-compatible wrapper returning `(content, usage)`."""
    content, _reasoning, _finish, usage = call_deepseek_full(
        api_key=api_key,
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        stream=False,
        session=session,
    )
    return content, usage


def run_workflow_analysis(
    ticker: str,
    workflow_id: str,
    files: Sequence[DossierFile],
    api_key: str,
    model: str = DEFAULT_MODEL,
    custom_prompt: Optional[str] = None,
    valuation_params: Optional[ValuationParameters] = None,
    protocol_text: Optional[str] = None,
    max_tokens: Optional[int] = None,
    stream: bool = False,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
    save_memo: bool = True,
    data_root: Path = DEFAULT_DATA_ROOT,
    session: Optional[requests.Session] = None,
) -> AnalysisResult:
    """Run a structured workflow or custom prompt over a ticker's dossier via DeepSeek."""
    clean = sanitize_ticker(ticker)
    if not files:
        raise ValueError(f"Select at least one local document for {clean} before running DeepSeek.")

    dossier_text, _ = build_dossier_context(files)
    wf = ANALYSIS_WORKFLOWS.get(workflow_id)
    if custom_prompt and custom_prompt.strip():
        user_task = custom_prompt.strip()
    elif workflow_id == "scientific_valuation":
        user_task = build_scientific_valuation_prompt(
            protocol_text=protocol_text,
            params=valuation_params,
        )
    else:
        user_task = str(wf["prompt"]) if wf else ""

    if not user_task:
        raise ValueError("Provide a valid workflow or question.")

    sys_prompt = str(wf.get("system_prompt") or SYSTEM_PROMPT_BASE) if wf else SYSTEM_PROMPT_BASE
    effective_max_tokens = int(max_tokens or (wf.get("max_tokens", 4096) if wf else 4096))

    # Place dossier in system message so follow-up turns hit DeepSeek's prompt cache!
    messages = [
        {"role": "system", "content": f"{sys_prompt}\n\n# DOSSIER FOR {clean}\n\n{dossier_text}"},
        {"role": "user", "content": user_task},
    ]

    content, reasoning, finish_reason, usage = call_deepseek_full(
        api_key=api_key,
        messages=messages,
        model=model,
        max_tokens=effective_max_tokens,
        stream=stream,
        on_progress=on_progress,
        session=session,
    )

    cost = estimate_cost_usd(
        model=model,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        cache_hit_tokens=usage["cache_hit_tokens"],
    )

    saved_path: Optional[Path] = None
    if save_memo:
        saved_path = save_analysis_memo(
            ticker=clean,
            workflow_id=workflow_id,
            model=model,
            content=content,
            source_files=[f.rel_label for f in files],
            data_root=data_root,
        )

    return AnalysisResult(
        ticker=clean,
        model=model,
        workflow_id=workflow_id,
        content=content,
        reasoning=reasoning,
        finish_reason=finish_reason,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        cache_hit_tokens=usage["cache_hit_tokens"],
        estimated_cost_usd=cost,
        saved_path=saved_path,
    )
