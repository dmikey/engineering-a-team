#!/usr/bin/env bash
set -euo pipefail

WORKFLOW_FILE="manual-agent-runner.yml"
DEFAULT_REF="main"

print_help() {
  cat <<'EOF'
Local CLI wrapper for the Manual Agent Runner workflow.

Usage:
  scripts/agent-cli.sh run --agent <name> [options]

Required:
  --agent <qa|pm|po|council|council-sprint|roadmap|self-improvement|task-assignment>

Common options:
  --task <value>
  --topic <value>
  --extra-context <value>
  --wait                       Wait for completion using `gh run watch`
  --ref <branch-or-tag>        Git ref to run against (default: main)

Agent-specific options:
  QA:
    --pr-number <number>

  Council / council-sprint:
    --issue-number <number>
    --sprint-goal <value>      (council-sprint)

  Product Owner:
    --feature-prompt <value>
    --base-url <url>

  Self-improvement:
    --reference-repo <owner/repo>

Examples:
  scripts/agent-cli.sh run --agent qa --pr-number 236 --extra-context "Focus on security regression risk"
  scripts/agent-cli.sh run --agent pm --task full-sprint-report
  scripts/agent-cli.sh run --agent po --task run-playwright --base-url https://app.example.com --wait
  scripts/agent-cli.sh run --agent self-improvement --task full-loop --reference-repo acme/get-milk
EOF
}

require_gh() {
  if ! command -v gh >/dev/null 2>&1; then
    echo "Error: GitHub CLI (gh) is not installed." >&2
    exit 1
  fi
  if ! gh auth status >/dev/null 2>&1; then
    echo "Error: gh is not authenticated. Run: gh auth login" >&2
    exit 1
  fi
}

add_field() {
  local key="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    GH_FIELDS+=("-f" "${key}=${value}")
  fi
}

main() {
  if [[ $# -lt 1 ]]; then
    print_help
    exit 1
  fi

  local command="$1"
  shift

  if [[ "$command" != "run" ]]; then
    print_help
    exit 1
  fi

  local agent=""
  local task=""
  local pr_number=""
  local topic=""
  local sprint_goal=""
  local issue_number=""
  local extra_context=""
  local feature_prompt=""
  local base_url=""
  local reference_repo=""
  local ref="$DEFAULT_REF"
  local wait_for_run="false"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --agent)
        agent="${2:-}"
        shift 2
        ;;
      --task)
        task="${2:-}"
        shift 2
        ;;
      --pr-number)
        pr_number="${2:-}"
        shift 2
        ;;
      --topic)
        topic="${2:-}"
        shift 2
        ;;
      --sprint-goal)
        sprint_goal="${2:-}"
        shift 2
        ;;
      --issue-number)
        issue_number="${2:-}"
        shift 2
        ;;
      --extra-context)
        extra_context="${2:-}"
        shift 2
        ;;
      --feature-prompt)
        feature_prompt="${2:-}"
        shift 2
        ;;
      --base-url)
        base_url="${2:-}"
        shift 2
        ;;
      --reference-repo)
        reference_repo="${2:-}"
        shift 2
        ;;
      --ref)
        ref="${2:-}"
        shift 2
        ;;
      --wait)
        wait_for_run="true"
        shift
        ;;
      -h|--help)
        print_help
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        print_help
        exit 1
        ;;
    esac
  done

  if [[ -z "$agent" ]]; then
    echo "Error: --agent is required." >&2
    print_help
    exit 1
  fi

  case "$agent" in
    qa|pm|po|council|council-sprint|roadmap|self-improvement|task-assignment)
      ;;
    *)
      echo "Error: invalid --agent value: $agent" >&2
      exit 1
      ;;
  esac

  require_gh

  GH_FIELDS=()
  add_field "agent" "$agent"
  add_field "task" "$task"
  add_field "pr_number" "$pr_number"
  add_field "topic" "$topic"
  add_field "sprint_goal" "$sprint_goal"
  add_field "issue_number" "$issue_number"
  add_field "extra_context" "$extra_context"
  add_field "feature_prompt" "$feature_prompt"
  add_field "base_url" "$base_url"
  add_field "reference_repo" "$reference_repo"

  echo "Dispatching $WORKFLOW_FILE on ref '$ref' for agent '$agent'..."
  gh workflow run "$WORKFLOW_FILE" --ref "$ref" "${GH_FIELDS[@]}"

  echo "Workflow dispatched successfully."
  echo "Recent runs:"
  gh run list --workflow "$WORKFLOW_FILE" --limit 5

  if [[ "$wait_for_run" == "true" ]]; then
    echo "Waiting for the newest run to complete..."
    run_id=$(gh run list --workflow "$WORKFLOW_FILE" --limit 1 --json databaseId --jq '.[0].databaseId')
    gh run watch "$run_id"
  fi
}

main "$@"
