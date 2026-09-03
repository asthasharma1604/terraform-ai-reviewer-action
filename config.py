import os
import sys
from pathlib import Path

# Cache file location on runner to reuse API results across steps
cache_root = Path(os.getenv("RUNNER_TEMP", "/tmp")) / "terraform-ai-review"
cache_id = os.getenv("GITHUB_RUN_ID", "local")
CACHE_FILE = cache_root / f"review_output_{cache_id}.json"

# Validates the GitHub settings and, when needed, the OpenAI settings for the action.
def validate_environment(require_openai=True):
    github_token = os.getenv("GITHUB_TOKEN")
    openai_key = os.getenv("OPENAI_API_KEY")
    pr_number = os.getenv("PR_NUMBER")
    repo_name = os.getenv("REPO_NAME")

    if not github_token:
        print("CRITICAL ERROR: GITHUB_TOKEN is missing or empty!")
        sys.exit(1)

    if not repo_name:
        print("Repository name is missing; cannot review the commit.")
        sys.exit(0)

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

    return github_token, openai_key, pr_number, repo_name, plan_path