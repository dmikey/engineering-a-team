import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class AgentSkillSetConfigurationTests(unittest.TestCase):
    def test_role_skill_variables_are_wired_into_workflows(self):
        expectations = {
            ".github/workflows/qa-engineer.yml": ["QA_AGENT_SKILLS", "${{ env.QA_AGENT_SKILLS }}"],
            ".github/workflows/project-manager.yml": ["PM_AGENT_SKILLS", "${{ env.PM_AGENT_SKILLS }}"],
            ".github/workflows/product-owner.yml": ["PO_AGENT_SKILLS", "${{ env.PO_AGENT_SKILLS }}"],
            ".github/workflows/council-discussion.yml": [
                "QA_AGENT_SKILLS",
                "PM_AGENT_SKILLS",
                "PO_AGENT_SKILLS",
                "COUNCIL_AGENT_SKILLS",
                "${{ env.COUNCIL_AGENT_SKILLS }}",
            ],
        }

        for rel_path, required_tokens in expectations.items():
            content = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
            for token in required_tokens:
                self.assertIn(token, content, f"{token} missing from {rel_path}")

    def test_configuration_docs_list_role_skill_variables(self):
        docs = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "CONFIGURATION.md",
            REPO_ROOT / ".github" / "agent-config.yml",
        ]
        for doc in docs:
            content = doc.read_text(encoding="utf-8")
            for variable in (
                "QA_AGENT_SKILLS",
                "PM_AGENT_SKILLS",
                "PO_AGENT_SKILLS",
                "COUNCIL_AGENT_SKILLS",
            ):
                self.assertIn(variable, content, f"{variable} missing from {doc}")

    def test_heartbeat_workflow_stays_non_interactive(self):
        workflow = REPO_ROOT / ".github" / "workflows" / "heartbeat.yml"
        content = workflow.read_text(encoding="utf-8")

        # CI fallback workflow must be manual-only and never enter TUI/chat code paths.
        self.assertIn("workflow_dispatch", content)
        self.assertNotIn("schedule:", content)
        self.assertNotIn("push:", content)
        self.assertIn("python3 scripts/heartbeat_runner.py --once", content)
        self.assertNotIn("--tui", content)
        self.assertNotIn("run_tui", content)
        self.assertNotIn("_send_chat", content)


if __name__ == "__main__":
    unittest.main()
