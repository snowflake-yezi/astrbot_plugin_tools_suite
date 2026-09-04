from __future__ import annotations

import asyncio
import datetime as dt
import math
import random
import re
import time

import aiohttp
from astrbot.api import logger

from ...core.models import GoldCandle, GoldQuote

_TROY_OZ_TO_GRAM = 31.1035
_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if extra:
        headers.update(extra)
    return headers


class GoldMarketClient:
    """按优先级查询黄金行情，并缓存汇率与走势图数据。"""

    KLINE_FRESH_CACHE_TTL = 300
    KLINE_STALE_CACHE_TTL = 86400

    def __init__(self) -> None:
        self._fx_cache: float | None = None
        self._fx_cache_time = 0.0
        self._kline_cache: list[GoldCandle] | None = None
        self._kline_cache_time = 0.0
        self._kline_lock = asyncio.Lock()

    async def fetch_price(self) -> GoldQuote | None:
        async with aiohttp.ClientSession() as session:
            quote = await self._fetch_intl_gold(session)
            if quote is None:
                logger.info("[gold] Intl gold unavailable, trying SGE...")
                quote = await self._fetch_sge(session)
            if quote is None:
                logger.info("[gold] SGE unavailable, trying Sina...")
                quote = await self._fetch_sina(session)
            return quote

    async def fetch_trend(self, days: int) -> list[GoldCandle] | None:
        async with aiohttp.ClientSession() as session:
            return await self._fetch_trend(session, days)

    async def _fetch_intl_gold(
        self,
        session: aiohttp.ClientSession,
    ) -> GoldQuote | None:
        try:
            async with session.get(
                "https://data-asg.goldprice.org/dbXRates/USD",
                headers=_headers({"Referer": "https://goldprice.org/"}),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    logger.warning(f"[gold] GoldPrice.org HTTP {response.status}")
                    return None
                payload = await response.json(content_type=None)
        except Exception as exc:
            logger.warning(f"[gold] GoldPrice.org request failed: {exc}")
            return None

        items = (payload or {}).get("items", [])
        if not items:
            return None
        try:
            xau_usd = float(items[0]["xauPrice"])
            change = float(items[0].get("chgXau", 0) or 0)
        except (KeyError, TypeError, ValueError):
            return None

        usd_cny = await self._get_usd_cny(session)
        if usd_cny is None:
            return None
        timestamp = (payload.get("ts") or 0) / 1000
        quote_time = (
            dt.datetime.fromtimestamp(timestamp, tz=dt.UTC).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
            if timestamp
            else dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        price = (xau_usd * usd_cny) / _TROY_OZ_TO_GRAM
        previous_close = ((xau_usd + change) * usd_cny) / _TROY_OZ_TO_GRAM
        return {
            "price": round(price, 2),
            "high": round(price * 1.005, 2),
            "low": round(price * 0.995, 2),
            "open": round(previous_close, 2),
            "prev_close": round(previous_close, 2),
            "time": quote_time,
            "source": "GoldPrice.org (国际现货)",
        }

    async def _get_usd_cny(
        self,
        session: aiohttp.ClientSession,
    ) -> float | None:
        now = dt.datetime.now().timestamp()
        if self._fx_cache and now - self._fx_cache_time < 3600:
            return self._fx_cache

        try:
            async with session.get(
                "https://open.er-api.com/v6/latest/USD",
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=8),
            ) as response:
                if response.status != 200:
                    logger.warning(f"[gold] FX API HTTP {response.status}")
                    return None
                payload = await response.json(content_type=None)
        except Exception as exc:
            logger.warning(f"[gold] FX API request failed: {exc}")
            return None

        rate = ((payload or {}).get("rates") or {}).get("CNY")
        try:
            self._fx_cache = float(rate)
        except (TypeError, ValueError):
            return None
        self._fx_cache_time = now
        return self._fx_cache

    async def _fetch_sge_rows(
        self,
        session: aiohttp.ClientSession,
    ) -> list[list] | None:
        try:
            async with session.post(
                "https://www.sge.com.cn/graph/Dailyhq",
                data={"instid": "Au99.99"},
                headers=_headers(
                    {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": "https://www.sge.com.cn",
                        "Referer": "https://www.sge.com.cn/",
                    }
                ),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    logger.warning(f"[gold] SGE HTTP {response.status}")
                    return None
                payload = await response.json(content_type=None)
        except Exception as exc:
            logger.warning(f"[gold] SGE request failed: {exc}")
            return None

        if not isinstance(payload, dict):
            return None
        rows = payload.get("time", [])
        if not isinstance(rows, list) or not rows:
            return None
        return rows

    async def _fetch_sge(self, session: aiohttp.ClientSession) -> GoldQuote | None:
        rows = await self._fetch_sge_rows(session)
        if not rows:
            return None
        last = rows[-1]
        if not isinstance(last, list) or len(last) < 5:
            return None
        try:
            # 上金所日线顺序为日期、开盘、收盘、最低、最高。
            open_price = float(last[1])
            close_price = float(last[2])
            low_price = float(last[3])
            high_price = float(last[4])
        except (TypeError, ValueError):
            return None
        previous_close = open_price
        if len(rows) > 1:
            try:
                previous_close = float(rows[-2][2])
            except (IndexError, TypeError, ValueError):
                pass
        if not self._valid_ohlc(open_price, close_price, high_price, low_price):
            return None
        return {
            "price": round(close_price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "open": round(open_price, 2),
            "prev_close": round(previous_close, 2),
            "time": str(last[0]),
            "source": "上海黄金交易所",
        }

    async def _fetch_sina(
        self,
        session: aiohttp.ClientSession,
    ) -> GoldQuote | None:
        timestamp = int(dt.datetime.now().timestamp() * 1000)
        url = f"https://hq.sinajs.cn/rn={timestamp}&list=gds_AU9999"
        try:
            async with session.get(
                url,
                headers=_headers({"Referer": "https://finance.sina.com.cn/"}),
                timeout=aiohttp.ClientTimeout(total=8),
            ) as response:
                if response.status != 200:
                    logger.warning(f"[gold] Sina HTTP {response.status}")
                    return None
                raw = await response.read()
        except Exception as exc:
            logger.warning(f"[gold] Sina request failed: {exc}")
            return None

        match = re.search(r'"(.*?)"', raw.decode("gbk", errors="ignore"))
        if match is None:
            return None
        parts = match.group(1).split(",")
        if len(parts) < 13:
            return None
        try:
            price = float(parts[0])
            high = float(parts[4])
            low = float(parts[5])
            previous_close = float(parts[7])
            opening = float(parts[8])
        except ValueError:
            return None
        if price <= 0:
            return None
        return {
            "price": price,
            "high": high,
            "low": low,
            "open": opening,
            "prev_close": previous_close,
            "time": f"{parts[12]} {parts[6]}",
            "source": "新浪财经",
        }

    @staticmethod
    def _valid_ohlc(
        open_price: float, close_price: float, high: float, low: float
    ) -> bool:
        values = (open_price, close_price, high, low)
        return (
            all(math.isfinite(value) and value > 0 for value in values)
            and high >= max(open_price, close_price)
            and low <= min(open_price, close_price)
            and high >= low
        )

    @classmethod
    def _normalize_kline_records(
        cls, records: list[dict], days: int
    ) -> list[GoldCandle] | None:
        unique: dict[str, GoldCandle] = {}
        for record in records:
            date_text = str(record.get("date") or "")
            try:
                dt.date.fromisoformat(date_text)
                open_price = float(record["open"])
                close_price = float(record["close"])
                high = float(record["high"])
                low = float(record["low"])
            except (KeyError, TypeError, ValueError):
                continue
            if not cls._valid_ohlc(open_price, close_price, high, low):
                continue
            unique[date_text] = {
                "date": date_text,
                "open": open_price,
                "close": close_price,
                "high": high,
                "low": low,
            }
        normalized = [unique[key] for key in sorted(unique)]
        return normalized[-max(2, days) :] or None

    @classmethod
    def _parse_eastmoney_klines(
        cls, rows: object, days: int
    ) -> list[GoldCandle] | None:
        if not isinstance(rows, list):
            return None
        records = []
        for row in rows:
            if not isinstance(row, str):
                continue
            parts = row.split(",")
            if len(parts) < 5:
                continue
            records.append(
                {
                    "date": parts[0],
                    "open": parts[1],
                    "close": parts[2],
                    "high": parts[3],
                    "low": parts[4],
                }
            )
        return cls._normalize_kline_records(records, days)

    @classmethod
    def _parse_sge_klines(cls, rows: object, days: int) -> list[GoldCandle] | None:
        if not isinstance(rows, list):
            return None
        records = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 5:
                continue
            records.append(
                {
                    "date": row[0],
                    "open": row[1],
                    "close": row[2],
                    "low": row[3],
                    "high": row[4],
                }
            )
        return cls._normalize_kline_records(records, days)

    async def _fetch_eastmoney_kline(
        self, session: aiohttp.ClientSession, days: int
    ) -> list[GoldCandle] | None:
        begin = (dt.date.today() - dt.timedelta(days=days * 2 + 30)).strftime("%Y%m%d")
        params = {
            "secid": "118.AU9999",
            "fields1": "f1,f2,f3,f4,f5",
            "fields2": "f51,f52,f53,f54,f55",
            "klt": "101",
            "fqt": "0",
            "beg": begin,
            "end": "20990101",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        try:
            async with session.get(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                params=params,
                headers=_headers({"Referer": "https://quote.eastmoney.com/"}),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    logger.warning(f"[gold] Eastmoney K-line HTTP {response.status}")
                    return None
                payload = await response.json(content_type=None)
        except Exception as exc:
            logger.warning(f"[gold] Eastmoney K-line request failed: {exc}")
            return None
        payload_data = payload.get("data") if isinstance(payload, dict) else None
        klines = (payload_data or {}).get("klines") or []
        result = self._parse_eastmoney_klines(klines, days)
        if not result:
            rc = payload.get("rc") if isinstance(payload, dict) else None
            logger.warning(f"[gold] Eastmoney K-line returned no valid rows (rc={rc})")
        return result

    async def _fetch_sge_kline(
        self, session: aiohttp.ClientSession, days: int
    ) -> list[GoldCandle] | None:
        rows = await self._fetch_sge_rows(session)
        result = self._parse_sge_klines(rows, days)
        if rows and not result:
            logger.warning("[gold] SGE K-line returned no valid rows")
        return result

    async def _fetch_trend(
        self, session: aiohttp.ClientSession, days: int
    ) -> list[GoldCandle] | None:
        days = max(2, int(days))
        async with self._kline_lock:
            now = time.time()
            cache_age = now - self._kline_cache_time
            if self._kline_cache and cache_age < self.KLINE_FRESH_CACHE_TTL:
                return self._kline_cache[-days:]
            for source_name, fetcher in (
                ("Eastmoney", self._fetch_eastmoney_kline),
                ("SGE", self._fetch_sge_kline),
            ):
                data = await fetcher(session, days)
                if data:
                    self._kline_cache = data
                    self._kline_cache_time = now
                    logger.info(
                        f"[gold] K-line loaded from {source_name}: {len(data)} rows"
                    )
                    return data
            if self._kline_cache and cache_age < self.KLINE_STALE_CACHE_TTL:
                logger.warning(
                    f"[gold] All K-line sources failed; using cached data ({cache_age:.0f}s old)"
                )
                return self._kline_cache[-days:]
            return None
