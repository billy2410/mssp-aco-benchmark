# ACO Benchmark — internal analytics tool

Benchmarks Medicare Shared Savings Program ACOs on financial and quality
performance against peer cohorts, and generates narrative explanations.

**Public CMS data only.** Shared Savings Program Public-Use Files plus the ACO
Participants file. No PHI, no claims-level data, no proprietary inputs.

---

## What it does

**Financial benchmarking** — 19 metrics per ACO, each ranked against five or six
peer cohorts (track, risk model, revenue category, size band, all ACOs, and
regional peers).

**Three actuarial KPIs** added at the request of the ACO program team:

| KPI | What it answers |
|---|---|
| Risk Score Ratio (BY3 → PY) | Did documented risk grow relative to the benchmark period? |
| Medical Expense Trend (BY3 → PY) | Did per-capita cost grow faster or slower than the benchmark period? |
| Final Benchmark Adjustment | What adjustment did CMS actually apply — regional or prior-savings? |

**Quality performance view** — every reported quality measure (CAHPS, MIPS
QualityIDs, admin-claims measures) named, ranked, and split into gaps and
strengths, with an LLM coach that explains *which* measures are dragging the
composite down.

**Regional comparison** — peer cohorts built from ACO service areas, so an ACO
is compared against the competitors it actually shares a market with.

---

## Setup

```bash
python3 -m pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...          # required for narratives
export APP_PASSWORD=some-passphrase          # optional access gate
streamlit run app.py
```

Opens at `http://localhost:8501`.

Data is pre-built and committed, so the app runs immediately. Rebuild only when
CMS publishes new files:

```bash
python3 build_data.py
```

---

## Editing the AI prompts (no code required)

Every narrative the tool produces is driven by a plain-text file in `prompts/`:

| File | Drives |
|---|---|
| `prompts/quality_coach.md` | The quality coaching narrative |
| `prompts/narrative.md` | The financial performance narrative |
| `prompts/medical_expense.md` | The medical expense / utilization narrative |
| `prompts/qa.md` | The "Ask the data" tab |
| `prompts/_shared_context.md` | MSSP program knowledge spliced into all four of the above — edit once, it propagates everywhere. Do not write the shared-context placeholder token literally in this file. |

Edit the file, save it, click **Rerun** in Streamlit. The next narrative uses
your version. No redeploy, no code changes.

Each file documents its own placeholders (`{aco_name}`, `{payload}`, and so on)
at the top. Anything outside those placeholders is yours to rewrite — tone,
structure, what to emphasize, what to ignore.

**Suggested first experiment:** open `prompts/quality_coach.md` and change the
"Where to focus" section to reflect how your team actually prioritizes
improvement work. That section is the one most worth tuning to house style.

---

## Multiple developers

**Repository** — push this folder to a Git repo and add collaborators. Prompt
files are plain text, so prompt changes produce readable diffs and can be
reviewed like any other change.

```bash
git init && git add . && git commit -m "Initial commit"
git remote add origin <your-repo-url>
git push -u origin main
```

**API keys** — each developer uses their own key from the same Anthropic
workspace, or a single shared key held in Streamlit secrets. Per-key spend
limits are set in the Anthropic console under Workspaces.

**Hosting options**

| Option | Good for | Notes |
|---|---|---|
| Local (`streamlit run app.py`) | Individual analysis | No hosting cost; each user needs a key |
| Streamlit Community Cloud, private app | Small shared team | Free; app is private but hosted externally |
| Internal container hosting | Team of record | Keeps everything inside the corporate boundary |

If this becomes a tool of record for the team, internal hosting is the right
answer — not because of the data (it is all public) but so access, spend, and
availability are managed by the same controls as other internal tooling.

---

## Cost control

Nothing calls the API until someone clicks a **Generate narrative** or **Ask**
button. Browsing, filtering, ranking, and CSV export are entirely local and
free.

Typical narrative cost is a few cents. Practical controls:

- Set a monthly spend limit on the workspace in the Anthropic console.
- Give each developer their own key so usage is attributable.
- Keep `ACO_MODEL` on the default; a larger model raises cost without improving
  these narratives much.
- The payload sent to the model is already trimmed and capped.

---

## Refreshing when the PY2025 PUF lands

1. Download the new results file into `data/`.
2. Add its filename to the top of `PY2024_CANDIDATES` in `build_data.py` (the
   list is checked in order; first match wins), or rename it to match.
3. Download the matching-year ACO Participants file for regional peering.
4. Run `python3 build_data.py`.
5. Commit the regenerated artifacts in `data/`.

CMS revises these files. The PY2024 file was revised on 2026-07-17, changing
savings rates for 131 of 476 ACOs — worth re-pulling before any cycle of
analysis rather than assuming a cached copy is current.

---

## Methodology notes worth knowing

**Direction-aware ranking.** Some measures are inverse — HbA1c poor control,
readmissions, MCC admission rates, cost, expense trend. A low value is good
performance. The tool ranks on the raw scale but also computes a
direction-adjusted `performance_pct` where 90 always means good. The narrative
prompts are instructed to use it. This is the single easiest thing to get
backwards and the reason it is handled centrally rather than per-view.

**BY3 risk weights.** CMS publishes normalized `RR_weight_*` fields for the
performance year but not for benchmark year 3. BY3 weights are therefore
derived from `N_AB_Year_*_BY3` person-year counts — the same membership
weighting expressed in the units CMS did publish. Worth confirming against your
own actuarial convention; it is isolated in one function (`_weighted`) if you
prefer a different basis.

**Quality coalescing.** Measures reported through multiple mechanisms are
collapsed to one value per ACO. Verified against the PY2024 file: no ACO reports
the same measure through more than one mechanism, so the coalescing is lossless.

**Regional peers.** Service-area **state** is the finest geography the
Participants file publishes. A multi-state ACO inherits every ACO touching any
of its states, which can produce large peer sets. 260 of 480 ACOs are
multi-state, so cohort size is displayed on every comparison and flagged when
under 10.

---

## Sources

- [Performance Year Financial and Quality Results](https://data.cms.gov/medicare-shared-savings-program/performance-year-financial-and-quality-results)
- [ACO Participants](https://data.cms.gov/medicare-shared-savings-program/accountable-care-organization-participants)
- [Data dictionary](https://data.cms.gov/resources/performance-year-financial-and-quality-results-data-dictionary)
