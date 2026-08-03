"""Agent Health Check.

Analyses recent workflow run data to produce a health dashboard and alert
list for every registered agent.  Called by the agent-health-check workflow
on a scheduled cadence (every 6 hours) and on demand.

Health status classification
-----------------------------
HEALTHY   — success rate ≥ WARN_THRESHOLD and ran within INACTIVITY_HOURS
DEGRADED  — success rate < WARN_THRESHOLD (but ≥ CRIT_THRESHOLD)
CRITICAL  — success rate < CRIT_THRESHOLD, OR last run was successful but
            the agent has not run at all in the analysis window
INACTIVE  — no runs recorded in the analysis window

Output formats (``--output-format``)
--------------------------------------
report       Full Markdown health dashboard (default).
alerts-json  JSON array of alert objects for agents that are not HEALTHY;
             used by the workflow to create targeted GitHub Issues.
status-json  JSON object mapping agent name → status string; useful for
             downstream steps that need a quick pass/fail signal.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from statistics import mean


# ── Agent registry ────────────────────────────────────────────────────────────

AGENT_WORKFLOWS: dict[str, str] = {
    "Quinn (QA Engineer)": ".github/workflows/qa-engineer.yml",
    "Morgan (Project Manager)": ".github/workflows/project-manager.yml",
    "Alex (Product Owner)": ".github/workflows/product-owner.yml",
    "Casey (Council Moderator)": ".github/workflows/council-discussion.yml",
}

# ── Thresholds ────────────────────────────────────────────────────────────────

DEFAULT_WARN_THRESHOLD = 85.0   # success rate % below which → DEGRADED
DEFAULT_CRIT_THRESHOLD = 70.0   # success rate % below which → CRITICAL
DEFAULT_INACTIVITY_HOURS = 48   # hours without a run → INACTIVE

# ── Status constants ──────────────────────────────────────────────────────────

STATUS_HEALTHY = "HEALTHY"
STATUS_DEGRADED = "DEGRADED"
STATUS_CRITICAL = "CRITICAL"
STATUS_INACTIVE = "INACTIVE"

STATUS_EMOJI = {
    STATUS_HEALTHY: "✅",
    STATUS_DEGRADED: "⚠️",
    STATUS_CRITICAL: "🔴",
    STATUS_INACTIVE: "💤",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


# ── Core metrics ──────────────────────────────────────────────────────────────

def collect_metrics(
    runs: list[dict],
    since: datetime,
    agent_workflows: dict[str, str] | None = None,
) -> dict[str, dict]:
    """Compute per-agent health metrics from a list of workflow run objects."""
    workflows = agent_workflows or AGENT_WORKFLOWS
    metrics: dict[str, dict] = {
        agent: {
            "runs": 0,
            "failures": 0,
            "durations": [],
            "last_run": None,
            "last_conclusion": None,
        }
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
        if not matched_agent:
            continue

        data = metrics[matched_agent]
        data["runs"] += 1

        if data["last_run"] is None or created_at > data["last_run"]:
            data["last_run"] = created_at
            data["last_conclusion"] = conclusion

        if status == "completed" and conclusion and conclusion != "success":
            data["failures"] += 1

        if status == "completed" and updated_raw:
            updated_at = parse_ts(updated_raw)
            duration_minutes = max(
                (updated_at - created_at).total_seconds() / 60.0, 0.0
            )
            data["durations"].append(duration_minutes)

    return metrics


def classify_status(
    data: dict,
    now: datetime,
    warn_threshold: float,
    crit_threshold: float,
    inactivity_hours: int,
) -> str:
    """Return a STATUS_* constant for a single agent."""
    total = data["runs"]
    if total == 0:
        return STATUS_INACTIVE

    failures = data["failures"]
    successes = max(total - failures, 0)
    success_rate = (successes / total * 100.0) if total else 0.0

    last_run: datetime | None = data["last_run"]
    hours_since_last = (
        (now - last_run).total_seconds() / 3600.0 if last_run else float("inf")
    )

    if hours_since_last >= inactivity_hours:
        return STATUS_INACTIVE

    if success_rate < crit_threshold:
        return STATUS_CRITICAL

    if success_rate < warn_threshold:
        return STATUS_DEGRADED

    return STATUS_HEALTHY


def build_rows(
    metrics: dict[str, dict],
    now: datetime,
    warn_threshold: float,
    crit_threshold: float,
    inactivity_hours: int,
) -> list[dict]:
    rows = []
    for agent, data in metrics.items():
        total = data["runs"]
        failures = data["failures"]
        successes = max(total - failures, 0)
        success_rate = (successes / total * 100.0) if total else 0.0
        avg_duration = mean(data["durations"]) if data["durations"] else 0.0
        last_dt: datetime | None = data["last_run"]
        last_run_str = last_dt.strftime("%Y-%m-%d %H:%M UTC") if last_dt else "Never"
        hours_since = (
            round((now - last_dt).total_seconds() / 3600.0, 1)
            if last_dt
            else None
        )

        status = classify_status(
            data, now, warn_threshold, crit_threshold, inactivity_hours
        )

        rows.append(
            {
                "agent": agent,
                "runs": total,
                "failures": failures,
                "success_rate": success_rate,
                "avg_duration": avg_duration,
                "last_run": last_run_str,
                "last_run_dt": last_dt,
                "hours_since_last_run": hours_since,
                "status": status,
                "last_conclusion": data["last_conclusion"],
            }
        )

    # Sort: unhealthy agents first, then by agent name
    status_order = {
        STATUS_CRITICAL: 0,
        STATUS_INACTIVE: 1,
        STATUS_DEGRADED: 2,
        STATUS_HEALTHY: 3,
    }
    rows.sort(key=lambda r: (status_order.get(r["status"], 9), r["agent"]))
    return rows


# ── Renderers ─────────────────────────────────────────────────────────────────

def render_markdown(
    rows: list[dict],
    current_date: str,
    period_hours: int,
    workflow_url: str,
    warn_threshold: float,
    crit_threshold: float,
) -> str:
    """Render a full Markdown health dashboard."""
    healthy = sum(1 for r in rows if r["status"] == STATUS_HEALTHY)
    degraded = sum(1 for r in rows if r["status"] == STATUS_DEGRADED)
    critical = sum(1 for r in rows if r["status"] == STATUS_CRITICAL)
    inactive = sum(1 for r in rows if r["status"] == STATUS_INACTIVE)
    total_agents = len(rows)

    if critical > 0 or inactive > 0:
        overall_emoji = "🔴"
        overall_label = "ATTENTION REQUIRED"
    elif degraded > 0:
        overall_emoji = "⚠️"
        overall_label = "DEGRADED"
    else:
        overall_emoji = "✅"
        overall_label = "ALL SYSTEMS HEALTHY"

    table_rows = "\n".join(
        f"| {STATUS_EMOJI[r['status']]} {r['status']} | {r['agent']} "
        f"| {r['runs']} | {r['success_rate']:.1f}% | {r['failures']} "
        f"| {r['avg_duration']:.1f} | {r['last_run']} |"
        for r in rows
    )

    notes = []
    for r in rows:
        if r["status"] != STATUS_HEALTHY:
            emoji = STATUS_EMOJI[r["status"]]
            if r["status"] == STATUS_INACTIVE:
                hours_info = (
                    f"{r['hours_since_last_run']}h ago"
                    if r["hours_since_last_run"] is not None
                    else "never"
                )
                notes.append(
                    f"- {emoji} **{r['agent']}** — No runs in the last "
                    f"{period_hours}h (last seen: {hours_info}). "
                    "Agent may be misconfigured or paused."
                )
            elif r["status"] == STATUS_CRITICAL:
                notes.append(
                    f"- {emoji} **{r['agent']}** — Success rate "
                    f"{r['success_rate']:.1f}% is below the critical "
                    f"threshold ({crit_threshold:.0f}%). Immediate attention needed."
                )
            elif r["status"] == STATUS_DEGRADED:
                notes.append(
                    f"- {emoji} **{r['agent']}** — Success rate "
                    f"{r['success_rate']:.1f}% is below the warning "
                    f"threshold ({warn_threshold:.0f}%). "
                    "Review recent failures."
                )

    notes_section = (
        "\n".join(notes)
        if notes
        else "_All agents are healthy — no action required._"
    )

    return "\n".join(
        [
            f"# {overall_emoji} Agent Health Check — {current_date}",
            "",
            f"> **Overall Status**: {overall_label}",
            f"> Generated automatically every 6 hours by the Agent Health Check workflow.",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total agents monitored | {total_agents} |",
            f"| ✅ Healthy | {healthy} |",
            f"| ⚠️ Degraded | {degraded} |",
            f"| 🔴 Critical | {critical} |",
            f"| 💤 Inactive | {inactive} |",
            f"| Analysis window | Last {period_hours}h |",
            "",
            "## Agent Health Status",
            "",
            "| Status | Agent | Runs | Success Rate | Failures | Avg Duration (min) | Last Run |",
            "|--------|-------|-----:|-------------:|---------:|-------------------:|---------|",
            table_rows,
            "",
            "## Alerts & Recommendations",
            "",
            notes_section,
            "",
            "---",
            f"*Thresholds: CRITICAL < {crit_threshold:.0f}% · DEGRADED < {warn_threshold:.0f}% · "
            f"INACTIVE = no runs in last {period_hours}h*  ",
            f"*🤖 Automated health check · [Workflow run]({workflow_url})*",
        ]
    )


def render_alerts_json(
    rows: list[dict],
    current_date: str,
    period_hours: int,
    warn_threshold: float,
    crit_threshold: float,
) -> str:
    """Return a JSON array of alert objects for non-healthy agents."""
    alerts = []
    for r in rows:
        if r["status"] == STATUS_HEALTHY:
            continue

        if r["status"] == STATUS_INACTIVE:
            title = f"🚨 Agent Health Alert: {r['agent']} is INACTIVE"
            description = (
                f"{r['agent']} has not executed in the last {period_hours} hours. "
                "The agent may be disabled, misconfigured, or encountering trigger failures."
            )
            severity = "high"
        elif r["status"] == STATUS_CRITICAL:
            title = f"🚨 Agent Health Alert: {r['agent']} is CRITICAL"
            description = (
                f"{r['agent']} has a success rate of {r['success_rate']:.1f}%, "
                f"which is below the critical threshold of {crit_threshold:.0f}%. "
                f"There were {r['failures']} failure(s) in {r['runs']} recent run(s)."
            )
            severity = "critical"
        else:  # DEGRADED
            title = f"⚠️ Agent Health Alert: {r['agent']} is DEGRADED"
            description = (
                f"{r['agent']} has a success rate of {r['success_rate']:.1f}%, "
                f"which is below the warning threshold of {warn_threshold:.0f}%. "
                f"There were {r['failures']} failure(s) in {r['runs']} recent run(s)."
            )
            severity = "medium"

        alerts.append(
            {
                "agent": r["agent"],
                "status": r["status"],
                "severity": severity,
                "title": title,
                "description": description,
                "success_rate": round(r["success_rate"], 1),
                "runs": r["runs"],
                "failures": r["failures"],
                "last_run": r["last_run"],
                "date": current_date,
            }
        )

    return json.dumps(alerts, indent=2)


def render_status_json(rows: list[dict]) -> str:
    """Return a JSON object mapping agent name → status string."""
    return json.dumps({r["agent"]: r["status"] for r in rows}, indent=2)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an agent health dashboard or alert list."
    )
    parser.add_argument(
        "--input",
        default="/tmp/health-runs.json",
        help="Path to workflow runs JSON payload",
    )
    parser.add_argument(
        "--output-format",
        default="report",
        choices=["report", "alerts-json", "status-json"],
        help="Output format (default: report)",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    period_hours = int(os.environ.get("PERIOD_HOURS", "24"))
    warn_threshold = float(os.environ.get("HEALTH_WARN_THRESHOLD", str(DEFAULT_WARN_THRESHOLD)))
    crit_threshold = float(os.environ.get("HEALTH_CRIT_THRESHOLD", str(DEFAULT_CRIT_THRESHOLD)))
    inactivity_hours = int(os.environ.get("HEALTH_INACTIVITY_HOURS", str(DEFAULT_INACTIVITY_HOURS)))
    current_date = os.environ.get("CURRENT_DATE", now.strftime("%Y-%m-%d %H:%M UTC"))
    workflow_url = os.environ.get("WORKFLOW_URL", "")

    since = now - timedelta(hours=period_hours)

    with open(args.input, encoding="utf-8") as fh:
        payload = json.load(fh)

    runs = payload.get("workflow_runs", [])
    metrics = collect_metrics(runs, since)
    rows = build_rows(metrics, now, warn_threshold, crit_threshold, inactivity_hours)

    if args.output_format == "alerts-json":
        print(render_alerts_json(rows, current_date, period_hours, warn_threshold, crit_threshold))
    elif args.output_format == "status-json":
        print(render_status_json(rows))
    else:
        print(render_markdown(rows, current_date, period_hours, workflow_url, warn_threshold, crit_threshold))


if __name__ == "__main__":
    main()
