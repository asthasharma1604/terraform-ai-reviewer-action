import os
import sys
from pathlib import Path

# Cache file location on runner to reuse API results across steps
CACHE_FILE = Path("/tmp/review_output.json")

def validate_environment():
    # Checks for required API keys, token etc. before executing any other code.    
    github_token = os.getenv("GITHUB_TOKEN")
    gemini_key = os.getenv("GEMINI_API_KEY")
    pr_number = os.getenv("PR_NUMBER")
    repo_name = os.getenv("REPO_NAME")

    if not github_token:
        print("❌ CRITICAL ERROR: GITHUB_TOKEN is missing or empty!")
        sys.exit(1)

    if not gemini_key:
        print("❌ CRITICAL ERROR: GEMINI_API_KEY is missing or empty!")
        sys.exit(1)

    if not pr_number or not repo_name:
        print("⚠️ Not running on a Pull Request. Missing PR_NUMBER or REPO_NAME.")
        sys.exit(0)

    return github_token, gemini_key, int(pr_number), repo_name