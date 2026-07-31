# Engineering a Team — Autonomous GitHub Workflow Agents

An autonomous AI engineering team living entirely in GitHub Actions. Intelligent agents — backed by **GitHub Models** — work together to ship
quality software: reviewing code, managing the backlog, and championing the
product. A **council mechanism** lets them discuss complex decisions and reach
consensus.

This repository is self-maintained by agentic systems: the same workflows and
agents that operate your engineering process also continuously evaluate,
improve, and evolve this repository.

---

## Agents

| Agent | Name | Role | Default Trigger |
|-------|------|------|----------------|
| 🔍 QA Engineer | Quinn | Reviews PRs, finds bugs, opens issues | Every PR |
| 📋 Project Manager | Morgan | Grooms backlog, manages milestones | Every weekday |
| 🧪 Product Owner | Alex | Suggests features, runs Playwright | Every weekday + default-branch push |
| 🏛️ Council Moderator | Casey | Facilitates multi-agent discussions | On demand |
| 🎯 Task Assignment | — | Assigns tasks by availability & performance | Every weekday 11:00 UTC |

---

## Quick Start

### 1. Fork / clone this repository

```bash
git clone https://github.com/YOUR-ORG/engineering-a-team.git
cd engineering-a-team
```

### 2. Configure GitHub Models access

The agents call **GitHub Models** (`https://models.inference.ai.azure.com`).
You need a token with the `models:read` scope.

Workflows in this repository now also request `models: read` permission for the
`GITHUB_TOKEN`. If your plan or org policy still does not allow GitHub Models
through `GITHUB_TOKEN`, set `MODELS_TOKEN` explicitly.

1. Go to **Settings → Secrets and variables → Secrets**
2. Create secret `MODELS_TOKEN` with your GitHub PAT (or leave it unset to
   fall back to `GITHUB_TOKEN`)

### 3. Create required labels

Run this once to set up the labels the agents use:

```bash
gh label create "bug"               --color "d73a4a" --force
gh label create "qa-review"         --color "f9d0c4" --force
gh label create "security"          --color "e4e669" --force
gh label create "feature"           --color "a2eeef" --force
gh label create "product-owner"     --color "7057ff" --force
gh label create "council-review"    --color "0075ca" --force
gh label create "council-decision"  --color "3b82f6" --force
gh label create "needs-qa"          --color "fef2c0" --force
gh label create "sprint-report"     --color "cfd3d7" --force
gh label create "priority: critical" --color "d73a4a" --force
gh label create "priority: high"    --color "e4e669" --force
gh label create "priority: medium"  --color "0075ca" --force
gh label create "priority: low"     --color "cfd3d7" --force
gh label create "blocked"           --color "e11d48" --force
```

### 4. Enable Discussions (optional but recommended)

Go to **Settings → Features → Discussions** and enable it. The council and
PM agents will post reports as Discussions. If disabled, they fall back to
Issues.

### 5. Push code and open a PR — Quinn reviews it automatically

### 6. Run any agent manually from the Actions tab

Open **Actions → Manual Agent Runner → Run workflow** and choose which agent
to execute:

- `qa`: optional `pr_number` and `extra_context`
- `pm`: `task` such as `groom-backlog`, `check-milestones`, `full-sprint-report`, `agent-performance-dashboard`, or `skill-development-suggestions` (optional `extra_context`: `period=<days> sort=<success-rate|runs|failures|avg-duration|last-run>`)
- `po`: `task` such as `product-health-report`, `suggest-features`, or `run-playwright`, plus optional `feature_prompt`, `base_url`, and `extra_context`
- `council`: `topic`, optional `issue_number`, and `extra_context`
- `council-sprint`: optional `sprint_goal`, `issue_number`, and `extra_context` — runs the council sprint prioritization meeting
- `roadmap`: set `task` to a roadmap horizon (for example `30/60/90 days`), optional `topic` as focus, and optional `extra_context`
- `self-improvement`: `task` as `full-loop`, `benchmark-only`, or `copilot-handoff`, plus optional `reference_repo`, `base_url`, and `extra_context`
- `task-assignment`: `task` as `assign-tasks` (default) or `workload-dashboard`, plus optional `extra_context`

The Project Manager's `skill-development-suggestions` task and the
scheduled Skill Development Tracking workflow now publish a shared
cross-agent feedback report after completed agent interactions, plus a
weekly summary for team-wide review.

You can still run the individual workflows directly from the Actions tab if
you want the workflow-specific form.

### 7. Run agents from your local CLI with `gh`

