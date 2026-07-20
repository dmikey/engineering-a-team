# Configuration Guide

This guide explains how to configure the Engineering Team agents for your
repository.

---

## Prerequisites

1. **GitHub Actions** must be enabled for your repository.
2. **GitHub Models** access — a PAT with `models:read` scope, stored as the
   `MODELS_TOKEN` secret (optional; falls back to `GITHUB_TOKEN`).
3. **Labels** created in your repository (see [Quick Start](./README.md)).
4. **Discussions enabled** (optional but recommended) for council decisions and
   weekly reports.

---

## Authentication

### MODELS_TOKEN (recommended)

Create a GitHub Personal Access Token with the `models:read` scope and store it
as a repository secret named `MODELS_TOKEN`.

```
Settings → Secrets and variables → Secrets → New repository secret
Name:  MODELS_TOKEN
Value: <your PAT>
```

If `MODELS_TOKEN` is not set, workflows fall back to the built-in
`GITHUB_TOKEN`. Note that `GITHUB_TOKEN` may have limited access to GitHub
Models depending on your plan.

The model-calling workflows in this repository request `models: read`
permission explicitly. If GitHub Models access is still denied, provide a
`MODELS_TOKEN` secret instead of relying on `GITHUB_TOKEN`.

### GH_USER_PAT (for Copilot agent workflow dispatch)

To dispatch the dedicated Copilot assignment workflow and authorize its
`assign-to-agent` safe output, create a user token (PAT or OAuth)
and store it as a repository secret named `GH_USER_PAT`.

```
Settings → Secrets and variables → Secrets → New repository secret
Name:  GH_USER_PAT
Value: <your user PAT or OAuth token>
```

The Project Manager workflow dispatches `assign-top-priority-agent.lock.yml`
with the repository `GITHUB_TOKEN`, which requires `actions: write` permission
on that workflow. The dedicated assignment workflow then uses `GH_USER_PAT` in
its `assign-to-agent` safe output. GitHub App installation tokens are not
accepted for the Copilot assignment itself, so `GH_USER_PAT` is still required
for that path.

---

## Repository Variables

All agent behaviour can be customised through repository variables without
editing any workflow YAML.

```
Settings → Secrets and variables → Variables → New repository variable
```

### Global Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_MODEL` | `gpt-4o-mini` | Default model for QA, PM, and PO agents |
| `COUNCIL_MODEL` | `gpt-4o` | Model for the Council Moderator (higher capability recommended) |
| `AGENT_MAX_TOKENS` | `2048` | Maximum tokens per model response |
| `AGENT_TEMPERATURE` | `0.7` | Sampling temperature (0.0 = deterministic, 1.0 = creative) |
| `AGENT_DEFAULT_COMMUNICATION_METHOD` | `discussion` | Default channel for agent-router notifications. Options: `comment`, `issue`, `discussion` |
| `AGENT_COMMUNICATION_PREFERENCES` | `{}` | JSON object mapping GitHub usernames to channels. Example: `{"octocat":"discussion","alice":"issue"}` |
| `AGENT_ROUTER_DISCUSSION_CATEGORY` | `General` | Discussion category name used by agent-router when posting notifications as discussions |

### QA Engineer Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `QA_MODEL` | `gpt-4o-mini` | Override model for QA Engineer |
| `QA_SEVERITY_THRESHOLD` | `HIGH` | Minimum severity to open a tracking issue. Options: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |

### Project Manager Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_MODEL` | `gpt-4o-mini` | Override model for Project Manager |
| `PM_MILESTONE_LOOKAHEAD_DAYS` | `30` | Days before a milestone due date to start drift detection |

### Product Owner Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PO_MODEL` | `gpt-4o-mini` | Override model for Product Owner |
| `PO_RUN_PLAYWRIGHT` | `true` | Set to `false` to disable Playwright test runs |
| `PLAYWRIGHT_BASE_URL` | _(empty)_ | Base URL passed to Playwright tests |

### Self-Improvement Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SELF_IMPROVEMENT_MODEL` | `gpt-4o-mini` | Override model for the self-improvement evaluator |
| `REFERENCE_APP_REPO` | current repository | Optional override for the `owner/repo` used as the Get Milk benchmark source |
| `REFERENCE_APP_BASE_URL` | _(empty)_ | Optional live URL for the Get Milk app |
| `COPILOT_ASSIGNEE` | `@copilot` | Assignee login used for native GitHub Copilot handoff when supported |

