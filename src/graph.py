from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.nodes.detectives import (
	doc_analyst_node,
	repo_investigator_node,
	vision_inspector_node,
)
from src.state import AgentState


workflow = StateGraph(AgentState)

workflow.add_node("doc_analyst_node", doc_analyst_node)
workflow.add_node("repo_investigator_node", repo_investigator_node)
workflow.add_node("vision_inspector_node", vision_inspector_node)

workflow.add_edge(START, "doc_analyst_node")
workflow.add_edge(START, "repo_investigator_node")
workflow.add_edge(START, "vision_inspector_node")

workflow.add_edge("doc_analyst_node", END)
workflow.add_edge("repo_investigator_node", END)
workflow.add_edge("vision_inspector_node", END)

app = workflow.compile()


if __name__ == "__main__":
	initial_state = {
		"repo_url": "https://github.com/YohannesDereje/Automation-Auditor.git",
		"pdf_path": "reports/interim_report.pdf",
		"rubric_dimensions": [],
		"evidences": {},
		"opinions": [],
		"messages": [],
	}

	final_state = app.invoke(initial_state)
	evidence_keys = list(final_state.get("evidences", {}).keys())
	print("Evidence keys:", evidence_keys)
