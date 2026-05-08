from __future__ import annotations

import re
from typing import List


_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")
_CURRENCY_ONLY_RE = re.compile(r"^\s*\$?\s*$")


def _split_row(line: str) -> List[str]:
    raw = line.strip()
    if not raw.startswith("|") or not raw.endswith("|"):
        return [line]
    inner = raw[1:-1]
    return inner.split("|")


def _join_row(cells: List[str]) -> str:
    return "| " + " | ".join(c.strip() for c in cells) + " |"


def _is_separator_row(cells: List[str]) -> bool:
    if not cells:
        return False
    return all(re.fullmatch(r"\s*:?-+:?\s*", c) for c in cells)


def _looks_currency_or_empty(cell: str) -> bool:
    return bool(_CURRENCY_ONLY_RE.match(cell))


def _strip_dollar(cell: str) -> str:
    return re.sub(r"^\s*\$\s*", "", cell.strip())


def _columns_equivalent(left_cells: List[str], right_cells: List[str]) -> bool:
    if len(left_cells) != len(right_cells) or not left_cells:
        return False

    n_data_rows = 0
    n_dup_pattern = 0
    n_value_rows = 0

    for L, R in zip(left_cells, right_cells):
        L_strip = L.strip()
        R_strip = R.strip()
        if not L_strip and not R_strip:
            continue
        n_data_rows += 1

        Ls = re.sub(r"[()$\s]+", "", L_strip)
        Rs = re.sub(r"[()$\s]+", "", R_strip)
        is_value = bool(Rs or Ls)
        if is_value:
            n_value_rows += 1

        same_after_strip = (Ls == Rs and is_value)
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

    if n_data_rows == 0 or n_value_rows < 2:
        return False
    return n_dup_pattern >= max(2, int(0.8 * n_data_rows))


def _dedupe_table_columns(rows: List[List[str]]) -> List[List[str]]:
    if not rows:
        return rows
    n_cols = max(len(r) for r in rows)
    padded = [r + [""] * (n_cols - len(r)) for r in rows]

    keep_mask = [True] * n_cols
    i = 0
    while i + 1 < n_cols:
        if not keep_mask[i] or not keep_mask[i + 1]:
            i += 1
            continue
        left_col = [r[i] for r in padded]
        right_col = [r[i + 1] for r in padded]
        if _columns_equivalent(left_col, right_col):
            keep_mask[i] = False
            i += 2
        else:
            i += 1

    if all(keep_mask):
        return rows
    out: List[List[str]] = []
    for r in padded:
        out.append([r[j] for j in range(n_cols) if keep_mask[j]])
    return out


def cleanup_markdown_tables(markdown: str) -> str:
    if "|" not in markdown:
        return markdown

    out_lines: List[str] = []
    buf: List[str] = []

    def flush_table():
        nonlocal buf
        if not buf:
            return
        rows = [_split_row(l) for l in buf]
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
