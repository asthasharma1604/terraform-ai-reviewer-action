import os
import json
from github import Github
from google import genai
from google.genai import types
from pydantic import BaseModel

# --- 1. Define the Expected Output Structure ---
class SecurityFinding(BaseModel):
    severity: str
    issue: str
    description: str
    remediation: str

class CostFinding(BaseModel):
    risk_level: str
    estimated_impact: str
    explanation: str
    optimization_tip: str

class ReviewResponse(BaseModel):
    summary: str
    security_issues: list[SecurityFinding]
    cost_issues: list[CostFinding]
    recommended_tf_code: str | None

SYSTEM_PROMPT = """
You are a Cloud Security & FinOps Expert. Analyze the provided Terraform code.
Identify security risks and cost optimization opportunities.
Provide a clear summary, security risks, cost optimization tips, and recommended code fixes.
"""

def main():
    repo_name = os.getenv("REPO_NAME")
    pr_number_str = os.getenv("PR_NUMBER")

    if not pr_number_str:
        print("Not a pull_request event. Skipping AI Review.")
        return

    pr_number = int(pr_number_str)
    
    # Initialize GitHub and Gemini Clients
    gh_client = Github(os.getenv("GITHUB_TOKEN"))
    gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    repo = gh_client.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    # --- 2. Gather Terraform Files ---
    tf_code_context = ""
    for file in pr.get_files():
        if file.filename.endswith(".tf") and file.status != "removed":
            try:
                content_file = repo.get_contents(file.filename, ref=pr.head.sha)
                raw_content = content_file.decoded_content.decode("utf-8")
                tf_code_context += f"### File: {file.filename}\n```hcl\n{raw_content}\n```\n\n"
            except Exception as e:
                print(f"Error reading file {file.filename}: {e}")

    if not tf_code_context:
        print("No Terraform files modified. Skipping review.")
        return

    # --- 3. Call Gemini API for Structured Output ---
    print("Calling Gemini API for code analysis...")
    full_prompt = f"Review this Terraform code:\n\n{tf_code_context}"
    
    response = gemini_client.models.generate_content(
        model='gemini-2.5-flash',
        contents=full_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=ReviewResponse, # Forces Gemini to return data matching our Pydantic model!
            temperature=0.2 # Lower temperature for more analytical/factual responses
        )
    )
    
    # Gemini automatically parses the JSON into our Pydantic object
    review_data = response.parsed

    # --- 4. Decide Review Action ---
    has_high_risk = any(issue.severity.upper() == "HIGH" for issue in (review_data.security_issues or []))
    review_action = "REQUEST_CHANGES" if has_high_risk else "APPROVE"

    # --- 5. Format Review Body ---
    md = f"## 🤖 Gemini AI Terraform PR Review\n\n**Summary:** {review_data.summary}\n\n"
    
    md += "### 🔒 Security Findings\n"
    if not review_data.security_issues:
        md += "✅ *No major security vulnerabilities found!*\n"
    else:
        for issue in review_data.security_issues:
            icon = "🔴" if issue.severity.upper() == "HIGH" else "🟠" if issue.severity.upper() == "MEDIUM" else "🟢"
            md += f"- {icon} **[{issue.severity}] {issue.issue}**\n  - *Risk:* {issue.description}\n  - *Fix:* {issue.remediation}\n"

    md += "\n### 💰 Cost Optimization\n"
    if not review_data.cost_issues:
        md += "✅ *No obvious cost pitfalls detected!*\n"
    else:
        for cost in review_data.cost_issues:
            md += f"- 💸 **[{cost.risk_level} Risk] Impact: {cost.estimated_impact}**\n  - *Why:* {cost.explanation}\n  - *Tip:* {cost.optimization_tip}\n"

    if review_data.recommended_tf_code:
        md += f"\n### ✨ Suggested Fixes\n```hcl\n{review_data.recommended_tf_code}\n```"

    # --- 6. Post Review to GitHub ---
    print(f"Posting {review_action} review to PR #{pr_number}...")
    pr.create_review(body=md, event=review_action)
    print("Review posted successfully!")

if __name__ == "__main__":
    main()