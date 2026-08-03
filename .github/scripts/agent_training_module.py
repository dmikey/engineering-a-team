"""Agent Skills Training Module.

Manages a structured training curriculum for each agent, tracks per-agent
progress by reading labelled GitHub Issues, and generates Markdown progress
reports and JSON alert payloads for agents that are falling behind.

The workflow (``agent-training.yml``) feeds issue data fetched with
``gh issue list`` into this script via ``--input``.

Training session issues use the label ``training-progress`` and follow the
title convention::

    Training Session: {agent} — {topic_id}

A completed session carries the additional label ``training-complete``.

Output formats (``--output-format``):
  report       Full Markdown training progress report (default).
  alerts-json  JSON array of alert objects for agents below the completion
               threshold; used by the training workflow to create targeted
               Project Manager issues.
  next-topics  JSON array of the next pending topic for each agent (used to
               drive per-agent training sessions in the same workflow run).
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

# ── Training curriculum ───────────────────────────────────────────────────────

TRAINING_CURRICULUM: dict[str, list[dict]] = {
    "Quinn (QA Engineer)": [
        {
            "id": "code-review-best-practices",
            "name": "Code Review Best Practices",
            "description": (
                "Review a pull request diff for logic errors, code style, "
                "test coverage, and documentation completeness."
            ),
            "skill": "code-review",
            "difficulty": "intermediate",
        },
        {
            "id": "security-vulnerability-identification",
            "name": "Security Vulnerability Identification",
            "description": (
                "Identify OWASP Top 10 vulnerabilities in a code sample and "
                "classify each finding by severity."
            ),
            "skill": "security-scan",
            "difficulty": "advanced",
        },
        {
            "id": "issue-severity-classification",
            "name": "Issue Severity Classification",
            "description": (
                "Classify a set of bug reports by severity (CRITICAL / HIGH / "
                "MEDIUM / LOW) and explain the reasoning."
            ),
            "skill": "issue-creation",
            "difficulty": "beginner",
        },
        {
            "id": "pr-diff-analysis",
            "name": "PR Diff Analysis",
            "description": (
                "Analyse a multi-file PR diff to summarise the intent of the "
                "change and highlight areas that need closer review."
            ),
            "skill": "pr-feedback",
            "difficulty": "intermediate",
        },
    ],
    "Morgan (Project Manager)": [
        {
            "id": "backlog-prioritization",
            "name": "Backlog Prioritisation",
            "description": (
                "Given a list of open issues, apply MoSCoW prioritisation and "
                "assign labels with a brief justification for each."
            ),
            "skill": "backlog-grooming",
            "difficulty": "intermediate",
        },
        {
            "id": "sprint-planning",
            "name": "Sprint Planning",
            "description": (
                "Construct a two-week sprint plan from a prioritised backlog, "
                "balancing effort estimates and agent availability."
            ),
            "skill": "milestone-management",
            "difficulty": "intermediate",
        },
        {
            "id": "milestone-risk-assessment",
            "name": "Milestone Risk Assessment",
            "description": (
                "Identify risks for an approaching milestone, estimate slip "
                "probability, and recommend mitigation actions."
            ),
            "skill": "milestone-management",
            "difficulty": "advanced",
        },
        {
            "id": "skill-gap-analysis",
            "name": "Skill Gap Analysis",
            "description": (
                "Review agent performance metrics and identify skill gaps "
                "with concrete improvement recommendations."
            ),
            "skill": "skill-development-analysis",
            "difficulty": "intermediate",
        },
    ],
    "Alex (Product Owner)": [
        {
            "id": "feature-requirements-writing",
            "name": "Feature Requirements Writing",
            "description": (
                "Write a complete user story with acceptance criteria and "
                "definition-of-done for a feature request."
            ),
            "skill": "feature-suggestion",
            "difficulty": "beginner",
        },
        {
            "id": "acceptance-criteria-definition",
            "name": "Acceptance Criteria Definition",
            "description": (
                "Given a vague feature description, produce measurable, "
                "testable acceptance criteria following the Given/When/Then format."
            ),
            "skill": "feature-suggestion",
            "difficulty": "intermediate",
        },
        {
            "id": "product-health-analysis",
            "name": "Product Health Analysis",
            "description": (
                "Synthesise a product health report from a README, recent "
                "commits, and open issues, identifying shipped work and gaps."
            ),
            "skill": "product-analysis",
            "difficulty": "intermediate",
        },
        {
            "id": "roadmap-prioritization",
            "name": "Roadmap Prioritisation",
            "description": (
                "Rank a set of proposed features by business value and effort, "
                "producing a prioritised roadmap with rationale."
            ),
            "skill": "product-analysis",
            "difficulty": "advanced",
        },
    ],
    "Casey (Council Moderator)": [
        {
            "id": "consensus-building",
            "name": "Consensus Building",
            "description": (
                "Facilitate a structured discussion between agents with "
                "conflicting viewpoints and guide them to a decision."
            ),
            "skill": "discussion-facilitation",
            "difficulty": "advanced",
        },
        {
            "id": "multi-perspective-synthesis",
            "name": "Multi-Perspective Synthesis",
            "description": (
                "Summarise the key perspectives from a multi-agent discussion "
                "into a concise, balanced decision document."
            ),
            "skill": "discussion-creation",
            "difficulty": "intermediate",
        },
        {
            "id": "conflict-resolution",
            "name": "Conflict Resolution",
            "description": (
                "Identify the root cause of a disagreement between agents and "
                "propose a resolution path acceptable to all parties."
            ),
            "skill": "discussion-facilitation",
            "difficulty": "advanced",
        },
    ],
}

# ── Thresholds ────────────────────────────────────────────────────────────────

COMPLETION_WARN = 50.0   # below this → training reminder
COMPLETION_CRIT = 25.0   # below this → critical training alert

# ── Progress collection ───────────────────────────────────────────────────────


def parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def collect_training_progress(issues: list[dict]) -> dict[str, set[str]]:
    """Parse GitHub Issues to determine which training topics each agent has completed.

    An issue is counted as a completed session when it:
      - Has the label ``training-complete``, AND
      - Has a title matching ``Training Session: {agent} — {topic_id}``.

    Returns a dict mapping agent name → set of completed topic IDs.
    """
    progress: dict[str, set[str]] = {agent: set() for agent in TRAINING_CURRICULUM}

    for issue in issues:
        labels = {lbl.get("name", "") for lbl in issue.get("labels", [])}
        if "training-complete" not in labels:
            continue

        title = issue.get("title", "")
        if not title.startswith("Training Session: "):
            continue

        remainder = title[len("Training Session: "):]
        if " \u2014 " not in remainder:
            continue
        agent_name, topic_id = remainder.split(" \u2014 ", 1)

        if agent_name in progress:
            progress[agent_name].add(topic_id.strip())

    return progress


# ── Completion metrics ────────────────────────────────────────────────────────


def calculate_completion_rate(agent: str, completed: set[str]) -> float:
    """Return the training completion percentage for an agent (0–100)."""
    topics = TRAINING_CURRICULUM.get(agent, [])
    if not topics:
        return 100.0
    return len(completed & {t["id"] for t in topics}) / len(topics) * 100.0


def get_pending_topics(agent: str, completed: set[str]) -> list[dict]:
    """Return training topics not yet completed by the agent, ordered by difficulty."""
    difficulty_order = {"beginner": 0, "intermediate": 1, "advanced": 2}
    topics = TRAINING_CURRICULUM.get(agent, [])
    pending = [t for t in topics if t["id"] not in completed]
    pending.sort(key=lambda t: difficulty_order.get(t["difficulty"], 1))
    return pending


# ── Alert generation ──────────────────────────────────────────────────────────


def generate_alerts(progress: dict[str, set[str]]) -> list[dict]:
    """Return alert dicts for agents whose completion rate is below thresholds.

    Severity levels:
      critical  Completion rate below ``COMPLETION_CRIT``.
      warning   Completion rate below ``COMPLETION_WARN``.

    Each dict contains: agent, severity, reason, completion_rate,
    completed_count, total_topics, pending_topics.
    """
    alerts: list[dict] = []
    for agent, completed in progress.items():
        topics = TRAINING_CURRICULUM.get(agent, [])
        total = len(topics)
        rate = calculate_completion_rate(agent, completed)
        completed_count = len(completed & {t["id"] for t in topics})
        pending = get_pending_topics(agent, completed)

        severity: str | None = None
        reason: str | None = None

        if rate < COMPLETION_CRIT:
            severity = "critical"
            reason = (
                f"**{agent}** has completed only {completed_count}/{total} "
                f"training topics ({rate:.0f}%), which is critically below "
                f"the {COMPLETION_CRIT:.0f}% threshold."
            )
        elif rate < COMPLETION_WARN:
            severity = "warning"
            reason = (
                f"**{agent}** has completed {completed_count}/{total} "
                f"training topics ({rate:.0f}%), below the recommended "
                f"{COMPLETION_WARN:.0f}% threshold."
            )

        if severity:
            alerts.append(
                {
                    "agent": agent,
                    "severity": severity,
                    "reason": reason,
                    "completion_rate": round(rate, 1),
                    "completed_count": completed_count,
                    "total_topics": total,
                    "pending_topics": [t["id"] for t in pending],
                }
            )

    return alerts


# ── Next-topics selection ─────────────────────────────────────────────────────


def select_next_topics(progress: dict[str, set[str]]) -> list[dict]:
    """Return the next pending training topic for each agent (one per agent).

    Used by the workflow to drive individual training sessions.
    """
    next_topics: list[dict] = []
    for agent, completed in progress.items():
        pending = get_pending_topics(agent, completed)
        if pending:
            next_topics.append(
                {
                    "agent": agent,
                    "topic": pending[0],
                }
            )
    return next_topics


# ── Markdown rendering ────────────────────────────────────────────────────────

_DIFFICULTY_EMOJI = {
    "beginner": "🟢",
    "intermediate": "🟡",
    "advanced": "🔴",
}


def render_markdown(
    progress: dict[str, set[str]],
    current_date: str,
    workflow_url: str,
) -> str:
    lines = [
        f"# 🎓 Agent Skills Training Progress — {current_date}",
        "",
        "> Generated by the Agent Skills Training Module",
        "",
        "---",
        "",
    ]

    for agent, completed in progress.items():
        topics = TRAINING_CURRICULUM.get(agent, [])
        total = len(topics)
        rate = calculate_completion_rate(agent, completed)
        completed_count = len(completed & {t["id"] for t in topics})
        pending = get_pending_topics(agent, completed)

        if rate >= 100.0:
            status_badge = "✅ All topics complete"
        elif rate >= COMPLETION_WARN:
            status_badge = "🔵 In progress"
        elif rate >= COMPLETION_CRIT:
            status_badge = "⚠️ Below target"
        else:
            status_badge = "🚨 Critical — needs attention"

        lines += [
            f"## {agent}",
            "",
            f"**Completion**: {completed_count}/{total} topics "
            f"({rate:.0f}%) | {status_badge}",
            "",
            "| Topic | Skill | Difficulty | Status |",
            "|-------|-------|-----------|--------|",
        ]

        for topic in topics:
            diff_emoji = _DIFFICULTY_EMOJI.get(topic["difficulty"], "⚪")
            done = "✅ Complete" if topic["id"] in completed else "⏳ Pending"
            lines.append(
                f"| {topic['name']} | `{topic['skill']}` | "
                f"{diff_emoji} {topic['difficulty']} | {done} |"
            )

        if pending:
            next_topic = pending[0]
            lines += [
                "",
                f"**Next topic**: {next_topic['name']} "
                f"({_DIFFICULTY_EMOJI.get(next_topic['difficulty'], '')} "
                f"{next_topic['difficulty']})",
                "",
                f"> {next_topic['description']}",
            ]

        lines += ["", "---", ""]

    lines += [
        "## Training Curriculum Summary",
        "",
        "| Agent | Completed | Total | Completion Rate |",
        "|-------|-----------|-------|----------------|",
    ]
    for agent, completed in progress.items():
        topics = TRAINING_CURRICULUM.get(agent, [])
        total = len(topics)
        rate = calculate_completion_rate(agent, completed)
        completed_count = len(completed & {t["id"] for t in topics})
        lines.append(
            f"| {agent} | {completed_count} | {total} | {rate:.0f}% |"
        )

    lines += [
        "",
        "---",
        f"*🤖 Automated report by Agent Skills Training Module · "
        f"[Workflow run]({workflow_url})*",
    ]

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent Skills Training Module — progress tracking and reporting"
    )
    parser.add_argument(
        "--input",
        default="/tmp/training-issues.json",
        help="Path to GitHub Issues JSON payload (from gh issue list)",
    )
    parser.add_argument(
        "--output-format",
        choices=["report", "alerts-json", "next-topics"],
        default="report",
        help=(
            "'report' prints a full Markdown training progress report; "
            "'alerts-json' prints a JSON array of alert objects; "
            "'next-topics' prints a JSON array of the next pending topic per agent."
        ),
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    current_date = os.environ.get("CURRENT_DATE", now.strftime("%Y-%m-%d"))
    workflow_url = os.environ.get("WORKFLOW_URL", "")

    with open(args.input, encoding="utf-8") as fh:
        issues = json.load(fh)

    progress = collect_training_progress(issues)

    if args.output_format == "alerts-json":
        alerts = generate_alerts(progress)
        print(json.dumps(alerts, indent=2))
    elif args.output_format == "next-topics":
        next_topics = select_next_topics(progress)
        print(json.dumps(next_topics, indent=2))
    else:
        print(render_markdown(progress, current_date, workflow_url))


if __name__ == "__main__":
    main()
