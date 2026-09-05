import unittest

from package_loader import load_module

dedup = load_module("features.forward.dedup")
FINGERPRINT_VERSION = dedup.FINGERPRINT_VERSION
ForwardStatus = dedup.ForwardStatus
classify_and_store_forward = dedup.classify_and_store_forward
enforce_forward_history_budget = dedup.enforce_forward_history_budget
prune_forward_records = dedup.prune_forward_records


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
        message_id="100",
        compatible_record_hashes=(),
        compatible_content_hashes=(),
    ):
        return classify_and_store_forward(
            self.scope,
            record_hash=record_hash,
            content_hashes=content_hashes,
            leaf_hashes=leaf_hashes,
            message_id=message_id,
            compatible_record_hashes=compatible_record_hashes,
            compatible_content_hashes=compatible_content_hashes,
            now=now,
            retention_days=2,
        )

    def test_shared_component_does_not_make_different_leaf_partial(self):
        self.classify(
            record_hash="a", content_hashes=("text", "image-a"), leaf_hashes=("leaf-a",)
        )
        match = self.classify(
            record_hash="b", content_hashes=("text", "image-b"), leaf_hashes=("leaf-b",)
        )
        self.assertEqual(match.status, ForwardStatus.LATEST)
        self.assertEqual(match.message_id, "")

    def test_partial_uses_most_recent_matching_record_not_list_order(self):
        self.classify(
            record_hash="recent",
            content_hashes=("a",),
            leaf_hashes=("a",),
            now=2000,
            message_id="200",
        )
        self.classify(
            record_hash="old",
            content_hashes=("b",),
            leaf_hashes=("b",),
            now=1000,
            message_id="100",
        )
        match = self.classify(
            record_hash="new",
            content_hashes=("a", "b", "c"),
            leaf_hashes=("a", "b", "c"),
            now=3000,
            message_id="300",
        )
        self.assertEqual(match.status, ForwardStatus.PARTIAL)
        self.assertEqual(match.message_id, "200")
        self.assertEqual(self.scope["forward_records"][-1]["message_id"], "300")

    def test_exact_keeps_original_message_and_time(self):
        self.classify(record_hash="a", content_hashes=("a",), leaf_hashes=("a",))
        match = self.classify(
            record_hash="a",
            content_hashes=("a",),
            leaf_hashes=("a",),
            message_id="200",
            now=2000,
        )
        self.assertEqual(match.status, ForwardStatus.EXACT)
        self.assertEqual(match.message_id, "100")
        self.assertEqual(len(self.scope["forward_records"]), 1)
        self.assertEqual(self.scope["forward_records"][0]["seen_at"], 1000)
        self.assertEqual(self.scope["forward_records"][0]["message_id"], "100")

    def test_exact_takes_priority_over_more_recent_partial(self):
        self.classify(record_hash="a", content_hashes=("a",), leaf_hashes=("a",))
        self.classify(
            record_hash="ab",
            content_hashes=("a", "b"),
            leaf_hashes=("a", "b"),
            message_id="200",
            now=2000,
        )
        match = self.classify(
            record_hash="a", content_hashes=("a",), leaf_hashes=("a",), now=3000
        )
        self.assertEqual(match.status, ForwardStatus.EXACT)
        self.assertEqual(match.message_id, "100")

    def test_equivalent_component_sequence_matches_despite_different_hash(self):
        self.classify(record_hash="old", content_hashes=("a", "b"), leaf_hashes=("ab",))
        match = self.classify(
            record_hash="new", content_hashes=("a", "b"), leaf_hashes=("a", "b")
        )
        self.assertEqual(match.status, ForwardStatus.EXACT)
        self.assertEqual(match.message_id, "100")
        later = self.classify(
            record_hash="a-only", content_hashes=("a",), leaf_hashes=("a",)
        )
        self.assertEqual(later.status, ForwardStatus.LATEST)

    def test_expired_original_allows_fresh_silent_record(self):
        self.classify(record_hash="a", content_hashes=("a",), leaf_hashes=("a",))
        match = self.classify(
            record_hash="a",
            content_hashes=("a",),
            leaf_hashes=("a",),
            now=1000 + 2 * 86400 + 1,
            message_id="200",
        )
        self.assertEqual(match.status, ForwardStatus.LATEST)
        self.assertEqual(self.scope["forward_records"][0]["message_id"], "200")

    def test_v2_matching_without_message_id_does_not_invent_reference(self):
        for exact in (False, True):
            with self.subTest(exact=exact):
                self.scope = {
                    "forward_fingerprint_version": 2,
                    "forward_records": [
                        {
                            "record_hash": "legacy",
                            "content_hashes": ["legacy-leaf"],
                            "seen_at": 999,
                        }
                    ],
                }
                match = self.classify(
                    record_hash="new",
                    content_hashes=("new",),
                    leaf_hashes=("leaf",),
                    compatible_content_hashes=("legacy-leaf",),
                    compatible_record_hashes=("legacy",) if exact else (),
                )
                self.assertEqual(
                    match.status,
                    ForwardStatus.EXACT if exact else ForwardStatus.PARTIAL,
                )
                self.assertEqual(match.message_id, "")
                self.assertEqual(self.scope["forward_records"][0]["message_id"], "")

    def test_v3_pruning_preserves_and_normalizes_message_ids(self):
        self.scope["forward_records"] = [
            {
                "record_hash": "a",
                "content_hashes": ["a"],
                "leaf_hashes": ["a"],
                "seen_at": 999,
                "message_id": -123,
            },
            {
                "record_hash": "b",
                "content_hashes": ["b"],
                "leaf_hashes": ["b"],
                "seen_at": 999,
            },
        ]
        records = prune_forward_records(self.scope, now=1000, retention_days=2)
        self.assertEqual([record["message_id"] for record in records], ["-123", ""])

    def test_unknown_fingerprint_version_clears_only_records(self):
        scope = {
            "forward_fingerprint_version": 1,
            "forward_records": [{"record_hash": "old"}],
            "forward_enabled": True,
        }
        self.assertEqual(prune_forward_records(scope, now=1000, retention_days=2), [])
        self.assertEqual(scope["forward_fingerprint_version"], FINGERPRINT_VERSION)
        self.assertTrue(scope["forward_enabled"])

    def test_history_budget_evicts_oldest_records_across_scopes(self):
        old = {
            "record_hash": "old",
            "content_hashes": ["a"],
            "leaf_hashes": ["a"],
            "seen_at": 1,
        }
        new = {
            "record_hash": "new",
            "content_hashes": ["b"],
            "leaf_hashes": ["b"],
            "seen_at": 2,
        }
        data = {
            "scopes": {
                "group:1": {"forward_records": [old], "forward_enabled": True},
                "group:2": {"forward_records": [new], "users": {"1": ["昵称"]}},
            }
        }
        self.assertEqual(enforce_forward_history_budget(data, max_bytes=77), 1)
        self.assertEqual(data["scopes"]["group:1"]["forward_records"], [])
        self.assertEqual(data["scopes"]["group:2"]["forward_records"], [new])
        self.assertTrue(data["scopes"]["group:1"]["forward_enabled"])
        self.assertEqual(data["scopes"]["group:2"]["users"], {"1": ["昵称"]})


if __name__ == "__main__":
    unittest.main()