### Task Assignment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TA_MODEL` | `gpt-4o-mini` | Override model for the Task Assignment System (falls back to `PM_MODEL` then `AGENT_MODEL`) |

### Skill Development Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SKILL_REMINDERS_OPT_IN` | `{}` | JSON map of agent names to `true`/`false` enabling reminder badges in the skill development report. Example: `{"Quinn (QA Engineer)": true}` |

### Council Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `COUNCIL_MODEL` | `gpt-4o` | Model for Council Moderator synthesis |
| `COUNCIL_DISCUSSION_CATEGORY` | `Team Decisions` | GitHub Discussion category for council decisions |

Council automation cadence is configured in workflow YAML:

- Weekdays at 14:30 UTC (scheduled product decision sweep)
- On successful Product Owner workflow completion
- Mondays at 08:00 UTC (council sprint prioritization sweep)

---

## Collaboration Rules

Shared collaboration rules live in
[`/.github/collaboration-rules.md`](./.github/collaboration-rules.md).

- Edit this file to define how agents should interact and make decisions.
- The shared `call-github-model` action reads the file on every invocation, so
  updates apply dynamically to subsequent agent runs without restarting
  anything.
- Keep the file as non-empty Markdown with headings and rule list items so the
  shared action can validate and load it safely.
- Changes are audited by the **Collaboration Rules Audit** workflow, which posts
  a timestamped record with the before/after content and diff.

---

## Schedules

Default schedules (UTC):

| Agent | Default | Override |
|-------|---------|---------|
| Project Manager | Every weekday 09:00 | Edit `cron` in `project-manager.yml` |
| Task Assignment | Every weekday 11:00 | Edit `cron` in `task-assignment.yml` |
| Product Owner | Every weekday 13:00 | Edit `cron` in `product-owner.yml` |
| Council Discussion | Every weekday 14:30 + Product Owner completion trigger | Edit `cron` and `workflow_run` in `council-discussion.yml` |
| Council Sprint Prioritization | Every Monday 08:00 | Edit `cron` in `council-sprint-prioritization.yml` |
| Roadmap Collaboration | Weekly Monday 15:00 | Edit `cron` in `roadmap-collaboration.yml` |
| Self-Improvement Loop | Every weekday 17:00 | Edit `cron` in `self-improvement-loop.yml` |

GitHub Actions does not support repository variables inside `on.schedule`, so
the schedule is controlled directly in the workflow YAML.

To change the schedule, edit the `cron:` value in the respective workflow file.

---

## Manual Runs

All agent workflows support `workflow_dispatch`, so they can be started from
the GitHub Actions UI.

For a single entrypoint, use **Manual Agent Runner** from the Actions tab. It
dispatches to the underlying QA, Project Manager, Product Owner, Council,
Council Sprint Prioritization, Roadmap Collaboration, Self-Improvement, or Task Assignment workflow and forwards the relevant inputs.
For Product Owner feature suggestion runs, use `feature_prompt` to steer the
generated GitHub issues.

---

## Self-Improvement Loop

The self-improvement workflow evaluates this repository itself rather than a
product codebase. Its purpose is to identify the next workflow, prompt,
configuration, or observability changes that will improve the development loop.

The benchmark target is the Get Milk reference app. Configure that benchmark in
repository variables:

- `REFERENCE_APP_REPO` for the reference application's repository
- `REFERENCE_APP_BASE_URL` for a live deployment, if you have one

If `REFERENCE_APP_REPO` is unset, the self-improvement loop defaults to the
current repository.

The workflow opens issues in this repository with `self-improvement` and
`copilot-ready` labels. Those issues are intended to be assigned using native
GitHub Copilot in the GitHub UI. If your GitHub setup exposes a stable Copilot
assignee identity, you can also set `COPILOT_ASSIGNEE` and the workflow will
attempt the assignment automatically.

---

## Agent Personas

Agent personas are defined in [`.github/agents.md`](./.github/agents.md).
Editing the persona sections changes how the model behaves for that agent.
The system prompts in the workflow files are seeded from these definitions.

To change Quinn's tone from "methodical" to "collaborative", simply edit the
QA Engineer persona in `.github/agents.md` and update the `system-prompt:`
in `.github/workflows/qa-engineer.yml`.

