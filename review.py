import os
import json
import argparse
from config import validate_environment, CACHE_FILE
from models import ReviewResponse
from analyzer import fetch_tf_code, analyze_with_openai
from github import Auth, Github
import sys
import concurrent.futures

# Runs the AI review once and saves its structured result to the cache.
def run_analysis():
    print("Starting AI Analysis phase...", flush=True)
    # Calls the API and saves the raw JSON to a file. Runs ONLY ONCE.
    github_token, openai_key, pr_number, repo_name, plan_path = validate_environment(require_openai=True)

    tf_code_context = fetch_tf_code(repo_name, pr_number, github_token)
    if not tf_code_context:
        print("No Terraform files modified in this commit. Skipping AI analysis.", flush=True)
        return

    # Call OpenAI API
    review_data = analyze_with_openai(tf_code_context, plan_path, openai_key)

    print(
        "Findings generated: "
        f"security={len(review_data.security_issues)}, "
        f"cost={len(review_data.cost_issues)}, "
        f"architecture={len(review_data.architecture_suggestions)}, "
        f"dangerous={len(review_data.dangerous_changes)}, "
        f"fixes={len(review_data.fix_suggestions)}",
        flush=True,
    )

    print(f"Writing parsed analysis to cache file ({CACHE_FILE})...", flush=True)
    # Save to file so other steps can read it
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(review_data.model_dump_json())
    
    print("✅ AI Analysis complete! Data saved to cache.", flush=True)

# Loads the cached review result without calling the OpenAI API.
def load_cached_data():
    if not CACHE_FILE.exists():
        print("⚠️ No cached analysis found. Assuming no Terraform changes were made.")
        sys.exit(0) # Exit peacefully

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return ReviewResponse(**json.load(f))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"❌ Unable to load cached analysis from {CACHE_FILE}: {e}")
        sys.exit(1)

# Prints review content and appends it to the GitHub Step Summary.
def write_output(content: str):
    print("\n=== \033[1mSTEP OUTPUT\033[0m ===\n")
    print(content)
    print("\n===================\n")
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(content + "\n\n")

# Posts cached security, cost, and fix findings as PR inline comments.
def post_inline_comments():
    github_token, _, pr_number, repo_name, _ = validate_environment(require_openai=False)

    if not pr_number:
        print("No pull request is associated with this push. Skipping inline comments.")
        return

    gh = Github(auth=Auth.Token(github_token))
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    commit_id = repo.get_commit(pr.head.sha) # We attach comments to the latest commit

    review_data = load_cached_data()

    # Helper function to extract a single line number (e.g., "15-20" -> 20)
    # Converts a reported line or line range to its final line number.
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

    # Format Architecture Comments
    for arch in review_data.architecture_suggestions:
        body = f"### 🏗️ Architecture Suggestion\n**{arch.component}**\n{arch.observation}\n\n**Recommendation:** {arch.recommendation}"
        all_issues.append({"path": arch.file_name, "line": parse_line(arch.line_numbers), "body": body})

    # Format Dangerous Change Comments when the plan provides a source location.
    for change in review_data.dangerous_changes:
        if change.file_name and change.line_numbers:
            body = f"### 🚨 Dangerous Terraform Change\n**{change.resource_name}** will be **{change.action}**.\n{change.why_it_matters}\n\n**Recommendation:** {change.recommendation}"
            all_issues.append({"path": change.file_name, "line": parse_line(change.line_numbers), "body": body})

    # Format Code Fix Comments
    for fix in review_data.fix_suggestions:
        body = f"### ✨ Suggested Fix\n{fix.description}\n```hcl\n{fix.code}\n```"
        all_issues.append({"path": fix.file_name, "line": parse_line(fix.line_numbers), "body": body})

    print(f"Preparing to post {len(all_issues)} inline comments...")

    # Post them to GitHub! Posts one finding to GitHub and skips findings without a line number.
    def post_single_comment(issue):
        if not issue.get("line"):
            return
        try:
            pr.create_review_comment(
                body=issue["body"],
                commit=commit_id,
                path=issue["path"],
                line=int(issue["line"]),
                side="RIGHT"
            )
            print(f"✅ Posted inline comment on {issue['path']} (Line {issue['line']})", flush=True)
        except Exception as e:
            print(f"⚠️ Skipped {issue['path']} (Line {issue['line']}): {e}", flush=True)

    print(f"🚀 Firing off {len(all_issues)} comments concurrently...", flush=True)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Takes every item in 'all_issues' and pass it to 'post_single_comment' simultaneously."
        executor.map(post_single_comment, all_issues)
        
    print("✅ Finished posting all concurrent comments!", flush=True)
    
# Parses the selected action mode and produces the corresponding review output.
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