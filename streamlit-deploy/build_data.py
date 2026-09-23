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
    ("pc_services_per_1000", "Primary Care Services / 1000", "P_EM_Total", "per 1k", None,
     "All evaluation & management services per 1,000 person-years. Deliberately "
     "undirected: raising ambulatory contact is usually how an ACO drives acute "
     "utilization down, so a high value is not on its own a negative finding."),
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
    ("risk_ratio_by3_py", "Risk Score Ratio (BY3 → PY)", "risk_ratio_by3_py", "pct_ratio", None,
     "Membership-weighted CMS-HCC risk score in the performance year divided by the "
     "membership-weighted risk score in benchmark year 3. Above 1.00 means documented "
     "risk grew relative to the benchmark period."),
    ("expense_trend_by3_py", "Medical Expense Trend (BY3 → PY)", "expense_trend_by3_py", "pct_ratio",
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
# Medical expense & utilization registry.
#
# (key, label, source_col, unit, higher_is_better, important, family, description)
#
# On `higher_is_better`: this drives percentile direction, so a wrong value
# tells an ACO the opposite of the truth. Categories where reducing spend is
# unambiguously the goal are marked False. Categories where the value-based
# reading is genuinely two-sided are marked None (ranked, but reported without
# a good/bad verdict) rather than guessed:
#   - Hospice, home health, physician/professional services and primary-care
#     E&M all rise when an ACO substitutes lower-acuity care for inpatient
#     care, which is the intended behaviour, not a failure.
# The narrative prompt is told to treat None-direction metrics as descriptive.
#
# `important` mirrors the 13 fields the actuarial reviewer asked to surface as
# cards; the remainder render in an expandable table. All of them are fed to
# the narrative regardless of display.
# ---------------------------------------------------------------------------
EXPENSE_METRICS = [
    # ---- Cost: per-capita annualized spend ($) ----
    ("cap_inp_all", "Inpatient — All", "CapAnn_INP_All", "$", False, True, "cost",
     "Per-capita annualized inpatient spend, all inpatient settings combined."),
    ("cap_inp_short", "Inpatient — Short-Term", "CapAnn_INP_S_trm", "$", False, False, "cost",
     "Per-capita annualized short-term acute inpatient spend."),
    ("cap_inp_long", "Inpatient — Long-Term", "CapAnn_INP_L_trm", "$", False, False, "cost",
     "Per-capita annualized long-term care hospital spend."),
    ("cap_inp_rehab", "Inpatient — Rehab", "CapAnn_INP_Rehab", "$", False, False, "cost",
     "Per-capita annualized inpatient rehabilitation facility spend."),
    ("cap_inp_psych", "Inpatient — Psych", "CapAnn_INP_Psych", "$", False, False, "cost",
     "Per-capita annualized inpatient psychiatric facility spend."),
    ("cap_hospice", "Hospice", "CapAnn_HSP", "$", None, False, "cost",
     "Per-capita annualized hospice spend. Two-sided: higher spend can reflect "
     "appropriate end-of-life care that displaces aggressive inpatient utilization."),
    ("cap_snf", "Skilled Nursing Facility", "CapAnn_SNF", "$", False, True, "cost",
     "Per-capita annualized SNF spend — a primary post-acute lever in value-based care."),
    ("cap_opd", "Hospital Outpatient", "CapAnn_OPD", "$", False, True, "cost",
     "Per-capita annualized hospital outpatient department spend."),
    ("cap_pb", "Physician / Professional", "CapAnn_PB", "$", None, True, "cost",
     "Per-capita annualized Part B professional services spend. Two-sided: higher "
     "professional spend may reflect stronger ambulatory access rather than waste."),
    ("cap_ambulance", "Ambulance", "CapAnn_AmbPay", "$", False, False, "cost",
     "Per-capita annualized ambulance spend."),
    ("cap_hha", "Home Health", "CapAnn_HHA", "$", None, False, "cost",
     "Per-capita annualized home health spend. Two-sided: home health frequently "
     "substitutes for more expensive SNF days."),
    ("cap_dme", "Durable Medical Equipment", "CapAnn_DME", "$", False, True, "cost",
     "Per-capita annualized DME spend."),
    # ---- Utilization: per 1,000 person-years unless noted ----
    ("util_adm", "Inpatient Discharges / 1000", "ADM", "per 1k", False, True, "utilization",
     "Acute inpatient discharges per 1,000 person-years."),
    ("util_adm_short", "Discharges — Short-Term / 1000", "ADM_S_Trm", "per 1k", False, False, "utilization",
     "Short-term acute inpatient discharges per 1,000 person-years."),
    ("util_adm_long", "Discharges — Long-Term / 1000", "ADM_L_Trm", "per 1k", False, False, "utilization",
     "Long-term care hospital discharges per 1,000 person-years."),
    ("util_adm_rehab", "Discharges — Rehab / 1000", "ADM_Rehab", "per 1k", False, False, "utilization",
     "Inpatient rehabilitation discharges per 1,000 person-years."),
    ("util_ed_visits", "ED Visits / 1000", "P_EDV_Vis", "per 1k", False, True, "utilization",
     "All emergency department visits per 1,000 person-years."),
    ("util_ed_hosp", "ED Visits Leading to Admission / 1000", "P_EDV_Vis_HOSP", "per 1k", False, True, "utilization",
     "Emergency department visits that resulted in a hospital admission, per 1,000 person-years."),
    ("util_ct", "CT Scans / 1000", "P_CT_VIS", "per 1k", False, False, "utilization",
     "CT imaging events per 1,000 person-years."),
    ("util_mri", "MRI Scans / 1000", "P_MRI_VIS", "per 1k", False, False, "utilization",
     "MRI imaging events per 1,000 person-years."),
    ("util_em_total", "Primary Care Services (E&M Total) / 1000", "P_EM_Total", "per 1k", None, True, "utilization",
     "All evaluation & management services per 1,000 person-years. Two-sided: higher "
     "ambulatory contact is often the mechanism by which acute utilization falls."),
    ("util_em_pcp", "E&M — Primary Care / 1000", "P_EM_PCP_Vis", "per 1k", None, True, "utilization",
     "Primary care E&M visits per 1,000 person-years. Two-sided, as above."),
    ("util_em_spec", "E&M — Specialist / 1000", "P_EM_SP_Vis", "per 1k", None, True, "utilization",
     "Specialist E&M visits per 1,000 person-years. Two-sided: appropriate specialty "
     "access and specialty overuse both raise this."),
    ("util_nurse", "Nurse Practitioner Visits / 1000", "P_Nurse_Vis", "per 1k", None, True, "utilization",
     "Nurse practitioner / physician assistant visits per 1,000 person-years."),
    ("util_fqhc_rhc", "FQHC / RHC Visits / 1000", "P_FQHC_RHC_Vis", "per 1k", None, False, "utilization",
     "Federally qualified health center and rural health clinic visits per 1,000 person-years."),
    ("util_snf_adm", "SNF Discharges / 1000", "P_SNF_ADM", "per 1k", False, True, "utilization",
     "Skilled nursing facility discharges per 1,000 person-years."),
    ("util_snf_los", "SNF Length of Stay (days)", "SNF_LOS", "days", False, True, "utilization",
     "Average length of stay per SNF admission."),
    ("util_snf_pay", "SNF Payment per Stay", "SNF_PayperStay", "$", False, False, "utilization",
     "Average Medicare payment per SNF stay."),
]

# Metrics CMS publishes as a proportion (0.1647) where every sibling metric on
# the same unit is published on a 0-100 scale. Rescaled once, at build time, so
# cohort percentiles and rendering agree. Measure_479 is the readmission rate:
# 0.1647 is 16.47%, and rendering it as "0.16%" understates it 100-fold.
RESCALE_X100 = {"readmits_proxy"}


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


# ---------------------------------------------------------------------------
# Metrics anchored to Benchmark Year 3. Their level depends on how many years
# separate BY3 from the performance year, so they must be compared only against
# ACOs with the same BY3 vintage. Median expense trend runs 1.07 at a one-year
# gap versus 1.19 at three years — pooling them inverts rankings.
# ---------------------------------------------------------------------------
VINTAGE_SENSITIVE = {"risk_ratio_by3_py", "expense_trend_by3_py"}


# ---------------------------------------------------------------------------
# Quality determination flags.
#
# MSSP quality is a CLIFF, not a slider. Met_QPS decides whether an ACO earns
# its track's full sharing rate. In PY2024, median final share rate was 50.0%
# for ACOs meeting the standard versus 30.6% for those that did not.
#
# confidence: "high" = corroborated by the published data relationships below;
# "verify" = plausible reading that should be checked against the CMS data
# dictionary before being asserted to a client.
# ---------------------------------------------------------------------------
QUALITY_FLAGS = [
    ("Met_QPS", "Met Quality Performance Standard",
     "The primary quality gate. Meeting it makes the ACO eligible for its track's maximum "
     "sharing rate. PY2024: 447 of 476 ACOs met it; median final share rate was 50.0% for "
     "those that did versus 30.6% for those that did not.", "high"),
    ("Met_AltQPS", "Met Alternative Quality Performance Standard",
     "Second-chance path, evaluated only when Met_QPS = 0. In PY2024 it is populated for "
     "exactly the 29 ACOs that missed the primary standard, and all 29 met it — so the "
     "practical effect is a reduced, scaled sharing rate rather than forfeiture.", "high"),
    ("Met_40pctl", "Met 40th-percentile quality threshold",
     "The main route to the quality performance standard. Every ACO that missed the QPS also "
     "missed this threshold. 57 ACOs met the QPS without it, mostly via first-year "
     "reporting flexibility.", "high"),
    ("Met_FirstYear", "First performance year in agreement",
     "First-year ACOs satisfy the standard by reporting rather than by score. This is why a "
     "quality score as low as 34.29 can still show Met_QPS = 1.", "high"),
    ("Met_SSP_quality_reporting_requirements", "Met SSP quality reporting requirements",
     "Baseline reporting compliance. 471 of 476 ACOs met it in PY2024; failing it is a "
     "serious operational red flag independent of score.", "high"),
    ("Report_WI", "Reported via CMS Web Interface",
     "Reporting mechanism used. Scores are not perfectly comparable across mechanisms.", "high"),
    ("Report_eCQM_CQM_MedicareCQM", "Reported via eCQM / MIPS CQM / Medicare CQM",
     "Reporting mechanism used. Scores are not perfectly comparable across mechanisms.", "high"),
    # Confirmed against the CMS data dictionary (supplied by the actuarial
    # reviewer, Aug 2026). An earlier build inferred Met_Incentive from data
    # relationships and read it as a needs-based adjustment. That was wrong:
    # it is a REPORTING incentive tied to eCQM/MIPS CQM submission. The
    # inferred reading is recorded here only so it is not re-derived.
    ("Met_Incentive", "Met eCQM / MIPS CQM reporting incentive criteria",
     "Qualifies for the eCQM/MIPS CQM reporting incentive. For PY2024 an ACO qualifies if it "
     "reports all three eCQMs/MIPS CQMs, meets the MIPS data completeness requirement for all "
     "three, and scores at or above the 10th percentile of the performance benchmark on at "
     "least one of the four outcome measures in the APP set AND at or above the 40th "
     "percentile on at least one of the remaining five measures. Does not apply to Medicare "
     "CQMs. Eligibility is determined independently of the measures that feed the ACO's "
     "quality score — so this flag says nothing about quality performance itself.", "high"),
    ("Recvd40p", "Quality score floored at the 40th percentile (extreme & uncontrollable circumstances)",
     "Set when an ACO was determined to be affected by an extreme and uncontrollable "
     "circumstance in PY2024, in which case its quality score is set to the higher of its own "
     "score or the equivalent of the 40th-percentile MIPS quality performance category score "
     "across all MIPS scores, excluding entities eligible for facility-based scoring. Low "
     "analytic value: it is uncommon by design and reflects a disaster adjustment rather than "
     "ACO performance. Do not use it to explain a quality result.", "high"),
]


COHORT_DEFS = {
    "track": lambda r: {
        "A": "BASIC-A", "B": "BASIC-B", "C": "BASIC-C",
        "D": "BASIC-D", "E": "BASIC-E", "EN": "ENHANCED",
    }.get(str(r.get("Current_Track", "")).strip()),
    "risk_model": lambda r: r.get("Risk_Model"),
    "rev_cat": lambda r: r.get("Rev_Exp_Cat"),
    "size_band": lambda r: _size_band(r.get("N_AB")),
    "by3_vintage": lambda r: r.get("by3_vintage"),
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


# CMS uses two visually similar but semantically different null sentinels.
# Conflating them loses real information: '-' means the field does not apply
# to this ACO, '*' means CMS computed a value and withheld it because the cell
# was too small to publish. Both are NaN numerically; only the second one means
# "this ACO has a value you are not allowed to see."
NULL_NOT_APPLICABLE = {"", "-", "--", ".", "NA", "N/A", "NULL", "Not published"}
NULL_SUPPRESSED = {"*", "**"}


def _to_num(s):
    """Coerce a CMS PUF cell to float, or NaN.

    The PUF is not internally consistent across performance years. PY2024
    publishes rate fields as bare floats (8.89); PY2023 publishes the SAME
    fields as percent-formatted strings ('9.09%'). Nine columns are affected
    in PY2023 — Sav_rate, MinSavPerc, QualScore, FinalShareRate,
    FinalLossRate, Perc_Dual, Perc_CovDiag, Perc_CovEpisode, Perc_LTI — and
    every one of them silently became NaN before the '%' strip was added here.

    Both years express these on a 0-100 scale ('9.09%' -> 9.09, matching
    PY2024's 8.89), so stripping the suffix is sufficient. Do NOT rescale.
    """
    if pd.isna(s):
        return np.nan
    if isinstance(s, (int, float, np.integer, np.floating)):
        return float(s)
    s = str(s).strip().replace(",", "").replace("$", "").replace("%", "")
    if s in NULL_NOT_APPLICABLE or s in NULL_SUPPRESSED:
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def _is_suppressed(s) -> bool:
    """True when CMS withheld this cell for small cell size (as opposed to n/a)."""
    return not pd.isna(s) and str(s).strip() in NULL_SUPPRESSED


# ---------------------------------------------------------------------------
# Cross-year column compatibility.
#
# CMS renamed several fields between PY2023 and PY2024. Two of the renames are
# NOT cosmetic — the underlying percentile threshold moved from the 30th to the
# 40th. Aliasing Met_30pctl onto Met_40pctl keeps one code path, but the label
# shown to the user has to stay year-accurate or the tool will tell a 2023 ACO
# it cleared a bar that did not exist that year. YEAR_THRESHOLD_LABELS carries
# that correction downstream.
# ---------------------------------------------------------------------------
YEAR_ALIASES = {
    2023: {
        "PosRegAdj": "RegAdj",
        "Met_30pctl": "Met_40pctl",
        "Recvd30p": "Recvd40p",
        "Report_eCQM_CQM": "Report_eCQM_CQM_MedicareCQM",
    },
}

# Fields CMS simply did not publish in a given year. Left absent (NaN) rather
# than defaulted, so they rank as "no data" instead of as a real zero.
YEAR_UNAVAILABLE = {
    2023: ["PriorSavAdj", "FinalAdjCat", "Met_SSP_quality_reporting_requirements"],
}

# Percentile threshold for the alternative quality standard, by year.
YEAR_THRESHOLD_LABELS = {2023: "30th", 2024: "40th"}


def apply_year_aliases(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Map an older PUF's column names onto the current schema."""
    aliases = YEAR_ALIASES.get(year, {})
    present = {old: new for old, new in aliases.items()
               if old in df.columns and new not in df.columns}
    if present:
        df = df.rename(columns=present)
        print(f"  PY{year} column aliases applied: "
              + ", ".join(f"{o}->{n}" for o, n in present.items()))
    missing = [c for c in YEAR_UNAVAILABLE.get(year, []) if c not in df.columns]
    if missing:
        print(f"  PY{year} not published by CMS (left null): {', '.join(missing)}")
    return df


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

    if "FinalAdjCat" in df.columns:
        # CMS published which adjustment it selected, so the applied figure is
        # knowable. "No Adjustment" genuinely means zero.
        final = pd.Series(0.0, index=df.index)
        final = final.mask(cat.str.contains("regional", na=False), reg)
        final = final.mask(cat.str.contains("prior", na=False), psa)
        final = final.fillna(0.0)
    else:
        # PY2023 published the regional adjustment but never disclosed which
        # adjustment was applied. Defaulting to zero would put every 2023 ACO
        # at $0 and rank them against each other on a number CMS never
        # released, so this stays null and the KPI reads "not published".
        final = pd.Series(np.nan, index=df.index)
    # --- 4. BY3 vintage -----------------------------------------------------
    # Benchmark years are the three years preceding the current agreement
    # period, so BY3 is the year before the agreement start. The gap between
    # BY3 and the performance year drives the level of both BY3-anchored
    # ratios, which is why they get their own peer cohort.
    start = pd.to_datetime(df.get("Current_Start_Date"), errors="coerce")
    df["by3_year"] = (start.dt.year - 1).astype("Int64")
    py = int(df["performance_year"].iloc[0]) if "performance_year" in df.columns else None
    df["by3_vintage"] = df["by3_year"].apply(
        lambda y: f"BY3 {int(y)}" if pd.notna(y) else None)

    df["final_adj"] = final
    df["final_adj_type"] = (
        df["FinalAdjCat"].astype(str).str.strip() if "FinalAdjCat" in df.columns
        else pd.Series("Not published", index=df.index)
    )
    return df


def add_quality_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the quality determination flags to 1 / 0 / None."""
    df = df.copy()
    for col, *_ in QUALITY_FLAGS:
        if col in df.columns:
            df[col] = df[col].apply(_to_num)
        else:
            df[col] = np.nan
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
    """(key, source_column) for every rankable metric — financial, expense, quality."""
    pairs = [(k, src) for k, _l, src, _u, _h, _d in METRICS]
    pairs += [(k, src) for k, _l, src, _u, _h, _i, _f, _d in EXPENSE_METRICS]
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
        # Stamped every build so a deployed instance can be identified on sight.
        # DATA_VINTAGE describes which CMS file was used and does not change
        # when the build logic changes — which made it impossible to tell a
        # freshly deployed app from a stale one. This does change, every time.
        "build_id": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "metric_definitions": [
            {"key": k, "label": l, "source_col": src, "unit": u,
             "higher_is_better": h, "description": d, "family": "financial",
             "vintage_sensitive": k in VINTAGE_SENSITIVE}
            for (k, l, src, u, h, d) in METRICS
        ],
        "expense_metrics": [
            {"key": k, "label": l, "source_col": src, "unit": u,
             "higher_is_better": h, "important": imp, "family": fam,
             "description": d, "vintage_sensitive": False}
            for (k, l, src, u, h, imp, fam, d) in EXPENSE_METRICS
        ],
        "quality_flags": [
            {"key": k, "label": l, "description": d, "confidence": c}
            for (k, l, d, c) in QUALITY_FLAGS
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
            "by3_vintage":
                "BY3 is the year before the current agreement period start, so the gap between "
                "BY3 and the performance year varies from one to six years across ACOs. Because "
                "both BY3-anchored ratios accumulate drift over that gap (PY2024 median expense "
                "trend: 1.07 at a one-year gap versus 1.19 at three years), those two metrics are "
                "always ranked only against ACOs in the selected cohort that share the same BY3 "
                "vintage. Other metrics are ranked against the whole cohort unless the reader "
                "turns on 'Same BY3 vintage only', which narrows every comparison the same way.",
            "quality_cliff":
                "MSSP quality is a threshold, not a slider. Met_QPS determines eligibility for the "
                "track's full sharing rate; ACOs missing it fall to the alternative standard and a "
                "reduced scaled rate. PY2024 median final share rate: 50.0 percent for ACOs meeting "
                "the standard, 30.6 percent for those that did not.",
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
    df23 = apply_year_aliases(df23, 2023)

    for df in (df24, df23):
        for _k, _l, src, _u, _h, _d in METRICS:
            if src in df.columns:
                df[src] = df[src].apply(_to_num)
        for _k, _l, src, _u, _h, _i, _f, _d in EXPENSE_METRICS:
            if src in df.columns:
                df[src] = df[src].apply(_to_num)
        for col in ("N_AB", "Current_Track", "Risk_Model", "Rev_Exp_Cat"):
            if col in df.columns and df[col].dtype == "object":
                df[col] = df[col].astype(str).str.strip()

    # Put proportion-scaled fields on the same 0-100 scale as their siblings.
    # Done after coercion and before any percentile is computed, so cohort
    # tables and rendered values can never disagree.
    src_by_key = {k: src for k, _l, src, _u, _h, _d in METRICS}
    for df, yr in ((df24, 2024), (df23, 2023)):
        for key in RESCALE_X100:
            src = src_by_key.get(key)
            if src and src in df.columns:
                before = df[src].dropna()
                df[src] = df[src] * 100.0
                if len(before):
                    print(f"  PY{yr} {src} rescaled x100 "
                          f"(median {before.median():.4f} -> {before.median()*100:.2f})")

    print("Computing derived KPIs...")
    df24["performance_year"] = 2024
    df23["performance_year"] = 2023
    df24 = add_quality_flags(add_quality_measures(add_derived_kpis(df24)))
    df23 = add_quality_flags(add_quality_measures(add_derived_kpis(df23)))
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
        "expense_metrics": build_meta()["expense_metrics"],
        "quality_measures": build_meta()["quality_measures"],
    }
    with open(COHORT_STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2, default=str, allow_nan=False)

    # Which metrics are structurally unavailable in each year, derived from
    # actual coverage rather than a hardcoded list so it stays true as new
    # performance years are added. A metric null for EVERY ACO in a year was
    # not published by CMS that year; a metric null for only some ACOs simply
    # does not apply to those ACOs. The UI needs to say different things.
    unavailable_by_year = {}
    for label, df in (("PY2024", df24), ("PY2023", df23)):
        missing = []
        for key, src in all_metric_keys():
            if src not in df.columns:
                missing.append(key)
            elif pd.to_numeric(df[src], errors="coerce").notna().sum() == 0:
                missing.append(key)
        unavailable_by_year[label] = sorted(set(missing))
        print(f"  {label} metrics not published by CMS: {len(missing)}"
              + (f" ({', '.join(missing[:6])}{'...' if len(missing) > 6 else ''})"
                 if missing else ""))

    print("Building per-ACO browser index...")
    base_cols = ["ACO_ID", "ACO_Name", "Current_Track", "Risk_Model", "Rev_Exp_Cat",
                 "N_AB", "Agreement_Period_Num", "Current_Start_Date", "EarnSaveLoss",
                 "final_adj_type", "by3_year", "by3_vintage"]
    base_cols += [c for c, *_ in QUALITY_FLAGS]
    needed = (base_cols
              + [src for _k, _l, src, _u, _h, _d in METRICS]
              + [src for _k, _l, src, _u, _h, _i, _f, _d in EXPENSE_METRICS]
              + [k for k, *_ in QUALITY_MEASURES])

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
        json.dump({**build_meta(), "unavailable_by_year": unavailable_by_year},
                  f, indent=2)

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
