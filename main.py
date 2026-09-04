from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.config import LEGACY_STATE_PATH, PLUGIN_NAME
from .core.event import group_id, scope_key
from .core.state import PluginStateStore
from .features.forward.handler import MergedForwardHandler
from .features.gold.handler import GoldHandler
from .features.nickname.handler import NicknameHandler


class ToolSuitePlugin(Star):
    """按会话组合金价、群昵称和 QQ 合并转发能力。"""

    def __init__(self, context: Context):
        super().__init__(context)
        plugin_name = str(getattr(self, "name", PLUGIN_NAME) or PLUGIN_NAME)
        self.state = PluginStateStore.for_plugin(
            plugin_name,
            legacy_path=LEGACY_STATE_PATH,
            warn=logger.warning,
        )
        self.gold_handler = GoldHandler(self.state, self.state.save)
        self.nickname_handler = NicknameHandler(self.state, self.state.save)
        self.forward_handler = MergedForwardHandler(self.state, warn=logger.warning)

    async def initialize(self) -> None:
        self.forward_handler.enforce_history_budget()

    def _set_features(
        self,
        event: AstrMessageEvent,
        *,
        gold: bool | None = None,
        nickname: bool | None = None,
        forward: bool | None = None,
        forward_enhanced: bool | None = None,
    ) -> None:
        data = self.state.load()
        scope = self.state.scope(data, scope_key(event))
        if gold is not None:
            scope["gold_enabled"] = gold
        if nickname is not None:
            scope["nickname_enabled"] = nickname
        if forward is not None:
            scope["forward_enabled"] = forward
        if forward_enhanced is not None:
            scope["forward_enhanced_enabled"] = forward_enhanced
        self.state.save(data)

    @filter.regex(r"^工具开\s*$")
    async def enable_tools(self, event: AstrMessageEvent):
        in_group = group_id(event) is not None
        self._set_features(
            event,
            gold=True,
            nickname=True if in_group else None,
            forward=True,
        )
        if in_group:
            yield event.plain_result("工具集已开启：金价、昵称、聊天记录查重均可使用。")
        else:
            yield event.plain_result(
                "工具集已开启：金价、聊天记录查重可使用；昵称仅支持群聊。"
            )

    @filter.regex(r"^工具关\s*$")
    async def disable_tools(self, event: AstrMessageEvent):
        self._set_features(
            event,
            gold=False,
            nickname=False,
            forward=False,
            forward_enhanced=False,
        )
        yield event.plain_result("工具集已关闭。")

    @filter.regex(r"^金价开\s*$")
    async def enable_gold(self, event: AstrMessageEvent):
        yield self.gold_handler.set_enabled(event, True)

    @filter.regex(r"^金价关\s*$")
    async def disable_gold(self, event: AstrMessageEvent):
        yield self.gold_handler.set_enabled(event, False)

    @filter.regex(r"^昵称开\s*$")
    async def enable_nickname(self, event: AstrMessageEvent):
        yield self.nickname_handler.set_enabled(event, True)

    @filter.regex(r"^昵称关\s*$")
    async def disable_nickname(self, event: AstrMessageEvent):
        yield self.nickname_handler.set_enabled(event, False)

    @filter.regex(r"^聊天记录开\s*$")
    async def enable_forward_records(self, event: AstrMessageEvent):
        yield self.forward_handler.set_enabled(event, True)

    @filter.regex(r"^聊天记录关\s*$")
    async def disable_forward_records(self, event: AstrMessageEvent):
        yield self.forward_handler.set_enabled(event, False)

    @filter.regex(r"^聊天记录增强开\s*$")
    async def enable_enhanced_forward_records(self, event: AstrMessageEvent):
        yield self.forward_handler.set_enhanced(event, True)

    @filter.regex(r"^聊天记录增强关\s*$")
    async def disable_enhanced_forward_records(self, event: AstrMessageEvent):
        yield self.forward_handler.set_enhanced(event, False)

    @filter.regex(r"^聊天记录查重天数\s*(\d+)\s*$")
    async def set_forward_dedup_days(self, event: AstrMessageEvent):
        result = self.forward_handler.set_retention_days(event)
        if result is not None:
            yield result

    @filter.regex(r"^聊天记录状态\s*$")
    async def forward_record_status(self, event: AstrMessageEvent):
        yield self.forward_handler.status(event)

    @filter.regex(r"^工具状态\s*$")
    async def tool_status(self, event: AstrMessageEvent):
        data = self.state.load()
        scope = self.state.scope(data, scope_key(event))
        gold_status = "开启" if scope.get("gold_enabled", False) else "关闭"
        if group_id(event) is None:
            nickname_status = "不可用（仅群聊）"
        else:
            nickname_status = "开启" if scope.get("nickname_enabled", False) else "关闭"
        forward_status = "开启" if scope.get("forward_enabled", False) else "关闭"
        enhanced_status = (
            "开启" if scope.get("forward_enhanced_enabled", False) else "关闭"
        )
        yield event.plain_result(
            "\n".join(
                [
                    "工具集状态",
                    f"金价：{gold_status}",
                    f"昵称：{nickname_status}",
                    f"聊天记录展开与查重：{forward_status}",
                    f"聊天记录增强：{enhanced_status}",
                    f"聊天记录查重范围：最近 {self.forward_handler.retention_days(scope)} 天",
                ]
            )
        )

    @filter.regex(r"^工具帮助\s*$")
    async def tool_help(self, event: AstrMessageEvent):
        yield event.plain_result(
            "\n".join(
                [
                    "工具集指令",
                    "工具开 / 工具关：开启或关闭当前会话的全部工具",
                    "金价开 / 金价关：单独控制金价工具",
                    "昵称开 / 昵称关：单独控制本群昵称工具",
                    "聊天记录开 / 聊天记录关：控制合并转发展开与查重",
                    "聊天记录增强开 / 聊天记录增强关：控制平铺重发、内容去重与完全重复撤回",
                    "聊天记录查重天数 N：调整查重范围，默认 2 天",
                    "聊天记录状态：查看开关、天数和已记录数量",
                    "工具状态：查看当前开关状态",
                    "金价：直接查询实时金价",
                    "金价走势：直接查看 15 日柱状折线混合图",
                    "gold / goldk：兼容的英文指令",
                    "@用户 昵称 [新昵称]：查询或绑定昵称",
                    "at昵称 [消息]：@ 该昵称绑定的全部用户",
                ]
            )
        )

    @filter.regex(r"^gold\s*$")
    async def gold(self, event: AstrMessageEvent):
        result = await self.gold_handler.price(event)
        if result is not None:
            yield result

    @filter.regex(r"^金价\s*$")
    async def gold_cn(self, event: AstrMessageEvent):
        result = await self.gold_handler.price(event)
        if result is not None:
            yield result

    @filter.regex(r"^goldk\s*$")
    async def gold_kline(self, event: AstrMessageEvent):
        result = await self.gold_handler.trend(event)
        if result is not None:
            yield result

    @filter.regex(r"^金价走势\s*$")
    async def gold_trend_cn(self, event: AstrMessageEvent):
        result = await self.gold_handler.trend(event)
        if result is not None:
            yield result

    @filter.regex(r"^金价K线\s*$")
    async def gold_kline_cn(self, event: AstrMessageEvent):
        result = await self.gold_handler.trend(event)
        if result is not None:
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_nickname_at(self, event: AstrMessageEvent):
        result = self.nickname_handler.handle(event)
        if result is not None:
            yield result

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_forward_records(self, event: AstrMessageEvent):
        result = await self.forward_handler.handle(event)
        if result is not None:
            yield result
