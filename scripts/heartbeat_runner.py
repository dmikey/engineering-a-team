#!/usr/bin/env python3

from __future__ import annotations

import argparse
import curses
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MODELS_URL = "https://models.inference.ai.azure.com/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_MODEL_EVERY = 3
FAILURE_CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "startup_failure",
    "stale",
    "timed_out",
}
BLOCKING_REVIEW_DECISIONS = {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}
ACTIVE_RUN_STATUSES = {"queued", "in_progress", "waiting", "requested", "pending"}
RELEVANT_PR_WORKFLOWS = {
    "QA Engineer Agent",
    "PR Compliance Checks",
    "Copilot cloud agent",
}
WORKFLOW_COOLDOWNS = {
    "qa-engineer.yml": timedelta(hours=6),
    "task-assignment.yml": timedelta(hours=6),
    "project-manager.yml": timedelta(hours=12),
    "product-owner.yml": timedelta(hours=12),
}
AUTH_FAILURE_COOLDOWN = timedelta(hours=6)
PR_READY_COOLDOWN = timedelta(hours=6)
COPILOT_HANDOFF_COOLDOWN = timedelta(hours=12)
PR_SYNC_COOLDOWN = timedelta(hours=6)

GH_COMMAND_ENV: dict[str, str] | None = None
GH_AUTH_SOURCE = "gh-auth"
GH_MAX_RETRIES = 2

TRANSIENT_ERROR_MARKERS = (
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "tls handshake timeout",
    "502",
    "503",
    "504",
    "429",
    "rate limit",
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime | None = None) -> str:
    return (dt or now_utc()).replace(microsecond=0).isoformat()


def parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def is_transient_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in TRANSIENT_ERROR_MARKERS)


def run_command(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    attempts = max(1, GH_MAX_RETRIES)
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, attempts + 1):
        result = subprocess.run(command, capture_output=True, text=True, env=GH_COMMAND_ENV)
        last_result = result
        if result.returncode == 0:
            return result

        detail = (result.stderr.strip() or result.stdout.strip() or "command failed").lower()
        if attempt < attempts and is_transient_error(detail):
            continue
        break

    assert last_result is not None
    if check and last_result.returncode != 0:
        raise RuntimeError(last_result.stderr.strip() or last_result.stdout.strip() or "command failed")
    return last_result


def gh_json(args: list[str], *, default: Any = None, check: bool = True) -> Any:
    result = run_command(["gh", *args], check=check)
    if result.returncode != 0:
        return default
    text = result.stdout.strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if check:
            raise
        return default


def git_dir() -> Path:
    result = run_command(["git", "rev-parse", "--git-dir"])
    return Path(result.stdout.strip()).resolve()


def resolve_repo(explicit_repo: str | None) -> dict[str, str]:
    if explicit_repo:
        owner, name = explicit_repo.split("/", 1)
        default_branch = gh_json(
            ["repo", "view", explicit_repo, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"],
            default="main",
            check=False,
        )
        return {"nameWithOwner": explicit_repo, "owner": owner, "name": name, "defaultBranch": default_branch or "main"}

    info = gh_json(["repo", "view", "--json", "nameWithOwner,owner,name,defaultBranchRef"])
    return {
        "nameWithOwner": info["nameWithOwner"],
        "owner": info["owner"]["login"],
        "name": info["name"],
        "defaultBranch": info["defaultBranchRef"]["name"],
    }


def resolve_models_token() -> tuple[str | None, str]:
    for env_name in ("MODELS_TOKEN", "GH_MODELS_TOKEN"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value, env_name
    return None, "unconfigured"


def resolve_gh_command_env() -> tuple[dict[str, str] | None, str]:
    for env_name in ("HEARTBEAT_GH_TOKEN", "GH_USER_PAT"):
        value = os.environ.get(env_name, "").strip()
        if value:
            env = os.environ.copy()
            env["GH_TOKEN"] = value
            return env, env_name
    return None, "gh-auth"


def state_paths() -> tuple[Path, Path]:
    base = git_dir() / "heartbeat-runner"
    base.mkdir(parents=True, exist_ok=True)
    return base / "state.json", base / "overview.md"


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"events": {}, "heartbeats": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"events": {}, "heartbeats": 0}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def read_collaboration_rules(repo_root: Path) -> str:
    rules_file = repo_root / ".github" / "collaboration-rules.md"
    if not rules_file.is_file():
        return ""
    try:
        return rules_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def call_github_model(model: str, system_prompt: str, user_prompt: str, token: str) -> tuple[bool, str]:
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    request = urllib.request.Request(
        MODELS_URL,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, f"HTTP {exc.code}: {body}"
    except urllib.error.URLError as exc:
        return False, f"Network error: {exc}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"Invalid JSON: {exc}"

    choices = data.get("choices") or []
    if choices:
        return True, choices[0].get("message", {}).get("content", "")

    error = data.get("error", {})
    return False, error.get("message", "No model choices returned")


def fetch_open_prs(repo: str, limit: int) -> list[dict[str, Any]]:
    base = gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,isDraft,baseRefName,headRefName,url",
        ],
        default=[],
        check=False,
    )
    prs: list[dict[str, Any]] = []
    for item in base:
        detailed = None
        for _ in range(3):
            detailed = gh_json(
                [
                    "pr",
                    "view",
                    str(item["number"]),
                    "--repo",
                    repo,
                    "--json",
                    "number,title,body,isDraft,mergeable,mergeStateStatus,reviewDecision,autoMergeRequest,baseRefName,headRefName,headRefOid,url,author,statusCheckRollup,updatedAt",
                ],
                default=None,
                check=False,
            )
            if detailed and detailed.get("mergeable") != "UNKNOWN":
                break
        prs.append(detailed or item)
    return prs


