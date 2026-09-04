import unittest

from package_loader import load_module

dedup = load_module("features.forward.dedup")
parser = load_module("features.forward.parser")
FINGERPRINT_VERSION = dedup.FINGERPRINT_VERSION
ForwardStatus = dedup.ForwardStatus
classify_and_store_forward = dedup.classify_and_store_forward
dedupe_flattened_nodes = dedup.dedupe_flattened_nodes
enforce_forward_history_budget = dedup.enforce_forward_history_budget
prune_forward_records = dedup.prune_forward_records
FlattenedForwardNode = parser.FlattenedForwardNode


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

        removed = enforce_forward_history_budget(
            data,
            max_bytes=77,
        )

        self.assertEqual(removed, 1)
        self.assertEqual(data["scopes"]["group:1"]["forward_records"], [])
        self.assertEqual(data["scopes"]["group:2"]["forward_records"], [new])
        self.assertTrue(data["scopes"]["group:1"]["forward_enabled"])
        self.assertEqual(data["scopes"]["group:2"]["users"], {"1": ["昵称"]})

    def test_flattened_node_deduplication_uses_history_and_current_order(self):
        def node(content_hash, content=True):
            return FlattenedForwardNode(
                content_hash=content_hash,
                component_hashes=(),
                content=({"type": "text", "data": {"text": content_hash}},)
                if content
                else (),
                sender_id="1",
                sender_name="成员",
                timestamp=0,
            )

        result = dedupe_flattened_nodes(
            (node("historical"), node("new"), node("new"), node("empty", False)),
            frozenset({"historical"}),
        )

        self.assertEqual([item.content_hash for item in result], ["new"])


if __name__ == "__main__":
    unittest.main()
