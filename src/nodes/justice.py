from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from src.state import AgentState, JudicialOpinion

load_dotenv()

_RUBRIC_PATH = Path(__file__).resolve().parents[2] / "rubric.json"
_REPORT_PATH = Path(__file__).resolve().parents[2] / "audit_report.md"

_SECURITY_KEYWORDS = ("security", "os.system", "shell=true", "vulnerability")
_WEIGHTED_CRITERIA = {"graph_orchestration", "state_management_rigor"}


@dataclass
class JudgeRecord:
    judge: str
    score: int
    argument: str
    cited_evidence: list[str]
    effective_score: float
    discounted: bool


def _load_rubric() -> dict[str, Any]:
    with _RUBRIC_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def _to_opinion(item: Any) -> JudicialOpinion | None:
    try:
        if isinstance(item, JudicialOpinion):
            return item
        if isinstance(item, dict):
            return JudicialOpinion.model_validate(item)
    except Exception:
        return None
    return None


def _collect_evidence_catalog(state: AgentState) -> set[str]:
    catalog: set[str] = set()
    evidences = state.get("evidences", {})

    for bucket_name, evidence_list in evidences.items():
        if bucket_name:
            catalog.add(str(bucket_name).strip())

        for evidence_item in evidence_list:
            if isinstance(evidence_item, dict):
                goal = evidence_item.get("goal")
                location = evidence_item.get("location")
                content = evidence_item.get("content")
            else:
                goal = getattr(evidence_item, "goal", None)
                location = getattr(evidence_item, "location", None)
                content = getattr(evidence_item, "content", None)

            for value in (goal, location, content):
                if isinstance(value, str) and value.strip():
                    catalog.add(value.strip())

    return catalog


def _has_valid_citation(citations: list[str], evidence_catalog: set[str]) -> bool:
    if not citations:
        return False

    lowered_catalog = [entry.lower() for entry in evidence_catalog]
    for citation in citations:
        normalized = citation.strip()
        if not normalized:
            continue

        if normalized in evidence_catalog:
            return True

        probe = normalized.lower()
        if any(probe in source or source in probe for source in lowered_catalog):
            return True

    return False


def _apply_evidence_rule(opinion: JudicialOpinion, evidence_catalog: set[str]) -> JudgeRecord:
    has_valid = _has_valid_citation(opinion.cited_evidence, evidence_catalog)
    if has_valid:
        return JudgeRecord(
            judge=opinion.judge,
            score=opinion.score,
            argument=opinion.argument,
            cited_evidence=opinion.cited_evidence,
            effective_score=float(opinion.score),
            discounted=False,
        )

    discounted_score = max(1.0, opinion.score - 1.0)
    return JudgeRecord(
        judge=opinion.judge,
        score=opinion.score,
        argument=opinion.argument,
        cited_evidence=opinion.cited_evidence,
        effective_score=discounted_score,
        discounted=True,
    )


def _criterion_final_score(criterion_id: str, records: list[JudgeRecord]) -> float:
    if not records:
        return 1.0

    by_judge = {record.judge: record for record in records}

    if criterion_id in _WEIGHTED_CRITERIA:
        weighted_pairs = [
            ("Prosecutor", 0.25),
            ("Defense", 0.25),
            ("TechLead", 0.50),
        ]
        numerator = 0.0
        denominator = 0.0
        for judge, weight in weighted_pairs:
            record = by_judge.get(judge)
            if record is None:
                continue
            numerator += record.effective_score * weight
            denominator += weight
        if denominator == 0:
            return 1.0
        return numerator / denominator

    return mean(record.effective_score for record in records)


def _security_cap(records: list[JudgeRecord], score: float) -> float:
    prosecutor = next((record for record in records if record.judge == "Prosecutor"), None)
    if prosecutor is None:
        return score

    search_blob = " ".join([prosecutor.argument, *prosecutor.cited_evidence]).lower()
    if any(keyword in search_blob for keyword in _SECURITY_KEYWORDS):
        return min(score, 3.0)

    return score


def _build_dissent(records: list[JudgeRecord]) -> str | None:
    if len(records) < 2:
        return None

    scores = [record.effective_score for record in records]
    if max(scores) - min(scores) <= 2:
        return None

    prosecutor = next((record for record in records if record.judge == "Prosecutor"), None)
    defense = next((record for record in records if record.judge == "Defense"), None)
    tech = next((record for record in records if record.judge == "TechLead"), None)

    return (
        "Contested Verdict: Prosecutor and Defense diverged significantly, while "
        "TechLead acted as the technical tie-breaker. "
        f"Scores -> Prosecutor: {prosecutor.effective_score if prosecutor else 'n/a'}, "
        f"Defense: {defense.effective_score if defense else 'n/a'}, "
        f"TechLead: {tech.effective_score if tech else 'n/a'}."
    )


