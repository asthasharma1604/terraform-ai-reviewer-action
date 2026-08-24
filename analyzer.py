import os
from openai import OpenAI
from models import ReviewResponse
import sys

SYSTEM_PROMPT = """
You are a Cloud Security, FinOps, and Cloud Architecture Expert performing a thorough Terraform review.

You will be provided with Terraform source code and optionally the `terraform plan` output.

Review every Terraform resource and module for:
- Security risks
- Cost optimization opportunities
- Architecture issues
- Dangerous plan changes
- Concrete code issues and fixes

IMPORTANT — HIGH PRECISION REVIEW:
Your goal is to identify real, actionable issues, not to maximize the number of comments. Only report a finding when there is concrete evidence in the provided Terraform or plan that the configuration is problematic.

EXPLICIT CONTEXT ONLY (CRITICAL):
Do not guess or assume the application's traffic, budget, or environment tier. You may ONLY base your review on requirements that are explicitly stated in the provided code (e.g., via tags, variables, or naming conventions like `environment = "production"`). 
- If the environment or requirements are NOT explicitly defined, you MUST assume that all resource sizes, capacities, and retention periods are 100% correct, intentional, and fit for purpose.
- NEVER flag an instance size (e.g., t3.micro, t3.small) as "too small" or "too expensive" unless it directly contradicts an explicitly stated requirement in the code.
- NEVER recommend autoscaling, multi-AZ, or load balancing unless the explicitly stated environment (e.g., "prod") strictly requires it and the current code breaks functionality.

SECURITY EXCEPTION: 
The Zero-Knowledge assumption does NOT apply to security vulnerabilities. You MUST ALWAYS report concrete security risks (e.g., publicly exposed databases, missing encryption, hardcoded secrets, or disabled IMDSv2), regardless of the environment.

A finding must contain:
1. Concrete evidence
2. The actual impact
3. A specific actionable recommendation
If you cannot provide all three without speculation, do not report the finding.

Cost review:
- Inspect for concrete and avoidable waste (e.g., unattached resources, explicitly missing lifecycle policies).
- Apply the Zero-Knowledge Assumption: Do not flag resource sizing, retention, or capacity.

Architecture review:
- Inspect for meaningful architectural weaknesses (e.g., poor state management, hardcoded availability zones that break scalability, dependency loops).
- A missing best practice is a finding only when it provides a clear benefit for this specific configuration.

Security review:
- Report concrete security risks such as public exposure, excessive permissions, hardcoded secrets, missing encryption, insecure network rules, or unsafe authentication/authorization.
- Do not report optional security hardening as a vulnerability.

Dangerous plan changes:
If `terraform plan` is provided, aggressively identify:
- Resource destruction or replacement
- Potential downtime or data loss
- Permission changes or network exposure
- Removal of encryption or backups
Classify plan actions as: Create, Update, Replace, Destroy.

For code issues, specify `file_name` and `line_numbers`.
For dangerous changes, specify `file_name` and `line_numbers` when available.

When a previously reported issue has been corrected, do not report it again.
If no concrete issues exist in a category, DO NOT output anything for that category. Never invent a finding just to provide feedback.

Writing requirements:
- Use short, plain-language sentences.
- Return each actionable finding as a separate item (e.g., `[SEVERITY] Issue description (file at Line X)` followed by `- Risk:` and `- Fix:`).
- Explain what is wrong, why it matters, and what the user should do.
- Keep field values concise.
- Avoid vague statements such as "may increase costs" unless the provided evidence demonstrates that this is actually relevant.

CLEAN REVIEW BEHAVIOR (CRITICAL RULE):
If you evaluate the provided code and plan, and you find ZERO concrete, actionable issues that violate the rules above, you MUST return an entirely empty response. 
Do not output a single word, greeting, summary, or confirmation. If the code is clean and follows the explicitly stated context, your output must be absolutely blank.
"""

