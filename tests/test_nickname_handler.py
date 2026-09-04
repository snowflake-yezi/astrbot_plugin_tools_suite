import tempfile
import unittest
from pathlib import Path

import astrbot_test_env  # noqa: F401
from package_loader import load_module

try:
    from astrbot.api.message_components import At, Plain
except ModuleNotFoundError:
    ASTRBOT_AVAILABLE = False
else:
    ASTRBOT_AVAILABLE = True


class FakeEvent:
    def __init__(self, messages, *, group_id="42"):
        self._messages = messages
        self._group_id = group_id
        self.message_str = ""

    def get_messages(self):
        return self._messages

    def get_message_str(self):
        return self.message_str

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return "8"

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)


@unittest.skipUnless(ASTRBOT_AVAILABLE, "AstrBot development dependency is unavailable")
class NicknameHandlerTests(unittest.TestCase):
    def setUp(self):
        handler_type = load_module("features.nickname.handler").NicknameHandler
        state_type = load_module("core.state").PluginStateStore
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        state = state_type(Path(self.temporary_directory.name) / "state.json")
        self.handler = handler_type(state, state.save)
        self.handler.set_enabled(FakeEvent([]), True)

    def test_binding_and_at_command_use_official_components(self):
        bound = self.handler.handle(FakeEvent([At(qq="7"), Plain(" 昵称 团队")]))

        result = self.handler.handle(FakeEvent([Plain("at团队 hello")]))

        self.assertEqual(bound, ("plain", "昵称“团队”已绑定到该用户。"))
        self.assertEqual(result[0], "chain")
        self.assertEqual([type(item).__name__ for item in result[1]], ["At", "Plain"])
        self.assertEqual(str(result[1][0].qq), "7")
        self.assertEqual(result[1][1].text, " hello")


if __name__ == "__main__":
    unittest.main()
