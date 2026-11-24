"""Local configuration, API key management, and free-tier quota tracking.

Secrets are stored in a gitignored `.secrets.json` file in the repository root
(and may also be supplied via environment variables). Raw keys are never printed
or logged.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).parent.resolve()
SECRETS_FILE = REPO_ROOT / ".secrets.json"
QUOTA_FILE = REPO_ROOT / ".quota_state.json"

PROVIDER_SPECS: Dict[str, Dict[str, Any]] = {
    "defeatbeta": {
        "label": "DefeatBeta (Hugging Face)",
        "requires_key": False,
        "env_var": None,
        "limit_desc": "Unlimited (open dataset, no API key)",
        "docs_url": "https://pypi.org/project/defeatbeta-api/",
    },
    "roic": {
        "label": "ROIC.ai API v3",
        "requires_key": True,
        "env_var": "ROIC_API_KEY",
        "limit_desc": "Free: 5 req/min, latest 2 calls/company",
        "rpm_limit": 5,
        "free_latest_calls": 2,
        "docs_url": "https://www.roic.ai/api/docs/earnings-calls",
    },
    "alphavantage": {
        "label": "Alpha Vantage",
        "requires_key": True,
        "env_var": "ALPHAVANTAGE_API_KEY",
        "limit_desc": "Free: 25 req/day (~1 req/sec), full history since 2010Q1",
        "daily_limit": 25,
        "min_interval_sec": 1.2,
        "docs_url": "https://www.alphavantage.co/documentation/",
    },
    "firecrawl": {
        "label": "Firecrawl (Web + Alexandria)",
        "requires_key": True,
        "env_var": "FIRECRAWL_API_KEY",
        "limit_desc": "Free: 1,000 credits/month (fallback only)",
        "monthly_credit_limit": 1000,
        "docs_url": "https://docs.firecrawl.dev/features/alexandria",
    },
}

DEFAULT_PROVIDER_ORDER = ["defeatbeta", "roic", "alphavantage", "firecrawl"]


def mask_key(key: Optional[str]) -> str:
    """Return a safe masked representation of an API key."""
    if not key:
        return "Not set"
    cleaned = key.strip()
    if len(cleaned) <= 6:
        return "••••••"
    return f"{cleaned[:2]}••••••{cleaned[-4:]}"


def parse_secrets_txt(path: Path) -> Dict[str, str]:
    """Parse a simple `provider API KEY: value` text file into normalized keys."""
    if not path.exists():
        return {}
    found: Dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    patterns = {
        "roic": re.compile(r"roic(?:\.ai)?[^\n:]*:\s*(\S+)", re.IGNORECASE),
        "alphavantage": re.compile(r"alpha\s*vantage(?:\.co)?[^\n:]*:\s*(\S+)", re.IGNORECASE),
        "firecrawl": re.compile(r"firecrawl(?:\.dev)?[^\n:]*:\s*(\S+)", re.IGNORECASE),
    }
    for provider, pat in patterns.items():
        m = pat.search(text)
        if m:
            found[provider] = m.group(1).strip()
    return found


def _write_json_private(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON atomically using a unique temp file and restrict POSIX permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def load_stored_api_keys(
    secrets_file: Path = SECRETS_FILE,
    auto_import_paths: Optional[List[Path]] = None,
) -> Dict[str, str]:
    """Load persisted API keys from `.secrets.json` (without env-var overlay).

    If `Secrets.txt` contains keys for providers not yet in `.secrets.json`,
    those missing keys are merged in automatically.
    """
    keys: Dict[str, str] = {}

    if secrets_file.exists():
        try:
            raw = json.loads(secrets_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(v, str) and v.strip():
                        keys[k.lower().strip()] = v.strip()
        except (OSError, json.JSONDecodeError):
            pass

    candidates = auto_import_paths or [
        REPO_ROOT / "Secrets.txt",
        REPO_ROOT / "secrets.txt",
    ]
    merged_any = False
    for cand in candidates:
        imported = parse_secrets_txt(cand)
        for prov, val in imported.items():
            if prov not in keys and val:
                keys[prov] = val
                merged_any = True
    if merged_any:
        save_api_keys(keys, secrets_file=secrets_file)

    return keys


def load_api_keys_with_sources(
    secrets_file: Path = SECRETS_FILE,
    auto_import_paths: Optional[List[Path]] = None,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Return `(keys, sources)` where `sources[provider]` is `'env'` or `'file'`."""
    keys = load_stored_api_keys(secrets_file=secrets_file, auto_import_paths=auto_import_paths)
    sources: Dict[str, str] = {k: "file" for k in keys}

    for provider, spec in PROVIDER_SPECS.items():
        env_var = spec.get("env_var")
        if env_var:
            val = os.environ.get(env_var, "").strip()
            if val:
                keys[provider] = val
                sources[provider] = "env"

    return keys, sources


def load_api_keys(
    secrets_file: Path = SECRETS_FILE,
    auto_import_paths: Optional[List[Path]] = None,
) -> Dict[str, str]:
    """Load effective API keys (stored file + environment variable overrides)."""
    keys, _ = load_api_keys_with_sources(
        secrets_file=secrets_file,
        auto_import_paths=auto_import_paths,
    )
    return keys


def save_api_keys(keys: Dict[str, str], secrets_file: Path = SECRETS_FILE) -> None:
    """Persist API keys into gitignored `.secrets.json`."""
    cleaned = {
        k.lower().strip(): v.strip()
        for k, v in keys.items()
        if isinstance(v, str) and v.strip()
    }
    _write_json_private(secrets_file, cleaned)