def _llm_summary_and_plan(payload: dict[str, Any]) -> tuple[str, str]:
    try:
        llm = ChatGroq(model_name="llama-3.3-70b-versatile", temperature=0)
        prompt = (
            "You are the Chief Justice writing the final synthesis for a software audit. "
            "Use the deterministic scoring results exactly as provided.\n\n"
            "Return plain text using this exact format:\n"
            "EXECUTIVE_SUMMARY:\n"
            "<2-4 sentences, include overall verdict>\n\n"
            "REMEDIATION_PLAN:\n"
            "- bullet 1\n"
            "- bullet 2\n"
            "- bullet 3\n\n"
            f"DATA:\n{json.dumps(payload, ensure_ascii=False)}"
        )
        response = llm.invoke(prompt)
        text = response.content if isinstance(response.content, str) else str(response.content)

        if "REMEDIATION_PLAN:" in text and "EXECUTIVE_SUMMARY:" in text:
            summary_part = text.split("EXECUTIVE_SUMMARY:", 1)[1]
            summary_text, plan_text = summary_part.split("REMEDIATION_PLAN:", 1)
            return summary_text.strip(), plan_text.strip()

        return text.strip(), "- Review contested criteria and align evidence citations."
    except Exception:
        return (
            "The audit identifies mixed compliance with several high-risk gaps that require immediate remediation.",
            "- Resolve security-sensitive tool usage and enforce safe execution patterns.\n"
            "- Tighten structured-output guarantees and citation validity checks.\n"
            "- Stabilize graph orchestration, especially fan-out/fan-in and synthesis logic.",
        )


def chief_justice_node(state: AgentState) -> dict[str, Any]:
    try:
        rubric = _load_rubric()
        dimensions = rubric.get("dimensions", [])

        all_opinions = [_to_opinion(item) for item in state.get("opinions", [])]
        opinions = [item for item in all_opinions if item is not None]

        evidence_catalog = _collect_evidence_catalog(state)

        by_criterion: dict[str, list[JudicialOpinion]] = {}
        for opinion in opinions:
            by_criterion.setdefault(opinion.criterion_id, []).append(opinion)

        criterion_blocks: list[str] = []
        criterion_scores: list[float] = []
        synthesis_payload: list[dict[str, Any]] = []

        for dimension in dimensions:
            criterion_id = str(dimension.get("id", "unknown_criterion"))
            criterion_name = str(dimension.get("name", criterion_id))
            criterion_opinions = by_criterion.get(criterion_id, [])

            records = [_apply_evidence_rule(opinion, evidence_catalog) for opinion in criterion_opinions]

            base_score = _criterion_final_score(criterion_id, records)
            final_score = _security_cap(records, base_score)
            rounded_score = max(1, min(5, int(round(final_score))))
            criterion_scores.append(float(rounded_score))

            dissent = _build_dissent(records)
            contested = dissent is not None

            judge_lines: list[str] = []
            for record in records:
                discount_note = " (discounted for invalid citation)" if record.discounted else ""
                citations = ", ".join(record.cited_evidence) if record.cited_evidence else "No citations"
                judge_lines.append(
                    f"- **{record.judge}**: score={record.score}, effective={record.effective_score:.2f}{discount_note}\n"
                    f"  - Argument: {record.argument}\n"
                    f"  - Citations: {citations}"
                )

            if not judge_lines:
                judge_lines.append("- No judicial opinion captured for this criterion.")

            criterion_blocks.append(
                f"### {criterion_name} (`{criterion_id}`)\n"
                f"- Final Score: **{rounded_score}/5**\n"
                f"- Contested Verdict: **{'Yes' if contested else 'No'}**\n"
                + (f"- Dissent Summary: {dissent}\n" if dissent else "")
                + "\n".join(judge_lines)
            )

            synthesis_payload.append(
                {
                    "criterion_id": criterion_id,
                    "criterion_name": criterion_name,
                    "final_score": rounded_score,
                    "contested": contested,
                    "dissent_summary": dissent,
                    "judges": [
                        {
                            "judge": record.judge,
                            "score": record.score,
                            "effective_score": record.effective_score,
                            "discounted": record.discounted,
                            "argument": record.argument,
                            "cited_evidence": record.cited_evidence,
                        }
                        for record in records
                    ],
                }
            )

        overall_score = (sum(criterion_scores) / len(criterion_scores)) if criterion_scores else 1.0
        overall_grade = (
            "Master Thinker" if overall_score >= 4.5 else
            "Competent Orchestrator" if overall_score >= 3.0 else
            "The Vibe Coder"
        )

        executive_summary, remediation_plan = _llm_summary_and_plan(
            {
                "overall_score": overall_score,
                "overall_grade": overall_grade,
                "criteria": synthesis_payload,
                "synthesis_rules": rubric.get("synthesis_rules", {}),
            }
        )

        report = (
            "# Executive Summary\n"
            f"- Overall Grade: **{overall_grade}**\n"
            f"- Total Score: **{overall_score:.2f} / 5.00**\n"
            f"- High-Level Verdict: {executive_summary}\n\n"
            "## Criterion Breakdown\n"
            + "\n\n".join(criterion_blocks)
            + "\n\n## Remediation Plan\n"
            + remediation_plan
            + "\n"
        )

        _REPORT_PATH.write_text(report, encoding="utf-8")

        return {
            "messages": [
                f"ChiefJustice synthesis complete. Audit report saved to {_REPORT_PATH}."
            ]
        }
    except Exception as error:
        return {
            "messages": [f"ChiefJustice synthesis failed: {error}"]
        }
