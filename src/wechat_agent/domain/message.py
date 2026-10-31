# -*- coding: utf-8 -*-
"""消息与会话的基础类型（纯数据，零 I/O）。"""
from __future__ import annotations

from dataclasses import dataclass, field

CHATROOM_SUFFIX = "@chatroom"


def is_group(chat_id: str) -> bool:
    """按 wechatauto 的约定：群聊会话 id 以 @chatroom 结尾。"""
    return str(chat_id).endswith(CHATROOM_SUFFIX)


@dataclass
class Mention:
    """一条 @我的消息（群聊）。"""
    nick: str       # 发送者昵称
    body: str       # @ 消息正文

    def __str__(self) -> str:
        return f"{self.nick}：{self.body}"


@dataclass
class ContextMsg:
    """群聊上下文里的一条消息。"""
    sender: str
    content: str


@dataclass
class ParsedMsg:
    """解析后的群消息。"""
    body: str
    sender_id: str | None


def truncate_text(text: str, max_chars: int, suffix: str = "\n…（消息过长已截断）") -> str:
    """超长文本截断（保留尾部说明），用于入口消息与回复。"""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + suffix


def is_mention_me(body: str, self_nicks: tuple) -> bool:
    """判断群消息正文里是否 @ 了我（小号）。"""
    if not body:
        return False
    for n in self_nicks:
        if not n:
            continue
        if ("@" + n) in body:
            return True
    # 兜底：以 @ 开头且包含我的昵称
    if body.startswith("@"):
        for n in self_nicks:
            if n and n in body:
                return True
    return False
