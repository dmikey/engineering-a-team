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

### 2. Configure GitHub Copilot access

The agents call **GitHub Copilot models** through GitHub Copilot CLI. Direct
GitHub Models endpoints are not used.

Model-calling workflows request `copilot-requests: write` for the built-in
`GITHUB_TOKEN`. Your GitHub account or organization must have Copilot access.
If your policy requires a dedicated token, configure it explicitly:

1. Go to **Settings → Secrets and variables → Secrets**
2. Create secret `COPILOT_GITHUB_TOKEN` with your Copilot-enabled token (or leave it unset to
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

Every pull request also runs the **PR Compliance Checks** workflow, which
publishes a compliance report artifact and fails the check with alert details
when non-compliance is detected.

### 6. Run any agent manually from the Actions tab

Open **Actions → Manual Agent Runner → Run workflow** and choose which agent
to execute:

- `qa`: optional `pr_number` and `extra_context`
- `pm`: `task` such as `groom-backlog`, `check-milestones`, `full-sprint-report`, `agent-performance-dashboard`, or `skill-development-suggestions` (optional `extra_context`: `period=<days> sort=<success-rate|runs|failures|avg-duration|last-run>`)
- `po`: `task` such as `product-health-report`, `suggest-features`, or `run-playwright`, plus optional `feature_prompt`, `base_url`, and `extra_context`
- `council`: `topic`, optional `issue_number`, optional `council_mode` (`discussion` or `role-adjustment`), and `extra_context`
- `council-sprint`: optional `sprint_goal`, `issue_number`, and `extra_context` — runs the council sprint prioritization meeting
- `roadmap`: set `task` to a roadmap horizon (for example `30/60/90 days`), optional `topic` as focus, and optional `extra_context`
- `self-improvement`: `task` as `full-loop`, `benchmark-only`, or `copilot-handoff`, plus optional `reference_repo`, `base_url`, and `extra_context`
- `task-assignment`: `task` as `assign-tasks` (default) or `workload-dashboard`, plus optional `extra_context`

You can also request a council-driven role rebalance from comments with:

```text
/council adjust-roles focus on bug triage and test stability
```

That mode uses the latest workflow performance and workload metrics to promote a lead agent, shift overloaded agents into advisory roles, and publish an audit log of the changes.

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
- `gh copilot` command available for best local TUI Copilot fallback experience
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

# Assess a new repository in read-only TUI mode before enabling automation
python3 ./scripts/heartbeat_runner.py --tui --repo acme/widgets --interval 300 --dry-run

# Inspect local supervisor status and logs
scripts/agent-cli.sh service status
scripts/agent-cli.sh service logs --tail-lines 120 --follow
```

To see all options:

```bash
scripts/agent-cli.sh --help
```

When launching `service tui` or dispatching runs, the CLI performs a preflight
check to verify:

- Manual Agent Runner workflow is accessible
- Discussions capability is available (or warns if it will fall back to issues)
- Copilot CLI command availability for local TUI Copilot integration

For a repository that has not yet adopted this automation, start with the
direct TUI command shown above. It loads a read-only snapshot and lets the
operator assess repository state, proposed actions, authentication, and
workflow pressure without mutating the target. Driving another repository
requires write access to that repository, including workflow dispatch
permission. The managed `service tui` command is available after the target
repository contains this project's compatible GitHub Actions workflows.

### 8. Run local autonomous heartbeat process

Local heartbeat supervision is the primary runtime. The cloud heartbeat workflow
is manual fallback only.

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

By default, if no dispatch-capable token is present (`GH_USER_PAT` or
`HEARTBEAT_GH_TOKEN` with `actions:write`), the local runner automatically
switches to dry-run mode so the loop continues without failing on workflow
dispatch permissions.

Enable live workflow dispatch from local mode:

```bash
export GH_USER_PAT=YOUR_PAT_WITH_ACTIONS_WRITE
scripts/autonomous-heartbeat.sh --interval 600
```

For 24/7 operation on macOS, install the user LaunchAgent instead. It starts at
login and `launchd` restarts it if the process exits:

```bash
scripts/autonomous-heartbeat.sh install-service --interval 600
```

Supervisor behavior per heartbeat cycle (event + time):

1. Verifies Copilot pipeline signals (`call-copilot-model` usage + `copilot-requests: write` permission)
2. Detects whether `COPILOT_GITHUB_TOKEN` exists (falls back to `GITHUB_TOKEN` mode when absent)
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

# Supervise a compatible remote repository through the TUI
scripts/autonomous-heartbeat.sh tui --repo acme/widgets --interval 300

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
| `AGENT_MODEL` | `gpt-5-mini` | Default model for all agents |
| `COUNCIL_MODEL` | `gpt-5.4` | Model for Council Moderator |
| `AGENT_MAX_TOKENS` | `2048` | Max response tokens |
| `AGENT_TEMPERATURE` | `0.7` | Generation temperature |
| `AGENT_DEFAULT_COMMUNICATION_METHOD` | `discussion` | Default channel for agent-router notifications (`comment`, `issue`, or `discussion`) |
| `AGENT_COMMUNICATION_PREFERENCES` | `{}` | JSON map of per-user communication preferences, e.g. `{\"octocat\":\"discussion\"}` |
| `AGENT_ROUTER_DISCUSSION_CATEGORY` | `General` | Discussion category used when router notifications are posted as discussions |
| `QA_SEVERITY_THRESHOLD` | `HIGH` | Minimum severity to open an issue |
| `QA_COLLAB_REPOSITORIES` | _(empty)_ | Optional comma-separated `owner/repo` list for cross-repo QA issue context and mirrored serious QA issues (uses up to 3 valid external repositories) |
| `QA_AGENT_SKILLS` | `code-review,issue-creation,pr-feedback,security-scan` | Comma-separated skill set injected into QA prompts |
| `PM_MILESTONE_LOOKAHEAD_DAYS` | `30` | Days ahead for milestone drift detection |
| `PM_AGENT_SKILLS` | `backlog-grooming,milestone-management,discussion-creation,issue-labeling,skill-development-analysis` | Comma-separated skill set injected into PM prompts |
| `PO_RUN_PLAYWRIGHT` | `true` | Run Playwright tests when config is found |
| `PO_AGENT_SKILLS` | `feature-suggestion,playwright-testing,issue-creation,discussion-facilitation,product-analysis` | Comma-separated skill set injected into PO prompts |
| `REFERENCE_APP_REPO` | current repository | Optional override for the `owner/repo` used for the Get Milk benchmark app |
| `REFERENCE_APP_BASE_URL` | _(empty)_ | Optional live URL for the Get Milk benchmark app |
| `SELF_IMPROVEMENT_MODEL` | `gpt-5-mini` | Model for self-improvement evaluation |
| `TA_MODEL` | `gpt-5-mini` | Model for the Task Assignment System (falls back to `PM_MODEL` then `AGENT_MODEL`) |
| `SKILL_REMINDERS_OPT_IN` | `{}` | JSON object mapping known agent names to `true`/`false` reminder opt-ins |
| `COPILOT_ASSIGNEE` | _(empty)_ | Optional native Copilot assignee identity |
| `COUNCIL_DISCUSSION_CATEGORY` | `Team Decisions` | GitHub Discussion category |
| `COUNCIL_AGENT_SKILLS` | `discussion-creation` | Comma-separated skill set injected into Council Moderator prompts |

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

### Customizing role skill sets

Set the `*_AGENT_SKILLS` variables as comma-separated lists to tailor each
role's active capabilities. Changes are read fresh on every workflow run, so
updated skill sets apply immediately to the next agent execution.

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

### Clear Mergeable PRs From Linux

When you want to clear the current PR backlog from a Linux shell, use the
local runner in [scripts/auto-merge-prs.sh](./scripts/auto-merge-prs.sh):

```bash
./scripts/auto-merge-prs.sh --base main --dry-run
./scripts/auto-merge-prs.sh --base main
```

The script uses `gh pr merge --auto --delete-branch` for every eligible,
non-draft PR. GitHub merges immediately when allowed and enables auto-merge
when checks are still pending. Pull requests with merge conflicts, failing
checks, or blocking review decisions are skipped.

### Run The Long-Lived Heartbeat Orchestrator

If you want a local process that keeps scanning the repo, builds a decision
queue, merges safe PRs, sends blocked PRs back to Copilot, and dispatches the
pending agent workflows, run [scripts/heartbeat_runner.py](./scripts/heartbeat_runner.py).

Start with a single dry-run heartbeat:

```bash
python3 ./scripts/heartbeat_runner.py --once --dry-run
```

Run it continuously every 5 minutes:

```bash
python3 ./scripts/heartbeat_runner.py --interval 300
```

For stronger, cost-effective automation, use retries plus model cadence:

```bash
python3 ./scripts/heartbeat_runner.py --interval 300 --max-retries 2 --model-every 3 --adaptive-model-cadence
```

- `--max-retries`: retries transient GitHub/API failures before surfacing an error.
- `--model-every`: uses GitHub Models every N cycles and heuristic planning in between to reduce token spend.
- `--adaptive-model-cadence`: increases model usage automatically when PR conflict risk or workflow failure pressure rises.

Escalation automation is built in:

- Repeated sync-conflict failures on the same PR automatically open/update a tracked **stuck PR escalation issue**.
- Higher conflict-failure streaks automatically dispatch the **council discussion workflow**.
- These escalations use cooldown guards to avoid noisy repeats while still ensuring unresolved work is actively routed.

Run the full interactive TUI:

```bash
python3 ./scripts/heartbeat_runner.py --tui --interval 300
```

### Onboard A Repository Through The TUI

The TUI is the operator-led entry point for a new repository. Begin with a
read-only assessment. `--dry-run` keeps the session observational even if the
operator later confirms a heartbeat, so the user can inspect the repository and
decide whether to adopt the automation before any mutation is possible:

```bash
python3 ./scripts/heartbeat_runner.py --tui --repo acme/widgets --interval 300 --dry-run
```

The initial preview shows the repository queue, PR decisions, workflow
pressure, model/auth status, and the proposed action plan. Use it to assess the
repository, then provision the target with this project's compatible GitHub
Actions workflows and required configuration before granting execution.

To drive the target repository instead of only observing it, run from a real
interactive terminal, remove `--dry-run`, and provide credentials with write
access to the target repository and `actions:write` capability:

```bash
export GH_USER_PAT=YOUR_TOKEN_WITH_ACTIONS_WRITE
python3 ./scripts/heartbeat_runner.py --tui --repo acme/widgets --interval 300
```

After that setup, use the managed TUI command. It validates workflow access
before it starts and can dispatch workflows when the token has permission:

```bash
scripts/agent-cli.sh service tui --repo acme/widgets --interval 300
```

To explicitly grant automatic execution at startup and run the first guarded
heartbeat immediately:

```bash
python3 ./scripts/heartbeat_runner.py --tui --tui-auto --interval 300
```

TUI controls:

- `q`: quit
- `r`: request a policy-gated heartbeat
- `p`: request enabling or disabling automatic policy-gated heartbeats
- `c`: request a council workflow dispatch
- `m`: request a project manager report
- `a`: request assignment of the highest-priority eligible issue
- `d`: request advancement of planner-approved drafts
- `y` / `n`: confirm or cancel the pending execution request
- `/`: open local agent chat

To have the TUI dispatch a council reply to a specific existing Discussion
comment, launch it with the Discussion node IDs, press `c`, then confirm with
`y`:

```bash
python3 ./scripts/heartbeat_runner.py --tui --repo dmikey/engineering-a-team \
  --discussion-id D_kwDOTcEQIM4AogB2 \
  --discussion-reply-to-id DC_kwDOTcEQIM4BEwCC \
  --discussion-topic "Discussion #1272: SKILL_REMINDERS_OPT_IN assignment" \
  --discussion-context "User asked: Let's move forward with assigning this SKILL_REMINDERS_OPT_IN"
```

TUI mode starts under operator control with automatic execution disabled.
On startup it loads a read-only repository preview so the queue, workflow
pressure, and heuristic decisions are visible before the operator grants any
execution. This preview does not approve runs, merge PRs, dispatch workflows,
or write heartbeat decisions.
Enabling automatic mode requires confirmation; after that grant, the first
heartbeat runs immediately and later heartbeats run at `--interval` until the
operator confirms that automatic mode should stop. `--tui-auto` supplies the
same grant explicitly on the command line.
Chat and pending confirmation dialogs suspend due runs. Manual controls and
automatic heartbeats use the same backend merge guards, cooldowns, action
limits, authentication, and decision ledger. The TUI displays the current
mode, automatic countdown, queue state, PR decisions, workflow pressure,
model/auth status, and latest execution results.

When the latest run for a workflow fails with `failure`, `startup_failure`, or
`timed_out`, an active heartbeat requests a rerun of failed jobs. Recovery is
limited to one workflow per heartbeat, waits 30 minutes between attempts, and
stops after three consecutive attempts. A later successful run clears the
attempt count. Dry-run mode only simulates this recovery and labels it as not
executed.

Chat does not publish or dispatch workflows automatically. Copilot tool and
URL access are disabled by default; operators can explicitly opt in with
`HEARTBEAT_CHAT_ALLOW_ALL_TOOLS=true` or
`HEARTBEAT_CHAT_ALLOW_ALL_URLS=true` before starting the TUI.

#### Copilot planner timeout and cooldown

Local heartbeats consult the Copilot CLI planner (`gh copilot`) each cycle.
To keep a hung or unavailable planner from stalling the TUI:

- The planner call times out after 45 seconds by default; override with
  `HEARTBEAT_MODEL_TIMEOUT` (seconds).
- After a planner timeout or failure, the runner enters a 30-minute
  **planner cooldown** and plans from safe heuristics immediately instead of
  retrying every beat. The status line shows
  `copilot-cli unavailable, cooldown ~29m` while active.
- The heuristic plan still merges safe PRs, dispatches QA, routes backlog
  work, and posts `@copilot` handoffs — no beat is skipped.

If the planner keeps timing out, run `gh copilot -p "say hi"` once in a
normal interactive terminal: first-run trust/login prompts are the usual
cause, and the heartbeat resumes Copilot planning automatically once the
cooldown expires.

The runner prints an in-depth overview each cycle and also writes the latest
report plus local dedupe state under `.git/heartbeat-runner/`.

For local runs, Copilot CLI uses your authenticated Copilot session. A
`GH_USER_PAT` with `actions:write` is only needed when the runner dispatches
workflows. For third-party repositories where you only have read access, keep
`--dry-run` enabled and treat the TUI as an observer:

```bash
export GH_USER_PAT=YOUR_TOKEN
python3 ./scripts/heartbeat_runner.py --interval 300
```

Token source policy for the heartbeat runner:

- Local inference: Copilot CLI uses `COPILOT_GITHUB_TOKEN` when set, otherwise its authenticated session.
- Local dispatch: `GH_USER_PAT` or `HEARTBEAT_GH_TOKEN` authorizes workflow dispatches.
- GitHub Actions inference: uses `COPILOT_GITHUB_TOKEN`, falling back to the workflow `GITHUB_TOKEN` with `copilot-requests: write`.

If you want local model inference without setting `GH_USER_PAT`, run:

```bash
gh auth login
python3 ./scripts/heartbeat_runner.py --interval 300
```

If you also want the runner to dispatch workflows itself from local Linux,
the same `GH_USER_PAT` is used — no separate token needed.
`HEARTBEAT_GH_TOKEN` is also still supported for local workflow dispatch auth.

Without local GitHub CLI auth and without `GH_USER_PAT`, the
runner falls back to safe heuristics. It will still merge clearly safe PRs,
dispatch QA on pending PRs, route feature and planning backlog to the existing
workflows, and comment on blocked PRs with an `@copilot` handoff.

The heartbeat PR flow is:

1. If a PR (draft or non-draft) has merge conflicts or failing workflow/check
  signals, post an `@copilot` fix request.
2. If a draft PR is mergeable with no failing checks/workflow signals, convert
  it to ready for review.
3. After conversion, attempt merge using the same safety guard used for
  non-draft PRs.

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
    call-copilot-model/       # Reusable composite action — Copilot CLI models
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
            └─► call-copilot-model (Quinn persona)
                    └─► PR review comment posted
                    └─► Non-approval reviews tag `@copilot` for PR follow-up
                    └─► Issue opened if HIGH/CRITICAL

Weekdays 09:00 UTC
    └─► project-manager.yml
            ├─► call-copilot-model (Morgan — grooming)
            ├─► call-copilot-model (Morgan — milestones)
    ├─► Uses latest Product + Project Roadmap to guide priority and assignment
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
            ├─► call-copilot-model (Alex — health report)
            ├─► call-copilot-model (Alex — feature suggestions)
            ├─► Feature issues opened
            ├─► Playwright tests run (if configured)
            └─► Product health report posted

      Weekdays 14:30 UTC and after successful Product Owner runs
        └─► council-discussion.yml
            ├─► call-copilot-model (Quinn perspective)
            ├─► call-copilot-model (Morgan perspective)
            ├─► call-copilot-model (Alex perspective)
            ├─► call-copilot-model (Casey synthesis)
            └─► Council decision posted to Discussion/Issue

      Mondays 15:00 UTC
        └─► roadmap-collaboration.yml
            ├─► call-copilot-model (Alex product direction)
            ├─► call-copilot-model (Morgan delivery plan)
            ├─► call-copilot-model (Casey merged roadmap)
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
      ├─► call-copilot-model (Quinn — risk & complexity assessment)
      ├─► call-copilot-model (Morgan — timeline & dependency ranking)
      ├─► call-copilot-model (Alex — user value & product strategy)
      ├─► call-copilot-model (Casey — ranked sprint backlog synthesis)
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
