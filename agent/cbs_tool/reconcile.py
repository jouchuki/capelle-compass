"""
Schema reconciliation for multi-edition series merges.

CBS appends a running ordinal to every Topic column name:
  NettoArbeidsparticipatie_5  (2024, 5th topic)
  NettoArbeidsparticipatie_3  (2013, 3rd topic)
Same concept, different suffix → zero overlap after naive pd.concat.

This module strips those ordinals (losslessly) and optionally applies a
concept rename map for true historical renames (e.g. Allochtonen → NietWesters).
"""
import re
from collections import Counter

import pandas as pd


# ─── Protected columns — never stripped or renamed ────────────────────────────

_ORDINAL_RE = re.compile(r'^(.+)_(\d+)$')

PROTECTED_COLS: frozenset[str] = frozenset({
    "ID", "Perioden",
    # Geo dimension keys
    "WijkenEnBuurten", "RegioS", "Gemeenten", "Regio", "Gebieden",
    # Context columns added by fetch_table
    "_year", "_gm_code", "_geo_name", "_table_id",
})


# ─── Historical concept rename map (base names, post ordinal-strip) ───────────
#
# Rules:
#   - Keys and values are BASE names (no ordinal suffix)
#   - Only include renames that are semantically safe or clearly documented
#   - Lossy renames are included but flagged via CONCEPT_RENAME_WARNINGS

CONCEPT_RENAMES: dict[str, str] = {
    # Ethnicity reclassification ~2014
    # Pre-2014: "Allochtonen" combined Western + non-Western immigrants
    # Post-2014: split into WestersAllochtonen + NietWestersAllochtonen
    # Mapping to NietWesters is the closest proxy but is lossy (excludes Western).
    "Allochtonen":                        "NietWestersAllochtonen",
    # "Autochtonen" was dropped ~2014 — no safe equivalent; leave in place (NaN tail)
    # Population total rename
    "BevolkingTotaal":                    "AantalInwoners",
    # Housing stock rename
    "WoningenTotaal":                     "AantalWoningen",
}

# Warnings attached to df.attrs["reconciliation_warnings"] when lossy renames fire
CONCEPT_RENAME_WARNINGS: dict[str, str] = {
    "Allochtonen": (
        "Column 'Allochtonen' (pre-2014, Western + non-Western combined) was mapped to "
        "'NietWestersAllochtonen' (post-2014). This is lossy: pre-2014 values include "
        "Western immigrants. Do not use for precise ethnic composition trends across 2014."
    ),
}


# ─── Core functions ───────────────────────────────────────────────────────────

def strip_ordinal_suffixes(
    df: pd.DataFrame,
    protected: frozenset[str] = PROTECTED_COLS,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """
    Strip CBS ordinal suffixes from Topic column names.

    e.g. NettoArbeidsparticipatie_5 → NettoArbeidsparticipatie

    Returns (renamed_df, {new_name: old_name}) mapping.
    Collision within the same edition: both columns keep their original names.
    Columns in `protected` or starting with '_' are never touched.
    """
    candidates: dict[str, str] = {}  # original_col → base_name

    for col in df.columns:
        if col in protected or col.startswith("_"):
            continue
        m = _ORDINAL_RE.match(col)
        if m:
            candidates[col] = m.group(1)

    # Drop candidates whose base name collides within this edition
    base_counts = Counter(candidates.values())
    rename: dict[str, str] = {
        col: base
        for col, base in candidates.items()
        if base_counts[base] == 1
    }

    return df.rename(columns=rename), {v: k for k, v in rename.items()}  # new→old


def apply_concept_renames(
    df: pd.DataFrame,
    rename_map: dict[str, str] = CONCEPT_RENAMES,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Apply historical concept renames (base names only, after ordinal stripping).

    Returns (renamed_df, warnings) where warnings is a list of strings
    for lossy renames that fired.
    """
    # Only rename columns that actually exist in this DataFrame
    applicable = {old: new for old, new in rename_map.items() if old in df.columns}
    warnings = [
        CONCEPT_RENAME_WARNINGS[old]
        for old in applicable
        if old in CONCEPT_RENAME_WARNINGS
    ]
    return df.rename(columns=applicable), warnings


def reconcile_editions(
    frames: list[pd.DataFrame],
    edition_ids: list[str],
    apply_concept_map: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """
    Reconcile a list of per-edition DataFrames before concat.

    Steps:
      1. Strip ordinal suffixes from each edition independently
      2. Optionally apply concept rename map
      3. pd.concat the reconciled frames

    Returns (merged_df, log) where log is attached to merged.attrs.
    """
    reconciled: list[pd.DataFrame] = []
    log: list[dict] = []
    all_warnings: list[str] = []

    for df, table_id in zip(frames, edition_ids):
        df_r, ordinal_renames = strip_ordinal_suffixes(df)
        warnings: list[str] = []

        if apply_concept_map:
            df_r, warnings = apply_concept_renames(df_r)

        log.append({
            "table_id":       table_id,
            "ordinal_renames": len(ordinal_renames),
            "concept_renames": [
                {"from": old, "to": new}
                for old, new in CONCEPT_RENAMES.items()
                if old in df.columns
            ],
        })
        all_warnings.extend(warnings)
        reconciled.append(df_r)

    merged = pd.concat(reconciled, ignore_index=True, sort=False)
    merged.attrs["reconciliation_log"]      = log
    merged.attrs["reconciliation_warnings"] = list(dict.fromkeys(all_warnings))  # dedup
    return merged, {"editions": log, "warnings": list(dict.fromkeys(all_warnings))}


def strip_base(key: str) -> str:
    """Strip ordinal suffix from a single column name (for diff display)."""
    m = _ORDINAL_RE.match(key)
    return m.group(1) if m else key