# Sends Terraform code and an optional plan to OpenAI for a structured review.
def analyze_with_openai(tf_code_context, plan_path, openai_key):
    plan_context = ""
    if plan_path and os.path.exists(plan_path):
        print(f"Reading Terraform plan from: {plan_path}...", flush=True)
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_text = f.read()
            print(f"Plan file loaded ({len(plan_text)} chars).", flush=True)
            plan_context = f"\n\n### Terraform Plan Output:\n```text\n{plan_text}\n```\n"
    else:
        print("No plan file found or path provided. Proceeding with code review only.", flush=True)

    full_prompt = f"Review this Terraform code:\n\n{tf_code_context}{plan_context}"

    print("Sending prompt to OpenAI (gpt-4o-mini)...", flush=True)

    try:
        print("Initializing OpenAI client with 60s timeout...", flush=True)
        # Sends the formatted code to OpenAI and returns structured JSON.
        openai_client = OpenAI(api_key=openai_key, timeout=60.0)
        response = openai_client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": full_prompt}
            ],
            response_format=ReviewResponse,
            temperature=0.2
        )
        parsed_response = response.choices[0].message.parsed
        if parsed_response is None:
            raise ValueError("OpenAI returned an empty structured response")
        print("OpenAI response received successfully!", flush=True)
        return parsed_response
    except Exception as e:
        print(f"OpenAI API Call Failed: {e}", flush=True)
        sys.exit(1)

def build_review_sections(review_data):
    sections = {}

    dangerous_summary = ""
    if review_data.dangerous_changes and len(review_data.dangerous_changes) > 0:
        for change in review_data.dangerous_changes:
            dangerous_summary += f"- `{change.resource_name}`: {change.action}\n"
            dangerous_summary += f"  - Why this matters: {change.why_it_matters}\n"
            dangerous_summary += f"  - Recommendation: {change.recommendation}\n"
    else:
        dangerous_summary += "No dangerous changes detected."
    
    sections["Dangerous Terraform Changes"] = dangerous_summary

    security_summary = ""
    if review_data.security_issues and len(review_data.security_issues) > 0:
        for issue in review_data.security_issues:
            severity_level = issue.severity.upper()
            security_summary += f"- [{severity_level}] {issue.issue} ({issue.file_name} at Line {issue.line_numbers})\n  - Risk: {issue.description}\n  - Fix: {issue.remediation}\n"
    else:
        security_summary += "No security issues detected."

    sections["Security Review"] = security_summary

    cost_summary = ""
    if review_data.cost_issues and len(review_data.cost_issues) > 0:
        for cost in review_data.cost_issues:
            cost_summary += f"- [{cost.risk_level} Risk] Impact: {cost.estimated_impact} ({cost.file_name} at Line {cost.line_numbers})\n  - Why: {cost.explanation}\n  - Tip: {cost.optimization_tip}\n"
    else:
        cost_summary += "No cost optimization issues detected."

    sections["Cost Optimization"] = cost_summary

    architecture_summary = ""
    if review_data.architecture_suggestions and len(review_data.architecture_suggestions) > 0:
        for arch in review_data.architecture_suggestions:
            architecture_summary += f"- {arch.component} ({arch.file_name} at lines {arch.line_numbers})\n"
            architecture_summary += f"  - Observation: {arch.observation}\n"
            architecture_summary += f"  - Recommendation: {arch.recommendation}\n"
    else:
        architecture_summary += "No architecture or best practice issues detected."

    sections["Architecture & Best Practices"] = architecture_summary

    fixes_summary = ""
    if review_data.fix_suggestions and len(review_data.fix_suggestions) > 0:
        for fix in review_data.fix_suggestions:
            fixes_summary += f"- {fix.file_name} (lines {fix.line_numbers})\n"
            fixes_summary += f"  - Recommendation: {fix.description}\n"
            fixes_summary += f"```hcl\n{fix.code}\n```\n"
    else:
        fixes_summary += "No code fixes suggested."

    sections["Suggested Fixes"] = fixes_summary

    return sections