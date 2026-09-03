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
            return ReviewResponse(**json.load(f))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"Unable to load cached analysis from {CACHE_FILE}: {e}")
        sys.exit(1)

# Prints review content and appends it to the GitHub Step Summary.
def write_output(content: str, title: str):
    print(f"\n\n{content}\n\n")
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(f"<details>\n<summary>{title}</summary>\n\n{content}\n</details>\n\n")

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
    for security_issue in review_data.security_issues:
        severity_level = security_issue.severity.upper()
        severity_label = "High" if severity_level == "HIGH" else "Medium" if severity_level == "MEDIUM" else "Low"
        security_comment_body = f"### Security Issue ({severity_label} {security_issue.severity})\n**{security_issue.issue}**\n{security_issue.description}\n\n**Remediation:** {security_issue.remediation}"
        all_issues.append({"path": security_issue.file_name, "line": parse_line(security_issue.line_numbers), "body": security_comment_body})

    # Format Cost Comments
    for cost_issue in review_data.cost_issues:
        cost_comment_body = f"### Cost Optimization ({cost_issue.risk_level} Risk)\n**Impact: {cost_issue.estimated_impact}**\n{cost_issue.explanation}\n\n**Tip:** {cost_issue.optimization_tip}"
        all_issues.append({"path": cost_issue.file_name, "line": parse_line(cost_issue.line_numbers), "body": cost_comment_body})

    # Format Architecture Comments
    for architecture_issue in review_data.architecture_suggestions:
        architecture_comment_body = f"### Architecture Suggestion\n**{architecture_issue.component}**\n{architecture_issue.observation}\n\n**Recommendation:** {architecture_issue.recommendation}"
        all_issues.append({"path": architecture_issue.file_name, "line": parse_line(architecture_issue.line_numbers), "body": architecture_comment_body})

    # Format Dangerous Change Comments when the plan provides a source location.
    for dangerous_change in review_data.dangerous_changes:
        if dangerous_change.file_name and dangerous_change.line_numbers:
            dangerous_change_body = f"### Dangerous Terraform Change\n**{dangerous_change.resource_name}** will be **{dangerous_change.action}**.\n{dangerous_change.why_it_matters}\n\n**Recommendation:** {dangerous_change.recommendation}"
            all_issues.append({"path": dangerous_change.file_name, "line": parse_line(dangerous_change.line_numbers), "body": dangerous_change_body})

    # Format Code Fix Comments
    for fix_suggestion in review_data.fix_suggestions:
        fix_comment_body = f"### Suggested Fix\n{fix_suggestion.description}\n```hcl\n{fix_suggestion.code}\n```"
        all_issues.append({"path": fix_suggestion.file_name, "line": parse_line(fix_suggestion.line_numbers), "body": fix_comment_body})

    print(f"Preparing to post {len(all_issues)} inline comments...")

    # Post them to GitHub! Posts one finding to GitHub and skips findings without a line number.
    def post_single_comment(comment_issue):
        if not comment_issue.get("line"):
            return
        try:
            pr.create_review_comment(
                body=comment_issue["body"],
                commit=commit_id,
                path=comment_issue["path"],
                line=int(comment_issue["line"]),
                side="RIGHT"
            )
            print(f"Posted inline comment on {comment_issue['path']} (Line {comment_issue['line']})", flush=True)
        except Exception as e:
            print(f"Skipped {comment_issue['path']} (Line {comment_issue['line']}): {e}", flush=True)

    print(f"Firing off {len(all_issues)} comments concurrently...", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Takes every item in 'all_issues' and pass it to 'post_single_comment' simultaneously."
        executor.map(post_single_comment, all_issues)

    print("Finished posting all concurrent comments!", flush=True)

# Parses the selected action mode and produces the corresponding review output.
def main():
    # parser = argparse.ArgumentParser(description="AI Reviewer Step Executor")

    # parser.add_argument("--mode", choices=["analyze", "security", "cost", "architecture", "dangerous", "fixes", "inline"], required=True)
    # args = parser.parse_args()
    mode = sys.argv[1] if len(sys.argv) > 1 else "analyze"

    # Generate the Data
    #if args.mode == "analyze":
    if mode == "analyze":
        run_analysis()
        return

    # Extract the Data ---
    review_data = load_cached_data()

    if mode == "dangerous":
        dangerous_summary = ""
        if not review_data.dangerous_changes:
            dangerous_summary += "*Plan looks clean! No destructive changes or replacements detected.*\n"
        else:
            for change in review_data.dangerous_changes:
                dangerous_summary += f"### Potentially destructive change\n"
                dangerous_summary += f"**`{change.resource_name}`** will be **{change.action}**.\n"
                dangerous_summary += f"- **Why this matters:** {change.why_it_matters}\n"
                dangerous_summary += f"- **Recommendation:** {change.recommendation}\n\n"
        print("\n\n\n\n")
        write_output(dangerous_summary, "**Dangerous Terraform Changes**")

    elif mode == "security":
        security_summary = f"**Summary:** {review_data.summary}\n\n"
        if not review_data.security_issues:
            security_summary += "*No major security vulnerabilities found!*\n"
        else:
            for issue in review_data.security_issues:
                severity_level = issue.severity.upper()
                icon = "High" if severity_level == "HIGH" else "Medium" if severity_level == "MEDIUM" else "Low"
                security_summary += f"- **[{issue.severity}] {issue.issue}** ({issue.file_name} at **Line {issue.line_numbers}**)\n  - *Risk:* {issue.description}\n  - *Fix:* {issue.remediation}\n"
        print("\n\n\n\n")
        write_output(security_summary, "**Security Review**")

    elif mode == "cost":
        cost_summary = ""
        if not review_data.cost_issues:
            cost_summary += "*No obvious cost pitfalls detected!*\n"
        else:
            for cost in review_data.cost_issues:
                cost_summary += f"- **[{cost.risk_level} Risk] Impact: {cost.estimated_impact}** ({cost.file_name} at **Line {cost.line_numbers}**)\n  - *Why:* {cost.explanation}\n  - *Tip:* {cost.optimization_tip}\n"
        print("\n\n\n\n")
        write_output(cost_summary, "**Cost Optimization**")

    elif mode == "architecture":
        architecture_summary = ""
        if not review_data.architecture_suggestions:
            architecture_summary += "*Architecture looks solid! No major improvements suggested.*\n"
        else:
            for arch in review_data.architecture_suggestions:
                architecture_summary += f"### {arch.component} ({arch.file_name} at Lines {arch.line_numbers})\n"
                architecture_summary += f"- **Observation:** {arch.observation}\n"
                architecture_summary += f"- **Recommendation:** {arch.recommendation}\n\n"
        print("\n\n\n\n")
        write_output(architecture_summary, "**Architecture & Best Practices**")

    elif mode == "fixes":
        fixes_summary = ""
        if not review_data.fix_suggestions:
            fixes_summary += "*No immediate code replacements recommended!*\n"
        else:
            for fix in review_data.fix_suggestions:
                fixes_summary += f"### {fix.file_name} (Lines {fix.line_numbers})\n"
                fixes_summary += f"**Why:** {fix.description}\n\n"
                fixes_summary += f"```hcl\n{fix.code}\n```\n\n"
        print("\n\n\n\n")
        write_output(fixes_summary, "**Suggested Fixes**")

    elif mode == "inline":
        post_inline_comments()
    else:
        raise ValueError(f"Unsupported mode: {mode}")

if __name__ == "__main__":
    main()