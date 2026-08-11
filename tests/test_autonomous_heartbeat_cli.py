import importlib.util
import os
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "autonomous-heartbeat.sh"
MODULE_PATH = REPO_ROOT / "scripts" / "heartbeat_runner.py"
SPEC = importlib.util.spec_from_file_location("heartbeat_runner", MODULE_PATH)
heartbeat_runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(heartbeat_runner)


class AutonomousHeartbeatCliTests(unittest.TestCase):
    def test_once_invokes_python_heartbeat_runner(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            log_path = tmp_path / "invocations.log"

            (bin_dir / "python3").write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$TEST_LOG\"\n"
                "exit 0\n",
                encoding="utf-8",
            )
            (bin_dir / "gh").write_text(
                "#!/usr/bin/env bash\n"
                "exit 0\n",
                encoding="utf-8",
            )
            (bin_dir / "python3").chmod(0o755)
            (bin_dir / "gh").chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["TEST_LOG"] = str(log_path)

            result = subprocess.run(
                ["bash", str(SCRIPT), "once", "--interval", "30"],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(log_path.exists(), msg="expected python invocation log")
            logged = log_path.read_text(encoding="utf-8")
            self.assertIn("scripts/heartbeat_runner.py", logged)
            self.assertIn("--once", logged)
            self.assertIn("--dry-run", logged)

    def test_state_paths_use_repo_root_when_cwd_is_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_cwd = Path.cwd()
            try:
                os.chdir(tmp_dir)
                state_file, overview_file = heartbeat_runner.state_paths()
            finally:
                os.chdir(original_cwd)

            self.assertTrue(str(state_file.resolve()).startswith(str(REPO_ROOT.resolve())))
            self.assertTrue(str(overview_file.resolve()).startswith(str(REPO_ROOT.resolve())))

    def test_resolve_gh_command_env_falls_back_when_explicit_token_is_invalid(self):
        invalid = subprocess.CompletedProcess(["gh", "api", "user"], 1, "", "HTTP 401: Bad credentials")
        with mock.patch.dict(os.environ, {"GH_USER_PAT": "bad-token"}, clear=False):
            with mock.patch.object(heartbeat_runner.subprocess, "run", return_value=invalid):
                env, source = heartbeat_runner.resolve_gh_command_env()

        self.assertIsNotNone(env)
        assert env is not None
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(source, "gh-auth")

    def test_resolve_gh_command_env_uses_explicit_token_when_valid(self):
        valid = subprocess.CompletedProcess(["gh", "api", "user"], 0, '{"login":"octocat"}', "")
        with mock.patch.dict(os.environ, {"GH_USER_PAT": "good-token"}, clear=False):
            with mock.patch.object(heartbeat_runner.subprocess, "run", return_value=valid):
                env, source = heartbeat_runner.resolve_gh_command_env()

        self.assertIsNotNone(env)
        assert env is not None
        self.assertEqual(env.get("GH_TOKEN"), "good-token")
        self.assertEqual(source, "GH_USER_PAT")

    def test_resolve_gh_command_env_gh_auth_strips_token_overrides(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "integration-token", "GH_TOKEN": "other-token"}, clear=False):
            env, source = heartbeat_runner.resolve_gh_command_env()

        self.assertIsNotNone(env)
        assert env is not None
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(source, "gh-auth")

    def test_auth_block_cooldown_remaining_bypasses_legacy_event_on_gh_auth(self):
        state = {
            "events": {
                "dispatch-blocked:task-assignment.yml": {
                    "at": heartbeat_runner.isoformat(),
                    "payload": {"reason": "legacy blocked event"},
                }
            }
        }
        with mock.patch.object(heartbeat_runner, "GH_AUTH_SOURCE", "gh-auth"):
            remaining = heartbeat_runner.auth_block_cooldown_remaining(
                state,
                "dispatch-blocked:task-assignment.yml",
                heartbeat_runner.AUTH_FAILURE_COOLDOWN,
            )
        self.assertIsNone(remaining)

    def test_auth_block_cooldown_remaining_enforced_same_source(self):
        state = {
            "events": {
                "dispatch-blocked:task-assignment.yml": {
                    "at": heartbeat_runner.isoformat(),
                    "payload": {"auth_source": "gh-auth", "reason": "blocked"},
                }
            }
        }
        with mock.patch.object(heartbeat_runner, "GH_AUTH_SOURCE", "gh-auth"):
            remaining = heartbeat_runner.auth_block_cooldown_remaining(
                state,
                "dispatch-blocked:task-assignment.yml",
                heartbeat_runner.AUTH_FAILURE_COOLDOWN,
            )
        self.assertIsNotNone(remaining)

    def test_normalize_copilot_chat_model_maps_legacy_model_names(self):
        self.assertEqual(
            heartbeat_runner.normalize_copilot_chat_model("gpt-4o-mini"),
            "gpt-5-mini",
        )
        self.assertEqual(
            heartbeat_runner.normalize_copilot_chat_model("gpt-4o"),
            "gpt-5.4",
        )

    def test_normalize_copilot_chat_model_falls_back_for_unknown_models(self):
        self.assertEqual(
            heartbeat_runner.normalize_copilot_chat_model("not-a-real-model", fallback="gpt-5.4"),
            "gpt-5.4",
        )

    def test_heuristic_repo_actions_dispatches_pm_groom_backlog_for_priority_issues(self):
        snapshot = {
            "issues": [
                {
                    "number": 1,
                    "labels": [{"name": "priority: high"}],
                    "assignees": [],
                }
            ],
            "runs": [],
        }
        actions = heartbeat_runner.heuristic_repo_actions(snapshot, {"events": {}, "heartbeats": 0})
        pm_action = next(action for action in actions if action.get("action") == "dispatch_project_manager")
        self.assertEqual(pm_action["workflow"], "project-manager.yml")
        self.assertEqual(pm_action["inputs"]["task"], "groom-backlog")

    def test_heuristic_repo_actions_uses_backlog_pressure_cooldown_for_task_assignment(self):
        snapshot = {
            "issues": [
                {
                    "number": idx,
                    "labels": [],
                    "assignees": [],
                }
                for idx in range(1, 40)
            ],
            "runs": [],
        }
        state = {
            "events": {
                "dispatch:task-assignment.yml": {
                    "at": heartbeat_runner.isoformat(heartbeat_runner.now_utc() - timedelta(hours=2)),
                    "payload": {"reason": "previous dispatch"},
                }
            },
            "heartbeats": 1,
        }

        actions = heartbeat_runner.heuristic_repo_actions(snapshot, state)
        action_names = [action.get("action") for action in actions]
        self.assertIn("dispatch_task_assignment", action_names)

    def test_select_top_unassigned_issue_prefers_highest_priority_label(self):
        issues = [
            {
                "number": 10,
                "labels": [{"name": "priority: medium"}],
                "assignees": [],
                "createdAt": "2026-08-11T04:00:00Z",
            },
            {
                "number": 11,
                "labels": [{"name": "priority: critical"}],
                "assignees": [],
                "createdAt": "2026-08-11T04:10:00Z",
            },
            {
                "number": 12,
                "labels": [{"name": "priority: high"}],
                "assignees": [{"login": "dmikey"}],
                "createdAt": "2026-08-11T03:00:00Z",
            },
        ]

        chosen = heartbeat_runner.select_top_unassigned_issue(issues)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen["number"], 11)

    def test_select_top_copilot_candidate_falls_back_to_assigned_issue(self):
        issues = [
            {
                "number": 20,
                "labels": [{"name": "priority: high"}],
                "assignees": [{"login": "dmikey"}],
                "createdAt": "2026-08-11T04:00:00Z",
            },
            {
                "number": 21,
                "labels": [{"name": "priority: critical"}],
                "assignees": [{"login": "dmikey"}],
                "createdAt": "2026-08-11T04:10:00Z",
            },
        ]

        chosen = heartbeat_runner.select_top_copilot_candidate(issues)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen["number"], 21)

    def test_issue_priority_label_defaults_to_blocked_then_unlabeled(self):
        blocked = {"labels": [{"name": "blocked"}]}
        unlabeled = {"labels": []}

        self.assertEqual(heartbeat_runner.issue_priority_label(blocked), "blocked")
        self.assertEqual(heartbeat_runner.issue_priority_label(unlabeled), "unlabeled")

    def test_describe_latest_failure_returns_none_when_no_failures(self):
        runs = [
            {
                "databaseId": 1,
                "workflowName": "CI",
                "conclusion": "success",
                "createdAt": "2026-08-11T12:00:00Z",
                "url": "https://example.com/runs/1",
            }
        ]

        summary = heartbeat_runner.describe_latest_failure("owner/repo", runs)
        self.assertIsNone(summary)

    def test_describe_latest_failure_includes_failed_job_step(self):
        runs = [
            {
                "databaseId": 99,
                "workflowName": "Skill Development Tracking",
                "conclusion": "failure",
                "createdAt": "2026-08-11T15:13:51Z",
                "url": "https://github.com/owner/repo/actions/runs/99",
            }
        ]
        detail = {
            "jobs": [
                {
                    "name": "Morgan · Skill Development Tracking",
                    "steps": [
                        {"name": "Set up job", "conclusion": "success"},
                        {"name": "Gather workflow run data", "conclusion": "failure"},
                    ],
                }
            ]
        }

        with mock.patch.object(heartbeat_runner, "gh_json", return_value=detail):
            summary = heartbeat_runner.describe_latest_failure("owner/repo", runs)

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("Gather workflow run data", summary["summary"])
        self.assertEqual(summary["run_id"], "99")
        self.assertIn("actions/runs/99", summary["url"])


if __name__ == "__main__":
    unittest.main()
