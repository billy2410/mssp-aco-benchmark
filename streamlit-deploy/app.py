"""
MSSP ACO Benchmark — Streamlit app.

Public CMS data only. No proprietary information. No company affiliation.
Designed to run on Streamlit Community Cloud or any personal machine.

Required secret/env (set in Streamlit Cloud → App settings → Secrets):
    ANTHROPIC_API_KEY = "sk-ant-..."

Optional secret for a basic password gate (recommended):
    APP_PASSWORD = "some-shared-passphrase"
"""
from __future__ import annotations

import json
import os

import pandas as pd
import streamlit as st

# Bridge Streamlit secrets to env vars so engine/llm work without changes.
for k in ("ANTHROPIC_API_KEY", "APP_PASSWORD"):
    if k in st.secrets and not os.environ.get(k):
        os.environ[k] = st.secrets[k]

import engine  # noqa: E402
import llm     # noqa: E402


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="MSSP ACO Benchmark",
    page_icon="📊",
    layout="wide",
)

# ---- Optional password gate. If APP_PASSWORD is set, require it. ---------
if os.environ.get("APP_PASSWORD"):
    if not st.session_state.get("_authed"):
        st.title("MSSP ACO Benchmark")
        st.write("Enter the access passphrase to continue.")
        with st.form("login"):
            pw = st.text_input("Passphrase", type="password")
            submitted = st.form_submit_button("Enter")
        if submitted:
            if pw == os.environ["APP_PASSWORD"]:
                st.session_state["_authed"] = True
                st.rerun()
            else:
                st.error("Incorrect passphrase.")
        st.stop()

