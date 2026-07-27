"""
Single source of truth for "everything known about this patient" — built once,
consumed by both care-plan generation and evidence retrieval/generation, so
neither flow re-derives its own partial view of the encounter.

Two representations, deliberately kept separate (see module docstring in
query_builder.py for why): a compact `sustained_trends` summary for retrieval
queries, and full `history` for LLM generation prompts. Retrieval wants a
sharp, keyword-dense query; generation wants everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from db import get_supabase


@dataclass
class SustainedTrends:
    """Streak-aware trend descriptors, computed across the FULL history, not
    just the latest delta row. Extends trend_calculator.dart's "last 2
    readings" check to an actual streak count, since "rising for 5 straight
    readings" is a stronger retrieval/generation signal than "rising for 2"."""
    assessment_count: int = 0
    hours_since_first_assessment: float | None = None
    crp_rising_streak: int = 0          # consecutive assessments with crp_delta > 0
    temp_unstable_streak: int = 0        # consecutive assessments with |temp_delta| >= 0.5
    score_direction: str = "none"        # "deteriorating" | "improving" | "stable" | "none"
    score_streak: int = 0                # consecutive assessments moving in score_direction
    aki_stage: int = 0                   # latest known AKI stage (0 = none/unstaged)

    def describe(self) -> list[str]:
        """Compact phrases for the RETRIEVAL query — not the raw numbers."""
        parts: list[str] = []
        if self.crp_rising_streak >= 2:
            parts.append(f"CRP rising {self.crp_rising_streak} consecutive readings")
        if self.temp_unstable_streak >= 2:
            parts.append(f"temperature instability {self.temp_unstable_streak} consecutive readings")
        if self.score_direction == "deteriorating" and self.score_streak >= 1:
            parts.append("deteriorating trend" if self.score_streak == 1 else
                          f"deteriorating trend {self.score_streak} consecutive assessments")
        elif self.score_direction == "improving" and self.score_streak >= 1:
            parts.append("improving trend")
        if self.aki_stage >= 1:
            parts.append(f"AKI stage {self.aki_stage}")
        if self.assessment_count >= 3:
            parts.append("multiple reassessments")
        return parts


@dataclass
class PatientContext:
    encounter_id: str
    current_snapshot: dict[str, Any] = field(default_factory=dict)   # flattened, ALL fields, latest assessment
    history: list[dict[str, Any]] = field(default_factory=list)      # chronological, oldest first, for LLM prompts
    latest_deltas: dict[str, Any] | None = None                      # latest assessment_deltas row (incl. AKI staging)
    trends: SustainedTrends = field(default_factory=SustainedTrends)

    def query_context_terms(self) -> list[str]:
        """What query_builder.py should append -- compact, not raw history."""
        return self.trends.describe()

    def history_summary_for_prompt(self, max_entries: int = 10) -> str:
        """Full-enough-to-be-useful history block for the generation prompt.
        Capped at max_entries (default 10, not 5 -- the old _summarize_trend
        cap) since LLM context windows handle this fine and the whole point
        of this change is not silently truncating what the model sees."""
        if not self.history:
            return "(no prior assessments for this encounter)"
        lines = []
        for h in self.history[-max_entries:]:
            ts = h.get("created_at", "?")
            score = h.get("combined_score", "?")
            cat = h.get("category", "?")
            symptoms = ", ".join(
                f"{k}={v}" for k, v in h.get("snapshot", {}).items() if v not in (None, "", "None")
            )
            lines.append(f"- {ts}: score={score} category={cat} | {symptoms}")
        return "\n".join(lines)


def _flatten_assessment(row: dict) -> dict[str, Any]:
    """Merge maternal_data + neonatal_data + lab_data into one flat dict,
    matching PatientSnapshot's field names (see schemas.py)."""
    flat: dict[str, Any] = {}
    for section in ("maternal_data", "neonatal_data", "lab_data"):
        flat.update(row.get(section) or {})
    return flat


