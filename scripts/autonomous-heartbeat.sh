#!/usr/bin/env bash
set -euo pipefail

on_error() {
  local exit_code=$?
  local line_no=${1:-unknown}
  local cmd=${2:-unknown}
  echo "Error: autonomous-heartbeat failed (line ${line_no}): ${cmd}" >&2
  exit "$exit_code"
}

trap 'on_error "$LINENO" "$BASH_COMMAND"' ERR

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
CLI_SCRIPT="$ROOT_DIR/scripts/agent-cli.sh"
STATE_DIR="$ROOT_DIR/.autonomous"
PID_FILE="$STATE_DIR/heartbeat.pid"
LOG_FILE="$STATE_DIR/heartbeat.log"
HEARTBEAT_FILE="$STATE_DIR/heartbeat.json"
WORKFLOW_FILE="manual-agent-runner.yml"

INTERVAL_SECONDS="${INTERVAL_SECONDS:-900}"
DEFAULT_REF="${DEFAULT_REF:-main}"
STALE_PR_HOURS="${STALE_PR_HOURS:-24}"
STALE_DISCUSSION_HOURS="${STALE_DISCUSSION_HOURS:-24}"
STALE_ISSUE_HOURS="${STALE_ISSUE_HOURS:-48}"
ACTION_FAILURE_WINDOW_HOURS="${ACTION_FAILURE_WINDOW_HOURS:-24}"
SUPERVISOR_ROLE="${SUPERVISOR_ROLE:-scrum-master}"
EVENT_PR_WINDOW_MIN="${EVENT_PR_WINDOW_MIN:-20}"
WAITING_RUN_MIN="${WAITING_RUN_MIN:-15}"
AUTO_READY_DRAFT_PRS="${AUTO_READY_DRAFT_PRS:-true}"
TAIL_LINES="${TAIL_LINES:-80}"

SEQUENCE=(
  "pm:full-sprint-report"
  "pm:groom-backlog"
  "pm:check-milestones"
  "task-assignment:assign-tasks"
)

usage() {
  cat <<'EOF'
Autonomous local heartbeat daemon for engineering-a-team.

Usage:
  scripts/autonomous-heartbeat.sh [options]
  scripts/autonomous-heartbeat.sh start [options]
  scripts/autonomous-heartbeat.sh run [options]
  scripts/autonomous-heartbeat.sh once [options]
  scripts/autonomous-heartbeat.sh doctor
  scripts/autonomous-heartbeat.sh stop
  scripts/autonomous-heartbeat.sh status

Options:
  --interval <seconds>   Heartbeat interval (default: 900)
  --ref <branch>         Ref for workflow dispatch (default: main)
  --max-cycles <n>       Run at most n cycles (run/once modes)
  --stale-pr-hours <h>   Escalate when open PR is stale for h hours (default: 24)
  --stale-discussion-hours <h>  Escalate when discussion is stale for h hours (default: 24)
  --stale-issue-hours <h> Escalate when issue is stale for h hours (default: 48)
  --failure-window-hours <h>     Lookback window for failed Actions runs (default: 24)
  --event-pr-window-min <m>      React to PR updates newer than m minutes (default: 20)
  --waiting-run-min <m>          Treat queued/waiting runs older than m minutes as stalled (default: 15)
  --auto-ready-draft-prs <bool>  Auto-mark recent non-WIP draft PRs ready for review (true/false)
  --follow                       After start, stream daemon log in current terminal
  --tail-lines <n>               Number of log lines to show before follow (default: 80)

Examples:
  scripts/autonomous-heartbeat.sh --interval 600
  scripts/autonomous-heartbeat.sh start --interval 600
  scripts/autonomous-heartbeat.sh status
  scripts/autonomous-heartbeat.sh stop
  scripts/autonomous-heartbeat.sh once --ref main
EOF
}

verify_models_pipeline() {
  local workflow_dir="$ROOT_DIR/.github/workflows"
  MODELS_PIPELINE_OK=0
  MODELS_PIPELINE_NOTE="unverified"

  if [[ ! -d "$workflow_dir" ]]; then
    MODELS_PIPELINE_NOTE="workflow-directory-missing"
    return 0
  fi

  local model_call_count
  local model_permission_count

  if command -v rg >/dev/null 2>&1; then
    model_call_count="$(rg -l "call-github-model" "$workflow_dir" 2>/dev/null | wc -l | tr -d ' ')"
    model_permission_count="$(rg -l "models:\\s*read" "$workflow_dir" 2>/dev/null | wc -l | tr -d ' ')"
  else
    model_call_count="$(grep -RIl "call-github-model" "$workflow_dir" 2>/dev/null | wc -l | tr -d ' ')"
    model_permission_count="$(grep -RIl "models: read" "$workflow_dir" 2>/dev/null | wc -l | tr -d ' ')"
    MODELS_PIPELINE_NOTE="call-github-model-scan-with-grep"
  fi

  if [[ "$model_call_count" -gt 0 && "$model_permission_count" -gt 0 ]]; then
    MODELS_PIPELINE_OK=1
    MODELS_PIPELINE_NOTE="call-github-model-and-models-read-detected"
  else
    MODELS_PIPELINE_NOTE="models-pipeline-signals-missing"
  fi
}

