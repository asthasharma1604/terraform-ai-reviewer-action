import os
import json
from config import validate_environment, CACHE_FILE
from models import ReviewResponse
from analyzer import analyze_with_openai, build_review_sections
from github_services import fetch_tf_code, post_inline_comments, update_pr_status
import sys

# Runs the AI review once and saves its structured result to the cache.
def run_analysis():
    print("Starting AI Analysis phase...", flush=True)
    # Calls the API and saves the raw JSON to a file. Runs ONLY ONCE.
    repo, openai_key, pr, plan_path = validate_environment(require_openai=True)

    tf_code_context = fetch_tf_code(repo, pr)
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
    return True

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
        review_sections = build_review_sections(review_data)
        write_output(review_sections["Dangerous Terraform Changes"], "Dangerous Terraform Changes")

    elif mode == "security":
        review_sections = build_review_sections(review_data)
        write_output(review_sections["Security Review"], "Security Review")

    elif mode == "cost":
        review_sections = build_review_sections(review_data)
        write_output(review_sections["Cost Optimization"], "Cost Optimization")

    elif mode == "architecture":
        review_sections = build_review_sections(review_data)
        write_output(review_sections["Architecture & Best Practices"], "Architecture & Best Practices")

    elif mode == "fixes":
        review_sections = build_review_sections(review_data)
        write_output(review_sections["Suggested Fixes"], "Suggested Fixes")

    elif mode == "inline":
        # Resolve the GitHub PR context once and pass it into downstream helpers so
        # inline comments and the final review verdict share the same repo/PR object.
        repo, _, pr, _ = validate_environment(require_openai=False)
        if not pr:
            print("No pull request is associated with this push. Skipping inline comments.")
            return

        post_inline_comments(load_cached_data, repo, pr)
        update_pr_status(review_data, repo, pr)
    else:
        raise ValueError(f"Unsupported mode: {mode}")

if __name__ == "__main__":
    main()