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

import html
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
  table.bm{width:100%;max-width:780px;border-collapse:separate;border-spacing:0 3px;font-size:.9rem;border:none}
  table.bm th,table.bm td{border:none;padding:7px 12px}
  table.bm th{font-size:.78rem;font-weight:600;color:#64748b;text-align:right;vertical-align:bottom}
  table.bm th:first-child,table.bm td:first-child{text-align:left}
  table.bm td.val{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
  table.bm td.pct{width:270px;cursor:help}
  table.bm.compact td.pct{width:210px}
  table.bm .tag{font-size:.68rem;font-weight:600;border:1px solid #94a3b8;border-radius:4px;
    padding:0 4px;margin-left:6px;color:#64748b}
  table.bm th.txt,table.bm td.txt{text-align:left}
  table.bm td.ind{padding-left:34px}
  table.bm.det{max-width:720px} table.bm.det td.val{width:150px}
  table.bm.cms{max-width:1100px} table.bm.cms td.txt{white-space:nowrap;color:#64748b;font-size:.82rem}
  .takeaway{border-left:4px solid #64748b;background:rgba(128,128,128,.08);border-radius:6px;
    padding:10px 14px;margin:2px 0 16px;line-height:1.5}
  .takeaway.t-good{border-color:#16a34a} .takeaway.t-mid{border-color:#d97706}
  .takeaway.t-bad{border-color:#dc2626}
  .legend span{display:inline-block;width:11px;height:11px;border-radius:50%;margin:0 4px -1px 10px}
  .kpis{display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 18px}
  .kpi{flex:1 1 135px;min-width:0;border:1px solid rgba(128,128,128,.3);border-radius:10px;padding:12px 14px;
    background:rgba(128,128,128,.05);cursor:help}
  .kpi .k-lbl{font-size:.78rem;font-weight:600;color:#64748b;line-height:1.35;min-height:2.7em}
  .kpi .k-val{font-size:1.45rem;font-weight:800;line-height:1.2;margin:2px 0 4px}
  .kpi .k-track{margin:12px 6px}
  .kpi .k-sub{font-size:.72rem;color:#64748b;margin-top:6px}
  .kpi.flag-yes{background:rgba(22,163,74,.13);border-color:rgba(22,163,74,.6)}
  .kpi.flag-no{background:rgba(220,38,38,.13);border-color:rgba(220,38,38,.6)}
  .kpi.flag-na{background:rgba(100,116,139,.13);border-color:rgba(100,116,139,.55)}
  .kpi.flag-yes .k-val{color:#16a34a} .kpi.flag-no .k-val{color:#dc2626}
  .kpi.flag-na .k-val{color:#64748b}
  .k-track{position:relative;height:4px;border-radius:2px;background:rgba(128,128,128,.35)}
  .k-mid{position:absolute;left:50%;top:-4px;width:2px;height:12px;background:rgba(128,128,128,.7)}
  .k-dot{position:absolute;top:50%;width:14px;height:14px;border-radius:50%;
    transform:translate(-50%,-50%);box-shadow:0 0 0 2px rgba(255,255,255,.85)}
  .k-word{display:inline-block;padding:1px 8px;border-radius:999px;color:#fff;
    font-size:.78rem;font-weight:700;white-space:nowrap}
  .rate{display:flex;align-items:center;gap:12px}
  .rate .k-track{flex:0 0 120px;margin:0 7px}
  table.bm.compact .rate .k-track{flex-basis:80px}
  .k-dot.t-good,.k-word.t-good{background:#15803d} .k-dot.t-mid,.k-word.t-mid{background:#d97706}
  .k-dot.t-bad,.k-word.t-bad{background:#dc2626} .k-dot.t-neut,.k-word.t-neut{background:#64748b}
  @media (max-width:640px){table.bm td.pct,table.bm.compact td.pct{width:auto}
    .rate .k-track{display:none} table.bm th,table.bm td{padding:6px 6px}}
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


def no_math(text: str) -> str:
    # Streamlit markdown renders text between two $ signs as LaTeX, which eats
    # the dollar signs of amounts like "$445 ... $362".
    return text.replace("$", "\\$")


def claude(prompt: str, max_tokens: int = 1100) -> str:
    """Narrative text from Claude, escaped for display with st.markdown."""
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("ACO_MODEL", "claude-sonnet-4-6"),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return no_math("".join(b.text for b in msg.content if hasattr(b, "text")).strip())


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
    if unit == "measure": return f"{v:.2f}"
    return f"{v}"


# Cutoffs apply to the percentile as displayed (rounded), so "p90" always reads Excellent.
def perf_tier(p) -> str:
    """good (p60+) / mid (p40–59) / bad (below p40) by direction-adjusted percentile."""
    if p is None:
        return "neut"
    p = round(p)
    return "good" if p >= 60 else ("bad" if p < 40 else "mid")


def perf_word(p: float) -> str:
    """Plain-language rating on the same cutoffs as perf_tier's colors."""
    p = round(p)
    words = ("Poor", "Below Average", "Average", "Above Average", "Excellent")
    return words[0 if p < 10 else 1 if p < 40 else 2 if p < 60 else 3 if p < 90 else 4]


def fmt_money_short(v) -> str:
    if v is None:
        return "—"
    sign, a = ("-" if v < 0 else ""), abs(v)
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:.0f}K"
    return f"{sign}${a:,.0f}"


def perf_pill(c: dict) -> str:
    p = c.get("performance_pct")
    if p is None:
        return '<span class="pill p-neut">no data</span>'
    return f'<span class="pill p-{perf_tier(p)}">{perf_word(p)} · p{p:.0f}</span>'


COHORT_LABELS = {"track": "Track", "risk_model": "Risk model", "rev_cat": "Revenue category",
                 "size_band": "Size band", "all": "All ACOs", "regional": "Regional"}
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

Once an ACO is selected, each option shows its group, how many ACOs are in it, and how \
many of those share this ACO's BY3 vintage (the year its benchmark was set). Counts \
under {engine.MIN_REGIONAL_N} are marked ⚠️ and should be read as directional."""

VINTAGE_HELP = """\
**Off:** every metric is ranked against all ACOs in the selected group.

**On:** every metric, on every tab, is ranked only against ACOs in the selected group whose \
benchmark year 3 (BY3) is the same as this ACO's.

Expense trend and risk score ratio always use same-vintage ACOs either way, because \
those two ratios depend on when the benchmark was set."""

BY3_NOTE = ("Expense trend and risk score ratio are always compared only against ACOs in "
            "the group that share this ACO's BY3 vintage, since those ratios depend on when "
            "the benchmark was set.")


def sticky(key: str, options: list, default=None) -> dict:
    """Widget kwargs that re-apply its last valid choice every run."""
    # Option labels that embed live counts otherwise come back blank or stale once the
    # labels change, e.g. after picking a different ACO or year.
    keep = f"_{key}"
    if default is not None:
        st.session_state.setdefault(keep, default)
    if st.session_state.get(keep) in options:
        st.session_state[key] = st.session_state[keep]

    def remember():
        if st.session_state[key] in options:
            st.session_state[keep] = st.session_state[key]
    return {"key": key, "on_change": remember}


def cohort_radio(aco_id: str | None, py: int) -> tuple[str, bool]:
    """The 'Compare against' selector plus the same-vintage switch."""
    sizes = engine.cohort_sizes(aco_id, py) if aco_id else {}

    def count(n: int) -> str:
        return f"{'⚠️ ' if n < engine.MIN_REGIONAL_N else ''}{n}"

    def label(c: str) -> str:
        base = COHORT_LABELS.get(c, c)
        if c not in sizes:
            return base
        value, n, n_vint = sizes[c]
        group = "" if value == base else f": {value}"
        return f"{base}{group} · {count(n)} ACOs ({count(n_vint)} same vintage)"

    cohort = st.radio("Compare against", COHORT_CHOICES, format_func=label, horizontal=True,
                      help=f"{COHORT_HELP}\n\n{BY3_NOTE}",
                      **sticky("cohort", COHORT_CHOICES, default=COHORT_CHOICES[0]))
    row = engine.get_aco(aco_id, py) if aco_id else None
    vintage = f" ({row['by3_vintage']})" if row and row.get("by3_vintage") else ""
    vintage_only = st.toggle(f"Same BY3 vintage only{vintage}", key="vintage",
                             help=VINTAGE_HELP)
    return cohort, vintage_only


def cohort_text(c: dict) -> str:
    """'Track: ENHANCED', plus the vintage when the group was narrowed to it."""
    text = f'{COHORT_LABELS.get(c["name"], c["name"])}: {c["value"]}'
    return f"{text}, same BY3 vintage only" if c.get("same_vintage") else text


def preferred_for(metric: dict, focus: str | None):
    if focus:
        return (focus, "track", "regional", "rev_cat", "size_band", "all")
    return ("regional", "track", "rev_cat", "size_band", "all")


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
        vint = ", same BY3 vintage" if c.get("same_vintage") else ""
        st.markdown(
            perf_pill(c)
            + f'<span class="muted"> vs {COHORT_LABELS.get(pick, pick)}'
              f' ({c["cohort_value"]}{vint}, n={c["cohort_n"]}){small}</span>',
            unsafe_allow_html=True)
    with st.expander("Cohort detail"):
        rows = []
        for cn, c in m["comparisons"].items():
            rows.append({
                "Cohort": cohort_text({"name": cn, **c, "value": c["cohort_value"]}),
                "n": c["cohort_n"],
                "This ACO": fmt(m["value"], m["unit"]),
                "p25": fmt(c.get("cohort_p25"), m["unit"]),
                "Median": fmt(c.get("cohort_p50"), m["unit"]),
                "p75": fmt(c.get("cohort_p75"), m["unit"]),
                "Performance": f'p{c["performance_pct"]:.0f}' if c.get("performance_pct") is not None else "—",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        st.caption(m["description"])


def rank_tip(r: dict, unit, group: str) -> str:
    """Hover text: comparison group, median and middle 50%, as an escaped title attribute."""
    tip = [f'{group}{", same BY3 vintage" if r.get("same_vintage") else ""} (n={r["cohort_n"]})',
           f'Median: {fmt(r.get("cohort_p50"), unit)}',
           f'Middle 50%: {fmt(r.get("cohort_p25"), unit)} – {fmt(r.get("cohort_p75"), unit)}']
    if r["cohort_n"] < engine.MIN_REGIONAL_N:
        tip.append(f"Fewer than {engine.MIN_REGIONAL_N} ACOs: directional only")
    return "&#10;".join(html.escape(t) for t in tip)


def rating(p: float, small: bool = False) -> tuple[str, str]:
    """(percentile track with a dot, rating pill like 'Above Average · p82')."""
    tier = perf_tier(p)
    track = (f'<div class="k-track"><span class="k-mid"></span><span class="k-dot t-{tier}" '
             f'style="left:{min(max(p, 0), 100):.0f}%"></span></div>')
    word = (f'<span class="k-word t-{tier}">{perf_word(p)} · p{p:.0f}'
            f'{" !" if small else ""}</span>')
    return track, word


def pct_cell(r: dict | None, unit, group: str, note: str | None = None) -> str:
    """Percentile track and rating; hovering shows the comparison group's median and middle 50%."""
    if note or not r or r.get("performance_pct") is None:
        return (f'<td class="pct" title="{html.escape(note or "No comparison available")}">'
                f'<span class="k-word t-neut">No data</span></td>')
    track, word = rating(r["performance_pct"], r["cohort_n"] < engine.MIN_REGIONAL_N)
    return (f'<td class="pct" title="{rank_tip(r, unit, group)}">'
            f'<div class="rate">{track}{word}</div></td>')


def name_cell(label: str, tags=(), title: str = "") -> str:
    tag_html = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in tags)
    return f'<td title="{html.escape(title)}">{html.escape(label)}{tag_html}</td>'


def rank_table(title: str, subtitle: str, rows: list[str], first_col: str = "Metric",
               value_col: str = "Value", compact: bool = False) -> str:
    """Name | value | one colored percentile column for the selected comparison group."""
    head = (f'<tr><th>{first_col}</th><th>{value_col}</th><th style="text-align:center">'
            f'{html.escape(title)}<br><span style="font-weight:400">{html.escape(subtitle)}</span>'
            f'</th></tr>')
    cls = "bm compact" if compact else "bm"
    # &#36; keeps dollar values out of Streamlit's $...$ math rendering, attributes included.
    return (f'<table class="{cls}"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'
            .replace("$", "&#36;"))


def table_row(name_td: str, value: str, pct_td: str) -> str:
    return f'<tr>{name_td}<td class="val">{value}</td>{pct_td}</tr>'


def cohort_header(c: dict) -> tuple[str, str]:
    """Column title and subtitle for a quality or expense profile's cohort."""
    vint = ", same BY3 vintage" if c.get("same_vintage") else ""
    return COHORT_LABELS.get(c["name"], c["name"]), f'{c["value"]} · {c["n_peers"]} ACOs{vint}'


TABLE_LEGEND = (
    '<div class="muted legend">The dot shows where this ACO sits in the group; the tick is the '
    'median. Percentiles are direction-adjusted, so higher is always better.'
    '<span style="background:#dc2626"></span>Poor (below p10) / Below Average (p10–40)'
    '<span style="background:#d97706"></span>Average (p40–60)'
    '<span style="background:#15803d"></span>Above Average (p60–90) / Excellent (p90+).'
    ' Hover a metric name for how its direction is judged, and a row for the group median and middle 50%; ! marks a group under '
    f'{engine.MIN_REGIONAL_N} ACOs.</div>')


def benchmark_table(rep: dict, keys: list[str], focus: str, sizes: dict,
                    qps_state: str | None = None) -> str:
    """One row per metric, one colored percentile column for the selected cohort."""
    metrics = [(k, rep["metrics"][k]) for k in keys if k in rep["metrics"]]
    pick = focus if any(focus in m["comparisons"] for _, m in metrics) else "all"
    value, n, n_vint = sizes.get(pick, ("All ACOs", None, None))
    base = COHORT_LABELS.get(pick, pick)
    count = (f"{n_vint} ACOs, same BY3 vintage" if rep.get("vintage_only") else f"{n} ACOs")
    rows = []
    for key, m in metrics:
        tags = ["BY3"] if m.get("vintage_sensitive") else []
        if key == "quality_score" and qps_state:
            tags.append(f"QPS met: {qps_state}")
        c = m["comparisons"].get(pick)
        group = f'{base}: {c["cohort_value"]}' if c else base
        rows.append(table_row(name_cell(m["label"], tags, m["description"]),
                              fmt(m["value"], m["unit"]),
                              pct_cell(c, m["unit"], group,
                                       m.get("unavailable"))))
    return rank_table(base, ("" if value == base else f"{value} · ") + count, rows)


def kpi_box(label: str, value: str, r: dict | None, unit, group: str,
            sub: str = "") -> str:
    p = r.get("performance_pct") if r else None
    if p is None:
        rating_html, tip = '<div class="k-sub">No comparison available</div>', ""
    else:
        rating_html = "".join(rating(p, r["cohort_n"] < engine.MIN_REGIONAL_N))
        tip = rank_tip(r, unit, group)
    sub_html = f'<div class="k-sub">{html.escape(sub)}</div>' if sub else ""
    return (f'<div class="kpi" title="{tip}"><div class="k-lbl">{html.escape(label)}</div>'
            f'<div class="k-val">{value}</div>{rating_html}{sub_html}</div>')


def group_label(pick: str, value: str) -> str:
    """'Track: ENHANCED', or the value alone when it already names the group ('Regional peers (CA)')."""
    base = COHORT_LABELS.get(pick, pick)
    return value if value.lower().startswith(base.lower()) else f"{base}: {value}"


def metric_box(rep: dict, key: str, label: str, pick: str) -> str:
    m = rep["metrics"][key]
    c = m["comparisons"].get(pick)
    group = group_label(pick, c["cohort_value"]) if c else COHORT_LABELS.get(pick, pick)
    vint = ", same BY3 vintage" if c and c.get("same_vintage") else ""
    sub = f'vs {group}{vint} · {c["cohort_n"]} ACOs' if c else ""
    return kpi_box(label, fmt(m["value"], m["unit"]), c, m["unit"], group, sub)


# (flag key, box label, plain-language note for Yes / No / N/A)
QUALITY_GATES = [
    ("Met_SSP_quality_reporting_requirements", "Met Quality Reporting Requirements",
     {"Yes": "Reported the quality data the program requires",
      "No": "Incomplete reporting: cannot meet the quality standard",
      "N/A": "Not published in this year's CMS file"}),
    ("Met_QPS", "Met Quality Performance Standard",
     {"Yes": "Qualifies for its track's full sharing rate",
      "No": "Missed: sharing rate is scaled down or forfeited",
      "N/A": "Not published in this year's CMS file"}),
    ("Met_AltQPS", "Met Alternative Quality Standard",
     {"Yes": "Missed the main standard but met this backup: savings shared at a scaled rate",
      "No": "Missed both standards: not eligible to share savings",
      "N/A": "Not needed: the main standard was met"}),
]


def flag_box(label: str, flag: dict | None, notes: dict) -> str:
    """Yes / No / N/A box tinted green, red or gray; hover shows the flag's definition."""
    state = flag["state"] if flag and flag["state"] in ("Yes", "No") else "N/A"
    tip = html.escape(flag["description"]) if flag else ""
    return (f'<div class="kpi flag-{state.replace("/", "").lower()}" title="{tip}">'
            f'<div class="k-lbl">{html.escape(label)}</div><div class="k-val">{state}</div>'
            f'<div class="k-sub">{html.escape(notes[state])}</div></div>')


def quality_boxes(rep: dict, pick: str, flags: dict) -> str:
    boxes = [metric_box(rep, "quality_score", "Composite Quality Score", pick)]
    # Under the EUC (disaster) policy an ACO that couldn't report is assigned the 40th-percentile
    # score and still meets the standard, so "No" on reporting isn't disqualifying for it.
    euc = flags.get("Recvd40p", {}).get("state") == "Yes"
    for key, label, notes in QUALITY_GATES:
        if euc and key == "Met_SSP_quality_reporting_requirements":
            notes = {**notes, "No": "Not reported, but excused under CMS's disaster (EUC) policy"}
        boxes.append(flag_box(label, flags.get(key), notes))
    return f'<div class="kpis">{"".join(boxes)}</div>'.replace("$", "&#36;")


def quality_takeaway(name: str, py: int, flags: dict, score: dict, group: str,
                     domains: list[dict]) -> str:
    """Two-sentence verdict on the quality gates and where the ACO ranks, from the CMS flags."""
    name = html.escape(name)
    state = {k: f.get("state") for k, f in flags.items()}
    euc = state.get("Recvd40p") == "Yes"
    if state.get("Met_QPS") == "Yes":
        tone = "good"
        if euc:
            how = " with its score set at the 40th-percentile floor under CMS's disaster (EUC) policy"
        elif state.get("Met_40pctl") == "Yes":
            how = " by scoring at or above the 40th-percentile MIPS quality threshold"
        elif state.get("Met_Incentive") == "Yes":
            how = (" through the eCQM / MIPS CQM reporting incentive, despite scoring below the "
                   "40th-percentile threshold")
        elif state.get("Met_FirstYear") == "Yes":
            how = " under the first-year ACO policy, despite scoring below the 40th-percentile threshold"
        else:
            how = ""
        first = (f"<b>{name} met CMS's quality performance standard for PY{py}</b>{how}, so it can "
                 f"share savings at its track's full rate.")
    elif state.get("Met_AltQPS") == "Yes":
        tone = "mid"
        first = (f"<b>{name} missed CMS's quality performance standard for PY{py} but met the "
                 f"alternative standard</b>, so it shares savings at a reduced rate scaled to its "
                 f"quality score.")
    elif state.get("Met_QPS") == "No":
        tone = "bad"
        first = (f"<b>{name} missed both CMS's quality performance standard and the alternative "
                 f"standard for PY{py}</b>, so it is not eligible to share in savings.")
    else:
        tone = "neut"
        first = f"CMS did not publish whether {name} met the quality performance standard for PY{py}."

    where = ""
    if len(domains) >= 2:
        weak, strong = domains[0], domains[-1]
        where = (f"weakest in {weak['domain'].lower()} (p{weak['avg_performance_pct']:.0f}) and "
                 f"strongest in {strong['domain'].lower()} (p{strong['avg_performance_pct']:.0f})")
    p = score.get("performance_pct") if score else None
    if euc and where:
        second = (f"Because that score is a floor, its own measure results say more: against "
                  f"{group} they are {where}.")
    elif euc:
        second = "That score is a floor rather than a measured result, and no measure results were reported."
    elif p is not None:
        second = (f"Its composite score of {fmt(score['value'], '%')} is "
                  f"{perf_word(p).lower()} (p{p:.0f}) against {group}"
                  f"{'; it is ' + where if where else ''}.")
    else:
        second = ""
    return f'<div class="takeaway t-{tone}">{first} {second}</div>'


# (label, flag key, indented under the row above, bold, a pass/fail gate rather than a fact)
QUALITY_DETERMINATION = [
    ("Met Shared Savings Program quality reporting requirements",
     "Met_SSP_quality_reporting_requirements", False, False, True),
    ("Reported via CMS Web Interface", "Report_WI", True, False, False),
    ("Reported via eCQM / MIPS CQM / Medicare CQM", "Report_eCQM_CQM_MedicareCQM", True, False, False),
    ("Affected by extreme and uncontrollable circumstances (score set to at least the 40th "
     "percentile)", "Recvd40p", False, False, False),
    ("ACO met the Quality Performance Standard", "Met_QPS", False, True, True),
    ("Pathway: quality score at or above the 40th-percentile MIPS score", "Met_40pctl", True, False, True),
    ("Pathway: met the eCQM / MIPS CQM reporting incentive", "Met_Incentive", True, False, True),
    ("Pathway: first-year ACO that met the reporting requirements", "Met_FirstYear", True, False, True),
    ("ACO met the Alternative Quality Performance Standard", "Met_AltQPS", False, True, True),
]


def yes_no(state, gate: bool = True) -> str:
    """Yes/No pill: green/red for pass/fail gates, gray for plain facts and N/A."""
    shown = state if state in ("Yes", "No") else "N/A"
    tier = {"Yes": "good", "No": "bad"}.get(shown, "neut") if gate else "neut"
    return f'<span class="k-word t-{tier}">{shown}</span>'


def determination_table(score_text: str, flags: dict) -> str:
    """CMS Tables 1–2 layout: the quality score and each Yes/No gate behind the standard."""
    rows = [f'<tr><td>Quality score</td><td class="val">{score_text}</td></tr>']
    # One pathway is enough, so a pathway's "No" only counts against an ACO that missed the standard.
    met_qps = flags.get("Met_QPS", {}).get("state") == "Yes"
    for label, key, indent, bold, gate in QUALITY_DETERMINATION:
        f = flags.get(key)
        if met_qps and label.startswith("Pathway:"):
            gate = f is not None and f["state"] == "Yes"
        text = f"<b>{html.escape(label)}</b>" if bold else html.escape(label)
        rows.append(f'<tr><td class="{"ind" if indent else ""}" '
                    f'title="{html.escape(f["description"]) if f else ""}">{text}</td>'
                    f'<td class="val">{yes_no(f["state"] if f else None, gate)}</td></tr>')
    rows.append('<tr><td>Population and income adjustment bonus points</td>'
                '<td class="val muted">Not in the public CMS file</td></tr>')
    return (f'<table class="bm det"><thead><tr><th class="txt">Quality determination</th>'
            f'<th>Result</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


def measure_table(items: list[dict], title: str, subtitle: str, group: str,
                  with_collection: bool) -> str:
    """CMS Table 3 / Table 5 layout: measure #, title, rate, SSP ACO mean, plus our rating."""
    def order(m):
        mid = m["measure_id"]
        return (1, int(mid[6:])) if mid.startswith("CAHPS-") else (0, int(mid))

    head = ('<tr><th class="txt">#</th><th class="txt">Measure</th>'
            + ('<th class="txt">Reported via</th>' if with_collection else '')
            + '<th>Rate</th><th>SSP ACO mean</th><th style="text-align:center">'
            + f'{html.escape(title)}<br><span style="font-weight:400">{html.escape(subtitle)}</span>'
            + '</th></tr>')
    rows = []
    for m in sorted(items, key=order):
        tags = (["Outcome"] if m["outcome"] else []) + (
            ["lower is better"] if m["higher_is_better"] is False else [])
        label = m["label"].removeprefix("CAHPS: ")
        rows.append(f'<tr><td>{m["measure_id"]}</td>{name_cell(label, tags, m["description"])}'
                    + (f'<td class="txt">{m["collection"]}</td>' if with_collection else '')
                    + f'<td class="val">{fmt(m["value"], "measure")}</td>'
                    + f'<td class="val">{fmt(m["aco_mean"], "measure")}</td>'
                    + pct_cell(m, "measure", group) + '</tr>')
    return (f'<table class="bm cms"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'
            .replace("$", "&#36;"))


def headline_boxes(rep: dict, pick: str, savings: dict | None) -> str:
    """The six headline KPIs vs the selected group; BY3 → PY ratios use same-vintage peers."""
    boxes = []
    for key, label in (("savings_rate", "Savings Rate"), ("shared_savings", "Shared Savings / Losses"),
                       ("quality_score", "Quality Score"),
                       ("expense_trend_by3_py", "Medical Expense Trend (BY3 → PY)"),
                       ("risk_ratio_by3_py", "Risk Score Ratio (BY3 → PY)"),
                       ("reg_adj", "Regional Adjustment / Beneficiary")):
        if key == "shared_savings":
            if not savings:
                continue
            total = savings["total"]
            group = group_label(pick, savings["cohort"]["value"])
            vint = ", same BY3 vintage" if savings.get("same_vintage") else ""
            sub = ("No shared savings earned" if not total else
                   f'{fmt(abs(savings["per_beneficiary"]), "$")} per beneficiary'
                   f'{"" if total > 0 else " owed"} vs {group}{vint} · '
                   f'{savings["cohort_n"]} ACOs')
            boxes.append(kpi_box(label, fmt_money_short(total), savings, "$", group, sub))
            continue
        boxes.append(metric_box(rep, key, label, pick))
    return f'<div class="kpis">{"".join(boxes)}</div>'.replace("$", "&#36;")


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

FIN_ORDER = ["savings_rate", "risk_ratio_by3_py", "expense_trend_by3_py", "final_adj",
             "n_beneficiaries", "quality_score", "per_capita_exp", "benchmark_per_capita",
             "share_rate", "admits_per_1000", "ed_visits_per_1000", "pc_services_per_1000",
             "readmits_proxy", "snf_admits_per_1000", "snf_los", "hcc_risk_py",
             "reg_adj", "prior_sav_adj", "pct_dual", "pct_lti"]

# ---------------------------------------------------------------- selection (every tab)
c1, c2 = st.columns([3, 1])
with c2:
    py = st.selectbox("Performance year", [2024, 2023], key="py")
with c1:
    names = {a["aco_id"]: f'{a["aco_name"]} · {a["aco_id"]} · {int(a["n_beneficiaries"] or 0):,} lives'
             for a in engine.list_acos(py)}
    aco_id = st.selectbox("ACO", list(names), format_func=names.get, index=None,
                          placeholder="Start typing an ACO name or ID…",
                          **sticky("sel", list(names)))
focus, vintage_only = cohort_radio(aco_id, py)

if aco_id:
    rep = engine.benchmark_aco(aco_id, performance_year=py, vintage_only=vintage_only)
    a = rep["aco"]
    fl = flags_by_key(a.get("quality_flags"))
    st.markdown(f"### {a['aco_name']}")
    bits = [f"**Track:** {a['track']}", f"**Revenue:** {a['rev_cat']}",
            f"**Size:** {a['size_band']}", f"**Risk:** {a['risk_model']}"]
    if a["n_beneficiaries"]:
        bits.append(f"**Beneficiaries:** {int(a['n_beneficiaries']):,}")
    if a.get("by3_vintage"):
        gap = py - int(a["by3_year"])
        bits.append(f"**BY3:** {int(a['by3_year'])} ({gap} year{'s' if gap != 1 else ''} "
                    f"before PY{py})")
    if a["service_area"]:
        bits.append(f"**Service area:** {', '.join(a['service_area'])}")
    if a["regional_peer_n"]:
        bits.append(f"**Regional peers:** {a['regional_peer_n']}")
    st.markdown(" · ".join(bits))
    if a.get("final_adj_type"):
        st.caption(f"CMS applied: {a['final_adj_type']}")
    # An ACO with no regional peers falls back to all ACOs, as the tables do.
    pick = focus if focus in rep["metrics"]["savings_rate"]["comparisons"] else "all"
    st.markdown(headline_boxes(rep, pick, engine.shared_savings(aco_id, py, pick, vintage_only)),
                unsafe_allow_html=True)
else:
    st.info("Pick an ACO above to see its performance.")

tabs = st.tabs(["🔎 ACO Performance", "🩺 Quality Performance", "💵 Medical Expense Performance",
                "📐 Benchmark My Numbers", "💬 Ask the Data", "📖 Methodology"])

# ================================================================ TAB 1
with tabs[0]:
    if aco_id:
        st.markdown("#### Financial benchmark")
        qps = fl.get("Met_QPS", {}).get("state")
        st.markdown(benchmark_table(rep, FIN_ORDER, focus, engine.cohort_sizes(aco_id, py), qps),
                    unsafe_allow_html=True)
        st.markdown(TABLE_LEGEND, unsafe_allow_html=True)
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
                                f"comparison cohort. Anchor the analysis there."
                                + (" The reader also chose to compare every metric against "
                                   f"same-vintage ACOs only ({a['by3_vintage']})."
                                   if vintage_only else "")),
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
    if aco_id:
        qp = engine.quality_profile(aco_id, cohort=focus, performance_year=py,
                                    vintage_only=vintage_only)
        if not qp:
            st.warning("No quality data for this ACO.")
        else:
            c = qp["cohort"]
            qfl = flags_by_key(qp["aco"].get("quality_flags"))
            # The gates matter more to the economics than the score does.
            st.markdown(quality_boxes(rep, pick, qfl), unsafe_allow_html=True)
            qs = rep["metrics"]["quality_score"]
            qs_c = qs["comparisons"].get(pick)
            st.markdown(quality_takeaway(
                a["aco_name"], py, qfl, {**qs_c, "value": qs["value"]} if qs_c else {},
                group_label(pick, qs_c["cohort_value"]) if qs_c else "its peers",
                qp["domain_summary"]), unsafe_allow_html=True)
            m2, m3, m4 = st.columns(3)
            m2.metric("Measures reported", len(qp["measures"]))
            m3.metric("Below cohort median", len([x for x in qp["measures"]
                                                  if x["performance_pct"] < 50]))
            m4.metric("Peer cohort size", c["n_peers"])
            if c["small_sample"]:
                st.markdown('<div class="warnbox">Small peer cohort — treat these '
                            'comparisons as directional.</div>', unsafe_allow_html=True)

            title, subtitle = cohort_header(c)
            group = cohort_text(c)

            def measure_rows(items: list[dict]) -> list[str]:
                return [table_row(
                    name_cell(mm["label"],
                              ["lower is better"] if mm["higher_is_better"] is False else [],
                              mm["description"]),
                    fmt(mm["value"], "measure"),
                    pct_cell(mm, "measure", group)) for mm in items]

            g1, g2 = st.columns(2)
            with g1:
                st.markdown("##### Biggest gaps")
                st.markdown(rank_table(title, subtitle, measure_rows(qp["biggest_gaps"]),
                                       "Measure", compact=True), unsafe_allow_html=True)
            with g2:
                st.markdown("##### Strongest measures")
                st.markdown(rank_table(title, subtitle, measure_rows(qp["top_strengths"]),
                                       "Measure", compact=True), unsafe_allow_html=True)

            st.markdown("##### Performance by domain")
            st.markdown(rank_table(title, subtitle, [table_row(
                name_cell(d["domain"]), str(d["n_measures"]),
                f'<td class="pct" title="Average percentile of {d["n_measures"]} measures">'
                f'<div class="rate">{"".join(rating(d["avg_performance_pct"]))}</div></td>')
                for d in qp["domain_summary"]],
                "Domain", "Measures"), unsafe_allow_html=True)

            st.markdown("##### Quality determination")
            st.caption("Laid out like Tables 1–2 of CMS's Quality Performance Report. "
                       "Hover a row for what it means.")
            st.markdown(determination_table(fmt(qs["value"], "%"), qfl), unsafe_allow_html=True)

            measures = [mm for mm in qp["measures"] if not mm["measure_id"].startswith("CAHPS")]
            cahps = [mm for mm in qp["measures"] if mm["measure_id"].startswith("CAHPS")]
            st.markdown("##### Measure results")
            st.caption("Laid out like CMS Table 3. SSP ACO mean is the average rate of every ACO "
                       "reporting the measure this year. Outcome measures are the ones the "
                       "alternative quality standard is judged on.")
            st.markdown(measure_table(measures, title, subtitle, group, with_collection=True),
                        unsafe_allow_html=True)
            if cahps:
                st.markdown("##### CAHPS for MIPS survey")
                st.caption("Laid out like CMS Table 5: patient-experience summary survey measures.")
                st.markdown(measure_table(cahps, title, subtitle, group, with_collection=False),
                            unsafe_allow_html=True)
            st.markdown(TABLE_LEGEND, unsafe_allow_html=True)
            st.caption("Not in the public CMS file, so not shown: numerators and denominators, MIPS "
                       "benchmarks and deciles, data completeness, measure-level detail of the "
                       "eCQM / MIPS CQM incentive (CMS Table 4), and population and income bonus "
                       "points (CMS Tables 6–7).")
            rows = [{
                "Measure #": mm["measure_id"],
                "Measure": mm["label"],
                "Domain": mm["domain"],
                "Reported via": mm["collection"],
                "Outcome measure": "Yes" if mm["outcome"] else "No",
                "Direction": "lower is better" if mm["higher_is_better"] is False else "higher is better",
                "This ACO": round(mm["value"], 2),
                "SSP ACO mean": round(mm["aco_mean"], 2),
                "Cohort p25": round(mm["cohort_p25"], 2),
                "Median": round(mm["cohort_p50"], 2),
                "Cohort p75": round(mm["cohort_p75"], 2),
                "Performance": round(mm["performance_pct"], 1),
                "n": mm["cohort_n"],
            } for mm in qp["measures"]]
            st.download_button("Download quality detail (CSV)",
                               pd.DataFrame(rows).to_csv(index=False),
                               file_name=f"quality_{aco_id}_{py}.csv", mime="text/csv")

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
                        cohort_label=cohort_text(c),
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
    st.caption("Per-capita cost and utilization ranked against a peer cohort. Spend and acute "
               "use rank lower-is-better; primary-care contact ranks higher-is-better, since "
               "raising it is often how acute utilization comes down.")
    if aco_id:
        ep = engine.expense_profile(aco_id, cohort=focus, performance_year=py,
                                    vintage_only=vintage_only)
        if not ep:
            st.warning("No expense data for this ACO.")
        else:
            c = ep["cohort"]
            a = ep["aco"]
            title, subtitle = cohort_header(c)
            group = cohort_text(c)
            m1, m2 = st.columns(2)
            m1.metric("Total per-capita expenditure", fmt(a["per_capita_exp"], "$"))
            m2.metric("Peer cohort size", c["n_peers"],
                      help="ACOs used for every metric on this tab except the BY3 → PY ratios.")

            st.markdown("##### BY3 → PY trend")
            by3_n = max((m["cohort_n"] for m in ep["by3_metrics"]), default=0)
            st.markdown(rank_table(title, f'{c["value"]} · {a["by3_vintage"]} · {by3_n} ACOs',
                                   [table_row(
                name_cell(m["label"], ["BY3"]), fmt(m["value"], m["unit"]),
                pct_cell(m, m["unit"], f'{title}: {c["value"]}'))
                for m in ep["by3_metrics"]]), unsafe_allow_html=True)

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

            def expense_rows(items: list[dict]) -> list[str]:
                return [table_row(name_cell(mm["label"], title=mm["description"]),
                                  fmt(mm["value"], mm["unit"]),
                                  pct_cell(mm, mm["unit"], group))
                        for mm in items]

            g1, g2 = st.columns(2)
            with g1:
                st.markdown("##### Biggest gaps")
                st.markdown(rank_table(title, subtitle, expense_rows(ep["biggest_gaps"]),
                                       compact=True), unsafe_allow_html=True)
            with g2:
                st.markdown("##### Strongest areas")
                st.markdown(rank_table(title, subtitle, expense_rows(ep["top_strengths"]),
                                       compact=True), unsafe_allow_html=True)

            for family, items in (("Cost", ep["cost"]), ("Utilization", ep["utilization"])):
                st.markdown(f"##### {family}")
                st.markdown(rank_table(title, subtitle, expense_rows(items)),
                            unsafe_allow_html=True)
            st.markdown(TABLE_LEGEND, unsafe_allow_html=True)

            all_rows = pd.DataFrame([{
                "Family": mm["family"], "Metric": mm["label"], "Unit": mm["unit"],
                "This ACO": mm["value"], "Cohort p25": mm.get("cohort_p25"),
                "Median": mm.get("cohort_p50"), "Cohort p75": mm.get("cohort_p75"),
                "Performance percentile": mm["performance_pct"], "n": mm["cohort_n"],
            } for mm in ep["cost"] + ep["utilization"]])
            st.download_button("Download expense detail (CSV)", all_rows.to_csv(index=False),
                               file_name=f"expense_{aco_id}_{py}.csv", mime="text/csv")

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
                        cohort_label=cohort_text(c),
                        cohort_n=c["n_peers"],
                        payload=json.dumps({
                            "total_per_capita_expenditure": a["per_capita_exp"],
                            "by3_vintage": a["by3_vintage"],
                            "by3_anchored_metrics": ep["by3_metrics"],
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
