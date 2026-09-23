"""
Benchmarking engine — pure logic, no UI, no branding.

v2 adds:
  * Quality-measure profiling (strengths / gaps vs. any cohort)
  * Regional peer cohorts (ACOs sharing >=1 service-area state)
  * Exact percentile ranks computed from peer raw values, replacing
    interpolation across stored percentile breaks
  * Direction-aware ranking so inverse measures (HbA1c poor control,
    readmissions, MCC admissions, cost) are never reported backwards

Entry points:
    find_acos(query)                      fuzzy ACO search
    benchmark_aco(aco_id)                 full financial benchmark report
    benchmark_synthetic(values, filters)  benchmark user-supplied numbers
    quality_profile(aco_id, cohort)       ranked quality strengths & gaps
    regional_peers(aco_id)                peer ACO ids sharing a service area
"""
from __future__ import annotations

import difflib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SQLITE_PATH = DATA / "aco_data.sqlite"
COHORT_STATS_PATH = DATA / "cohort_stats.json"
ACO_INDEX_PATH = DATA / "aco_index.json"
ACO_STATES_PATH = DATA / "aco_states.json"
META_PATH = DATA / "meta.json"

# Below this many peers a regional comparison is directional at best.
MIN_REGIONAL_N = 10


# ---------------------------------------------------------------------------
# Cached loaders
# ---------------------------------------------------------------------------
def _load_json(path: Path):
    with open(path) as f:
        return json.load(f)


def cohort_stats() -> dict:
    if not hasattr(cohort_stats, "_c"):
        cohort_stats._c = _load_json(COHORT_STATS_PATH)
    return cohort_stats._c


def aco_index() -> dict:
    if not hasattr(aco_index, "_c"):
        aco_index._c = _load_json(ACO_INDEX_PATH)
    return aco_index._c


def aco_states() -> dict:
    if not hasattr(aco_states, "_c"):
        try:
            aco_states._c = _load_json(ACO_STATES_PATH)
        except FileNotFoundError:
            aco_states._c = {}
    return aco_states._c


def meta() -> dict:
    if not hasattr(meta, "_c"):
        meta._c = _load_json(META_PATH)
    return meta._c


METRIC_DEFS = {m["key"]: m for m in meta()["metric_definitions"]}
EXPENSE_DEFS = {m["key"]: m for m in meta().get("expense_metrics", [])}
QUALITY_FLAG_DEFS = {f["key"]: f for f in meta().get("quality_flags", [])}
QUALITY_DEFS = {m["key"]: m for m in meta()["quality_measures"]}
ALL_DEFS = {**METRIC_DEFS, **EXPENSE_DEFS, **QUALITY_DEFS}

_TRACK_MAP = {"A": "BASIC-A", "B": "BASIC-B", "C": "BASIC-C",
              "D": "BASIC-D", "E": "BASIC-E", "EN": "ENHANCED"}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_for(year: int) -> str:
    if year not in (2023, 2024):
        raise ValueError(f"Performance year {year} not supported.")
    return f"aco_py{year}"


def _num(v):
    if v is None or v == "" or v == "-":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # drop NaN


def _size_band(n) -> str | None:
    n = _num(n)
    if n is None:
        return None
    if n < 5_000:   return "<5K lives"
    if n < 10_000:  return "5K–10K lives"
    if n < 25_000:  return "10K–25K lives"
    if n < 50_000:  return "25K–50K lives"
    if n < 100_000: return "50K–100K lives"
    return "100K+ lives"


def cohort_labels_for_row(row: dict) -> dict:
    return {
        "track": _TRACK_MAP.get(str(row.get("Current_Track", "")).strip()),
        "risk_model": row.get("Risk_Model"),
        "rev_cat": row.get("Rev_Exp_Cat"),
        "size_band": _size_band(row.get("N_AB")),
        "all": "All ACOs",
    }


