from pydantic import BaseModel

class SecurityFinding(BaseModel):
    file_name: str
    line_numbers: str
    severity: str
    issue: str
    description: str
    remediation: str

class CostFinding(BaseModel):
    file_name: str
    line_numbers: str
    risk_level: str
    estimated_impact: str
    explanation: str
    optimization_tip: str

class ArchitectureFinding(BaseModel):
    file_name: str
    line_numbers: str
    component: str
    observation: str
    recommendation: str

class DangerousChange(BaseModel):
    resource_name: str
    action: str
    why_it_matters: str
    recommendation: str

class FixSuggestion(BaseModel):
    file_name: str
    line_numbers: str
    description: str
    code: str

class ReviewResponse(BaseModel):
    summary: str
    security_issues: list[SecurityFinding]
    cost_issues: list[CostFinding]
    recommended_tf_code: str | None
    architecture_suggestions: list[ArchitectureFinding]
    fix_suggestions: list[FixSuggestion]