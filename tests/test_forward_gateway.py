import unittest
from types import SimpleNamespace

import astrbot_test_env  # noqa: F401
from package_loader import load_module

OneBotForwardGateway = load_module("features.forward.gateway").OneBotForwardGateway
message_id = load_module("core.event").message_id
FlattenedForwardNode = load_module("features.forward.parser").FlattenedForwardNode


class FakeBot:
    def __init__(self, action, api=None):
        self._action = action
        self.api = api

    async def call_action(self, name, **params):
        return await self._action(name, params)


class FakeEvent:
    def __init__(self, bot, *, message_id="123", self_id="9"):
        self.bot = bot
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            self_id=self_id,
            raw_message={"message_id": message_id},
        )

    def get_sender_id(self):
        return "7"


class OneBotForwardGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_falls_back_from_message_id_to_id(self):
        calls = []

        async def action(name, params):
            calls.append((name, params))
            if "message_id" in params:
                raise RuntimeError("unsupported parameter")
            return {"messages": []}

        gateway = OneBotForwardGateway(
            FakeEvent(FakeBot(action)), group_id="42", warn=lambda _: None
        )
        self.assertEqual(await gateway.fetch("forward-1"), {"messages": []})
        self.assertEqual(calls[0][1], {"message_id": "forward-1", "self_id": "9"})
        self.assertEqual(calls[1][1], {"id": "forward-1", "self_id": "9"})

    async def test_flattened_batches_preserve_order_content_and_metadata(self):
        for group_id in ("42", None):
            for count in (0, 1, 100, 101, 200, 205):
                with self.subTest(group_id=group_id, count=count):
                    calls = []

                    async def action(name, params, calls=calls):
                        calls.append((name, params))
                        return {"status": "ok", "retcode": 0}

                    gateway = OneBotForwardGateway(
                        FakeEvent(FakeBot(action)),
                        group_id=group_id,
                        warn=lambda _: None,
                    )
                    nodes = tuple(
                        FlattenedForwardNode(
                            content=({"type": "text", "data": {"text": str(index)}},),
                            sender_id=str(index + 1),
                            sender_name=f"成员{index}",
                            timestamp=1000 + index,
                        )
                        for index in range(count)
                    )
                    self.assertIsNone(
                        await gateway.send_flattened(nodes, batch_size=100)
                    )
                    self.assertEqual(
                        [len(params["messages"]) for _, params in calls],
                        [min(100, count - start) for start in range(0, count, 100)],
                    )
                    expected_action = (
                        "send_group_forward_msg"
                        if group_id
                        else "send_private_forward_msg"
                    )
                    target = {"group_id": "42"} if group_id else {"user_id": "7"}
                    sent_nodes = []
                    for name, params in calls:
                        self.assertEqual(name, expected_action)
                        self.assertEqual(params["self_id"], "9")
                        for key, value in target.items():
                            self.assertEqual(params[key], value)
                        sent_nodes.extend(params["messages"])
                    self.assertEqual(
                        sent_nodes,
                        [
                            {
                                "type": "node",
                                "data": {
                                    "user_id": node.sender_id,
                                    "nickname": node.sender_name,
                                    "time": node.timestamp,
                                    "content": list(node.content),
                                },
                            }
                            for node in nodes
                        ],
                    )

    async def test_flattened_send_stops_after_failed_batch(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                calls = []
                warnings = []

                async def action(name, params, calls=calls, raises=raises):
                    calls.append((name, params))
                    if len(calls) == 2:
                        if raises:
                            raise OSError("rejected")
                        return {"status": "failed", "retcode": 100}
                    return {"status": "ok", "retcode": 0}

                gateway = OneBotForwardGateway(
                    FakeEvent(FakeBot(action)), group_id="42", warn=warnings.append
                )
                node = FlattenedForwardNode(
                    content=({"type": "text", "data": {"text": "same"}},),
                    sender_id="7",
                    sender_name="成员",
                    timestamp=0,
                )
                self.assertIsNotNone(
                    await gateway.send_flattened((node,) * 205, batch_size=100)
                )
                self.assertEqual(len(calls), 2)
                self.assertTrue(warnings)

    async def test_recall_falls_back_to_api_object(self):
        api_calls = []
        direct_calls = []

        async def api_action(name, **params):
            api_calls.append((name, params))

        async def direct_action(name, params):
            direct_calls.append((name, params))
            raise RuntimeError("direct action unavailable")

        gateway = OneBotForwardGateway(
            FakeEvent(
                FakeBot(direct_action, api=SimpleNamespace(call_action=api_action))
            ),
            group_id="42",
            warn=lambda _: None,
        )
        self.assertIsNone(await gateway.recall_original())
        self.assertEqual(api_calls, [("delete_msg", {"message_id": 123})])
        self.assertEqual(
            direct_calls, [("delete_msg", {"message_id": 123, "self_id": "9"})]
        )

    async def test_recall_reports_failed_action_without_raising(self):
        warnings = []

        async def action(_name, _params):
            return {"status": "failed", "retcode": 100}

        gateway = OneBotForwardGateway(
            FakeEvent(FakeBot(action)), group_id="42", warn=warnings.append
        )
        self.assertIsNotNone(await gateway.recall_original())
        self.assertEqual(len(warnings), 1)

    async def test_missing_message_id_does_not_call_recall(self):
        async def action(_name, _params):
            raise AssertionError("no message to recall")

        gateway = OneBotForwardGateway(
            FakeEvent(FakeBot(action), message_id=""),
            group_id=None,
            warn=lambda _: None,
        )
        self.assertIsNotNone(await gateway.recall_original())

    def test_message_id_uses_raw_id_then_normalized_event_id(self):
        event = FakeEvent(None, message_id="fallback")
        event.message_obj.raw_message = {"message_id": " -123 "}
        self.assertEqual(message_id(event), -123)
        event.message_obj.raw_message = {}
        self.assertEqual(message_id(event), "fallback")
        event.message_obj.message_id = " "
        self.assertIsNone(message_id(event))


if __name__ == "__main__":
    unittest.main()
