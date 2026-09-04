import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from package_loader import load_module

try:
    import astrbot.api.message_components  # noqa: F401
except ModuleNotFoundError:
    ASTRBOT_AVAILABLE = False
else:
    ASTRBOT_AVAILABLE = True


class FakeEvent:
    def __init__(self, messages, *, group_id="42", sender_id="7", self_id="9"):
        self._messages = messages
        self._group_id = group_id
        self._sender_id = sender_id
        self._self_id = self_id
        self.message_str = ""
        self.message_obj = SimpleNamespace(
            message=messages,
            message_id="123",
            self_id=self_id,
            raw_message={},
        )
        self.llm_enabled = True

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


class RecordingGateway:
    instances = []

    def __init__(self, event, **_):
        self.event = event
        self.recalled = False
        self.sent = []
        self.instances.append(self)

    async def fetch(self, _forward_id):
        raise AssertionError("inline fixture must not fetch")

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
        path = Path(self.temporary_directory.name) / "state.json"
        self.state = state_module.PluginStateStore(path)
        self.handler = handler_module.MergedForwardHandler(
            self.state,
            clock=lambda: 1000,
            warn=lambda _: None,
            gateway_factory=RecordingGateway,
            image_path=Path(self.temporary_directory.name) / "missing.jpg",
        )
        RecordingGateway.instances.clear()

    def enable(self, *, enhanced=False):
        data = self.state.load()
        scope = self.state.scope(data, "group:42")
        scope["forward_enabled"] = True
        scope["forward_enhanced_enabled"] = enhanced
        self.state.save(data)

    @staticmethod
    def inline_forward(content):
        return [{"type": "forward", "data": {"content": content}}]

    async def test_basic_mode_returns_official_reply_chain_and_persists(self):
        self.enable()
        event = FakeEvent(
            self.inline_forward([{"type": "text", "data": {"text": "hello"}}])
        )

        result = await self.handler.handle(event)

        self.assertEqual(result[0], "chain")
        self.assertEqual([type(item).__name__ for item in result[1]], ["Reply", "Plain"])
        self.assertFalse(event.llm_enabled)
        records = self.state.load()["scopes"]["group:42"]["forward_records"]
        self.assertEqual(len(records), 1)

    async def test_enhanced_exact_duplicate_recalls_without_reply(self):
        self.enable(enhanced=True)
        messages = self.inline_forward([{"type": "text", "data": {"text": "same"}}])

        first = await self.handler.handle(FakeEvent(messages))
        second = await self.handler.handle(FakeEvent(messages))

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertFalse(RecordingGateway.instances[0].recalled)
        self.assertTrue(RecordingGateway.instances[1].recalled)

    async def test_enhanced_nested_latest_record_is_flattened(self):
        self.enable(enhanced=True)
        nested = self.inline_forward(
            [
                {
                    "type": "node",
                    "data": {
                        "content": self.inline_forward(
                            [{"type": "text", "data": {"text": "nested"}}]
                        )
                    },
                }
            ]
        )

        result = await self.handler.handle(FakeEvent(nested))

        self.assertIsNone(result)
        sent = RecordingGateway.instances[0].sent
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent[0][0]), 1)
        self.assertEqual(sent[0][1], self.handler.SEND_BATCH_SIZE)


if __name__ == "__main__":
    unittest.main()
