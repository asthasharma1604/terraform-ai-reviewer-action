import concurrent.futures
import os
import re

# Fetches a commit comparison through PyGithub's authenticated REST requester.
def compare_commits(repo, before_sha, after_sha):
    url = f"{repo.url}/compare/{before_sha}...{after_sha}"
    _, comparison = repo._requester.requestJsonAndCheck("GET", url)
    return comparison["files"]

# Reads a file property from either a PyGithub object or REST response data.
def file_value(file, key):
    return file.get(key) if isinstance(file, dict) else getattr(file, key)

# Fetches all Terraform files from the repository at the reviewed revision.
def fetch_tf_code(repo, pr):
    commit_sha = os.getenv("GITHUB_SHA")
    print(f"Connecting to GitHub repo: {repo.name}...", flush=True)
    
    if pr:
        revision = pr.head.sha
        source = f"PR #{pr.number} at {revision[:7]}"
    elif commit_sha:
        revision = commit_sha
        source = f"commit {commit_sha[:7]}"
    else:
        print("Neither PR_NUMBER nor GITHUB_SHA is available.", flush=True)
        return ""

    # Analysis uses the complete repository tree so a PR changing a non-Terraform
    # file can still receive findings from Terraform files already in the project.
    files = [
        file
        for file in repo.get_git_tree(revision, recursive=True).tree
        if file.type == "blob" and file.path.endswith(".tf")
    ]

    tf_code_context = ""
    print(f"Found {len(files)} Terraform file(s) in {source}.", flush=True)

    for file in files:
        file_name = file.path
        try:
            content_file = repo.get_contents(file_name, ref=revision)
            raw_content = content_file.decoded_content.decode("utf-8")
            lines = raw_content.split("\n")
            numbered_code = "\n".join([f"{i + 1}: {line}" for i, line in enumerate(lines)])
            tf_code_context += f"### File: {file_name}\n```hcl\n{numbered_code}\n```\n\n"
        except Exception as e:
            print(f"Error reading file {file_name}: {e}")

    return tf_code_context

# Returns the file and line pairs changed since the previous revision.
def changed_lines_since_previous_revision(repo, pr):
    before_sha = os.getenv("GITHUB_BEFORE")
    # Without an event SHA, the exact previously reviewed revision is unknown.
    # Return None so existing comments remain suppressed rather than guessing.
    if not before_sha or before_sha == pr.head.sha or before_sha == "0" * 40:
        return None

    # Store new-side lines from the patch; deleted lines have no current line to comment on.
    changed_lines = set()
    changed_files = compare_commits(repo, before_sha, pr.head.sha)
    for changed_file in changed_files:
        patch = changed_file.get("patch") or ""
        new_line = None
        for patch_line in patch.splitlines():
            hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", patch_line)
            if hunk:
                new_line = int(hunk.group(1))
                continue
            if new_line is None or patch_line.startswith("\\"):
                continue
            if patch_line.startswith("+"):
                changed_lines.add((file_value(changed_file, "filename"), new_line))
                new_line += 1
            elif patch_line.startswith("-"):
                continue
            else:
                new_line += 1

    return changed_lines

# Converts AI findings into a GitHub review action and a compatibility status message.
def summarize_review_status(review_data):
    total_findings = (
        len(review_data.security_issues)
        + len(review_data.cost_issues)
        + len(review_data.architecture_suggestions)
        + len(review_data.dangerous_changes)
        + len(review_data.fix_suggestions)
    )

    if total_findings == 0:
        return "APPROVE", "No Terraform issues found by AI review."

    return "REQUEST_CHANGES", f"{total_findings} Terraform review finding(s) detected by AI review."

# Updates the PR review decision and keeps the commit status in sync.
def update_pr_status(review_data, repo, pr):
    review_event, description = summarize_review_status(review_data)
    status_state = "success" if review_event == "APPROVE" else "failure"

    try:
        pr.create_review(
            event=review_event,
            body=description,
        )
        print(f"Submitted PR review: {review_event} - {description}", flush=True)
    except Exception as exc:
        print(f"Failed to submit PR review for {repo.full_name}#{pr.number}: {exc}", flush=True)

    try:
        repo.get_commit(pr.head.sha).create_status(
            state=status_state,
            context="terraform-ai-reviewer",
            description=description,
        )
    except Exception as exc:
        print(f"Failed to update PR status for {repo.full_name}#{pr.number}: {exc}", flush=True)

