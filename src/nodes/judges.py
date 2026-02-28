from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from src.nodes.llm_locks import LLM_CALL_LOCK
from src.state import AgentState, JudicialOpinion

load_dotenv()

_RUBRIC_PATH = Path(__file__).resolve().parents[2] / "rubric.json"


def _load_rubric() -> dict[str, Any]:
    if not _RUBRIC_PATH.exists():
        raise FileNotFoundError(f"rubric.json not found at {_RUBRIC_PATH}")

    with _RUBRIC_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def _extract_evidence_strings(state: AgentState, limit: int = 18) -> list[str]:
    evidences = state.get("evidences", {})
    strings: list[str] = []

    for bucket_name, evidence_list in evidences.items():
        for evidence_item in evidence_list:
            if isinstance(evidence_item, dict):
                goal = evidence_item.get("goal")
                location = evidence_item.get("location")
                content = evidence_item.get("content")
            else:
                goal = getattr(evidence_item, "goal", None)
                location = getattr(evidence_item, "location", None)
                content = getattr(evidence_item, "content", None)

            if isinstance(goal, str) and goal.strip():
                strings.append(goal.strip())
            if isinstance(location, str) and location.strip():
                strings.append(location.strip())
            if isinstance(content, str) and content.strip():
                strings.append(content.strip())

            if len(strings) >= limit:
                break

        if bucket_name and len(strings) < limit:
            strings.append(bucket_name)

    sanitized: list[str] = []
    for text in strings[:limit]:
        cleaned = text.replace("\\", "/").replace('"', "'").strip()
        sanitized.append(cleaned[:200])

    return sanitized


def _resolve_citations(
    cited_evidence: list[str],
    evidence_strings: list[str],
    fallback_source: str,
) -> list[str]:
    if not evidence_strings:
        return [f"No forensic evidence found for this dimension ({fallback_source})"]

    valid_exact = [item for item in cited_evidence if item in evidence_strings]
    if valid_exact:
        return valid_exact

    fuzzy_matches: list[str] = []
    lowered_sources = {source.lower(): source for source in evidence_strings}
    for citation in cited_evidence:
        probe = citation.lower().strip()
        if not probe:
            continue

        for source_lower, source_original in lowered_sources.items():
            if probe in source_lower or source_lower in probe:
                fuzzy_matches.append(source_original)
                break

    if fuzzy_matches:
        deduped: list[str] = []
        for item in fuzzy_matches:
            if item not in deduped:
                deduped.append(item)
        return deduped

    return [evidence_strings[0]]


def _extract_failed_generation_text(error: Exception) -> str:
    failed_generation = getattr(error, "failed_generation", None)
    if failed_generation is None:
        return str(error)

    if isinstance(failed_generation, str):
        return failed_generation
    if isinstance(failed_generation, dict):
        return json.dumps(failed_generation, ensure_ascii=False)
    if isinstance(failed_generation, list):
        parts: list[str] = []
        for item in failed_generation:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)

    return str(failed_generation)


def _force_json_extraction(raw_text: str) -> str:
    match = re.search(r"\{[\s\S]*\}", raw_text)
    if not match:
        return raw_text.strip()
    return match.group(0).strip().strip("`")


def _manual_parse_judicial_opinion(error: Exception) -> JudicialOpinion | None:
    raw_text = _extract_failed_generation_text(error)
    cleaned = _force_json_extraction(raw_text)
    try:
        return JudicialOpinion.model_validate_json(cleaned)
    except Exception:
        return None


def _truncate_evidence_context(evidence_strings: list[str], max_chars: int = 2000) -> str:
    payload = json.dumps(evidence_strings, ensure_ascii=False)
    if len(payload) <= max_chars:
        return payload
    return payload[:1000] + " ...[TRUNCATED]... " + payload[-1000:]


