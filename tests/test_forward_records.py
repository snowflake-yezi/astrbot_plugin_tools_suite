import unittest

from forward_records import ForwardRecordExpander


def text(value):
    return {"type": "text", "data": {"text": value}}


def node(content):
    return {"type": "node", "data": {"content": content}}


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


if __name__ == "__main__":
    unittest.main()
