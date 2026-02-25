import operator
from typing import Annotated, Dict, List, Literal, Optional, Any

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

# --- Detective Output ---

class Evidence(BaseModel):
    """
    Represents a single piece of forensic evidence gathered by a detective agent.
    Includes structured data, location markers, and a normalized confidence score.
    """
    goal: str = Field(description="The specific audit requirement being investigated")
    found: bool = Field(description="Whether the specific artifact or logic was identified")
    content: Optional[Any] = Field(default=None, description="The raw data extracted (Markdown, AST snippet, etc.)")
    location: str = Field(description="File path, line number, or commit hash where evidence exists")
    rationale: str = Field(description="Detailed logic explaining why this evidence supports the find")
    confidence: float = Field(
        ge=0, le=1, 
        description="Confidence score from 0.0 (uncertain) to 1.0 (verified fact)"
    )

# --- Judge Output ---

class JudicialOpinion(BaseModel):
    """
    Structured critique from a specialized judge (Prosecutor/Defense) 
    regarding a specific rubric dimension.
    """
    judge: Literal["Prosecutor", "Defense", "TechLead"]
    criterion_id: str = Field(description="The unique ID of the rubric dimension being scored")
    score: int = Field(ge=1, le=5, description="Numerical score assigned by the judge")
    argument: str = Field(description="The dialectical reasoning behind the score")
    cited_evidence: List[str] = Field(description="List of evidence IDs or goal names referenced")

# --- Chief Justice Output ---

class CriterionResult(BaseModel):
    """The final synthesized result for a single rubric dimension."""
    dimension_id: str
    dimension_name: str
    final_score: int = Field(ge=1, le=5)
    judge_opinions: List[JudicialOpinion]
    dissent_summary: Optional[str] = Field(
        default=None,
        description="Summary of conflicting views, required when judge score variance > 2"
    )
    remediation: str = Field(description="Actionable instructions to fix identified gaps")

class AuditReport(BaseModel):
    """The final end-state object of the LangGraph audit workflow."""
    repo_url: str
    executive_summary: str
    overall_score: float
    criteria: List[CriterionResult]
    remediation_plan: str

# --- Graph State ---

class AgentState(TypedDict):
    """
    The global state of the LangGraph swarm. 
    Utilizes reducers for parallel detective execution and evidence aggregation.
    """
    repo_url: str
    repo_path: str
    pdf_path: str
    rubric_dimensions: List[Dict]
    
    # operator.ior: Merges dictionaries from parallel detectives without overwriting
    evidences: Annotated[
        Dict[str, List[Evidence]], operator.ior
    ]
    
    # operator.add: Appends opinions and logs to a growing list for the final audit trail
    opinions: Annotated[
        List[JudicialOpinion], operator.add
    ]
    messages: Annotated[list, operator.add]
    
    final_report: Optional[AuditReport]