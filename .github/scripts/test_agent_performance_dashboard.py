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


if __name__ == "__main__":
    unittest.main()
