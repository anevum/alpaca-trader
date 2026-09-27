from __future__ import annotations

import hashlib
import json
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "research" / "artifacts" / "residual-downshock-rebound-v2.1"
EARLIER = ARTIFACT_DIR / "development-report-ded64342.json"
LATER = ARTIFACT_DIR / "development-report-d408a930.json"
VERIFICATION = ARTIFACT_DIR / "dual-report-verification.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def semantic_sha256(document: dict) -> str:
    semantic = dict(document)
    semantic.pop("generated_at")
    payload = json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


class RecoveredDevelopmentArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.earlier = json.loads(EARLIER.read_text())
        cls.later = json.loads(LATER.read_text())
        cls.verification = json.loads(VERIFICATION.read_text())

    def test_raw_report_identities_are_preserved(self) -> None:
        self.assertEqual(EARLIER.stat().st_size, 139731)
        self.assertEqual(LATER.stat().st_size, 139731)
        self.assertEqual(
            sha256(EARLIER),
            "173cdf9d86a129ddb02bde6c1e60f896d1a4db5c6a88e898a25c63a8162bdf7f",
        )
        self.assertEqual(
            sha256(LATER),
            "addf91840e07c94a564f5a00b0acf0ae3dfa6f2dda1d32b4dfd8a3250575e610",
        )

    def test_only_generated_at_differs(self) -> None:
        differing = {
            key
            for key in self.earlier | self.later
            if self.earlier.get(key) != self.later.get(key)
        }
        self.assertEqual(differing, {"generated_at"})
        expected = "71a7636b6d31694899fd55b2d2d6d4f6f8f07452ec9204bbe1c09b1fc34edaa9"
        self.assertEqual(semantic_sha256(self.earlier), expected)
        self.assertEqual(semantic_sha256(self.later), expected)

    def test_corpus_matrix_and_exact_totals(self) -> None:
        rows = self.later["corpus_integrity"]["coverage"]
        self.assertEqual(len(rows), 138)
        self.assertEqual(len({row["window"] for row in rows}), 6)
        self.assertEqual(len({row["symbol"] for row in rows}), 23)
        self.assertTrue(all(row["expected_sessions"] == row["represented_sessions"] for row in rows))
        self.assertTrue(all(not row["missing_sessions"] for row in rows))
        self.assertEqual(sum(bool(row["partial_sessions"]) for row in rows), 33)
        self.assertTrue(all(row["pagination_complete"] for row in rows))
        self.assertEqual(sum(row["raw_one_minute_bar_count"] for row in rows), 726277)
        self.assertEqual(sum(row["expected_session_count"] * 78 for row in rows), 156078)
        self.assertEqual(sum(row["five_minute_observation_count"] for row in rows), 155604)
        self.assertEqual(sum(row["expected_session_count"] * 65 for row in rows), 130065)
        represented_model = sum(
            int(
                (
                    Decimal(str(row["model_availability_ratio"]))
                    * row["expected_session_count"]
                    * 65
                ).to_integral_value()
            )
            for row in rows
        )
        self.assertEqual(represented_model, 128943)

        failures = [row for row in rows if not row["gate_pass"]]
        self.assertEqual(
            [(row["window"], row["symbol"]) for row in failures],
            [("dev-04", "COST"), ("dev-06", "COST")],
        )
        self.assertEqual(Decimal(str(failures[0]["synchronization_completeness"])), Decimal("0.8789743589743589"))
        self.assertEqual(Decimal(str(failures[1]["synchronization_completeness"])), Decimal("0.84"))

    def test_pre_performance_verdict_and_access_boundaries(self) -> None:
        self.assertEqual(self.later["corpus_integrity"]["verdict"], "FAIL")
        self.assertEqual(self.later["configurations"], [])
        self.assertIsNone(self.later["selected_configuration_id"])
        self.assertFalse(self.later["validation_eligible"])
        self.assertEqual(
            self.later["decision"],
            "DEVELOPMENT CORPUS FAIL; performance evaluation not run",
        )
        self.assertEqual(
            self.later["access_audit"],
            {
                "development_opened": True,
                "holdout_opened": False,
                "latest_performance_date_accessed": "2026-05-08",
                "quarantine_accessed": False,
                "validation_opened": False,
            },
        )

    def test_provenance_values_are_distinct_and_explicit(self) -> None:
        provenance = self.verification["provenance"]
        self.assertEqual(
            provenance["report_declared_source_commit"],
            "46c2de0d84c736b12f8a3bbeb64ef4ad1d5305cc",
        )
        self.assertEqual(
            provenance["actual_railway_source_commit"],
            "4f42fbcae33526659b6336ecd68808c53ad351f1",
        )
        self.assertNotEqual(
            provenance["report_declared_source_commit"],
            provenance["actual_railway_source_commit"],
        )
        self.assertEqual(len(provenance["verified_identical_git_blobs"]), 3)


if __name__ == "__main__":
    unittest.main()
