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
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MODELS_URL = "https://models.github.ai/inference/chat/completions"
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
    "council-discussion.yml": timedelta(hours=24),
}
AUTH_FAILURE_COOLDOWN = timedelta(hours=6)
PR_READY_COOLDOWN = timedelta(hours=6)
COPILOT_HANDOFF_COOLDOWN = timedelta(hours=12)
PR_SYNC_COOLDOWN = timedelta(hours=6)
STUCK_PR_ISSUE_COOLDOWN = timedelta(hours=3)
COUNCIL_ESCALATION_COOLDOWN = timedelta(hours=12)
STUCK_PR_ISSUE_THRESHOLD = 3
STUCK_PR_COUNCIL_THRESHOLD = 5

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


def repo_root_from_cwd() -> Path:
    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / ".git").exists() or (candidate / ".git").is_dir():
            return candidate
    return cwd


def script_repo_root() -> Path:
    script_path = Path(__file__).resolve()
    for candidate in [script_path.parent, *script_path.parents]:
        if (candidate / ".git").exists() or (candidate / ".git").is_dir():
            return candidate
    return repo_root_from_cwd()


def git_dir() -> Path:
    repo_root = script_repo_root()
    git_dir_path = repo_root / ".git"
    if git_dir_path.exists() and git_dir_path.is_dir():
        return git_dir_path.resolve()
    result = run_command(["git", "rev-parse", "--git-dir"], check=False)
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return (repo_root / ".git").resolve()


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
    # In GitHub Actions, require explicit model token env vars.
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        for env_name in ("MODELS_TOKEN", "GH_MODELS_TOKEN"):
            value = os.environ.get(env_name, "").strip()
            if value:
                return value, env_name
        return None, "actions-unconfigured"

    # Local runs prefer explicit model token env vars when present.
    for env_name in ("MODELS_TOKEN", "GH_MODELS_TOKEN"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value, env_name

    # Local fallback to GitHub CLI auth token.
    gh_token_result = run_command(["gh", "auth", "token"], check=False)
    if gh_token_result.returncode == 0:
        value = gh_token_result.stdout.strip()
        if value:
            return value, "gh-auth-token"

    return None, "local-unconfigured"


def resolve_gh_command_env() -> tuple[dict[str, str] | None, str]:
    for env_name in ("HEARTBEAT_GH_TOKEN", "GH_USER_PAT"):
        value = os.environ.get(env_name, "").strip()
        if value:
            env = os.environ.copy()
            env["GH_TOKEN"] = value
            return env, env_name
    return None, "gh-auth"


def repo_root() -> Path:
    return script_repo_root()


def state_paths() -> tuple[Path, Path]:
    base = repo_root() / ".git" / "heartbeat-runner"
    base.mkdir(parents=True, exist_ok=True)
    return base / "state.json", base / "overview.md"


def default_ledger_path() -> Path:
    base = repo_root() / ".git" / "heartbeat-runner"
    base.mkdir(parents=True, exist_ok=True)
    return base / "decision-ledger.jsonl"


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"events": {}, "heartbeats": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"events": {}, "heartbeats": 0}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def build_reason_lookup(plan: dict[str, Any]) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for repo_action in plan.get("repo_actions", []):
        action = repo_action.get("action") or ""
        if action:
            reasons[f"repo:{action}"] = repo_action.get("reason") or ""

    for pr_action in plan.get("pull_requests", []):
        number = pr_action.get("number")
        action = pr_action.get("action") or ""
        if number is None or not action:
            continue
        reasons[f"pr:{number}:{action}"] = pr_action.get("reason") or ""
    return reasons


def _result_pr_number(result: dict[str, Any]) -> int | None:
    target = str(result.get("target") or "")
    if not target.startswith("pr#"):
        return None
    raw = target[3:]
    try:
        return int(raw)
    except ValueError:
        return None


def decision_ledger_events(
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    results: list[dict[str, Any]],
    meta: dict[str, str],
    dry_run: bool,
) -> list[dict[str, Any]]:
    reasons = build_reason_lookup(plan)
    events: list[dict[str, Any]] = []
    trace_id = str(uuid.uuid4())
    repo_name = snapshot.get("repo", {}).get("nameWithOwner", "")

    for idx, result in enumerate(results, start=1):
        action = str(result.get("action") or "")
        pr_number = _result_pr_number(result)
        if pr_number is not None:
            reason = reasons.get(f"pr:{pr_number}:{action}") or result.get("detail") or ""
            target_type = "pr"
            target = f"pr#{pr_number}"
        else:
            reason = reasons.get(f"repo:{action}") or result.get("detail") or ""
            target_type = "repo"
            target = str(result.get("target") or "")

        events.append(
            {
                "event_id": f"{trace_id}:{idx}",
                "trace_id": trace_id,
                "timestamp": isoformat(),
                "repo": repo_name,
                "decision_source": meta.get("decision_source", "unknown"),
                "models_status": meta.get("models_status", "unknown"),
                "target_type": target_type,
                "target": target,
                "action": action,
                "status": str(result.get("status") or "unknown"),
                "reason": str(reason),
                "detail": str(result.get("detail") or ""),
                "dry_run": dry_run,
            }
        )

    return events


