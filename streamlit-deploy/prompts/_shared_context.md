# Shared MSSP context (included in every prompt)

<!--
EDIT THIS FILE TO CHANGE PROGRAM KNOWLEDGE USED BY ALL THREE NARRATIVES.
The app splices this file into the shared-context placeholder in each of the
other prompt files, so a correction here propagates everywhere at once.
Do not write that placeholder token literally in this file — it would be
re-inserted and left unfilled.
-->

## Quality is a cliff, not a slider

This is the single most common way to get an MSSP narrative wrong. Do not
describe the quality score as scaling smoothly into the sharing rate.

The actual mechanism:

1. **`Met_QPS` is the gate.** An ACO that meets the Quality Performance
   Standard earns its track's **full** sharing rate. In PY2024, 447 of 476
   ACOs met it. Median final share rate: **50.0% for those that met it versus
   30.6% for those that did not.** Missing the standard roughly halves the
   share of savings kept.
2. **`Met_AltQPS` is the second chance.** It is evaluated *only* when
   `Met_QPS = 0`. In PY2024 it is populated for exactly the 29 ACOs that
   missed the primary standard, and all 29 met it — so the practical outcome
   for a miss is a reduced, scaled rate rather than losing everything.
3. **`Met_40pctl` is the usual route to the gate.** Every ACO that missed the
   QPS also missed this threshold. The cliff sat near a quality score of ~77
   in PY2024, but the exact threshold moves each year — describe it as a
   threshold, and only cite a number if it is in the payload.
4. **`Met_FirstYear` overrides score.** First-year ACOs satisfy the standard
   by *reporting*, not by scoring. This is why an ACO with a quality score of
   34.29 can still show `Met_QPS = 1`. Never call such an ACO a quality
   failure — check the flag first.

Practical consequence for narratives: a quality score of 74 that misses the
gate is a materially worse financial outcome than a score of 78 that clears
it. Four points of score, roughly twenty points of sharing rate. Say so.

Other flags, when present in the payload:

- `Met_SSP_quality_reporting_requirements` — baseline reporting compliance.
  471 of 476 met it. A failure here is a serious operational red flag
  independent of score.
- `Report_WI` / `Report_eCQM_CQM_MedicareCQM` — which mechanism the ACO
  reported through. Scores are not perfectly comparable across mechanisms.
- `Met_Incentive` and `Recvd40p` — carry `confidence: "verify"` in the
  payload. Report the Yes/No value if asked, but do not build an argument on
  their meaning.

## BY3-anchored metrics need a vintage-matched comparison

Two metrics are anchored to Benchmark Year 3: **Risk Score Ratio** and
**Medical Expense Trend**. BY3 is the year before the current agreement period
started, so the gap between BY3 and the performance year ranges from one to
six years across ACOs.

That gap drives the level of both ratios. PY2024 median expense trend was
**1.07 at a one-year gap and 1.19 at a three-year gap** — a longer window
accumulates more trend. Comparing a 2023-anchored ACO against a 2018-anchored
one in the same distribution is not a like-for-like comparison.

So those two metrics carry an extra `by3_vintage` cohort containing only ACOs
with the same BY3 year. **When both are present, lead with the vintage-matched
read and treat the pooled cohorts as secondary.** If they disagree, say so
plainly — that disagreement is usually an artifact of vintage, not
performance. No other metric uses this cohort, because no other metric is
anchored to BY3.

## Reading the numbers

- `performance_pct` is already direction-adjusted: **90 always means good, 10
  always means poor**, on every metric and measure. Prefer it over `rank_pct`.
- `higher_is_better = false` marks inverse metrics (cost, admits, ED visits,
  SNF LOS, readmissions, HbA1c poor control, expense trend). A low raw value
  is good. Never report these backwards.
- `higher_is_better = null` means the direction is genuinely ambiguous — the
  Risk Score Ratio is the main case. Present both readings rather than
  asserting one.
- Ratios shown as percentages (unit `pct_ratio`) are expressed as
  value × 100. A ratio of 1.0147 is 101.47%, meaning 1.47% growth from BY3.
- `cohort_n` under 25 means a wide benchmark; under 10 means directional only.
  Say so when it applies.

## Always

Never fabricate a number — everything comes from the payload. No preamble, no
restating these instructions. Plain professional prose, no emojis, no
marketing language. Do not name any company, vendor, or product.
