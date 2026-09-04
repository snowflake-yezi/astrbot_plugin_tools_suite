import unittest
from types import SimpleNamespace

from package_loader import load_module

gateway = load_module("features.forward.gateway")
parser = load_module("features.forward.parser")
FlattenedForwardNode = parser.FlattenedForwardNode
OneBotForwardGateway = gateway.OneBotForwardGateway


class FakeBot:
    def __init__(self, action, api=None):
        self._action = action
        self.api = api

    async def call_action(self, name, **params):
        return await self._action(name, params)


class FakeEvent:
    def __init__(self, bot, *, sender_id="7", message_id="123", self_id="9"):
        self.bot = bot
        self._sender_id = sender_id
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            self_id=self_id,
            raw_message={"message_id": message_id},
        )

    def get_sender_id(self):
        return self._sender_id


def flattened_node(value):
    return FlattenedForwardNode(
        content_hash=f"hash-{value}",
        component_hashes=(f"component-{value}",),
        content=({"type": "text", "data": {"text": value}},),
        sender_id="1",
        sender_name="成员",
        timestamp=100,
    )


class OneBotForwardGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_falls_back_from_message_id_to_id(self):
        calls = []

        async def action(name, params):
            calls.append((name, params))
            if "message_id" in params:
                raise RuntimeError("unsupported parameter")
            return {"messages": []}

        gateway = OneBotForwardGateway(
            FakeEvent(FakeBot(action)),
            group_id="42",
            warn=lambda _: None,
        )

        result = await gateway.fetch("forward-1")

        self.assertEqual(result, {"messages": []})
        self.assertEqual(calls[0][1], {"message_id": "forward-1", "self_id": "9"})
        self.assertEqual(calls[1][1], {"id": "forward-1", "self_id": "9"})

    async def test_group_send_uses_node_only_batches(self):
        calls = []

        async def action(name, params):
            calls.append((name, params))
            return {"message_id": 1}

        gateway = OneBotForwardGateway(
            FakeEvent(FakeBot(action)),
            group_id="42",
            warn=lambda _: None,
        )

        error = await gateway.send_flattened(
            [flattened_node("a"), flattened_node("b"), flattened_node("c")],
            batch_size=2,
        )

        self.assertIsNone(error)
        self.assertEqual([name for name, _ in calls], [
            "send_group_forward_msg",
            "send_group_forward_msg",
        ])
        self.assertEqual([len(params["messages"]) for _, params in calls], [2, 1])
        self.assertTrue(all(params["group_id"] == "42" for _, params in calls))
        self.assertTrue(all(params["self_id"] == "9" for _, params in calls))
        self.assertTrue(
            all(
                node["type"] == "node"
                for _, params in calls
                for node in params["messages"]
            )
        )

    async def test_send_reports_failed_action(self):
        warnings = []

        async def action(_name, _params):
            return {"status": "failed", "retcode": 100}

        gateway = OneBotForwardGateway(
            FakeEvent(FakeBot(action)),
            group_id=None,
            warn=warnings.append,
        )

        error = await gateway.send_flattened(
            [flattened_node("a")],
            batch_size=100,
        )

        self.assertEqual(error, "聊天记录平铺发送失败，请查看机器人日志。")
        self.assertEqual(len(warnings), 1)

    async def test_recall_falls_back_to_api_object(self):
        api_calls = []
        direct_calls = []

        async def api_action(name, **params):
            api_calls.append((name, params))

        async def direct_action(name, params):
            direct_calls.append((name, params))
            raise RuntimeError("direct action unavailable")

        api = SimpleNamespace(call_action=api_action)
        gateway = OneBotForwardGateway(
            FakeEvent(FakeBot(direct_action, api=api)),
            group_id="42",
            warn=lambda _: None,
        )

        error = await gateway.recall_original()

        self.assertIsNone(error)
        self.assertEqual(api_calls, [("delete_msg", {"message_id": 123})])
        self.assertEqual(
            direct_calls,
            [("delete_msg", {"message_id": 123, "self_id": "9"})],
        )


if __name__ == "__main__":
    unittest.main()
