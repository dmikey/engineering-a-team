import json
import os
import re
import subprocess
import sys


def run_command(command):
    return subprocess.run(command, capture_output=True, text=True, check=False)


def main():
    raw = os.environ.get("RECOMMENDATIONS_JSON", "")
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        print("No JSON array found in recommendations")
        return 0

    try:
        recommendations = json.loads(match.group())
    except json.JSONDecodeError as exc:
        print(f"Failed to parse recommendations JSON: {exc}")
        return 0

    existing_raw = run_command(
        ["gh", "issue", "list", "--state", "open", "--limit", "100", "--json", "number,title"]
    ).stdout or "[]"

    try:
        existing = {item["title"] for item in json.loads(existing_raw)}
    except Exception:
        existing = set()

    reference_repo = os.environ.get("REFERENCE_REPO", "")
    reference_base_url = os.environ.get("REFERENCE_BASE_URL", "")
    current_date = os.environ.get("CURRENT_DATE", "")
    copilot_assignee = os.environ.get("COPILOT_ASSIGNEE", "")

    for rec in recommendations[:3]:
        title = rec.get("title", "Workflow self-improvement")
        issue_title = f"[Self-Improvement] {title}"
        if issue_title in existing:
            print(f"Skipping duplicate open issue: {issue_title}")
            continue

        labels = set(rec.get("labels") or [])
        labels.update({"self-improvement", "copilot-ready", "benchmarking"})
        if rec.get("needs_qa"):
            labels.add("needs-qa")
        if rec.get("needs_council"):
            labels.add("council-review")

        evidence = rec.get("evidence") or []
        evidence_md = "\n".join(f"- {item}" for item in evidence) or "- No evidence supplied"
        body = "\n".join(
            [
                "## Summary",
                rec.get("summary", ""),
                "",
                "## Benchmark Gap",
                rec.get("benchmark_gap", ""),
                "",
                "## Evidence",
                evidence_md,
                "",
                "## Implementation Hint",
                rec.get("implementation_hint", ""),
                "",
                "## Copilot Brief",
                rec.get("copilot_brief", ""),
                "",
                "## Benchmark Context",
                "- Reference project: Get Milk",
                f"- Reference repo: {reference_repo or 'Not configured'}",
                f"- Reference base URL: {reference_base_url or 'Not configured'}",
                f"- Generated: {current_date}",
                "",
                "## Native GitHub Copilot Handoff",
                "- This issue is intended for native GitHub Copilot assignment.",
                "- Assign Copilot in the GitHub issue UI after triage if your repository has the feature enabled.",
                "- If automatic assignment is configured in this repository, the workflow will attempt it.",
            ]
        )

        command = ["gh", "issue", "create", "--title", issue_title, "--body", body]
        for label in sorted(labels):
            command.extend(["--label", label])

        created = run_command(command)
        if created.returncode != 0:
            print(f"Failed to create issue {issue_title}: {created.stderr}")
            continue

        issue_url = created.stdout.strip().splitlines()[-1]
        issue_number = issue_url.rstrip("/").split("/")[-1]
        print(f"Created {issue_title}: {issue_url}")

        if copilot_assignee:
            assign = run_command(
                ["gh", "issue", "edit", issue_number, "--add-assignee", copilot_assignee]
            )
            if assign.returncode != 0:
                print(
                    f"Automatic Copilot assignment failed for #{issue_number}: {assign.stderr.strip()}"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())