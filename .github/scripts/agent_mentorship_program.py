"""Cross-Agent Mentorship Program helpers.

Parses mentorship application/progress issues, performs skill-based mentor↔mentee
matching, and renders a progress/outcomes report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone


_APPLICATION_PREFIX = "Mentorship Application: "
_RELATIONSHIP_PREFIX = "Mentorship Relationship: "
_PROGRESS_PREFIX = "Mentorship Progress: "
_OUTCOME_PREFIX = "Mentorship Outcome: "


def _parse_csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _extract_field(body: str, name: str) -> str:
    pattern = rf"\*\*{re.escape(name)}\*\*:\s*(.+)"
    match = re.search(pattern, body, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _parse_application_title(title: str) -> tuple[str, str] | None:
    if not title.startswith(_APPLICATION_PREFIX):
        return None
    remainder = title[len(_APPLICATION_PREFIX):]
    if " — " not in remainder:
        return None
    agent, role = remainder.split(" — ", 1)
    role_normalized = role.strip().lower()
    if role_normalized not in {"mentor", "mentee"}:
        return None
    return agent.strip(), role_normalized


def _parse_relationship_title(title: str, prefix: str) -> tuple[str, str] | None:
    if not title.startswith(prefix):
        return None
    remainder = title[len(prefix):]
    if " -> " not in remainder:
        return None
    mentor, mentee = remainder.split(" -> ", 1)
    mentor = mentor.strip()
    mentee = mentee.strip()
    if not mentor or not mentee:
        return None
    return mentor, mentee


def collect_applications(issues: list[dict]) -> dict[str, list[dict]]:
    mentors: list[dict] = []
    mentees: list[dict] = []

    for issue in issues:
        parsed_title = _parse_application_title(issue.get("title", ""))
        if not parsed_title:
            continue

        agent, role = parsed_title
        body = issue.get("body", "") or ""
        offered = _parse_csv(_extract_field(body, "Skills Offered"))
        needed = _parse_csv(_extract_field(body, "Skills Needed"))
        goals = _extract_field(body, "Goals")

        max_mentees_raw = _extract_field(body, "Max Mentees")
        try:
            max_mentees = max(1, int(max_mentees_raw)) if max_mentees_raw else 1
        except ValueError:
            max_mentees = 1

        application = {
            "agent": agent,
            "skills_offered": offered,
            "skills_needed": needed,
            "goals": goals,
            "max_mentees": max_mentees,
        }

        if role == "mentor":
            mentors.append(application)
        else:
            mentees.append(application)

    return {"mentors": mentors, "mentees": mentees}


def _match_score(mentor: dict, mentee: dict) -> float:
    needed = mentee["skills_needed"]
    if not needed:
        return 0.0

    offered_overlap = mentor["skills_offered"] & needed
    if not offered_overlap:
        return 0.0

    base = (len(offered_overlap) / len(needed)) * 100.0
    reciprocal_overlap = mentor["skills_needed"] & mentee["skills_offered"]
    bonus = min(len(reciprocal_overlap) * 5.0, 10.0)
    return round(base + bonus, 1)


def build_matches(applications: dict[str, list[dict]]) -> list[dict]:
    mentors = applications.get("mentors", [])
    mentees = applications.get("mentees", [])

    capacities = {m["agent"]: m.get("max_mentees", 1) for m in mentors}
    matches: list[dict] = []

    for mentee in mentees:
        candidates: list[tuple[float, dict]] = []
        for mentor in mentors:
            if mentor["agent"] == mentee["agent"]:
                continue
            score = _match_score(mentor, mentee)
            if score > 0:
                candidates.append((score, mentor))

        candidates.sort(key=lambda item: (-item[0], item[1]["agent"]))

        for score, mentor in candidates:
            if capacities.get(mentor["agent"], 0) <= 0:
                continue
            capacities[mentor["agent"]] -= 1
            matched_skills = sorted(
                list(mentor["skills_offered"] & mentee["skills_needed"])
            )
            matches.append(
                {
                    "mentor": mentor["agent"],
                    "mentee": mentee["agent"],
                    "score": score,
                    "matched_skills": matched_skills,
                }
            )
            break

    # keep deterministic order
    matches.sort(key=lambda m: (m["mentor"], m["mentee"]))
    return matches


def collect_tracking(issues: list[dict], matches: list[dict]) -> dict[str, dict]:
    tracking: dict[str, dict] = {}

    def ensure_pair(mentor: str, mentee: str) -> str:
        key = f"{mentor} -> {mentee}"
        tracking.setdefault(key, {"progress_updates": 0, "outcomes": []})
        return key

    for match in matches:
        ensure_pair(match["mentor"], match["mentee"])

    for issue in issues:
        title = issue.get("title", "")
        body = issue.get("body", "") or ""

        relation = _parse_relationship_title(title, _RELATIONSHIP_PREFIX)
        if relation:
            ensure_pair(*relation)
            continue

        progress = _parse_relationship_title(title, _PROGRESS_PREFIX)
        if progress:
            key = ensure_pair(*progress)
            tracking[key]["progress_updates"] += 1
            continue

        outcome = _parse_relationship_title(title, _OUTCOME_PREFIX)
        if outcome:
            key = ensure_pair(*outcome)
            summary = body.splitlines()[0].strip() if body.strip() else "Outcome recorded"
            tracking[key]["outcomes"].append(summary)

    return tracking


def render_report(
    applications: dict[str, list[dict]],
    matches: list[dict],
    tracking: dict[str, dict],
    current_date: str,
    workflow_url: str,
) -> str:
    mentors = applications.get("mentors", [])
    mentees = applications.get("mentees", [])

    lines = [
        f"# 🤝 Cross-Agent Mentorship Report — {current_date}",
        "",
        "> Generated by the Cross-Agent Mentorship Program",
        "",
        "## Applications",
        "",
        f"- Mentors applied: **{len(mentors)}**",
        f"- Mentees applied: **{len(mentees)}**",
        "",
        "## Skill-Based Matches",
        "",
    ]

    if matches:
        lines += [
            "| Mentor | Mentee | Match Score | Matched Skills |",
            "|--------|--------|-------------|----------------|",
        ]
        for match in matches:
            skills = ", ".join(f"`{s}`" for s in match["matched_skills"]) or "—"
            lines.append(
                f"| {match['mentor']} | {match['mentee']} | {match['score']:.1f}% | {skills} |"
            )
    else:
        lines.append("No mentor/mentee pairs matched yet.")

    lines += ["", "## Mentorship Progress & Outcomes", ""]

    if tracking:
        lines += [
            "| Relationship | Progress Updates | Outcomes Logged |",
            "|--------------|------------------|-----------------|",
        ]
        for relationship, data in sorted(tracking.items()):
            lines.append(
                f"| {relationship} | {data['progress_updates']} | {len(data['outcomes'])} |"
            )
    else:
        lines.append("No mentorship relationships tracked yet.")

    lines += [
        "",
        "---",
        f"*🤖 Automated mentorship report · [Workflow run]({workflow_url})*",
    ]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-Agent Mentorship Program")
    parser.add_argument(
        "--input",
        default="/tmp/mentorship-issues.json",
        help="Path to GitHub Issues JSON payload",
    )
    parser.add_argument(
        "--output-format",
        choices=["report", "matches-json"],
        default="report",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    current_date = os.environ.get("CURRENT_DATE", now.strftime("%Y-%m-%d"))
    workflow_url = os.environ.get("WORKFLOW_URL", "")

    with open(args.input, encoding="utf-8") as fh:
        issues = json.load(fh)

    applications = collect_applications(issues)
    matches = build_matches(applications)
    tracking = collect_tracking(issues, matches)

    if args.output_format == "matches-json":
        print(json.dumps(matches, indent=2))
    else:
        print(render_report(applications, matches, tracking, current_date, workflow_url))


if __name__ == "__main__":
    main()