# ---------------------------------------------------------------------------
# Regional peering
# ---------------------------------------------------------------------------
def regional_peers(aco_id: str) -> list[str]:
    """ACO ids sharing at least one service-area state (excludes self)."""
    states_map = aco_states()
    mine = set(states_map.get(aco_id, []))
    if not mine:
        return []
    return [aid for aid, sts in states_map.items()
            if aid != aco_id and mine.intersection(sts)]


def regional_label(aco_id: str) -> str:
    sts = aco_states().get(aco_id, [])
    if not sts:
        return "Regional peers"
    shown = ", ".join(sts[:4]) + ("…" if len(sts) > 4 else "")
    return f"Regional peers ({shown})"


# ---------------------------------------------------------------------------
# Exact percentile ranking
# ---------------------------------------------------------------------------
def _rank_exact(value: float | None, peer_values: list[float],
                higher_is_better: bool | None) -> dict:
    """Percentile rank of `value` within `peer_values`.

    Always reported on the raw scale (p90 = high value). `performance_pct`
    additionally flips inverse measures so that 90 always means "good".
    """
    vals = [v for v in peer_values if v is not None]
    if value is None or not vals:
        return {"value": value, "rank_pct": None, "performance_pct": None,
                "rank_label": "n/a", "cohort_n": len(vals)}
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    rank = (below + 0.5 * equal) / len(vals) * 100.0
    perf = rank if higher_is_better is not False else 100.0 - rank
    srt = sorted(vals)

    def q(p):
        if not srt:
            return None
        i = (len(srt) - 1) * p
        lo, hi = int(i), min(int(i) + 1, len(srt) - 1)
        return srt[lo] + (srt[hi] - srt[lo]) * (i - lo)

    return {
        "value": value,
        "rank_pct": round(rank, 1),
        "performance_pct": round(perf, 1),
        "rank_label": _rank_label(perf if higher_is_better is not None else rank,
                                  neutral=higher_is_better is None),
        "cohort_p10": q(0.10), "cohort_p25": q(0.25), "cohort_p50": q(0.50),
        "cohort_p75": q(0.75), "cohort_p90": q(0.90),
        "cohort_mean": sum(vals) / len(vals),
        "cohort_min": srt[0], "cohort_max": srt[-1],
        "cohort_n": len(vals),
    }


def _rank_label(pct: float, neutral: bool = False) -> str:
    if neutral:
        return f"p{pct:.0f} of cohort"
    if pct >= 90: return "top decile"
    if pct >= 75: return "top quartile"
    if pct >= 50: return "above median"
    if pct >= 25: return "below median"
    return "bottom quartile"


# ---------------------------------------------------------------------------
# Search / fetch
# ---------------------------------------------------------------------------
@dataclass
class AcoMatch:
    aco_id: str
    aco_name: str
    n_beneficiaries: float | None
    track: str | None
    rev_cat: str | None
    score: float


def find_acos(name_query: str, performance_year: int = 2024, limit: int = 10) -> list[dict]:
    if not name_query or not name_query.strip():
        return []
    q = name_query.strip().lower()
    rows = aco_index().get(f"PY{performance_year}", [])
    matches: list[AcoMatch] = []
    for r in rows:
        name = (r.get("ACO_Name") or "").lower()
        if not name:
            continue
        ratio = difflib.SequenceMatcher(None, q, name).ratio()
        if q in name:
            ratio = max(ratio, 0.85)
        if ratio < 0.4:
            continue
        matches.append(AcoMatch(
            aco_id=r["ACO_ID"], aco_name=r["ACO_Name"],
            n_beneficiaries=r.get("N_AB"),
            track=_TRACK_MAP.get(str(r.get("Current_Track") or "").strip()),
            rev_cat=r.get("Rev_Exp_Cat"), score=round(ratio, 3),
        ))
    matches.sort(key=lambda m: -m.score)
    return [m.__dict__ for m in matches[:limit]]


def get_aco(aco_id: str, performance_year: int = 2024) -> dict | None:
    for r in aco_index().get(f"PY{performance_year}", []):
        if r.get("ACO_ID") == aco_id:
            return r
    return None


