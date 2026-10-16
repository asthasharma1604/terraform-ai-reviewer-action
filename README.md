# Terraform AI Reviewer Action

A GitHub Action that uses OpenAI to review Terraform changes for dangerous changes, security risks, cost optimization opportunities, architecture improvements, and suggested fixes.

## The problem

Terraform pull requests can change infrastructure in ways that are difficult to assess quickly: a small code change can expose a service, weaken permissions, remove a resource, or increase ongoing costs. This Action adds an AI-generated review to help reviewers spot potential issues and consider remediations. It is an aid to human review, not a substitute for it or for tools such as `terraform validate`, `tflint`, and Checkov.

## Data Privacy and Security

This action sends Terraform source code and, when provided, Terraform plan output to OpenAI for AI-powered review.

Before using this action, ensure that your organization's security policies allow sharing infrastructure code with OpenAI.

Do not include secrets, credentials, or sensitive information in Terraform files or plan output.

## Setup

1. Add an OpenAI API key as Terraform repository or organization Actions secret named `OPENAI_API_KEY`.
2. Add a workflow to the Terraform repository. The example below runs on pull requests and passes an optional Terraform plan to the Action.
3. Replace `OWNER/REPOSITORY@REF` with this Action's repository and a tag or commit reference.

```yaml
name: Terraform AI review

on:
  pull_request:
    paths:
      - "**/*.tf"

permissions:
  contents: read
  pull-requests: write
  statuses: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Terraform
        uses: hashicorp/setup-terraform@v3
      - name: Initialize Terraform
        run: terraform init -backend=false
      - name: Generate plan
        run: |
          terraform plan -no-color -out=tfplan
          terraform show -no-color tfplan > plan.txt
      - name: Review Terraform
        uses: OWNER/REPOSITORY@REF
        with:
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          github_token: ${{ secrets.github.token }}
          plan_path: plan.txt
```

Authenticate Terraform providers before generating a plan if your configuration needs cloud credentials. The plan is optional; omit `plan_path` to review source only.

## Configuration

| Input            | Required | Description |
| ---------------- | -------- | ----------- |
| `openai_api_key` | Yes      | OpenAI API key |
| `github_token`   | Yes      | GitHub token used by GitHub Action mode to read the repository and publish a review/status |
| `plan_path`      | No       | Path to a text file containing `terraform show -no-color` output |

Terraform source and the optional plan are sent to OpenAI for analysis. Plans can include sensitive values; check your organization's data-handling requirements before enabling plan review, and do not include data you are not permitted to share.

In GitHub mode, the review sends only changed lines from `.tf` files changed in the latest PR commit or push commit. Earlier commits in an open PR are not included. The complete OpenAI request—including instructions, changed-file diffs, and optional plan—is limited to 100,000 characters. Changed-file diffs are prioritized; an oversized plan is truncated with an explicit marker to fit the remaining budget. The review stops with an error if the instructions and changed-file diffs alone exceed the limit.

For environment variable names used by the Python review script, see [`.env.example`](.env.example). The Action itself receives its secrets through its inputs; `.env.example` is a reference.

## Promised vs. built

| Capability | Status |
| ---------- | ------ |
| AI review for dangerous changes, security, cost, architecture, and fixes | Implemented |
| GitHub pull request review comments and commit status | Implemented in inline mode; requires appropriate token permissions |
| Optional Terraform plan context | Implemented when `plan_path` is supplied |

## Team

Astha Sharma - Project development, AI reviewer implementation, and prompt engineering, testing
Saloni Agrawal - prompt engineering, GitHub Actions integration and workflow automation, testing

## Development

The GitHub Action uses Node.js 24 and installs the Python dependencies from [`requirements.txt`](requirements.txt) when it runs. To rebuild its JavaScript bundle after changing `src/index.js`:

```sh
npm run build
```
