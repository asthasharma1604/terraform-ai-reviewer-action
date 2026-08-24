import concurrent.futures
import os
import re
from github import Auth, Github
from config import validate_environment

# Fetches a commit comparison through PyGithub's authenticated REST requester.
def compare_commits(repo, before_sha, after_sha):
    url = f"{repo.url}/compare/{before_sha}...{after_sha}"
    _, comparison = repo._requester.requestJsonAndCheck("GET", url)
    return comparison["files"]

# Reads a file property from either a PyGithub object or REST response data.
def file_value(file, key):
    return file.get(key) if isinstance(file, dict) else getattr(file, key)

# Fetches the Terraform files changed in the current or previous revision.
def fetch_tf_code(repo_name, pr_number, token):
    commit_sha = os.getenv("GITHUB_SHA")
    before_sha = os.getenv("GITHUB_BEFORE")
    print(f"Connecting to GitHub repo: {repo_name}...", flush=True)
    auth = Auth.Token(token)
    gh_client = Github(auth=auth)

    repo = gh_client.get_repo(repo_name)
    if pr_number:
        pr = repo.get_pull(pr_number)
        revision = pr.head.sha
        # A PR with no existing inline review comments must be reviewed in full,
        # even when the event includes a before SHA from an earlier commit.
        has_previous_review = any(
            comment.path and comment.line
            for comment in pr.get_review_comments()
        )
        # Compare revisions after the first run so analysis covers only the new push.
        if has_previous_review and before_sha and before_sha != revision and before_sha != "0" * 40:
            files = compare_commits(repo, before_sha, revision)
            source = f"PR #{pr_number} changes since {before_sha[:7]}"
        else:
            files = list(pr.get_files())
            source = f"PR #{pr_number}"
    elif commit_sha:
        revision = commit_sha
        # Push events provide the previous SHA in the event payload.
        if before_sha and before_sha != revision and before_sha != "0" * 40:
            files = compare_commits(repo, before_sha, revision)
            source = f"commit {commit_sha} changes since {before_sha[:7]}"
        else:
            files = list(repo.get_commit(commit_sha).files)
            source = f"commit {commit_sha}"
    else:
        print("Neither PR_NUMBER nor GITHUB_SHA is available.", flush=True)
        return ""

    tf_code_context = ""
    print(f"Found {len(files)} total changed file(s) in {source}.", flush=True)

    for file in files:
        file_name = file_value(file, "filename")
        if file_name.endswith(".tf") and file_value(file, "status") != "removed":
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

# Builds and posts deduplicated inline comments for the cached review findings.
def post_inline_comments(load_cached_data):
    github_token, _, pr_number, repo_name, _ = validate_environment(require_openai=False)

    if not pr_number:
        print("No pull request is associated with this push. Skipping inline comments.")
        return

    gh = Github(auth=Auth.Token(github_token))
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    commit_id = repo.get_commit(pr.head.sha)
    changed_lines = changed_lines_since_previous_revision(repo, pr)

    existing_comment_keys = {
        (comment.path, comment.line)
        for comment in pr.get_review_comments()
        if comment.path and comment.line
    }

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
        # Repeat a finding only when this push changed the previously commented line.
        old_comment_is_unchanged = changed_lines is None or key not in changed_lines
        if key in issue_keys or (key in existing_comment_keys and old_comment_is_unchanged):
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