def fetch_open_issues(repo: str, limit: int) -> list[dict[str, Any]]:
    return gh_json(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,labels,assignees,body,url,createdAt,updatedAt",
        ],
        default=[],
        check=False,
    )


def fetch_runs(repo: str, limit: int) -> list[dict[str, Any]]:
    return gh_json(
        [
            "run",
            "list",
            "--repo",
            repo,
            "--limit",
            str(limit),
            "--json",
            "databaseId,workflowName,status,conclusion,event,displayTitle,createdAt,headBranch,url",
        ],
        default=[],
        check=False,
    )


def label_names(issue: dict[str, Any]) -> list[str]:
    return [label.get("name", "") for label in issue.get("labels", [])]


def checks_summary(pr: dict[str, Any]) -> dict[str, int]:
    pending = 0
    failing = 0
    for check in pr.get("statusCheckRollup") or []:
        status = (check.get("status") or "").upper()
        conclusion = (check.get("conclusion") or "").upper()
        if status == "PENDING" or not conclusion:
            pending += 1
        if conclusion.lower() in FAILURE_CONCLUSIONS:
            failing += 1
    return {"pending": pending, "failing": failing}


def relevant_runs_for_branch(runs: list[dict[str, Any]], branch: str) -> list[dict[str, Any]]:
    filtered = [run for run in runs if run.get("headBranch") == branch and run.get("workflowName") in RELEVANT_PR_WORKFLOWS]
    filtered.sort(key=lambda run: run.get("createdAt", ""), reverse=True)
    return filtered


def workflow_failure_runs(branch_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run for run in branch_runs if (run.get("conclusion") or "").lower() in FAILURE_CONCLUSIONS]


def has_recent_successful_qa(branch_runs: list[dict[str, Any]], horizon: timedelta) -> bool:
    cutoff = now_utc() - horizon
    for run in branch_runs:
        if run.get("workflowName") != "QA Engineer Agent":
            continue
        created = parse_ts(run.get("createdAt"))
        if created and created >= cutoff and run.get("conclusion") == "success":
            return True
    return False


def fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def handoff_fingerprint(pr: dict[str, Any], reason: str) -> str:
    checks = checks_summary(pr)
    state_blob = "|".join(
        [
            reason,
            str(pr.get("headRefName") or ""),
            str(pr.get("headRefOid") or ""),
            str(pr.get("mergeable") or ""),
            str(pr.get("mergeStateStatus") or ""),
            str(pr.get("reviewDecision") or ""),
            str(checks.get("pending", 0)),
            str(checks.get("failing", 0)),
        ]
    )
    return fingerprint(state_blob)


def state_event_recent(state: dict[str, Any], key: str, cooldown: timedelta) -> bool:
    event = state.get("events", {}).get(key)
    if not event:
        return False
    at = parse_ts(event.get("at"))
    return bool(at and at >= now_utc() - cooldown)


def event_cooldown_remaining(state: dict[str, Any], key: str, cooldown: timedelta) -> timedelta | None:
    event = state.get("events", {}).get(key)
    if not event:
        return None
    at = parse_ts(event.get("at"))
    if not at:
        return None
    remaining = (at + cooldown) - now_utc()
    if remaining.total_seconds() <= 0:
        return None
    return remaining


