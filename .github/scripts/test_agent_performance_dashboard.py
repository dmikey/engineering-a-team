import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone


MODULE_PATH = pathlib.Path(__file__).with_name("agent_performance_dashboard.py")
SPEC = importlib.util.spec_from_file_location("agent_performance_dashboard", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AgentPerformanceDashboardTests(unittest.TestCase):
    def test_collect_metrics_filters_period_and_path(self):
        since = datetime(2026, 7, 10, tzinfo=timezone.utc)
        runs = [
            {
                "path": ".github/workflows/project-manager.yml",
                "created_at": "2026-07-11T10:00:00Z",
                "updated_at": "2026-07-11T10:05:00Z",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "path": ".github/workflows/project-manager.yml",
                "created_at": "2026-07-11T11:00:00Z",
                "updated_at": "2026-07-11T11:03:00Z",
                "status": "completed",
                "conclusion": "failure",
            },
            {
                "path": ".github/workflows/project-manager.yml",
                "created_at": "2026-07-01T11:00:00Z",
                "updated_at": "2026-07-01T11:03:00Z",
                "status": "completed",
                "conclusion": "failure",
            },
            {
                "path": ".github/workflows/unknown.yml",
                "created_at": "2026-07-11T11:00:00Z",
                "updated_at": "2026-07-11T11:02:00Z",
                "status": "completed",
                "conclusion": "success",
            },
        ]

        metrics = MODULE.collect_metrics(runs, since)
        morgan = metrics["Morgan (Project Manager)"]
        self.assertEqual(morgan["runs"], 2)
        self.assertEqual(morgan["failures"], 1)
        self.assertEqual(len(morgan["durations"]), 2)

    def test_sort_rows_by_last_run(self):
        rows = [
            {"agent": "A", "last_run_sort": datetime(2026, 7, 11, tzinfo=timezone.utc)},
            {"agent": "B", "last_run_sort": datetime(2026, 7, 12, tzinfo=timezone.utc)},
        ]
        sorted_rows = MODULE.sort_rows(rows, "last-run")
        self.assertEqual([r["agent"] for r in sorted_rows], ["B", "A"])

    def test_filter_rows_by_partial_agent_name(self):
        rows = [
            {"agent": "Quinn (QA Engineer)"},
            {"agent": "Morgan (Project Manager)"},
            {"agent": "Alex (Product Owner)"},
        ]
        result = MODULE.filter_rows(rows, "quinn")
        self.assertEqual([r["agent"] for r in result], ["Quinn (QA Engineer)"])

    def test_filter_rows_case_insensitive(self):
        rows = [
            {"agent": "Quinn (QA Engineer)"},
            {"agent": "Morgan (Project Manager)"},
        ]
        result = MODULE.filter_rows(rows, "MORGAN")
        self.assertEqual([r["agent"] for r in result], ["Morgan (Project Manager)"])

    def test_filter_rows_all_returns_all(self):
        rows = [
            {"agent": "Quinn (QA Engineer)"},
            {"agent": "Morgan (Project Manager)"},
        ]
        self.assertEqual(MODULE.filter_rows(rows, "all"), rows)
        self.assertEqual(MODULE.filter_rows(rows, ""), rows)
        self.assertEqual(MODULE.filter_rows(rows, None), rows)

    def test_filter_rows_no_match_returns_empty(self):
        rows = [{"agent": "Quinn (QA Engineer)"}]
        result = MODULE.filter_rows(rows, "nonexistent")
        self.assertEqual(result, [])

    def test_render_markdown_includes_filter_agent_line(self):
        rows = [
            {
                "agent": "Quinn (QA Engineer)",
                "runs": 5,
                "success_rate": 80.0,
                "failures": 1,
                "avg_duration": 2.5,
                "last_run": "2026-07-30",
            }
        ]
        md = MODULE.render_markdown(rows, "2026-07-31", 30, "success-rate", "http://example.com", "Quinn (QA Engineer)")
        self.assertIn("**Filtered Agent**: `Quinn (QA Engineer)`", md)
        self.assertIn("Quinn (QA Engineer)", md)

    def test_render_markdown_no_filter_line_when_all(self):
        rows = [
            {
                "agent": "Quinn (QA Engineer)",
                "runs": 3,
                "success_rate": 100.0,
                "failures": 0,
                "avg_duration": 1.0,
                "last_run": "2026-07-30",
            }
        ]
        md = MODULE.render_markdown(rows, "2026-07-31", 30, "success-rate", "", "all")
        self.assertNotIn("**Filtered Agent**", md)

    def test_render_markdown_empty_rows_shows_no_data_message(self):
        md = MODULE.render_markdown([], "2026-07-31", 30, "success-rate", "", "nobody")
        self.assertIn("No workflow runs found", md)
        self.assertNotIn("| Agent |", md)


if __name__ == "__main__":
    unittest.main()
