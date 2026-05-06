"""
LLM narrator. Wraps Claude API.

Two prompts:

    narrate_benchmark(report, user_question=None)
        Given a benchmark report (from engine.benchmark_aco or
        benchmark_synthetic), produce a 4-6 sentence interpretation
        suitable for an analytics dashboard.

    answer_question(question, context_data)
        Free-form Q&A over the MSSP cohort, where context_data is a
        small JSON blob the engine assembled.

Both prompts are deliberately conservative: the model is told to refuse
to answer if the data isn't in the context, and to flag small-sample
warnings when n < 25.

Auth: reads ANTHROPIC_API_KEY from environment. Override the model with
MSSP_BENCH_MODEL (default: claude-sonnet-4-6).
"""
from __future__ import annotations

import json
import os
from typing import Any

# We import lazily so that build_data.py and engine.py don't require
# anthropic to be installed.
def _client():
    import anthropic
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


MODEL = os.environ.get("MSSP_BENCH_MODEL", "claude-sonnet-4-6")


SYSTEM_NARRATE = """You are a healthcare analytics narrator. You receive a
JSON benchmark report comparing one ACO (or one set of synthetic numbers)
to its peer cohorts in the CMS Medicare Shared Savings Program.

Your job:
1. Translate the percentile ranks into plain-English findings.
2. Pay attention to `higher_is_better` for each metric — for metrics
   where lower is better (admits, ED visits, SNF LOS, readmits,
   per-capita expenditure), a LOW percentile rank means GOOD performance,
   a HIGH percentile rank means BAD performance. Always frame relative
   to "good" vs "bad," not just "high" vs "low."
3. Lead with the most decision-relevant finding (savings rate and
   quality typically come first).
4. Where percentile ranks differ across cohort cuts (e.g. top quartile
   vs same-track peers but only median vs all ACOs), call that out.
5. If a cohort has fewer than 25 ACOs (cohort_n < 25), note that the
   benchmark is wide and should be interpreted cautiously.
6. NEVER fabricate numbers. If the report doesn't contain the metric,
   say so plainly.
7. Keep it tight: 4-6 sentences, professional, no marketing fluff,
   no emojis. End with one specific suggestion for what to investigate next.
"""

SYSTEM_QA = """You are a healthcare analytics assistant answering questions
about Medicare Shared Savings Program (MSSP) ACOs using only the JSON
data provided in the user message under "DATA:".

Rules:
1. If the answer is not derivable from the DATA block, say so plainly
   and suggest what data would be needed.
2. Cite specific numbers from DATA when answering.
3. For metrics where lower is better (admits, ED visits, readmits,
   SNF LOS, per-capita expenditure), frame results in terms of good/bad
   not just high/low.
4. Keep responses 3-6 sentences unless the user asks for detail.
5. Never mention any specific company, vendor, or product by name
   unless the user already mentioned it.
6. The data is from the CMS Public-Use File. It does NOT include
   provider-level detail, claims, or PHI.
"""


def narrate_benchmark(report: dict, user_question: str | None = None,
                      max_tokens: int = 600) -> str:
    """Return a narrative interpretation of a benchmark report."""
    user = "BENCHMARK_REPORT:\n" + json.dumps(report, indent=2, default=str)
    if user_question:
        user += "\n\nUSER_QUESTION (frame the narrative around this):\n" + user_question.strip()
    else:
        user += "\n\nGenerate a balanced narrative."

    msg = _client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=SYSTEM_NARRATE,
        messages=[{"role": "user", "content": user}],
    )
    # Concatenate text blocks
    return "".join(b.text for b in msg.content if hasattr(b, "text")).strip()


def answer_question(question: str, context_data: dict, max_tokens: int = 600) -> str:
    """Free-form Q&A grounded in pre-assembled context_data."""
    user = (
        "DATA:\n" + json.dumps(context_data, indent=2, default=str) +
        "\n\nQUESTION: " + question.strip()
    )
    msg = _client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=SYSTEM_QA,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if hasattr(b, "text")).strip()


if __name__ == "__main__":
    # Smoke test against a real ACO if API key is set.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY to run the narration smoke test.")
        raise SystemExit(0)
    from engine import benchmark_aco, find_acos
    hits = find_acos("aledade delaware", limit=1)
    if not hits:
        raise SystemExit("No ACO found.")
    report = benchmark_aco(hits[0]["aco_id"])
    print("=== Narration ===")
    print(narrate_benchmark(report,
        user_question="How is this ACO performing relative to peers, and where should the team focus next year?"))