def format_remaining(duration: timedelta) -> str:
    total_seconds = max(int(duration.total_seconds()), 0)
    hours, rem = divmod(total_seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def record_event(state: dict[str, Any], key: str, payload: dict[str, Any] | None = None) -> None:
    state.setdefault("events", {})[key] = {"at": isoformat(), "payload": payload or {}}


def heuristic_repo_actions(snapshot: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    issues = snapshot["issues"]
    runs = snapshot["runs"]
    actions: list[dict[str, Any]] = []

    unassigned = [issue for issue in issues if not issue.get("assignees")]
    feature_issues = [issue for issue in issues if {"feature", "product-owner"}.intersection(label_names(issue))]
    blocked_or_priority = [
        issue
        for issue in issues
        if any(label.startswith("priority:") or label == "blocked" for label in label_names(issue))
    ]

    recent_runs_by_workflow: dict[str, datetime] = {}
    for run in runs:
        created = parse_ts(run.get("createdAt"))
        workflow_name = run.get("workflowName") or ""
        if created and workflow_name and workflow_name not in recent_runs_by_workflow:
            recent_runs_by_workflow[workflow_name] = created

    if unassigned:
        blocked_for = event_cooldown_remaining(state, "dispatch-blocked:task-assignment.yml", AUTH_FAILURE_COOLDOWN)
        dispatch_for = event_cooldown_remaining(state, "dispatch:task-assignment.yml", WORKFLOW_COOLDOWNS["task-assignment.yml"])
        if blocked_for:
            actions.append(
                {
                    "action": "wait",
                    "reason": f"task-assignment dispatch is auth-blocked for ~{format_remaining(blocked_for)}. Export GH_USER_PAT or HEARTBEAT_GH_TOKEN with actions:write.",
                }
            )
        elif dispatch_for:
            actions.append(
                {
                    "action": "wait",
                    "reason": f"task-assignment dispatch cooldown active for ~{format_remaining(dispatch_for)}.",
                }
            )
        else:
            actions.append(
                {
                    "action": "dispatch_task_assignment",
                    "reason": f"{len(unassigned)} open issues are unassigned and need routing.",
                    "workflow": "task-assignment.yml",
                    "inputs": {
                        "task": "assign-tasks",
                        "extra_context": f"Heartbeat backlog routing for {len(unassigned)} unassigned issues.",
                    },
                }
            )

    if blocked_or_priority:
        blocked_for = event_cooldown_remaining(state, "dispatch-blocked:project-manager.yml", AUTH_FAILURE_COOLDOWN)
        dispatch_for = event_cooldown_remaining(state, "dispatch:project-manager.yml", WORKFLOW_COOLDOWNS["project-manager.yml"])
        if blocked_for:
            actions.append(
                {
                    "action": "wait",
                    "reason": f"project-manager dispatch is auth-blocked for ~{format_remaining(blocked_for)}. Export GH_USER_PAT or HEARTBEAT_GH_TOKEN with actions:write.",
                }
            )
        elif dispatch_for:
            actions.append(
                {
                    "action": "wait",
                    "reason": f"project-manager dispatch cooldown active for ~{format_remaining(dispatch_for)}.",
                }
            )
        else:
            actions.append(
                {
                    "action": "dispatch_project_manager",
                    "reason": f"{len(blocked_or_priority)} blocked or priority-tagged issues need planning attention.",
                    "workflow": "project-manager.yml",
                    "inputs": {
                        "task": "full-sprint-report",
                        "extra_context": f"Heartbeat escalation: {len(blocked_or_priority)} blocked/priority issues are open.",
                    },
                }
            )

    if feature_issues:
        blocked_for = event_cooldown_remaining(state, "dispatch-blocked:product-owner.yml", AUTH_FAILURE_COOLDOWN)
        dispatch_for = event_cooldown_remaining(state, "dispatch:product-owner.yml", WORKFLOW_COOLDOWNS["product-owner.yml"])
        if blocked_for:
            actions.append(
                {
                    "action": "wait",
                    "reason": f"product-owner dispatch is auth-blocked for ~{format_remaining(blocked_for)}. Export GH_USER_PAT or HEARTBEAT_GH_TOKEN with actions:write.",
                }
            )
        elif dispatch_for:
            actions.append(
                {
                    "action": "wait",
                    "reason": f"product-owner dispatch cooldown active for ~{format_remaining(dispatch_for)}.",
                }
            )
        else:
            actions.append(
                {
                    "action": "dispatch_product_owner",
                    "reason": f"{len(feature_issues)} feature issues are waiting for product guidance.",
                    "workflow": "product-owner.yml",
                    "inputs": {
                        "task": "product-health-report",
                        "extra_context": f"Heartbeat feature backlog review for {len(feature_issues)} open feature issues.",
                    },
                }
            )

    return actions


def heuristic_pr_decisions(snapshot: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for pr in snapshot["prs"]:
        checks = checks_summary(pr)
        review_decision = pr.get("reviewDecision") or ""
        mergeable = pr.get("mergeable") or "UNKNOWN"
        merge_state = pr.get("mergeStateStatus") or "UNKNOWN"

        if mergeable == "CONFLICTING" or merge_state == "DIRTY":
            decisions.append({
                "number": pr["number"],
                "action": "sync_branch",
                "reason": "PR conflicts with base; attempt an automatic branch update before requesting manual conflict resolution.",
            })
            continue

        if pr.get("isDraft"):
            if checks["failing"] > 0:
                decisions.append({
                    "number": pr["number"],
                    "action": "send_back_to_copilot",
                    "reason": "Draft PR has failing status checks and needs fixes before it can be marked ready.",
                })
                continue

            if review_decision == "CHANGES_REQUESTED":
                decisions.append({
                    "number": pr["number"],
                    "action": "send_back_to_copilot",
                    "reason": "Draft PR has requested changes and needs implementation updates before readying.",
                })
                continue

            if mergeable == "MERGEABLE":
                decisions.append({
                    "number": pr["number"],
                    "action": "mark_ready",
                    "reason": "Draft PR is mergeable with no failing checks and should be converted to ready-for-review.",
                })
                continue

            decisions.append({
                "number": pr["number"],
                "action": "wait",
                "reason": "Draft PR is not mergeable yet.",
            })
            continue

        if checks["failing"] > 0:
            decisions.append({
                "number": pr["number"],
                "action": "send_back_to_copilot",
                "reason": "Status checks are failing or require action.",
            })
            continue

        if review_decision == "CHANGES_REQUESTED":
            decisions.append({
                "number": pr["number"],
                "action": "send_back_to_copilot",
                "reason": "A reviewer requested changes.",
            })
            continue

        if review_decision == "REVIEW_REQUIRED":
            decisions.append({
                "number": pr["number"],
                "action": "run_qa",
                "reason": "The PR still needs review before it can merge.",
            })
            continue

        if mergeable == "MERGEABLE" and checks["failing"] == 0:
            decisions.append({
                "number": pr["number"],
                "action": "merge",
                "reason": "PR is mergeable, non-draft, and has no blocking review or failing checks.",
            })
            continue

        decisions.append({
            "number": pr["number"],
            "action": "wait",
            "reason": "Heartbeat could not safely advance this PR yet.",
        })
    return decisions


def mergeable_guard(pr: dict[str, Any], runs: list[dict[str, Any]]) -> tuple[bool, str]:
    checks = checks_summary(pr)
    if pr.get("isDraft"):
        return False, "draft"
    if (pr.get("mergeable") or "") != "MERGEABLE":
        return False, f"mergeable={pr.get('mergeable', 'UNKNOWN')}"
    if (pr.get("mergeStateStatus") or "") == "DIRTY":
        return False, "mergeStateStatus=DIRTY"
    if (pr.get("reviewDecision") or "") in BLOCKING_REVIEW_DECISIONS:
        return False, f"reviewDecision={pr.get('reviewDecision')}"
    if checks["failing"] > 0:
        return False, f"failingChecks={checks['failing']}"
    return True, "ready"


def model_prompt(snapshot: dict[str, Any], heuristic: dict[str, Any], rules: str) -> tuple[str, str]:
    system_prompt = "\n".join(
        part for part in [
            "You are Casey, the local heartbeat orchestrator for an autonomous GitHub engineering team.",
            "You must choose only safe actions and return JSON only.",
            "Allowed PR actions: merge, mark_ready, sync_branch, send_back_to_copilot, run_qa, wait.",
            "Allowed repo actions: dispatch_task_assignment, dispatch_project_manager, dispatch_product_owner, wait.",
            "Prefer merge only for non-draft PRs that are MERGEABLE, have no failing checks, and no blocking review.",
            "For draft PRs that are mergeable with no failing checks, prefer mark_ready.",
            "For merge conflicts, prefer sync_branch first and then send_back_to_copilot if conflicts remain.",
            "Use send_back_to_copilot for failing compliance, requested changes, or unresolved conflicts.",
            "Use run_qa when review is missing or stale.",
            rules and f"Follow these collaboration rules:\n\n{rules}",
        ] if part
    )

    condensed_prs = []
    for pr in snapshot["prs"]:
        branch_runs = relevant_runs_for_branch(snapshot["runs"], pr.get("headRefName", ""))[:3]
        condensed_prs.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "isDraft": pr.get("isDraft"),
                "mergeable": pr.get("mergeable"),
                "mergeStateStatus": pr.get("mergeStateStatus"),
                "reviewDecision": pr.get("reviewDecision"),
                "checks": checks_summary(pr),
                "recentRuns": [
                    {
                        "workflow": run.get("workflowName"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                    }
                    for run in branch_runs
                ],
            }
        )

    condensed_issues = []
    for issue in snapshot["issues"][:10]:
        condensed_issues.append(
            {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "labels": label_names(issue),
                "assignees": [assignee.get("login") for assignee in issue.get("assignees", [])],
            }
        )

    active_runs = [
        {
            "workflow": run.get("workflowName"),
            "status": run.get("status"),
            "branch": run.get("headBranch"),
        }
        for run in snapshot["runs"]
        if (run.get("status") or "").lower() in ACTIVE_RUN_STATUSES
    ][:10]

    user_prompt = json.dumps(
        {
            "summary": {
                "repo": snapshot["repo"]["nameWithOwner"],
                "openPrCount": len(snapshot["prs"]),
                "openIssueCount": len(snapshot["issues"]),
                "activeRunCount": len(active_runs),
            },
            "pullRequests": condensed_prs,
            "issues": condensed_issues,
            "activeRuns": active_runs,
            "heuristicPlan": heuristic,
            "responseShape": {
                "pull_requests": [{"number": 123, "action": "merge", "reason": "..."}],
                "repo_actions": [{"action": "dispatch_task_assignment", "reason": "..."}],
            },
        },
        indent=2,
    )
    return system_prompt, user_prompt


def sanitize_model_plan(raw_text: str, heuristic: dict[str, Any]) -> dict[str, Any]:
    try:
        start = raw_text.index("{")
        end = raw_text.rindex("}") + 1
        payload = json.loads(raw_text[start:end])
    except (ValueError, json.JSONDecodeError):
        return heuristic

    valid_pr_actions = {"merge", "mark_ready", "sync_branch", "send_back_to_copilot", "run_qa", "wait"}
    valid_repo_actions = {"dispatch_task_assignment", "dispatch_project_manager", "dispatch_product_owner", "wait"}

    by_number = {entry["number"]: entry for entry in heuristic["pull_requests"]}
    final_prs: list[dict[str, Any]] = []
    for suggested in payload.get("pull_requests", []):
        number = suggested.get("number")
        action = suggested.get("action")
        if number not in by_number or action not in valid_pr_actions:
            continue
        final_prs.append(
            {
                "number": number,
                "action": action,
                "reason": suggested.get("reason") or by_number[number]["reason"],
            }
        )

    existing = {entry["number"] for entry in final_prs}
    for fallback in heuristic["pull_requests"]:
        if fallback["number"] not in existing:
            final_prs.append(fallback)

    final_repo: list[dict[str, Any]] = []
    fallback_actions = {entry["action"]: entry for entry in heuristic["repo_actions"]}
    for suggested in payload.get("repo_actions", []):
        action = suggested.get("action")
        if action not in valid_repo_actions or action == "wait":
            continue
        if action not in fallback_actions:
            continue
        merged = dict(fallback_actions[action])
        merged["reason"] = suggested.get("reason") or merged["reason"]
        final_repo.append(merged)

    existing_repo = {entry["action"] for entry in final_repo}
    for fallback in heuristic["repo_actions"]:
        if fallback["action"] not in existing_repo:
            final_repo.append(fallback)

    return {"pull_requests": final_prs, "repo_actions": final_repo}


def build_plan(
    snapshot: dict[str, Any],
    state: dict[str, Any],
    repo_root: Path,
    model: str,
    models_token: str | None,
    model_every: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    heuristic = {
        "pull_requests": heuristic_pr_decisions(snapshot, state),
        "repo_actions": heuristic_repo_actions(snapshot, state),
    }
    meta = {"decision_source": "heuristic", "models_status": "disabled"}

    if not models_token:
        meta["models_status"] = "disabled: MODELS_TOKEN or GH_MODELS_TOKEN not set"
        return heuristic, meta

    cadence = max(1, model_every)
    upcoming_heartbeat = int(state.get("heartbeats", 0)) + 1
    if cadence > 1 and (upcoming_heartbeat % cadence) != 0:
        meta["models_status"] = f"skipped this cycle for cost control (model_every={cadence})"
        return heuristic, meta

    system_prompt, user_prompt = model_prompt(snapshot, heuristic, read_collaboration_rules(repo_root))
    ok, response = call_github_model(model, system_prompt, user_prompt, models_token)
    if not ok:
        meta["models_status"] = f"degraded: {response}"
        return heuristic, meta

    meta["decision_source"] = f"github-models:{model}"
    meta["models_status"] = "ready"
    return sanitize_model_plan(response, heuristic), meta


def dispatch_workflow(repo: str, workflow: str, inputs: dict[str, str], dry_run: bool) -> str:
    command = ["gh", "workflow", "run", workflow, "--repo", repo]
    for key, value in inputs.items():
        command.extend(["-f", f"{key}={value}"])
    if dry_run:
        return "DRY RUN: " + " ".join(command)
    result = run_command(command, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"failed to dispatch {workflow}"
        if "Resource not accessible by integration" in detail:
            detail += " | hint: export GH_USER_PAT or HEARTBEAT_GH_TOKEN with actions:write before starting the runner"
        raise RuntimeError(detail)
    return result.stdout.strip() or f"Dispatched {workflow}"


def merge_pr(repo: str, pr_number: int, dry_run: bool) -> str:
    command = ["gh", "pr", "merge", str(pr_number), "--repo", repo, "--squash", "--auto", "--delete-branch"]
    if dry_run:
        return "DRY RUN: " + " ".join(command)
    result = run_command(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"failed to merge PR #{pr_number}")
    return result.stdout.strip() or f"Merged or queued PR #{pr_number}"


def sync_pr_branch(repo: str, pr_number: int, dry_run: bool) -> str:
    command = ["gh", "api", "--method", "PUT", f"repos/{repo}/pulls/{pr_number}/update-branch"]
    if dry_run:
        return "DRY RUN: " + " ".join(command)
    result = run_command(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"failed to sync PR #{pr_number} with base branch")
    return result.stdout.strip() or f"Requested branch update for PR #{pr_number}"


def mark_pr_ready(repo: str, pr_number: int, dry_run: bool) -> str:
    command = ["gh", "pr", "ready", str(pr_number), "--repo", repo]
    if dry_run:
        return "DRY RUN: " + " ".join(command)
    result = run_command(command, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"failed to mark PR #{pr_number} ready"
        if "already in \"ready for review\"" in detail.lower():
            return f"PR #{pr_number} is already ready for review"
        raise RuntimeError(detail)
    return result.stdout.strip() or f"Marked PR #{pr_number} ready for review"


def send_back_to_copilot(repo: str, pr: dict[str, Any], reason: str, dry_run: bool) -> str:
    body = (
        "@copilot\n\n"
        "Heartbeat decision: this pull request needs another implementation pass before merge.\n\n"
        f"Reason: {reason}\n\n"
        "Please update the branch, resolve the blocking issues, and rerun the relevant checks."
    )
    command = ["gh", "pr", "comment", str(pr["number"]), "--repo", repo, "--body", body]
    if dry_run:
        return "DRY RUN: " + " ".join(command)
    result = run_command(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"failed to comment on PR #{pr['number']}")
    return result.stdout.strip() or f"Commented on PR #{pr['number']}"


def run_qa(repo: str, pr_number: int, dry_run: bool) -> str:
    return dispatch_workflow(
        repo,
        "qa-engineer.yml",
        {
            "pr_number": str(pr_number),
            "extra_context": "Heartbeat-triggered QA review for a pending PR decision.",
        },
        dry_run,
    )


def execute_plan(snapshot: dict[str, Any], plan: dict[str, Any], state: dict[str, Any], repo: str, dry_run: bool, max_actions: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    actions_taken = 0
    pr_by_number = {pr["number"]: pr for pr in snapshot["prs"]}

    for repo_action in plan["repo_actions"]:
        if actions_taken >= max_actions:
            break
        workflow = repo_action.get("workflow")
        if not workflow:
            continue
        try:
            output = dispatch_workflow(repo, workflow, repo_action.get("inputs", {}), dry_run)
            if not dry_run:
                record_event(state, f"dispatch:{workflow}", {"reason": repo_action.get("reason", "")})
            results.append({"target": workflow, "action": repo_action["action"], "status": "ok", "detail": output})
            actions_taken += 1
        except RuntimeError as exc:
            if "Resource not accessible by integration" in str(exc) and not dry_run:
                record_event(state, f"dispatch-blocked:{workflow}", {"reason": str(exc)})
            results.append({"target": workflow, "action": repo_action["action"], "status": "error", "detail": str(exc)})

    for decision in plan["pull_requests"]:
        if actions_taken >= max_actions:
            break
        pr = pr_by_number.get(decision["number"])
        if not pr:
            continue

        action = decision["action"]
        reason = decision.get("reason", "")

        if action == "wait":
            results.append({"target": f"pr#{pr['number']}", "action": action, "status": "skipped", "detail": reason})
            continue

        if action == "merge":
            allowed, detail = mergeable_guard(pr, snapshot["runs"])
            if not allowed:
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "skipped", "detail": f"guard blocked merge: {detail}"})
                continue
            try:
                output = merge_pr(repo, pr["number"], dry_run)
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "ok", "detail": output})
                actions_taken += 1
            except RuntimeError as exc:
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "error", "detail": str(exc)})
            continue

        if action == "mark_ready":
            event_key = f"ready:pr:{pr['number']}"
            if state_event_recent(state, event_key, PR_READY_COOLDOWN):
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "skipped", "detail": "Ready conversion is still within cooldown."})
                continue
            try:
                ready_output = mark_pr_ready(repo, pr["number"], dry_run)
                if not dry_run:
                    record_event(state, event_key, {"reason": reason})

                if dry_run:
                    results.append({"target": f"pr#{pr['number']}", "action": action, "status": "ok", "detail": ready_output})
                    actions_taken += 1
                    continue

                refreshed = gh_json(
                    [
                        "pr",
                        "view",
                        str(pr["number"]),
                        "--repo",
                        repo,
                        "--json",
                        "number,title,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,headRefName",
                    ],
                    default=pr,
                    check=False,
                )
                allowed, detail = mergeable_guard(refreshed, snapshot["runs"])
                if allowed:
                    merge_output = merge_pr(repo, pr["number"], dry_run)
                    results.append({"target": f"pr#{pr['number']}", "action": action, "status": "ok", "detail": f"{ready_output}; {merge_output}"})
                else:
                    results.append({"target": f"pr#{pr['number']}", "action": action, "status": "ok", "detail": f"{ready_output}; merge deferred ({detail})"})
                actions_taken += 1
            except RuntimeError as exc:
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "error", "detail": str(exc)})
            continue

        if action == "run_qa":
            event_key = f"dispatch:qa:{pr['number']}"
            if state_event_recent(state, event_key, WORKFLOW_COOLDOWNS["qa-engineer.yml"]):
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "skipped", "detail": "QA dispatch is still within cooldown."})
                continue
            try:
                output = run_qa(repo, pr["number"], dry_run)
                if not dry_run:
                    record_event(state, event_key, {"reason": reason})
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "ok", "detail": output})
                actions_taken += 1
            except RuntimeError as exc:
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "error", "detail": str(exc)})
            continue

        if action == "sync_branch":
            event_key = f"sync:pr:{pr['number']}"
            if state_event_recent(state, event_key, PR_SYNC_COOLDOWN):
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "skipped", "detail": "Branch sync is still within cooldown."})
                continue
            try:
                output = sync_pr_branch(repo, pr["number"], dry_run)
                if not dry_run:
                    record_event(state, event_key, {"reason": reason})
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "ok", "detail": output})
                actions_taken += 1
            except RuntimeError as exc:
                detail = str(exc)
                lowered = detail.lower()
                unresolved_conflict = (
                    "merge conflict" in lowered
                    or "not mergeable" in lowered
                    or "422" in lowered
                    or "conflict" in lowered
                )
                if unresolved_conflict:
                    fallback_reason = "Automatic branch update could not resolve base conflicts; manual conflict resolution is required."
                    message_hash = handoff_fingerprint(pr, fallback_reason)
                    comment_event = f"comment:pr:{pr['number']}:{message_hash}"
                    if state_event_recent(state, comment_event, COPILOT_HANDOFF_COOLDOWN):
                        results.append({"target": f"pr#{pr['number']}", "action": action, "status": "skipped", "detail": "Sync failed with conflicts and no PR state changes since the last Copilot handoff."})
                    else:
                        try:
                            output = send_back_to_copilot(repo, pr, fallback_reason, dry_run)
                            if not dry_run:
                                record_event(state, comment_event, {"reason": fallback_reason})
                            results.append({"target": f"pr#{pr['number']}", "action": "send_back_to_copilot", "status": "ok", "detail": f"sync failed ({detail}); {output}"})
                            actions_taken += 1
                        except RuntimeError as comment_exc:
                            results.append({"target": f"pr#{pr['number']}", "action": "send_back_to_copilot", "status": "error", "detail": f"sync failed ({detail}); comment failed ({comment_exc})"})
                else:
                    results.append({"target": f"pr#{pr['number']}", "action": action, "status": "error", "detail": detail})
            continue

        if action == "send_back_to_copilot":
            message_hash = handoff_fingerprint(pr, reason)
            event_key = f"comment:pr:{pr['number']}:{message_hash}"
            if state_event_recent(state, event_key, COPILOT_HANDOFF_COOLDOWN):
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "skipped", "detail": "No PR state changes since the last Copilot handoff."})
                continue
            try:
                output = send_back_to_copilot(repo, pr, reason, dry_run)
                if not dry_run:
                    record_event(state, event_key, {"reason": reason})
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "ok", "detail": output})
                actions_taken += 1
            except RuntimeError as exc:
                results.append({"target": f"pr#{pr['number']}", "action": action, "status": "error", "detail": str(exc)})
            continue

    return results


