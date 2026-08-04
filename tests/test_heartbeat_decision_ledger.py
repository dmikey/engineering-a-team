import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "heartbeat_runner.py"
SPEC = importlib.util.spec_from_file_location("heartbeat_runner", MODULE_PATH)
heartbeat_runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(heartbeat_runner)


class HeartbeatDecisionLedgerTests(unittest.TestCase):
    def test_decision_ledger_events_include_reason_lookup(self):
        snapshot = {
            "repo": {"nameWithOwner": "acme/widgets"},
            "prs": [],
            "issues": [],
            "runs": [],
        }
        plan = {
            "pull_requests": [
                {"number": 12, "action": "merge", "reason": "Safe-label gate passed."},
            ],
            "repo_actions": [
                {"action": "dispatch_project_manager", "reason": "Backlog drift detected."},
            ],
        }
        results = [
            {"target": "pr#12", "action": "merge", "status": "ok", "detail": "Merged or queued PR #12"},
            {
                "target": "project-manager.yml",
                "action": "dispatch_project_manager",
                "status": "ok",
                "detail": "Dispatched project-manager.yml",
            },
        ]
        meta = {"decision_source": "heuristic", "models_status": "disabled"}

        events = heartbeat_runner.decision_ledger_events(snapshot, plan, results, meta, dry_run=False)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["target"], "pr#12")
        self.assertEqual(events[0]["target_type"], "pr")
        self.assertEqual(events[0]["reason"], "Safe-label gate passed.")
        self.assertEqual(events[1]["target_type"], "repo")
        self.assertEqual(events[1]["reason"], "Backlog drift detected.")
        self.assertEqual(events[0]["trace_id"], events[1]["trace_id"])

    def test_append_decision_ledger_writes_json_lines(self):
        events = [
            {
                "event_id": "t:1",
                "trace_id": "t",
                "timestamp": "2026-08-03T00:00:00+00:00",
                "repo": "acme/widgets",
                "decision_source": "heuristic",
                "models_status": "disabled",
                "target_type": "pr",
                "target": "pr#12",
                "action": "merge",
                "status": "ok",
                "reason": "Safe-label gate passed.",
                "detail": "Merged or queued PR #12",
                "dry_run": False,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            ledger_path = Path(tmp_dir) / "ledger.jsonl"
            heartbeat_runner.append_decision_ledger(ledger_path, events)

            self.assertTrue(ledger_path.exists())
            lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["event_id"], "t:1")
            self.assertEqual(payload["action"], "merge")


if __name__ == "__main__":
    unittest.main()
