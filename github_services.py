import concurrent.futures
import os
import re
from config import MAX_REQUEST_CHARS

# Compare two commits to find which files were changed.
def compare_commits(repo, before_sha, after_sha):
    # Build the GitHub API URL for comparing the two commits
    url = f"{repo.url}/compare/{before_sha}...{after_sha}"

    # Send a request to GitHub to get the commit comparison
    _, comparison = repo._requester.requestJsonAndCheck("GET", url)

    # Return the list of files changed between the two commits
    return comparison["files"]

# Get a file property from either a dictionary or a PyGithub object.
def file_value(file, key):
    # If the file data is a dictionary, get the value using its key
    if isinstance(file, dict):
        return file.get(key)
    # Otherwise, get the value from the object's attribute
    return getattr(file, key)

# Fetch only the changed lines from Terraform (.tf) files
# in a pull request (PR) or a Git push.
# This avoids sending complete files to the AI reviewer.
def fetch_tf_code(repo, pr):

    # Get the SHA of the current commit from GitHub Actions.
    commit_sha = os.getenv("GITHUB_SHA")
    

    print(f"Connecting to GitHub repo: {repo.name}...{os.getenv("GITHUB_ACTION")}", flush=True)

    # Check whether this function is processing a pull request.
    if pr:
        # Get the latest commit SHA from the PR branch.
        revision = pr.head.sha

        # On an existing PR receiving new commits, review only the new push.
        if os.getenv("GITHUB_ACTION") == "synchronize":
            base_revision = os.getenv("GITHUB_BEFORE")

            if not base_revision or base_revision == "0" * 40:
                raise ValueError(
                    "GITHUB_BEFORE is required for PR updates."
                )

            source = f"new changes pushed to PR #{pr.number}"
        else:
            # For reopened PRs or other events, use the full PR diff.
            base_revision = pr.base.sha
            source = f"all changes in PR #{pr.number}"

        changed_files = compare_commits(
            pr.head.repo,
            base_revision,
            revision,
        )

    # If this is not a PR, check whether the workflow was triggered by a push.
    elif commit_sha:
        # Get the SHA of the commit before the push.
        # We need this to compare the old code with the new code.
        base_revision = os.getenv("GITHUB_BEFORE")

        # Stop if the previous commit SHA is missing or contains only zeros.
        # An all-zero SHA can indicate that a new branch was pushed,
        # so there may not be a usable previous commit to compare.
        if not base_revision or base_revision == "0" * 40:
            raise ValueError(
                "GITHUB_BEFORE is required to identify changed files."
            )

        # Use the current commit as the latest version of the code.
        revision = commit_sha

        # Create a readable label for logging.
        source = f"commit {commit_sha[:7]}"

        # Compare the previous commit with the current commit.
        # The result contains files changed between these two versions.
        changed_files = compare_commits(
            repo, base_revision, revision
        )

    # Stop if neither a PR nor a valid current commit is available.
    else:
        raise ValueError(
            "Neither PR nor GITHUB_SHA is available."
        )

    # Store the formatted changes for each Terraform file.
    # Each item will contain a filename and its changed lines.
    sections = []

    # Keep track of the total number of characters added.
    # This helps prevent the request from exceeding the configured limit.
    total_chars = 0

    # Examine every file returned by GitHub.
    for file in changed_files:

        # Get the current filename.
        file_name = file_value(file, "filename")

        # Get the original filename if the file was renamed.
        # This value may be None if the file was not renamed.
        previous_name = file_value(file, "previous_filename")

        # Get the type of change reported by GitHub.
        # Examples: added, modified, removed, or renamed.
        status = file_value(file, "status")

        # Check whether the current filename ends with ".tf".
        # Also check the original filename in case a Terraform file
        # was renamed to a different extension, such as ".txt".
        is_tf = (
            (file_name and file_name.endswith(".tf"))
            or (previous_name and previous_name.endswith(".tf"))
        )

        # Skip files that are not Terraform files.
        # Also skip files whose status is not one of the supported changes.
        if not is_tf or status not in {
            "added", "modified", "removed", "renamed"
        }:
            continue

        # Get the diff patch provided by GitHub.
        # A patch shows the lines added and deleted between two versions.
        # It avoids downloading the complete file contents.
        patch = file_value(file, "patch")

        # GitHub may not provide a patch for some files,
        # for example, files with very large changes or binary content.
        # Log a warning so we know that the file was not reviewed.
        if not patch:
            print(
                f"WARNING: No patch returned for {file_name}; "
                "this file is not included in the review context.",
                flush=True,
            )
            continue

        # Create a list to store only the relevant lines from the patch.
        diff_lines = []

        # Read the patch one line at a time.
        for line in patch.splitlines():

            # Keep diff hunk headers.
            # Example: @@ -10,3 +10,5 @@
            # These headers identify the approximate locations of changes.
            if line.startswith("@@"):
                diff_lines.append(line)

            # Skip GitHub's file header lines.
            # "+++" and "---" identify the new and old files; they are not actual code changes.
            elif line.startswith("+++") or line.startswith("---"):
                continue

            # Keep lines starting with "+" or "-".
            elif line.startswith("+") or line.startswith("-"):
                diff_lines.append(line)

        # Skip the file if no useful diff lines were collected.
        if not diff_lines:
            continue

        # Create a readable section for this file.
        # The filename tells the AI which file the changes belong to.
        # The status indicates whether the file was added, changed, etc.
        # The diff code block preserves the "+" and "-" change markers.
        section = (
            f"### File: {file_name} (status: {status})\n"
            f"```diff\n"
            f"{"\n".join(diff_lines)}\n"
            f"```\n"
        )

        # Add a blank line between file sections when necessary.
        separator = "\n" if sections else ""

        # Calculate how many characters this file would add,
        # including the separator before the file section.
        additional_chars = len(separator + section)

        # Check the size BEFORE adding this file to the request.
        # This prevents the collected Terraform diffs from exceeding
        # MAX_REQUEST_CHARS.
        if total_chars + additional_chars > MAX_REQUEST_CHARS:

            # Stop with an error rather than silently dropping a changed file.
            # The caller can then split the review into smaller requests.
            raise ValueError(
                f"Changed Terraform diffs exceed the "
                f"{MAX_REQUEST_CHARS:,}-character limit. "
                f"Stopped before adding {file_name}. "
                "Split the review into smaller requests."
            )

        # Add this file's diff because it fits within the size limit.
        sections.append(section)

        # Update the running character count.
        total_chars += additional_chars

    # Join all the collected file sections into one string.
    # This string will be passed to the AI reviewer.
    result = "\n".join(sections)

    # Print how many Terraform files were included
    # and how many characters the final result contains.
    print(
        f"Found {len(sections)} Terraform diff(s) in {source}; "
        f"{len(result):,} characters.",
        flush=True,
    )

    # Return the changed Terraform code to the calling function.
    return result

