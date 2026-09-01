import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from social_upload import affiliate_poc, affiliate_store


class AffiliatePocTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aurex-affiliate-poc-test-")
        self.db_patch = patch.object(affiliate_store, "AFFILIATE_DB_PATH", Path(self.temp_dir.name) / "affiliate.sqlite3")
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_fixed_matrix_and_start_are_persistent_and_idempotent(self):
        run = affiliate_poc.start_run("Knowzy", "poc-001")
        again = affiliate_poc.start_run("knowzy", "poc-001")

        self.assertEqual(run["id"], again["id"])
        self.assertEqual(run["created_at"], again["created_at"])
        self.assertEqual(affiliate_poc.CASE_MATRIX["D"], {"publish": "api", "comment": "api"})
        with self.assertRaises(TypeError):
            affiliate_poc.CASE_MATRIX["A"] = {"publish": "api", "comment": "api"}
        self.assertEqual([(case["case"], case["publish"], case["comment"]) for case in run["cases"]], [
            ("A", "manual", "manual"), ("B", "api", "manual"),
            ("C", "manual", "api"), ("D", "api", "api"),
        ])
        self.assertTrue(Path(affiliate_store.AFFILIATE_DB_PATH).exists())

    def test_brand_isolation_does_not_reveal_other_run(self):
        run = affiliate_poc.start_run("brand-a", "key-a")
        other = affiliate_poc.start_run("brand-b", "key-a")

        self.assertNotEqual(run["id"], other["id"])
        with self.assertRaisesRegex(ValueError, "không tồn tại"):
            affiliate_poc.summarize_run("brand-b", run["id"])
        with self.assertRaisesRegex(ValueError, "không tồn tại"):
            affiliate_poc.record_case_result("brand-b", run["id"], "A", "passed")

    def test_case_updates_are_idempotent_and_preserve_case_creation(self):
        run = affiliate_poc.start_run("knowzy", "case-idempotency")
        original = run["cases"][0]
        updated = affiliate_poc.record_case_result(
            "knowzy", run["id"], "A", "running", note="operator started", evidence="clip-1"
        )
        repeated = affiliate_poc.record_case_result(
            "knowzy", run["id"], "A", "running", note="operator started", evidence="clip-1"
        )
        matching = [case for case in repeated["cases"] if case["case"] == "A"]

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["created_at"], original["created_at"])
        self.assertEqual(updated["counts"]["running"], 1)

    def test_only_bounded_scalar_evidence_fields_are_accepted(self):
        run = affiliate_poc.start_run("knowzy", "safe-fields")
        with self.assertRaises(ValueError):
            affiliate_poc.record_case_result("knowzy", run["id"], "A", "passed", evidence={"raw": "payload"})
        with self.assertRaises(ValueError):
            affiliate_poc.record_case_result("knowzy", run["id"], "A", "passed", note="x" * 1001)
        result = affiliate_poc.record_case_result(
            "knowzy", run["id"], "A", "passed", message="verified", reference="facebook-post-1"
        )
        case = result["cases"][0]
        self.assertEqual(case["message"], "verified")
        self.assertEqual(case["reference"], "facebook-post-1")

    def test_validation_summary_precedence_and_all_status_counts(self):
        run = affiliate_poc.start_run("knowzy", "summary")
        for args in (("unknown", "pending"), ("A", "unknown")):
            with self.assertRaises(ValueError):
                affiliate_poc.record_case_result("knowzy", run["id"], *args)
        with self.assertRaises(ValueError):
            affiliate_poc.start_run("bad brand!", "key")
        with self.assertRaises(ValueError):
            affiliate_poc.start_run("knowzy", "key with spaces")
        with self.assertRaises(ValueError):
            affiliate_poc.summarize_run("knowzy", "not-a-run")

        affiliate_poc.record_case_result("knowzy", run["id"], "A", "running")
        self.assertEqual(affiliate_poc.summarize_run("knowzy", run["id"])["status"], "running")
        affiliate_poc.record_case_result("knowzy", run["id"], "B", "blocked")
        self.assertEqual(affiliate_poc.summarize_run("knowzy", run["id"])["status"], "blocked")
        affiliate_poc.record_case_result("knowzy", run["id"], "C", "failed")
        summary = affiliate_poc.summarize_run("knowzy", run["id"])
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["counts"], {"blocked": 1, "failed": 1, "passed": 0, "pending": 1, "running": 1})

        for case in "ABCD":
            affiliate_poc.record_case_result("knowzy", run["id"], case, "passed")
        self.assertEqual(affiliate_poc.summarize_run("knowzy", run["id"])["status"], "passed")

    def test_list_filters_and_validates_pagination(self):
        first = affiliate_poc.start_run("knowzy", "list-1")
        second = affiliate_poc.start_run("knowzy", "list-2")
        affiliate_poc.record_case_result("knowzy", second["id"], "A", "running")

        self.assertEqual([row["id"] for row in affiliate_poc.list_runs("knowzy", status="running", limit=1)], [second["id"]])
        self.assertEqual(len(affiliate_poc.list_runs("knowzy", limit=1, offset=1)), 1)
        self.assertIn(first["id"], [row["id"] for row in affiliate_poc.list_runs("knowzy", limit=2)])
        for kwargs in ({"limit": 0}, {"limit": True}, {"offset": -1}, {"status": "bad"}):
            with self.assertRaises(ValueError):
                affiliate_poc.list_runs("knowzy", **kwargs)


if __name__ == "__main__":
    unittest.main()
