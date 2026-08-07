# Agent Skills

This file catalogues the skills available to agents in this repository.
Each skill describes a discrete capability: what it does, the GitHub APIs
or tools it uses, and which agents currently use it. When adding a new
agent in [`agents.md`](./agents.md), pick the skills you need from this
list — or add a new skill entry here.

---

## code-review

**Description**: Analyse a pull request diff and produce a structured
quality review with risk classification, issue list, and an
approval recommendation.

**Inputs**: PR diff, PR description, changed file list  
**Outputs**: Markdown review comment posted to the PR  
**GitHub APIs**: `POST /repos/{owner}/{repo}/issues/{number}/comments`,
`POST /repos/{owner}/{repo}/pulls/{number}/reviews`  
**Used by**: QA Engineer

---

## issue-creation

**Description**: Create a well-structured GitHub Issue with a title,
body, labels, and optional milestone.

**Inputs**: Title, body (Markdown), label list, optional milestone  
**Outputs**: GitHub Issue URL  
**GitHub APIs**: `POST /repos/{owner}/{repo}/issues`  
**Used by**: QA Engineer, Product Owner

---

## pr-feedback

**Description**: Post a review comment or inline annotation on a pull
request; optionally request changes or approve.

**Inputs**: PR number, comment body, review action (COMMENT / APPROVE /
REQUEST_CHANGES)  
**Outputs**: Review posted on PR  
**GitHub APIs**: `POST /repos/{owner}/{repo}/pulls/{number}/reviews`  
**Used by**: QA Engineer

---

## security-scan

**Description**: Evaluate code changes for common security vulnerabilities
using the full OWASP Top 10 checklist (A01–A10): Broken Access Control,
Cryptographic Failures, Injection (SQL/command/LDAP/XSS/template),
Insecure Design, Security Misconfiguration, Vulnerable & Outdated
Components, Identification & Authentication Failures, Software & Data
Integrity Failures, Security Logging & Monitoring Failures, and
Server-Side Request Forgery. Also detects hardcoded credentials, insecure
randomness, overly broad GitHub Actions permissions, and unsafe
deserialisation patterns.

**Inputs**: PR diff, file list  
**Outputs**: Findings list (CRITICAL / HIGH / MEDIUM / LOW) included in
the QA review, with per-category OWASP verdict or "Not applicable"  
**GitHub APIs**: Issues (for tracking), Code Scanning alerts API  
**Used by**: QA Engineer

---

## backlog-grooming

**Description**: Retrieve all open issues, classify them by priority and
effort, and apply appropriate labels.

**Inputs**: Open issue list (title, body, existing labels, created date)  
**Outputs**: Updated issue labels; grooming summary Markdown  
**GitHub APIs**: `GET /repos/{owner}/{repo}/issues`,
`PATCH /repos/{owner}/{repo}/issues/{number}`,
`POST /repos/{owner}/{repo}/issues/{number}/labels`  
**Used by**: Project Manager

---

## milestone-management

**Description**: Inspect milestone due dates and assigned issues. Detect
drift (issues not closing fast enough), comment on the milestone issue or

---

## benchmark-evaluation

**Description**: Compare the current workflow system against a reference
application benchmark, identify gaps in observability, execution cadence,
handoff quality, and backlog generation, and recommend workflow-level changes.

**Inputs**: Reference project brief, optional reference repository signals,
current repository issues, recent commits, workflow configuration
**Outputs**: Ranked self-improvement recommendations with evidence and clear
scope
**GitHub APIs**: `GET /repos/{owner}/{repo}`, `GET /repos/{owner}/{repo}/issues`,
`GET /repos/{owner}/{repo}/pulls`
**Used by**: Self-Improvement Loop

---

## copilot-handoff

**Description**: Prepare issues for native GitHub Copilot execution by writing
implementation-ready issue bodies, labeling them for triage, and optionally
assigning a configured Copilot assignee when the platform supports it.

**Inputs**: Improvement recommendation, benchmark evidence, implementation brief,
optional assignee
**Outputs**: Copilot-ready issue in GitHub
**GitHub APIs**: `POST /repos/{owner}/{repo}/issues`,
`PATCH /repos/{owner}/{repo}/issues/{number}`
**Used by**: Self-Improvement Loop
post a warning discussion.

**Inputs**: Milestone list, assigned issues, current date  
**Outputs**: Milestone health comment; label updates  
**GitHub APIs**: `GET /repos/{owner}/{repo}/milestones`,
`GET /repos/{owner}/{repo}/issues?milestone={id}`,
`PATCH /repos/{owner}/{repo}/milestones/{id}`  
**Used by**: Project Manager