detect_models_token_mode() {
  MODELS_TOKEN_MODE="github-token-fallback"
  if gh secret list 2>/dev/null | awk '{print $1}' | grep -qx "MODELS_TOKEN"; then
    MODELS_TOKEN_MODE="models-token-secret"
  fi
}

ensure_prereqs() {
  mkdir -p "$STATE_DIR"

  if [[ ! -x "$CLI_SCRIPT" ]]; then
    echo "Error: missing or non-executable script: $CLI_SCRIPT" >&2
    exit 1
  fi

  if ! command -v gh >/dev/null 2>&1; then
    echo "Error: GitHub CLI (gh) is not installed." >&2
    exit 1
  fi

  if ! gh auth status >/dev/null 2>&1; then
    echo "Error: gh is not authenticated. Run: gh auth login" >&2
    exit 1
  fi

  verify_models_pipeline
  detect_models_token_mode
}

is_running() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

active_runner_count() {
  gh run list \
    --workflow "$WORKFLOW_FILE" \
    --limit 30 \
    --json status \
    --jq '[.[] | select(.status == "in_progress" or .status == "queued" or .status == "waiting")] | length' \
    2>/dev/null || echo "0"
}

detect_repo_owner_name() {
  local name_with_owner
  name_with_owner="$(gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null || true)"
  if [[ -n "$name_with_owner" && "$name_with_owner" == */* ]]; then
    REPO_OWNER="${name_with_owner%%/*}"
    REPO_NAME="${name_with_owner##*/}"
    return 0
  fi

  REPO_OWNER=""
  REPO_NAME=""
  return 1
}

to_int_or_zero() {
  local value="$1"
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "$value"
  else
    echo "0"
  fi
}

collect_supervisor_signals() {
  local pr_json discussion_json issue_json run_json

  STALE_PR_COUNT=0
  STALE_PR_NUMBER=""
  STALE_PR_AGE_HOURS=0
  STALE_PR_TITLE=""
  STALE_DISCUSSION_COUNT=0
  STALE_DISCUSSION_NUMBER=""
  STALE_DISCUSSION_AGE_HOURS=0
  STALE_DISCUSSION_TITLE=""
  STALE_ISSUE_COUNT=0
  FAILED_ACTION_COUNT=0
  PM_RUNS_LAST_24H=0
  RECENT_PR_EVENT_COUNT=0
  RECENT_PR_NUMBER=""
  RECENT_PR_IS_DRAFT=0
  RECENT_PR_TITLE=""
  WAITING_RUN_COUNT=0
  OLDEST_WAITING_MIN=0

  pr_json="$(gh pr list --state open --limit 50 --json number,title,updatedAt,isDraft,reviewDecision 2>/dev/null || echo '[]')"
  IFS='|' read -r STALE_PR_COUNT STALE_PR_NUMBER STALE_PR_AGE_HOURS STALE_PR_TITLE <<<"$(
    STALE_PR_HOURS="$STALE_PR_HOURS" python3 - <<'PYEOF' "$pr_json"
import json, os, sys
from datetime import datetime, timezone

threshold = int(os.environ.get("STALE_PR_HOURS", "24"))
now = datetime.now(timezone.utc)

try:
    prs = json.loads(sys.argv[1])
except Exception:
    prs = []

stale = []
for pr in prs:
    try:
        updated = datetime.fromisoformat(pr.get("updatedAt", "").replace("Z", "+00:00"))
    except Exception:
        continue
    age_hours = int((now - updated).total_seconds() // 3600)
    if age_hours >= threshold:
        stale.append((age_hours, pr.get("number", ""), pr.get("title", "")))

if not stale:
    print("0|||")
else:
    stale.sort(reverse=True)
    age, number, title = stale[0]
    print(f"{len(stale)}|{number}|{age}|{str(title).replace('|', '/')}" )
PYEOF
  )"

  IFS='|' read -r RECENT_PR_EVENT_COUNT RECENT_PR_NUMBER RECENT_PR_IS_DRAFT RECENT_PR_TITLE <<<"$(
  EVENT_PR_WINDOW_MIN="$EVENT_PR_WINDOW_MIN" python3 - <<'PYEOF' "$pr_json"
import json, os, sys
from datetime import datetime, timezone

window_min = int(os.environ.get("EVENT_PR_WINDOW_MIN", "20"))
now = datetime.now(timezone.utc)

try:
  prs = json.loads(sys.argv[1])
except Exception:
  prs = []

recent = []
for pr in prs:
  try:
    updated = datetime.fromisoformat(pr.get("updatedAt", "").replace("Z", "+00:00"))
  except Exception:
    continue
  age_min = int((now - updated).total_seconds() // 60)
  if age_min <= window_min:
    recent.append((updated, pr.get("number", ""), bool(pr.get("isDraft", False)), pr.get("title", "")))

if not recent:
  print("0|||")
else:
  recent.sort(reverse=True)
  _, number, is_draft, title = recent[0]
  print(f"{len(recent)}|{number}|{1 if is_draft else 0}|{str(title).replace('|', '/')}" )
PYEOF
  )"

  if detect_repo_owner_name; then
    discussion_json="$(gh api graphql \
      -f query='query($owner:String!, $name:String!){repository(owner:$owner,name:$name){discussions(first:40,states:OPEN,orderBy:{field:UPDATED_AT,direction:ASC}){nodes{number title updatedAt comments{totalCount}}}}}' \
      -f owner="$REPO_OWNER" -f name="$REPO_NAME" 2>/dev/null || echo '{}')"
  else
    discussion_json='{}'
  fi

  IFS='|' read -r STALE_DISCUSSION_COUNT STALE_DISCUSSION_NUMBER STALE_DISCUSSION_AGE_HOURS STALE_DISCUSSION_TITLE <<<"$(
    STALE_DISCUSSION_HOURS="$STALE_DISCUSSION_HOURS" python3 - <<'PYEOF' "$discussion_json"
import json, os, sys
from datetime import datetime, timezone

threshold = int(os.environ.get("STALE_DISCUSSION_HOURS", "24"))
now = datetime.now(timezone.utc)

try:
    payload = json.loads(sys.argv[1])
except Exception:
    payload = {}

nodes = (
    payload.get("data", {})
    .get("repository", {})
    .get("discussions", {})
    .get("nodes", [])
)

stale = []
for d in nodes:
    try:
        updated = datetime.fromisoformat(d.get("updatedAt", "").replace("Z", "+00:00"))
    except Exception:
        continue
    age_hours = int((now - updated).total_seconds() // 3600)
    comments = (d.get("comments") or {}).get("totalCount", 0)
    if age_hours >= threshold and comments == 0:
        stale.append((age_hours, d.get("number", ""), d.get("title", "")))

if not stale:
    print("0|||")
else:
    stale.sort(reverse=True)
    age, number, title = stale[0]
    print(f"{len(stale)}|{number}|{age}|{str(title).replace('|', '/')}" )
PYEOF
  )"

  issue_json="$(gh issue list --state open --limit 100 --json number,updatedAt,title 2>/dev/null || echo '[]')"
  STALE_ISSUE_COUNT="$(
    STALE_ISSUE_HOURS="$STALE_ISSUE_HOURS" python3 - <<'PYEOF' "$issue_json"
import json, os, sys
from datetime import datetime, timezone

threshold = int(os.environ.get("STALE_ISSUE_HOURS", "48"))
now = datetime.now(timezone.utc)

try:
    issues = json.loads(sys.argv[1])
except Exception:
    issues = []

count = 0
for issue in issues:
    try:
        updated = datetime.fromisoformat(issue.get("updatedAt", "").replace("Z", "+00:00"))
    except Exception:
        continue
    age_hours = int((now - updated).total_seconds() // 3600)
    if age_hours >= threshold:
        count += 1
print(count)
PYEOF
  )"

  run_json="$(gh run list --limit 80 --json workflowName,conclusion,updatedAt,status,createdAt 2>/dev/null || echo '[]')"
  FAILED_ACTION_COUNT="$(
    ACTION_FAILURE_WINDOW_HOURS="$ACTION_FAILURE_WINDOW_HOURS" python3 - <<'PYEOF' "$run_json"
import json, os, sys
from datetime import datetime, timezone

threshold = int(os.environ.get("ACTION_FAILURE_WINDOW_HOURS", "24"))
now = datetime.now(timezone.utc)

try:
    runs = json.loads(sys.argv[1])
except Exception:
    runs = []

count = 0
for run in runs:
    if (run.get("status") or "") != "completed":
        continue
    if (run.get("conclusion") or "") != "failure":
        continue
    try:
        updated = datetime.fromisoformat(run.get("updatedAt", "").replace("Z", "+00:00"))
    except Exception:
        continue
    age_hours = int((now - updated).total_seconds() // 3600)
    if age_hours <= threshold:
        count += 1
print(count)
PYEOF
  )"

  IFS='|' read -r WAITING_RUN_COUNT OLDEST_WAITING_MIN <<<"$(
  python3 - <<'PYEOF' "$run_json"
import json, sys
from datetime import datetime, timezone

now = datetime.now(timezone.utc)

try:
  runs = json.loads(sys.argv[1])
except Exception:
  runs = []

waiting = []
for run in runs:
  status = (run.get("status") or "").lower()
  if status not in {"queued", "waiting"}:
    continue
  raw = run.get("createdAt") or run.get("updatedAt")
  try:
    created = datetime.fromisoformat((raw or "").replace("Z", "+00:00"))
  except Exception:
    continue
  age_min = int((now - created).total_seconds() // 60)
  waiting.append(age_min)

if not waiting:
  print("0|0")
else:
  print(f"{len(waiting)}|{max(waiting)}")
PYEOF
  )"

  PM_RUNS_LAST_24H="$(
  python3 - <<'PYEOF' "$run_json"
import json, sys
from datetime import datetime, timezone

now = datetime.now(timezone.utc)

try:
  runs = json.loads(sys.argv[1])
except Exception:
  runs = []

count = 0
for run in runs:
  name = (run.get("workflowName") or "").lower()
  if "project manager" not in name:
    continue
  try:
    updated = datetime.fromisoformat(run.get("updatedAt", "").replace("Z", "+00:00"))
  except Exception:
    continue
  age_hours = int((now - updated).total_seconds() // 3600)
  if age_hours <= 24:
    count += 1
print(count)
PYEOF
  )"

  STALE_PR_COUNT="$(to_int_or_zero "$STALE_PR_COUNT")"
  STALE_PR_AGE_HOURS="$(to_int_or_zero "$STALE_PR_AGE_HOURS")"
  STALE_DISCUSSION_COUNT="$(to_int_or_zero "$STALE_DISCUSSION_COUNT")"
  STALE_DISCUSSION_AGE_HOURS="$(to_int_or_zero "$STALE_DISCUSSION_AGE_HOURS")"
  STALE_ISSUE_COUNT="$(to_int_or_zero "$STALE_ISSUE_COUNT")"
  FAILED_ACTION_COUNT="$(to_int_or_zero "$FAILED_ACTION_COUNT")"
  PM_RUNS_LAST_24H="$(to_int_or_zero "$PM_RUNS_LAST_24H")"
  RECENT_PR_EVENT_COUNT="$(to_int_or_zero "$RECENT_PR_EVENT_COUNT")"
  RECENT_PR_IS_DRAFT="$(to_int_or_zero "$RECENT_PR_IS_DRAFT")"
  WAITING_RUN_COUNT="$(to_int_or_zero "$WAITING_RUN_COUNT")"
  OLDEST_WAITING_MIN="$(to_int_or_zero "$OLDEST_WAITING_MIN")"
}

auto_ready_recent_pr_if_needed() {
  AUTO_READY_ACTION="none"

  if [[ "$AUTO_READY_DRAFT_PRS" != "true" ]]; then
    return 0
  fi
  if [[ "$RECENT_PR_EVENT_COUNT" -eq 0 ]]; then
    return 0
  fi
  if [[ "$RECENT_PR_IS_DRAFT" -ne 1 || -z "$RECENT_PR_NUMBER" ]]; then
    return 0
  fi

  local lowered_title
  lowered_title="$(echo "$RECENT_PR_TITLE" | tr '[:upper:]' '[:lower:]')"
  if [[ "$lowered_title" == *"wip"* ]] || [[ "$lowered_title" == *"draft"* ]]; then
    AUTO_READY_ACTION="skipped-wip-draft-title"
    return 0
  fi

  if gh pr ready "$RECENT_PR_NUMBER" >/dev/null 2>&1; then
    AUTO_READY_ACTION="ready-pr-${RECENT_PR_NUMBER}"
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Auto-ready succeeded for PR #$RECENT_PR_NUMBER" | tee -a "$LOG_FILE"
  else
    AUTO_READY_ACTION="ready-failed-pr-${RECENT_PR_NUMBER}"
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Auto-ready failed for PR #$RECENT_PR_NUMBER" | tee -a "$LOG_FILE"
  fi
}

choose_dispatch() {
  local slot="$1"

  DISPATCH_AGENT=""
  DISPATCH_TASK=""
  DISPATCH_TOPIC=""
  DISPATCH_PR_NUMBER=""
  DISPATCH_REASON=""

  if [[ "$RECENT_PR_EVENT_COUNT" -gt 0 ]] && [[ -n "$RECENT_PR_NUMBER" ]]; then
    DISPATCH_AGENT="qa"
    DISPATCH_PR_NUMBER="$RECENT_PR_NUMBER"
    DISPATCH_REASON="recent_pr_event=#${RECENT_PR_NUMBER} window=${EVENT_PR_WINDOW_MIN}m"
    return 0
  fi

  if [[ "$WAITING_RUN_COUNT" -gt 0 ]] && [[ "$OLDEST_WAITING_MIN" -ge "$WAITING_RUN_MIN" ]]; then
    DISPATCH_AGENT="task-assignment"
    DISPATCH_TASK="assign-tasks"
    DISPATCH_REASON="waiting_runs=${WAITING_RUN_COUNT} oldest=${OLDEST_WAITING_MIN}m"
    return 0
  fi

  if [[ "$FAILED_ACTION_COUNT" -gt 0 ]]; then
    DISPATCH_AGENT="pm"
    DISPATCH_TASK="agent-performance-dashboard"
    DISPATCH_REASON="failed_actions=${FAILED_ACTION_COUNT} window=${ACTION_FAILURE_WINDOW_HOURS}h"
    return 0
  fi

  if [[ "$PM_RUNS_LAST_24H" -eq 0 ]]; then
    DISPATCH_AGENT="pm"
    DISPATCH_TASK="full-sprint-report"
    DISPATCH_REASON="pm-heartbeat-missing-last24h"
    return 0
  fi

  if [[ "$STALE_PR_COUNT" -gt 0 ]] && [[ -n "$STALE_PR_NUMBER" ]]; then
    DISPATCH_AGENT="qa"
    DISPATCH_PR_NUMBER="$STALE_PR_NUMBER"
    DISPATCH_REASON="stale_pr=#${STALE_PR_NUMBER} age=${STALE_PR_AGE_HOURS}h"
    return 0
  fi

  if [[ "$STALE_DISCUSSION_COUNT" -gt 0 ]]; then
    DISPATCH_AGENT="pm"
    DISPATCH_TASK="full-sprint-report"
    DISPATCH_REASON="stale_discussions=${STALE_DISCUSSION_COUNT} oldest=#${STALE_DISCUSSION_NUMBER} age=${STALE_DISCUSSION_AGE_HOURS}h"
    return 0
  fi

  if [[ "$STALE_ISSUE_COUNT" -gt 0 ]]; then
    DISPATCH_AGENT="pm"
    DISPATCH_TASK="groom-backlog"
    DISPATCH_REASON="stale_issues=${STALE_ISSUE_COUNT} threshold=${STALE_ISSUE_HOURS}h"
    return 0
  fi

  local spec="${SEQUENCE[$slot]}"
  DISPATCH_AGENT="${spec%%:*}"
  DISPATCH_TASK="${spec#*:}"
  DISPATCH_REASON="scheduled-rotation"
}

write_heartbeat() {
  local status="$1"
  local cycle="$2"
  local slot="$3"
  local agent="$4"
  local task="$5"
  local note="$6"

  cat > "$HEARTBEAT_FILE" <<EOF
{
  "timestamp_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "status": "$status",
  "cycle": $cycle,
  "sequence_slot": $slot,
  "agent": "$agent",
  "task": "$task",
  "note": "$note",
  "signals": {
    "supervisor_role": "${SUPERVISOR_ROLE}",
    "models_pipeline_ok": $MODELS_PIPELINE_OK,
    "models_pipeline_note": "${MODELS_PIPELINE_NOTE}",
    "models_token_mode": "${MODELS_TOKEN_MODE}",
    "failed_actions": $FAILED_ACTION_COUNT,
    "recent_pr_event_count": $RECENT_PR_EVENT_COUNT,
    "recent_pr_number": "${RECENT_PR_NUMBER}",
    "waiting_run_count": $WAITING_RUN_COUNT,
    "oldest_waiting_min": $OLDEST_WAITING_MIN,
    "auto_ready_action": "${AUTO_READY_ACTION}",
    "pm_runs_last_24h": $PM_RUNS_LAST_24H,
    "stale_pr_count": $STALE_PR_COUNT,
    "stale_pr_number": "${STALE_PR_NUMBER}",
    "stale_discussion_count": $STALE_DISCUSSION_COUNT,
    "stale_discussion_number": "${STALE_DISCUSSION_NUMBER}",
    "stale_issue_count": $STALE_ISSUE_COUNT
  }
}
EOF
}

dispatch_slot() {
  local ref="$1"
  local cycle="$2"
  local slot="$3"

  local active_count

  collect_supervisor_signals
  auto_ready_recent_pr_if_needed
  choose_dispatch "$slot"

  local agent="$DISPATCH_AGENT"
  local task="$DISPATCH_TASK"
  local topic="$DISPATCH_TOPIC"
  local pr_number="$DISPATCH_PR_NUMBER"
  local reason="$DISPATCH_REASON"

  active_count="$(active_runner_count)"
  if [[ "$active_count" != "0" ]]; then
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Skip cycle=$cycle agent=$agent task=$task reason=$reason (active manual runner count=$active_count)" | tee -a "$LOG_FILE"
    write_heartbeat "skipped" "$cycle" "$slot" "$agent" "$task" "active run in progress | $reason"
    return 0
  fi

  local context
  context="supervisor_role=$SUPERVISOR_ROLE cycle=$cycle slot=$slot reason=$reason recent_pr_events=$RECENT_PR_EVENT_COUNT recent_pr=$RECENT_PR_NUMBER waiting_runs=$WAITING_RUN_COUNT oldest_waiting_min=$OLDEST_WAITING_MIN stale_prs=$STALE_PR_COUNT stale_discussions=$STALE_DISCUSSION_COUNT stale_issues=$STALE_ISSUE_COUNT failed_actions=$FAILED_ACTION_COUNT pm_runs_24h=$PM_RUNS_LAST_24H models_pipeline_ok=$MODELS_PIPELINE_OK models_token_mode=$MODELS_TOKEN_MODE auto_ready=$AUTO_READY_ACTION host=$(hostname -s 2>/dev/null || echo local)"

  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Dispatch cycle=$cycle agent=$agent task=$task reason=$reason ref=$ref" | tee -a "$LOG_FILE"

  local dispatch_cmd=("$CLI_SCRIPT" run --agent "$agent" --ref "$ref" --extra-context "$context")
  if [[ -n "$task" ]]; then
    dispatch_cmd+=(--task "$task")
  fi
  if [[ -n "$topic" ]]; then
    dispatch_cmd+=(--topic "$topic")
  fi
  if [[ -n "$pr_number" ]]; then
    dispatch_cmd+=(--pr-number "$pr_number")
  fi

  if "${dispatch_cmd[@]}" >> "$LOG_FILE" 2>&1; then
    write_heartbeat "dispatched" "$cycle" "$slot" "$agent" "$task" "workflow dispatched | $reason"
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Dispatch success cycle=$cycle agent=$agent" | tee -a "$LOG_FILE"
  else
    write_heartbeat "error" "$cycle" "$slot" "$agent" "$task" "dispatch failed | $reason"
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Dispatch failed cycle=$cycle agent=$agent" | tee -a "$LOG_FILE"
  fi
}

run_loop() {
  local interval="$1"
  local ref="$2"
  local max_cycles="$3"

  local cycle=0
  local slot=0
  local seq_len="${#SEQUENCE[@]}"

  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Heartbeat loop started interval=${interval}s ref=$ref" | tee -a "$LOG_FILE"

  while true; do
    cycle=$((cycle + 1))
    dispatch_slot "$ref" "$cycle" "$slot"
    slot=$(((slot + 1) % seq_len))

    if [[ "$max_cycles" -gt 0 && "$cycle" -ge "$max_cycles" ]]; then
      echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Max cycles reached ($max_cycles), exiting." | tee -a "$LOG_FILE"
      break
    fi

    sleep "$interval"
  done
}

start_daemon() {
  local interval="$1"
  local ref="$2"
  local max_cycles="$3"
  local stale_pr_hours="$4"
  local stale_discussion_hours="$5"
  local stale_issue_hours="$6"
  local failure_window_hours="$7"
  local event_pr_window_min="$8"
  local waiting_run_min="$9"
  local auto_ready_draft_prs="${10}"
  local follow_log="${11}"
  local tail_lines="${12}"

  ensure_prereqs

  if is_running; then
    echo "Heartbeat daemon already running with PID $(cat "$PID_FILE")." >&2
    exit 1
  fi

  nohup "$SCRIPT_PATH" run \
    --interval "$interval" \
    --ref "$ref" \
    --max-cycles "$max_cycles" \
    --stale-pr-hours "$stale_pr_hours" \
    --stale-discussion-hours "$stale_discussion_hours" \
    --stale-issue-hours "$stale_issue_hours" \
    --failure-window-hours "$failure_window_hours" \
    --event-pr-window-min "$event_pr_window_min" \
    --waiting-run-min "$waiting_run_min" \
    --auto-ready-draft-prs "$auto_ready_draft_prs" \
    >> "$LOG_FILE" 2>&1 &
  local pid=$!
  echo "$pid" > "$PID_FILE"

  # If the child dies immediately, surface log context instead of silently returning.
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Error: heartbeat daemon failed to stay running." >&2
    echo "Recent log output:" >&2
    tail -n "$tail_lines" "$LOG_FILE" 2>/dev/null >&2 || true
    rm -f "$PID_FILE"
    exit 1
  fi

  echo "Heartbeat daemon started."
  echo "PID: $pid"
  echo "Log: $LOG_FILE"
  echo "Heartbeat: $HEARTBEAT_FILE"

  if [[ "$follow_log" == "true" ]]; then
    echo "Following log (Ctrl+C to stop following; daemon keeps running)..."
    touch "$LOG_FILE"
    tail -n "$tail_lines" -f "$LOG_FILE"
  fi
}

stop_daemon() {
  if ! is_running; then
    echo "Heartbeat daemon is not running."
    rm -f "$PID_FILE"
    exit 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "Heartbeat daemon stopped (PID $pid)."
}

doctor() {
  echo "== autonomous-heartbeat doctor =="
  echo "script_path: $SCRIPT_PATH"
  echo "root_dir: $ROOT_DIR"
  echo "cli_script: $CLI_SCRIPT"

  if [[ -x "$SCRIPT_PATH" ]]; then
    echo "script_executable: yes"
  else
    echo "script_executable: no"
  fi

  if [[ -x "$CLI_SCRIPT" ]]; then
    echo "agent_cli_executable: yes"
  else
    echo "agent_cli_executable: no"
  fi

  if command -v gh >/dev/null 2>&1; then
    echo "gh_path: $(command -v gh)"
  else
    echo "gh_path: missing"
  fi

  if command -v rg >/dev/null 2>&1; then
    echo "rg_path: $(command -v rg)"
  else
    echo "rg_path: missing (fallback grep will be used)"
  fi

  if gh auth status >/dev/null 2>&1; then
    echo "gh_auth: ok"
  else
    echo "gh_auth: not-authenticated"
  fi

  if gh repo view --json nameWithOwner --jq '.nameWithOwner' >/dev/null 2>&1; then
    echo "gh_repo_context: ok"
  else
    echo "gh_repo_context: unavailable"
  fi

  echo "state_dir: $STATE_DIR"
  echo "log_file: $LOG_FILE"
  echo "pid_file: $PID_FILE"
}

status_daemon() {
  if is_running; then
    echo "Heartbeat daemon: running (PID $(cat "$PID_FILE"))"
  else
    echo "Heartbeat daemon: stopped"
  fi

  if [[ -f "$HEARTBEAT_FILE" ]]; then
    echo "Last heartbeat:"
    cat "$HEARTBEAT_FILE"
  else
    echo "Last heartbeat: none"
  fi

  echo "Log file: $LOG_FILE"
}

parse_common_flags() {
  INTERVAL="$INTERVAL_SECONDS"
  REF="$DEFAULT_REF"
  MAX_CYCLES="0"
  STALE_PR_HOURS_ARG="$STALE_PR_HOURS"
  STALE_DISCUSSION_HOURS_ARG="$STALE_DISCUSSION_HOURS"
  STALE_ISSUE_HOURS_ARG="$STALE_ISSUE_HOURS"
  ACTION_FAILURE_WINDOW_HOURS_ARG="$ACTION_FAILURE_WINDOW_HOURS"
  EVENT_PR_WINDOW_MIN_ARG="$EVENT_PR_WINDOW_MIN"
  WAITING_RUN_MIN_ARG="$WAITING_RUN_MIN"
  AUTO_READY_DRAFT_PRS_ARG="$AUTO_READY_DRAFT_PRS"
  FOLLOW_LOG="false"
  TAIL_LINES_ARG="$TAIL_LINES"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --interval)
        INTERVAL="${2:-}"
        shift 2
        ;;
      --ref)
        REF="${2:-}"
        shift 2
        ;;
      --max-cycles)
        MAX_CYCLES="${2:-}"
        shift 2
        ;;
      --stale-pr-hours)
        STALE_PR_HOURS_ARG="${2:-}"
        shift 2
        ;;
      --stale-discussion-hours)
        STALE_DISCUSSION_HOURS_ARG="${2:-}"
        shift 2
        ;;
      --stale-issue-hours)
        STALE_ISSUE_HOURS_ARG="${2:-}"
        shift 2
        ;;
      --failure-window-hours)
        ACTION_FAILURE_WINDOW_HOURS_ARG="${2:-}"
        shift 2
        ;;
      --event-pr-window-min)
        EVENT_PR_WINDOW_MIN_ARG="${2:-}"
        shift 2
        ;;
      --waiting-run-min)
        WAITING_RUN_MIN_ARG="${2:-}"
        shift 2
        ;;
      --auto-ready-draft-prs)
        AUTO_READY_DRAFT_PRS_ARG="${2:-}"
        shift 2
        ;;
      --follow)
        FOLLOW_LOG="true"
        shift
        ;;
      --tail-lines)
        TAIL_LINES_ARG="${2:-}"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage
        exit 1
        ;;
    esac
  done

  if [[ ! "$INTERVAL" =~ ^[0-9]+$ ]] || [[ "$INTERVAL" -lt 30 ]]; then
    echo "Error: --interval must be an integer >= 30 seconds." >&2
    exit 1
  fi

  if [[ ! "$MAX_CYCLES" =~ ^[0-9]+$ ]]; then
    echo "Error: --max-cycles must be a non-negative integer." >&2
    exit 1
  fi

  if [[ ! "$STALE_PR_HOURS_ARG" =~ ^[0-9]+$ ]] || [[ "$STALE_PR_HOURS_ARG" -lt 1 ]]; then
    echo "Error: --stale-pr-hours must be an integer >= 1." >&2
    exit 1
  fi

  if [[ ! "$STALE_DISCUSSION_HOURS_ARG" =~ ^[0-9]+$ ]] || [[ "$STALE_DISCUSSION_HOURS_ARG" -lt 1 ]]; then
    echo "Error: --stale-discussion-hours must be an integer >= 1." >&2
    exit 1
  fi

  if [[ ! "$STALE_ISSUE_HOURS_ARG" =~ ^[0-9]+$ ]] || [[ "$STALE_ISSUE_HOURS_ARG" -lt 1 ]]; then
    echo "Error: --stale-issue-hours must be an integer >= 1." >&2
    exit 1
  fi

  if [[ ! "$ACTION_FAILURE_WINDOW_HOURS_ARG" =~ ^[0-9]+$ ]] || [[ "$ACTION_FAILURE_WINDOW_HOURS_ARG" -lt 1 ]]; then
    echo "Error: --failure-window-hours must be an integer >= 1." >&2
    exit 1
  fi

  if [[ ! "$EVENT_PR_WINDOW_MIN_ARG" =~ ^[0-9]+$ ]] || [[ "$EVENT_PR_WINDOW_MIN_ARG" -lt 1 ]]; then
    echo "Error: --event-pr-window-min must be an integer >= 1." >&2
    exit 1
  fi

  if [[ ! "$WAITING_RUN_MIN_ARG" =~ ^[0-9]+$ ]] || [[ "$WAITING_RUN_MIN_ARG" -lt 1 ]]; then
    echo "Error: --waiting-run-min must be an integer >= 1." >&2
    exit 1
  fi

  case "$AUTO_READY_DRAFT_PRS_ARG" in
    true|false) ;;
    *)
      echo "Error: --auto-ready-draft-prs must be 'true' or 'false'." >&2
      exit 1
      ;;
  esac

  if [[ ! "$TAIL_LINES_ARG" =~ ^[0-9]+$ ]] || [[ "$TAIL_LINES_ARG" -lt 1 ]]; then
    echo "Error: --tail-lines must be an integer >= 1." >&2
    exit 1
  fi

  STALE_PR_HOURS="$STALE_PR_HOURS_ARG"
  STALE_DISCUSSION_HOURS="$STALE_DISCUSSION_HOURS_ARG"
  STALE_ISSUE_HOURS="$STALE_ISSUE_HOURS_ARG"
  ACTION_FAILURE_WINDOW_HOURS="$ACTION_FAILURE_WINDOW_HOURS_ARG"
  EVENT_PR_WINDOW_MIN="$EVENT_PR_WINDOW_MIN_ARG"
  WAITING_RUN_MIN="$WAITING_RUN_MIN_ARG"
  AUTO_READY_DRAFT_PRS="$AUTO_READY_DRAFT_PRS_ARG"
  TAIL_LINES="$TAIL_LINES_ARG"
}

