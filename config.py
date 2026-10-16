import os
import sys
from pathlib import Path
from github import Auth, Github, BadCredentialsException, GithubException

# Cache file location on runner to reuse API results across steps
# Set the cache directory inside the temporary folder provided by GitHub Actions
cache_root = Path(os.getenv("RUNNER_TEMP", "/tmp")) / "terraform-ai-review"

# Get the current GitHub Actions run ID to identify this run's cache
cache_id = os.getenv("GITHUB_RUN_ID", "local")

# Build a unique JSON cache file path for the current workflow run
CACHE_FILE = cache_root / f"review_output_{cache_id}.json"
MAX_REQUEST_CHARS = 100_000

# Validates the GitHub settings and, when needed, the OpenAI settings for the action.
def validate_environment(require_openai=True):
    github_token = os.getenv("GITHUB_TOKEN")
    openai_key = os.getenv("OPENAI_API_KEY")
    pr_number = os.getenv("PR_NUMBER")
    repo_name = os.getenv("REPO_NAME")

    try:
        float(os.getenv("OPENAI_TIMEOUT", "60"))
        int(os.getenv("THREAD_COUNT", "10"))
    except ValueError:
        print("OPENAI_TIMEOUT and THREAD_COUNT must be valid integers.")
        sys.exit(1)

    if not github_token:
        print("CRITICAL ERROR: GITHUB_TOKEN is missing or empty!")
        sys.exit(1)

    if not repo_name:
        print("Repository name is missing; cannot review the commit.")
        sys.exit(1)

    if pr_number:
        try:
            pr_number = int(pr_number)
        except ValueError:
            print("CRITICAL ERROR: PR_NUMBER must be an integer!")
            sys.exit(1)

    if require_openai and not openai_key:
        print("CRITICAL ERROR: OPENAI_API_KEY is missing or empty!")
        sys.exit(1)

    plan_path = os.getenv("PLAN_PATH")

    if not plan_path:
        print("PLAN_PATH is missing. Proceeding with code review only.")

    try:
        # Connect to GitHub using the provided token
        gh = Github(auth=Auth.Token(github_token))

        # Verify access to the requested repository
        repo = gh.get_repo(repo_name)

        # Get the pull request if a PR number was provided
        pr = repo.get_pull(pr_number) if pr_number else None

    except BadCredentialsException:
        print(
            "::error::GitHub authentication failed. "
            "Check whether GITHUB_TOKEN is valid and has not expired.",
            flush=True
        )
        sys.exit(1)

    except GithubException as e:
        print(
            f"::error::GitHub API request failed (HTTP {e.status}). "
            "Check repository access and token permissions.",
            flush=True
        )
        sys.exit(1)

    except Exception as e:
        # Handle unexpected errors without printing a traceback
        print(
            f"::error::Unexpected GitHub error ({type(e).__name__}).",
            flush=True
        )
        sys.exit(1)

    return repo, openai_key, pr, plan_path