def _peer_rows(cohort_name: str, cohort_value, rows: list[dict],
               subject_id: str | None = None) -> list[dict]:
    """Rows belonging to a cohort. 'regional' is handled specially."""
    if cohort_name == "regional":
        peers = set(regional_peers(subject_id or ""))
        return [r for r in rows if r.get("ACO_ID") in peers]
    if cohort_name == "all":
        return rows
    if cohort_name == "by3_vintage":
        return [r for r in rows if r.get("by3_vintage") == cohort_value]
    if cohort_name == "size_band":
        return [r for r in rows if _size_band(r.get("N_AB")) == cohort_value]
    if cohort_name == "track":
        return [r for r in rows
                if _TRACK_MAP.get(str(r.get("Current_Track") or "").strip()) == cohort_value]
    key = {"risk_model": "Risk_Model", "rev_cat": "Rev_Exp_Cat"}.get(cohort_name)
    if key is None:
        return []
    return [r for r in rows if r.get(key) == cohort_value]


def _same_vintage(peers: list[dict], row: dict) -> list[dict]:
    v = row.get("by3_vintage")
    return [p for p in peers if p.get("by3_vintage") == v] if v else peers


def _group_peers(row: dict, aco_id: str, cohort: str,
                 rows: list[dict]) -> tuple[str, str, list[dict]]:
    """Resolve the ACO's group within `cohort`; falls back to all ACOs if it has none."""
    cvalue = (regional_label(aco_id) if cohort == "regional"
              else cohort_labels_for_row(row).get(cohort))
    if cohort != "regional" and not cvalue:
        cohort, cvalue = "all", "All ACOs"
    return cohort, cvalue, _peer_rows(cohort, cvalue, rows, subject_id=aco_id)


def cohort_sizes(aco_id: str, performance_year: int = 2024) -> dict[str, tuple[str, int, int]]:
    """cohort name -> (this ACO's group, ACOs in the group, of those with the same BY3 vintage)."""
    row = get_aco(aco_id, performance_year)
    if not row:
        return {}
    rows = aco_index().get(f"PY{performance_year}", [])
    groups = [(name, value) for name, value in cohort_labels_for_row(row).items() if value]
    sts = aco_states().get(aco_id, [])
    if sts:
        groups.append(("regional", ", ".join(sts[:4]) + ("…" if len(sts) > 4 else "")))
    out = {}
    for name, value in groups:
        peers = _peer_rows(name, value, rows, subject_id=aco_id)
        out[name] = (value, len(peers), len(_same_vintage(peers, row)))
    return out


# ---------------------------------------------------------------------------
# Financial benchmark
# ---------------------------------------------------------------------------
UNAVAILABLE_BY_YEAR = meta().get("unavailable_by_year", {})


def unavailable_note(key: str, performance_year: int, value) -> str | None:
    """Why a metric has no value — or None when it does.

    A bare dash conflates three different situations. CMS not publishing a
    field for a whole performance year is a statement about the program; the
    field not applying to one ACO is a statement about that ACO; and a
    suppressed cell is a statement about cohort size. Callers render the
    distinction rather than showing an ambiguous placeholder.
    """
    if value is not None:
        return None
    if key in UNAVAILABLE_BY_YEAR.get(f"PY{performance_year}", []):
        return f"Not published by CMS for PY{performance_year}"
    return "Not applicable to this ACO"