def render_overview(snapshot: dict[str, Any], plan: dict[str, Any], results: list[dict[str, Any]], meta: dict[str, str], interval: int, dry_run: bool) -> str:
    prs = snapshot["prs"]
    issues = snapshot["issues"]
    runs = snapshot["runs"]
    active_runs = [run for run in runs if (run.get("status") or "").lower() in ACTIVE_RUN_STATUSES]
    action_required_runs = [run for run in runs if (run.get("conclusion") or "").lower() in FAILURE_CONCLUSIONS]
    non_draft = [pr for pr in prs if not pr.get("isDraft")]
    mergeable = [pr for pr in non_draft if pr.get("mergeable") == "MERGEABLE"]
    conflicting = [pr for pr in non_draft if pr.get("mergeable") == "CONFLICTING" or pr.get("mergeStateStatus") == "DIRTY"]
    unassigned = [issue for issue in issues if not issue.get("assignees")]

    lines = [
        "# Heartbeat Overview",
        "",
        f"- Timestamp: {isoformat()}",
        f"- Repository: {snapshot['repo']['nameWithOwner']}",
        f"- Default branch: {snapshot['repo']['defaultBranch']}",
        f"- Mode: {'dry-run' if dry_run else 'active'}",
        f"- Interval seconds: {interval}",
        f"- Decision source: {meta['decision_source']}",
        f"- Models status: {meta['models_status']}",
        f"- GH command auth: {meta['gh_auth_source']}",
        "",
        "## Queue Summary",
        "",
        f"- Open pull requests: {len(prs)} total, {len(non_draft)} non-draft, {len(mergeable)} mergeable, {len(conflicting)} conflicting",
        f"- Open issues: {len(issues)} total, {len(unassigned)} unassigned",
        f"- Workflow runs: {len(active_runs)} active, {len(action_required_runs)} recent failing/action-required",
        "",
        "## Pending Workflow Dispatches",
        "",
    ]

    if plan["repo_actions"]:
        for entry in plan["repo_actions"]:
            lines.append(f"- {entry['action']}: {entry['reason']}")
    else:
        lines.append("- None")

    lines.extend(["", "## Pull Request Decision Queue", ""])
    if plan["pull_requests"]:
        for decision in plan["pull_requests"]:
            pr = next((candidate for candidate in prs if candidate.get("number") == decision["number"]), None)
            if not pr:
                continue
            checks = checks_summary(pr)
            lines.append(
                f"- PR #{pr['number']} {pr.get('title', '')} | action={decision['action']} | mergeable={pr.get('mergeable', 'UNKNOWN')} | review={pr.get('reviewDecision', '') or 'none'} | checks(pending={checks['pending']}, failing={checks['failing']})"
            )
            lines.append(f"  reason: {decision['reason']}")
    else:
        lines.append("- None")

    lines.extend(["", "## Active Workflow Pressure", ""])
    if active_runs:
        for run in active_runs[:10]:
            lines.append(f"- {run.get('workflowName')} | {run.get('status')} | branch={run.get('headBranch')} | {run.get('url')}")
    else:
        lines.append("- No queued or in-progress workflow runs.")

    lines.extend(["", "## Actions This Heartbeat", ""])
    if results:
        for result in results:
            lines.append(f"- {result['target']} | {result['action']} | {result['status']} | {result['detail']}")
    else:
        lines.append("- No actions executed.")

    return "\n".join(lines) + "\n"


