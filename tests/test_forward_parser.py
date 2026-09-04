import unittest

from package_loader import load_module

ForwardRecordExpander = load_module("features.forward.parser").ForwardRecordExpander


def text(value):
    return {"type": "text", "data": {"text": value}}


def node(content):
    return {"type": "node", "data": {"content": content}}


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