def benchmark_aco(aco_id: str, performance_year: int = 2024,
                  include_regional: bool = True, vintage_only: bool = False) -> dict | None:
    """Rank every metric against each cohort. BY3-anchored ratios are always ranked
    within same-vintage peers; `vintage_only` applies that to every metric."""
    row = get_aco(aco_id, performance_year)
    if not row:
        return None
    rows = aco_index().get(f"PY{performance_year}", [])
    labels = cohort_labels_for_row(row)

    cohort_specs = [(k, v) for k, v in labels.items() if v]
    if include_regional and regional_peers(aco_id):
        cohort_specs.append(("regional", regional_label(aco_id)))
    peers_by_cohort = {c: _peer_rows(c, v, rows, subject_id=aco_id) for c, v in cohort_specs}

    metric_results = {}
    for key, mdef in METRIC_DEFS.items():
        value = _num(row.get(mdef["source_col"], row.get(key)))
        same_vintage = vintage_only or bool(mdef.get("vintage_sensitive"))
        comparisons = {}
        for cname, cvalue in cohort_specs:
            peers = peers_by_cohort[cname]
            if same_vintage:
                peers = _same_vintage(peers, row)
            pvals = [_num(p.get(mdef["source_col"], p.get(key))) for p in peers]
            r = _rank_exact(value, pvals, mdef["higher_is_better"])
            r["small_sample"] = r["cohort_n"] < MIN_REGIONAL_N
            comparisons[cname] = {"cohort_value": cvalue, "same_vintage": same_vintage, **r}
        metric_results[key] = {**mdef, "value": value, "comparisons": comparisons,
                               "unavailable": unavailable_note(key, performance_year, value)}

    return {
        "aco": {
            "aco_id": row["ACO_ID"], "aco_name": row["ACO_Name"],
            "track": labels["track"], "rev_cat": labels["rev_cat"],
            "risk_model": labels["risk_model"], "size_band": labels["size_band"],
            "n_beneficiaries": row.get("N_AB"),
            "final_adj_type": row.get("final_adj_type"),
            "service_area": aco_states().get(aco_id, []),
            "regional_peer_n": len(peers_by_cohort.get("regional", [])),
            "by3_year": row.get("by3_year"),
            "by3_vintage": row.get("by3_vintage"),
            "quality_flags": quality_flags(row),
        },
        "vintage_only": vintage_only,
        "performance_year": performance_year,
        "data_vintage": meta().get("data_vintage"),
        "metrics": metric_results,
    }


def benchmark_synthetic(metric_values: dict[str, float],
                        cohort_filters: dict[str, str] | None = None,
                        performance_year: int = 2024) -> dict:
    cohort_filters = cohort_filters or {"all": "All ACOs"}
    rows = aco_index().get(f"PY{performance_year}", [])
    metric_results = {}
    for key, value in metric_values.items():
        mdef = ALL_DEFS.get(key)
        if not mdef:
            continue
        value = _num(value)
        src = mdef.get("source_col", key)
        comps = {}
        for cname, cvalue in cohort_filters.items():
            peers = _peer_rows(cname, cvalue, rows)
            pvals = [_num(p.get(src, p.get(key))) for p in peers]
            comps[cname] = {"cohort_value": cvalue,
                            **_rank_exact(value, pvals, mdef["higher_is_better"])}
        metric_results[key] = {**mdef, "value": value, "comparisons": comps}
    return {"aco": {"aco_name": "(your ACO)", **cohort_filters},
            "performance_year": performance_year,
            "data_vintage": meta().get("data_vintage"),
            "metrics": metric_results}


