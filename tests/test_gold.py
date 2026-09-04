from __future__ import annotations

import time
import unittest

from package_loader import load_module

GoldMarketClient = load_module("features.gold.sources").GoldMarketClient


SAMPLE = [
    {"date": "2026-09-01", "open": 962.5, "close": 957.92, "high": 966.0, "low": 956.0},
    {"date": "2026-09-02", "open": 948.0, "close": 937.5, "high": 948.0, "low": 926.8},
    {"date": "2026-09-03", "open": 939.99, "close": 957.5, "high": 961.0, "low": 937.5},
]


class KlineParserTests(unittest.TestCase):
    def test_eastmoney_rows_are_parsed_sorted_and_bounded(self) -> None:
        rows = [
            "2026-09-03,939.99,957.50,961.00,937.50",
            "broken",
            "2026-09-01,962.50,957.92,966.00,956.00",
            "2026-09-02,948.00,937.50,948.00,926.80",
        ]
        result = GoldMarketClient._parse_eastmoney_klines(rows, 2)
        self.assertIsNotNone(result)
        self.assertEqual([row["date"] for row in result], ["2026-09-02", "2026-09-03"])
        self.assertEqual(result[-1]["close"], 957.5)

    def test_sge_field_order_maps_low_and_high_correctly(self) -> None:
        rows = [
            ["2026-09-02", 948.0, 937.5, 926.8, 948.0],
            ["2026-09-03", 939.99, 957.5, 937.5, 961.0],
        ]
        result = GoldMarketClient._parse_sge_klines(rows, 15)
        self.assertIsNotNone(result)
        self.assertEqual(result[-1], SAMPLE[-1])

    def test_invalid_ohlc_rows_are_discarded(self) -> None:
        rows = [
            "2026-09-01,962.50,957.92,950.00,956.00",
            "2026-09-02,nan,937.50,948.00,926.80",
        ]
        self.assertIsNone(GoldMarketClient._parse_eastmoney_klines(rows, 15))


class KlineFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_sge_is_used_when_eastmoney_fails(self) -> None:
        class Service(GoldMarketClient):
            def __init__(self):
                super().__init__()
                self.calls = []

            async def _fetch_eastmoney_kline(self, session, days):
                self.calls.append("eastmoney")
                return None

            async def _fetch_sge_kline(self, session, days):
                self.calls.append("sge")
                return SAMPLE[-days:]

        service = Service()
        result = await service._fetch_trend(object(), days=3)
        self.assertEqual(service.calls, ["eastmoney", "sge"])
        self.assertEqual(result, SAMPLE)

    async def test_fresh_cache_skips_network(self) -> None:
        class Service(GoldMarketClient):
            async def _fetch_eastmoney_kline(self, session, days):
                raise AssertionError("新鲜缓存不应请求网络")

            async def _fetch_sge_kline(self, session, days):
                raise AssertionError("新鲜缓存不应请求网络")

        service = Service()
        service._kline_cache = SAMPLE
        service._kline_cache_time = time.time()
        self.assertEqual(await service._fetch_trend(object(), days=2), SAMPLE[-2:])

    async def test_recent_stale_cache_is_used_after_both_sources_fail(self) -> None:
        class Service(GoldMarketClient):
            async def _fetch_eastmoney_kline(self, session, days):
                return None

            async def _fetch_sge_kline(self, session, days):
                return None

        service = Service()
        service._kline_cache = SAMPLE
        service._kline_cache_time = time.time() - 3600
        self.assertEqual(await service._fetch_trend(object(), days=3), SAMPLE)

    async def test_expired_cache_is_not_used(self) -> None:
        class Service(GoldMarketClient):
            async def _fetch_eastmoney_kline(self, session, days):
                return None

            async def _fetch_sge_kline(self, session, days):
                return None

        service = Service()
        service._kline_cache = SAMPLE
        service._kline_cache_time = time.time() - service.KLINE_STALE_CACHE_TTL - 1
        self.assertIsNone(await service._fetch_trend(object(), days=3))

    async def test_sge_realtime_mapping_uses_close_as_price(self) -> None:
        class Service(GoldMarketClient):
            async def _fetch_sge_rows(self, session):
                return [
                    ["2026-09-02", 948.0, 937.5, 926.8, 948.0],
                    ["2026-09-03", 939.99, 957.5, 937.5, 961.0],
                ]

        result = await Service()._fetch_sge(object())
        self.assertEqual(result["price"], 957.5)
        self.assertEqual(result["prev_close"], 937.5)
        self.assertEqual(result["high"], 961.0)
        self.assertEqual(result["low"], 937.5)

    async def test_sge_realtime_tolerates_invalid_previous_row(self) -> None:
        class Service(GoldMarketClient):
            async def _fetch_sge_rows(self, session):
                return [
                    ["invalid"],
                    ["2026-09-03", 939.99, 957.5, 937.5, 961.0],
                ]

        result = await Service()._fetch_sge(object())
        self.assertEqual(result["price"], 957.5)
        self.assertEqual(result["prev_close"], 939.99)


if __name__ == "__main__":
    unittest.main()
