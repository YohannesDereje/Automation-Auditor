from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from langchain_groq import ChatGroq
from langchain_core.globals import set_debug
from langchain_core.messages import HumanMessage

from src.state import AgentState, Evidence
from src.tools.doc_tools import DocAnalyst
from src.tools.repo_tools import RepoManager

import os
from dotenv import load_dotenv

set_debug(True)

load_dotenv() # This pulls the keys from your .env file


_DOC_ANALYST = DocAnalyst()
_REPO_MANAGER = RepoManager()


def _resolve_doc_pdf_path(repo_path: str, state_pdf_path: str) -> tuple[str, list[str]]:
	search_patterns = ["reports/final_report.pdf", "**/final_report.pdf", "reports/*.pdf", "**/*.pdf"]

	if not repo_path:
		return state_pdf_path, search_patterns

	repo_root = Path(repo_path)
	if not repo_root.exists() or not repo_root.is_dir():
		return state_pdf_path, search_patterns

	if state_pdf_path:
		candidate = Path(state_pdf_path)
		if candidate.is_absolute() and candidate.exists() and candidate.is_file():
			return str(candidate), search_patterns

		relative_candidate = repo_root / state_pdf_path
		if relative_candidate.suffix.lower() == ".pdf" and relative_candidate.exists() and relative_candidate.is_file():
			return str(relative_candidate), search_patterns

	for pattern in search_patterns:
		for file_path in repo_root.glob(pattern):
			if file_path.suffix.lower() == ".pdf" and file_path.is_file():
				return str(file_path), search_patterns

	if state_pdf_path:
		return str(repo_root / state_pdf_path), search_patterns

	return str(repo_root / "reports" / "final_report.pdf"), search_patterns


def _build_image_message(image_paths: list[str], prompt: str) -> HumanMessage:
	content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]

	for image_path in image_paths[:8]:
		path = Path(image_path)
		if not path.exists() or not path.is_file():
			continue

		mime_type, _ = mimetypes.guess_type(str(path))
		resolved_mime = mime_type or "image/png"
		encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
		content.append(
			{
				"type": "image_url",
				"image_url": {"url": f"data:{resolved_mime};base64,{encoded}"},
			}
		)

	return HumanMessage(content=content)


def doc_analyst_node(state: AgentState) -> dict[str, Any]:
	repo_path = state.get("repo_path", "")
	pdf_path = state.get("pdf_path", "")
	resolved_pdf_path, search_patterns = _resolve_doc_pdf_path(repo_path, pdf_path)

	try:
		llm = ChatGroq(model_name="llama-3.3-70b-versatile", temperature=0, verbose=True)
		loaded = _DOC_ANALYST.load_requirements(resolved_pdf_path)
		dimensions = _DOC_ANALYST.extract_rubric_dimensions()
		markdown_text = loaded.get("markdown", "")

		refinement_prompt = (
			"You are a forensic auditor for a LangGraph project. Analyze the provided "
			"architectural markdown and extract two focused sections:\n"
			"1) Scoring Rubric Rules\n"
			"2) Technical Constraints\n"
			"Return concise JSON with keys 'scoring_rubric_rules' and "
			"'technical_constraints'.\n\n"
			f"MARKDOWN:\n{markdown_text[:18000]}"
		)
		llm_response = llm.invoke(refinement_prompt)
		refined_requirements = (
			llm_response.content
			if isinstance(llm_response.content, str)
			else json.dumps(llm_response.content, ensure_ascii=False)
		)

		requirements_payload = {
			"source_pdf": resolved_pdf_path,
			"search_patterns": search_patterns,
			"objectives": dimensions.objectives,
			"deliverables": dimensions.deliverables,
			"constraints": dimensions.constraints,
			"markdown_length": len(loaded.get("markdown", "")),
			"llm_refined_requirements": refined_requirements,
		}

		evidence = Evidence(
			goal="Extract architectural requirements from PDF",
			found=True,
			content=json.dumps(requirements_payload, ensure_ascii=False),
			location=resolved_pdf_path,
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
				f"doc_analyst_node: requirements extracted from {resolved_pdf_path} using PDF patterns {search_patterns}."
			],
		}
	except FileNotFoundError as error:
		evidence = Evidence(
			goal="Extract architectural requirements from PDF",
			found=False,
			content=str(error),
			location=resolved_pdf_path or "unknown",
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
			location=resolved_pdf_path or "unknown",
			rationale="Unexpected failure while parsing project requirements PDF.",
			confidence=0.0,
		)
		return {
			"evidences": {"doc_analysis": [evidence]},
			"messages": [f"doc_analyst_node unexpected error: {error}"],
		}


def repo_investigator_node(state: AgentState) -> dict[str, Any]:
	repo_url = state.get("repo_url", "")
	state_pdf_path = state.get("pdf_path", "")

	try:
		repo_path = _REPO_MANAGER.clone_repo(repo_url)
		analysis = _REPO_MANAGER.analyze_graph_structure(repo_path)
		resolved_pdf_path, _ = _resolve_doc_pdf_path(repo_path, state_pdf_path)

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
			"pdf_path": resolved_pdf_path,
			"evidences": {"repo_analysis": [evidence]},
			"messages": [
				f"repo_investigator_node: clone and AST graph analysis completed. Resolved PDF path: {resolved_pdf_path}"
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
	repo_path = state.get("repo_path", "")
	pdf_path = state.get("pdf_path", "")
	resolved_pdf_path, _ = _resolve_doc_pdf_path(repo_path, pdf_path)

	try:
		llm = ChatGroq(model_name="meta-llama/llama-4-scout-17b-16e-instruct", temperature=0)
		image_paths = _DOC_ANALYST.extract_images_from_pdf(resolved_pdf_path)

		if image_paths:
			vision_prompt = (
				"Look at these architectural diagrams from a LangGraph project. "
				"Does the diagram show a 'Fan-In' or 'Aggregator' pattern? "
				"Respond with a brief forensic confirmation."
			)
			vision_message = _build_image_message(image_paths, vision_prompt)
			vision_response = llm.invoke([vision_message])
			vision_findings = (
				vision_response.content
				if isinstance(vision_response.content, str)
				else json.dumps(vision_response.content, ensure_ascii=False)
			)
		else:
			vision_findings = (
				"No images were found in the PDF, so Llama Vision analysis could not "
				"confirm Fan-In/Aggregator diagram patterns."
			)

		evidence_payload = {
			"image_count": len(image_paths),
			"image_paths": image_paths,
			"vision_status": "Analyzed",
			"question": "Is this a StateGraph diagram or a generic box diagram?",
			"forensic_confirmation": vision_findings,
		}

		evidence = Evidence(
			goal="Inspect architectural diagrams for StateGraph fidelity",
			found=len(image_paths) > 0,
			content=vision_findings,
			location=resolved_pdf_path,
			rationale=(
				"Images were extracted from the PDF and queued for multimodal "
				"classification using Llama-3.2-Vision on Groq for forensic signals."
			),
			confidence=0.7 if image_paths else 0.4,
		)

		return {
			"evidences": {"vision_analysis": [evidence]},
			"messages": [
				f"vision_inspector_node: image extraction and analysis completed via Llama-3.2-Vision on Groq. PDF used: {resolved_pdf_path}"
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