main() {
  local cmd="run"

  if [[ $# -gt 0 ]]; then
    case "$1" in
      start|run|once|doctor|stop|status|-h|--help)
        cmd="$1"
        shift
        ;;
      --*)
        # No subcommand: treat flags as foreground run options.
        cmd="run"
        ;;
      *)
        echo "Unknown command: $1" >&2
        usage
        exit 1
        ;;
    esac
  fi

  case "$cmd" in
    start)
      parse_common_flags "$@"
      start_daemon \
        "$INTERVAL" \
        "$REF" \
        "$MAX_CYCLES" \
        "$STALE_PR_HOURS" \
        "$STALE_DISCUSSION_HOURS" \
        "$STALE_ISSUE_HOURS" \
        "$ACTION_FAILURE_WINDOW_HOURS" \
        "$EVENT_PR_WINDOW_MIN" \
        "$WAITING_RUN_MIN" \
        "$AUTO_READY_DRAFT_PRS" \
        "$FOLLOW_LOG" \
        "$TAIL_LINES"
      ;;
    run)
      parse_common_flags "$@"
      ensure_prereqs
      run_loop "$INTERVAL" "$REF" "$MAX_CYCLES"
      ;;
    once)
      parse_common_flags "$@"
      ensure_prereqs
      run_loop "$INTERVAL" "$REF" "1"
      ;;
    doctor)
      doctor
      ;;
    stop)
      stop_daemon
      ;;
    status)
      status_daemon
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown command: $cmd" >&2
      usage
      exit 1
      ;;
  esac
}

main "$@"
