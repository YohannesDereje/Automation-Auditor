# Automaton Auditor

Automaton Auditor is a forensic, multi-agent governance swarm built with LangGraph. It audits a target GitHub repository against an architectural report using AST-based structural checks, document analysis, and evidence aggregation.

## Requirements

- Python 3.12+
- `uv` for dependency and environment management

## Installation

```bash
git clone https://github.com/YohannesDereje/Automation-Auditor.git
cd Automation-Auditor
uv sync
```

Optional LLM provider integrations:

```bash
uv sync --extra llm
```

## Configuration

Create and populate `.env` with your keys:

```env
OPENAI_API_KEY=your_key_here
GOOGLE_API_KEY=your_key_here
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_key_here
```

## Parallel Execution

The current graph uses a Fan-Out/Fan-In design:

- **Fan-Out (`START -> Detectives`)**: `doc_analyst_node`, `repo_investigator_node`, and `vision_inspector_node` execute in parallel.
- **Shared State Merge**:
	- `evidences` uses `operator.ior` to merge branch outputs safely.
	- `opinions` and `messages` use `operator.add` to append without overwriting.
- **Fan-In (`Detectives -> END`)**: branch results converge so you can inspect combined evidence before judicial synthesis.

This architecture ensures one slow or failing branch does not corrupt state from other branches.

## Run

```bash
uv run python -m src.graph
```

Run against a specific repository/PDF without editing code:

```bash
uv run python -m src.graph -- --repo-url https://github.com/owner/repo.git --pdf-path reports/final_report.pdf
```

`--pdf-path` is resolved inside the cloned target repository (not your local workspace).

## Project Structure

```text
src/
	state.py              # Typed state + Pydantic evidence/opinion/report schemas
	graph.py              # LangGraph workflow wiring
	nodes/
		detectives.py       # Detective node implementations
	tools/
		repo_tools.py       # Sandboxed clone, git forensics, AST analysis
		doc_tools.py        # Docling parsing, section forensics, PDF image extraction
reports/
pyproject.toml
```

## Troubleshooting (Windows)

### Docling and symlink issues

Docling or downstream tooling may require symlink support on Windows. If you see permission or symlink-related failures:

1. Enable **Developer Mode**:
	 - `Settings -> Privacy & security -> For developers -> Developer Mode`.
2. Restart your terminal/editor after enabling it.
3. Re-run dependency sync:

```bash
uv sync
```

### Common checks

- Confirm Python version:

```bash
python --version
```

- Ensure environment packages are present:

```bash
uv pip list
```

- If PDF parsing fails, verify the file exists and path is correct in your initial graph state (`pdf_path`).


## uv run python -m src.graph -- --repo-url https://github.com/ephrata1888/automation-auditor.git --pdf-path reports/final_report.pdf