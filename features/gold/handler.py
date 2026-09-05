from __future__ import annotations

from typing import Any

from ...core.event import scope_key
from ...core.state import PluginStateStore
from .service import GoldPriceService


class GoldHandler:
    def __init__(
        self,
        state: PluginStateStore,
        service: GoldPriceService | None = None,
    ) -> None:
        self._state = state
        self._service = service or GoldPriceService()

    def set_enabled(self, event: Any, enabled: bool) -> Any:
        data = self._state.load()
        scope = self._state.scope(data, scope_key(event))
        scope["gold_enabled"] = enabled
        self._state.save(data)
        text = "金价工具已开启。" if enabled else "金价工具已关闭。"
        return event.plain_result(text)

    async def price(self, event: Any) -> Any | None:
        if not self._enabled(event):
            return None
        quote = await self._service.get_quote()
        if quote is None:
            return event.plain_result(
                "\n".join(
                    [
                        "金价查询失败。",
                        "已尝试国际金价 API、上海黄金交易所和新浪财经。",
                        "请稍后重试或查看 AstrBot 日志。",
                    ]
                )
            )
        return event.plain_result(self._service.format_quote(quote))

    async def trend(self, event: Any) -> Any | None:
        if not self._enabled(event):
            return None
        path = await self._service.get_trend_image()
        if path is None:
            return event.plain_result(
                "K 线数据获取失败：东方财富和上海黄金交易所暂时均不可用，请稍后重试。"
            )
        return event.image_result(path)

    def _enabled(self, event: Any) -> bool:
        data = self._state.load()
        scope = self._state.scope(data, scope_key(event))
        return bool(scope.get("gold_enabled", False))
