import os
import json
from config import validate_environment, CACHE_FILE
from models import ReviewResponse
from analyzer import analyze_with_openai
from github_services import fetch_tf_code, post_inline_comments
import sys

# Runs the AI review once and saves its structured result to the cache.
def run_analysis():
    print("Starting AI Analysis phase...", flush=True)
    # Calls the API and saves the raw JSON to a file. Runs ONLY ONCE.
    github_token, openai_key, pr_number, repo_name, plan_path = validate_environment(require_openai=True)

    tf_code_context = fetch_tf_code(repo_name, pr_number, github_token)
    if not tf_code_context:
        print("No Terraform files modified in this commit. Skipping AI analysis.", flush=True)
        empty_review = ReviewResponse(
            summary="No Terraform files modified in this commit.",
            dangerous_changes=[],
            security_issues=[],
            cost_issues=[],
            architecture_suggestions=[],
            fix_suggestions=[],
        )
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(empty_review.model_dump_json())
        return

    # Call OpenAI API
    review_data = analyze_with_openai(tf_code_context, plan_path, openai_key)

    print(
        "Note: "
        f"{review_data.summary} ("
        f"security={len(review_data.security_issues)}, "
        f"cost={len(review_data.cost_issues)}, "
        f"architecture={len(review_data.architecture_suggestions)}, "
        f"dangerous={len(review_data.dangerous_changes)}, "
        f"fixes={len(review_data.fix_suggestions)})",
        flush=True,
    )

    print(f"Writing parsed analysis to cache file ({CACHE_FILE})...", flush=True)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(review_data.model_dump_json())

    print("AI Analysis complete! Data saved to cache.", flush=True)

# Loads the cached review result, generating it when the cache is missing.
def load_cached_data():
    if not CACHE_FILE.exists():
        print("No cached analysis found. Starting AI analysis...")
        run_analysis()

    if not CACHE_FILE.exists():
        print("No Terraform files were found. Skipping review.")
        sys.exit(0)

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return ReviewResponse.model_validate(json.load(f))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"Unable to load cached analysis from {CACHE_FILE}: {e}")
        sys.exit(1)

# Prints review content and appends it to the GitHub Step Summary.
def write_output(content: str, title: str):
    print(content)
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(f"<details>\n<summary>{title}</summary>\n{content}\n</details>\n")

# Parses the selected action mode and produces the corresponding review output.
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "analyze"

    # Generate the Data
    if mode == "analyze":
        run_analysis()
        return

    # Extract the Data ---
    review_data = load_cached_data()

    if mode == "dangerous":
        dangerous_summary = ""
        if not review_data.dangerous_changes:
            dangerous_summary += "Plan looks clean! No destructive changes or replacements detected.\n"
        else:
            for change in review_data.dangerous_changes:
                dangerous_summary += f"- `{change.resource_name}`: {change.action}\n"
                dangerous_summary += f"  - Why this matters: {change.why_it_matters}\n"
                dangerous_summary += f"  - Recommendation: {change.recommendation}\n"
        write_output(dangerous_summary, "Dangerous Terraform Changes")

    elif mode == "security":
        security_summary = ""
        if not review_data.security_issues:
            security_summary += "No major security vulnerabilities found!\n"
        else:
            for issue in review_data.security_issues:
                severity_level = issue.severity.upper()
                security_summary += f"- [{severity_level}] {issue.issue} ({issue.file_name} at Line {issue.line_numbers})\n  - Risk: {issue.description}\n  - Fix: {issue.remediation}\n"
        write_output(security_summary, "Security Review")

    elif mode == "cost":
        cost_summary = ""
        if not review_data.cost_issues:
            cost_summary += "No obvious cost pitfalls detected!\n"
        else:
            for cost in review_data.cost_issues:
                cost_summary += f"- [{cost.risk_level} Risk] Impact: {cost.estimated_impact} ({cost.file_name} at Line {cost.line_numbers})\n  - Why: {cost.explanation}\n  - Tip: {cost.optimization_tip}\n"
        write_output(cost_summary, "Cost Optimization")

    elif mode == "architecture":
        architecture_summary = ""
        if not review_data.architecture_suggestions:
            architecture_summary += "Architecture looks solid! No major improvements suggested.\n"
        else:
            for arch in review_data.architecture_suggestions:
                architecture_summary += f"- {arch.component} ({arch.file_name} at lines {arch.line_numbers})\n"
                architecture_summary += f"  - Observation: {arch.observation}\n"
                architecture_summary += f"  - Recommendation: {arch.recommendation}\n"
        write_output(architecture_summary, "Architecture & Best Practices")

    elif mode == "fixes":
        fixes_summary = ""
        if not review_data.fix_suggestions:
            fixes_summary += "No immediate code replacements recommended!\n"
        else:
            for fix in review_data.fix_suggestions:
                fixes_summary += f"- {fix.file_name} (lines {fix.line_numbers})\n"
                fixes_summary += f"  - Recommendation: {fix.description}\n"
                fixes_summary += f"```hcl\n{fix.code}\n```\n"
        write_output(fixes_summary, "Suggested Fixes")

    elif mode == "inline":
        post_inline_comments(load_cached_data)
    else:
        raise ValueError(f"Unsupported mode: {mode}")

if __name__ == "__main__":
    main()