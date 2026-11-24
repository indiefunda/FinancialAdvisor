# ============================================================
# Markdown Table Cleanup Utility
# ============================================================
# SEC filings often produce messy tables when converted to
# Markdown:
#   1) Duplicate adjacent value columns: "| $1,234 | 1,234 |"
#   2) Standalone currency/punctuation spacer columns:
#      "| $ | 1,234 |" or "| (1,234 | ) |"
#
# This module detects and removes duplicate or currency-only
# spacer columns while preserving all real financial data and
# keeping Markdown separator rows valid.
# ============================================================

from __future__ import annotations

import re
from typing import List, Optional

# Matches lines that look like Markdown table rows: | cell | cell |
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")

# Matches cells that contain only currency/punctuation symbols or whitespace
_DECORATION_ONLY_RE = re.compile(r"^\s*[\$€£¥\(\)%\-\—\–]*\s*$")
_HAS_CURRENCY_OR_PAREN_RE = re.compile(r"[\$€£¥\(\)%]")


def _split_row(line: str) -> List[str]:
    """Split a Markdown table row into individual cells."""
    raw = line.strip()
    if not raw.startswith("|") or not raw.endswith("|"):
        return [line]
    return raw[1:-1].split("|")


def _join_row(cells: List[str]) -> str:
    """Join cells back into a Markdown table row."""
    return "| " + " | ".join(c.strip() for c in cells) + " |"


def _is_separator_row(cells: List[str]) -> bool:
    """Return True if a row is a Markdown table separator (e.g., | :--- | ---: |)."""
    if not cells:
        return False
    return all(re.fullmatch(r"\s*:?-+:?\s*", c) is not None for c in cells)


def _is_empty_or_decoration(cell: str) -> bool:
    """Return True if a cell is empty or only currency/parenthesis punctuation."""
    return bool(_DECORATION_ONLY_RE.match(cell))


def _column_is_currency_spacer(col: List[str]) -> bool:
    """Return True if a column contains ONLY currency/paren placeholders and blanks
    (with at least one actual currency/paren symbol, so sparse text columns like
    'Notes' are never falsely dropped).
    """
    has_symbol = False
    for cell in col:
        stripped = cell.strip()
        if not stripped:
            continue
        if _HAS_CURRENCY_OR_PAREN_RE.search(stripped) and _is_empty_or_decoration(stripped):
            has_symbol = True
        else:
            return False
    return has_symbol


def _choose_column_to_drop(left_cells: List[str], right_cells: List[str]) -> Optional[str]:
    """Compare two adjacent data columns (excluding separator rows).

    Returns:
      - "left"  if the left column should be dropped
      - "right" if the right column should be dropped
      - None    if both columns carry distinct data and must be kept
    """
    if len(left_cells) != len(right_cells) or not left_cells:
        return None

    # Case 1: One column is purely a currency/punctuation spacer (e.g. "$" or ")")
    # next to a column with real data — always drop the spacer, NEVER the data!
    left_is_spacer = _column_is_currency_spacer(left_cells)
    right_is_spacer = _column_is_currency_spacer(right_cells)
    if left_is_spacer and not right_is_spacer:
        return "left"
    if right_is_spacer and not left_is_spacer:
        return "right"

    # Case 2: Both columns contain near-identical values (e.g. "$1,234" vs "1,234")
    n_data_rows = 0
    n_same = 0
    left_nonempty = 0
    right_nonempty = 0

    for left, right in zip(left_cells, right_cells):
        l_strip = left.strip()
        r_strip = right.strip()
        if not l_strip and not r_strip:
            continue
        n_data_rows += 1
        if l_strip and not _is_empty_or_decoration(l_strip):
            left_nonempty += 1
        if r_strip and not _is_empty_or_decoration(r_strip):
            right_nonempty += 1

        l_norm = re.sub(r"[()\$€£¥,\s]+", "", l_strip)
        r_norm = re.sub(r"[()\$€£¥,\s]+", "", r_strip)
        if l_norm and r_norm and l_norm == r_norm:
            n_same += 1

    if n_data_rows < 2 or n_same < 2:
        return None

    if n_same >= max(2, int(0.8 * n_data_rows)):
        # Keep whichever column has more populated cells (tie-break: keep right)
        return "right" if left_nonempty > right_nonempty else "left"

    return None


def _dedupe_table_columns(rows: List[List[str]]) -> List[List[str]]:
    """Remove duplicate or currency-spacer adjacent columns from a table."""
    if not rows:
        return rows

    n_cols = max(len(r) for r in rows)
    padded = [r + [""] * (n_cols - len(r)) for r in rows]

    # Exclude Markdown separator rows (|---|---|) from equivalence checks!
    data_rows = [r for r in padded if not _is_separator_row(r)]
    if len(data_rows) < 2:
        return rows

    keep_mask = [True] * n_cols
    left_idx = 0
    for right_idx in range(1, n_cols):
        left_col = [r[left_idx] for r in data_rows]
        right_col = [r[right_idx] for r in data_rows]
        drop = _choose_column_to_drop(left_col, right_col)
        if drop == "left":
            keep_mask[left_idx] = False
            left_idx = right_idx
        elif drop == "right":
            keep_mask[right_idx] = False
            # Keep left_idx anchored to compare against subsequent columns
        else:
            left_idx = right_idx

    if all(keep_mask):
        return rows

    return [[r[j] for j in range(n_cols) if keep_mask[j]] for r in padded]


def cleanup_markdown_tables(markdown: str) -> str:
    """Clean up Markdown tables by removing duplicate and currency-spacer columns."""
    if not markdown or "|" not in markdown:
        return markdown

    out_lines: List[str] = []
    buf: List[str] = []

    def flush_table() -> None:
        nonlocal buf
        if not buf:
            return
        rows = [_split_row(line) for line in buf]
        new_rows = _dedupe_table_columns(rows)
        if new_rows is rows or new_rows == rows:
            out_lines.extend(buf)
        else:
            for r in new_rows:
                out_lines.append(_join_row(r))
        buf = []

    for line in markdown.split("\n"):
        if _TABLE_LINE_RE.match(line):
            buf.append(line)
        else:
            flush_table()
            out_lines.append(line)

    flush_table()
    return "\n".join(out_lines)