Use the local wrapper script to dispatch `manual-agent-runner.yml` directly
from your machine. This is useful when you need a local operator loop for
tasks that are awkward to trigger only from cloud UI flows.

Prerequisites:

- `gh` installed and authenticated (`gh auth login`)
- Execute permission on the script (already set in this repository):

```bash
chmod +x scripts/agent-cli.sh
```

Examples:

```bash
# QA review for a specific PR
scripts/agent-cli.sh run --agent qa --pr-number 236 --extra-context "Focus on auth regressions"

# PM sprint report
scripts/agent-cli.sh run --agent pm --task full-sprint-report

# Product Owner Playwright run against a live URL and wait for completion
scripts/agent-cli.sh run --agent po --task run-playwright --base-url https://example.app --wait

# Self-improvement loop against a reference repo
scripts/agent-cli.sh run --agent self-improvement --task full-loop --reference-repo owner/get-milk

# Open local supervisor TUI (interactive)
scripts/agent-cli.sh service tui --tail-lines 100 --refresh 2

# Inspect local supervisor status and logs
scripts/agent-cli.sh service status
scripts/agent-cli.sh service logs --tail-lines 120 --follow
```

To see all options:

```bash
scripts/agent-cli.sh --help
```

### 8. Run local autonomous heartbeat process

If you want this repo to run autonomously from your machine, start the local
heartbeat daemon. It runs as a Scrum Master supervisor on behalf of the user,
dispatches agent workflows, and keeps a heartbeat status file.

Simplest mode (single process in your terminal):

```bash
scripts/autonomous-heartbeat.sh --interval 600
```

That one command runs continuously and heartbeats every 10 minutes. The local
supervisor makes orchestration, prioritization, merge, and throttle decisions;
GitHub Actions executes the selected agent workflow using GitHub Models.

For 24/7 operation on macOS, install the user LaunchAgent instead. It starts at
login and `launchd` restarts it if the process exits:

```bash
scripts/autonomous-heartbeat.sh install-service --interval 600
```

Supervisor behavior per heartbeat cycle (event + time):

1. Verifies GH Models pipeline signals (`call-github-model` usage + `models: read` permission)
2. Detects whether `MODELS_TOKEN` secret exists (falls back to `GITHUB_TOKEN` mode when absent)
3. Checks recent PR update events (short rolling window)
4. Optionally marks eligible draft PRs as ready for review
5. Auto-merges approved PRs (`--auto-merge-prs true` by default; queues auto-merge when checks are pending)
6. Checks waiting/queued workflow runs
7. Checks recent failed Actions runs
8. Checks stale open PRs, Discussions, and Issues
9. Dispatches the highest-priority agent action based on those signals

Priority order:

1. No task assignment execution in last 4h: run `task-assignment` task `assign-tasks`
2. No PM execution in last 24h: run `pm` task `full-sprint-report`
3. No council execution in last 24h: run `council` with an autonomous cycle topic
4. No Product Owner execution in last 24h: run `po` task `product-health-report`
5. PR needs QA (no verdict, or verdict older than latest commit): run `qa` for that PR
6. Recent PR event found: run `qa` for that PR
7. Waiting/queued runs older than threshold: run `task-assignment` task `assign-tasks`
8. Failed Actions found: run `pm` task `agent-performance-dashboard`
9. Stale PR, Discussion, or Issue found: dispatch its owning agent
10. Otherwise use normal rotation; if a selected action is cooling down, choose another rotation action

Default rotation (keeps PM assigning and council cycling with no user):

1. `pm` → `full-sprint-report`
2. `task-assignment` → `assign-tasks`
3. `pm` → `groom-backlog`
4. `po` → `product-health-report`
5. `council` → autonomous council cycle
6. `pm` → `check-milestones`

