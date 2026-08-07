import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "heartbeat_runner.py"
SPEC = importlib.util.spec_from_file_location("heartbeat_runner", MODULE_PATH)
heartbeat_runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(heartbeat_runner)


class HeartbeatRunnerTuiTests(unittest.TestCase):
    def test_execute_plan_prioritizes_merge_actions_before_repo_dispatches(self):
        snapshot = {
            "repo": {"nameWithOwner": "acme/widgets", "defaultBranch": "main"},
            "prs": [
                {
                    "number": 42,
                    "title": "Merge me",
                    "isDraft": False,
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN",
                    "reviewDecision": "",
                    "statusCheckRollup": [],
                    "headRefName": "feature/merge-me",
                }
            ],
            "issues": [],
            "runs": [],
        }
        plan = {
            "pull_requests": [{"number": 42, "action": "merge", "reason": "Ready to merge."}],
            "repo_actions": [{"action": "dispatch_task_assignment", "workflow": "task-assignment.yml", "inputs": {}}],
        }

        with patch.object(heartbeat_runner, "mergeable_guard", return_value=(True, "ready")), \
             patch.object(heartbeat_runner, "merge_pr", return_value="merged") as merge_pr_mock, \
             patch.object(heartbeat_runner, "dispatch_workflow", return_value="dispatched") as dispatch_workflow_mock:
            heartbeat_runner.execute_plan(snapshot, plan, {}, "acme/widgets", dry_run=True, max_actions=1)

        self.assertEqual(merge_pr_mock.call_count, 1)
        self.assertEqual(dispatch_workflow_mock.call_count, 0)

    def test_unstable_pr_state_triggers_copilot_handoff(self):
        snapshot = {
            "repo": {"nameWithOwner": "acme/widgets", "defaultBranch": "main"},
            "prs": [
                {
                    "number": 77,
                    "title": "Needs follow-up",
                    "isDraft": False,
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "UNSTABLE",
                    "reviewDecision": "",
                    "statusCheckRollup": [],
                    "headRefName": "feature/unstable",
                }
            ],
            "issues": [],
            "runs": [],
        }
        state = {}

        decisions = heartbeat_runner.heuristic_pr_decisions(snapshot, state)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["action"], "send_back_to_copilot")
        self.assertIn("unstable", decisions[0]["reason"].lower())

    def test_render_tui_lines_includes_pr_reasons(self):
        heartbeat_data = {
            "snapshot": {
                "repo": {"nameWithOwner": "acme/widgets"},
                "prs": [
                    {
                        "number": 12,
                        "title": "Example PR",
                        "isDraft": False,
                        "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN",
                        "reviewDecision": "REVIEW_REQUIRED",
                        "statusCheckRollup": [],
                    }
                ],
                "issues": [],
                "runs": [],
            },
            "plan": {
                "pull_requests": [
                    {
                        "number": 12,
                        "action": "run_qa",
                        "reason": "The PR still needs review before it can merge.",
                    }
                ],
                "repo_actions": [],
            },
            "results": [],
            "meta": {},
        }

        lines = heartbeat_runner.render_tui_lines(heartbeat_data, 5, False, False, 60, "")
        rendered = "\n".join(lines)

        self.assertIn("The PR still needs review before it can merge.", rendered)

    def test_heuristic_repo_actions_plan_discussion_participation(self):
        snapshot = {
            "repo": {"nameWithOwner": "acme/widgets", "defaultBranch": "main"},
            "prs": [],
            "issues": [],
            "runs": [],
            "discussions": [
                {
                    "number": 985,
                    "title": "Need team planning",
                    "body": "We should split this work into implementation tasks.",
                    "updatedAt": "2026-07-19T19:00:00Z",
                    "comments": {"totalCount": 0},
                }
            ],
        }

        state = {}
        actions = heartbeat_runner.heuristic_repo_actions(snapshot, state)

        self.assertTrue(any(action.get("action") == "participate_in_discussion" for action in actions))

    def test_execute_plan_handles_discussion_participation(self):
        snapshot = {
            "repo": {"nameWithOwner": "acme/widgets", "defaultBranch": "main"},
            "prs": [],
            "issues": [],
            "runs": [],
            "discussions": [
                {
                    "number": 985,
                    "title": "Need team planning",
                    "body": "We should split this work into implementation tasks.",
                    "updatedAt": "2026-07-19T19:00:00Z",
                    "comments": {"totalCount": 0},
                }
            ],
        }
        plan = {
            "pull_requests": [],
            "repo_actions": [
                {
                    "action": "participate_in_discussion",
                    "reason": "Discussion needs a heartbeat response.",
                    "discussion_number": 985,
                }
            ],
        }

        with patch.object(heartbeat_runner, "participate_in_discussion", return_value="posted") as participate_mock:
            heartbeat_runner.execute_plan(snapshot, plan, {}, "acme/widgets", dry_run=True, max_actions=1)

        self.assertEqual(participate_mock.call_count, 1)

    def test_sanitize_model_plan_allows_discussion_participation(self):
        heuristic = {
            "pull_requests": [],
            "repo_actions": [
                {
                    "action": "participate_in_discussion",
                    "reason": "Discussion needs a heartbeat response.",
                    "discussion_number": 985,
                }
            ],
        }
        raw_text = '{"pull_requests": [], "repo_actions": [{"action": "participate_in_discussion", "reason": "Discussion needs a heartbeat response."}]}'

        plan = heartbeat_runner.sanitize_model_plan(raw_text, heuristic)

        self.assertEqual(plan["repo_actions"][0]["action"], "participate_in_discussion")

    def test_normalize_repo_action_inputs_uses_workflow_dispatch_schema(self):
        normalized = heartbeat_runner.normalize_repo_action_inputs(
            "project-manager.yml",
            {"task": "groom-backlog", "extra_context": "Heartbeat backlog triage"},
        )

        self.assertEqual(normalized["task"], "groom-backlog")
        self.assertNotIn("extra_context", normalized)

    def test_build_plan_skips_copilot_after_recent_failure(self):
        snapshot = {
            "repo": {"nameWithOwner": "acme/widgets", "defaultBranch": "main"},
            "prs": [],
            "issues": [],
            "runs": [],
        }
        state = {"events": {}, "heartbeats": 0}
        heartbeat_runner.record_event(state, "planner-unavailable:copilot-cli", {"reason": "timeout"})

        with patch.object(heartbeat_runner, "call_copilot_cli_model") as copilot_mock, \
             patch.dict("os.environ", {"GITHUB_ACTIONS": "", "HEARTBEAT_USE_GITHUB_MODELS": "false"}):
            plan, meta = heartbeat_runner.build_plan(
                snapshot, state, Path("."), "gpt-4o-mini", None, 1
            )

        self.assertEqual(copilot_mock.call_count, 0)
        self.assertIn("cooldown", meta["models_status"])

    def test_build_plan_skips_copilot_by_cadence_with_clear_status(self):
        snapshot = {
            "repo": {"nameWithOwner": "acme/widgets", "defaultBranch": "main"},
            "prs": [],
            "issues": [],
            "runs": [],
        }
        state = {"events": {}, "heartbeats": 0}

        plan, meta = heartbeat_runner.build_plan(
            snapshot, state, Path("."), "gpt-4o-mini", None, 3
        )

        self.assertEqual(plan.get("pull_requests"), [])
        self.assertIn("cadence", meta["models_status"])
        self.assertIn("heuristic", meta["models_status"])

    def test_build_plan_records_copilot_failure_for_cooldown(self):
        snapshot = {
            "repo": {"nameWithOwner": "acme/widgets", "defaultBranch": "main"},
            "prs": [],
            "issues": [],
            "runs": [],
        }
        state = {"events": {}, "heartbeats": 0}

        with patch.object(heartbeat_runner, "call_copilot_cli_model", return_value=(False, "gh copilot timed out after 45s")), \
             patch.dict("os.environ", {"GITHUB_ACTIONS": "", "HEARTBEAT_USE_GITHUB_MODELS": "false"}):
            plan, meta = heartbeat_runner.build_plan(
                snapshot, state, Path("."), "gpt-4o-mini", None, 1
            )

        self.assertIn("planner-unavailable:copilot-cli", state.get("events", {}))


if __name__ == "__main__":
    unittest.main()
