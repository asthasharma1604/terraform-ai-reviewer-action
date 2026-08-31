# Terraform AI Reviewer Action

A GitHub Action that reviews Terraform changes with OpenAI. It fetches the
current contents of changed Terraform files, adds line numbers, and reports
security findings, cost optimization opportunities, and suggested HCL fixes in
the GitHub Actions step summary.

## What It Reviews

The action runs three review passes:

- **Security**: identifies security issues and gives each finding a severity,
  affected file and line, risk description, and remediation.
- **Cost**: identifies potential cost pitfalls and provides an estimated
  impact and optimization tip.
- **Fixes**: provides suggested Terraform code changes for the reported
  findings.

Only changed `.tf` files that are not removed are analyzed. For pull request
events, the action reviews their complete contents at the pull request head;
for push events, it reviews the complete contents at the pushed commit. If the
change does not include Terraform files, the action skips the review.

## Prerequisites

- A GitHub repository.
- An OpenAI API key.
- Python 3.10 (configured automatically by the action).

The action uses the GitHub token to read changed files through the GitHub API.
No checkout step is required. The workflow must grant the action permission to
read repository contents. Add `pull-requests: write` when using a
`pull_request` trigger and posting inline comments.

## Usage

Create a workflow such as `.github/workflows/terraform-review.yml`:

```yaml
name: Terraform AI Review

on:
  push:
    branches: [main]
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - name: Review Terraform changes
        uses: asthasharma1604/terraform-ai-reviewer-action@main
        with:
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

Both `openai_api_key` and `github_token` are required inputs. The action maps
these inputs to `OPENAI_API_KEY` and `GITHUB_TOKEN`. It uses `PR_NUMBER` for
pull request events and `GITHUB_SHA` for push events. Inline comments are
skipped for pushes because there is no pull request review to attach them to.

## Outputs

The action writes review results to:

- The workflow log.
- The GitHub Actions step summary through `GITHUB_STEP_SUMMARY`.

Results are reused between the review steps through a cache file in the
runner's temporary directory. The cache is keyed by the GitHub workflow run,
so concurrent or later runs do not reuse another run's analysis.

## Development

The main files are:

- `action.yml`: composite action metadata and workflow steps.
- `review.py`: command-line entry point and Markdown output formatting.
- `analyzer.py`: GitHub pull request retrieval and OpenAI analysis.
- `config.py`: environment validation and cache configuration.
- `models.py`: Pydantic response models used for structured OpenAI output.

To run a review locally, install the action dependencies and provide the
required environment variables:

```bash
pip install PyGithub openai pydantic

export GITHUB_TOKEN=your-github-token
export OPENAI_API_KEY=your-openai-api-key
export PR_NUMBER=123
export REPO_NAME=owner/repository

python review.py --mode security
```

Valid modes are `analyze`, `dangerous`, `security`, `cost`, `architecture`,
`fixes`, and `inline`.
