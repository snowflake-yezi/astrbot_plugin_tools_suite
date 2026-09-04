from __future__ import annotations

import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from astrbot.api import logger

from ...core.config import PACKAGE_ROOT
from ...core.models import GoldCandle

_FONT_CANDIDATES = (
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Zen Hei",
    "PingFang SC",
    "Arial Unicode MS",
)
_BUNDLED_FONT = PACKAGE_ROOT / "assets" / "NotoSansSC-GoldChart.ttf"


class GoldTrendChart:
    def __init__(self) -> None:
        self._font = self._pick_font()
        self._text_style = {"fontproperties": self._font} if self._font else {}

    @staticmethod
    def _pick_font() -> font_manager.FontProperties | None:
        available = {font.name for font in font_manager.fontManager.ttflist}
        for name in _FONT_CANDIDATES:
            if name in available:
                return font_manager.FontProperties(family=name)
        if _BUNDLED_FONT.is_file():
            return font_manager.FontProperties(fname=_BUNDLED_FONT)
        logger.warning("[gold] No Chinese font is available for trend charts")
        return None

    def render(self, data: list[GoldCandle]) -> str:
        dates = [item["date"] for item in data]
        closes = [item["close"] for item in data]
        daily_changes = [item["close"] - item["open"] for item in data]
        positions = list(range(len(data)))
        first, last = closes[0], closes[-1]
        change = last - first
        percentage = (change / first) * 100 if first else 0
        line_color = "#d62728" if change >= 0 else "#2ca02c"
        bar_colors = [
            "#d84a4a" if daily_change >= 0 else "#2f9e62"
            for daily_change in daily_changes
        ]

        figure, price_axis = plt.subplots(figsize=(10, 5.2), dpi=130)
        change_axis = price_axis.twinx()
        bars = change_axis.bar(
            positions,
            daily_changes,
            width=0.68,
            color=bar_colors,
            alpha=0.42,
            label="每日涨跌",
            zorder=1,
        )
        change_axis.axhline(0, color="#777777", linewidth=0.8, alpha=0.7)
        line = price_axis.plot(
            positions,
            closes,
            color=line_color,
            linewidth=2,
            marker="o",
            markersize=3.5,
            label="收盘价",
            zorder=3,
        )[0]
        price_axis.margins(y=0.14)
        price_axis.grid(True, axis="y", linestyle="--", alpha=0.35)
        price_axis.set_zorder(change_axis.get_zorder() + 1)
        price_axis.patch.set_visible(False)

        maximum_index = closes.index(max(closes))
        minimum_index = closes.index(min(closes))
        price_axis.annotate(
            f"高 {closes[maximum_index]:.2f}",
            xy=(maximum_index, closes[maximum_index]),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            **self._text_style,
        )
        price_axis.annotate(
            f"低 {closes[minimum_index]:.2f}",
            xy=(minimum_index, closes[minimum_index]),
            xytext=(0, -14),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            **self._text_style,
        )

        price_axis.set_title(
            f"Au99.99 {len(data)}日走势  "
            f"{first:.2f} → {last:.2f} ({percentage:+.2f}%)",
            fontsize=12,
            **self._text_style,
        )
        price_axis.set_ylabel("收盘价（元/克）", **self._text_style)
        change_axis.set_ylabel("每日涨跌（元/克）", **self._text_style)
        legend_options = {"prop": self._font} if self._font else {}
        price_axis.legend(
            [line, bars],
            ["收盘价", "每日涨跌"],
            loc="upper left",
            **legend_options,
        )

        tick_count = min(8, len(dates))
        tick_positions = (
            [0]
            if tick_count == 1
            else [
                round(index * (len(dates) - 1) / (tick_count - 1))
                for index in range(tick_count)
            ]
        )
        price_axis.set_xticks(tick_positions)
        price_axis.set_xticklabels(
            [dates[index] for index in tick_positions],
            rotation=30,
        )

        figure.tight_layout()
        path = Path(tempfile.gettempdir()) / "gold_trend.png"
        figure.savefig(path)
        plt.close(figure)
        return str(path)
