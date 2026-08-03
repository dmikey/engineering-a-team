#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: auto-merge-prs.sh [options]

Merge or auto-merge open pull requests that are ready to land.

Options:
  --repo OWNER/REPO   Target repository. Defaults to the current gh repo.
  --base BRANCH       Only process pull requests targeting this base branch.
  --method METHOD     Merge method: squash, merge, or rebase. Default: squash.
  --limit COUNT       Number of open pull requests to inspect. Default: 100.
  --dry-run           Print the actions without merging anything.
  --help              Show this help message.

The script skips pull requests that are drafts, have merge conflicts, or have
blocking review decisions. For eligible pull requests it runs:

  gh pr merge --auto --delete-branch

GitHub merges immediately when allowed, or enables auto-merge when checks are
still pending.
EOF
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: missing required command: $1" >&2
    exit 1
  fi
}

fetch_pr_record() {
  local number="$1"
  local attempts=0
  local record=""
  local mergeable="UNKNOWN"

  while [[ $attempts -lt 3 ]]; do
    record=$(gh pr view "${repo_args[@]}" "$number" \
      --json number,title,mergeable,reviewDecision,mergeStateStatus,statusCheckRollup,autoMergeRequest)
    mergeable=$(jq -r '.mergeable' <<<"$record")

    if [[ "$mergeable" != "UNKNOWN" ]]; then
      printf '%s\n' "$record"
      return 0
    fi

    attempts=$((attempts + 1))
  done

  printf '%s\n' "$record"
}

repo=""
base=""
method="squash"
limit=100
dry_run=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      repo="$2"
      shift 2
      ;;
    --base)
      base="$2"
      shift 2
      ;;
    --method)
      method="$2"
      shift 2
      ;;
    --limit)
      limit="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "$method" in
  squash|merge|rebase)
    ;;
  *)
    echo "error: --method must be one of: squash, merge, rebase" >&2
    exit 1
    ;;
esac

require_command gh
require_command jq

if ! gh auth status >/dev/null 2>&1; then
  echo "error: gh is not authenticated. Run 'gh auth login' first." >&2
  exit 1
fi

repo_args=()
if [[ -n "$repo" ]]; then
  repo_args=(--repo "$repo")
fi

prs_json=$(gh pr list "${repo_args[@]}" \
  --state open \
  --limit "$limit" \
  --json number,title,isDraft,baseRefName)

mapfile -t candidates < <(
  jq -r --arg base "$base" '
    .[]
    | select(($base == "" or .baseRefName == $base) and (.isDraft | not))
    | @base64
  ' <<<"$prs_json"
)

if [[ ${#candidates[@]} -eq 0 ]]; then
  echo "No open non-draft pull requests matched the requested scope."
  exit 0
fi

processed=0
merged_or_queued=0
skipped=0
failed=0

for row in "${candidates[@]}"; do
  list_record=$(printf '%s' "$row" | base64 --decode)
  number=$(jq -r '.number' <<<"$list_record")

  record=$(fetch_pr_record "$number")

  title=$(jq -r '.title' <<<"$record")
  mergeable=$(jq -r '.mergeable' <<<"$record")
  review_decision=$(jq -r '.reviewDecision' <<<"$record")
  merge_state=$(jq -r '.mergeStateStatus' <<<"$record")
  pending_checks=$(jq -r '[.statusCheckRollup[]? | select((.status // "") == "PENDING" or (.conclusion // "") == "")] | length' <<<"$record")
  failing_checks=$(jq -r '[.statusCheckRollup[]? | select((.conclusion // "") | IN("ACTION_REQUIRED", "CANCELLED", "FAILURE", "STALE", "STARTUP_FAILURE", "TIMED_OUT"))] | length' <<<"$record")
  auto_merge_enabled=$(jq -r '(.autoMergeRequest != null)' <<<"$record")

  processed=$((processed + 1))

  if [[ "$mergeable" == "CONFLICTING" ]]; then
    echo "Skipping #$number: mergeable=$mergeable ($title)"
    skipped=$((skipped + 1))
    continue
  fi

  if [[ "$mergeable" != "MERGEABLE" && "$mergeable" != "UNKNOWN" ]]; then
    echo "Skipping #$number: mergeable=$mergeable ($title)"
    skipped=$((skipped + 1))
    continue
  fi

  if [[ "$review_decision" == "CHANGES_REQUESTED" || "$review_decision" == "REVIEW_REQUIRED" ]]; then
    echo "Skipping #$number: reviewDecision=$review_decision ($title)"
    skipped=$((skipped + 1))
    continue
  fi

  if [[ "$failing_checks" != "0" ]]; then
    echo "Skipping #$number: failing checks=$failing_checks ($title)"
    skipped=$((skipped + 1))
    continue
  fi

  if [[ "$mergeable" == "UNKNOWN" ]]; then
    echo "Proceeding with #$number despite mergeable=UNKNOWN; deferring final readiness check to GitHub ($title)"
  fi

  action="merge now"
  if [[ "$pending_checks" != "0" || "$auto_merge_enabled" == "true" || "$merge_state" == "UNSTABLE" || "$merge_state" == "BLOCKED" ]]; then
    action="enable auto-merge"
  fi

  echo "Queueing #$number for $action: $title"

  cmd=(gh pr merge "${repo_args[@]}" "$number" "--$method" --auto --delete-branch)

  if [[ "$dry_run" == "true" ]]; then
    printf 'DRY RUN:'
    printf ' %q' "${cmd[@]}"
    printf '\n'
    merged_or_queued=$((merged_or_queued + 1))
    continue
  fi

  if output=$("${cmd[@]}" 2>&1); then
    echo "$output"
    merged_or_queued=$((merged_or_queued + 1))
  else
    echo "Failed to process #$number: $title" >&2
    echo "$output" >&2
    failed=$((failed + 1))
  fi
done

echo
echo "Processed: $processed"
echo "Merged or queued: $merged_or_queued"
echo "Skipped: $skipped"
echo "Failed: $failed"

if [[ "$failed" != "0" ]]; then
  exit 1
fi