def run_heartbeat_cycle(
    repo_root: Path,
    repo_info: dict[str, str],
    args: argparse.Namespace,
    state: dict[str, Any],
    state_file: Path,
    overview_file: Path,
) -> tuple[int, dict[str, Any]]:
    snapshot = {
        "repo": repo_info,
        "prs": fetch_open_prs(repo_info["nameWithOwner"], args.pr_limit),
        "issues": fetch_open_issues(repo_info["nameWithOwner"], args.issue_limit),
        "runs": fetch_runs(repo_info["nameWithOwner"], args.run_limit),
    }
    models_token, token_source = resolve_models_token()
    plan, meta = build_plan(snapshot, state, repo_root, args.model, models_token, args.model_every)
    if meta["models_status"] == "ready":
        meta["models_status"] = f"ready via {token_source}"
    meta["gh_auth_source"] = GH_AUTH_SOURCE
    results = execute_plan(snapshot, plan, state, repo_info["nameWithOwner"], args.dry_run, args.max_actions)
    state["heartbeats"] = int(state.get("heartbeats", 0)) + 1
    state["last_heartbeat_at"] = isoformat()
    save_state(state_file, state)

    overview = render_overview(snapshot, plan, results, meta, args.interval, args.dry_run)
    overview_file.write_text(overview, encoding="utf-8")
    errors = sum(1 for result in results if result["status"] == "error")
    return errors, {
        "snapshot": snapshot,
        "plan": plan,
        "results": results,
        "meta": meta,
        "overview": overview,
        "errors": errors,
    }