# ---------------------------------------------------------------------------
# Quality profiling
# ---------------------------------------------------------------------------
def quality_profile(aco_id: str, cohort: str = "track", performance_year: int = 2024,
                    vintage_only: bool = False) -> dict | None:
    """Every quality measure ranked against one cohort, split into
    strengths and gaps by direction-aware performance percentile."""
    row = get_aco(aco_id, performance_year)
    if not row:
        return None
    rows = aco_index().get(f"PY{performance_year}", [])
    labels = cohort_labels_for_row(row)
    cohort, cvalue, peers = _group_peers(row, aco_id, cohort, rows)
    if vintage_only:
        peers = _same_vintage(peers, row)

    measures = []
    for key, qdef in QUALITY_DEFS.items():
        value = _num(row.get(key))
        pvals = [_num(p.get(key)) for p in peers]
        r = _rank_exact(value, pvals, qdef["higher_is_better"])
        if r["performance_pct"] is None:
            continue
        measures.append({
            "key": key, "label": qdef["label"], "domain": qdef["domain"],
            "higher_is_better": qdef["higher_is_better"],
            "description": qdef["description"], **r,
        })

    measures.sort(key=lambda m: m["performance_pct"])
    gaps = [m for m in measures if m["performance_pct"] < 50]
    strengths = [m for m in measures if m["performance_pct"] >= 50]

    by_domain: dict[str, list] = {}
    for m in measures:
        by_domain.setdefault(m["domain"], []).append(m)
    domain_summary = [
        {"domain": d,
         "avg_performance_pct": round(sum(x["performance_pct"] for x in ms) / len(ms), 1),
         "n_measures": len(ms)}
        for d, ms in by_domain.items()
    ]
    domain_summary.sort(key=lambda d: d["avg_performance_pct"])

    return {
        "aco": {"aco_id": row["ACO_ID"], "aco_name": row["ACO_Name"],
                "quality_score": _num(row.get("QualScore")),
                "track": labels["track"], "size_band": labels["size_band"],
                "by3_vintage": row.get("by3_vintage"),
                "quality_flags": quality_flags(row)},
        "cohort": {"name": cohort, "value": cvalue, "n_peers": len(peers),
                   "same_vintage": vintage_only,
                   "small_sample": len(peers) < MIN_REGIONAL_N},
        "performance_year": performance_year,
        "data_vintage": meta().get("data_vintage"),
        "measures": measures,
        "biggest_gaps": gaps[:5],
        "top_strengths": list(reversed(strengths[-5:])),
        "domain_summary": domain_summary,
    }


def expense_profile(aco_id: str, cohort: str = "track", performance_year: int = 2024,
                    vintage_only: bool = False) -> dict | None:
    """Cost and utilization metrics ranked against one cohort.

    Deliberately mirrors quality_profile's cohort resolution so that the
    "Compare Against" control behaves identically on every tab.

    Metrics whose higher_is_better is None are two-sided: they are ranked and
    their percentile is reported, but they are kept out of strengths/gaps,
    because calling a high primary-care visit rate a "gap" would invert the
    actual value-based reading.
    """
    row = get_aco(aco_id, performance_year)
    if not row:
        return None
    rows = aco_index().get(f"PY{performance_year}", [])
    labels = cohort_labels_for_row(row)
    cohort, cvalue, group = _group_peers(row, aco_id, cohort, rows)
    vintage_peers = _same_vintage(group, row)
    peers = vintage_peers if vintage_only else group

    by3_metrics = []
    for key, mdef in METRIC_DEFS.items():
        if mdef.get("vintage_sensitive"):
            value = _num(row.get(mdef["source_col"], row.get(key)))
            pvals = [_num(p.get(mdef["source_col"], p.get(key))) for p in vintage_peers]
            by3_metrics.append({"key": key, "label": mdef["label"], "unit": mdef["unit"],
                                "higher_is_better": mdef["higher_is_better"],
                                "same_vintage": True,
                                **_rank_exact(value, pvals, mdef["higher_is_better"])})

    cost, utilization = [], []
    for key, mdef in EXPENSE_DEFS.items():
        src = mdef.get("source_col", key)
        value = _num(row.get(src, row.get(key)))
        pvals = [_num(p.get(src, p.get(key))) for p in peers]
        r = _rank_exact(value, pvals, mdef["higher_is_better"])
        rec = {**mdef, "value": value, **r,
               "two_sided": mdef["higher_is_better"] is None}
        (cost if mdef["family"] == "cost" else utilization).append(rec)

    directional = [m for m in cost + utilization
                   if m["performance_pct"] is not None and not m["two_sided"]]
    directional.sort(key=lambda m: m["performance_pct"])

    def _order(items):
        # Important metrics first (they render as cards), then by percentile so
        # the worst-performing rows surface at the top of the expandable table.
        return sorted(items, key=lambda m: (not m.get("important"),
                                            m["performance_pct"]
                                            if m["performance_pct"] is not None else 999))

    return {
        "aco": {"aco_id": row["ACO_ID"], "aco_name": row["ACO_Name"],
                "track": labels["track"], "size_band": labels["size_band"],
                "n_beneficiaries": row.get("N_AB"),
                "per_capita_exp": _num(row.get("Per_Capita_Exp_TOTAL_PY")),
                "expense_trend_by3_py": _num(row.get("expense_trend_by3_py")),
                "risk_ratio_by3_py": _num(row.get("risk_ratio_by3_py")),
                "by3_vintage": row.get("by3_vintage")},
        "cohort": {"name": cohort, "value": cvalue, "n_peers": len(peers),
                   "same_vintage": vintage_only,
                   "small_sample": len(peers) < MIN_REGIONAL_N},
        "by3_metrics": by3_metrics,
        "performance_year": performance_year,
        "data_vintage": meta().get("data_vintage"),
        "cost": _order(cost),
        "utilization": _order(utilization),
        "biggest_gaps": directional[:5],
        "top_strengths": list(reversed(directional[-5:])),
    }


