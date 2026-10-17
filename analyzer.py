import os
from openai import (
    OpenAI,
    AuthenticationError,
    APITimeoutError,
    APIError,
)
from config import MAX_REQUEST_CHARS
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
Act as an AWS FinOps engineer. Inspect every resource change in the supplied Terraform plan. You MUST identify cost increases and potential savings, including context-dependent opportunities.
Explicitly evaluate:
 - EC2 and RDS instance type upgrades or downgrades.
 - NAT gateways and other newly created billable resources.
 - Auto Scaling capacity changes.
 - Storage size, logging retention, and managed service costs.
For each finding, report resource address, before/after values, cost impact (Increase, Decrease, or Needs Validation), reason, and recommendation.
Do not return "No cost optimization issues detected" if a resource is upsized or a new billable resource is added without evaluating its cost impact. Do not invent dollar estimates. If no findings exist, explain why.

Use only the supplied Terraform plan. Do not report issues from files or tools not provided.
Terraform plan:
{{PLAN}}

Architecture review:
Act as a senior AWS Solutions Architect. Analyze the supplied Terraform plan and identify architectural risks and improvement opportunities.
MANDATORY CHECKS:
 - Detect reduced availability when Multi-AZ changes from true to false.
 - Detect reduced resilience when Auto Scaling minimum, maximum, or desired capacity decreases.
 - Detect database replacements, weakened backup configurations, and potential downtime.
 - Evaluate newly created or removed NAT gateways, load balancers, and networking resources for availability and design implications.
 - Identify potential single points of failure, scalability limitations, and reliability regressions.
For every finding, return:
 - Risk level: High, Medium, or Low.
 - Resource address.
 - Exact change detected.
 - Architectural impact.
 - Recommended action.
 - Confidence: High, Medium, or Low.

Return both risks and positive architecture changes. Do not invent missing infrastructure or assume every change is incorrect. If more context is needed, report the finding as "Needs Validation" instead of omitting it.
Do not return an empty result when the listed changes have architectural implications. If no findings are generated, explain why each mandatory check produced no finding.

Use only the supplied Terraform plan.
Terraform plan:
{{PLAN}}

Security review:
Analyze the Terraform plan for security risks, including public exposure, overly permissive IAM policies, open security groups, disabled encryption, exposed secrets, and weakened security controls. Report each finding with severity, affected resource, risk, and recommended fix. Avoid false positives and do not expose secret values.

Dangerous plan changes:
Analyze the Terraform plan for dangerous changes, including resource deletion or replacement, production data loss, disabled backups, removal of deletion protection, and potential downtime. Report severity, affected resource, potential impact, and mitigation. Distinguish actual risks from possibilities.

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
    print(f"Analyzing Terraform code:\n{tf_code_context}", flush=True)
    plan_context = ""
    # Check whether a plan path is provided and the file exists
    if plan_path and os.path.exists(plan_path):
        print(f"Reading Terraform plan from: {plan_path}...", flush=True)

        # Add formatting around the plan output
        plan_header = "\n\n### Terraform Plan Output:\n```text\n"
        plan_footer = "\n```\n"

        # Add instructions before the Terraform code in the prompt
        user_prompt_prefix = "Review this Terraform code:\n\n"

        # Calculate how many characters are available for the plan
        # after reserving space for the instructions and Terraform source   
        remaining_plan_chars = (
            MAX_REQUEST_CHARS
            - len(SYSTEM_PROMPT)
            - len(user_prompt_prefix)
            - len(tf_code_context)
            - len(plan_header)
            - len(plan_footer)
        )

        # Stop if the Terraform source and instructions already exceed the limit
        if remaining_plan_chars < 0:
            raise ValueError(
                f"Terraform source exceeds the {MAX_REQUEST_CHARS:,}-character "
                "OpenAI request limit."
            )
        
        # Read only the allowed number of characters plus one extra
        # to check whether the plan exceeds the limit
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_text = f.read(remaining_plan_chars + 1)

            # Stop if the plan is too large to fit in the request
            if len(plan_text) > remaining_plan_chars:
                raise ValueError(
                    f"Terraform source and plan exceed the {MAX_REQUEST_CHARS:,}-character "
                    "OpenAI request limit; reduce the changed-file set or plan size."
                )
            
            # Log the number of characters read from the plan file
            print(f"Plan file loaded ({len(plan_text)} chars).", flush=True)
            
            # Add the plan output to the review context
            plan_context = f"{plan_header}{plan_text}{plan_footer}"
    else:
        # Continue with code review only if no plan file is available
        print(
            "No plan file found or path provided. "
            "Proceeding with code review only.",
            flush=True
        )

    # Combine the Terraform source code and plan into one prompt
    full_prompt = f"Review this Terraform code:\n\n{tf_code_context}{plan_context}"
    
    # Calculate the total number of characters in the request
    request_chars = len(SYSTEM_PROMPT) + len(full_prompt)
    
    # Stop if the request exceeds the allowed character limit
    if request_chars > MAX_REQUEST_CHARS:
        raise ValueError(
            f"Terraform source and plan exceed the {MAX_REQUEST_CHARS:,}-character "
            "OpenAI request limit; reduce the changed-file set or plan size."
        )

    print("Sending prompt to OpenAI (gpt-4o-mini)...", flush=True)

    try:
        print("Initializing OpenAI client with 60s timeout...", flush=True)

        # Read settings provided by the GitHub Action
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        openai_timeout = float(os.getenv("OPENAI_TIMEOUT", "60"))

        # Create the OpenAI client
        openai_client = OpenAI(
            api_key=openai_key,
            timeout=openai_timeout
        )

        # Sends the formatted code to OpenAI and returns structured JSON.
        response = openai_client.beta.chat.completions.parse(
            model=openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": full_prompt}
            ],
            response_format=ReviewResponse,
            temperature=0.2
        )
        # Get the structured review result
        parsed_response = response.choices[0].message.parsed

        # Stop if OpenAI did not return a valid structured response
        if parsed_response is None:
            raise ValueError("OpenAI returned an empty structured response")
        
        print("OpenAI response received successfully!", flush=True)
        return parsed_response

    except AuthenticationError:
        print(
            "::error::OpenAI authentication failed. "
            "Please check your OPENAI_API_KEY.",
            flush=True
        )
        sys.exit(1)

    except APITimeoutError:
        print(
            "::error::OpenAI request timed out after 60 seconds. "
            "Please try again or increase the timeout.",
            flush=True
        )
        sys.exit(1)

    except APIError as e:
        # Show the error type without exposing the full API error details
        print(
            f"::error::OpenAI API request failed ({type(e).__name__}).",
            flush=True
        )
        sys.exit(1)

    except ValueError as e:
        print(f"::error::{e}", flush=True)
        sys.exit(1)

    except Exception as e:
        # Handle unexpected errors without printing a traceback
        print(
            f"::error::Unexpected review error ({type(e).__name__}).",
            flush=True
        )
        sys.exit(1)