def render_tui_lines(
    heartbeat_data: dict[str, Any] | None,
    interval: int,
    dry_run: bool,
    paused: bool,
    next_run_in: int,
    status_message: str,
) -> list[str]:
    mode = "dry-run" if dry_run else "active"
    header = f"Heartbeat Runner TUI | mode={mode} | interval={max(interval, 5)}s"
    lines = [header]
    lines.append("Controls: q quit | r run now | p pause/resume")
    if paused:
        lines.append("State: paused")
    else:
        lines.append(f"Next run in: {max(next_run_in, 0)}s")

    if status_message:
        lines.append(f"Status: {status_message}")

    if heartbeat_data is None:
        lines.extend(["", "Waiting for first heartbeat cycle..."])
        return lines

    snapshot = heartbeat_data["snapshot"]
    plan = heartbeat_data["plan"]
    results = heartbeat_data["results"]
    meta = heartbeat_data["meta"]
    prs = snapshot["prs"]
    issues = snapshot["issues"]
    runs = snapshot["runs"]

    active_runs = [run for run in runs if (run.get("status") or "").lower() in ACTIVE_RUN_STATUSES]
    failing_runs = [run for run in runs if (run.get("conclusion") or "").lower() in FAILURE_CONCLUSIONS]
    non_draft = [pr for pr in prs if not pr.get("isDraft")]
    mergeable = [pr for pr in non_draft if pr.get("mergeable") == "MERGEABLE"]
    conflicting = [pr for pr in non_draft if pr.get("mergeable") == "CONFLICTING" or pr.get("mergeStateStatus") == "DIRTY"]
    unassigned = [issue for issue in issues if not issue.get("assignees")]

    lines.extend(
        [
            "",
            "Summary",
            f"- Repo: {snapshot['repo']['nameWithOwner']} | branch: {snapshot['repo']['defaultBranch']}",
            f"- Decision source: {meta['decision_source']}",
            f"- Models: {meta['models_status']} | GH auth: {meta['gh_auth_source']}",
            f"- PRs: total={len(prs)} non-draft={len(non_draft)} mergeable={len(mergeable)} conflicting={len(conflicting)}",
            f"- Issues: total={len(issues)} unassigned={len(unassigned)}",
            f"- Workflow pressure: active={len(active_runs)} failing/action-required={len(failing_runs)}",
            "",
            "PR Decision Queue",
        ]
    )

    if plan["pull_requests"]:
        for decision in plan["pull_requests"][:12]:
            pr = next((candidate for candidate in prs if candidate.get("number") == decision["number"]), None)
            if not pr:
                continue
            checks = checks_summary(pr)
            lines.append(
                f"- #{pr['number']} action={decision['action']} mergeable={pr.get('mergeable', 'UNKNOWN')} review={pr.get('reviewDecision', '') or 'none'} checks(p={checks['pending']},f={checks['failing']})"
            )
    else:
        lines.append("- None")

    lines.extend(["", "Actions This Heartbeat"])
    if results:
        for result in results[:12]:
            lines.append(f"- {result['target']} | {result['action']} | {result['status']}")
    else:
        lines.append("- No actions executed")

    return lines