def quality_flags(row: dict) -> list[dict]:
    """The quality determination flags with their values and meaning."""
    out = []
    for key, fdef in QUALITY_FLAG_DEFS.items():
        v = _num(row.get(key))
        out.append({**fdef, "value": (None if v is None else int(v)),
                    "state": "n/a" if v is None else ("Yes" if v == 1 else "No")})
    return out


def focus_report(report: dict, focus: str | None) -> dict:
    """Narrow a report's comparisons to one cohort so the narrative can hone in."""
    if not focus or focus == "all_cohorts":
        return report
    out = json.loads(json.dumps(report, default=str))
    for m in out.get("metrics", {}).values():
        if focus in m["comparisons"]:
            m["comparisons"] = {focus: m["comparisons"][focus]}
    out["focus_cohort"] = focus
    return out


def list_metrics() -> list[dict]:
    return list(METRIC_DEFS.values())


def list_quality_measures() -> list[dict]:
    return list(QUALITY_DEFS.values())


def list_cohort_options() -> dict[str, list[str]]:
    return {c: sorted(v.keys())
            for c, v in cohort_stats()["PY2024"]["cohorts"].items()}


if __name__ == "__main__":
    hits = find_acos("aledade delaware", limit=1)
    if not hits:
        raise SystemExit("no match")
    aid = hits[0]["aco_id"]
    rep = benchmark_aco(aid)
    a = rep["aco"]
    print(f"{a['aco_name']}  ({a['track']}, {a['rev_cat']}, {a['size_band']})")
    print(f"  service area: {', '.join(a['service_area'])}  |  regional peers: {a['regional_peer_n']}")
    print(f"  data vintage: {rep['data_vintage']}")
    print()
    for k in ("savings_rate", "risk_ratio_by3_py", "expense_trend_by3_py", "final_adj"):
        m = rep["metrics"][k]
        print(f"  {m['label']}: {m['value']}")
        for cn, c in m["comparisons"].items():
            flag = "  [small n]" if c.get("small_sample") else ""
            print(f"      {cn:10} {str(c['cohort_value'])[:34]:34} n={c['cohort_n']:>3} "
                  f"perf=p{c['performance_pct']} ({c['rank_label']}){flag}")
    print()
    qp = quality_profile(aid, cohort="track")
    print(f"Quality vs {qp['cohort']['value']} (n={qp['cohort']['n_peers']}), "
          f"composite {qp['aco']['quality_score']}")
    print("  Biggest gaps:")
    for m in qp["biggest_gaps"][:3]:
        print(f"    {m['label'][:50]:50} {m['value']:>7} → p{m['performance_pct']}")
    print("  Top strengths:")
    for m in qp["top_strengths"][:3]:
        print(f"    {m['label'][:50]:50} {m['value']:>7} → p{m['performance_pct']}")
