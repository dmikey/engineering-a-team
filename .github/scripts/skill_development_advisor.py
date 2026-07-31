"""Cross-Agent Skill Development Advisor.

Analyses agent performance metrics and generates personalized skill
development suggestions for each agent. Called by the project-manager
workflow when the ``skill-development-suggestions`` task is selected,
and automatically by the skill-development-tracking workflow after each
agent run completes.

Opt-in reminders are controlled by the ``SKILL_REMINDERS_OPT_IN``
repository variable, which holds a JSON object mapping agent names to
``true`` / ``false``:

    {"Quinn (QA Engineer)": true, "Morgan (Project Manager)": false}

Set the variable in **Settings → Secrets and variables → Variables**.

Output formats (``--output-format``):
  report       Full Markdown skill development report (default).
  alerts-json  JSON array of agent alert objects for agents falling below
               performance thresholds; used by the tracking workflow to
               create targeted Project Manager issues.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from statistics import mean


# ── Agent definitions ────────────────────────────────────────────────────────

AGENT_WORKFLOWS: dict[str, str] = {
    "Quinn (QA Engineer)": ".github/workflows/qa-engineer.yml",
    "Morgan (Project Manager)": ".github/workflows/project-manager.yml",
    "Alex (Product Owner)": ".github/workflows/product-owner.yml",
    "Casey (Council Moderator)": ".github/workflows/council-discussion.yml",
}

AGENT_SKILLS: dict[str, list[str]] = {
    "Quinn (QA Engineer)": [
        "code-review",
        "issue-creation",
        "pr-feedback",
        "security-scan",
    ],
    "Morgan (Project Manager)": [
        "backlog-grooming",
        "milestone-management",
        "discussion-creation",
        "issue-labeling",
        "skill-development-analysis",
    ],
    "Alex (Product Owner)": [
        "feature-suggestion",
        "playwright-testing",
        "issue-creation",
        "discussion-facilitation",
        "product-analysis",
    ],
    "Casey (Council Moderator)": [
        "discussion-creation",
        "discussion-facilitation",
    ],
}

WORKFLOW_NAME_TO_AGENT: dict[str, str] = {
    "QA Engineer Agent": "Quinn (QA Engineer)",
    "Project Manager Agent": "Morgan (Project Manager)",
    "Product Owner Agent": "Alex (Product Owner)",
    "Council Discussion": "Casey (Council Moderator)",
}

# ── Thresholds ────────────────────────────────────────────────────────────────

SUCCESS_RATE_WARN = 85.0   # below this → reliability improvement suggestions
SUCCESS_RATE_CRIT = 70.0   # below this → critical reliability suggestions
SLOW_DURATION_WARN = 5.0   # above this (minutes) → efficiency suggestion
FEW_RUNS_WARN = 3          # fewer than this → underutilisation suggestion

FAILURE_CONCLUSIONS = {
    "cancelled",
    "failure",
    "startup_failure",
    "timed_out",
}


# ── Metrics collection ────────────────────────────────────────────────────────

def calculate_success_rate(total: int, failures: int) -> float:
    """Return success rate percentage while safely handling zero runs."""
    return ((total - failures) / total * 100.0) if total else 100.0


def parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def collect_metrics(
    runs: list[dict],
    since: datetime,
    agent_workflows: dict[str, str] | None = None,
) -> dict[str, dict]:
    """Aggregate workflow run data into per-agent performance metrics."""
    workflows = agent_workflows or AGENT_WORKFLOWS
    metrics: dict[str, dict] = {
        agent: {"runs": 0, "failures": 0, "durations": [], "last_run": None}
        for agent in workflows
    }

    for run in runs:
        path = run.get("path") or ""
        created_raw = run.get("created_at")
        updated_raw = run.get("updated_at")
        status = run.get("status")
        conclusion = run.get("conclusion")

        if not created_raw:
            continue
        created_at = parse_ts(created_raw)
        if created_at < since:
            continue

        matched_agent = None
        for agent, workflow_path in workflows.items():
            if path.endswith(workflow_path):
                matched_agent = agent
                break
        if matched_agent is None:
            continue

        data = metrics[matched_agent]

        # Skip runs where every job was skipped (e.g. the workflow fired on an
        # `issues: labeled` event but the label was not `needs-qa`).  These are
        # expected no-ops, not agent failures, and must not inflate the failure
        # count or distort duration averages.
        if conclusion == "skipped":
            continue

        data["runs"] += 1
        if data["last_run"] is None or created_at > data["last_run"]:
            data["last_run"] = created_at
        normalized_conclusion = (conclusion or "").strip().lower()
        if status == "completed" and normalized_conclusion in FAILURE_CONCLUSIONS:
            data["failures"] += 1
        if status == "completed" and updated_raw:
            updated_at = parse_ts(updated_raw)
            duration_minutes = max(
                (updated_at - created_at).total_seconds() / 60.0, 0.0
            )
            data["durations"].append(duration_minutes)

    return metrics


# ── Suggestion generation ─────────────────────────────────────────────────────

def generate_suggestions(
    agent: str,
    data: dict,
    skills: list[str],
) -> list[str]:
    """Return skill development suggestion strings for a single agent."""
    suggestions: list[str] = []
    total = data["runs"]
    failures = data["failures"]
    durations = data["durations"]
    avg_dur = mean(durations) if durations else 0.0
    success_rate = calculate_success_rate(total, failures)

    if total == 0:
        suggestions.append(
            "No runs recorded in the analysis period. Review trigger configuration "
            "and ensure the agent is active."
        )
        return suggestions

    if total < FEW_RUNS_WARN:
        suggestions.append(
            f"Only {total} run(s) recorded. Consider reviewing the agent's trigger "
            "schedule to increase utilisation."
        )

    if success_rate < SUCCESS_RATE_CRIT:
        suggestions.append(
            f"Success rate is critically low ({success_rate:.1f}%). Prioritise "
            "reviewing error logs, improving prompt robustness, and adding error "
            "handling to core skills: "
            + ", ".join(f"`{s}`" for s in skills[:3])
            + "."
        )
    elif success_rate < SUCCESS_RATE_WARN:
        suggestions.append(
            f"Success rate ({success_rate:.1f}%) is below the recommended threshold "
            f"of {SUCCESS_RATE_WARN:.0f}%. Focus on improving reliability for the "
            f"`{skills[0]}` skill."
        )

    if avg_dur > SLOW_DURATION_WARN and durations:
        suggestions.append(
            f"Average run duration is {avg_dur:.1f} min. Review long-running steps "
            "and consider caching or parallelisation to improve efficiency."
        )

    # Agent-specific suggestions based on skill set
    if "security-scan" in skills and success_rate < SUCCESS_RATE_WARN:
        suggestions.append(
            "Security scanning (`security-scan`) may benefit from updated detection "
            "patterns and additional test cases covering OWASP Top 10."
        )
    if "playwright-testing" in skills and success_rate < SUCCESS_RATE_WARN:
        suggestions.append(
            "Playwright test runs show instability. Review browser configuration, "
            "increase timeouts, and ensure test fixtures are up to date."
        )
    if "backlog-grooming" in skills and total > 0 and failures / total > 0.2:
        suggestions.append(
            "Backlog grooming has a higher-than-expected failure rate. Check that "
            "the model prompt handles edge cases in issue data (empty bodies, "
            "unusual labels)."
        )

    if not suggestions:
        suggestions.append(
            f"Performance looks healthy (success rate: {success_rate:.1f}%, "
            f"avg duration: {avg_dur:.1f} min). Continue monitoring for regressions."
        )

    return suggestions


# ── Trend analysis ────────────────────────────────────────────────────────────

def collect_trend(
    current_metrics: dict[str, dict],
    prior_metrics: dict[str, dict],
) -> dict[str, dict]:
    """Compare current and prior period metrics and return per-agent trend data.

    Each entry in the returned dict contains:
      current_rate   Success rate (%) for the current period.
      prior_rate     Success rate (%) for the prior period.
      delta          Difference (current − prior).
      current_runs   Number of runs in the current period.
      prior_runs     Number of runs in the prior period.
      direction      ``"up"`` / ``"down"`` / ``"stable"`` based on delta.
    """
    trend: dict[str, dict] = {}
    for agent in current_metrics:
        cur = current_metrics[agent]
        pri = prior_metrics.get(agent, {"runs": 0, "failures": 0, "durations": [], "last_run": None})

        cur_rate = calculate_success_rate(cur["runs"], cur["failures"])
        pri_rate = calculate_success_rate(pri["runs"], pri["failures"])
        delta = cur_rate - pri_rate

        if delta > 1.0:
            direction = "up"
        elif delta < -1.0:
            direction = "down"
        else:
            direction = "stable"

        trend[agent] = {
            "current_rate": cur_rate,
            "prior_rate": pri_rate,
            "delta": delta,
            "current_runs": cur["runs"],
            "prior_runs": pri["runs"],
            "direction": direction,
        }
    return trend


# ── Alert generation ──────────────────────────────────────────────────────────

def generate_alerts(
    metrics: dict[str, dict],
    period_days: int,
) -> list[dict]:
    """Return a list of alert dicts for agents with concerning performance.

    Severity levels:
      critical  Success rate below ``SUCCESS_RATE_CRIT``.
      warning   Success rate below ``SUCCESS_RATE_WARN`` or zero runs.

    Each dict in the returned list contains:
      agent        Agent display name.
      severity     ``"critical"`` or ``"warning"``.
      reason       Human-readable description of the issue.
      success_rate Current success rate (%).
      runs         Total run count in the analysis period.
      failures     Failure count.
      suggestions  List of skill development suggestion strings.
    """
    alerts: list[dict] = []
    for agent, data in metrics.items():
        skills = AGENT_SKILLS.get(agent, [])
        total = data["runs"]
        failures = data["failures"]
        success_rate = calculate_success_rate(total, failures)

        severity: str | None = None
        reason: str | None = None

        if total == 0:
            severity = "warning"
            reason = (
                f"No workflow runs recorded for **{agent}** in the last "
                f"{period_days} days. Review trigger configuration and ensure "
                "the agent is active."
            )
        elif success_rate < SUCCESS_RATE_CRIT:
            severity = "critical"
            reason = (
                f"**{agent}** success rate is critically low "
                f"({success_rate:.1f}%) — below the {SUCCESS_RATE_CRIT:.0f}% "
                "critical threshold."
            )
        elif success_rate < SUCCESS_RATE_WARN:
            severity = "warning"
            reason = (
                f"**{agent}** success rate ({success_rate:.1f}%) is below the "
                f"recommended {SUCCESS_RATE_WARN:.0f}% threshold."
            )

        if severity:
            alerts.append(
                {
                    "agent": agent,
                    "severity": severity,
                    "reason": reason,
                    "success_rate": round(success_rate, 1),
                    "runs": total,
                    "failures": failures,
                    "suggestions": generate_suggestions(agent, data, skills),
                }
            )

    return alerts


# ── Opt-in reminders ──────────────────────────────────────────────────────────

def load_reminders_opt_in(raw: str) -> dict[str, bool]:
    """Parse the SKILL_REMINDERS_OPT_IN JSON env var into a bool map."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        agent: enabled
        for agent, enabled in data.items()
        if agent in AGENT_SKILLS and isinstance(enabled, bool)
    }