def _run_judge(
    state: AgentState,
    *,
    judge_key: str,
    judge_label: str,
    emergency_retry_count: int = 0,
) -> dict[str, Any]:
    try:
        rubric = _load_rubric()
        judicial_logic = rubric.get("judicial_logic", {}).get(judge_key, {})
        dimensions = rubric.get("dimensions", [])

        llm = ChatGroq(model_name="llama-3.1-8b-instant", temperature=0)
        judge_chain = llm.with_structured_output(JudicialOpinion)

        evidence_strings = _extract_evidence_strings(state)
        if not evidence_strings:
            evidence_strings = ["No forensic evidence found for this dimension"]
        evidence_context = _truncate_evidence_context(evidence_strings)

        opinions: list[JudicialOpinion] = []

        for index, dimension in enumerate(dimensions, start=1):
            time.sleep(15)
            if index % 5 == 0:
                time.sleep(30)
            criterion_id = str(dimension.get("id", "unknown_criterion"))
            forensic_instruction = str(dimension.get("forensic_instruction", ""))[:300]
            success_pattern = str(dimension.get("success_pattern", ""))[:300]
            prompt = (
                f"You are the {judge_label} in a Digital Courtroom.\n"
                f"Core Philosophy: {judicial_logic.get('core_philosophy', '')}\n"
                f"Handbook: {judicial_logic.get('handbook', '')}\n"
                f"Focus Areas: {json.dumps(judicial_logic.get('focus', []), ensure_ascii=False)}\n\n"
                f"Criterion ID: {criterion_id}\n"
                f"Criterion Name: {dimension.get('name', '')}\n"
                f"Forensic Instruction: {forensic_instruction}\n"
                f"Success Pattern: {success_pattern}\n"
                f"Failure Pattern: {dimension.get('failure_pattern', '')}\n\n"
                "Available evidence strings (you must cite exact strings from this list):\n"
                f"{evidence_context}\n\n"
                "When citing evidence, do not copy-paste large JSON objects. Instead, summarize the key finding "
                "(e.g., 'stategraph_detected: true') to ensure valid JSON formatting.\n\n"
                "Return a JudicialOpinion with:\n"
                f"- judge='{judge_label}'\n"
                f"- criterion_id='{criterion_id}'\n"
                "- score from 1 to 5\n"
                "- argument with concrete forensic reasoning\n"
                "- cited_evidence as exact strings from the provided evidence list only.\n"
                "You are an API component. Your output is consumed by a strict JSON parser. "
                "Any text outside of the JSON schema will break the system. Output only the raw JSON object.\n"
                "CRITICAL: You MUST respond ONLY with a valid function call to JudicialOpinion. "
                "Do not include any conversational text, preamble, or markdown code blocks like ```json. "
                "Output raw JSON only."
            )

            simplified_prompt = (
                prompt
                + "\n\nThe previous attempt failed due to JSON formatting. "
                "Please provide a shorter argument and avoid using special characters "
                "or raw code snippets in your citations."
            )

            model_opinion: JudicialOpinion | dict[str, Any] | None = None
            last_error: Exception | None = None
            for attempt in range(3):
                current_prompt = prompt if attempt < 2 else simplified_prompt
                try:
                    with LLM_CALL_LOCK:
                        model_opinion = judge_chain.invoke(current_prompt)
                    break
                except Exception as error:
                    last_error = error

                    parse_error_hint = (
                        "Failed to call a function" in str(error)
                        or "OutputParserException" in type(error).__name__
                    )
                    if parse_error_hint:
                        recovered = _manual_parse_judicial_opinion(error)
                        if recovered is not None:
                            model_opinion = recovered
                            break

                    status_code = getattr(error, "status_code", None)
                    error_name = type(error).__name__
                    if status_code == 429 or "RateLimitError" in error_name:
                        print("Daily/Minute Limit Hit. Emergency sleep for 60 seconds...")
                        time.sleep(60)
                        if emergency_retry_count < 1:
                            return _run_judge(
                                state,
                                judge_key=judge_key,
                                judge_label=judge_label,
                                emergency_retry_count=emergency_retry_count + 1,
                            )

            if model_opinion is None:
                if last_error is not None:
                    raise last_error
                raise RuntimeError("Judge invocation failed without a recoverable response")

            if isinstance(model_opinion, JudicialOpinion):
                opinion = model_opinion
            else:
                opinion = JudicialOpinion.model_validate(model_opinion)

            valid_citations = _resolve_citations(
                opinion.cited_evidence,
                evidence_strings,
                fallback_source=dimension.get("target_artifact", "Source"),
            )

            normalized = opinion.model_copy(
                update={
                    "judge": judge_label,
                    "criterion_id": criterion_id,
                    "cited_evidence": valid_citations,
                }
            )
            opinions.append(normalized)

        return {
            "opinions": opinions,
            "messages": [
                f"{judge_label} node completed {len(opinions)} criterion reviews."
            ],
        }
    except Exception as error:
        fallback = JudicialOpinion(
            judge=judge_label,  # type: ignore[arg-type]
            criterion_id="judicial_runtime_error",
            score=1,
            argument=f"{judge_label} could not complete judicial analysis: {error}",
            cited_evidence=["No forensic evidence found for this dimension"],
        )
        return {
            "opinions": [fallback],
            "messages": [f"{judge_label} node error: {error}"],
        }


def prosecutor_node(state: AgentState) -> dict[str, Any]:
    time.sleep(0)
    return _run_judge(
        state,
        judge_key="prosecutor",
        judge_label="Prosecutor",
    )


def defense_node(state: AgentState) -> dict[str, Any]:
    time.sleep(4)
    return _run_judge(
        state,
        judge_key="defense",
        judge_label="Defense",
    )


def tech_lead_node(state: AgentState) -> dict[str, Any]:
    time.sleep(8)
    return _run_judge(
        state,
        judge_key="tech_lead",
        judge_label="TechLead",
    )
