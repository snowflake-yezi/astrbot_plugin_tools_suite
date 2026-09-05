import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import astrbot_test_env
from package_loader import load_module

try:
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import At, Image, Plain, Reply
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )
except ModuleNotFoundError:
    ASTRBOT_AVAILABLE = False
else:
    ASTRBOT_AVAILABLE = True


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_HANDLERS = {
    "disable_enhanced_forward_records",
    "disable_forward_records",
    "disable_gold",
    "disable_nickname",
    "disable_tools",
    "enable_enhanced_forward_records",
    "enable_forward_records",
    "enable_gold",
    "enable_nickname",
    "enable_tools",
    "forward_record_status",
    "gold",
    "gold_cn",
    "gold_kline",
    "gold_kline_cn",
    "gold_trend_cn",
    "handle_forward_records",
    "handle_nickname_at",
    "set_forward_dedup_days",
    "tool_help",
    "tool_status",
}


class RecordingBot:
    def __init__(self):
        self.actions = []

    async def send_group_msg(self, **payload):
        self.actions.append(("send_group_msg", payload))

    async def send_private_msg(self, **payload):
        self.actions.append(("send_private_msg", payload))


@unittest.skipUnless(ASTRBOT_AVAILABLE, "AstrBot development dependency is unavailable")
class AstrBotIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def test_real_package_import_registers_all_main_module_handlers(self):
        with tempfile.TemporaryDirectory() as runtime_root:
            script = f"""
import asyncio
import inspect
import os
from pathlib import Path

import astrbot_plugin_tool_suite.main as module
from astrbot.core.star.star_handler import star_handlers_registry

handlers = star_handlers_registry.get_handlers_by_module_name(module.__name__)
expected = {EXPECTED_HANDLERS!r}
assert {{handler.handler_name for handler in handlers}} == expected
assert all(handler.handler.__module__ == module.__name__ for handler in handlers)
assert all(inspect.isasyncgenfunction(handler.handler) for handler in handlers)
forward = next(handler for handler in handlers if handler.handler_name == 'handle_forward_records')
assert {{type(item).__name__ for item in forward.event_filters}} == {{
    'EventMessageTypeFilter',
    'PlatformAdapterTypeFilter',
}}
plugin = module.ToolSuitePlugin(object())
expected_path = (
    Path(os.environ['ASTRBOT_ROOT'])
    / 'data'
    / 'plugin_data'
    / 'tool_suite'
    / 'tool_suite.json'
).resolve()
assert plugin.state.path.resolve() == expected_path
asyncio.run(plugin.initialize())
print(len(handlers))
"""
            environment = os.environ.copy()
            environment["ASTRBOT_ROOT"] = runtime_root
            environment["PYTHONPATH"] = str(PACKAGE_ROOT.parent)
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=PACKAGE_ROOT.parent,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.strip().splitlines()[-1], str(len(EXPECTED_HANDLERS))
        )

    async def test_official_aiocqhttp_serializes_historical_reply_and_mention(self):
        bot = RecordingBot()
        for is_group, session_id in ((True, "42"), (False, "8")):
            await AiocqhttpMessageEvent.send_message(
                bot,
                MessageChain([Reply(id="100"), At(qq="7"), Plain("重复提醒")]),
                is_group=is_group,
                session_id=session_id,
            )

        expected = [
            {"type": "reply", "data": {"id": "100"}},
            {"type": "at", "data": {"qq": "7"}},
            {"type": "text", "data": {"text": " "}},
            {"type": "text", "data": {"text": "重复提醒"}},
        ]
        self.assertEqual(
            bot.actions,
            [
                ("send_group_msg", {"group_id": 42, "message": expected}),
                ("send_private_msg", {"user_id": 8, "message": expected}),
            ],
        )

    async def test_duplicate_image_shares_one_message_with_reply_and_mention(self):
        bot = RecordingBot()
        await AiocqhttpMessageEvent.send_message(
            bot,
            MessageChain(
                [
                    Reply(id="100"),
                    At(qq="7"),
                    Image.fromFileSystem(
                        str(PACKAGE_ROOT / "assets" / "duplicate_forward.jpg")
                    ),
                ]
            ),
            is_group=True,
            session_id="42",
        )
        self.assertEqual(len(bot.actions), 1)
        messages = bot.actions[0][1]["message"]
        self.assertEqual(
            [segment["type"] for segment in messages], ["reply", "at", "text", "image"]
        )
        self.assertEqual(messages[0]["data"]["id"], "100")
        self.assertEqual(messages[1]["data"]["qq"], "7")
        self.assertTrue(messages[-1]["data"]["file"].startswith("base64://"))

    def test_metadata_declares_tested_astrbot_and_platform_contracts(self):
        from importlib.metadata import version

        import yaml
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        metadata = yaml.safe_load(
            (PACKAGE_ROOT / "metadata.yaml").read_text(encoding="utf-8")
        )

        self.assertEqual(metadata["support_platforms"], ["aiocqhttp"])
        requirement = SpecifierSet(metadata["astrbot_version"])
        self.assertIn(Version(version("AstrBot")), requirement)

    def test_gold_chart_is_nonblank_and_uses_astrbot_data_tree(self):
        from PIL import Image as PillowImage

        chart_type = load_module("features.gold.chart").GoldTrendChart
        candles = [
            {
                "date": "2026-01-01",
                "open": 500.0,
                "close": 510.0,
                "high": 515.0,
                "low": 495.0,
            },
            {
                "date": "2026-01-02",
                "open": 510.0,
                "close": 505.0,
                "high": 516.0,
                "low": 502.0,
            },
        ]

        path = Path(chart_type().render(candles)).resolve()

        expected_parent = (
            astrbot_test_env.RUNTIME_ROOT / "data" / "temp" / "tool_suite"
        ).resolve()
        self.assertEqual(path.parent, expected_parent)
        with PillowImage.open(path) as image:
            self.assertEqual(image.format, "PNG")
            self.assertGreater(image.width, 100)
            self.assertGreater(image.height, 100)
            self.assertTrue(
                any(low != high for low, high in image.convert("RGB").getextrema())
            )


if __name__ == "__main__":
    unittest.main()
