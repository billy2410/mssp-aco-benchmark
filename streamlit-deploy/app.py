"""
ACO Benchmark — internal analytics tool.

Public CMS data only (Shared Savings Program Public-Use Files + ACO
Participants). No PHI, no claims-level data, no proprietary inputs.

Run:
    streamlit run app.py

Environment (or .streamlit/secrets.toml):
    ANTHROPIC_API_KEY   required for narrative generation
    APP_PASSWORD        optional access gate
    ACO_MODEL           optional model override
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

import engine

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"

# Streamlit raises if no secrets.toml exists; env vars still work.
try:
    _secrets = dict(st.secrets)
except Exception:
    _secrets = {}
for _k in ("ANTHROPIC_API_KEY", "APP_PASSWORD", "ACO_MODEL"):
    if _k in _secrets and not os.environ.get(_k):
        os.environ[_k] = _secrets[_k]

st.set_page_config(page_title="ACO Benchmark", page_icon="📊", layout="wide")

st.markdown("""
<style>
  .big-num{font-size:2.1rem;font-weight:800;line-height:1.05}
  .lbl{font-size:.74rem;color:#64748b;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
  .pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.74rem;font-weight:700}
  .p-good{background:#d1fae5;color:#065f46}
  .p-mid{background:#fef3c7;color:#92400e}
  .p-bad{background:#fee2e2;color:#991b1b}
  .p-neut{background:#e5e7eb;color:#374151}
  .muted{color:#64748b;font-size:.83rem}
  .src{color:#94a3b8;font-size:.76rem;border-top:1px solid #e2e8f0;padding-top:10px;margin-top:22px}
  .warnbox{background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:10px 14px;font-size:.86rem;color:#92400e}
  .okbox{background:#ecfdf5;border:1px solid #6ee7b7;border-radius:8px;padding:10px 14px;font-size:.86rem;color:#065f46}
  [data-testid="stTooltipContent"]{max-height:75vh}
</style>
""", unsafe_allow_html=True)

if os.environ.get("APP_PASSWORD") and not st.session_state.get("_ok"):
    st.title("ACO Benchmark")
    with st.form("gate"):
        pw = st.text_input("Access passphrase", type="password")
        if st.form_submit_button("Enter"):
            if pw == os.environ["APP_PASSWORD"]:
                st.session_state["_ok"] = True
                st.rerun()
            else:
                st.error("Incorrect passphrase.")
    st.stop()


# ---------------------------------------------------------------- helpers
def load_prompt(name: str) -> str:
    """Load a prompt and splice in the shared MSSP context."""
    txt = (PROMPTS / name).read_text()
    shared = (PROMPTS / "_shared_context.md")
    return txt.replace("{shared_context}", shared.read_text() if shared.exists() else "")


def claude(prompt: str, max_tokens: int = 1100) -> str:
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("ACO_MODEL", "claude-sonnet-4-6"),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if hasattr(b, "text")).strip()


def fmt(v, unit):
    if v is None:
        return "—"
    if unit == "$":     return f"${v:,.0f}"
    if unit == "%":     return f"{v:.2f}%"
    if unit == "lives": return f"{v:,.0f}"
    if unit == "days":  return f"{v:.1f}"
    if unit == "ratio": return f"{v:.4f}"
    if unit == "pct_ratio": return f"{v * 100:.2f}%"
    if unit == "score": return f"{v:.3f}"
    if unit == "per 1k":return f"{v:,.0f}"
    return f"{v}"


def perf_pill(c: dict, higher_is_better) -> str:
    p = c.get("performance_pct")
    if p is None:
        return '<span class="pill p-neut">no data</span>'
    if higher_is_better is None:
        return f'<span class="pill p-neut">p{p:.0f} of cohort</span>'
    cls = "p-good" if p >= 75 else ("p-bad" if p <= 25 else "p-mid")
    return f'<span class="pill {cls}">{c["rank_label"]} · p{p:.0f}</span>'


COHORT_LABELS = {"track": "Track", "risk_model": "Risk model", "rev_cat": "Revenue category",
                 "size_band": "Size band", "all": "All ACOs", "regional": "Regional",
                 "by3_vintage": "Same BY3 vintage"}
COHORT_CHOICES = ["track", "regional", "rev_cat", "size_band", "all"]

COHORT_HELP = f"""\
Choose which group of ACOs this one is ranked against. Percentiles and medians \
change with the group you pick.

- **Track**: ACOs in the same MSSP track (BASIC A–E or ENHANCED). Tracks set how much \
downside risk an ACO carries and how much of its savings it can keep, so this is the \
closest like-for-like comparison on financial terms.
- **Regional**: ACOs that serve at least one of the same states. This is the best proxy \
for competing in the same local market. ACOs that span several states get large, broad \
peer groups.
- **Revenue category**: high-revenue vs. low-revenue ACOs as CMS classifies them. \
High-revenue ACOs usually include a hospital or health system; low-revenue ACOs are \
usually physician-led.
- **Size band**: ACOs with a similar number of assigned beneficiaries (<5K, 5–10K, \
10–25K, 25–50K, 50–100K, 100K+). Smaller ACOs' results tend to swing more from year \
to year.
- **All ACOs**: every ACO in the performance year, for the broadest national view.

Once an ACO is selected, each option shows its group and how many ACOs are in it. \
Groups with fewer than {engine.MIN_REGIONAL_N} ACOs are marked ⚠️ and should be read \
as directional."""


def cohort_radio(key: str, aco_id: str | None, py: int, tab_note: str) -> str:
    sizes = engine.cohort_sizes(aco_id, py) if aco_id else {}

    def label(c: str) -> str:
        base = COHORT_LABELS.get(c, c)
        if c not in sizes:
            return base
        value, n = sizes[c]
        warn = "⚠️ " if n < engine.MIN_REGIONAL_N else ""
        group = "" if value == base else f": {value}"
        return f"{base}{group} · {warn}{n} ACOs"

    return st.radio("Compare against", COHORT_CHOICES, format_func=label,
                    horizontal=True, key=key, help=f"{COHORT_HELP}\n\n{tab_note}")

# BY3-anchored metrics lead with the vintage-matched cohort; everything else
# follows the user's selection.
def preferred_for(metric: dict, focus: str | None):
    if metric.get("vintage_sensitive"):
        return ("by3_vintage", focus or "track", "regional", "rev_cat", "size_band", "all")
    if focus:
        return (focus, "track", "regional", "rev_cat", "size_band", "all")
    return ("regional", "track", "rev_cat", "size_band", "all")


def flag_pill(state: str) -> str:
    cls = {"Yes": "p-good", "No": "p-bad"}.get(state, "p-neut")
    return f'<span class="pill {cls}">{state}</span>'


def flags_by_key(flags: list[dict]) -> dict:
    return {f["key"]: f for f in (flags or [])}


def metric_card(m: dict, preferred=None, focus=None, extra_badge: str = ""):
    if preferred is None:
        preferred = preferred_for(m, focus)
    st.markdown(f'<div class="lbl">{m["label"]}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="big-num">{fmt(m["value"], m["unit"])}</div>', unsafe_allow_html=True)
    if extra_badge:
        st.markdown(extra_badge, unsafe_allow_html=True)
    # A value-less card explains itself rather than showing a bare dash.
    if m.get("unavailable"):
        st.markdown(f'<span class="muted">{m["unavailable"]}</span>',
                    unsafe_allow_html=True)
        with st.expander("Cohort detail"):
            st.caption(m["description"])
        return
    pick = next((c for c in preferred if c in m["comparisons"]), None)
    if pick:
        c = m["comparisons"][pick]
        small = " ⚠︎ small cohort" if c.get("small_sample") else ""
        st.markdown(
            perf_pill(c, m["higher_is_better"])
            + f'<span class="muted"> vs {COHORT_LABELS.get(pick, pick)}'
              f' ({c["cohort_value"]}, n={c["cohort_n"]}){small}</span>',
            unsafe_allow_html=True)
    with st.expander("Cohort detail"):
        rows = []
        for cn, c in m["comparisons"].items():
            rows.append({
                "Cohort": f'{COHORT_LABELS.get(cn, cn)}: {c["cohort_value"]}',
                "n": c["cohort_n"],
                "This ACO": fmt(m["value"], m["unit"]),
                "p25": fmt(c.get("cohort_p25"), m["unit"]),
                "Median": fmt(c.get("cohort_p50"), m["unit"]),
                "p75": fmt(c.get("cohort_p75"), m["unit"]),
                "Performance": f'p{c["performance_pct"]:.0f}' if c.get("performance_pct") is not None else "—",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        st.caption(m["description"])


# ---------------------------------------------------------------- header
st.title("ACO Benchmark")
_meta = engine.meta()
vint = _meta.get("data_vintage", "")
build = _meta.get("build_id", "unknown")
st.caption(f"Medicare Shared Savings Program performance and quality benchmarking · {vint} · "
           "public CMS data only")
# Deployment tell-tale: if this timestamp is older than your last data build,
# the running instance is serving stale data files.
st.caption(f"Data build: {build}")

tabs = st.tabs(["🔎 ACO Lookup", "🩺 Quality Performance", "💵 Medical Expense Performance",
                "📐 Benchmark My Numbers", "💬 Ask the Data", "📖 Methodology"])

FIN_ORDER = ["savings_rate", "risk_ratio_by3_py", "expense_trend_by3_py", "final_adj",
             "n_beneficiaries", "quality_score", "per_capita_exp", "benchmark_per_capita",
             "share_rate", "admits_per_1000", "ed_visits_per_1000", "pc_services_per_1000",
             "readmits_proxy", "snf_admits_per_1000", "snf_los", "hcc_risk_py",
             "reg_adj", "prior_sav_adj", "pct_dual", "pct_lti"]


def aco_picker(key: str):
    c1, c2 = st.columns([3, 1])
    with c1:
        q = st.text_input("ACO name", placeholder="e.g. Aledade Delaware, Privia, Hackensack",
                          key=f"q_{key}")
    with c2:
        py = st.selectbox("Performance year", [2024, 2023], key=f"py_{key}")
    if not q:
        return None, py
    hits = engine.find_acos(q, performance_year=py, limit=12)
    if not hits:
        st.warning("No ACO matches that name.")
        return None, py
    opts = {f'{h["aco_name"]} · {h["aco_id"]} · {int(h["n_beneficiaries"] or 0):,} lives': h["aco_id"]
            for h in hits}
    label = st.selectbox(f"{len(hits)} matches", list(opts), key=f"sel_{key}")
    return opts[label], py


# ================================================================ TAB 1
with tabs[0]:
    aco_id, py = aco_picker("lookup")
    focus = cohort_radio(
        "lookup_cohort", aco_id, py,
        "This sets which group the cards and narrative lead with. Every group is still "
        "shown under 'Cohort detail'. Risk score ratio and expense trend are also compared "
        "against ACOs whose benchmark was set in the same year, since those ratios depend "
        "on when the benchmark was set.")
    if aco_id:
        rep = engine.benchmark_aco(aco_id, performance_year=py)
        a = rep["aco"]
        fl = flags_by_key(a.get("quality_flags"))
        st.markdown(f"### {a['aco_name']}")
        bits = [f"**Track:** {a['track']}", f"**Revenue:** {a['rev_cat']}",
                f"**Size:** {a['size_band']}", f"**Risk:** {a['risk_model']}"]
        if a["n_beneficiaries"]:
            bits.append(f"**Beneficiaries:** {int(a['n_beneficiaries']):,}")
        if a.get("by3_vintage"):
            bits.append(f"**{a['by3_vintage']}** ({py - int(a['by3_year'])}-yr window)")
        if a["service_area"]:
            bits.append(f"**Service area:** {', '.join(a['service_area'])}")
        if a["regional_peer_n"]:
            bits.append(f"**Regional peers:** {a['regional_peer_n']}")
        st.markdown(" · ".join(bits))
        if a.get("final_adj_type"):
            st.caption(f"CMS applied: {a['final_adj_type']}")

        sel = rep["metrics"].get(focus)  # noqa: F841 (focus is a cohort key, not a metric)
        st.markdown("#### Headline actuarial KPIs")
        cols = st.columns(4)
        for i, k in enumerate(["savings_rate", "risk_ratio_by3_py",
                               "expense_trend_by3_py", "final_adj"]):
            with cols[i]:
                metric_card(rep["metrics"][k], focus=focus)

        st.markdown("#### All metrics")
        rest = [k for k in FIN_ORDER if k in rep["metrics"]][4:]
        cols = st.columns(3)
        for i, k in enumerate(rest):
            with cols[i % 3]:
                # Quality score carries its gate flag — the score alone misleads.
                badge = ""
                if k == "quality_score" and fl.get("Met_QPS"):
                    q = fl["Met_QPS"]
                    badge = (f'<span class="muted">Met Quality Performance Standard </span>'
                             f'{flag_pill(q["state"])}')
                metric_card(rep["metrics"][k], focus=focus, extra_badge=badge)
                st.write("")

        with st.expander("Quality determination flags — what drove the sharing rate"):
            st.dataframe(pd.DataFrame([{
                "Flag": f["label"], "Value": f["state"],
                "Confidence": f["confidence"], "Meaning": f["description"],
            } for f in a.get("quality_flags", [])]), hide_index=True, width='stretch')

        st.markdown("#### Narrative")
        uq = st.text_input("Optional — focus the narrative on a question",
                           placeholder="e.g. Is our risk score growth defensible?")
        if st.button("Generate narrative", type="primary"):
            if not os.environ.get("ANTHROPIC_API_KEY"):
                st.error("Set ANTHROPIC_API_KEY to enable narrative generation.")
            else:
                focused = engine.focus_report(rep, focus)
                slim = {"aco": focused["aco"], "performance_year": focused["performance_year"],
                        "focus_cohort": focus,
                        "metrics": {k: {kk: vv for kk, vv in m.items() if kk != "source_col"}
                                    for k, m in focused["metrics"].items()}}
                prompt = load_prompt("narrative.md").format(
                    aco_name=a["aco_name"],
                    focus_note=(f"The reader selected **{COHORT_LABELS.get(focus, focus)}** as the "
                                f"comparison cohort. Anchor the analysis there. The two BY3-anchored "
                                f"ratios also carry their same-vintage cohort — lead with that for "
                                f"those two metrics."),
                    payload=json.dumps(slim, default=str)[:60000],
                    user_question=(f"The reader specifically asks: {uq}" if uq else ""))
                with st.spinner("Generating…"):
                    try:
                        st.write(claude(prompt))
                    except Exception as e:
                        st.error(f"Narrative failed: {e}")

# ================================================================ TAB 2
with tabs[1]:
    st.markdown("### Quality performance")
    st.caption("Every reported quality measure ranked against a peer cohort, "
               "direction-adjusted so a high percentile always means good performance.")
    aco_id_q, py_q = aco_picker("quality")
    cohort_choice = cohort_radio(
        "qcohort", aco_id_q, py_q,
        "Every quality measure, gap, and strength on this tab is ranked against the group "
        "you pick.")
    if aco_id_q:
        qp = engine.quality_profile(aco_id_q, cohort=cohort_choice, performance_year=py_q)
        if not qp:
            st.warning("No quality data for this ACO.")
        else:
            c = qp["cohort"]
            qfl = flags_by_key(qp["aco"].get("quality_flags"))
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Composite quality score", f'{qp["aco"]["quality_score"]:.2f}'
                      if qp["aco"]["quality_score"] else "—")
            m2.metric("Measures reported", len(qp["measures"]))
            m3.metric("Below cohort median", len([x for x in qp["measures"]
                                                  if x["performance_pct"] < 50]))
            m4.metric("Peer cohort size", c["n_peers"])

            # The gate matters more to the economics than the score does.
            g1, g2 = st.columns(2)
            for col, key, label in ((g1, "Met_QPS", "Met Quality Performance Standard"),
                                    (g2, "Met_AltQPS", "Met Alternative Standard")):
                f = qfl.get(key)
                if f:
                    with col:
                        st.markdown(f'<div class="lbl">{label}</div>{flag_pill(f["state"])}',
                                    unsafe_allow_html=True)
            if qfl.get("Met_QPS", {}).get("value") == 0:
                st.markdown('<div class="warnbox">This ACO missed the primary quality gate. '
                            'Its sharing rate is reduced regardless of where individual measures '
                            'rank — PY2024 median final share rate was 30.6% for ACOs in this '
                            'position versus 50.0% for those that cleared it.</div>',
                            unsafe_allow_html=True)
            if c["small_sample"]:
                st.markdown('<div class="warnbox">Small peer cohort — treat these '
                            'comparisons as directional.</div>', unsafe_allow_html=True)

            g1, g2 = st.columns(2)
            with g1:
                st.markdown("##### Biggest gaps")
                for mm in qp["biggest_gaps"]:
                    inv = " *(lower is better)*" if mm["higher_is_better"] is False else ""
                    st.markdown(
                        f'**{mm["label"]}**{inv}  \n'
                        f'<span class="muted">This ACO {mm["value"]:.2f} · cohort median '
                        f'{mm["cohort_p50"]:.2f} · </span>'
                        + perf_pill(mm, mm["higher_is_better"]), unsafe_allow_html=True)
            with g2:
                st.markdown("##### Strongest measures")
                for mm in qp["top_strengths"]:
                    inv = " *(lower is better)*" if mm["higher_is_better"] is False else ""
                    st.markdown(
                        f'**{mm["label"]}**{inv}  \n'
                        f'<span class="muted">This ACO {mm["value"]:.2f} · cohort median '
                        f'{mm["cohort_p50"]:.2f} · </span>'
                        + perf_pill(mm, mm["higher_is_better"]), unsafe_allow_html=True)

            st.markdown("##### Performance by domain")
            dd = pd.DataFrame(qp["domain_summary"])
            dd.columns = ["Domain", "Avg performance percentile", "Measures"]
            st.dataframe(dd, hide_index=True, width='stretch')

            st.markdown("##### All measures")
            rows = [{
                "Measure": mm["label"],
                "Domain": mm["domain"],
                "Direction": "lower is better" if mm["higher_is_better"] is False else "higher is better",
                "This ACO": round(mm["value"], 2),
                "Cohort p25": round(mm["cohort_p25"], 2),
                "Median": round(mm["cohort_p50"], 2),
                "Cohort p75": round(mm["cohort_p75"], 2),
                "Performance": round(mm["performance_pct"], 1),
                "n": mm["cohort_n"],
            } for mm in qp["measures"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
            st.download_button("Download quality detail (CSV)",
                               pd.DataFrame(rows).to_csv(index=False),
                               file_name=f"quality_{aco_id_q}_{py_q}.csv", mime="text/csv")

            st.markdown("##### Quality coaching narrative")
            quq = st.text_input("Optional — focus the narrative on a question",
                                placeholder="e.g. What are the two cheapest measures to move "
                                            "us above the quality gate?", key="qual_q")
            if st.button("Generate quality narrative", type="primary"):
                if not os.environ.get("ANTHROPIC_API_KEY"):
                    st.error("Set ANTHROPIC_API_KEY to enable narrative generation.")
                else:
                    prompt = load_prompt("quality_coach.md").format(
                        aco_name=qp["aco"]["aco_name"],
                        cohort_label=f'{COHORT_LABELS.get(c["name"], c["name"])}: {c["value"]}',
                        cohort_n=c["n_peers"],
                        payload=json.dumps({"composite_quality_score": qp["aco"]["quality_score"],
                                            "quality_flags": qp["aco"].get("quality_flags"),
                                            "domain_summary": qp["domain_summary"],
                                            "measures": qp["measures"]}, default=str)[:60000],
                        user_question=(f"The reader specifically asks: {quq}" if quq else ""))
                    with st.spinner("Generating…"):
                        try:
                            st.markdown(claude(prompt))
                        except Exception as e:
                            st.error(f"Narrative failed: {e}")

# ================================================================ TAB 3
with tabs[2]:
    st.markdown("### Medical expense performance")
    st.caption("Per-capita cost and utilization ranked against a peer cohort. "
               "Metrics marked two-sided are ranked but not scored good or bad — "
               "raising ambulatory contact is often how acute utilization comes down.")
    aco_id_e, py_e = aco_picker("expense")
    cohort_e = cohort_radio(
        "ecohort", aco_id_e, py_e,
        "Every cost and utilization metric on this tab is ranked against the group you pick.")
    if aco_id_e:
        ep = engine.expense_profile(aco_id_e, cohort=cohort_e, performance_year=py_e)
        if not ep:
            st.warning("No expense data for this ACO.")
        else:
            c = ep["cohort"]
            a = ep["aco"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total per-capita expenditure",
                      fmt(a["per_capita_exp"], "$"))
            m2.metric("Expense trend (BY3 → PY)",
                      fmt(a["expense_trend_by3_py"], "pct_ratio"))
            m3.metric("Risk score ratio (BY3 → PY)",
                      fmt(a["risk_ratio_by3_py"], "pct_ratio"))
            m4.metric("Peer cohort size", c["n_peers"])

            # Cost trend read in isolation is misleading: an ACO whose documented
            # risk grew faster than its spend is arguably improving efficiency.
            tr, rr = a["expense_trend_by3_py"], a["risk_ratio_by3_py"]
            if tr and rr:
                if tr > rr:
                    st.markdown('<div class="warnbox">Expense trend is outpacing risk-score '
                                f'growth ({tr * 100:.2f}% vs {rr * 100:.2f}%). Cost is rising '
                                'faster than documented acuity explains.</div>',
                                unsafe_allow_html=True)
                else:
                    st.markdown('<div class="okbox">Expense trend is running at or below '
                                f'risk-score growth ({tr * 100:.2f}% vs {rr * 100:.2f}%), so '
                                'per-capita cost growth is at least matched by documented '
                                'acuity.</div>', unsafe_allow_html=True)
            if c["small_sample"]:
                st.markdown('<div class="warnbox">Small peer cohort — treat these '
                            'comparisons as directional.</div>', unsafe_allow_html=True)

            g1, g2 = st.columns(2)
            with g1:
                st.markdown("##### Biggest gaps")
                st.caption("Directional metrics only.")
                for mm in ep["biggest_gaps"]:
                    st.markdown(
                        f'**{mm["label"]}**  \n'
                        f'<span class="muted">This ACO {fmt(mm["value"], mm["unit"])} · cohort '
                        f'median {fmt(mm["cohort_p50"], mm["unit"])} · </span>'
                        + perf_pill(mm, mm["higher_is_better"]), unsafe_allow_html=True)
            with g2:
                st.markdown("##### Strongest areas")
                st.caption("Directional metrics only.")
                for mm in ep["top_strengths"]:
                    st.markdown(
                        f'**{mm["label"]}**  \n'
                        f'<span class="muted">This ACO {fmt(mm["value"], mm["unit"])} · cohort '
                        f'median {fmt(mm["cohort_p50"], mm["unit"])} · </span>'
                        + perf_pill(mm, mm["higher_is_better"]), unsafe_allow_html=True)

            def expense_block(title: str, items: list[dict], slug: str):
                st.markdown(f"##### {title}")
                headline = [m for m in items if m.get("important")]
                rest = [m for m in items if not m.get("important")]
                for i in range(0, len(headline), 4):
                    for col, mm in zip(st.columns(4), headline[i:i + 4]):
                        with col:
                            badge = ('<span class="pill p-neut">two-sided</span>'
                                     if mm["two_sided"] else "")
                            st.markdown(f'<div class="lbl">{mm["label"]}</div>',
                                        unsafe_allow_html=True)
                            st.markdown(f'<div class="big-num">{fmt(mm["value"], mm["unit"])}</div>',
                                        unsafe_allow_html=True)
                            st.markdown(perf_pill(mm, mm["higher_is_better"]) + " " + badge,
                                        unsafe_allow_html=True)
                            st.markdown(f'<span class="muted">cohort median '
                                        f'{fmt(mm["cohort_p50"], mm["unit"])}</span>',
                                        unsafe_allow_html=True)
                if rest:
                    with st.expander(f"All {title.lower()} metrics ({len(items)})"):
                        st.dataframe(pd.DataFrame([{
                            "Metric": mm["label"],
                            "Direction": ("two-sided" if mm["two_sided"]
                                          else "lower is better" if mm["higher_is_better"] is False
                                          else "higher is better"),
                            "This ACO": fmt(mm["value"], mm["unit"]),
                            "Cohort p25": fmt(mm.get("cohort_p25"), mm["unit"]),
                            "Median": fmt(mm.get("cohort_p50"), mm["unit"]),
                            "Cohort p75": fmt(mm.get("cohort_p75"), mm["unit"]),
                            "Performance": (f'p{mm["performance_pct"]:.0f}'
                                            if mm["performance_pct"] is not None else "—"),
                            "n": mm["cohort_n"],
                        } for mm in items]), hide_index=True, width='stretch')

            expense_block("Cost", ep["cost"], "cost")
            expense_block("Utilization", ep["utilization"], "util")

            all_rows = pd.DataFrame([{
                "Family": mm["family"], "Metric": mm["label"], "Unit": mm["unit"],
                "This ACO": mm["value"], "Cohort p25": mm.get("cohort_p25"),
                "Median": mm.get("cohort_p50"), "Cohort p75": mm.get("cohort_p75"),
                "Performance percentile": mm["performance_pct"], "n": mm["cohort_n"],
            } for mm in ep["cost"] + ep["utilization"]])
            st.download_button("Download expense detail (CSV)", all_rows.to_csv(index=False),
                               file_name=f"expense_{aco_id_e}_{py_e}.csv", mime="text/csv")

            st.markdown("##### Medical expense narrative")
            expq = st.text_input("Optional — focus the narrative on a question",
                                 placeholder="e.g. Where is our post-acute spend leaking, and "
                                             "is it a SNF admission or a length-of-stay problem?",
                                 key="exp_q")
            if st.button("Generate expense narrative", type="primary"):
                if not os.environ.get("ANTHROPIC_API_KEY"):
                    st.error("Set ANTHROPIC_API_KEY to enable narrative generation.")
                else:
                    prompt = load_prompt("medical_expense.md").format(
                        aco_name=a["aco_name"],
                        cohort_label=f'{COHORT_LABELS.get(c["name"], c["name"])}: {c["value"]}',
                        cohort_n=c["n_peers"],
                        payload=json.dumps({
                            "total_per_capita_expenditure": a["per_capita_exp"],
                            "expense_trend_by3_py": a["expense_trend_by3_py"],
                            "risk_ratio_by3_py": a["risk_ratio_by3_py"],
                            "n_beneficiaries": a["n_beneficiaries"],
                            "cost": ep["cost"],
                            "utilization": ep["utilization"],
                        }, default=str)[:60000],
                        user_question=(f"The reader specifically asks: {expq}" if expq else ""))
                    with st.spinner("Generating…"):
                        try:
                            st.markdown(claude(prompt))
                        except Exception as e:
                            st.error(f"Narrative failed: {e}")

# ================================================================ TAB 4
with tabs[3]:
    st.markdown("### Benchmark my numbers")
    st.caption("Enter values and pick a peer cohort. Nothing is stored; the computation "
               "runs locally.")
    opts = engine.list_cohort_options()
    c1, c2, c3 = st.columns(3)
    track = c1.selectbox("Track", ["(any)"] + opts.get("track", []))
    rev = c2.selectbox("Revenue category", ["(any)"] + opts.get("rev_cat", []))
    size = c3.selectbox("Size band", ["(any)"] + opts.get("size_band", []))
    filters = {}
    if track != "(any)": filters["track"] = track
    if rev != "(any)":   filters["rev_cat"] = rev
    if size != "(any)":  filters["size_band"] = size
    if not filters:      filters["all"] = "All ACOs"

    st.markdown("##### Your numbers")
    inputs = {}
    mets = engine.list_metrics()
    cols = st.columns(3)
    for i, m in enumerate(mets):
        with cols[i % 3]:
            v = st.number_input(f'{m["label"]} ({m["unit"]})', value=None,
                                format="%f", placeholder="—", key=f"syn_{m['key']}")
            if v is not None:
                inputs[m["key"]] = v
    if inputs:
        rep = engine.benchmark_synthetic(inputs, cohort_filters=filters)
        st.markdown("##### Where you stand")
        cols = st.columns(3)
        for i, k in enumerate(rep["metrics"]):
            with cols[i % 3]:
                metric_card(rep["metrics"][k], preferred=tuple(filters))
                st.write("")

# ================================================================ TAB 5
with tabs[4]:
    st.markdown("### Ask the data")
    st.caption("Free-form questions grounded in the cohort statistics. The model sees "
               "cohort distributions and measure definitions — not per-ACO records.")
    q = st.text_area("Question", height=90,
                     placeholder="e.g. Which quality domains separate top-quartile "
                                 "ENHANCED ACOs from the rest?")
    py_a = st.selectbox("Performance year", [2024, 2023], key="qa_py")
    if st.button("Ask", type="primary") and q.strip():
        if not os.environ.get("ANTHROPIC_API_KEY"):
            st.error("Set ANTHROPIC_API_KEY to enable Q&A.")
        else:
            ctx = {"performance_year": py_a,
                   "data_vintage": engine.meta().get("data_vintage"),
                   "cohorts": engine.cohort_stats()[f"PY{py_a}"]["cohorts"],
                   "metric_definitions": engine.meta()["metric_definitions"],
                   "quality_measures": engine.meta()["quality_measures"]}
            prompt = load_prompt("qa.md").format(
                payload=json.dumps(ctx, default=str)[:120000], question=q.strip())
            with st.spinner("Thinking…"):
                try:
                    st.markdown(claude(prompt))
                except Exception as e:
                    st.error(f"Q&A failed: {e}")

# ================================================================ TAB 6
with tabs[5]:
    mm = engine.meta()
    st.markdown("### Methodology")
    st.markdown(f"**Data vintage:** {mm.get('data_vintage')}")
    st.markdown("#### Derived KPIs")
    for k in ("risk_ratio_by3_py", "expense_trend_by3_py", "by3_vintage", "quality_cliff",
              "final_adj", "quality_coalescing", "inverse_measures", "regional_peers"):
        if k in mm["methodology"]:
            st.markdown(f"**`{k}`** — {mm['methodology'][k]}")
    st.markdown("#### Quality determination flags")
    st.dataframe(pd.DataFrame([{
        "Flag": f["label"], "Confidence": f["confidence"], "Meaning": f["description"],
    } for f in mm.get("quality_flags", [])]), hide_index=True, width='stretch')
    st.caption('Flags marked "verify" are readings inferred from the published data '
               "relationships; confirm against the CMS data dictionary before asserting "
               "them to a client.")

    st.markdown("#### Quality measures")
    st.dataframe(pd.DataFrame([{
        "Measure": q["label"], "Domain": q["domain"],
        "Direction": "lower is better" if q["higher_is_better"] is False else "higher is better",
        "PUF fields": ", ".join(q["variants"]),
    } for q in mm["quality_measures"]]), hide_index=True, width='stretch')
    st.markdown("#### Limitations")
    st.markdown("""
- FFS-only. Comparisons exclude Medicare Advantage populations.
- Regional peers are defined by shared service-area **state**, the finest geography
  the ACO Participants file publishes. A multi-state ACO inherits a broad peer set.
- Quality measures reported through different mechanisms (Web Interface, eCQM,
  MIPS CQM, Medicare CQM) are coalesced; scores are not perfectly comparable across
  mechanisms.
- Percentile ranks are exact within the cohort, but cohorts under ~25 ACOs carry
  wide uncertainty.
- Public-use file only: ACO-level aggregates, no provider or claims detail.
""")
    st.markdown("#### Sources")
    for k, v in mm["sources"].items():
        st.markdown(f"- {k}: {v}")

st.markdown(f'<div class="src">Sources: CMS Shared Savings Program Public-Use Files and '
            f'ACO Participants file. {vint}. Public data only — no PHI, no claims-level '
            f'data, no proprietary inputs.</div>', unsafe_allow_html=True)
