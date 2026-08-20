import os
import json
import argparse
from config import validate_environment, CACHE_FILE
from models import ReviewResponse
from analyzer import fetch_tf_code, analyze_with_gemini

def fetch_and_analyze():
    # Checks cache first; if empty, fetches code and calls Gemini API.
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return ReviewResponse(**json.load(f))

    # Validate Environment
    github_token, gemini_key, pr_number, repo_name = validate_environment()

    # Fetch Code
    tf_code_context = fetch_tf_code(repo_name, pr_number, github_token)
    if not tf_code_context:
        print("No Terraform files modified. Skipping review.")
        return None

    # Analyze with AI
    review_data = analyze_with_gemini(tf_code_context, gemini_key)

    # Save to Cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(review_data.model_dump_json())

    return review_data

def write_output(content: str):
    # Outputs text to both console logs and the GitHub Step Summary.
    print("\n=== \033[1mSTEP OUTPUT\033[0m ===\n")
    print(content)
    print("\n===================\n")
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(content + "\n\n")

def main():
    parser = argparse.ArgumentParser(description="AI Reviewer Step Executor")
    parser.add_argument("--mode", choices=["security", "cost", "fixes"], required=True)
    args = parser.parse_args()

    # Get data (either from API or Cache)
    review_data = fetch_and_analyze()
    if not review_data:
        return

    # Format based on the requested step
    if args.mode == "security":
        md = f"## 🔒 Security Review\n\n**Summary:** {review_data.summary}\n\n"
        if not review_data.security_issues:
            md += "✅ *No major security vulnerabilities found!*\n"
        else:
            for issue in review_data.security_issues:
                icon = "🔴" if issue.severity.upper() == "HIGH" else "🟠" if issue.severity.upper() == "MEDIUM" else "🟢"
                md += f"- {icon} **[{issue.severity}] {issue.issue}** (📁 `{issue.file_name}` at **Line {issue.line_numbers}**)\n  - *Risk:* {issue.description}\n  - *Fix:* {issue.remediation}\n"
        write_output(md)

    elif args.mode == "cost":
        md = "## 💰 Cost Optimization\n\n"
        if not review_data.cost_issues:
            md += "✅ *No obvious cost pitfalls detected!*\n"
        else:
            for cost in review_data.cost_issues:
                md += f"- 💸 **[{cost.risk_level} Risk] Impact: {cost.estimated_impact}** (📁 `{cost.file_name}` at **Line {cost.line_numbers}**)\n  - *Why:* {cost.explanation}\n  - *Tip:* {cost.optimization_tip}\n"
        write_output(md)

    # elif args.mode == "fixes":
    #     md = "## ✨ Suggested Fixes\n\n"
    #     if review_data.recommended_tf_code:
    #         md += f"```hcl\n{review_data.recommended_tf_code}\n```"
    #     else:
    #         md += "✅ *No immediate code replacements recommended!*"
    #     write_output(md)

    elif args.mode == "fixes":
        md = "## ✨ Suggested Fixes\n\n"
        if not review_data.fix_suggestions:
            md += "✅ *No immediate code replacements recommended!*\n"
        else:
            for fix in review_data.fix_suggestions:
                md += f"### 📁 `{fix.file_name}` (Lines {fix.line_numbers})\n"
                md += f"**Why:** {fix.description}\n\n"
                md += f"```hcl\n{fix.code}\n```\n\n"
        write_output(md)

if __name__ == "__main__":
    main()