def build_patient_context(encounter_id: str) -> PatientContext:
    """
    Queries the full assessment history for this encounter (not just the
    latest row) plus the latest assessment_deltas row, and computes streak-
    aware sustained trends. Never raises -- returns an empty PatientContext
    on any failure, so a DB hiccup degrades to "no patient context" rather
    than breaking retrieval/generation entirely.
    """
    ctx = PatientContext(encounter_id=encounter_id)
    try:
        supabase = get_supabase()

        assessments_resp = (
            supabase.table("clinical_assessments")
            .select("id, created_at, maternal_data, neonatal_data, lab_data")
            .eq("encounter_id", encounter_id)
            .order("created_at", desc=False)
            .execute()
        )
        assessment_rows = assessments_resp.data or []
        if not assessment_rows:
            return ctx

        assessment_ids = [r["id"] for r in assessment_rows]
        risk_resp = (
            supabase.table("risk_results")
            .select("assessment_id, combined_score, category, created_at")
            .in_("assessment_id", assessment_ids)
            .execute()
        )
        score_by_assessment = {r["assessment_id"]: r for r in (risk_resp.data or [])}

        history: list[dict[str, Any]] = []
        for row in assessment_rows:
            flat = _flatten_assessment(row)
            risk = score_by_assessment.get(row["id"], {})
            history.append({
                "created_at": row.get("created_at"),
                "combined_score": risk.get("combined_score"),
                "category": risk.get("category"),
                "snapshot": flat,
            })

        ctx.history = history
        ctx.current_snapshot = history[-1]["snapshot"]

        deltas_resp = (
            supabase.table("assessment_deltas")
            .select("*")
            .eq("encounter_id", encounter_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        deltas_rows = deltas_resp.data or []
        ctx.latest_deltas = deltas_rows[0] if deltas_rows else None

        ctx.trends = _compute_sustained_trends(encounter_id, history, ctx.latest_deltas)
    except Exception as e:
        print(f"[PatientContext] build failed for encounter {encounter_id} (non-fatal): {e}")
    return ctx


def _compute_sustained_trends(
    encounter_id: str,
    history: list[dict[str, Any]],
    latest_deltas: dict[str, Any] | None,
) -> SustainedTrends:
    trends = SustainedTrends(assessment_count=len(history))

    if len(history) >= 2:
        first_ts = history[0].get("created_at")
        last_ts = history[-1].get("created_at")
        # Only bother parsing if both present -- avoid crashing on odd formats.
        try:
            from datetime import datetime
            fmt = "%Y-%m-%dT%H:%M:%S"
            t0 = datetime.fromisoformat(str(first_ts).replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))
            trends.hours_since_first_assessment = (t1 - t0).total_seconds() / 3600
        except Exception:
            pass

    # Streak computation walks history backward from the most recent entry.
    crp_streak = 0
    for i in range(len(history) - 1, 0, -1):
        prev = history[i - 1]["snapshot"].get("crp_level")
        curr = history[i]["snapshot"].get("crp_level")
        if prev is not None and curr is not None and float(curr) > float(prev):
            crp_streak += 1
        else:
            break
    trends.crp_rising_streak = crp_streak

    temp_streak = 0
    for i in range(len(history) - 1, 0, -1):
        prev = history[i - 1]["snapshot"].get("neonatal_temperature")
        curr = history[i]["snapshot"].get("neonatal_temperature")
        if prev is not None and curr is not None and abs(float(curr) - float(prev)) >= 0.5:
            temp_streak += 1
        else:
            break
    trends.temp_unstable_streak = temp_streak

    score_streak = 0
    direction = "none"
    scores = [h.get("combined_score") for h in history if h.get("combined_score") is not None]
    if len(scores) >= 2:
        last_delta = scores[-1] - scores[-2]
        direction = "deteriorating" if last_delta > 0 else "improving" if last_delta < 0 else "stable"
        if direction != "stable":
            for i in range(len(scores) - 1, 0, -1):
                d = scores[i] - scores[i - 1]
                same_direction = (d > 0 and direction == "deteriorating") or (d < 0 and direction == "improving")
                if same_direction:
                    score_streak += 1
                else:
                    break
    trends.score_direction = direction
    trends.score_streak = score_streak

    if latest_deltas:
        trends.aki_stage = int(latest_deltas.get("aki_stage") or 0)

    return trends