from __future__ import annotations

import unittest

from app.source_provenance import resolve_source_commit


class SourceProvenanceTests(unittest.TestCase):
    def test_railway_deployment_commit_is_authoritative(self) -> None:
        self.assertEqual(
            resolve_source_commit(
                {
                    "RAILWAY_GIT_COMMIT_SHA": "A" * 40,
                    "RDR21_SOURCE_COMMIT": "b" * 40,
                }
            ),
            "a" * 40,
        )

    def test_explicit_non_railway_commit_is_supported(self) -> None:
        self.assertEqual(
            resolve_source_commit({"RDR21_SOURCE_COMMIT": "C" * 40}),
            "c" * 40,
        )

    def test_missing_commit_fails_instead_of_fabricating_provenance(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "source commit is unavailable"):
            resolve_source_commit({})

    def test_invalid_railway_commit_does_not_fall_through(self) -> None:
        with self.assertRaisesRegex(ValueError, "RAILWAY_GIT_COMMIT_SHA"):
            resolve_source_commit(
                {
                    "RAILWAY_GIT_COMMIT_SHA": "short",
                    "RDR21_SOURCE_COMMIT": "d" * 40,
                }
            )


if __name__ == "__main__":
    unittest.main()
