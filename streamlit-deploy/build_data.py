"""
MSSP PUF data preparation.

Loads the CMS Performance Year Financial and Quality Results PUF for PY2024
and PY2023, normalizes the schema (CMS files have inconsistent dtypes — some
numeric columns are stored as strings with '-' meaning null), computes cohort
summary statistics, and emits two artifacts:

    core/data/aco_data.sqlite    — full per-ACO records (read-only DB)
    core/data/cohort_stats.json  — pre-computed percentile tables for
                                    each metric × cohort definition.
                                    Small (~50 KB), shippable to the browser
                                    and to Streamlit without a DB query.

Run after refreshing the CSVs:
    python3 core/build_data.py
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"
SQLITE_PATH = DATA / "aco_data.sqlite"
COHORT_STATS_PATH = DATA / "cohort_stats.json"
ACO_INDEX_PATH = DATA / "aco_index.json"  # slim per-ACO records for browser-side lookup

# ---------------------------------------------------------------------------
# Metric registry — what we benchmark, and how to interpret the numbers.
# Adding a metric here makes it queryable across all cohorts automatically.
# ---------------------------------------------------------------------------
METRICS = [
    # (key, display_label, source_col, unit, higher_is_better, description)
    ("savings_rate", "Savings Rate", "Sav_rate", "%", True,
     "Generated savings or losses as a percent of the updated benchmark."),
    ("per_capita_exp", "Per-Capita Expenditure (Total PY)", "Per_Capita_Exp_TOTAL_PY", "$",
     False, "Total Medicare per-capita expenditure for assigned beneficiaries in the PY."),
    ("quality_score", "Quality Score", "QualScore", "%", True,
     "Composite ACO quality score used to determine final shared-savings payment."),
    ("hcc_risk_py", "CMS-HCC Risk Score (AGND, PY)", "CMS_HCC_RiskScore_AGND_PY", "score", None,
     "CMS-HCC risk score for aged non-dual beneficiaries in the performance year."),
    ("admits_per_1000", "All-Cause Inpatient Admits / 1000", "ADM", "per 1k", False,
     "Risk-adjusted inpatient admissions per 1,000 person-years."),
    ("ed_visits_per_1000", "ED Visits / 1000", "P_EDV_Vis", "per 1k", False,
     "Emergency department visits per 1,000 person-years."),
    ("snf_admits_per_1000", "SNF Admits / 1000", "P_SNF_ADM", "per 1k", False,
     "Skilled nursing facility admissions per 1,000 person-years."),
    ("snf_los", "SNF Length of Stay (days)", "SNF_LOS", "days", False,
     "Average SNF length of stay."),
    ("readmits_proxy", "30-day Readmission Risk-Std (Measure 479)", "Measure_479", "%", False,
     "All-cause unplanned admissions for patients with multiple chronic conditions."),
    ("pct_dual", "Dual-Eligible %", "Perc_Dual", "%", None,
     "Percent of assigned beneficiaries who are dual-eligible (Medicare + Medicaid)."),
    ("pct_lti", "Long-Term Institutionalized %", "Perc_LTI", "%", None,
     "Percent of assigned beneficiaries who are long-term institutionalized."),
    ("benchmark_per_capita", "Updated Benchmark / Beneficiary", "UpdatedBnchmk", "$", None,
     "Per-capita updated benchmark used for the performance year."),
    ("n_beneficiaries", "Assigned Beneficiaries", "N_AB", "lives", None,
     "Total number of assigned Medicare FFS beneficiaries."),
    ("share_rate", "Final Share Rate", "FinalShareRate", "%", True,
     "Percentage of generated savings the ACO is eligible to keep."),
]

# Cohort definitions — how we slice the population for benchmarking.
# Each yields a function that takes a row dict and returns a cohort label
# (or None if the row should be excluded from that cohort dimension).
COHORT_DEFS = {
    "track": lambda r: {
        "A": "BASIC-A", "B": "BASIC-B", "C": "BASIC-C",
        "D": "BASIC-D", "E": "BASIC-E", "EN": "ENHANCED",
    }.get(str(r.get("Current_Track", "")).strip()),
    "risk_model": lambda r: r.get("Risk_Model"),
    "rev_cat": lambda r: r.get("Rev_Exp_Cat"),
    "size_band": lambda r: _size_band(r.get("N_AB")),
    "all": lambda r: "All ACOs",
}


def _size_band(n):
    """Bucket ACOs into common size bands for cohort comparisons."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return None
    if n < 5_000:        return "<5K lives"
    if n < 10_000:       return "5K–10K lives"
    if n < 25_000:       return "10K–25K lives"
    if n < 50_000:       return "25K–50K lives"
    if n < 100_000:      return "50K–100K lives"
    return "100K+ lives"


