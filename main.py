from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.star import Context, Star

from .core.config import LEGACY_STATE_PATH, PLUGIN_NAME
from .core.event import group_id, scope_key
from .core.models import PluginData
from .core.state import PluginStateStore
from .forward_dedup import (
    ForwardStatus,
    classify_and_store_forward,
    dedupe_flattened_nodes,
    enforce_forward_history_budget,
    prune_forward_records,
)
from .forward_records import ForwardRecordExpander, find_forward_candidates
from .features.gold.handler import GoldHandler
from .onebot_forward import OneBotForwardGateway
from .features.nickname.handler import NicknameHandler


class ToolSuitePlugin(Star):
    """Gold-price lookup and group nickname tools with per-chat switches."""

    DEFAULT_FORWARD_DEDUP_DAYS = 2
    MAX_FORWARD_DEDUP_DAYS = 365
    FORWARD_SEND_BATCH_SIZE = 100
    FORWARD_REPLY_TEXT = {
        ForwardStatus.LATEST: "咪~让我看看，你又发出来什么好东西",
        ForwardStatus.PARTIAL: "咦，好熟悉的感觉，里面有部分内容已经有人发过一次啦",
        ForwardStatus.EXACT: "该条news已经发送过啦",
    }
    EXACT_FORWARD_IMAGE = "news.jpg"

    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        plugin_name = str(getattr(self, "name", PLUGIN_NAME) or PLUGIN_NAME)
        self.state = PluginStateStore.for_plugin(
            plugin_name,
            legacy_path=LEGACY_STATE_PATH,
            warn=logger.warning,
        )
        self.gold_handler = GoldHandler(self.state, self._save_data)
        self.nickname_handler = NicknameHandler(self.state, self._save_data)
        self._forward_lock = asyncio.Lock()

    def _save_data(self, data: PluginData) -> None:
        enforce_forward_history_budget(data)
        self.state.save(data)


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
        self._save_data(data)

    def _forward_dedup_days(self, scope: dict[str, Any]) -> int:
        try:
            days = int(
                scope.get("forward_dedup_days", self.DEFAULT_FORWARD_DEDUP_DAYS)
            )
        except (TypeError, ValueError):
            days = self.DEFAULT_FORWARD_DEDUP_DAYS
        return max(1, min(days, self.MAX_FORWARD_DEDUP_DAYS))

    def _forward_reply_result(self, event: AstrMessageEvent, text: str):
        message_id = str(getattr(event.message_obj, "message_id", "") or "").strip()
        if message_id:
            return event.chain_result([Reply(id=message_id), Plain(text)])
        return event.plain_result(text)

    @staticmethod
    def _build_local_image_component(local_path: str) -> Any | None:
        factory_names = (
            "fromFileSystem",
            "from_file_system",
            "fromFile",
            "from_file",
            "fromPath",
            "from_path",
        )
        for name in factory_names:
            factory = getattr(Image, name, None)
            if not callable(factory):
                continue
            try:
                return factory(local_path)
            except TypeError:
                continue

        for kwargs in ({"file": local_path}, {"path": local_path}, {"url": local_path}):
            try:
                return Image(**kwargs)
            except TypeError:
                continue
        return None

    def _forward_image_reply_result(
        self,
        event: AstrMessageEvent,
        local_path: str,
    ) -> Any | None:
        image = self._build_local_image_component(local_path)
        if image is None:
            return None
        chain: list[Any] = []
        message_id = str(getattr(event.message_obj, "message_id", "") or "").strip()
        if message_id:
            chain.append(Reply(id=message_id))
        chain.append(image)
        return event.chain_result(chain)

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
        self._set_features(event, forward=True)
        data = self.state.load()
        scope = self.state.scope(data, scope_key(event))
        yield event.plain_result(
            f"聊天记录展开与查重已开启，当前查重范围为最近 "
            f"{self._forward_dedup_days(scope)} 天。"
        )

    @filter.regex(r"^聊天记录关\s*$")
    async def disable_forward_records(self, event: AstrMessageEvent):
        self._set_features(event, forward=False)
        yield event.plain_result("聊天记录展开与查重已关闭，已有查重记录会保留。")

    @filter.regex(r"^聊天记录增强开\s*$")
    async def enable_enhanced_forward_records(self, event: AstrMessageEvent):
        self._set_features(event, forward=True, forward_enhanced=True)
        yield event.plain_result(
            "聊天记录增强已开启：基础查重已同步开启，将平铺重发新内容并撤回完全重复记录。"
        )

    @filter.regex(r"^聊天记录增强关\s*$")
    async def disable_enhanced_forward_records(self, event: AstrMessageEvent):
        self._set_features(event, forward_enhanced=False)
        yield event.plain_result("聊天记录增强已关闭，基础展开与查重不受影响。")

    @filter.regex(r"^聊天记录查重天数\s*(\d+)\s*$")
    async def set_forward_dedup_days(self, event: AstrMessageEvent):
        match = re.match(
            r"^聊天记录查重天数\s*(\d+)\s*$",
            event.get_message_str().strip(),
        )
        if not match:
            return
        days = int(match.group(1))
        if days < 1 or days > self.MAX_FORWARD_DEDUP_DAYS:
            yield event.plain_result(
                f"聊天记录查重天数可设置为 1-{self.MAX_FORWARD_DEDUP_DAYS} 天。"
            )
            return

        data = self.state.load()
        scope = self.state.scope(data, scope_key(event))
        scope["forward_dedup_days"] = days
        prune_forward_records(
            scope,
            now=int(time.time()),
            retention_days=self._forward_dedup_days(scope),
        )
        self._save_data(data)
        yield event.plain_result(f"聊天记录查重范围已调整为最近 {days} 天。")

    @filter.regex(r"^聊天记录状态\s*$")
    async def forward_record_status(self, event: AstrMessageEvent):
        data = self.state.load()
        scope = self.state.scope(data, scope_key(event))
        records = prune_forward_records(
            scope,
            now=int(time.time()),
            retention_days=self._forward_dedup_days(scope),
        )
        enabled = "开启" if scope.get("forward_enabled", False) else "关闭"
        enhanced_enabled = (
            "开启" if scope.get("forward_enhanced_enabled", False) else "关闭"
        )
        yield event.plain_result(
            "\n".join(
                [
                    f"聊天记录展开与查重：{enabled}",
                    f"聊天记录增强：{enhanced_enabled}",
                    f"查重范围：最近 {self._forward_dedup_days(scope)} 天",
                    f"范围内聊天记录：{len(records)} 份",
                ]
            )
        )

    @filter.regex(r"^工具状态\s*$")
    async def tool_status(self, event: AstrMessageEvent):
        data = self.state.load()
        scope = self.state.scope(data, scope_key(event))
        gold_status = "开启" if scope.get("gold_enabled", False) else "关闭"
        if group_id(event) is None:
            nickname_status = "不可用（仅群聊）"
        else:
            nickname_status = (
                "开启" if scope.get("nickname_enabled", False) else "关闭"
            )
        forward_status = "开启" if scope.get("forward_enabled", False) else "关闭"
        forward_enhanced_status = (
            "开启" if scope.get("forward_enhanced_enabled", False) else "关闭"
        )
        yield event.plain_result(
            "\n".join(
                [
                    "工具集状态",
                    f"金价：{gold_status}",
                    f"昵称：{nickname_status}",
                    f"聊天记录展开与查重：{forward_status}",
                    f"聊天记录增强：{forward_enhanced_status}",
                    f"聊天记录查重范围：最近 {self._forward_dedup_days(scope)} 天",
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

    @filter.event_message_type(filter.EventMessageType.ALL)
    @filter.regex(r".*")
    async def handle_forward_records(self, event: AstrMessageEvent):
        candidates = find_forward_candidates(event)
        if not candidates:
            return
        if str(event.get_sender_id()) == str(event.get_self_id() or ""):
            return

        data = self.state.load()
        scope = self.state.scope(data, scope_key(event))
        if not scope.get("forward_enabled", False):
            return
        enhanced_enabled = bool(scope.get("forward_enhanced_enabled", False))

        should_call_llm = getattr(event, "should_call_llm", None)
        if callable(should_call_llm):
            should_call_llm(False)

        gateway = OneBotForwardGateway(
            event,
            group_id=group_id(event),
            warn=logger.warning,
        )
        expander = ForwardRecordExpander(gateway.fetch)
        expanded = await expander.expand(candidates)
        if not expanded.complete or not expanded.record_hash:
            logger.warning(
                "[tool_suite] forward record expansion incomplete: "
                f"leaf_count={expanded.leaf_count}, errors={expanded.errors}"
            )
            if not enhanced_enabled:
                yield self._forward_reply_result(
                    event,
                    "聊天记录展开失败，未执行查重。",
                )
            return

        async with self._forward_lock:
            data = self.state.load()
            scope = self.state.scope(data, scope_key(event))
            if not scope.get("forward_enabled", False):
                return
            decision = classify_and_store_forward(
                scope,
                record_hash=expanded.record_hash,
                content_hashes=expanded.content_hashes,
                leaf_hashes=tuple(node.content_hash for node in expanded.nodes),
                compatible_record_hashes=(expanded.legacy_record_hash,),
                compatible_content_hashes=expanded.legacy_content_hashes,
                now=int(time.time()),
                retention_days=self._forward_dedup_days(scope),
            )
            enhanced_enabled = bool(
                scope.get("forward_enhanced_enabled", False)
            )
            self._save_data(data)

        if enhanced_enabled:
            if decision.status == ForwardStatus.EXACT:
                recall_error = await gateway.recall_original()
                if recall_error:
                    logger.warning(
                        "[tool_suite] enhanced forward recall did not complete: "
                        f"{recall_error}"
                    )
                return

            if (
                decision.status == ForwardStatus.LATEST
                and expanded.max_forward_depth <= 1
            ):
                return

            flattened_nodes = dedupe_flattened_nodes(
                expanded.nodes,
                decision.duplicate_leaf_hashes,
            )
            send_error = await gateway.send_flattened(
                flattened_nodes,
                batch_size=self.FORWARD_SEND_BATCH_SIZE,
            )
            if send_error:
                logger.warning(
                    "[tool_suite] enhanced forward send did not complete: "
                    f"{send_error}"
                )
            return

        if decision.status == ForwardStatus.EXACT:
            reply_sent = False
            image_path = Path(__file__).with_name(self.EXACT_FORWARD_IMAGE)
            if image_path.is_file():
                image_reply = self._forward_image_reply_result(event, str(image_path))
                if image_reply is not None:
                    yield image_reply
                    reply_sent = True
                else:
                    logger.warning(
                        "[tool_suite] unable to build exact duplicate image component: "
                        f"{image_path}"
                    )
            else:
                logger.warning(
                    f"[tool_suite] exact duplicate image missing: {image_path}"
                )
            if not reply_sent:
                yield self._forward_reply_result(
                    event,
                    self.FORWARD_REPLY_TEXT[decision.status],
                )
            return

        yield self._forward_reply_result(
            event,
            self.FORWARD_REPLY_TEXT[decision.status],
        )
