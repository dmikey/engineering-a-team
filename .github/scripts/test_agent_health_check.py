import importlib.util
import json
import pathlib
import unittest
from datetime import datetime, timedelta, timezone


MODULE_PATH = pathlib.Path(__file__).with_name("agent_health_check.py")
SPEC = importlib.util.spec_from_file_location("agent_health_check", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _make_run(path, created_offset_h, conclusion="success", duration_min=5):
    """Helper: build a minimal workflow run dict."""
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    created = now - timedelta(hours=created_offset_h)
    updated = created + timedelta(minutes=duration_min)
    return {
        "path": path,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "updated_at": updated.isoformat().replace("+00:00", "Z"),
        "status": "completed",
        "conclusion": conclusion,
    }


SINCE = datetime(2026, 7, 31, 0, 0, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
WARN = MODULE.DEFAULT_WARN_THRESHOLD
CRIT = MODULE.DEFAULT_CRIT_THRESHOLD
INACTIVITY = MODULE.DEFAULT_INACTIVITY_HOURS


class TestCollectMetrics(unittest.TestCase):
    def test_filters_by_period(self):
        """Runs before `since` should be excluded."""
        runs = [
            _make_run(".github/workflows/qa-engineer.yml", created_offset_h=6),   # within window
            _make_run(".github/workflows/qa-engineer.yml", created_offset_h=25),  # outside window
        ]
        metrics = MODULE.collect_metrics(runs, SINCE)
        self.assertEqual(metrics["Quinn (QA Engineer)"]["runs"], 1)

    def test_filters_unknown_workflow(self):
        """Runs for unregistered workflow paths should be ignored."""
        runs = [
            _make_run(".github/workflows/unknown-agent.yml", created_offset_h=2),
            _make_run(".github/workflows/qa-engineer.yml", created_offset_h=2),
        ]
        metrics = MODULE.collect_metrics(runs, SINCE)
        self.assertEqual(metrics["Quinn (QA Engineer)"]["runs"], 1)

    def test_counts_failures(self):
        """Failed conclusions should be counted separately from successes."""
        runs = [
            _make_run(".github/workflows/project-manager.yml", created_offset_h=4, conclusion="success"),
            _make_run(".github/workflows/project-manager.yml", created_offset_h=3, conclusion="failure"),
            _make_run(".github/workflows/project-manager.yml", created_offset_h=2, conclusion="failure"),
        ]
        metrics = MODULE.collect_metrics(runs, SINCE)
        data = metrics["Morgan (Project Manager)"]
        self.assertEqual(data["runs"], 3)
        self.assertEqual(data["failures"], 2)

    def test_records_durations(self):
        """Duration in minutes should be calculated for completed runs."""
        runs = [
            _make_run(".github/workflows/product-owner.yml", created_offset_h=5, duration_min=10),
            _make_run(".github/workflows/product-owner.yml", created_offset_h=3, duration_min=20),
        ]
        metrics = MODULE.collect_metrics(runs, SINCE)
        durations = metrics["Alex (Product Owner)"]["durations"]
        self.assertEqual(len(durations), 2)
        self.assertAlmostEqual(durations[0], 10.0, places=1)
        self.assertAlmostEqual(durations[1], 20.0, places=1)

    def test_tracks_last_run(self):
        """last_run should be the most recent run timestamp."""
        runs = [
            _make_run(".github/workflows/qa-engineer.yml", created_offset_h=8),
            _make_run(".github/workflows/qa-engineer.yml", created_offset_h=2),
            _make_run(".github/workflows/qa-engineer.yml", created_offset_h=5),
        ]
        metrics = MODULE.collect_metrics(runs, SINCE)
        last = metrics["Quinn (QA Engineer)"]["last_run"]
        expected = NOW - timedelta(hours=2)
        self.assertEqual(last, expected)

    def test_last_run_kept_even_if_outside_since_window(self):
        """A run outside the period should still populate last_run for inactivity checks."""
        runs = [
            _make_run(".github/workflows/qa-engineer.yml", created_offset_h=30),
        ]
        metrics = MODULE.collect_metrics(runs, SINCE)
        data = metrics["Quinn (QA Engineer)"]
        self.assertEqual(data["runs"], 0)
        self.assertIsNotNone(data["last_run"])


class TestClassifyStatus(unittest.TestCase):
    def _classify(self, runs=1, failures=0, hours_since_last=1):
        last_run = NOW - timedelta(hours=hours_since_last)
        data = {
            "runs": runs,
            "failures": failures,
            "durations": [5.0] * (runs - failures),
            "last_run": last_run if runs > 0 else None,
            "last_conclusion": "success",
        }
        return MODULE.classify_status(data, NOW, WARN, CRIT, INACTIVITY)

    def test_healthy(self):
        self.assertEqual(self._classify(runs=10, failures=0), MODULE.STATUS_HEALTHY)

    def test_degraded(self):
        # 80% success rate → below WARN (85) but above CRIT (70)
        self.assertEqual(self._classify(runs=10, failures=2), MODULE.STATUS_DEGRADED)

    def test_critical(self):
        # 60% success rate → below CRIT (70)
        self.assertEqual(self._classify(runs=10, failures=4), MODULE.STATUS_CRITICAL)

    def test_inactive_no_runs(self):
        data = {
            "runs": 0,
            "failures": 0,
            "durations": [],
            "last_run": None,
            "last_conclusion": None,
        }
        status = MODULE.classify_status(data, NOW, WARN, CRIT, INACTIVITY)
        self.assertEqual(status, MODULE.STATUS_INACTIVE)

    def test_inactive_stale_run(self):
        # Last run was 50 hours ago — beyond INACTIVITY_HOURS (48)
        self.assertEqual(
            self._classify(runs=5, failures=0, hours_since_last=50),
            MODULE.STATUS_INACTIVE,
        )

    def test_healthy_recent_run(self):
        # Last run was 1 hour ago
        self.assertEqual(
            self._classify(runs=5, failures=0, hours_since_last=1),
            MODULE.STATUS_HEALTHY,
        )

    def test_healthy_when_no_runs_in_window_but_recent_last_run(self):
        data = {
            "runs": 0,
            "failures": 0,
            "durations": [],
            "last_run": NOW - timedelta(hours=10),
            "last_conclusion": "success",
        }
        status = MODULE.classify_status(data, NOW, WARN, CRIT, INACTIVITY)
        self.assertEqual(status, MODULE.STATUS_HEALTHY)


class TestBuildRows(unittest.TestCase):
    def _build(self, agent_runs):
        """agent_runs: dict of agent name → list of (conclusion, created_offset_h)"""
        all_runs = []
        workflows = MODULE.AGENT_WORKFLOWS
        for agent, run_specs in agent_runs.items():
            path = workflows[agent]
            for conclusion, offset in run_specs:
                all_runs.append(_make_run(path, created_offset_h=offset, conclusion=conclusion))
        metrics = MODULE.collect_metrics(all_runs, SINCE)
        return MODULE.build_rows(metrics, NOW, WARN, CRIT, INACTIVITY)

    def test_unhealthy_agents_sorted_first(self):
        rows = self._build(
            {
                "Quinn (QA Engineer)": [("success", 2), ("success", 4)],
                "Morgan (Project Manager)": [("failure", 2), ("failure", 3)],
            }
        )
        statuses = [r["status"] for r in rows]
        # Critical/inactive agents should appear before healthy ones
        first_non_healthy = next(
            (i for i, s in enumerate(statuses) if s == MODULE.STATUS_HEALTHY), len(statuses)
        )
        for i, status in enumerate(statuses[:first_non_healthy]):
            self.assertNotEqual(status, MODULE.STATUS_HEALTHY)

    def test_row_fields_present(self):
        rows = self._build({"Quinn (QA Engineer)": [("success", 3)]})
        quinn = next(r for r in rows if r["agent"] == "Quinn (QA Engineer)")
        self.assertIn("agent", quinn)
        self.assertIn("runs", quinn)
        self.assertIn("failures", quinn)
        self.assertIn("success_rate", quinn)
        self.assertIn("avg_duration", quinn)
        self.assertIn("last_run", quinn)
        self.assertIn("status", quinn)


class TestRenderMarkdown(unittest.TestCase):
    def _rows(self, status=MODULE.STATUS_HEALTHY):
        return [
            {
                "agent": "Quinn (QA Engineer)",
                "runs": 5,
                "failures": 0,
                "success_rate": 100.0,
                "avg_duration": 4.5,
                "last_run": "2026-07-31 10:00 UTC",
                "last_run_dt": NOW - timedelta(hours=2),
                "hours_since_last_run": 2.0,
                "status": status,
                "last_conclusion": "success",
            }
        ]

    def test_contains_title(self):
        md = MODULE.render_markdown(
            self._rows(), "2026-07-31", 24, "https://example.com", WARN, CRIT
        )
        self.assertIn("Agent Health Check", md)

    def test_healthy_overall_label(self):
        md = MODULE.render_markdown(
            self._rows(MODULE.STATUS_HEALTHY), "2026-07-31", 24, "", WARN, CRIT
        )
        self.assertIn("ALL SYSTEMS HEALTHY", md)

    def test_critical_overall_label(self):
        md = MODULE.render_markdown(
            self._rows(MODULE.STATUS_CRITICAL), "2026-07-31", 24, "", WARN, CRIT
        )
        self.assertIn("ATTENTION REQUIRED", md)

    def test_degraded_overall_label(self):
        md = MODULE.render_markdown(
            self._rows(MODULE.STATUS_DEGRADED), "2026-07-31", 24, "", WARN, CRIT
        )
        self.assertIn("DEGRADED", md)


class TestRenderAlertsJson(unittest.TestCase):
    def _row(self, status, success_rate=50.0, runs=10, failures=5):
        return {
            "agent": "Quinn (QA Engineer)",
            "status": status,
            "success_rate": success_rate,
            "runs": runs,
            "failures": failures,
            "last_run": "2026-07-31 10:00 UTC",
            "hours_since_last_run": 2.0,
        }

    def test_healthy_excluded(self):
        rows = [self._row(MODULE.STATUS_HEALTHY, success_rate=100.0, failures=0)]
        result = json.loads(MODULE.render_alerts_json(rows, "2026-07-31", 24, INACTIVITY, WARN, CRIT))
        self.assertEqual(result, [])

    def test_critical_alert_included(self):
        rows = [self._row(MODULE.STATUS_CRITICAL, success_rate=60.0)]
        result = json.loads(MODULE.render_alerts_json(rows, "2026-07-31", 24, INACTIVITY, WARN, CRIT))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], MODULE.STATUS_CRITICAL)
        self.assertEqual(result[0]["severity"], "critical")

    def test_degraded_alert_included(self):
        rows = [self._row(MODULE.STATUS_DEGRADED, success_rate=80.0, failures=2)]
        result = json.loads(MODULE.render_alerts_json(rows, "2026-07-31", 24, INACTIVITY, WARN, CRIT))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["severity"], "medium")

    def test_inactive_alert_included(self):
        rows = [self._row(MODULE.STATUS_INACTIVE, success_rate=0.0, runs=0, failures=0)]
        result = json.loads(MODULE.render_alerts_json(rows, "2026-07-31", 24, INACTIVITY, WARN, CRIT))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["severity"], "high")

    def test_alert_has_required_fields(self):
        rows = [self._row(MODULE.STATUS_CRITICAL)]
        result = json.loads(MODULE.render_alerts_json(rows, "2026-07-31", 24, INACTIVITY, WARN, CRIT))
        alert = result[0]
        for field in ("agent", "status", "severity", "title", "description", "date"):
            self.assertIn(field, alert)


class TestRenderStatusJson(unittest.TestCase):
    def test_returns_agent_status_map(self):
        rows = [
            {
                "agent": "Quinn (QA Engineer)",
                "status": MODULE.STATUS_HEALTHY,
            },
            {
                "agent": "Morgan (Project Manager)",
                "status": MODULE.STATUS_CRITICAL,
            },
        ]
        result = json.loads(MODULE.render_status_json(rows))
        self.assertEqual(result["Quinn (QA Engineer)"], MODULE.STATUS_HEALTHY)
        self.assertEqual(result["Morgan (Project Manager)"], MODULE.STATUS_CRITICAL)


if __name__ == "__main__":
    unittest.main()
