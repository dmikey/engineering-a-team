import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("agent_mentorship_program.py")
SPEC = importlib.util.spec_from_file_location("agent_mentorship_program", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _issue(title, labels=None, body=""):
    return {
        "title": title,
        "labels": [{"name": lbl} for lbl in (labels or [])],
        "body": body,
    }


def _application_body(offered="", needed="", goals="", max_mentees=""):
    parts = []
    if offered:
        parts.append(f"- **Skills Offered**: {offered}")
    if needed:
        parts.append(f"- **Skills Needed**: {needed}")
    if goals:
        parts.append(f"- **Goals**: {goals}")
    if max_mentees:
        parts.append(f"- **Max Mentees**: {max_mentees}")
    return "\n".join(parts)


class CollectApplicationsTests(unittest.TestCase):
    def test_collects_mentor_and_mentee_applications(self):
        issues = [
            _issue(
                "Mentorship Application: Quinn (QA Engineer) — Mentor",
                body=_application_body(
                    offered="code-review, security-scan",
                    needed="discussion-creation",
                    goals="Help with QA and security",
                    max_mentees="2",
                ),
            ),
            _issue(
                "Mentorship Application: Alex (Product Owner) — Mentee",
                body=_application_body(
                    offered="product-analysis",
                    needed="code-review, security-scan",
                    goals="Improve PR quality reviews",
                ),
            ),
        ]

        apps = MODULE.collect_applications(issues)
        self.assertEqual(len(apps["mentors"]), 1)
        self.assertEqual(len(apps["mentees"]), 1)
        self.assertEqual(apps["mentors"][0]["max_mentees"], 2)
        self.assertIn("code-review", apps["mentors"][0]["skills_offered"])
        self.assertIn("security-scan", apps["mentees"][0]["skills_needed"])

    def test_ignores_non_application_titles(self):
        issues = [_issue("Regular Issue", body="- **Skills Offered**: code-review")]
        apps = MODULE.collect_applications(issues)
        self.assertEqual(apps, {"mentors": [], "mentees": []})


class BuildMatchesTests(unittest.TestCase):
    def test_matches_based_on_skills_needed(self):
        applications = {
            "mentors": [
                {
                    "agent": "Quinn (QA Engineer)",
                    "skills_offered": {"code-review", "security-scan"},
                    "skills_needed": set(),
                    "goals": "",
                    "max_mentees": 1,
                }
            ],
            "mentees": [
                {
                    "agent": "Alex (Product Owner)",
                    "skills_offered": {"product-analysis"},
                    "skills_needed": {"code-review"},
                    "goals": "",
                    "max_mentees": 1,
                }
            ],
        }

        matches = MODULE.build_matches(applications)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["mentor"], "Quinn (QA Engineer)")
        self.assertEqual(matches[0]["mentee"], "Alex (Product Owner)")
        self.assertIn("code-review", matches[0]["matched_skills"])

    def test_respects_mentor_capacity(self):
        applications = {
            "mentors": [
                {
                    "agent": "Quinn (QA Engineer)",
                    "skills_offered": {"code-review"},
                    "skills_needed": set(),
                    "goals": "",
                    "max_mentees": 1,
                }
            ],
            "mentees": [
                {
                    "agent": "Alex (Product Owner)",
                    "skills_offered": set(),
                    "skills_needed": {"code-review"},
                    "goals": "",
                    "max_mentees": 1,
                },
                {
                    "agent": "Morgan (Project Manager)",
                    "skills_offered": set(),
                    "skills_needed": {"code-review"},
                    "goals": "",
                    "max_mentees": 1,
                },
            ],
        }

        matches = MODULE.build_matches(applications)
        self.assertEqual(len(matches), 1)


class TrackingAndReportTests(unittest.TestCase):
    def test_collect_tracking_counts_progress_and_outcomes(self):
        matches = [
            {
                "mentor": "Quinn (QA Engineer)",
                "mentee": "Alex (Product Owner)",
                "score": 100.0,
                "matched_skills": ["code-review"],
            }
        ]
        issues = [
            _issue("Mentorship Progress: Quinn (QA Engineer) -> Alex (Product Owner)"),
            _issue(
                "Mentorship Outcome: Quinn (QA Engineer) -> Alex (Product Owner)",
                body="Alex now leads code review summaries.",
            ),
        ]

        tracking = MODULE.collect_tracking(issues, matches)
        key = "Quinn (QA Engineer) -> Alex (Product Owner)"
        self.assertEqual(tracking[key]["progress_updates"], 1)
        self.assertEqual(len(tracking[key]["outcomes"]), 1)

    def test_render_report_includes_core_sections(self):
        applications = {
            "mentors": [{"agent": "Q", "skills_offered": set(), "skills_needed": set(), "goals": "", "max_mentees": 1}],
            "mentees": [{"agent": "A", "skills_offered": set(), "skills_needed": {"x"}, "goals": "", "max_mentees": 1}],
        }
        matches = [{"mentor": "Q", "mentee": "A", "score": 90.0, "matched_skills": ["x"]}]
        tracking = {"Q -> A": {"progress_updates": 2, "outcomes": ["Done"]}}

        report = MODULE.render_report(applications, matches, tracking, "2026-08-06", "https://example.com")
        self.assertIn("Cross-Agent Mentorship Report", report)
        self.assertIn("Skill-Based Matches", report)
        self.assertIn("Mentorship Progress & Outcomes", report)
        self.assertIn("Q", report)
        self.assertIn("A", report)


if __name__ == "__main__":
    unittest.main()
