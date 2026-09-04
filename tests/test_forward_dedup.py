import unittest

from forward_dedup import (
    FINGERPRINT_VERSION,
    ForwardStatus,
    classify_and_store_forward,
    prune_forward_records,
)


class ForwardDedupTests(unittest.TestCase):
    def setUp(self):
        self.scope = {
            "forward_fingerprint_version": FINGERPRINT_VERSION,
            "forward_records": [],
        }

    def classify(
        self,
        *,
        record_hash,
        content_hashes,
        leaf_hashes,
        now=1000,
        compatible_record_hashes=(),
        compatible_content_hashes=(),
    ):
        return classify_and_store_forward(
            self.scope,
            record_hash=record_hash,
            content_hashes=content_hashes,
            leaf_hashes=leaf_hashes,
            compatible_record_hashes=compatible_record_hashes,
            compatible_content_hashes=compatible_content_hashes,
            now=now,
            retention_days=2,
        )

    def test_shared_component_does_not_make_different_leaf_partial(self):
        first = self.classify(
            record_hash="record-a",
            content_hashes=("shared-text", "image-a"),
            leaf_hashes=("leaf-a",),
        )
        second = self.classify(
            record_hash="record-b",
            content_hashes=("shared-text", "image-b"),
            leaf_hashes=("leaf-b",),
            now=1001,
        )

        self.assertEqual(first.status, ForwardStatus.LATEST)
        self.assertEqual(second.status, ForwardStatus.LATEST)
        self.assertEqual(second.duplicate_leaf_hashes, frozenset())

    def test_partial_duplicate_reports_the_matching_leaf(self):
        self.classify(
            record_hash="record-a",
            content_hashes=("component-a",),
            leaf_hashes=("leaf-a",),
        )
        decision = self.classify(
            record_hash="record-b",
            content_hashes=("component-b",),
            leaf_hashes=("leaf-a", "leaf-b"),
            now=1001,
        )

        self.assertEqual(decision.status, ForwardStatus.PARTIAL)
        self.assertEqual(decision.duplicate_leaf_hashes, frozenset({"leaf-a"}))

    def test_exact_duplicate_refreshes_existing_record(self):
        self.classify(
            record_hash="record-a",
            content_hashes=("component-a",),
            leaf_hashes=("leaf-a",),
        )
        decision = self.classify(
            record_hash="record-a",
            content_hashes=("component-a",),
            leaf_hashes=("leaf-a",),
            now=2000,
        )

        self.assertEqual(decision.status, ForwardStatus.EXACT)
        self.assertEqual(len(self.scope["forward_records"]), 1)
        self.assertEqual(self.scope["forward_records"][0]["seen_at"], 2000)

    def test_v2_partial_compatibility_does_not_claim_deletable_leaves(self):
        self.scope = {
            "forward_fingerprint_version": 2,
            "forward_records": [
                {
                    "record_hash": "legacy-record",
                    "content_hashes": ["legacy-content"],
                    "seen_at": 999,
                }
            ],
        }

        decision = self.classify(
            record_hash="record-new",
            content_hashes=("component-new",),
            leaf_hashes=("leaf-new",),
            compatible_content_hashes=("legacy-content",),
        )

        self.assertEqual(decision.status, ForwardStatus.PARTIAL)
        self.assertEqual(decision.duplicate_leaf_hashes, frozenset())

    def test_unknown_fingerprint_version_clears_only_records(self):
        scope = {
            "forward_fingerprint_version": 1,
            "forward_records": [{"record_hash": "old"}],
            "forward_enabled": True,
        }

        records = prune_forward_records(scope, now=1000, retention_days=2)

        self.assertEqual(records, [])
        self.assertEqual(scope["forward_records"], [])
        self.assertEqual(scope["forward_fingerprint_version"], FINGERPRINT_VERSION)
        self.assertTrue(scope["forward_enabled"])


if __name__ == "__main__":
    unittest.main()
