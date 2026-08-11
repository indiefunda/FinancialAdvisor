# ============================================================
# Markdown Table Cleanup Utility
# ============================================================
# SEC filings often have badly formatted tables in their
# Markdown conversion. This script cleans them up by removing
# duplicate columns.
#
# Why duplicate columns?
#   The datamule library sometimes converts table cells twice —
#   e.g. "$1,234 | $1,234" instead of just "$1,234".
#   This happens because SEC HTML tables sometimes have
#   overlapping/merged cells that get expanded twice.
#
# This script detects when two adjacent columns have nearly
# identical data and removes the duplicate.
#
# LIBRARIES USED:
#   - re  → regular expressions for pattern matching
#   - typing → type hints (List[str] means "a list of strings")
# ============================================================

from __future__ import annotations

import re
from typing import List


# ── Regex patterns ─────────────────────────────────────────────────

# Matches lines that look like Markdown table rows: | cell | cell |
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")

# Matches separator rows: | :--- | :---: | ---: |
_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")

# Matches cells that are just a dollar sign, possibly with spaces
_CURRENCY_ONLY_RE = re.compile(r"^\s*\$?\s*$")


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _split_row(line: str) -> List[str]:
    """
    Split a Markdown table row into individual cells.
    
    Example:
      "| A | B | C |"  →  ["A", "B", "C"]
    
    If the line isn't a proper table row, return it as-is in a list.
    """
    raw = line.strip()
    if not raw.startswith("|") or not raw.endswith("|"):
        return [line]
    # Remove the outer pipes, then split by "|"
    inner = raw[1:-1]
    return inner.split("|")


def _join_row(cells: List[str]) -> str:
    """
    Join cells back into a Markdown table row.
    
    Example:
      ["A", "B", "C"]  →  "| A | B | C |"
    """
    return "| " + " | ".join(c.strip() for c in cells) + " |"


def _is_separator_row(cells: List[str]) -> bool:
    """
    Check if a row is a Markdown table separator (the |---| line).
    
    Example: | :--- | :---: | ---: |
    """
    if not cells:
        return False
    return all(re.fullmatch(r"\s*:?-+:?\s*", c) for c in cells)


def _looks_currency_or_empty(cell: str) -> bool:
    """
    Check if a cell is just a currency symbol or empty.
    
    Some SEC tables have cells with just "$" or "(" or ")" 
    as placeholders for formatting. We treat these as empty.
    """
    return bool(_CURRENCY_ONLY_RE.match(cell))


def _strip_dollar(cell: str) -> str:
    """
    Remove leading dollar sign and spaces from a cell.
    
    Example: "  $  1,234"  →  "1,234"
    """
    return re.sub(r"^\s*\$\s*", "", cell.strip())


def _columns_equivalent(left_cells: List[str], right_cells: List[str]) -> bool:
    """
    Check if two adjacent columns contain essentially the same data.
    
    This is the core detection logic. Two columns are "equivalent"
    if most of their data rows have the same values (with some
    allowance for currency symbols and formatting differences).
    
    Example of duplicate columns:
      | $1,234 | 1,234 |   ← both have the same number
      | $5,678 | 5,678 |
    
    Returns True if they're duplicates (one should be removed).
    """
    if len(left_cells) != len(right_cells) or not left_cells:
        return False

    n_data_rows = 0       # how many rows have actual data
    n_dup_pattern = 0     # how many rows look like duplicates
    n_value_rows = 0      # rows where at least one side has a value

    for L, R in zip(left_cells, right_cells):
        L_strip = L.strip()
        R_strip = R.strip()
        if not L_strip and not R_strip:
            continue       # skip rows where both sides are empty
        n_data_rows += 1

        # Remove currency symbols and parentheses for comparison
        Ls = re.sub(r"[()$\s]+", "", L_strip)
        Rs = re.sub(r"[()$\s]+", "", R_strip)
        is_value = bool(Rs or Ls)
        if is_value:
            n_value_rows += 1

        same_after_strip = (Ls == Rs and is_value)
        
        # Handle cases where one column has "$" and the other has the number
        left_currency_or_empty = _looks_currency_or_empty(L) or L_strip in ("(", "(  ")
        right_has_data = bool(R_strip) and not _looks_currency_or_empty(R)
        right_currency_or_empty = _looks_currency_or_empty(R) or R_strip in (")", "  )")
        left_has_data = bool(L_strip) and not _looks_currency_or_empty(L)

        if (
            same_after_strip
            or (left_currency_or_empty and right_has_data)
            or (left_has_data and right_currency_or_empty)
        ):
            n_dup_pattern += 1

    # Need at least some data rows and values to make a判断
    if n_data_rows == 0 or n_value_rows < 2:
        return False
    
    # Columns are duplicates if >= 80% of rows match the pattern
    return n_dup_pattern >= max(2, int(0.8 * n_data_rows))


