import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone

MODULE_PATH = pathlib.Path(__file__).with_name("task_assignment.py")
SPEC = importlib.util.spec_from_file_location("task_assignment", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _make_run(path, created, updated=None, status="completed", conclusion="success"):
    return {
        "path": path,
        "created_at": created,
        "updated_at": updated or created,
        "status": status,
        "conclusion": conclusion,
    }


class CollectMetricsTests(unittest.TestCase):
    def test_active_runs_counted(self):
        runs = [
            _make_run(
                ".github/workflows/qa-engineer.yml",
                "2026-07-20T09:00:00Z",
                status="in_progress",
                conclusion="",
            ),
        ]
        metrics = MODULE.collect_agent_metrics(runs)
        self.assertEqual(metrics["Quinn (QA Engineer)"]["active_runs"], 1)
        self.assertEqual(metrics["Morgan (Project Manager)"]["active_runs"], 0)

    def test_recent_and_perf_runs(self):
        # One run inside 24 h and 30-day window; one only in 30-day window
        runs = [
            _make_run(
                ".github/workflows/project-manager.yml",
                "2026-07-20T01:00:00Z",
                "2026-07-20T01:05:00Z",
            ),
            _make_run(
                ".github/workflows/project-manager.yml",
                "2026-07-01T10:00:00Z",
                "2026-07-01T10:05:00Z",
                conclusion="failure",
            ),
        ]
        # Patch now so tests are deterministic
        from datetime import timedelta

        fake_now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
        original_now = MODULE.datetime

        class _FakeDatetime(MODULE.datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_now

        MODULE.datetime = _FakeDatetime
        try:
            metrics = MODULE.collect_agent_metrics(runs)
        finally:
            MODULE.datetime = original_now

        morgan = metrics["Morgan (Project Manager)"]
        self.assertEqual(morgan["recent_runs"], 1)
        self.assertEqual(morgan["perf_runs"], 2)
        self.assertEqual(morgan["perf_failures"], 1)

    def test_unknown_workflow_ignored(self):
        runs = [
            _make_run(".github/workflows/unknown.yml", "2026-07-20T09:00:00Z"),
        ]
        metrics = MODULE.collect_agent_metrics(runs)
        for data in metrics.values():
            self.assertEqual(data["active_runs"], 0)
            self.assertEqual(data["recent_runs"], 0)


class BuildScoresTests(unittest.TestCase):
    def test_idle_agent_scores_high(self):
        metrics = {
            "Quinn (QA Engineer)": {
                "active_runs": 0,
                "recent_runs": 0,
                "perf_runs": 10,
                "perf_failures": 0,
                "durations": [2.0] * 10,
                "last_run": None,
            },
        }
        scores = MODULE.build_agent_scores(metrics)
        self.assertGreater(scores["Quinn (QA Engineer)"], 90)

    def test_busy_agent_scores_lower(self):
        base = {
            "active_runs": 3,
            "recent_runs": 5,
            "perf_runs": 10,
            "perf_failures": 0,
            "durations": [2.0] * 10,
            "last_run": None,
        }
        idle = dict(base, active_runs=0, recent_runs=0)
        scores_busy = MODULE.build_agent_scores({"A": base})
        scores_idle = MODULE.build_agent_scores({"A": idle})
        self.assertLess(scores_busy["A"], scores_idle["A"])


class AssignIssuesTests(unittest.TestCase):
    def _scores(self):
        return {
            "Quinn (QA Engineer)": 90.0,
            "Morgan (Project Manager)": 80.0,
            "Alex (Product Owner)": 70.0,
        }

    def test_bug_label_routes_to_quinn(self):
        issues = [
            {
                "number": 1,
                "title": "App crashes on login",
                "body": "",
                "state": "open",
                "labels": [{"name": "bug"}],
            }
        ]
        recs = MODULE.assign_issues(issues, self._scores())
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["recommended_agent"], "Quinn (QA Engineer)")

    def test_feature_label_routes_to_alex(self):
        issues = [
            {
                "number": 2,
                "title": "Add dark mode feature",
                "body": "User story: As a user...",
                "state": "open",
                "labels": [{"name": "feature"}],
            }
        ]
        recs = MODULE.assign_issues(issues, self._scores())
        self.assertEqual(recs[0]["recommended_agent"], "Alex (Product Owner)")

    def test_closed_issues_skipped(self):
        issues = [
            {
                "number": 3,
                "title": "Old bug",
                "body": "",
                "state": "closed",
                "labels": [{"name": "bug"}],
            }
        ]
        recs = MODULE.assign_issues(issues, self._scores())
        self.assertEqual(recs, [])


class RenderDashboardTests(unittest.TestCase):
    def test_dashboard_contains_headings(self):
        metrics = {
            name: {
                "active_runs": 0,
                "recent_runs": 0,
                "perf_runs": 0,
                "perf_failures": 0,
                "durations": [],
                "last_run": None,
            }
            for name in MODULE.AGENTS
        }
        scores = {name: 100.0 for name in MODULE.AGENTS}
        recommendations = []
        output = MODULE.render_dashboard(metrics, scores, recommendations, "2026-07-20", "http://example.com")
        self.assertIn("Dynamic Task Assignment Dashboard", output)
        self.assertIn("Agent Availability & Workload", output)
        self.assertIn("Task Assignment Recommendations", output)


if __name__ == "__main__":
    unittest.main()
