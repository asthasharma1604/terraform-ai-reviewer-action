# Terraform AI Reviewer Action

A GitHub Action that reviews Terraform changes with OpenAI. It fetches the current contents of changed Terraform files, adds line numbers, optionally reads a Terraform plan, and reports AI analysis, dangerous changes, security findings, cost optimization opportunities, architecture suggestions, HCL fixes, and inline pull request comments.

## What It Reviews

The action runs seven review passes:

- **AI Analysis**: analyzes the changed Terraform files and prepares findings
  for the specialized review passes.
- **Dangerous Changes**: identifies Terraform changes that could cause
  destructive or high-impact infrastructure behavior.
- **Security**: identifies security issues and gives each finding a severity,
  affected file and line, risk description, and remediation.
- **Cost**: identifies potential cost pitfalls and provides an estimated
  impact and optimization tip.
- **Architecture**: suggests improvements to the Terraform architecture and
  resource organization.
- **Fixes**: provides suggested Terraform code changes for the reported
  findings.
- **Inline Comments**: posts applicable findings as inline pull request
  comments.

Only changed `.tf` files that are not removed are analyzed. For pull request events, the action reviews their complete contents at the pull request head; for push events, it reviews the complete contents at the pushed commit. If the change does not include Terraform files, the action skips the review.

The `analyze` mode calls OpenAI once and stores the structured result in a temporary cache on the runner. The remaining report modes reuse that result, so they do not make additional OpenAI requests. The `inline` mode posts security, cost, architecture, dangerous-change, and fix findings to the pull request when a source file and line are available.

## Prerequisites

- A GitHub repository.
- An OpenAI API key.
- Python 3 with the `venv` module available on the runner.

The action uses the GitHub token to read changed files through the GitHub API.
No checkout step is required. The action creates an isolated virtual
environment in the runner's temporary directory and installs the packages
listed in `requirements.txt`. The workflow must grant the action permission to read repository contents. Add `pull-requests: write` when using a
`pull_request` trigger and posting inline comments.

## Usage

Create a workflow such as `.github/workflows/terraform-review.yml`:

```yaml
name: Terraform AI Review

on:
  push:
    branches: 
      - '**'
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
          # Optional: path to a generated Terraform plan on the runner.
          plan_path: terraform.plan
```

Both `openai_api_key` and `github_token` are required inputs. The action maps these inputs to `OPENAI_API_KEY` and `GITHUB_TOKEN`. The optional `plan_path` input supplies Terraform plan output for dangerous-change analysis. It uses `PR_NUMBER` for pull request events and `GITHUB_SHA` for push events. Inline comments are skipped for pushes because there is no pull request review to
attach them to.

## Outputs

The action writes review results to:

- The workflow log.
- The GitHub Actions step summary through `GITHUB_STEP_SUMMARY`.

Results are reused between the review modes through a JSON cache file in the runner's temporary directory. The cache is keyed by the GitHub workflow run, so concurrent or later runs do not reuse another run's analysis.

## Development

The main files are:

- `action.yml`: JavaScript action metadata and inputs.
- `src/index.js`: action entry point, dependency setup, and review-mode runner.
- `dist/index.js`: bundled JavaScript executed by GitHub Actions.
- `review.py`: command-line entry point and Markdown output formatting.
- `analyzer.py`: GitHub pull request retrieval and OpenAI analysis.
- `config.py`: environment validation and cache configuration.
- `models.py`: Pydantic response models used for structured OpenAI output.

To run the Python review code locally, install the action dependencies and provide the required environment variables:

```bash
pip install PyGithub openai pydantic

export GITHUB_TOKEN=your-github-token
export OPENAI_API_KEY=your-openai-api-key
export PR_NUMBER=123
export REPO_NAME=owner/repository

python review.py --mode security
```

Valid modes are **analyze**, **dangerous**, **security**, **cost**,
**architecture**, **fixes**, and **inline**. The JavaScript action runs them in that order automatically.