# Builds and posts deduplicated inline comments for the cached review findings.
def post_inline_comments(load_cached_data, repo, pr):
    commit_id = repo.get_commit(pr.head.sha)
    changed_lines = changed_lines_since_previous_revision(repo, pr)

    # GitHub may expose the current line or only the original line for older comments, so normalize both forms into the same deduplication key.
    existing_comment_keys = set()
    for comment in pr.get_review_comments():
        comment_path = getattr(comment, "path", None)
        comment_line = getattr(comment, "line", None) or getattr(comment, "original_line", None)
        if comment_path and comment_line:
            existing_comment_keys.add((comment_path, int(comment_line)))

    review_data = load_cached_data()

    # Converts an AI line number or range into the line used by GitHub.
    def parse_line(line_str):
        try:
            return int(str(line_str).split("-")[-1].strip())
        except (TypeError, ValueError):
            return None

    all_issues = []
    issue_keys = set()

    # Adds a finding unless it is duplicated or already reported on unchanged code.
    def add_issue(path, line, body):
        if not path or not line:
            return
        key = (path, line)
        # Never repeat a finding on an unchanged line. If the exact line changed,
        # allow a fresh suggestion for the new revision.
        if key in issue_keys:
            return
        # A matching comment is reusable until this exact path/line changes.
        if key in existing_comment_keys and (changed_lines is None or key not in changed_lines):
            return
        issue_keys.add(key)
        all_issues.append({"path": path, "line": line, "body": body})

    for security_issue in review_data.security_issues:
        severity_level = security_issue.severity.upper()
        body = f"<strong>Security Issue ({severity_level})</strong>\n{security_issue.issue}\n{security_issue.description}\nRemediation: {security_issue.remediation}"
        add_issue(security_issue.file_name, parse_line(security_issue.line_numbers), body)

    for cost_issue in review_data.cost_issues:
        body = f"<strong>Cost Optimization ({cost_issue.risk_level} Risk)</strong>\nImpact: {cost_issue.estimated_impact}\n{cost_issue.explanation}\nTip: {cost_issue.optimization_tip}"
        add_issue(cost_issue.file_name, parse_line(cost_issue.line_numbers), body)

    for architecture_issue in review_data.architecture_suggestions:
        body = f"<strong>Architecture Suggestion</strong>\n{architecture_issue.component}\n{architecture_issue.observation}\nRecommendation: {architecture_issue.recommendation}"
        add_issue(architecture_issue.file_name, parse_line(architecture_issue.line_numbers), body)

    for dangerous_change in review_data.dangerous_changes:
        if dangerous_change.file_name and dangerous_change.line_numbers:
            body = f"<strong>Dangerous Terraform Change</strong>\n{dangerous_change.resource_name} will be {dangerous_change.action}.\n{dangerous_change.why_it_matters}\nRecommendation: {dangerous_change.recommendation}"
            add_issue(dangerous_change.file_name, parse_line(dangerous_change.line_numbers), body)

    for fix_suggestion in review_data.fix_suggestions:
        body = f"<strong>Suggested Fix</strong>\n{fix_suggestion.description}\n```hcl\n{fix_suggestion.code}\n```"
        add_issue(fix_suggestion.file_name, parse_line(fix_suggestion.line_numbers), body)

    if not all_issues:
        print("No inline issues found. Skipping inline comments.")
        return

    print(f"Preparing to post {len(all_issues)} inline comments...")

    # Posts one prepared comment and keeps one failed request from stopping others.
    def post_single_comment(comment_issue):
        try:
            pr.create_review_comment(
                body=comment_issue["body"],
                commit=commit_id,
                path=comment_issue["path"],
                line=int(comment_issue["line"]),
                side="RIGHT",
            )
            print(f"Posted inline comment on {comment_issue['path']} (Line {comment_issue['line']})", flush=True)
        except Exception as e:
            print(f"Skipped {comment_issue['path']} (Line {comment_issue['line']}): {e}", flush=True)

    print(f"Firing off {len(all_issues)} comments concurrently...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(post_single_comment, all_issues)

    print("Finished posting all concurrent comments!", flush=True)
