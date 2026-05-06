"""
Benchmarking engine — pure logic, no UI, no branding.

Three entry points:

    find_acos(name_query)
        Fuzzy search ACOs by name; returns ranked candidates.

    benchmark_aco(aco_id, performance_year=2024)
        Pull full metrics for a known ACO and compute its percentile rank
        within each relevant cohort.

    benchmark_synthetic(metric_values, cohort_filters, performance_year=2024)
        Take user-supplied metric values and a cohort definition (e.g.
        {'track': 'BASIC-E', 'rev_cat': 'Low Revenue'}); return the same
        percentile-rank structure as benchmark_aco. This is the path
        users hit when they don't want to disclose their ACO identity.

All functions return JSON-serializable dicts so the same engine drives the
Streamlit shell and the Netlify Function shell.
"""
from __future__ import annotations

import difflib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
DATA = HERE / "data"
SQLITE_PATH = DATA / "aco_data.sqlite"
COHORT_STATS_PATH = DATA / "cohort_stats.json"


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_for(year: int) -> str:
    if year not in (2023, 2024):
        raise ValueError(f"Performance year {year} not supported. Use 2023 or 2024.")
    return f"aco_py{year}"


def cohort_stats() -> dict:
    """Cached cohort percentile tables."""
    if not hasattr(cohort_stats, "_cache"):
        with open(COHORT_STATS_PATH) as f:
            cohort_stats._cache = json.load(f)
    return cohort_stats._cache


METRIC_DEFS = {m["key"]: m for m in cohort_stats()["PY2024"]["metric_definitions"]}


# ---------------------------------------------------------------------------
# Cohort assignment for a given ACO row
# ---------------------------------------------------------------------------
def _size_band(n_ab) -> str | None:
    try:
        n = float(n_ab)
    except (TypeError, ValueError):
        return None
    if n < 5_000:        return "<5K lives"
    if n < 10_000:       return "5K–10K lives"
    if n < 25_000:       return "10K–25K lives"
    if n < 50_000:       return "25K–50K lives"
    if n < 100_000:      return "50K–100K lives"
    return "100K+ lives"


_TRACK_MAP = {
    "A": "BASIC-A", "B": "BASIC-B", "C": "BASIC-C",
    "D": "BASIC-D", "E": "BASIC-E", "EN": "ENHANCED",
}


def cohort_labels_for_row(row: dict) -> dict:
    """Map a raw ACO row to its cohort labels across every cohort dimension."""
    return {
        "track":       _TRACK_MAP.get(str(row.get("Current_Track", "")).strip()),
        "risk_model":  row.get("Risk_Model"),
        "rev_cat":     row.get("Rev_Exp_Cat"),
        "size_band":   _size_band(row.get("N_AB")),
        "all":         "All ACOs",
    }


def _percentile_rank(value: float | None, table: dict) -> dict:
    """Where does `value` fall in this percentile table?"""
    if value is None or table.get("n", 0) == 0:
        return {"value": value, "rank_label": "n/a", "rank_pct": None,
                "cohort_p50": table.get("p50"), "cohort_n": table.get("n", 0)}
    # Linear interpolation across the published percentile breaks.
    breaks = [(10, table["p10"]), (25, table["p25"]), (50, table["p50"]),
              (75, table["p75"]), (90, table["p90"])]
    if value <= table["min"]:
        rank = 0.0
    elif value >= table["max"]:
        rank = 100.0
    else:
        rank = None
        prev_p, prev_v = 0, table["min"]
        for p, v in breaks + [(100, table["max"])]:
            if value <= v:
                # interpolate within (prev_p, p)
                if v == prev_v:
                    rank = float(p)
                else:
                    rank = prev_p + (p - prev_p) * (value - prev_v) / (v - prev_v)
                break
            prev_p, prev_v = p, v
        if rank is None:
            rank = 100.0
    rank = max(0.0, min(100.0, rank))
    return {
        "value": value,
        "rank_pct": round(rank, 1),
        "rank_label": _rank_label(rank),
        "cohort_p10": table.get("p10"),
        "cohort_p25": table.get("p25"),
        "cohort_p50": table.get("p50"),
        "cohort_p75": table.get("p75"),
        "cohort_p90": table.get("p90"),
        "cohort_mean": table.get("mean"),
        "cohort_min": table.get("min"),
        "cohort_max": table.get("max"),
        "cohort_n": table.get("n"),
    }


def _rank_label(rank: float) -> str:
    if rank >= 90: return "top decile"
    if rank >= 75: return "top quartile"
    if rank >= 50: return "above median"
    if rank >= 25: return "below median"
    return "bottom quartile"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
@dataclass
class AcoMatch:
    aco_id: str
    aco_name: str
    n_beneficiaries: float | None
    track: str | None
    rev_cat: str | None
    score: float  # similarity score 0..1


def find_acos(name_query: str, performance_year: int = 2024, limit: int = 10) -> list[dict]:
    """Fuzzy search ACO names. Returns ranked candidates."""
    if not name_query or not name_query.strip():
        return []
    q = name_query.strip().lower()
    with _connect() as conn:
        cur = conn.execute(
            f"SELECT ACO_ID, ACO_Name, N_AB, Current_Track, Rev_Exp_Cat FROM {_table_for(performance_year)}"
        )
        rows = cur.fetchall()
    matches: list[AcoMatch] = []
    for r in rows:
        name = (r["ACO_Name"] or "").lower()
        if not name:
            continue
        # Cheap-and-good: ratio + substring boost
        ratio = difflib.SequenceMatcher(None, q, name).ratio()
        if q in name:
            ratio = max(ratio, 0.85)
        if ratio < 0.4:
            continue
        matches.append(AcoMatch(
            aco_id=r["ACO_ID"], aco_name=r["ACO_Name"],
            n_beneficiaries=r["N_AB"],
            track=_TRACK_MAP.get((r["Current_Track"] or "").strip()),
            rev_cat=r["Rev_Exp_Cat"],
            score=round(ratio, 3),
        ))
    matches.sort(key=lambda m: -m.score)
    return [m.__dict__ for m in matches[:limit]]


