"""Agent Personality Profiles helpers.

Parses personality profile issues, computes trait-based compatibility
scores between agents, and renders a profile and pairing-suggestion report.

Predefined traits
-----------------
analytical   – methodical, data-driven reasoning
creative     – innovative, exploratory thinking
empathetic   – user-focused, relational approach
decisive     – assertive, opinionated leadership
collaborative – team-oriented, consensus-seeking
risk-aware   – cautious, thorough evaluation
strategic    – big-picture, long-term orientation

Compatibility is higher when agents share *complementary* traits —
traits that create productive tension rather than duplication.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone


VALID_TRAITS: set[str] = {
    "analytical",
    "creative",
    "empathetic",
    "decisive",
    "collaborative",
    "risk-aware",
    "strategic",
}

# Pairs of traits that complement each other (order-independent)
COMPLEMENTARY_PAIRS: list[tuple[str, str]] = [
    ("analytical", "creative"),
    ("decisive", "collaborative"),
    ("risk-aware", "creative"),
    ("strategic", "analytical"),
    ("empathetic", "decisive"),
    ("strategic", "empathetic"),
    ("risk-aware", "strategic"),
]

# Default profiles for known agents (used when no issue overrides exist)
DEFAULT_PROFILES: dict[str, dict] = {
    "Quinn (QA Engineer)": {
        "traits": ["analytical", "risk-aware", "collaborative"],
        "strengths": "Deep code analysis, security review, risk classification",
        "working_style": "Methodical and thorough; prefers structured feedback loops",
    },
    "Morgan (Project Manager)": {
        "traits": ["decisive", "strategic", "collaborative"],
        "strengths": "Timeline management, priority setting, dependency tracking",
        "working_style": "Data-driven; communicates in milestones and capacity",
    },
    "Alex (Product Owner)": {
        "traits": ["creative", "empathetic", "strategic"],
        "strengths": "Feature ideation, user story mapping, product health analysis",
        "working_style": "Customer-outcome focused; connects every feature to value",
    },
}

_PROFILE_PREFIX = "Agent Personality Profile: "
_UPDATE_PREFIX = "Agent Personality Profile Update: "


def _parse_csv_traits(value: str) -> list[str]:
    return [t.strip().lower() for t in value.split(",") if t.strip().lower() in VALID_TRAITS]


def _extract_field(body: str, name: str) -> str:
    pattern = rf"\*\*{re.escape(name)}\*\*:\s*(.+)"
    match = re.search(pattern, body, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _parse_profile_title(title: str) -> str | None:
    """Return agent name from a profile issue title, or None."""
    if title.startswith(_PROFILE_PREFIX):
        return title[len(_PROFILE_PREFIX):].strip() or None
    return None


def _parse_update_title(title: str) -> str | None:
    """Return agent name from a profile-update issue title, or None."""
    if title.startswith(_UPDATE_PREFIX):
        return title[len(_UPDATE_PREFIX):].strip() or None
    return None


def load_profiles(issues: list[dict]) -> dict[str, dict]:
    """Build a profile map from GitHub issues, falling back to defaults."""
    profiles: dict[str, dict] = {}

    # Seed with defaults
    for agent, data in DEFAULT_PROFILES.items():
        profiles[agent] = {
            "traits": list(data["traits"]),
            "strengths": data["strengths"],
            "working_style": data["working_style"],
            "update_count": 0,
            "last_update": None,
        }

    # Apply profile issues (latest wins per agent)
    for issue in issues:
        title = issue.get("title", "")
        body = issue.get("body", "") or ""
        created_at = issue.get("createdAt", "")

        agent = _parse_profile_title(title)
        if agent is None:
            continue

        traits_raw = _extract_field(body, "Traits")
        traits = _parse_csv_traits(traits_raw) if traits_raw else []
        strengths = _extract_field(body, "Strengths")
        working_style = _extract_field(body, "Working Style")

        existing = profiles.get(agent, {"update_count": 0, "last_update": None})

        profiles[agent] = {
            "traits": traits or existing.get("traits", []),
            "strengths": strengths or existing.get("strengths", ""),
            "working_style": working_style or existing.get("working_style", ""),
            "update_count": existing.get("update_count", 0),
            "last_update": created_at or existing.get("last_update"),
        }

    # Apply update issues (increment update counter, potentially patch traits)
    for issue in issues:
        title = issue.get("title", "")
        body = issue.get("body", "") or ""
        created_at = issue.get("createdAt", "")

        agent = _parse_update_title(title)
        if agent is None:
            continue

        if agent not in profiles:
            profiles[agent] = {
                "traits": [],
                "strengths": "",
                "working_style": "",
                "update_count": 0,
                "last_update": None,
            }

        profiles[agent]["update_count"] += 1

        # If the update supplies new traits, replace them
        traits_raw = _extract_field(body, "Traits")
        if traits_raw:
            new_traits = _parse_csv_traits(traits_raw)
            if new_traits:
                profiles[agent]["traits"] = new_traits

        strengths = _extract_field(body, "Strengths")
        if strengths:
            profiles[agent]["strengths"] = strengths

        working_style = _extract_field(body, "Working Style")
        if working_style:
            profiles[agent]["working_style"] = working_style

        if created_at:
            profiles[agent]["last_update"] = created_at

    return profiles


def _complementary_score(traits_a: list[str], traits_b: list[str]) -> float:
    """Return a 0–100 compatibility score based on complementary trait overlap."""
    set_a = set(traits_a)
    set_b = set(traits_b)

    if not set_a or not set_b:
        return 0.0

    complementary_count = sum(
        1
        for p1, p2 in COMPLEMENTARY_PAIRS
        if (p1 in set_a and p2 in set_b) or (p2 in set_a and p1 in set_b)
    )

    # Shared traits add a small bonus (same perspective reduces blind spots)
    shared = len(set_a & set_b)

    max_possible = len(COMPLEMENTARY_PAIRS)
    score = (complementary_count / max_possible) * 90.0 + min(shared * 3.0, 10.0)
    return round(min(score, 100.0), 1)


def suggest_pairings(profiles: dict[str, dict]) -> list[dict]:
    """Return sorted list of agent pairing suggestions with compatibility scores."""
    agents = sorted(profiles.keys())
    pairings: list[dict] = []

    for i, agent_a in enumerate(agents):
        for agent_b in agents[i + 1:]:
            score = _complementary_score(
                profiles[agent_a]["traits"],
                profiles[agent_b]["traits"],
            )
            pairings.append(
                {
                    "agent_a": agent_a,
                    "agent_b": agent_b,
                    "score": score,
                    "complementary_traits": _find_complementary_traits(
                        profiles[agent_a]["traits"],
                        profiles[agent_b]["traits"],
                    ),
                }
            )

    pairings.sort(key=lambda p: (-p["score"], p["agent_a"], p["agent_b"]))
    return pairings


def _find_complementary_traits(traits_a: list[str], traits_b: list[str]) -> list[str]:
    set_a = set(traits_a)
    set_b = set(traits_b)
    found: list[str] = []
    for p1, p2 in COMPLEMENTARY_PAIRS:
        if (p1 in set_a and p2 in set_b):
            found.append(f"{p1} ↔ {p2}")
        elif (p2 in set_a and p1 in set_b):
            found.append(f"{p2} ↔ {p1}")
    return found


def render_report(
    profiles: dict[str, dict],
    pairings: list[dict],
    current_date: str,
    workflow_url: str,
) -> str:
    lines = [
        f"# 🧠 Agent Personality Profiles Report — {current_date}",
        "",
        "> Generated by the Agent Personality Profiles workflow",
        "",
        "## Agent Profiles",
        "",
    ]

    if profiles:
        for agent, data in sorted(profiles.items()):
            traits_display = ", ".join(f"`{t}`" for t in data["traits"]) or "—"
            update_info = (
                f" · {data['update_count']} update(s)" if data["update_count"] else ""
            )
            lines += [
                f"### {agent}{update_info}",
                "",
                f"**Traits**: {traits_display}",
                "",
                f"**Strengths**: {data['strengths'] or '—'}",
                "",
                f"**Working Style**: {data['working_style'] or '—'}",
                "",
            ]
    else:
        lines.append("No profiles found.")
        lines.append("")

    lines += [
        "## Optimal Agent Pairings",
        "",
        "Pairs are ranked by complementary trait compatibility.",
        "",
    ]

    if pairings:
        lines += [
            "| Agent A | Agent B | Compatibility | Complementary Traits |",
            "|---------|---------|---------------|----------------------|",
        ]
        for pair in pairings:
            traits_display = (
                ", ".join(pair["complementary_traits"]) if pair["complementary_traits"] else "—"
            )
            lines.append(
                f"| {pair['agent_a']} | {pair['agent_b']} "
                f"| {pair['score']:.1f}% | {traits_display} |"
            )
    else:
        lines.append("No pairings available.")

    lines += [
        "",
        "---",
        f"*🤖 Automated personality profile report · [Workflow run]({workflow_url})*",
    ]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Personality Profiles")
    parser.add_argument(
        "--input",
        default="/tmp/personality-issues.json",
        help="Path to GitHub Issues JSON payload",
    )
    parser.add_argument(
        "--output-format",
        choices=["report", "pairings-json", "profiles-json"],
        default="report",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    current_date = os.environ.get("CURRENT_DATE", now.strftime("%Y-%m-%d"))
    workflow_url = os.environ.get("WORKFLOW_URL", "")

    with open(args.input, encoding="utf-8") as fh:
        issues = json.load(fh)

    profiles = load_profiles(issues)
    pairings = suggest_pairings(profiles)

    if args.output_format == "pairings-json":
        print(json.dumps(pairings, indent=2))
    elif args.output_format == "profiles-json":
        print(json.dumps(profiles, indent=2))
    else:
        print(render_report(profiles, pairings, current_date, workflow_url))


if __name__ == "__main__":
    main()
