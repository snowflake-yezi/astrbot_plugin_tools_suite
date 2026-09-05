import hashlib
import json
import unittest

from package_loader import load_module

ForwardRecordExpander = load_module("features.forward.parser").ForwardRecordExpander


def text(value):
    return {"type": "text", "data": {"text": value}}


def node(content):
    return {"type": "node", "data": {"content": content}}


def napcat_media_payload(components):
    return {
        "messages": [
            {
                "sender": {"user_id": index, "nickname": f"成员{index}"},
                "message": [component],
            }
            for index, component in enumerate(components, 1)
        ]
    }


def nested_forward_fetch(levels):
    payloads = {}
    for level in range(1, levels + 1):
        message = (
            [{"type": "forward", "data": {"id": str(level + 1)}}]
            if level < levels
            else [text("leaf")]
        )
        payloads[str(level)] = {
            "messages": [
                {
                    "type": "node",
                    "data": {
                        "user_id": 1,
                        "nickname": "成员",
                        "message": message,
                    },
                }
            ]
        }

    async def fetch(forward_id):
        return payloads[forward_id]

    return fetch


class ForwardRecordLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def unexpected_fetch(_):
            raise AssertionError("inline records must not fetch")

        self.fetch = unexpected_fetch

    async def test_leaf_limit_checks_pending_content_after_nested_node(self):
        for extra_leaf in (False, True):
            with self.subTest(extra_leaf=extra_leaf):
                expander = ForwardRecordExpander(self.fetch)
                expander.MAX_LEAF_MESSAGES = 2
                content = [text("first"), node([text("second")])]
                if extra_leaf:
                    content.append(text("third"))
                result = await expander.expand([node(content)])

                self.assertEqual(result.complete, not extra_leaf)
                self.assertEqual(result.leaf_count, 2)
                self.assertEqual(len(result.leaf_hashes), 2)
                if extra_leaf:
                    self.assertIn("max-leaf-messages-exceeded", result.errors)

    async def test_component_limit_accepts_exact_limit(self):
        expander = ForwardRecordExpander(self.fetch)
        result = await expander.expand(
            [node([text(str(index)) for index in range(expander.MAX_COMPONENTS)])]
        )

        self.assertTrue(result.complete)
        self.assertEqual(len(result.content_hashes), expander.MAX_COMPONENTS)

    async def test_component_limit_rejects_one_extra_component(self):
        expander = ForwardRecordExpander(self.fetch)
        result = await expander.expand(
            [node([text(str(index)) for index in range(expander.MAX_COMPONENTS + 1)])]
        )

        self.assertFalse(result.complete)
        self.assertIn("max-components-exceeded", result.errors)

    async def test_single_layer_napcat_media_fingerprints_two_videos_and_image(self):
        components = [
            {
                "type": "video",
                "data": {
                    "file": "2c4c5ee7405d4c16bfd3ebac5ceb0503.mp4",
                    "url": "/app/.config/QQ/nt_data/Video/Ori/video-a.mp4",
                    "seq": 17,
                    "vendor": {"timestamp": 123, "token": "keep"},
                },
            },
            {
                "type": "video",
                "data": {
                    "file": "df796194d123ebd72ad1eb61bd84f1f5.mp4",
                    "url": "/app/.config/QQ/nt_data/Video/Ori/video-b.mp4",
                },
            },
            {
                "type": "image",
                "data": {
                    "file": "image.jpg",
                    "url": "https://multimedia.nt.qq.com.cn/image.jpg",
                },
            },
        ]

        async def fetch(_forward_id):
            return napcat_media_payload(components)

        result = await ForwardRecordExpander(fetch).expand(
            [{"type": "forward", "data": {"id": "outer"}}]
        )

        self.assertTrue(result.complete)
        self.assertEqual(result.max_forward_depth, 1)
        self.assertEqual(result.leaf_count, 3)
        expected = [
            "video:digest:2c4c5ee7405d4c16bfd3ebac5ceb0503",
            "video:digest:df796194d123ebd72ad1eb61bd84f1f5",
            "image:location:image.jpg",
        ]
        self.assertEqual(
            result.content_hashes,
            tuple(hashlib.sha256(value.encode()).hexdigest() for value in expected),
        )
        self.assertEqual(len(result.leaf_hashes), 3)
        self.assertEqual(components[0]["data"]["seq"], 17)

    async def test_flatten_preserves_original_content_duplicates_and_senders(self):
        before = text("  原文\n ")
        blank = text(" \n ")
        image = {
            "type": "image",
            "data": {
                "file": "cache.jpg",
                "url": "https://example.test/a?token=keep",
                "vendor": {"seq": 17},
            },
        }
        inner_content = [text("重复"), image]
        inner = {
            "messages": [
                {
                    "sender": {"user_id": 2, "nickname": "内层"},
                    "time": 22,
                    "message": inner_content,
                },
                {
                    "sender": {"user_id": 2, "nickname": "内层"},
                    "time": 23,
                    "message": inner_content,
                },
            ]
        }
        outer = {
            "messages": [
                {
                    "sender": {"user_id": 1, "nickname": "外层"},
                    "time": 11,
                    "message": [
                        before,
                        {"type": "forward", "data": {"id": "inner"}},
                        blank,
                        text("结尾"),
                    ],
                }
            ]
        }

        async def fetch(forward_id):
            return {"outer": outer, "inner": inner}[forward_id]

        result = await ForwardRecordExpander(fetch).expand(
            [{"type": "forward", "data": {"id": "outer"}}]
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.max_forward_depth, 2)
        self.assertEqual(
            [node.content for node in result.nodes],
            [
                (before,),
                tuple(inner_content),
                tuple(inner_content),
                (blank, text("结尾")),
            ],
        )
        self.assertEqual(
            [
                (node.sender_id, node.sender_name, node.timestamp)
                for node in result.nodes
            ],
            [
                ("1", "外层", 11),
                ("2", "内层", 22),
                ("2", "内层", 23),
                ("1", "外层", 11),
            ],
        )
        result.nodes[1].content[1]["data"]["vendor"]["seq"] = 99
        self.assertEqual(image["data"]["vendor"]["seq"], 17)
        self.assertEqual(result.nodes[2].content[1]["data"]["vendor"]["seq"], 17)

    async def test_whitespace_only_node_is_retained_for_resend_not_fingerprint(self):
        result = await ForwardRecordExpander(self.fetch).expand(
            [node([text("a")]), node([text(" \n ")]), node([text("b")])]
        )
        reference = await ForwardRecordExpander(self.fetch).expand(
            [node([text("a")]), node([text("b")])]
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.leaf_count, 3)
        self.assertEqual(result.nodes[1].content, (text(" \n "),))
        self.assertEqual(result.record_hash, reference.record_hash)
        self.assertEqual(result.leaf_hashes, reference.leaf_hashes)

    async def test_fingerprint_preserves_order_and_duplicates_but_not_grouping(self):
        grouped = await ForwardRecordExpander(self.fetch).expand(
            [node([text("a"), text("b")])]
        )
        separate = await ForwardRecordExpander(self.fetch).expand(
            [node([text("a")]), node([text("b")])]
        )
        reversed_record = await ForwardRecordExpander(self.fetch).expand(
            [node([text("b"), text("a")])]
        )
        repeated = await ForwardRecordExpander(self.fetch).expand(
            [node([text("a"), text("b"), text("b")])]
        )
        self.assertEqual(grouped.record_hash, separate.record_hash)
        self.assertNotEqual(grouped.leaf_hashes, separate.leaf_hashes)
        self.assertNotEqual(grouped.record_hash, reversed_record.record_hash)
        self.assertNotEqual(grouped.record_hash, repeated.record_hash)
        canonical = json.dumps(
            ["text:a", "text:b"], ensure_ascii=False, separators=(",", ":")
        )
        self.assertEqual(
            grouped.leaf_hashes, (hashlib.sha256(canonical.encode()).hexdigest(),)
        )

    async def test_forward_cycle_is_incomplete(self):
        async def fetch(_):
            return {"messages": [node([{"type": "forward", "data": {"id": "cycle"}}])]}

        result = await ForwardRecordExpander(fetch).expand(
            [{"type": "forward", "data": {"id": "cycle"}}]
        )
        self.assertFalse(result.complete)
        self.assertIn("forward-cycle:cycle", result.errors)

    async def test_forward_depth_accepts_sixteen_layers(self):
        result = await ForwardRecordExpander(nested_forward_fetch(16)).expand(
            [{"type": "forward", "data": {"id": "1"}}]
        )

        self.assertTrue(result.complete)
        self.assertEqual(result.max_forward_depth, 16)
        self.assertEqual(result.leaf_count, 1)

    async def test_forward_depth_rejects_seventeen_layers(self):
        result = await ForwardRecordExpander(nested_forward_fetch(17)).expand(
            [{"type": "forward", "data": {"id": "1"}}]
        )

        self.assertFalse(result.complete)
        self.assertEqual(result.max_forward_depth, 17)
        self.assertIn("max-forward-depth-exceeded", result.errors)

    async def test_structure_depth_has_an_independent_guard(self):
        expander = ForwardRecordExpander(self.fetch)
        payload = node([text("leaf")])
        for _ in range(expander.MAX_STRUCTURE_DEPTH + 1):
            payload = {"messages": [payload]}

        result = await expander.expand([payload])

        self.assertFalse(result.complete)
        self.assertIn("max-structure-depth-exceeded", result.errors)


if __name__ == "__main__":
    unittest.main()
