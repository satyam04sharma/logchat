"""Offline migration contract; real PostgreSQL acceptance lives in pipeline_smoke.

These checks prevent reintroducing the nullable guard while no DB is available.
They do not simulate PL/pgSQL execution or establish database correctness.
"""
from pathlib import Path
import re
import unittest


class SchedulerSQLContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = (Path(__file__).resolve().parents[1] / "supabase/migrations/0007_completed_feedback.sql").read_text()

    def test_enqueue_rejects_incomplete_lease_before_any_mutation(self):
        enqueue = self.sql.split("END $$;", 1)[0]
        guard = re.search(r"IF NOT FOUND.*?END IF;", enqueue, re.S)
        self.assertIsNotNone(guard)
        expression = " ".join(guard.group().split())
        for clause in (
            "IF NOT FOUND OR p_check_token IS NULL",
            "OR st.check_lease_token IS NULL",
            "OR st.check_lease_expires_at IS NULL",
            "OR st.check_lease_token IS DISTINCT FROM p_check_token",
            "OR st.check_lease_expires_at < now()",
            "RAISE EXCEPTION 'stale scheduler lease'",
        ):
            self.assertIn(clause, expression)
        self.assertIn("FOR UPDATE;", enqueue[:guard.start()])
        self.assertNotRegex(enqueue[:guard.end()], r"\b(INSERT INTO|UPDATE public|DELETE FROM)\b")

    def test_enqueue_preserves_unknown_count_in_both_dispatch_paths(self):
        enqueue = self.sql.split("END $$;", 1)[0]
        assignments = re.findall(r"last_event_count\s*=\s*(.*?)\s*,\s*check_lease_token=", enqueue, re.S)
        # Both the blocked-job path and normal enqueue consume the lease.
        self.assertEqual(len(assignments), 2)
        for assignment in assignments:
            self.assertEqual(" ".join(assignment.split()),
                             "CASE WHEN p_observed_events IS NULL THEN NULL ELSE greatest(0,p_observed_events) END")

    def test_completed_feedback_preserves_lease_and_evidence_scope(self):
        feedback = self.sql.split("CREATE FUNCTION public.pipeline_completed_feedback", 1)[1].split("$$;", 1)[0]
        expression = " ".join(feedback.split())
        # A completed job and its successful coverage must belong to the same
        # source/tenant/project/environment as the leased scheduler state.
        for field in ("source_id", "owner_id", "project_id", "environment_id"):
            self.assertIn(f"j.{field}=st.{field}", expression)
            self.assertIn(f"c.{field}=j.{field}", expression)
        for clause in (
            "st.source_id=p_source_id AND st.check_lease_token=p_check_token",
            "st.check_lease_expires_at>=now() AND session_user='logchat_worker'",
            "j.status='completed'",
            "c.status IN ('complete','empty')",
            "c.window_start>=j.window_start AND c.window_end=j.window_end",
            "c.event_count=j.events_fetched",
            "(SELECT count(*) FROM bounded WHERE status IN ('complete','empty'))=1",
            "NOT EXISTS (SELECT 1 FROM bounded WHERE status='failed')",
        ):
            self.assertIn(clause, expression)
        self.assertIn("REVOKE ALL ON FUNCTION public.pipeline_completed_feedback(uuid,uuid) FROM PUBLIC,authenticated;", self.sql)
        self.assertIn("GRANT EXECUTE ON FUNCTION public.pipeline_completed_feedback(uuid,uuid) TO logchat_worker;", self.sql)


if __name__ == "__main__":
    unittest.main()
