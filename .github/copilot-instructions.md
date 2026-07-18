# GitHub Copilot Instructions

This repository is a collection of GitHub Actions workflows that implement
an autonomous AI engineering team. When editing files here, keep the
following context in mind.

## Repository Purpose

The system provides three AI agents — **Quinn (QA Engineer)**,
**Morgan (Project Manager)**, and **Alex (Product Owner)** — plus a
**Council Moderator (Casey)** that facilitates multi-agent discussions.
All agents are powered by GitHub Models and live entirely in GitHub Actions.

## Key Files

| File | Purpose |
|------|---------|
| `.github/agents.md` | Agent personas, responsibilities, triggers — the source of truth for all agent behaviour |
| `.github/skills.md` | Skills catalog — discrete capabilities agents can use |
| `.github/agent-config.yml` | Central configuration knobs; runtime values come from GitHub repository variables |
| `.github/actions/call-github-model/action.yml` | Reusable composite action for all GitHub Models API calls |
| `.github/actions/post-council-results/action.yml` | Composite action for posting council decisions to Discussions or Issues |
| `.github/workflows/qa-engineer.yml` | QA Engineer agent workflow |
| `.github/workflows/project-manager.yml` | Project Manager agent workflow |
| `.github/workflows/product-owner.yml` | Product Owner agent workflow |
| `.github/workflows/council-discussion.yml` | Multi-agent council discussion workflow |
| `.github/workflows/agent-router.yml` | Routes slash-commands from issue/PR comments |

## Conventions

- **System prompts** come from the persona sections in `.github/agents.md`.
  Keep them consistent when modifying agent behaviour.
- **All GitHub Models API calls** go through
  `.github/actions/call-github-model`. Do not call the API directly in
  workflow steps.
- **Configuration** is via repository variables (not hardcoded values).
  See `.github/agent-config.yml` for the full list.
- **Outputs** follow the pattern: PR comments for PR-triggered workflows,
  GitHub Issues for findings that need tracking, and GitHub Discussions for
  strategic reports and council decisions.
- **Labels** used by the system: `bug`, `qa-review`, `security`,
  `feature`, `product-owner`, `priority: critical`, `priority: high`,
  `priority: medium`, `priority: low`, `blocked`, `council-review`,
  `council-decision`, `needs-qa`.
- **Workflow permissions** are declared explicitly per-workflow using the
  `permissions:` key with least-privilege principle.
- **Slash-commands** (`/qa`, `/pm`, `/po`, `/council`) in issue or PR
  comments are routed by `agent-router.yml`.

## Adding a New Agent

1. Add the agent's persona to `.github/agents.md`.
2. Add any new skills to `.github/skills.md`.
3. Create `.github/workflows/<agent-id>.yml` following the patterns in
   the existing workflow files.
4. Optionally register a slash-command in
   `.github/workflows/agent-router.yml`.

## Model Access

Workflows use `secrets.MODELS_TOKEN` (falling back to
`secrets.GITHUB_TOKEN`) to authenticate with GitHub Models at
`https://models.inference.ai.azure.com`. Ensure the token has the
`models:read` scope.
