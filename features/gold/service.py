from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from ...core.models import GoldQuote

if TYPE_CHECKING:
    from .chart import GoldTrendChart
    from .sources import GoldMarketClient


class GoldPriceService:
    TREND_DAYS = 15

    def __init__(
        self,
        market: GoldMarketClient | None = None,
        chart: GoldTrendChart | None = None,
        *,
        cache_ttl: int = 90,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if market is None:
            from .sources import GoldMarketClient

            market = GoldMarketClient()
        if chart is None:
            from .chart import GoldTrendChart

            chart = GoldTrendChart()
        self._market = market
        self._chart = chart
        self._cache_ttl = cache_ttl
        self._clock = clock
        self._cache: GoldQuote | None = None
        self._cache_time = 0.0
        self._lock = asyncio.Lock()

    async def get_quote(self) -> GoldQuote | None:
        async with self._lock:
            now = self._clock()
            if self._cache is not None and now - self._cache_time < self._cache_ttl:
                return self._cache
            quote = await self._market.fetch_price()
            if quote is not None:
                self._cache = quote
                self._cache_time = now
            return quote

    async def get_trend_image(self) -> str | None:
        data = await self._market.fetch_trend(self.TREND_DAYS)
        if data is None:
            return None
        return self._chart.render(data)

    @staticmethod
    def format_quote(quote: GoldQuote) -> str:
        price = quote["price"]
        previous_close = quote.get("prev_close") or quote.get("open") or 0
        change = price - previous_close if previous_close else 0
        percentage = (change / previous_close) * 100 if previous_close else 0
        if change > 0:
            trend = "📈 上涨"
        elif change < 0:
            trend = "📉 下跌"
        else:
            trend = "➖ 平盘"

        lines = [
            "📊 【黄金实时行情】",
            "━━━━━━━━━━━━━━━━━━━",
            f"现价: {price:.2f} 元/克",
            f"涨跌: {change:+.2f} ({percentage:+.2f}%) {trend}",
            f"开盘: {quote['open']:.2f}",
            f"昨收: {previous_close:.2f}",
            f"最高: {quote['high']:.2f}",
            f"最低: {quote['low']:.2f}",
            f"时间: {quote['time']}",
            f"来源: {quote['source']}",
        ]
        if "国际" in quote.get("source", ""):
            lines.append("⚠ 价格为国际现货换算，与国内 Au99.99 略有差异")
        return "\n".join(lines)