# Find the added lines in the PR's complete diff.
# These are the lines where GitHub can generally place new inline comments
# on the RIGHT (new-file) side of the diff.
def changed_lines_since_previous_revision(repo, pr):
    # Compare the PR's target branch with the latest PR commit.
    # This includes changes made across the PR, not just the latest push.
    base_sha = pr.base.sha
    head_sha = pr.head.sha

    # Store pairs of (filename, new-file line number).
    changed_lines = set()

    # Get the changed files and their diff patches.
    changed_files = compare_commits(repo, base_sha, head_sha)

    for changed_file in changed_files:
        # Read the filename and patch from the comparison result.
        file_name = file_value(changed_file, "filename")
        patch = file_value(changed_file, "patch") or ""

        # We will use this variable to track the current line number in the new file.
        new_line = None

        # Read the patch one line at a time.
        for patch_line in patch.splitlines():

            # Each hunk header tells us where the changes occur.
            # Example: @@ -10,2 +10,3 @@ means the new hunk starts at line 10.
            if patch_line.startswith("@@"):
                match = re.search(r"\+(\d+)(?:,\d+)?", patch_line)

                # Skip malformed hunk headers safely.
                if not match:
                    new_line = None
                    continue

                # Save the starting line number
                new_line = int(match.group(1))
                continue

            # Skip lines before the first section and special patch markers.
            if new_line is None or patch_line.startswith("\\"):
                continue

            # If a line was added, save its file name and line number.
            if patch_line.startswith("+"):
                changed_lines.add((file_name, new_line))
                # Move to the next line in the new file.
                new_line += 1

            # If a line was deleted, do not move the new file's line number.
            elif patch_line.startswith("-"):
                continue

            # For an unchanged line, move to the next line in the new file.
            else:
                new_line += 1

    return changed_lines

# Check the AI review results and decide what status to show on GitHub.
def summarize_review_status(review_data):
    total_findings = (
        len(review_data.security_issues)
        + len(review_data.cost_issues)
        + len(review_data.architecture_suggestions)
        + len(review_data.dangerous_changes)
        + len(review_data.fix_suggestions)
    )

    if total_findings == 0:
        return "APPROVE", "The Terraform AI review completed successfully. No findings were identified."

    return "REQUEST_CHANGES", "The Terraform AI review identified findings that should be addressed before this PR is merged. Please review the inline comments for details and recommended fixes."

# Update the pull request review and its commit status on GitHub.
def update_pr_status(review_data, repo, pr):
    # Decide whether to approve the PR or request changes.
    # Also get a message explaining the review result.
    review_event, review_message = summarize_review_status(review_data)

    # Set the commit status to success if the review is approved.
    # Otherwise, mark it as failure.
    status_state = "success" if review_event == "APPROVE" else "failure"

    try:
        # Submit the review to the pull request on GitHub.
        pr.create_review(event=review_event, body=review_message)
        # print(f"Submitted PR review: {review_event}", flush=True)
    except Exception as exc:
        print(f"Failed to submit PR review for {repo.full_name}#{pr.number}: {exc}", flush=True)

    try:
        # Get the latest commit of the pull request and update its status.
        repo.get_commit(pr.head.sha).create_status(
            state=status_state,
            context="terraform-ai-reviewer",
            description=(
                "Terraform AI review completed"
                if review_event == "APPROVE"
                else "Terraform AI review identified findings"
            ),
        )
    except Exception as exc:
        print(f"Failed to update PR status for {repo.full_name}#{pr.number}: {exc}", flush=True)

