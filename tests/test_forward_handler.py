import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

import astrbot_test_env  # noqa: F401
from package_loader import load_module

try:
    from astrbot.api.message_components import At, Image, Plain, Reply
except ModuleNotFoundError:
    ASTRBOT_AVAILABLE = False
else:
    ASTRBOT_AVAILABLE = True


class FakeEvent:
    def __init__(
        self, messages, *, group_id="42", sender_id="7", self_id="9", message_id="123"
    ):
        self._messages = messages
        self._group_id = group_id
        self._sender_id = sender_id
        self._self_id = self_id
        self.message_str = ""
        self.message_obj = SimpleNamespace(
            message=messages,
            message_id=message_id,
            self_id=self_id,
            raw_message={},
        )
        self.llm_enabled = True
        self.sent = []
        self.send_error = None

    def get_messages(self):
        return self._messages

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    def get_self_id(self):
        return self._self_id

    def should_call_llm(self, enabled):
        self.llm_enabled = enabled

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)

    async def send(self, result):
        if self.send_error:
            raise self.send_error
        self.sent.append(result)


class RecordingGateway:
    instances: ClassVar[list["RecordingGateway"]] = []

    def __init__(self, event, **_):
        self.event = event
        self.recalled = False
        self.sent = []
        self.instances.append(self)

    async def fetch(self, _forward_id):
        raise RuntimeError("forward unavailable")

    async def recall_original(self):
        self.recalled = True
        return None

    async def send_flattened(self, nodes, *, batch_size):
        self.sent.append((nodes, batch_size))
        return None


