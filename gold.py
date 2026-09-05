import asyncio
import datetime as dt
import math
import os
import random
import re
import ssl
import tempfile
import time
from typing import Optional, List

import aiohttp
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from astrbot.api import logger

# ═══════════════════════════════════════════════════════════════════════
# Chinese font setup
# ═══════════════════════════════════════════════════════════════════════
_CN_FONT_CANDIDATES = [
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Zen Hei",
    "PingFang SC",
    "Arial Unicode MS",
]
_BUNDLED_CN_FONT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "assets",
    "NotoSansSC-GoldChart.ttf",
)


def _pick_cn_font() -> Optional[font_manager.FontProperties]:
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CN_FONT_CANDIDATES:
        if name in available:
            return font_manager.FontProperties(family=name)
    if os.path.isfile(_BUNDLED_CN_FONT):
        return font_manager.FontProperties(fname=_BUNDLED_CN_FONT)
    logger.warning("[gold] No Chinese font is available for trend charts")
    return None


_CN_FONT = _pick_cn_font()
_CN_TEXT_STYLE = {"fontproperties": _CN_FONT} if _CN_FONT else {}

# ═══════════════════════════════════════════════════════════════════════
# Anti-bot: User-Agent pool
# ═══════════════════════════════════════════════════════════════════════
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _headers(extra: Optional[dict] = None) -> dict:
    h = {
        "User-Agent": _random_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if extra:
        h.update(extra)
    return h


# ═══════════════════════════════════════════════════════════════════════
# Plugin
# ═══════════════════════════════════════════════════════════════════════
class GoldPriceService:
    """Au99.99 / 国际现货黄金实时行情查询。

    数据源优先级：GoldPrice.org 国际金价 → SGE 官方 → 新浪财经
    国际金价（美元/盎司）+ 实时汇率 → 换算为人民币/克，无国内风控。
    """

    TROY_OZ_TO_GRAM = 31.1035
    TREND_DAYS = 15
    KLINE_FRESH_CACHE_TTL = 300
    KLINE_STALE_CACHE_TTL = 86400

    def __init__(self):
        self._cache: Optional[dict] = None
        self._cache_time: float = 0.0
        self._ttl: int = 90  # 缓存 90 秒
        self._lock = asyncio.Lock()
        # 汇率缓存（汇率变化慢，可缓存更久）
        self._fx_cache: Optional[float] = None
        self._fx_cache_time: float = 0.0
        self._kline_cache: Optional[List[dict]] = None
        self._kline_cache_time: float = 0.0
        self._kline_lock = asyncio.Lock()

    # ── helpers ────────────────────────────────────────────────────
    @staticmethod
    def _no_ssl_connector() -> aiohttp.TCPConnector:
        return aiohttp.TCPConnector(ssl=False)

    # ── Source 1: GoldPrice.org (International, primary) ───────────
    async def _fetch_intl_gold(
        self, session: aiohttp.ClientSession
    ) -> Optional[dict]:
        """国际现货黄金 XAU/USD + USD/CNY 汇率 → 人民币/克。

        使用 goldprice.org 免费 JSON 接口，无 API Key、无风控。
        """
        # 1) 获取 XAU/USD 价格
        xau_url = "https://data-asg.goldprice.org/dbXRates/USD"
        try:
            async with session.get(
                xau_url,
                headers=_headers({"Referer": "https://goldprice.org/"}),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[gold] GoldPrice.org HTTP {resp.status}")
                    return None
                xau_data = await resp.json(content_type=None)
        except Exception as e:
            logger.warning(f"[gold] GoldPrice.org request failed: {e}")
            return None

        items = (xau_data or {}).get("items", [])
        if not items:
            return None

        xau_usd = items[0].get("xauPrice")
        if not xau_usd:
            return None

        ts = (xau_data.get("ts") or 0) / 1000
        gold_time = (
            dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if ts
            else dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        # 2) 获取 USD/CNY 汇率（带缓存）
        usd_cny = await self._get_usd_cny(session)
        if not usd_cny:
            return None

        # 3) 换算：美元/盎司 → 人民币/克
        price_cny_per_g = (xau_usd * usd_cny) / self.TROY_OZ_TO_GRAM
        prev_close = xau_usd + items[0].get("chgXau", 0)  # 昨收 ≈ 现价 - 涨跌
        prev_close_cny = (prev_close * usd_cny) / self.TROY_OZ_TO_GRAM
        high_cny = price_cny_per_g  # 日内数据有限，用现价近似
        low_cny = price_cny_per_g

        return {
            "price": round(price_cny_per_g, 2),
            "high": round(high_cny * 1.005, 2),  # ±0.5% 日内波幅估算
            "low": round(low_cny * 0.995, 2),
            "open": round(prev_close_cny, 2),
            "prev_close": round(prev_close_cny, 2),
            "time": gold_time,
            "source": "GoldPrice.org (国际现货)",
        }

    async def _get_usd_cny(self, session: aiohttp.ClientSession) -> Optional[float]:
        """获取 USD→CNY 汇率，缓存 1 小时。"""
        now = dt.datetime.now().timestamp()
        if self._fx_cache and (now - self._fx_cache_time < 3600):
            return self._fx_cache

        fx_url = "https://open.er-api.com/v6/latest/USD"
        try:
            async with session.get(
                fx_url,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[gold] FX API HTTP {resp.status}")
                    return None
                fx_data = await resp.json(content_type=None)
        except Exception as e:
            logger.warning(f"[gold] FX API request failed: {e}")
            return None

        rate = ((fx_data or {}).get("rates") or {}).get("CNY")
        if rate:
            self._fx_cache = float(rate)
            self._fx_cache_time = now
        return self._fx_cache

    # ── Source 2: SGE Official (domestic, fallback 1) ──────────────
    async def _fetch_sge_rows(
        self, session: aiohttp.ClientSession
    ) -> Optional[List[list]]:
        """读取上海黄金交易所 Au99.99 日线原始记录。"""
        url = "https://www.sge.com.cn/graph/Dailyhq"
        headers = _headers({
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.sge.com.cn",
            "Referer": "https://www.sge.com.cn/",
        })
        try:
            async with session.post(
                url,
                data={"instid": "Au99.99"},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[gold] SGE HTTP {resp.status}")
                    return None
                payload = await resp.json(content_type=None)
        except Exception as e:
            logger.warning(f"[gold] SGE request failed: {e}")
            return None

        if not isinstance(payload, dict):
            return None

        rows = payload.get("time", [])
        if not rows or not isinstance(rows, list):
            logger.warning("[gold] SGE history response contains no rows")
            return None
        return rows

    async def _fetch_sge(self, session: aiohttp.ClientSession) -> Optional[dict]:
        """上海黄金交易所官方行情，字段为日期、开盘、收盘、最低、最高。"""
        rows = await self._fetch_sge_rows(session)
        if not rows:
            return None

        last = rows[-1]
        if not isinstance(last, list) or len(last) < 5:
            return None

        try:
            date_str = str(last[0])
            open_price = float(last[1])
            close_price = float(last[2])
            low_price = float(last[3])
            high_price = float(last[4])
        except (ValueError, TypeError):
            return None

        prev_close = open_price
        if len(rows) > 1:
            try:
                prev_close = float(rows[-2][2])
            except (IndexError, TypeError, ValueError):
                pass

        if not self._valid_ohlc(open_price, close_price, high_price, low_price):
            return None

        return {
            "price": round(close_price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "open": round(open_price, 2),
            "prev_close": round(prev_close, 2),
            "time": date_str,
            "source": "上海黄金交易所",
        }

    # ── Source 3: Sina Finance (domestic, fallback 2) ──────────────
    async def _fetch_sina(self, session: aiohttp.ClientSession) -> Optional[dict]:
        """新浪财经行情接口 — 最后兜底，GBK 解码。"""
        ts = int(dt.datetime.now().timestamp() * 1000)
        url = f"https://hq.sinajs.cn/rn={ts}&list=gds_AU9999"
        headers = _headers({"Referer": "https://finance.sina.com.cn/"})
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[gold] Sina HTTP {resp.status}")
                    return None
                raw = await resp.read()
        except Exception as e:
            logger.warning(f"[gold] Sina request failed: {e}")
            return None

        text = raw.decode("gbk", errors="ignore")
        m = re.search(r'"(.*?)"', text)
        if not m:
            return None

        parts = m.group(1).split(",")
        if len(parts) < 13:
            return None

        try:
            price = float(parts[0])
            high = float(parts[4])
            low = float(parts[5])
            prev_close = float(parts[7])
            open_price = float(parts[8])
        except ValueError:
            return None

        if price <= 0:
            return None

        date_str = parts[12] if len(parts) > 12 else dt.date.today().isoformat()
        time_str = f"{date_str} {parts[6]}"
        return {
            "price": price,
            "high": high,
            "low": low,
            "open": open_price,
            "prev_close": prev_close,
            "time": time_str,
            "source": "新浪财经",
        }

    # ── K-line (Eastmoney → SGE → recent cache) ────────────────────
    @staticmethod
    def _valid_ohlc(open_price: float, close_price: float, high: float, low: float) -> bool:
        values = (open_price, close_price, high, low)
        return (
            all(math.isfinite(value) and value > 0 for value in values)
            and high >= max(open_price, close_price)
            and low <= min(open_price, close_price)
            and high >= low
        )

    @classmethod
    def _normalize_kline_records(cls, records: List[dict], days: int) -> Optional[List[dict]]:
        unique: dict[str, dict] = {}
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
    def _parse_eastmoney_klines(cls, rows: object, days: int) -> Optional[List[dict]]:
        if not isinstance(rows, list):
            return None
        records: List[dict] = []
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
    def _parse_sge_klines(cls, rows: object, days: int) -> Optional[List[dict]]:
        if not isinstance(rows, list):
            return None
        records: List[dict] = []
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
    ) -> Optional[List[dict]]:
        beg = (dt.date.today() - dt.timedelta(days=days * 2 + 30)).strftime("%Y%m%d")
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": "118.AU9999",
            "fields1": "f1,f2,f3,f4,f5",
            "fields2": "f51,f52,f53,f54,f55",
            "klt": "101",
            "fqt": "0",
            "beg": beg,
            "end": "20990101",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        headers = _headers({"Referer": "https://quote.eastmoney.com/"})
        try:
            async with session.get(
                url,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[gold] Eastmoney K-line HTTP {resp.status}")
                    return None
                payload = await resp.json(content_type=None)
        except Exception as e:
            logger.warning(f"[gold] Eastmoney K-line request failed: {e}")
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
    ) -> Optional[List[dict]]:
        rows = await self._fetch_sge_rows(session)
        result = self._parse_sge_klines(rows, days)
        if rows and not result:
            logger.warning("[gold] SGE K-line returned no valid rows")
        return result

    async def _fetch_kline(
        self, session: aiohttp.ClientSession, days: int = TREND_DAYS
    ) -> Optional[List[dict]]:
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
                    logger.info(f"[gold] K-line loaded from {source_name}: {len(data)} rows")
                    return data

            if self._kline_cache and cache_age < self.KLINE_STALE_CACHE_TTL:
                logger.warning(
                    f"[gold] All K-line sources failed; using cached data ({cache_age:.0f}s old)"
                )
                return self._kline_cache[-days:]
            return None

    # ── Chart ──────────────────────────────────────────────────────
    def _draw_kline(self, data: List[dict]) -> str:
        dates = [x["date"] for x in data]
        closes = [x["close"] for x in data]
        daily_changes = [x["close"] - x["open"] for x in data]
        positions = list(range(len(data)))
        first, last = closes[0], closes[-1]
        change = last - first
        pct = (change / first) * 100 if first else 0
        line_color = "#d62728" if change >= 0 else "#2ca02c"
        bar_colors = [
            "#d84a4a" if daily_change >= 0 else "#2f9e62"
            for daily_change in daily_changes
        ]

        fig, price_ax = plt.subplots(figsize=(10, 5.2), dpi=130)
        change_ax = price_ax.twinx()
        bars = change_ax.bar(
            positions,
            daily_changes,
            width=0.68,
            color=bar_colors,
            alpha=0.42,
            label="每日涨跌",
            zorder=1,
        )
        change_ax.axhline(0, color="#777777", linewidth=0.8, alpha=0.7)
        line = price_ax.plot(
            positions,
            closes,
            color=line_color,
            linewidth=2,
            marker="o",
            markersize=3.5,
            label="收盘价",
            zorder=3,
        )[0]
        price_ax.margins(y=0.14)
        price_ax.grid(True, axis="y", linestyle="--", alpha=0.35)
        price_ax.set_zorder(change_ax.get_zorder() + 1)
        price_ax.patch.set_visible(False)

        max_idx = closes.index(max(closes))
        min_idx = closes.index(min(closes))
        price_ax.annotate(
            f"高 {closes[max_idx]:.2f}",
            xy=(max_idx, closes[max_idx]),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            **_CN_TEXT_STYLE,
        )
        price_ax.annotate(
            f"低 {closes[min_idx]:.2f}",
            xy=(min_idx, closes[min_idx]),
            xytext=(0, -14),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            **_CN_TEXT_STYLE,
        )

        title = (
            f"Au99.99 {len(data)}日走势  "
            f"{first:.2f} → {last:.2f} ({pct:+.2f}%)"
        )
        price_ax.set_title(title, fontsize=12, **_CN_TEXT_STYLE)
        price_ax.set_ylabel("收盘价（元/克）", **_CN_TEXT_STYLE)
        change_ax.set_ylabel("每日涨跌（元/克）", **_CN_TEXT_STYLE)
        legend_options = {"prop": _CN_FONT} if _CN_FONT else {}
        price_ax.legend(
            [line, bars],
            ["收盘价", "每日涨跌"],
            loc="upper left",
            **legend_options,
        )

        tick_count = min(8, len(dates))
        if tick_count == 1:
            tick_positions = [0]
        else:
            tick_positions = [
                round(i * (len(dates) - 1) / (tick_count - 1))
                for i in range(tick_count)
            ]
        price_ax.set_xticks(tick_positions)
        price_ax.set_xticklabels(
            [dates[i] for i in tick_positions],
            rotation=30,
        )

        fig.tight_layout()
        path = os.path.join(tempfile.gettempdir(), "gold_trend.png")
        fig.savefig(path)
        plt.close(fig)
        return path

    # ── Price orchestration ────────────────────────────────────────
    async def _get_price(self) -> Optional[dict]:
        """按优先级尝试多数据源：国际金价 → SGE → 新浪。"""
        now = dt.datetime.now().timestamp()
        async with self._lock:
            if self._cache and (now - self._cache_time < self._ttl):
                return self._cache

            async with aiohttp.ClientSession() as session:
                # 第一优先：国际金价（无国内风控）
                data = await self._fetch_intl_gold(session)
                if not data:
                    logger.info("[gold] Intl gold unavailable, trying SGE…")
                    data = await self._fetch_sge(session)
                if not data:
                    logger.info("[gold] SGE unavailable, trying Sina…")
                    data = await self._fetch_sina(session)

            if data:
                self._cache = data
                self._cache_time = now
            return data

    # ── Formatting ─────────────────────────────────────────────────
    @staticmethod
    def _format(d: dict) -> str:
        price = d["price"]
        prev_close = d.get("prev_close") or d.get("open") or 0
        change = price - prev_close if prev_close else 0
        pct = (change / prev_close) * 100 if prev_close else 0
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
            f"涨跌: {change:+.2f} ({pct:+.2f}%) {trend}",
            f"开盘: {d['open']:.2f}",
            f"昨收: {prev_close:.2f}",
            f"最高: {d['high']:.2f}",
            f"最低: {d['low']:.2f}",
            f"时间: {d['time']}",
            f"来源: {d['source']}",
        ]
        if "国际" in d.get("source", ""):
            lines.append("⚠ 价格为国际现货换算，与国内 Au99.99 略有差异")

        return "\n".join(lines)
