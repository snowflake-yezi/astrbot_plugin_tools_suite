import unittest

from package_loader import load_module

GoldPriceService = load_module("features.gold.service").GoldPriceService


def quote(price=510.0):
    return {
        "price": price,
        "high": 515.0,
        "low": 500.0,
        "open": 505.0,
        "prev_close": 505.0,
        "time": "2026-01-01 10:00:00",
        "source": "上海黄金交易所",
    }


class FakeMarket:
    def __init__(self, prices=None, trend=None):
        self.prices = list(prices or [])
        self.trend = trend
        self.price_calls = 0
        self.trend_days = []

    async def fetch_price(self):
        self.price_calls += 1
        return self.prices.pop(0) if self.prices else None

    async def fetch_trend(self, days):
        self.trend_days.append(days)
        return self.trend


class FakeChart:
    def __init__(self):
        self.rendered = []

    def render(self, data):
        self.rendered.append(data)
        return "trend.png"


class GoldPriceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_quote_cache_expires_after_ttl(self):
        now = [100.0]
        market = FakeMarket([quote(510.0), quote(520.0)])
        service = GoldPriceService(
            market,
            FakeChart(),
            cache_ttl=90,
            clock=lambda: now[0],
        )

        first = await service.get_quote()
        now[0] = 150.0
        cached = await service.get_quote()
        now[0] = 191.0
        refreshed = await service.get_quote()

        self.assertEqual(first["price"], 510.0)
        self.assertIs(cached, first)
        self.assertEqual(refreshed["price"], 520.0)
        self.assertEqual(market.price_calls, 2)

    async def test_failed_quote_is_not_cached(self):
        market = FakeMarket([None, quote()])
        service = GoldPriceService(market, FakeChart())

        self.assertIsNone(await service.get_quote())
        self.assertIsNotNone(await service.get_quote())
        self.assertEqual(market.price_calls, 2)

    async def test_trend_uses_standard_window_and_chart(self):
        candles = [
            {"date": "2026-01-01", "open": 500.0, "close": 510.0, "high": 515.0, "low": 495.0}
        ]
        market = FakeMarket(trend=candles)
        chart = FakeChart()
        service = GoldPriceService(market, chart)

        path = await service.get_trend_image()

        self.assertEqual(path, "trend.png")
        self.assertEqual(market.trend_days, [service.TREND_DAYS])
        self.assertEqual(chart.rendered, [candles])

    def test_format_quote_reports_change_and_source(self):
        text = GoldPriceService.format_quote(quote())

        self.assertIn("现价: 510.00 元/克", text)
        self.assertIn("涨跌: +5.00 (+0.99%)", text)
        self.assertIn("来源: 上海黄金交易所", text)


if __name__ == "__main__":
    unittest.main()