def build_review_sections(review_data):
    # Store all review sections in a dictionary
    sections = {}

    # Build a summary of dangerous Terraform changes
    dangerous_summary = ""
    if review_data.dangerous_changes and len(review_data.dangerous_changes) > 0:
        # Add details for each dangerous change
        for change in review_data.dangerous_changes:
            dangerous_summary += f"- `{change.resource_name}`: {change.action}\n"
            dangerous_summary += f"  - Why this matters: {change.why_it_matters}\n"
            dangerous_summary += f"  - Recommendation: {change.recommendation}\n"
    else:
        dangerous_summary += "No dangerous changes detected."
    
    sections["Dangerous Terraform Changes"] = dangerous_summary

    # Build a summary of security issues
    security_summary = ""
    if review_data.security_issues and len(review_data.security_issues) > 0:
        # Add severity, location, risk, and fix for each issue
        for issue in review_data.security_issues:
            severity_level = issue.severity.upper()
            security_summary += f"- [{severity_level}] {issue.issue} ({issue.file_name} at Line {issue.line_numbers})\n  - Risk: {issue.description}\n  - Fix: {issue.remediation}\n"
    else:
        security_summary += "No security issues detected."

    sections["Security Review"] = security_summary

    # Build a summary of cost optimization issues
    cost_summary = ""
    if review_data.cost_issues and len(review_data.cost_issues) > 0:
        # Add the estimated impact and optimization advice for each issue
        for cost in review_data.cost_issues:
            cost_summary += f"- [{cost.risk_level} Risk] Impact: {cost.estimated_impact} ({cost.file_name} at Line {cost.line_numbers})\n  - Why: {cost.explanation}\n  - Tip: {cost.optimization_tip}\n"
    else:
        cost_summary += "No cost optimization issues detected."

    sections["Cost Optimization"] = cost_summary

    # Build a summary of architecture and best-practice suggestions
    architecture_summary = ""
    if review_data.architecture_suggestions and len(review_data.architecture_suggestions) > 0:
        # Add observations and recommendations for each suggestion
        for arch in review_data.architecture_suggestions:
            architecture_summary += f"- {arch.component} ({arch.file_name} at lines {arch.line_numbers})\n"
            architecture_summary += f"  - Observation: {arch.observation}\n"
            architecture_summary += f"  - Recommendation: {arch.recommendation}\n"
    else:
        architecture_summary += "No architecture or best practice issues detected."

    sections["Architecture & Best Practices"] = architecture_summary

    # Build a summary of suggested code fixes
    fixes_summary = ""
    if review_data.fix_suggestions and len(review_data.fix_suggestions) > 0:
        # Add the file location, recommendation, and example code for each fix
        for fix in review_data.fix_suggestions:
            fixes_summary += f"- {fix.file_name} (lines {fix.line_numbers})\n"
            fixes_summary += f"  - Recommendation: {fix.description}\n"
            fixes_summary += f"```hcl\n{fix.code}\n```\n"
    else:
        fixes_summary += "No code fixes suggested."

    sections["Suggested Fixes"] = fixes_summary

    return sections