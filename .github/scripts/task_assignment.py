"""Dynamic Task Assignment System — core logic.

Analyses agent workloads and performance metrics, then produces assignment
recommendations for open issues.  Called by the task-assignment workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

# ── Agent definitions ────────────────────────────────────────────────────────

AGENTS: dict[str, dict[str, Any]] = {
    "Quinn (QA Engineer)": {
        "workflow": ".github/workflows/qa-engineer.yml",
        "label_triggers": ["bug", "security", "qa-review", "needs-qa"],
        "keyword_triggers": ["bug", "error", "crash", "security", "vulnerability", "test", "fix"],
        "slash_command": "/qa",
    },
    "Morgan (Project Manager)": {
        "workflow": ".github/workflows/project-manager.yml",
        "label_triggers": ["sprint", "milestone", "blocked", "planning"],
        "keyword_triggers": ["milestone", "sprint", "plan", "schedule", "priority", "backlog", "assign"],
        "slash_command": "/pm groom-backlog",
    },
    "Alex (Product Owner)": {
        "workflow": ".github/workflows/product-owner.yml",
        "label_triggers": ["feature", "enhancement", "product-owner"],
        "keyword_triggers": ["feature", "enhancement", "user story", "acceptance criteria", "product"],
        "slash_command": "/po suggest-features",
    },
}

PERIOD_DAYS_WORKLOAD = 1   # 24 h window for "current workload"
PERIOD_DAYS_PERF = 30      # 30-day window for performance metrics


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


# ── Metrics collection ────────────────────────────────────────────────────────

def collect_agent_metrics(runs: list[dict]) -> dict[str, dict]:
    """Return workload and performance data for every agent."""
    now = datetime.now(timezone.utc)
    perf_since = now - timedelta(days=PERIOD_DAYS_PERF)
    workload_since = now - timedelta(days=PERIOD_DAYS_WORKLOAD)

    metrics: dict[str, dict] = {
        name: {
            "active_runs": 0,          # currently in_progress or queued
            "recent_runs": 0,          # runs in the last 24 h
            "perf_runs": 0,            # runs in the last 30 days
            "perf_failures": 0,        # failures in the last 30 days
            "durations": [],           # completed-run durations (minutes)
            "last_run": None,          # datetime of most-recent run
        }
        for name in AGENTS
    }

    for run in runs:
        path = run.get("path") or ""
        created_raw = run.get("created_at")
        updated_raw = run.get("updated_at")
        status = run.get("status", "")
        conclusion = run.get("conclusion", "")

        if not created_raw:
            continue
        created_at = _parse_ts(created_raw)

        matched = None
        for name, cfg in AGENTS.items():
            if path.endswith(cfg["workflow"]):
                matched = name
                break
        if matched is None:
            continue

        data = metrics[matched]

        # Active (in-progress or queued)
        if status in ("in_progress", "queued", "waiting"):
            data["active_runs"] += 1

        # Recent workload (last 24 h)
        if created_at >= workload_since:
            data["recent_runs"] += 1

        # Performance window (last 30 days)
        if created_at >= perf_since:
            data["perf_runs"] += 1
            if status == "completed" and conclusion and conclusion != "success":
                data["perf_failures"] += 1
            if status == "completed" and updated_raw:
                updated_at = _parse_ts(updated_raw)
                dur = max((updated_at - created_at).total_seconds() / 60.0, 0.0)
                data["durations"].append(dur)

        # Last run
        if data["last_run"] is None or created_at > data["last_run"]:
            data["last_run"] = created_at

    return metrics


def build_agent_scores(metrics: dict[str, dict]) -> dict[str, float]:
    """Compute a 0–100 availability+performance score per agent.

    Higher score = more suitable to receive a new task right now.

    Score = performance_component (0–70) + availability_component (0–30)
    """
    scores: dict[str, float] = {}
    for name, data in metrics.items():
        # Performance: success rate over last 30 days
        total = data["perf_runs"]
        failures = data["perf_failures"]
        success_rate = ((total - failures) / total * 100.0) if total else 100.0
        perf_score = success_rate * 0.70  # up to 70 points

        # Availability: penalise for active runs and recent workload
        # Each active run subtracts 10 points; each recent run beyond 2 subtracts 2.
        active_penalty = min(data["active_runs"] * 10, 30)
        workload_penalty = max(data["recent_runs"] - 2, 0) * 2
        avail_score = max(30 - active_penalty - workload_penalty, 0)

        scores[name] = round(perf_score + avail_score, 1)

    return scores


# ── Issue matching ────────────────────────────────────────────────────────────

def _score_issue_agent(issue: dict, agent_name: str, agent_cfg: dict) -> int:
    """Return a keyword/label match score for a given issue→agent pair."""
    score = 0
    labels = [lbl["name"].lower() for lbl in issue.get("labels", [])]
    title = (issue.get("title") or "").lower()
    body = (issue.get("body") or "").lower()

    for trigger in agent_cfg["label_triggers"]:
        if trigger.lower() in labels:
            score += 5

    for kw in agent_cfg["keyword_triggers"]:
        if kw in title:
            score += 2
        if kw in body:
            score += 1

    return score


def assign_issues(
    issues: list[dict],
    agent_scores: dict[str, float],
) -> list[dict]:
    """Return a list of assignment recommendations.

    Each entry contains:
        issue_number, issue_title, recommended_agent, confidence,
        reason, slash_command
    """
    recommendations = []

    for issue in issues:
        num = issue.get("number")
        title = issue.get("title", "")

        # Skip closed issues
        if issue.get("state") == "closed":
            continue

        best_agent = None
        best_combined = -1.0

        for agent_name, cfg in AGENTS.items():
            match_score = _score_issue_agent(issue, agent_name, cfg)
            avail_score = agent_scores.get(agent_name, 50.0)
            combined = match_score * 10 + avail_score  # weight both

            if combined > best_combined:
                best_combined = combined
                best_agent = agent_name

        if best_agent is None:
            continue

        cfg = AGENTS[best_agent]
        confidence = "HIGH" if best_combined >= 120 else ("MEDIUM" if best_combined >= 80 else "LOW")
        avail = agent_scores.get(best_agent, 50.0)

        recommendations.append(
            {
                "issue_number": num,
                "issue_title": title,
                "recommended_agent": best_agent,
                "availability_score": avail,
                "confidence": confidence,
                "slash_command": cfg["slash_command"],
            }
        )

    return recommendations


# ── Markdown rendering ────────────────────────────────────────────────────────

def render_workload_table(metrics: dict[str, dict], scores: dict[str, float]) -> str:
    lines = [
        "| Agent | Active Runs | 24 h Runs | Perf Runs (30d) | Failures (30d) | Success Rate | Avg Duration (min) | Score |",
        "|-------|:-----------:|:---------:|:---------------:|:--------------:|:------------:|-------------------:|------:|",
    ]
    for name, data in metrics.items():
        total = data["perf_runs"]
        failures = data["perf_failures"]
        success_rate = ((total - failures) / total * 100.0) if total else 100.0
        avg_dur = mean(data["durations"]) if data["durations"] else 0.0
        score = scores.get(name, 0.0)
        lines.append(
            f"| {name} | {data['active_runs']} | {data['recent_runs']} | {total} "
            f"| {failures} | {success_rate:.1f}% | {avg_dur:.1f} | {score:.1f} |"
        )
    return "\n".join(lines)


def render_assignment_table(recommendations: list[dict]) -> str:
    if not recommendations:
        return "_No open issues found for assignment._"
    lines = [
        "| Issue | Title | Recommended Agent | Score | Confidence | Slash Command |",
        "|------:|-------|-------------------|------:|:----------:|---------------|",
    ]
    for r in recommendations:
        lines.append(
            f"| #{r['issue_number']} | {r['issue_title'][:60]} "
            f"| {r['recommended_agent']} | {r['availability_score']:.1f} "
            f"| {r['confidence']} | `{r['slash_command']}` |"
        )
    return "\n".join(lines)


def render_dashboard(
    metrics: dict[str, dict],
    scores: dict[str, float],
    recommendations: list[dict],
    current_date: str,
    workflow_url: str,
) -> str:
    workload_table = render_workload_table(metrics, scores)
    assignment_table = render_assignment_table(recommendations)

    return "\n".join(
        [
            f"# 🎯 Dynamic Task Assignment Dashboard — {current_date}",
            "",
            "> Generated by the Task Assignment System",
            "",
            "## Agent Availability & Workload",
            "",
            "Scores are 0–100 (higher = more capacity).  "
            "**Active Runs** = workflows currently queued or running.",
            "",
            workload_table,
            "",
            "---",
            "",
            "## Task Assignment Recommendations",
            "",
            assignment_table,
            "",
            "---",
            "",
            "> **How assignments work**: Each open issue is scored against every agent "
            "based on keyword/label match and the agent's current availability score.  "
            "Use the slash command in the *Slash Command* column to dispatch the "
            "recommended agent.",
            "",
            f"*🤖 Task Assignment System · [Workflow run]({workflow_url})*",
        ]
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Dynamic Task Assignment System")
    parser.add_argument("--runs", required=True, help="Path to workflow runs JSON")
    parser.add_argument("--issues", required=True, help="Path to open issues JSON")
    parser.add_argument("--output", choices=["dashboard", "recommendations"], default="dashboard")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    current_date = os.environ.get("CURRENT_DATE", now.strftime("%Y-%m-%d"))
    workflow_url = os.environ.get("WORKFLOW_URL", "")

    with open(args.runs, encoding="utf-8") as fh:
        runs_payload = json.load(fh)
    runs = runs_payload.get("workflow_runs", [])

    with open(args.issues, encoding="utf-8") as fh:
        issues = json.load(fh)

    metrics = collect_agent_metrics(runs)
    scores = build_agent_scores(metrics)
    recommendations = assign_issues(issues, scores)

    if args.output == "recommendations":
        print(json.dumps(recommendations, indent=2, default=str))
    else:
        print(render_dashboard(metrics, scores, recommendations, current_date, workflow_url))


if __name__ == "__main__":
    main()