# Post inline review comments on GitHub and avoid posting duplicate comments.
def post_inline_comments(load_cached_data, repo, pr):
    # Get the latest commit of the pull request.
    commit_id = repo.get_commit(pr.head.sha)

    # Get the lines that changed since the previous revision.
    changed_lines = changed_lines_since_previous_revision(repo, pr)

    # Store the file names and line numbers that already have review comments.
    existing_comment_keys = set()

    # Check the review comments that already exist on this pull request.
    for comment in pr.get_review_comments():
        comment_path = getattr(comment, "path", None)

        # Get the current line number of the comment.
        comment_line = getattr(comment, "line", None)

        # If the current line is not available, use the original line number.
        if comment_line is None:
            comment_line = getattr(comment, "original_line", None)

        # Save the file name and line number if they are available.
        if comment_path and comment_line:
            existing_comment_keys.add((comment_path, int(comment_line)))

    # Load the AI review results.
    review_data = load_cached_data()

    # Converts an AI line number or range into the line used by GitHub.
    def parse_line(line_str):
        try:
            # Extract the last number from a line range (e.g., "5-10" becomes 10).
            return int(str(line_str).split("-")[-1].strip())
        except (TypeError, ValueError):
            return None

    comments_to_post  = []
    added_comments = set()

    # Add a comment only if it is valid and not a duplicate.
    def add_comment(path, line, body):
        # Skip comments with a missing file name or line number.
        if not path or not line:
            return

        # Use the file name and line number to identify the comment.
        comment_key = (path, line)

        # Do not add the same file and line more than once.
        if comment_key in added_comments:
            return

        # Skip old comments if the line has not changed.
        if comment_key in existing_comment_keys:
            if changed_lines is None or comment_key not in changed_lines:
                return

        # Remember this comment so it is not added again.
        added_comments.add(comment_key)

        # Save the comment details for posting.
        comments_to_post .append({
            "path": path, 
            "line": line, 
            "body": body
        })

    # Add comments for security issues.
    for security_issue in review_data.security_issues:
        severity_level = security_issue.severity.upper()
        message = (
            f"<strong>Security Issue ({severity_level})</strong>\n"
            f"{security_issue.issue}\n"
            f"{security_issue.description}\n"
            f"Remediation: {security_issue.remediation}"
        )
        
        add_comment(
            security_issue.file_name, 
            parse_line(security_issue.line_numbers), 
            message
        )

    # Add comments for cost optimization issues.
    for cost_issue in review_data.cost_issues:
        message = (
            f"<strong>Cost Optimization ({cost_issue.risk_level} Risk)</strong>\n"
            f"Impact: {cost_issue.estimated_impact}\n"
            f"{cost_issue.explanation}\n"
            f"Tip: {cost_issue.optimization_tip}"
        )
        add_comment(
            cost_issue.file_name, 
            parse_line(cost_issue.line_numbers), 
            message
        )

    # Add comments for architecture suggestions.
    for architecture_issue in review_data.architecture_suggestions:
        message = (
            f"<strong>Architecture Suggestion</strong>\n"
            f"{architecture_issue.component}\n"
            f"{architecture_issue.observation}\n"
            f"Recommendation: {architecture_issue.recommendation}"
        )
        add_comment(
            architecture_issue.file_name, 
            parse_line(architecture_issue.line_numbers), 
            message
        )

    # Add comments for dangerous Terraform changes.
    for dangerous_change in review_data.dangerous_changes:
        if dangerous_change.file_name and dangerous_change.line_numbers:
            message = (
                f"<strong>Dangerous Terraform Change</strong>\n"
                f"{dangerous_change.resource_name} will be {dangerous_change.action}.\n"
                f"{dangerous_change.why_it_matters}\n"
                f"Recommendation: {dangerous_change.recommendation}"
            )
            add_comment(
                dangerous_change.file_name, 
                parse_line(dangerous_change.line_numbers), 
                message
            )

    # Add comments for suggested code fixes.
    for fix_suggestion in review_data.fix_suggestions:
        message = (
            f"<strong>Suggested Fix</strong>\n"
            f"{fix_suggestion.description}\n"
            f"```hcl\n{fix_suggestion.code}\n```"
        )
        add_comment(
            fix_suggestion.file_name, 
            parse_line(fix_suggestion.line_numbers), 
            message
        )

    if not comments_to_post :
        print("No new inline comments to post.")
        return

    # Show how many comments will be posted.
    print(f"Posting {len(comments_to_post)} inline comments...")

    # Post one comment on GitHub.
    def post_comment(comment_issue):
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
            return None

    # Post comments using multiple threads.
    thread_count = int(os.getenv("THREAD_COUNT", "10"))
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=thread_count
    ) as executor:
        list(executor.map(post_comment, comments_to_post))

    print("Finished posting all concurrent comments!", flush=True)
