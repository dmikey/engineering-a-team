---
description: Assign the highest-priority backlog issue to GitHub Copilot
on:
  workflow_dispatch:
    inputs:
      issue_number:
        description: Issue number to assign
        required: true
      priority:
        description: Priority selected by backlog grooming
        required: false
permissions:
  contents: read
  issues: read
  pull-requests: read
tools:
  github:
    toolsets: [issues]
safe-outputs:
  assign-to-agent:
    name: "copilot"
    allowed: [copilot]
    max: 1
    target: "*"
    github-token: ${{ secrets.GH_USER_PAT }}
    ignore-if-error: true
  noop:
---

# Assign Top Priority Agent

Read issue #${{ inputs.issue_number }} in ${{ github.repository }}.

Assign GitHub Copilot to exactly that issue using the `assign-to-agent` safe
output.

Use `${{ inputs.priority }}` only as context from backlog grooming. Do not act
on any other issue or pull request.

If the issue is closed, missing, already inappropriate for Copilot, or the
assignment cannot be completed safely, call `noop` with a brief explanation.