def load_latest_interaction(raw: str) -> dict | None:
    """Parse the latest workflow interaction JSON into a normalized dict."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    workflow_name = str(data.get("workflow_name") or "").strip()
    workflow_path = str(data.get("workflow_path") or "").strip()
    agent = None

    if workflow_name:
        agent = WORKFLOW_NAME_TO_AGENT.get(workflow_name)
    if not agent and workflow_path:
        for candidate, path in AGENT_WORKFLOWS.items():
            if workflow_path.endswith(path):
                agent = candidate
                break

    return {
        "agent": agent or "Unknown Agent",
        "workflow_name": workflow_name or "Unknown Workflow",
        "workflow_path": workflow_path,
        "run_number": data.get("run_number", ""),
        "conclusion": str(data.get("conclusion") or "unknown"),
        "html_url": str(data.get("html_url") or ""),
        "created_at": str(data.get("created_at") or ""),
        "updated_at": str(data.get("updated_at") or ""),
        "event": str(data.get("event") or ""),
    }


# ── Markdown rendering ────────────────────────────────────────────────────────

def render_markdown(
    metrics: dict[str, dict],
    reminders: dict[str, bool],
    current_date: str,
    period_days: int,
    workflow_url: str,
    trend: dict[str, dict] | None = None,
    latest_interaction: dict | None = None,
) -> str:
    lines = [
        f"# 🤝 Cross-Agent Feedback & Skill Development Report — {current_date}",
        "",
        "> Generated by Morgan (Project Manager Agent)",
        "",
        f"**Analysis Period**: Last {period_days} days",
        "",
        "---",
        "",
    ]

    if latest_interaction:
        run_number = latest_interaction.get("run_number")
        run_suffix = f" run #{run_number}" if str(run_number).strip() else ""
        html_url = latest_interaction.get("html_url", "")
        link = f"[View workflow run]({html_url})" if html_url else "Workflow run link unavailable"
        lines += [
            "## Latest Feedback Submission",
            "",
            f"- Agent feedback captured after **{latest_interaction['agent']}** completed "
            f"**{latest_interaction['workflow_name']}**{run_suffix}.",
            f"- Outcome: **{latest_interaction['conclusion'].upper()}**"
            + (
                f" · Trigger: `{latest_interaction['event']}`"
                if latest_interaction.get("event")
                else ""
            ),
            f"- {link}",
            "",
            "This report aggregates that interaction with recent team performance so every agent can review the latest feedback in one place.",
            "",
            "---",
            "",
        ]

    for agent, data in metrics.items():
        skills = AGENT_SKILLS.get(agent, [])
        suggestions = generate_suggestions(agent, data, skills)
        opt_in = reminders.get(agent, False)
        reminder_badge = "🔔 Reminders: ON" if opt_in else "🔕 Reminders: OFF"

        total = data["runs"]
        failures = data["failures"]
        success_rate = calculate_success_rate(total, failures)
        avg_dur = mean(data["durations"]) if data["durations"] else 0.0

        # Build trend indicator when prior-period data is available
        trend_badge = ""
        if trend and agent in trend:
            t = trend[agent]
            direction = t["direction"]
            delta = t["delta"]
            if direction == "up":
                trend_badge = f" | 📈 Trend: +{delta:.1f}pp vs prior period"
            elif direction == "down":
                trend_badge = f" | 📉 Trend: {delta:.1f}pp vs prior period"
            else:
                trend_badge = " | ➡️ Trend: stable vs prior period"

        lines += [
            f"## {agent}",
            "",
            f"**Skills**: {', '.join(f'`{s}`' for s in skills)}  ",
            f"**Runs**: {total} | **Success Rate**: {success_rate:.1f}% | "
            f"**Avg Duration**: {avg_dur:.1f} min | {reminder_badge}{trend_badge}",
            "",
            "### Aggregated Feedback",
            "",
        ]
        for i, suggestion in enumerate(suggestions, 1):
            lines.append(f"{i}. {suggestion}")
        lines += ["", "---", ""]

    example_opt_in = json.dumps(
        {agent: False for agent in AGENT_SKILLS}, indent=2
    )
    lines += [
        "## How to Enable Skill Reminders",
        "",
        "Set the repository variable `SKILL_REMINDERS_OPT_IN` to a JSON object "
        "mapping agent names to `true` or `false`. Example:",
        "",
        "```json",
        example_opt_in,
        "```",
        "",
        f"*🤖 Automated report by Morgan (Project Manager Agent) · "
        f"[Workflow run]({workflow_url})*",
    ]

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate cross-agent skill development suggestions"
    )
    parser.add_argument(
        "--input",
        default="/tmp/agent-runs.json",
        help="Path to workflow runs JSON payload (current period)",
    )
    parser.add_argument(
        "--prior-input",
        default="",
        help="Path to workflow runs JSON payload for the prior period (optional). "
             "When provided, trend data is included in the report.",
    )
    parser.add_argument(
        "--output-format",
        choices=["report", "alerts-json"],
        default="report",
        help="Output format: 'report' prints a full Markdown report; "
             "'alerts-json' prints a JSON array of alert objects for agents "
             "falling below performance thresholds.",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    try:
        period_days = int(os.environ.get("PERIOD_DAYS", "30"))
    except ValueError:
        period_days = 30
    since = now - timedelta(days=period_days)
    current_date = os.environ.get("CURRENT_DATE", now.strftime("%Y-%m-%d"))
    workflow_url = os.environ.get("WORKFLOW_URL", "")
    reminders_raw = os.environ.get("SKILL_REMINDERS_OPT_IN", "")
    latest_interaction_raw = os.environ.get("LATEST_INTERACTION", "")

    with open(args.input, encoding="utf-8") as fh:
        payload = json.load(fh)

    runs = payload.get("workflow_runs", [])
    metrics = collect_metrics(runs, since)

    # Load optional prior-period data for trend comparison
    trend: dict[str, dict] | None = None
    if args.prior_input:
        try:
            prior_since = since - timedelta(days=period_days)
            with open(args.prior_input, encoding="utf-8") as fh:
                prior_payload = json.load(fh)
            prior_runs = prior_payload.get("workflow_runs", [])
            prior_metrics = collect_metrics(prior_runs, prior_since)
            trend = collect_trend(metrics, prior_metrics)
        except (OSError, json.JSONDecodeError):
            trend = None

    if args.output_format == "alerts-json":
        alerts = generate_alerts(metrics, period_days)
        print(json.dumps(alerts, indent=2))
    else:
        reminders = load_reminders_opt_in(reminders_raw)
        latest_interaction = load_latest_interaction(latest_interaction_raw)
        print(
            render_markdown(
                metrics,
                reminders,
                current_date,
                period_days,
                workflow_url,
                trend,
                latest_interaction=latest_interaction,
            )
        )


if __name__ == "__main__":
    main()