st.markdown("""
<style>
    .big-num { font-size: 2.4rem; font-weight: 700; }
    .label   { font-size: 0.85rem; color: #6b7280; text-transform: uppercase;
               letter-spacing: 0.04em; }
    .pill    { display: inline-block; padding: 2px 10px; border-radius: 999px;
               font-size: 0.8rem; font-weight: 600; }
    .pill-good   { background: #d1fae5; color: #065f46; }
    .pill-mid    { background: #fef3c7; color: #92400e; }
    .pill-bad    { background: #fee2e2; color: #991b1b; }
    .pill-neutral{ background: #e5e7eb; color: #374151; }
    .small   { color: #6b7280; font-size: 0.85rem; }
    .source  { color: #6b7280; font-size: 0.8rem; padding-top: 12px;
               border-top: 1px solid #e5e7eb; margin-top: 24px; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def pill_for(rank_pct, higher_is_better):
    if rank_pct is None:
        return '<span class="pill pill-neutral">no data</span>'
    if higher_is_better is None:
        return f'<span class="pill pill-neutral">p{rank_pct:.0f}</span>'
    is_good = (rank_pct >= 75) if higher_is_better else (rank_pct <= 25)
    is_bad  = (rank_pct <= 25) if higher_is_better else (rank_pct >= 75)
    cls = "pill-good" if is_good else ("pill-bad" if is_bad else "pill-mid")
    if higher_is_better:
        label = (f"top decile (p{rank_pct:.0f})" if rank_pct >= 90 else
                 f"top quartile (p{rank_pct:.0f})" if rank_pct >= 75 else
                 f"above median (p{rank_pct:.0f})" if rank_pct >= 50 else
                 f"below median (p{rank_pct:.0f})" if rank_pct >= 25 else
                 f"bottom quartile (p{rank_pct:.0f})")
    else:
        label = (f"top decile (p{rank_pct:.0f})" if rank_pct <= 10 else
                 f"top quartile (p{rank_pct:.0f})" if rank_pct <= 25 else
                 f"better than median (p{rank_pct:.0f})" if rank_pct <= 50 else
                 f"worse than median (p{rank_pct:.0f})" if rank_pct <= 75 else
                 f"bottom quartile (p{rank_pct:.0f})")
    return f'<span class="pill {cls}">{label}</span>'


def format_value(value, unit):
    if value is None:
        return "—"
    if unit == "$":      return f"${value:,.0f}"
    if unit == "%":      return f"{value:.2f}%"
    if unit == "lives":  return f"{value:,.0f}"
    if unit == "days":   return f"{value:.1f}"
    if unit == "score":  return f"{value:.3f}"
    if unit == "per 1k": return f"{value:,.0f}"
    return f"{value}"


def render_metric_card(metric):
    label = metric["label"]
    val = format_value(metric["value"], metric["unit"])
    desc = metric["description"]
    pref = ["track", "rev_cat", "size_band", "risk_model", "all"]
    best_cohort = next((c for c in pref if c in metric["comparisons"]), None)

    st.markdown(f'<div class="label">{label}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="big-num">{val}</div>', unsafe_allow_html=True)

    if best_cohort:
        cmp = metric["comparisons"][best_cohort]
        st.markdown(
            pill_for(cmp["rank_pct"], metric["higher_is_better"]) +
            f' <span class="small">vs {cmp["cohort_value"]} (n={cmp["cohort_n"]})</span>',
            unsafe_allow_html=True,
        )

    with st.expander("Cohort detail", expanded=False):
        rows = []
        for cohort_name, cmp in metric["comparisons"].items():
            rows.append({
                "Cohort":   f"{cohort_name}: {cmp['cohort_value']}",
                "n":        cmp["cohort_n"],
                "Your value": format_value(metric["value"], metric["unit"]),
                "Cohort p25": format_value(cmp.get("cohort_p25"), metric["unit"]),
                "Cohort p50": format_value(cmp.get("cohort_p50"), metric["unit"]),
                "Cohort p75": format_value(cmp.get("cohort_p75"), metric["unit"]),
                "Rank":     f"p{cmp['rank_pct']:.0f}" if cmp["rank_pct"] is not None else "—",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        st.caption(desc)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("MSSP ACO Benchmark")
st.caption("Compare a Medicare Shared Savings Program ACO against its peer cohort. "
           "Powered by the CMS Performance Year Financial and Quality Results PUF.")

mode = st.radio(
    "Mode",
    ["Look up an ACO", "Benchmark my numbers", "Ask the data"],
    horizontal=True,
)


# ---------------------------------------------------------------------------
# Mode 1 — Look up an ACO by name
# ---------------------------------------------------------------------------
if mode == "Look up an ACO":
    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input("ACO name (full or partial)", placeholder="e.g. Aledade Delaware, MercyOne, Privia")
    with col2:
        py = st.selectbox("Performance Year", [2024, 2023], index=0)

    if query:
        candidates = engine.find_acos(query, performance_year=py, limit=12)
        if not candidates:
            st.warning("No ACOs match that name in the PUF.")
        else:
            options = {f"{c['aco_name']}  ·  {c['aco_id']}  ·  {int(c['n_beneficiaries']):,} lives": c["aco_id"]
                       for c in candidates if c["n_beneficiaries"]}
            picked_label = st.selectbox(f"Found {len(candidates)} matches — pick one", list(options.keys()))
            aco_id = options[picked_label]

            report = engine.benchmark_aco(aco_id, performance_year=py)
            if report:
                a = report["aco"]
                st.markdown(f"### {a['aco_name']}")
                meta = []
                if a["track"]:        meta.append(f"**Track:** {a['track']}")
                if a["rev_cat"]:      meta.append(f"**Revenue:** {a['rev_cat']}")
                if a["size_band"]:    meta.append(f"**Size:** {a['size_band']}")
                if a["n_beneficiaries"]: meta.append(f"**Beneficiaries:** {int(a['n_beneficiaries']):,}")
                if a["risk_model"]:   meta.append(f"**Risk:** {a['risk_model']}")
                st.markdown(" · ".join(meta))

                top = ["savings_rate", "quality_score", "per_capita_exp",
                       "admits_per_1000", "ed_visits_per_1000", "readmits_proxy"]
                cols = st.columns(3)
                for i, key in enumerate(top):
                    if key in report["metrics"]:
                        with cols[i % 3]:
                            render_metric_card(report["metrics"][key])
                            st.write("")

                st.markdown("### Other metrics")
                rest = [k for k in report["metrics"] if k not in top]
                cols = st.columns(3)
                for i, key in enumerate(rest):
                    with cols[i % 3]:
                        render_metric_card(report["metrics"][key])
                        st.write("")

                st.markdown("### Narrative")
                user_q = st.text_input(
                    "Optional: ask a specific question to focus the narrative",
                    placeholder="e.g. Where should we focus next year?",
                )
                if st.button("Generate narrative"):
                    with st.spinner("Asking the model..."):
                        try:
                            narration = llm.narrate_benchmark(report, user_question=user_q or None)
                            st.write(narration)
                        except KeyError:
                            st.error("Set ANTHROPIC_API_KEY in Streamlit secrets to enable narration.")
                        except Exception as e:
                            st.error(f"Narration failed: {e}")


# ---------------------------------------------------------------------------
# Mode 2 — Benchmark synthetic / user-supplied numbers
# ---------------------------------------------------------------------------
elif mode == "Benchmark my numbers":
    st.markdown("Enter your ACO's numbers and choose a peer cohort. Nothing is "
                "stored. The computation runs entirely on the server; only the "
                "narrative call hits the LLM API (and only with the cohort "
                "summary, never your inputs alone).")

    cohort_options = engine.list_cohort_options()
    py = st.selectbox("Performance Year (cohort to compare against)", [2024, 2023], index=0, key="syn_py")

    c1, c2, c3 = st.columns(3)
    with c1:
        track = st.selectbox("Track",       ["(any)"] + cohort_options.get("track", []))
    with c2:
        rev_cat = st.selectbox("Revenue category", ["(any)"] + cohort_options.get("rev_cat", []))
    with c3:
        size_band = st.selectbox("Size band", ["(any)"] + cohort_options.get("size_band", []))

    cohort_filters = {}
    if track    != "(any)": cohort_filters["track"]    = track
    if rev_cat  != "(any)": cohort_filters["rev_cat"]  = rev_cat
    if size_band!= "(any)": cohort_filters["size_band"]= size_band
    if not cohort_filters: cohort_filters["all"] = "All ACOs"

    st.markdown("##### Your numbers (leave blank if you don't want to compare a metric)")
    c1, c2, c3 = st.columns(3)
    inputs = {}
    metrics = engine.list_metrics()
    for i, m in enumerate(metrics):
        col = [c1, c2, c3][i % 3]
        with col:
            v = st.number_input(
                f"{m['label']} ({m['unit']})",
                value=None, format="%f",
                placeholder="—",
                key=f"syn_{m['key']}",
            )
            if v is not None:
                inputs[m["key"]] = v

    if inputs:
        report = engine.benchmark_synthetic(inputs, cohort_filters=cohort_filters,
                                            performance_year=py)
        st.markdown("### Where you stand")
        cols = st.columns(3)
        for i, key in enumerate(report["metrics"]):
            with cols[i % 3]:
                render_metric_card(report["metrics"][key])
                st.write("")

        st.markdown("### Narrative")
        if st.button("Generate narrative", key="syn_narrate"):
            with st.spinner("Asking the model..."):
                try:
                    text = llm.narrate_benchmark(report)
                    st.write(text)
                except KeyError:
                    st.error("Set ANTHROPIC_API_KEY in Streamlit secrets to enable narration.")
                except Exception as e:
                    st.error(f"Narration failed: {e}")


# ---------------------------------------------------------------------------
# Mode 3 — Free-form Q&A grounded in cohort statistics
# ---------------------------------------------------------------------------
else:
    st.markdown("Ask a question about the MSSP cohort. The model can only see "
                "the pre-computed cohort statistics (count, mean, percentiles "
                "across all metrics × cohort dimensions) — no per-ACO records.")

    py = st.selectbox("Performance Year", [2024, 2023], index=0, key="qa_py")
    question = st.text_area("Question", height=80,
        placeholder="e.g. How much variation is there in savings rates by track? "
                    "Or: do bigger ACOs really do better than smaller ones?")
    if st.button("Ask") and question.strip():
        with st.spinner("Thinking..."):
            ctx = {
                "performance_year": py,
                "cohorts": engine.cohort_stats()[f"PY{py}"]["cohorts"],
                "metric_definitions": engine.cohort_stats()[f"PY{py}"]["metric_definitions"],
            }
            try:
                answer = llm.answer_question(question, ctx)
                st.markdown(answer)
            except KeyError:
                st.error("Set ANTHROPIC_API_KEY in Streamlit secrets.")
            except Exception as e:
                st.error(f"Q&A failed: {e}")


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown(
    '<div class="source">'
    "Source: <a href='https://data.cms.gov/medicare-shared-savings-program/performance-year-financial-and-quality-results' target='_blank'>"
    "CMS Performance Year Financial and Quality Results PUF</a>. "
    "PY2024 results released September 2025; rerun September 25, 2025. "
    "All metrics are computed from public data. This tool does not use any "
    "proprietary, claims-level, or PHI data."
    "</div>",
    unsafe_allow_html=True,
)
