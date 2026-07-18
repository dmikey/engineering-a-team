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

**Description**: Evaluate code changes for common security vulnerabilities:
injection, secrets exposure, insecure dependencies, OWASP Top 10 patterns.

**Inputs**: PR diff, file list  
**Outputs**: Findings list (CRITICAL / HIGH / MEDIUM / LOW) included in
the QA review  
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

## Adding a New Skill

1. Add an entry to this file with the required fields:
   `Description`, `Inputs`, `Outputs`, `GitHub APIs`, `Used by`.
2. Implement the skill as a step (or composite action) inside the relevant
   agent workflow.
3. Reference the skill in the agent's **Skills Used** list in
   [`agents.md`](./agents.md).