def _to_num(s):
    """Coerce CMS string-with-dash columns to floats. '-' and '' → NaN."""
    if pd.isna(s):
        return np.nan
    if isinstance(s, (int, float, np.integer, np.floating)):
        return float(s)
    s = str(s).strip().replace(",", "")
    if s in {"", "-", "NA", "N/A"}:
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce all metric source columns to numeric in place."""
    for _, _, col, _, _, _ in METRICS:
        if col in df.columns:
            df[col] = df[col].apply(_to_num)
    # Also normalize the cohort dimension fields we touch
    for col in ("N_AB", "Current_Track", "Risk_Model", "Rev_Exp_Cat"):
        if col in df.columns and df[col].dtype == "object":
            df[col] = df[col].astype(str).str.strip()
    return df


def percentile_table(values: pd.Series) -> dict:
    """Cohort summary: count, mean, p10/25/50/75/90."""
    s = pd.to_numeric(values, errors="coerce").dropna()
    if len(s) == 0:
        return {"n": 0}
    return {
        "n":    int(len(s)),
        "mean": round(float(s.mean()), 4),
        "p10":  round(float(s.quantile(0.10)), 4),
        "p25":  round(float(s.quantile(0.25)), 4),
        "p50":  round(float(s.quantile(0.50)), 4),
        "p75":  round(float(s.quantile(0.75)), 4),
        "p90":  round(float(s.quantile(0.90)), 4),
        "min":  round(float(s.min()), 4),
        "max":  round(float(s.max()), 4),
    }


def build_cohort_stats(df: pd.DataFrame, py_label: str) -> dict:
    """Compute percentile tables for every metric × cohort × cohort-value."""
    out: dict = {"performance_year": py_label, "cohorts": {}}

    # Tag each row with its cohort labels.
    for cohort_name, fn in COHORT_DEFS.items():
        df[f"_cohort_{cohort_name}"] = df.apply(fn, axis=1)

    for cohort_name in COHORT_DEFS:
        out["cohorts"][cohort_name] = {}
        col = f"_cohort_{cohort_name}"
        for value, sub in df.groupby(col, dropna=True):
            if value is None or pd.isna(value) or value == "nan":
                continue
            out["cohorts"][cohort_name][str(value)] = {
                "metrics": {
                    key: percentile_table(sub[src])
                    for key, _, src, _, _, _ in METRICS
                    if src in sub.columns
                },
                "n_acos": int(len(sub)),
            }

    out["metric_definitions"] = [
        {
            "key": k, "label": lbl, "source_col": col, "unit": unit,
            "higher_is_better": hib, "description": desc,
        }
        for (k, lbl, col, unit, hib, desc) in METRICS
    ]
    return out


def main():
    print("Loading PUFs...")
    df24 = pd.read_csv(DATA / "PY2024_ACO_Results.csv", low_memory=False)
    df23 = pd.read_csv(DATA / "PY2023_ACO_Results.csv", low_memory=False)
    df24 = normalize(df24)
    df23 = normalize(df23)
    df24["performance_year"] = 2024
    df23["performance_year"] = 2023

    print(f"  PY2024: {len(df24):,} ACOs")
    print(f"  PY2023: {len(df23):,} ACOs")

    # SQLite — full per-ACO records, separate tables per PY.
    # Build in /tmp because some mounted filesystems block sqlite's incremental
    # I/O. Then truncate-write the bytes into place via plain open('wb').
    import tempfile, os
    tmp_dir = Path(tempfile.mkdtemp(prefix="aco_db_"))
    tmp_db = tmp_dir / "build.sqlite"
    print(f"Building SQLite in {tmp_db}...")
    with sqlite3.connect(tmp_db) as conn:
        df24.to_sql("aco_py2024", conn, index=False)
        df23.to_sql("aco_py2023", conn, index=False)
        conn.execute("CREATE INDEX idx_24_name ON aco_py2024(ACO_Name);")
        conn.execute("CREATE INDEX idx_23_name ON aco_py2023(ACO_Name);")
        conn.execute("CREATE INDEX idx_24_id ON aco_py2024(ACO_ID);")
        conn.execute("CREATE INDEX idx_23_id ON aco_py2023(ACO_ID);")
        conn.commit()
    print(f"Copying bytes into {SQLITE_PATH}...")
    with open(tmp_db, "rb") as src, open(SQLITE_PATH, "wb") as dst:
        dst.write(src.read())
    # Cleanup tmp
    try:
        os.remove(tmp_db)
        os.rmdir(tmp_dir)
    except OSError:
        pass
    # Remove any leftover -journal that an aborted prior run left behind
    journal = SQLITE_PATH.with_suffix(SQLITE_PATH.suffix + "-journal")
    if journal.exists():
        try:
            with open(journal, "wb"): pass  # truncate
        except OSError:
            pass

    # Cohort stats — small JSON for browser/Streamlit consumption.
    print("Computing cohort stats...")
    stats = {
        "PY2024": build_cohort_stats(df24, "PY2024"),
        "PY2023": build_cohort_stats(df23, "PY2023"),
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "source": "CMS MSSP Performance Year Financial and Quality Results PUF",
        "source_url": "https://data.cms.gov/medicare-shared-savings-program/performance-year-financial-and-quality-results",
    }
    with open(COHORT_STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2, default=str)

    sz = COHORT_STATS_PATH.stat().st_size
    print(f"Wrote {COHORT_STATS_PATH.name} ({sz:,} bytes)")
    print(f"Wrote {SQLITE_PATH.name} ({SQLITE_PATH.stat().st_size:,} bytes)")

    # Slim per-ACO index for browser-side lookup. Columns include ID, name,
    # cohort fields, and the source columns for every metric in METRICS.
    print("Building per-ACO browser index...")
    needed_cols = ["ACO_ID", "ACO_Name", "Current_Track", "Risk_Model",
                   "Rev_Exp_Cat", "N_AB", "Agreement_Period_Num",
                   "Current_Start_Date", "EarnSaveLoss"]
    needed_cols += [m[2] for m in METRICS]
    index_records = {"PY2024": [], "PY2023": []}

    def clean(v):
        # Strict-JSON-safe: NaN/inf -> None, everything else passes through.
        if v is None:
            return None
        if isinstance(v, float):
            if np.isnan(v) or np.isinf(v):
                return None
            return v
        if isinstance(v, (int, np.integer)):
            return int(v)
        if pd.isna(v):
            return None
        return v

    for label, df in (("PY2024", df24), ("PY2023", df23)):
        cols = [c for c in needed_cols if c in df.columns]
        records = df[cols].to_dict(orient="records")
        index_records[label] = [{k: clean(v) for k, v in rec.items()} for rec in records]

    with open(ACO_INDEX_PATH, "w") as f:
        json.dump(index_records, f, allow_nan=False, default=str)
    print(f"Wrote {ACO_INDEX_PATH.name} ({ACO_INDEX_PATH.stat().st_size:,} bytes)")
    print()
    print("Quick sanity check — PY2024 'all' cohort, savings_rate:")
    print(json.dumps(
        stats["PY2024"]["cohorts"]["all"]["All ACOs"]["metrics"]["savings_rate"],
        indent=2,
    ))


if __name__ == "__main__":
    main()
