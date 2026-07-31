"""
MSSP PUF data preparation — v2.

Loads the CMS Performance Year Financial and Quality Results PUF and the
ACO Participants file, then emits everything the benchmarking engine needs.

v2 adds (driven by actuarial-partner feedback, July 2026):
  * Revised PY2024 PUF (2026-07-17) — supersedes the Sept 2025 release;
    savings rates changed for 131 of 476 ACOs.
  * Three actuarial KPIs:
      - BY3->PY membership-weighted CMS-HCC risk ratio
      - BY3->PY membership-weighted per-capita expense trend
      - Final benchmark adjustment (RegAdj vs PriorSavAdj per FinalAdjCat)
  * A named quality-measure layer (CAHPS, MIPS QualityIDs, admin-claims
    measures) with reporting-variant coalescing and per-measure direction.
  * Regional peer cohorts derived from ACO Participants service areas.

Outputs (core/data/):
    aco_data.sqlite     full per-ACO records
    aco_index.json      slim per-ACO records incl. derived KPIs + quality
    cohort_stats.json   percentile tables per metric x cohort
    aco_states.json     aco_id -> [service-area states]  (regional peering)
    meta.json           metric + measure definitions, provenance

Run:
    python3 core/build_data.py
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SQLITE_PATH = DATA / "aco_data.sqlite"
COHORT_STATS_PATH = DATA / "cohort_stats.json"
ACO_INDEX_PATH = DATA / "aco_index.json"
ACO_STATES_PATH = DATA / "aco_states.json"
META_PATH = DATA / "meta.json"

# Prefer the revised file when present.
PY2024_CANDIDATES = ["PY2024_ACO_Results_revised.csv", "PY2024_ACO_Results.csv"]
PARTICIPANTS_FILE = "PY2024_Participants.csv"

DATA_VINTAGE = "PY2024 PUF revised 2026-07-17"


# ---------------------------------------------------------------------------
# Population segments shared by the risk / expense KPIs.
# Order matters only for readability; each tuple is
#   (risk-score stem, per-capita-expense stem, person-year field stem)
# ---------------------------------------------------------------------------
SEGMENTS = [
    ("ESRD", "ESRD", "ESRD"),
    ("DIS",  "DIS",  "DIS"),
    ("AGDU", "AGDU", "AGED_Dual"),
    ("AGND", "AGND", "AGED_NonDual"),
]


# ---------------------------------------------------------------------------
# Financial / utilization metric registry
# (key, label, source_col, unit, higher_is_better, description)
# ---------------------------------------------------------------------------
METRICS = [
    ("savings_rate", "Savings Rate", "Sav_rate", "%", True,
     "Generated savings or losses as a percent of the updated benchmark."),
    ("per_capita_exp", "Per-Capita Expenditure (Total PY)", "Per_Capita_Exp_TOTAL_PY", "$",
     False, "Total Medicare per-capita expenditure for assigned beneficiaries in the PY."),
    ("quality_score", "Quality Score", "QualScore", "%", True,
     "Composite ACO quality score used to determine the final shared-savings payment."),
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
     "Percent of assigned beneficiaries who are dual-eligible."),
    ("pct_lti", "Long-Term Institutionalized %", "Perc_LTI", "%", None,
     "Percent of assigned beneficiaries who are long-term institutionalized."),
    ("benchmark_per_capita", "Updated Benchmark / Beneficiary", "UpdatedBnchmk", "$", None,
     "Per-capita updated benchmark used for the performance year."),
    ("n_beneficiaries", "Assigned Beneficiaries", "N_AB", "lives", None,
     "Total number of assigned Medicare FFS beneficiaries."),
    ("share_rate", "Final Share Rate", "FinalShareRate", "%", True,
     "Percentage of generated savings the ACO is eligible to keep."),
    # ---- v2 derived KPIs (source_col is the derived column name) ----
    ("risk_ratio_by3_py", "Risk Score Ratio (BY3 → PY)", "risk_ratio_by3_py", "ratio", None,
     "Membership-weighted CMS-HCC risk score in the performance year divided by the "
     "membership-weighted risk score in benchmark year 3. Above 1.00 means documented "
     "risk grew relative to the benchmark period."),
    ("expense_trend_by3_py", "Medical Expense Trend (BY3 → PY)", "expense_trend_by3_py", "ratio",
     False, "Membership-weighted per-capita expenditure in the performance year divided by "
     "the membership-weighted per-capita expenditure in benchmark year 3. Below 1.00 means "
     "per-capita cost fell relative to the benchmark period."),
    ("final_adj", "Final Benchmark Adjustment / Beneficiary", "final_adj", "$", True,
     "The benchmark adjustment CMS actually applied — the regional adjustment or the prior "
     "savings adjustment, whichever CMS selected (per FinalAdjCat). Higher raises the benchmark."),
    ("reg_adj", "Regional Adjustment / Beneficiary", "RegAdj", "$", True,
     "Regional efficiency adjustment to the benchmark, before CMS selects between it and "
     "the prior savings adjustment."),
    ("prior_sav_adj", "Prior Savings Adjustment / Beneficiary", "PriorSavAdj", "$", True,
     "Credit for past program performance, before CMS selects between it and the regional "
     "adjustment."),
]


# ---------------------------------------------------------------------------
# Quality measure registry.
#
# `variants` lists the raw PUF columns that carry the same clinical measure
# under different reporting mechanisms (Web Interface, eCQM, MIPS CQM,
# Medicare CQM). An ACO reports through exactly one, so we coalesce.
#
# higher_is_better = False marks inverse measures where a LOW rate is good
# (HbA1c poor control, readmissions, admissions). Ranking these naively is
# the single easiest way to tell an ACO the opposite of the truth.
# ---------------------------------------------------------------------------
QUALITY_MEASURES = [
    # key, label, variants, domain, higher_is_better, description
    ("q_diabetes_hba1c_poor", "Diabetes: HbA1c Poor Control (>9%)",
     ["QualityID_001_WI", "QualityID_001_eCQM", "QualityID_001_MIPSCQM", "QualityID_001_MedicareCQM"],
     "Chronic disease", False,
     "Percent of diabetic patients whose most recent HbA1c was greater than 9% or missing. "
     "An inverse measure — lower is better."),
    ("q_bp_control", "Controlling High Blood Pressure",
     ["QualityID_236_WI", "QualityID_236_eCQM", "QualityID_236_MIPSCQM", "QualityID_236_MedicareCQM"],
     "Chronic disease", True,
     "Percent of hypertensive patients whose blood pressure was adequately controlled."),
    ("q_depression_screen", "Screening for Depression and Follow-Up Plan",
     ["QualityID_134_WI", "QualityID_134_eCQM", "QualityID_134_MIPSCQM", "QualityID_134_MedicareCQM"],
     "Behavioral health", True,
     "Percent of patients screened for depression with a documented follow-up plan."),
    ("q_depression_remission", "Depression Remission at Twelve Months",
     ["QualityID_370"], "Behavioral health", True,
     "Percent of patients with depression who reached remission at twelve months."),
    ("q_falls_screen", "Falls: Screening for Future Fall Risk",
     ["QualityID_318"], "Preventive care", True,
     "Percent of patients aged 65+ screened for future fall risk."),
    ("q_flu_immunization", "Preventive Care: Influenza Immunization",
     ["QualityID_110"], "Preventive care", True,
     "Percent of patients who received an influenza immunization."),
    ("q_tobacco_screen", "Tobacco Use: Screening and Cessation Intervention",
     ["QualityID_226"], "Preventive care", True,
     "Percent of patients screened for tobacco use who received cessation intervention if needed."),
    ("q_breast_cancer_screen", "Breast Cancer Screening",
     ["QualityID_112"], "Cancer screening", True,
     "Percent of women aged 50-74 screened for breast cancer."),
    ("q_colorectal_screen", "Colorectal Cancer Screening",
     ["QualityID_113"], "Cancer screening", True,
     "Percent of patients aged 45-75 screened for colorectal cancer."),
    ("q_statin_therapy", "Statin Therapy for CVD Prevention/Treatment",
     ["QualityID_438"], "Chronic disease", True,
     "Percent of eligible patients prescribed or on statin therapy."),
    ("q_readmission", "All-Cause Unplanned Readmission (HWR)",
     ["Measure_479"], "Utilization outcomes", False,
     "Risk-standardized, hospital-wide, 30-day all-cause unplanned readmission rate. "
     "An inverse measure — lower is better."),
    ("q_admissions_mcc", "Admissions for Patients with Multiple Chronic Conditions",
     ["Measure_484"], "Utilization outcomes", False,
     "Risk-standardized acute admission rate for patients with multiple chronic conditions. "
     "An inverse measure — lower is better."),
    # ---- CAHPS patient-experience summary survey measures ----
    ("q_cahps_timely_care", "CAHPS: Getting Timely Care, Appointments, Information",
     ["CAHPS_1"], "Patient experience", True, "CAHPS summary survey measure."),
    ("q_cahps_communication", "CAHPS: How Well Providers Communicate",
     ["CAHPS_2"], "Patient experience", True, "CAHPS summary survey measure."),
    ("q_cahps_rating", "CAHPS: Patient's Rating of Provider",
     ["CAHPS_3"], "Patient experience", True, "CAHPS summary survey measure."),
    ("q_cahps_specialist_access", "CAHPS: Access to Specialists",
     ["CAHPS_4"], "Patient experience", True, "CAHPS summary survey measure."),
    ("q_cahps_health_promotion", "CAHPS: Health Promotion and Education",
     ["CAHPS_5"], "Patient experience", True, "CAHPS summary survey measure."),
    ("q_cahps_shared_decision", "CAHPS: Shared Decision Making",
     ["CAHPS_6"], "Patient experience", True, "CAHPS summary survey measure."),
    ("q_cahps_health_status", "CAHPS: Health Status and Functional Status",
     ["CAHPS_7"], "Patient experience", True, "CAHPS summary survey measure."),
    ("q_cahps_care_coordination", "CAHPS: Care Coordination",
     ["CAHPS_8"], "Patient experience", True, "CAHPS summary survey measure."),
    ("q_cahps_office_staff", "CAHPS: Courteous and Helpful Office Staff",
     ["CAHPS_9"], "Patient experience", True, "CAHPS summary survey measure."),
    ("q_cahps_stewardship", "CAHPS: Stewardship of Patient Resources",
     ["CAHPS_11"], "Patient experience", True, "CAHPS summary survey measure."),
]


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
    try:
        n = float(n)
    except (TypeError, ValueError):
        return None
    if n < 5_000:   return "<5K lives"
    if n < 10_000:  return "5K–10K lives"
    if n < 25_000:  return "10K–25K lives"
    if n < 50_000:  return "25K–50K lives"
    if n < 100_000: return "50K–100K lives"
    return "100K+ lives"


def _to_num(s):
    """CMS uses '-' for not-applicable; coerce everything else to float."""
    if pd.isna(s):
        return np.nan
    if isinstance(s, (int, float, np.integer, np.floating)):
        return float(s)
    s = str(s).strip().replace(",", "").replace("$", "")
    if s in {"", "-", "NA", "N/A", "*"}:
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


# ---------------------------------------------------------------------------
# v2 derived KPIs
# ---------------------------------------------------------------------------
def _col_or_nan(df: pd.DataFrame, col: str) -> np.ndarray:
    """Numeric column values, or an all-NaN vector if the column is absent."""
    if col in df.columns:
        return df[col].apply(_to_num).values
    return np.full(len(df), np.nan)


def _weighted(df: pd.DataFrame, value_cols: list[str], weight_cols: list[str],
              fallback_weight_cols: list[str] | None = None) -> pd.Series:
    """Row-wise sumproduct(values, weights) / sum(weights), NaN-safe.

    Segments with a missing value or missing weight drop out of both the
    numerator and the denominator, so the result stays a true weighted mean
    over whatever segments the ACO actually reports.

    Older performance years omit the published RR_weight_* fields; when the
    primary weights are entirely absent we fall back to person-year counts,
    which express the same membership weighting.
    """
    vals = np.column_stack([_col_or_nan(df, c) for c in value_cols])
    wts = np.column_stack([_col_or_nan(df, c) for c in weight_cols])
    if np.isnan(wts).all() and fallback_weight_cols:
        wts = np.column_stack([_col_or_nan(df, c) for c in fallback_weight_cols])
    usable = ~np.isnan(vals) & ~np.isnan(wts)
    v = np.where(usable, vals, 0.0)
    w = np.where(usable, wts, 0.0)
    denom = w.sum(axis=1)
    num = (v * w).sum(axis=1)
    out = np.divide(num, denom, out=np.full_like(num, np.nan), where=denom > 0)
    return pd.Series(out, index=df.index)


def add_derived_kpis(df: pd.DataFrame) -> pd.DataFrame:
    """Risk ratio, expense trend, and final benchmark adjustment."""
    df = df.copy()

    # --- 1. BY3 -> PY membership-weighted risk score ratio -----------------
    # PY weights ship as normalized RR_weight_* fields. BY3 has no equivalent,
    # so we build BY3 weights from the N_AB_Year_*_BY3 person-year counts —
    # the same membership weighting, expressed in the units CMS published.
    py_risk = [f"CMS_HCC_RiskScore_{a}_PY" for a, _, _ in SEGMENTS]
    py_wts = [f"RR_weight_{a}_PY" for a, _, _ in SEGMENTS]
    py_mem_w = [f"N_AB_Year_{c}_PY" for _, _, c in SEGMENTS]
    by3_risk = [f"CMS_HCC_RiskScore_{a}_BY3" for a, _, _ in SEGMENTS]
    by3_wts = [f"N_AB_Year_{c}_BY3" for _, _, c in SEGMENTS]

    df["risk_score_py_wtd"] = _weighted(df, py_risk, py_wts, fallback_weight_cols=py_mem_w)
    df["risk_score_by3_wtd"] = _weighted(df, by3_risk, by3_wts)
    df["risk_ratio_by3_py"] = df["risk_score_py_wtd"] / df["risk_score_by3_wtd"].replace(0, np.nan)

    # --- 2. BY3 -> PY membership-weighted per-capita expense trend ---------
    py_exp = [f"Per_Capita_Exp_ALL_{b}_PY" for _, b, _ in SEGMENTS]
    py_mem = [f"N_AB_Year_{c}_PY" for _, _, c in SEGMENTS]
    by3_exp = [f"Per_Capita_Exp_ALL_{b}_BY3" for _, b, _ in SEGMENTS]
    by3_mem = [f"N_AB_Year_{c}_BY3" for _, _, c in SEGMENTS]

    df["exp_py_wtd"] = _weighted(df, py_exp, py_mem)
    df["exp_by3_wtd"] = _weighted(df, by3_exp, by3_mem)
    df["expense_trend_by3_py"] = df["exp_py_wtd"] / df["exp_by3_wtd"].replace(0, np.nan)

    # --- 3. Final benchmark adjustment ------------------------------------
    reg = (df["RegAdj"].apply(_to_num) if "RegAdj" in df.columns
           else pd.Series(np.nan, index=df.index))
    psa = (df["PriorSavAdj"].apply(_to_num) if "PriorSavAdj" in df.columns
           else pd.Series(np.nan, index=df.index))
    cat = (df["FinalAdjCat"].astype(str).str.strip().str.lower()
           if "FinalAdjCat" in df.columns else pd.Series("", index=df.index))

    final = pd.Series(0.0, index=df.index)
    final = final.mask(cat.str.contains("regional", na=False), reg)
    final = final.mask(cat.str.contains("prior", na=False), psa)
    final = final.fillna(0.0)
    df["final_adj"] = final
    df["final_adj_type"] = (
        df["FinalAdjCat"].astype(str).str.strip() if "FinalAdjCat" in df.columns
        else pd.Series("Not published", index=df.index)
    )
    return df


def add_quality_measures(df: pd.DataFrame) -> pd.DataFrame:
    """Coalesce reporting variants into one named column per clinical measure."""
    df = df.copy()
    for key, _label, variants, _domain, _hib, _desc in QUALITY_MEASURES:
        present = [c for c in variants if c in df.columns]
        if not present:
            df[key] = np.nan
            continue
        stacked = pd.concat([df[c].apply(_to_num) for c in present], axis=1)
        # An ACO reports through exactly one mechanism; take the first non-null.
        df[key] = stacked.bfill(axis=1).iloc[:, 0]
        if len(present) > 1:
            df[f"{key}__source"] = stacked.notna().idxmax(axis=1).where(stacked.notna().any(axis=1))
    return df


# ---------------------------------------------------------------------------
# Regional peering
# ---------------------------------------------------------------------------
def build_aco_states(participants_path: Path) -> dict[str, list[str]]:
    """aco_id -> sorted list of service-area state codes."""
    if not participants_path.exists():
        print(f"  WARN: {participants_path.name} not found — regional peering disabled.")
        return {}
    part = pd.read_csv(participants_path, low_memory=False)
    slim = part[["aco_id", "aco_service_area"]].drop_duplicates("aco_id")
    out: dict[str, list[str]] = {}
    for _, row in slim.iterrows():
        raw = str(row["aco_service_area"] or "")
        states = sorted({s.strip().upper() for s in raw.split(",") if s.strip() and s.strip().lower() != "nan"})
        if states:
            out[str(row["aco_id"]).strip()] = states
    return out


def percentile_table(values: pd.Series) -> dict:
    s = pd.to_numeric(values, errors="coerce").dropna()
    if len(s) == 0:
        return {"n": 0}
    return {
        "n": int(len(s)),
        "mean": round(float(s.mean()), 4),
        "p10": round(float(s.quantile(0.10)), 4),
        "p25": round(float(s.quantile(0.25)), 4),
        "p50": round(float(s.quantile(0.50)), 4),
        "p75": round(float(s.quantile(0.75)), 4),
        "p90": round(float(s.quantile(0.90)), 4),
        "min": round(float(s.min()), 4),
        "max": round(float(s.max()), 4),
    }


def all_metric_keys() -> list[tuple[str, str]]:
    """(key, source_column) for every rankable metric, financial + quality."""
    pairs = [(k, src) for k, _l, src, _u, _h, _d in METRICS]
    pairs += [(k, k) for k, *_ in QUALITY_MEASURES]
    return pairs


def build_cohort_stats(df: pd.DataFrame, py_label: str) -> dict:
    out: dict = {"performance_year": py_label, "cohorts": {}}
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
                    for key, src in all_metric_keys() if src in sub.columns
                },
                "n_acos": int(len(sub)),
            }
    return out


def build_meta() -> dict:
    return {
        "data_vintage": DATA_VINTAGE,
        "metric_definitions": [
            {"key": k, "label": l, "source_col": src, "unit": u,
             "higher_is_better": h, "description": d, "family": "financial"}
            for (k, l, src, u, h, d) in METRICS
        ],
        "quality_measures": [
            {"key": k, "label": l, "variants": v, "domain": dom,
             "higher_is_better": h, "description": d, "family": "quality"}
            for (k, l, v, dom, h, d) in QUALITY_MEASURES
        ],
        "methodology": {
            "risk_ratio_by3_py":
                "Membership-weighted PY CMS-HCC risk score divided by membership-weighted "
                "BY3 risk score. PY weights use the published RR_weight_* fields; BY3 weights "
                "are derived from N_AB_Year_*_BY3 person-years because CMS does not publish "
                "RR_weight_*_BY3. Segments missing a score or a weight are excluded from both "
                "numerator and denominator.",
            "expense_trend_by3_py":
                "Membership-weighted PY per-capita expenditure divided by membership-weighted "
                "BY3 per-capita expenditure, using Per_Capita_Exp_ALL_* weighted by "
                "N_AB_Year_* person-years for the matching period.",
            "final_adj":
                "If FinalAdjCat is a Regional Adjustment, FinalAdj = RegAdj. If it is a Prior "
                "Savings Adjustment, FinalAdj = PriorSavAdj. Otherwise 0.",
            "quality_coalescing":
                "Measures reported through multiple mechanisms (Web Interface, eCQM, MIPS CQM, "
                "Medicare CQM) are coalesced to a single value; each ACO reports through one.",
            "inverse_measures":
                "HbA1c poor control, readmissions, and MCC admission rates are inverse: a lower "
                "value is better performance. Percentile ranks are reported on the raw value; "
                "presentation layers invert the language.",
            "regional_peers":
                "Peer set = all ACOs sharing at least one service-area state, from the PY2024 "
                "ACO Participants file. Ranks are computed exactly from peer raw values.",
        },
        "sources": {
            "performance": "https://data.cms.gov/medicare-shared-savings-program/performance-year-financial-and-quality-results",
            "participants": "https://data.cms.gov/medicare-shared-savings-program/accountable-care-organization-participants",
            "data_dictionary": "https://data.cms.gov/resources/performance-year-financial-and-quality-results-data-dictionary",
        },
    }


def main():
    print("Loading PUFs...")
    py24_path = next((DATA / c for c in PY2024_CANDIDATES if (DATA / c).exists()), None)
    if py24_path is None:
        raise SystemExit("No PY2024 results file found in core/data/.")
    print(f"  PY2024 source: {py24_path.name}")
    df24 = pd.read_csv(py24_path, low_memory=False)
    df23 = pd.read_csv(DATA / "PY2023_ACO_Results.csv", low_memory=False)

    for df in (df24, df23):
        for _k, _l, src, _u, _h, _d in METRICS:
            if src in df.columns:
                df[src] = df[src].apply(_to_num)
        for col in ("N_AB", "Current_Track", "Risk_Model", "Rev_Exp_Cat"):
            if col in df.columns and df[col].dtype == "object":
                df[col] = df[col].astype(str).str.strip()

    print("Computing derived KPIs...")
    df24 = add_quality_measures(add_derived_kpis(df24))
    df23 = add_quality_measures(add_derived_kpis(df23))
    df24["performance_year"] = 2024
    df23["performance_year"] = 2023
    print(f"  PY2024: {len(df24):,} ACOs   PY2023: {len(df23):,} ACOs")
    for k in ("risk_ratio_by3_py", "expense_trend_by3_py", "final_adj"):
        print(f"    {k}: {df24[k].notna().sum()}/{len(df24)} populated")

    print("Building regional peer map...")
    aco_states = build_aco_states(DATA / PARTICIPANTS_FILE)
    matched = df24["ACO_ID"].isin(aco_states).sum() if aco_states else 0
    print(f"  service areas for {len(aco_states)} ACOs; {matched}/{len(df24)} PY2024 ACOs matched")

    # SQLite (built in /tmp — some mounts block sqlite's incremental I/O)
    import tempfile, os
    tmp_dir = Path(tempfile.mkdtemp(prefix="aco_db_"))
    tmp_db = tmp_dir / "build.sqlite"
    with sqlite3.connect(tmp_db) as conn:
        df24.to_sql("aco_py2024", conn, index=False)
        df23.to_sql("aco_py2023", conn, index=False)
        for y in (24, 23):
            conn.execute(f"CREATE INDEX idx_{y}_name ON aco_py20{y}(ACO_Name);")
            conn.execute(f"CREATE INDEX idx_{y}_id ON aco_py20{y}(ACO_ID);")
        conn.commit()
    with open(tmp_db, "rb") as src, open(SQLITE_PATH, "wb") as dst:
        dst.write(src.read())
    try:
        os.remove(tmp_db); os.rmdir(tmp_dir)
    except OSError:
        pass

    print("Computing cohort stats...")
    stats = {
        "PY2024": build_cohort_stats(df24, "PY2024"),
        "PY2023": build_cohort_stats(df23, "PY2023"),
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "data_vintage": DATA_VINTAGE,
        "metric_definitions": build_meta()["metric_definitions"],
        "quality_measures": build_meta()["quality_measures"],
    }
    with open(COHORT_STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2, default=str, allow_nan=False)

    print("Building per-ACO browser index...")
    base_cols = ["ACO_ID", "ACO_Name", "Current_Track", "Risk_Model", "Rev_Exp_Cat",
                 "N_AB", "Agreement_Period_Num", "Current_Start_Date", "EarnSaveLoss",
                 "final_adj_type"]
    needed = base_cols + [src for _k, _l, src, _u, _h, _d in METRICS] + [k for k, *_ in QUALITY_MEASURES]

    def clean(v):
        if v is None:
            return None
        if isinstance(v, float):
            return None if (np.isnan(v) or np.isinf(v)) else v
        if isinstance(v, (int, np.integer)):
            return int(v)
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        return v

    index_records = {}
    for label, df in (("PY2024", df24), ("PY2023", df23)):
        cols = [c for c in dict.fromkeys(needed) if c in df.columns]
        records = df[cols].to_dict(orient="records")
        index_records[label] = [{k: clean(v) for k, v in rec.items()} for rec in records]
    with open(ACO_INDEX_PATH, "w") as f:
        json.dump(index_records, f, allow_nan=False, default=str)

    with open(ACO_STATES_PATH, "w") as f:
        json.dump(aco_states, f)
    with open(META_PATH, "w") as f:
        json.dump(build_meta(), f, indent=2)

    print()
    print("=" * 68)
    for p in (SQLITE_PATH, COHORT_STATS_PATH, ACO_INDEX_PATH, ACO_STATES_PATH, META_PATH):
        print(f"{p.name:24} {p.stat().st_size:>10,} bytes")
    print()
    print("PY2024 sanity — new KPIs, all-ACO cohort:")
    for k in ("risk_ratio_by3_py", "expense_trend_by3_py", "final_adj"):
        t = stats["PY2024"]["cohorts"]["all"]["All ACOs"]["metrics"][k]
        print(f"  {k:22} n={t['n']:>3}  p25={t['p25']:>8}  p50={t['p50']:>8}  p75={t['p75']:>8}")
    print()
    print("Quality coverage (all-ACO cohort):")
    for k, l, *_ in QUALITY_MEASURES[:6]:
        t = stats["PY2024"]["cohorts"]["all"]["All ACOs"]["metrics"][k]
        print(f"  {l[:46]:46} n={t['n']:>3}  p50={t.get('p50')}")


if __name__ == "__main__":
    main()
