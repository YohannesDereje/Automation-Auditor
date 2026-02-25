🤖 Automaton Auditor
The Automaton Auditor is a high-fidelity, multi-agent forensic swarm built with LangGraph. It is designed to bridge the gap between architectural intent and technical implementation by conducting parallel code and document audits.

🚀 Overview
Unlike standard static analysis tools, the Automaton Auditor treats a repository as a forensic scene. It utilizes a Parallel Fan-Out architecture to simultaneously:

Analyze Code Structure: Uses Python's Abstract Syntax Tree (AST) to verify LangGraph implementation details.

Parse Requirements: Utilizes AI-powered document conversion to extract constraints and objectives from architectural PDFs.

Inspect Visual Evidence: Extracts and queues architectural diagrams for multimodal classification.

🏛️ Architectural Pillars
1. State Governance
The system utilizes a strictly typed AgentState powered by Pydantic. To enable a robust parallel swarm, we implement custom state reducers:

operator.ior: Merges evidence dictionaries from concurrent nodes without data loss.

operator.add: Aggregates judicial opinions and system messages into a continuous audit trail.

2. Forensic Detectives
RepoInvestigator: Performs sandboxed git cloning and structural AST analysis to identify StateGraph nodes and edges.

DocAnalyst: Uses Docling to transform PDF documentation into structured Markdown for high-context section querying.

VisionInspector: Uses PyMuPDF for forensic image extraction from architectural reports.

🛠️ Tech Stack
Orchestration: LangGraph

Code Analysis: Python AST & GitPython

Document Intelligence: Docling (AI Layout Analysis)

Dependency Management: uv

📦 Installation & Setup
Clone the Repository:

Bash
git clone https://github.com/YohannesDereje/Automation-Auditor.git
cd Automation-Auditor
Install Dependencies:
Ensure you have uv installed.

Bash
uv sync
Configure Environment:

Bash
cp .env.example .env
# Open .env and add your OPENAI_API_KEY
🔍 How to Run
To execute the parallel detective swarm against the target repository and local architectural report:

Bash
uv run python -m src.graph
📂 Project Structure
Plaintext
├── src/
│   ├── tools/          # Forensic tools (AST, Git, Docling)
│   ├── nodes/          # LangGraph node implementations
│   ├── state.py        # Pydantic schema and state reducers
│   └── graph.py        # Workflow orchestration (Parallel Fan-Out)
├── reports/            # Project requirements and interim reports
├── .env.example        # Environment variable template
└── pyproject.toml      # Project dependencies