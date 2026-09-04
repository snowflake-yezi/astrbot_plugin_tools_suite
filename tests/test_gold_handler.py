import tempfile
import unittest
from pathlib import Path

import astrbot_test_env  # noqa: F401
from package_loader import load_module

try:
    import astrbot.api  # noqa: F401
except ModuleNotFoundError:
    ASTRBOT_AVAILABLE = False
else:
    ASTRBOT_AVAILABLE = True


class FakeEvent:
    def get_group_id(self):
        return "42"

    def plain_result(self, text):
        return ("plain", text)

    def image_result(self, path):
        return ("image", path)


class FakeGoldService:
    def __init__(self):
        self.quote_calls = 0
        self.trend_calls = 0

    async def get_quote(self):
        self.quote_calls += 1
        return {"price": 510.0}

    @staticmethod
    def format_quote(quote):
        return f"price={quote['price']}"

    async def get_trend_image(self):
        self.trend_calls += 1
        return "trend.png"


@unittest.skipUnless(ASTRBOT_AVAILABLE, "AstrBot development dependency is unavailable")
class GoldHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        handler_type = load_module("features.gold.handler").GoldHandler
        state_type = load_module("core.state").PluginStateStore
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        state = state_type(Path(self.temporary_directory.name) / "state.json")
        self.service = FakeGoldService()
        self.handler = handler_type(state, state.save, self.service)
        self.event = FakeEvent()

    async def test_disabled_feature_does_not_query_provider(self):
        self.assertIsNone(await self.handler.price(self.event))
        self.assertEqual(self.service.quote_calls, 0)

    async def test_enabled_feature_returns_quote_and_chart_results(self):
        self.handler.set_enabled(self.event, True)

        price = await self.handler.price(self.event)
        trend = await self.handler.trend(self.event)

        self.assertEqual(price, ("plain", "price=510.0"))
        self.assertEqual(trend, ("image", "trend.png"))
        self.assertEqual(self.service.quote_calls, 1)
        self.assertEqual(self.service.trend_calls, 1)


if __name__ == "__main__":
    unittest.main()