---

## issue-labeling

**Description**: Apply or remove labels on issues based on content,
age, or priority.

**Inputs**: Issue metadata, desired labels  
**Outputs**: Labels applied  
**GitHub APIs**: `POST /repos/{owner}/{repo}/issues/{number}/labels`,
`DELETE /repos/{owner}/{repo}/issues/{number}/labels/{name}`  
**Used by**: Project Manager

---

## discussion-creation

**Description**: Create a GitHub Discussion (if Discussions are enabled)
or fall back to creating a labeled GitHub Issue.

**Inputs**: Title, body (Markdown), preferred category  
**Outputs**: Discussion or Issue URL  
**GitHub APIs**: GraphQL `createDiscussion` mutation;
`POST /repos/{owner}/{repo}/issues` (fallback)  
**Used by**: Project Manager, Product Owner, Council Moderator

---

## discussion-facilitation

**Description**: Open a GitHub Discussion to invite community or team
input; link related issues for cross-referencing.

**Inputs**: Topic, related issue numbers  
**Outputs**: Discussion with links to related issues  
**GitHub APIs**: GraphQL `createDiscussion`; issue cross-references  
**Used by**: Product Owner

---

## feature-suggestion

**Description**: Analyse the current codebase, open issues, and recent
commits to identify gaps and opportunities, then generate structured
feature request issues with user stories and acceptance criteria.

**Inputs**: Recent commit list, open issues, README  
**Outputs**: One or more GitHub Issues tagged `feature`  
**GitHub APIs**: `GET /repos/{owner}/{repo}/commits`,
`GET /repos/{owner}/{repo}/issues`,
`GET /repos/{owner}/{repo}/contents`  
**Used by**: Product Owner

---

## playwright-testing

**Description**: Detect whether the repository has a Playwright config,
install dependencies, run the test suite, and post a test-results report
as a PR comment or new Issue.

**Inputs**: Playwright config path, base URL  
**Outputs**: Test results report (pass/fail counts, screenshots on
failure)  
**Tools**: `npx playwright test`  
**Used by**: Product Owner

---

## product-analysis

**Description**: Read the README, recent commits, and open feature issues
to synthesise a product health report describing what has shipped, what is
in-flight, and what is missing.

**Inputs**: README, commits (last 30 days), open issues  
**Outputs**: Markdown product health summary  
**GitHub APIs**: `GET /repos/{owner}/{repo}/readme`,
`GET /repos/{owner}/{repo}/commits`,
`GET /repos/{owner}/{repo}/issues`  
**Used by**: Product Owner

---

## task-assignment

**Description**: Analyse real-time agent availability (in-progress workflow
runs) and historical performance metrics (success rate, average duration) to
produce dynamic assignment recommendations for open issues, then post a
workload dashboard.

**Inputs**: Workflow runs JSON (recent + active), open issues list  
**Outputs**: Per-issue assignment recommendations (agent name, confidence,
slash command); markdown dashboard posted as a Discussion or Issue  
**GitHub APIs**: `GET /repos/{owner}/{repo}/actions/runs`,
`GET /repos/{owner}/{repo}/issues`,
`POST /repos/{owner}/{repo}/issues/{number}/comments`,
GraphQL `createDiscussion`  
**Used by**: Task Assignment System

---

## skill-development-analysis

**Description**: Analyse historical workflow run metrics per agent to
identify performance gaps and generate personalized skill development
suggestions. Surfaces reliability, efficiency, and domain-specific
improvement areas based on success rate, run frequency, and average
duration. Each completed agent interaction can be captured as a feedback
submission, aggregated into a shared report, and rolled up into the
weekly summary. Respects per-agent opt-in preferences for reminder
notifications stored in the `SKILL_REMINDERS_OPT_IN` repository
variable. Supports collaboration feedback submissions through labelled
issues, including anonymous submissions, and aggregates those signals
in the report.

**Inputs**: Workflow runs JSON (last N days), `SKILL_REMINDERS_OPT_IN`
repository variable (JSON opt-in map), labelled `agent-feedback` issues  
**Outputs**: Markdown cross-agent feedback and skill development report
posted as a Discussion or Issue fallback  
**GitHub APIs**: `GET /repos/{owner}/{repo}/actions/runs`,
`GET /repos/{owner}/{repo}/issues`, GraphQL `createDiscussion`,
`POST /repos/{owner}/{repo}/issues`  
**Used by**: Project Manager

---

## agent-training

