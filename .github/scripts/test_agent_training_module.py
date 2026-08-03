import importlib.util
import json
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("agent_training_module.py")
SPEC = importlib.util.spec_from_file_location("agent_training_module", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _make_issue(title, labels=None, number=1):
    return {
        "number": number,
        "title": title,
        "labels": [{"name": lbl} for lbl in (labels or [])],
        "createdAt": "2026-07-31T10:00:00Z",
    }


class CollectTrainingProgressTests(unittest.TestCase):
    def test_empty_issues_returns_empty_progress(self):
        progress = MODULE.collect_training_progress([])
        for agent in MODULE.TRAINING_CURRICULUM:
            self.assertEqual(progress[agent], set())

    def test_issue_without_training_complete_label_not_counted(self):
        issue = _make_issue(
            "Training Session: Quinn (QA Engineer) \u2014 code-review-best-practices",
            labels=["training-progress"],
        )
        progress = MODULE.collect_training_progress([issue])
        self.assertEqual(progress["Quinn (QA Engineer)"], set())

    def test_completed_issue_marks_topic_done(self):
        issue = _make_issue(
            "Training Session: Quinn (QA Engineer) \u2014 code-review-best-practices",
            labels=["training-progress", "training-complete"],
        )
        progress = MODULE.collect_training_progress([issue])
        self.assertIn("code-review-best-practices", progress["Quinn (QA Engineer)"])

    def test_wrong_title_prefix_ignored(self):
        issue = _make_issue(
            "Completed: Quinn (QA Engineer) \u2014 code-review-best-practices",
            labels=["training-complete"],
        )
        progress = MODULE.collect_training_progress([issue])
        self.assertEqual(progress["Quinn (QA Engineer)"], set())

    def test_unknown_agent_ignored(self):
        issue = _make_issue(
            "Training Session: Unknown Agent \u2014 some-topic",
            labels=["training-complete"],
        )
        # Should not raise; unknown agent simply not added to progress
        progress = MODULE.collect_training_progress([issue])
        self.assertNotIn("Unknown Agent", progress)

    def test_multiple_topics_counted(self):
        issues = [
            _make_issue(
                "Training Session: Morgan (Project Manager) \u2014 backlog-prioritization",
                labels=["training-complete"],
                number=1,
            ),
            _make_issue(
                "Training Session: Morgan (Project Manager) \u2014 sprint-planning",
                labels=["training-complete"],
                number=2,
            ),
        ]
        progress = MODULE.collect_training_progress(issues)
        self.assertIn("backlog-prioritization", progress["Morgan (Project Manager)"])
        self.assertIn("sprint-planning", progress["Morgan (Project Manager)"])

    def test_title_missing_separator_ignored(self):
        issue = _make_issue(
            "Training Session: Quinn (QA Engineer) code-review-best-practices",
            labels=["training-complete"],
        )
        progress = MODULE.collect_training_progress([issue])
        self.assertEqual(progress["Quinn (QA Engineer)"], set())


class CalculateCompletionRateTests(unittest.TestCase):
    def test_zero_completed_returns_zero(self):
        rate = MODULE.calculate_completion_rate("Quinn (QA Engineer)", set())
        self.assertEqual(rate, 0.0)

    def test_all_completed_returns_hundred(self):
        topics = MODULE.TRAINING_CURRICULUM["Quinn (QA Engineer)"]
        completed = {t["id"] for t in topics}
        rate = MODULE.calculate_completion_rate("Quinn (QA Engineer)", completed)
        self.assertEqual(rate, 100.0)

    def test_partial_completion(self):
        topics = MODULE.TRAINING_CURRICULUM["Morgan (Project Manager)"]
        # Complete half the topics
        half = {t["id"] for t in topics[: len(topics) // 2]}
        rate = MODULE.calculate_completion_rate("Morgan (Project Manager)", half)
        expected = len(half) / len(topics) * 100.0
        self.assertAlmostEqual(rate, expected)

    def test_unknown_agent_returns_hundred(self):
        rate = MODULE.calculate_completion_rate("Unknown Agent", set())
        self.assertEqual(rate, 100.0)

    def test_irrelevant_topics_not_counted(self):
        # Completed topics that don't belong to the agent's curriculum
        rate = MODULE.calculate_completion_rate("Quinn (QA Engineer)", {"backlog-prioritization"})
        self.assertEqual(rate, 0.0)


class GetPendingTopicsTests(unittest.TestCase):
    def test_no_completed_returns_all_topics(self):
        pending = MODULE.get_pending_topics("Quinn (QA Engineer)", set())
        all_ids = {t["id"] for t in MODULE.TRAINING_CURRICULUM["Quinn (QA Engineer)"]}
        pending_ids = {t["id"] for t in pending}
        self.assertEqual(pending_ids, all_ids)

    def test_completed_topics_excluded(self):
        topics = MODULE.TRAINING_CURRICULUM["Quinn (QA Engineer)"]
        first_id = topics[0]["id"]
        pending = MODULE.get_pending_topics("Quinn (QA Engineer)", {first_id})
        pending_ids = {t["id"] for t in pending}
        self.assertNotIn(first_id, pending_ids)

    def test_all_completed_returns_empty(self):
        topics = MODULE.TRAINING_CURRICULUM["Morgan (Project Manager)"]
        completed = {t["id"] for t in topics}
        pending = MODULE.get_pending_topics("Morgan (Project Manager)", completed)
        self.assertEqual(pending, [])

    def test_pending_ordered_by_difficulty(self):
        # Ensure beginner topics come before advanced
        agent = "Quinn (QA Engineer)"
        pending = MODULE.get_pending_topics(agent, set())
        difficulty_order = {"beginner": 0, "intermediate": 1, "advanced": 2}
        ranks = [difficulty_order.get(t["difficulty"], 1) for t in pending]
        self.assertEqual(ranks, sorted(ranks))


class GenerateAlertsTests(unittest.TestCase):
    def _all_complete_progress(self):
        return {
            agent: {t["id"] for t in topics}
            for agent, topics in MODULE.TRAINING_CURRICULUM.items()
        }

    def test_fully_trained_agents_produce_no_alerts(self):
        progress = self._all_complete_progress()
        alerts = MODULE.generate_alerts(progress)
        self.assertEqual(alerts, [])

    def test_zero_completion_produces_critical_alert(self):
        progress = {agent: set() for agent in MODULE.TRAINING_CURRICULUM}
        alerts = MODULE.generate_alerts(progress)
        for alert in alerts:
            self.assertEqual(alert["severity"], "critical")

    def test_alert_contains_required_fields(self):
        progress = {agent: set() for agent in MODULE.TRAINING_CURRICULUM}
        alerts = MODULE.generate_alerts(progress)
        self.assertTrue(len(alerts) > 0)
        for alert in alerts:
            for field in (
                "agent", "severity", "reason", "completion_rate",
                "completed_count", "total_topics", "pending_topics",
            ):
                self.assertIn(field, alert)

    def test_partial_below_warn_produces_warning_alert(self):
        # Complete just one topic out of many — rate will be below WARN (50%)
        agent = "Quinn (QA Engineer)"
        topics = MODULE.TRAINING_CURRICULUM[agent]
        # Complete only 1 topic — rate ≈ 25% (4 topics), below WARN
        completed = {topics[0]["id"]}
        progress = {a: set() for a in MODULE.TRAINING_CURRICULUM}
        progress[agent] = completed
        alerts = MODULE.generate_alerts(progress)
        agent_alerts = [a for a in alerts if a["agent"] == agent]
        self.assertEqual(len(agent_alerts), 1)
        # Rate for 1/4 = 25% which is at or below CRIT (25%), so critical
        self.assertIn(agent_alerts[0]["severity"], ("warning", "critical"))

    def test_near_complete_produces_no_alert(self):
        agent = "Quinn (QA Engineer)"
        topics = MODULE.TRAINING_CURRICULUM[agent]
        # Complete all but one — high completion rate
        completed = {t["id"] for t in topics[:-1]}
        progress = {a: set() for a in MODULE.TRAINING_CURRICULUM}
        progress[agent] = completed
        # Other agents have no completions so they'll alert; focus only on Quinn
        alerts = MODULE.generate_alerts(progress)
        quinn_alerts = [a for a in alerts if a["agent"] == agent]
        self.assertEqual(len(quinn_alerts), 0)


class SelectNextTopicsTests(unittest.TestCase):
    def test_returns_one_topic_per_agent_with_pending(self):
        progress = {agent: set() for agent in MODULE.TRAINING_CURRICULUM}
        next_topics = MODULE.select_next_topics(progress)
        agents_in_result = {item["agent"] for item in next_topics}
        self.assertEqual(agents_in_result, set(MODULE.TRAINING_CURRICULUM.keys()))

    def test_fully_trained_agent_not_in_result(self):
        agent = "Quinn (QA Engineer)"
        topics = MODULE.TRAINING_CURRICULUM[agent]
        completed = {t["id"] for t in topics}
        progress = {a: set() for a in MODULE.TRAINING_CURRICULUM}
        progress[agent] = completed
        next_topics = MODULE.select_next_topics(progress)
        agents_in_result = {item["agent"] for item in next_topics}
        self.assertNotIn(agent, agents_in_result)

    def test_next_topic_has_required_fields(self):
        progress = {agent: set() for agent in MODULE.TRAINING_CURRICULUM}
        next_topics = MODULE.select_next_topics(progress)
        for item in next_topics:
            self.assertIn("agent", item)
            self.assertIn("topic", item)
            topic = item["topic"]
            for field in ("id", "name", "description", "skill", "difficulty"):
                self.assertIn(field, topic)

    def test_next_topic_is_beginner_before_advanced(self):
        # When no topics are completed, the first pending topic should be
        # beginner-or-intermediate, not advanced (if mixed curriculum exists)
        progress = {agent: set() for agent in MODULE.TRAINING_CURRICULUM}
        next_topics = MODULE.select_next_topics(progress)
        for item in next_topics:
            agent = item["agent"]
            topics = MODULE.TRAINING_CURRICULUM.get(agent, [])
            has_beginner = any(t["difficulty"] == "beginner" for t in topics)
            if has_beginner:
                self.assertEqual(item["topic"]["difficulty"], "beginner")


class RenderMarkdownTests(unittest.TestCase):
    def _empty_progress(self):
        return {agent: set() for agent in MODULE.TRAINING_CURRICULUM}

    def _full_progress(self):
        return {
            agent: {t["id"] for t in topics}
            for agent, topics in MODULE.TRAINING_CURRICULUM.items()
        }

    def test_report_contains_date(self):
        output = MODULE.render_markdown(self._empty_progress(), "2026-07-31", "")
        self.assertIn("2026-07-31", output)

    def test_report_contains_all_agents(self):
        output = MODULE.render_markdown(self._empty_progress(), "2026-07-31", "")
        for agent in MODULE.TRAINING_CURRICULUM:
            self.assertIn(agent, output)

    def test_fully_trained_shows_all_complete_badge(self):
        output = MODULE.render_markdown(self._full_progress(), "2026-07-31", "")
        self.assertIn("All topics complete", output)

    def test_zero_completion_shows_critical_badge(self):
        output = MODULE.render_markdown(self._empty_progress(), "2026-07-31", "")
        self.assertIn("Critical", output)

    def test_workflow_url_appears_in_footer(self):
        url = "https://example.com/run/42"
        output = MODULE.render_markdown(self._empty_progress(), "2026-07-31", url)
        self.assertIn(url, output)

    def test_summary_table_present(self):
        output = MODULE.render_markdown(self._empty_progress(), "2026-07-31", "")
        self.assertIn("Completion Rate", output)

    def test_topic_names_appear_in_output(self):
        output = MODULE.render_markdown(self._empty_progress(), "2026-07-31", "")
        for agent, topics in MODULE.TRAINING_CURRICULUM.items():
            for topic in topics:
                self.assertIn(topic["name"], output)


class CurriculumIntegrityTests(unittest.TestCase):
    """Sanity-checks on the built-in training curriculum structure."""

    def test_all_agents_have_topics(self):
        for agent, topics in MODULE.TRAINING_CURRICULUM.items():
            self.assertTrue(len(topics) > 0, f"{agent} has no training topics")

    def test_all_topics_have_required_fields(self):
        required = {"id", "name", "description", "skill", "difficulty"}
        for agent, topics in MODULE.TRAINING_CURRICULUM.items():
            for topic in topics:
                missing = required - set(topic.keys())
                self.assertEqual(
                    missing, set(), f"{agent}/{topic.get('id')} missing fields: {missing}"
                )

    def test_difficulty_values_are_valid(self):
        valid = {"beginner", "intermediate", "advanced"}
        for agent, topics in MODULE.TRAINING_CURRICULUM.items():
            for topic in topics:
                self.assertIn(
                    topic["difficulty"],
                    valid,
                    f"{agent}/{topic['id']} has invalid difficulty: {topic['difficulty']}",
                )

    def test_topic_ids_are_unique_within_agent(self):
        for agent, topics in MODULE.TRAINING_CURRICULUM.items():
            ids = [t["id"] for t in topics]
            self.assertEqual(
                len(ids),
                len(set(ids)),
                f"{agent} has duplicate topic IDs: {ids}",
            )


if __name__ == "__main__":
    unittest.main()
