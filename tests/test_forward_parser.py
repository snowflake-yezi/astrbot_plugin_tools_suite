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
                self.assertEqual(len(result.nodes), 2)
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

    async def test_single_layer_napcat_media_keeps_two_videos_and_image(self):
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
        self.assertEqual(
            [
                [component["type"] for component in item.content]
                for item in result.nodes
            ],
            [["video"], ["video"], ["image"]],
        )
        self.assertEqual(
            [item.content[0] for item in result.nodes],
            components,
        )
        self.assertIsNot(result.nodes[0].content[0], components[0])
        components[0]["data"]["file"] = "changed.mp4"
        self.assertEqual(
            result.nodes[0].content[0]["data"]["file"],
            "2c4c5ee7405d4c16bfd3ebac5ceb0503.mp4",
        )

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