def draw_tui(stdscr: curses.window, lines: list[str]) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    max_lines = max(height - 1, 1)
    for idx, line in enumerate(lines[:max_lines]):
        truncated = line[: max(width - 1, 1)]
        stdscr.addstr(idx, 0, truncated)
    stdscr.refresh()


def run_tui(
    repo_root: Path,
    repo_info: dict[str, str],
    args: argparse.Namespace,
    state: dict[str, Any],
    state_file: Path,
    overview_file: Path,
) -> int:
    interval = max(args.interval, 5)
    heartbeat_data: dict[str, Any] | None = None
    paused = False
    next_run_at = time.monotonic()
    status_message = ""

    def _main(stdscr: curses.window) -> int:
        nonlocal heartbeat_data, paused, next_run_at, status_message
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.nodelay(True)
        stdscr.timeout(200)

        while True:
            now = time.monotonic()
            if not paused and (heartbeat_data is None or now >= next_run_at):
                errors, heartbeat_data = run_heartbeat_cycle(repo_root, repo_info, args, state, state_file, overview_file)
                if errors:
                    status_message = f"Completed with {errors} action errors"
                else:
                    status_message = "Completed successfully"
                next_run_at = time.monotonic() + interval

            remaining = int(next_run_at - time.monotonic()) if not paused else interval
            lines = render_tui_lines(heartbeat_data, args.interval, args.dry_run, paused, remaining, status_message)
            draw_tui(stdscr, lines)

            key = stdscr.getch()
            if key == -1:
                continue

            if key in (ord("q"), ord("Q")):
                return 0
            if key in (ord("r"), ord("R")):
                next_run_at = time.monotonic()
                paused = False
                status_message = "Manual run requested"
            if key in (ord("p"), ord("P")):
                paused = not paused
                status_message = "Paused" if paused else "Resumed"

    return curses.wrapper(_main)