def append_decision_ledger(path: Path, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def read_collaboration_rules(repo_root: Path) -> str:
    rules_file = repo_root / ".github" / "collaboration-rules.md"
    if not rules_file.is_file():
        return ""
    try:
        return rules_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def call_github_model(model: str, system_prompt: str, user_prompt: str, token: str) -> tuple[bool, str]:
    model_name = model if "/" in model else f"openai/{model}"
    payload = {
        "model": model_name,
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


def call_copilot_cli_model(system_prompt: str, user_prompt: str) -> tuple[bool, str]:
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        return False, "copilot-cli fallback disabled in GitHub Actions"

    prompt = (
        f"{system_prompt}\n\n"
        "Respond using JSON only with this schema:\n"
        "{\"pull_requests\": [{\"number\": 123, \"action\": \"merge\", \"reason\": \"...\"}], "
        "\"repo_actions\": [{\"action\": \"dispatch_task_assignment\", \"reason\": \"...\"}]}\n\n"
        "Request context:\n"
        f"{user_prompt}\n"
    )

    env = os.environ.copy()
    # Ensure Copilot CLI uses the logged-in OAuth session instead of dispatch PAT overrides.
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)

    result = subprocess.run(
        ["gh", "copilot", "-p", prompt],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "gh copilot command failed"
        return False, detail.splitlines()[0][:240]

    output = result.stdout.strip()
    if not output:
        return False, "gh copilot returned empty output"
    return True, output


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


def conflict_failure_map(state: dict[str, Any]) -> dict[str, int]:
    raw = state.setdefault("conflict_failures", {})
    clean: dict[str, int] = {}
    for key, value in raw.items():
        try:
            clean[str(int(key))] = max(0, int(value))
        except (TypeError, ValueError):
            continue
    state["conflict_failures"] = clean
    return clean


def reset_conflict_failure(state: dict[str, Any], pr_number: int) -> None:
    failures = conflict_failure_map(state)
    failures[str(pr_number)] = 0


def increment_conflict_failure(state: dict[str, Any], pr_number: int) -> int:
    failures = conflict_failure_map(state)
    key = str(pr_number)
    failures[key] = int(failures.get(key, 0)) + 1
    return failures[key]


def reconcile_conflict_failures(state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    failures = conflict_failure_map(state)
    active_conflicting: set[str] = set()
    for pr in snapshot["prs"]:
        mergeable = pr.get("mergeable") or ""
        merge_state = pr.get("mergeStateStatus") or ""
        if mergeable == "CONFLICTING" or merge_state == "DIRTY":
            active_conflicting.add(str(pr.get("number")))

    for key in list(failures.keys()):
        if key not in active_conflicting:
            failures.pop(key, None)


def adaptive_model_every(snapshot: dict[str, Any], configured_every: int, enabled: bool) -> tuple[int, str]:
    baseline = max(1, configured_every)
    if not enabled:
        return baseline, "adaptive-off"

    prs = snapshot["prs"]
    issues = snapshot["issues"]
    runs = snapshot["runs"]
    conflicting = [pr for pr in prs if pr.get("mergeable") == "CONFLICTING" or pr.get("mergeStateStatus") == "DIRTY"]
    failing_runs = [run for run in runs if (run.get("conclusion") or "").lower() in FAILURE_CONCLUSIONS]
    unassigned = [issue for issue in issues if not issue.get("assignees")]

    if conflicting or len(failing_runs) >= 5:
        return 1, "high-risk"
    if len(unassigned) >= 30:
        return min(baseline, 2), "backlog-pressure"
    return baseline, "steady-state"


def collect_stuck_conflicting_prs(snapshot: dict[str, Any], state: dict[str, Any], threshold: int) -> list[dict[str, Any]]:
    failures = conflict_failure_map(state)
    stuck: list[dict[str, Any]] = []
    for pr in snapshot["prs"]:
        mergeable = pr.get("mergeable") or ""
        merge_state = pr.get("mergeStateStatus") or ""
        if mergeable != "CONFLICTING" and merge_state != "DIRTY":
            continue
        count = int(failures.get(str(pr.get("number")), 0))
        if count < threshold:
            continue
        checks = checks_summary(pr)
        stuck.append(
            {
                "number": pr["number"],
                "title": pr.get("title", ""),
                "url": pr.get("url", ""),
                "failures": count,
                "reviewDecision": pr.get("reviewDecision") or "none",
                "pendingChecks": checks["pending"],
                "failingChecks": checks["failing"],
            }
        )
    return stuck


def build_stuck_pr_issue(stuck_prs: list[dict[str, Any]]) -> tuple[str, str, str]:
    title = "[Heartbeat] Stuck PR Escalation"
    lines = [
        "Heartbeat detected pull requests that remain in conflict after repeated automatic sync attempts.",
        "",
        f"Generated at: {isoformat()}",
        "",
        "| PR | Failures | Review | Checks (pending/failing) |",
        "| --- | --- | --- | --- |",
    ]
    for item in stuck_prs:
        lines.append(
            f"| #{item['number']} {item['title']} ({item['url']}) | {item['failures']} | {item['reviewDecision']} | {item['pendingChecks']}/{item['failingChecks']} |"
        )
    lines.extend(
        [
            "",
            "Action requested:",
            "- Rebase or merge latest main into these branches.",
            "- Resolve conflicts and rerun checks.",
            "- If repeated failures continue, trigger council review.",
        ]
    )
    body = "\n".join(lines)
    digest_source = "|".join(str(item["number"]) for item in sorted(stuck_prs, key=lambda entry: entry["number"]))
    digest = fingerprint(digest_source)
    return title, body, digest


def upsert_stuck_pr_issue(repo: str, title: str, body: str, dry_run: bool) -> tuple[str, str]:
    existing = gh_json(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "20",
            "--search",
            f"{title} in:title",
            "--json",
            "number,title,url",
        ],
        default=[],
        check=False,
    )
    issue = next((item for item in existing if item.get("title") == title), None)

    if dry_run:
        if issue:
            return "updated", f"DRY RUN: would update issue #{issue['number']} ({issue['url']})"
        return "created", "DRY RUN: would create a new stuck PR escalation issue"

    if issue:
        result = run_command(
            [
                "gh",
                "api",
                "--method",
                "PATCH",
                f"repos/{repo}/issues/{issue['number']}",
                "-f",
                f"title={title}",
                "-f",
                f"body={body}",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "failed to update stuck PR issue")
        return "updated", issue["url"]

    created = gh_json(
        [
            "api",
            "--method",
            "POST",
            f"repos/{repo}/issues",
            "-f",
            f"title={title}",
            "-f",
            f"body={body}",
            "-f",
            "labels[]=blocked",
        ],
        default=None,
        check=False,
    )
    if not created or not created.get("html_url"):
        raise RuntimeError("failed to create stuck PR issue")
    return "created", created["html_url"]


def run_stuck_pr_escalations(snapshot: dict[str, Any], state: dict[str, Any], repo: str, dry_run: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    stuck_issue_prs = collect_stuck_conflicting_prs(snapshot, state, STUCK_PR_ISSUE_THRESHOLD)
    if stuck_issue_prs:
        title, body, body_hash = build_stuck_pr_issue(stuck_issue_prs)
        issue_event_key = f"stuck-pr-issue:{body_hash}"
        if state_event_recent(state, issue_event_key, STUCK_PR_ISSUE_COOLDOWN):
            results.append({"target": "stuck-pr-issue", "action": "upsert_issue", "status": "skipped", "detail": "Stuck PR issue content unchanged within cooldown."})
        else:
            try:
                outcome, detail = upsert_stuck_pr_issue(repo, title, body, dry_run)
                if not dry_run:
                    record_event(state, issue_event_key, {"outcome": outcome, "stuck_count": len(stuck_issue_prs)})
                results.append({"target": "stuck-pr-issue", "action": "upsert_issue", "status": "ok", "detail": detail})
            except RuntimeError as exc:
                results.append({"target": "stuck-pr-issue", "action": "upsert_issue", "status": "error", "detail": str(exc)})

    stuck_council_prs = collect_stuck_conflicting_prs(snapshot, state, STUCK_PR_COUNCIL_THRESHOLD)
    if stuck_council_prs:
        council_event_key = "dispatch:council-stuck-prs"
        if state_event_recent(state, council_event_key, COUNCIL_ESCALATION_COOLDOWN):
            results.append({"target": "council-discussion.yml", "action": "dispatch_council", "status": "skipped", "detail": "Council escalation cooldown active."})
        elif state_event_recent(state, "dispatch-blocked:council-discussion.yml", AUTH_FAILURE_COOLDOWN):
            results.append({"target": "council-discussion.yml", "action": "dispatch_council", "status": "skipped", "detail": "Council escalation is auth-blocked and still within cooldown."})
        else:
            numbers = ", ".join(f"#{item['number']}" for item in stuck_council_prs)
            topic = f"Resolve persistently conflicting pull requests: {numbers}"
            context = "Heartbeat escalation: repeated automatic branch sync attempts were unable to clear merge conflicts."
            try:
                default_branch = snapshot.get("repo", {}).get("defaultBranch", "main")
                output = dispatch_workflow(
                    repo,
                    "council-discussion.yml",
                    {"topic": topic, "context": context},
                    dry_run,
                    ref=default_branch,
                )
                if not dry_run:
                    record_event(state, council_event_key, {"prs": [item["number"] for item in stuck_council_prs]})
                results.append({"target": "council-discussion.yml", "action": "dispatch_council", "status": "ok", "detail": output})
            except RuntimeError as exc:
                detail = str(exc)
                if "Resource not accessible by integration" in detail and not dry_run:
                    record_event(state, "dispatch-blocked:council-discussion.yml", {"reason": detail})
                if "Resource not accessible by integration" in detail or "403" in detail:
                    results.append(
                        {
                            "target": "council-discussion.yml",
                            "action": "dispatch_council",
                            "status": "skipped",
                            "detail": "Council escalation dispatch requires actions:write token (GH_USER_PAT or HEARTBEAT_GH_TOKEN).",
                        }
                    )
                else:
                    results.append({"target": "council-discussion.yml", "action": "dispatch_council", "status": "error", "detail": detail})

    return results


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

    # Periodic council meeting — keep agents aligned even when nothing is "stuck"
    if not state_event_recent(state, "dispatch:council-periodic", timedelta(hours=24)) \
            and not state_event_recent(state, "dispatch-blocked:council-discussion.yml", AUTH_FAILURE_COOLDOWN):
        actions.append({
            "action": "dispatch_council",
            "reason": "Scheduled periodic council review to align agents on roadmap and open work.",
            "workflow": "council-discussion.yml",
            "inputs": {
                "topic": "Periodic agent team sync: review open PRs, backlog priorities, and roadmap alignment.",
                "context": "Heartbeat-triggered daily council sync.",
            },
        })

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
                        "feature_prompt": f"Review and prioritize {len(feature_issues)} open feature issues from the backlog.",
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
            "Allowed repo actions: dispatch_task_assignment, dispatch_project_manager, dispatch_product_owner, dispatch_council, wait.",
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
    valid_repo_actions = {"dispatch_task_assignment", "dispatch_project_manager", "dispatch_product_owner", "dispatch_council", "wait"}

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

    in_actions = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    use_github_models = os.environ.get("HEARTBEAT_USE_GITHUB_MODELS", "false").lower() == "true"

    cadence = max(1, model_every)
    upcoming_heartbeat = int(state.get("heartbeats", 0)) + 1
    if cadence > 1 and (upcoming_heartbeat % cadence) != 0:
        meta["models_status"] = f"skipped this cycle for cost control (model_every={cadence})"
        return heuristic, meta

    system_prompt, user_prompt = model_prompt(snapshot, heuristic, read_collaboration_rules(repo_root))

    # Local runs default to Copilot CLI now that GitHub Models endpoint is retired.
    if not in_actions and not use_github_models:
        copilot_ok, copilot_response = call_copilot_cli_model(system_prompt, user_prompt)
        if copilot_ok:
            meta["decision_source"] = f"copilot-cli:{model}"
            meta["models_status"] = "ready via gh copilot"
            return sanitize_model_plan(copilot_response, heuristic), meta
        meta["models_status"] = f"degraded: copilot-cli unavailable: {copilot_response}"
        return heuristic, meta

    if not models_token:
        meta["models_status"] = "disabled: MODELS_TOKEN or GH_MODELS_TOKEN not set"
        return heuristic, meta

    ok, response = call_github_model(model, system_prompt, user_prompt, models_token)
    if not ok:
        use_copilot_fallback = os.environ.get("HEARTBEAT_COPILOT_FALLBACK", "true").lower() != "false"
        if use_copilot_fallback:
            copilot_ok, copilot_response = call_copilot_cli_model(system_prompt, user_prompt)
            if copilot_ok:
                meta["decision_source"] = f"copilot-cli:{model}"
                meta["models_status"] = "fallback-ready via gh copilot"
                return sanitize_model_plan(copilot_response, heuristic), meta
            meta["models_status"] = f"degraded: {response} | copilot-fallback failed: {copilot_response}"
            return heuristic, meta

        meta["models_status"] = f"degraded: {response}"
        return heuristic, meta

    meta["decision_source"] = f"github-models:{model}"
    meta["models_status"] = "ready"
    return sanitize_model_plan(response, heuristic), meta


def dispatch_workflow(repo: str, workflow: str, inputs: dict[str, str], dry_run: bool, ref: str = "main") -> str:
    command = ["gh", "workflow", "run", workflow, "--repo", repo, "--ref", ref]
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


def run_qa(repo: str, pr_number: int, dry_run: bool, ref: str = "main") -> str:
    return dispatch_workflow(
        repo,
        "qa-engineer.yml",
        {
            "pr_number": str(pr_number),
            "extra_context": "Heartbeat-triggered QA review for a pending PR decision.",
        },
        dry_run,
        ref=ref,
    )


def execute_plan(snapshot: dict[str, Any], plan: dict[str, Any], state: dict[str, Any], repo: str, dry_run: bool, max_actions: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    actions_taken = 0
    default_branch = snapshot.get("repo", {}).get("defaultBranch", "main")
    pr_by_number = {pr["number"]: pr for pr in snapshot["prs"]}

    for repo_action in plan["repo_actions"]:
        if actions_taken >= max_actions:
            break
        workflow = repo_action.get("workflow")
        action_key = repo_action.get("action", "")
        if not workflow:
            continue
        try:
            output = dispatch_workflow(repo, workflow, repo_action.get("inputs", {}), dry_run, ref=default_branch)
            if not dry_run:
                # Use action-specific event key for council so periodic and stuck-PR cooldowns are independent
                event_key = "dispatch:council-periodic" if action_key == "dispatch_council" else f"dispatch:{workflow}"
                record_event(state, event_key, {"reason": repo_action.get("reason", "")})
            results.append({"target": workflow, "action": action_key, "status": "ok", "detail": output})
            actions_taken += 1
        except RuntimeError as exc:
            detail = str(exc)
            if "Resource not accessible by integration" in detail and not dry_run:
                record_event(state, f"dispatch-blocked:{workflow}", {"reason": detail})
            short = detail.split("\n")[0][:120]
            if "403" in short or "Resource not accessible" in short:
                results.append(
                    {
                        "target": workflow,
                        "action": action_key,
                        "status": "skipped",
                        "detail": f"auth blocked: set GH_USER_PAT or HEARTBEAT_GH_TOKEN with actions:write to dispatch {workflow}",
                    }
                )
            else:
                results.append({"target": workflow, "action": action_key, "status": "error", "detail": short})

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
                output = run_qa(repo, pr["number"], dry_run, ref=default_branch)
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
                    reset_conflict_failure(state, pr["number"])
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
                    if not dry_run:
                        streak = increment_conflict_failure(state, pr["number"])
                    else:
                        streak = int(conflict_failure_map(state).get(str(pr["number"]), 0)) + 1
                    fallback_reason = "Automatic branch update could not resolve base conflicts; manual conflict resolution is required."
                    message_hash = handoff_fingerprint(pr, fallback_reason)
                    comment_event = f"comment:pr:{pr['number']}:{message_hash}"
                    if state_event_recent(state, comment_event, COPILOT_HANDOFF_COOLDOWN):
                        results.append({"target": f"pr#{pr['number']}", "action": action, "status": "skipped", "detail": f"Sync failed with conflicts and no PR state changes since the last Copilot handoff (streak={streak})."})
                    else:
                        try:
                            output = send_back_to_copilot(repo, pr, fallback_reason, dry_run)
                            if not dry_run:
                                record_event(state, comment_event, {"reason": fallback_reason})
                            results.append({"target": f"pr#{pr['number']}", "action": "send_back_to_copilot", "status": "ok", "detail": f"sync failed ({detail}); {output}; streak={streak}"})
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
        f"- Model cadence: {meta.get('model_cadence', 'n/a')}",
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
    ledger_file: Path,
) -> tuple[int, dict[str, Any]]:
    snapshot = {
        "repo": repo_info,
        "prs": fetch_open_prs(repo_info["nameWithOwner"], args.pr_limit),
        "issues": fetch_open_issues(repo_info["nameWithOwner"], args.issue_limit),
        "runs": fetch_runs(repo_info["nameWithOwner"], args.run_limit),
    }
    reconcile_conflict_failures(state, snapshot)
    effective_model_every, cadence_reason = adaptive_model_every(snapshot, args.model_every, args.adaptive_model_cadence)
    models_token, token_source = resolve_models_token()
    plan, meta = build_plan(snapshot, state, repo_root, args.model, models_token, effective_model_every)
    if meta["models_status"] == "ready":
        meta["models_status"] = f"ready via {token_source}"
    meta["model_cadence"] = f"{effective_model_every} ({cadence_reason})"
    meta["gh_auth_source"] = GH_AUTH_SOURCE
    results = execute_plan(snapshot, plan, state, repo_info["nameWithOwner"], args.dry_run, args.max_actions)
    results.extend(run_stuck_pr_escalations(snapshot, state, repo_info["nameWithOwner"], args.dry_run))
    append_decision_ledger(ledger_file, decision_ledger_events(snapshot, plan, results, meta, args.dry_run))
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


CHAT_AGENTS: dict[str, dict[str, str]] = {
    "Casey (Council)": {
        "model": "gpt-4o-mini",
        "persona": (
            "You are Casey, the Council Moderator for an autonomous AI engineering team. "
            "You facilitate multi-agent discussions, synthesise QA, PM, and PO perspectives, "
            "and produce clear, actionable consensus decisions. You are impartial, evidence-driven, "
            "and always tie conclusions back to user value and engineering quality. "
            "You are being addressed interactively from the team's supervisor TUI."
        ),
    },
    "Quinn (QA)": {
        "model": "gpt-5.3-codex",
        "persona": (
            "You are Quinn, the QA Engineer. You have deep expertise in automated testing, "
            "security review, and code quality. You are methodical, thorough, and risk-aware. "
            "You give actionable, constructive feedback and always provide specific severity ratings. "
            "You can use tools and internet sources to verify claims before answering. "
            "You are being addressed interactively from the team's supervisor TUI."
        ),
    },
    "Morgan (PM)": {
        "model": "gpt-4o-mini",
        "persona": (
            "You are Morgan, the Project Manager. You keep the team focused, on schedule, and "
            "aligned with business goals. You think in timelines, dependencies, and risk. "
            "You are data-driven and communicate clearly, grounding decisions in milestone dates "
            "and team capacity. You are being addressed interactively from the team's supervisor TUI."
        ),
    },
    "Alex (PO)": {
        "model": "gpt-4o-mini",
        "persona": (
            "You are Alex, the Product Owner. You champion the end-user, review the current product "
            "state, and identify gaps and opportunities. You think in user stories, acceptance criteria, "
            "and business value. You are creative, empathetic, and always tie features back to customer "
            "outcomes. You are being addressed interactively from the team's supervisor TUI."
        ),
    },
}
CHAT_AGENT_NAMES = list(CHAT_AGENTS.keys())


def _color(stdscr: Any, pair: int, text: str, bold: bool = False) -> tuple[int, str]:
    attr = curses.color_pair(pair)
    if bold:
        attr |= curses.A_BOLD
    return attr, text


def _init_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)    # ok / merged
    curses.init_pair(2, curses.COLOR_RED, -1)      # error / conflict
    curses.init_pair(3, curses.COLOR_YELLOW, -1)   # warn / pending
    curses.init_pair(4, curses.COLOR_CYAN, -1)     # info / header
    curses.init_pair(5, curses.COLOR_MAGENTA, -1)  # action / dispatch
    curses.init_pair(6, curses.COLOR_WHITE, -1)    # normal
    curses.init_pair(7, curses.COLOR_BLUE, -1)     # draft / wait


def _safe_addstr(win: Any, y: int, x: int, text: str, attr: int = 0) -> None:
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    text = text[: max(w - x - 1, 0)]
    if not text:
        return
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def _pr_action_color(action: str) -> int:
    if action in ("merge",):
        return curses.color_pair(1) | curses.A_BOLD
    if action in ("send_back_to_copilot", "sync_branch"):
        return curses.color_pair(2)
    if action in ("mark_ready",):
        return curses.color_pair(5)
    if action in ("run_qa",):
        return curses.color_pair(3)
    return curses.color_pair(6)


def _result_color(status: str) -> int:
    if status == "ok":
        return curses.color_pair(1)
    if status == "error":
        return curses.color_pair(2) | curses.A_BOLD
    if status == "skipped":
        return curses.color_pair(7)
    return curses.color_pair(6)


def _draw_box(win: Any, title: str) -> None:
    try:
        win.box()
    except curses.error:
        pass
    if title:
        _safe_addstr(win, 0, 2, f" {title} ", curses.color_pair(4) | curses.A_BOLD)


def render_tui_lines(
    heartbeat_data: dict[str, Any] | None,
    interval: int,
    dry_run: bool,
    paused: bool,
    next_run_in: int,
    status_message: str,
) -> list[str]:
    """Legacy plain-text render kept for non-interactive fallback."""
    mode = "dry-run" if dry_run else "active"
    header = f"Heartbeat Runner | mode={mode} | interval={max(interval, 5)}s"
    lines = [header, "Controls: q quit | r run now | p pause/resume | c council | m pm | d draft→ready+merge | a assign | s status"]
    lines.append("State: PAUSED" if paused else f"Next run in: {max(next_run_in, 0)}s")
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
    active_runs = [r for r in runs if (r.get("status") or "").lower() in ACTIVE_RUN_STATUSES]
    failing_runs = [r for r in runs if (r.get("conclusion") or "").lower() in FAILURE_CONCLUSIONS]
    non_draft = [p for p in prs if not p.get("isDraft")]
    mergeable = [p for p in non_draft if p.get("mergeable") == "MERGEABLE"]
    conflicting = [p for p in non_draft if p.get("mergeable") == "CONFLICTING" or p.get("mergeStateStatus") == "DIRTY"]
    unassigned = [i for i in issues if not i.get("assignees")]
    ok_c = sum(1 for r in results if r.get("status") == "ok")
    err_c = sum(1 for r in results if r.get("status") == "error")
    lines.extend([
        "",
        f"Repo: {snapshot['repo']['nameWithOwner']} | Models: {meta.get('models_status','?')} | Auth: {meta.get('gh_auth_source','?')}",
        f"PRs: total={len(prs)} draft={len(prs)-len(non_draft)} mergeable={len(mergeable)} conflicting={len(conflicting)}",
        f"Issues: total={len(issues)} unassigned={len(unassigned)}",
        f"Runs: active={len(active_runs)} failing={len(failing_runs)}",
        f"Last cycle: ok={ok_c} errors={err_c}",
        "",
        "PR Queue:",
    ])
    for dec in (plan.get("pull_requests") or [])[:15]:
        pr = next((p for p in prs if p.get("number") == dec["number"]), None)
        if not pr:
            continue
        ch = checks_summary(pr)
        tag = "[DRAFT] " if pr.get("isDraft") else ""
        lines.append(f"  #{pr['number']} {tag}{dec['action']:18} mergeable={pr.get('mergeable','?'):12} checks(p={ch['pending']},f={ch['failing']}) {pr.get('title','')[:40]}")
    lines.extend(["", "Dispatches:"])
    for a in (plan.get("repo_actions") or [])[:8]:
        lines.append(f"  {a['action']:30} {a.get('reason','')[:60]}")
    lines.extend(["", "Last actions:"])
    for r in (results or [])[:15]:
        lines.append(f"  {r['target']:25} {r['action']:25} [{r['status']}]")
    return lines


def draw_tui(stdscr: Any, lines: list[str]) -> None:
    """Legacy single-pane draw kept for terminals that fail color init."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    for idx, line in enumerate(lines[: max(h - 1, 1)]):
        _safe_addstr(stdscr, idx, 0, line[: max(w - 1, 1)])
    stdscr.refresh()


def _draw_full_tui(
    stdscr: Any,
    heartbeat_data: dict[str, Any] | None,
    interval: int,
    dry_run: bool,
    paused: bool,
    next_run_in: int,
    status_msg: str,
    log_scroll: int,
    action_log: list[str],
    heartbeat_count: int,
) -> None:
    stdscr.erase()
    H, W = stdscr.getmaxyx()
    if H < 14 or W < 50:
        # Too small — fall back to single-pane
        lines = render_tui_lines(heartbeat_data, interval, dry_run, paused, next_run_in, status_msg)
        draw_tui(stdscr, lines)
        return

    # ── Layout ───────────────────────────────────────────────────────────────
    # Row 0     : header bar (full width)
    # Row 1     : controls bar
    # Row 2..H-log_h-2 : left panel (PRs) | right panel (dispatches)
    # Row H-log_h-1..H-2 : action log panel
    # Row H-1   : status bar

    log_h = min(10, max(5, H // 4))
    body_top = 2
    body_bot = H - log_h - 1
    body_h = max(body_bot - body_top, 2)
    mid = W // 2

    mode = "DRY-RUN" if dry_run else "ACTIVE"
    state_str = "⏸ PAUSED" if paused else f"next in {max(next_run_in,0)}s"

    # Header
    header = f" ◆ Engineering Team Heartbeat  mode={mode}  {state_str}  beat #{heartbeat_count} "
    _safe_addstr(stdscr, 0, 0, header.ljust(W - 1), curses.color_pair(4) | curses.A_BOLD | curses.A_REVERSE)

    # Controls
    controls = " q=quit  r=run  p=pause  /=chat  c=council  m=pm  d=draft→ready  a=assign  ↑↓=scroll "
    _safe_addstr(stdscr, 1, 0, controls[:W - 1], curses.color_pair(7))

    # ── Left panel: PR queue ─────────────────────────────────────────────────
    pr_win = curses.newwin(body_h, mid - 1, body_top, 0)
    _draw_box(pr_win, "PR Decision Queue")
    row = 1
    if heartbeat_data is None:
        _safe_addstr(pr_win, row, 1, "Waiting for first cycle...", curses.color_pair(3))
    else:
        plan = heartbeat_data["plan"]
        snapshot = heartbeat_data["snapshot"]
        prs = snapshot["prs"]
        pr_by_num = {p["number"]: p for p in prs}
        non_draft = [p for p in prs if not p.get("isDraft")]
        mergeable_list = [p for p in non_draft if p.get("mergeable") == "MERGEABLE"]
        conflicting_list = [p for p in non_draft if p.get("mergeable") == "CONFLICTING" or p.get("mergeStateStatus") == "DIRTY"]
        drafts = [p for p in prs if p.get("isDraft")]

        summary = f" {len(prs)} open | {len(drafts)} draft | {len(mergeable_list)} mergeable | {len(conflicting_list)} conflict"
        _safe_addstr(pr_win, row, 1, summary[:mid - 3], curses.color_pair(6))
        row += 1

        for dec in (plan.get("pull_requests") or []):
            if row >= body_h - 1:
                break
            pr = pr_by_num.get(dec["number"])
            if not pr:
                continue
            action = dec["action"]
            ch = checks_summary(pr)
            is_draft = pr.get("isDraft", False)
            draft_tag = "✎" if is_draft else " "
            m = pr.get("mergeable", "?")[:7]
            num_str = f"#{pr['number']:4}"
            action_str = f"{action:17}"
            title = pr.get("title", "")[:mid - 40]

            _safe_addstr(pr_win, row, 1, num_str, curses.color_pair(4) | curses.A_BOLD)
            _safe_addstr(pr_win, row, 6, draft_tag, curses.color_pair(7))
            _safe_addstr(pr_win, row, 8, action_str, _pr_action_color(action))
            _safe_addstr(pr_win, row, 26, f"m={m}", curses.color_pair(3) if m == "UNKNOWN" else (curses.color_pair(1) if m == "MERGEAB" else curses.color_pair(2)))
            _safe_addstr(pr_win, row, 35, f"c={ch['failing']}", curses.color_pair(2) if ch["failing"] else curses.color_pair(6))
            _safe_addstr(pr_win, row, 39, title, curses.color_pair(6))
            row += 1
    pr_win.refresh()

    # ── Right panel: dispatches + issues + runs ───────────────────────────────
    rp_win = curses.newwin(body_h, W - mid - 1, body_top, mid)
    _draw_box(rp_win, "Repo Actions & Status")
    row = 1
    if heartbeat_data is not None:
        plan = heartbeat_data["plan"]
        meta = heartbeat_data["meta"]
        snapshot = heartbeat_data["snapshot"]
        issues = snapshot["issues"]
        runs = snapshot["runs"]
        active_runs = [r for r in runs if (r.get("status") or "").lower() in ACTIVE_RUN_STATUSES]
        failing_runs = [r for r in runs if (r.get("conclusion") or "").lower() in FAILURE_CONCLUSIONS]
        unassigned = [i for i in issues if not i.get("assignees")]

        _safe_addstr(rp_win, row, 1, f"Models: {meta.get('models_status','?')[:W-mid-6]}", curses.color_pair(1) if "ready" in meta.get("models_status","") else curses.color_pair(3))
        row += 1
        _safe_addstr(rp_win, row, 1, f"Auth:   {meta.get('gh_auth_source','?')[:W-mid-6]}", curses.color_pair(6))
        row += 1
        _safe_addstr(rp_win, row, 1, f"Issues: {len(issues)} total  {len(unassigned)} unassigned", curses.color_pair(6))
        row += 1
        _safe_addstr(rp_win, row, 1, f"Runs:   {len(active_runs)} active  {len(failing_runs)} failing", curses.color_pair(2) if failing_runs else curses.color_pair(6))
        row += 1
        _safe_addstr(rp_win, row, 1, "─" * (W - mid - 3), curses.color_pair(7))
        row += 1
        _safe_addstr(rp_win, row, 1, "Scheduled dispatches:", curses.color_pair(4))
        row += 1
        for ra in (plan.get("repo_actions") or []):
            if row >= body_h - 1:
                break
            act = ra["action"]
            col = curses.color_pair(5) if act != "wait" else curses.color_pair(7)
            _safe_addstr(rp_win, row, 2, f"• {act:30}", col)
            row += 1
        if not plan.get("repo_actions"):
            _safe_addstr(rp_win, row, 2, "  none pending", curses.color_pair(7))
            row += 1
        if active_runs:
            _safe_addstr(rp_win, row, 1, "─" * (W - mid - 3), curses.color_pair(7))
            row += 1
            _safe_addstr(rp_win, row, 1, "Active workflows:", curses.color_pair(4))
            row += 1
            for r in active_runs[:4]:
                if row >= body_h - 1:
                    break
                _safe_addstr(rp_win, row, 2, f"⟳ {r.get('workflowName','?')[:W-mid-5]}", curses.color_pair(3))
                row += 1
    rp_win.refresh()

    # ── Action log panel ──────────────────────────────────────────────────────
    log_win = curses.newwin(log_h, W, body_bot, 0)
    _draw_box(log_win, f"Action Log  ({len(action_log)} entries, ↑↓ scroll)")
    visible = log_win.getmaxyx()[0] - 2
    total = len(action_log)
    start = max(0, min(log_scroll, total - visible)) if total > visible else 0
    for li, entry in enumerate(action_log[start: start + visible]):
        if "| ok" in entry or "✓" in entry:
            col = curses.color_pair(1)
        elif "| error" in entry or "✗" in entry:
            col = curses.color_pair(2) | curses.A_BOLD
        elif "| skipped" in entry or "–" in entry:
            col = curses.color_pair(7)
        else:
            col = curses.color_pair(6)
        _safe_addstr(log_win, li + 1, 1, entry[: W - 2], col)
    log_win.refresh()

    # Status bar
    _safe_addstr(stdscr, H - 1, 0, f" {status_msg}"[: W - 1].ljust(W - 1), curses.color_pair(3) | curses.A_REVERSE)
    stdscr.refresh()


def _draw_chat_panel(stdscr: Any, messages: list[dict[str, str]], agent_name: str, input_buf: str, thinking: bool) -> None:
    """Draw a full-screen chat overlay over the TUI."""
    H, W = stdscr.getmaxyx()
    stdscr.erase()
    title = f" ◆ Chat with {agent_name}  (Esc=close  Tab=switch agent  Enter=send) "
    _safe_addstr(stdscr, 0, 0, title.ljust(W - 1), curses.color_pair(4) | curses.A_BOLD | curses.A_REVERSE)

    # Message area: rows 1 .. H-3
    msg_h = H - 3
    row = 1
    for msg in messages:
        if row >= msg_h:
            break
        role = msg["role"]
        content = msg["content"]
        if role == "user":
            prefix = "You: "
            col = curses.color_pair(6) | curses.A_BOLD
        elif role == "assistant":
            prefix = f"{agent_name}: "
            col = curses.color_pair(1)
        else:
            prefix = "  "
            col = curses.color_pair(7)

        # Word-wrap each message into the available width
        max_w = max(W - 2, 10)
        line = prefix + content
        while line and row < msg_h:
            chunk = line[:max_w]
            _safe_addstr(stdscr, row, 1, chunk, col)
            line = line[max_w:]
            row += 1
        # blank separator
        row += 1

    # Thinking indicator
    if thinking:
        _safe_addstr(stdscr, H - 3, 1, f"  {agent_name} is thinking…", curses.color_pair(3))

    # Input bar
    prompt = f" > {input_buf}"
    _safe_addstr(stdscr, H - 2, 0, prompt[: W - 1].ljust(W - 1), curses.color_pair(5) | curses.A_REVERSE)
    # cursor position
    try:
        curses.curs_set(1)
        stdscr.move(H - 2, min(len(prompt), W - 2))
    except curses.error:
        pass

    # Footer hint
    hint = " Tab=switch agent  Enter=send  Esc=close "
    _safe_addstr(stdscr, H - 1, 0, hint.ljust(W - 1), curses.color_pair(7))
    stdscr.refresh()


def run_tui(
    repo_root: Path,
    repo_info: dict[str, str],
    args: argparse.Namespace,
    state: dict[str, Any],
    state_file: Path,
    overview_file: Path,
    ledger_file: Path,
) -> int:
    interval = max(args.interval, 5)
    heartbeat_data: dict[str, Any] | None = None
    paused = False
    next_run_at = time.monotonic()
    status_message = "Starting up…"
    log_scroll = 0
    action_log: list[str] = []
    heartbeat_count = 0

    # Chat state
    chat_open = False
    chat_agent_idx = 0
    chat_input: list[str] = []          # current input buffer as char list
    chat_messages: list[dict[str, str]] = []   # [{role, content}, ...]
    chat_thinking = False

    def _append_log(results: list[dict[str, Any]]) -> None:
        ts = isoformat()
        for r in results:
            status_sym = "✓" if r["status"] == "ok" else ("✗" if r["status"] == "error" else "–")
            action_log.append(f"{ts}  {r['target']:25} {r['action']:22} | {r['status']} {status_sym}  {r.get('detail','')[:60]}")

    def _trigger_dispatch(workflow: str, inputs: dict[str, str], label: str) -> None:
        nonlocal status_message
        try:
            out = dispatch_workflow(repo_info["nameWithOwner"], workflow, inputs, args.dry_run, ref=repo_info.get("defaultBranch", "main"))
            if not args.dry_run:
                record_event(state, f"dispatch:{workflow}", {"reason": label})
                save_state(state_file, state)
            action_log.append(f"{isoformat()}  {workflow:30} dispatch_manual      | ok ✓  {out[:50]}")
            status_message = f"Dispatched {label}"
        except RuntimeError as exc:
            short = str(exc).split("\n")[0][:80]
            if "403" in short or "Resource not accessible" in short:
                short = f"Auth error dispatching {label} — export GH_USER_PAT with actions:write"
            action_log.append(f"{isoformat()}  {workflow:30} dispatch_manual      | error ✗  {short}")
            status_message = f"✗ {short}"

    def _force_draft_ready() -> None:
        """Mark every open draft PR ready and queue merge."""
        nonlocal status_message
        if heartbeat_data is None:
            status_message = "No snapshot yet — run a heartbeat first"
            return
        prs = heartbeat_data["snapshot"]["prs"]
        for pr in prs:
            if not pr.get("isDraft"):
                continue
            try:
                out = mark_pr_ready(repo_info["nameWithOwner"], pr["number"], args.dry_run)
                action_log.append(f"{isoformat()}  pr#{pr['number']:5}                    mark_ready           | ok ✓  {out[:50]}")
                if not args.dry_run:
                    # immediate merge attempt after promoting
                    refreshed = gh_json(
                        ["pr", "view", str(pr["number"]), "--repo", repo_info["nameWithOwner"],
                         "--json", "number,title,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,headRefName"],
                        default=pr, check=False,
                    )
                    allowed, detail = mergeable_guard(refreshed, heartbeat_data["snapshot"]["runs"])
                    if allowed:
                        mout = merge_pr(repo_info["nameWithOwner"], pr["number"], False)
                        action_log.append(f"{isoformat()}  pr#{pr['number']:5}                    merge                | ok ✓  {mout[:50]}")
                    else:
                        action_log.append(f"{isoformat()}  pr#{pr['number']:5}                    merge                | skipped –  {detail}")
            except RuntimeError as exc:
                action_log.append(f"{isoformat()}  pr#{pr['number']:5}                    mark_ready           | error ✗  {exc}")
        status_message = "Draft → ready pass complete"

    def _send_chat(message: str, agent_name: str) -> str:
        """Send a chat message via gh copilot CLI."""
        agent = CHAT_AGENTS[agent_name]

        context_note = ""
        if heartbeat_data:
            snap = heartbeat_data["snapshot"]
            prs, issues, runs = snap["prs"], snap["issues"], snap["runs"]
            active = [r for r in runs if (r.get("status") or "").lower() in ACTIVE_RUN_STATUSES]
            context_note = (
                f"\n\nRepo snapshot: {snap['repo']['nameWithOwner']} — "
                f"{len(prs)} open PRs ({sum(1 for p in prs if p.get('isDraft'))} drafts, "
                f"{sum(1 for p in prs if p.get('mergeable')=='CONFLICTING')} conflicting), "
                f"{len(issues)} open issues, {len(active)} active runs."
            )

        messages = [{"role": "system", "content": agent["persona"] + context_note}]
        for m in chat_messages[-10:]:
            if m["role"] in ("user", "assistant"):
                messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": message})

        prompt = "\n\n".join(
            [
                f"You are {agent_name}.",
                "Persona and context:",
                messages[0]["content"],
                "Conversation:",
                "\n".join(f"{m['role']}: {m['content']}" for m in messages[1:]),
                "Respond clearly and concisely.",
            ]
        )

        env = os.environ.copy()
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)

        # Quinn is configured for maximum capability and internet/tool access by default.
        # Env overrides:
        # - HEARTBEAT_CHAT_ALLOW_ALL_TOOLS=false
        # - HEARTBEAT_CHAT_ALLOW_ALL_URLS=false
        # - HEARTBEAT_CHAT_MODEL=<model>
        model = os.environ.get("HEARTBEAT_CHAT_MODEL", agent.get("model") or "gpt-5.3-codex")
        allow_all_tools = os.environ.get("HEARTBEAT_CHAT_ALLOW_ALL_TOOLS", "true").lower() == "true"
        allow_all_urls = os.environ.get("HEARTBEAT_CHAT_ALLOW_ALL_URLS", "true").lower() == "true"

        command = [
            "gh",
            "copilot",
            "--model",
            model,
            "--no-alt-screen",
            "--stream",
            "off",
            "--output-format",
            "text",
            "--silent",
        ]
        if allow_all_tools:
            command.append("--allow-all-tools")
        if allow_all_urls:
            command.append("--allow-all-urls")
        command.extend(["-p", prompt])

        # Occasionally Copilot returns exit code 0 with empty stdout in prompt
        # mode. Retry once before surfacing an error to the chat panel.
        for attempt in range(2):
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                env=env,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or "gh copilot command failed"
                return f"⚠ Copilot CLI failed: {detail.splitlines()[0][:220]}"

            output = result.stdout.strip()
            if output:
                return output

            # Some builds emit the response to stderr when rendering is disabled.
            stderr_output = result.stderr.strip()
            if stderr_output and "warning" not in stderr_output.lower():
                return stderr_output.splitlines()[0][:400]

            if attempt == 0:
                continue

        return "⚠ Copilot CLI returned empty output after retry"


    def _main(stdscr: Any) -> int:
        nonlocal heartbeat_data, paused, next_run_at, status_message
        nonlocal log_scroll, heartbeat_count
        nonlocal chat_open, chat_agent_idx, chat_input, chat_messages, chat_thinking
        force_plain = args.tui_plain or os.environ.get("HEARTBEAT_TUI_PLAIN", "false").lower() == "true"
        use_full_layout = not force_plain
        try:
            if force_plain or not curses.has_colors():
                colors_ok = False
            else:
                _init_colors()
                colors_ok = True
        except Exception:
            colors_ok = False

        try:
            curses.curs_set(0)
        except curses.error:
            pass
        try:
            stdscr.keypad(True)
        except curses.error:
            pass
        stdscr.nodelay(True)
        stdscr.timeout(200)

        # Draw immediately so startup is visible while the first heartbeat cycle gathers data.
        status_message = "Initializing heartbeat..."
        lines = render_tui_lines(None, args.interval, args.dry_run, paused, interval, status_message)
        draw_tui(stdscr, lines)

        while True:
            now = time.monotonic()

            # ── Heartbeat cycle (skipped when chat is blocking) ──────────────
            if not chat_open and not paused and (heartbeat_data is None or now >= next_run_at):
                errors, heartbeat_data = run_heartbeat_cycle(repo_root, repo_info, args, state, state_file, overview_file, ledger_file)
                heartbeat_count += 1
                _append_log(heartbeat_data["results"])
                log_scroll = max(0, len(action_log) - 10)
                status_message = (
                    f"Beat #{heartbeat_count} complete — {errors} errors" if errors
                    else f"Beat #{heartbeat_count} complete ✓"
                )
                next_run_at = time.monotonic() + interval

            remaining = int(next_run_at - time.monotonic()) if not paused else interval
            agent_name = CHAT_AGENT_NAMES[chat_agent_idx % len(CHAT_AGENT_NAMES)]

            # ── Draw ─────────────────────────────────────────────────────────
            if chat_open:
                _draw_chat_panel(stdscr, chat_messages, agent_name, "".join(chat_input), chat_thinking)
            elif use_full_layout:
                _draw_full_tui(stdscr, heartbeat_data, args.interval, args.dry_run, paused, remaining, status_message, log_scroll, action_log, heartbeat_count)
            else:
                lines = render_tui_lines(heartbeat_data, args.interval, args.dry_run, paused, remaining, status_message)
                draw_tui(stdscr, lines)

            # ── Input ────────────────────────────────────────────────────────
            key = stdscr.getch()
            if key == -1:
                continue

            if chat_open:
                if key == 27:                             # Esc — close chat
                    chat_open = False
                    chat_input.clear()
                    try:
                        curses.curs_set(0)
                    except curses.error:
                        pass
                elif key == 9:                            # Tab — cycle agent
                    chat_agent_idx = (chat_agent_idx + 1) % len(CHAT_AGENT_NAMES)
                    agent_name = CHAT_AGENT_NAMES[chat_agent_idx]
                    chat_messages.append({"role": "system", "content": f"[switched to {agent_name}]"})
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    if chat_input:
                        chat_input.pop()
                elif key in (10, 13):                     # Enter — send
                    user_text = "".join(chat_input).strip()
                    chat_input.clear()
                    if user_text:
                        chat_messages.append({"role": "user", "content": user_text})
                        # Redraw with thinking indicator
                        chat_thinking = True
                        _draw_chat_panel(stdscr, chat_messages, agent_name, "", True)
                        # Blocking call — TUI pauses during model call
                        reply = _send_chat(user_text, agent_name)
                        chat_thinking = False
                        chat_messages.append({"role": "assistant", "content": reply})
                        action_log.append(f"{isoformat()}  chat:{agent_name[:20]:20}             | ok ✓  {reply[:60]}")
                        # Relay chat context through the council workflow so
                        # discussion publishing flows through GitHub Actions.
                        try:
                            chat_transcript = "\n\n".join(
                                f"**{'You' if m['role']=='user' else agent_name}**: {m['content']}"
                                for m in chat_messages
                                if m["role"] in ("user", "assistant")
                            )
                            if len(chat_transcript) > 6000:
                                chat_transcript = chat_transcript[-6000:]

                            _trigger_dispatch(
                                "council-discussion.yml",
                                {
                                    "mode": "discussion",
                                    "topic": f"TUI Chat Relay: {user_text[:80]}",
                                    "context": (
                                        "Heartbeat TUI chat relay. Use this transcript as"
                                        " context and post outcomes to Discussions via the"
                                        " existing council workflow.\n\n"
                                        f"{chat_transcript}"
                                    ),
                                },
                                "Council chat relay",
                            )
                        except Exception:
                            pass
                elif 32 <= key <= 126:                    # printable
                    chat_input.append(chr(key))
            else:
                # Normal TUI keys
                if key in (ord("q"), ord("Q"), 27):
                    return 0
                elif key == ord("/"):                     # open chat
                    chat_open = True
                    chat_input.clear()
                    status_message = f"Chat open — talking to {agent_name}  (Tab to switch, Esc to close)"
                elif key in (ord("r"), ord("R")):
                    next_run_at = time.monotonic()
                    paused = False
                    status_message = "Manual run triggered…"
                elif key in (ord("p"), ord("P")):
                    paused = not paused
                    status_message = "Paused" if paused else "Resumed"
                elif key in (ord("c"), ord("C")):
                    _trigger_dispatch(
                        "council-discussion.yml",
                        {"topic": "Manual agent council: review open PRs, priorities, and team alignment.", "context": "Supervisor TUI manual dispatch."},
                        "Council meeting",
                    )
                elif key in (ord("m"), ord("M")):
                    _trigger_dispatch(
                        "project-manager.yml",
                        {"task": "full-sprint-report"},
                        "PM sprint report",
                    )
                elif key in (ord("a"), ord("A")):
                    _trigger_dispatch(
                        "task-assignment.yml",
                        {"task": "assign-tasks", "extra_context": "Supervisor TUI manual task assignment."},
                        "Task assignment",
                    )
                elif key in (ord("d"), ord("D")):
                    _force_draft_ready()
                elif key in (curses.KEY_UP, ord("k")):
                    log_scroll = max(0, log_scroll - 1)
                elif key in (curses.KEY_DOWN, ord("j")):
                    log_scroll = min(max(0, len(action_log) - 1), log_scroll + 1)

    return curses.wrapper(_main)


def heartbeat(
    repo_root: Path,
    repo_info: dict[str, str],
    args: argparse.Namespace,
    state: dict[str, Any],
    state_file: Path,
    overview_file: Path,
    ledger_file: Path,
) -> int:
    errors, heartbeat_data = run_heartbeat_cycle(repo_root, repo_info, args, state, state_file, overview_file, ledger_file)
    print(heartbeat_data["overview"], end="")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Long-running GitHub heartbeat runner for PR decisions and workflow dispatch.")
    parser.add_argument("--repo", help="Target repository in owner/repo format. Defaults to the current gh repo.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"GitHub Models identifier. Default: {DEFAULT_MODEL}")
    parser.add_argument("--model-every", type=int, default=DEFAULT_MODEL_EVERY, help=f"Use GitHub Models inference every N heartbeats (heuristic otherwise). Default: {DEFAULT_MODEL_EVERY}")
    parser.add_argument("--adaptive-model-cadence", action=argparse.BooleanOptionalAction, default=True, help="Dynamically increase model usage under high queue risk. Default: enabled")
    parser.add_argument("--interval", type=int, default=300, help="Heartbeat interval in seconds. Default: 300")
    parser.add_argument("--pr-limit", type=int, default=30, help="Number of open PRs to inspect per heartbeat. Default: 30")
    parser.add_argument("--issue-limit", type=int, default=50, help="Number of open issues to inspect per heartbeat. Default: 50")
    parser.add_argument("--run-limit", type=int, default=100, help="Number of recent workflow runs to inspect per heartbeat. Default: 100")
    parser.add_argument("--max-actions", type=int, default=25, help="Maximum actions to execute per heartbeat. Default: 25")
    parser.add_argument("--max-retries", type=int, default=2, help="Retry attempts for transient CLI/API errors. Default: 2")
    parser.add_argument("--ledger-file", help="Optional path for decision ledger JSONL output.")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print the queue without mutating GitHub state.")
    parser.add_argument("--once", action="store_true", help="Run a single heartbeat instead of looping forever.")
    parser.add_argument("--tui", action="store_true", help="Run an interactive terminal UI with live heartbeat status.")
    parser.add_argument("--tui-plain", action="store_true", help="Force plain-text TUI rendering without colors or panel layout.")
    return parser.parse_args()


def main() -> int:
    global GH_AUTH_SOURCE, GH_COMMAND_ENV, GH_MAX_RETRIES

    args = parse_args()
    GH_MAX_RETRIES = max(1, args.max_retries)
    repo_root = Path.cwd()
    state_file, overview_file = state_paths()
    ledger_file = Path(args.ledger_file).expanduser() if args.ledger_file else default_ledger_path()
    state = load_state(state_file)
    repo_info = resolve_repo(args.repo)
    GH_COMMAND_ENV, GH_AUTH_SOURCE = resolve_gh_command_env()

    try:
        if args.tui:
            return run_tui(repo_root, repo_info, args, state, state_file, overview_file, ledger_file)

        if args.once:
            return heartbeat(repo_root, repo_info, args, state, state_file, overview_file, ledger_file)

        while True:
            errors = heartbeat(repo_root, repo_info, args, state, state_file, overview_file, ledger_file)
            if errors:
                print(f"Heartbeat completed with {errors} action errors. Sleeping until next interval.", file=sys.stderr)
            time.sleep(max(args.interval, 5))
    except KeyboardInterrupt:
        print("\nHeartbeat runner stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())