def _dedupe_table_columns(rows: List[List[str]]) -> List[List[str]]:
    """
    Remove duplicate adjacent columns from a table.
    
    Scans left-to-right. If column i and column i+1 are
    equivalent, column i is removed.
    
    Example:
      | $1,234 | 1,234 | $5,678 |
      →  | 1,234 | $5,678 |
    """
    if not rows:
        return rows
    
    # Pad all rows to the same length (in case of ragged tables)
    n_cols = max(len(r) for r in rows)
    padded = [r + [""] * (n_cols - len(r)) for r in rows]

    # Start with all columns visible
    keep_mask = [True] * n_cols
    
    # Scan adjacent column pairs
    i = 0
    while i + 1 < n_cols:
        if not keep_mask[i] or not keep_mask[i + 1]:
            i += 1
            continue
        left_col = [r[i] for r in padded]
        right_col = [r[i + 1] for r in padded]
        if _columns_equivalent(left_col, right_col):
            keep_mask[i] = False  # remove the left duplicate
            i += 2                # skip the pair
        else:
            i += 1

    # If nothing to remove, return original rows unchanged
    if all(keep_mask):
        return rows
    
    # Apply the mask — keep only non-duplicate columns
    out: List[List[str]] = []
    for r in padded:
        out.append([r[j] for j in range(n_cols) if keep_mask[j]])
    return out


# ============================================================
# MAIN FUNCTION
# ============================================================

def cleanup_markdown_tables(markdown: str) -> str:
    """
    Clean up Markdown tables by removing duplicate columns.
    
    This is the main function called by download_data.py and app.py.
    
    Args:
        markdown: Raw Markdown text (possibly with messy tables)
    
    Returns:
        Cleaned Markdown text with duplicate table columns removed
    
    How it works:
        1. Split the text into lines
        2. Group consecutive table rows together
        3. For each table group, detect and remove duplicate columns
        4. Reassemble the text
    """
    if "|" not in markdown:
        # No tables at all — return as-is
        return markdown

    out_lines: List[str] = []
    buf: List[str] = []  # buffer for current table rows

    def flush_table():
        """
        Process the buffered table rows and append cleaned version to output.
        """
        nonlocal buf
        if not buf:
            return
        rows = [_split_row(l) for l in buf]
        new_rows = _dedupe_table_columns(rows)
        if new_rows is rows or new_rows == rows:
            # No changes — output original rows
            out_lines.extend(buf)
        else:
            # Changes made — output cleaned rows
            for r in new_rows:
                out_lines.append(_join_row(r))
        buf = []

    # Process each line
    for line in markdown.split("\n"):
        if _TABLE_LINE_RE.match(line):
            # This is a table row — add to buffer
            buf.append(line)
        else:
            # Not a table row — flush any buffered table, then output the line
            flush_table()
            out_lines.append(line)
    
    # Flush any remaining table at the end
    flush_table()

    return "\n".join(out_lines)
