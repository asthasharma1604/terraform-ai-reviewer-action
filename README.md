# Terraform AI Reviewer Action

A GitHub Action that reviews Terraform pull requests with Google Gemini. It
downloads the Terraform files changed by the pull request, adds line numbers,
and reports security findings, cost optimization opportunities, and suggested
HCL fixes in the GitHub Actions step summary.

## What It Reviews

The action runs three review passes:

- **Security**: identifies security issues and gives each finding a severity,
  affected file and line, risk description, and remediation.
- **Cost**: identifies potential cost pitfalls and provides an estimated
  impact and optimization tip.
- **Fixes**: provides suggested Terraform code changes for the reported
	findings.

Only `.tf` files that are not removed from the pull request are analyzed. If a
pull request does not change Terraform files, the action skips the review.

## Prerequisites

- A GitHub repository with pull requests enabled.
- A Google Gemini API key.
- Python 3.10 or later (configured automatically by the action).

The action uses the GitHub token to read pull request files. The workflow must
grant the action permission to read repository contents and pull requests.

## Usage

Create a workflow such as `.github/workflows/terraform-review.yml`:

```yaml
name: Terraform AI Review

on:
	pull_request:
		types: [opened, synchronize, reopened]

permissions:
	contents: read
	pull-requests: read

jobs:
	review:
		runs-on: ubuntu-latest
		steps:
			- name: Review Terraform changes
				uses: asthasharma1604/terraform-ai-reviewer-action@main
				with:
					gemini_api_key: ${{ secrets.GEMINI_API_KEY }}
				env:
					GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

`gemini_api_key` is required. The action also reads `GITHUB_TOKEN`,
`PR_NUMBER`, and `REPO_NAME` from the workflow environment. `PR_NUMBER` and
`REPO_NAME` are populated automatically for pull request events by the action's
GitHub context.

## Outputs

The action writes review results to:

- The workflow log.
- The GitHub Actions step summary through `GITHUB_STEP_SUMMARY`.

Results are reused between the security, cost, and fixes steps through the
temporary cache file `/tmp/review_output.json`, so the Gemini API is called
once per runner execution.

## Development

The main files are:

- `action.yml`: composite action metadata and workflow steps.
- `review.py`: command-line entry point and Markdown output formatting.
- `analyzer.py`: GitHub pull request retrieval and Gemini analysis.
- `config.py`: environment validation and cache configuration.
- `models.py`: Pydantic response models used for structured Gemini output.

To run a review locally, install the action dependencies and provide the
required environment variables:

```bash
pip install PyGithub openai pydantic google-genai

export GITHUB_TOKEN=your-github-token
export GEMINI_API_KEY=your-gemini-api-key
export PR_NUMBER=123
export REPO_NAME=owner/repository

python review.py --mode security
```

Valid modes are `security`, `cost`, and `fixes`.
