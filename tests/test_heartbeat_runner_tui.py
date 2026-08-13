import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "heartbeat_runner.py"
SPEC = importlib.util.spec_from_file_location("heartbeat_runner", MODULE_PATH)
heartbeat_runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(heartbeat_runner)


class HeartbeatRunnerTuiTests(unittest.TestCase):
    def test_tui_header_reports_active_beat_instead_of_zero_countdown(self):
        self.assertEqual(
            heartbeat_runner.tui_header_status(
                paused=False,
                next_run_in=0,
                live_active=True,
                heartbeat_count=0,
            ),
            "RUNNING beat #1",
        )
        self.assertEqual(
            heartbeat_runner.tui_header_status(
                paused=False,
                next_run_in=18,
                live_active=False,
                heartbeat_count=3,
            ),
            "AUTO next in 18s  beat #3",
        )

    def test_copilot_planner_status_surfaces_timeout(self):
        self.assertEqual(
            heartbeat_runner.copilot_planner_status("gpt-4o-mini", 45),
            "consulting Copilot planner (gpt-4o-mini; timeout 45s)",
        )

    def test_parse_args_accepts_explicit_tui_auto_startup_grant(self):
        with patch.object(sys, "argv", ["heartbeat_runner.py", "--tui", "--tui-auto"]):
            args = heartbeat_runner.parse_args()

        self.assertTrue(args.tui)
        self.assertTrue(args.tui_auto)

    def test_enabling_automatic_mode_makes_first_run_due_immediately(self):
        self.assertEqual(heartbeat_runner.tui_automatic_next_run(True, now=120.0, interval=300), 120.0)
        self.assertEqual(heartbeat_runner.tui_automatic_next_run(False, now=120.0, interval=300), 420.0)

    def test_load_tui_preview_reads_state_without_executing_actions(self):
        repo_info = {"nameWithOwner": "acme/widgets", "defaultBranch": "main"}
        args = SimpleNamespace(pr_limit=30, issue_limit=50, run_limit=100)
        prs = [{"number": 42, "isDraft": False, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "", "statusCheckRollup": []}]
        issues = [{"number": 9, "assignees": []}]
        runs = []
        progress_messages = []

        with patch.object(heartbeat_runner, "fetch_open_prs", return_value=prs), \
             patch.object(heartbeat_runner, "fetch_open_issues", return_value=issues), \
             patch.object(heartbeat_runner, "fetch_runs", return_value=runs), \
             patch.object(heartbeat_runner, "execute_plan") as execute_plan_mock, \
             patch.object(heartbeat_runner, "approve_pending_workflow_runs") as approve_mock:
            preview = heartbeat_runner.load_tui_preview(
                repo_info,
                args,
                {},
                progress=progress_messages.append,
            )

        self.assertEqual(preview["snapshot"]["prs"], prs)
        self.assertEqual(preview["snapshot"]["issues"], issues)
        self.assertEqual(preview["results"], [])
        self.assertEqual(preview["meta"]["decision_source"], "heuristic preview")
        self.assertEqual(preview["plan"]["pull_requests"][0]["action"], "merge")
        self.assertIn("Repository state ready", progress_messages)
        execute_plan_mock.assert_not_called()
        approve_mock.assert_not_called()

    def test_tui_key_normalizer_accepts_characters_and_special_keys(self):
        self.assertEqual(heartbeat_runner.normalize_tui_key("r"), ord("r"))
        self.assertEqual(heartbeat_runner.normalize_tui_key("/"), ord("/"))
        self.assertEqual(heartbeat_runner.normalize_tui_key("ENTER"), 10)
        self.assertEqual(heartbeat_runner.normalize_tui_key(ord("r")), ord("r"))

    def test_tui_mutating_actions_require_explicit_confirmation(self):
        for key, expected_action in {
            ord("r"): "heartbeat",
            ord("c"): "council",
            ord("m"): "project_manager",
            ord("a"): "copilot_assignment",
            ord("d"): "advance_drafts",
        }.items():
            action = heartbeat_runner.tui_action_for_key(key)
            state, confirmed_action = heartbeat_runner.resolve_tui_confirmation(action, ord("y"))

            self.assertEqual(action, expected_action)
            self.assertEqual(state, "confirmed")
            self.assertEqual(confirmed_action, expected_action)

    def test_tui_confirmation_can_be_cancelled(self):
        state, confirmed_action = heartbeat_runner.resolve_tui_confirmation("heartbeat", ord("n"))

        self.assertEqual(state, "cancelled")
        self.assertIsNone(confirmed_action)

    def test_tui_heartbeat_runs_only_after_confirmation_and_outside_chat(self):
        self.assertFalse(heartbeat_runner.should_run_tui_heartbeat(False, False))
        self.assertFalse(heartbeat_runner.should_run_tui_heartbeat(True, True))
        self.assertTrue(heartbeat_runner.should_run_tui_heartbeat(False, True))

    def test_tui_automatic_path_is_opt_in_and_respects_interaction_gates(self):
        self.assertEqual(heartbeat_runner.tui_automatic_action(False), "enable_automatic")
        self.assertEqual(heartbeat_runner.tui_automatic_action(True), "disable_automatic")
        self.assertFalse(
            heartbeat_runner.should_run_tui_heartbeat(
                chat_open=False,
                run_requested=False,
                automatic_enabled=False,
                automatic_due=True,
            )
        )
        self.assertTrue(
            heartbeat_runner.should_run_tui_heartbeat(
                chat_open=False,
                run_requested=False,
                automatic_enabled=True,
                automatic_due=True,
            )
        )
        self.assertFalse(
            heartbeat_runner.should_run_tui_heartbeat(
                chat_open=False,
                run_requested=False,
                automatic_enabled=True,
                automatic_due=True,
                confirmation_pending=True,
            )
        )
        self.assertFalse(
            heartbeat_runner.should_run_tui_heartbeat(
                chat_open=True,
                run_requested=False,
                automatic_enabled=True,
                automatic_due=True,
            )
        )

    def test_ready_guard_only_allows_safe_draft_promotion(self):
        safe_draft = {
            "isDraft": True,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "",
            "statusCheckRollup": [],
        }

        self.assertEqual(heartbeat_runner.ready_guard(safe_draft), (True, "ready"))
        self.assertEqual(heartbeat_runner.ready_guard({**safe_draft, "isDraft": False}), (False, "not a draft"))
        self.assertEqual(
            heartbeat_runner.ready_guard({**safe_draft, "mergeable": "CONFLICTING"}),
            (False, "mergeable=CONFLICTING"),
        )

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

    def test_render_tui_lines_explains_user_actor_policy_gate_model(self):
        heartbeat_data = {
            "snapshot": {
                "repo": {"nameWithOwner": "acme/widgets"},
                "prs": [],
                "issues": [],
                "runs": [],
            },
            "plan": {"pull_requests": [], "repo_actions": []},
            "results": [],
            "meta": {},
        }

        lines = heartbeat_runner.render_tui_lines(heartbeat_data, 5, False, False, 60, "Ready")
        rendered = "\n".join(lines)

        self.assertIn("User acts through the TUI", rendered)
        self.assertIn("policy gate", rendered.lower())

    def test_render_tui_lines_surfaces_manual_and_automatic_state(self):
        manual = heartbeat_runner.render_tui_lines(None, 300, False, True, 300, "Ready")
        automatic = heartbeat_runner.render_tui_lines(None, 300, False, False, 42, "Ready")

        self.assertIn("State: MANUAL (automatic runs disabled)", manual)
        self.assertIn("Automatic run in: 42s", automatic)
        self.assertTrue(any("p automatic" in line for line in automatic))

    def test_preview_actions_are_not_labeled_as_scheduled(self):
        self.assertEqual(
            heartbeat_runner.tui_repo_actions_heading({"decision_source": "heuristic preview"}),
            "Proposed actions (not executed):",
        )
        self.assertEqual(
            heartbeat_runner.tui_repo_actions_heading({"decision_source": "copilot-cli:gpt-5-mini"}),
            "Scheduled dispatches:",
        )
        self.assertEqual(
            heartbeat_runner.tui_repo_actions_heading({"decision_source": "heuristic"}, dry_run=True),
            "Simulated actions (not executed):",
        )

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