Loop-completion guarantees (the process acts on the user's behalf):

- When open issues exist but none are assigned and no PR work is active, PM selects the most urgent issue immediately
- PM uses model priorities when available and a deterministic priority-label/age fallback otherwise
- The selected issue is visibly assigned to the triggering user and handed to the Copilot coding agent
- Every non-draft open PR with no QA verdict (or a verdict older than the latest commit) gets a QA dispatch
- When Quinn's QA comment recommends APPROVE, the supervisor submits the formal PR approval and merges (squash; `--auto-merge-prs false` to disable)
- PRs where Quinn requested changes or blocked are left open and re-QA'd after new commits
- PM and council each run at least once every 24 hours, plus their rotation slots
- `task-assignment` keeps issues assigned every rotation and whenever runs stall
- Task assignment is guaranteed at least every 4 hours; PM, Product Owner, and council are guaranteed daily

Local rate controls prevent an unchanged signal from producing unbounded runs:

- At most 4 workflow dispatches per rolling hour
- 30-minute cooldown for identical agent/task/PR decisions
- 10-minute continuity cooldown when no delivery run or PR exists
- 60-minute cooldown between merge attempts for the same PR
- Only one Manual Agent Runner may be in progress
- When no delivery run or PR exists, one continuity action may exceed the general hourly budget; cooldown and concurrency limits still apply
- Throttle state survives daemon restarts in `.autonomous/action-state.tsv`
- Every selected, throttled, and merge decision is recorded in `.autonomous/decisions.tsv`

The daemon skips dispatch when `manual-agent-runner.yml` already has an active
run, so it avoids overlapping/flooding runs.

You can tune supervisor thresholds:

```bash
scripts/autonomous-heartbeat.sh \
  --interval 600 \
  --stale-pr-hours 24 \
  --stale-discussion-hours 24 \
  --stale-issue-hours 48 \
  --failure-window-hours 24 \
  --event-pr-window-min 20 \
  --waiting-run-min 15 \
  --auto-ready-draft-prs true \
  --auto-merge-prs true \
  --max-dispatches-per-hour 4 \
  --action-cooldown-min 30 \
  --merge-retry-cooldown-min 60
```

```bash
# Start daemon in background (10-minute heartbeat)
scripts/autonomous-heartbeat.sh start --interval 600

# Open interactive local TUI dashboard
scripts/autonomous-heartbeat.sh tui --tail-lines 120 --refresh 2

# Check daemon state and latest heartbeat JSON
scripts/autonomous-heartbeat.sh status

# Stop daemon
scripts/autonomous-heartbeat.sh stop

# Remove the persistent macOS service
scripts/autonomous-heartbeat.sh uninstall-service

# Run one immediate heartbeat cycle in foreground
scripts/autonomous-heartbeat.sh once
```

Artifacts written locally:

- PID file: `.autonomous/heartbeat.pid`
- Log file: `.autonomous/heartbeat.log`
- Heartbeat status: `.autonomous/heartbeat.json`
- Persistent throttle state: `.autonomous/action-state.tsv`
- Local decision history: `.autonomous/decisions.tsv`

Use `scripts/autonomous-heartbeat.sh --help` for all options.

---

## Slash Commands

Post any of these in an issue or PR comment (write access required):

| Command | Effect |
|---------|--------|
| `/qa [context]` | Trigger Quinn for a QA review |
| `/pm groom-backlog` | Trigger Morgan to groom the backlog |
| `/pm check-milestones` | Trigger Morgan to check milestone health |
| `/pm full-sprint-report` | Trigger Morgan for a full sprint report |
| `/pm agent-performance-dashboard [period=<days> sort=<metric>]` | Trigger Morgan to publish an agent KPI dashboard |
| `/pm skill-development-suggestions` | Trigger Morgan to generate cross-agent skill development suggestions |
| `/pm roadmap-collaboration [focus]` | Trigger the shared Alex + Morgan roadmap workflow |
| `/po suggest-features` | Trigger Alex to suggest features |
| `/po product-health-report` | Trigger Alex for a product health report |
| `/po run-playwright` | Trigger Alex to run Playwright tests |
| `/council [topic]` | Convene the full council on a topic |
| `/council sprint-prioritization [goal]` | Run a council sprint prioritization meeting to rank the backlog |
| `/ta [assign-tasks\|workload-dashboard]` | Run the Task Assignment System |
| `/profile comms [comment\|issue\|discussion]` | Set your personal communication channel preference |
| `/help` | List all commands |

---

## Council Discussion

Label any issue or PR with `council-review` to automatically convene the
engineering council. Or dispatch it manually:

```bash
gh workflow run council-discussion.yml \
  -f topic="Should we migrate to TypeScript?" \
  -f context="We have 50k lines of JavaScript..."
```

Each agent independently analyses the topic, then the Council Moderator
synthesises a consensus decision with action items.

The council also runs automatically to review product decisions:

- Every weekday at 14:30 UTC
- After successful completion of the Product Owner workflow

---

## Configuration

See [CONFIGURATION.md](./CONFIGURATION.md) for the full reference.

Override defaults using GitHub repository variables
(**Settings → Secrets and variables → Variables**):

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_MODEL` | `gpt-4o-mini` | Default model for all agents |
| `COUNCIL_MODEL` | `gpt-4o` | Model for Council Moderator |
| `AGENT_MAX_TOKENS` | `2048` | Max response tokens |
| `AGENT_TEMPERATURE` | `0.7` | Generation temperature |
| `AGENT_DEFAULT_COMMUNICATION_METHOD` | `discussion` | Default channel for agent-router notifications (`comment`, `issue`, or `discussion`) |
| `AGENT_COMMUNICATION_PREFERENCES` | `{}` | JSON map of per-user communication preferences, e.g. `{\"octocat\":\"discussion\"}` |
| `AGENT_ROUTER_DISCUSSION_CATEGORY` | `General` | Discussion category used when router notifications are posted as discussions |
| `QA_SEVERITY_THRESHOLD` | `HIGH` | Minimum severity to open an issue |
| `QA_COLLAB_REPOSITORIES` | _(empty)_ | Optional comma-separated `owner/repo` list for cross-repo QA issue context and mirrored serious QA issues (uses up to 3 valid external repositories) |
| `PM_MILESTONE_LOOKAHEAD_DAYS` | `30` | Days ahead for milestone drift detection |
| `PO_RUN_PLAYWRIGHT` | `true` | Run Playwright tests when config is found |
| `REFERENCE_APP_REPO` | current repository | Optional override for the `owner/repo` used for the Get Milk benchmark app |
| `REFERENCE_APP_BASE_URL` | _(empty)_ | Optional live URL for the Get Milk benchmark app |
| `SELF_IMPROVEMENT_MODEL` | `gpt-4o-mini` | Model for self-improvement evaluation |
| `TA_MODEL` | `gpt-4o-mini` | Model for the Task Assignment System (falls back to `PM_MODEL` then `AGENT_MODEL`) |
| `SKILL_REMINDERS_OPT_IN` | `{}` | JSON object mapping known agent names to `true`/`false` reminder opt-ins |
| `COPILOT_ASSIGNEE` | _(empty)_ | Optional native Copilot assignee identity |
| `COUNCIL_DISCUSSION_CATEGORY` | `Team Decisions` | GitHub Discussion category |

Shared agent interaction rules are defined in
[`/.github/collaboration-rules.md`](./.github/collaboration-rules.md). The file
is loaded dynamically on every model call, and changes are logged by the
**Collaboration Rules Audit** workflow.

### `SKILL_REMINDERS_OPT_IN` format

Set `SKILL_REMINDERS_OPT_IN` in **Settings → Secrets and variables → Variables**
as a JSON object whose keys are agent names and whose values are booleans:

```json
{
  "Quinn (QA Engineer)": true,
  "Morgan (Project Manager)": false,
  "Alex (Product Owner)": true,
  "Casey (Council Moderator)": false
}
```

Invalid JSON, unknown agent names, and non-boolean values are ignored.

Default automation cadence is tuned for active development:

- Project Manager runs every weekday at 09:00 UTC
- Task Assignment System runs every weekday at 11:00 UTC
- Product Owner runs every weekday at 13:00 UTC
- Council runs every weekday at 14:30 UTC
- Roadmap Collaboration runs weekly on Monday at 15:00 UTC
- Self-Improvement Loop runs every weekday at 17:00 UTC
- Product Owner also runs on pushes to the default branch

See [CONFIGURATION.md](./CONFIGURATION.md) for schedule details and how to
change them.

---

## Reference Project: Get Milk

Use a small, concrete app as the proving ground for the autonomous team. The
recommended reference project is **Get Milk**: a lightweight shopping-list app
focused on the recurring job of remembering and buying staples.

Start with this brief:

- User can add an item with a quantity
- User can mark an item complete when purchased
- User can see items due today or overdue
- User can quickly re-add common recurring items like milk, eggs, and bread
- Team tracks follow-up work as GitHub issues with milestones and priorities

See [docs/reference-projects/get-milk.md](./docs/reference-projects/get-milk.md)
for the full scope, backlog seeds, and acceptance criteria.

---

## Self-Improvement Model

This repository is meant to improve itself.

- The workflows in this repo evaluate how well the agent system is supporting
  development against the Get Milk benchmark app.
- Evaluation findings become issues in this repo labeled `self-improvement` and
  `copilot-ready`.
- Those issues are intended to be assigned using native GitHub Copilot, so the
  platform's built-in execution model does the implementation work.
- Existing QA, PM, and Council workflows can then triage, prioritize, and
  review the resulting changes.

Use **Actions → Self-Improvement Loop** for a direct manual run, or choose
`self-improvement` from **Manual Agent Runner**.

If you do not set `REFERENCE_APP_REPO`, the self-improvement loop uses this
repository by default and combines that repository state with the Get Milk
brief as its benchmark context.

---

## Extending the Team

See [`.github/agents.md`](./.github/agents.md) for the full agent spec.

To add a new agent:

1. Add a persona section to `.github/agents.md`
2. Add skills to `.github/skills.md`
3. Create `.github/workflows/<agent-id>.yml`
4. Register a slash-command in `.github/workflows/agent-router.yml`

---

## Repository Structure

```
.github/
  agents.md                  # Agent personas and responsibilities
  skills.md                  # Shared skills catalog
  agent-config.yml           # Configuration reference
  collaboration-rules.md     # Shared agent interaction and decision rules
  copilot-instructions.md    # GitHub Copilot context
  actions/
    call-github-model/       # Reusable composite action — GitHub Models API
    post-council-results/    # Composite action — post to Discussions/Issues
  workflows/
    collaboration-rules-audit.yml # Audits collaboration rule changes
    qa-engineer.yml          # Quinn — QA reviews
    project-manager.yml      # Morgan — backlog & milestones
    product-owner.yml        # Alex — features & Playwright
    roadmap-collaboration.yml# Alex + Morgan — shared roadmap planning
    self-improvement-loop.yml# Casey — benchmark-driven repo improvement
    council-discussion.yml   # Casey — multi-agent council
    agent-router.yml         # Routes /commands from comments
CONFIGURATION.md             # Full configuration guide
```

---

## How It Works

```
PR Opened
    └─► qa-engineer.yml
            └─► call-github-model (Quinn persona)
                    └─► PR review comment posted
                    └─► Non-approval reviews tag `@copilot` for PR follow-up
                    └─► Issue opened if HIGH/CRITICAL

Weekdays 09:00 UTC
    └─► project-manager.yml
            ├─► call-github-model (Morgan — grooming)
            ├─► call-github-model (Morgan — milestones)
            ├─► Labels applied to issues
            └─► Sprint report posted to Discussion/Issue

Weekdays 11:00 UTC
    └─► task-assignment.yml
            ├─► Fetches live workflow run status (availability)
            ├─► Computes 30-day performance scores per agent
            ├─► Matches open issues to best-fit agent
            ├─► Posts per-issue assignment recommendations
            └─► Workload dashboard posted to Discussion/Issue

Weekdays 13:00 UTC or on push to default branch
    └─► product-owner.yml
            ├─► call-github-model (Alex — health report)
            ├─► call-github-model (Alex — feature suggestions)
            ├─► Feature issues opened
            ├─► Playwright tests run (if configured)
            └─► Product health report posted

      Weekdays 14:30 UTC and after successful Product Owner runs
        └─► council-discussion.yml
            ├─► call-github-model (Quinn perspective)
            ├─► call-github-model (Morgan perspective)
            ├─► call-github-model (Alex perspective)
            ├─► call-github-model (Casey synthesis)
            └─► Council decision posted to Discussion/Issue

      Mondays 15:00 UTC
        └─► roadmap-collaboration.yml
            ├─► call-github-model (Alex product direction)
            ├─► call-github-model (Morgan delivery plan)
            ├─► call-github-model (Casey merged roadmap)
            └─► Shared roadmap posted to Discussion/Issue

Weekdays 17:00 UTC
  └─► self-improvement-loop.yml
      ├─► Benchmarks workflow repo against Get Milk signals
      ├─► Opens `self-improvement` + `copilot-ready` issues
      ├─► Optionally attempts native Copilot assignment
      └─► Feeds backlog back into PM, QA, and Council workflows

/council topic
  └─► council-discussion.yml
      └─► On-demand council decision posted to Discussion/Issue

/council sprint-prioritization [goal]
  └─► council-sprint-prioritization.yml
      ├─► call-github-model (Quinn — risk & complexity assessment)
      ├─► call-github-model (Morgan — timeline & dependency ranking)
      ├─► call-github-model (Alex — user value & product strategy)
      ├─► call-github-model (Casey — ranked sprint backlog synthesis)
      └─► Sprint backlog posted to Discussion/Issue

Mondays 08:00 UTC
  └─► council-sprint-prioritization.yml
      └─► Weekly automated sprint planning sweep

/qa /pm /po /ta in comment
    └─► agent-router.yml
            └─► Dispatches the appropriate workflow
```

---

## License

MIT
