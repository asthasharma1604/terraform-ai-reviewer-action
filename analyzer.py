import os
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

When summarizing the terraform plan, classify actions using these labels: Create, Update, Replace, and Destroy.

Writing requirements:
- Use short, plain-language sentences that are understandable to someone who is not a Terraform expert.
- Return each actionable finding as a separate item in the appropriate list; do not combine unrelated findings.
- Make every finding specific: explain what is wrong, why it matters, and what the user should do next.
- Keep field values concise and avoid repeating the field name inside the value.
"""

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
        print("OpenAI response received successfully!", flush=True)
        return parsed_response
    except Exception as e:
        print(f"OpenAI API Call Failed: {e}", flush=True)
        sys.exit(1)