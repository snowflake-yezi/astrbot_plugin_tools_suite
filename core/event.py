from __future__ import annotations

from typing import Any

from astrbot.api.message_components import At


def group_id(event: Any) -> str | None:
    value = event.get_group_id()
    if value is None or not str(value).strip():
        return None
    return str(value)


def scope_key(event: Any) -> str:
    current_group_id = group_id(event)
    if current_group_id is not None:
        return f"group:{current_group_id}"
    return f"private:{event.get_sender_id()}"


def message_text(event: Any) -> str:
    parts: list[str] = []
    for component in event.get_messages():
        if isinstance(component, At):
            continue
        text = getattr(component, "text", None)
        if text is None:
            text = getattr(component, "plain", None)
        if text is None:
            continue
        value = str(text).strip()
        if value:
            parts.append(value)
    if parts:
        return " ".join(parts).strip()
    value = getattr(event, "message_str", None)
    if value is None:
        value = event.get_message_str()
    return str(value).strip()


def at_user_ids(event: Any) -> list[str]:
    user_ids: list[str] = []
    seen: set[str] = set()
    for component in event.get_messages():
        if not isinstance(component, At):
            continue
        user_id = str(getattr(component, "qq", "") or "").strip()
        if not user_id or user_id == "all" or user_id in seen:
            continue
        seen.add(user_id)
        user_ids.append(user_id)
    return user_ids


def message_id(event: Any) -> int | str | None:
    message_obj = getattr(event, "message_obj", None)
    raw_message = getattr(message_obj, "raw_message", None)
    value = raw_message.get("message_id") if isinstance(raw_message, dict) else None
    if value in (None, ""):
        value = getattr(message_obj, "message_id", None)
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if value.lstrip("-").isdigit():
            return int(value)
    return value