class QuotaManager:
    """Tracks local API usage to respect free-tier limits across runs.

    All default limits are read directly from `PROVIDER_SPECS` (single source of truth).
    """

    def __init__(self, quota_file: Path = QUOTA_FILE):
        self.quota_file = quota_file
        self.state = self._load()

    def _today_utc(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _month_utc(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _default_state(self) -> Dict[str, Any]:
        return {
            "roic": {"timestamps": []},
            "alphavantage": {"date": self._today_utc(), "used": 0},
            "firecrawl": {"month": self._month_utc(), "credits_used": 0},
        }

    def _load(self) -> Dict[str, Any]:
        state = self._default_state()
        if self.quota_file.exists():
            try:
                loaded = json.loads(self.quota_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass
        self._rollover(state)
        return state

    def _rollover(self, state: Dict[str, Any]) -> None:
        now = time.time()
        roic_ts = state.setdefault("roic", {}).get("timestamps", [])
        state["roic"]["timestamps"] = [t for t in roic_ts if now - t < 65.0]

        today = self._today_utc()
        av = state.setdefault("alphavantage", {"date": today, "used": 0})
        if av.get("date") != today:
            av["date"] = today
            av["used"] = 0

        month = self._month_utc()
        fc = state.setdefault("firecrawl", {"month": month, "credits_used": 0})
        if fc.get("month") != month:
            fc["month"] = month
            fc["credits_used"] = 0

    def save(self) -> None:
        self._rollover(self.state)
        _write_json_private(self.quota_file, self.state)

    def reset(self) -> None:
        self.state = self._default_state()
        self.save()

    # ── ROIC.ai ────────────────────────────────────────────────────────
    @property
    def roic_rpm_limit(self) -> int:
        return int(PROVIDER_SPECS["roic"]["rpm_limit"])

    def roic_wait_if_needed(self, max_rpm: Optional[int] = None) -> float:
        """Sleep politely if RPM limit has been reached in the last 60s."""
        limit = max_rpm if max_rpm is not None else self.roic_rpm_limit
        self._rollover(self.state)
        ts = self.state["roic"]["timestamps"]
        now = time.time()
        recent = [t for t in ts if now - t < 61.0]
        if len(recent) >= limit:
            oldest = min(recent)
            wait = max(0.0, 61.5 - (now - oldest))
            if wait > 0:
                time.sleep(wait)
            return wait
        return 0.0

    def record_roic_call(self) -> None:
        self._rollover(self.state)
        self.state["roic"]["timestamps"].append(time.time())
        self.save()

    # ── Alpha Vantage ──────────────────────────────────────────────────
    @property
    def alphavantage_daily_limit(self) -> int:
        return int(PROVIDER_SPECS["alphavantage"]["daily_limit"])

    def alphavantage_remaining(self, daily_limit: Optional[int] = None) -> int:
        limit = daily_limit if daily_limit is not None else self.alphavantage_daily_limit
        self._rollover(self.state)
        return max(0, limit - int(self.state["alphavantage"].get("used", 0)))

    def can_call_alphavantage(self, daily_limit: Optional[int] = None) -> bool:
        return self.alphavantage_remaining(daily_limit) > 0

    def record_alphavantage_call(self, count: int = 1) -> None:
        self._rollover(self.state)
        self.state["alphavantage"]["used"] = int(self.state["alphavantage"].get("used", 0)) + count
        self.save()

    def mark_alphavantage_exhausted(self, daily_limit: Optional[int] = None) -> None:
        limit = daily_limit if daily_limit is not None else self.alphavantage_daily_limit
        self._rollover(self.state)
        self.state["alphavantage"]["used"] = limit
        self.save()

    # ── Firecrawl ──────────────────────────────────────────────────────
    @property
    def firecrawl_monthly_limit(self) -> int:
        return int(PROVIDER_SPECS["firecrawl"]["monthly_credit_limit"])

    def firecrawl_remaining(self, monthly_limit: Optional[int] = None) -> int:
        limit = monthly_limit if monthly_limit is not None else self.firecrawl_monthly_limit
        self._rollover(self.state)
        return max(0, limit - int(self.state["firecrawl"].get("credits_used", 0)))

    def can_call_firecrawl(self, cost: int = 2, monthly_limit: Optional[int] = None) -> bool:
        return self.firecrawl_remaining(monthly_limit) >= cost

    def record_firecrawl_credits(self, credits: int = 1) -> None:
        self._rollover(self.state)
        self.state["firecrawl"]["credits_used"] = (
            int(self.state["firecrawl"].get("credits_used", 0)) + credits
        )
        self.save()

    def summary(self) -> Dict[str, Dict[str, Any]]:
        """Return human-readable quota status for UI display."""
        self._rollover(self.state)
        roic_lim = self.roic_rpm_limit
        av_lim = self.alphavantage_daily_limit
        fc_lim = self.firecrawl_monthly_limit
        return {
            "defeatbeta": {"used": 0, "limit": "Unlimited", "remaining": "Unlimited"},
            "roic": {
                "used": len(self.state["roic"]["timestamps"]),
                "limit": f"{roic_lim} / min",
                "remaining": f"{max(0, roic_lim - len(self.state['roic']['timestamps']))} / min",
            },
            "alphavantage": {
                "used": self.state["alphavantage"]["used"],
                "limit": f"{av_lim} / day",
                "remaining": f"{self.alphavantage_remaining()} / day",
            },
            "firecrawl": {
                "used": self.state["firecrawl"]["credits_used"],
                "limit": f"{fc_lim:,} credits / mo",
                "remaining": f"{self.firecrawl_remaining():,} credits / mo",
            },
        }
