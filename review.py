import os
import json
import argparse
from config import validate_environment, CACHE_FILE
from models import ReviewResponse
from analyzer import fetch_tf_code, analyze_with_openai
from github import Github
import sys

def run_analysis():
    print("Starting AI Analysis phase...", flush=True)
    # Calls the API and saves the raw JSON to a file. Runs ONLY ONCE.
    github_token, openai_key, pr_number, repo_name, plan_path = validate_environment()

    tf_code_context = fetch_tf_code(repo_name, pr_number, github_token)
    if not tf_code_context:
        print("No Terraform files modified in this PR. Skipping AI analysis.", flush=True)
        return

    # Call OpenAI API
    review_data = analyze_with_openai(tf_code_context, plan_path, openai_key)

    print(f"Writing parsed analysis to cache file ({CACHE_FILE})...", flush=True)
    # Save to file so other steps can read it
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(review_data.model_dump_json())
    
    print("✅ AI Analysis complete! Data saved to cache.", flush=True)

def load_cached_data():
    """Reads the JSON from the file without calling the API."""
    if not CACHE_FILE.exists():
        print("⚠️ No cached analysis found. Assuming no Terraform changes were made.")
        sys.exit(0) # Exit peacefully

    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return ReviewResponse(**json.load(f))

def write_output(content: str):
    # Outputs text to both console logs and the GitHub Step Summary.
    print("\n=== \033[1mSTEP OUTPUT\033[0m ===\n")
    print(content)
    print("\n===================\n")
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(content + "\n\n")

def post_inline_comments():
    """Posts issues directly to the PR files as inline comments."""
    # We need the GitHub Token again to post comments
    github_token = os.getenv("GITHUB_TOKEN")
    repo_name = os.getenv("REPO_NAME")
    pr_number_str = os.getenv("PR_NUMBER")

    if not github_token or not repo_name or not pr_number_str:
        print("Missing GitHub variables. Cannot post inline comments.")
        sys.exit(0)
    
    gh = Github(github_token)
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(int(pr_number_str))
    commit_id = pr.head.sha # We attach comments to the latest commit

    review_data = load_cached_data()

    # Helper function to extract a single line number (e.g., "15-20" -> 20)
    def parse_line(line_str):
        try:
            return int(str(line_str).split('-')[-1].strip())
        except:
            return None

    all_issues = []

    # Format Security Comments
    for sec in review_data.security_issues:
        icon = "🔴" if sec.severity.upper() == "HIGH" else "🟠" if sec.severity.upper() == "MEDIUM" else "🟢"
        body = f"### 🔒 Security Issue ({icon} {sec.severity})\n**{sec.issue}**\n{sec.description}\n\n**Remediation:** {sec.remediation}"
        all_issues.append({"path": sec.file_name, "line": parse_line(sec.line_numbers), "body": body})

    # Format Cost Comments
    for cost in review_data.cost_issues:
        body = f"### 💰 Cost Optimization ({cost.risk_level} Risk)\n**Impact: {cost.estimated_impact}**\n{cost.explanation}\n\n**Tip:** {cost.optimization_tip}"
        all_issues.append({"path": cost.file_name, "line": parse_line(cost.line_numbers), "body": body})

    # Format Code Fix Comments
    for fix in review_data.fix_suggestions:
        body = f"### ✨ Suggested Fix\n{fix.description}\n```hcl\n{fix.code}\n```"
        all_issues.append({"path": fix.file_name, "line": parse_line(fix.line_numbers), "body": body})

    print(f"Preparing to post {len(all_issues)} inline comments...")

    # Post them to GitHub!
    for issue in all_issues:
        if not issue["line"]:
            continue
        try:
            pr.create_review_comment(
                body=issue["body"],
                commit_id=commit_id,
                path=issue["path"],
                line=issue["line"]
            )
            print(f"✅ Posted inline comment on {issue['path']} (Line {issue['line']})")
        except Exception as e:
            # GitHub blocks comments on lines that weren't modified in the PR diff.
            print(f"⚠️ Skipped inline comment for {issue['path']} (Line {issue['line']}): Line is not part of the PR diff.")

def main():
    parser = argparse.ArgumentParser(description="AI Reviewer Step Executor")

    parser.add_argument("--mode", choices=["analyze", "security", "cost", "architecture", "dangerous", "fixes", "inline"], required=True)
    args = parser.parse_args()

    # Generate the Data
    if args.mode == "analyze":
        run_analysis()
        return

    # Extract the Data ---
    review_data = load_cached_data()

    if args.mode == "dangerous":
        md = "## 🚨 Dangerous Terraform Changes\n\n"
        if not review_data.dangerous_changes:
            md += "✅ *Plan looks clean! No destructive changes or replacements detected.*\n"
        else:
            for change in review_data.dangerous_changes:
                md += f"### 🔴 Potentially destructive change\n"
                md += f"**`{change.resource_name}`** will be **{change.action}**.\n"
                md += f"- **Why this matters:** {change.why_it_matters}\n"
                md += f"- **Recommendation:** {change.recommendation}\n\n"
        write_output(md)

    elif args.mode == "security":
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

    elif args.mode == "architecture":
        md = "## 🏗️ Architecture & Best Practices\n\n"
        if not review_data.architecture_suggestions:
            md += "✅ *Architecture looks solid! No major improvements suggested.*\n"
        else:
            for arch in review_data.architecture_suggestions:
                md += f"### 🧩 {arch.component} (📁 `{arch.file_name}` at Lines {arch.line_numbers})\n"
                md += f"- **Observation:** {arch.observation}\n"
                md += f"- **Recommendation:** {arch.recommendation}\n\n"
        write_output(md)

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

    elif args.mode == "inline":
        post_inline_comments()

if __name__ == "__main__":
    main()