For cross-agent interaction rules that should apply everywhere, prefer updating
[`/.github/collaboration-rules.md`](./.github/collaboration-rules.md) instead of
editing each workflow prompt individually.

---

## Skills

Skills are documented in [`.github/skills.md`](./.github/skills.md). They
describe the capabilities agents use and the GitHub APIs they call. This file
is a reference — actual skill implementation lives in the workflow steps and
composite actions.

---

## Adding a New Agent

### Step 1 — Define the persona

Add a new section to `.github/agents.md`:

```markdown
## My New Agent

**ID**: `my-agent`
**Name**: Sam (My Agent)
**Model**: `gpt-4o-mini`

### Persona
...

### Responsibilities
...

### Triggers
...

### Skills Used
- `issue-creation`
- `pr-feedback`
```

### Step 2 — List skills

Add any new skills to `.github/skills.md`.

### Step 3 — Create the workflow

Create `.github/workflows/my-agent.yml`. Use an existing workflow as a
template. The minimum structure is:

```yaml
name: My Agent

on:
  workflow_dispatch:
  workflow_call:
    inputs:
      extra_context:
        type: string
        required: false

permissions:
  issues: write
  pull-requests: write

jobs:
  my-agent:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Sam — analysis
        id: analysis
        uses: ./.github/actions/call-github-model
        with:
          model: gpt-4o-mini
          token: ${{ secrets.MODELS_TOKEN || secrets.GITHUB_TOKEN }}
          system-prompt: |
            You are Sam, a ... (paste persona here)
          user-prompt: |
            ${{ inputs.extra_context }}

      - name: Post result
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          echo "${{ steps.analysis.outputs.response }}"
```

### Step 4 — Register a slash-command (optional)

Add a new route to `.github/workflows/agent-router.yml` following the
pattern of the existing `/qa`, `/pm`, `/po` routes.

---

## Composite Actions Reference

### `.github/actions/call-github-model`

Calls GitHub Models API and returns the text response.

**Inputs**:

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `model` | No | `gpt-4o-mini` | Model identifier |
| `system-prompt` | Yes | — | Agent persona and instructions |
| `user-prompt` | Yes | — | Context to analyse |
| `temperature` | No | `0.7` | Sampling temperature |
| `max-tokens` | No | `2048` | Max response tokens |
| `token` | Yes | — | GitHub token with `models:read` |

**Outputs**:

| Output | Description |
|--------|-------------|
| `response` | Plain-text model response |
| `raw-json` | Full JSON response body |

### `.github/actions/post-council-results`

Posts a council decision to GitHub Discussions, falling back to Issues.

**Inputs**:

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `title` | Yes | — | Discussion/Issue title |
| `body` | Yes | — | Markdown body |
| `category` | No | `General` | Preferred Discussion category |
| `labels` | No | `council-decision` | Labels for Issue fallback |
| `token` | Yes | — | GitHub token |

**Outputs**:

| Output | Description |
|--------|-------------|
| `url` | URL of the created Discussion or Issue |
| `type` | `"discussion"` or `"issue"` |

---

## Troubleshooting

### Model returns "⚠️ Model call failed"

- Check that `MODELS_TOKEN` is set and has `models:read` scope.
- Confirm the model name is valid (see [GitHub Models docs](https://github.com/marketplace/models)).
- Check the Actions run log for the raw API response.

### Labels not being applied

- Ensure the labels exist in your repository (run the `gh label create`
  commands from the Quick Start).
- The agent will skip label creation silently if labels don't exist. Run
  the setup script once.

### Discussions not being created

- Enable Discussions in **Settings → Features → Discussions**.
- If Discussions are disabled, agents automatically fall back to Issues.

### Agent Router not responding to commands

- Ensure the commenter has **write access** to the repository.
- Ensure the comment starts exactly with `/qa`, `/pm`, `/po`, `/council`,
  or `/help` (no leading spaces).
- Check the Actions tab for the `Agent Router` run.

### Council workflow times out

- Council discussions make 4 model calls sequentially. If using a slow model
  (e.g., `gpt-4`), the workflow may approach the 6-hour limit.
  Switch to `gpt-4o-mini` via `COUNCIL_MODEL` variable for faster runs.