def get_aco(aco_id: str, performance_year: int = 2024) -> dict | None:
    with _connect() as conn:
        cur = conn.execute(
            f"SELECT * FROM {_table_for(performance_year)} WHERE ACO_ID = ?", (aco_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def benchmark_aco(aco_id: str, performance_year: int = 2024) -> dict | None:
    """Return full benchmark report for one ACO."""
    row = get_aco(aco_id, performance_year)
    if not row:
        return None
    labels = cohort_labels_for_row(row)
    py_key = f"PY{performance_year}"
    cohorts_data = cohort_stats()[py_key]["cohorts"]

    metric_results = {}
    for key, mdef in METRIC_DEFS.items():
        src = mdef["source_col"]
        value = row.get(src)
        try:
            value = float(value) if value not in (None, "", "-") else None
        except (TypeError, ValueError):
            value = None
        # Compare against each cohort dimension
        cohort_comparisons = {}
        for cohort_name, cohort_value in labels.items():
            if cohort_value is None:
                continue
            t = cohorts_data.get(cohort_name, {}).get(str(cohort_value), {}).get("metrics", {}).get(key)
            if t is None:
                continue
            cohort_comparisons[cohort_name] = {
                "cohort_value": cohort_value, **_percentile_rank(value, t),
            }
        metric_results[key] = {
            "label": mdef["label"], "unit": mdef["unit"],
            "higher_is_better": mdef["higher_is_better"],
            "description": mdef["description"],
            "value": value, "comparisons": cohort_comparisons,
        }

    return {
        "aco": {
            "aco_id": row["ACO_ID"], "aco_name": row["ACO_Name"],
            "track": labels["track"], "rev_cat": labels["rev_cat"],
            "risk_model": labels["risk_model"], "size_band": labels["size_band"],
            "n_beneficiaries": row.get("N_AB"),
            "agreement_period": row.get("Agreement_Period_Num"),
            "current_start": row.get("Current_Start_Date"),
            "earned_savings": row.get("EarnSaveLoss"),
        },
        "performance_year": performance_year,
        "metrics": metric_results,
    }


def benchmark_synthetic(
    metric_values: dict[str, float],
    cohort_filters: dict[str, str] | None = None,
    performance_year: int = 2024,
) -> dict:
    """
    Compare user-supplied metrics against a chosen cohort. Use for
    'I run a Low-Revenue, 25K-life BASIC-E ACO with savings rate 2.5% —
    where do I stand?' workflows.
    """
    cohort_filters = cohort_filters or {"all": "All ACOs"}
    py_key = f"PY{performance_year}"
    cohorts_data = cohort_stats()[py_key]["cohorts"]

    metric_results = {}
    for key, value in metric_values.items():
        mdef = METRIC_DEFS.get(key)
        if not mdef:
            continue
        try:
            value = float(value) if value is not None else None
        except (TypeError, ValueError):
            value = None
        comps = {}
        for cohort_name, cohort_value in cohort_filters.items():
            t = cohorts_data.get(cohort_name, {}).get(str(cohort_value), {}).get("metrics", {}).get(key)
            if t is None:
                continue
            comps[cohort_name] = {
                "cohort_value": cohort_value, **_percentile_rank(value, t),
            }
        metric_results[key] = {
            "label": mdef["label"], "unit": mdef["unit"],
            "higher_is_better": mdef["higher_is_better"],
            "description": mdef["description"],
            "value": value, "comparisons": comps,
        }
    return {
        "aco": {"aco_name": "(your ACO)", **cohort_filters},
        "performance_year": performance_year,
        "metrics": metric_results,
    }


def list_metrics() -> list[dict]:
    return list(METRIC_DEFS.values())


def list_cohort_options() -> dict[str, list[str]]:
    """Available cohort labels — used by the UI to populate dropdowns."""
    py_key = "PY2024"
    return {
        cohort_name: sorted(values.keys())
        for cohort_name, values in cohort_stats()[py_key]["cohorts"].items()
    }


if __name__ == "__main__":
    # Smoke test — find a well-known ACO and benchmark it.
    print("=== find_acos('aledade') ===")
    for m in find_acos("aledade", limit=5):
        print(f"  {m['aco_id']:<6} {m['aco_name'][:50]:<50}  n={m['n_beneficiaries']}  score={m['score']}")

    if hits := find_acos("aledade", limit=1):
        first_id = hits[0]["aco_id"]
        print(f"\n=== benchmark_aco({first_id}) ===")
        report = benchmark_aco(first_id)
        print(f"ACO: {report['aco']['aco_name']}")
        print(f"  Track: {report['aco']['track']}, Rev: {report['aco']['rev_cat']}, "
              f"Size: {report['aco']['size_band']}")
        for key in ("savings_rate", "quality_score", "admits_per_1000"):
            r = report["metrics"][key]
            print(f"  {r['label']}: {r['value']}")
            for cn, cmp in r["comparisons"].items():
                print(f"    vs {cn} ({cmp['cohort_value']}, n={cmp['cohort_n']}): "
                      f"p{cmp['rank_pct']} — {cmp['rank_label']}")
