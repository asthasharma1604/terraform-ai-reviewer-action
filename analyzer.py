import os

from github import Github, Auth
from openai import OpenAI
from models import ReviewResponse
import sys

SYSTEM_PROMPT = """
You are a Cloud Security, FinOps, and Cloud Architecture Expert performing a thorough Terraform review.
You will be provided with Terraform source code and optionally the `terraform plan` output.
Review every Terraform resource and module for security risks, cost optimization opportunities,
architectural improvements, and specific code fixes.

Cost review requirements:
- Inspect instance sizes, autoscaling, storage types and sizes, retention, backups, log levels,
    network transfer, idle resources, redundancy, and missing lifecycle policies.
- Report concrete cost issues when a configuration is oversized, wasteful, unexpectedly retained,
    or likely to incur avoidable charges. Include the affected file and line when possible.

Architecture review requirements:
- Inspect reliability, availability zones, scaling, dependency design, state management, networking,
    observability, maintainability, and environment separation.
- Report concrete improvements when the configuration has a meaningful architectural weakness or
    a clear best-practice gap. Include the affected file and line when possible.

Do not leave cost_issues or architecture_suggestions empty merely because the code is syntactically
valid or because no critical vulnerability exists. Empty lists are appropriate only after explicitly
checking the categories above and finding no actionable recommendation.

If `terraform plan` output is provided, aggressively analyze it to detect DANGEROUS CHANGES such as:
- Resource destruction (destroy)
- Resource replacement (replace)
- Potential downtime or data loss
- Permission changes or network exposure

For code issues, specify `file_name` and `line_numbers`.
For dangerous changes, specify `file_name` and `line_numbers` when the plan identifies a source location.
Provide a clear summary, security risks, cost optimization tips, architecture best practices, dangerous plan changes, and code fixes.

When summarizing the terraform plan, strictly classify actions using these emojis: Create (🟢), Update (🟡), Replace (🟠), and Destroy (🔴).
"""

# Fetches changed Terraform files from a PR or pushed commit and adds line numbers.
def fetch_tf_code(repo_name, pr_number, token):
    commit_sha = os.getenv("GITHUB_SHA")
    print(f"Connecting to GitHub repo: {repo_name}...", flush=True)
    auth = Auth.Token(token)
    gh_client = Github(auth=auth)

    repo = gh_client.get_repo(repo_name)
    if pr_number:
        pr = repo.get_pull(pr_number)
        revision = pr.head.sha
        files = list(pr.get_files())
        source = f"PR #{pr_number}"
    elif commit_sha:
        revision = commit_sha
        files = list(repo.get_commit(commit_sha).files)
        source = f"commit {commit_sha}"
    else:
        print("❌ Neither PR_NUMBER nor GITHUB_SHA is available.", flush=True)
        return ""

    tf_code_context = ""
    print(f"Found {len(files)} total changed file(s) in {source}.", flush=True)

    for file in files:
        if file.filename.endswith(".tf") and file.status != "removed":
            try:
                content_file = repo.get_contents(file.filename, ref=revision)
                raw_content = content_file.decoded_content.decode("utf-8")
                
                # Inject line numbers
                lines = raw_content.split('\n')
                numbered_code = "\n".join([f"{i+1}: {line}" for i, line in enumerate(lines)])
                
                tf_code_context += f"### File: {file.filename}\n```hcl\n{numbered_code}\n```\n\n"
            except Exception as e:
                print(f"Error reading file {file.filename}: {e}")

    return tf_code_context

# Sends Terraform code and an optional plan to OpenAI for a structured review.
def analyze_with_openai(tf_code_context, plan_path, openai_key):
    plan_context = ""
    if plan_path and os.path.exists(plan_path):
        print(f"Reading Terraform plan from: {plan_path}...", flush=True)
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_text = f.read()
            print(f"Plan file loaded ({len(plan_text)} chars).", flush=True)
            plan_context = f"\n\n### Terraform Plan Output:\n```text\n{plan_text}\n```\n"
    else:
        print("No plan file found or path provided. Proceeding with code review only.", flush=True)

    full_prompt = f"Review this Terraform code:\n\n{tf_code_context}{plan_context}"

    print("Sending prompt to OpenAI (gpt-4o-mini)...", flush=True)

    try:
        print("Initializing OpenAI client with 60s timeout...", flush=True)
        # Sends the formatted code to OpenAI and returns structured JSON.
        openai_client = OpenAI(api_key=openai_key, timeout=60.0)
        response = openai_client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": full_prompt}
            ],
            response_format=ReviewResponse,
            temperature=0.2
        )
        parsed_response = response.choices[0].message.parsed
        if parsed_response is None:
            raise ValueError("OpenAI returned an empty structured response")
        print("✅ OpenAI response received successfully!", flush=True)
        return parsed_response
    except Exception as e:
        print(f"❌ OpenAI API Call Failed: {e}", flush=True)
        sys.exit(1)