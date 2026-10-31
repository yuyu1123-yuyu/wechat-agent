# -*- coding: utf-8 -*-
"""会话身份与数据作用域（纯函数）。

数据目录约定（与旧项目兼容，用户可手改 md）：
    data/chats/<safe_id>/
        persona.md   专属性格（可选，覆盖默认人设）
        rules.md     高等级规则（用户维护，AI 只读）
        memory.md    AI 长期记忆（AI 通过 remember 指令维护）
        history.json 对话历史（私聊 / 群聊里 AI 参与过的）
        schedules.json 日程
        mode.json    通道偏好（api / harness）
        active_mask.json 当前面具
        group_context.jsonl 群聊上下文（追加式）
"""
from __future__ import annotations

from pathlib import Path

from wechat_agent.domain.message import is_group


def safe_dir_name(chat_id: str) -> str:
    """把会话 id 变成安全的目录名（去掉路径危险字符）。"""
    safe = "".join(c for c in str(chat_id) if c.isalnum() or c in "-_.")[:80]
    return safe or "unknown"


def scope_dir(chat_dir: Path, chat_id: str, mask: str | None, default_mask: str) -> Path:
    """
    返回当前生效的数据目录：
    - 群聊 → 顶层（面具只用于私聊）
    - 私聊且当前面具非默认 → masks/<面具名>/
    - 私聊默认面具 / 未设置 → 顶层
    """
    if is_group(chat_id):
        return chat_dir
    if mask and mask != default_mask:
        d = chat_dir / "masks" / mask
        d.mkdir(parents=True, exist_ok=True)
        return d
    return chat_dir
