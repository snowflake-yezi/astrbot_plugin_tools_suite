from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ...core.event import message_id

if TYPE_CHECKING:
    from .parser import FlattenedForwardNode


class OneBotForwardGateway:
    """封装当前事件上的 OneBot 合并转发动作及兼容参数。"""

    def __init__(
        self,
        event: Any,
        *,
        group_id: str | None,
        warn: Callable[[str], None],
    ) -> None:
        self._event = event
        self._group_id = group_id
        self._warn = warn

    def _routing_params(self) -> dict[str, Any]:
        self_id = getattr(self._event.message_obj, "self_id", None)
        return {"self_id": self_id} if self_id else {}

    @staticmethod
    def _action_failed(result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        status = str(result.get("status") or "").strip().lower()
        if status in {"failed", "failure", "error"}:
            return True
        retcode = result.get("retcode")
        try:
            return retcode is not None and int(retcode) != 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _build_nodes(
        nodes: tuple[FlattenedForwardNode, ...],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for node in nodes:
            data: dict[str, Any] = {
                "user_id": node.sender_id or "0",
                "nickname": node.sender_name or "未知成员",
                "content": list(node.content),
            }
            if node.timestamp > 0:
                data["time"] = node.timestamp
            result.append({"type": "node", "data": data})
        return result

    async def fetch(self, forward_id: str) -> Any:
        bot = getattr(self._event, "bot", None)
        call_action = getattr(bot, "call_action", None)
        if not callable(call_action):
            raise RuntimeError("当前平台事件不支持 get_forward_msg")

        routing_params = self._routing_params()
        try:
            return await call_action(
                "get_forward_msg",
                message_id=forward_id,
                **routing_params,
            )
        except Exception as message_id_error:
            try:
                return await call_action(
                    "get_forward_msg",
                    id=forward_id,
                    **routing_params,
                )
            except Exception as id_error:
                raise RuntimeError(
                    f"get_forward_msg failed: {message_id_error}; fallback: {id_error}"
                ) from id_error

    async def send_flattened(
        self,
        nodes: tuple[FlattenedForwardNode, ...],
        *,
        batch_size: int,
    ) -> str | None:
        if not nodes:
            return None
        bot = getattr(self._event, "bot", None)
        call_action = getattr(bot, "call_action", None)
        if not callable(call_action):
            return "当前平台不支持发送平铺合并转发。"

        if self._group_id is not None:
            action = "send_group_forward_msg"
            target_params: dict[str, Any] = {"group_id": self._group_id}
        else:
            action = "send_private_forward_msg"
            target_params = {"user_id": str(self._event.get_sender_id())}
        routing_params = self._routing_params()

        for start in range(0, len(nodes), batch_size):
            messages = self._build_nodes(nodes[start : start + batch_size])
            try:
                result = await call_action(
                    action,
                    messages=messages,
                    **target_params,
                    **routing_params,
                )
            except Exception as exc:
                self._warn(
                    "[tool_suite] flattened forward send failed: "
                    f"action={action}, batch_start={start}, error={exc}"
                )
                return "聊天记录平铺发送失败，请查看机器人日志。"
            if self._action_failed(result):
                self._warn(
                    "[tool_suite] flattened forward action failed: "
                    f"action={action}, batch_start={start}, result={result}"
                )
                return "聊天记录平铺发送失败，请查看机器人日志。"
        return None

    async def recall_original(self) -> str | None:
        original_id = message_id(self._event)
        if original_id is None:
            return "撤回重复聊天记录失败：当前消息没有可用的消息 ID。"
        bot = getattr(self._event, "bot", None)
        api = getattr(bot, "api", None)
        api_call_action = getattr(api, "call_action", None)
        direct_call_action = getattr(bot, "call_action", None)
        callers: list[tuple[str, Any, dict[str, Any]]] = []
        if callable(direct_call_action):
            # aiocqhttp 事件公开 bot.call_action；旧适配器的 api 对象仅作回退。
            callers.append(
                (
                    "bot.call_action",
                    direct_call_action,
                    self._routing_params(),
                )
            )
        if callable(api_call_action) and api is not bot:
            callers.append(("bot.api.call_action", api_call_action, {}))
        if not callers:
            return "撤回重复聊天记录失败：当前平台不支持撤回操作。"

        failures: list[str] = []
        for caller_name, call_action, extra_params in callers:
            try:
                result = await call_action(
                    "delete_msg",
                    message_id=original_id,
                    **extra_params,
                )
            except Exception as exc:
                failures.append(f"{caller_name}: {type(exc).__name__}: {exc}")
                continue
            if not self._action_failed(result):
                return None
            failures.append(f"{caller_name}: result={result}")

        self._warn(
            "[tool_suite] exact duplicate recall failed: "
            f"message_id={original_id}, attempts={failures}"
        )
        if self._group_id is not None:
            return (
                "撤回重复聊天记录失败。请确认机器人拥有群管理员权限；"
                "QQ 不允许管理员撤回群主或其他管理员的消息。"
            )
        return "撤回重复聊天记录失败，当前私聊可能不支持撤回对方消息。"
