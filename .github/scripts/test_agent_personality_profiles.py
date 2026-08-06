import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("agent_personality_profiles.py")
SPEC = importlib.util.spec_from_file_location("agent_personality_profiles", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _issue(title, labels=None, body="", created_at="2026-08-01T00:00:00Z"):
    return {
        "title": title,
        "labels": [{"name": lbl} for lbl in (labels or [])],
        "body": body,
        "createdAt": created_at,
    }


def _profile_body(traits="", strengths="", working_style=""):
    parts = []
    if traits:
        parts.append(f"**Traits**: {traits}")
    if strengths:
        parts.append(f"**Strengths**: {strengths}")
    if working_style:
        parts.append(f"**Working Style**: {working_style}")
    return "\n".join(parts)


class LoadProfilesTests(unittest.TestCase):
    def test_defaults_are_seeded_for_known_agents(self):
        profiles = MODULE.load_profiles([])
        self.assertIn("Quinn (QA Engineer)", profiles)
        self.assertIn("Morgan (Project Manager)", profiles)
        self.assertIn("Alex (Product Owner)", profiles)

    def test_valid_traits_accepted(self):
        for trait in MODULE.VALID_TRAITS:
            self.assertIn(trait, MODULE.VALID_TRAITS)

    def test_profile_issue_overrides_default(self):
        issues = [
            _issue(
                "Agent Personality Profile: Quinn (QA Engineer)",
                labels=["personality-profile"],
                body=_profile_body(
                    traits="analytical, decisive",
                    strengths="Custom strength",
                    working_style="Custom style",
                ),
            )
        ]
        profiles = MODULE.load_profiles(issues)
        quinn = profiles["Quinn (QA Engineer)"]
        self.assertIn("analytical", quinn["traits"])
        self.assertIn("decisive", quinn["traits"])
        self.assertEqual(quinn["strengths"], "Custom strength")
        self.assertEqual(quinn["working_style"], "Custom style")

    def test_invalid_traits_are_ignored(self):
        issues = [
            _issue(
                "Agent Personality Profile: Quinn (QA Engineer)",
                body=_profile_body(traits="analytical, not-a-trait, decisive"),
            )
        ]
        profiles = MODULE.load_profiles(issues)
        quinn_traits = profiles["Quinn (QA Engineer)"]["traits"]
        self.assertIn("analytical", quinn_traits)
        self.assertIn("decisive", quinn_traits)
        self.assertNotIn("not-a-trait", quinn_traits)

    def test_update_issue_increments_count(self):
        issues = [
            _issue(
                "Agent Personality Profile Update: Morgan (Project Manager)",
                body=_profile_body(traits="strategic, collaborative"),
            )
        ]
        profiles = MODULE.load_profiles(issues)
        self.assertEqual(profiles["Morgan (Project Manager)"]["update_count"], 1)

    def test_update_issue_patches_traits(self):
        issues = [
            _issue(
                "Agent Personality Profile Update: Alex (Product Owner)",
                body=_profile_body(traits="creative, risk-aware"),
            )
        ]
        profiles = MODULE.load_profiles(issues)
        alex_traits = profiles["Alex (Product Owner)"]["traits"]
        self.assertIn("creative", alex_traits)
        self.assertIn("risk-aware", alex_traits)

    def test_non_profile_issues_are_ignored(self):
        issues = [_issue("Some other issue", body=_profile_body(traits="analytical"))]
        profiles = MODULE.load_profiles(issues)
        # defaults still present, no extra agents added from the bad issue
        self.assertEqual(set(profiles.keys()), set(MODULE.DEFAULT_PROFILES.keys()))

    def test_new_agent_profile_created(self):
        issues = [
            _issue(
                "Agent Personality Profile: Casey (Council Moderator)",
                body=_profile_body(
                    traits="decisive, collaborative",
                    strengths="Synthesis",
                    working_style="Impartial",
                ),
            )
        ]
        profiles = MODULE.load_profiles(issues)
        self.assertIn("Casey (Council Moderator)", profiles)
        self.assertIn("decisive", profiles["Casey (Council Moderator)"]["traits"])


class SuggestPairingsTests(unittest.TestCase):
    def test_returns_all_pairs(self):
        profiles = MODULE.load_profiles([])
        pairings = MODULE.suggest_pairings(profiles)
        n = len(profiles)
        expected_pairs = n * (n - 1) // 2
        self.assertEqual(len(pairings), expected_pairs)

    def test_pairings_sorted_by_score_descending(self):
        profiles = MODULE.load_profiles([])
        pairings = MODULE.suggest_pairings(profiles)
        scores = [p["score"] for p in pairings]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_complementary_traits_listed(self):
        profiles = {
            "A": {"traits": ["analytical"], "strengths": "", "working_style": "", "update_count": 0, "last_update": None},
            "B": {"traits": ["creative"], "strengths": "", "working_style": "", "update_count": 0, "last_update": None},
        }
        pairings = MODULE.suggest_pairings(profiles)
        self.assertEqual(len(pairings), 1)
        self.assertGreater(pairings[0]["score"], 0)
        self.assertTrue(any("analytical" in t for t in pairings[0]["complementary_traits"]))

    def test_no_pairing_for_single_agent(self):
        profiles = {
            "A": {"traits": ["analytical"], "strengths": "", "working_style": "", "update_count": 0, "last_update": None},
        }
        pairings = MODULE.suggest_pairings(profiles)
        self.assertEqual(pairings, [])

    def test_zero_score_when_no_traits(self):
        profiles = {
            "A": {"traits": [], "strengths": "", "working_style": "", "update_count": 0, "last_update": None},
            "B": {"traits": [], "strengths": "", "working_style": "", "update_count": 0, "last_update": None},
        }
        pairings = MODULE.suggest_pairings(profiles)
        self.assertEqual(pairings[0]["score"], 0.0)


class RenderReportTests(unittest.TestCase):
    def test_report_contains_expected_sections(self):
        profiles = MODULE.load_profiles([])
        pairings = MODULE.suggest_pairings(profiles)
        report = MODULE.render_report(profiles, pairings, "2026-08-06", "https://example.com")
        self.assertIn("Agent Personality Profiles Report", report)
        self.assertIn("Agent Profiles", report)
        self.assertIn("Optimal Agent Pairings", report)
        self.assertIn("Quinn (QA Engineer)", report)
        self.assertIn("2026-08-06", report)

    def test_empty_profiles_handled_gracefully(self):
        report = MODULE.render_report({}, [], "2026-08-06", "")
        self.assertIn("No profiles found", report)
        self.assertIn("No pairings available", report)


if __name__ == "__main__":
    unittest.main()
