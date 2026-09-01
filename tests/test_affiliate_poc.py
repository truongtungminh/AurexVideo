import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from social_upload import affiliate_poc, affiliate_store


class AffiliatePocTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aurex-affiliate-poc-test-")
        self.db_patch = patch.object(
            affiliate_store,
            "AFFILIATE_DB_PATH",
            Path(self.temp_dir.name) / "affiliate.sqlite3",
        )
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_fixed_matrix_and_start_are_persistent_and_idempotent(self):
        run = affiliate_poc.start_run("Knowzy", "video-001", idempotency_key="poc-001")
        again = affiliate_poc.start_run("knowzy", "video-001", idempotency_key="poc-001")

        self.assertEqual(run["runId"], again["runId"])
        self.assertEqual(run["createdAt"], again["createdAt"])
        self.assertEqual(affiliate_poc.CASES, ("A", "B", "C", "D"))
        self.assertEqual(
            [(case["caseKey"], case["publishMode"], case["commentMode"]) for case in run["cases"]],
            [
                ("A", "manual", "manual"),
                ("B", "api", "manual"),
                ("C", "manual", "api"),
                ("D", "api", "api"),
            ],
        )
        self.assertTrue(Path(affiliate_store.AFFILIATE_DB_PATH).exists())

    def test_brand_isolation_does_not_reveal_other_run(self):
        run = affiliate_poc.start_run("brand-a", "video-001", idempotency_key="poc-001")
        other = affiliate_poc.start_run("brand-b", "video-001", idempotency_key="poc-001")

        self.assertNotEqual(run["runId"], other["runId"])
        with self.assertRaisesRegex(ValueError, "không tồn tại"):
            affiliate_poc.summarize_run("brand-b", run["runId"])
        with self.assertRaisesRegex(ValueError, "không tồn tại"):
            affiliate_poc.record_result("brand-b", "A", "passed", run_id=run["runId"])

    def test_case_updates_are_idempotent_and_preserve_case_creation(self):
        run = affiliate_poc.start_run("knowzy", "video-001", idempotency_key="case-idempotency")
        original = run["cases"][0]
        updated = affiliate_poc.record_result(
            "knowzy",
            "A",
            "running",
            run_id=run["runId"],
            content_id="video-001",
            page_id="page-1",
            evidence_url="https://example.test/poc-a",
            notes="operator started",
        )
        repeated = affiliate_poc.record_result(
            "knowzy",
            "A",
            "running",
            run_id=run["runId"],
            content_id="video-001",
            page_id="page-1",
            evidence_url="https://example.test/poc-a",
            notes="operator started",
        )
        matching = [case for case in repeated["cases"] if case["caseKey"] == "A"]

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["createdAt"], original["createdAt"])
        self.assertEqual(updated["counts"]["running"], 1)
        self.assertIsNone(matching[0]["bannerObserved"])

    def test_only_bounded_scalar_evidence_fields_are_accepted(self):
        run = affiliate_poc.start_run("knowzy", "video-001", idempotency_key="safe-fields")
        with self.assertRaises(ValueError):
            affiliate_poc.record_result(
                "knowzy", "A", "passed", run_id=run["runId"], evidence_url={"raw": "payload"}
            )
        with self.assertRaises(ValueError):
            affiliate_poc.record_result(
                "knowzy", "A", "passed", run_id=run["runId"], notes="x" * 1001
            )
        result = affiliate_poc.record_result(
            "knowzy",
            "A",
            "passed",
            run_id=run["runId"],
            content_id="video-001",
            post_id="post-1",
            comment_id="comment-1",
            banner_observed=True,
            evidence_url="https://example.test/poc-a",
            notes="verified",
        )
        case = result["cases"][0]
        self.assertEqual(case["postId"], "post-1")
        self.assertEqual(case["commentId"], "comment-1")
        self.assertTrue(case["bannerObserved"])

    def test_validation_summary_precedence_and_all_status_counts(self):
        run = affiliate_poc.start_run("knowzy", "video-001", idempotency_key="summary")
        for args in (("unknown", "pending"), ("A", "unknown")):
            with self.assertRaises(ValueError):
                affiliate_poc.record_result("knowzy", *args, run_id=run["runId"])
        with self.assertRaises(ValueError):
            affiliate_poc.start_run("bad brand!", "video-001")
        with self.assertRaises(ValueError):
            affiliate_poc.start_run("knowzy", "key with spaces")
        with self.assertRaises(ValueError):
            affiliate_poc.summarize_run("knowzy", "not-a-run")

        affiliate_poc.record_result("knowzy", "A", "running", run_id=run["runId"])
        self.assertEqual(affiliate_poc.summarize_run("knowzy", run["runId"])["status"], "running")
        affiliate_poc.record_result("knowzy", "B", "blocked", run_id=run["runId"])
        self.assertEqual(affiliate_poc.summarize_run("knowzy", run["runId"])["status"], "blocked")
        affiliate_poc.record_result("knowzy", "C", "failed", run_id=run["runId"])
        summary = affiliate_poc.summarize_run("knowzy", run["runId"])
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["counts"], {"pending": 1, "running": 1, "passed": 0, "failed": 1, "blocked": 1})

        for case_key in affiliate_poc.CASES:
            affiliate_poc.record_result("knowzy", case_key, "passed", run_id=run["runId"])
        self.assertEqual(affiliate_poc.summarize_run("knowzy", run["runId"])["status"], "passed")

    def test_list_filters_and_unstarted_summary_are_content_scoped(self):
        empty = affiliate_poc.poc_summary("knowzy", "video-001")
        self.assertFalse(empty["started"])
        self.assertEqual(empty["counts"]["pending"], 4)
        first = affiliate_poc.start_run("knowzy", "video-001", idempotency_key="list-1")
        second = affiliate_poc.start_run("knowzy", "video-001", idempotency_key="list-2")
        affiliate_poc.record_result("knowzy", "A", "running", run_id=second["runId"])

        self.assertEqual(
            [row["runId"] for row in affiliate_poc.list_runs("knowzy", "video-001", status="running", limit=1)],
            [second["runId"]],
        )
        self.assertEqual(len(affiliate_poc.list_runs("knowzy", "video-001", limit=1, offset=1)), 1)
        self.assertIn(first["runId"], [row["runId"] for row in affiliate_poc.list_runs("knowzy", "video-001", limit=2)])
        self.assertEqual(affiliate_poc.list_runs("knowzy", "other-video"), [])
        for kwargs in ({"limit": 0}, {"limit": True}, {"offset": -1}, {"status": "bad"}):
            with self.assertRaises(ValueError):
                affiliate_poc.list_runs("knowzy", "video-001", **kwargs)


if __name__ == "__main__":
    unittest.main()
