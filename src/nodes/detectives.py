from __future__ import annotations

import json
from typing import Any

from src.state import AgentState, Evidence
from src.tools.doc_tools import DocAnalyst
from src.tools.repo_tools import RepoManager


_DOC_ANALYST = DocAnalyst()
_REPO_MANAGER = RepoManager()


def doc_analyst_node(state: AgentState) -> dict[str, Any]:
	pdf_path = state.get("pdf_path", "")

	try:
		loaded = _DOC_ANALYST.load_requirements(pdf_path)
		dimensions = _DOC_ANALYST.extract_rubric_dimensions()

		requirements_payload = {
			"objectives": dimensions.objectives,
			"deliverables": dimensions.deliverables,
			"constraints": dimensions.constraints,
			"markdown_length": len(loaded.get("markdown", "")),
		}

		evidence = Evidence(
			goal="Extract architectural requirements from PDF",
			found=True,
			content=json.dumps(requirements_payload, ensure_ascii=False),
			location=pdf_path,
			rationale=(
				"DocAnalyst successfully parsed the PDF and extracted rubric-oriented "
				"sections for downstream judicial reasoning."
			),
			confidence=0.9,
		)

		return {
			"rubric_dimensions": [requirements_payload],
			"evidences": {"doc_analysis": [evidence]},
			"messages": [
				"doc_analyst_node: requirements extracted from architectural PDF."
			],
		}
	except FileNotFoundError as error:
		evidence = Evidence(
			goal="Extract architectural requirements from PDF",
			found=False,
			content=str(error),
			location=pdf_path or "unknown",
			rationale="PDF path was invalid or missing during DocAnalyst ingestion.",
			confidence=0.0,
		)
		return {
			"evidences": {"doc_analysis": [evidence]},
			"messages": [f"doc_analyst_node error: {error}"],
		}
	except Exception as error:
		evidence = Evidence(
			goal="Extract architectural requirements from PDF",
			found=False,
			content=str(error),
			location=pdf_path or "unknown",
			rationale="Unexpected failure while parsing project requirements PDF.",
			confidence=0.0,
		)
		return {
			"evidences": {"doc_analysis": [evidence]},
			"messages": [f"doc_analyst_node unexpected error: {error}"],
		}


def repo_investigator_node(state: AgentState) -> dict[str, Any]:
	repo_url = state.get("repo_url", "")

	try:
		repo_path = _REPO_MANAGER.clone_repo(repo_url)
		analysis = _REPO_MANAGER.analyze_graph_structure(repo_path)

		summary = analysis.get("summary", {})
		evidence = Evidence(
			goal="Analyze repository graph architecture with AST",
			found=bool(summary.get("stategraph_detected", False)),
			content=json.dumps(analysis, ensure_ascii=False),
			location=repo_path,
			rationale=(
				"RepoManager cloned repository in an isolated temporary directory and "
				"performed AST-based structural analysis for LangGraph components."
			),
			confidence=0.85,
		)

		return {
			"repo_path": repo_path,
			"evidences": {"repo_analysis": [evidence]},
			"messages": [
				"repo_investigator_node: clone and AST graph analysis completed."
			],
		}
	except Exception as error:
		evidence = Evidence(
			goal="Analyze repository graph architecture with AST",
			found=False,
			content=str(error),
			location=repo_url or "unknown",
			rationale="Repository investigation failed before successful AST analysis.",
			confidence=0.0,
		)
		return {
			"evidences": {"repo_analysis": [evidence]},
			"messages": [f"repo_investigator_node error: {error}"],
		}


def vision_inspector_node(state: AgentState) -> dict[str, Any]:
	pdf_path = state.get("pdf_path", "")

	try:
		image_paths = _DOC_ANALYST.extract_images_from_pdf(pdf_path)
		evidence_payload = {
			"image_count": len(image_paths),
			"image_paths": image_paths,
			"vision_status": "Pending Full Inference",
			"question": "Is this a StateGraph diagram or a generic box diagram?",
		}

		evidence = Evidence(
			goal="Inspect architectural diagrams for StateGraph fidelity",
			found=len(image_paths) > 0,
			content=json.dumps(evidence_payload, ensure_ascii=False),
			location=pdf_path,
			rationale=(
				"Images were extracted from the PDF and queued for multimodal "
				"classification. Full vision-model inference is pending."
			),
			confidence=0.7 if image_paths else 0.4,
		)

		return {
			"evidences": {"vision_analysis": [evidence]},
			"messages": [
				"vision_inspector_node: image extraction complete; vision inference pending."
			],
		}
	except FileNotFoundError as error:
		evidence = Evidence(
			goal="Inspect architectural diagrams for StateGraph fidelity",
			found=False,
			content=str(error),
			location=pdf_path or "unknown",
			rationale="Vision inspection failed because the PDF was not accessible.",
			confidence=0.0,
		)
		return {
			"evidences": {"vision_analysis": [evidence]},
			"messages": [f"vision_inspector_node error: {error}"],
		}
	except Exception as error:
		evidence = Evidence(
			goal="Inspect architectural diagrams for StateGraph fidelity",
			found=False,
			content=str(error),
			location=pdf_path or "unknown",
			rationale="Unexpected failure while extracting or preparing diagram evidence.",
			confidence=0.0,
		)
		return {
			"evidences": {"vision_analysis": [evidence]},
			"messages": [f"vision_inspector_node unexpected error: {error}"],
		}