def heartbeat(repo_root: Path, repo_info: dict[str, str], args: argparse.Namespace, state: dict[str, Any], state_file: Path, overview_file: Path) -> int:
    errors, heartbeat_data = run_heartbeat_cycle(repo_root, repo_info, args, state, state_file, overview_file)
    print(heartbeat_data["overview"], end="")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Long-running GitHub heartbeat runner for PR decisions and workflow dispatch.")
    parser.add_argument("--repo", help="Target repository in owner/repo format. Defaults to the current gh repo.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"GitHub Models identifier. Default: {DEFAULT_MODEL}")
    parser.add_argument("--model-every", type=int, default=DEFAULT_MODEL_EVERY, help=f"Use GitHub Models inference every N heartbeats (heuristic otherwise). Default: {DEFAULT_MODEL_EVERY}")
    parser.add_argument("--interval", type=int, default=300, help="Heartbeat interval in seconds. Default: 300")
    parser.add_argument("--pr-limit", type=int, default=30, help="Number of open PRs to inspect per heartbeat. Default: 30")
    parser.add_argument("--issue-limit", type=int, default=50, help="Number of open issues to inspect per heartbeat. Default: 50")
    parser.add_argument("--run-limit", type=int, default=100, help="Number of recent workflow runs to inspect per heartbeat. Default: 100")
    parser.add_argument("--max-actions", type=int, default=25, help="Maximum actions to execute per heartbeat. Default: 25")
    parser.add_argument("--max-retries", type=int, default=2, help="Retry attempts for transient CLI/API errors. Default: 2")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print the queue without mutating GitHub state.")
    parser.add_argument("--once", action="store_true", help="Run a single heartbeat instead of looping forever.")
    parser.add_argument("--tui", action="store_true", help="Run an interactive terminal UI with live heartbeat status.")
    return parser.parse_args()


def main() -> int:
    global GH_AUTH_SOURCE, GH_COMMAND_ENV, GH_MAX_RETRIES

    args = parse_args()
    GH_MAX_RETRIES = max(1, args.max_retries)
    repo_root = Path.cwd()
    state_file, overview_file = state_paths()
    state = load_state(state_file)
    repo_info = resolve_repo(args.repo)
    GH_COMMAND_ENV, GH_AUTH_SOURCE = resolve_gh_command_env()

    try:
        if args.tui:
            return run_tui(repo_root, repo_info, args, state, state_file, overview_file)

        if args.once:
            return heartbeat(repo_root, repo_info, args, state, state_file, overview_file)

        while True:
            errors = heartbeat(repo_root, repo_info, args, state, state_file, overview_file)
            if errors:
                print(f"Heartbeat completed with {errors} action errors. Sleeping until next interval.", file=sys.stderr)
            time.sleep(max(args.interval, 5))
    except KeyboardInterrupt:
        print("\nHeartbeat runner stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())