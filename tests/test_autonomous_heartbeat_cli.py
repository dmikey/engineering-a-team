import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
