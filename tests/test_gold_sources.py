import unittest
from unittest.mock import patch

import astrbot_test_env  # noqa: F401
from package_loader import load_module

try:
    import aiohttp  # noqa: F401
    import astrbot.api  # noqa: F401
except ModuleNotFoundError:
    DEPENDENCIES_AVAILABLE = False
else:
    DEPENDENCIES_AVAILABLE = True


@unittest.skipUnless(
    DEPENDENCIES_AVAILABLE,
    "Gold source development dependencies are unavailable",
)
class GoldMarketClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_price_sources_follow_declared_fallback_order(self):
        sources = load_module("features.gold.sources")
        client_type = sources.GoldMarketClient
        expected = {
            "price": 510.0,
            "high": 515.0,
            "low": 500.0,
            "open": 505.0,
            "prev_close": 505.0,
            "time": "2026-01-01 10:00:00",
            "source": "上海黄金交易所",
        }

        class OrderedClient(client_type):
            def __init__(self):
                super().__init__()
                self.calls = []

            async def _fetch_intl_gold(self, _session):
                self.calls.append("international")
                return None

            async def _fetch_sge(self, _session):
                self.calls.append("sge")
                return expected

            async def _fetch_sina(self, _session):
                self.calls.append("sina")
                return None

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

        client = OrderedClient()

        with patch.object(sources.aiohttp, "ClientSession", return_value=FakeSession()):
            result = await client.fetch_price()

        self.assertIs(result, expected)
        self.assertEqual(client.calls, ["international", "sge"])


if __name__ == "__main__":
    unittest.main()