@unittest.skipUnless(ASTRBOT_AVAILABLE, "AstrBot development dependency is unavailable")
class MergedForwardHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        handler_module = load_module("features.forward.handler")
        state_module = load_module("core.state")
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.state = state_module.PluginStateStore(
            Path(self.temporary_directory.name) / "state.json"
        )
        self.handler_type = handler_module.MergedForwardHandler
        self.now = 1000
        self.warnings = []
        self.handler = self.handler_type(
            self.state,
            clock=lambda: self.now,
            warn=self.warnings.append,
            gateway_factory=RecordingGateway,
        )
        RecordingGateway.instances.clear()
        self.handler.set_enabled(FakeEvent([]), True)

    @staticmethod
    def forward(*values, nested=False):
        nodes = [
            {
                "type": "node",
                "data": {"content": [{"type": "text", "data": {"text": value}}]},
            }
            for value in values
        ]
        forward = {"type": "forward", "data": {"content": nodes}}
        if nested:
            return [{"type": "forward", "data": {"content": [forward]}}]
        return [forward]

    async def seed(self, *values, message_id="100", group_id="42"):
        event = FakeEvent(
            self.forward(*values), message_id=message_id, group_id=group_id
        )
        await self.handler.handle(event)
        self.now += 1
        return event

    def assert_reminder(self, result, historical_id="100", sender_id="7", image=False):
        self.assertEqual(result[0], "chain")
        chain = result[1]
        self.assertEqual(
            [type(item) for item in chain], [Reply, At, Image if image else Plain]
        )
        self.assertEqual(str(chain[0].id), historical_id)
        self.assertEqual(str(chain[1].qq), sender_id)
        return chain[-1]

    async def test_latest_records_are_silent_with_enhanced_disabled(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                event = FakeEvent(self.forward(str(nested), nested=nested))
                self.assertIsNone(await self.handler.handle(event))
                self.assertEqual(event.sent, [])
                self.assertFalse(event.llm_enabled)
                self.assertFalse(RecordingGateway.instances[-1].recalled)
                self.assertEqual(RecordingGateway.instances[-1].sent, [])
        self.assertEqual(
            len(self.state.load()["scopes"]["group:42"]["forward_records"]), 2
        )

    async def test_enhanced_latest_nested_record_is_flattened_without_notice(self):
        self.handler.set_enhanced(FakeEvent([]), True)
        event = FakeEvent(self.forward("a", "b", "b", nested=True))

        self.assertIsNone(await self.handler.handle(event))

        gateway = RecordingGateway.instances[-1]
        self.assertEqual(len(gateway.sent), 1)
        nodes, batch_size = gateway.sent[0]
        self.assertEqual(
            [item.content[0]["data"]["text"] for item in nodes], ["a", "b", "b"]
        )
        self.assertEqual(batch_size, 100)
        self.assertEqual(event.sent, [])
        self.assertFalse(gateway.recalled)

    async def test_enhanced_partial_keeps_all_content_and_still_reminds(self):
        await self.seed("old")
        self.handler.set_enhanced(FakeEvent([]), True)
        event = FakeEvent(
            self.forward("old", "new", "new", nested=True), message_id="200"
        )

        result = await self.handler.handle(event)

        self.assert_reminder(result)
        nodes, _ = RecordingGateway.instances[-1].sent[0]
        self.assertEqual(
            [item.content[0]["data"]["text"] for item in nodes], ["old", "new", "new"]
        )

    async def test_enhanced_does_not_rebuild_single_layer_records(self):
        self.handler.set_enhanced(FakeEvent([]), True)
        event = FakeEvent(self.forward("single"))
        self.assertIsNone(await self.handler.handle(event))
        self.assertEqual(RecordingGateway.instances[-1].sent, [])

    async def test_enhanced_exact_recalls_and_reminds_without_resend(self):
        await self.seed("same")
        self.handler.set_enhanced(FakeEvent([]), True)
        event = FakeEvent(self.forward("same", nested=True), message_id="200")
        self.assertIsNone(await self.handler.handle(event))
        gateway = RecordingGateway.instances[-1]
        self.assertTrue(gateway.recalled)
        self.assertEqual(gateway.sent, [])
        self.assert_reminder(event.sent[0], image=True)

    async def test_disabling_enhanced_preserves_base_reminders(self):
        self.handler.set_enabled(FakeEvent([]), False)
        self.handler.set_enhanced(FakeEvent([]), True)
        self.assertTrue(self.state.load()["scopes"]["group:42"]["forward_enabled"])
        self.handler.set_enhanced(FakeEvent([]), False)
        await self.seed("old")
        event = FakeEvent(self.forward("old", "new", nested=True), message_id="200")
        self.assert_reminder(await self.handler.handle(event))
        self.assertEqual(RecordingGateway.instances[-1].sent, [])

    async def test_flatten_failure_does_not_suppress_partial_reminder(self):
        class FailingSend(RecordingGateway):
            async def send_flattened(self, nodes, *, batch_size):
                return "send failed"

        self.handler._gateway_factory = FailingSend
        self.handler.set_enhanced(FakeEvent([]), True)
        await self.seed("old")
        event = FakeEvent(self.forward("old", "new", nested=True), message_id="200")
        self.assert_reminder(await self.handler.handle(event))
        self.assertTrue(any("send failed" in warning for warning in self.warnings))

    async def test_partial_quotes_latest_matching_history_and_mentions_current_sender(
        self,
    ):
        await self.seed("old", message_id="100")
        await self.seed("recent", message_id="101")
        event = FakeEvent(
            self.forward("old", "recent", "new"), message_id="102", sender_id="8"
        )

        result = await self.handler.handle(event)

        text = self.assert_reminder(result, historical_id="101", sender_id="8")
        self.assertEqual(text.text.strip(), self.handler.PARTIAL_TEXT)
        self.assertEqual(event.sent, [])
        self.assertFalse(RecordingGateway.instances[-1].recalled)

    async def test_exact_recalls_and_sends_historical_reply_mention_and_image(self):
        await self.seed("same")
        event = FakeEvent(self.forward("same"), message_id="200", sender_id="8")

        self.assertIsNone(await self.handler.handle(event))

        self.assertTrue(RecordingGateway.instances[-1].recalled)
        self.assertEqual(len(event.sent), 1)
        self.assert_reminder(event.sent[0], sender_id="8", image=True)
        record = self.state.load()["scopes"]["group:42"]["forward_records"][0]
        self.assertEqual(record["message_id"], "100")
        self.assertEqual(record["seen_at"], 1000)

    async def test_exact_reminder_survives_recall_failure(self):
        class FailingRecall(RecordingGateway):
            async def recall_original(self):
                self.recalled = True
                return "permission denied"

        self.handler._gateway_factory = FailingRecall
        await self.seed("same")
        event = FakeEvent(self.forward("same"), message_id="200")

        self.assertIsNone(await self.handler.handle(event))
        self.assert_reminder(event.sent[0], image=True)
        self.assertTrue(any("permission denied" in item for item in self.warnings))

    async def test_missing_image_uses_text_fallback(self):
        self.handler._image_path = Path(self.temporary_directory.name) / "missing.jpg"
        await self.seed("same")
        event = FakeEvent(self.forward("same"), message_id="200")

        result = await self.handler.handle(event)

        self.assertEqual(
            self.assert_reminder(result).text.strip(), self.handler.EXACT_TEXT
        )
        self.assertTrue(RecordingGateway.instances[-1].recalled)
        self.assertEqual(event.sent, [])

    async def test_image_send_failure_uses_text_fallback(self):
        await self.seed("same")
        event = FakeEvent(self.forward("same"), message_id="200")
        event.send_error = OSError("image rejected")

        result = await self.handler.handle(event)

        self.assertEqual(
            self.assert_reminder(result).text.strip(), self.handler.EXACT_TEXT
        )
        self.assertTrue(RecordingGateway.instances[-1].recalled)
        self.assertEqual(event.sent, [])

    async def test_image_construction_failure_uses_text_fallback(self):
        await self.seed("same")
        event = FakeEvent(self.forward("same"), message_id="200")
        with patch.object(Image, "fromFileSystem", side_effect=OSError("unreadable")):
            result = await self.handler.handle(event)
        self.assertEqual(
            self.assert_reminder(result).text.strip(), self.handler.EXACT_TEXT
        )
        self.assertTrue(RecordingGateway.instances[-1].recalled)

    async def test_history_without_message_id_does_not_quote_current_message(self):
        await self.seed("same")
        data = self.state.load()
        del data["scopes"]["group:42"]["forward_records"][0]["message_id"]
        self.state.save(data)
        event = FakeEvent(self.forward("same"), message_id="200")

        self.assertIsNone(await self.handler.handle(event))

        self.assertEqual([type(item) for item in event.sent[0][1]], [At, Image])
        self.assertTrue(RecordingGateway.instances[-1].recalled)

    async def test_failed_or_empty_expansion_is_silent_and_not_stored(self):
        for candidate in (
            {"type": "forward", "data": {"id": "unavailable"}},
            {"type": "forward", "data": {}},
            {"type": "forward", "data": {"content": []}},
        ):
            with self.subTest(candidate=candidate):
                event = FakeEvent([candidate])
                self.assertIsNone(await self.handler.handle(event))
                self.assertFalse(event.llm_enabled)
                self.assertEqual(event.sent, [])
                self.assertFalse(RecordingGateway.instances[-1].recalled)
                self.assertEqual(RecordingGateway.instances[-1].sent, [])
        self.assertEqual(self.state.load()["scopes"]["group:42"]["forward_records"], [])

    async def test_unexpected_recognition_error_is_silent(self):
        parser = load_module("features.forward.parser")
        event = FakeEvent(self.forward("bad"))
        with patch.object(
            parser.ForwardRecordExpander, "expand", side_effect=ValueError("bad data")
        ):
            self.assertIsNone(await self.handler.handle(event))
        self.assertEqual(event.sent, [])
        self.assertEqual(self.state.load()["scopes"]["group:42"]["forward_records"], [])

    async def test_disabled_self_and_plain_messages_are_ignored(self):
        events = [FakeEvent(self.forward("self"), sender_id="9"), FakeEvent([])]
        self.handler.set_enabled(FakeEvent([]), False)
        events.append(FakeEvent(self.forward("disabled")))
        for event in events:
            self.assertIsNone(await self.handler.handle(event))
            self.assertTrue(event.llm_enabled)
            self.assertEqual(event.sent, [])
        self.assertEqual(RecordingGateway.instances, [])

    async def test_history_is_scope_local_including_private_messages(self):
        await self.seed("same")
        for group in ("43", None):
            event = FakeEvent(self.forward("same"), group_id=group)
            self.handler.set_enabled(event, True)
            self.assertIsNone(await self.handler.handle(event))
            self.assertEqual(event.sent, [])
            self.assertFalse(RecordingGateway.instances[-1].recalled)

    async def test_concurrent_duplicates_keep_first_message_as_reference(self):
        first = FakeEvent(self.forward("same"), message_id="100")
        second = FakeEvent(self.forward("same"), message_id="200")
        await asyncio.gather(self.handler.handle(first), self.handler.handle(second))
        self.assertEqual(first.sent, [])
        self.assert_reminder(second.sent[0], image=True)
        self.assertFalse(RecordingGateway.instances[0].recalled)
        self.assertTrue(RecordingGateway.instances[1].recalled)

    async def test_raw_message_id_is_used_for_history(self):
        event = FakeEvent(self.forward("same"), message_id="fallback")
        event.message_obj.raw_message = {"message_id": -123}
        await self.handler.handle(event)
        record = self.state.load()["scopes"]["group:42"]["forward_records"][0]
        self.assertEqual(record["message_id"], "-123")


if __name__ == "__main__":
    unittest.main()