**Description**: Run structured training sessions for each agent based on a
predefined curriculum. For each agent, identifies the next pending topic
(ordered by difficulty), invokes the GitHub Models API to generate a scenario,
model answer, and improvement tips, then records the completed session as a
labelled GitHub Issue. Posts a full per-agent progress report weekly as a
Discussion or Issue. Creates priority-labelled alert issues for agents whose
completion rate falls below configured thresholds.

**Inputs**: GitHub Issues with label `training-progress` (fetched via
`gh issue list`), `TRAINING_MODEL` / `AGENT_MAX_TOKENS` / `AGENT_TEMPERATURE`
repository variables  
**Outputs**: Per-session GitHub Issues tagged `training-progress` +
`training-complete` (or `training-needs-review`); weekly progress report as a
Discussion or Issue  
**GitHub APIs**: `GET /repos/{owner}/{repo}/issues`,
`POST /repos/{owner}/{repo}/issues`,
GraphQL `createDiscussion`, GitHub Models chat completions  
**Used by**: Agent Skills Training workflow

---

## cross-agent-mentorship

**Description**: Allow agents to apply as mentors or mentees, generate skill-based mentor/mentee pairings, and track mentorship progress and outcomes over time.

**Inputs**: Mentorship application issues (role, skills offered, skills needed, goals), mentorship progress/outcome issues  
**Outputs**: Skill-based mentorship matches; markdown mentorship report posted as Discussion or Issue fallback  
**GitHub APIs**: `GET /repos/{owner}/{repo}/issues`, `POST /repos/{owner}/{repo}/issues`, GraphQL `createDiscussion`  
**Used by**: Cross-Agent Mentorship Program workflow

---

## agent-health-monitoring

**Description**: Collect recent workflow run history for every registered
agent, compute health metrics (success rate, failure count, average duration,
time since last run), and classify each agent as HEALTHY, DEGRADED, CRITICAL,
or INACTIVE.  Publishes a full health dashboard and emits structured alert
objects for downstream issue creation.

**Inputs**: Workflow runs JSON (last N hours), configurable success-rate and
inactivity thresholds  
**Outputs**: Markdown health dashboard; JSON alert array; JSON status map  
**GitHub APIs**: `GET /repos/{owner}/{repo}/actions/runs`,
GraphQL `createDiscussion`, `POST /repos/{owner}/{repo}/issues`  
**Used by**: Agent Health Monitor

---

## cross-agent-communication

**Description**: Implement the standardized cross-agent communication protocol
defined in `docs/cross-agent-communication-protocol.md`. Each agent produces a
brief status update in plain English using the defined message format
(`From / Context / Update / Next Step`). Casey synthesises the updates into a
readable digest and posts it as a GitHub Discussion so that human contributors
can read and participate. Stalled threads (`comms-open` issues with no activity
for ≥ 14 days) receive an advancement comment and are labeled
`comms-pending-human`. A quarterly protocol-review Discussion invites all
agents and humans to submit feedback for the next protocol version.

**Inputs**: Open issue list, recent commits, stale `comms-open` issues,
optional topic or context  
**Outputs**: Weekly communication digest posted as Discussion (or Issue
fallback); advancement comments on stale threads; quarterly review Discussion  
**GitHub APIs**: GraphQL `createDiscussion`, `POST /repos/{owner}/{repo}/issues`,
`POST /repos/{owner}/{repo}/issues/{number}/comments`,
`PATCH /repos/{owner}/{repo}/issues/{number}`  
**Used by**: Cross-Agent Communication Protocol workflow

---

## personality-profiling

**Description**: Manage customizable personality profiles for each agent based on
predefined traits (`analytical`, `creative`, `empathetic`, `decisive`,
`collaborative`, `risk-aware`, `strategic`). Profiles are stored as labelled
GitHub Issues so they can be created and updated by any agent. The skill also
computes trait-based compatibility scores and surfaces optimal agent pairings
for specific tasks or collaborations.

**Inputs**: GitHub Issues with label `personality-profile` or
`personality-profile-update` (containing agent name, traits, strengths, and
working style)
**Outputs**: Markdown personality profile and pairing-suggestion report posted
as a Discussion or Issue fallback; JSON pairings array for downstream
automation
**GitHub APIs**: `GET /repos/{owner}/{repo}/issues`,
`POST /repos/{owner}/{repo}/issues`, GraphQL `createDiscussion`
**Used by**: Agent Personality Profiles workflow, Project Manager

---

## Adding a New Skill

1. Add an entry to this file with the required fields:
   `Description`, `Inputs`, `Outputs`, `GitHub APIs`, `Used by`.
2. Implement the skill as a step (or composite action) inside the relevant
   agent workflow.
3. Reference the skill in the agent's **Skills Used** list in
   [`agents.md`](./agents.md).
