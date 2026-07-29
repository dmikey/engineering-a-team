import importlib.util
import json
import pathlib
import unittest
from datetime import datetime, timezone


MODULE_PATH = pathlib.Path(__file__).with_name("skill_development_advisor.py")
SPEC = importlib.util.spec_from_file_location("skill_development_advisor", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _make_run(path, created, updated, status="completed", conclusion="success"):
    return {
        "path": path,
        "created_at": created,
        "updated_at": updated,
        "status": status,
        "conclusion": conclusion,
    }


class CollectMetricsTests(unittest.TestCase):
    def setUp(self):
        self.since = datetime(2026, 7, 10, tzinfo=timezone.utc)

    def test_counts_runs_within_period(self):
        runs = [
            _make_run(
                ".github/workflows/qa-engineer.yml",
                "2026-07-11T10:00:00Z",
                "2026-07-11T10:03:00Z",
            ),
            _make_run(
                ".github/workflows/qa-engineer.yml",
                "2026-07-11T11:00:00Z",
                "2026-07-11T11:04:00Z",
            ),
        ]
        metrics = MODULE.collect_metrics(runs, self.since)
        quinn = metrics["Quinn (QA Engineer)"]
        self.assertEqual(quinn["runs"], 2)
        self.assertEqual(quinn["failures"], 0)
        self.assertEqual(len(quinn["durations"]), 2)

    def test_excludes_runs_before_since(self):
        runs = [
            _make_run(
                ".github/workflows/qa-engineer.yml",
                "2026-07-01T10:00:00Z",  # before since
                "2026-07-01T10:03:00Z",
            ),
        ]
        metrics = MODULE.collect_metrics(runs, self.since)
        self.assertEqual(metrics["Quinn (QA Engineer)"]["runs"], 0)

    def test_counts_failures(self):
        runs = [
            _make_run(
                ".github/workflows/project-manager.yml",
                "2026-07-11T10:00:00Z",
                "2026-07-11T10:05:00Z",
                conclusion="failure",
            ),
        ]
        metrics = MODULE.collect_metrics(runs, self.since)
        morgan = metrics["Morgan (Project Manager)"]
        self.assertEqual(morgan["runs"], 1)
        self.assertEqual(morgan["failures"], 1)

    def test_skipped_runs_are_not_counted_as_failures(self):
        # Skipped runs arise when the workflow fires (e.g. issues: labeled) but
        # the job condition is not met (label is not "needs-qa").  They must not
        # inflate the failure count or the total run count.
        runs = [
            _make_run(
                ".github/workflows/qa-engineer.yml",
                "2026-07-11T10:00:00Z",
                "2026-07-11T10:00:05Z",
                conclusion="skipped",
            ),
        ]
        metrics = MODULE.collect_metrics(runs, self.since)
        quinn = metrics["Quinn (QA Engineer)"]
        self.assertEqual(quinn["runs"], 0)
        self.assertEqual(quinn["failures"], 0)
        self.assertEqual(len(quinn["durations"]), 0)

    def test_skipped_runs_do_not_affect_success_rate(self):
        # 1 successful run + 9 skipped runs → 100% success rate, not 10%.
        runs = [
            _make_run(
                ".github/workflows/qa-engineer.yml",
                "2026-07-11T10:00:00Z",
                "2026-07-11T10:03:00Z",
                conclusion="success",
            ),
        ] + [
            _make_run(
                ".github/workflows/qa-engineer.yml",
                f"2026-07-{12 + i:02d}T10:00:00Z",
                f"2026-07-{12 + i:02d}T10:00:05Z",
                conclusion="skipped",
            )
            for i in range(9)
        ]
        metrics = MODULE.collect_metrics(runs, self.since)
        quinn = metrics["Quinn (QA Engineer)"]
        self.assertEqual(quinn["runs"], 1)
        self.assertEqual(quinn["failures"], 0)
        success_rate = MODULE.calculate_success_rate(quinn["runs"], quinn["failures"])
        self.assertAlmostEqual(success_rate, 100.0)

    def test_ignores_unknown_workflow(self):
        runs = [
            _make_run(
                ".github/workflows/unknown.yml",
                "2026-07-11T10:00:00Z",
                "2026-07-11T10:03:00Z",
            ),
        ]
        metrics = MODULE.collect_metrics(runs, self.since)
        for data in metrics.values():
            self.assertEqual(data["runs"], 0)

    def test_duration_calculation(self):
        runs = [
            _make_run(
                ".github/workflows/qa-engineer.yml",
                "2026-07-11T10:00:00Z",
                "2026-07-11T10:06:00Z",  # 6-minute run
            ),
        ]
        metrics = MODULE.collect_metrics(runs, self.since)
        quinn = metrics["Quinn (QA Engineer)"]
        self.assertAlmostEqual(quinn["durations"][0], 6.0)


class GenerateSuggestionsTests(unittest.TestCase):
    def _data(self, runs=10, failures=0, durations=None):
        return {
            "runs": runs,
            "failures": failures,
            "durations": durations if durations is not None else [2.0] * runs,
        }

    def test_healthy_agent_returns_positive_message(self):
        data = self._data(runs=10, failures=0, durations=[2.0] * 10)
        suggestions = MODULE.generate_suggestions(
            "Quinn (QA Engineer)", data, ["code-review"]
        )
        self.assertEqual(len(suggestions), 1)
        self.assertIn("healthy", suggestions[0])

    def test_zero_runs_returns_inactive_message(self):
        data = self._data(runs=0, failures=0, durations=[])
        suggestions = MODULE.generate_suggestions(
            "Quinn (QA Engineer)", data, ["code-review"]
        )
        self.assertEqual(len(suggestions), 1)
        self.assertIn("No runs recorded", suggestions[0])

    def test_zero_runs_success_rate_defaults_to_full_score(self):
        self.assertEqual(MODULE.calculate_success_rate(0, 0), 100.0)

    def test_few_runs_suggests_utilisation(self):
        data = self._data(runs=2, failures=0, durations=[2.0, 2.0])
        suggestions = MODULE.generate_suggestions(
            "Quinn (QA Engineer)", data, ["code-review"]
        )
        combined = " ".join(suggestions)
        self.assertIn("utilisation", combined)

    def test_low_success_rate_suggests_reliability(self):
        # 5 failures out of 10 = 50% success rate (below CRIT threshold)
        data = self._data(runs=10, failures=5)
        suggestions = MODULE.generate_suggestions(
            "Quinn (QA Engineer)", data, ["code-review", "pr-feedback"]
        )
        combined = " ".join(suggestions)
        self.assertIn("critically low", combined)

    def test_warn_success_rate_suggests_skill_focus(self):
        # 2 failures out of 10 = 80% success rate (below WARN but above CRIT)
        data = self._data(runs=10, failures=2)
        suggestions = MODULE.generate_suggestions(
            "Quinn (QA Engineer)", data, ["code-review"]
        )
        combined = " ".join(suggestions)
        self.assertIn("below the recommended threshold", combined)

    def test_slow_duration_suggests_optimisation(self):
        data = self._data(runs=10, failures=0, durations=[10.0] * 10)
        suggestions = MODULE.generate_suggestions(
            "Quinn (QA Engineer)", data, ["code-review"]
        )
        combined = " ".join(suggestions)
        self.assertIn("duration", combined)

    def test_security_scan_skill_adds_owasp_suggestion(self):
        # Low success rate + security-scan skill → OWASP suggestion
        data = self._data(runs=10, failures=2)  # 80% < WARN threshold
        suggestions = MODULE.generate_suggestions(
            "Quinn (QA Engineer)", data, ["code-review", "security-scan"]
        )
        combined = " ".join(suggestions)
        self.assertIn("OWASP", combined)

    def test_playwright_testing_skill_adds_browser_suggestion(self):
        data = self._data(runs=10, failures=2)  # 80% < WARN threshold
        suggestions = MODULE.generate_suggestions(
            "Alex (Product Owner)", data, ["playwright-testing"]
        )
        combined = " ".join(suggestions)
        self.assertIn("Playwright", combined)


class LoadRemindersOptInTests(unittest.TestCase):
    def test_empty_string_returns_empty_dict(self):
        result = MODULE.load_reminders_opt_in("")
        self.assertEqual(result, {})

    def test_valid_json_parsed_correctly(self):
        raw = json.dumps({"Quinn (QA Engineer)": True, "Morgan (Project Manager)": False})
        result = MODULE.load_reminders_opt_in(raw)
        self.assertTrue(result["Quinn (QA Engineer)"])
        self.assertFalse(result["Morgan (Project Manager)"])

    def test_invalid_json_returns_empty_dict(self):
        result = MODULE.load_reminders_opt_in("not-valid-json")
        self.assertEqual(result, {})

    def test_non_object_json_returns_empty_dict(self):
        result = MODULE.load_reminders_opt_in('["Quinn (QA Engineer)"]')
        self.assertEqual(result, {})

    def test_unknown_agents_are_ignored(self):
        raw = json.dumps({"Unknown Agent": True, "Quinn (QA Engineer)": True})
        result = MODULE.load_reminders_opt_in(raw)
        self.assertEqual(result, {"Quinn (QA Engineer)": True})

    def test_non_boolean_values_are_ignored(self):
        raw = json.dumps({"Quinn (QA Engineer)": "false"})
        result = MODULE.load_reminders_opt_in(raw)
        self.assertEqual(result, {})


class RenderMarkdownTests(unittest.TestCase):
    def _minimal_metrics(self):
        since = datetime(2026, 7, 1, tzinfo=timezone.utc)
        runs = [
            {
                "path": ".github/workflows/qa-engineer.yml",
                "created_at": "2026-07-11T10:00:00Z",
                "updated_at": "2026-07-11T10:03:00Z",
                "status": "completed",
                "conclusion": "success",
            }
        ]
        return MODULE.collect_metrics(runs, since)

    def test_renders_heading_with_date(self):
        metrics = self._minimal_metrics()
        output = MODULE.render_markdown(
            metrics, {}, "2026-07-20", 30, "https://example.com/run/1"
        )
        self.assertIn("2026-07-20", output)
        self.assertIn("Cross-Agent Skill Development Report", output)

    def test_reminder_on_badge_shown(self):
        metrics = self._minimal_metrics()
        reminders = {"Quinn (QA Engineer)": True}
        output = MODULE.render_markdown(
            metrics, reminders, "2026-07-20", 30, ""
        )
        self.assertIn("Reminders: ON", output)

    def test_reminder_off_badge_shown(self):
        metrics = self._minimal_metrics()
        output = MODULE.render_markdown(
            metrics, {}, "2026-07-20", 30, ""
        )
        self.assertIn("Reminders: OFF", output)

    def test_how_to_enable_section_present(self):
        metrics = self._minimal_metrics()
        output = MODULE.render_markdown(
            metrics, {}, "2026-07-20", 30, ""
        )
        self.assertIn("SKILL_REMINDERS_OPT_IN", output)

    def test_all_agents_appear_in_output(self):
        metrics = self._minimal_metrics()
        output = MODULE.render_markdown(
            metrics, {}, "2026-07-20", 30, ""
        )
        for agent in MODULE.AGENT_WORKFLOWS:
            self.assertIn(agent, output)

    def test_trend_up_badge_shown(self):
        metrics = self._minimal_metrics()
        trend = {agent: {"direction": "up", "delta": 5.0} for agent in metrics}
        output = MODULE.render_markdown(
            metrics, {}, "2026-07-20", 30, "", trend=trend
        )
        self.assertIn("📈 Trend", output)

    def test_trend_down_badge_shown(self):
        metrics = self._minimal_metrics()
        trend = {agent: {"direction": "down", "delta": -5.0} for agent in metrics}
        output = MODULE.render_markdown(
            metrics, {}, "2026-07-20", 30, "", trend=trend
        )
        self.assertIn("📉 Trend", output)

    def test_trend_stable_badge_shown(self):
        metrics = self._minimal_metrics()
        trend = {agent: {"direction": "stable", "delta": 0.0} for agent in metrics}
        output = MODULE.render_markdown(
            metrics, {}, "2026-07-20", 30, "", trend=trend
        )
        self.assertIn("➡️ Trend", output)

    def test_no_trend_badge_when_trend_none(self):
        metrics = self._minimal_metrics()
        output = MODULE.render_markdown(
            metrics, {}, "2026-07-20", 30, "", trend=None
        )
        self.assertNotIn("📈 Trend", output)
        self.assertNotIn("📉 Trend", output)
        self.assertNotIn("➡️ Trend", output)


class CollectTrendTests(unittest.TestCase):
    def _make_metrics(self, runs, failures, durations=None):
        return {
            "runs": runs,
            "failures": failures,
            "durations": durations if durations is not None else [2.0] * runs,
            "last_run": None,
        }

    def test_improvement_shows_up(self):
        current = {"Quinn (QA Engineer)": self._make_metrics(10, 0)}  # 100%
        prior = {"Quinn (QA Engineer)": self._make_metrics(10, 3)}    # 70%
        trend = MODULE.collect_trend(current, prior)
        self.assertEqual(trend["Quinn (QA Engineer)"]["direction"], "up")
        self.assertGreater(trend["Quinn (QA Engineer)"]["delta"], 0)

    def test_regression_shows_down(self):
        current = {"Quinn (QA Engineer)": self._make_metrics(10, 3)}  # 70%
        prior = {"Quinn (QA Engineer)": self._make_metrics(10, 0)}    # 100%
        trend = MODULE.collect_trend(current, prior)
        self.assertEqual(trend["Quinn (QA Engineer)"]["direction"], "down")
        self.assertLess(trend["Quinn (QA Engineer)"]["delta"], 0)

    def test_no_change_shows_stable(self):
        current = {"Quinn (QA Engineer)": self._make_metrics(10, 1)}  # 90%
        prior = {"Quinn (QA Engineer)": self._make_metrics(10, 1)}    # 90%
        trend = MODULE.collect_trend(current, prior)
        self.assertEqual(trend["Quinn (QA Engineer)"]["direction"], "stable")
        self.assertAlmostEqual(trend["Quinn (QA Engineer)"]["delta"], 0.0)

    def test_missing_prior_agent_treated_as_zero_runs(self):
        current = {"Quinn (QA Engineer)": self._make_metrics(10, 0)}
        prior = {}
        trend = MODULE.collect_trend(current, prior)
        # prior rate defaults to 100% (0 runs → 100%), so delta ≈ 0
        self.assertIn("Quinn (QA Engineer)", trend)
        self.assertAlmostEqual(trend["Quinn (QA Engineer)"]["prior_rate"], 100.0)

    def test_run_counts_preserved(self):
        current = {"Quinn (QA Engineer)": self._make_metrics(8, 0)}
        prior = {"Quinn (QA Engineer)": self._make_metrics(5, 1)}
        trend = MODULE.collect_trend(current, prior)
        self.assertEqual(trend["Quinn (QA Engineer)"]["current_runs"], 8)
        self.assertEqual(trend["Quinn (QA Engineer)"]["prior_runs"], 5)


class GenerateAlertsTests(unittest.TestCase):
    def _data(self, runs=10, failures=0, durations=None):
        return {
            "runs": runs,
            "failures": failures,
            "durations": durations if durations is not None else [2.0] * runs,
            "last_run": None,
        }

    def _metrics(self, **overrides):
        metrics = {
            agent: self._data()
            for agent in MODULE.AGENT_WORKFLOWS
        }
        metrics.update(overrides)
        return metrics

    def test_healthy_agents_produce_no_alerts(self):
        metrics = self._metrics()
        alerts = MODULE.generate_alerts(metrics, 30)
        self.assertEqual(alerts, [])

    def test_zero_runs_produces_warning_alert(self):
        metrics = self._metrics(**{"Quinn (QA Engineer)": self._data(runs=0)})
        alerts = MODULE.generate_alerts(metrics, 30)
        quinn_alerts = [a for a in alerts if a["agent"] == "Quinn (QA Engineer)"]
        self.assertEqual(len(quinn_alerts), 1)
        self.assertEqual(quinn_alerts[0]["severity"], "warning")

    def test_critically_low_success_rate_produces_critical_alert(self):
        # 5 failures / 10 runs = 50% → below CRIT threshold
        metrics = self._metrics(**{"Morgan (Project Manager)": self._data(runs=10, failures=5)})
        alerts = MODULE.generate_alerts(metrics, 30)
        morgan_alerts = [a for a in alerts if a["agent"] == "Morgan (Project Manager)"]
        self.assertEqual(len(morgan_alerts), 1)
        self.assertEqual(morgan_alerts[0]["severity"], "critical")

    def test_warn_success_rate_produces_warning_alert(self):
        # 2 failures / 10 runs = 80% → below WARN (85%) but above CRIT (70%)
        metrics = self._metrics(**{"Alex (Product Owner)": self._data(runs=10, failures=2)})
        alerts = MODULE.generate_alerts(metrics, 30)
        alex_alerts = [a for a in alerts if a["agent"] == "Alex (Product Owner)"]
        self.assertEqual(len(alex_alerts), 1)
        self.assertEqual(alex_alerts[0]["severity"], "warning")

    def test_alert_contains_required_fields(self):
        metrics = self._metrics(**{"Quinn (QA Engineer)": self._data(runs=10, failures=5)})
        alerts = MODULE.generate_alerts(metrics, 30)
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        for field in ("agent", "severity", "reason", "success_rate", "runs", "failures", "suggestions"):
            self.assertIn(field, alert)

    def test_alert_suggestions_are_non_empty(self):
        metrics = self._metrics(**{"Quinn (QA Engineer)": self._data(runs=10, failures=5)})
        alerts = MODULE.generate_alerts(metrics, 30)
        self.assertTrue(len(alerts[0]["suggestions"]) > 0)

    def test_multiple_underperforming_agents_produce_multiple_alerts(self):
        metrics = self._metrics(
            **{
                "Quinn (QA Engineer)": self._data(runs=10, failures=5),
                "Alex (Product Owner)": self._data(runs=0),
            }
        )
        alerts = MODULE.generate_alerts(metrics, 30)
        self.assertEqual(len(alerts), 2)


if __name__ == "__main__":
    unittest.main()
