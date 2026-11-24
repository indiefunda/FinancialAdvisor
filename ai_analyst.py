"""DeepSeek AI Intelligence Engine for FinancialAdvisor.

Connects to DeepSeek's OpenAI-compatible API (`https://api.deepseek.com`) using
`deepseek-flash` (V4.1-Flash, 1M context) or `deepseek-v4-pro` (deep reasoning,
1M context) to run grounded, citation-backed institutional equity research
workflows directly over each ticker's local dossier (`CompanyBrief.md`,
`Valuation/DCF_Summary.md`, `Peers.md`, earnings transcripts, and SEC filings).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from config_manager import PROVIDER_SPECS
from sec_downloader import DEFAULT_DATA_ROOT, sanitize_ticker

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

SYSTEM_PROMPT_BASE = (
    "You are a senior buy-side equity research analyst. "
    "You MUST ground every number, ratio, quote, and claim strictly in the attached "
    "dossier files. Always cite the exact source filename (e.g., `[CompanyBrief.md]`, "
    "`[AAPL - Q3 2026.md]`, `[DCF_Summary.md]`). "
    "Never mix financial reporting currencies (e.g., CNY vs USD) — explicitly state the currency. "
    "If a metric is missing or `n/a`, state that clearly instead of guessing. "
    "This is analytical research, not personalized financial advice."
)

ANALYSIS_WORKFLOWS: Dict[str, Dict[str, str]] = {
    "dcf_audit": {
        "label": "🎯 DCF vs. Guidance Reality Check",
        "short_desc": "Cross-examine DCF assumptions against management's actual transcript guidance & verbatim quotes.",
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

    # Priority 1: Structured summaries & valuation
    priority_specs = [
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
            text = df.path.read_text(encoding="utf-8", errors="replace").strip()
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


def call_deepseek(
    api_key: str,
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout: int = 180,
    session: Optional[requests.Session] = None,
) -> Tuple[str, Dict[str, int]]:
    """Call DeepSeek's OpenAI-compatible chat completions endpoint safely."""
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
    # Reasoning/pro models may ignore or disallow custom temperature; pass safely for flash
    if model == "deepseek-flash":
        payload["temperature"] = temperature

    resp = http.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=timeout)

    try:
        body = resp.json()
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"DeepSeek API returned non-JSON response (HTTP {resp.status_code})."
        ) from exc

    if resp.status_code != 200:
        err_msg = ""
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                err_msg = str(err.get("message") or err)
            elif err:
                err_msg = str(err)
        raise RuntimeError(f"DeepSeek API error (HTTP {resp.status_code}): {err_msg or 'Unknown error'}")

    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("DeepSeek API returned an empty response with no choices.")

    msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = str(msg.get("content") or "").strip()
    reasoning = str(msg.get("reasoning_content") or "").strip()
    if not content and reasoning:
        content = reasoning

    usage_raw = body.get("usage", {}) if isinstance(body, dict) else {}
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
    return content, usage


def run_workflow_analysis(
    ticker: str,
    workflow_id: str,
    files: Sequence[DossierFile],
    api_key: str,
    model: str = DEFAULT_MODEL,
    custom_prompt: Optional[str] = None,
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
    user_task = custom_prompt.strip() if custom_prompt else (wf["prompt"] if wf else "")
    if not user_task:
        raise ValueError("Provide a valid workflow or question.")

    # Place dossier in system message so follow-up turns hit DeepSeek's prompt cache!
    messages = [
        {"role": "system", "content": f"{SYSTEM_PROMPT_BASE}\n\n# DOSSIER FOR {clean}\n\n{dossier_text}"},
        {"role": "user", "content": user_task},
    ]

    content, usage = call_deepseek(
        api_key=api_key,
        messages=messages,
        model=model,
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
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        cache_hit_tokens=usage["cache_hit_tokens"],
        estimated_cost_usd=cost,
        saved_path=saved_path,
    )
